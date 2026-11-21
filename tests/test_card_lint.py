"""Testes do lint de cartão da evolução."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.card_lint import lint_card, lint_cards_document


def valid_card() -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "title": "Implement search filtering",
        "id": "E9-F02-T01",
        "kind": "implement",
        "allowedPaths": ["src/search.ts"],
        "protectedTests": ["src/search.test.ts"],
        "readFirst": ["docs/plano/E9-F02/plano.md"],
        "checks": ["pnpm test src/search.test.ts"],
        "dor": ["feature validada"],
        "dod": ["gatilho aprovado"],
    }


def test_valid_card_passes() -> None:
    assert lint_card(valid_card()) == []
    assert lint_card(valid_card(), strict=True) == []


def test_missing_scope_dor_and_dod_fail() -> None:
    card = valid_card()
    card.pop("allowedPaths")
    card.pop("dor")
    card.pop("dod")
    errors = lint_card(card, strict=True)
    assert any("allowedPaths" in error for error in errors)
    assert any("DoR" in error for error in errors)
    assert any("DoD" in error for error in errors)


def test_legacy_card_shape_is_supported() -> None:
    document = {
        "cards": [{
            "id": "E1-T01",
            "title": "Write search tests",
            "kind": "test-author",
            "allowedPaths": ["src/filter.test.ts"],
            "checks": ["pnpm test"],
            "done": ["tests fail before implementation"],
            "refs": ["PRD RF-01"],
        }]
    }
    assert lint_cards_document(document) == []


def test_strict_card_requires_explicit_v2_contract() -> None:
    card = valid_card()
    card.pop("schemaVersion")
    errors = lint_card(card, strict=True)
    assert any("schemaVersion" in error for error in errors)


def test_strict_generated_paths_must_stay_inside_allowed_paths(tmp_path: Path) -> None:
    card = valid_card()
    card["generatedPaths"] = ["src/unrelated.ts"]
    errors = lint_card(card, strict=True, project_root=tmp_path)
    assert any("fora de allowedPaths" in error for error in errors)
