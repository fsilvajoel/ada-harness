"""Métricas por feature e engine (Onda 6).

`summarize_events` é o resumo legado usado pelo dashboard. `build_report` gera o relatório comparativo por
feature e por engine a partir dos event logs, só com agregados (nunca conteúdo de prompt, request ou saída).

Regras de honestidade dos números:
- consumo desconhecido (tentativa sem `usage` reportado pelo worker) é contado à parte em `usage_unknown_attempts`
  e nunca somado como zero;
- custo só existe com taxa explícita (`workers.<nome>.rates.prompt_per_1k`/`completion_per_1k`) **e** consumo
  conhecido em todas as tentativas daquela engine; é sempre rotulado `estimate`. Caso contrário fica `null`
  com o motivo em `cost_unavailable`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]


def _usage(event: dict[str, Json]) -> dict[str, Json]:
    usage = event.get("usage")
    return usage if isinstance(usage, dict) else {}


def _usage_known(usage: dict[str, Json]) -> bool:
    return any(type(usage.get(key)) is int for key in ("prompt_tokens", "completion_tokens"))


def summarize_events(
    events: Iterable[dict[str, Json]],
    *,
    engine_rates: dict[str, dict[str, float]] | None = None,
) -> dict[str, object]:
    stage_attempts: dict[str, int] = {}
    engine_usage: dict[str, dict[str, int]] = {}
    stage_duration_seconds: dict[str, float] = {}
    active_attempts: dict[tuple[str, int], datetime] = {}
    first_started: datetime | None = None
    last_finished: datetime | None = None
    prompt_tokens = 0
    completion_tokens = 0
    gate_failures = 0
    worker_failures = 0
    preflight_failures = 0
    commit_failures = 0
    skipped_stages = 0
    fallbacks = 0
    review_outcomes: dict[str, int] = {}

    for event in events:
        event_type = event.get("event")
        stage = str(event.get("stage", ""))
        timestamp: datetime | None = None
        raw_timestamp = event.get("timestamp")
        if isinstance(raw_timestamp, str):
            try:
                timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except ValueError:
                timestamp = None

        if stage and event_type == "stage_started":
            stage_attempts[stage] = stage_attempts.get(stage, 0) + 1
            attempt = event.get("attempt")
            attempt_number = attempt if type(attempt) is int and attempt > 0 else stage_attempts[stage]
            if timestamp is not None:
                active_attempts[(stage, attempt_number)] = timestamp
                first_started = min(first_started, timestamp) if first_started else timestamp

        if stage and event_type in {"stage_passed", "stage_failed", "needs_input"}:
            attempt = event.get("attempt")
            attempt_number = attempt if type(attempt) is int and attempt > 0 else stage_attempts.get(stage, 1)
            started = active_attempts.pop((stage, attempt_number), None)
            if started is not None and timestamp is not None:
                elapsed = max(0.0, (timestamp - started).total_seconds())
                stage_duration_seconds[stage] = stage_duration_seconds.get(stage, 0.0) + elapsed
                last_finished = max(last_finished, timestamp) if last_finished else timestamp

        engine = str(event.get("executor", ""))
        usage = _usage(event)
        if engine and (event_type == "attempt_result" or usage):
            record = engine_usage.setdefault(
                engine,
                {"events": 0, "prompt_tokens": 0, "completion_tokens": 0, "usage_unknown": 0},
            )
            if event_type == "attempt_result" or usage:
                record["events"] += 1
            if event_type == "attempt_result" and not _usage_known(usage):
                record["usage_unknown"] += 1

        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if type(prompt) is int and prompt >= 0:
            prompt_tokens += prompt
            if engine:
                engine_usage[engine]["prompt_tokens"] += prompt
        if type(completion) is int and completion >= 0:
            completion_tokens += completion
            if engine:
                engine_usage[engine]["completion_tokens"] += completion

        if event_type == "stage_failed" and (event.get("gate") or event.get("failure_type") == "gate-fail"):
            gate_failures += 1
        if event_type == "stage_failed" and event.get("failure_type") == "worker-error":
            worker_failures += 1
        if event_type == "stage_failed" and event.get("failure_type") == "preflight-fail":
            preflight_failures += 1
        if event_type == "stage_failed" and str(event.get("reason", "")).startswith("commit-error:"):
            commit_failures += 1
        if event_type == "stage_skipped":
            skipped_stages += 1
        if event_type == "fallback":
            fallbacks += 1
        if event_type in {"review_accepted", "review_rejected", "review_needs_input"}:
            key = str(event_type)
            review_outcomes[key] = review_outcomes.get(key, 0) + 1

    cost_by_engine: dict[str, float] = {}
    unpriced_engines: list[str] = []
    for engine, usage in engine_usage.items():
        rates = engine_rates.get(engine) if engine_rates is not None else None
        if not isinstance(rates, dict) or usage["usage_unknown"]:
            # Sem taxa explícita ou com tentativa de consumo desconhecido: custo não é estimável (nunca 0).
            unpriced_engines.append(engine)
            continue
        prompt_rate = rates.get("prompt_per_1k")
        completion_rate = rates.get("completion_per_1k")
        if (
            type(prompt_rate) not in {int, float}
            or type(completion_rate) not in {int, float}
            or not math.isfinite(prompt_rate)
            or not math.isfinite(completion_rate)
            or prompt_rate < 0
            or completion_rate < 0
        ):
            raise ValueError(f"taxas inválidas para engine {engine}")
        cost_by_engine[engine] = (
            usage["prompt_tokens"] * prompt_rate
            + usage["completion_tokens"] * completion_rate
        ) / 1000

    total_cost: float | None = (
        sum(cost_by_engine.values()) if not unpriced_engines else None
    )

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "stage_attempts": stage_attempts,
        "stage_retries": {stage: max(0, attempts - 1) for stage, attempts in stage_attempts.items()},
        "stage_duration_seconds": stage_duration_seconds,
        "feature_duration_seconds": (
            max(0.0, (last_finished - first_started).total_seconds())
            if first_started is not None and last_finished is not None else None
        ),
        "gate_failures": gate_failures,
        "worker_failures": worker_failures,
        "preflight_failures": preflight_failures,
        "commit_failures": commit_failures,
        "skipped_stages": skipped_stages,
        "fallbacks": fallbacks,
        "engine_usage": engine_usage,
        "estimated_cost": total_cost,
        "cost_by_engine": cost_by_engine,
        "unpriced_engines": sorted(unpriced_engines),
        "review_outcomes": review_outcomes,
    }


type Rates = dict[str, dict[str, float]]

FAILURE_EVENTS = frozenset({"stage_failed", "needs_input"})
FINISH_EVENTS = frozenset({"stage_passed", "stage_failed", "needs_input"})


def _timestamp(event: dict[str, Json]) -> datetime | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _failure_kind(event: dict[str, Json]) -> str:
    failure = event.get("failure_type")
    if isinstance(failure, str) and failure:
        return failure
    if event.get("event") == "needs_input":
        return "needs-input"
    if str(event.get("reason", "")).startswith("commit-error:"):
        return "commit-error"
    if event.get("gate_status") == "FAIL":
        return "gate-fail"
    return "unknown"


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


@dataclass
class _Attempt:
    run_id: str
    feature_id: str
    stage: str
    executor: str
    first_of_stage: bool
    started: datetime | None = None
    finished: datetime | None = None
    outcome: str | None = None  # passed | failed
    failure: str | None = None
    gate: str | None = None  # PASS | FAIL quando a stage executou gate
    worker_called: bool = False
    usage_known: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class _Group:
    attempts: int = 0
    passed: int = 0
    failed: int = 0
    first_attempts: int = 0
    first_attempt_passed: int = 0
    retries: int = 0
    fallbacks_from: int = 0
    fallbacks_to: int = 0
    gate_runs: int = 0
    gate_failures: int = 0
    failure_types: dict[str, int] = field(default_factory=dict)
    duration_seconds: float = 0.0
    worker_calls: int = 0
    usage_known_attempts: int = 0
    usage_unknown_attempts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    engines: set[str] = field(default_factory=set)

    def add(self, attempt: _Attempt) -> None:
        self.attempts += 1
        self.engines.add(attempt.executor)
        if attempt.first_of_stage:
            self.first_attempts += 1
        else:
            self.retries += 1
        if attempt.outcome == "passed":
            self.passed += 1
            if attempt.first_of_stage:
                self.first_attempt_passed += 1
        elif attempt.outcome == "failed":
            self.failed += 1
            kind = attempt.failure or "unknown"
            self.failure_types[kind] = self.failure_types.get(kind, 0) + 1
        if attempt.started is not None and attempt.finished is not None:
            self.duration_seconds += max(0.0, (attempt.finished - attempt.started).total_seconds())
        if attempt.worker_called:
            self.worker_calls += 1
            if attempt.usage_known:
                self.usage_known_attempts += 1
                self.prompt_tokens += attempt.prompt_tokens
                self.completion_tokens += attempt.completion_tokens
            else:
                self.usage_unknown_attempts += 1

    def tokens(self) -> dict[str, object]:
        known = self.usage_known_attempts > 0
        return {
            "prompt_tokens": self.prompt_tokens if known else None,
            "completion_tokens": self.completion_tokens if known else None,
            "total_tokens": self.prompt_tokens + self.completion_tokens if known else None,
            "usage_known_attempts": self.usage_known_attempts,
            "usage_unknown_attempts": self.usage_unknown_attempts,
            "usage_complete": self.worker_calls > 0 and self.usage_unknown_attempts == 0,
        }

    def summary(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "passed": self.passed,
            "failed": self.failed,
            "first_attempt_success_rate": _rate(self.first_attempt_passed, self.first_attempts),
            "first_attempts": self.first_attempts,
            "retries": self.retries,
            "retry_rate": _rate(self.retries, self.attempts),
            "fallbacks_from": self.fallbacks_from,
            "fallbacks_to": self.fallbacks_to,
            "fallback_rate": _rate(self.fallbacks_from, self.attempts),
            "gate_runs": self.gate_runs,
            "gate_failures": self.gate_failures,
            "failure_types": dict(sorted(self.failure_types.items())),
            "duration_seconds": round(self.duration_seconds, 3),
            "worker_calls": self.worker_calls,
            **self.tokens(),
        }


def _validate_rates(engine: str, rates: dict[str, float]) -> tuple[float, float]:
    prompt_rate = rates.get("prompt_per_1k")
    completion_rate = rates.get("completion_per_1k")
    for value in (prompt_rate, completion_rate):
        if type(value) not in {int, float} or not math.isfinite(float(value)) or float(value) < 0:  # type: ignore[arg-type]
            raise ValueError(f"taxas inválidas para engine {engine}")
    return float(prompt_rate), float(completion_rate)  # type: ignore[arg-type]


def _cost(group: _Group, engine: str, rates: Rates | None) -> tuple[float | None, str | None]:
    engine_rates = rates.get(engine) if rates is not None else None
    if not isinstance(engine_rates, dict):
        return None, "sem taxa configurada"
    prompt_rate, completion_rate = _validate_rates(engine, engine_rates)
    if group.worker_calls == 0:
        return None, "nenhuma chamada de worker"
    if group.usage_unknown_attempts:
        return None, f"consumo desconhecido em {group.usage_unknown_attempts} tentativa(s)"
    return round((group.prompt_tokens * prompt_rate + group.completion_tokens * completion_rate) / 1000, 6), None


def _attempts_from_log(events: list[dict[str, Json]]) -> tuple[list[_Attempt], list[dict[str, Json]], int]:
    """Reconstrói tentativas de um event log. Retorna (tentativas, fallbacks, stages puladas)."""
    attempts: list[_Attempt] = []
    open_attempts: dict[tuple[str, str, str, int], _Attempt] = {}
    seen_stages: set[tuple[str, str, str]] = set()
    fallbacks: list[dict[str, Json]] = []
    skipped = 0
    for event in events:
        kind = event.get("event")
        run_id, feature_id = str(event.get("run_id", "")), str(event.get("feature_id", ""))
        stage = str(event.get("stage", ""))
        raw_attempt = event.get("attempt")
        number = raw_attempt if type(raw_attempt) is int else 0
        key = (run_id, feature_id, stage, number)
        if kind == "stage_skipped":
            skipped += 1
        elif kind == "fallback":
            fallbacks.append(event)
        elif kind == "stage_started" and stage:
            stage_key = (run_id, feature_id, stage)
            attempt = _Attempt(
                run_id, feature_id, stage, str(event.get("executor") or "-"),
                first_of_stage=stage_key not in seen_stages, started=_timestamp(event),
            )
            seen_stages.add(stage_key)
            open_attempts[key] = attempt
            attempts.append(attempt)
        elif kind == "attempt_result" and key in open_attempts:
            attempt = open_attempts[key]
            attempt.worker_called = True
            usage = _usage(event)
            if _usage_known(usage):
                attempt.usage_known = True
                prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
                attempt.prompt_tokens = prompt if type(prompt) is int and prompt >= 0 else 0
                attempt.completion_tokens = completion if type(completion) is int and completion >= 0 else 0
        elif kind in FINISH_EVENTS and key in open_attempts:
            attempt = open_attempts.pop(key)
            attempt.finished = _timestamp(event)
            attempt.outcome = "passed" if kind == "stage_passed" else "failed"
            if kind in FAILURE_EVENTS:
                attempt.failure = _failure_kind(event)
            gate_status = event.get("gate_status")
            if gate_status in {"PASS", "FAIL"}:
                attempt.gate = str(gate_status)
    return attempts, fallbacks, skipped


def build_report(
    logs: Iterable[list[dict[str, Json]]],
    *,
    engine_rates: Rates | None = None,
) -> dict[str, object]:
    """Relatório agregado por feature (run+feature) e por engine. Só números; nenhum texto de prompt/saída."""
    by_feature: dict[str, _Group] = {}
    by_engine: dict[str, _Group] = {}
    skipped_by_feature: dict[str, int] = {}
    for events in logs:
        attempts, fallbacks, skipped = _attempts_from_log(events)
        for attempt in attempts:
            label = f"{attempt.run_id}/{attempt.feature_id}"
            for group in (by_feature.setdefault(label, _Group()), by_engine.setdefault(attempt.executor, _Group())):
                group.add(attempt)
                if attempt.gate is not None:
                    group.gate_runs += 1
                    group.gate_failures += attempt.gate == "FAIL"
        for event in fallbacks:
            source = str(event.get("from_executor") or "-")
            target = str(event.get("executor") or "-")
            label = f"{event.get('run_id', '')}/{event.get('feature_id', '')}"
            by_engine.setdefault(source, _Group()).fallbacks_from += 1
            by_engine.setdefault(target, _Group()).fallbacks_to += 1
            by_feature.setdefault(label, _Group()).fallbacks_from += 1
        if attempts:
            label = f"{attempts[0].run_id}/{attempts[0].feature_id}"
            skipped_by_feature[label] = skipped_by_feature.get(label, 0) + skipped

    engines: dict[str, object] = {}
    for engine, group in sorted(by_engine.items()):
        cost, reason = _cost(group, engine, engine_rates)
        engines[engine] = {
            **group.summary(),
            "estimated_cost": cost,
            "cost_basis": "estimate" if cost is not None else None,
            "cost_unavailable": reason,
        }
    features: dict[str, object] = {}
    for label, group in sorted(by_feature.items()):
        features[label] = {
            **group.summary(),
            "skipped_stages": skipped_by_feature.get(label, 0),
            "engines": sorted(group.engines),
        }
    return {
        "schemaVersion": 1,
        "note": "Agregados de event log. Custo é estimativa e só aparece com taxa explícita e consumo conhecido.",
        "features": features,
        "engines": engines,
    }


def load_event_logs(root: Path) -> list[list[dict[str, Json]]]:
    """Lê `.harness/runs/<run>/<feature>/events.jsonl`; linhas inválidas são ignoradas."""
    logs: list[list[dict[str, Json]]] = []
    for path in sorted((root / ".harness" / "runs").glob("*/*/events.jsonl")):
        events: list[dict[str, Json]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
        logs.append(events)
    return logs


def rates_from_config(config: dict[str, object]) -> Rates:
    rates: Rates = {}
    workers = config.get("workers")
    if not isinstance(workers, dict):
        return rates
    for name, worker in workers.items():
        if isinstance(worker, dict) and isinstance(worker.get("rates"), dict):
            raw = worker["rates"]
            rates[str(name)] = {
                key: value for key, value in raw.items()
                if key in {"prompt_per_1k", "completion_per_1k"} and type(value) in {int, float}
            }
    return rates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Relatório de métricas por feature e engine (sem conteúdo de prompt).")
    parser.add_argument("root", type=Path, help="raiz do projeto com .harness/runs")
    parser.add_argument("--config", type=Path, default=None, help="harness.project.yaml (taxas em workers.<nome>.rates)")
    args = parser.parse_args(argv)
    rates: Rates | None = None
    if args.config is not None:
        import yaml  # dependência já usada pelo harness

        loaded = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
        rates = rates_from_config(loaded if isinstance(loaded, dict) else {})
    try:
        report = build_report(load_event_logs(args.root.resolve()), engine_rates=rates)
    except ValueError as error:
        print(f"erro: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
