#!/usr/bin/env python3
"""Revisão visual automatizada com evidência verificável (Ada Harness 2.0, onda 3).

O Harness não traz navegador próprio: o projeto configura em `visual_review.command` a ferramenta que já usa
(ex.: um script Playwright/Puppeteer do próprio projeto). O comando roda uma vez por critério visual do plano e
precisa gravar um screenshot e um relatório JSON. Qualquer ausência vira FAIL explícito, nunca PASS fictício.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

ROOT: Final = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.evidence import sha256_file, utc_now
from script.plan_lint import lint_feature_plan
from script.project_config import load_config
from script.testing_policy import LIBRARY, STYLE, PolicyValidationError, resolve_feature_gates

EVIDENCE_FILE: Final = "visual-review.json"
SCHEMA_VERSION: Final = 1
DEFAULT_TIMEOUT_S: Final = 120
PLACEHOLDERS: Final[frozenset[str]] = frozenset({"target", "criterion", "screenshot", "report", "feature"})
EVIDENCE_FIELDS: Final[frozenset[str]] = frozenset({
    "schemaVersion", "feature", "status", "target", "generated_at", "summary", "errors", "criteria",
})
CRITERION_FIELDS: Final[frozenset[str]] = frozenset({
    "criterion", "statement", "command", "exit_code", "status", "screenshot", "screenshot_sha256",
    "report", "report_sha256", "summary", "error", "finished_at",
})


class VisualReviewError(ValueError):
    pass


@dataclass(frozen=True)
class VisualConfig:
    command: list[str]
    target: str
    timeout_s: int


@dataclass
class VisualReviewResult:
    status: str  # PASS, FAIL ou NOT_APPLICABLE
    errors: list[str] = field(default_factory=list)
    evidence: Path | None = None

    @property
    def passed(self) -> bool:
        return self.status in {"PASS", "NOT_APPLICABLE"}


def sanitize_target(target: str) -> str:
    """Remove credenciais, query e fragmento (onde costumam morar tokens) de uma URL."""
    parts = urlsplit(target)
    if not parts.scheme or not parts.netloc:
        return target.split("?", 1)[0].split("#", 1)[0]
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def load_visual_config(config: dict[str, object]) -> VisualConfig:
    raw = config.get("visual_review")
    if not isinstance(raw, dict):
        raise VisualReviewError("visual_review ausente em harness.project.yaml")
    command_text = raw.get("command")
    target = raw.get("target")
    timeout_s = raw.get("timeout_s", DEFAULT_TIMEOUT_S)
    if not isinstance(command_text, str) or not command_text.strip():
        raise VisualReviewError("visual_review.command ausente")
    if not isinstance(target, str) or not target.strip():
        raise VisualReviewError("visual_review.target ausente")
    if type(timeout_s) is not int or timeout_s < 1:
        raise VisualReviewError("visual_review.timeout_s precisa ser inteiro positivo")
    unknown = set(raw) - {"command", "target", "timeout_s"}
    if unknown:
        raise VisualReviewError(f"visual_review: campos desconhecidos: {', '.join(sorted(map(str, unknown)))}")
    command = shlex.split(command_text)
    used = {name for part in command for name in re.findall(r"\{([a-z]+)\}", part)}
    if used - PLACEHOLDERS:
        raise VisualReviewError(f"visual_review.command: placeholder desconhecido: {', '.join(sorted(used - PLACEHOLDERS))}")
    if not {"screenshot", "report"} <= used:
        raise VisualReviewError("visual_review.command precisa usar {screenshot} e {report}")
    return VisualConfig(command=command, target=target, timeout_s=timeout_s)


def has_visual_sources(plan: dict[str, object]) -> bool:
    sources = plan.get("sources")
    return isinstance(sources, list) and any(
        isinstance(source, dict) and source.get("classification") in {STYLE, LIBRARY} for source in sources
    )


def visual_required(plan: dict[str, object]) -> bool:
    """Revisão visual é exigida quando a política resolve visual_review ou o plano inválido tem fonte visual."""
    if lint_feature_plan(plan):
        return has_visual_sources(plan)
    try:
        return resolve_feature_gates(plan)["visual_review"]
    except PolicyValidationError:
        return has_visual_sources(plan)


def visual_criteria(plan: dict[str, object]) -> list[tuple[str, str]]:
    criteria = plan.get("criteria")
    if not isinstance(criteria, list):
        return []
    found: list[tuple[str, str]] = []
    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        cid, layers, statement = criterion.get("id"), criterion.get("layers"), criterion.get("statement")
        if isinstance(cid, str) and isinstance(layers, list) and "visual" in layers:
            found.append((cid, statement if isinstance(statement, str) else ""))
    return found


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _write_evidence(directory: Path, payload: dict[str, object]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / EVIDENCE_FILE
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _review_criterion(
    cfg: VisualConfig,
    *,
    feature_id: str,
    criterion: str,
    statement: str,
    cwd: Path,
    output_dir: Path,
) -> dict[str, object]:
    screenshot = output_dir / f"{criterion}.png"
    report = output_dir / f"{criterion}.report.json"
    for stale in (screenshot, report):
        stale.unlink(missing_ok=True)
    values = {
        "target": cfg.target,
        "criterion": criterion,
        "screenshot": str(screenshot),
        "report": str(report),
        "feature": feature_id,
    }
    safe_values = {**values, "target": sanitize_target(cfg.target)}

    def render(part: str, mapping: dict[str, str]) -> str:
        return re.sub(r"\{([a-z]+)\}", lambda match: mapping.get(match.group(1), match.group(0)), part)

    command = [render(part, values) for part in cfg.command]
    entry: dict[str, object] = {
        "criterion": criterion,
        "statement": statement,
        "command": [render(part, safe_values) for part in cfg.command],
        "exit_code": None,
        "status": "FAIL",
        "screenshot": screenshot.name,
        "screenshot_sha256": None,
        "report": report.name,
        "report_sha256": None,
        "summary": "",
        "error": None,
        "finished_at": None,
    }
    try:
        completed = subprocess.run(
            command, cwd=cwd, check=False, capture_output=True, text=True, timeout=cfg.timeout_s
        )
    except FileNotFoundError:
        entry["error"] = f"ferramenta visual não encontrada: {command[0]}"
        entry["finished_at"] = utc_now()
        return entry
    except subprocess.TimeoutExpired:
        entry["error"] = f"timeout após {cfg.timeout_s}s"
        entry["finished_at"] = utc_now()
        return entry
    entry["exit_code"] = completed.returncode
    entry["finished_at"] = utc_now()
    if completed.returncode != 0:
        entry["error"] = f"ferramenta visual saiu com {completed.returncode}"
        return entry
    if not screenshot.is_file() or screenshot.stat().st_size == 0:
        entry["error"] = "screenshot ausente ou vazio"
        return entry
    entry["screenshot_sha256"] = sha256_file(screenshot)
    with screenshot.open("rb") as handle:
        if handle.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
            entry["error"] = "screenshot não é um PNG válido"
            return entry
    if not report.is_file():
        entry["error"] = "relatório do critério ausente"
        return entry
    entry["report_sha256"] = sha256_file(report)
    try:
        report_data = json.loads(report.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        entry["error"] = f"relatório ilegível: {exc}"
        return entry
    if not isinstance(report_data, dict) or report_data.get("criterion") != criterion:
        entry["error"] = "relatório não corresponde ao critério"
        return entry
    summary = report_data.get("summary")
    entry["summary"] = summary if isinstance(summary, str) else ""
    if report_data.get("found") is not True:
        entry["error"] = "critério visual não encontrado na tela"
        return entry
    if not entry["summary"]:
        entry["error"] = "relatório sem resumo"
        return entry
    entry["status"] = "PASS"
    return entry


def run_visual_review(
    plan: dict[str, object],
    config: dict[str, object],
    *,
    feature_id: str,
    cwd: Path,
    evidence_dir: Path,
) -> VisualReviewResult:
    """Roda a revisão visual e grava evidência; FAIL explícito em qualquer lacuna."""
    if not visual_required(plan):
        return VisualReviewResult("NOT_APPLICABLE")

    errors = [f"plano: {error}" for error in lint_feature_plan(plan)]
    criteria = visual_criteria(plan)
    if not errors and not criteria:
        errors.append("classificação visual sem critério com layer visual executável")
    cfg: VisualConfig | None = None
    if not errors:
        try:
            cfg = load_visual_config(config)
        except VisualReviewError as exc:
            errors.append(str(exc))
    payload: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "feature": feature_id,
        "status": "FAIL",
        "target": sanitize_target(cfg.target) if cfg else None,
        "generated_at": utc_now(),
        "summary": "",
        "errors": errors,
        "criteria": [],
    }
    if errors or cfg is None:
        payload["summary"] = "revisão visual bloqueada antes da execução"
        return VisualReviewResult("FAIL", errors, _write_evidence(evidence_dir, payload))

    evidence_dir.mkdir(parents=True, exist_ok=True)
    entries = [
        _review_criterion(
            cfg, feature_id=feature_id, criterion=cid, statement=statement, cwd=cwd, output_dir=evidence_dir
        )
        for cid, statement in criteria
    ]
    failed = [entry for entry in entries if entry["status"] != "PASS"]
    errors = [f"{entry['criterion']}: {entry['error']}" for entry in failed]
    payload.update({
        "status": "FAIL" if failed else "PASS",
        "criteria": entries,
        "errors": errors,
        "summary": f"{len(entries) - len(failed)}/{len(entries)} critérios visuais aprovados",
    })
    path = _write_evidence(evidence_dir, payload)
    validation = validate_visual_evidence(path)
    if validation:
        return VisualReviewResult("FAIL", errors + validation, path)
    return VisualReviewResult(str(payload["status"]), errors, path)


def validate_visual_evidence(path: Path) -> list[str]:
    """Revalida estrutura, coerência de status e hashes dos artefatos visuais."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"evidência visual ilegível: {exc}"]
    if not isinstance(data, dict):
        return ["evidência visual precisa ser mapa"]
    errors: list[str] = []
    if set(data) != EVIDENCE_FIELDS:
        errors.append("evidência visual com campos ausentes ou desconhecidos")
    if data.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("schemaVersion inválido")
    status = data.get("status")
    if status not in {"PASS", "FAIL"}:
        errors.append("status inválido")
    target = data.get("target")
    if isinstance(target, str) and sanitize_target(target) != target:
        errors.append("target contém query, fragmento ou credencial")
    criteria = data.get("criteria")
    if not isinstance(criteria, list):
        return errors + ["criteria precisa ser lista"]
    if status == "PASS" and (not criteria or data.get("errors")):
        errors.append("PASS sem critérios ou com erros")
    directory = path.resolve().parent
    for entry in criteria:
        if not isinstance(entry, dict) or set(entry) != CRITERION_FIELDS:
            errors.append("critério com campos inválidos")
            continue
        cid = entry.get("criterion")
        if entry.get("status") not in {"PASS", "FAIL"}:
            errors.append(f"{cid}: status inválido")
        if status == "PASS" and entry.get("status") != "PASS":
            errors.append(f"{cid}: PASS global com critério reprovado")
        for name, sha_field in (("screenshot", "screenshot_sha256"), ("report", "report_sha256")):
            file_name, digest = entry.get(name), entry.get(sha_field)
            if entry.get("status") == "PASS" and not isinstance(digest, str):
                errors.append(f"{cid}: {sha_field} ausente")
            if not isinstance(digest, str):
                continue
            if not isinstance(file_name, str) or "/" in file_name or "\\" in file_name or file_name in {"", ".", ".."}:
                errors.append(f"{cid}: {name} precisa ser arquivo da pasta da evidência")
                continue
            artifact = directory / file_name
            if not artifact.is_file():
                errors.append(f"{cid}: {name} ausente")
            elif sha256_file(artifact) != digest:
                errors.append(f"{cid}: {sha_field} não confere")
    return errors


