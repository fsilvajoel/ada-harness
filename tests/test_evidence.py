"""Testes dos manifestos de evidência."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.evidence import EvidenceError, sha256_file, validate_manifest, write_manifest


def test_manifest_records_output_hash(tmp_path: Path) -> None:
    output = tmp_path / "gate.txt"
    output.write_text("PASS\n", encoding="utf-8")
    manifest = write_manifest(
        tmp_path,
        gate="coverage",
        status="PASS",
        exit_code=0,
        command=["pnpm", "gate", "coverage", "E9-F02"],
        output_file=output,
    )
    assert manifest["status"] == "PASS"
    assert manifest["output_sha256"] == sha256_file(output)
    assert validate_manifest(tmp_path / "coverage.json") == []


def test_manifest_rejects_status_exit_code_mismatch(tmp_path: Path) -> None:
    output = tmp_path / "gate.txt"
    output.write_text("FAIL\n", encoding="utf-8")
    with pytest.raises(EvidenceError, match="não corresponde"):
        write_manifest(
            tmp_path,
            gate="coverage",
            status="PASS",
            exit_code=1,
            command=["pnpm", "gate"],
            output_file=output,
        )


def test_manifest_detects_tampered_output(tmp_path: Path) -> None:
    output = tmp_path / "gate.txt"
    output.write_text("PASS\n", encoding="utf-8")
    write_manifest(
        tmp_path,
        gate="coverage",
        status="PASS",
        exit_code=0,
        command=["pnpm", "gate"],
        output_file=output,
    )
    output.write_text("changed\n", encoding="utf-8")
    assert "output_sha256 não confere" in validate_manifest(tmp_path / "coverage.json")


def test_manifest_rejects_missing_output(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError, match="saída do gate ausente"):
        write_manifest(
            tmp_path,
            gate="coverage",
            status="PASS",
            exit_code=0,
            command=["pnpm", "gate", "coverage", "E9-F02"],
            output_file=tmp_path / "missing.txt",
        )
