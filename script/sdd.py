"""Validação da cadeia SDD da Ada Harness 2.0."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.card_lint import lint_cards_document
from script.plan_lint import lint_feature_plan
from script.project_config import get_str, load_config


HEADINGS: Final[dict[str, tuple[str, ...]]] = {
    "prd": ("## Problema", "## Requisitos funcionais", "## Requisitos não funcionais", "## Critérios de aceite"),
    "adr": ("## Contexto", "## Decisão", "## Consequências"),
    "rfc": ("## Desenho", "## Interfaces", "## Testes", "## Rollback"),
}


class SDDError(ValueError):
    pass


def lint_document(path: Path, kind: str) -> list[str]:
    if kind not in HEADINGS:
        raise SDDError(f"tipo SDD inválido: {kind}")
    if not path.is_file():
        return [f"documento ausente: {path}"]
    text = path.read_text(encoding="utf-8")
    missing = [heading for heading in HEADINGS[kind] if heading not in text]
    vague = re.findall(r"\b(adéqu?|adequad[a-z]*|appropriad[a-z]*|de forma segura)\b", text, flags=re.I)
    return [f"seção ausente: {heading}" for heading in missing] + (
        ["critério vago encontrado; use resultado observável"] if vague else []
    )


def write_approval(
    path: Path,
    *,
    document: str,
    status: str,
    sha: str,
    reviewer: str,
) -> dict[str, object]:
    if status not in {"approved", "rejected", "needs_input"}:
        raise SDDError(f"status de aprovação inválido: {status}")
    if document not in {*HEADINGS, "plan"}:
        raise SDDError(f"documento inválido: {document}")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SDDError("hash do documento precisa ser SHA-256")
    if not reviewer.strip():
        raise SDDError("reviewer ausente")
    approval = {
        "schemaVersion": 2,
        "document": document,
        "status": status,
        "reviewer": reviewer.strip(),
        "hash": sha,
        "decisions": [],
        "open_questions": [],
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return approval


def approve_document(
    approval_path: Path,
    document_path: Path,
    *,
    kind: str,
    reviewer: str,
    decisions: list[str] | None = None,
    open_questions: list[str] | None = None,
) -> dict[str, object]:
    if kind not in HEADINGS and kind != "plan":
        raise SDDError(f"tipo de documento inválido: {kind}")
    if not reviewer.strip():
        raise SDDError("reviewer precisa ser identificado")
    if decisions is not None and (
        not isinstance(decisions, list)
        or any(not isinstance(item, str) or not item.strip() for item in decisions)
    ):
        raise SDDError("decisions precisa ser lista de textos não vazios")
    if open_questions is not None and (
        not isinstance(open_questions, list)
        or any(not isinstance(item, str) or not item.strip() for item in open_questions)
    ):
        raise SDDError("open_questions precisa ser lista de textos não vazios")
    if not document_path.is_file():
        raise SDDError(f"documento ausente: {document_path}")
    content_hash = hashlib.sha256(document_path.read_bytes()).hexdigest()
    approval = {
        "schemaVersion": 2,
        "document": kind,
        "status": "approved",
        "reviewer": reviewer.strip(),
        "hash": content_hash,
        "decisions": decisions or [],
        "open_questions": open_questions or [],
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_atomic(approval_path, json.dumps(approval, ensure_ascii=False, indent=2) + "\n")
    return approval


def validate_approval(document_path: Path, approval_path: Path, *, kind: str) -> list[str]:
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"aprovação ilegível: {exc}"]
    if not isinstance(approval, dict):
        return ["aprovação precisa ser mapa"]

    errors: list[str] = []
    required = {
        "schemaVersion", "document", "status", "reviewer", "hash", "decisions",
        "open_questions", "approved_at",
    }
    if required - approval.keys():
        errors.append(f"aprovação: campos ausentes {', '.join(sorted(required - approval.keys()))}")
    if approval.keys() - required:
        errors.append(f"aprovação: campos desconhecidos {', '.join(sorted(approval.keys() - required))}")
    if approval.get("schemaVersion") != 2:
        errors.append("aprovação: schemaVersion esperado 2")
    if approval.get("document") != kind:
        errors.append(f"aprovação: documento esperado {kind}")
    if approval.get("status") != "approved":
        errors.append("aprovação: status não é approved")
    if not isinstance(approval.get("reviewer"), str) or not approval["reviewer"].strip():
        errors.append("aprovação: reviewer ausente")
    approved_at = approval.get("approved_at")
    if not isinstance(approved_at, str):
        errors.append("aprovação: approved_at inválido")
    else:
        try:
            datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append("aprovação: approved_at inválido")
    if not document_path.is_file():
        errors.append(f"documento ausente: {document_path}")
    elif approval.get("hash") != hashlib.sha256(document_path.read_bytes()).hexdigest():
        errors.append("aprovação: hash do documento mudou")
    questions = approval.get("open_questions")
    if not isinstance(questions, list) or any(
        not isinstance(item, str) or not item.strip() for item in questions
    ):
        errors.append("aprovação: open_questions inválido")
    elif questions:
        errors.append("aprovação: há questões abertas")
    decisions = approval.get("decisions")
    if not isinstance(decisions, list) or any(
        not isinstance(item, str) or not item.strip() for item in decisions
    ):
        errors.append("aprovação: decisions inválido")
    return errors


def validate_chain(directory: Path) -> list[str]:
    errors: list[str] = []
    for kind in ("prd", "adr", "rfc"):
        path = directory / f"{kind}.md"
        errors.extend(lint_document(path, kind))
    return errors


def validate_approved_chain(directory: Path) -> list[str]:
    errors = validate_chain(directory)
    if errors:
        return errors
    for kind in ("prd", "adr", "rfc"):
        errors.extend(
            f"{kind}: {message}"
            for message in validate_approval(
                directory / f"{kind}.md",
                directory / f"{kind}.approval.json",
                kind=kind,
            )
        )
    return errors


def prepare_feature(
    project_root: Path,
    *,
    sdd_directory: Path,
    plan_path: Path,
    cards_path: Path,
    approval_path: Path,
) -> tuple[Path, Path]:
    chain_errors = validate_approved_chain(sdd_directory)
    if chain_errors:
        raise SDDError("cadeia SDD não aprovada: " + "; ".join(chain_errors))
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SDDError(f"plano ilegível: {exc}") from exc
    if not isinstance(plan, dict):
        raise SDDError("plano precisa ser mapa")
    plan_errors = lint_feature_plan(plan)
    if plan_errors:
        raise SDDError("plano inválido: " + "; ".join(plan_errors))
    plan_errors = validate_approval(plan_path, approval_path, kind="plan")
    if plan_errors:
        raise SDDError("plano sem aprovação válida: " + "; ".join(plan_errors))

    try:
        cards = yaml.safe_load(cards_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SDDError(f"cartões ilegíveis: {exc}") from exc
    if not isinstance(cards, dict):
        raise SDDError("cartões precisam ser mapa")
    card_errors = lint_cards_document(cards, strict=True, project_root=project_root)
    if card_errors:
        raise SDDError("cartões inválidos: " + "; ".join(card_errors))

    feature = plan.get("feature")
    if not isinstance(feature, str) or not re.fullmatch(r"[A-Za-z0-9_-]+-F\d+", feature):
        raise SDDError("feature precisa ser um ID <entrega>-F<n>")
    card_items = cards.get("cards")
    if not isinstance(card_items, list) or not card_items:
        raise SDDError("cards precisa conter ao menos um cartão")
    for card in card_items:
        if not isinstance(card, dict) or not str(card.get("id", "")).startswith(feature + "-T"):
            raise SDDError(f"todo cartão precisa pertencer a {feature}")

    try:
        config = load_config(project_root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        raise SDDError(f"harness.project.yaml inválido: {exc}") from exc
    docs_dir = get_str(config, "docs_dir") or "docs"
    if (
        Path(docs_dir).is_absolute()
        or Path(docs_dir).drive
        or "\\" in docs_dir
        or ".." in Path(docs_dir).parts
    ):
        raise SDDError("docs_dir precisa ser relativo e seguro")
    plan_output = project_root / docs_dir / "plano" / feature / "plan.json"
    cards_output = project_root / docs_dir / "plano" / f"{feature}.yaml"
    cards_glob = get_str(config, "cards_glob")
    if not cards_glob or not fnmatch.fnmatchcase(str(cards_output.relative_to(project_root)), cards_glob):
        raise SDDError(f"cards_glob não inclui o destino {cards_output.relative_to(project_root)}")
    if plan_output.exists() or cards_output.exists():
        raise SDDError("destino da feature já existe; não sobrescrevendo")
    _write_atomic(plan_output, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    _write_atomic(cards_output, yaml.safe_dump(cards, allow_unicode=True, sort_keys=False))
    return plan_output, cards_output


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"lint", "approve", "validate", "prepare-feature"}:
        parser = argparse.ArgumentParser(description="Validação da cadeia SDD.")
        parser.add_argument("directory", type=Path)
        args = parser.parse_args(arguments)
        errors = validate_chain(args.directory)
    else:
        parser = argparse.ArgumentParser(description="Ferramentas da cadeia SDD.")
        commands = parser.add_subparsers(dest="command", required=True)
        lint_parser = commands.add_parser("lint")
        lint_parser.add_argument("directory", type=Path)
        approve_parser = commands.add_parser("approve")
        approve_parser.add_argument("kind", choices=[*HEADINGS, "plan"])
        approve_parser.add_argument("document", type=Path)
        approve_parser.add_argument("approval", type=Path)
        approve_parser.add_argument("--reviewer", required=True)
        approve_parser.add_argument("--decision", action="append", default=[])
        approve_parser.add_argument("--open-question", action="append", default=[])
        validate_parser = commands.add_parser("validate")
        validate_parser.add_argument("directory", type=Path)
        prepare_parser = commands.add_parser("prepare-feature")
        prepare_parser.add_argument("project_root", type=Path)
        prepare_parser.add_argument("--sdd-directory", type=Path, required=True)
        prepare_parser.add_argument("--plan", type=Path, required=True)
        prepare_parser.add_argument("--cards", type=Path, required=True)
        prepare_parser.add_argument("--approval", type=Path, required=True)
        args = parser.parse_args(arguments)
        if args.command == "approve":
            try:
                approval = approve_document(
                    args.approval,
                    args.document,
                    kind=args.kind,
                    reviewer=args.reviewer,
                    decisions=args.decision,
                    open_questions=args.open_question,
                )
            except SDDError as exc:
                print(f"sdd: FAIL: {exc}")
                return 1
            print(f"sdd: approved {approval['document']} sha256={approval['hash']}")
            return 0
        if args.command == "prepare-feature":
            try:
                plan_output, cards_output = prepare_feature(
                    args.project_root,
                    sdd_directory=args.sdd_directory,
                    plan_path=args.plan,
                    cards_path=args.cards,
                    approval_path=args.approval,
                )
            except SDDError as exc:
                print(f"sdd: FAIL: {exc}")
                return 1
            print(f"sdd: feature criada: {plan_output} / {cards_output}")
            return 0
        errors = (
            validate_approved_chain(args.directory)
            if args.command == "validate"
            else validate_chain(args.directory)
        )
    if not errors:
        print("sdd-lint: PASS")
        return 0
    for error in errors:
        print(f"sdd-lint: FAIL: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
