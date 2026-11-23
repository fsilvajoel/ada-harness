"""Testes de execução determinística de stages."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.events import read_events
from script.pipeline import STAGES, finish_stage, latest_commit_sha, replay, start_stage
from script.stage_runner import CommandResult, _worker_usage, run_command, run_configured_stage, run_stage
from script.worktree import commit_attempt, create_attempt_worktree, remove_attempt_worktree


def test_feature_runs_all_stages_without_agent_in_control_plane(tmp_path: Path) -> None:
    event_path = tmp_path / "events.jsonl"
    evidence_root = tmp_path / "evidence"

    for stage in STAGES:
        result = run_stage(
            event_path,
            evidence_root,
            run_id="run-1",
            feature_id="E1-F01",
            stage=stage,
            gate_name=f"{stage}-gate",
            primary_executor="fake-agent",
            fallbacks=[],
            max_attempts=1,
            worktree_for_attempt=lambda attempt: tmp_path / "worktree",
            run_worker=lambda _executor, _worktree: CommandResult(0, "worker ok"),
            run_gate=lambda _worktree: CommandResult(0, "gate ok"),
        )
        assert result.passed is True

    state = replay("run-1", "E1-F01", read_events(event_path))
    assert state["current_stage"] is None
    assert all(state["stages"][stage]["status"] == "done" for stage in STAGES)
    assert all(state["attempts"][stage] == 1 for stage in STAGES)


def test_worker_failure_uses_fallback_and_records_evidence(tmp_path: Path) -> None:
    event_path = tmp_path / "events.jsonl"
    evidence_root = tmp_path / "evidence"
    worker_calls: list[str] = []
    cleaned: list[Path] = []

    def worker(executor: str, _worktree: Path) -> CommandResult:
        worker_calls.append(executor)
        return CommandResult(1, "failed") if executor == "primary" else CommandResult(0, "ok")

    result = run_stage(
        event_path,
        evidence_root,
        run_id="run-1",
        feature_id="E1-F01",
        stage="plan",
        gate_name="plan-lint",
        primary_executor="primary",
        fallbacks=["backup"],
        max_attempts=2,
        worktree_for_attempt=lambda attempt: tmp_path / f"worktree-{attempt}",
        run_worker=worker,
        run_gate=lambda _worktree: CommandResult(0, "pass"),
        cleanup_attempt=cleaned.append,
    )

    assert result.passed is True
    assert worker_calls == ["primary", "backup"]
    assert cleaned == [tmp_path / "worktree-1"]
    events = read_events(event_path)
    assert sum(event["event"] == "fallback" for event in events) == 1
    assert (evidence_root / "plan" / "1" / "worker.json").is_file()
    assert (evidence_root / "plan" / "2" / "plan-lint.json").is_file()


def test_exhausted_gate_fallback_returns_failure(tmp_path: Path) -> None:
    result = run_stage(
        tmp_path / "events.jsonl",
        tmp_path / "evidence",
        run_id="run-1",
        feature_id="E1-F01",
        stage="plan",
        gate_name="plan-lint",
        primary_executor="primary",
        fallbacks=["backup"],
        max_attempts=2,
        worktree_for_attempt=lambda attempt: tmp_path / f"worktree-{attempt}",
        run_worker=lambda _executor, _worktree: CommandResult(0, "worker ok"),
        run_gate=lambda _worktree: CommandResult(1, "gate failed"),
    )
    assert result.passed is False
    assert result.attempts == 2
    assert result.reason == "gate-fail"


def test_artifact_preflight_blocks_worker_without_fallback(tmp_path: Path) -> None:
    called = False

    def worker(_executor: str, _worktree: Path) -> CommandResult:
        nonlocal called
        called = True
        return CommandResult(0, "should not run")

    result = run_stage(
        tmp_path / "events.jsonl",
        tmp_path / "evidence",
        run_id="run-1",
        feature_id="E1-F01",
        stage="plan",
        gate_name="verify",
        primary_executor="primary",
        fallbacks=["backup"],
        max_attempts=2,
        worktree_for_attempt=lambda attempt: tmp_path / f"worktree-{attempt}",
        run_worker=worker,
        run_gate=lambda _worktree: CommandResult(0, "pass"),
        preflight=lambda _worktree: ["E1-T01: allowedPaths ausente"],
    )
    assert result.passed is False
    assert result.reason == "preflight-fail"
    assert called is False


def test_policy_skips_inapplicable_stage_without_creating_worktree(tmp_path: Path) -> None:
    event_path = tmp_path / "events.jsonl"
    for stage in ("plan", "refinement"):
        start_stage(event_path, "run-1", "E1-F01", stage)
        finish_stage(event_path, "run-1", "E1-F01", stage, passed=True)

    def unexpected_worktree(_attempt: int) -> Path:
        raise AssertionError("worktree created")

    result = run_stage(
        event_path,
        tmp_path / "evidence",
        run_id="run-1",
        feature_id="E1-F01",
        stage="tests",
        gate_name="",
        primary_executor="",
        fallbacks=[],
        max_attempts=0,
        worktree_for_attempt=unexpected_worktree,
        run_worker=lambda _executor, _worktree: CommandResult(0, ""),
        run_gate=lambda _worktree: CommandResult(0, ""),
        gate_policy={"tests": False},
    )
    assert result.passed is True
    assert result.executor == "skipped"
    state = replay("run-1", "E1-F01", read_events(event_path))
    assert state["stages"]["tests"]["status"] == "skipped"
    assert state["current_stage"] == "testReview"


def test_invalid_engine_result_is_rejected() -> None:
    with pytest.raises(ValueError, match="usage"):
        CommandResult(0, "ok", {"prompt_tokens": -1})


def test_cli_agent_usage_json_is_normalized_and_metadata_must_match(tmp_path: Path) -> None:
    metadata = tmp_path / "engine-meta.json"
    metadata.write_text(
        '{"exit_code": 0, "status": "ok", "usage": "{\\"input_tokens\\": 12, \\"output_tokens\\": 4}"}\n',
        encoding="utf-8",
    )
    assert _worker_usage(metadata, exit_code=0) == (
        {"prompt_tokens": 12, "completion_tokens": 4},
        None,
        None,
    )
    assert _worker_usage(metadata, exit_code=1)[1] == "invalid-result"


def test_successful_stage_commit_is_the_next_stage_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "harness@example.invalid")
    git(repo, "config", "user.name", "Harness Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "base")
    event_path = tmp_path / "events.jsonl"
    evidence_root = tmp_path / "evidence"

    def create(stage: str, base: str | None = None):
        return lambda attempt: create_attempt_worktree(
            repo,
            "run-1",
            "E1-F01",
            stage,
            attempt,
            base,
        )[0]

    first_path: Path | None = None

    def first_worker(_executor: str, worktree: Path) -> CommandResult:
        nonlocal first_path
        first_path = worktree
        plan = worktree / "docs" / "plano" / "E1-F01"
        plan.mkdir(parents=True)
        (plan / "plan.json").write_text("{}\n", encoding="utf-8")
        return CommandResult(0, "plan written")

    first = run_stage(
        event_path,
        evidence_root,
        run_id="run-1",
        feature_id="E1-F01",
        stage="plan",
        gate_name="plan-lint",
        primary_executor="worker",
        fallbacks=[],
        max_attempts=1,
        worktree_for_attempt=create("plan"),
        run_worker=first_worker,
        run_gate=lambda _worktree: CommandResult(0, "PASS"),
        commit_success=lambda path, attempt: commit_attempt(
            path,
            allowed_paths=["docs/plano/E1-F01/plan.json"],
            feature_id="E1-F01",
            stage="plan",
            attempt=attempt,
        ),
    )
    assert first.passed and first.commit_sha
    base = first.commit_sha

    second = run_stage(
        event_path,
        evidence_root,
        run_id="run-1",
        feature_id="E1-F01",
        stage="refinement",
        gate_name="refinement-lint",
        primary_executor="worker",
        fallbacks=[],
        max_attempts=1,
        worktree_for_attempt=create("refinement", base),
        run_worker=lambda _executor, worktree: CommandResult(
            0,
            "found" if (worktree / "docs" / "plano" / "E1-F01" / "plan.json").is_file() else "missing",
        ),
        run_gate=lambda _worktree: CommandResult(0, "PASS"),
        commit_success=lambda path, attempt: commit_attempt(
            path,
            allowed_paths=["docs/plano/E1-F01/plan.json"],
            feature_id="E1-F01",
            stage="refinement",
            attempt=attempt,
        ),
    )
    assert second.passed
    assert second.commit_sha == base
    assert first_path is not None
    remove_attempt_worktree(repo, first_path, delete_branch=True)
    second_path = tmp_path / ".harness-worktrees" / "repo" / "run-1" / "E1-F01" / "refinement" / "1"
    remove_attempt_worktree(repo, second_path, delete_branch=True)
    events = read_events(event_path)
    assert events[-1]["event"] == "stage_passed"
    assert events[-1]["commit_sha"] == base
    assert latest_commit_sha(events, "run-1", "E1-F01") == base


def test_configured_stage_dispatches_worker_runs_gate_and_commits(tmp_path: Path) -> None:
    repo = tmp_path / "configured-project"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "harness@example.invalid")
    git(repo, "config", "user.name", "Harness Test")
    fake_worker = repo / "fake_worker.py"
    fake_worker.write_text(
        "from pathlib import Path\n"
        "Path('src').mkdir(exist_ok=True)\n"
        "Path('src/generated.txt').write_text('generated\\n', encoding='utf-8')\n"
        "print('worker completed')\n",
        encoding="utf-8",
    )
    (repo / "check.py").write_text(
        "from pathlib import Path\n"
        "raise SystemExit(0 if Path('src/generated.txt').is_file() else 1)\n",
        encoding="utf-8",
    )
    (repo / "request.md").write_text("Create generated output", encoding="utf-8")
    (repo / "docs" / "plano").mkdir(parents=True)
    (repo / "docs" / "plano" / "E1-F01.yaml").write_text(
        """cards:
  - schemaVersion: 2
    id: E1-F01-T01
    title: Generate file
    kind: implement
    allowedPaths: [src/generated.txt]
    generatedPaths: [src/generated.txt]
    checks: [python3 check.py]
    dor: [approved]
    dod: [file generated]
