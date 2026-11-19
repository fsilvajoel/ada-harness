#!/usr/bin/env python3
"""Despachante de trabalhadores (`adapter/README.md`): escolhe o executor pelo papel e o chama.

Uso:
    dispatch.py <raiz do projeto> --role PAPEL [--second-opinion] [--mode read|write] [--skills a,b#regra]
                [--request ARQ] [--system ARQ] [--cwd PASTA] [--output ARQ] [--meta ARQ]
                [--stage ETAPA] [--allowed-path CAMINHO ...]

- Lê `roles.<papel>.worker` (ou `roles.<papel>.second_opinion` com `--second-opinion`) do `harness.project.yaml`,
  descobre o `adapter` de `workers.<nome>` e chama o executor certo (`api_chat.py`, `api_tools.py` ou
  `cli_agent.py`) com `--role` e `--skills` do cartão: o orquestrador não monta comando nem skills.
- Rota opcional por papel (só para `worker`): `roles.<papel>.worker_stages` (lista de etapas; fora delas, ou sem
  `--stage`, o papel segue com o agent) e `roles.<papel>.worker_paths` (lista de prefixos; todo `--allowed-path` do
  cartão precisa cair num deles, senão o papel segue com o agent). A decisão sai no stderr: `dispatch: rota {json}`.
- `--system` (api-chat e api-tools): sem ele, o system é o bloco fixo de `adapter/<adaptador>.md` (única definição
  no harness); com ele, o arquivo informado substitui o padrão. Passar `--system` com cli-agent = saída 2.
- `--mode` (cli-agent e api-tools) padrão: `write` para `refiner` e `implementer`, `read` para os demais papéis.
  No api-tools, `write` com papel de escrita repassa cada `--allowed-path` como `--allow-write`; sem isso, o
  trabalhador não recebe `write_file`.
- stdin, stdout e stderr passam direto ao executor; o código de saída é o dele.
- Papel sem trabalhador = saída 8 (o orquestrador usa o `agent` do papel). Configuração inválida = saída 2.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import ConfigValue, load_config

EXIT_CONFIG: Final = 2
EXIT_NO_WORKER: Final = 8

EXECUTORS: Final[dict[str, str]] = {
    "api-chat": "api_chat.py",
    "api-tools": "api_tools.py",
    "cli-agent": "cli_agent.py",
}
WRITE_ROLES: Final = frozenset({"refiner", "implementer"})
ADAPTER_DOC: Final = ROOT / "adapter" / "api-chat.md"
SYSTEM_DOCS: Final[dict[str, Path]] = {
    "api-chat": ADAPTER_DOC,
    "api-tools": ROOT / "adapter" / "api-tools.md",
}
SYSTEM_ANCHOR: Final = "**system** (fixo):"
SYSTEM_BLOCK: Final = re.compile(r"```text\n(.*?)\n```", re.DOTALL)


class DispatchError(Exception):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def default_system(doc: Path = ADAPTER_DOC) -> str:
    """Texto do system fixo: o bloco ```text logo após o marcador `**system** (fixo):` do adaptador."""
    _, found, after = doc.read_text(encoding="utf-8").partition(SYSTEM_ANCHOR)
    match = SYSTEM_BLOCK.search(after) if found else None
    if match is None:
        raise DispatchError(EXIT_CONFIG, f"system fixo ausente em {doc.name}")
    return match.group(1) + "\n"


def _entry(config: dict[str, ConfigValue], key: str, name: str) -> dict[str, ConfigValue] | None:
    section = config.get(key)
    value = section.get(name) if isinstance(section, dict) else None
    return value if isinstance(value, dict) else None


def _str_list(entry: dict[str, ConfigValue], field: str, role: str) -> list[str] | None:
    """Lista opcional de textos (`worker_stages`, `worker_paths`); ausente = None, outro tipo = saída 2."""
    value = entry.get(field)
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise DispatchError(EXIT_CONFIG, f"roles.{role}.{field} precisa ser lista de textos")
    return [str(item).strip() for item in value]


def _under(path: str, prefix: str) -> bool:
    clean = path.strip().removeprefix("./").strip("/")
    base = prefix.strip().removeprefix("./").strip("/")
    if ".." in clean.split("/"):
        return False
    return bool(base) and (clean == base or clean.startswith(base + "/"))


def route_reason(
    role_entry: dict[str, ConfigValue], role: str, stage: str | None, allowed_paths: list[str] | None
) -> str | None:
    """Por que o papel não usa o `worker` aqui (None = usa)."""
    stages = _str_list(role_entry, "worker_stages", role)
    if stages is not None and (stage is None or stage not in stages):
        return f"etapa '{stage or '-'}' fora de roles.{role}.worker_stages {stages}"
    prefixes = _str_list(role_entry, "worker_paths", role)
    if prefixes is not None:
        paths = allowed_paths or []
        if not paths:
            return f"cartão sem allowedPaths; roles.{role}.worker_paths exige todos dentro de {prefixes}"
        outside = [p for p in paths if not any(_under(p, prefix) for prefix in prefixes)]
        if outside:
            return f"allowedPaths fora de roles.{role}.worker_paths {prefixes}: {outside}"
    return None


def log_route(role: str, stage: str | None, decision: str, worker: str | None, adapter: str | None, reason: str) -> None:
    record = {
        "role": role,
        "stage": stage,
        "decision": decision,
        "worker": worker,
        "adapter": adapter,
        "reason": reason,
    }
    print(f"dispatch: rota {json.dumps(record, ensure_ascii=False)}", file=sys.stderr)


def resolve_worker(
    project_root: Path,
    role: str,
    second_opinion: bool,
    stage: str | None = None,
    allowed_paths: list[str] | None = None,
) -> tuple[str, str]:
    """Devolve (nome do trabalhador, adaptador) do papel; sem trabalhador (ou fora da rota), `DispatchError` 8."""
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
    if not second_opinion and role_entry is not None:
        reason = route_reason(role_entry, role, stage, allowed_paths)
        if reason is not None:
            raise DispatchError(EXIT_NO_WORKER, f"{reason}: o papel segue com o agent")
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
    if args.system is not None and adapter in SYSTEM_DOCS:
        command += ["--system", str(args.system)]
    if args.output:
        command += ["--output", str(args.output)]
    if args.meta:
        command += ["--meta", str(args.meta)]
    if adapter == "cli-agent":
        mode = args.mode or ("write" if args.role in WRITE_ROLES else "read")
        command += ["--mode", mode]
        if args.cwd:
            command += ["--cwd", str(args.cwd)]
    if adapter == "api-tools":
        mode = args.mode or ("write" if args.role in WRITE_ROLES else "read")
        if mode == "write" and args.role in WRITE_ROLES:
            for path in args.allowed_path:
                command += ["--allow-write", path]
        if args.cwd:
            command += ["--cwd", str(args.cwd)]
    return command


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Despacha a etapa do papel para o trabalhador configurado.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--role", required=True)
    parser.add_argument("--second-opinion", action="store_true", help="usa roles.<papel>.second_opinion")
    parser.add_argument("--mode", choices=["read", "write"], help="cli-agent e api-tools (padrão pelo papel)")
    parser.add_argument("--skills", action="append", default=[], help="skills do cartão: nome ou nome#regra")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--system", type=Path, help="api-chat e api-tools: system próprio (padrão: o do adaptador)")
    parser.add_argument("--cwd", type=Path, help="cli-agent e api-tools: a cópia de trabalho")
    parser.add_argument("--stage", help="etapa do workflow (para roles.<papel>.worker_stages)")
    parser.add_argument(
        "--allowed-path",
        action="append",
        default=[],
        help="allowedPaths do cartão (repetir): worker_paths e escopo de escrita do api-tools",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--meta", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        try:
            name, adapter = resolve_worker(
                args.project_root.resolve(), args.role, args.second_opinion, args.stage, args.allowed_path
            )
        except DispatchError as exc:
            if exc.exit_code == EXIT_NO_WORKER:
                log_route(args.role, args.stage, "agent", None, None, str(exc))
            raise
        log_route(args.role, args.stage, "worker", name, adapter, "second_opinion" if args.second_opinion else "worker")
        if args.system is not None and adapter not in SYSTEM_DOCS:
            raise DispatchError(EXIT_CONFIG, "--system só vale para os adaptadores api-chat e api-tools")
        with tempfile.TemporaryDirectory() as tmp:
            if adapter in SYSTEM_DOCS and args.system is None:
                args.system = Path(tmp) / "system.txt"
                args.system.write_text(default_system(SYSTEM_DOCS[adapter]), encoding="utf-8")
            return subprocess.run(build_command(args, name, adapter), check=False).returncode
    except DispatchError as exc:
        print(f"dispatch: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
