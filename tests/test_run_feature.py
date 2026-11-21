"""Testes do driver de feature ponta a ponta (Onda 5) com worker fake e Git temporário.

Nenhum modelo real é chamado: o worker é um script Python local que lê o request no stdin, descobre a stage pelo
cabeçalho `# <feature> — stage <stage>` e age conforme um arquivo JSON de modos.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.events import read_events
from script.feature_contract import build_contract, lint_contract, load_contract_config, ContractError
from script.pipeline import replay
from script.project_config import load_config
from script.run_feature import main
from script.sdd import approve_document

FEATURE = "E1-F01"
RUN = "run-1"

WORKER = r'''
import json, re, sys
from pathlib import Path

args = sys.argv[1:]
name = args[args.index("--name") + 1]
control = Path(args[args.index("--control") + 1])
prompt = sys.stdin.read()
match = re.search(r"^# (\S+) — stage (\w+)", prompt, re.M)
stage = match.group(2) if match else "?"
modes = json.loads((control / "modes.json").read_text(encoding="utf-8"))
mode = modes.get(f"{stage}@{name}", modes.get(stage, modes.get("default", "ok")))
with (control / "calls.log").open("a", encoding="utf-8") as log:
    log.write(f"{stage} {name} {mode}\n")
if stage == "plan":
    if mode == "drafts":
        Path("plan.json").write_text((control / "plan.json").read_text(encoding="utf-8"), encoding="utf-8")
        Path("cards.yaml").write_text((control / "cards.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    elif mode == "questions":
        Path("questions.json").write_text(json.dumps({"questions": ["Qual o limite de registros?"]}), encoding="utf-8")
    elif mode == "outside":
        Path(modes["root"], "stray.txt").write_text("x", encoding="utf-8")
    elif mode == "invalid":
        Path("plan.json").write_text("{}", encoding="utf-8")
        Path("cards.yaml").write_text("cards: []\n", encoding="utf-8")
    print("planned")
    raise SystemExit(0)
if mode == "fail":
    print("falhou de propósito")
    raise SystemExit(1)
if mode == "outside":
    Path("outside.txt").write_text("fora do escopo\n", encoding="utf-8")
    print("escreveu fora")
    raise SystemExit(0)
scope = prompt.split("## Escopo permitido desta stage", 1)[1].split("##", 1)[0]
for target in [line[2:].strip() for line in scope.splitlines() if line.startswith("- ")]:
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    Path(target).write_text(f"{stage} by {name}\n", encoding="utf-8")
print(f"{stage} ok")
'''

SDD_DOCS: dict[str, str] = {
    "prd": "## Problema\nP\n## Requisitos funcionais\nRF-01 filtrar\n## Requisitos não funcionais\nN\n"
           "## Critérios de aceite\nA1 correspondências",
    "adr": "## Contexto\nC\n## Decisão\nD\n## Consequências\nS",
    "rfc": "## Desenho\nD\n## Interfaces\nI\n## Testes\nT\n## Rollback\nR",
}

PLAN: dict[str, object] = {
    "schemaVersion": 2,
    "feature": FEATURE,
    "scope": "Filtrar registros",
    "contract": "Retorna apenas registros correspondentes",
    "sources": [{"file": "src/filter.py", "classification": "tdd", "domain": True}],
    "criteria": [{
        "id": "A1",
        "statement": "Correspondências são retornadas",
        "source": "PRD RF-01",
        "layers": ["unit"],
    }],
    "scenarios": [{
        "id": "C1",
        "criterion": "A1",
        "layer": "unit",
        "file": "tests/test_filter.py",
        "input": "termo",
        "expected": "registros filtrados",
        "source": "PRD RF-01",
    }],
    "tests": ["tests/test_filter.py"],
    "e2e": [],
}

CARDS = """cards:
  - schemaVersion: 2
    id: E1-F01-T01
    title: Write filter tests
    kind: test-author
    allowedPaths: [tests/test_filter.py]
    generatedPaths: [tests/test_filter.py]
    checks: [python -m pytest]
    dor: [approved feature contract]
    dod: [tests fail before implementation]
  - schemaVersion: 2
    id: E1-F01-T02
    title: Implement filtering
    kind: implement
    dependsOn: [E1-F01-T01]
    allowedPaths: [src/filter.py]
    generatedPaths: [src/filter.py]
    checks: [python -m pytest]
    dor: [approved feature contract]
    dod: [unit tests pass]
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def config_text(worker: Path, control: Path) -> str:
    def args(name: str) -> str:
        return f"{worker} --name {name} --control {control}"

    stage_lines = {
        "refinement": ("refiner", "write", None, "docs/plano/{feature}/refinement.md"),
        "tests": ("test-author", "write", "test-author", "docs/plano/{feature}/tests-notes.md"),
        "testReview": ("reviewer", "write", None, "docs/plano/{feature}/test-review.md"),
        "implement": ("implementer", "write", "implement", "docs/plano/{feature}/implement-notes.md"),
        "mutation_e2e": ("reviewer", "write", None, "docs/plano/{feature}/mutation.md"),
        "finalReview": ("reviewer", "write", None, "docs/plano/{feature}/final-review.md"),
        "closing": ("closer", "write", None, "docs/plano/{feature}/closing.md"),
    }
    stages = []
    for stage, (role, mode, cards, path) in stage_lines.items():
        cards_line = f"\n      cards: {cards}" if cards else ""
        stages.append(
            f"    {stage}:\n      role: {role}\n      gate: verify\n      mode: {mode}{cards_line}\n"
            f"      allowedPaths: [\"{path}\"]"
        )
    return f"""cards_glob: docs/plano/E*.yaml
docs_dir: docs
roles:
  planner: {{agent: planner.md, worker: fake}}
  refiner: {{agent: refiner.md, worker: fake}}
  test-author: {{agent: test-author.md, worker: fake}}
  reviewer: {{agent: reviewer.md, worker: fake}}
  implementer: {{agent: implementer.md, worker: fake, fallbacks: [fake2]}}
  closer: {{agent: closer.md, worker: fake}}
workers:
  fake:
    adapter: cli-agent
    command: {sys.executable}
    read_args: {args("fake")}
    write_args: {args("fake")}
    timeout_s: 30
  fake2:
    adapter: cli-agent
    command: {sys.executable}
    read_args: {args("fake2")}
    write_args: {args("fake2")}
    timeout_s: 30
commands:
  verify: {sys.executable} -c "raise SystemExit(0)"
feature_contract:
  sdd_dir: docs/sdd
  planner: {{role: planner}}
  stages:
""" + "\n".join(stages) + "\n"


