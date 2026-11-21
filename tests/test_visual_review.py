"""Onda 3: revisão visual automatizada com evidência verificável (ferramentas fake, sem navegador real)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.dashboard import status_payload
from script.events import read_events
from script.pipeline import finish_stage, replay, start_stage
from script.stage_runner import run_configured_stage
from script.testing_policy import resolve_feature_gates, stage_policy
from script.visual_review import (
    load_evidence_summaries,
    run_visual_review,
    sanitize_target,
    validate_visual_evidence,
)

TOOL = """import json, sys, time
from pathlib import Path
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
mode = Path(args['--mode-file']).read_text().strip() if '--mode-file' in args else 'ok'
if mode == 'sleep':
    time.sleep(5)
if mode == 'crash':
    raise SystemExit(3)
if mode != 'no-shot':
    Path(args['--shot']).write_bytes(b'\\x89PNG fake ' + args['--criterion'].encode())
if mode != 'no-report':
    Path(args['--report']).write_text(json.dumps({
        'criterion': args['--criterion'],
        'found': mode != 'not-found',
        'summary': 'painel renderizado sem overflow',
    }))
"""


def visual_plan(*, domain: bool = False, criterion_layer: str = "visual") -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "feature": "E1-F01",
        "scope": "Ajustar espaçamento do painel",
        "contract": "O painel corresponde ao layout aprovado",
        "sources": [{
            "file": "src/Panel.tsx",
            "classification": "dispensa-estilo",
            "reason": "Mudança exclusivamente visual",
            "domain": domain,
        }],
        "criteria": [{
            "id": "A1",
            "statement": "O painel corresponde ao layout aprovado",
            "source": "Figma",
            "layers": [criterion_layer],
        }],
        "scenarios": [{
            "id": "C1",
            "criterion": "A1",
            "layer": "visual" if criterion_layer == "visual" else "unit",
            "file": "src/Panel.tsx" if criterion_layer == "visual" else "tests/panel.test.ts",
            "input": "Viewport 1440px",
            "expected": "Sem overflow",
            "source": "Figma",
        }],
        "tests": [] if criterion_layer == "visual" else ["tests/panel.test.ts"],
        "e2e": [],
    }


def tdd_plan() -> dict[str, object]:
    plan = visual_plan(criterion_layer="unit")
    plan["sources"] = [{"file": "src/rules.ts", "classification": "tdd", "domain": True}]
    return plan


def tool_config(tmp_path: Path, *, timeout_s: int = 10, mode: str = "ok") -> dict[str, object]:
    tool = tmp_path / "fake_browser.py"
    tool.write_text(TOOL, encoding="utf-8")
    mode_file = tmp_path / "mode.txt"
    mode_file.write_text(mode, encoding="utf-8")
    return {
        "visual_review": {
            "command": (
                f"{sys.executable} {tool} --target {{target}} --criterion {{criterion}} "
                f"--shot {{screenshot}} --report {{report}} --mode-file {mode_file}"
            ),
            "target": "http://user:secret@localhost:5173/painel?token=abc#frag",
            "timeout_s": timeout_s,
        }
    }


def review(tmp_path: Path, plan: dict[str, object], config: dict[str, object]):
    return run_visual_review(plan, config, feature_id="E1-F01", cwd=tmp_path, evidence_dir=tmp_path / "ev")


def test_visual_feature_skips_tdd_but_requires_visual_review() -> None:
    gates = resolve_feature_gates(visual_plan())
    policy = stage_policy(gates)
    assert gates["visual_review"] is True
    assert policy["tests"] is False and policy["testReview"] is False


def test_visual_review_passes_with_hashed_evidence_and_sanitized_target(tmp_path: Path) -> None:
    result = review(tmp_path, visual_plan(), tool_config(tmp_path))
    assert result.status == "PASS"
    assert result.evidence is not None
    data = json.loads(result.evidence.read_text(encoding="utf-8"))
    assert data["target"] == "http://localhost:5173/painel"
    serialized = json.dumps(data)
    assert "secret" not in serialized and "token=abc" not in serialized
    entry = data["criteria"][0]
    assert entry["criterion"] == "A1" and entry["status"] == "PASS"
    assert len(entry["screenshot_sha256"]) == 64
    assert validate_visual_evidence(result.evidence) == []


def test_tampered_screenshot_invalidates_evidence(tmp_path: Path) -> None:
    result = review(tmp_path, visual_plan(), tool_config(tmp_path))
    assert result.evidence is not None
    (result.evidence.parent / "A1.png").write_bytes(b"outra imagem")
    assert any("screenshot_sha256" in error for error in validate_visual_evidence(result.evidence))


def test_non_visual_feature_does_not_run_review(tmp_path: Path) -> None:
    result = review(tmp_path, tdd_plan(), {})
    assert result.status == "NOT_APPLICABLE"
    assert not (tmp_path / "ev").exists()


def test_domain_logic_in_visual_source_fails(tmp_path: Path) -> None:
    result = review(tmp_path, visual_plan(domain=True), tool_config(tmp_path))
    assert result.status == "FAIL"
    assert any("lógica de domínio" in error for error in result.errors)
    assert not (tmp_path / "ev" / "A1.png").exists()


def test_visual_classification_without_visual_criterion_fails(tmp_path: Path) -> None:
    plan = visual_plan(criterion_layer="unit")
    result = review(tmp_path, plan, tool_config(tmp_path))
    assert result.status == "FAIL"
    assert any("sem critério com layer visual" in error for error in result.errors)


def test_missing_tool_config_fails_explicitly(tmp_path: Path) -> None:
    result = review(tmp_path, visual_plan(), {})
    assert result.status == "FAIL"
    assert result.evidence is not None
    assert json.loads(result.evidence.read_text(encoding="utf-8"))["status"] == "FAIL"


def test_missing_executable_fails(tmp_path: Path) -> None:
    config = {"visual_review": {
        "command": "ferramenta-inexistente-xyz {screenshot} {report}",
        "target": "http://localhost:1",
    }}
    result = review(tmp_path, visual_plan(), config)
    assert result.status == "FAIL"
    assert any("não encontrada" in error for error in result.errors)


def test_timeout_missing_screenshot_and_not_found_fail(tmp_path: Path) -> None:
    for mode, message in (
        ("sleep", "timeout"),
        ("no-shot", "screenshot ausente"),
        ("no-report", "relatório do critério ausente"),
        ("not-found", "não encontrado"),
        ("crash", "saiu com 3"),
    ):
        case = tmp_path / mode
        case.mkdir()
        result = review(case, visual_plan(), tool_config(case, timeout_s=1, mode=mode))
        assert result.status == "FAIL", mode
        assert any(message in error for error in result.errors), (mode, result.errors)
        assert result.evidence is not None
        assert validate_visual_evidence(result.evidence) == []


def test_sanitize_target_removes_credentials_and_query() -> None:
    assert sanitize_target("https://a:b@example.invalid:8443/x?y=1#z") == "https://example.invalid:8443/x"


def test_dashboard_reads_visual_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / ".harness" / "runs" / "run-1" / "E1-F01" / "evidence" / "finalReview" / "1" / "visual-review"
    run_visual_review(visual_plan(), tool_config(tmp_path), feature_id="E1-F01", cwd=tmp_path, evidence_dir=evidence)
    summaries = load_evidence_summaries(evidence.parents[2])
    assert summaries[0]["status"] == "PASS" and summaries[0]["validation_errors"] == []
    event_path = tmp_path / ".harness" / "runs" / "run-1" / "E1-F01" / "events.jsonl"
    start_stage(event_path, "run-1", "E1-F01", "plan")
    (tmp_path / "harness.project.yaml").write_text("cards_glob: docs/plano/*.yaml\n", encoding="utf-8")
    payload = status_payload(tmp_path)
    run = payload["runs"][0]
    assert isinstance(run, dict)
    assert run["visual_reviews"][0]["criteria"][0]["criterion"] == "A1"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def test_final_review_stage_requires_visual_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "harness@example.invalid")
    git(repo, "config", "user.name", "Harness Test")
    worker = repo / "fake_worker.py"
    worker.write_text(
        "from pathlib import Path\nPath('src').mkdir(exist_ok=True)\n"
        "Path('src/review.txt').write_text('ok\\n', encoding='utf-8')\nprint('done')\n",
        encoding="utf-8",
    )
    (repo / "request.md").write_text("Revisar", encoding="utf-8")
    (repo / "docs" / "plano" / "E1-F01").mkdir(parents=True)
    (repo / "docs" / "plano" / "E1-F01" / "plan.json").write_text(json.dumps(visual_plan()), encoding="utf-8")
    (repo / "docs" / "plano" / "E1-F01.yaml").write_text(
        """cards:
  - schemaVersion: 2
    id: E1-F01-T01
    title: Review
    kind: implement
    allowedPaths: [src/review.txt]
    generatedPaths: [src/review.txt]
    checks: [python3 -c pass]
    dor: [approved]
    dod: [reviewed]
