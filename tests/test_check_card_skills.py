"""Testes do gate card-skills (`script/check_card_skills.py`)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script" / "check_card_skills.py"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "skills" / "alfa" / "rules").mkdir(parents=True)
    (tmp_path / "skills" / "alfa" / "SKILL.md").write_text("# alfa\n", encoding="utf-8")
    (tmp_path / "skills" / "alfa" / "rules" / "r1.md").write_text("r\n", encoding="utf-8")
    (tmp_path / "harness.project.yaml").write_text(
        "skill_dir: skills\ncards_glob: plano/E*.yaml\nskills_required_paths:\n  - web\n", encoding="utf-8"
    )
    (tmp_path / "plano").mkdir()
    return tmp_path


def cards(project: Path, body: str) -> None:
    (project / "plano" / "E1.yaml").write_text("cards:\n" + body, encoding="utf-8")


def run(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(project)], capture_output=True, text=True, check=False)


def test_cartao_web_sem_skills_falha_com_2(project: Path) -> None:
    cards(project, "  - id: T1\n    allowedPaths:\n      - web/app/page.tsx\n")
    done = run(project)
    assert done.returncode == 2
    assert "T1" in done.stdout and "skills" in done.stdout


def test_cartao_web_com_skills_validas_passa(project: Path) -> None:
    cards(project, "  - id: T1\n    allowedPaths: [web/x.ts]\n    skills: [alfa, 'alfa#r1']\n")
    assert run(project).returncode == 0


def test_cartao_fora_do_web_sem_skills_passa(project: Path) -> None:
    cards(project, "  - id: T1\n    allowedPaths: [packages/x.ts, website/y.ts]\n")
    assert run(project).returncode == 0


def test_skill_ou_regra_inexistente_falha_com_2(project: Path) -> None:
    cards(project, "  - id: T1\n    allowedPaths: [web/x.ts]\n    skills: [alfa, nada, 'alfa#zzz']\n")
    done = run(project)
    assert done.returncode == 2
    assert "skill inexistente: 'nada'" in done.stdout
    assert "regra inexistente: 'alfa#zzz'" in done.stdout


def test_skills_invalidas_em_cartao_nao_web_tambem_falham(project: Path) -> None:
    cards(project, "  - id: T1\n    allowedPaths: [packages/x.ts]\n    skills: [nada]\n")
    assert run(project).returncode == 2


def test_configuracao_ausente_falha_com_2(project: Path) -> None:
    (project / "harness.project.yaml").write_text("skill_dir: skills\n", encoding="utf-8")
    assert run(project).returncode == 2
