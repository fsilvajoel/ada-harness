"""Testes do pré-gate local."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.events import read_events
from script.pregate import PregateCommand, PregateConfigError, parse_pregate, run_pregate
from script.stage_runner import CommandResult, PregateRunner, StageResult, _request_with_feedback, run_stage


def _run(
    tmp_path: Path,
    pregate: PregateRunner | None,
    max_attempts: int = 1,
    fallbacks: list[str] | None = None,
) -> tuple[StageResult, list[int]]:
    gate_calls: list[int] = []

    def gate(_worktree: Path) -> CommandResult:
        gate_calls.append(1)
        return CommandResult(0, "gate ok")

    result = run_stage(
        tmp_path / "events.jsonl",
        tmp_path / "evidence",
        run_id="r", feature_id="E1-F01", stage="plan", gate_name="verify",
        primary_executor="a", fallbacks=fallbacks or [], max_attempts=max_attempts,
        worktree_for_attempt=lambda attempt: tmp_path / str(attempt),
        run_worker=lambda _e, _w: CommandResult(0, "ok"),
        run_gate=gate,
        run_pregate=pregate,
    )
    return result, gate_calls


def test_pregate_pass_continues_to_gate(tmp_path: Path) -> None:
    result, gate_calls = _run(tmp_path, lambda _w: CommandResult(0, "lint ok"))
    assert result.passed and gate_calls == [1]
    assert (tmp_path / "evidence" / "plan" / "1" / "pregate.log").read_text() == "lint ok"
    assert not [e for e in read_events(tmp_path / "events.jsonl") if e["event"] == "pregate-fail"]


def test_pregate_fail_skips_gate_records_event_and_log(tmp_path: Path) -> None:
    result, gate_calls = _run(tmp_path, lambda _w: CommandResult(1, "eslint: erro X"))
    assert not result.passed and gate_calls == []
    events = read_events(tmp_path / "events.jsonl")
    fails = [e for e in events if e["event"] == "pregate-fail"]
    assert len(fails) == 1 and fails[0]["attempt"] == 1 and fails[0]["stage"] == "plan"
    assert (tmp_path / "evidence" / "plan" / "1" / "pregate.log").read_text() == "eslint: erro X"
    failed = [e for e in events if e["event"] == "stage_failed"][-1]
    assert failed["reason"] == "pregate-fail" and failed["gate"] == "pregate"


def test_pregate_fail_counts_as_attempt_and_retries(tmp_path: Path) -> None:
    calls: list[int] = []

    def pregate(_w: Path) -> CommandResult:
        calls.append(1)
        return CommandResult(1, "ruim") if len(calls) == 1 else CommandResult(0, "ok")

    result, gate_calls = _run(tmp_path, pregate, max_attempts=2, fallbacks=["b"])
    assert result.passed and result.attempts == 2
    assert gate_calls == [1] and len(calls) == 2


def test_pregate_fail_respects_max_attempts(tmp_path: Path) -> None:
    result, gate_calls = _run(tmp_path, lambda _w: CommandResult(1, "sempre"), max_attempts=2, fallbacks=["b"])
    assert not result.passed and gate_calls == []
    assert len([e for e in read_events(tmp_path / "events.jsonl") if e["event"] == "pregate-fail"]) == 2


def test_pregate_disabled_keeps_current_behavior(tmp_path: Path) -> None:
    result, gate_calls = _run(tmp_path, None)
    assert result.passed and gate_calls == [1]
    assert not (tmp_path / "evidence" / "plan" / "1" / "pregate.log").exists()


def test_pregate_retry_request_carries_pregate_log(tmp_path: Path) -> None:
    _run(tmp_path, lambda _w: CommandResult(1, "ERRO-DO-PREGATE"))
    request = tmp_path / "req.md"
    request.write_text("# pedido\n", encoding="utf-8")
    out = _request_with_feedback(
        request, read_events(tmp_path / "events.jsonl"), "r", "E1-F01", "plan", tmp_path,
        tmp_path / "evidence" / "plan",
    )
    assert "ERRO-DO-PREGATE" in out.read_text(encoding="utf-8")


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "base.txt").write_text("x")
    return path


def test_pregate_expands_files(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    (repo / "a.ts").write_text("1")
    (repo / "b.ts").write_text("2")
    cmd = PregateCommand((sys.executable, "-c", "import sys; print(sys.argv[1:])", "{files}"), 30)
    out = run_pregate([cmd], repo)
    assert out.passed and "['a.ts', 'b.ts', 'base.txt']" in out.output


def test_pregate_skips_files_command_without_changes(tmp_path: Path) -> None:
    repo = tmp_path / "vazio"
    repo.mkdir()
    out = run_pregate([PregateCommand(("false", "{files}"), 30)], repo)
    assert out.passed and "pulado" in out.output


def test_pregate_failure_exit_code_in_log(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    cmd = PregateCommand((sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"), 30)
    out = run_pregate([cmd], repo)
    assert not out.passed and out.exit_code == 3 and "boom" in out.output and "exit 3" in out.output


def test_pregate_timeout_gives_clear_error(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    out = run_pregate([PregateCommand((sys.executable, "-c", "import time; time.sleep(30)"), 1)], repo)
    assert not out.passed and out.exit_code == 124 and "timeout" in out.output


def test_pregate_missing_command_gives_clear_error(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    out = run_pregate([PregateCommand(("comando-inexistente-xyz",), 5)], repo)
    assert not out.passed and "não encontrado" in out.output


def test_parse_pregate_config() -> None:
    assert parse_pregate({}, "plan") == []
    off = {"pregate": {"plan": {"commands": ["x {files}"]}}}
    assert parse_pregate(off, "plan") == []
    on = {"pregate": {"plan": {"enabled": True, "commands": ["x {files}", {"run": "y", "timeout_s": 9}]}}}
    parsed = parse_pregate(on, "plan")
    assert [c.argv for c in parsed] == [("x", "{files}"), ("y",)] and parsed[1].timeout_s == 9
    assert parse_pregate(on, "refinement") == []
    with pytest.raises(PregateConfigError):
        parse_pregate({"pregate": {"plan": {"enabled": "sim"}}}, "plan")
