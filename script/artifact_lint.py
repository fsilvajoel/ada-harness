#!/usr/bin/env python3
"""Valida cartões e planos declarados pelo projeto antes do dispatch."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Final

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.card_lint import lint_cards_document
from script.plan_lint import lint_feature_plan, lint_plan, lint_plan_markdown
from script.project_config import get_str, load_config

EXIT_CONFIG: Final = 2
EXIT_FAIL: Final = 1


def lint_project(project_root: Path, *, strict: bool = False) -> list[str]:
    try:
        config = load_config(project_root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        return [f"harness.project.yaml inválido: {exc}"]
    cards_glob = get_str(config, "cards_glob")
    if not cards_glob:
        return ["harness.project.yaml sem cards_glob"]
    docs_dir = get_str(config, "docs_dir") or "docs"

    errors: list[str] = []
    card_ids: dict[str, Path] = {}
    dependencies: list[tuple[str, str, Path]] = []
    paths = sorted(project_root.glob(cards_glob))
    if not paths:
        errors.append(f"cards_glob não encontrou arquivos: {cards_glob}")
    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{path.relative_to(project_root)}: YAML inválido: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{path.relative_to(project_root)}: raiz precisa ser mapa")
            continue
        errors.extend(
            f"{path.relative_to(project_root)}: {message}"
            for message in lint_cards_document(data, strict=strict, project_root=project_root)
        )
        cards = data.get("cards")
        if not isinstance(cards, list):
            continue
        for card in cards:
            if not isinstance(card, dict):
                continue
            card_id = card.get("id")
            if isinstance(card_id, str):
                if card_id in card_ids:
                    errors.append(
                        f"{card_id}: ID duplicado em {card_ids[card_id].relative_to(project_root)} "
                        f"e {path.relative_to(project_root)}"
                    )
                else:
                    card_ids[card_id] = path
            deps = card.get("dependsOn")
            if isinstance(card_id, str) and isinstance(deps, list):
                dependencies.extend(
                    (card_id, dep, path)
                    for dep in deps if isinstance(dep, str)
                )

    for card_id, dependency, path in dependencies:
        feature_plan = (
            project_root / docs_dir / "plano" / dependency / "plano.md"
            if re.fullmatch(r"[A-Za-z0-9_-]+-F\d+", dependency)
            else None
        )
        if (
            (strict or "-T" in dependency)
            and dependency not in card_ids
            and (feature_plan is None or not feature_plan.is_file())
        ):
            errors.append(
                f"{path.relative_to(project_root)}: {card_id} depende de cartão inexistente {dependency}"
            )

    plan_files = sorted((project_root / docs_dir / "plano").glob("*/plano.md"))
    for plan_path in plan_files:
        try:
            text = plan_path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{plan_path.relative_to(project_root)}: não foi possível ler: {exc}")
            continue
        messages = lint_plan_markdown(text)
        errors.extend(
            f"{plan_path.relative_to(project_root)}: {message}"
            for message in messages
        )

    for plan_path in sorted((project_root / docs_dir / "plano").glob("*/plan.json")):
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{plan_path.relative_to(project_root)}: JSON inválido: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{plan_path.relative_to(project_root)}: plano precisa ser mapa")
            continue
        errors.extend(
            f"{plan_path.relative_to(project_root)}: {message}"
            for message in lint_feature_plan(data)
        )
    for state_path in sorted((project_root / docs_dir / "plano").glob("*/estado.json")):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{state_path.relative_to(project_root)}: JSON inválido: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{state_path.relative_to(project_root)}: estado precisa ser mapa")
            continue
        if "schemaVersion" in data:
            errors.extend(
                f"{state_path.relative_to(project_root)}: {message}"
                for message in lint_plan(
                    data,
                    strict=True,
                    project_root=project_root,
                )
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida cartões e planos do projeto.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="valida contratos versionados e dependências estritamente, preservando artefatos legados",
    )
    args = parser.parse_args(argv)
    errors = lint_project(args.project_root.resolve(), strict=args.strict)
    if not errors:
        print("artifact-lint: PASS")
        return 0
    for error in errors:
        print(f"artifact-lint: FAIL: {error}")
    return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
