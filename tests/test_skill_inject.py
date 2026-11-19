"""Testes da injeção de skills (`script/skill_inject.py`), sem rede."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.skill_inject import (
    DEFAULT_MAX_BYTES,
    SkillError,
    build_injection,
    injection_for,
    merge_specs,
    parse_spec,
    split_cli_items,
)


@pytest.fixture
def skills(tmp_path: Path) -> Path:
    base = tmp_path / "skill"
    (base / "alfa" / "rules").mkdir(parents=True)
    (base / "beta").mkdir()
    (base / "alfa" / "SKILL.md").write_text("# indice alfa\nlinha 2\n", encoding="utf-8")
    (base / "alfa" / "rules" / "regra-um.md").write_text("regra um\n", encoding="utf-8")
    (base / "alfa" / "AGENTS.md").write_text("X" * 5000, encoding="utf-8")
    (base / "beta" / "SKILL.md").write_text("# beta", encoding="utf-8")
    return base


def labels(base: Path, specs: list[str], limit: int = DEFAULT_MAX_BYTES) -> list[str]:
    return [r.label for r in build_injection(base, specs, limit).resources]


def test_nome_injeta_so_o_indice(skills: Path) -> None:
    assert labels(skills, ["alfa"]) == ["skill/alfa/SKILL.md"]


def test_nome_com_regra_injeta_indice_e_regra(skills: Path) -> None:
    assert labels(skills, ["alfa#regra-um"]) == ["skill/alfa/SKILL.md", "skill/alfa/rules/regra-um.md"]
    assert labels(skills, ["alfa#regra-um.md"]) == labels(skills, ["alfa#regra-um"])


def test_agents_compilado_nunca_entra(skills: Path) -> None:
    block = build_injection(skills, ["alfa", "alfa#regra-um"]).block
    assert "XXXXX" not in block
    with pytest.raises(SkillError, match="regra inexistente"):
        build_injection(skills, ["alfa#AGENTS"])


def test_deduplicacao_por_arquivo(skills: Path) -> None:
    result = labels(skills, ["alfa", "alfa#regra-um", "alfa", "alfa#regra-um", "beta"])
    assert result == ["skill/alfa/SKILL.md", "skill/alfa/rules/regra-um.md", "skill/beta/SKILL.md"]


def test_formato_do_bloco(skills: Path) -> None:
    block = build_injection(skills, ["beta"]).block
    assert block == "# Skills e regras\n\n=== RECURSO: skill/beta/SKILL.md ===\n# beta\n=== END RECURSO ===\n"


def test_tamanho_por_recurso(skills: Path) -> None:
    injection = build_injection(skills, ["alfa#regra-um"])
    first, second = injection.resources
    assert (first.size_bytes, first.lines) == (len("# indice alfa\nlinha 2\n"), 2)
    assert (second.size_bytes, second.lines) == (9, 1)
    assert injection.total_bytes == 31
    assert injection.summary()[1] == {"resource": "skill/alfa/rules/regra-um.md", "bytes": 9, "lines": 1}


def test_skill_inexistente_erro_claro(skills: Path) -> None:
    with pytest.raises(SkillError, match=r"skill inexistente: 'zeta'.*disponíveis: alfa, beta"):
        build_injection(skills, ["zeta"])


def test_regra_inexistente(skills: Path) -> None:
    with pytest.raises(SkillError, match=r"regra inexistente: 'alfa#nada'"):
        build_injection(skills, ["alfa#nada"])


@pytest.mark.parametrize("spec", ["", "../alfa", "alfa#../x", "a/b", "alfa#", "alfa#a#b", ".oculta"])
def test_item_invalido(skills: Path, spec: str) -> None:
    with pytest.raises(SkillError, match="inválido"):
        build_injection(skills, [spec])


def test_teto_falha_em_vez_de_truncar(skills: Path) -> None:
    assert build_injection(skills, ["alfa"], limit=22).total_bytes == 22
    with pytest.raises(SkillError, match=r"acima do teto de 21.*maior recurso: skill/alfa/SKILL.md"):
        build_injection(skills, ["alfa"], limit=21)


def test_parse_spec() -> None:
    assert parse_spec("alfa") == ("alfa", None)
    assert parse_spec(" alfa#regra ") == ("alfa", "regra")


def test_merge_papel_antes_do_cartao_sem_repetir() -> None:
    assert merge_specs(["a", "b"], ["b", "c#x", "a"]) == ["a", "b", "c#x"]
    assert split_cli_items(["a, b#x", "c"]) == ["a", "b#x", "c"]


def test_injection_for_usa_role_skills_cartao_e_teto(tmp_path: Path, skills: Path) -> None:
    (tmp_path / "harness.project.yaml").write_text(
        "skill_dir: skill\nrole_skills:\n  implementer:\n    - beta\nskills_max_bytes: 100\n", encoding="utf-8"
    )
    injection = injection_for(tmp_path, "implementer", ["alfa#regra-um", "beta"])
    assert [r.label for r in injection.resources] == [
        "skill/beta/SKILL.md",
        "skill/alfa/SKILL.md",
        "skill/alfa/rules/regra-um.md",
    ]
    assert injection.max_bytes == 100
    assert injection_for(tmp_path, "papel-sem-skills", []).block == ""
    (tmp_path / "harness.project.yaml").write_text("skill_dir: skill\nskills_max_bytes: 10\n", encoding="utf-8")
    with pytest.raises(SkillError, match="acima do teto de 10"):
        injection_for(tmp_path, None, ["alfa"])