@pytest.fixture()
def project(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "project"
    control = tmp_path / "control"
    repo.mkdir()
    control.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "harness@example.invalid")
    git(repo, "config", "user.name", "Harness Test")
    worker = tmp_path / "fake_worker.py"
    worker.write_text(WORKER, encoding="utf-8")
    (control / "plan.json").write_text(json.dumps(PLAN, indent=2), encoding="utf-8")
    (control / "cards.yaml").write_text(CARDS, encoding="utf-8")
    set_modes(control, {"plan": "drafts", "default": "ok", "root": str(repo)})
    sdd = repo / "docs" / "sdd" / FEATURE
    sdd.mkdir(parents=True)
    for kind, body in SDD_DOCS.items():
        (sdd / f"{kind}.md").write_text(body, encoding="utf-8")
        approve_document(sdd / f"{kind}.approval.json", sdd / f"{kind}.md", kind=kind, reviewer="human")
    (repo / "harness.project.yaml").write_text(config_text(worker, control), encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo, control


def set_modes(control: Path, modes: dict[str, str]) -> None:
    (control / "modes.json").write_text(json.dumps(modes), encoding="utf-8")


def calls(control: Path) -> list[str]:
    log = control / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def run(repo: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, dict[str, object]]:
    code = main([str(repo), "--run", RUN, "--feature", FEATURE, *extra])
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    return code, json.loads(lines[-1])


def staging(repo: Path, attempt: int = 1) -> Path:
    return repo / ".harness" / "runs" / RUN / FEATURE / "staging" / str(attempt)


def approve_drafts(repo: Path, attempt: int = 1) -> None:
    folder = staging(repo, attempt)
    approve_document(folder / "plan.approval.json", folder / "plan.json", kind="plan", reviewer="tech-lead")
    approve_document(folder / "cards.approval.json", folder / "cards.yaml", kind="plan", reviewer="tech-lead")


def state(repo: Path) -> dict[str, object]:
    return replay(RUN, FEATURE, read_events(repo / ".harness" / "runs" / RUN / FEATURE / "events.jsonl"))


def to_contract(repo: Path, capsys: pytest.CaptureFixture[str], control: Path) -> None:
    """Gera rascunhos, aprova e para com refinement em FAIL (contrato publicado, stage seguinte pendente)."""
    set_modes(control, {"plan": "drafts", "refinement": "fail", "default": "ok", "root": str(repo)})
    assert run(repo, capsys)[0] == 3
    approve_drafts(repo)
    code, summary = run(repo, capsys)
    assert code == 1 and summary["stage"] == "refinement"


def test_full_feature_runs_with_one_command_and_only_the_approval_gate(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, control = project
    head = git(repo, "rev-parse", "HEAD")

    code, summary = run(repo, capsys)
    assert code == 3 and summary["status"] == "needs_input" and "sdd.py approve plan" in str(summary["reason"])
    assert calls(control) == ["plan fake drafts"]

    # Rodar de novo sem aprovar não chama o planner outra vez.
    assert run(repo, capsys)[0] == 3
    assert len(calls(control)) == 1

    approve_drafts(repo)
    code, summary = run(repo, capsys)
    assert code == 0, summary
    assert summary["status"] == "done"
    # Plano só unitário, sem e2e/mutação: a política pula mutation_e2e sem chamar worker.
    assert [line.split()[0] for line in calls(control)] == [
        "plan", "refinement", "tests", "testReview", "implement", "finalReview", "closing",
    ]
    final = state(repo)
    assert final["current_stage"] is None
    stages = final["stages"]
    assert isinstance(stages, dict)
    assert stages["mutation_e2e"]["status"] == "skipped"
    assert all(
        isinstance(entry, dict) and entry["status"] == "done"
        for name, entry in stages.items() if name != "mutation_e2e"
    )
    # Nada mesclado: a raiz continua no commit base; o resultado está na última branch harness/*.
    assert git(repo, "rev-parse", "HEAD") == head
    branch = str(summary["branch"])
    assert branch == f"harness/{FEATURE}/{RUN}/closing/1"
    assert git(repo, "rev-parse", branch) == summary["commit_sha"]
    files = git(repo, "ls-tree", "-r", "--name-only", branch).splitlines()
    for path in ("docs/plano/E1-F01/plan.json", "docs/plano/E1-F01.yaml", "docs/plano/E1-F01/contract.json",
                 "tests/test_filter.py", "src/filter.py", "docs/plano/E1-F01/closing.md"):
        assert path in files
    # Cadeia de commits: cada stage parte do commit da anterior.
    log = git(repo, "log", "--format=%s", branch).splitlines()
    assert len(log) >= 8
    contract = json.loads(git(repo, "show", f"{branch}:docs/plano/E1-F01/contract.json"))
    assert lint_contract(contract, load_config(repo, required=True)) == []
    implement = next(entry for entry in contract["stages"] if entry["stage"] == "implement")
    assert implement["cards"] == ["E1-F01-T02"] and "src/filter.py" in implement["allowedPaths"]
    assert [entry["id"] for entry in contract["cardOrder"]] == ["E1-F01-T01", "E1-F01-T02"]

    # Retomar uma feature concluída não repete stage nem chama worker.
    before = len(calls(control))
    code, summary = run(repo, capsys)
    assert code == 0 and len(calls(control)) == before


def test_open_questions_stop_as_needs_input_without_publishing(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, control = project
    set_modes(control, {"plan": "questions", "root": str(repo)})
    code, summary = run(repo, capsys)
    assert code == 3 and "questão" in str(summary["reason"])
    assert run(repo, capsys)[0] == 3
    assert len(calls(control)) == 1
    assert "harness/" not in git(repo, "branch", "--list", "harness/*")

    # O humano responde no SDD e reaprova: a nova geração acontece (hash do SDD mudou).
    sdd = repo / "docs" / "sdd" / FEATURE
    (sdd / "prd.md").write_text(SDD_DOCS["prd"] + "\nLimite: 100 registros.", encoding="utf-8")
    approve_document(sdd / "prd.approval.json", sdd / "prd.md", kind="prd", reviewer="human")
    git(repo, "commit", "-am", "responde questões")
    set_modes(control, {"plan": "drafts", "default": "ok", "root": str(repo)})
    assert run(repo, capsys)[0] == 3
    assert calls(control)[-1] == "plan fake drafts"
    assert staging(repo, 2).is_dir()


def test_unapproved_sdd_blocks_before_planner(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, control = project
    (repo / "docs" / "sdd" / FEATURE / "adr.approval.json").unlink()
    git(repo, "commit", "-am", "remove aprovação")
    code, summary = run(repo, capsys)
    assert code == 1 and "SDD não aprovada" in str(summary["reason"])
    assert calls(control) == []


@pytest.mark.parametrize("mode, reason", [("outside", "fora do staging"), ("invalid", "rascunhos inválidos")])
def test_planner_misbehaviour_fails_plan(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str], mode: str, reason: str
) -> None:
    repo, control = project
    set_modes(control, {"plan": mode, "root": str(repo)})
    code, summary = run(repo, capsys)
    assert code == 1 and reason in str(summary["reason"])
    plan = state(repo)["stages"]
    assert isinstance(plan, dict) and plan["plan"]["status"] == "failed"
    assert "harness/" not in git(repo, "branch", "--list", "harness/*")


def test_stage_failure_blocks_later_stages_and_resume_does_not_repeat(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, control = project
    to_contract(repo, capsys, control)
    assert [line.split()[0] for line in calls(control)] == ["plan", "refinement"]

    # Sem --retry-failed não roda nada.
    code, summary = run(repo, capsys)
    assert code == 1 and "--retry-failed" in str(summary["reason"])
    assert len(calls(control)) == 2

    set_modes(control, {"default": "ok", "root": str(repo)})
    code, summary = run(repo, capsys, "--retry-failed")
    assert code == 0, summary
    assert [line.split()[0] for line in calls(control)] == [
        "plan", "refinement", "refinement", "tests", "testReview", "implement", "finalReview", "closing",
    ]
    assert summary["branch"] == f"harness/{FEATURE}/{RUN}/closing/1"
    attempts = state(repo)["attempts"]
    assert isinstance(attempts, dict) and attempts["refinement"] == 2


@pytest.mark.parametrize("tamper, reason", [
    ("sdd", "SDD prd mudou"),
    ("config", "harness.project.yaml mudou"),
    ("request", "request de refinement mudou"),
])
def test_hash_changes_block_before_any_worker(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str], tamper: str, reason: str
) -> None:
    repo, control = project
    to_contract(repo, capsys, control)
    set_modes(control, {"default": "ok", "root": str(repo)})
    before = len(calls(control))
    if tamper == "sdd":
        prd = repo / "docs" / "sdd" / FEATURE / "prd.md"
        prd.write_text(prd.read_text(encoding="utf-8") + "\nmudança", encoding="utf-8")
    elif tamper == "config":
        config = repo / "harness.project.yaml"
        config.write_text(config.read_text(encoding="utf-8") + "# comentário\n", encoding="utf-8")
    else:
        request = repo / ".harness" / "runs" / RUN / FEATURE / "requests" / "refinement.md"
        request.write_text(request.read_text(encoding="utf-8") + "\nfaça outra coisa\n", encoding="utf-8")
    code, summary = run(repo, capsys, "--retry-failed")
    assert code == 1 and reason in str(summary["reason"])
    assert len(calls(control)) == before


def test_contract_tampered_in_event_log_is_rejected(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, control = project
    to_contract(repo, capsys, control)
    events = repo / ".harness" / "runs" / RUN / FEATURE / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    patched = []
    for line in lines:
        event = json.loads(line)
        if event.get("event") == "contract_published":
            event["sha256"] = "0" * 64
        patched.append(json.dumps(event))
    events.write_text("\n".join(patched) + "\n", encoding="utf-8")
    before = len(calls(control))
    set_modes(control, {"default": "ok", "root": str(repo)})
    code, summary = run(repo, capsys, "--retry-failed")
    assert code == 1 and "hash do contrato" in str(summary["reason"])
    assert len(calls(control)) == before


def test_worker_failure_uses_configured_fallback(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, control = project
    set_modes(control, {"plan": "drafts", "implement@fake": "fail", "default": "ok", "root": str(repo)})
    assert run(repo, capsys)[0] == 3
    approve_drafts(repo)
    code, summary = run(repo, capsys)
    assert code == 0, summary
    assert [line for line in calls(control) if line.startswith("implement")] == [
        "implement fake fail", "implement fake2 ok",
    ]
    branch = str(summary["branch"])
    assert git(repo, "show", f"{branch}:src/filter.py") == "implement by fake2"
    events = read_events(repo / ".harness" / "runs" / RUN / FEATURE / "events.jsonl")
    assert any(event.get("event") == "fallback" and event.get("stage") == "implement" for event in events)


def test_write_outside_allowed_paths_blocks_and_discards_attempt(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, control = project
    set_modes(control, {"plan": "drafts", "implement": "outside", "default": "ok", "root": str(repo)})
    assert run(repo, capsys)[0] == 3
    approve_drafts(repo)
    code, summary = run(repo, capsys)
    assert code == 1 and summary["stage"] == "implement"
    assert "fora de allowedPaths: outside.txt" in str(summary["reason"])
    # Violação de escopo não é falha recuperável: não aciona fallback nem stages seguintes.
    assert [line for line in calls(control) if line.startswith("implement")] == ["implement fake outside"]
    later = [line for line in calls(control) if line.split()[0] in {"finalReview", "closing"}]
    assert later == []
    assert git(repo, "branch", "--list", "harness/*/implement/*") == ""
    assert "outside.txt" not in git(repo, "ls-files")
    assert not (repo / "outside.txt").exists()


def test_lint_contract_rejects_tampered_fields(
    project: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, control = project
    set_modes(control, {"plan": "drafts", "refinement": "fail", "default": "ok", "root": str(repo)})
    to_contract(repo, capsys, control)
    branch = f"harness/{FEATURE}/{RUN}/plan/2"
    contract = json.loads(git(repo, "show", f"{branch}:docs/plano/E1-F01/contract.json"))
    config = load_config(repo, required=True)
    assert lint_contract(contract, config) == []

    def mutated(change: str) -> dict[str, object]:
        data = copy.deepcopy(contract)
        stage = data["stages"][3]
        if change == "chain":
            stage["executorChain"] = ["fake"]
        elif change == "path":
            stage["allowedPaths"] = ["../fora"]
        elif change == "empty":
            stage["allowedPaths"] = []
        elif change == "gate":
            stage["gate"] = "inexistente"
        elif change == "card":
            stage["cards"] = ["E9-F09-T01"]
        elif change == "order":
            data["stages"] = list(reversed(data["stages"]))
        elif change == "cycle":
            data["cardOrder"][0]["dependsOn"] = ["E1-F01-T02"]
        elif change == "extra":
            data["comando"] = "rm -rf /"
        return data

    for change, fragment in [
        ("chain", "executorChain diverge"),
        ("path", "caminho inseguro"),
        ("empty", "vazio em stage aplicável"),
        ("gate", "commands.inexistente"),
        ("card", "cartão desconhecido"),
        ("order", "ordem/conjunto"),
        ("cycle", "ciclo"),
        ("extra", "campos desconhecidos"),
    ]:
        errors = lint_contract(mutated(change), config)
        assert any(fragment in error for error in errors), (change, errors)


def test_contract_config_requires_every_stage(project: tuple[Path, Path]) -> None:
    repo, _ = project
    config = load_config(repo, required=True)
    section = config["feature_contract"]
    assert isinstance(section, dict) and isinstance(section["stages"], dict)
    del section["stages"]["closing"]
    with pytest.raises(ContractError, match="closing ausente"):
        load_contract_config(config, FEATURE)


def test_build_contract_is_deterministic(project: tuple[Path, Path], tmp_path: Path) -> None:
    repo, control = project
    plan_dir = repo / "docs" / "plano" / FEATURE
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.json").write_text((control / "plan.json").read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "docs" / "plano" / f"{FEATURE}.yaml").write_text(CARDS, encoding="utf-8")
    config = load_config(repo, required=True)
    kwargs = {
        "feature_id": FEATURE,
        "config": config,
        "plan_path": plan_dir / "plan.json",
        "cards_path": repo / "docs" / "plano" / f"{FEATURE}.yaml",
        "request_dir": Path(".harness/requests"),
    }
    first = build_contract(repo, **kwargs)  # type: ignore[arg-type]
    second = build_contract(repo, **kwargs)  # type: ignore[arg-type]
    assert first == second
    tests_stage = next(entry for entry in first[0]["stages"] if entry["stage"] == "tests")  # type: ignore[union-attr]
    assert tests_stage["cards"] == ["E1-F01-T01"]


def test_build_contract_adds_stub_paths_from_implement_cards(project: tuple[Path, Path]) -> None:
    repo, control = project
    plan_dir = repo / "docs" / "plano" / FEATURE
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.json").write_text((control / "plan.json").read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "docs" / "plano" / f"{FEATURE}.yaml").write_text(CARDS, encoding="utf-8")
    config = load_config(repo, required=True)
    section = config["feature_contract"]
    assert isinstance(section, dict) and isinstance(section["stages"], dict)
    section["stages"]["tests"]["stubsFrom"] = "implement"
    contract, _ = build_contract(
        repo,
        feature_id=FEATURE,
        config=config,
        plan_path=plan_dir / "plan.json",
        cards_path=repo / "docs" / "plano" / f"{FEATURE}.yaml",
        request_dir=Path(".harness/requests"),
    )
    tests_stage = next(entry for entry in contract["stages"] if entry["stage"] == "tests")  # type: ignore[union-attr]
    assert tests_stage["stubPaths"] == ["src/filter.py"]
    assert "src/filter.py" in tests_stage["allowedPaths"]
    assert lint_contract(contract, config) == []
