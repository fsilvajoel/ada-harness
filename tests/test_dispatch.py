"""Testes do despachante (`script/dispatch.py`): comando externo simulado, sem chamar agente real."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script" / "dispatch.py"

FAKE = """\
import json, sys
prompt = sys.stdin.read()
open("prompt.txt", "w", encoding="utf-8").write(prompt)
open("argv.json", "w", encoding="utf-8").write(json.dumps(sys.argv[1:]))
print(json.dumps({"type": "assistant.message", "text": "ola"}))
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "skills" / "beta").mkdir(parents=True)
    (tmp_path / "skills" / "beta" / "SKILL.md").write_text("# beta\n", encoding="utf-8")
    (tmp_path / "skills" / "gama").mkdir()
    (tmp_path / "skills" / "gama" / "SKILL.md").write_text("# gama\n", encoding="utf-8")
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE, encoding="utf-8")
    (tmp_path / "harness.project.yaml").write_text(
        f"""skill_dir: skills
role_skills:
  refiner:
    - beta
roles:
  refiner: {{ agent: a.md, worker: cli }}
  reviewer: {{ agent: b.md, second_opinion: cli }}
  planner: {{ agent: c.md }}
  implementer: {{ agent: d.md, worker: ghost }}
  tester: {{ agent: e.md, worker: other }}
workers:
  cli:
    adapter: cli-agent
    command: {sys.executable}
    read_args: "{fake} --ro"
    write_args: "{fake} --rw"
    output_jq: '[.[] | select(.type == "assistant.message")] | last | .text'
    roles: "refiner reviewer"
    timeout_s: 30
  other:
    adapter: desconhecido
""",
        encoding="utf-8",
    )
    (tmp_path / "work").mkdir()
    return tmp_path


def dispatch(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(project), *args, "--cwd", str(project / "work")],
        input="# Tarefa\nfaça X\n",
        capture_output=True,
        text=True,
        check=False,
    )


def argv_of(project: Path) -> list[str]:
    return json.loads((project / "work" / "argv.json").read_text(encoding="utf-8"))


def test_papel_com_worker_cli_agent_chama_o_executor_com_role_e_skills(project: Path) -> None:
    done = dispatch(project, "--role", "refiner", "--skills", "gama")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ola"
    prompt = (project / "work" / "prompt.txt").read_text(encoding="utf-8")
    assert prompt.startswith("# Tarefa\nfaça X")
    assert "skill/beta/SKILL.md" in prompt and "skill/gama/SKILL.md" in prompt


def test_modo_padrao_escrita_para_refiner_e_leitura_para_o_resto(project: Path) -> None:
    assert dispatch(project, "--role", "refiner").returncode == 0
    assert argv_of(project)[-1] == "--rw"
    assert dispatch(project, "--role", "reviewer", "--second-opinion").returncode == 0
    assert argv_of(project)[-1] == "--ro"
    assert dispatch(project, "--role", "refiner", "--mode", "read").returncode == 0
    assert argv_of(project)[-1] == "--ro"


def test_papel_sem_worker_sai_com_8(project: Path) -> None:
    assert dispatch(project, "--role", "planner").returncode == 8
    assert dispatch(project, "--role", "reviewer").returncode == 8  # só tem second_opinion


def test_worker_ou_adapter_invalido_sai_com_2(project: Path) -> None:
    assert dispatch(project, "--role", "implementer").returncode == 2
    assert dispatch(project, "--role", "tester").returncode == 2


def test_codigo_de_saida_do_executor_passa(project: Path) -> None:
    config = project / "harness.project.yaml"
    config.write_text(config.read_text(encoding="utf-8").replace("refiner reviewer", "reviewer"), encoding="utf-8")
    assert dispatch(project, "--role", "refiner").returncode == 2  # papel fora de workers.cli.roles
