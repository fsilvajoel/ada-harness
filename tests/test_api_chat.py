"""Testes do executor api-chat (`script/api_chat.py`), sem rede: servidor HTTP local."""

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
SCRIPT = ROOT / "script" / "api_chat.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.api_chat import JsonValue, jq_path, read_key_line

KEY = "sk-test-abc123SECRET"
VAR = "TEST_WORKER_KEY"


@dataclass
class FakeApi:
    """Estado do servidor falso: o que responder e o que recebeu."""

    remaining: int = 500
    chat_status: int = 200
    chat_body: dict[str, object] = field(
        default_factory=lambda: {
            "model": "modelo-x",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }
    )
    auth_headers: list[str] = field(default_factory=list)
    chat_requests: list[dict[str, object]] = field(default_factory=list)


def make_handler(api: FakeApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - assinatura da base
            return

        def _send(self, status: int, body: object) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            api.auth_headers.append(self.headers.get("Authorization", ""))
            self._send(200, {"data": {"daily": {"remaining": api.remaining}}})

        def do_POST(self) -> None:
            api.auth_headers.append(self.headers.get("Authorization", ""))
            length = int(self.headers.get("Content-Length", "0"))
            api.chat_requests.append(json.loads(self.rfile.read(length)))
            # Erro que ecoa o cabeçalho: a saída do script precisa mascarar.
            if api.chat_status != 200:
                self._send(api.chat_status, {"error": {"message": f"echo {self.headers.get('Authorization')}"}})
            else:
                self._send(200, api.chat_body)

    return Handler


@pytest.fixture
def api() -> Iterator[tuple[FakeApi, str]]:
    state = FakeApi()
    server = HTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def write_project(root: Path, base: str, key_file: str | None = "segredos.txt") -> None:
    fields = [
        "adapter: api-chat",
        f'base_url: "{base}/v1"',
        'model: "modelo-x"',
        f"key_env: {VAR}",
        f'quota_url: "{base}/v1/key"',
        'quota_remaining: ".data.daily.remaining"',
        "quota_reserve: 20",
        "max_attempts: 6",
        "timeout_s: 5",
    ]
    if key_file is not None:
        fields.insert(4, f"key_file: {key_file}")
    (root / "harness.project.yaml").write_text(
        "workers:\n  w: { " + ", ".join(fields) + " }\n", encoding="utf-8"
    )


def run_script(root: Path, env_key: str | None = None, stdin: str = "responda só OK") -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != VAR}
    if env_key is not None:
        env[VAR] = env_key
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(root), "w"],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )


