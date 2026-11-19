#!/usr/bin/env python3
"""Executor do adaptador api-tools (`adapter/api-tools.md`): api-chat com laço de function calling.

Uso:
    api_tools.py <raiz do projeto> <trabalhador> [--request ARQ] [--system ARQ] [--output ARQ] [--meta ARQ]
                 [--role PAPEL] [--skills a,b#regra] [--allow-write CAMINHO ...] [--cwd PASTA] [--max-steps N]

- Mesma configuração do api-chat em `workers.<trabalhador>` (`base_url`, `model`, `key_env`, `key_file`, `quota_*`,
  `timeout_s`) mais `max_steps` (padrão 20), `command_timeout_s` (padrão 600) e `roles` (opcional). Aceita
  trabalhador `adapter: api-tools` ou `api-chat` (a mesma conta, agora com ferramentas).
- Chave, cota, skills (`--role`, `--skills`), máscara, códigos de saída e `--meta`: os do `script/api_chat.py`,
  reaproveitados por import.
- Ferramentas (as mesmas para todo modelo e papel), sempre dentro da pasta de trabalho (`--cwd`, padrão a raiz):
  `read_file`, `list_dir`, `search`, `run_command` (só chaves de `commands.*`, sem shell), `write_file` (só nos
  caminhos de `--allow-write`; sem nenhum, a ferramenta nem é oferecida) e `finish(summary)`.
- Resposta sem `tool_calls`: blocos `=== FILE: ===` são gravados com a mesma regra do `write_file`; o texto é a resposta.
- Saída: o `summary` do `finish` (ou o texto final) no stdout ou `--output`; meta JSON no stderr ou `--meta`, com
  `steps`, `tools` (chamadas por nome), `files_written`, `denied` e `usage` somado.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.api_chat import (
    ERROR_SNIPPET,
    EXIT_CONFIG,
    EXIT_FAILED,
    EXIT_RATE_LIMIT,
    STATUS_BY_EXIT,
    JsonValue,
    Meta,
    RunError,
    Secret,
    Worker,
    _error_text,
    _http_json,
    _number,
    _opt_str,
    _req_str,
    build_messages,
    check_quota,
    emit_meta,
    mask,
    resolve_key,
)
from script.project_config import ConfigValue, load_config
from script.skill_inject import SkillError, injection_for, split_cli_items

ADAPTERS: Final = frozenset({"api-tools", "api-chat"})
DEFAULT_MAX_STEPS: Final = 20
DEFAULT_COMMAND_TIMEOUT: Final = 600.0
READ_LIMIT: Final = 60_000  # caracteres por read_file
OUTPUT_LIMIT: Final = 20_000  # caracteres da saída de run_command (fica o fim)
LIST_LIMIT: Final = 500
SEARCH_LIMIT: Final = 200
SEARCH_FILE_BYTES: Final = 1_000_000
SKIP_DIRS: Final = frozenset({".git", "node_modules", "__pycache__", ".venv", ".pytest_cache"})
# Arquivos de segredo: nunca lidos, listados, buscados nem gravados.
SECRET_PATTERNS: Final = (
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "credentials*",
    "secrets*",
)
PARAM_VALUE: Final = re.compile(r"^[A-Za-z0-9_@:=+,./-]+$")
PLACEHOLDER: Final = re.compile(r"<([A-Za-z0-9_-]+)>")
FILE_HEADER: Final = re.compile(r"^=== FILE: (.+?) ===\s*$")
FILE_END: Final = "=== END FILE ==="


@dataclass(frozen=True)
class ToolsWorker:
    """`workers.<nome>` do api-tools: o Worker do api-chat mais os campos do laço."""

    base: Worker
    max_steps: int
    command_timeout_s: float
    roles: list[str]


@dataclass
class ToolsMeta(Meta):
    """Meta do api-chat mais o registro do laço."""

    steps: int = 0
    tools: dict[str, int] = field(default_factory=dict)
    files_written: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    fallback: bool = False


class ToolDenied(Exception):
    """Pedido de ferramenta recusado: vira resposta de erro para o modelo, não encerra o laço."""


# ---------------------------------------------------------------- configuração


def load_worker(project_root: Path, name: str, max_steps: int | None = None) -> ToolsWorker:
    """Lê e valida `workers.<name>` (adapter api-tools ou api-chat)."""
    try:
        config = load_config(project_root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        raise RunError(EXIT_CONFIG, f"harness.project.yaml inválido: {exc}") from exc
    workers = config.get("workers")
    entry = workers.get(name) if isinstance(workers, dict) else None
    if not isinstance(entry, dict):
        raise RunError(EXIT_CONFIG, f"workers.{name} ausente no harness.project.yaml")
    adapter = _opt_str(entry, "adapter")
    if adapter not in ADAPTERS:
        raise RunError(EXIT_CONFIG, f"workers.{name}.adapter é '{adapter}', esperado 'api-tools'")
    base = Worker(
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
    steps = max_steps if max_steps is not None else int(_number(entry, "max_steps", DEFAULT_MAX_STEPS))
    if steps < 1:
        raise RunError(EXIT_CONFIG, "max_steps precisa ser >= 1")
    return ToolsWorker(
        base=base,
        max_steps=steps,
        command_timeout_s=_number(entry, "command_timeout_s", DEFAULT_COMMAND_TIMEOUT),
        roles=(_opt_str(entry, "roles") or "").split(),
    )


def load_commands(project_root: Path) -> dict[str, str]:
    """`commands.*` do projeto: a única lista do que o `run_command` pode rodar."""
    config = load_config(project_root, required=True)
    section: ConfigValue = config.get("commands")
    if not isinstance(section, dict):
        return {}
    return {str(k): str(v) for k, v in section.items() if isinstance(v, str) and v.strip()}


# ---------------------------------------------------------------- sandbox


def is_secret(rel: str) -> bool:
    """Algum componente do caminho casa com um padrão de segredo."""
    return any(fnmatch.fnmatch(part, pattern) for part in Path(rel).parts for pattern in SECRET_PATTERNS)


@dataclass(frozen=True)
class Sandbox:
    """Raiz das ferramentas e escopo de escrita. Todo caminho passa por `resolve`."""

    root: Path
    writable: tuple[str, ...]
    blocked: frozenset[str] = frozenset()

    def resolve(self, rel: str) -> tuple[Path, str]:
        """(caminho real, relativo normalizado); recusa absoluto, fuga (.. ou link) e segredo."""
        text = rel.strip()
        if not text:
            text = "."
        if Path(text).is_absolute():
            raise ToolDenied(f"caminho absoluto recusado: {text}")
        real = (self.root / text).resolve()
        try:
            inside = real.relative_to(self.root)
        except ValueError:
            raise ToolDenied(f"caminho fora da raiz: {text}") from None
        normal = inside.as_posix()
        if is_secret(text) or is_secret(normal) or normal in self.blocked:
            raise ToolDenied(f"arquivo de segredo bloqueado: {text}")
        return real, normal

    def can_write(self, normal: str) -> bool:
        if normal in {"", "."} or normal.split("/")[0] == ".git":
            return False
        for entry in self.writable:
            prefix = entry.strip().strip("/")
            if prefix and (normal == prefix or normal.startswith(prefix + "/")):
                return True
        return False


def normalize_scope(entries: list[str]) -> tuple[str, ...]:
    """Escopo de escrita: relativo, sem `..` e sem segredo; o resto é erro de configuração."""
    result: list[str] = []
    for entry in entries:
        text = entry.strip().strip("/")
        if not text:
            continue
        if Path(text).is_absolute() or ".." in Path(text).parts or is_secret(text):
            raise RunError(EXIT_CONFIG, f"--allow-write inválido: {entry}")
        result.append(text)
    return tuple(result)


# ---------------------------------------------------------------- ferramentas


def _str_arg(arguments: dict[str, JsonValue], name: str, default: str | None = None) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ToolDenied(f"argumento '{name}' precisa ser texto")
    return value


def tool_read_file(box: Sandbox, arguments: dict[str, JsonValue]) -> str:
    real, normal = box.resolve(_str_arg(arguments, "path"))
    if not real.is_file():
        raise ToolDenied(f"não é arquivo: {normal}")
    text = real.read_bytes().decode("utf-8", errors="replace")
    if len(text) > READ_LIMIT:
        return text[:READ_LIMIT] + f"\n[... truncado em {READ_LIMIT} de {len(text)} caracteres]"
    return text


def tool_list_dir(box: Sandbox, arguments: dict[str, JsonValue]) -> str:
    real, normal = box.resolve(_str_arg(arguments, "path", "."))
    if not real.is_dir():
        raise ToolDenied(f"não é pasta: {normal}")
    rows: list[str] = []
    for child in sorted(real.iterdir(), key=lambda p: p.name):
        if is_secret(child.name) or child.name in SKIP_DIRS:
            continue
        rows.append(child.name + ("/" if child.is_dir() else ""))
        if len(rows) >= LIST_LIMIT:
            rows.append(f"[... mais de {LIST_LIMIT} itens]")
            break
    return "\n".join(rows) if rows else "(vazia)"


def tool_search(box: Sandbox, arguments: dict[str, JsonValue]) -> str:
    pattern = _str_arg(arguments, "pattern")
    real, _ = box.resolve(_str_arg(arguments, "path", "."))
    glob = _str_arg(arguments, "glob", "*")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolDenied(f"regex inválida: {exc}") from None
    files = [real] if real.is_file() else sorted(real.rglob("*"))
    hits: list[str] = []
    for path in files:
        try:
            rel = path.resolve().relative_to(box.root).as_posix()
        except ValueError:
            continue  # link para fora da raiz
        parts = Path(rel).parts
        if any(part in SKIP_DIRS for part in parts) or is_secret(rel) or rel in box.blocked:
            continue
        if not path.is_file() or not fnmatch.fnmatch(path.name, glob) or path.stat().st_size > SEARCH_FILE_BYTES:
            continue
        raw = path.read_bytes()
        if b"\0" in raw[:4096]:
            continue
        for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{rel}:{number}: {line[:300]}")
                if len(hits) >= SEARCH_LIMIT:
                    return "\n".join(hits) + f"\n[... parou em {SEARCH_LIMIT} ocorrências]"
    return "\n".join(hits) if hits else "(nenhuma ocorrência)"


def command_argv(template: str, params: dict[str, JsonValue]) -> list[str]:
    """Troca `<nome>` pelos `params` (valor seguro; lista separada por espaço vira vários argumentos)."""
    argv: list[str] = []
    for token in shlex.split(template):
        whole = PLACEHOLDER.fullmatch(token)
        names = PLACEHOLDER.findall(token)
        values: dict[str, list[str]] = {}
        for name in names:
            raw = params.get(name)
            if not isinstance(raw, str) or not raw.strip():
                raise ToolDenied(f"parâmetro <{name}> ausente")
            items = raw.split()
            for item in items:
                if not PARAM_VALUE.match(item) or ".." in Path(item).parts or item.startswith("-"):
                    raise ToolDenied(f"valor recusado para <{name}>: {item}")
            values[name] = items
        if whole is not None:
            argv += values[whole.group(1)]
        else:
            argv.append(PLACEHOLDER.sub(lambda m: " ".join(values[m.group(1)]), token))
    return argv


def tool_run_command(
    box: Sandbox,
    arguments: dict[str, JsonValue],
    commands: dict[str, str],
    timeout: float,
    hidden_env: str,
) -> str:
    name = _str_arg(arguments, "name")
    if name not in commands:
        raise ToolDenied(f"comando fora de commands.*: {name}; disponíveis: {', '.join(sorted(commands))}")
    params = arguments.get("params") or {}
    if not isinstance(params, dict):
        raise ToolDenied("params precisa ser objeto")
    argv = command_argv(commands[name], params)
    env = {k: v for k, v in os.environ.items() if k != hidden_env}
    try:
        done = subprocess.run(
            argv,
            cwd=box.root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"exit=timeout ({timeout:.0f}s)"
    except OSError as exc:
        return f"exit=erro: {type(exc).__name__}: {exc}"
    output = (done.stdout or "") + (done.stderr or "")
    if len(output) > OUTPUT_LIMIT:
        output = f"[... {len(output) - OUTPUT_LIMIT} caracteres cortados do início]\n" + output[-OUTPUT_LIMIT:]
    return f"exit={done.returncode}\n{output}"


def write_checked(box: Sandbox, rel: str, content: str) -> str:
    """Grava dentro do escopo; devolve o caminho normalizado."""
    real, normal = box.resolve(rel)
    if not box.can_write(normal):
        raise ToolDenied(f"escrita fora do escopo do papel: {normal}; permitidos: {', '.join(box.writable) or '(nenhum)'}")
    if real.is_dir():
        raise ToolDenied(f"é pasta: {normal}")
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(content, encoding="utf-8")
    return normal


def tool_specs(writable: bool, commands: dict[str, str]) -> list[JsonValue]:
    """Definições no formato OpenAI (`tools`). `write_file` só existe para quem tem escopo de escrita."""

    def spec(name: str, description: str, properties: dict[str, JsonValue], required: list[JsonValue]) -> JsonValue:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    text: dict[str, JsonValue] = {"type": "string"}
    names: list[JsonValue] = list(sorted(commands))
    specs: list[JsonValue] = [
        spec("read_file", "Lê um arquivo do projeto (caminho relativo à raiz).", {"path": text}, ["path"]),
        spec("list_dir", "Lista uma pasta do projeto (caminho relativo; padrão a raiz).", {"path": text}, []),
        spec(
            "search",
            "Busca uma regex (Python) nas linhas dos arquivos; devolve caminho:linha: texto.",
            {"pattern": text, "path": text, "glob": text},
            ["pattern"],
        ),
        spec(
            "run_command",
            "Roda um comando de verificação do projeto pelo nome (commands.<nome>); params preenche os <marcadores>.",
            {"name": {"type": "string", "enum": names}, "params": {"type": "object"}},
            ["name"],
        ),
    ]
    if writable:
        specs.append(
            spec(
                "write_file",
                "Grava o arquivo inteiro (caminho relativo), só dentro dos caminhos que o papel pode escrever.",
                {"path": text, "content": text},
                ["path", "content"],
            )
        )
    specs.append(spec("finish", "Encerra a tarefa com o resumo final.", {"summary": text}, ["summary"]))
    return specs


# ---------------------------------------------------------------- laço


@dataclass(frozen=True)
class Reply:
    content: str
    tool_calls: list[dict[str, JsonValue]]
    message: dict[str, JsonValue]


def _add_usage(meta: ToolsMeta, usage: JsonValue) -> None:
    if not isinstance(usage, dict):
        return
    total = meta.usage if isinstance(meta.usage, dict) else {}
    for key, value in usage.items():
        if isinstance(value, int | float) and not isinstance(value, bool):
            current = total.get(key)
            base = current if isinstance(current, int | float) and not isinstance(current, bool) else 0
            total[key] = base + value
    meta.usage = total


def call_model(worker: Worker, key: str, messages: list[JsonValue], tools: list[JsonValue], meta: ToolsMeta) -> Reply:
    """Uma chamada de chat com `tools`; erros como no `api_chat.chat`."""
    body: dict[str, JsonValue] = {
        "model": worker.model,
        "temperature": 0,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }
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
    _add_usage(meta, payload.get("usage"))
    error = payload.get("error")
    if error is not None:
        error_code = error.get("code") if isinstance(error, dict) else None
        code = EXIT_RATE_LIMIT if error_code == 429 else EXIT_FAILED
        raise RunError(code, f"corpo com error: {json.dumps(error, ensure_ascii=False)[:ERROR_SNIPPET]}", status)
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(first, dict) or not isinstance(message, dict):
        raise RunError(EXIT_FAILED, "resposta sem choices[0].message", status)
    if isinstance(first.get("finish_reason"), str):
        meta.finish_reason = str(first["finish_reason"])
    content = message.get("content")
    raw_calls = message.get("tool_calls")
    calls = [c for c in raw_calls if isinstance(c, dict)] if isinstance(raw_calls, list) else []
    return Reply(content if isinstance(content, str) else "", calls, message)


def parse_file_blocks(text: str) -> dict[str, str]:
    """Blocos `=== FILE: <caminho> ===` ... `=== END FILE ===` (fallback sem tool_calls)."""
    blocks: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        header = FILE_HEADER.match(line)
        if current is None:
            if header is not None:
                current, body = header.group(1).strip(), []
        elif line.strip() == FILE_END:
            blocks[current] = "\n".join(body) + "\n"
            current = None
        elif header is not None:
            raise RunError(EXIT_FAILED, f"bloco de {current} sem '{FILE_END}' antes do próximo")
        else:
            body.append(line)
    if current is not None:
        raise RunError(EXIT_FAILED, f"bloco de {current} sem '{FILE_END}' (resposta cortada?)")
    return blocks


@dataclass
class Loop:
    """Estado de uma execução: sandbox, comandos e meta."""

    box: Sandbox
    commands: dict[str, str]
    worker: ToolsWorker
    meta: ToolsMeta
    handlers: dict[str, Callable[[dict[str, JsonValue]], str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.handlers = {
            "read_file": lambda a: tool_read_file(self.box, a),
            "list_dir": lambda a: tool_list_dir(self.box, a),
            "search": lambda a: tool_search(self.box, a),
            "run_command": lambda a: tool_run_command(
                self.box, a, self.commands, self.worker.command_timeout_s, self.worker.base.key_env
            ),
        }
        if self.box.writable:
            self.handlers["write_file"] = self._write

    def _write(self, arguments: dict[str, JsonValue]) -> str:
        normal = write_checked(self.box, _str_arg(arguments, "path"), _str_arg(arguments, "content"))
        if normal not in self.meta.files_written:
            self.meta.files_written.append(normal)
        return f"gravado: {normal}"

    def execute(self, name: str, raw_arguments: JsonValue) -> str:
        self.meta.tools[name] = self.meta.tools.get(name, 0) + 1
        handler = self.handlers.get(name)
        try:
            if handler is None:
                raise ToolDenied(f"ferramenta indisponível para este papel: {name}")
            parsed: JsonValue = json.loads(raw_arguments) if isinstance(raw_arguments, str) and raw_arguments else {}
            if not isinstance(parsed, dict):
                raise ToolDenied("argumentos precisam ser um objeto JSON")
            return handler(parsed)
        except ToolDenied as exc:
            self.meta.denied.append(f"{name}: {exc}")
            return f"erro: {exc}"
        except (ValueError, OSError) as exc:
            return f"erro: {type(exc).__name__}: {exc}"

    def apply_blocks(self, blocks: dict[str, str]) -> None:
        if not self.box.writable:
            raise RunError(EXIT_FAILED, "resposta com blocos FILE, mas o papel não escreve")
        for rel, content in blocks.items():
            try:
                normal = write_checked(self.box, rel, content)
            except ToolDenied as exc:
                raise RunError(EXIT_FAILED, f"bloco FILE recusado: {exc}") from None
            self.meta.files_written.append(normal)

    def run(self, key: str, messages: list[JsonValue]) -> str:
        tools = tool_specs(bool(self.box.writable), self.commands)
        while self.meta.steps < self.worker.max_steps:
            self.meta.steps += 1
            reply = call_model(self.worker.base, key, messages, tools, self.meta)
            if not reply.tool_calls:
                blocks = parse_file_blocks(reply.content)
                if blocks:
                    self.meta.fallback = True
                    self.apply_blocks(blocks)
                if not reply.content.strip():
                    raise RunError(EXIT_FAILED, "resposta vazia, sem tool_calls", self.meta.http_status)
                return reply.content
            assistant: dict[str, JsonValue] = {
                "role": "assistant",
                "content": reply.content or None,
                "tool_calls": list(reply.tool_calls),
            }
            messages.append(assistant)
            summary: str | None = None
            for call in reply.tool_calls:
                function = call.get("function")
                name = str(function.get("name", "")) if isinstance(function, dict) else ""
                arguments = function.get("arguments") if isinstance(function, dict) else None
                if name == "finish":
                    self.meta.tools["finish"] = self.meta.tools.get("finish", 0) + 1
                    parsed = json.loads(arguments) if isinstance(arguments, str) and arguments else {}
                    text = parsed.get("summary") if isinstance(parsed, dict) else None
                    summary = text if isinstance(text, str) and text.strip() else (reply.content or "(sem resumo)")
                    result = "ok"
                else:
                    result = self.execute(name, arguments)
                messages.append({"role": "tool", "tool_call_id": str(call.get("id", "")), "content": result})
            if summary is not None:
                return summary
        raise RunError(EXIT_FAILED, f"max_steps ({self.worker.max_steps}) atingido sem finish")


# ---------------------------------------------------------------- CLI


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executor do adaptador api-tools (laço de function calling).")
    parser.add_argument("project_root", type=Path, help="raiz do projeto (onde está o harness.project.yaml)")
    parser.add_argument("worker", help="nome em workers.<nome>")
    parser.add_argument("--request", type=Path, help="arquivo com a mensagem user (padrão: stdin)")
    parser.add_argument("--system", type=Path, help="arquivo com a mensagem system (opcional)")
    parser.add_argument("--output", type=Path, help="arquivo da resposta (padrão: stdout)")
    parser.add_argument("--meta", type=Path, help="arquivo do resumo JSON (padrão: stderr)")
    parser.add_argument("--role", help="papel: injeta role_skills.<papel> e confere workers.<nome>.roles")
    parser.add_argument("--skills", action="append", default=[], help="skills do cartão: nome ou nome#regra")
    parser.add_argument(
        "--allow-write",
        action="append",
        default=[],
        help="caminho ou prefixo que o write_file pode gravar (repetir); sem nenhum, não há write_file",
    )
    parser.add_argument("--cwd", type=Path, help="pasta das ferramentas (padrão: a raiz do projeto)")
    parser.add_argument("--max-steps", type=int, help="substitui workers.<nome>.max_steps")
    return parser.parse_args(argv)


def run(args: argparse.Namespace, meta: ToolsMeta, secret: Secret) -> str:
    project_root = args.project_root.resolve()
    worker = load_worker(project_root, args.worker, args.max_steps)
    meta.model = worker.base.model
    if args.role is not None and worker.roles and args.role not in worker.roles:
        raise RunError(EXIT_CONFIG, f"papel '{args.role}' fora de workers.{args.worker}.roles")
    tool_root = (args.cwd or project_root).resolve()
    if not tool_root.is_dir():
        raise RunError(EXIT_CONFIG, f"--cwd não é pasta: {args.cwd}")
    blocked = frozenset({worker.base.key_file} if worker.base.key_file else set())
    box = Sandbox(root=tool_root, writable=normalize_scope(args.allow_write), blocked=blocked)
    try:
        injection = injection_for(project_root, args.role, split_cli_items(args.skills))
        commands = load_commands(project_root)
    except SkillError as exc:
        raise RunError(EXIT_CONFIG, f"skills: {exc}") from None
    meta.skills = injection.summary() if injection.resources else None
    for row in injection.summary_lines() if injection.resources else []:
        print(row, file=sys.stderr)
    messages = build_messages(args, injection)
    key, meta.key_source = resolve_key(worker.base, project_root)
    secret.value = key
    check_quota(worker.base, key, meta)
    return Loop(box=box, commands=commands, worker=worker, meta=meta).run(key, messages)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    meta = ToolsMeta(worker=args.worker)
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
