"""Onda 4: manifesto imutável por tentativa e rerun seguro pela UI (worker fake, Git temporário, sem rede)."""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.attempt_manifest import (
    LOCK_NAME,
    ManifestError,
    load_manifests,
    rerun_eligibility,
    rerun_stage,
)
from script.dashboard import RerunControl, main as dashboard_main, make_handler, status_payload
from script.events import read_events
from script.pipeline import replay
from script.stage_runner import run_configured_stage

RUN = "run-1"
FEATURE = "E1-F01"
SECRET_VALUE = "sk-valor-que-nunca-pode-vazar"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ADA_FAKE_KEY", SECRET_VALUE)
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "harness@example.invalid")
    git(repo, "config", "user.name", "Harness Test")
    worker = repo / "fake_worker.py"
    worker.write_text(
        "from pathlib import Path\nPath('src').mkdir(exist_ok=True)\n"
        "Path('src/out.txt').write_text('ok\\n', encoding='utf-8')\nprint('done')\n",
        encoding="utf-8",
    )
    gate = tmp_path / "gate.py"
    gate.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"raise SystemExit(0 if Path({str(tmp_path / 'gate-mode.txt')!r}).read_text().strip() == 'pass' else 1)\n",
        encoding="utf-8",
    )
    (tmp_path / "gate-mode.txt").write_text("fail", encoding="utf-8")
    (repo / "request.md").write_text("Planejar", encoding="utf-8")
    (repo / "harness.project.yaml").write_text(
        f"""cards_glob: docs/plano/E*.yaml
roles:
  planner: {{agent: planner.md, worker: fake}}
workers:
  fake:
    adapter: cli-agent
    command: {sys.executable}
    read_args: {worker}
    write_args: {worker}
    roles: planner
    timeout_s: 30
    key_env: ADA_FAKE_KEY
commands:
  verify: {sys.executable} {gate}
""",
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fixture")
    return repo


def first_run(repo: Path) -> bool:
    return run_configured_stage(
        repo, run_id=RUN, feature_id=FEATURE, stage="plan", role="planner",
        request=repo / "request.md", gate_name="verify", allowed_paths=["src/out.txt"],
        skills=["escrever-teste"],
    ).passed


def events(repo: Path) -> list[dict[str, object]]:
    return read_events(repo / ".harness" / "runs" / RUN / FEATURE / "events.jsonl")


def manifest_file(repo: Path, attempt: int) -> Path:
    return repo / ".harness" / "runs" / RUN / FEATURE / "evidence" / "plan" / str(attempt) / "attempt-manifest.json"


def pass_gate(repo: Path) -> None:
    (repo.parent / "gate-mode.txt").write_text("pass", encoding="utf-8")


def test_manifest_is_complete_hashed_and_secret_free(project: Path) -> None:
    assert first_run(project) is False
    path = manifest_file(project, 1)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 1
    assert data["role"] == "planner" and data["executor"] == "fake" and data["executor_chain"] == ["fake"]
    assert data["gate"] == "verify" and data["mode"] == "write" and data["skills"] == ["escrever-teste"]
    assert data["allowed_paths"] == ["src/out.txt"]
    assert data["request"]["path"] == "request.md" and len(data["request"]["sha256"]) == 64
    assert len(data["config"]["sha256"]) == 64
    assert data["base_sha"] == git(project, "rev-parse", "HEAD")
    assert data["secret_env"] == ["ADA_FAKE_KEY"]
    raw = path.read_text(encoding="utf-8") + json.dumps(events(project))
    assert SECRET_VALUE not in raw
    manifest_events = [event for event in events(project) if event["event"] == "attempt_manifest"]
    assert len(manifest_events) == 1 and len(str(manifest_events[0]["manifest_sha256"])) == 64
    assert load_manifests(project, RUN, FEATURE)[0]["errors"] == []


def test_valid_rerun_creates_new_attempt_and_preserves_history(project: Path) -> None:
    assert first_run(project) is False
    first = manifest_file(project, 1).read_bytes()
    check = rerun_eligibility(project, RUN, FEATURE, "plan")
    assert check.eligible, check.reason
    pass_gate(project)
    result = rerun_stage(project, RUN, FEATURE, "plan")
    assert result["passed"] is True and result["from_attempt"] == 1 and result["attempts"] == 2
    assert manifest_file(project, 1).read_bytes() == first
    assert manifest_file(project, 2).is_file()
    state = replay(RUN, FEATURE, events(project))
    assert state["current_stage"] == "refinement" and state["attempts"]["plan"] == 2
    assert not (project / ".harness" / "runs" / RUN / FEATURE / LOCK_NAME).exists()
    assert git(project, "branch", "--show-current") in {"master", "main"}


def test_changed_config_request_or_manifest_blocks_rerun_without_worker(project: Path) -> None:
    assert first_run(project) is False
    config = project / "harness.project.yaml"
    original = config.read_text(encoding="utf-8")
    config.write_text(original + "# mudou\n", encoding="utf-8")
    assert rerun_eligibility(project, RUN, FEATURE, "plan").reason == "configuração mudou desde a tentativa"
    config.write_text(original, encoding="utf-8")

    request = project / "request.md"
    request.write_text("outro pedido", encoding="utf-8")
    assert rerun_eligibility(project, RUN, FEATURE, "plan").reason == "request mudou desde a tentativa"
    request.write_text("Planejar", encoding="utf-8")

    manifest = manifest_file(project, 1)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["allowed_paths"] = ["**"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    check = rerun_eligibility(project, RUN, FEATURE, "plan")
    assert not check.eligible and "adulterado" in check.reason

    calls: list[object] = []
    with pytest.raises(ManifestError):
        rerun_stage(project, RUN, FEATURE, "plan", runner=lambda *a, **k: calls.append(k))
    assert calls == []


def test_base_change_and_wrong_stage_are_ineligible(project: Path) -> None:
    assert first_run(project) is False
    assert "não é a atual" in rerun_eligibility(project, RUN, FEATURE, "tests").reason
    (project / "extra.txt").write_text("x", encoding="utf-8")
    git(project, "add", "extra.txt")
    git(project, "commit", "-m", "novo base")
    assert rerun_eligibility(project, RUN, FEATURE, "plan").reason == "base SHA mudou desde a tentativa"


def test_passed_stage_is_ineligible(project: Path) -> None:
    pass_gate(project)
    assert first_run(project) is True
    check = rerun_eligibility(project, RUN, FEATURE, "plan")
    assert not check.eligible and "não é a atual" in check.reason


def test_active_worktree_and_lock_block_rerun(project: Path) -> None:
    assert first_run(project) is False
    stage_dir = project.parent / ".harness-worktrees" / project.name / RUN / FEATURE / "plan"
    (stage_dir / "9").mkdir(parents=True)
    assert rerun_eligibility(project, RUN, FEATURE, "plan").reason == "há worktree ativa para a stage"
    (stage_dir / "9").rmdir()
    lock = project / ".harness" / "runs" / RUN / FEATURE / LOCK_NAME
    lock.write_text("1\n", encoding="utf-8")
    assert rerun_eligibility(project, RUN, FEATURE, "plan").reason == "rerun já em andamento"


def test_missing_run_and_invalid_ids_are_ineligible(project: Path) -> None:
    assert rerun_eligibility(project, "nada", FEATURE, "plan").reason == "execução inexistente"
    assert not rerun_eligibility(project, "../x", FEATURE, "plan").eligible


@contextmanager
def serve(repo: Path, control: RerunControl | None) -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(repo, control))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def post(
    port: int,
    body: object,
    *,
    token: str | None,
    host: str | None = None,
    origin: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    raw = json.dumps(body).encode("utf-8")
    host_header = host or f"127.0.0.1:{port}"
    headers = {
        "Host": host_header,
        "Origin": origin or f"http://{host_header}",
        "Content-Type": content_type,
        "Content-Length": str(len(raw)),
    }
    if token is not None:
        headers["X-Ada-Rerun-Token"] = token
    connection.request("POST", "/api/rerun", body=raw, headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    return response.status, payload


def get(port: int, path: str) -> str:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    connection.request("GET", path)
    body = connection.getresponse().read().decode("utf-8")
    connection.close()
    return body


VALID = {"run_id": RUN, "feature_id": FEATURE, "stage": "plan"}


def test_rerun_is_disabled_by_default(project: Path) -> None:
    assert first_run(project) is False
    with serve(project, None) as port:
        status, payload = post(port, VALID, token="qualquer")
        assert status == 405 and "desabilitado" in str(payload["error"])
        assert "<meta name=\"ada-rerun-token\"" not in get(port, "/")
    assert not manifest_file(project, 2).exists()


def test_http_rejects_bad_token_origin_host_type_and_fields(project: Path) -> None:
    assert first_run(project) is False
    control = RerunControl(background=False)
    with serve(project, control) as port:
        assert post(port, VALID, token=None)[0] == 403
        assert post(port, VALID, token="errado")[0] == 403
        assert post(port, VALID, token=control.token, origin="http://evil.example")[0] == 403
        assert post(port, VALID, token=control.token, host="evil.example")[0] == 403
        assert post(port, VALID, token=control.token, content_type="text/plain")[0] == 400
        assert post(port, {**VALID, "command": "rm -rf /"}, token=control.token)[0] == 400
        assert post(port, {**VALID, "run_id": "../../etc"}, token=control.token)[0] == 400
        assert post(port, {**VALID, "run_id": "outro"}, token=control.token)[0] == 404
        assert post(port, {**VALID, "stage": "tests"}, token=control.token)[0] == 409
        assert f'content="{control.token}"' in get(port, "/")
    assert not manifest_file(project, 2).exists()


def test_http_valid_rerun_runs_from_persisted_state_after_restart(project: Path) -> None:
    assert first_run(project) is False
    # "Reinício": nenhum estado em memória; só o que está em disco.
    with serve(project, RerunControl(background=False)) as port:
        payload = json.loads(get(port, "/api/status"))
        run = payload["runs"][0]
        assert run["rerun"] == {"eligible": True, "reason": "ok", "stage": "plan"}
        assert run["manifests"][0]["attempt"] == 1
    pass_gate(project)
    control = RerunControl(background=False)
    with serve(project, control) as port:
        status, result = post(port, VALID, token=control.token)
        assert status == 200 and result["passed"] is True and result["attempts"] == 2
        status, _ = post(port, VALID, token=control.token)
        assert status == 409
    run = status_payload(project)["runs"][0]
    assert [item["attempt"] for item in run["manifests"]] == [1, 2]
    assert run["rerun"]["stage"] == "refinement" and run["rerun"]["eligible"] is False


def test_enable_rerun_refuses_non_loopback_bind(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert dashboard_main([str(tmp_path), "--host", "0.0.0.0", "--enable-rerun"]) == 2
    assert "loopback" in capsys.readouterr().err
