#!/usr/bin/env python3
"""Dashboard local do pipeline Ada Harness 2.0."""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import re
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Final

ROOT: Final = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.attempt_manifest import ManifestError, load_manifests, rerun_eligibility, rerun_stage
from script.events import read_events
from script.metrics import summarize_events
from script.pipeline import replay
from script.project_config import load_config
from script.status import compute
from script.visual_review import load_evidence_summaries


class DashboardError(ValueError):
    pass


def _git(project_root: Path, *args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd or project_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise DashboardError((result.stderr or result.stdout).strip())
    return result.stdout


def _worktree_snapshots(
    project_root: Path,
    run_id: str,
    feature_id: str,
    events: list[dict[str, object]],
) -> list[dict[str, object]]:
    project_root = project_root.resolve()
    if not (project_root / ".git").exists():
        return []
    listing = _git(project_root, "worktree", "list", "--porcelain")
    worktree_root = (
        project_root.parent / ".harness-worktrees" / project_root.name
    ).resolve()
    snapshots: list[dict[str, object]] = []
    for block in listing.strip().split("\n\n"):
        fields = dict(
            line.split(" ", 1) for line in block.splitlines() if " " in line
        )
        raw_path = fields.get("worktree")
        if not raw_path:
            continue
        path = Path(raw_path)
        try:
            relative = path.relative_to(worktree_root)
        except ValueError:
            continue
        parts = relative.parts
        if len(parts) != 4 or parts[0] != run_id or parts[1] != feature_id:
            continue
        _, _, stage, attempt_text = parts
        if not attempt_text.isdecimal():
            continue
        head = fields.get("HEAD", "")
        stage_event_index = next(
            (
                index for index in range(len(events) - 1, -1, -1)
                if events[index].get("event") == "stage_passed"
                and events[index].get("stage") == stage
                and events[index].get("attempt") == int(attempt_text)
                and events[index].get("commit_sha") == head
            ),
            None,
        )
        changed_paths: set[str] = set()
        diff_stats: list[str] = []
        if stage_event_index is not None:
            previous_commit = next(
                (
                    str(event["commit_sha"])
                    for event in reversed(events[:stage_event_index])
                    if event.get("event") == "stage_passed"
                    and isinstance(event.get("commit_sha"), str)
                ),
                None,
            )
            previous_commit = previous_commit or _git(
                project_root, "rev-parse", "HEAD"
            ).strip()
            if previous_commit != head:
                changed_paths.update(
                    line for line in _git(path, "diff", "--name-only", previous_commit, head).splitlines()
                    if line
                )
                diff_stats.append(_git(path, "diff", "--stat", previous_commit, head).strip())
        else:
            changed_paths.update(
                line for line in _git(path, "diff", "--name-only", "HEAD").splitlines() if line
            )
            current_diff = _git(path, "diff", "--stat", "HEAD").strip()
            if current_diff:
                diff_stats.append(current_diff)
        changed_paths.update(
            line for line in _git(path, "diff", "--name-only", "HEAD").splitlines() if line
        )
        unstaged_diff = _git(path, "diff", "--stat", "HEAD").strip()
        if unstaged_diff and unstaged_diff not in diff_stats:
            diff_stats.append(unstaged_diff)
        untracked = [
            line for line in _git(path, "ls-files", "--others", "--exclude-standard").splitlines()
            if line
        ]
        changed_paths.update(untracked)
        if untracked:
            diff_stats.append(f"{len(untracked)} untracked file(s)")
        snapshots.append({
            "stage": stage,
            "attempt": int(attempt_text),
            "branch": fields.get("branch", "").removeprefix("refs/heads/"),
            "head_sha": head,
            "files": sorted(changed_paths),
            "diff_stat": "\n".join(filter(None, diff_stats)),
        })
    return snapshots


def _engine_rates(project_root: Path) -> dict[str, dict[str, float]]:
    config = load_config(project_root)
    workers = config.get("workers")
    if not isinstance(workers, dict):
        return {}
    rates: dict[str, dict[str, float]] = {}
    for name, worker in workers.items():
        if not isinstance(name, str) or not isinstance(worker, dict):
            continue
        worker_rates = worker.get("rates")
        if not isinstance(worker_rates, dict):
            continue
        prompt = worker_rates.get("prompt_per_1k")
        completion = worker_rates.get("completion_per_1k")
        if (
            type(prompt) in {int, float}
            and type(completion) in {int, float}
        ):
            rates[name] = {
                "prompt_per_1k": float(prompt),
                "completion_per_1k": float(completion),
            }
    return rates


def load_events(project_root: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    runs = project_root / ".harness" / "runs"
    if not runs.exists():
        return events
    for path in sorted(runs.glob("*/*/events.jsonl")):
        for event in read_events(path):
            events.append(event)
    events.sort(key=lambda item: str(item.get("timestamp", "")))
    return events


def status_payload(project_root: Path) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    rates = _engine_rates(project_root)
    runs_root = project_root / ".harness" / "runs"
    if runs_root.exists():
        for path in sorted(runs_root.glob("*/*/events.jsonl")):
            run_id = path.parent.parent.name
            feature_id = path.parent.name
            events = read_events(path)
            state = replay(run_id, feature_id, events)
            current = state.get("current_stage")
            eligibility = (
                rerun_eligibility(project_root, run_id, feature_id, current)
                if isinstance(current, str) else None
            )
            runs.append({
                "run_id": run_id,
                "feature_id": feature_id,
                "state": state,
                "metrics": summarize_events(events, engine_rates=rates),
                "events": events[-40:],
                "worktrees": _worktree_snapshots(project_root, run_id, feature_id, events),
                "visual_reviews": load_evidence_summaries(path.parent / "evidence"),
                "manifests": load_manifests(project_root, run_id, feature_id),
                "rerun": {
                    "eligible": bool(eligibility and eligibility.eligible),
                    "reason": eligibility.reason if eligibility else "execução concluída",
                    "stage": current,
                },
            })
    return {
        "pipeline": compute(project_root),
        "events": load_events(project_root),
        "runs": runs,
    }


PAGE: Final = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Ada Harness 2.0</title>
  <!--RERUN_META-->
  <style>
    body { margin: 0; font-family: system-ui, sans-serif; background: #f6f7f9; color: #172033; }
    header { padding: 20px 24px; background: #172033; color: white; }
    main { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; padding: 16px; }
    section { background: white; border: 1px solid #dde2ea; border-radius: 10px; padding: 16px; }
    h1, h2 { margin: 0 0 12px; }
    .feature { border-bottom: 1px solid #edf0f5; padding: 10px 0; }
    .feature strong { display: inline-block; min-width: 100px; }
    .done { color: #16794a; } .blocked { color: #b42318; } .ready { color: #175cd3; }
    .run { border-bottom: 1px solid #edf0f5; padding: 10px 0; }
    pre { max-height: 70vh; overflow: auto; margin: 0; font-size: 12px; }
  </style>
</head>
<body>
  <header><h1>Ada Harness 2.0</h1><div id="stamp">aguardando dados</div></header>
  <main>
    <section>
      <h2>Features</h2>
      <div id="features"></div>
      <h2>Execuções</h2>
      <div id="runs"></div>
      <h2>Fila</h2>
      <pre id="queue"></pre>
    </section>
    <section>
      <h2>Eventos recentes</h2>
      <pre id="events"></pre>
    </section>
  </main>
  <script>
    async function refresh() {
      const response = await fetch('/api/status');
      const data = await response.json();
      const pipeline = data.pipeline;
      document.getElementById('stamp').textContent =
        'Atualizado em ' + (pipeline.generated_at || '-');
      const features = document.getElementById('features');
      features.replaceChildren(...(pipeline.features || []).map(row => {
        const css = row.blocked ? 'blocked' : row.step === 'done' ? 'done' : 'ready';
        const element = document.createElement('div');
        element.className = `feature ${css}`;
        const id = document.createElement('strong');
        id.textContent = row.id;
        const detail = document.createElement('span');
        detail.textContent = ` ${row.step} · ${row.percent}%`;
        element.append(id, detail);
        return element;
      }));
      const runs = document.getElementById('runs');
      runs.replaceChildren(...(data.runs || []).map(run => {
        const element = document.createElement('div');
        element.className = 'run';
        const summary = document.createElement('strong');
        summary.textContent = `${run.feature_id} · ${run.state.current_stage || 'concluído'}`;
        const detail = document.createElement('div');
        const latest = (run.events || []).filter(event => event.event === 'stage_started').at(-1);
        const cost = run.metrics.estimated_cost === null
          ? 'não precificado'
          : `${run.metrics.estimated_cost.toFixed(4)} (configurado)`;
        detail.textContent =
          `engine: ${latest?.executor || '-'} · tentativas: ${Object.values(run.metrics.stage_attempts || {}).reduce((a, b) => a + b, 0)}` +
          ` · fallbacks: ${run.metrics.fallbacks} · tokens: ${run.metrics.total_tokens} · custo: ${cost}`;
        element.append(summary, detail);
        const evidence = (run.events || []).filter(event => event.event === 'gate_evidence').at(-1);
        if (evidence) {
          const proof = document.createElement('div');
          proof.textContent = `evidência: ${evidence.gate} ${evidence.status} · SHA ${evidence.output_sha256}`;
          element.append(proof);
        }
        for (const worktree of run.worktrees || []) {
          const details = document.createElement('div');
          const files = worktree.files.length ? worktree.files.join(', ') : 'sem diff';
          details.textContent =
            `worktree ${worktree.stage} #${worktree.attempt} · ${worktree.branch} · ${files}` +
            (worktree.diff_stat ? ` · ${worktree.diff_stat.replace(/\\n/g, '; ')}` : '');
          element.append(details);
        }
        for (const manifest of run.manifests || []) {
          const line = document.createElement('div');
          const bad = manifest.errors.length ? ` · INVÁLIDO: ${manifest.errors.join('; ')}` : '';
          line.textContent = `manifesto ${manifest.stage} #${manifest.attempt} · ${manifest.executor} · base ${String(manifest.base_sha || '').slice(0, 12)} · SHA ${manifest.sha256.slice(0, 16)}${bad}`;
          element.append(line);
        }
        const token = document.querySelector('meta[name="ada-rerun-token"]')?.content;
        if (run.rerun) {
          const rerun = document.createElement('div');
          rerun.textContent = `rerun ${run.rerun.stage || '-'}: ${run.rerun.eligible ? 'elegível' : run.rerun.reason}`;
          if (token && run.rerun.eligible) {
            const button = document.createElement('button');
            button.textContent = 'Reexecutar etapa';
            button.onclick = async () => {
              button.disabled = true;
              const response = await fetch('/api/rerun', {
                method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-Ada-Rerun-Token': token},
                body: JSON.stringify({run_id: run.run_id, feature_id: run.feature_id, stage: run.rerun.stage}),
              });
              rerun.append(` → ${response.status}`);
            };
            rerun.append(' ', button);
          }
          element.append(rerun);
        }
        for (const review of run.visual_reviews || []) {
          const visual = document.createElement('div');
          const invalid = review.validation_errors.length ? ` · INVÁLIDA: ${review.validation_errors.join('; ')}` : '';
          visual.textContent =
            `revisão visual ${review.stage} #${review.attempt}: ${review.status} · ${review.summary || ''}` +
            review.criteria.map(item => ` · ${item.criterion} ${item.status} ${item.screenshot_sha256 || ''}`).join('') +
            invalid;
          element.append(visual);
        }
        return element;
      }));
      document.getElementById('queue').textContent =
        JSON.stringify(pipeline.queue || {}, null, 2);
      document.getElementById('events').textContent =
        JSON.stringify((data.events || []).slice(-40).reverse(), null, 2);
    }
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


RERUN_FIELDS: Final = frozenset({"run_id", "feature_id", "stage"})
RERUN_FIELD_RE: Final = {
    "run_id": re.compile(r"[A-Za-z0-9._-]{1,128}"),
    "feature_id": re.compile(r"[A-Za-z0-9_-]+-F\d+"),
    "stage": re.compile(r"[A-Za-z0-9_]{1,32}"),
}
MAX_BODY: Final = 4096
LOOPBACK_NAMES: Final = frozenset({"127.0.0.1", "localhost", "[::1]"})


@dataclass
class RerunControl:
    """Rerun pela UI: desabilitado por padrão; só existe quando o operador liga `--enable-rerun`."""

    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    runner: Callable[..., object] | None = None
    background: bool = True
    threads: list[threading.Thread] = field(default_factory=list)


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def make_handler(project_root: Path, rerun: RerunControl | None = None) -> type[BaseHTTPRequestHandler]:
    page = PAGE.replace(
        "<!--RERUN_META-->",
        f'<meta name="ada-rerun-token" content="{rerun.token}">' if rerun is not None else "",
    )

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status: int, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                body = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/status":
                try:
                    self.send_json(200, status_payload(project_root))
                except (ValueError, FileNotFoundError, TypeError) as exc:
                    self.send_json(500, {"error": str(exc)})
                return
            self.send_json(404, {"error": "not found"})

        def _rerun_request(self) -> tuple[int, dict[str, object]]:
            if rerun is None:
                return 405, {"error": "rerun desabilitado (somente leitura)"}
            port = self.server.server_address[1]
            allowed_hosts = {f"{name}:{port}" for name in LOOPBACK_NAMES}
            host = self.headers.get("Host", "")
            if host not in allowed_hosts:
                return 403, {"error": "Host não permitido"}
            if self.headers.get("Origin") != f"http://{host}":
                return 403, {"error": "Origin não permitido"}
            token = self.headers.get("X-Ada-Rerun-Token", "")
            if not hmac.compare_digest(token.encode(), rerun.token.encode()):
                return 403, {"error": "token inválido"}
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return 400, {"error": "Content-Type precisa ser application/json"}
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return 400, {"error": "Content-Length obrigatório"}
            if length < 1 or length > MAX_BODY:
                return 400, {"error": "corpo vazio ou grande demais"}
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return 400, {"error": "JSON inválido"}
            if not isinstance(body, dict) or set(body) != RERUN_FIELDS or any(
                not isinstance(body[key], str) or not RERUN_FIELD_RE[key].fullmatch(body[key])
                for key in RERUN_FIELDS
            ):
                return 400, {"error": "corpo precisa ter exatamente run_id, feature_id e stage válidos"}
            run_id, feature_id, stage = str(body["run_id"]), str(body["feature_id"]), str(body["stage"])
            if not (project_root / ".harness" / "runs" / run_id / feature_id / "events.jsonl").is_file():
                return 404, {"error": "execução inexistente"}
            check = rerun_eligibility(project_root, run_id, feature_id, stage)
            if not check.eligible:
                return 409, {"error": check.reason}

            def execute() -> dict[str, object]:
                return rerun_stage(project_root, run_id, feature_id, stage, runner=rerun.runner)

            if rerun.background:
                thread = threading.Thread(target=lambda: _swallow(execute), daemon=True)
                rerun.threads.append(thread)
                thread.start()
                return 202, {"accepted": True, "stage": stage}
            try:
                return 200, execute()
            except ManifestError as exc:
                return 409, {"error": str(exc)}

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/rerun":
                self.send_json(404, {"error": "not found"})
                return
            status, payload = self._rerun_request()
            self.send_json(status, payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def _swallow(action: Callable[[], object]) -> None:
    try:
        action()
    except (ManifestError, ValueError, OSError):
        # O resultado fica no event log; a UI acompanha por polling.
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dashboard local Ada Harness 2.0.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--enable-rerun",
        action="store_true",
        help="habilita POST /api/rerun (só em loopback, com token por sessão); padrão: somente leitura",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.enable_rerun and not is_loopback_host(args.host):
        print("dashboard: --enable-rerun só é permitido em host loopback", file=sys.stderr)
        return 2

    rerun = RerunControl() if args.enable_rerun else None
    server = ThreadingHTTPServer((args.host, args.port), make_handler(root, rerun))
    print(f"dashboard: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
