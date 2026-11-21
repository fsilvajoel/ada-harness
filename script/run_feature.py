#!/usr/bin/env python3
"""Driver de feature ponta a ponta (Onda 5): SDD aprovado -> plano/cartões -> contrato -> stages -> closing.

Um único comando conduz a feature. As únicas paradas previstas são decisões humanas explícitas:

- o planner devolveu `questions.json` (requisito aberto): o humano responde editando e reaprovando o SDD;
- o planner devolveu rascunhos válidos de `plan.json`/`cards.yaml`: o humano aprova os dois por hash
  (`sdd.py approve plan ...`) e roda o mesmo comando de novo.

Fluxo da stage `plan`:
1. valida a cadeia PRD/ADR/RFC aprovada por hash (sem worker se inválida);
2. despacha o planner (worker primário de `feature_contract.planner.role`) numa pasta de staging dentro de
   `.harness/runs/<run>/<feature>/staging/<n>`; a raiz precisa continuar limpa depois do worker;
3. com aprovação válida dos dois rascunhos, publica numa worktree isolada: `prepare_feature` (plano e cartões),
   `build_contract` (`<docs>/plano/<feature>/contract.json` + requests por stage) e commit em `harness/*`.

Depois, para cada stage, o contrato é relido do commit publicado e verificado (hash do contrato, lint, SHA-256
do `harness.project.yaml`, do SDD e dos requests) **antes** de qualquer worker. Stage não aplicável é ignorada
com evento; as demais rodam via `stage_runner.run_configured_stage` (worktree por tentativa, gate, fallback,
evidência, commit encadeado). Nada é mesclado: o resultado é a última branch `harness/*` aprovada.

Saídas: 0 feature concluída; 1 stage falhou/bloqueada; 2 uso ou configuração; 3 aguardando decisão humana.

Limitação: o planner usa só o worker primário e roda fora de worktree (cwd = staging); a proteção é a
verificação de raiz limpa e a lista fechada de arquivos aceitos no staging.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

from script.card_lint import lint_cards_document
from script.events import EventError, append_event, read_events
from script.feature_contract import (
    CONTRACT_STAGES,
    SDD_KINDS,
    ContractError,
    build_contract,
    contract_bytes,
    executor_chain,
    lint_contract,
    load_contract_config,
    sha256_bytes,
    sha256_file,
    worker_timeout,
)
from script.pipeline import TransitionError, finish_stage, replay, request_input, skip_stage, start_stage
from script.plan_lint import lint_feature_plan
from script.project_config import CONFIG_NAME, get_str, load_config
from script.sdd import SDDError, prepare_feature, validate_approval, validate_approved_chain
from script.stage_runner import StageRunnerError, run_configured_stage
from script.worktree import (
    WorktreeError,
    commit_attempt,
    create_attempt_worktree,
    preflight,
    remove_attempt_worktree,
)

EXIT_DONE: Final[int] = 0
EXIT_BLOCKED: Final[int] = 1
EXIT_USAGE: Final[int] = 2
EXIT_NEEDS_INPUT: Final[int] = 3

ID_RUN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._-]+")
ID_FEATURE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+-F\d+")
PLAN_DRAFT: Final[str] = "plan.json"
CARDS_DRAFT: Final[str] = "cards.yaml"
QUESTIONS: Final[str] = "questions.json"
PLAN_APPROVAL: Final[str] = "plan.approval.json"
CARDS_APPROVAL: Final[str] = "cards.approval.json"
STAGING_CONTROL: Final[frozenset[str]] = frozenset({"request.md", "engine-meta.json", "generation.json"})
STAGING_ALLOWED: Final[frozenset[str]] = STAGING_CONTROL | {
    PLAN_DRAFT, CARDS_DRAFT, QUESTIONS, PLAN_APPROVAL, CARDS_APPROVAL,
}
CONTRACT_EVENT: Final[str] = "contract_published"


class FeatureRunError(RuntimeError):
    """Bloqueio do driver com código de saída."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


