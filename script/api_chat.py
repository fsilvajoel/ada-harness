#!/usr/bin/env python3
"""Executor do adaptador api-chat (`adapter/api-chat.md`): uma requisição por chamada.

Uso:
    api_chat.py <raiz do projeto> <trabalhador> [--request ARQ] [--system ARQ]
                [--output ARQ] [--meta ARQ] [--role PAPEL] [--skills a,b#regra]

- Lê `workers.<trabalhador>` do `harness.project.yaml` da raiz do projeto.
- Resolve a chave sozinho: variável `key_env`, senão só a linha `<key_env>=` do
  `key_file` (relativo à raiz). Nada mais do arquivo é lido para o ambiente.
- Consulta a cota (`quota_url` + `quota_remaining`, com `quota_reserve`) e faz
  UMA chamada de chat (`temperature` 0, `timeout_s`). O pedido do usuário vem
  de `--request` ou do stdin; `--system` é opcional.
- A resposta (`choices[0].message.content`) vai para o stdout ou `--output`; o
  resumo de metadados, em JSON, vai para o stderr ou `--meta`.

- Skills (`script/skill_inject.py`): junta `role_skills.<--role>` do projeto com `--skills` (campo `skills`
  do cartão) e anexa ao pedido a seção `# Skills e regras`. Tamanho por recurso no meta (`skills`); acima do
  teto `skills_max_bytes` = saída 2, sem chamar a API.

A chave nunca vai para argv, stdout, stderr nem mensagens de erro: segue só no
cabeçalho da requisição, e todo texto de saída passa por máscara.

O laço de tentativas, os gates e a escalada ficam com o orquestrador.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import ConfigValue, load_config
from script.skill_inject import Injection, SkillError, injection_for, split_cli_items

# Códigos de saída (o orquestrador escala conforme adapter/api-chat.md).
EXIT_OK: Final = 0
EXIT_CONFIG: Final = 2  # uso ou configuração inválida
EXIT_NO_KEY: Final = 3  # escala na hora: no-key
EXIT_QUOTA: Final = 4  # escala na hora: cota abaixo da reserva ou consulta falhou
EXIT_RATE_LIMIT: Final = 5  # escala na hora: HTTP 429
EXIT_FAILED: Final = 6  # tentativa falha: HTTP != 200, timeout, rede, corpo com error, resposta vazia

STATUS_BY_EXIT: Final[dict[int, str]] = {
    EXIT_OK: "ok",
    EXIT_CONFIG: "config",
    EXIT_NO_KEY: "no-key",
    EXIT_QUOTA: "quota",
    EXIT_RATE_LIMIT: "rate-limit",
    EXIT_FAILED: "failed",
}

MASK: Final = "***"
ERROR_SNIPPET: Final = 500

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


class RunError(Exception):
    """Falha que encerra a execução com um código de saída definido."""

    def __init__(self, exit_code: int, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class Worker:
    """Campos de `workers.<nome>` usados pelo adaptador api-chat."""

    name: str
    base_url: str
    model: str
    key_env: str
    key_file: str | None
    quota_url: str | None
    quota_remaining: str | None
    quota_reserve: int
    timeout_s: float


@dataclass
class Meta:
    """Resumo da execução, gravado em JSON (nunca contém a chave)."""

    worker: str
    model: str | None = None
    status: str = "ok"
    exit_code: int = EXIT_OK
    key_source: str | None = None
    http_status: int | None = None
    quota_remaining: float | None = None
    quota_reserve: int | None = None
    finish_reason: str | None = None
    usage: JsonValue = None
    error: str | None = None
    skills: list[dict[str, str | int]] | None = None

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


def mask(text: str, secret: str | None) -> str:
    """Troca toda ocorrência da chave (e do cabeçalho com ela) pela máscara."""
    if not secret:
        return text
    return text.replace(secret, MASK)


# ---------------------------------------------------------------- configuração


def _opt_str(entry: dict[str, ConfigValue], field: str) -> str | None:
    value = entry.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _req_str(entry: dict[str, ConfigValue], field: str, name: str) -> str:
    value = _opt_str(entry, field)
    if value is None:
        raise RunError(EXIT_CONFIG, f"workers.{name}.{field} ausente")
    return value


def _number(entry: dict[str, ConfigValue], field: str, default: float) -> float:
    value = entry.get(field)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise RunError(EXIT_CONFIG, f"{field} precisa ser número")
    try:
        return float(value)
    except ValueError as exc:
        raise RunError(EXIT_CONFIG, f"{field} precisa ser número") from exc


def load_worker(project_root: Path, name: str) -> Worker:
    """Lê e valida `workers.<name>` (adapter api-chat)."""
    try:
        config = load_config(project_root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        raise RunError(EXIT_CONFIG, f"harness.project.yaml inválido: {exc}") from exc
    workers = config.get("workers")
    entry = workers.get(name) if isinstance(workers, dict) else None
    if not isinstance(entry, dict):
        raise RunError(EXIT_CONFIG, f"workers.{name} ausente no harness.project.yaml")
    adapter = _opt_str(entry, "adapter")
    if adapter != "api-chat":
        raise RunError(EXIT_CONFIG, f"workers.{name}.adapter é '{adapter}', esperado 'api-chat'")
    return Worker(
        name=name,
        base_url=_req_str(entry, "base_url", name).rstrip("/"),
        model=_req_str(entry, "model", name),
        key_env=_req_str(entry, "key_env", name),
        key_file=_opt_str(entry, "key_file"),
        quota_url=_opt_str(entry, "quota_url"),
        quota_remaining=_opt_str(entry, "quota_remaining"),
        quota_reserve=int(_number(entry, "quota_reserve", 0)),
        timeout_s=_number(entry, "timeout_s", 300),
    )


# ---------------------------------------------------------------- chave


_KEY_LINE: Final = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def read_key_line(path: Path, var: str) -> str | None:
    """Devolve o valor da linha `var=` de `path`, sem tocar no resto do arquivo.

    Aceita `export `, aspas simples ou duplas, CRLF e comentário de fim de linha
    fora das aspas. As demais linhas são lidas só para achar a certa e descartadas.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        match = _KEY_LINE.match(line)
        if match is None or match.group(1) != var:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] in {"'", '"'}:
            end = value.find(value[0], 1)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        return value or None
    return None


