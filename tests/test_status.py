"""Testes do comando status (`script/status.py`)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.status import compute, current_step, parse_pm_queue, satisfied_steps  # noqa: E402

CARDS = """\
delivery: X1
cards:
  - {id: X1-T01, kind: test-author, dependsOn: []}
  - {id: X1-T02, kind: implement, dependsOn: [X1-T01]}
  - {id: X1-T03, kind: implement, dependsOn: [X1-T02]}
features:
  - {id: X1-F01, title: First, cards: [X1-T01, X1-T02]}
  - {id: X1-F02, title: Second, cards: [X1-T03]}
"""


def write_state(project: Path, feature: str, stages: dict[str, object], extra: dict[str, object] | None = None) -> None:
    folder = project / "plano" / feature
    folder.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {"feature": feature, "stages": stages, **(extra or {})}
    (folder / "estado.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "harness.project.yaml").write_text(
        "cards_glob: plano/X*.yaml\npm_queue: plano/pm.md\n", encoding="utf-8"
    )
    (tmp_path / "plano").mkdir()
    (tmp_path / "plano" / "X1.yaml").write_text(CARDS, encoding="utf-8")
    (tmp_path / "plano" / "pm.md").write_text("# fila\n\n## Entradas\n\nNenhuma.\n", encoding="utf-8")
    return tmp_path


def feature(status: dict[str, object], fid: str) -> dict[str, object]:
    rows = status["features"]
    assert isinstance(rows, list)
    return next(r for r in rows if r["id"] == fid)


def test_feature_without_state_is_queued_at_plan(project: Path) -> None:
    status = compute(project)
    row = feature(status, "X1-F01")
    assert row["step"] == "plan"
    assert row["has_state"] is False
    assert row["percent"] == 0


def test_steps_follow_state_and_gates(project: Path) -> None:
    write_state(project, "X1-F01", {"plan": {"status": "done"}, "tests": {"status": "done"}})
    assert feature(compute(project), "X1-F01")["step"] == "test_review"
    write_state(
        project,
        "X1-F01",
        {"plan": {"status": "done"}, "refinement": {"status": "done"}, "tests": {"status": "done"}},
    )
    assert feature(compute(project), "X1-F01")["step"] == "test_review"
    write_state(
        project,
        "X1-F01",
        {
            "plan": {"status": "done"},
            "refinement": {"status": "done"},
            "tests": {"status": "done"},
            "testReview": {"status": "done"},
            "implement": {"status": "done"},
        },
        {"gates": {"mutation": {"status": "PASS"}}},
    )
    assert feature(compute(project), "X1-F01")["step"] == "mutation_e2e"


def test_legacy_state_skips_steps_that_did_not_exist(project: Path) -> None:
    write_state(
        project,
        "X1-F01",
        {
            "plan": {"status": "done"},
            "tests": {"status": "done"},
            "implement": {"status": "done"},
            "audit": {"status": "done", "verdict": "aprovado"},
            "review": {"status": "ready"},
        },
    )
    assert current_step(json.loads((project / "plano/X1-F01/estado.json").read_text())) == "closing"


def test_done_feature_is_100_percent(project: Path) -> None:
    stages = {
        name: {"status": "done"} for name in ("plan", "refinement", "tests", "testReview", "implement")
    }
    stages["review"] = {"status": "done", "verdict": "aprovado"}
    gates = {"mutation": {"status": "PASS"}, "e2e": {"status": "PASS"}}
    write_state(project, "X1-F01", stages, {"gates": gates})
    row = feature(compute(project), "X1-F01")
    assert (row["step"], row["percent"], row["ready"]) == ("done", 100, False)


def test_blocked_status_and_cards(project: Path) -> None:
    write_state(
        project,
        "X1-F01",
        {"plan": {"status": "done"}, "tests": {"status": "done"}, "implement": {"status": "quota"}},
    )
    status = compute(project)
    row = feature(status, "X1-F01")
    assert row["blocked"] == {"stage": "implement", "step": "implement", "status": "quota", "message": None}
    states = {str(c["id"]): c["state"] for c in status["cards"]}  # type: ignore[union-attr]
    assert states == {"X1-T01": "done", "X1-T02": "blocked", "X1-T03": "waiting"}
    assert status["blocked_by_status"] == {"quota": ["X1-F01"]}
    queue = status["queue"]
    assert queue["implement"]["blocked"] == ["X1-F01"]  # type: ignore[index]


def test_open_pm_entry_blocks_feature(project: Path) -> None:
    (project / "plano" / "pm.md").write_text(
        "## X1-T01 (X1-F01)\n- status: aberta\n- pergunta: x\n", encoding="utf-8"
    )
    write_state(project, "X1-F01", {"plan": {"status": "done"}})
    status = compute(project)
    assert feature(status, "X1-F01")["blocked"]["status"] == "needs_input"  # type: ignore[index]
    assert status["pm_queue"]["open"] == 1  # type: ignore[index]


def test_delivery_percent_is_mean_of_features(project: Path) -> None:
    status = compute(project)
    deliveries = status["deliveries"]
    assert deliveries == [{"id": "X1", "features": 2, "done": 0, "blocked": 0, "percent": 0}]


def test_parse_pm_queue_ignores_fenced_template() -> None:
    text = "```md\n## <ID> (<F>)\n- status: aberta\n```\n## A-T1 (A-F1)\n- status: resolvida\n"
    assert parse_pm_queue(text) == [{"card": "A-T1", "feature": "A-F1", "status": "resolvida"}]


def test_satisfied_steps_without_state_is_all_false() -> None:
    assert not any(satisfied_steps(None).values())


def test_cli_writes_file(project: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "script" / "status.py"), str(project), "--out", str(out), "--print"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text())["features"]
    assert "Entregas" in proc.stdout


def test_missing_cards_glob_exits_2(tmp_path: Path) -> None:
    (tmp_path / "harness.project.yaml").write_text("harness: x\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "script" / "status.py"), str(tmp_path)], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 2