def meta_of(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    loaded: object = json.loads(result.stderr.strip().splitlines()[-1])
    assert isinstance(loaded, dict)
    return loaded


def test_chave_pela_variavel(tmp_path: Path, api: tuple[FakeApi, str]) -> None:
    state, base = api
    write_project(tmp_path, base, key_file=None)
    result = run_script(tmp_path, env_key=KEY)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "OK\n"
    meta = meta_of(result)
    assert meta["status"] == "ok"
    assert meta["key_source"] == "env"
    assert meta["quota_remaining"] == 500
    assert meta["finish_reason"] == "stop"
    assert state.auth_headers == [f"Bearer {KEY}", f"Bearer {KEY}"]
    request = state.chat_requests[0]
    assert request["temperature"] == 0
    assert request["model"] == "modelo-x"
    assert request["messages"] == [{"role": "user", "content": "responda só OK"}]


@pytest.mark.parametrize(
    "line",
    [
        f"{VAR}={KEY}",
        f'{VAR}="{KEY}"',
        f"{VAR}='{KEY}'",
        f"export {VAR}={KEY}",
        f'export {VAR}="{KEY}"  # comentário',
    ],
)
def test_chave_pelo_key_file(tmp_path: Path, api: tuple[FakeApi, str], line: str) -> None:
    state, base = api
    write_project(tmp_path, base)
    content = f"OUTRA=valor\r\n{line}\r\nDEPOIS=x\r\n"
    (tmp_path / "segredos.txt").write_bytes(content.encode("utf-8"))
    result = run_script(tmp_path)
    assert result.returncode == 0, result.stderr
    assert meta_of(result)["key_source"] == "key_file"
    assert state.auth_headers[-1] == f"Bearer {KEY}"


def test_variavel_tem_prioridade_sobre_key_file(tmp_path: Path, api: tuple[FakeApi, str]) -> None:
    state, base = api
    write_project(tmp_path, base)
    (tmp_path / "segredos.txt").write_text(f"{VAR}=do-arquivo\n", encoding="utf-8")
    result = run_script(tmp_path, env_key=KEY)
    assert result.returncode == 0, result.stderr
    assert state.auth_headers[-1] == f"Bearer {KEY}"


def test_key_file_le_so_a_linha_da_chave(tmp_path: Path) -> None:
    arquivo = tmp_path / "segredos.txt"
    arquivo.write_text(f"OUTRA_VAR_TESTE=vazou\n{VAR}={KEY}\n", encoding="utf-8")
    assert read_key_line(arquivo, VAR) == KEY
    assert "OUTRA_VAR_TESTE" not in os.environ
    assert read_key_line(tmp_path / "falta.txt", VAR) is None


def test_sem_chave(tmp_path: Path, api: tuple[FakeApi, str]) -> None:
    state, base = api
    write_project(tmp_path, base)
    (tmp_path / "segredos.txt").write_text("OUTRA=valor\n", encoding="utf-8")
    result = run_script(tmp_path)
    assert result.returncode == 3
    assert meta_of(result)["status"] == "no-key"
    assert result.stdout == ""
    assert state.auth_headers == []


def test_sem_cota(tmp_path: Path, api: tuple[FakeApi, str]) -> None:
    state, base = api
    state.remaining = 20
    write_project(tmp_path, base, key_file=None)
    result = run_script(tmp_path, env_key=KEY)
    assert result.returncode == 4
    meta = meta_of(result)
    assert meta["status"] == "quota"
    assert meta["quota_remaining"] == 20
    assert state.chat_requests == []


def test_429(tmp_path: Path, api: tuple[FakeApi, str]) -> None:
    state, base = api
    state.chat_status = 429
    write_project(tmp_path, base, key_file=None)
    result = run_script(tmp_path, env_key=KEY)
    assert result.returncode == 5
    meta = meta_of(result)
    assert meta["status"] == "rate-limit"
    assert meta["http_status"] == 429


def test_erro_http(tmp_path: Path, api: tuple[FakeApi, str]) -> None:
    state, base = api
    state.chat_status = 500
    write_project(tmp_path, base, key_file=None)
    result = run_script(tmp_path, env_key=KEY)
    assert result.returncode == 6
    assert meta_of(result)["status"] == "failed"


def test_resposta_vazia(tmp_path: Path, api: tuple[FakeApi, str]) -> None:
    state, base = api
    state.chat_body = {"choices": [{"message": {"content": "  "}}]}
    write_project(tmp_path, base, key_file=None)
    assert run_script(tmp_path, env_key=KEY).returncode == 6


def test_rede_fora(tmp_path: Path) -> None:
    write_project(tmp_path, "http://127.0.0.1:1", key_file=None)
    result = run_script(tmp_path, env_key=KEY)
    assert result.returncode == 4  # a consulta de cota é a primeira a falhar
    assert KEY not in result.stdout + result.stderr


@pytest.mark.parametrize("status", [200, 401, 429, 500])
def test_chave_nunca_aparece_na_saida(tmp_path: Path, api: tuple[FakeApi, str], status: int) -> None:
    state, base = api
    state.chat_status = status
    # O modelo ecoa a chave e o erro ecoa o cabeçalho: ambos precisam sair mascarados.
    state.chat_body = {"choices": [{"message": {"content": f"eco {KEY}"}}]}
    write_project(tmp_path, base)
    (tmp_path / "segredos.txt").write_text(f'export {VAR}="{KEY}"\r\n', encoding="utf-8")
    result = run_script(tmp_path)
    assert KEY not in result.stdout
    assert KEY not in result.stderr
    assert "***" in result.stdout + result.stderr


def test_jq_path() -> None:
    document: JsonValue = {"data": {"a-b": [1, {"x": 7}]}}
    assert jq_path(document, '.data."a-b"[1].x') == 7
    assert jq_path(document, ".data.falta") is None
