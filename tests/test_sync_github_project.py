"""Testes do sincronizador GitHub Projects (`script/sync_github_project.py`), com `gh` simulado."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from script.sync_github_project import REFRESH_COMMAND, desired_issues, leaks, main
from script.render_issue import render_selected

STATUS: dict[str, object] = {
    "steps": [{"id": "plan", "label": "plan"}, {"id": "implement", "label": "implement"}, {"id": "done", "label": "done"}],
    "features": [
        {"id": "X-F01", "title": "First", "delivery": "X", "step": "implement", "percent": 50, "blocked": {"status": "quota"}},
        {"id": "X-F02", "title": "Second", "delivery": "X", "step": "done", "percent": 100, "blocked": None},
    ],
    "cards": [{"id": "X-T01", "feature": "X-F01", "state": "blocked"}],
}


CARDS = """\
delivery: X
cards:
  - id: X-T01
    title: Write the first thing
    kind: implement
    assignee: bia
    refs: []
    dependsOn: []
    allowedPaths: [src/a.ts]
    done: [it works]
  - id: X-T02
    title: Write the second thing
    kind: implement
    assignee: bia
    refs: []
    dependsOn: []
    allowedPaths: [src/b.ts]
    done: [it also works]
features:
  - {id: X-F01, title: First, cards: [X-T01]}
  - {id: X-F02, title: Second, cards: [X-T02]}
"""

DEFECT = """\
# D{n}: Defeito {n}

| Campo | Valor |
|---|---|
| Severidade | baixa |
| Status | {status} |

