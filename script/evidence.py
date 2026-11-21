#!/usr/bin/env python3
"""Manifestos de evidência determinísticos para gates e stages."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


class EvidenceError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise EvidenceError(f"arquivo de evidência ausente: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_manifest(
    directory: Path,
    *,
    gate: str,
    status: str,
    exit_code: int,
    command: list[str],
    output_file: Path,
) -> dict[str, object]:
    if status not in {"PASS", "FAIL"}:
        raise EvidenceError("status da evidência precisa ser PASS ou FAIL")
    if type(exit_code) is not int or (status == "PASS") != (exit_code == 0):
        raise EvidenceError("status da evidência não corresponde ao exit code")
    if not isinstance(gate, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", gate) or gate in {".", ".."}:
        raise EvidenceError("gate inválido")
    if not isinstance(command, list) or not command or any(
        not isinstance(item, str) or not item.strip() for item in command
    ):
        raise EvidenceError("gate e command precisam ser não vazios")
    if not output_file.is_file():
        raise EvidenceError(f"saída do gate ausente: {output_file}")
    if directory.resolve() not in output_file.resolve().parents:
        raise EvidenceError("saída do gate precisa ficar na pasta da evidência")

    directory.mkdir(parents=True, exist_ok=True)
    output_sha = sha256_file(output_file)
    manifest: dict[str, object] = {
        "gate": gate,
        "status": status,
        "exit_code": exit_code,
        "command": command,
        "output": str(output_file.resolve()),
        "output_sha256": output_sha,
        "finished_at": utc_now(),
    }
    manifest_path = directory / f"{gate}.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    errors = validate_manifest(manifest_path)
    if errors:
        raise EvidenceError("; ".join(errors))
    return manifest


def validate_manifest(path: Path) -> list[str]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifesto ilegível: {exc}"]
    if not isinstance(manifest, dict):
        return ["manifesto precisa ser mapa"]

    errors: list[str] = []
    required = {
        "gate", "status", "exit_code", "command", "output", "output_sha256", "finished_at"
    }
    if required - manifest.keys():
        errors.append(f"campos ausentes: {', '.join(sorted(required - manifest.keys()))}")
    if manifest.keys() - required:
        errors.append(f"campos desconhecidos: {', '.join(sorted(manifest.keys() - required))}")
    gate = manifest.get("gate")
    status = manifest.get("status")
    exit_code = manifest.get("exit_code")
    command = manifest.get("command")
    output = manifest.get("output")
    output_sha = manifest.get("output_sha256")
    if not isinstance(gate, str) or not gate.strip():
        errors.append("gate ausente")
    if not isinstance(status, str) or status not in {"PASS", "FAIL"}:
        errors.append("status inválido")
    if type(exit_code) is not int:
        errors.append("exit_code inválido")
    elif isinstance(status, str) and status in {"PASS", "FAIL"} and (
        (status == "PASS") != (exit_code == 0)
    ):
        errors.append("status não corresponde ao exit_code")
    if not isinstance(command, list) or not command or any(
        not isinstance(item, str) or not item.strip() for item in command
    ):
        errors.append("command inválido")
    finished_at = manifest.get("finished_at")
    if not isinstance(finished_at, str):
        errors.append("finished_at inválido")
    else:
        try:
            datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append("finished_at inválido")
    if not isinstance(output, str) or not output.strip():
        errors.append("output ausente")
    else:
        output_path = Path(output).resolve()
        manifest_dir = path.resolve().parent
        if manifest_dir not in output_path.parents:
            errors.append("output fora da pasta do manifesto")
        elif not output_path.is_file():
            errors.append("output ausente")
        elif not isinstance(output_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", output_sha):
            errors.append("output_sha256 inválido")
        elif sha256_file(output_path) != output_sha:
            errors.append("output_sha256 não confere")
    return errors
