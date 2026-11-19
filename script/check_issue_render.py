#!/usr/bin/env python3
"""Gate das issues renderizadas ou traduzidas: confere cada `.md` de uma pasta.

Falha (uma linha por achado) se o corpo passa de 65000 caracteres, se um link ou imagem relativa não resolve, se
sobrou prosa em inglês fora de código, IDs e blocos `<!--t:HASH-->...<!--/t-->` (heurística de palavras vazias do
inglês como `the`, `and`, `with`, `must`, `should`) ou, com `--translated`, se ainda há bloco `<!--t:HASH-->`
sem tradução.

A primeira linha `# título` não entra na checagem de inglês. Links relativos resolvem em relação ao arquivo ou à raiz (`--root`, padrão a própria pasta); links `http(s)`,
`mailto:` e âncoras `#...` não são conferidos.

Uso: `check_issue_render.py <pasta> [--root RAIZ] [--translated]`. Saída: 0 limpo, 1 achados, 2 erro de uso.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

MAX_BODY = 65000
CODE_RE = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
TBLOCK_RE = re.compile(r"<!--t:[0-9a-f]+-->.*?<!--/t-->", re.DOTALL)
OPEN_TBLOCK_RE = re.compile(r"<!--t:[0-9a-f]+-->")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
LINK_RE = re.compile(r"!?\[[^\]\n]*\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
URL_RE = re.compile(r"https?://\S+")
WORD_RE = re.compile(r"[A-Za-z']+")
ENGLISH_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "with", "must", "should", "will", "when", "which", "that", "this",
        "are", "not", "only", "each", "into", "without", "have", "has", "does", "your",
    }
)  # fmt: skip


def strip_code(text: str) -> str:
    """Remove código (cercas e crases) preservando as quebras de linha, para contar linhas depois."""
    return CODE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Linhas de prosa (número, texto): sem código, blocos de tradução, comentários, URLs e alvos de link."""

    def blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    clean = TBLOCK_RE.sub(blank, text)
    clean = COMMENT_RE.sub(blank, clean)
    clean = strip_code(clean)
    clean = LINK_RE.sub(lambda m: m.group(0).replace(m.group("target"), ""), clean)
    clean = URL_RE.sub(" ", clean)
    code_line = re.compile(r"^\s*(?:-\s+)?(?:import|export)\b.*(?:from\s+[\"'][^\"']+[\"']|[\"'][^\"']+[\"'];?)\s*$")
    return [
        (n, line) for n, line in enumerate(clean.splitlines(), 1) if line.strip() and not code_line.match(line)
    ]


def english_hits(line: str) -> list[str]:
    """Palavras vazias do inglês presentes na linha (sem repetir)."""
    found = {w.lower() for w in WORD_RE.findall(line)} & ENGLISH_STOPWORDS
    return sorted(found)


def broken_links(text: str, file: Path, root: Path) -> list[str]:
    """Alvos relativos (fora de código) que não existem em relação ao arquivo nem à raiz."""
    bad: list[str] = []
    for match in LINK_RE.finditer(strip_code(COMMENT_RE.sub("", text))):
        target = match.group("target")
        if target.startswith(("#", "mailto:", "/")) or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
            continue
        path = unquote(re.split(r"[?#]", target, maxsplit=1)[0])
        if path and not ((file.parent / path).exists() or (root / path).exists()):
            bad.append(target)
    return bad


def check_file(file: Path, root: Path, translated: bool) -> list[str]:
    """Achados de um arquivo, já com o nome na frente."""
    text = file.read_text(encoding="utf-8")
    name = file.name
    problems: list[str] = []
    if len(text) > MAX_BODY:
        problems.append(f"{name}: corpo com {len(text)} caracteres (limite {MAX_BODY})")
    problems += [f"{name}: link ou imagem relativa não resolve: {t}" for t in broken_links(text, file, root)]
    for number, line in prose_lines(text):
        if number == 1 and line.startswith("# "):
            continue  # título da issue (vem do título do cartão; a tradução dele fica fora deste gate)
        hits = english_hits(line)
        if hits:
            problems.append(f"{name}:{number}: inglês residual ({', '.join(hits)}): {line.strip()[:80]}")
    if translated:
        pending = len(OPEN_TBLOCK_RE.findall(text))
        if pending:
            problems.append(f"{name}: {pending} bloco(s) <!--t:--> sem tradução")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Confere as issues renderizadas ou traduzidas de uma pasta.")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--root", type=Path, default=None, help="raiz para resolver links relativos")
    parser.add_argument("--translated", action="store_true", help="exige que não sobre bloco <!--t:-->")
    args = parser.parse_args(argv)
    folder: Path = args.folder.resolve()
    if not folder.is_dir():
        print(f"check_issue_render: '{folder}' não é uma pasta", file=sys.stderr)
        return 2
    root: Path = (args.root or folder).resolve()
    files = sorted(folder.rglob("*.md"))
    problems: list[str] = []
    try:
        for file in files:
            problems += check_file(file, root, args.translated)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"check_issue_render: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("\n".join(problems))
        return 1
    print(f"ok ({len(files)} arquivo(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
