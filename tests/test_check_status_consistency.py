"""Testes do gate status-consistency (`script/check_status_consistency.py`)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script" / "check_status_consistency.py"

CARDS = """\
delivery: X1
cards:
  - {id: X1-T01, kind: implement, dependsOn: []}
features:
  - {id: X1-F01, title: First, cards: [X1-T01]}
"""


def git(project: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=project, check=True, capture_output=True
    )


def commit(project: Path, message: str) -> None:
    git(project, "add", "-A")
    git(project, "commit", "-q", "--allow-empty", "-m", message)


def set_state(project: Path, stages: dict[str, object]) -> None:
    folder = project / "plano" / "X1-F01"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "estado.json").write_text(json.dumps({"feature": "X1-F01", "stages": stages}), encoding="utf-8")


def run(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(project)], capture_output=True, text=True, check=False)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "harness.project.yaml").write_text("cards_glob: plano/X*.yaml\n", encoding="utf-8")
    (tmp_path / "plano").mkdir()
    (tmp_path / "plano" / "X1.yaml").write_text(CARDS, encoding="utf-8")
    commit(tmp_path, "docs(plan): add X1")
    return tmp_path


DONE = {"plan": {"status": "done"}, "tests": {"status": "done"}, "implement": {"status": "done"}}


def test_passes_when_state_follows_commits(project: Path) -> None:
    set_state(project, DONE)
    commit(project, "feat(web): implement thing (X1-F01)")
    proc = run(project)
    assert proc.returncode == 0, proc.stdout


def test_fails_when_feat_commit_but_implement_not_done(project: Path) -> None:
    set_state(project, {"plan": {"status": "done"}, "tests": {"status": "done"}})
    commit(project, "feat(web): implement thing (X1-F01)")
    proc = run(project)
    assert proc.returncode == 1
    assert "X1-F01" in proc.stdout and "implement" in proc.stdout


def test_fails_when_feat_commit_without_state_file(project: Path) -> None:
    commit(project, "feat(web): implement thing (X1-F01)")
    proc = run(project)
    assert proc.returncode == 1
    assert "não há estado.json" in proc.stdout


def test_docs_commit_without_state_is_fine(project: Path) -> None:
    commit(project, "docs(plan): plan X1-F01")
    assert run(project).returncode == 0


def test_id_match_is_whole_word(project: Path) -> None:
    commit(project, "feat(web): implement thing (X1-F010)")
    assert run(project).returncode == 0


def test_committed_running_stage_fails(project: Path) -> None:
    set_state(project, {**DONE, "review": {"status": "running"}})
    commit(project, "test(web): tweak (X1-F01)")
    proc = run(project)
    assert proc.returncode == 1
    assert "running" in proc.stdout


def test_dirty_running_stage_is_allowed(project: Path) -> None:
    set_state(project, DONE)
    commit(project, "feat(web): implement thing (X1-F01)")
    set_state(project, {**DONE, "review": {"status": "running"}})
    assert run(project).returncode == 0


def test_outside_git_exits_2(tmp_path: Path) -> None:
    (tmp_path / "harness.project.yaml").write_text("cards_glob: plano/X*.yaml\n", encoding="utf-8")
    assert run(tmp_path).returncode == 2
