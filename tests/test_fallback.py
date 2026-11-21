"""Testes do fallback configurável."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.fallback import resolve_executor


def test_fallback_resolves_by_attempt() -> None:
    assert resolve_executor("bia", ["sonnet", "claude"], "gate-fail", 1) == "bia"
    assert resolve_executor("bia", ["sonnet", "claude"], "gate-fail", 2) == "sonnet"
    assert resolve_executor("bia", ["sonnet", "claude"], "gate-fail", 3) == "claude"


def test_exhausted_fallback_chain_is_explicit() -> None:
    with pytest.raises(ValueError, match="fallback esgotado"):
        resolve_executor("bia", ["sonnet"], "gate-fail", 3)


def test_unknown_failure_is_rejected() -> None:
    with pytest.raises(ValueError, match="sem fallback"):
        resolve_executor("bia", ["sonnet"], "product-rejection", 1)
