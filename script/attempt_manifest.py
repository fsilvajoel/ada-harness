#!/usr/bin/env python3
"""Manifesto imutável por tentativa e elegibilidade de rerun seguro (Onda 4).

O manifesto registra tudo o que é preciso para reproduzir uma tentativa: request e SHA-256, hash da configuração
efetiva, papel, gate, `allowedPaths`, skills, modo, executor resolvido, cadeia de executores e base SHA. Segredos
nunca entram: só os *nomes* das variáveis de ambiente que os workers usam (`key_env`).

Um rerun só é permitido quando a etapa atual falhou, o manifesto mais recente está íntegro (hash bate com o evento),
config/request/base não mudaram, não há worktree ativa da etapa e nenhum outro rerun está em andamento.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Final

from script.events import append_event, read_events
from script.pipeline import STAGES, latest_commit_sha, replay

SCHEMA_VERSION: Final = 1
MANIFEST_NAME: Final = "attempt-manifest.json"
CONFIG_NAME: Final = "harness.project.yaml"
LOCK_NAME: Final = "rerun.lock"
RUN_ID_RE: Final = re.compile(r"[A-Za-z0-9._-]+")
FEATURE_RE: Final = re.compile(r"[A-Za-z0-9_-]+-F\d+")
REQUIRED_FIELDS: Final = frozenset({
    "schemaVersion", "run_id", "feature_id", "stage", "attempt", "role", "executor", "executor_chain", "mode",
    "gate", "allowed_paths", "skills", "request", "config", "base_sha", "secret_env", "created_at",
})


class ManifestError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_dir(root: Path, run_id: str, feature_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."} or not FEATURE_RE.fullmatch(feature_id):
        raise ManifestError("run_id ou feature_id inválido")
    return root / ".harness" / "runs" / run_id / feature_id


def manifest_path(root: Path, run_id: str, feature_id: str, stage: str, attempt: int) -> Path:
    if stage not in STAGES or attempt < 1:
        raise ManifestError("stage ou tentativa inválida")
    return run_dir(root, run_id, feature_id) / "evidence" / stage / str(attempt) / MANIFEST_NAME


def secret_env_names(config: dict[str, object], executors: list[str]) -> list[str]:
    """Nomes (nunca valores) das variáveis de segredo dos executores da cadeia."""
    workers = config.get("workers")
    names: set[str] = set()
    if isinstance(workers, dict):
        for executor in executors:
            worker = workers.get(executor)
            if isinstance(worker, dict) and isinstance(worker.get("key_env"), str):
                names.add(str(worker["key_env"]))
    return sorted(names)


def _request_ref(root: Path, request: Path) -> str:
    resolved = request.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def _resolve_request(root: Path, ref: str) -> Path:
    path = Path(ref)
    return path if path.is_absolute() else root / path


def write_attempt_manifest(
    root: Path,
    *,
    run_id: str,
    feature_id: str,
    stage: str,
    attempt: int,
    role: str,
    executor: str,
    executor_chain: list[str],
    mode: str,
    gate: str,
    allowed_paths: list[str],
    skills: list[str],
    request: Path,
    base_sha: str,
    config: dict[str, object],
) -> Path:
    root = root.resolve()
    path = manifest_path(root, run_id, feature_id, stage, attempt)
    payload: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "run_id": run_id,
        "feature_id": feature_id,
        "stage": stage,
        "attempt": attempt,
        "role": role,
        "executor": executor,
        "executor_chain": list(executor_chain),
        "mode": mode,
        "gate": gate,
        "allowed_paths": list(allowed_paths),
        "skills": list(skills),
        "request": {"path": _request_ref(root, request), "sha256": sha256_file(request)},
        "config": {"path": CONFIG_NAME, "sha256": sha256_file(root / CONFIG_NAME)},
        "base_sha": base_sha,
        "secret_env": secret_env_names(config, executor_chain),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with path.open("xb") as handle:  # imutável: nunca sobrescreve
            handle.write(data)
    except FileExistsError as exc:
        raise ManifestError(f"manifesto já existe: {path}") from exc
    append_event(run_dir(root, run_id, feature_id) / "events.jsonl", {
        "event": "attempt_manifest",
        "run_id": run_id,
        "feature_id": feature_id,
        "stage": stage,
        "attempt": attempt,
        "executor": executor,
        "manifest": path.relative_to(root).as_posix(),
        "manifest_sha256": hashlib.sha256(data).hexdigest(),
    })
    return path


def validate_manifest(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["manifesto precisa ser objeto"]
    errors: list[str] = []
    missing = REQUIRED_FIELDS - data.keys()
    extra = data.keys() - REQUIRED_FIELDS
    if missing:
        errors.append(f"campos ausentes: {sorted(missing)}")
    if extra:
        errors.append(f"campos desconhecidos: {sorted(extra)}")
    if data.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("schemaVersion incompatível")
    if data.get("stage") not in STAGES:
        errors.append("stage inválida")
    if type(data.get("attempt")) is not int or int(data.get("attempt", 0)) < 1:
        errors.append("attempt inválido")
    for key in ("role", "executor", "mode", "gate", "base_sha", "run_id", "feature_id"):
        if not isinstance(data.get(key), str) or not str(data.get(key)).strip():
            errors.append(f"{key} precisa ser texto")
    for key in ("executor_chain", "allowed_paths", "skills", "secret_env"):
        value = data.get(key)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            errors.append(f"{key} precisa ser lista de textos")
    for key in ("request", "config"):
        ref = data.get(key)
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", str(ref.get("sha256", ""))
        ):
            errors.append(f"{key} precisa ter path e sha256")
    return errors


def load_manifests(root: Path, run_id: str, feature_id: str) -> list[dict[str, object]]:
    """Resumo dos manifestos persistidos, com integridade conferida contra o event log."""
    base = run_dir(root, run_id, feature_id)
    events = read_events(base / "events.jsonl")
    expected = {
        str(event.get("manifest")): str(event.get("manifest_sha256"))
        for event in events if event.get("event") == "attempt_manifest"
    }
    summaries: list[dict[str, object]] = []
    for path in sorted((base / "evidence").glob(f"*/*/{MANIFEST_NAME}")):
        relative = path.relative_to(root).as_posix()
        digest = sha256_file(path)
        try:
            data: object = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        errors = validate_manifest(data)
        if expected.get(relative) != digest:
            errors.append("hash do manifesto não confere com o evento")
        summary: dict[str, object] = {"path": relative, "sha256": digest, "errors": errors}
        if isinstance(data, dict):
            for key in ("stage", "attempt", "executor", "role", "gate", "mode", "base_sha"):
                summary[key] = data.get(key)
        summaries.append(summary)
    summaries.sort(key=lambda item: (str(item.get("stage")), int(item.get("attempt") or 0)))
    return summaries


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str
    stage: str | None = None
    manifest: dict[str, object] | None = None


def _current_base(root: Path, events: list[dict[str, object]], run_id: str, feature_id: str) -> str:
    from script.worktree import preflight

    return latest_commit_sha(events, run_id, feature_id) or preflight(root)["base_sha"]


def active_stage_worktrees(root: Path, run_id: str, feature_id: str, stage: str) -> list[Path]:
    directory = root.parent / ".harness-worktrees" / root.name / run_id / feature_id / stage
    return sorted(path for path in directory.iterdir()) if directory.is_dir() else []


def rerun_eligibility(root: Path, run_id: str, feature_id: str, stage: str) -> Eligibility:
    root = root.resolve()
    try:
        base = run_dir(root, run_id, feature_id)
    except ManifestError as exc:
        return Eligibility(False, str(exc))
    events_path = base / "events.jsonl"
    if not events_path.is_file():
        return Eligibility(False, "execução inexistente")
    events = read_events(events_path)
    state = replay(run_id, feature_id, events)
    stages = state["stages"]
    assert isinstance(stages, dict)
    if stage not in STAGES:
        return Eligibility(False, "stage inválida")
    if state["current_stage"] != stage:
        return Eligibility(False, f"stage não é a atual ({state['current_stage']})", stage)
    status = stages.get(stage, {}).get("status") if isinstance(stages.get(stage), dict) else None
    if status != "failed":
        return Eligibility(False, f"stage não está falha ({status})", stage)
    manifest_events = [
        event for event in events if event.get("event") == "attempt_manifest" and event.get("stage") == stage
    ]
    if not manifest_events:
        return Eligibility(False, "sem manifesto persistido para a stage", stage)
    latest = manifest_events[-1]
    path = root / str(latest.get("manifest"))
    if not path.is_file():
        return Eligibility(False, "manifesto ausente", stage)
    if sha256_file(path) != latest.get("manifest_sha256"):
        return Eligibility(False, "manifesto adulterado (hash difere do evento)", stage)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return Eligibility(False, "manifesto ilegível", stage)
    errors = validate_manifest(manifest)
    if errors:
        return Eligibility(False, "manifesto inválido: " + "; ".join(errors), stage)
    if manifest["run_id"] != run_id or manifest["feature_id"] != feature_id or manifest["stage"] != stage:
        return Eligibility(False, "manifesto não pertence a esta execução", stage)
    config_path = root / CONFIG_NAME
    if not config_path.is_file() or sha256_file(config_path) != manifest["config"]["sha256"]:
        return Eligibility(False, "configuração mudou desde a tentativa", stage)
    request_path = _resolve_request(root, str(manifest["request"]["path"]))
    if not request_path.is_file() or sha256_file(request_path) != manifest["request"]["sha256"]:
        return Eligibility(False, "request mudou desde a tentativa", stage)
    try:
        current_base = _current_base(root, events, run_id, feature_id)
    except ValueError as exc:
        return Eligibility(False, f"base indisponível: {exc}", stage)
    if current_base != manifest["base_sha"]:
        return Eligibility(False, "base SHA mudou desde a tentativa", stage)
    if active_stage_worktrees(root, run_id, feature_id, stage):
        return Eligibility(False, "há worktree ativa para a stage", stage)
    if (base / LOCK_NAME).exists():
        return Eligibility(False, "rerun já em andamento", stage)
    return Eligibility(True, "ok", stage, manifest)


RerunRunner = Callable[..., object]


def rerun_stage(
    root: Path,
    run_id: str,
    feature_id: str,
    stage: str,
    *,
    runner: RerunRunner | None = None,
) -> dict[str, object]:
    """Reexecuta a stage com os parâmetros do manifesto; cria nova tentativa e preserva o histórico."""
    root = root.resolve()
    check = rerun_eligibility(root, run_id, feature_id, stage)
    if not check.eligible or check.manifest is None:
        raise ManifestError(check.reason)
    manifest = check.manifest
    lock = run_dir(root, run_id, feature_id) / LOCK_NAME
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ManifestError("rerun já em andamento") from exc
    try:
        os.write(fd, f"{os.getpid()}\n".encode())
        os.close(fd)
        if runner is None:
            from script.stage_runner import run_configured_stage

            runner = run_configured_stage
        result = runner(
            root,
            run_id=run_id,
            feature_id=feature_id,
            stage=stage,
            role=str(manifest["role"]),
            request=_resolve_request(root, str(manifest["request"]["path"])),
            gate_name=str(manifest["gate"]),
            allowed_paths=list(manifest["allowed_paths"]),
            skills=list(manifest["skills"]),
            mode=str(manifest["mode"]),
        )
    finally:
        lock.unlink(missing_ok=True)
    payload = dict(getattr(result, "__dict__", {}))
    payload["from_attempt"] = manifest["attempt"]
    return payload
