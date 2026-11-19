#!/usr/bin/env python3
"""Despachante de trabalhadores (`adapter/README.md`): escolhe o executor pelo papel e o chama.

Uso:
    dispatch.py <raiz do projeto> --role PAPEL [--second-opinion] [--mode read|write] [--skills a,b#regra]
                [--request ARQ] [--cwd PASTA] [--output ARQ] [--meta ARQ]

- Lê `roles.<papel>.worker` (ou `roles.<papel>.second_opinion` com `--second-opinion`) do `harness.project.yaml`,
  descobre o `adapter` de `workers.<nome>` e chama o executor certo (`api_chat.py` ou `cli_agent.py`) com
  `--role` e `--skills` do cartão: o orquestrador não monta comando nem skills.
- `--mode` (só cli-agent) padrão: `write` para `refiner` e `implementer`, `read` para os demais papéis.
- stdin, stdout e stderr passam direto ao executor; o código de saída é o dele.
- Papel sem trabalhador = saída 8 (o orquestrador usa o `agent` do papel). Configuração inválida = saída 2.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import ConfigValue, load_config

EXIT_CONFIG: Final = 2
EXIT_NO_WORKER: Final = 8

EXECUTORS: Final[dict[str, str]] = {"api-chat": "api_chat.py", "cli-agent": "cli_agent.py"}
WRITE_ROLES: Final = frozenset({"refiner", "implementer"})


class DispatchError(Exception):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _entry(config: dict[str, ConfigValue], key: str, name: str) -> dict[str, ConfigValue] | None:
    section = config.get(key)
    value = section.get(name) if isinstance(section, dict) else None
    return value if isinstance(value, dict) else None


def resolve_worker(project_root: Path, role: str, second_opinion: bool) -> tuple[str, str]:
    """Devolve (nome do trabalhador, adaptador) do papel; sem trabalhador, `DispatchError` 8."""
    try:
        config = load_config(project_root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        raise DispatchError(EXIT_CONFIG, f"harness.project.yaml inválido: {exc}") from exc
    role_entry = _entry(config, "roles", role)
    field = "second_opinion" if second_opinion else "worker"
    name = role_entry.get(field) if role_entry else None
    if not isinstance(name, str) or not name.strip():
        raise DispatchError(EXIT_NO_WORKER, f"roles.{role}.{field} ausente: o papel segue com o agent")
    name = name.strip()
    worker = _entry(config, "workers", name)
    if worker is None:
        raise DispatchError(EXIT_CONFIG, f"workers.{name} ausente no harness.project.yaml")
    adapter = str(worker.get("adapter", "")).strip()
    if adapter not in EXECUTORS:
        raise DispatchError(EXIT_CONFIG, f"workers.{name}.adapter '{adapter}' sem executor")
    return name, adapter


def build_command(args: argparse.Namespace, name: str, adapter: str) -> list[str]:
    command = [sys.executable, str(Path(__file__).resolve().parent / EXECUTORS[adapter]), str(args.project_root), name]
    command += ["--role", args.role]
    for item in args.skills:
        command += ["--skills", item]
    if args.request:
        command += ["--request", str(args.request)]
    if args.output:
        command += ["--output", str(args.output)]
    if args.meta:
        command += ["--meta", str(args.meta)]
    if adapter == "cli-agent":
        mode = args.mode or ("write" if args.role in WRITE_ROLES else "read")
        command += ["--mode", mode]
        if args.cwd:
            command += ["--cwd", str(args.cwd)]
    return command


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Despacha a etapa do papel para o trabalhador configurado.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--role", required=True)
    parser.add_argument("--second-opinion", action="store_true", help="usa roles.<papel>.second_opinion")
    parser.add_argument("--mode", choices=["read", "write"], help="só cli-agent (padrão pelo papel)")
    parser.add_argument("--skills", action="append", default=[], help="skills do cartão: nome ou nome#regra")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--cwd", type=Path, help="só cli-agent: a cópia de trabalho")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--meta", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        name, adapter = resolve_worker(args.project_root.resolve(), args.role, args.second_opinion)
    except DispatchError as exc:
        print(f"dispatch: {exc}", file=sys.stderr)
        return exc.exit_code
    return subprocess.run(build_command(args, name, adapter), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
