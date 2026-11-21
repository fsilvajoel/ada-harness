#!/usr/bin/env python3
"""Lint estruturado de cartões da Ada Harness 2.0."""

from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path, PureWindowsPath
from typing import Final

import yaml

KINDS: Final[frozenset[str]] = frozenset({"docs", "implement", "pre-review", "test-author"})
EXECUTABLE_KINDS: Final[frozenset[str]] = frozenset({"implement", "test-author"})


def _text_list(card: dict[str, object], field: str, *, required: bool = False) -> list[str]:
    value = card.get(field)
    if value is None:
        return [] if not required else ["missing"]
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        return ["invalid"]
    return value


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    windows_path = PureWindowsPath(value)
    return (
        bool(value.strip())
        and value not in {".", "./"}
        and "\\" not in value
        and not path.is_absolute()
        and not windows_path.is_absolute()
        and not windows_path.drive
        and ".." not in path.parts
        and ".." not in windows_path.parts
    )


def lint_card(
    card: dict[str, object],
    *,
    strict: bool = False,
    project_root: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    card_id = card.get("id")
    if not isinstance(card_id, str) or not card_id.strip():
        errors.append("id: ausente")
    title = card.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        errors.append("title: precisa ser texto não vazio")
    if strict and (not isinstance(title, str) or not title.strip()):
        errors.append("title: obrigatório no contrato 2.0")
    if strict and card.get("schemaVersion") != 2:
        errors.append("schemaVersion: esperado 2")

    kind = card.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        errors.append("kind: inválido")
        kind = ""

    allowed = _text_list(card, "allowedPaths", required=True)
    if allowed == ["missing"]:
        errors.append("allowedPaths: ausente")
        allowed = []
    elif allowed == ["invalid"]:
        errors.append("allowedPaths: precisa ser lista de textos")
        allowed = []
    if not allowed and kind in EXECUTABLE_KINDS:
        errors.append("allowedPaths: vazio para cartão executável")
    for path in allowed:
        if strict and not _safe_relative_path(path):
            errors.append(f"allowedPaths: caminho inseguro '{path}'")

    checks = _text_list(card, "checks", required=strict)
    if checks == ["missing"]:
        errors.append("checks: ausente")
        checks = []
    elif checks == ["invalid"]:
        errors.append("checks: precisa ser lista de textos")
    elif strict and not checks:
        errors.append("checks: lista não vazia obrigatória")

    if strict or not isinstance(card.get("done"), list):
        done = _text_list(card, "done")
    else:
        raw_done = card["done"]
        legacy_done = all(
            isinstance(item, str) and item.strip()
            or isinstance(item, dict)
            and len(item) == 1
            and all(isinstance(key, str) and isinstance(value, str) for key, value in item.items())
            for item in raw_done
        )
        done = raw_done if legacy_done else ["invalid"]
    if done == ["missing"]:
        errors.append("done: DoD ausente")
        done = []
    elif done == ["invalid"]:
        errors.append("done: precisa ser lista de textos")

    read_first = _text_list(card, "readFirst")
    if read_first == ["invalid"]:
        errors.append("readFirst: precisa ser lista de textos")
        read_first = []
    refs = _text_list(card, "refs")
    if refs == ["invalid"]:
        errors.append("refs: precisa ser lista de textos")
        refs = []
    depends = _text_list(card, "dependsOn")
    if depends == ["invalid"]:
        errors.append("dependsOn: precisa ser lista de textos")
        depends = []
    if strict:
        for field in ("dor", "dod"):
            value = _text_list(card, field, required=True)
            if value == ["missing"]:
                label = "DoR" if field == "dor" else "DoD"
                errors.append(f"{label} ({field}): obrigatório no contrato 2.0")
            elif value == ["invalid"] or not value:
                label = "DoR" if field == "dor" else "DoD"
                errors.append(f"{label} ({field}): precisa ser lista não vazia de textos")

    for field in ("protectedTests", "skills", "rules", "hints"):
        value = _text_list(card, field)
        if value == ["invalid"]:
            errors.append(f"{field}: precisa ser lista de textos")

    budget = card.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            errors.append("budget: precisa ser mapa")
        elif strict:
            for name in ("attempts", "toolCalls", "minutes"):
                value = budget.get(name)
                if type(value) is not int or value < 1:
                    errors.append(f"budget.{name}: precisa ser inteiro positivo")

    generated = _text_list(card, "generatedPaths")
    if generated == ["invalid"]:
        errors.append("generatedPaths: precisa ser lista de textos")
        generated = []
    for path in generated:
        if not _safe_relative_path(path):
            errors.append(f"generatedPaths: caminho inseguro '{path}'")
    if len(set(generated)) != len(generated):
        errors.append("generatedPaths: caminhos duplicados")

    if strict and project_root is not None:
        declared_new = set(generated)
        for path in generated:
            if not any(fnmatch.fnmatch(path, pattern) for pattern in allowed):
                errors.append(f"generatedPaths: '{path}' fora de allowedPaths")
        for path in allowed:
            if any(char in path for char in "*?["):
                exists = any(project_root.glob(path)) or any(fnmatch.fnmatch(new, path) for new in generated)
            else:
                exists = (project_root / path).exists() or path in declared_new
            if not exists and path not in declared_new:
                errors.append(f"allowedPaths: '{path}' não existe nem está em generatedPaths")

    return errors


def lint_cards_document(
    data: dict[str, object],
    *,
    strict: bool = False,
    project_root: Path | None = None,
) -> list[str]:
    cards = data.get("cards")
    if not isinstance(cards, list):
        return ["arquivo precisa ter uma lista cards"]
    errors: list[str] = []
    known_ids: set[str] = set()
    parsed: list[dict[str, object]] = []

    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            errors.append(f"cards[{index}]: cartão precisa ser mapa")
            continue
        card_id = card.get("id")
        if isinstance(card_id, str):
            if card_id in known_ids:
                errors.append(f"{card_id}: id duplicado")
            known_ids.add(card_id)
        parsed.append(card)
        card_strict = strict and card.get("schemaVersion") == 2
        if strict and "schemaVersion" in card and card.get("schemaVersion") != 2:
            errors.append(f"{card_id or index}: schemaVersion inválida")
        errors.extend(f"{card_id or index}: {error}" for error in lint_card(
            card,
            strict=card_strict,
            project_root=project_root,
        ))

    for card in parsed:
        card_id = str(card.get("id", "?"))
        depends = _text_list(card, "dependsOn")
        if depends in (["missing"], ["invalid"]):
            continue
        if len(set(depends)) != len(depends):
            errors.append(f"{card_id}: dependsOn contém IDs duplicados")
        for dependency in depends:
            if dependency == card_id:
                errors.append(f"{card_id}: depende de si mesmo")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint de cartões Ada Harness 2.0.")
    parser.add_argument("cards", type=Path)
    parser.add_argument("--root", type=Path, help="raiz do projeto para checar allowedPaths")
    parser.add_argument("--strict", action="store_true", help="aplica contrato de cartão 2.0")
    args = parser.parse_args(argv)
    try:
        data = yaml.safe_load(args.cards.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"card-lint: arquivo ilegível: {exc}")
        return 2
    if not isinstance(data, dict):
        print("card-lint: arquivo precisa ter uma lista cards")
        return 1

    errors = lint_cards_document(data, strict=args.strict, project_root=args.root)
    if not errors:
        print("card-lint: PASS")
        return 0
    for error in errors:
        print(f"card-lint: FAIL: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
