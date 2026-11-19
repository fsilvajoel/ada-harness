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


def test_system_padrao_do_adaptador_api_chat_chega_ao_executor(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from script import dispatch as mod

    seen: dict[str, str] = {}

    def fake_run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        path = Path(cmd[cmd.index("--system") + 1])
        seen["system"] = path.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0)

    (project / "harness.project.yaml").write_text(
        "roles:\n  implementer: {agent: d.md, worker: api}\n"
        "workers:\n  api:\n    adapter: api-chat\n    base_url: http://x\n    model: m\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod.main([str(project), "--role", "implementer"]) == 0
    assert seen["system"] == mod.default_system()
    assert "Você é o papel implementer" in seen["system"]


def test_system_com_cli_agent_sai_com_2(project: Path) -> None:
    custom = project / "work" / "sys.txt"
    custom.write_text("x", encoding="utf-8")
    assert dispatch(project, "--role", "refiner", "--system", str(custom)).returncode == 2


ROUTED = """\
roles:
  reviewer: {agent: b.md, worker: tools, worker_stages: [review], second_opinion: tools}
  refiner: {agent: a.md, worker: tools, worker_paths: [docs, tests/spec]}
  planner: {agent: c.md, worker: tools, worker_stages: [plan], worker_paths: [docs]}
workers:
  tools:
    adapter: api-tools
    base_url: http://x
    model: m
    key_env: K
"""


def run_routed(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str
) -> tuple[int, list[list[str]], dict[str, object] | None, str]:
    from script import dispatch as mod

    calls: list[list[str]] = []
    systems: list[str] = []

    def fake_run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if "--system" in cmd:
            systems.append(Path(cmd[cmd.index("--system") + 1]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(cmd, 0)

    (project / "harness.project.yaml").write_text(ROUTED, encoding="utf-8")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    code = mod.main([str(project), *args])
    err = capsys.readouterr().err
    routes = [line for line in err.splitlines() if line.startswith("dispatch: rota ")]
    route: object = json.loads(routes[0].removeprefix("dispatch: rota ")) if routes else None
    assert route is None or isinstance(route, dict)
    return code, calls, route, systems[0] if systems else ""


def test_rota_por_etapa(project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, calls, route, system = run_routed(project, monkeypatch, capsys, "--role", "reviewer", "--stage", "review")
    assert code == 0 and calls[0][1].endswith("api_tools.py")
    assert route is not None and route["decision"] == "worker" and route["adapter"] == "api-tools"
    assert "trabalha só por ferramentas" in system
    for extra in (["--stage", "implement"], []):
        code, calls, route, _ = run_routed(project, monkeypatch, capsys, "--role", "reviewer", *extra)
        assert code == 8 and calls == []
        assert route is not None and route["decision"] == "agent" and "worker_stages" in str(route["reason"])


def test_second_opinion_ignora_a_rota(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls, route, _ = run_routed(project, monkeypatch, capsys, "--role", "reviewer", "--second-opinion")
    assert code == 0 and len(calls) == 1
    assert route is not None and route["reason"] == "second_opinion"


def test_rota_por_caminhos(project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, calls, _, _ = run_routed(
        project, monkeypatch, capsys, "--role", "refiner", "--allowed-path", "docs/a.md", "--allowed-path", "tests/spec"
    )
    assert code == 0
    cmd = calls[0]
    assert [cmd[i + 1] for i, v in enumerate(cmd) if v == "--allow-write"] == ["docs/a.md", "tests/spec"]
    for paths in (["docs/a.md", "src/x.ts"], ["docsx/a.md"], ["docs/../src/x.ts"], []):
        argv = [item for p in paths for item in ("--allowed-path", p)]
        code, calls, route, _ = run_routed(project, monkeypatch, capsys, "--role", "refiner", *argv)
        assert code == 8 and calls == [], paths
        assert route is not None and route["decision"] == "agent"


def test_refiner_em_modo_leitura_nao_recebe_escrita(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls, _, _ = run_routed(
        project, monkeypatch, capsys, "--role", "refiner", "--mode", "read", "--allowed-path", "docs/a.md"
    )
    assert code == 0 and "--allow-write" not in calls[0]


def test_planner_e_reviewer_api_tools_so_leem(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, calls, _, _ = run_routed(
        project, monkeypatch, capsys, "--role", "planner", "--stage", "plan", "--allowed-path", "docs/p.md", "--mode", "write"
    )
    assert code == 0 and "--allow-write" not in calls[0]
    code, calls, _, _ = run_routed(
        project, monkeypatch, capsys, "--role", "reviewer", "--stage", "review", "--allowed-path", "docs/p.md"
    )
    assert code == 0 and "--allow-write" not in calls[0]


def test_resolve_worker_sem_etapa_continua_compativel(project: Path) -> None:
    from script.dispatch import DispatchError, resolve_worker

    assert resolve_worker(project, "refiner", False) == ("cli", "cli-agent")
    (project / "harness.project.yaml").write_text(ROUTED, encoding="utf-8")
    with pytest.raises(DispatchError) as caught:
        resolve_worker(project, "reviewer", False)
    assert caught.value.exit_code == 8
    assert resolve_worker(project, "reviewer", False, stage="review") == ("tools", "api-tools")
