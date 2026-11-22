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
    assert message.startswith("chore(harness): e1-f01 implement attempt 1")
    assert "Copilot" not in message
    remove_attempt_worktree(root, worktree, delete_branch=True)


def test_stage_commit_formats_code_files_before_commit(tmp_path: Path) -> None:
    root = repo(tmp_path)
    worktree, _ = create_attempt_worktree(root, "run-1", "E1-F01", "tests", 1)
    (worktree / "a.test.ts").write_text("x\n", encoding="utf-8")
    (worktree / "notes.md").write_text("x\n", encoding="utf-8")
    formatter = [
        "python3", "-c",
        "import sys,pathlib;[pathlib.Path(a).write_text('formatado\\n') for a in sys.argv[1:]]",
    ]
    commit_attempt(
        worktree,
        allowed_paths=["a.test.ts", "notes.md"],
        feature_id="E1-F01",
        stage="tests",
        attempt=1,
        format_commands=[formatter],
    )
    assert git_output(worktree, "show", "HEAD:a.test.ts") == "formatado"
    assert git_output(worktree, "show", "HEAD:notes.md") == "x"
    remove_attempt_worktree(root, worktree, delete_branch=True)


def test_stage_commit_stub_paths_accept_only_new_files(tmp_path: Path) -> None:
    root = repo(tmp_path)
    worktree, _ = create_attempt_worktree(root, "run-1", "E1-F01", "tests", 1)
    (worktree / "stub.ts").write_text("export {};\n", encoding="utf-8")
    commit_attempt(
        worktree, allowed_paths=["stub.ts"], feature_id="E1-F01", stage="tests", attempt=1,
        new_only_paths=["stub.ts"],
    )
    (worktree / "stub.ts").write_text("export const a = 1;\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="arquivo novo"):
        commit_attempt(
            worktree, allowed_paths=["stub.ts"], feature_id="E1-F01", stage="tests", attempt=2,
            new_only_paths=["stub.ts"],
        )
    remove_attempt_worktree(root, worktree, force=True, delete_branch=True)


def _barrel_worktree(tmp_path: Path) -> tuple[Path, Path]:
    root = repo(tmp_path)
    (root / "pkg").mkdir()
    (root / "pkg" / "index.ts").write_text("export * from './a';\nexport const x = 1;\n", encoding="utf-8")
    (root / "pkg" / "other.ts").write_text("export const a = 1;\n", encoding="utf-8")
    git(root, "add", "pkg")
    git(root, "commit", "-m", "barrel")
    worktree, _ = create_attempt_worktree(root, "run-1", "E1-F01", "tests", 1)
    return root, worktree


def _commit_barrel(worktree: Path) -> str:
    return commit_attempt(
        worktree, allowed_paths=["pkg/*"], feature_id="E1-F01", stage="tests", attempt=1,
        new_only_paths=["pkg/*"],
    )


def test_stage_commit_stub_barrel_accepts_appended_reexports(tmp_path: Path) -> None:
    root, worktree = _barrel_worktree(tmp_path)
    (worktree / "pkg" / "index.ts").write_text(
        "export * from './a';\nexport const x = 1;\n"
        "export * from './b';\nexport { Y } from './y';\nexport type { Z } from \"./z\";\n"
        "export {\n  W,\n} from './w';\n",
        encoding="utf-8",
    )
    _commit_barrel(worktree)
    remove_attempt_worktree(root, worktree, force=True, delete_branch=True)


@pytest.mark.parametrize(
    "content",
    [
        "export * from './a';\nexport * from './b';\n",
        "export * from './a2';\nexport const x = 1;\n",
        "export * from './a';\nexport const x = 1;\nexport const y = 2;\n",
        "export * from './a';\nexport const x = 1;\nexport * from './b'; foo();\n",
    ],
)
def test_stage_commit_stub_barrel_rejects_other_changes(tmp_path: Path, content: str) -> None:
    root, worktree = _barrel_worktree(tmp_path)
    (worktree / "pkg" / "index.ts").write_text(content, encoding="utf-8")
    with pytest.raises(WorktreeError, match="arquivo novo"):
        _commit_barrel(worktree)
    remove_attempt_worktree(root, worktree, force=True, delete_branch=True)


def test_stage_commit_stub_non_barrel_existing_file_still_rejected(tmp_path: Path) -> None:
    root, worktree = _barrel_worktree(tmp_path)
    (worktree / "pkg" / "other.ts").write_text("export const a = 1;\nexport * from './b';\n", encoding="utf-8")
    with pytest.raises(WorktreeError, match="arquivo novo"):
        _commit_barrel(worktree)
    remove_attempt_worktree(root, worktree, force=True, delete_branch=True)


def test_stage_commit_keeps_files_when_hook_rejects(tmp_path: Path) -> None:
    root = repo(tmp_path)
    worktree, _ = create_attempt_worktree(root, "run-1", "E1-F01", "tests", 1)
    hook = Path(git_output(worktree, "rev-parse", "--git-path", "hooks")) / "pre-commit"
    hook = hook if hook.is_absolute() else worktree / hook
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    (worktree / "a.ts").write_text("x\n", encoding="utf-8")
    keep = tmp_path / "keep"
    with pytest.raises(WorktreeError):
        commit_attempt(
            worktree, allowed_paths=["a.ts"], feature_id="E1-F01", stage="tests", attempt=1, keep_on_failure=keep
        )
    assert (keep / "a.ts").read_text(encoding="utf-8") == "x\n"
    remove_attempt_worktree(root, worktree, force=True, delete_branch=True)


def test_format_exit_one_does_not_block_commit(tmp_path: Path) -> None:
    root = repo(tmp_path)
    worktree, _ = create_attempt_worktree(root, "run-1", "E1-F01", "tests", 1)
    (worktree / "a.ts").write_text("x\n", encoding="utf-8")
    commit_attempt(
        worktree, allowed_paths=["a.ts"], feature_id="E1-F01", stage="tests", attempt=1,
        format_commands=[["python3", "-c", "import sys; sys.exit(1)"]],
    )
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
