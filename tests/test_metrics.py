"""Testes de telemetria do pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.metrics import summarize_events


def test_metrics_summarize_usage_and_failures() -> None:
    events = [
        {
            "event": "stage_failed",
            "stage": "implement",
            "gate": "verify",
            "executor": "sonnet",
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        },
        {
            "event": "fallback",
            "stage": "implement",
            "executor": "claude",
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        },
        {
            "event": "stage_started",
            "stage": "implement",
            "executor": "sonnet",
        },
    ]
    summary = summarize_events(events)
    assert summary["prompt_tokens"] == 150
    assert summary["completion_tokens"] == 30
    assert summary["total_tokens"] == 180
    assert summary["gate_failures"] == 1
    assert summary["fallbacks"] == 1
    assert summary["stage_attempts"] == {"implement": 1}
    assert summary["engine_usage"]["sonnet"]["prompt_tokens"] == 100


def test_metrics_measure_attempts_durations_and_configured_cost() -> None:
    events = [
        {
            "event": "stage_started",
            "stage": "implement",
            "attempt": 1,
            "executor": "local",
            "timestamp": "2025-01-01T00:00:00Z",
        },
        {
            "event": "stage_failed",
            "stage": "implement",
            "attempt": 1,
            "failure_type": "worker-error",
            "executor": "local",
            "timestamp": "2025-01-01T00:00:05Z",
        },
        {
            "event": "stage_started",
            "stage": "implement",
            "attempt": 2,
            "executor": "local",
            "timestamp": "2025-01-01T00:00:06Z",
        },
        {
            "event": "attempt_result",
            "stage": "implement",
            "attempt": 2,
            "executor": "local",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
            "timestamp": "2025-01-01T00:00:10Z",
        },
        {
            "event": "stage_passed",
            "stage": "implement",
            "attempt": 2,
            "executor": "local",
            "gate": "verify",
            "timestamp": "2025-01-01T00:00:11Z",
        },
    ]
    summary = summarize_events(
        events,
        engine_rates={"local": {"prompt_per_1k": 0.1, "completion_per_1k": 0.2}},
    )
    assert summary["stage_attempts"] == {"implement": 2}
    assert summary["stage_retries"] == {"implement": 1}
    assert summary["stage_duration_seconds"] == {"implement": 10.0}
    assert summary["feature_duration_seconds"] == 11.0
    assert summary["worker_failures"] == 1
    assert summary["estimated_cost"] == 0.2
