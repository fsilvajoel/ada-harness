"""Testes da política condicional de testes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.testing_policy import (
    PolicyValidationError,
    resolve_gates,
    resolve_feature_gates,
    stage_policy,
    validate_visual_extraction,
)


def test_visual_only_feature_uses_visual_gate_without_tdd_gates() -> None:
    gates = resolve_gates(
        classifications={"src/page.css": "dispensa-estilo"},
        domain_sources=[],
        e2e_criteria=[],
    )
    assert gates["expect_red"] is False
    assert gates["coverage"] is False
    assert gates["mutation"] is False
    assert gates["visual_review"] is True
    assert gates["feature"] is True
    assert validate_visual_extraction({"src/page.css": "dispensa-estilo"}) == []


def test_domain_logic_keeps_full_tdd_gates() -> None:
    gates = resolve_gates(
        classifications={"src/filter.ts": "tdd"},
        domain_sources=["src/filter.ts"],
        e2e_criteria=["A1"],
    )
    assert gates["expect_red"] is True
    assert gates["coverage"] is True
    assert gates["mutation"] is True
    assert gates["e2e"] is True


def test_second_opinion_is_opt_in_for_high_risk_work() -> None:
    gates = resolve_gates(
        classifications={"src/auth.ts": "tdd"},
        domain_sources=["src/auth.ts"],
        second_opinion_required=True,
    )
    assert gates["second_opinion"] is True


def test_visual_only_policy_skips_test_stages_but_keeps_review() -> None:
    gates = resolve_gates(classifications={"src/Panel.tsx": "dispensa-estilo"})
    policy = stage_policy(gates)
    assert policy == {"tests": False, "testReview": False, "mutation_e2e": False}
    assert gates["visual_review"] is True


def test_feature_layers_control_mutation_and_e2e_gates() -> None:
    gates = resolve_feature_gates({
        "sources": [{"file": "src/handler.ts", "classification": "tdd", "domain": True}],
        "criteria": [{"id": "A1", "layers": ["unit"]}],
    })
    assert gates["mutation"] is False
    assert gates["e2e"] is False
    assert gates["coverage"] is True

    gates = resolve_feature_gates({
        "sources": [{"file": "src/handler.ts", "classification": "tdd", "domain": True}],
        "criteria": [{"id": "A1", "layers": ["unit", "mut", "e2e"]}],
    })
    assert gates["mutation"] is True
    assert gates["e2e"] is True


def test_visual_domain_marker_must_be_extracted() -> None:
    errors = validate_visual_extraction(
        {"src/page.tsx": "dispensa-estilo"},
        {"src/page.tsx"},
    )
    assert any("extraia" in error for error in errors)


def test_invalid_classification_is_rejected() -> None:
    with pytest.raises(PolicyValidationError, match="classificação inválida"):
        resolve_gates(classifications={"src/file.ts": "incerto"})
