#!/usr/bin/env python3
"""Gate portable: o harness não pode citar o projeto alvo nem caminhos locais.

A lista de textos proibidos fica em `portable.forbidden` no
`harness.project.yaml` do projeto alvo, que mora fora do harness.
Uso: `check_portable.py <caminho do harness.project.yaml>`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import get_str_list, parse_config_text

SKIPPED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".harness"}


def forbidden_tokens(config_path: Path) -> list[str]:
    """Lê `portable.forbidden` do yaml do projeto."""
    return get_str_list(parse_config_text(config_path.read_text(encoding="utf-8")), "portable", "forbidden")


def find_forbidden(harness_root: Path, tokens: list[str]) -> list[tuple[str, str]]:
    """Devolve pares (caminho relativo, texto proibido) encontrados no harness."""
    matches: list[tuple[str, str]] = []
    for path in sorted(harness_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIPPED_PARTS for part in path.relative_to(harness_root).parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in tokens:
            if token in content:
                matches.append((str(path.relative_to(harness_root)), token))
    return matches


def main() -> int:
    if len(sys.argv) != 2:
        print("uso: check_portable.py <caminho do harness.project.yaml do projeto>")
        return 2
    config_path = Path(sys.argv[1]).resolve()
    tokens = forbidden_tokens(config_path)
    if not tokens:
        print("Gate portable falhou.")
        print(f"- {config_path.name} não declara portable.forbidden")
        return 1
    matches = find_forbidden(ROOT, tokens)
    if matches:
        print("Gate portable falhou.")
        for path, token in matches:
            print(f"- {path} contém texto proibido: {token}")
        return 1
    print("Gate portable passou.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
