#!/usr/bin/env python3
"""Traduz os blocos `<!--t:HASH-->texto<!--/t-->` de uma issue renderizada para pt-BR, com cache por hash.

Lê o `issue.md` gerado por `render_issue.py`. Cada bloco é procurado no cache JSON (`issue.cache.json`, hash ->
tradução, ao lado do `issue.md`); os que faltam vão para um modelo via HTTP Ollama (`/api/chat`). Cada tradução passa
por `check_issue_tokens.diff_tokens` (código, IDs, caminhos e números intactos): se reprovar, pede de novo, até 2
novas tentativas; depois disso o comando falha e não grava nada. Com tudo aprovado grava `issue.pt-br.md` (blocos
trocados pela tradução) e atualiza o cache.

Sem `--ollama-url` (nem `OLLAMA_URL`) só reporta quantos blocos faltam. O modelo vem de `--model` ou `OLLAMA_MODEL`.

Uso: `translate_blocks.py <issue.md> [--ollama-url URL] [--model NOME] [--cache arq.json] [--out arq.md]`.
Saída: 0 ok (ou só relatório), 1 tradução reprovada ou modelo inacessível, 2 erro de uso.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.check_issue_tokens import diff_tokens

type Ask = Callable[[str], str]

PROMPT = (
    "Traduza para português do Brasil técnico. Preserve código, IDs, nomes de arquivo e números. "
    "Devolva só o texto."
)
MAX_RETRIES = 2
BLOCK_RE = re.compile(r"<!--t:(?P<hash>[0-9a-f]+)-->(?P<text>.*?)<!--/t-->", re.DOTALL)
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class TranslateError(Exception):
    """Tradução reprovada ou modelo inacessível."""


def find_blocks(text: str) -> dict[str, str]:
    """Blocos do texto: hash -> original."""
    return {m.group("hash"): m.group("text") for m in BLOCK_RE.finditer(text)}


def load_cache(path: Path) -> dict[str, str]:
    """Cache hash -> tradução (vazio se o arquivo não existe)."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: o cache deve ser um objeto JSON")
    return {str(k): str(v) for k, v in data.items()}


def ollama_ask(url: str, model: str, timeout: float = 300.0) -> Ask:
    """Função que envia o prompt fixo + texto ao Ollama e devolve a resposta."""

    def ask(text: str) -> str:
        payload = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": f"{PROMPT}\n\n{text}"}],
        }
        request = urllib.request.Request(
            url.rstrip("/") + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise TranslateError(f"modelo inacessível: {exc}") from exc
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise TranslateError("resposta do modelo sem message.content")
        return content

    return ask


def translate_one(original: str, ask: Ask) -> str:
    """Traduz um bloco; reprova e repete até `MAX_RETRIES` vezes se os tokens protegidos divergirem."""
    problems: list[str] = []
    for _ in range(MAX_RETRIES + 1):
        answer = THINK_RE.sub("", ask(original)).strip()
        problems = diff_tokens(original, answer) if answer else ["tradução vazia"]
        if not problems:
            return answer
    raise TranslateError(f"tradução reprovada após {MAX_RETRIES + 1} tentativas: {'; '.join(problems)}")


def apply_translations(text: str, translations: dict[str, str]) -> str:
    """Troca cada bloco pela tradução (blocos sem tradução ficam intactos)."""
    return BLOCK_RE.sub(lambda m: translations.get(m.group("hash"), m.group(0)), text)


def run(issue: Path, cache_path: Path, out: Path, ask: Ask | None) -> tuple[int, str]:
    """Executa a tradução; devolve (saída, mensagem). Só grava arquivos se tudo for aprovado."""
    text = issue.read_text(encoding="utf-8")
    blocks = find_blocks(text)
    cache = load_cache(cache_path)
    missing = {h: t for h, t in blocks.items() if h not in cache}
    if missing and ask is None:
        return 0, f"{len(missing)} de {len(blocks)} bloco(s) sem tradução no cache; informe --ollama-url para traduzir"
    new = dict(cache)
    try:
        for digest, original in missing.items():
            assert ask is not None
            new[digest] = translate_one(original, ask)
    except TranslateError as exc:
        return 1, f"translate_blocks: {exc}; nada foi gravado"
    out.write_text(apply_translations(text, new), encoding="utf-8")
    cache_path.write_text(json.dumps(new, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0, f"{len(blocks)} bloco(s): {len(missing)} traduzido(s), {len(blocks) - len(missing)} do cache -> {out}"


def main(argv: list[str] | None = None, ask: Ask | None = None) -> int:
    parser = argparse.ArgumentParser(description="Traduz os blocos <!--t:--> de uma issue renderizada.")
    parser.add_argument("issue", type=Path)
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL"))
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL"))
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    issue: Path = args.issue
    if not issue.is_file():
        print(f"translate_blocks: '{issue}' não existe", file=sys.stderr)
        return 2
    if ask is None and args.ollama_url:
        if not args.model:
            print("translate_blocks: informe --model ou OLLAMA_MODEL", file=sys.stderr)
            return 2
        ask = ollama_ask(args.ollama_url, args.model)
    cache_path: Path = args.cache or issue.parent / "issue.cache.json"
    out: Path = args.out or issue.parent / "issue.pt-br.md"
    try:
        code, message = run(issue, cache_path, out, ask)
    except (OSError, ValueError) as exc:
        print(f"translate_blocks: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
