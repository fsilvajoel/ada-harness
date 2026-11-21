"""Testes da máquina de estados da evolução."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.pipeline import (
    TransitionError,
    finish_stage,
    import_legacy_state,
    replay,
    request_input,
    start_stage,
)


def test_empty_state_starts_at_plan() -> None:
    state = replay("run-1", "E9-F02", [])
    assert state["current_stage"] == "plan"
    assert state["stages"]["plan"]["status"] == "pending"


def test_valid_sequence_and_replay(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    start_stage(path, "run-1", "E9-F02", "plan")
    state = finish_stage(
        path,
        "run-1",
        "E9-F02",
        "plan",
        passed=True,
        gate="plan-lint",
    )
    assert state["current_stage"] == "refinement"
    assert state["stages"]["plan"]["status"] == "done"
    assert state["gates"]["plan-lint"] == "PASS"


def test_start_out_of_sequence_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    with pytest.raises(TransitionError, match="esperado plan"):
        start_stage(path, "run-1", "E9-F02", "tests")


def test_running_stage_cannot_be_started_twice(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    start_stage(path, "run-1", "E9-F02", "plan")
    with pytest.raises(TransitionError, match="já está running"):
        start_stage(path, "run-1", "E9-F02", "plan")


def test_needs_input_can_resume_same_stage(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    start_stage(path, "run-1", "E9-F02", "plan")
    paused = request_input(path, "run-1", "E9-F02", "plan", "falta decisão do produto")
    assert paused["stages"]["plan"]["status"] == "needs_input"
    resumed = start_stage(path, "run-1", "E9-F02", "plan")
    assert resumed["attempts"]["plan"] == 2
    assert resumed["stages"]["plan"]["status"] == "running"


def test_failed_stage_records_reason(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    start_stage(path, "run-1", "E9-F02", "plan")
    state = finish_stage(
        path,
        "run-1",
        "E9-F02",
        "plan",
        passed=False,
        gate="plan-lint",
        reason="critério sem fonte",
    )
    assert state["stages"]["plan"]["status"] == "failed"
    assert state["stages"]["plan"]["reason"] == "critério sem fonte"
    assert state["gates"]["plan-lint"] == "FAIL"


def test_legacy_state_is_imported() -> None:
    legacy = {
        "stages": {
            "plan": {"status": "done"},
            "tests": {"status": "done"},
            "implement": {"status": "running"},
        },
        "gates": {
            "expect-red": {"status": "PASS"},
        },
    }
    state = import_legacy_state("run-legacy", "E9-F02", legacy)
    assert state["stages"]["plan"]["status"] == "done"
    assert state["stages"]["tests"]["status"] == "done"
    assert state["stages"]["implement"]["status"] == "running"
    assert state["gates"]["expect-red"] == "PASS"
    assert state["current_stage"] == "implement"
