"""Testes do renderizador de issues (`script/render_issue.py`).

Fixtures sintéticas em `tmp_path`; os testes com um projeto real (E7-F01, E4-F02 e D1) leem a raiz da variável de
ambiente `ADA_TEST_PROJECT` (somente leitura) e são pulados sem ela.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.render_issue import MAX_BODY, RenderError, main, mark, render_selected, short_hash  # noqa: E402

REAL = os.environ.get("ADA_TEST_PROJECT")
real_project = pytest.mark.skipif(not REAL, reason="defina ADA_TEST_PROJECT com a raiz de um projeto real")

CARDS = """\
delivery: X1
cards:
  - id: X1-T01
    title: Write the tests
    kind: test-author
    assignee: bia
    refs: [PRD-001 RF-01, ADR-0002, docs/plano/nota.md]
    dependsOn: []
    allowedPaths: [src/a.test.ts]
    checks: ['pnpm test']
    done:
      - the test fails now
      - 'covers `RF-01` and X1-T02'
    hints: [use the helper]
  - id: X1-T02
    title: Implement it
    kind: implement
    assignee: alice
    refs: []
    dependsOn: [X1-T01]
    allowedPaths: [src/a.ts]
    done: [it passes]
features:
  - {id: X1-F01, title: First feature, cards: [X1-T01, X1-T02]}
"""

PRD = """\
# PRD-001 - teste

## 5. Requisitos
- **RF-01** ADMIN cadastra pessoa.
  - Esboço: [tela](telas/tela.png)
- **RF-02** Outra coisa.

## 8. Critérios
| # | Cenário | Esperado |
|---|---|---|
| CA-01 | Principal 5.000 | total 6.900 |
"""

PLANO = """\
# Plano X1-F01

## Escopo
- cadastrar pessoa, veja [nota](../nota.md)

## Contrato
- `f(x): y`

## Critérios de aceite
- A1: funciona
"""

DEFEITO = """\
# D1: Parcelas aceitam 1e1

| Campo | Valor |
|---|---|
| Origem | revisão |
| Feature de origem | X1-F01 (`docs/plano/X1-F01/plano.md`, achado 1) |
| Severidade | baixa |
| Status | fechado |
| Correção | `src/a.ts` |

## Reprodução
- obtido: aceita
- esperado: rejeita

## Qual regra deixou passar?
Faltou a tabela de domínio.
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "harness.project.yaml").write_text("cards_glob: docs/plano/X*.yaml\n", encoding="utf-8")
    plano = tmp_path / "docs" / "plano"
    (plano / "X1-F01").mkdir(parents=True)
    (plano / "X1.yaml").write_text(CARDS, encoding="utf-8")
    (plano / "X1-F01" / "plano.md").write_text(PLANO, encoding="utf-8")
    (plano / "nota.md").write_text("# nota\n", encoding="utf-8")
    (tmp_path / "docs" / "adr").mkdir()
    (tmp_path / "docs" / "adr" / "0002-monolito.md").write_text("# ADR-0002\n", encoding="utf-8")
    (tmp_path / "docs" / "prd" / "telas").mkdir(parents=True)
    (tmp_path / "docs" / "prd" / "PRD-001-teste.md").write_text(PRD, encoding="utf-8")
    (tmp_path / "docs" / "prd" / "telas" / "tela.png").write_bytes(b"png")
    (tmp_path / "docs" / "defeitos").mkdir()
    (tmp_path / "docs" / "defeitos" / "D1.md").write_text(DEFEITO, encoding="utf-8")
    return tmp_path


def feature(project: Path, **kwargs: object) -> str:
    (issue,) = render_selected(project, feature="X1-F01", **kwargs)  # type: ignore[arg-type]
    return issue.body


def test_sections_in_order(project: Path) -> None:
    body = feature(project)
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Resumo e escopo",
        "## Regras e critérios",
        "## Contrato",
        "## Cartões",
        "## Referências",
        "## Telas",
        "## Estado",
    ]
    assert body.startswith("<!-- ada-feature:X1-F01 -->")
    assert "cadastrar pessoa" in body and "`f(x): y`" in body and "A1: funciona" in body


def test_english_card_text_is_marked_with_hash(project: Path) -> None:
    body = feature(project)
    assert f"<!--t:{short_hash('Write the tests')}-->Write the tests<!--/t-->" in body
    assert mark("the test fails now") in body
    assert mark("use the helper") in body
    assert mark("covers `RF-01` and X1-T02") in body
    assert mark("  padded  ") == mark("padded")


def test_prd_rule_and_references(project: Path) -> None:
    body = feature(project, repo_url="https://github.com/o/r")
    assert "**RF-01** ADMIN cadastra pessoa." in body
    assert "RF-02" not in body
    assert "[ADR-0002](https://github.com/o/r/blob/main/docs/adr/0002-monolito.md)" in body
    assert "[docs/plano/nota.md](https://github.com/o/r/blob/main/docs/plano/nota.md)" in body


