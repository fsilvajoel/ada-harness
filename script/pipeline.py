#!/usr/bin/env python3
"""Máquina de estados determinística da evolução Ada Harness 2.0."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final

from script.events import Event, EventError, append_event, read_events

STAGES: Final[tuple[str, ...]] = (
    "plan",
    "refinement",
    "tests",
    "testReview",
    "implement",
    "mutation_e2e",
    "finalReview",
    "closing",
)

STAGE_RESULT: Final[dict[str, str]] = {
    "stage_started": "running",
    "stage_passed": "done",
    "stage_failed": "failed",
    "needs_input": "needs_input",
    "stage_skipped": "skipped",
}

NEXT_STAGE: Final[dict[str, str | None]] = {
    "plan": "refinement",
    "refinement": "tests",
    "tests": "testReview",
    "testReview": "implement",
    "implement": "mutation_e2e",
    "mutation_e2e": "finalReview",
    "finalReview": "closing",
    "closing": None,
}

LEGACY_STAGE_TO_EVENT: Final[dict[str, str]] = {
    "plan": "plan",
    "refinement": "refinement",
    "tests": "tests",
    "testReview": "testReview",
    "implement": "implement",
    "review": "finalReview",
}


class TransitionError(ValueError):
    pass


def empty_state(run_id: str, feature_id: str) -> dict[str, object]:
    return {
        "run_id": run_id,
        "feature": feature_id,
        "current_stage": "plan",
        "stages": {stage: {"status": "pending"} for stage in STAGES},
        "gates": {},
        "attempts": {},
    }


def replay(run_id: str, feature_id: str, events: list[Event]) -> dict[str, object]:
    state = empty_state(run_id, feature_id)
    stages = state["stages"]
    assert isinstance(stages, dict)

    for event in events:
        if event["run_id"] != run_id or event["feature_id"] != feature_id:
            continue
        kind = str(event["event"])
        stage = str(event["stage"])
        if stage not in STAGES:
            raise TransitionError(f"stage desconhecida: {stage}")
        current = state["current_stage"]
        stage_state = stages[stage]
        assert isinstance(stage_state, dict)
        if kind == "stage_started":
            if stage != current:
                raise TransitionError(f"início fora de sequência: esperado {current}, recebido {stage}")
            if stage_state["status"] == "running":
                raise TransitionError(f"stage {stage} já está running")
            stage_state["status"] = "running"
            attempts = state["attempts"]
            assert isinstance(attempts, dict)
            attempts[stage] = int(attempts.get(stage, 0)) + 1
            continue

        if kind == "stage_skipped":
            if stage != current or stage_state["status"] != "pending":
                raise TransitionError(f"stage {stage} não pode ser ignorada neste estado")
            stage_state["status"] = "skipped"
            state["current_stage"] = NEXT_STAGE[stage]
            continue
        if kind not in {"stage_passed", "stage_failed", "needs_input"}:
            continue
        if stage != current or stage_state["status"] != "running":
            raise TransitionError(f"resultado sem stage running: {stage}")
        stage_state["status"] = STAGE_RESULT[kind]

        if kind in {"stage_passed", "stage_failed"}:
            gate = event.get("gate")
            gate_status = event.get("gate_status")
            if isinstance(gate, str) and isinstance(gate_status, str):
                gates = state["gates"]
                assert isinstance(gates, dict)
                gates[gate] = gate_status
        if kind == "stage_passed":
            commit_sha = event.get("commit_sha")
            if isinstance(commit_sha, str):
                stage_state["commit_sha"] = commit_sha
        if kind == "stage_passed":
            state["current_stage"] = NEXT_STAGE[stage]
        elif kind == "stage_failed":
            reason = event.get("reason")
            if isinstance(reason, str):
                stage_state["reason"] = reason
    return state


def import_legacy_state(run_id: str, feature_id: str, legacy: dict[str, object]) -> dict[str, object]:
    state = empty_state(run_id, feature_id)
    raw_stages = legacy.get("stages")
    stages = state["stages"]
    assert isinstance(stages, dict)
    if not isinstance(raw_stages, dict):
        return state

    for legacy_name, stage_name in LEGACY_STAGE_TO_EVENT.items():
        raw = raw_stages.get(legacy_name)
        if not isinstance(raw, dict):
            continue
        status = raw.get("status")
        if isinstance(status, str) and status in {"done", "running", "failed", "needs_input", "pending"}:
            stage_state = stages[stage_name]
            assert isinstance(stage_state, dict)
            stage_state["status"] = status

    raw_gates = legacy.get("gates")
    gates = state["gates"]
    assert isinstance(gates, dict)
    if isinstance(raw_gates, dict):
        for name, result in raw_gates.items():
            if isinstance(result, dict) and isinstance(result.get("status"), str):
                gates[str(name)] = str(result["status"])
    running = next(
        (
            stage for stage in STAGES
            if isinstance(stages[stage], dict) and stages[stage].get("status") == "running"
        ),
        None,
    )
    state["current_stage"] = running or next(
        (
            stage for stage in STAGES
            if isinstance(stages[stage], dict) and stages[stage].get("status") != "done"
        ),
        None,
    )
    return state


def start_stage(
    event_path: Path,
    run_id: str,
    feature_id: str,
    stage: str,
    *,
    executor: str | None = None,
    attempt: int | None = None,
) -> dict[str, object]:
    events = read_events(event_path)
    state = replay(run_id, feature_id, events)
    if stage != state["current_stage"]:
        raise TransitionError(f"início fora de sequência: esperado {state['current_stage']}, recebido {stage}")
    event: dict[str, object] = {
        "event": "stage_started",
        "run_id": run_id,
        "feature_id": feature_id,
        "stage": stage,
    }
    if executor:
        event["executor"] = executor
    if attempt is not None:
        event["attempt"] = attempt
    append_event(event_path, event)
    return replay(run_id, feature_id, read_events(event_path))


def finish_stage(
    event_path: Path,
    run_id: str,
    feature_id: str,
    stage: str,
    *,
    passed: bool,
    gate: str | None = None,
    reason: str | None = None,
    executor: str | None = None,
    attempt: int | None = None,
    commit_sha: str | None = None,
) -> dict[str, object]:
    if stage not in STAGES:
        raise TransitionError(f"stage desconhecida: {stage}")
    events = read_events(event_path)
    state = replay(run_id, feature_id, events)
    stage_state = state["stages"]
    assert isinstance(stage_state, dict)
    current = stage_state[stage]
    assert isinstance(current, dict)
    if current.get("status") != "running":
        raise TransitionError(f"stage {stage} não está running")

    event: dict[str, object] = {
        "event": "stage_passed" if passed else "stage_failed",
        "run_id": run_id,
        "feature_id": feature_id,
        "stage": stage,
    }
    if gate:
        event["gate"] = gate
        event["gate_status"] = "PASS" if passed else "FAIL"
    if reason:
        event["reason"] = reason
    if executor:
        event["executor"] = executor
    if attempt is not None:
        event["attempt"] = attempt
    if commit_sha:
        event["commit_sha"] = commit_sha
    append_event(event_path, event)
    return replay(run_id, feature_id, read_events(event_path))


def latest_commit_sha(events: list[Event], run_id: str, feature_id: str) -> str | None:
    for event in reversed(events):
        if (
            event.get("run_id") == run_id
            and event.get("feature_id") == feature_id
            and event.get("event") == "stage_passed"
            and isinstance(event.get("commit_sha"), str)
        ):
            return str(event["commit_sha"])
    return None


def request_input(
    event_path: Path,
    run_id: str,
    feature_id: str,
    stage: str,
    reason: str,
) -> dict[str, object]:
    if stage not in STAGES:
        raise TransitionError(f"stage desconhecida: {stage}")
    if not reason.strip():
        raise TransitionError("needs_input exige motivo")
    events = read_events(event_path)
    state = replay(run_id, feature_id, events)
    stages = state["stages"]
    assert isinstance(stages, dict)
    stage_state = stages[stage]
    assert isinstance(stage_state, dict)
    if stage != state["current_stage"] or stage_state.get("status") != "running":
        raise TransitionError(f"stage {stage} não está running")
    append_event(event_path, {
        "event": "needs_input",
        "run_id": run_id,
        "feature_id": feature_id,
        "stage": stage,
        "reason": reason,
    })
    return replay(run_id, feature_id, read_events(event_path))


def skip_stage(
    event_path: Path,
    run_id: str,
    feature_id: str,
    stage: str,
    reason: str,
) -> dict[str, object]:
    if stage not in STAGES:
        raise TransitionError(f"stage desconhecida: {stage}")
    if not reason.strip():
        raise TransitionError("stage_skipped exige motivo")
    events = read_events(event_path)
    state = replay(run_id, feature_id, events)
    stages = state["stages"]
    assert isinstance(stages, dict)
    stage_state = stages[stage]
    assert isinstance(stage_state, dict)
    if stage != state["current_stage"] or stage_state.get("status") != "pending":
        raise TransitionError(f"stage {stage} não pode ser ignorada neste estado")
    append_event(event_path, {
        "event": "stage_skipped",
        "run_id": run_id,
        "feature_id": feature_id,
        "stage": stage,
        "reason": reason,
    })
    return replay(run_id, feature_id, read_events(event_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline determinístico Ada Harness 2.0.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--start", choices=STAGES)
    result_group = parser.add_mutually_exclusive_group()
    result_group.add_argument("--pass-stage", choices=STAGES)
    result_group.add_argument("--fail-stage", choices=STAGES)
    result_group.add_argument("--needs-input", choices=STAGES)
    parser.add_argument("--gate")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)

    event_path = args.root / ".harness" / "runs" / args.run / args.feature / "events.jsonl"
    try:
        if args.start:
            state = start_stage(event_path, args.run, args.feature, args.start)
        elif args.pass_stage:
            state = finish_stage(
                event_path,
                args.run,
                args.feature,
                args.pass_stage,
                passed=True,
                gate=args.gate,
            )
        elif args.fail_stage:
            state = finish_stage(
                event_path,
                args.run,
                args.feature,
                args.fail_stage,
                passed=False,
                gate=args.gate,
                reason=args.reason,
            )
        elif args.needs_input:
            state = request_input(
                event_path,
                args.run,
                args.feature,
                args.needs_input,
                args.reason or "",
            )
        else:
            state = replay(args.run, args.feature, read_events(event_path))
    except (EventError, TransitionError) as exc:
        print(f"pipeline: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
