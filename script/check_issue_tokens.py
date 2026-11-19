#!/usr/bin/env python3
"""Compara os tokens protegidos de um texto original com a sua tradução.

Tokens protegidos: trechos entre crases, IDs (E7-F01, RN-C04, ADR-0016, D1), números e caminhos de arquivo.
A tradução não pode perder nem inventar nenhum deles (comparação como multiconjunto).
Uso: `check_issue_tokens.py ORIGINAL.md TRADUZIDO.md`. Saída 0 = ok, 1 = diferenças, 2 = erro de uso.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

CODE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
ID = re.compile(r"\b[A-Z]{1,5}-?[0-9]+(?:-[A-Z][0-9]+)*\b")
NUMBER = re.compile(r"(?<!\w)[0-9]+(?:[.,][0-9]+)*(?!\w)")
PATH = re.compile(r"[\w.@-]+(?:/[\w.@-]+)+|[\w-]+\.(?:ts|tsx|md|py|yaml|yml|json|png|jpg|svg|mts|mjs)\b")


def protected_tokens(text: str) -> Counter[str]:
    """Extrai os tokens protegidos de um texto."""
    tokens: Counter[str] = Counter()
    rest = text
    for match in CODE.finditer(text):
        tokens[f"código:{match.group(0)}"] += 1
    rest = CODE.sub(" ", rest)
    for kind, pattern in (("caminho", PATH), ("id", ID), ("número", NUMBER)):
        for match in pattern.finditer(rest):
            tokens[f"{kind}:{match.group(0)}"] += 1
        rest = pattern.sub(" ", rest)
    return tokens


def diff_tokens(original: str, translated: str) -> list[str]:
    """Devolve as linhas 'faltando'/'sobrando' entre original e tradução."""
    a, b = protected_tokens(original), protected_tokens(translated)
    lines = [f"faltando: {t}" for t in sorted((a - b).elements())]
    lines += [f"sobrando: {t}" for t in sorted((b - a).elements())]
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        original = Path(argv[1]).read_text(encoding="utf-8")
        translated = Path(argv[2]).read_text(encoding="utf-8")
    except OSError as error:
        print(f"erro: {error}", file=sys.stderr)
        return 2
    problems = diff_tokens(original, translated)
    print("\n".join(problems) if problems else "ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
