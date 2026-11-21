"""Testes da API de estado do dashboard."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.dashboard import PAGE, load_events, status_payload
from script.events import append_event
from script.pipeline import finish_stage, start_stage
from script.worktree import commit_attempt, create_attempt_worktree, remove_attempt_worktree


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_load_events_empty(tmp_path: Path) -> None:
    assert load_events(tmp_path) == []


def test_status_payload_requires_valid_project(tmp_path: Path) -> None:
    (tmp_path / "harness.project.yaml").write_text(
        "cards_glob: plano/*.yaml\n",
        encoding="utf-8",
    )
    payload = status_payload(tmp_path)
    assert "pipeline" in payload
    assert payload["events"] == []


def test_status_payload_projects_runs_without_html_interpolation(tmp_path: Path) -> None:
    (tmp_path / "harness.project.yaml").write_text(
        """cards_glob: docs/plano/*.yaml
workers:
  local-worker:
    rates:
      prompt_per_1k: 0.1
      completion_per_1k: 0.2
""",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "plano").mkdir(parents=True)
    event_path = tmp_path / ".harness" / "runs" / "run-1" / "E1-F01" / "events.jsonl"
    append_event(event_path, {
        "event": "stage_started",
        "run_id": "run-1",
        "feature_id": "E1-F01",
        "stage": "plan",
        "executor": "local-worker",
    })
    append_event(event_path, {
        "event": "attempt_result",
        "run_id": "run-1",
        "feature_id": "E1-F01",
        "stage": "plan",
        "executor": "local-worker",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
    })
    append_event(event_path, {
        "event": "gate_evidence",
        "run_id": "run-1",
        "feature_id": "E1-F01",
        "stage": "plan",
        "executor": "local-worker",
        "gate": "plan-lint",
        "status": "PASS",
        "output": "/tmp/evidence.log",
        "output_sha256": "a" * 64,
    })
    payload = status_payload(tmp_path)
    run = payload["runs"][0]
    assert run["state"]["stages"]["plan"]["status"] == "running"
    assert run["events"][0]["executor"] == "local-worker"
    assert run["metrics"]["estimated_cost"] == 0.2
    assert "innerHTML" not in PAGE


def test_status_payload_includes_committed_worktree_diff(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "harness@example.invalid")
    git(repo, "config", "user.name", "Harness Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    (repo / "harness.project.yaml").write_text(
        "cards_glob: docs/plano/*.yaml\n",
        encoding="utf-8",
    )
    (repo / "docs" / "plano").mkdir(parents=True)
    git(repo, "add", "harness.project.yaml", "docs")
    git(repo, "commit", "-m", "dashboard fixture")

    worktree, _ = create_attempt_worktree(repo, "run-1", "E1-F01", "plan", 1)
    generated = worktree / "src" / "generated.py"
    generated.parent.mkdir()
    generated.write_text("value = 1\n", encoding="utf-8")
    commit_sha = commit_attempt(
        worktree,
        allowed_paths=["src/generated.py"],
        feature_id="E1-F01",
        stage="plan",
        attempt=1,
    )
    events_path = repo / ".harness" / "runs" / "run-1" / "E1-F01" / "events.jsonl"
    start_stage(events_path, "run-1", "E1-F01", "plan", executor="worker", attempt=1)
    finish_stage(
        events_path,
        "run-1",
        "E1-F01",
        "plan",
        passed=True,
        executor="worker",
        attempt=1,
        commit_sha=commit_sha,
    )

    no_op_worktree, _ = create_attempt_worktree(
        repo,
        "run-1",
        "E1-F01",
        "refinement",
        1,
        commit_sha,
    )
    start_stage(events_path, "run-1", "E1-F01", "refinement", executor="worker", attempt=1)
    finish_stage(
        events_path,
        "run-1",
        "E1-F01",
        "refinement",
        passed=True,
        executor="worker",
        attempt=1,
        commit_sha=commit_sha,
    )

    payload = status_payload(repo)
    worktrees = payload["runs"][0]["worktrees"]
    snapshots = {worktree["stage"]: worktree for worktree in worktrees}
    assert snapshots["plan"]["files"] == ["src/generated.py"]
    assert "generated.py" in snapshots["plan"]["diff_stat"]
    assert snapshots["plan"]["branch"] == "harness/E1-F01/run-1/plan/1"
    assert snapshots["refinement"]["files"] == []
    assert snapshots["refinement"]["diff_stat"] == ""

    remove_attempt_worktree(repo, worktree, delete_branch=True)
    remove_attempt_worktree(repo, no_op_worktree, delete_branch=True)