""",
        encoding="utf-8",
    )
    (repo / "harness.project.yaml").write_text(
        f"""cards_glob: docs/plano/E*.yaml
roles:
  implementer: {{agent: implementer.md, worker: fake}}
workers:
  fake:
    adapter: cli-agent
    command: {sys.executable}
    read_args: {fake_worker}
    write_args: {fake_worker}
    roles: implementer
    timeout_s: 30
commands:
  verify: python3 check.py
""",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    request_path = repo / "request.md"
    event_path = repo / ".harness" / "runs" / "run-1" / "E1-F01" / "events.jsonl"
    for stage in ("plan", "refinement", "tests", "testReview"):
        start_stage(event_path, "run-1", "E1-F01", stage)
        finish_stage(event_path, "run-1", "E1-F01", stage, passed=True)

    result = run_configured_stage(
        repo,
        run_id="run-1",
        feature_id="E1-F01",
        stage="implement",
        role="implementer",
        request=request_path,
        gate_name="verify",
        allowed_paths=["src/generated.txt"],
    )
    assert result.passed is True
    assert result.commit_sha is not None
    engine_result = json.loads(
        (
            repo / ".harness" / "runs" / "run-1" / "E1-F01"
            / "evidence" / "implement" / "1" / "engine-result.json"
        ).read_text(encoding="utf-8")
    )
    assert engine_result["exit_code"] == 0
    assert engine_result["output"].startswith("worker completed\n")
    assert "generated" in subprocess.run(
        ["git", "-C", str(repo), "show", f"{result.commit_sha}:src/generated.txt"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert replay("run-1", "E1-F01", read_events(event_path))["current_stage"] == "mutation_e2e"
    worktree_path = (
        repo.parent / ".harness-worktrees" / repo.name / "run-1" / "E1-F01" / "implement" / "1"
    )
    remove_attempt_worktree(repo, worktree_path, delete_branch=True)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def test_run_command_exports_harness_dir(tmp_path: Path) -> None:
    result = run_command(["sh", "-c", 'test -f "$ADA_HARNESS_DIR/script/stage_runner.py" && echo ok'], cwd=tmp_path, timeout_s=10)
    assert result.exit_code == 0
    assert "ok" in result.output


def test_retry_request_carries_previous_commit_failure(tmp_path: Path) -> None:
    from script.stage_runner import _request_with_feedback

    request = tmp_path / "tests.md"
    request.write_text("# pedido\n", encoding="utf-8")
    base = {"run_id": "r", "feature_id": "E1-F01", "stage": "tests"}
    assert _request_with_feedback(request, [], "r", "E1-F01", "tests", tmp_path) == request.resolve()
    events = [{**base, "event": "stage_failed", "reason": "commit-error: \x1b[31mQ1 toBeDefined() sempre passa\x1b[m"}]
    result = _request_with_feedback(request, events, "r", "E1-F01", "tests", tmp_path)
    text = result.read_text(encoding="utf-8")
    assert text.startswith("# pedido\n")
    assert "Q1 toBeDefined() sempre passa" in text and "\x1b" not in text
    assert request.read_text(encoding="utf-8") == "# pedido\n"


def test_install_dependencies_runs_in_worktree(tmp_path: Path) -> None:
    from script.stage_runner import install_dependencies

    install_dependencies(tmp_path, [])
    install_dependencies(tmp_path, ["sh", "-c", "touch installed"])
    assert (tmp_path / "installed").is_file()


def test_install_dependencies_failure_raises(tmp_path: Path) -> None:
    from script.stage_runner import StageRunnerError, install_dependencies

    with pytest.raises(StageRunnerError, match="commands.install falhou"):
        install_dependencies(tmp_path, ["sh", "-c", "echo boom; exit 3"])
