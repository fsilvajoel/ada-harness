"""Testes do event log append-only."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.events import EventError, append_event, read_events


def test_append_and_read_event(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    append_event(path, {
        "event": "stage_started",
        "run_id": "run-1",
        "feature_id": "E9-F02",
        "stage": "plan",
    })
    events = read_events(path)
    assert len(events) == 1
    assert events[0]["feature_id"] == "E9-F02"
    assert isinstance(events[0]["timestamp"], str)


def test_event_requires_fields(tmp_path: Path) -> None:
    with pytest.raises(EventError):
        append_event(tmp_path / "events.jsonl", {"event": "stage_started"})


def test_invalid_jsonl_line_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(EventError, match="JSON inválido"):
        read_events(path)
