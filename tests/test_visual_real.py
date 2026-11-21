"""Integration of visual_review with a real browser (optional; ADA_REQUIRE_OPTIONAL=1 makes skips fail)."""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from script.visual_review import PNG_SIGNATURE, run_visual_review, validate_visual_evidence
from tests.test_visual_review import visual_plan

pytestmark = pytest.mark.visual_real

TOOL = Path(__file__).parent / "fixtures" / "visual_check_playwright.py"
PROJECT_WEB = Path(os.environ.get("ADA_PROJECT_WEB", Path.home() / "code/pilla/apps/web"))
HTML = "<!doctype html><html><body><h1 id='title'>Painel de resumo</h1><p>Saldo atual</p></body></html>"


def optional_missing(reason: str) -> None:
    if os.environ.get("ADA_REQUIRE_OPTIONAL") == "1":
        pytest.fail(f"dependência opcional ausente com ADA_REQUIRE_OPTIONAL=1: {reason}")
    pytest.skip(reason)


def chromium() -> str:
    path = os.environ.get("ADA_CHROMIUM") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not path:
        optional_missing("chromium não encontrado")
    return str(path)


@pytest.fixture()
def site(tmp_path: Path) -> Iterator[str]:
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(HTML, encoding="utf-8")
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    finally:
        server.shutdown()
        server.server_close()


def run(tmp_path: Path, url: str, engine: str, expect: str, extra: str = ""):
    config = {
        "visual_review": {
            "command": (
                f"{sys.executable} {TOOL} --engine {engine} --url {{target}} --criterion {{criterion}} "
                f"--shot {{screenshot}} --report {{report}} --expect '{expect}' {extra}"
            ),
            "target": url,
            "timeout_s": 120,
        }
    }
    return run_visual_review(visual_plan(), config, feature_id="E1-F01", cwd=tmp_path, evidence_dir=tmp_path / "ev")


def engine_ready(engine: str) -> None:
    chromium()
    if engine == "playwright":
        try:
            import playwright  # noqa: F401
        except ImportError:
            optional_missing("playwright (python) não instalado")
    elif engine == "node-playwright":
        if shutil.which("node") is None or not any(
            (PROJECT_WEB / "node_modules" / name).exists() for name in ("playwright", "@playwright/test")
        ):
            optional_missing("node ou playwright do projeto (ADA_PROJECT_WEB) ausente")


ENGINES = ["playwright", "node-playwright", "chromium-cli"]


@pytest.mark.parametrize("engine", ENGINES)
def test_real_browser_pass(tmp_path: Path, site: str, engine: str) -> None:
    engine_ready(engine)
    extra = f"--project-dir {PROJECT_WEB}" if engine == "node-playwright" else ""
    result = run(tmp_path, site, engine, "Saldo atual", extra)
    assert result.status == "PASS", result
    assert result.evidence is not None
    assert validate_visual_evidence(result.evidence) == []
    data = json.loads(result.evidence.read_text(encoding="utf-8"))
    entry = data["criteria"][0]
    assert len(entry["screenshot_sha256"]) == 64
    shot = result.evidence.parent / entry["screenshot"]
    assert shot.read_bytes().startswith(PNG_SIGNATURE) and shot.stat().st_size > 100


@pytest.mark.parametrize("engine", ENGINES)
def test_real_browser_fail_when_criterion_absent(tmp_path: Path, site: str, engine: str) -> None:
    engine_ready(engine)
    extra = f"--project-dir {PROJECT_WEB}" if engine == "node-playwright" else ""
    result = run(tmp_path, site, engine, "Texto inexistente", extra)
    assert result.status == "FAIL"


def test_real_browser_selector(tmp_path: Path, site: str) -> None:
    engine_ready("playwright")
    assert run(tmp_path, site, "playwright", "css=#title").status == "PASS"
    assert run(tmp_path, site, "playwright", "css=#nao-existe").status == "FAIL"
