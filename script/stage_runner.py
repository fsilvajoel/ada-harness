#!/usr/bin/env python3
"""Executa uma stage com worker, gate, tentativas e evidências."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable

from script.events import EventError, append_event, read_events
from script.evidence import write_manifest
from script.fallback import FALLBACK_ERRORS, resolve_executor
from script.pipeline import (
    STAGES,
    TransitionError,
    finish_stage,
    latest_commit_sha,
    replay,
    skip_stage,
    start_stage,
)
from script.project_config import get_str, load_config
from script.worktree import (
    WorktreeError,
    commit_attempt,
    create_attempt_worktree,
    preflight as worktree_preflight,
    remove_attempt_worktree,
)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str
    usage: dict[str, int] | None = None
    failure_type: str | None = None

    def __post_init__(self) -> None:
        engine_result: dict[str, object] = {
            "exit_code": self.exit_code,
            "output": self.output,
        }
        if self.usage is not None:
            engine_result["usage"] = self.usage
        _validate_engine_result(engine_result)
        if self.failure_type is not None and (
            not isinstance(self.failure_type, str) or not self.failure_type.strip()
        ):
            raise ValueError("failure_type precisa ser texto não vazio")
        if self.failure_type is not None and self.exit_code == 0:
            raise ValueError("result com failure_type não pode ter exit_code 0")


def _validate_engine_result(result: dict[str, object]) -> None:
    if result.keys() - {"exit_code", "output", "usage"}:
        raise ValueError("engine result contém campos desconhecidos")
    if type(result.get("exit_code")) is not int:
        raise ValueError("exit_code precisa ser inteiro")
    if not isinstance(result.get("output"), str):
        raise ValueError("output precisa ser texto")
    usage = result.get("usage")
    if "usage" in result and (
        not isinstance(usage, dict)
        or any(
            not isinstance(key, str) or type(value) is not int or value < 0
            for key, value in usage.items()
        )
    ):
        raise ValueError("usage precisa ser mapa de contadores inteiros não negativos")


@dataclass(frozen=True)
class StageResult:
    passed: bool
    executor: str
    attempts: int
    reason: str | None = None
    commit_sha: str | None = None
    failure_type: str | None = None


Worker = Callable[[str, Path], CommandResult]
Gate = Callable[[Path], CommandResult]
Preflight = Callable[[Path], list[str]]
Worktree = Callable[[int], Path]
CleanupWorktree = Callable[[Path], None]
CommitWorktree = Callable[[Path, int], str]
RecordAttempt = Callable[[int, str, Path], None]


class StageRunnerError(ValueError):
    pass


def _safe_scope_path(value: str) -> bool:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return False
    path = Path(value)
    windows_path = PureWindowsPath(value)
    return (
        not path.is_absolute()
        and not windows_path.is_absolute()
        and not windows_path.drive
        and ".." not in path.parts
        and value not in {".", "./"}
    )


def _record_result(
    event_path: Path,
    *,
    run_id: str,
    feature_id: str,
    stage: str,
    executor: str,
    attempt: int,
    failure: str | None,
    usage: dict[str, int] | None,
) -> None:
    event: dict[str, object] = {
        "event": "attempt_result",
        "run_id": run_id,
        "feature_id": feature_id,
        "stage": stage,
        "executor": executor,
        "attempt": attempt,
        "failure_type": failure,
    }
    if usage is not None:
        event["usage"] = usage
    append_event(event_path, event)


def _save_evidence(
    evidence_root: Path,
    *,
    gate: str,
    result: CommandResult,
    command: list[str],
    attempt: int,
) -> dict[str, object]:
    directory = evidence_root / str(attempt)
    directory.mkdir(parents=True, exist_ok=True)
    output_file = directory / f"{gate}.log"
    output_file.write_text(result.output, encoding="utf-8")
    return write_manifest(
        directory,
        gate=gate,
        status="PASS" if result.exit_code == 0 and result.failure_type is None else "FAIL",
        exit_code=result.exit_code,
        command=command,
        output_file=output_file,
    )


def _save_engine_result(
    evidence_root: Path,
    *,
    stage: str,
    attempt: int,
    result: CommandResult,
) -> None:
    payload: dict[str, object] = {
        "exit_code": result.exit_code,
        "output": result.output,
    }
    if result.usage is not None:
        payload["usage"] = result.usage
    _validate_engine_result(payload)
    path = evidence_root / stage / str(attempt) / "engine-result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_evidence(
    event_path: Path,
    *,
    run_id: str,
    feature_id: str,
    stage: str,
    executor: str,
    attempt: int,
    manifest: dict[str, object],
) -> None:
    append_event(event_path, {
        "event": "gate_evidence",
        "run_id": run_id,
        "feature_id": feature_id,
        "stage": stage,
        "executor": executor,
        "attempt": attempt,
        "gate": manifest["gate"],
        "status": manifest["status"],
        "output": manifest["output"],
        "output_sha256": manifest["output_sha256"],
    })


def run_stage(
    event_path: Path,
    evidence_root: Path,
    *,
    run_id: str,
    feature_id: str,
    stage: str,
    gate_name: str,
    primary_executor: str,
    fallbacks: list[str],
    max_attempts: int,
    worktree_for_attempt: Worktree,
    run_worker: Worker,
    run_gate: Gate,
    preflight: Preflight | None = None,
    gate_policy: dict[str, bool] | None = None,
    cleanup_attempt: CleanupWorktree | None = None,
    commit_success: CommitWorktree | None = None,
    record_attempt: RecordAttempt | None = None,
) -> StageResult:
    if stage not in STAGES:
        raise ValueError(f"stage inválida: {stage}")
    if gate_policy is not None and any(type(value) is not bool for value in gate_policy.values()):
        raise ValueError("gate_policy precisa mapear stages para booleanos")
    if gate_policy is not None and gate_policy.get(stage, True) is False:
        skip_stage(
            event_path,
            run_id,
            feature_id,
            stage,
            "nenhum gate aplicável para esta classificação",
        )
        return StageResult(True, "skipped", 0, "no-applicable-gate")
    if max_attempts < 1:
        raise ValueError("max_attempts precisa ser positivo")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", gate_name) or gate_name in {".", ".."}:
        raise ValueError("gate_name precisa ser um identificador simples")
    if not primary_executor.strip() or any(not item.strip() for item in fallbacks):
        raise ValueError("executor primário e fallbacks precisam ser nomes não vazios")
    if len(set([primary_executor, *fallbacks])) != len(fallbacks) + 1:
        raise ValueError("executor repetido na cadeia de fallback")
    if max_attempts > 1 + len(fallbacks):
        raise ValueError("max_attempts excede o número de executores configurados")
    previous_executor: str | None = None
    previous_failure: str | None = None
    attempt_limit = max_attempts
    # Numeração global por stage: um rerun nunca sobrescreve evidência de tentativa anterior.
    prior_state = replay(run_id, feature_id, read_events(event_path))
    prior_attempts = prior_state["attempts"]
    attempt_offset = int(prior_attempts.get(stage, 0)) if isinstance(prior_attempts, dict) else 0
    for chain_index in range(1, attempt_limit + 1):
        attempt = attempt_offset + chain_index
        executor = resolve_executor(
            primary_executor,
            fallbacks,
            previous_failure or "worker-error",
            chain_index,
        )
        worktree = worktree_for_attempt(attempt)
        if not isinstance(worktree, Path):
            raise TypeError("worktree_for_attempt precisa retornar Path")
        if chain_index > 1:
            append_event(event_path, {
                "event": "fallback",
                "run_id": run_id,
                "feature_id": feature_id,
                "stage": stage,
                "from_executor": previous_executor or primary_executor,
                "executor": executor,
                "attempt": attempt,
                "failure_type": previous_failure or "worker-error",
            })

        start_stage(
            event_path,
            run_id,
            feature_id,
            stage,
            executor=executor,
            attempt=attempt,
        )
        if record_attempt is not None:
            record_attempt(attempt, executor, worktree)
        if preflight is not None:
            preflight_errors = preflight(worktree)
            if not isinstance(preflight_errors, list) or any(
                not isinstance(message, str) for message in preflight_errors
            ):
                raise TypeError("preflight precisa retornar lista de mensagens")
            if preflight_errors:
                preflight_result = CommandResult(1, "\n".join(preflight_errors) + "\n")
                manifest = _save_evidence(
                    evidence_root / stage,
                    gate="artifact-lint",
                    result=preflight_result,
                    command=["artifact-lint", feature_id],
                    attempt=attempt,
                )
                _record_evidence(
                    event_path,
                    run_id=run_id,
                    feature_id=feature_id,
                    stage=stage,
                    executor=executor,
                    attempt=attempt,
                    manifest=manifest,
                )
                _record_result(
                    event_path,
                    run_id=run_id,
                    feature_id=feature_id,
                    stage=stage,
                    executor=executor,
                    attempt=attempt,
                    failure="preflight-fail",
                    usage=None,
                )
                finish_stage(
                    event_path,
                    run_id,
                    feature_id,
                    stage,
                    passed=False,
                    gate="artifact-lint",
                    reason="preflight-fail",
                    executor=executor,
                    attempt=attempt,
                )
                if cleanup_attempt is not None:
                    cleanup_attempt(worktree)
                return StageResult(False, executor, attempt, "preflight-fail")
        worker_result = run_worker(executor, worktree)
        if not isinstance(worker_result, CommandResult):
            raise TypeError("run_worker precisa retornar CommandResult")
        _save_engine_result(
            evidence_root,
            stage=stage,
            attempt=attempt,
            result=worker_result,
        )
        manifest = _save_evidence(
            evidence_root / stage,
            gate="worker",
            result=worker_result,
            command=[executor, stage],
            attempt=attempt,
        )
        _record_evidence(
            event_path,
            run_id=run_id,
            feature_id=feature_id,
            stage=stage,
            executor=executor,
            attempt=attempt,
            manifest=manifest,
        )
        failure: str | None = None
        gate_result: CommandResult | None = None
        if worker_result.exit_code != 0:
            failure = worker_result.failure_type or "worker-error"
        elif worker_result.failure_type:
            failure = worker_result.failure_type
        else:
            gate_result = run_gate(worktree)
            if not isinstance(gate_result, CommandResult):
                raise TypeError("run_gate precisa retornar CommandResult")
            manifest = _save_evidence(
                evidence_root / stage,
                gate=gate_name,
                result=gate_result,
                command=[gate_name, feature_id],
                attempt=attempt,
            )
            _record_evidence(
                event_path,
                run_id=run_id,
                feature_id=feature_id,
                stage=stage,
                executor=executor,
                attempt=attempt,
                manifest=manifest,
            )
            if gate_result.exit_code != 0:
                failure = gate_result.failure_type or "gate-fail"
            elif gate_result.failure_type:
                failure = gate_result.failure_type

        _record_result(
            event_path,
            run_id=run_id,
            feature_id=feature_id,
            stage=stage,
            executor=executor,
            attempt=attempt,
            failure=failure,
            usage=worker_result.usage,
        )
        if failure is None:
            commit_sha: str | None = None
            if commit_success is not None:
                try:
                    commit_sha = commit_success(worktree, attempt)
                except WorktreeError as exc:
                    finish_stage(
                        event_path,
                        run_id,
                        feature_id,
                        stage,
                        passed=False,
                        reason=f"commit-error: {exc}",
                        executor=executor,
                        attempt=attempt,
                    )
                    if cleanup_attempt is not None:
                        cleanup_attempt(worktree)
                    return StageResult(False, executor, attempt, f"commit-error: {exc}")
                if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
                    raise ValueError("commit_success precisa retornar SHA de commit")
            finish_stage(
                event_path,
                run_id,
                feature_id,
                stage,
                passed=True,
                gate=gate_name,
                executor=executor,
                attempt=attempt,
                commit_sha=commit_sha,
            )
            return StageResult(True, executor, attempt, commit_sha=commit_sha)

        finish_stage(
            event_path,
            run_id,
            feature_id,
            stage,
            passed=False,
            gate=gate_name if gate_result is not None else None,
            reason=failure,
            executor=executor,
            attempt=attempt,
        )
        if cleanup_attempt is not None:
            cleanup_attempt(worktree)
        if failure not in FALLBACK_ERRORS:
            return StageResult(False, executor, attempt, failure, failure)
        previous_executor = executor
        previous_failure = failure

    return StageResult(
        False,
        previous_executor or primary_executor,
        attempt_offset + attempt_limit,
        previous_failure,
        previous_failure,
    )


def _config_map(config: dict[str, object], *keys: str) -> dict[str, object]:
    node: object = config
    for key in keys:
        if not isinstance(node, dict):
            return {}
        node = node.get(key)
    return node if isinstance(node, dict) else {}


def _worker_usage(
    meta_path: Path,
    *,
    exit_code: int,
) -> tuple[dict[str, int] | None, str | None, str | None]:
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, "invalid-result", f"worker metadata inválida: {exc}"
    if not isinstance(meta, dict):
        return None, "invalid-result", "worker metadata precisa ser mapa"
    if type(meta.get("exit_code")) is not int or meta["exit_code"] != exit_code:
        return None, "invalid-result", "worker metadata exit_code não corresponde ao processo"
    status = meta.get("status")
    if not isinstance(status, str) or not status.strip():
        return None, "invalid-result", "worker metadata status ausente"
    if (status == "ok") != (exit_code == 0):
        return None, "invalid-result", "worker metadata status não corresponde ao exit_code"

    raw_usage = meta.get("usage")
    usage: dict[str, int] = {}
    if isinstance(raw_usage, str):
        try:
            raw_usage = json.loads(raw_usage)
        except json.JSONDecodeError:
            return None, "invalid-result", "worker metadata usage não é JSON válido"
    if raw_usage is not None and not isinstance(raw_usage, dict):
        return None, "invalid-result", "worker metadata usage precisa ser mapa"
    if isinstance(raw_usage, dict) and any(
        not isinstance(key, str) or type(value) is not int or value < 0
        for key, value in raw_usage.items()
    ):
        return None, "invalid-result", "worker metadata usage contém contador inválido"
    if isinstance(raw_usage, dict):
        for target, names in (
            ("prompt_tokens", ("prompt_tokens", "promptTokens", "input_tokens")),
            ("completion_tokens", ("completion_tokens", "completionTokens", "output_tokens")),
        ):
            value = next((raw_usage.get(name) for name in names if name in raw_usage), None)
            if value is not None:
                usage[target] = value
    failure = {
        "config": "configuration",
        "no-key": "no-key",
        "quota": "quota",
        "rate-limit": "429",
        "failed": "worker-error",
        "no-command": "configuration",
    }.get(status)
    if status not in {"ok", "config", "no-key", "quota", "rate-limit", "failed", "no-command"}:
        return None, "invalid-result", f"worker metadata status desconhecido: {status}"
    error = meta.get("error")
    if error is not None and not isinstance(error, str):
        return None, "invalid-result", "worker metadata error precisa ser texto ou null"
    return usage or None, failure, error if isinstance(error, str) else None


def run_command(command: list[str], *, cwd: Path, timeout_s: int) -> CommandResult:
    if not command or timeout_s < 1:
        raise ValueError("command não vazio e timeout positivo são obrigatórios")
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**os.environ, "ADA_HARNESS_DIR": str(Path(__file__).resolve().parents[1])},
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        return CommandResult(124, stdout + stderr, failure_type="timeout")
    return CommandResult(result.returncode, result.stdout + result.stderr)


def run_configured_stage(
    project_root: Path,
    *,
    run_id: str,
    feature_id: str,
    stage: str,
    role: str,
    request: Path,
    gate_name: str,
    allowed_paths: list[str],
    skills: list[str] | None = None,
    mode: str = "write",
) -> StageResult:
    root = project_root.resolve()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id) or not re.fullmatch(
        r"[A-Za-z0-9_-]+-F\d+", feature_id
    ):
        raise StageRunnerError("run_id ou feature_id inválido")
    if not request.is_file():
        raise StageRunnerError(f"request ausente: {request}")
    if not allowed_paths or any(not _safe_scope_path(path) for path in allowed_paths):
        raise StageRunnerError("allowed_paths precisa conter paths relativos e seguros")
    try:
        config = load_config(root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        raise StageRunnerError(f"harness.project.yaml inválido: {exc}") from exc

    role_config = _config_map(config, "roles", role)
    primary = role_config.get("worker")
    fallbacks = role_config.get("fallbacks", [])
    if not isinstance(primary, str) or not primary.strip():
        raise StageRunnerError(f"roles.{role}.worker ausente")
    if not isinstance(fallbacks, list) or any(
        not isinstance(item, str) or not item.strip() for item in fallbacks
    ):
        raise StageRunnerError(f"roles.{role}.fallbacks precisa ser lista de textos")
    executor_chain = [primary, *fallbacks]
    if len(set(executor_chain)) != len(executor_chain):
        raise StageRunnerError(f"roles.{role} tem executores repetidos")

    commands = _config_map(config, "commands")
    raw_gate_command = commands.get(gate_name)
    if not isinstance(raw_gate_command, str) or not raw_gate_command.strip():
        raise StageRunnerError(f"commands.{gate_name} ausente")
    gate_command = shlex.split(raw_gate_command)
    if not gate_command:
        raise StageRunnerError(f"commands.{gate_name} vazio")

    workers = _config_map(config, "workers")
    event_path = root / ".harness" / "runs" / run_id / feature_id / "events.jsonl"
    evidence_root = root / ".harness" / "runs" / run_id / feature_id / "evidence"
    events = read_events(event_path)
    base_sha = latest_commit_sha(events, run_id, feature_id) or worktree_preflight(root)["base_sha"]

    def make_worktree(attempt: int) -> Path:
        path, _ = create_attempt_worktree(root, run_id, feature_id, stage, attempt, base_sha)
        return path

    def run_worker(executor: str, worktree: Path) -> CommandResult:
        worker_config = workers.get(executor)
        timeout_s = worker_config.get("timeout_s", 1800) if isinstance(worker_config, dict) else 1800
        if type(timeout_s) is not int or timeout_s < 1:
            raise StageRunnerError(f"workers.{executor}.timeout_s inválido")
        attempt = int(worktree.name)
        meta_path = evidence_root / stage / str(attempt) / "engine-meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(Path(__file__).with_name("dispatch.py")),
            str(root),
            "--role",
            role,
            "--worker",
            executor,
            "--mode",
            mode,
            "--stage",
            stage,
            "--request",
            str(request.resolve()),
            "--cwd",
            str(worktree),
            "--meta",
            str(meta_path),
        ]
        for skill in skills or []:
            command.extend(["--skills", skill])
        for allowed_path in allowed_paths:
            command.extend(["--allowed-path", allowed_path])
        result = run_command(command, cwd=root, timeout_s=timeout_s)
        if result.failure_type == "timeout":
            return result
        if meta_path.exists():
            usage, failure, diagnostic = _worker_usage(meta_path, exit_code=result.exit_code)
            if failure == "invalid-result":
                output = result.output + (f"\n{diagnostic}" if diagnostic else "")
                return CommandResult(1, output, failure_type=failure)
            output = result.output
            if diagnostic and failure is not None:
                output += f"\n{diagnostic}"
            return CommandResult(result.exit_code, output, usage, failure)
        if result.exit_code == 0:
            return CommandResult(
                1,
                f"worker não escreveu metadata: {meta_path}",
                failure_type="invalid-result",
            )
        failure = "configuration" if result.exit_code in {2, 8} else "invalid-result"
        return CommandResult(result.exit_code, result.output, failure_type=failure)

    def run_gate(worktree: Path) -> CommandResult:
        gate_config = _config_map(config, "gates", gate_name)
        timeout_s = gate_config.get("command_timeout_s", 600)
        if type(timeout_s) is not int or timeout_s < 1:
            raise StageRunnerError(f"gates.{gate_name}.command_timeout_s inválido")
        command = [
            part.replace("{feature}", feature_id).replace("{worktree}", str(worktree))
            for part in gate_command
        ]
        result = run_command(command, cwd=worktree, timeout_s=timeout_s)
        if result.failure_type == "timeout":
            return CommandResult(result.exit_code, result.output, failure_type="gate-timeout")
        return result

    docs_dir = get_str(config, "docs_dir") or "docs"
    docs_path = Path(docs_dir)
    if docs_path.is_absolute() or docs_path.drive or "\\" in docs_dir or ".." in docs_path.parts:
        raise StageRunnerError("docs_dir precisa ser relativo e seguro")
    plan_path = root / docs_path / "plano" / feature_id / "plan.json"
    if not plan_path.is_file():
        relative_plan = plan_path.relative_to(root).as_posix()
        stored_plan = subprocess.run(
            ["git", "-C", str(root), "show", f"{base_sha}:{relative_plan}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if stored_plan.returncode == 0:
            plan_text = stored_plan.stdout
        else:
            plan_text = ""
    else:
        plan_text = plan_path.read_text(encoding="utf-8")
    gate_policy: dict[str, bool] | None = None
    plan_data: dict[str, object] | None = None
    if plan_text:
        try:
            plan = json.loads(plan_text)
        except json.JSONDecodeError as exc:
            raise StageRunnerError(f"plano de feature inválido: {exc}") from exc
        if isinstance(plan, dict):
            from script.plan_lint import lint_feature_plan
            from script.testing_policy import resolve_feature_gates, stage_policy

            plan_data = plan
            if not lint_feature_plan(plan):
                gate_policy = stage_policy(resolve_feature_gates(plan))

    stage_gate = run_gate
    if stage == "finalReview" and plan_data is not None:
        from script.visual_review import run_visual_review, visual_required

        if visual_required(plan_data):
            visual_plan = plan_data

            def gate_with_visual_review(worktree: Path) -> CommandResult:
                result = run_gate(worktree)
                if result.exit_code != 0:
                    return result
                visual_dir = evidence_root / stage / worktree.name / "visual-review"
                visual = run_visual_review(
                    visual_plan,
                    config,
                    feature_id=feature_id,
                    cwd=worktree,
                    evidence_dir=visual_dir,
                )
                lines = [f"visual-review: {visual.status}", *(f"visual-review: {e}" for e in visual.errors)]
                output = result.output + "\n" + "\n".join(lines)
                if not visual.passed:
                    return CommandResult(1, output, result.usage, failure_type="visual-review-fail")
                return CommandResult(0, output, result.usage)

            stage_gate = gate_with_visual_review

    from script.artifact_lint import lint_project
    from script.attempt_manifest import write_attempt_manifest

    def record_attempt(attempt: int, executor: str, _worktree: Path) -> None:
        write_attempt_manifest(
            root,
            run_id=run_id,
            feature_id=feature_id,
            stage=stage,
            attempt=attempt,
            role=role,
            executor=executor,
            executor_chain=executor_chain,
            mode=mode,
            gate=gate_name,
            allowed_paths=allowed_paths,
            skills=list(skills or []),
            request=request,
            base_sha=base_sha,
            config=config,
        )

    preflight = (
        (lambda worktree: lint_project(worktree, strict=True))
        if stage not in {"plan", "refinement"}
        else None
    )
    return run_stage(
        event_path,
        evidence_root,
        run_id=run_id,
        feature_id=feature_id,
        stage=stage,
        gate_name=gate_name,
        primary_executor=primary,
        fallbacks=list(fallbacks),
        max_attempts=len(executor_chain),
        worktree_for_attempt=make_worktree,
        run_worker=run_worker,
        run_gate=stage_gate,
        preflight=preflight,
        gate_policy=gate_policy,
        cleanup_attempt=lambda path: remove_attempt_worktree(
            root,
            path,
            force=True,
            delete_branch=True,
        ),
        commit_success=lambda path, attempt: commit_attempt(
            path,
            allowed_paths=allowed_paths,
            feature_id=feature_id,
            stage=stage,
            attempt=attempt,
        ),
        record_attempt=record_attempt,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Executa uma stage com worker, gate e worktree isolada.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--run", required=True)
    parser.add_argument("--feature", required=True)
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--gate", required=True)
    parser.add_argument("--allowed-path", action="append", required=True)
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument("--mode", choices=["read", "write"], default="write")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = run_configured_stage(
            args.project_root,
            run_id=args.run,
            feature_id=args.feature,
            stage=args.stage,
            role=args.role,
            request=args.request,
            gate_name=args.gate,
            allowed_paths=args.allowed_path,
            skills=args.skill,
            mode=args.mode,
        )
    except (EventError, StageRunnerError, TransitionError, ValueError, OSError, WorktreeError) as exc:
        print(f"stage-runner: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.__dict__, ensure_ascii=False))
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