def test_image_link_uses_raw_and_branch(project: Path) -> None:
    body = feature(project, repo_url="https://github.com/o/r/", branch="dev")
    assert "![tela](https://github.com/o/r/blob/dev/docs/prd/telas/tela.png?raw=true)" in body
    assert "## Telas\n\n![tela]" in body


def test_without_repo_url_keeps_relative_paths(project: Path) -> None:
    body = feature(project)
    assert "github.com" not in body
    assert "`docs/prd/telas/tela.png`" in body


def test_state_section(project: Path) -> None:
    body = feature(project)
    assert "- Etapa: plan" in body and "- `X1-T01`: ready" in body and "- `X1-T02`: waiting" in body


def test_broken_doc_link_fails(project: Path) -> None:
    plano = project / "docs" / "plano" / "X1-F01" / "plano.md"
    plano.write_text(PLANO + "\nVeja [x](nao-existe.md)\n", encoding="utf-8")
    with pytest.raises(RenderError, match="nao-existe.md"):
        feature(project)
    assert main([str(project), "--feature", "X1-F01"]) == 1


def test_broken_image_and_missing_ref_fail(project: Path) -> None:
    (project / "docs" / "prd" / "telas" / "tela.png").unlink()
    with pytest.raises(RenderError, match="tela.png"):
        feature(project)


def test_unknown_adr_fails(project: Path) -> None:
    (project / "docs" / "adr" / "0002-monolito.md").unlink()
    with pytest.raises(RenderError, match="ADR-0002"):
        feature(project)


def test_unknown_prd_rule_fails(project: Path) -> None:
    (project / "docs" / "prd" / "PRD-001-teste.md").write_text("# PRD\n", encoding="utf-8")
    with pytest.raises(RenderError, match="RF-01"):
        feature(project)


def test_long_body_is_truncated_with_notice(project: Path) -> None:
    plano = project / "docs" / "plano" / "X1-F01" / "plano.md"
    plano.write_text(PLANO.replace("- cadastrar pessoa", "- " + "linha longa " * 40000 + "\n- cadastrar pessoa"), encoding="utf-8")
    body = feature(project, repo_url="https://github.com/o/r")
    assert len(body) <= MAX_BODY + 1
    assert "Texto truncado" in body
    assert "[docs/plano/X1-F01/plano.md](https://github.com/o/r/blob/main/docs/plano/X1-F01/plano.md)" in body


def test_defect_rendering(project: Path) -> None:
    (issue,) = render_selected(project, defect="D1", repo_url="https://github.com/o/r")
    assert issue.title == "[D1] Parcelas aceitam 1e1"
    assert issue.labels == ("bug",)
    assert issue.body.startswith("<!-- ada-defeito:D1 -->")
    assert "- **Severidade**: baixa" in issue.body
    assert "- obtido: aceita" in issue.body
    assert "Faltou a tabela de domínio." in issue.body
    assert "[docs/plano/X1-F01/plano.md](https://github.com/o/r/blob/main/docs/plano/X1-F01/plano.md)" in issue.body
    assert "<!--t:" not in issue.body


def test_defect_with_missing_origin_doc_fails(project: Path) -> None:
    (project / "docs" / "plano" / "X1-F01" / "plano.md").unlink()
    with pytest.raises(RenderError, match="plano.md"):
        render_selected(project, defect="D1")


def test_all_writes_one_file_per_target(project: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    out = tmp_path_factory.mktemp("out")
    assert main([str(project), "--all", "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["D1.md", "X1-F01.md"]
    assert main([str(project), "--all"]) == 2


def test_unknown_targets_exit_2(project: Path) -> None:
    assert main([str(project), "--feature", "ZZ-F99"]) == 2
    assert main([str(project), "--defeito", "D99"]) == 2


def test_deterministic(project: Path) -> None:
    assert feature(project) == feature(project)


@real_project
def test_real_feature_e7_f01() -> None:
    assert REAL
    (issue,) = render_selected(Path(REAL), feature="E7-F01", repo_url="https://github.com/o/r")
    assert issue.title.startswith("[E7-F01]")
    assert "E7-T01" in issue.body and "**RF-01**" in issue.body
    assert "<!--t:" in issue.body and len(issue.body) <= MAX_BODY + 1


@real_project
def test_real_feature_e4_f02() -> None:
    assert REAL
    (issue,) = render_selected(Path(REAL), feature="E4-F02")
    assert "E4-T04" in issue.body and "redact" in issue.body


@real_project
def test_real_defect_d1() -> None:
    assert REAL
    (issue,) = render_selected(Path(REAL), defect="D1")
    assert "1e1" in issue.title and issue.labels == ("bug",)


@real_project
def test_real_everything_renders() -> None:
    assert REAL
    issues = render_selected(Path(REAL), everything=True)
    assert {i.id for i in issues} >= {"E7-F01", "E4-F02", "D1"}
