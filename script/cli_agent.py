#!/usr/bin/env python3
"""Executor do adaptador cli-agent (`adapter/cli-agent.md`): uma execução do comando do trabalhador.

Uso:
    cli_agent.py <raiz do projeto> <trabalhador> --mode read|write [--request ARQ] [--cwd PASTA]
                 [--output ARQ] [--meta ARQ] [--role PAPEL] [--skills a,b#regra]

- Lê `workers.<trabalhador>` do `harness.project.yaml`: `command`, `read_args` ou `write_args` (conforme `--mode`),
  `output_jq`, `usage_jq`, `roles` e `timeout_s`. `--role` fora de `roles` = saída 2.
- O briefing vem de `--request` ou do stdin. O executor anexa a seção `# Skills e regras` (`script/skill_inject.py`:
  `role_skills.<--role>` mais `--skills`, o campo `skills` do cartão) e passa tudo ao comando no stdin, com a pasta
  corrente em `--cwd` (a cópia de trabalho; padrão: a atual). Acima do teto `skills_max_bytes` = saída 2, sem rodar.
- A resposta (stdout do comando, filtrado por `output_jq` com `jq -rs` quando houver) vai para o stdout ou
  `--output`; o resumo JSON (status, recursos injetados, consumo por `usage_jq`, erro) vai para o stderr ou `--meta`.

Uma execução por chamada: tentativas, conferência da cópia, gates e escalada ficam com o orquestrador.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import ConfigValue, load_config
from script.skill_inject import Injection, SkillError, injection_for, split_cli_items

EXIT_OK: Final = 0
EXIT_CONFIG: Final = 2  # uso ou configuração inválida (inclui skill inexistente e teto)
EXIT_NO_COMMAND: Final = 7  # comando ausente: escala na hora
EXIT_FAILED: Final = 6  # saída diferente de 0, timeout, resposta vazia ou filtro jq inválido

STATUS_BY_EXIT: Final[dict[int, str]] = {
    EXIT_OK: "ok",
    EXIT_CONFIG: "config",
    EXIT_NO_COMMAND: "no-command",
    EXIT_FAILED: "failed",
}
SNIPPET: Final = 500

# Bash restrito (opcional, desligado por padrão): `workers.<nome>.allowedBashCommands`, só no modo write.
BASH_DENY_BASE: Final[tuple[str, ...]] = (
    "curl", "wget", "nc", "ncat", "ssh", "scp", "rsync", "sudo", "su", "rm", "rmdir", "mv", "dd", "chmod", "chown",
    "kill", "pkill", "bash", "sh", "zsh", "eval", "exec", "xargs", "env", "python", "python3", "node", "perl",
    "git push", "git remote", "git config", "git reset", "git clean", "git checkout", "git branch -D",
    "pnpm publish", "npm publish", "pnpm add", "pnpm install", "npm install", "pnpm dlx", "npx",
)  # fmt: skip
_FORBIDDEN_CHARS: Final = frozenset(";&|<>`$()\\{}\n\r")
_DANGEROUS_WORDS: Final = frozenset(BASH_DENY_BASE) | {"cd", "source", "."}


class RunError(Exception):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


@dataclass(frozen=True)
class Worker:
    name: str
    command: str
    args: list[str]
    output_jq: str | None
    usage_jq: str | None
    roles: list[str]
    timeout_s: float
    allowed_bash: tuple[str, ...] = ()


@dataclass
class Meta:
    worker: str
    mode: str | None = None
    status: str = "ok"
    exit_code: int = EXIT_OK
    returncode: int | None = None
    usage: str | None = None
    error: str | None = None
    skills: list[dict[str, str | int]] | None = None

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


def _opt(entry: dict[str, ConfigValue], field: str) -> str | None:
    value = entry.get(field)
    text = str(value).strip() if value is not None else ""
    return text or None


def load_worker(project_root: Path, name: str, mode: str) -> Worker:
    try:
        config = load_config(project_root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        raise RunError(EXIT_CONFIG, f"harness.project.yaml inválido: {exc}") from exc
    workers = config.get("workers")
    entry = workers.get(name) if isinstance(workers, dict) else None
    if not isinstance(entry, dict):
        raise RunError(EXIT_CONFIG, f"workers.{name} ausente no harness.project.yaml")
    adapter = _opt(entry, "adapter")
    if adapter != "cli-agent":
        raise RunError(EXIT_CONFIG, f"workers.{name}.adapter é '{adapter}', esperado 'cli-agent'")
    command = _opt(entry, "command")
    if command is None:
        raise RunError(EXIT_CONFIG, f"workers.{name}.command ausente")
    args_field = f"{mode}_args"
    raw_args = _opt(entry, args_field)
    if raw_args is None:
        raise RunError(EXIT_CONFIG, f"workers.{name}.{args_field} ausente")
    timeout = entry.get("timeout_s", 900)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float | str):
        raise RunError(EXIT_CONFIG, "timeout_s precisa ser número")
    try:
        timeout_s = float(timeout)
    except ValueError as exc:
        raise RunError(EXIT_CONFIG, "timeout_s precisa ser número") from exc
    allowed_bash = parse_allowed_bash(entry.get("allowedBashCommands")) if mode == "write" else ()
    return Worker(
        name=name,
        command=command,
        args=bash_args(shlex.split(raw_args), allowed_bash),
        output_jq=_opt(entry, "output_jq"),
        usage_jq=_opt(entry, "usage_jq"),
        roles=(_opt(entry, "roles") or "").split(),
        timeout_s=timeout_s,
        allowed_bash=allowed_bash,
    )


def validate_bash_pattern(pattern: str) -> str:
    """Valida um padrão de Bash permitido; levanta RunError(EXIT_CONFIG) se perigoso ou fora do escopo."""
    text = pattern.strip()
    if not text or text.startswith("*"):
        raise RunError(EXIT_CONFIG, f"allowedBashCommands: padrão genérico demais: '{pattern}'")
    if any(char in _FORBIDDEN_CHARS for char in text):
        raise RunError(EXIT_CONFIG, f"allowedBashCommands: metacaractere de shell em '{pattern}'")
    if ".." in text or text.startswith("/") or "~" in text or " /" in text:
        raise RunError(EXIT_CONFIG, f"allowedBashCommands: caminho fora da worktree em '{pattern}'")
    lowered = text.lower()
    for word in _DANGEROUS_WORDS:
        if lowered == word or lowered.startswith(word + " ") or lowered.startswith(word + "*"):
            raise RunError(EXIT_CONFIG, f"allowedBashCommands: comando proibido '{word}' em '{pattern}'")
    if " -c" in lowered or "--eval" in lowered or " -e " in lowered:
        raise RunError(EXIT_CONFIG, f"allowedBashCommands: execução de código inline em '{pattern}'")
    return text


def parse_allowed_bash(raw: ConfigValue | None) -> tuple[str, ...]:
    """Aceita lista YAML ou texto com padrões separados por vírgula; vazio = desligado."""
    if raw is None:
        return ()
    items = raw if isinstance(raw, list) else str(raw).split(",")
    patterns = [validate_bash_pattern(str(item)) for item in items if str(item).strip()]
    return tuple(dict.fromkeys(patterns))


def _extend_flag(args: list[str], flag: str, values: list[str], drop: frozenset[str] = frozenset()) -> list[str]:
    """Acrescenta `values` à lista variádica de `flag` (criando-a se faltar), removendo itens de `drop`."""
    out: list[str] = []
    seen = False
    in_flag = False
    for token in args:
        if token.startswith("-"):
            in_flag = token == flag
            if in_flag:
                seen = True
                out.append(token)
                out.extend(values)
                continue
        elif in_flag and token in drop:
            continue
        out.append(token)
    if not seen:
        out.extend([flag, *values])
    return out


def bash_args(args: list[str], patterns: tuple[str, ...]) -> list[str]:
    """Traduz os padrões para `--allowedTools Bash(...)` + negações explícitas (negar vence permitir no claude)."""
    if not patterns:
        return args
    allow = [f"Bash({pattern})" for pattern in patterns]
    deny = [f"Bash({word} *)" for word in BASH_DENY_BASE] + [f"Bash({word})" for word in BASH_DENY_BASE]
    out = _extend_flag(args, "--allowedTools", allow)
    return _extend_flag(out, "--disallowedTools", deny, drop=frozenset({"Bash"}))


def jq_filter(expression: str, document: str) -> str:
    """Aplica `jq -rs <expressão>` ao texto; erro do jq (ou jq ausente) = tentativa falha."""
    try:
        done = subprocess.run(
            ["jq", "-rs", expression], input=document, capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunError(EXIT_FAILED, f"jq falhou: {type(exc).__name__}") from None
    if done.returncode != 0:
        raise RunError(EXIT_FAILED, f"filtro jq inválido ou saída não-JSON: {done.stderr.strip()[:SNIPPET]}")
    return done.stdout.strip()


def build_prompt(request: str, injection: Injection) -> str:
    if not request.strip():
        raise RunError(EXIT_CONFIG, "pedido vazio")
    if injection.block:
        return request.rstrip("\n") + "\n\n" + injection.block
    return request


def run_command(worker: Worker, prompt: str, cwd: Path) -> str:
    try:
        done = subprocess.run(
            [worker.command, *worker.args],
            input=prompt,
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            timeout=worker.timeout_s,
        )
    except FileNotFoundError:
        raise RunError(EXIT_NO_COMMAND, f"comando ausente: {worker.command}") from None
    except subprocess.TimeoutExpired:
        raise RunError(EXIT_FAILED, f"timeout de {worker.timeout_s:g}s") from None
    except OSError as exc:
        raise RunError(EXIT_FAILED, f"comando não executou: {type(exc).__name__}: {exc}") from None
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip()[-SNIPPET:]
        raise RunError(EXIT_FAILED, f"comando saiu com {done.returncode}: {tail}")
    return done.stdout


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executor do adaptador cli-agent (uma execução).")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("worker", help="nome em workers.<nome>")
    parser.add_argument("--mode", choices=["read", "write"], required=True, help="read_args ou write_args")
    parser.add_argument("--request", type=Path, help="arquivo com o briefing (padrão: stdin)")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="pasta de execução (a cópia de trabalho)")
    parser.add_argument("--output", type=Path, help="arquivo da resposta (padrão: stdout)")
    parser.add_argument("--meta", type=Path, help="arquivo do resumo JSON (padrão: stderr)")
    parser.add_argument("--role", help="papel: injeta role_skills.<papel> e confere workers.<nome>.roles")
    parser.add_argument("--skills", action="append", default=[], help="skills do cartão: nome ou nome#regra")
    return parser.parse_args(argv)


def run(args: argparse.Namespace, meta: Meta) -> str:
    project_root = args.project_root.resolve()
    meta.mode = args.mode
    worker = load_worker(project_root, args.worker, args.mode)
    if args.role is not None and worker.roles and args.role not in worker.roles:
        raise RunError(EXIT_CONFIG, f"papel '{args.role}' fora de workers.{args.worker}.roles")
    try:
        injection = injection_for(project_root, args.role, split_cli_items(args.skills))
    except SkillError as exc:
        raise RunError(EXIT_CONFIG, f"skills: {exc}") from None
    meta.skills = injection.summary() if injection.resources else None
    for row in injection.summary_lines() if injection.resources else []:
        print(row, file=sys.stderr)
    try:
        request = args.request.read_text(encoding="utf-8") if args.request else sys.stdin.read()
    except OSError as exc:
        raise RunError(EXIT_CONFIG, f"pedido ilegível: {exc}") from None
    prompt = build_prompt(request, injection)
    if not args.cwd.is_dir():
        raise RunError(EXIT_CONFIG, f"--cwd não é pasta: {args.cwd}")
    raw = run_command(worker, prompt, args.cwd)
    answer = jq_filter(worker.output_jq, raw) if worker.output_jq else raw
    if worker.usage_jq:
        meta.usage = jq_filter(worker.usage_jq, raw) or None
    if not answer.strip() or answer.strip() == "null":
        raise RunError(EXIT_FAILED, "resposta vazia")
    return answer


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    meta = Meta(worker=args.worker)
    answer: str | None = None
    try:
        answer = run(args, meta)
    except RunError as exc:
        meta.exit_code = exc.exit_code
        meta.error = exc.message
    meta.status = STATUS_BY_EXIT[meta.exit_code]
    if answer is not None:
        text = answer if answer.endswith("\n") else answer + "\n"
        if args.output is None:
            sys.stdout.write(text)
        else:
            args.output.write_text(text, encoding="utf-8")
    if args.meta is None:
        print(meta.to_json(), file=sys.stderr)
    else:
        args.meta.write_text(meta.to_json() + "\n", encoding="utf-8")
    return meta.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
