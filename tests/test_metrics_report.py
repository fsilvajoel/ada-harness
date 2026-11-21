"""Testes do relatório da Onda 6 (por feature e engine), só com event logs fake."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.metrics import Json, build_report, main, summarize_events

type Event = dict[str, Json]


def _ts(second: int) -> str:
    return f"2026-11-21T10:00:{second:02d}+00:00"


def _attempt(
    stage: str,
    executor: str,
    attempt: int,
    start: int,
    end: int,
    *,
    passed: bool,
    usage: dict[str, Json] | None,
    gate: str | None = "verify",
    failure_type: str | None = None,
    feature: str = "E1-F01",
    run: str = "run-1",
) -> list[Event]:
    base: Event = {"run_id": run, "feature_id": feature, "stage": stage, "attempt": attempt}
    result: Event = {**base, "event": "attempt_result", "executor": executor, "timestamp": _ts(end)}
    if usage is not None:
        result["usage"] = usage
    if failure_type is not None:
        result["failure_type"] = failure_type
    finish: Event = {
        **base,
        "event": "stage_passed" if passed else "stage_failed",
        "executor": executor,
        "timestamp": _ts(end),
        "reason": "SEGREDO-NAO-VAZA prompt completo",
    }
    if gate is not None:
        finish["gate"] = gate
        finish["gate_status"] = "PASS" if passed else "FAIL"
    if failure_type is not None:
        finish["failure_type"] = failure_type
    return [
        {**base, "event": "stage_started", "executor": executor, "timestamp": _ts(start)},
        result,
        finish,
    ]


def _log() -> list[Event]:
    events: list[Event] = []
    events += _attempt("plan", "alpha", 1, 0, 2, passed=True, usage={"prompt_tokens": 1000, "completion_tokens": 200}, gate=None)
    events += _attempt(
        "implement", "alpha", 1, 2, 5, passed=False,
        usage={"prompt_tokens": 2000, "completion_tokens": 500}, failure_type="gate-fail",
    )
    events.append({
        "run_id": "run-1", "feature_id": "E1-F01", "stage": "implement", "attempt": 2,
        "event": "fallback", "from_executor": "alpha", "executor": "beta", "failure_type": "gate-fail",
    })
    events += _attempt("implement", "beta", 2, 5, 15, passed=True, usage=None)
    events.append({"run_id": "run-1", "feature_id": "E1-F01", "stage": "mutation_e2e", "event": "stage_skipped"})
    return events


def test_report_aggregates_per_feature_and_engine() -> None:
    report = build_report([_log()], engine_rates={"alpha": {"prompt_per_1k": 0.5, "completion_per_1k": 1.0}})
    feature = report["features"]["run-1/E1-F01"]  # type: ignore[index]
    assert feature["attempts"] == 3
    assert feature["passed"] == 2 and feature["failed"] == 1
    assert feature["first_attempts"] == 2 and feature["retries"] == 1
    assert feature["first_attempt_success_rate"] == 0.5
    assert feature["retry_rate"] == round(1 / 3, 4)
    assert feature["fallbacks_from"] == 1
    assert feature["gate_runs"] == 2 and feature["gate_failures"] == 1
    assert feature["failure_types"] == {"gate-fail": 1}
    assert feature["duration_seconds"] == 15.0
    assert feature["skipped_stages"] == 1
    assert feature["engines"] == ["alpha", "beta"]

    alpha = report["engines"]["alpha"]  # type: ignore[index]
    assert alpha["attempts"] == 2 and alpha["fallbacks_from"] == 1 and alpha["fallback_rate"] == 0.5
    assert alpha["prompt_tokens"] == 3000 and alpha["completion_tokens"] == 700
    assert alpha["usage_complete"] is True
    # (3000 * 0.5 + 700 * 1.0) / 1000
    assert alpha["estimated_cost"] == 2.2 and alpha["cost_basis"] == "estimate"
    beta = report["engines"]["beta"]  # type: ignore[index]
    assert beta["fallbacks_to"] == 1 and beta["first_attempts"] == 0 and beta["retries"] == 1


def test_engine_without_usage_is_unknown_not_zero() -> None:
    rates = {"beta": {"prompt_per_1k": 1.0, "completion_per_1k": 1.0}}
    report = build_report([_log()], engine_rates=rates)
    beta = report["engines"]["beta"]  # type: ignore[index]
    assert beta["usage_unknown_attempts"] == 1 and beta["usage_known_attempts"] == 0
    assert beta["prompt_tokens"] is None and beta["total_tokens"] is None
    assert beta["usage_complete"] is False
    assert beta["estimated_cost"] is None and beta["cost_basis"] is None
    assert "desconhecido" in str(beta["cost_unavailable"])
    feature = report["features"]["run-1/E1-F01"]  # type: ignore[index]
    assert feature["usage_unknown_attempts"] == 1 and feature["usage_complete"] is False

    # Legado do dashboard: com taxa, engine sem usage não pode virar custo 0.
    summary = summarize_events(_log(), engine_rates=rates)
    assert "beta" in summary["unpriced_engines"]  # type: ignore[operator]
    assert summary["estimated_cost"] is None


def test_failure_counted_with_type_and_unknown_fallback() -> None:
    events = _attempt(
        "refinement", "alpha", 1, 0, 3, passed=False,
        usage={"prompt_tokens": 10, "completion_tokens": 1}, gate=None, failure_type="timeout",
    )
    events += _attempt("refinement", "alpha", 2, 3, 4, passed=False, usage={"prompt_tokens": 5}, gate=None)
    report = build_report([events])
    feature = report["features"]["run-1/E1-F01"]  # type: ignore[index]
    assert feature["failed"] == 2 and feature["passed"] == 0
    assert feature["first_attempt_success_rate"] == 0.0
    assert feature["failure_types"] == {"timeout": 1, "unknown": 1}
    assert feature["prompt_tokens"] == 15 and feature["completion_tokens"] == 1


def test_missing_rate_gives_no_cost() -> None:
    report = build_report([_log()], engine_rates={})
    for engine in ("alpha", "beta"):
        entry = report["engines"][engine]  # type: ignore[index]
        assert entry["estimated_cost"] is None
        assert entry["cost_unavailable"] == "sem taxa configurada"
    assert build_report([_log()])["engines"]["alpha"]["estimated_cost"] is None  # type: ignore[index]


def test_report_has_no_prompt_or_reason_text_and_cli_reads_runs(tmp_path: Path, capsys: object) -> None:
    run_dir = tmp_path / ".harness" / "runs" / "run-1" / "E1-F01"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in _log()) + "\nnao-json\n", encoding="utf-8"
    )
    config = tmp_path / "harness.project.yaml"
    config.write_text("workers:\n  alpha:\n    rates: {prompt_per_1k: 0.5, completion_per_1k: 1.0}\n", encoding="utf-8")
    assert main([str(tmp_path), "--config", str(config)]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "SEGREDO-NAO-VAZA" not in output
    parsed = json.loads(output)
    assert parsed["engines"]["alpha"]["estimated_cost"] == 2.2
    assert parsed["engines"]["beta"]["estimated_cost"] is None
