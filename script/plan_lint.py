#!/usr/bin/env python3
"""Lint estruturado de estado/plano da Ada Harness 2.0."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Final

import yaml

VALID_LAYERS: Final[frozenset[str]] = frozenset({"unit", "prop", "e2e", "mut", "visual"})
PLAN_HEADINGS: Final[tuple[str, ...]] = (
    "## Escopo",
    "## Contrato",
    "## Arquivos de produção",
    "## Critérios de aceite",
    "## Cenários",
    "## Arquivos de teste previstos",
)


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    windows_path = PureWindowsPath(value)
    return (
        bool(value.strip())
        and "\\" not in value
        and not path.is_absolute()
        and not windows_path.is_absolute()
        and not windows_path.drive
        and ".." not in path.parts
        and ".." not in windows_path.parts
    )


def lint_plan(
    state: dict[str, object],
    *,
    strict: bool = False,
    project_root: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    feature = state.get("feature")
    if not isinstance(feature, str) or not feature.strip():
        errors.append("feature: ID ausente")

    sources = state.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources: lista vazia ou ausente")
    elif any(not isinstance(item, str) or not _safe_relative_path(item) for item in sources):
        errors.append("sources: precisa ser lista de paths relativos")

    domain_sources = state.get("domainSources")
    if not isinstance(domain_sources, list) and (strict or domain_sources is not None):
        errors.append("domainSources: lista ausente")
    elif isinstance(domain_sources, list) and isinstance(sources, list):
        if any(not isinstance(item, str) or not _safe_relative_path(item) for item in domain_sources):
            errors.append("domainSources: precisa ser lista de paths relativos")
        unknown = set(item for item in domain_sources if isinstance(item, str)) - set(
            item for item in sources if isinstance(item, str)
        )
        if unknown:
            errors.append(f"domainSources: fora de sources: {', '.join(sorted(map(str, unknown)))}")

    criteria = state.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        errors.append("criteria: lista vazia ou ausente")
    else:
        ids: set[str] = set()
        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, dict):
                errors.append(f"criteria[{index}]: item precisa ser mapa")
                continue
            criterion_id = criterion.get("id")
            if not isinstance(criterion_id, str) or not re.fullmatch(r"A\d+", criterion_id):
                errors.append(f"criteria[{index}]: id precisa ser A<n>")
            elif criterion_id in ids:
                errors.append(f"criteria[{index}]: id repetido {criterion_id}")
            else:
                ids.add(criterion_id)
            layers = criterion.get("camadas")
            if not isinstance(layers, list) or not layers:
                errors.append(f"{criterion_id or index}: camadas vazias ou ausentes")
            elif any(not isinstance(layer, str) or layer not in VALID_LAYERS for layer in layers):
                errors.append(f"{criterion_id}: camada fora do domínio")
            elif len(set(layers)) != len(layers):
                errors.append(f"{criterion_id}: camadas duplicadas")

    tests = state.get("tests")
    if not isinstance(tests, list):
        errors.append("tests: lista ausente")
    elif any(not isinstance(path, str) or not _safe_relative_path(path) for path in tests):
        errors.append("tests: precisa ser lista de paths relativos")

    e2e = state.get("e2e")
    if not isinstance(e2e, list):
        errors.append("e2e: lista ausente")
    elif any(not isinstance(path, str) or not _safe_relative_path(path) for path in e2e):
        errors.append("e2e: precisa ser lista de paths relativos")

    scenarios = state.get("scenarios")
    if strict and not isinstance(scenarios, list):
        errors.append("scenarios: lista ausente")
    elif isinstance(scenarios, list):
        criterion_ids = {
            item.get("id")
            for item in criteria
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        } if isinstance(criteria, list) else set()
        scenario_ids: set[str] = set()
        scenario_layers: set[tuple[str, str]] = set()
        for index, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict):
                errors.append(f"scenarios[{index}]: item precisa ser mapa")
                continue
            sid = scenario.get("id")
            criterion_id = scenario.get("criterio")
            layer = scenario.get("camada")
            file = scenario.get("arquivo")
            name = scenario.get("teste")
            if not isinstance(sid, str) or not re.fullmatch(r"C\d+", sid):
                errors.append(f"scenarios[{index}].id: esperado C<n>")
            elif sid in scenario_ids:
                errors.append(f"scenarios[{index}].id: repetido {sid}")
            else:
                scenario_ids.add(sid)
            if not isinstance(criterion_id, str) or criterion_id not in criterion_ids:
                errors.append(f"{sid or index}: critério inexistente")
            if not isinstance(layer, str) or layer not in {"unit", "prop", "e2e"}:
                errors.append(f"{sid or index}: camada de cenário inválida")
            if not isinstance(file, str) or not file.strip():
                errors.append(f"{sid or index}: arquivo ausente")
            elif not _safe_relative_path(file):
                errors.append(f"{sid or index}: arquivo precisa ser path relativo")
            elif layer == "e2e" and isinstance(e2e, list) and file not in e2e:
                errors.append(f"{sid or index}: cenário e2e fora da lista e2e")
            elif layer in {"unit", "prop"} and isinstance(tests, list) and file not in tests:
                errors.append(f"{sid or index}: cenário unit/prop fora da lista tests")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{sid or index}: nome do teste ausente")
            if isinstance(criterion_id, str) and isinstance(layer, str):
                scenario_layers.add((criterion_id, layer))

        if strict and isinstance(criteria, list):
            for criterion in criteria:
                if not isinstance(criterion, dict) or not isinstance(criterion.get("id"), str):
                    continue
                layers = criterion.get("camadas")
                if not isinstance(layers, list):
                    continue
                for layer in layers:
                    if layer != "mut" and isinstance(layer, str) and (criterion["id"], layer) not in scenario_layers:
                        errors.append(f"{criterion['id']}: sem cenário {layer}")

    if strict:
        if state.get("schemaVersion") != 2:
            errors.append("schemaVersion: esperado 2")
        if isinstance(sources, list) and not sources:
            errors.append("sources: feature precisa declarar ao menos uma fonte")
        if isinstance(state.get("exemptSources"), list) and isinstance(sources, list):
            exempt_files = {
                item.get("file") for item in state["exemptSources"] if isinstance(item, dict)
            }
            overlap = {
                path for path in exempt_files if isinstance(path, str)
            } & {
                path for path in sources if isinstance(path, str)
            }
            if overlap:
                errors.append(f"exemptSources também está em sources: {', '.join(sorted(overlap))}")
        test_shas = state.get("testShas")
        if test_shas is not None:
            if not isinstance(test_shas, dict):
                errors.append("testShas: precisa ser mapa de path para SHA-256")
            else:
                test_paths = set(path for path in tests if isinstance(path, str)) if isinstance(tests, list) else set()
                for path, sha in test_shas.items():
                    if not isinstance(path, str) or not _safe_relative_path(path) or path not in test_paths:
                        errors.append(f"testShas.{path}: teste fora da lista tests")
                    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
                        errors.append(f"testShas.{path}: SHA-256 inválido")
                    elif project_root is not None and isinstance(path, str):
                        test_path = project_root / path
                        if not test_path.is_file():
                            errors.append(f"testShas.{path}: arquivo de teste ausente")
                        else:
                            actual_sha = hashlib.sha256(test_path.read_bytes()).hexdigest()
                            if actual_sha.lower() != sha.lower():
                                errors.append(f"testShas.{path}: hash do arquivo mudou")

    exempt = state.get("exemptSources", [])
    if not isinstance(exempt, list):
        errors.append("exemptSources: precisa ser lista")
    else:
        for index, item in enumerate(exempt):
            if not isinstance(item, dict):
                errors.append(f"exemptSources[{index}]: item precisa ser mapa")
                continue
            for field in ("file", "class", "reason"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"exemptSources[{index}].{field}: valor ausente")
            if not isinstance(item.get("class"), str) or item.get("class") not in {
                "dispensa-estilo", "dispensa-biblioteca"
            }:
                errors.append(f"exemptSources[{index}].class: classificação inválida")

    return errors


def lint_feature_plan(plan: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if plan.get("schemaVersion") != 2:
        errors.append("schemaVersion: esperado 2")
    allowed_fields = {
        "schemaVersion", "feature", "scope", "contract", "sources", "criteria", "scenarios",
        "tests", "e2e", "decisions", "openQuestions",
    }
    for field in sorted(set(plan) - allowed_fields):
        errors.append(f"{field}: campo não permitido no plano 2.0")
    for field in ("feature", "scope", "contract"):
        value = plan.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field}: texto obrigatório")

    sources = plan.get("sources")
    source_files: set[str] = set()
    has_domain = False
    if not isinstance(sources, list) or not sources:
        errors.append("sources: lista não vazia obrigatória")
    else:
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                errors.append(f"sources[{index}]: precisa ser mapa")
                continue
            path, classification = source.get("file"), source.get("classification")
            if not isinstance(path, str) or not _safe_relative_path(path):
                errors.append(f"sources[{index}].file: caminho relativo obrigatório")
                continue
            if path in source_files:
                errors.append(f"sources[{index}].file: duplicado {path}")
            source_files.add(path)
            if not isinstance(classification, str) or classification not in {
                "tdd", "dispensa-estilo", "dispensa-biblioteca"
            }:
                errors.append(f"{path}: classification inválida")
            domain = source.get("domain", False)
            if not isinstance(domain, bool):
                errors.append(f"{path}: domain precisa ser boolean")
            elif domain:
                has_domain = True
            if classification in {"dispensa-estilo", "dispensa-biblioteca"} and (
                not isinstance(source.get("reason"), str) or not source["reason"].strip()
            ):
                errors.append(f"{path}: dispensa sem justificativa")
            if classification != "tdd" and source.get("domain") is True:
                errors.append(f"{path}: fonte dispensada não pode conter lógica de domínio")

    criteria = plan.get("criteria")
    criterion_layers: dict[str, list[str]] = {}
    if not isinstance(criteria, list) or not criteria:
        errors.append("criteria: lista não vazia obrigatória")
    else:
        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, dict):
                errors.append(f"criteria[{index}]: precisa ser mapa")
                continue
            cid, statement, source, layers = (
                criterion.get("id"),
                criterion.get("statement"),
                criterion.get("source"),
                criterion.get("layers"),
            )
            if not isinstance(cid, str) or not re.fullmatch(r"A\d+", cid):
                errors.append(f"criteria[{index}].id: esperado A<n>")
                continue
            if cid in criterion_layers:
                errors.append(f"criteria[{index}].id: duplicado {cid}")
            if not isinstance(statement, str) or not statement.strip():
                errors.append(f"{cid}: statement ausente")
            if not isinstance(source, str) or not source.strip():
                errors.append(f"{cid}: fonte ausente")
            if not isinstance(layers, list) or not layers:
                errors.append(f"{cid}: layers vazias")
                continue
            if any(not isinstance(layer, str) or layer not in VALID_LAYERS for layer in layers):
                errors.append(f"{cid}: layer inválida")
            criterion_layers[cid] = [
                layer for layer in layers
                if isinstance(layer, str) and layer in VALID_LAYERS
            ]

    scenarios = plan.get("scenarios")
    scenario_layers: set[tuple[str, str]] = set()
    if not isinstance(scenarios, list) or not scenarios:
        errors.append("scenarios: lista não vazia obrigatória")
    else:
        scenario_ids: set[str] = set()
        for index, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict):
                errors.append(f"scenarios[{index}]: precisa ser mapa")
                continue
            sid, cid, layer = scenario.get("id"), scenario.get("criterion"), scenario.get("layer")
            if not isinstance(sid, str) or not re.fullmatch(r"C\d+", sid):
                errors.append(f"scenarios[{index}].id: esperado C<n>")
            elif sid in scenario_ids:
                errors.append(f"scenarios[{index}].id: duplicado {sid}")
            scenario_ids.add(sid) if isinstance(sid, str) else None
            if not isinstance(cid, str) or cid not in criterion_layers:
                errors.append(f"{sid or index}: critério inexistente")
            if not isinstance(layer, str) or layer not in {"unit", "prop", "e2e", "visual"}:
                errors.append(f"{sid or index}: layer inválida")
            file = scenario.get("file")
            if not isinstance(file, str) or not _safe_relative_path(file):
                errors.append(f"{sid or index}.file: path relativo obrigatório")
            for field in ("input", "expected", "source"):
                value = scenario.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{sid or index}.{field}: texto obrigatório")
            if isinstance(cid, str) and isinstance(layer, str):
                scenario_layers.add((cid, layer))

    for criterion_id, layers in criterion_layers.items():
        for layer in layers:
            if layer == "mut":
                if not has_domain:
                    errors.append(f"{criterion_id}: mutation exige uma fonte de domínio")
                continue
            if (criterion_id, layer) not in scenario_layers:
                errors.append(f"{criterion_id}: sem cenário {layer}")

    tests = plan.get("tests")
    e2e = plan.get("e2e")
    if not isinstance(tests, list) or any(not isinstance(path, str) or not _safe_relative_path(path) for path in tests):
        errors.append("tests: precisa ser lista de paths relativos")
    if not isinstance(e2e, list) or any(not isinstance(path, str) or not _safe_relative_path(path) for path in e2e):
        errors.append("e2e: precisa ser lista de paths relativos")
    if isinstance(scenarios, list) and isinstance(e2e, list) and isinstance(tests, list):
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                continue
            layer, path = scenario.get("layer"), scenario.get("file")
            allowed_files = e2e if layer == "e2e" else tests if layer in {"unit", "prop"} else None
            if isinstance(allowed_files, list) and path not in allowed_files:
                errors.append(f"{scenario.get('id', '?')}: arquivo de cenário fora da lista da camada")
            if layer == "visual" and isinstance(path, str) and path not in source_files:
                errors.append(f"{scenario.get('id', '?')}: revisão visual precisa apontar para fonte")
    for field in ("decisions", "openQuestions"):
        value = plan.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            errors.append(f"{field}: precisa ser lista de textos não vazios")
    return errors


def lint_plan_markdown(text: str, *, strict: bool = False) -> list[str]:
    errors: list[str] = []
    if not text.strip():
        return ["plano vazio"]
    missing = [heading for heading in PLAN_HEADINGS if heading not in text]
    if strict:
        errors.extend(f"seção ausente: {heading}" for heading in missing)
    elif "## Critérios de aceite" not in text:
        errors.append("seção ausente: ## Critérios de aceite")
    criteria = set(re.findall(r"(?m)^\s*(?:#{1,6}\s+|[-*]\s*)?(A\d+)(?=[:.)\s]|$)", text))
    scenarios = set(re.findall(r"\bC\d+\s*\(\s*(A\d+)", text))
    if strict:
        for criterion in sorted(criteria - scenarios):
            errors.append(f"{criterion}: sem cenário C<n>")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint de plano Ada Harness 2.0.")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--strict", action="store_true", help="aplica contrato de plano 2.0")
    parser.add_argument("--root", type=Path, help="raiz do projeto para verificar testShas")
    args = parser.parse_args(argv)
    try:
        raw = args.plan.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"plan-lint: arquivo ilegível: {exc}")
        return 2
    if args.plan.suffix.lower() == ".md":
        errors = lint_plan_markdown(raw, strict=args.strict)
        if not errors:
            print("plan-lint: PASS")
            return 0
        for error in errors:
            print(f"plan-lint: FAIL: {error}")
        return 1
    try:
        data = json.loads(raw) if args.plan.suffix.lower() == ".json" else yaml.safe_load(raw)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"plan-lint: arquivo ilegível: {exc}")
        return 2
    if not isinstance(data, dict):
        print("plan-lint: plano precisa ser mapa")
        return 1
    errors = lint_feature_plan(data) if data.get("schemaVersion") == 2 and "scope" in data else lint_plan(
        data,
        strict=args.strict,
        project_root=args.root,
    )
    if not errors:
        print("plan-lint: PASS")
        return 0
    for error in errors:
        print(f"plan-lint: FAIL: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
