"""Testes de preparação de worktrees isoladas."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.worktree import (
    commit_attempt,
    WorktreeError,
    create_attempt_worktree,
    preflight,
    remove_attempt_worktree,
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "harness@example.invalid")
    git(root, "config", "user.name", "Harness Test")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "base")
    return root


def test_preflight_reports_clean_branch_and_sha(tmp_path: Path) -> None:
    root = repo(tmp_path)
    state = preflight(root)
    assert state["branch"]
    assert len(state["base_sha"]) == 40


def test_preflight_rejects_dirty_worktree(tmp_path: Path) -> None:
    root = repo(tmp_path)
    (root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="não está limpa"):
        preflight(root)


def test_preflight_ignores_only_untracked_harness_runtime_data(tmp_path: Path) -> None:
    root = repo(tmp_path)
    runtime = root / ".harness" / "runs" / "run-1"
    runtime.mkdir(parents=True)
    (runtime / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert preflight(root)["base_sha"]
    (root / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="não está limpa"):
        preflight(root)


def test_attempt_worktree_is_external_and_removable(tmp_path: Path) -> None:
    root = repo(tmp_path)
    worktree, base_sha = create_attempt_worktree(
        root,
        "run-1",
        "E1-F01",
        "implement",
        1,
    )
    assert worktree.exists()
    assert root not in worktree.parents
    assert len(base_sha) == 40
    assert preflight(root)["base_sha"] == base_sha
    remove_attempt_worktree(root, worktree)
    assert not worktree.exists()


def test_attempt_worktree_rejects_path_traversal_ids(tmp_path: Path) -> None:
    root = repo(tmp_path)
    with pytest.raises(WorktreeError, match="feature_id"):
        create_attempt_worktree(root, "run-1", "../feature", "implement", 1)


def test_stage_commit_is_scoped_and_has_required_trailer(tmp_path: Path) -> None:
    root = repo(tmp_path)
    worktree, _ = create_attempt_worktree(root, "run-1", "E1-F01", "implement", 1)
    (worktree / "src").mkdir()
    (worktree / "src" / "feature.py").write_text("value = 1\n", encoding="utf-8")
    commit_sha = commit_attempt(
        worktree,
        allowed_paths=["src/feature.py"],
        feature_id="E1-F01",
        stage="implement",
        attempt=1,
    )
    message = git_output(worktree, "log", "-1", "--pretty=%B")
    assert len(commit_sha) == 40
    assert "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" in message
    remove_attempt_worktree(root, worktree, delete_branch=True)


def test_stage_commit_rejects_changes_outside_scope(tmp_path: Path) -> None:
    root = repo(tmp_path)
    worktree, _ = create_attempt_worktree(root, "run-1", "E1-F01", "implement", 1)
    (worktree / "unexpected.txt").write_text("outside\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="fora de allowedPaths"):
        commit_attempt(
            worktree,
            allowed_paths=["src/feature.py"],
            feature_id="E1-F01",
            stage="implement",
            attempt=1,
        )
    remove_attempt_worktree(root, worktree, force=True, delete_branch=True)


def git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