""",
        encoding="utf-8",
    )
    config = tool_config(tmp_path, mode="no-shot")
    visual = config["visual_review"]
    assert isinstance(visual, dict)
    (repo / "harness.project.yaml").write_text(
        f"""cards_glob: docs/plano/E*.yaml
roles:
  reviewer: {{agent: reviewer.md, worker: fake}}
workers:
  fake:
    adapter: cli-agent
    command: {sys.executable}
    read_args: {worker}
    write_args: {worker}
    roles: reviewer
    timeout_s: 30
commands:
  verify: {sys.executable} -c "raise SystemExit(0)"
visual_review:
  command: {json.dumps(visual["command"])}
  target: http://localhost:5173/painel
  timeout_s: 10
""",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    event_path = repo / ".harness" / "runs" / "run-1" / "E1-F01" / "events.jsonl"
    for stage in ("plan", "refinement", "tests", "testReview", "implement", "mutation_e2e"):
        start_stage(event_path, "run-1", "E1-F01", stage)
        finish_stage(event_path, "run-1", "E1-F01", stage, passed=True)

    def run() -> bool:
        return run_configured_stage(
            repo, run_id="run-1", feature_id="E1-F01", stage="finalReview", role="reviewer",
            request=repo / "request.md", gate_name="verify", allowed_paths=["src/review.txt"],
        ).passed

    assert run() is False
    evidence = repo / ".harness" / "runs" / "run-1" / "E1-F01" / "evidence" / "finalReview" / "1" / "visual-review"
    assert json.loads((evidence / "visual-review.json").read_text(encoding="utf-8"))["status"] == "FAIL"
    assert replay("run-1", "E1-F01", read_events(event_path))[
        "current_stage"
    ] == "finalReview"

    (tmp_path / "mode.txt").write_text("ok", encoding="utf-8")
    assert run() is True
    state = replay("run-1", "E1-F01", read_events(event_path))
    assert state["current_stage"] == "closing"
    passed = repo / ".harness" / "runs" / "run-1" / "E1-F01" / "evidence" / "finalReview" / "2" / "visual-review"
    assert validate_visual_evidence(passed / "visual-review.json") == []
