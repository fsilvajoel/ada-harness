"""Testes do gate role-skills (`script/check_role_skills.py`)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script" / "check_role_skills.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.check_role_skills import template_skills

CONFIG = """roles:
  orchestrator: {{ agent: null }}
  planner: {{ agent: agents/planner.md }}
  tester: {{ agent: agents/tester.md }}
role_skills:
{skills}"""

TEMPLATE = """# papel

## Skills obrigatórias

- `../h/skill/{a}/SKILL.md`
{extra}
Depois leia as do cartão.

## Outra seção

- `../h/skill/zzz/SKILL.md`
"""


def write(root: Path, planner_skills: str, tester_skills: str, planner_tpl: list[str], tester_tpl: list[str]) -> None:
    (root / "agents").mkdir(exist_ok=True)
    for name, items in (("planner", planner_tpl), ("tester", tester_tpl)):
        first, *rest = items
        extra = "".join(f"- `../h/skill/{i}`\n" for i in rest)
        (root / "agents" / f"{name}.md").write_text(TEMPLATE.format(a=first, extra=extra), encoding="utf-8")
    skills = f"  planner:\n{planner_skills}  tester:\n{tester_skills}"
    (root / "harness.project.yaml").write_text(CONFIG.format(skills=skills), encoding="utf-8")


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, check=False)


def test_listas_iguais_mesmo_em_ordem_diferente_passam(tmp_path: Path) -> None:
    write(tmp_path, "    - a\n    - b\n", "    - c\n", ["b", "a/SKILL.md"], ["c"])
    assert run(tmp_path).returncode == 0


def test_skill_a_mais_no_molde_falha_com_2(tmp_path: Path) -> None:
    write(tmp_path, "    - a\n", "    - c\n", ["a", "extra/SKILL.md"], ["c"])
    done = run(tmp_path)
    assert done.returncode == 2
    assert "papel planner" in done.stdout and "só no molde: extra" in done.stdout


def test_skill_a_mais_no_yaml_falha_com_2(tmp_path: Path) -> None:
    write(tmp_path, "    - a\n", "    - c\n    - d\n", ["a"], ["c"])
    done = run(tmp_path)
    assert done.returncode == 2
    assert "papel tester" in done.stdout and "só em role_skills: d" in done.stdout


def test_role_skills_de_papel_sem_molde_falha(tmp_path: Path) -> None:
    write(tmp_path, "    - a\n", "    - c\n", ["a"], ["c"])
    config = tmp_path / "harness.project.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "  orchestrator:\n    - a\n", encoding="utf-8")
    done = run(tmp_path)
    assert done.returncode == 2
    assert "role_skills.orchestrator não tem molde" in done.stdout


def test_template_skills_le_so_a_secao_e_reconhece_regras() -> None:
    text = TEMPLATE.format(a="a", extra="- `../h/skill/b/rules/r1.md`\n")
    assert template_skills(text) == {"a", "b#r1"}
    assert template_skills("# sem seção\n") is None
