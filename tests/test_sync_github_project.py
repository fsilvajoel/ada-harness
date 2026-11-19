"""Testes do sincronizador GitHub Projects (`script/sync_github_project.py`), com `gh` simulado."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from script.sync_github_project import REFRESH_COMMAND, desired_issues, leaks, main

STATUS: dict[str, object] = {
    "steps": [{"id": "plan", "label": "plan"}, {"id": "implement", "label": "implement"}, {"id": "done", "label": "done"}],
    "features": [
        {"id": "X-F01", "title": "First", "delivery": "X", "step": "implement", "percent": 50, "blocked": {"status": "quota"}},
        {"id": "X-F02", "title": "Second", "delivery": "X", "step": "done", "percent": 100, "blocked": None},
    ],
    "cards": [{"id": "X-T01", "feature": "X-F01", "state": "blocked"}],
}


class FakeGh:
    def __init__(self, scopes: str = "'repo', 'project'", issues: list[dict[str, object]] | None = None) -> None:
        self.scopes = scopes
        self.issues = issues or []
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], stdin: str | None = None) -> str:
        self.calls.append(args)
        head = " ".join(args[:2])
        if head == "auth status":
            return f"  - Token scopes: {self.scopes}\n"
        if head == "issue list":
            return json.dumps(self.issues)
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


@pytest.fixture
def status_file(tmp_path: Path) -> Path:
    (tmp_path / "harness.project.yaml").write_text("cards_glob: x\n", encoding="utf-8")
    path = tmp_path / "status.json"
    path.write_text(json.dumps(STATUS), encoding="utf-8")
    return path


def test_leaks_detects_ip_path_and_secret() -> None:
    assert leaks("host 10.0.0.5") and leaks("/ho" + "me/x/y") and leaks("see .env") and leaks("ghp_abcdefghijklmnop")
    assert leaks("plain text 1.5") == []


def test_issue_text_has_marker_and_no_card_internals() -> None:
    issues = desired_issues(STATUS)
    assert issues[0]["title"] == "[X-F01] First"
    assert "<!-- ada-feature:X-F01 -->" in str(issues[0]["body"])
    assert "X-T01 (blocked)" in str(issues[0]["body"])
    assert issues[0]["blocked"] is True and issues[1]["done"] is True


def test_dry_run_writes_nothing(status_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    gh = FakeGh(scopes="'repo'")
    rc = main([str(status_file.parent), "--repo", "o/r"], run=gh)
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert [a["issue"] for a in out["actions"]] == ["create", "create"]
    assert all(call[0] in {"auth", "issue"} and call[1] in {"status", "list"} for call in gh.calls)


def test_apply_without_project_scope_prints_command_and_exits_3(
    status_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh(scopes="'repo'")
    rc = main([str(status_file.parent), "--repo", "o/r", "--apply", "--create-project"], run=gh)
    assert rc == 3
    assert REFRESH_COMMAND in capsys.readouterr().err
    assert not any(call[0] in {"issue", "project"} and call[1] != "list" for call in gh.calls)


def test_leaking_text_stops_before_any_call(status_file: Path) -> None:
    data = json.loads(status_file.read_text())
    data["features"][0]["title"] = "Server 192.168.1.10"
    status_file.write_text(json.dumps(data))
    gh = FakeGh()
    assert main([str(status_file.parent), "--repo", "o/r", "--apply", "--create-project"], run=gh) == 4
    assert gh.calls == []


def test_apply_creates_issues_and_sets_status(status_file: Path) -> None:
    gh = FakeGh()
    rc = main([str(status_file.parent), "--repo", "o/r", "--apply", "--create-project"], run=gh)
    assert rc == 0
    verbs = [" ".join(c[:2]) for c in gh.calls]
    assert verbs.count("issue create") == 2 and verbs.count("project item-edit") == 2
    assert "issue close" in verbs
    edits = [c for c in gh.calls if c[:2] == ["project", "item-edit"]]
    assert edits[0][-1] == "o-implement" and edits[1][-1] == "o-done"
    creates = [c for c in gh.calls if c[:2] == ["issue", "create"]]
    assert "--label" in creates[0] and "--label" not in creates[1]


def test_apply_updates_existing_issue_by_marker(status_file: Path) -> None:
    existing = [{"number": 3, "state": "OPEN", "title": "old", "body": "<!-- ada-feature:X-F01 -->\nold"}]
    gh = FakeGh(issues=existing)
    assert main([str(status_file.parent), "--repo", "o/r", "--apply", "--project-number", "7"], run=gh) == 0
    assert any(c[:3] == ["issue", "edit", "3"] for c in gh.calls)
    assert sum(1 for c in gh.calls if c[:2] == ["issue", "create"]) == 1
