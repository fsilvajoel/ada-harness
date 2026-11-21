#!/usr/bin/env python3
"""Event log append-only do pipeline Ada Harness 2.0."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]
type Event = dict[str, Json]

REQUIRED_FIELDS: Final[frozenset[str]] = frozenset({
    "event",
    "run_id",
    "feature_id",
    "stage",
})


class EventError(ValueError):
    pass


def validate_event(event: Event) -> None:
    missing = sorted(REQUIRED_FIELDS - event.keys())
    if missing:
        raise EventError(f"evento sem campos obrigatórios: {', '.join(missing)}")
    if not all(isinstance(event[field], str) and event[field] for field in REQUIRED_FIELDS):
        raise EventError("campos obrigatórios do evento precisam ser textos não vazios")


def append_event(path: Path, event: Event) -> None:
    validate_event(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(event)
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def read_events(path: Path) -> list[Event]:
    if not path.exists():
        return []
    events: list[Event] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EventError(f"{path}:{line_number}: JSON inválido") from exc
            if not isinstance(data, dict):
                raise EventError(f"{path}:{line_number}: evento precisa ser mapa")
            validate_event(data)
            events.append(data)
    return events
