"""Reference visual tool for the visual-review gate (optional, not used by the runtime).

Fulfils the contract of ``visual_review.command``: takes ``--url --criterion --shot --report``, takes a
screenshot, looks for the expectation (visible text, or a selector when prefixed with ``css=``) and writes
``{"criterion", "found", "summary"}``. Engines:

- ``playwright`` (default): Python Playwright driving the system Chromium (``ADA_CHROMIUM``).
- ``node-playwright``: Playwright of the target project, resolved from ``--project-dir`` through node.
- ``chromium-cli``: ``chromium --headless --screenshot --dump-dom`` (no Playwright needed).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

NODE_SCRIPT = """
const { createRequire } = require('module');
const req = createRequire(process.argv[1] + '/package.json');
let pw;
try { pw = req('playwright'); } catch (e) { pw = req('@playwright/test'); }
const { chromium } = pw;
(async () => {
  const [, , url, shot, expect, exe] = process.argv;
  const browser = await chromium.launch({ executablePath: exe || undefined, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.goto(url, { waitUntil: 'load' });
  await page.screenshot({ path: shot });
  const found = expect.startsWith('css=')
    ? (await page.locator(expect.slice(4)).count()) > 0
    : (await page.getByText(expect).count()) > 0;
  await browser.close();
  process.stdout.write(JSON.stringify({ found }));
})().catch((e) => { console.error(String(e)); process.exit(2); });
"""


def chromium_path() -> str:
    configured = os.environ.get("ADA_CHROMIUM")
    if configured:
        return configured
    return shutil.which("chromium") or shutil.which("chromium-browser") or "/usr/bin/chromium"


def check_playwright(url: str, shot: Path, expect: str) -> bool:
    from playwright.sync_api import sync_playwright  # optional dependency, imported lazily

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium_path(), args=["--no-sandbox"])
        try:
            page = browser.new_page()
            page.goto(url, wait_until="load")
            page.screenshot(path=str(shot))
            if expect.startswith("css="):
                return page.locator(expect[4:]).count() > 0
            return page.get_by_text(expect).count() > 0
        finally:
            browser.close()


def check_node_playwright(url: str, shot: Path, expect: str, project_dir: Path) -> bool:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node não encontrado")
    done = subprocess.run(
        [node, "-e", NODE_SCRIPT, str(project_dir.resolve()), url, str(shot), expect, chromium_path()],
        cwd=project_dir, capture_output=True, text=True, timeout=120, check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"node saiu com {done.returncode}")
    found = json.loads(done.stdout)["found"]
    return found is True


def check_chromium_cli(url: str, shot: Path, expect: str) -> bool:
    if expect.startswith("css="):
        raise RuntimeError("chromium-cli não suporta seletor css=; use texto ou outro engine")
    done = subprocess.run(
        [chromium_path(), "--headless", "--no-sandbox", "--disable-gpu", f"--screenshot={shot}", "--dump-dom", url],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip()[-300:] or f"chromium saiu com {done.returncode}")
    return expect in done.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", "--target", dest="url", required=True)
    parser.add_argument("--criterion", required=True)
    parser.add_argument("--shot", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expect", help="texto visível ou css=<seletor>; padrão: o id do critério")
    parser.add_argument("--engine", choices=["playwright", "node-playwright", "chromium-cli"], default="playwright")
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    expect: str = args.expect or args.criterion
    try:
        if args.engine == "playwright":
            found = check_playwright(args.url, args.shot, expect)
        elif args.engine == "node-playwright":
            found = check_node_playwright(args.url, args.shot, expect, args.project_dir)
        else:
            found = check_chromium_cli(args.url, args.shot, expect)
    except Exception as exc:  # noqa: BLE001 - any engine failure must fail the tool visibly
        print(f"visual_check_playwright: {exc}", file=sys.stderr)
        return 2
    summary = (
        f"'{expect}' encontrado na página ({args.engine})" if found else f"'{expect}' não encontrado ({args.engine})"
    )
    args.report.write_text(
        json.dumps({"criterion": args.criterion, "found": found, "summary": summary}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
