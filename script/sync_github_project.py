#!/usr/bin/env python3
"""Sincroniza o `status.json` com GitHub Issues e um GitHub Project v2, via `gh`.

Uma issue por feature (marcador `<!-- ada-feature:<ID> -->` no corpo, então rodar de novo atualiza em vez de
duplicar) e um item no Project por issue, com o campo single-select `Status` na etapa do pipeline. Feature pronta
(`done`) fecha a issue. Feature bloqueada recebe o rótulo `ada:blocked`.

O repositório alvo pode ser público: o texto das issues só leva ID, título, entrega, etapa, percentual, IDs de
cartões e o status de bloqueio. Antes de qualquer chamada de escrita, o texto passa por um filtro que recusa IPs,
caminhos de máquina, chaves e arquivos de segredo (saída 4).

Sem `--apply` nada é escrito: imprime o plano (JSON). Com `--apply` e sem o escopo `project` no token do `gh`,
imprime o comando exato que o humano precisa rodar e sai com 3 (o refresh do escopo exige login interativo).

Uso: `sync_github_project.py <raiz do projeto> [--status status.json] [--repo dono/repo] [--owner dono]
[--project-number N | --create-project --project-title T] [--apply]`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import get_str, load_config

type Runner = Callable[[list[str], str | None], str]

MARKER = "<!-- ada-feature:{id} -->"
MARKER_RE = re.compile(r"<!-- ada-feature:([^ ]+) -->")
BLOCKED_LABEL = "ada:blocked"
REFRESH_COMMAND = "gh auth refresh -s project"
OPTION_COLORS: tuple[str, ...] = ("GRAY", "BLUE", "PURPLE", "YELLOW", "ORANGE", "PINK", "RED", "GREEN", "GREEN")
FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("endereço IPv4", re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")),
    ("caminho de máquina", re.compile(r"(?:/hom[e]/|/Users/|/root/|[A-Za-z]:\\)")),
    ("arquivo de segredo", re.compile(r"(?:\.env\b|id_rsa|\.pem\b|credentials)", re.IGNORECASE)),
    (
        "chave ou token",
        re.compile(r"(?:gh[pousr]_\w{10,}|sk-\w{10,}|Bearer\s+\S{10,}|api[_-]?key\s*[:=])", re.IGNORECASE),
    ),
)


class SyncError(Exception):
    """Erro de execução do sincronizador."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def gh_runner(args: list[str], stdin: str | None = None) -> str:
    proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SyncError(f"gh {' '.join(args[:3])} falhou: {proc.stderr.strip()}")
    return proc.stdout


def leaks(text: str) -> list[str]:
    """Nomes dos padrões proibidos que aparecem no texto."""
    return [name for name, pattern in FORBIDDEN if pattern.search(text)]


