"""Pré-gate local: comandos baratos (sem LLM) rodados na worktree da tentativa antes do commit e do gate."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_TIMEOUT_S = 300
PREGATE_FAILURE = "pregate-fail"


class PregateConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PregateCommand:
    argv: tuple[str, ...]
    timeout_s: int


@dataclass(frozen=True)
class PregateResult:
    passed: bool
    output: str
    exit_code: int


def parse_pregate(config: Mapping[str, object], stage: str) -> list[PregateCommand]:
    """Comandos do pré-gate da stage; lista vazia se ausente ou `enabled` != true (padrão false)."""
    section = config.get("pregate")
    if section is None:
        return []
    if not isinstance(section, dict):
        raise PregateConfigError("pregate precisa ser mapa stage -> {enabled, commands}")
    entry = section.get(stage)
    if entry is None:
        return []
    if not isinstance(entry, dict):
        raise PregateConfigError(f"pregate.{stage} precisa ser mapa")
    enabled = entry.get("enabled", False)
    if type(enabled) is not bool:
        raise PregateConfigError(f"pregate.{stage}.enabled precisa ser booleano")
    if not enabled:
        return []
    raw_commands = entry.get("commands", [])
    if not isinstance(raw_commands, list):
        raise PregateConfigError(f"pregate.{stage}.commands precisa ser lista")
    commands: list[PregateCommand] = []
    for raw in raw_commands:
        timeout_s: object = DEFAULT_TIMEOUT_S
        run: object = raw
        if isinstance(raw, dict):
            run = raw.get("run")
            timeout_s = raw.get("timeout_s", DEFAULT_TIMEOUT_S)
        if not isinstance(run, str) or not run.strip():
            raise PregateConfigError(f"pregate.{stage}: comando precisa ser texto não vazio")
        if type(timeout_s) is not int or timeout_s < 1:
            raise PregateConfigError(f"pregate.{stage}: timeout_s inválido")
        commands.append(PregateCommand(tuple(shlex.split(run)), timeout_s))
    return commands


def changed_files(worktree: Path) -> list[str]:
    """Arquivos alterados/novos da tentativa que ainda existem (exclui removidos)."""
    raw = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all", "-z"],
        check=False, capture_output=True, text=True,
    )
    if raw.returncode != 0:
        return []
    files: list[str] = []
    entries = raw.stdout.split("\0")
    skip_next = False
    for entry in entries:
        if skip_next:
            skip_next = False
            continue
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            skip_next = True  # o próximo item é o path de origem
        if "D" not in code and (worktree / path).is_file():
            files.append(path)
    return files


def expand_files(argv: tuple[str, ...], files: list[str]) -> list[str]:
    """Troca o token `{files}` pelos arquivos (um argumento cada); `{files}` embutido em outro token vira a lista separada por espaço."""
    out: list[str] = []
    for token in argv:
        if token == "{files}":
            out.extend(files)
        elif "{files}" in token:
            out.append(token.replace("{files}", " ".join(files)))
        else:
            out.append(token)
    return out


def run_pregate(commands: list[PregateCommand], worktree: Path) -> PregateResult:
    """Roda os comandos em ordem e para no primeiro que falhar. Sem arquivos alterados, comandos com {files} são pulados."""
    files = changed_files(worktree)
    log: list[str] = []
    for command in commands:
        uses_files = any("{files}" in token for token in command.argv)
        if uses_files and not files:
            log.append(f"$ {shlex.join(command.argv)}\n(pulado: nenhum arquivo alterado)\n")
            continue
        argv = expand_files(command.argv, files)
        log.append(f"$ {shlex.join(argv)}\n")
        try:
            result = subprocess.run(
                argv, cwd=worktree, check=False, capture_output=True, text=True, timeout=command.timeout_s,
            )
        except FileNotFoundError:
            log.append(f"pregate-fail: comando não encontrado: {argv[0]}\n")
            return PregateResult(False, "".join(log), 127)
        except OSError as exc:
            log.append(f"pregate-fail: não foi possível executar {argv[0]}: {exc}\n")
            return PregateResult(False, "".join(log), 126)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
            log.append(partial)
            log.append(f"\npregate-fail: timeout após {command.timeout_s}s\n")
            return PregateResult(False, "".join(log), 124)
        log.append(result.stdout + result.stderr)
        if result.returncode != 0:
            log.append(f"\npregate-fail: exit {result.returncode}\n")
            return PregateResult(False, "".join(log), result.returncode)
    return PregateResult(True, "".join(log), 0)
