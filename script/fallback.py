"""Fallback configurável por stage e por tipo de falha."""

from __future__ import annotations

from typing import Final


FALLBACK_ERRORS: Final[frozenset[str]] = frozenset({
    "rejected",
    "gate-fail",
    "worker-error",
    "quota",
    "429",
    "no-key",
    "timeout",
})


def resolve_executor(
    primary: str,
    fallbacks: list[str],
    failure: str,
    attempt: int,
) -> str:
    if failure not in FALLBACK_ERRORS:
        raise ValueError(f"falha sem fallback definido: {failure}")
    if attempt < 1:
        raise ValueError("tentativa deve ser maior ou igual a 1")
    if attempt == 1:
        return primary
    fallback_index = attempt - 2
    if fallback_index < len(fallbacks):
        return fallbacks[fallback_index]
    raise ValueError(f"fallback esgotado após {attempt - 1} executor(es)")
