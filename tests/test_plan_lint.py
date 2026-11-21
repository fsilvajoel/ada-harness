"""Testes do lint de plano da evolução."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.plan_lint import lint_feature_plan, lint_plan, lint_plan_markdown


def valid_plan() -> dict[str, object]:
    return {
        "feature": "E9-F02",
        "sources": ["src/search.ts"],
        "domainSources": ["src/search.ts"],
        "exemptSources": [],
        "criteria": [{"id": "A1", "camadas": ["unit", "prop", "mut"]}],
        "tests": ["src/search.test.ts"],
        "e2e": [],
    }


def test_valid_plan_passes() -> None:
    assert lint_plan(valid_plan()) == []


def test_missing_domain_and_invalid_layer_fail() -> None:
    plan = valid_plan()
    plan.pop("domainSources")
    plan["criteria"] = [{"id": "A1", "camadas": ["mutacao"]}]
    errors = lint_plan(plan, strict=True)
    assert any("domainSources" in error for error in errors)
    assert any("camada fora" in error for error in errors)


def test_exempt_source_requires_reason() -> None:
    plan = valid_plan()
    plan["exemptSources"] = [{"file": "src/view.tsx", "class": "dispensa-estilo", "reason": ""}]
    errors = lint_plan(plan)
    assert any("reason" in error for error in errors)


def test_markdown_plan_requires_scenario_for_each_criterion() -> None:
    text = "## Critérios de aceite\n### A1: resultado correto\n## Cenários\n"
    assert "A1: sem cenário C<n>" in lint_plan_markdown(text, strict=True)


def test_versioned_feature_plan_checks_scenario_and_test_mapping() -> None:
    plan: dict[str, object] = {
        "schemaVersion": 2,
        "feature": "E1-F01",
        "scope": "Validar a busca",
        "contract": "Retorna resultados filtrados",
        "sources": [{"file": "src/filter.ts", "classification": "tdd", "domain": True}],
        "criteria": [{
            "id": "A1",
            "statement": "Termos válidos retornam resultados",
            "source": "PRD RF-01",
            "layers": ["unit"],
        }],
        "scenarios": [{
            "id": "C1",
            "criterion": "A1",
            "layer": "unit",
            "file": "src/filter.test.ts",
            "input": "termo válido",
            "expected": "lista filtrada",
            "source": "PRD RF-01",
        }],
        "tests": ["src/filter.test.ts"],
        "e2e": [],
    }
    assert lint_feature_plan(plan) == []


def test_visual_only_feature_can_skip_tdd_and_unit_tests() -> None:
    plan: dict[str, object] = {
        "schemaVersion": 2,
        "feature": "E1-F02",
        "scope": "Ajustar espaçamento do painel",
        "contract": "O painel corresponde ao layout aprovado",
        "sources": [{
            "file": "src/Panel.tsx",
            "classification": "dispensa-estilo",
            "reason": "Mudança exclusivamente visual, validada por revisão de screenshot",
            "domain": False,
        }],
        "criteria": [{
            "id": "A1",
            "statement": "O painel corresponde ao layout aprovado",
            "source": "Figma tela principal",
            "layers": ["visual"],
        }],
        "scenarios": [{
            "id": "C1",
            "criterion": "A1",
            "layer": "visual",
            "file": "src/Panel.tsx",
            "input": "Viewport desktop 1440px",
            "expected": "Sem overflow; espaçamento conforme design",
            "source": "Figma tela principal",
        }],
        "tests": [],
        "e2e": [],
    }
    assert lint_feature_plan(plan) == []


def test_strict_state_validates_test_hashes() -> None:
    state = valid_plan()
    state.update({
        "schemaVersion": 2,
        "criteria": [{"id": "A1", "camadas": ["unit"]}],
        "scenarios": [{
            "id": "C1",
            "criterio": "A1",
            "camada": "unit",
            "arquivo": "src/search.test.ts",
            "teste": "retorna resultados",
        }],
        "testShas": {"src/search.test.ts": "f" * 64},
    })
    assert lint_plan(state, strict=True) == []
    state["testShas"] = {"../outside.ts": "bad"}
    errors = lint_plan(state, strict=True)
    assert any("testShas" in error for error in errors)


def test_strict_state_verifies_test_hash_against_project_file(tmp_path: Path) -> None:
    test_path = tmp_path / "tests" / "test_feature.py"
    test_path.parent.mkdir()
    test_path.write_text("assert True\n", encoding="utf-8")
    import hashlib

    sha = hashlib.sha256(test_path.read_bytes()).hexdigest()
    state = {
        "schemaVersion": 2,
        "feature": "E1-F01",
        "sources": ["src/feature.py"],
        "domainSources": ["src/feature.py"],
        "exemptSources": [],
        "criteria": [{"id": "A1", "camadas": ["unit"]}],
        "tests": ["tests/test_feature.py"],
        "e2e": [],
        "scenarios": [{
            "id": "C1",
            "criterio": "A1",
            "camada": "unit",
            "arquivo": "tests/test_feature.py",
            "teste": "returns expected result",
        }],
        "testShas": {"tests/test_feature.py": sha},
    }
    assert lint_plan(state, strict=True, project_root=tmp_path) == []
    test_path.write_text("assert False\n", encoding="utf-8")
    assert any(
        "hash do arquivo mudou" in error
        for error in lint_plan(state, strict=True, project_root=tmp_path)
    )
