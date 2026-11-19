#!/usr/bin/env python3
"""Comando `status`: calcula o estado do pipeline e grava `status.json`.

Fonte única: os cartões (`cards_glob` do `harness.project.yaml`), o `estado.json` de cada feature
(`<pasta dos cartões>/<feature>/estado.json`) e a fila de PM (`pm_queue`). Nada é gravado à mão: o
`status.json` é derivado e pode ser apagado e regerado. O orquestrador só pilota; o conhecimento fica nos
cartões e no estado.

Uso: `status.py <raiz do projeto> [--out status.json] [--print]`. Sem `--out`, grava `status_file` do yaml
(padrão `status.json` na raiz). Saída 0 sempre que o cálculo roda; 2 em configuração inválida.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import get_str, load_config

type Json = str | int | float | bool | None | list[Json] | dict[str, Json]
type JsonMap = dict[str, Json]

STEPS: tuple[str, ...] = (
    "plan",
    "refinement",
    "tests",
    "test_review",
    "implement",
    "mutation_e2e",
    "final_review",
    "closing",
    "done",
)
STEP_LABELS: dict[str, str] = {
    "plan": "plan",
    "refinement": "1b refinamento funcional",
    "tests": "tests",
    "test_review": "revisão dos testes",
    "implement": "implement",
    "mutation_e2e": "mutação / e2e",
    "final_review": "revisão final",
    "closing": "fechamento",
    "done": "done",
}
BLOCKED_STATUSES: frozenset[str] = frozenset({"quota", "needs_input", "failed", "card_error", "error"})
STAGE_TO_STEP: dict[str, str] = {
    "plan": "plan",
    "refinement": "refinement",
    "tests": "tests",
    "testReview": "test_review",
    "implement": "implement",
    "audit": "final_review",
    "review": "final_review",
}
CARD_KIND_STAGE: dict[str, str] = {"test-author": "tests", "implement": "implement"}
FEATURE_ID = re.compile(r"^(?P<delivery>.+)-F\d+$")


@dataclass
class Feature:
    """Uma feature: dados dos cartões (título, cartões) e o estado.json, se existe."""

    id: str
    title: str = ""
    delivery: str = ""
    cards: list[str] = field(default_factory=list)
    state: dict[str, Json] | None = None


def _as_map(value: Json | object) -> dict[str, Json]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def _stage(state: dict[str, Json] | None, name: str) -> dict[str, Json] | None:
    if state is None:
        return None
    stages = _as_map(state.get("stages"))
    raw = stages.get(name)
    return _as_map(raw) if isinstance(raw, dict) else None


def _status(stage: dict[str, Json] | None) -> str | None:
    value = stage.get("status") if stage else None
    return value if isinstance(value, str) else None


def _started(stage: dict[str, Json] | None) -> bool:
    return stage is not None and _status(stage) not in {None, "pending"}


def _gate_pass(state: dict[str, Json], name: str) -> bool:
    gate = _as_map(_as_map(state.get("gates")).get(name))
    return gate.get("status") == "PASS"


def satisfied_steps(state: dict[str, Json] | None) -> dict[str, bool]:
    """Para cada etapa do pipeline, se ela está cumprida segundo o estado.json.

    Estados antigos (sem `refinement`, `testReview` ou `gates`) contam a etapa como cumprida quando uma etapa
    posterior já andou: o pipeline cresceu depois deles.
    """
    result = {step: False for step in STEPS}
    if state is None:
        return result
    plan, refinement = _stage(state, "plan"), _stage(state, "refinement")
    tests, test_review = _stage(state, "tests"), _stage(state, "testReview")
    implement, audit, review = _stage(state, "implement"), _stage(state, "audit"), _stage(state, "review")

    result["plan"] = _status(plan) == "done" or _started(tests)
    result["refinement"] = _status(refinement) == "done" or (refinement is None and _started(tests))
    result["tests"] = _status(tests) == "done" or _started(implement)
    result["test_review"] = (
        _status(test_review) == "done"
        or _gate_pass(state, "tests-reviewed")
        or (test_review is None and _started(implement))
    )
    result["implement"] = _status(implement) == "done"
    approved = any(
        stage is not None and stage.get("verdict") == "aprovado" for stage in (audit, review)
    ) or _status(review) == "done"
    if "gates" in state:
        result["mutation_e2e"] = _gate_pass(state, "mutation") and _gate_pass(state, "e2e")
    else:
        result["mutation_e2e"] = approved
    result["final_review"] = approved
    result["closing"] = _status(review) == "done"
    result["done"] = result["closing"]
    return result


def current_step(state: dict[str, Json] | None) -> str:
    """Primeira etapa não cumprida; `done` quando todas estão."""
    done = satisfied_steps(state)
    for step in STEPS[:-1]:
        # A etapa só conta como pendente depois de todas as anteriores: o pipeline é sequencial.
        if not done[step]:
            return step
    return "done"


def blocked_info(state: dict[str, Json] | None) -> dict[str, Json] | None:
    """Primeiro estágio com status de bloqueio, ou None."""
    if state is None:
        return None
    for name in ("plan", "refinement", "tests", "testReview", "implement", "audit", "review"):
        stage = _stage(state, name)
        status = _status(stage)
        if stage is not None and status in BLOCKED_STATUSES:
            message = stage.get("message")
            return {
                "stage": name,
                "step": STAGE_TO_STEP[name],
                "status": status,
                "message": message if isinstance(message, str) else None,
            }
    return None


def _read_yaml(path: Path) -> dict[str, Json]:
    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{path.name}: ilegível ({type(exc).__name__})") from exc
    return _as_map(loaded)


def _str_list(value: Json | object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def load_cards(root: Path, cards_glob: str) -> tuple[dict[str, dict[str, Json]], dict[str, Feature]]:
    """Cartões (por id) e features declaradas nos arquivos de cartões."""
    cards: dict[str, dict[str, Json]] = {}
    features: dict[str, Feature] = {}
    for path in sorted(root.glob(cards_glob)):
        data = _read_yaml(path)
        delivery = str(data.get("delivery") or path.stem)
        for raw in data.get("cards") or []:
            card = _as_map(raw)
            card_id = str(card.get("id", ""))
            if card_id:
                card["delivery"] = delivery
                cards[card_id] = card
        for raw in data.get("features") or []:
            item = _as_map(raw)
            fid = str(item.get("id", ""))
            if fid:
                match = FEATURE_ID.match(fid)
                features[fid] = Feature(
                    id=fid,
                    title=str(item.get("title") or ""),
                    delivery=match.group("delivery") if match else delivery,
                    cards=_str_list(item.get("cards")),
                )
    return cards, features


def load_states(root: Path, cards_glob: str, features: dict[str, Feature]) -> None:
    """Anexa o estado.json às features; cria feature para pasta com estado que os cartões não listam."""
    folders = {path.parent for path in root.glob(cards_glob)}
    for folder in sorted(folders):
        for state_path in sorted(folder.glob("*/estado.json")):
            fid = state_path.parent.name
            try:
                loaded: object = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"{fid}/estado.json: ilegível ({type(exc).__name__})") from exc
            feature = features.setdefault(fid, Feature(id=fid))
            feature.state = _as_map(loaded)
            if not feature.delivery:
                match = FEATURE_ID.match(fid)
                feature.delivery = match.group("delivery") if match else ""


def parse_pm_queue(text: str) -> list[dict[str, str]]:
    """Entradas `## <cartão> (<feature>)` da fila de PM, com o campo `status`."""
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = re.match(r"^##\s+(\S+)\s+\(([^)]+)\)\s*$", line)
        if heading:
            current = {"card": heading.group(1), "feature": heading.group(2), "status": ""}
            entries.append(current)
            continue
        field_match = re.match(r"^-\s+status:\s*(\S+)", line)
        if field_match and current is not None and not current["status"]:
            current["status"] = field_match.group(1)
    return entries


def _card_state(card: dict[str, Json], feature: Feature | None, done_cards: set[str]) -> tuple[str, str | None]:
    """Estado do cartão derivado do estado da feature: done, ready, blocked ou waiting."""
    if feature is None:
        return "waiting", "cartão sem feature"
    done = satisfied_steps(feature.state)
    kind = str(card.get("kind") or "")
    stage_name = CARD_KIND_STAGE.get(kind)
    block = blocked_info(feature.state)
    if stage_name == "tests" and done["tests"] or stage_name == "implement" and done["implement"]:
        return "done", None
    if block is not None and (stage_name is None or block["stage"] == stage_name or block["stage"] == "refinement"):
        return "blocked", str(block["status"])
    deps = [str(dep) for dep in _str_list(card.get("dependsOn"))]
    pending = [dep for dep in deps if dep not in done_cards]
    if pending:
        return "waiting", "depende de " + ", ".join(pending)
    return "ready", None


def compute(root: Path, now: datetime | None = None) -> JsonMap:
    """Monta o conteúdo do status.json."""
    config = load_config(root, required=True)
    cards_glob = get_str(config, "cards_glob")
    if not cards_glob:
        raise ValueError("harness.project.yaml sem 'cards_glob'")
    cards, features = load_cards(root, cards_glob)
    load_states(root, cards_glob, features)

    pm_path = get_str(config, "pm_queue")
    pm_entries = parse_pm_queue((root / pm_path).read_text(encoding="utf-8")) if pm_path and (root / pm_path).is_file() else []
    pm_open = [entry for entry in pm_entries if entry["status"] == "aberta"]
    pm_open_features = {entry["feature"] for entry in pm_open}

    feature_rows: list[JsonMap] = []
    for fid in sorted(features):
        feature = features[fid]
        step = current_step(feature.state)
        block = blocked_info(feature.state)
        if fid in pm_open_features and block is None:
            block = {"stage": "refinement", "step": "refinement", "status": "needs_input", "message": "pendência de PM aberta"}
        completed = sum(1 for s in STEPS[:-1] if satisfied_steps(feature.state)[s])
        if step == "done":
            completed = len(STEPS) - 1
        percent = round(100 * completed / (len(STEPS) - 1))
        feature_rows.append(
            {
                "id": fid,
                "title": feature.title,
                "delivery": feature.delivery,
                "step": step,
                "has_state": feature.state is not None,
                "percent": percent,
                "blocked": block,
                "ready": block is None and step != "done",
                "cards": list(feature.cards),
            }
        )

    # Cartões: pronto/bloqueado/aguardando/feito, derivados do estado da feature.
    card_feature: dict[str, Feature] = {cid: feat for feat in features.values() for cid in feat.cards}
    done_cards: set[str] = set()
    for cid, card in cards.items():
        if _card_state(card, card_feature.get(cid), set())[0] == "done":
            done_cards.add(cid)
    card_rows: list[JsonMap] = []
    for cid, card in cards.items():
        feat = card_feature.get(cid)
        state, reason = _card_state(card, feat, done_cards)
        card_rows.append(
            {
                "id": cid,
                "feature": feat.id if feat else None,
                "kind": card.get("kind"),
                "state": state,
                "reason": reason,
            }
        )

    queue: dict[str, JsonMap] = {
        step: {"label": STEP_LABELS[step], "ready": [], "blocked": []} for step in STEPS
    }
    for row in feature_rows:
        bucket = queue[str(row["step"])]
        key = "blocked" if row["blocked"] else "ready"
        ids = bucket[key]
        assert isinstance(ids, list)
        ids.append(str(row["id"]))

    deliveries: dict[str, list[JsonMap]] = {}
    for row in feature_rows:
        deliveries.setdefault(str(row["delivery"]), []).append(row)
    delivery_rows: list[JsonMap] = []
    for name in sorted(deliveries):
        rows = deliveries[name]
        pcts = [int(str(r["percent"])) for r in rows]
        delivery_rows.append(
            {
                "id": name,
                "features": len(rows),
                "done": sum(1 for r in rows if r["step"] == "done"),
                "blocked": sum(1 for r in rows if r["blocked"]),
                "percent": round(sum(pcts) / len(pcts)),
            }
        )

    blocked_by_status: dict[str, list[Json]] = {}
    for row in feature_rows:
        block = row["blocked"]
        if isinstance(block, dict):
            blocked_by_status.setdefault(str(block["status"]), []).append(str(row["id"]))
    card_counts: dict[str, int] = {}
    for crow in card_rows:
        card_counts[str(crow["state"])] = card_counts.get(str(crow["state"]), 0) + 1

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "generated_at": stamp,
        "steps": [{"id": s, "label": STEP_LABELS[s]} for s in STEPS],
        "queue": queue,  # type: ignore[dict-item]
        "features": feature_rows,  # type: ignore[dict-item]
        "cards": card_rows,  # type: ignore[dict-item]
        "card_counts": card_counts,  # type: ignore[dict-item]
        "blocked_by_status": blocked_by_status,  # type: ignore[dict-item]
        "deliveries": delivery_rows,  # type: ignore[dict-item]
        "pm_queue": {"open": len(pm_open), "entries": pm_open},  # type: ignore[dict-item]
    }


def summary(status: JsonMap) -> str:
    """Resumo curto em texto, para terminal."""
    lines: list[str] = []
    queue = status["queue"]
    assert isinstance(queue, dict)
    lines.append("Fila por etapa (prontas / bloqueadas):")
    for step in STEPS:
        bucket = _as_map(queue[step])
        ready, blocked = _str_list(bucket["ready"]), _str_list(bucket["blocked"])
        if ready or blocked:
            lines.append(f"  {STEP_LABELS[step]}: {', '.join(ready) or '-'} / {', '.join(blocked) or '-'}")
    lines.append("Bloqueios:")
    blocked_map = _as_map(status["blocked_by_status"])
    for name, ids in blocked_map.items():
        lines.append(f"  {name}: {', '.join(_str_list(ids))}")
    if not blocked_map:
        lines.append("  nenhum")
    lines.append("Entregas:")
    for raw in status["deliveries"] if isinstance(status["deliveries"], list) else []:
        d = _as_map(raw)
        lines.append(f"  {d['id']}: {d['percent']}% ({d['done']}/{d['features']} features prontas, {d['blocked']} bloqueadas)")
    lines.append(f"Cartões: {json.dumps(status['card_counts'], ensure_ascii=False)}")
    lines.append(f"Fila de PM aberta: {_as_map(status['pm_queue'])['open']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calcula o status.json do pipeline.")
    parser.add_argument("root", type=Path, help="raiz do projeto")
    parser.add_argument("--out", type=Path, default=None, help="arquivo de saída (padrão: status_file do yaml)")
    parser.add_argument("--print", dest="show", action="store_true", help="imprime o resumo")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    try:
        status = compute(root)
        config = load_config(root)
    except (ValueError, FileNotFoundError, TypeError) as exc:
        print(f"status: {exc}", file=sys.stderr)
        return 2
    out: Path = args.out or root / (get_str(config, "status_file") or "status.json")
    out.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"status: {out}")
    if args.show:
        print(summary(status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