## Reprodução
- obtido: errado
"""


class FakeGh:
    def __init__(
        self,
        scopes: str = "'repo', 'project'",
        issues: list[dict[str, object]] | None = None,
        labels: list[str] | None = None,
    ) -> None:
        self.scopes = scopes
        self.issues = issues or []
        self.labels = labels or []
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], stdin: str | None = None) -> str:
        self.calls.append(args)
        head = " ".join(args[:2])
        if head == "auth status":
            return f"  - Token scopes: {self.scopes}\n"
        if head == "issue list":
            return json.dumps(self.issues)
        if head == "label list":
            return json.dumps([{"name": n} for n in self.labels])
        if head == "project create":
            return json.dumps({"number": 7})
        if head == "project view":
            return json.dumps({"id": "PID"})
        if head == "project field-list":
            opts = [{"id": f"o-{n}", "name": n} for n in ("plan", "implement", "done")]
            return json.dumps({"fields": [{"id": "FID", "name": "Status", "options": opts}]})
        if head == "issue create":
            return "https://github.com/o/r/issues/" + str(len(self.calls)) + "\n"
        if head == "project item-add":
            return json.dumps({"id": "ITEM"})
        return ""

    def verbs(self) -> list[str]:
        return [" ".join(c[:2]) for c in self.calls]


@pytest.fixture
def status_file(tmp_path: Path) -> Path:
    (tmp_path / "harness.project.yaml").write_text("cards_glob: docs/plano/X*.yaml\n", encoding="utf-8")
    plano = tmp_path / "docs" / "plano"
    plano.mkdir(parents=True)
    (plano / "X.yaml").write_text(CARDS, encoding="utf-8")
    defects = tmp_path / "docs" / "defeitos"
    defects.mkdir()
    (defects / "D1.md").write_text(DEFECT.format(n=1, status="fechado"), encoding="utf-8")
    (defects / "D2.md").write_text(DEFECT.format(n=2, status="aberto"), encoding="utf-8")
    path = tmp_path / "status.json"
    path.write_text(json.dumps(STATUS), encoding="utf-8")
    return path


def test_leaks_detects_ip_path_and_secret() -> None:
    assert leaks("host 10.0.0.5") and leaks("/ho" + "me/x/y") and leaks("see .env") and leaks("ghp_abcdefghijklmnop")
    assert leaks("plain text 1.5") == []


def test_issue_body_is_rendered_with_marker_labels_and_defects(status_file: Path) -> None:
    root = status_file.parent
    issues = desired_issues(STATUS, render_selected(root, everything=True), root / "docs" / "plano")
    by_id = {str(i["feature"]): i for i in issues}
    assert by_id["X-F01"]["title"] == "[X-F01] First"
    assert str(by_id["X-F01"]["body"]).startswith("<!-- ada-feature:X-F01 -->")
    assert "## " in str(by_id["X-F01"]["body"]) and by_id["X-F01"]["labels"] == ["entrega:X"]
    assert by_id["X-F01"]["blocked"] is True and by_id["X-F02"]["done"] is True
    assert by_id["D1"]["kind"] == "defeito" and by_id["D1"]["labels"] == ["bug"]
    assert str(by_id["D1"]["body"]).startswith("<!-- ada-defeito:D1 -->")
    assert by_id["D1"]["done"] is True and by_id["D2"]["done"] is False


def test_translated_file_replaces_the_body(status_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = status_file.parent / "docs" / "plano" / "X-F01"
    folder.mkdir()
    (folder / "issue.pt-br.md").write_text("# [X-F01] Primeiro\n\nTexto traduzido.\n", encoding="utf-8")
    gh = FakeGh(scopes="'repo'")
    assert main([str(status_file.parent), "--repo", "o/r"], run=gh) == 0
    root = status_file.parent
    issues = desired_issues(STATUS, render_selected(root, everything=True), root / "docs" / "plano")
    body = str(issues[0]["body"])
    assert body.startswith("<!-- ada-feature:X-F01 -->\nTexto traduzido.") and "# [X-F01]" not in body


def test_dry_run_writes_nothing(status_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh(scopes="'repo'")
    rc = main([str(status_file.parent), "--repo", "o/r"], run=gh)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [(a["kind"], a["feature"], a["issue"]) for a in out["actions"]] == [
        ("feature", "X-F01", "create"),
        ("feature", "X-F02", "create"),
        ("defeito", "D1", "create"),
        ("defeito", "D2", "create"),
    ]
    assert out["labels_to_create"] == ["bug", "entrega:X"]
    assert [a["close"] for a in out["actions"]] == [False, True, True, False]
    assert set(gh.verbs()) <= {"auth status", "issue list", "label list"}


def test_apply_without_project_scope_prints_command_and_exits_3(
    status_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(scopes="'repo'")
    rc = main([str(status_file.parent), "--repo", "o/r", "--apply", "--create-project"], run=gh)
    assert rc == 3
    assert REFRESH_COMMAND in capsys.readouterr().err
    assert not any(call[0] in {"issue", "project", "label"} and call[1] != "list" for call in gh.calls)


def test_leaking_text_stops_before_any_call(status_file: Path) -> None:
    cards = status_file.parent / "docs" / "plano" / "X.yaml"
    cards.write_text(cards.read_text().replace("title: First", "title: Server 192.168.1.10"), encoding="utf-8")
    gh = FakeGh()
    assert main([str(status_file.parent), "--repo", "o/r", "--apply", "--create-project"], run=gh) == 4
    assert gh.calls == []


def test_broken_link_exits_1_without_calls(status_file: Path) -> None:
    (status_file.parent / "docs" / "defeitos" / "D2.md").write_text(
        DEFECT.format(n=2, status="aberto") + "\nVeja [x](nao-existe.md)\n", encoding="utf-8"
    )
    gh = FakeGh()
    assert main([str(status_file.parent), "--repo", "o/r", "--apply", "--create-project"], run=gh) == 1
    assert gh.calls == []


def test_apply_creates_issues_labels_and_sets_status(status_file: Path) -> None:
    gh = FakeGh(labels=["bug"])
    rc = main([str(status_file.parent), "--repo", "o/r", "--apply", "--create-project"], run=gh)
    assert rc == 0
    verbs = gh.verbs()
    assert verbs.count("issue create") == 4 and verbs.count("project item-edit") == 2
    assert verbs.count("issue close") == 2  # X-F02 (done) e D1 (fechado)
    created = [c[2] for c in gh.calls if c[:2] == ["label", "create"]]
    assert created == ["ada:blocked", "entrega:X"]  # "bug" já existia
    edits = [c for c in gh.calls if c[:2] == ["project", "item-edit"]]
    assert edits[0][-1] == "o-implement" and edits[1][-1] == "o-done"
    creates = [c for c in gh.calls if c[:2] == ["issue", "create"]]
    assert creates[0].count("--label") == 2 and creates[1].count("--label") == 1
    assert any("<!-- ada-defeito:D1 -->" in c[c.index("--body") + 1] for c in creates)
    assert not any(c[:2] == ["label", "create"] and c[2] == "bug" for c in gh.calls)


def test_apply_updates_existing_issue_by_marker_and_kind(status_file: Path) -> None:
    existing = [
        {"number": 3, "state": "OPEN", "title": "old", "body": "<!-- ada-feature:X-F01 -->\nold"},
        {"number": 4, "state": "OPEN", "title": "old", "body": "<!-- ada-defeito:D2 -->\nold"},
    ]
    gh = FakeGh(issues=existing)
    assert main([str(status_file.parent), "--repo", "o/r", "--apply", "--project-number", "7"], run=gh) == 0
    assert any(c[:3] == ["issue", "edit", "3"] for c in gh.calls)
    assert any(c[:3] == ["issue", "edit", "4"] for c in gh.calls)
    assert sum(1 for c in gh.calls if c[:2] == ["issue", "create"]) == 2