class FeatureRunner:
    def __init__(self, project_root: Path, run_id: str, feature_id: str, *, retry_failed: bool) -> None:
        if not ID_RUN.fullmatch(run_id) or not ID_FEATURE.fullmatch(feature_id):
            raise FeatureRunError(EXIT_USAGE, "run_id ou feature_id inválido")
        self.root = project_root.resolve()
        self.run_id = run_id
        self.feature_id = feature_id
        self.retry_failed = retry_failed
        try:
            self.config: dict[str, object] = load_config(self.root, required=True)
            self.spec = load_contract_config(self.config, feature_id)
        except (OSError, ValueError, TypeError) as exc:
            raise FeatureRunError(EXIT_USAGE, f"configuração inválida: {exc}") from exc
        docs_dir = get_str(self.config, "docs_dir") or "docs"
        if Path(docs_dir).is_absolute() or "\\" in docs_dir or ".." in Path(docs_dir).parts:
            raise FeatureRunError(EXIT_USAGE, "docs_dir precisa ser relativo e seguro")
        self.docs_dir = docs_dir
        self.run_dir = self.root / ".harness" / "runs" / run_id / feature_id
        self.event_path = self.run_dir / "events.jsonl"
        self.sdd_dir = self.root / self.spec.sdd_dir / feature_id
        self.request_dir = Path(".harness") / "runs" / run_id / feature_id / "requests"
        self.contract_relative = f"{docs_dir}/plano/{feature_id}/contract.json"

    # ---------- estado ----------
    def _state(self) -> dict[str, object]:
        return replay(self.run_id, self.feature_id, read_events(self.event_path))

    def _stage_status(self, state: dict[str, object], stage: str) -> str:
        stages = state["stages"]
        assert isinstance(stages, dict)
        entry = stages[stage]
        assert isinstance(entry, dict)
        return str(entry.get("status"))

    def _attempts(self, state: dict[str, object], stage: str) -> int:
        attempts = state.get("attempts")
        value = attempts.get(stage, 0) if isinstance(attempts, dict) else 0
        return value if isinstance(value, int) else 0

    # ---------- stage plan ----------
    def _sdd_hashes(self) -> dict[str, str]:
        errors = validate_approved_chain(self.sdd_dir)
        if errors:
            raise FeatureRunError(EXIT_BLOCKED, "cadeia SDD não aprovada: " + "; ".join(errors))
        return {kind: sha256_file(self.sdd_dir / f"{kind}.md") for kind in SDD_KINDS}

    def _latest_staging(self) -> tuple[Path, dict[str, object]] | None:
        staging_root = self.run_dir / "staging"
        if not staging_root.is_dir():
            return None
        numbered = sorted(
            (int(item.name), item) for item in staging_root.iterdir() if item.is_dir() and item.name.isdigit()
        )
        for _, directory in reversed(numbered):
            generation = directory / "generation.json"
            try:
                data = json.loads(generation.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                return directory, data
        return None

    def _plan_request(self, hashes: dict[str, str]) -> str:
        lines = [
            f"# {self.feature_id} — stage plan",
            "",
            f"Papel: {self.spec.planner_role}. Gere os artefatos a partir das fontes aprovadas abaixo.",
            "",
            "## Fontes aprovadas (SHA-256)",
            *(f"- {kind}: {self.spec.sdd_dir}/{self.feature_id}/{kind}.md ({hashes[kind]})" for kind in SDD_KINDS),
            "",
            "## Saída esperada (no diretório atual)",
            f"- `{PLAN_DRAFT}`: plano V2 da feature {self.feature_id} (`plan_lint`).",
            f"- `{CARDS_DRAFT}`: cartões V2 com IDs `{self.feature_id}-T<n>` (`card_lint --strict`).",
            f"- Ou, havendo requisito aberto, somente `{QUESTIONS}` com `{{\"questions\": [\"...\"]}}`.",
            "",
            "## Regras",
            "- Não escreva fora do diretório atual, não faça commit e não altere o SDD nem a configuração.",
            "",
        ]
        return "\n".join(lines)

    def _draft_errors(self, staging: Path) -> list[str]:
        errors: list[str] = []
        try:
            plan = json.loads((staging / PLAN_DRAFT).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [f"plano ilegível: {exc}"]
        try:
            cards = yaml.safe_load((staging / CARDS_DRAFT).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            return [f"cartões ilegíveis: {exc}"]
        if not isinstance(plan, dict):
            return ["plano precisa ser mapa"]
        if plan.get("feature") != self.feature_id:
            errors.append("plano de outra feature")
        errors.extend(lint_feature_plan(plan))
        if not isinstance(cards, dict):
            return [*errors, "cartões precisam ser mapa"]
        errors.extend(lint_cards_document(cards, strict=True, project_root=self.root))
        items = cards.get("cards")
        if not isinstance(items, list) or not items:
            errors.append("cards vazio")
        elif any(not isinstance(card, dict) or not str(card.get("id", "")).startswith(self.feature_id + "-T")
                 for card in items):
            errors.append(f"cartão fora da feature {self.feature_id}")
        return errors

    def _approvals_valid(self, staging: Path) -> bool:
        return not (
            validate_approval(staging / PLAN_DRAFT, staging / PLAN_APPROVAL, kind="plan")
            or validate_approval(staging / CARDS_DRAFT, staging / CARDS_APPROVAL, kind="plan")
        )

    def _approve_hint(self, staging: Path) -> str:
        relative = staging.relative_to(self.root).as_posix()
        return (
            "aprovar rascunhos: "
            f"python3 script/sdd.py approve plan {relative}/{PLAN_DRAFT} {relative}/{PLAN_APPROVAL} --reviewer <nome> && "
            f"python3 script/sdd.py approve plan {relative}/{CARDS_DRAFT} {relative}/{CARDS_APPROVAL} --reviewer <nome>"
        )

    def _generate(self, hashes: dict[str, str], attempt: int) -> int:
        staging = self.run_dir / "staging" / str(attempt)
        if staging.exists():
            raise FeatureRunError(EXIT_BLOCKED, f"staging já existe: {staging}")
        staging.mkdir(parents=True)
        request = staging / "request.md"
        request.write_text(self._plan_request(hashes), encoding="utf-8")
        try:
            primary = executor_chain(self.config, self.spec.planner_role)[0]
            timeout_s = worker_timeout(self.config, primary)
        except ContractError as exc:
            raise FeatureRunError(EXIT_USAGE, str(exc)) from exc
        start_stage(self.event_path, self.run_id, self.feature_id, "plan", executor=primary, attempt=attempt)
        command = [
            sys.executable, str(Path(__file__).with_name("dispatch.py")), str(self.root),
            "--role", self.spec.planner_role, "--worker", primary, "--mode", "write", "--stage", "plan",
            "--request", str(request), "--cwd", str(staging), "--meta", str(staging / "engine-meta.json"),
        ]
        try:
            completed = subprocess.run(
                command, cwd=self.root, capture_output=True, text=True, timeout=timeout_s, check=False
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            exit_code = -1
        outcome, reason = self._classify_generation(staging, exit_code)
        (staging / "generation.json").write_text(
            json.dumps({"attempt": attempt, "sdd": hashes, "outcome": outcome, "exitCode": exit_code}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        if outcome == "questions":
            request_input(self.event_path, self.run_id, self.feature_id, "plan", reason)
            _emit({"status": "needs_input", "stage": "plan", "reason": reason})
            return EXIT_NEEDS_INPUT
        if outcome == "drafts":
            request_input(self.event_path, self.run_id, self.feature_id, "plan", self._approve_hint(staging))
            _emit({"status": "needs_input", "stage": "plan", "reason": self._approve_hint(staging)})
            return EXIT_NEEDS_INPUT
        finish_stage(self.event_path, self.run_id, self.feature_id, "plan", passed=False, reason=reason,
                     executor=primary, attempt=attempt)
        _emit({"status": "failed", "stage": "plan", "reason": reason})
        return EXIT_BLOCKED

    def _classify_generation(self, staging: Path, exit_code: int) -> tuple[str, str]:
        try:
            preflight(self.root)
        except WorktreeError:
            return "invalid", "planner escreveu fora do staging (raiz suja)"
        if exit_code != 0:
            return "invalid", f"planner falhou (exit {exit_code})"
        unexpected = sorted(
            item.relative_to(staging).as_posix()
            for item in staging.rglob("*")
            if item.relative_to(staging).as_posix() not in STAGING_ALLOWED - {PLAN_APPROVAL, CARDS_APPROVAL}
        )
        if unexpected:
            return "invalid", "arquivos inesperados no staging: " + ", ".join(unexpected)
        if (staging / QUESTIONS).exists():
            try:
                data = json.loads((staging / QUESTIONS).read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return "invalid", "questions.json ilegível"
            questions = data.get("questions") if isinstance(data, dict) else None
            if not isinstance(questions, list) or not questions or any(
                not isinstance(item, str) or not item.strip() for item in questions
            ):
                return "invalid", "questions.json precisa de lista não vazia de textos"
            relative = (staging / QUESTIONS).relative_to(self.root).as_posix()
            return "questions", (
                f"{len(questions)} questão(ões) aberta(s) em {relative}; responda no SDD, reaprove e rode de novo"
            )
        if not (staging / PLAN_DRAFT).exists() or not (staging / CARDS_DRAFT).exists():
            return "invalid", "planner não gerou plan.json e cards.yaml"
        errors = self._draft_errors(staging)
        if errors:
            return "invalid", "rascunhos inválidos: " + "; ".join(errors)
        return "drafts", "rascunhos válidos aguardando aprovação"

    def _publish(self, staging: Path, attempt: int) -> int:
        start_stage(self.event_path, self.run_id, self.feature_id, "plan", executor="publish", attempt=attempt)
        worktree: Path | None = None
        try:
            worktree, _ = create_attempt_worktree(self.root, self.run_id, self.feature_id, "plan", attempt)
            plan_output, cards_output = prepare_feature(
                worktree,
                sdd_directory=worktree / self.spec.sdd_dir / self.feature_id,
                plan_path=staging / PLAN_DRAFT,
                cards_path=staging / CARDS_DRAFT,
                approval_path=staging / PLAN_APPROVAL,
            )
            contract, requests = build_contract(
                worktree,
                feature_id=self.feature_id,
                config=load_config(worktree, required=True),
                plan_path=plan_output,
                cards_path=cards_output,
                request_dir=self.request_dir,
            )
            payload = contract_bytes(contract)
            contract_path = worktree / self.contract_relative
            if contract_path.exists():
                raise ContractError("contrato já existe; não sobrescrevendo")
            contract_path.write_bytes(payload)
            for relative, content in requests.items():
                target = self.root / relative
                if target.exists() and target.read_text(encoding="utf-8") != content:
                    raise ContractError(f"request divergente já existe: {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            allowed = [
                plan_output.relative_to(worktree).as_posix(),
                cards_output.relative_to(worktree).as_posix(),
                self.contract_relative,
            ]
            commit_sha = commit_attempt(
                worktree, allowed_paths=allowed, feature_id=self.feature_id, stage="plan", attempt=attempt
            )
        except (SDDError, ContractError, WorktreeError, OSError, ValueError) as exc:
            finish_stage(self.event_path, self.run_id, self.feature_id, "plan", passed=False,
                         reason=f"publicação falhou: {exc}", executor="publish", attempt=attempt)
            if worktree is not None:
                remove_attempt_worktree(self.root, worktree, force=True, delete_branch=True)
            _emit({"status": "failed", "stage": "plan", "reason": f"publicação falhou: {exc}"})
            return EXIT_BLOCKED
        finish_stage(self.event_path, self.run_id, self.feature_id, "plan", passed=True,
                     executor="publish", attempt=attempt, commit_sha=commit_sha)
        append_event(self.event_path, {
            "event": CONTRACT_EVENT,
            "run_id": self.run_id,
            "feature_id": self.feature_id,
            "stage": "plan",
            "path": self.contract_relative,
            "sha256": sha256_bytes(payload),
            "commit_sha": commit_sha,
        })
        remove_attempt_worktree(self.root, worktree)
        return EXIT_DONE

    def run_plan(self) -> int | None:
        """Retorna código de saída para parar, ou None quando o plano foi publicado agora ou antes."""
        state = self._state()
        status = self._stage_status(state, "plan")
        if status in {"done", "skipped"}:
            return None
        if status == "running":
            raise FeatureRunError(EXIT_BLOCKED, "stage plan ficou running; recuperação manual necessária")
        hashes = self._sdd_hashes()
        attempt = self._attempts(state, "plan") + 1
        if status == "failed" and not self.retry_failed:
            raise FeatureRunError(EXIT_BLOCKED, "stage plan falhou; use --retry-failed para nova geração")
        latest = self._latest_staging() if status == "needs_input" else None
        if latest is None or latest[1].get("sdd") != hashes:
            return self._generate(hashes, attempt)
        staging, generation = latest
        if generation.get("outcome") == "drafts" and self._approvals_valid(staging):
            code = self._publish(staging, attempt)
            return None if code == EXIT_DONE else code
        reason = (
            self._approve_hint(staging) if generation.get("outcome") == "drafts"
            else "aguardando resposta às questões no SDD (edite e reaprove PRD/ADR/RFC)"
        )
        _emit({"status": "needs_input", "stage": "plan", "reason": reason})
        return EXIT_NEEDS_INPUT

    # ---------- contrato ----------
    def _published(self) -> tuple[dict[str, object], str]:
        events = read_events(self.event_path)
        published = [
            event for event in events
            if event.get("event") == CONTRACT_EVENT and event.get("run_id") == self.run_id
            and event.get("feature_id") == self.feature_id
        ]
        if not published:
            raise FeatureRunError(EXIT_BLOCKED, "contrato não publicado")
        event = published[-1]
        commit_sha, expected_sha = event.get("commit_sha"), event.get("sha256")
        if not isinstance(commit_sha, str) or not isinstance(expected_sha, str):
            raise FeatureRunError(EXIT_BLOCKED, "evento contract_published inválido")
        raw = self._show(commit_sha, self.contract_relative)
        if sha256_bytes(raw) != expected_sha:
            raise FeatureRunError(EXIT_BLOCKED, "hash do contrato diverge do publicado")
        try:
            contract = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeatureRunError(EXIT_BLOCKED, f"contrato ilegível: {exc}") from exc
        if not isinstance(contract, dict):
            raise FeatureRunError(EXIT_BLOCKED, "contrato precisa ser mapa")
        return contract, commit_sha

    def _show(self, commit_sha: str, relative: str) -> bytes:
        completed = subprocess.run(
            ["git", "-C", str(self.root), "show", f"{commit_sha}:{relative}"],
            check=False, capture_output=True,
        )
        if completed.returncode != 0:
            raise FeatureRunError(EXIT_BLOCKED, f"{relative} ausente no commit {commit_sha[:12]}")
        return completed.stdout

    def verify_contract(self) -> dict[str, object]:
        """Reverifica tudo antes de cada stage; qualquer divergência bloqueia antes do worker."""
        contract, commit_sha = self._published()
        try:
            config = load_config(self.root, required=True)
        except (OSError, ValueError, TypeError) as exc:
            raise FeatureRunError(EXIT_USAGE, f"configuração inválida: {exc}") from exc
        errors = lint_contract(contract, config)
        if errors:
            raise FeatureRunError(EXIT_BLOCKED, "contrato inválido: " + "; ".join(errors))
        if contract["configSha256"] != sha256_file(self.root / CONFIG_NAME):
            raise FeatureRunError(EXIT_BLOCKED, f"{CONFIG_NAME} mudou desde a publicação do contrato")
        sdd = contract["sdd"]
        assert isinstance(sdd, dict)
        for kind, ref in sdd.items():
            assert isinstance(ref, dict)
            path = self.root / str(ref["path"])
            if not path.is_file() or sha256_file(path) != ref["sha256"]:
                raise FeatureRunError(EXIT_BLOCKED, f"SDD {kind} mudou desde a publicação do contrato")
        for field in ("plan", "cards"):
            ref = contract[field]
            assert isinstance(ref, dict)
            if sha256_bytes(self._show(commit_sha, str(ref["path"]))) != ref["sha256"]:
                raise FeatureRunError(EXIT_BLOCKED, f"{field} publicado diverge do contrato")
        stages = contract["stages"]
        assert isinstance(stages, list)
        for entry in stages:
            assert isinstance(entry, dict)
            ref = entry["request"]
            assert isinstance(ref, dict)
            path = self.root / str(ref["path"])
            if not path.is_file() or sha256_file(path) != ref["sha256"]:
                raise FeatureRunError(EXIT_BLOCKED, f"request de {entry['stage']} mudou ou sumiu")
        self.config = config
        return contract

    # ---------- driver ----------
    def run(self) -> int:
        code = self.run_plan()
        if code is not None:
            return code
        retried: set[str] = set()
        while True:
            state = self._state()
            stage = state["current_stage"]
            if stage is None:
                return self._finish()
            assert isinstance(stage, str)
            status = self._stage_status(state, stage)
            if status == "running":
                raise FeatureRunError(EXIT_BLOCKED, f"stage {stage} ficou running; recuperação manual necessária")
            if status == "needs_input":
                _emit({"status": "needs_input", "stage": stage})
                return EXIT_NEEDS_INPUT
            if status == "failed":
                if not self.retry_failed or stage in retried:
                    raise FeatureRunError(EXIT_BLOCKED, f"stage {stage} falhou; use --retry-failed")
                retried.add(stage)
            contract = self.verify_contract()
            stages = contract["stages"]
            assert isinstance(stages, list)
            entry = next(item for item in stages if isinstance(item, dict) and item.get("stage") == stage)
            if entry["applicable"] is False:
                if status != "pending":
                    raise FeatureRunError(EXIT_BLOCKED, f"stage {stage} não aplicável em estado {status}")
                skip_stage(self.event_path, self.run_id, self.feature_id, stage, "contrato: stage não aplicável")
                continue
            request = entry["request"]
            assert isinstance(request, dict)
            result = run_configured_stage(
                self.root,
                run_id=self.run_id,
                feature_id=self.feature_id,
                stage=stage,
                role=str(entry["role"]),
                request=self.root / str(request["path"]),
                gate_name=str(entry["gate"]),
                allowed_paths=[str(path) for path in entry["allowedPaths"]],  # type: ignore[union-attr]
                skills=[str(skill) for skill in entry["skills"]],  # type: ignore[union-attr]
                mode=str(entry["mode"]),
                stub_paths=[str(path) for path in entry.get("stubPaths", [])],  # type: ignore[union-attr]
            )
            if not result.passed:
                _emit({"status": "failed", "stage": stage, "reason": result.reason,
                       "failure_type": result.failure_type, "attempts": result.attempts})
                return EXIT_BLOCKED

    def _finish(self) -> int:
        events = read_events(self.event_path)
        last = next(
            (event for event in reversed(events)
             if event.get("event") == "stage_passed" and isinstance(event.get("commit_sha"), str)),
            None,
        )
        if last is None:
            raise FeatureRunError(EXIT_BLOCKED, "nenhum commit aprovado")
        branch = f"harness/{self.feature_id}/{self.run_id}/{last['stage']}/{last.get('attempt')}"
        _emit({"status": "done", "feature": self.feature_id, "run": self.run_id,
               "branch": branch, "commit_sha": last["commit_sha"]})
        return EXIT_DONE


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executa uma feature inteira a partir do SDD aprovado.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--run", required=True, dest="run_id")
    parser.add_argument("--feature", required=True, dest="feature_id")
    parser.add_argument("--retry-failed", action="store_true", help="refaz uma vez a stage atual em failed")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        runner = FeatureRunner(args.project_root, args.run_id, args.feature_id, retry_failed=args.retry_failed)
        return runner.run()
    except FeatureRunError as exc:
        _emit({"status": "blocked", "reason": str(exc)})
        return exc.code
    except (StageRunnerError, WorktreeError, TransitionError, ContractError, SDDError, EventError) as exc:
        _emit({"status": "blocked", "reason": f"{type(exc).__name__}: {exc}"})
        return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
