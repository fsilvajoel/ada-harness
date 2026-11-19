"""Testes do executor cli-agent (`script/cli_agent.py`): comando externo simulado, sem chamar agente real."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script" / "cli_agent.py"

FAKE = """\
import json, os, sys
prompt = sys.stdin.read()
open(os.environ.get("FAKE_PROMPT_FILE", "prompt.txt"), "w", encoding="utf-8").write(prompt)
open("argv.json", "w", encoding="utf-8").write(json.dumps(sys.argv[1:]))
mode = os.environ.get("FAKE_MODE", "json")
if mode == "fail":
    sys.stderr.write("boom")
    sys.exit(3)
if mode == "empty":
    sys.exit(0)
if mode == "plain":
    print("resposta simples")
    sys.exit(0)
print(json.dumps({"type": "assistant.message", "text": "ola"}))
print(json.dumps({"type": "result", "usage": {"premium": 1}}))
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    skills = tmp_path / "skills"
    (skills / "alfa" / "rules").mkdir(parents=True)
    (skills / "alfa" / "SKILL.md").write_text("# indice alfa\n", encoding="utf-8")
    (skills / "alfa" / "rules" / "r1.md").write_text("regra um\n", encoding="utf-8")
    (skills / "beta").mkdir()
    (skills / "beta" / "SKILL.md").write_text("# beta\n", encoding="utf-8")
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE, encoding="utf-8")
    (tmp_path / "harness.project.yaml").write_text(
        f"""skill_dir: skills
role_skills:
  refiner:
    - beta
workers:
  cli:
    adapter: cli-agent
    command: {sys.executable}
    read_args: "{fake} --ro"
    write_args: "{fake} --rw"
    output_jq: '[.[] | select(.type == "assistant.message")] | last | .text'
    usage_jq: '[.[] | select(.type == "result")] | last | .usage.premium'
    roles: "refiner reviewer"
    timeout_s: 30
  plain:
    adapter: cli-agent
    command: {sys.executable}
    read_args: "{fake}"
    write_args: "{fake}"
    timeout_s: 30
  broken:
    adapter: cli-agent
    command: comando-que-nao-existe-xyz
    read_args: "--x"
    write_args: "--x"
""",
        encoding="utf-8",
    )
    (tmp_path / "work").mkdir()
    return tmp_path


def run(
    project: Path, *args: str, stdin: str = "# Tarefa\nfaça X\n", env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run(
        [sys.executable, str(SCRIPT), str(project), *args, "--cwd", str(project / "work")],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def test_injeta_skills_do_papel_e_do_cartao_no_stdin_do_comando(project: Path) -> None:
    done = run(project, "cli", "--mode", "read", "--role", "refiner", "--skills", "alfa#r1")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ola"
    prompt = (project / "work" / "prompt.txt").read_text(encoding="utf-8")
    assert prompt.startswith("# Tarefa\nfaça X")
    assert "# Skills e regras" in prompt
    for label in ("skill/beta/SKILL.md", "skill/alfa/SKILL.md", "skill/alfa/rules/r1.md"):
        assert f"=== RECURSO: {label} ===" in prompt
    assert prompt.index("skill/beta/SKILL.md") < prompt.index("skill/alfa/SKILL.md")


def test_modo_escolhe_args_e_meta_traz_skills_e_consumo(project: Path) -> None:
    meta_file = project / "meta.json"
    done = run(project, "cli", "--mode", "write", "--role", "refiner", "--meta", str(meta_file))
    assert done.returncode == 0, done.stderr
    assert json.loads((project / "work" / "argv.json").read_text(encoding="utf-8"))[-1] == "--rw"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["status"] == "ok" and meta["mode"] == "write"
    assert meta["usage"] == "1"
    assert meta["skills"] == [{"resource": "skill/beta/SKILL.md", "bytes": 7, "lines": 1}]


def test_sem_role_nem_skills_o_pedido_segue_como_veio(project: Path) -> None:
    done = run(project, "plain", "--mode", "read", env={"FAKE_MODE": "plain"})
    assert done.returncode == 0
    assert (project / "work" / "prompt.txt").read_text(encoding="utf-8") == "# Tarefa\nfaça X\n"
    assert done.stdout == "resposta simples\n"


def test_skill_inexistente_falha_com_2_sem_rodar_o_comando(project: Path) -> None:
    done = run(project, "cli", "--mode", "read", "--skills", "nao-existe")
    assert done.returncode == 2
    assert "skill inexistente" in json.loads(done.stderr.strip().splitlines()[-1])["error"]
    assert not (project / "work" / "prompt.txt").exists()


def test_teto_de_skills_falha_com_2(project: Path) -> None:
    config = project / "harness.project.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "skills_max_bytes: 5\n", encoding="utf-8")
    done = run(project, "cli", "--mode", "read", "--role", "refiner")
    assert done.returncode == 2
    assert "teto" in done.stderr


def test_papel_fora_de_roles_do_trabalhador_falha_com_2(project: Path) -> None:
    done = run(project, "cli", "--mode", "read", "--role", "implementer")
    assert done.returncode == 2
    assert "fora de workers.cli.roles" in done.stderr


def test_comando_com_saida_diferente_de_zero_e_falha_6(project: Path) -> None:
    done = run(project, "cli", "--mode", "read", env={"FAKE_MODE": "fail"})
    assert done.returncode == 6
    assert "saiu com 3" in done.stderr and "boom" in done.stderr


def test_resposta_vazia_e_falha_6(project: Path) -> None:
    assert run(project, "plain", "--mode", "read", env={"FAKE_MODE": "empty"}).returncode == 6


def test_comando_ausente_escala_com_7(project: Path) -> None:
    done = run(project, "broken", "--mode", "read")
    assert done.returncode == 7
    assert "comando ausente" in done.stderr


def test_trabalhador_de_outro_adaptador_ou_inexistente_falha_com_2(project: Path) -> None:
    assert run(project, "nada", "--mode", "read").returncode == 2


def test_pedido_vazio_falha_com_2(project: Path) -> None:
    assert run(project, "cli", "--mode", "read", stdin="  \n").returncode == 2
