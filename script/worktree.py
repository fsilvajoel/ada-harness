#!/usr/bin/env python3
"""Worktrees isoladas por tentativa do pipeline."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path, PureWindowsPath


class WorktreeError(RuntimeError):
    pass


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise WorktreeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def preflight(repo: Path) -> dict[str, str]:
    inside = git(repo, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        raise WorktreeError("raiz não está em worktree git")
    status = git(repo, "status", "--porcelain", "--untracked-files=all", "-z")
    dirty_entries = [
        entry for entry in status.split("\0")
        if entry and not (
            entry.startswith("?? ")
            and (entry[3:] == ".harness" or entry[3:].startswith(".harness/"))
        )
    ]
    if dirty_entries:
        raise WorktreeError("worktree atual não está limpa")
    return {
        "branch": git(repo, "branch", "--show-current"),
        "base_sha": git(repo, "rev-parse", "HEAD"),
    }


def create_attempt_worktree(
    repo: Path,
    run_id: str,
    feature_id: str,
    stage: str,
    attempt: int,
    base: str | None = None,
) -> tuple[Path, str]:
    clean = preflight(repo)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
        raise WorktreeError("run_id contém caracteres inválidos")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", feature_id):
        raise WorktreeError("feature_id contém caracteres inválidos")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", stage) or attempt < 1:
        raise WorktreeError("stage ou attempt inválidos")
    base_sha = base or clean["base_sha"]
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", base_sha):
        raise WorktreeError("base precisa ser SHA de commit")
    resolved_base = git(repo, "rev-parse", "--verify", f"{base_sha}^{{commit}}")
    if not re.fullmatch(r"[0-9a-f]{40,64}", resolved_base):
        raise WorktreeError("base SHA não corresponde a um commit")
    base_sha = resolved_base
    branch = f"harness/{feature_id}/{run_id}/{stage}/{attempt}"
    destination = (
        repo.parent
        / ".harness-worktrees"
        / repo.name
        / run_id
        / feature_id
        / stage
        / str(attempt)
    )
    if destination.exists():
        raise WorktreeError(f"destino de worktree já existe: {destination}")
    if git(repo, "branch", "--list", branch):
        raise WorktreeError(f"branch de tentativa já existe: {branch}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", branch, str(destination), base_sha)

    if git(destination, "rev-parse", "HEAD") != git(repo, "rev-parse", branch):
        raise WorktreeError("worktree criada em SHA diferente da branch da feature")
    return destination, base_sha


FORMAT_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs", ".json")


def _format_changed(worktree: Path, format_command: list[str], changed: list[str]) -> None:
    """Formata (ex.: `prettier --write`) os arquivos de código alterados antes do commit; o hook segue ligado."""
    targets = [
        path for path in changed
        if path.endswith(FORMAT_SUFFIXES) and (worktree / path).is_file()
    ]
    if not targets:
        return
    result = subprocess.run(
        [*format_command, *targets], cwd=worktree, check=False, capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        raise WorktreeError(f"format-write falhou: {(result.stderr or result.stdout).strip()}")


def commit_attempt(
    worktree: Path,
    *,
    allowed_paths: list[str],
    feature_id: str,
    stage: str,
    attempt: int,
    format_command: list[str] | None = None,
    new_only_paths: list[str] | None = None,
) -> str:
    if not allowed_paths or any(not _safe_path(path) for path in allowed_paths):
        raise WorktreeError("allowed_paths precisa conter paths relativos e seguros")
    if (
        attempt < 1
        or not re.fullmatch(r"[A-Za-z0-9_-]+-F\d+", feature_id)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", stage)
    ):
        raise WorktreeError("feature_id, stage ou attempt inválidos")

    # sem strip: o primeiro código pode começar com espaço (" M path") e o strip deslocaria o path
    raw = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all", "-z"],
        check=False, capture_output=True, text=True,
    )
    if raw.returncode != 0:
        raise WorktreeError((raw.stderr or raw.stdout).strip())
    status = raw.stdout
    changed: list[str] = []
    untracked: set[str] = set()
    for entry in status.split("\0"):
        if not entry:
            continue
        code, separator, path = entry[:2], entry[2:3], entry[3:]
        if not separator or code.strip() in {"R", "C"} or "\n" in path:
            raise WorktreeError("rename/copy ou path ambíguo bloqueado no commit da stage")
        changed.append(path)
        if code == "??":
            untracked.add(path)
    outside = [
        path for path in changed
        if not any(_path_matches(path, allowed) for allowed in allowed_paths)
    ]
    if outside:
        raise WorktreeError(f"arquivos alterados fora de allowedPaths: {', '.join(outside)}")
    protected = [
        path for path in changed
        if path not in untracked and any(_path_matches(path, entry) for entry in new_only_paths or [])
    ]
    if protected:
        raise WorktreeError(f"stub só pode criar arquivo novo; alterados: {', '.join(protected)}")
    if not changed:
        return git(worktree, "rev-parse", "HEAD")

    if format_command:
        _format_changed(worktree, format_command, changed)

    # Só os paths alterados (já validados contra allowedPaths): um allowedPath ainda inexistente não quebra o commit.
    git(worktree, "add", "-A", "--", *changed)
    branch = git(worktree, "branch", "--show-current")
    if not branch.startswith("harness/"):
        raise WorktreeError("commit de stage permitido apenas em branch harness/*")
    git(
        worktree,
        "-c",
        "user.name=Ada Harness",
        "-c",
        "user.email=harness@ada.local",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        f"chore(harness): {feature_id.lower()} {stage} attempt {attempt}",
    )
    return git(worktree, "rev-parse", "HEAD")


def remove_attempt_worktree(
    repo: Path,
    destination: Path,
    *,
    force: bool = False,
    delete_branch: bool = False,
) -> None:
    root = repo.resolve()
    target = destination.resolve()
    worktree_root = (root.parent / ".harness-worktrees" / root.name).resolve()
    if target == worktree_root or worktree_root not in target.parents:
        raise WorktreeError("destino fora da pasta de worktrees do harness")
    if git(target, "rev-parse", "--show-toplevel") != str(target):
        raise WorktreeError("destino não é a raiz de uma worktree")
    branch = git(target, "branch", "--show-current")
    if not branch.startswith("harness/"):
        raise WorktreeError("branch não pertence ao namespace do harness")
    arguments = ["worktree", "remove"]
    if force:
        arguments.append("--force")
    arguments.append(str(target))
    git(root, *arguments)
    if delete_branch:
        git(root, "branch", "-D", branch)


def _safe_path(value: str) -> bool:
    path = Path(value)
    windows_path = PureWindowsPath(value)
    return (
        bool(value.strip())
        and value not in {".", "./"}
        and "\\" not in value
        and not path.is_absolute()
        and not windows_path.is_absolute()
        and not windows_path.drive
        and ".." not in path.parts
    )


def _path_matches(path: str, pattern: str) -> bool:
    clean_pattern = pattern.rstrip("/")
    return (
        path == clean_pattern
        or path.startswith(clean_pattern + "/")
        or any(char in pattern for char in "*?[") and fnmatch.fnmatch(path, pattern)
    )