def load_evidence_summaries(evidence_root: Path) -> list[dict[str, object]]:
    """Leitura para o dashboard: status, hashes e validação de cada evidência visual."""
    summaries: list[dict[str, object]] = []
    if not evidence_root.is_dir():
        return summaries
    for path in sorted(evidence_root.glob(f"*/*/visual-review/{EVIDENCE_FILE}")):
        stage, attempt = path.parent.parent.parent.name, path.parent.parent.name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        criteria = data.get("criteria") if isinstance(data, dict) else None
        summaries.append({
            "stage": stage,
            "attempt": int(attempt) if attempt.isdecimal() else attempt,
            "status": data.get("status") if isinstance(data, dict) else None,
            "summary": data.get("summary") if isinstance(data, dict) else None,
            "criteria": [
                {
                    "criterion": item.get("criterion"),
                    "status": item.get("status"),
                    "screenshot_sha256": item.get("screenshot_sha256"),
                }
                for item in criteria if isinstance(item, dict)
            ] if isinstance(criteria, list) else [],
            "validation_errors": validate_visual_evidence(path),
        })
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Revisão visual com evidência (Ada Harness 2.0).")
    parser.add_argument("root", type=Path, help="raiz do projeto (harness.project.yaml)")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--evidence", type=Path, required=True, help="pasta de saída da evidência")
    parser.add_argument("--cwd", type=Path, help="pasta onde a ferramenta roda (padrão: raiz)")
    parser.add_argument("--validate", action="store_true", help="só revalida a evidência existente")
    args = parser.parse_args(argv)
    if args.validate:
        errors = validate_visual_evidence(args.evidence / EVIDENCE_FILE)
        for error in errors:
            print(f"visual-review: FAIL: {error}")
        print("visual-review: PASS" if not errors else "", end="\n" if not errors else "")
        return 1 if errors else 0
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        config = load_config(args.root.resolve(), required=True)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"visual-review: erro de configuração: {exc}", file=sys.stderr)
        return 2
    if not isinstance(plan, dict):
        print("visual-review: plano precisa ser mapa", file=sys.stderr)
        return 2
    result = run_visual_review(
        plan, config, feature_id=args.feature, cwd=(args.cwd or args.root).resolve(), evidence_dir=args.evidence
    )
    print(f"visual-review: {result.status}")
    for error in result.errors:
        print(f"visual-review: {error}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