def resolve_key(worker: Worker, project_root: Path) -> tuple[str, str]:
    """Chave e origem (`env` ou `key_file`); sem chave = `no-key`."""
    from_env = os.environ.get(worker.key_env, "").strip()
    if from_env:
        return from_env, "env"
    if worker.key_file is not None:
        value = read_key_line(project_root / worker.key_file, worker.key_env)
        if value:
            return value, "key_file"
    raise RunError(EXIT_NO_KEY, f"chave ausente: variável {worker.key_env} vazia e key_file sem a linha")


# ---------------------------------------------------------------- HTTP


def _http_json(
    url: str,
    key: str,
    timeout: float,
    body: dict[str, JsonValue] | None = None,
) -> tuple[int, JsonValue]:
    """GET (sem body) ou POST JSON com a chave no cabeçalho. Devolve (status, corpo)."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="GET" if body is None else "POST")
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = int(response.status)
        payload: JsonValue = json.loads(response.read().decode("utf-8"))
    return status, payload


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:ERROR_SNIPPET]
    except OSError:
        return ""


_PATH_TOKEN: Final = re.compile(r'\.(?:([A-Za-z_][A-Za-z0-9_-]*)|"([^"]+)")|\[(\d+)\]')


def jq_path(document: JsonValue, path: str) -> JsonValue:
    """Avalia um caminho jq simples (`.a.b`, `.a[0]`, `."c-d"`). Ausente = None."""
    text = path.strip()
    if text == ".":
        return document
    position = 0
    node = document
    while position < len(text):
        match = _PATH_TOKEN.match(text, position)
        if match is None:
            raise RunError(EXIT_CONFIG, f"quota_remaining não é um caminho jq simples: {path}")
        position = match.end()
        key = match.group(1) or match.group(2)
        if key is not None:
            node = node.get(key) if isinstance(node, dict) else None
        else:
            index = int(match.group(3))
            node = node[index] if isinstance(node, list) and index < len(node) else None
    return node


def check_quota(worker: Worker, key: str, meta: Meta) -> None:
    """Escala (`quota`) se a consulta falhar ou se o restante for <= reserva."""
    if worker.quota_url is None or worker.quota_remaining is None:
        return
    meta.quota_reserve = worker.quota_reserve
    try:
        _, payload = _http_json(worker.quota_url, key, worker.timeout_s)
    except urllib.error.HTTPError as exc:
        raise RunError(EXIT_QUOTA, f"consulta de cota falhou: HTTP {exc.code}", exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RunError(EXIT_QUOTA, f"consulta de cota falhou: {type(exc).__name__}: {exc}") from None
    remaining = jq_path(payload, worker.quota_remaining)
    if isinstance(remaining, bool) or not isinstance(remaining, int | float):
        raise RunError(EXIT_QUOTA, f"cota ilegível em {worker.quota_remaining}")
    meta.quota_remaining = float(remaining)
    if remaining <= worker.quota_reserve:
        raise RunError(EXIT_QUOTA, f"cota restante {remaining} <= reserva {worker.quota_reserve}")


def chat(worker: Worker, key: str, messages: list[JsonValue], meta: Meta) -> str:
    """Faz UMA chamada de chat e devolve o conteúdo da resposta."""
    body: dict[str, JsonValue] = {"model": worker.model, "temperature": 0, "messages": messages}
    try:
        status, payload = _http_json(f"{worker.base_url}/chat/completions", key, worker.timeout_s, body)
    except urllib.error.HTTPError as exc:
        meta.http_status = exc.code
        code = EXIT_RATE_LIMIT if exc.code == 429 else EXIT_FAILED
        raise RunError(code, f"HTTP {exc.code}: {_error_text(exc)}", exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RunError(EXIT_FAILED, f"rede ou timeout: {type(exc).__name__}: {exc}") from None
    except ValueError as exc:
        raise RunError(EXIT_FAILED, f"corpo não é JSON: {exc}") from None
    meta.http_status = status
    if not isinstance(payload, dict):
        raise RunError(EXIT_FAILED, "corpo não é objeto JSON", status)
    meta.model = str(payload.get("model") or worker.model)
    meta.usage = payload.get("usage")
    error = payload.get("error")
    if error is not None:
        error_code = error.get("code") if isinstance(error, dict) else None
        code = EXIT_RATE_LIMIT if error_code == 429 else EXIT_FAILED
        raise RunError(code, f"corpo com error: {json.dumps(error, ensure_ascii=False)[:ERROR_SNIPPET]}", status)
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(first, dict) and isinstance(first.get("finish_reason"), str):
        meta.finish_reason = str(first["finish_reason"])
    if not isinstance(content, str) or not content.strip():
        raise RunError(EXIT_FAILED, "resposta vazia", status)
    return content


# ---------------------------------------------------------------- CLI


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executor do adaptador api-chat (uma requisição).")
    parser.add_argument("project_root", type=Path, help="raiz do projeto (onde está o harness.project.yaml)")
    parser.add_argument("worker", help="nome em workers.<nome>")
    parser.add_argument("--request", type=Path, help="arquivo com a mensagem user (padrão: stdin)")
    parser.add_argument("--system", type=Path, help="arquivo com a mensagem system (opcional)")
    parser.add_argument("--output", type=Path, help="arquivo da resposta (padrão: stdout)")
    parser.add_argument("--meta", type=Path, help="arquivo do resumo JSON (padrão: stderr)")
    parser.add_argument("--role", help="papel: injeta role_skills.<papel> do harness.project.yaml")
    parser.add_argument(
        "--skills",
        action="append",
        default=[],
        help="skills do cartão: nome ou nome#regra, separados por vírgula ou repetindo a opção",
    )
    return parser.parse_args(argv)


def build_messages(args: argparse.Namespace, injection: Injection) -> list[JsonValue]:
    try:
        user = args.request.read_text(encoding="utf-8") if args.request else sys.stdin.read()
        system = args.system.read_text(encoding="utf-8") if args.system else None
    except OSError as exc:
        raise RunError(EXIT_CONFIG, f"pedido ilegível: {exc}") from None
    if not user.strip():
        raise RunError(EXIT_CONFIG, "pedido vazio")
    if injection.block:
        user = user.rstrip("\n") + "\n\n" + injection.block
    messages: list[JsonValue] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    return messages


@dataclass
class Secret:
    """Guarda a chave só para mascarar a saída; nunca é impressa."""

    value: str | None = None


def run(args: argparse.Namespace, meta: Meta, secret: Secret) -> str:
    project_root = args.project_root.resolve()
    worker = load_worker(project_root, args.worker)
    meta.model = worker.model
    try:
        injection = injection_for(project_root, args.role, split_cli_items(args.skills))
    except SkillError as exc:
        raise RunError(EXIT_CONFIG, f"skills: {exc}") from None
    meta.skills = injection.summary() if injection.resources else None
    for row in injection.summary_lines() if injection.resources else []:
        print(row, file=sys.stderr)
    messages = build_messages(args, injection)
    key, meta.key_source = resolve_key(worker, project_root)
    secret.value = key
    check_quota(worker, key, meta)
    return chat(worker, key, messages, meta)


def emit_meta(meta: Meta, path: Path | None, secret: Secret) -> None:
    text = mask(meta.to_json(), secret.value)
    if path is None:
        print(text, file=sys.stderr)
    else:
        path.write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    meta = Meta(worker=args.worker)
    secret = Secret()
    try:
        content = run(args, meta, secret)
    except RunError as exc:
        meta.exit_code = exc.exit_code
        meta.error = mask(exc.message, secret.value)
        if exc.http_status is not None:
            meta.http_status = exc.http_status
    except Exception as exc:  # noqa: BLE001 - nada pode vazar em traceback
        meta.exit_code = EXIT_FAILED
        meta.error = mask(f"erro inesperado: {type(exc).__name__}: {exc}", secret.value)
    else:
        safe = mask(content, secret.value)
        if args.output is None:
            sys.stdout.write(safe if safe.endswith("\n") else safe + "\n")
        else:
            args.output.write_text(safe, encoding="utf-8")
    meta.status = STATUS_BY_EXIT[meta.exit_code]
    emit_meta(meta, args.meta, secret)
    return meta.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