def _map(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def desired_issues(status: dict[str, object]) -> list[dict[str, object]]:
    """Uma issue desejada por feature, só com texto público."""
    labels = {str(_map(s)["id"]): str(_map(s)["label"]) for s in _list(status.get("steps"))}
    cards_by_feature: dict[str, list[str]] = {}
    for raw in _list(status.get("cards")):
        card = _map(raw)
        cards_by_feature.setdefault(str(card.get("feature")), []).append(f"{card['id']} ({card['state']})")
    issues: list[dict[str, object]] = []
    for raw in _list(status.get("features")):
        feature = _map(raw)
        fid = str(feature["id"])
        block = _map(feature.get("blocked")) if feature.get("blocked") else {}
        title = f"[{fid}] {feature.get('title') or fid}"
        step_label = labels.get(str(feature["step"]), str(feature["step"]))
        lines = [
            MARKER.format(id=fid),
            f"Feature `{fid}` (delivery `{feature.get('delivery')}`).",
            "",
            f"- Stage: {step_label}",
            f"- Progress: {feature['percent']}%",
            f"- Blocked: {block.get('status', 'no')}",
            f"- Cards: {', '.join(cards_by_feature.get(fid, [])) or 'none'}",
            "",
            "Generated from the cards and estado.json by the harness status command. Do not edit by hand.",
        ]
        issues.append(
            {
                "feature": fid,
                "title": title,
                "body": "\n".join(lines),
                "step": str(feature["step"]),
                "step_label": step_label,
                "blocked": bool(block),
                "done": feature["step"] == "done",
            }
        )
    return issues


def check_public(issues: list[dict[str, object]], labels: list[str]) -> None:
    for issue in issues:
        found = leaks(f"{issue['title']}\n{issue['body']}") + leaks(str(issue["step_label"]))
        if found:
            raise SyncError(f"{issue['feature']}: texto público com {', '.join(sorted(set(found)))}; nada foi enviado", 4)
    for label in labels:
        if leaks(label):
            raise SyncError(f"opção de Status '{label}' com dado sensível; nada foi enviado", 4)


def token_scopes(run: Runner) -> set[str]:
    out = run(["auth", "status"], None)
    match = re.search(r"Token scopes:\s*(.+)", out)
    return {part.strip().strip("'") for part in match.group(1).split(",")} if match else set()


def existing_issues(run: Runner, repo: str) -> dict[str, dict[str, object]]:
    """Issues já criadas pelo sincronizador, por feature."""
    out = run(
        ["issue", "list", "--repo", repo, "--state", "all", "--limit", "1000", "--json", "number,body,state,title"],
        None,
    )
    found: dict[str, dict[str, object]] = {}
    for raw in json.loads(out or "[]"):
        issue = _map(raw)
        match = MARKER_RE.search(str(issue.get("body") or ""))
        if match:
            found[match.group(1)] = issue
    return found


def plan(issues: list[dict[str, object]], existing: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for issue in issues:
        current = existing.get(str(issue["feature"]))
        if current is None:
            action = "create"
        elif current.get("title") != issue["title"] or current.get("body") != issue["body"]:
            action = "update"
        else:
            action = "keep"
        actions.append(
            {"feature": issue["feature"], "issue": action, "status": issue["step_label"], "close": issue["done"]}
        )
    return actions


def ensure_project(run: Runner, owner: str, number: int | None, title: str, create: bool) -> int:
    if number is not None:
        return number
    if not create:
        raise SyncError("informe --project-number ou --create-project", 2)
    out = run(["project", "create", "--owner", owner, "--title", title, "--format", "json"], None)
    return int(str(_map(json.loads(out))["number"]))


def ensure_status_field(
    run: Runner, owner: str, number: int, options: list[str]
) -> tuple[str, dict[str, str]]:
    """Id do campo Status e mapa nome da opção -> id, criando as opções que faltam."""
    listing = json.loads(run(["project", "field-list", str(number), "--owner", owner, "--format", "json"], None))
    status = next((_map(f) for f in _list(_map(listing).get("fields")) if _map(f).get("name") == "Status"), None)
    if status is None:
        raise SyncError("o Project não tem o campo Status")
    field_id = str(status["id"])
    have = {str(_map(o)["name"]): str(_map(o)["id"]) for o in _list(status.get("options"))}
    if not all(name in have for name in options):
        payload = {
            "query": (
                "mutation($f:ID!,$o:[ProjectV2SingleSelectFieldOptionInput!]!){"
                "updateProjectV2Field(input:{fieldId:$f,singleSelectOptions:$o}){"
                "projectV2Field{... on ProjectV2SingleSelectField{options{id name}}}}}"
            ),
            "variables": {
                "f": field_id,
                "o": [
                    {"name": n, "color": OPTION_COLORS[i % len(OPTION_COLORS)], "description": ""}
                    for i, n in enumerate(options)
                ],
            },
        }
        out = run(["api", "graphql", "--input", "-"], json.dumps(payload))
        data = _map(_map(json.loads(out)).get("data"))
        result = _map(_map(data.get("updateProjectV2Field")).get("projectV2Field"))
        have = {str(_map(o)["name"]): str(_map(o)["id"]) for o in _list(result.get("options"))}
    return field_id, have


def apply(
    run: Runner,
    repo: str,
    owner: str,
    number: int | None,
    title: str,
    create: bool,
    issues: list[dict[str, object]],
    options: list[str],
) -> list[str]:
    if "project" not in token_scopes(run):
        raise SyncError(f"o token do gh não tem o escopo 'project'. Rode, interativo, uma vez: {REFRESH_COMMAND}", 3)
    log: list[str] = []
    project_number = ensure_project(run, owner, number, title, create)
    view = run(["project", "view", str(project_number), "--owner", owner, "--format", "json"], None)
    project_id = str(_map(json.loads(view))["id"])
    field_id, option_ids = ensure_status_field(run, owner, project_number, options)
    run(["label", "create", BLOCKED_LABEL, "--repo", repo, "--color", "B60205", "--force"], None)
    existing = existing_issues(run, repo)
    for issue in issues:
        fid = str(issue["feature"])
        current = existing.get(fid)
        if current is None:
            args = ["issue", "create", "--repo", repo, "--title", str(issue["title"]), "--body", str(issue["body"])]
            if issue["blocked"]:
                args += ["--label", BLOCKED_LABEL]
            url = run(args, None).strip().splitlines()[-1]
            log.append(f"{fid}: issue criada")
        else:
            number_text = str(current["number"])
            url = f"https://github.com/{repo}/issues/{number_text}"
            base = ["issue", "edit", number_text, "--repo", repo, "--title", str(issue["title"]), "--body", str(issue["body"])]
            label_args = ["--add-label", BLOCKED_LABEL] if issue["blocked"] else ["--remove-label", BLOCKED_LABEL]
            try:
                run(base + label_args, None)
            except SyncError:
                run(base, None)  # rótulo ausente ao remover: edita só título e corpo
            log.append(f"{fid}: issue atualizada")
        state = str(current.get("state", "OPEN")) if current else "OPEN"
        if issue["done"] and state != "CLOSED":
            run(["issue", "close", url, "--repo", repo], None)
        elif not issue["done"] and state == "CLOSED":
            run(["issue", "reopen", url, "--repo", repo], None)
        added = run(["project", "item-add", str(project_number), "--owner", owner, "--url", url, "--format", "json"], None)
        item_id = str(_map(json.loads(added))["id"])
        run(
            [
                "project", "item-edit", "--id", item_id, "--project-id", project_id,
                "--field-id", field_id, "--single-select-option-id", option_ids[str(issue["step_label"])],
            ],
            None,
        )
        log.append(f"{fid}: Status = {issue['step_label']}")
    return log


def repo_from_remote(root: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True, check=False
    )
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", proc.stdout.strip())
    if not match:
        raise SyncError("não achei o repositório GitHub em 'origin'; passe --repo", 2)
    return match.group(1)


def main(argv: list[str] | None = None, run: Runner = gh_runner) -> int:
    parser = argparse.ArgumentParser(description="Sincroniza status.json com GitHub Issues e Projects v2.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--status", type=Path, default=None)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--owner", default=None)
    parser.add_argument("--project-number", type=int, default=None)
    parser.add_argument("--create-project", action="store_true")
    parser.add_argument("--project-title", default="Pipeline")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    try:
        config = load_config(root)
        status_path: Path = args.status or root / (get_str(config, "status_file") or "status.json")
        if not status_path.is_file():
            raise SyncError(f"{status_path} não existe; rode script/status.py primeiro", 2)
        status = _map(json.loads(status_path.read_text(encoding="utf-8")))
        repo: str = args.repo or repo_from_remote(root)
        owner: str = args.owner or repo.split("/")[0]
        issues = desired_issues(status)
        options = [str(_map(s)["label"]) for s in _list(status.get("steps"))]
        check_public(issues, options)
        if not args.apply:
            try:
                existing = existing_issues(run, repo)
            except SyncError:
                existing = {}
            report = {"repo": repo, "owner": owner, "status_options": options, "actions": plan(issues, existing)}
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if "project" not in token_scopes(run):
                print(f"aviso: falta o escopo 'project' no token do gh; antes do --apply rode: {REFRESH_COMMAND}", file=sys.stderr)
            return 0
        for line in apply(run, repo, owner, args.project_number, args.project_title, args.create_project, issues, options):
            print(line)
        return 0
    except SyncError as exc:
        print(f"sync: {exc}", file=sys.stderr)
        return exc.code
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        print(f"sync: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
