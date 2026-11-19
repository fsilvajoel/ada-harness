"""Testes do executor api-tools (`script/api_tools.py`), sem rede: servidor HTTP local com respostas roteirizadas."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "script" / "api_tools.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.api_chat import JsonValue
from script.api_tools import Sandbox, ToolDenied, command_argv, tool_specs

KEY = "sk-test-tools-SECRET42"
VAR = "TEST_TOOLS_KEY"


@dataclass
class ScriptedApi:
    """Responde, em ordem, às mensagens roteirizadas; repete a última quando acabam."""

    replies: list[dict[str, JsonValue]] = field(default_factory=list)
    requests: list[dict[str, JsonValue]] = field(default_factory=list)


def make_handler(api: ScriptedApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - assinatura da base
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            api.requests.append(json.loads(self.rfile.read(length)))
            index = min(len(api.requests), len(api.replies)) - 1
            body = {
                "model": "modelo-t",
                "choices": [{"message": api.replies[index], "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            }
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


@pytest.fixture
def api() -> Iterator[tuple[ScriptedApi, str]]:
    state = ScriptedApi()
    server = HTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def call(name: str, arguments: dict[str, JsonValue], call_id: str = "c1") -> dict[str, JsonValue]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def tool_reply(*calls: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def finish(summary: str = "pronto") -> dict[str, JsonValue]:
    return tool_reply(call("finish", {"summary": summary}, "fim"))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src" / "a.txt").write_text("linha um\nalvo aqui\n", encoding="utf-8")
    (root / ".env").write_text(f"{VAR}={KEY}\n", encoding="utf-8")
    (root / "src" / ".env.local").write_text("X=1\n", encoding="utf-8")
    (tmp_path / "fora.txt").write_text("segredo de fora\n", encoding="utf-8")
    (root / "src" / "link").symlink_to(tmp_path / "fora.txt")
    return root


def write_config(root: Path, base: str, max_steps: int = 5, roles: str | None = None) -> None:
    fields = [
        "adapter: api-tools",
        f'base_url: "{base}/v1"',
        'model: "modelo-t"',
        f"key_env: {VAR}",
        "key_file: .env",
        f"max_steps: {max_steps}",
        "command_timeout_s: 20",
        "timeout_s: 5",
    ]
    if roles is not None:
        fields.append(f'roles: "{roles}"')
    (root / "harness.project.yaml").write_text(
        "workers:\n  t: { "
        + ", ".join(fields)
        + " }\ncommands:\n"
        + f'  eco: "{sys.executable} -c \\"import sys; print(sys.argv[1:])\\" <arquivo>"\n'
        + f'  ambiente: "{sys.executable} -c \\"import os; print(os.environ.get(\'{VAR}\', \'sem-chave\'))\\""\n',
        encoding="utf-8",
    )


def run_tools(root: Path, *args: str, stdin: str = "# Tarefa\nrevise\n") -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != VAR}
    env[VAR] = KEY
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "t", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def meta_of(result: subprocess.CompletedProcess[str]) -> dict[str, JsonValue]:
    loaded: object = json.loads(result.stderr.strip().splitlines()[-1])
    assert isinstance(loaded, dict)
    return loaded


def tool_results(state: ScriptedApi, request: int) -> list[str]:
    messages = state.requests[request]["messages"]
    assert isinstance(messages, list)
    return [str(m["content"]) for m in messages if isinstance(m, dict) and m.get("role") == "tool"]


# ---------------------------------------------------------------- sandbox (unidade)


@pytest.mark.parametrize(
    "rel",
    ["../fora.txt", "src/../../fora.txt", "/etc/passwd", "src/link", ".env", "src/.env.local", "x/id_rsa", "c.pem"],
)
def test_sandbox_recusa_fuga_link_e_segredo(project: Path, rel: str) -> None:
    box = Sandbox(root=project.resolve(), writable=())
    with pytest.raises(ToolDenied):
        box.resolve(rel)


def test_sandbox_aceita_caminho_interno(project: Path) -> None:
    box = Sandbox(root=project.resolve(), writable=())
    real, normal = box.resolve("src/../src/a.txt")
    assert normal == "src/a.txt"
    assert real.read_text(encoding="utf-8").startswith("linha um")


def test_escopo_de_escrita_por_prefixo_e_exato(project: Path) -> None:
    box = Sandbox(root=project.resolve(), writable=("docs", "src/a.txt"))
    assert box.can_write("docs/novo.md")
    assert box.can_write("src/a.txt")
    assert not box.can_write("docsx/novo.md")
    assert not box.can_write("src/b.txt")
    assert not box.can_write(".git/config")


def test_marcadores_do_comando_so_aceitam_valor_seguro() -> None:
    assert command_argv("lint <arquivo>", {"arquivo": "a.py b.py"}) == ["lint", "a.py", "b.py"]
    assert command_argv("t --pkg=<pkg>", {"pkg": "web"}) == ["t", "--pkg=web"]
    for bad in ["a.py; rm -rf x", "$(id)", "../x", "-rf", "a|b"]:
        with pytest.raises(ToolDenied):
            command_argv("lint <arquivo>", {"arquivo": bad})
    with pytest.raises(ToolDenied):
        command_argv("lint <arquivo>", {})


def test_write_file_so_existe_com_escopo() -> None:
    def names(specs: list[JsonValue]) -> set[str]:
        return {str(s["function"]["name"]) for s in specs if isinstance(s, dict) and isinstance(s["function"], dict)}

    assert "write_file" not in names(tool_specs(False, {"eco": "x"}))
    assert {"read_file", "list_dir", "search", "run_command", "finish", "write_file"} == names(tool_specs(True, {}))


# ---------------------------------------------------------------- laço (ponta a ponta)


def test_leitura_busca_e_finish(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base)
    state.replies = [
        tool_reply(call("read_file", {"path": "src/a.txt"}), call("list_dir", {"path": "src"}, "c2")),
        tool_reply(call("search", {"pattern": "alvo|VAR"})),
        finish("tudo certo"),
    ]
    result = run_tools(project, "--role", "reviewer")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "tudo certo\n"
    first = tool_results(state, 1)
    assert first[0].startswith("linha um")
    assert ".env.local" not in first[1] and "a.txt" in first[1]
    second = tool_results(state, 2)[-1]
    assert "src/a.txt:2: alvo aqui" in second
    assert KEY not in second and ".env" not in second
    request = state.requests[0]
    assert request["tool_choice"] == "auto"
    meta = meta_of(result)
    assert meta["status"] == "ok"
    assert meta["steps"] == 3
    assert meta["tools"] == {"read_file": 1, "list_dir": 1, "search": 1, "finish": 1}
    assert meta["usage"] == {"prompt_tokens": 30, "completion_tokens": 6}
    assert KEY not in result.stderr


def test_traversal_link_e_env_voltam_como_erro_ao_modelo(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base)
    state.replies = [
        tool_reply(
            call("read_file", {"path": "../fora.txt"}, "a"),
            call("read_file", {"path": "src/link"}, "b"),
            call("read_file", {"path": ".env"}, "c"),
        ),
        finish(),
    ]
    result = run_tools(project, "--role", "reviewer")
    assert result.returncode == 0, result.stderr
    results = tool_results(state, 1)
    assert all(r.startswith("erro:") for r in results), results
    assert not any("segredo de fora" in r or KEY in r for r in results)
    assert len(meta_of(result)["denied"]) == 3  # type: ignore[arg-type]


def test_run_command_so_da_lista_e_sem_a_chave(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base)
    state.replies = [
        tool_reply(
            call("run_command", {"name": "rm", "params": {}}, "a"),
            call("run_command", {"name": "eco", "params": {"arquivo": "src/a.txt"}}, "b"),
            call("run_command", {"name": "eco", "params": {"arquivo": "x; id"}}, "c"),
            call("run_command", {"name": "ambiente"}, "d"),
        ),
        finish(),
    ]
    result = run_tools(project, "--role", "reviewer")
    assert result.returncode == 0, result.stderr
    fora, eco, injecao, ambiente = tool_results(state, 1)
    assert fora.startswith("erro: comando fora de commands.*")
    assert eco.startswith("exit=0") and "['src/a.txt']" in eco
    assert injecao.startswith("erro:")
    assert "sem-chave" in ambiente and KEY not in ambiente


def test_reviewer_sem_escopo_nao_recebe_write_file(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base)
    state.replies = [tool_reply(call("write_file", {"path": "docs/x.md", "content": "oi"})), finish()]
    result = run_tools(project, "--role", "reviewer")
    assert result.returncode == 0, result.stderr
    tools = state.requests[0]["tools"]
    assert isinstance(tools, list)
    assert "write_file" not in json.dumps(tools)
    assert tool_results(state, 1)[0].startswith("erro: ferramenta indisponível")
    assert not (project / "docs" / "x.md").exists()


def test_write_file_dentro_e_fora_do_escopo(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base)
    state.replies = [
        tool_reply(
            call("write_file", {"path": "docs/sub/novo.md", "content": "# novo\n"}, "a"),
            call("write_file", {"path": "src/a.txt", "content": "estrago"}, "b"),
            call("write_file", {"path": "docs/../.env", "content": "estrago"}, "c"),
        ),
        finish(),
    ]
    result = run_tools(project, "--role", "refiner", "--allow-write", "docs")
    assert result.returncode == 0, result.stderr
    dentro, fora, segredo = tool_results(state, 1)
    assert dentro == "gravado: docs/sub/novo.md"
    assert fora.startswith("erro: escrita fora do escopo")
    assert segredo.startswith("erro:")
    assert (project / "docs" / "sub" / "novo.md").read_text(encoding="utf-8") == "# novo\n"
    assert (project / "src" / "a.txt").read_text(encoding="utf-8").startswith("linha um")
    assert meta_of(result)["files_written"] == ["docs/sub/novo.md"]


def test_limite_de_passos(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base, max_steps=3)
    state.replies = [tool_reply(call("list_dir", {}))]
    result = run_tools(project, "--role", "reviewer")
    assert result.returncode == 6
    meta = meta_of(result)
    assert meta["status"] == "failed"
    assert meta["steps"] == 3
    assert "max_steps" in str(meta["error"])
    assert len(state.requests) == 3
    assert run_tools(project, "--role", "reviewer", "--max-steps", "1").returncode == 6
    assert len(state.requests) == 4


def test_sem_tool_calls_texto_e_a_resposta(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base)
    state.replies = [{"role": "assistant", "content": "parecer: aprovado"}]
    result = run_tools(project, "--role", "reviewer")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "parecer: aprovado\n"
    assert meta_of(result)["fallback"] is False


def test_fallback_blocos_file_respeitam_o_escopo(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base)
    state.replies = [{"role": "assistant", "content": "=== FILE: docs/r.md ===\n# r\n=== END FILE ===\n"}]
    result = run_tools(project, "--role", "refiner", "--allow-write", "docs/r.md")
    assert result.returncode == 0, result.stderr
    assert (project / "docs" / "r.md").read_text(encoding="utf-8") == "# r\n"
    meta = meta_of(result)
    assert meta["fallback"] is True and meta["files_written"] == ["docs/r.md"]

    state.replies = [{"role": "assistant", "content": "=== FILE: src/a.txt ===\nx\n=== END FILE ===\n"}]
    assert run_tools(project, "--role", "refiner", "--allow-write", "docs").returncode == 6
    assert run_tools(project, "--role", "reviewer").returncode == 6
    assert (project / "src" / "a.txt").read_text(encoding="utf-8").startswith("linha um")


def test_resposta_vazia_falha(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base)
    state.replies = [{"role": "assistant", "content": ""}]
    assert run_tools(project).returncode == 6


def test_papel_fora_de_roles_e_adapter_errado_saem_com_2(project: Path, api: tuple[ScriptedApi, str]) -> None:
    state, base = api
    write_config(project, base, roles="planner reviewer")
    state.replies = [finish()]
    assert run_tools(project, "--role", "implementer").returncode == 2
    assert state.requests == []
    text = (project / "harness.project.yaml").read_text(encoding="utf-8").replace("api-tools", "cli-agent")
    (project / "harness.project.yaml").write_text(text, encoding="utf-8")
    assert run_tools(project, "--role", "reviewer").returncode == 2


def test_allow_write_invalido_sai_com_2(project: Path, api: tuple[ScriptedApi, str]) -> None:
    _, base = api
    write_config(project, base)
    assert run_tools(project, "--role", "refiner", "--allow-write", "../x").returncode == 2
    assert run_tools(project, "--role", "refiner", "--allow-write", ".env").returncode == 2
