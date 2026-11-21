"""Testes de integração do lint dos artefatos do projeto."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.artifact_lint import lint_project


def project(tmp_path: Path) -> Path:
    (tmp_path / "harness.project.yaml").write_text(
        "cards_glob: docs/plano/E*.yaml\ndocs_dir: docs\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "plano").mkdir(parents=True)
    return tmp_path


def test_project_lint_checks_card_dependency_and_plan(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "docs" / "plano" / "E1.yaml").write_text(
        """delivery: E1
cards:
  - id: E1-T01
    title: Write test
    kind: test-author
    allowedPaths: [src/example.test.ts]
    checks: [pnpm test]
    done: [test fails before implementation]
    dependsOn: [E1-T99]
""",
        encoding="utf-8",
    )
    (root / "docs" / "plano" / "E1-F01").mkdir()
    (root / "docs" / "plano" / "E1-F01" / "plano.md").write_text(
        "## Critérios de aceite\n### A1: works\n## Cenários\n- C1 (A1, unit): input -> output\n",
        encoding="utf-8",
    )
    errors = lint_project(root)
    assert any("cartão inexistente E1-T99" in error for error in errors)
    assert not any("A1: sem cenário" in error for error in errors)


def test_project_lint_accepts_legacy_card_without_forcing_v2_fields(tmp_path: Path) -> None:
    root = project(tmp_path)
    (root / "docs" / "plano" / "E1.yaml").write_text(
        """cards:
  - id: E1-T01
    title: Write test
    kind: test-author
    allowedPaths: [src/example.test.ts]
    checks: [pnpm test]
    done: [test is red]
""",
        encoding="utf-8",
    )
    assert lint_project(root) == []
