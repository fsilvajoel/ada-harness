"""Testes da validação da cadeia SDD."""

from __future__ import annotations

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.sdd import (
    approve_document,
    lint_document,
    prepare_feature,
    validate_approved_chain,
    validate_approval,
    validate_chain,
    write_approval,
)
from script.artifact_lint import lint_project


def test_complete_documents_pass(tmp_path: Path) -> None:
    (tmp_path / "prd.md").write_text(
        "## Problema\nP\n## Requisitos funcionais\nR\n## Requisitos não funcionais\nN\n## Critérios de aceite\nA",
        encoding="utf-8",
    )
    (tmp_path / "adr.md").write_text(
        "## Contexto\nC\n## Decisão\nD\n## Consequências\nS",
        encoding="utf-8",
    )
    (tmp_path / "rfc.md").write_text(
        "## Desenho\nD\n## Interfaces\nI\n## Testes\nT\n## Rollback\nR",
        encoding="utf-8",
    )
    assert validate_chain(tmp_path) == []


def test_missing_document_is_reported(tmp_path: Path) -> None:
    assert lint_document(tmp_path / "prd.md", "prd")[0].startswith("documento ausente")


def test_approval_is_structured(tmp_path: Path) -> None:
    approval = write_approval(
        tmp_path / "approval.json",
        document="adr",
        status="approved",
        sha="a" * 64,
        reviewer="product-owner",
    )
    assert approval["status"] == "approved"
    assert approval["hash"] == "a" * 64


def test_approval_is_bound_to_document_hash(tmp_path: Path) -> None:
    document = tmp_path / "adr.md"
    document.write_text("## Context\nDecision", encoding="utf-8")
    approval_path = tmp_path / "adr.approval.json"
    approve_document(
        approval_path,
        document,
        kind="adr",
        reviewer="product-owner",
    )
    assert validate_approval(document, approval_path, kind="adr") == []
    document.write_text("## Context\nChanged", encoding="utf-8")
    assert "aprovação: hash do documento mudou" in validate_approval(
        document,
        approval_path,
        kind="adr",
    )


def test_prepare_feature_requires_approved_sdd_and_publishes_valid_artifacts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    sdd = project / "docs" / "sdd"
    sdd.mkdir(parents=True)
    for kind, body in {
        "prd": "## Problema\nP\n## Requisitos funcionais\nR\n## Requisitos não funcionais\nN\n## Critérios de aceite\nA",
        "adr": "## Contexto\nC\n## Decisão\nD\n## Consequências\nS",
        "rfc": "## Desenho\nD\n## Interfaces\nI\n## Testes\nT\n## Rollback\nR",
    }.items():
        document = sdd / f"{kind}.md"
        document.write_text(body, encoding="utf-8")
        approve_document(
            sdd / f"{kind}.approval.json",
            document,
            kind=kind,
            reviewer="human",
        )
    (project / "harness.project.yaml").write_text(
        "cards_glob: docs/plano/E*.yaml\ndocs_dir: docs\n",
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.json"
    plan = {
        "schemaVersion": 2,
        "feature": "E1-F01",
        "scope": "Filtrar registros",
        "contract": "Retorna apenas registros correspondentes",
        "sources": [{"file": "src/filter.py", "classification": "tdd", "domain": True}],
        "criteria": [{
            "id": "A1",
            "statement": "Correspondências são retornadas",
            "source": "PRD RF-01",
            "layers": ["unit"],
        }],
        "scenarios": [{
            "id": "C1",
            "criterion": "A1",
            "layer": "unit",
            "file": "tests/test_filter.py",
            "input": "termo",
            "expected": "registros filtrados",
            "source": "PRD RF-01",
        }],
        "tests": ["tests/test_filter.py"],
        "e2e": [],
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    approval_path = tmp_path / "plan.approval.json"
    approve_document(
        approval_path,
        plan_path,
        kind="plan",
        reviewer="tech-lead",
    )
    cards_path = tmp_path / "cards.yaml"
    cards_path.write_text(
        """cards:
  - schemaVersion: 2
    id: E1-F01-T01
    title: Implement filtering
    kind: implement
    allowedPaths: [src/filter.py]
    generatedPaths: [src/filter.py]
    checks: [python -m pytest]
    dor: [approved feature contract]
    dod: [unit tests pass]
""",
        encoding="utf-8",
    )

    plan_output, cards_output = prepare_feature(
        project,
        sdd_directory=sdd,
        plan_path=plan_path,
        cards_path=cards_path,
        approval_path=approval_path,
    )
    assert plan_output.is_file()
    assert cards_output.is_file()
    assert validate_approved_chain(sdd) == []
    assert lint_project(project, strict=True) == []
