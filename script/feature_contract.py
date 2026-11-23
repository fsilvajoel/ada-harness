"""Contrato versionado de execução por feature (Onda 5).

O contrato liga os artefatos aprovados (PRD/ADR/RFC, plano e cartões) a cada stage pós-plano: papel, cadeia de
executores, request (com SHA-256), gate, timeout, `allowedPaths`, skills, modo e cartões. É gerado de forma
determinística por `build_contract` e validado por `lint_contract` antes de qualquer worker rodar.

Limitação assumida: os cartões são executados **uma stage por kind** (`test-author` na stage `tests`,
`implement` na stage `implement`). O worker recebe todos os cartões da stage em ordem topológica no mesmo
request, e o escopo da stage é a união dos `allowedPaths` desses cartões.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

import yaml

from script.pipeline import STAGES
from script.project_config import CONFIG_NAME
from script.testing_policy import resolve_feature_gates, stage_policy

CONTRACT_VERSION: Final[int] = 1
CONTRACT_STAGES: Final[tuple[str, ...]] = tuple(stage for stage in STAGES if stage != "plan")
SDD_KINDS: Final[tuple[str, ...]] = ("prd", "adr", "rfc")
CARD_KINDS: Final[frozenset[str]] = frozenset({"docs", "implement", "pre-review", "test-author"})
FEATURE_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+-F\d+")
SHA_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
TOP_FIELDS: Final[frozenset[str]] = frozenset({
    "schemaVersion", "feature", "sdd", "plan", "cards", "cardOrder", "gates", "configSha256", "stages",
    "needsInput",
})
STAGE_FIELDS: Final[frozenset[str]] = frozenset({
    "stage", "role", "executorChain", "maxAttempts", "request", "gate", "timeoutS", "allowedPaths", "skills",
    "mode", "cards", "applicable",
})
OPTIONAL_STAGE_FIELDS: Final[frozenset[str]] = frozenset({"stubPaths", "stageSha256", "enabled", "reactivateWhen"})
CORE_STAGE: Final[str] = "implement"
DISABLED_REASON: Final[str] = "disabled-by-config"
DISABLED_PLACEHOLDER: Final[str] = "-"
DEFAULT_WORKER_TIMEOUT_S: Final[int] = 1800

type Contract = dict[str, object]


class ContractError(ValueError):
    """Contrato ou configuração de contrato inválidos."""


@dataclass(frozen=True)
class StageSpec:
    role: str
    gate: str
    mode: str
    allowed_paths: tuple[str, ...]
    skills: tuple[str, ...]
    card_kind: str | None
    stubs_from: str | None = None
    enabled: bool = True
    reactivate_when: str | None = None


@dataclass(frozen=True)
class ContractConfig:
    sdd_dir: str
    planner_role: str
    stages: dict[str, StageSpec]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def safe_relative(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or value in {".", "./"} or "\\" in value:
        return False
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and ".." not in posix.parts
    )


def _str_list(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ContractError(f"{label} precisa ser lista de textos")
    return tuple(value)


def load_contract_config(config: dict[str, object], feature_id: str) -> ContractConfig:
    section = config.get("feature_contract")
    if not isinstance(section, dict):
        raise ContractError("feature_contract ausente no harness.project.yaml")
    sdd_dir = section.get("sdd_dir", "docs/sdd")
    if not safe_relative(sdd_dir):
        raise ContractError("feature_contract.sdd_dir precisa ser relativo e seguro")
    planner = section.get("planner", {})
    planner_role = planner.get("role", "planner") if isinstance(planner, dict) else None
    if not isinstance(planner_role, str) or not planner_role.strip():
        raise ContractError("feature_contract.planner.role inválido")
    raw_stages = section.get("stages")
    if not isinstance(raw_stages, dict):
        raise ContractError("feature_contract.stages ausente")
    unknown = sorted(set(raw_stages) - set(CONTRACT_STAGES))
    if unknown:
        raise ContractError(f"feature_contract.stages: stage desconhecida {', '.join(map(str, unknown))}")
    stages: dict[str, StageSpec] = {}
    for stage in CONTRACT_STAGES:
        entry = raw_stages.get(stage)
        label = f"feature_contract.stages.{stage}"
        if not isinstance(entry, dict):
            raise ContractError(f"{label} ausente")
        enabled = entry.get("enabled", True)
        if type(enabled) is not bool:
            raise ContractError(f"{label}.enabled precisa ser booleano")
        reactivate = entry.get("reactivate_when")
        if reactivate is not None and (not isinstance(reactivate, str) or not reactivate.strip()):
            raise ContractError(f"{label}.reactivate_when precisa ser texto não vazio")
        if reactivate is not None and enabled:
            raise ContractError(f"{label}.reactivate_when só vale com enabled: false")
        role, gate, mode = entry.get("role"), entry.get("gate"), entry.get("mode", "write")
        if not enabled:
            role = role if isinstance(role, str) and role.strip() else DISABLED_PLACEHOLDER
            gate = gate if isinstance(gate, str) and gate.strip() else DISABLED_PLACEHOLDER
        if not isinstance(role, str) or not role.strip():
            raise ContractError(f"{label}.role ausente")
        if not isinstance(gate, str) or not gate.strip():
            raise ContractError(f"{label}.gate ausente")
        if mode not in {"read", "write"}:
            raise ContractError(f"{label}.mode precisa ser read ou write")
        card_kind = entry.get("cards")
        if card_kind is not None and card_kind not in CARD_KINDS:
            raise ContractError(f"{label}.cards precisa ser um kind de cartão")
        stubs_from = entry.get("stubsFrom")
        if stubs_from is not None and stubs_from not in CARD_KINDS:
            raise ContractError(f"{label}.stubsFrom precisa ser um kind de cartão")
        allowed = tuple(
            path.replace("{feature}", feature_id)
            for path in _str_list(entry.get("allowedPaths"), f"{label}.allowedPaths")
        )
        stages[stage] = StageSpec(
            role=role,
            gate=gate,
            mode=str(mode),
            allowed_paths=allowed,
            skills=_str_list(entry.get("skills"), f"{label}.skills"),
            card_kind=card_kind if isinstance(card_kind, str) else None,
            stubs_from=stubs_from if isinstance(stubs_from, str) else None,
            enabled=enabled,
            reactivate_when=reactivate.strip() if isinstance(reactivate, str) else None,
        )
    core = stages[CORE_STAGE]
    if not core.enabled:
        raise ContractError(
            f"feature_contract.stages.{CORE_STAGE}.enabled: {CORE_STAGE} é o núcleo do pipeline e não pode ser desligada"
        )
    return ContractConfig(sdd_dir=str(sdd_dir), planner_role=planner_role, stages=stages)


def executor_chain(config: dict[str, object], role: str) -> list[str]:
    roles = config.get("roles")
    entry = roles.get(role) if isinstance(roles, dict) else None
    if not isinstance(entry, dict):
        raise ContractError(f"roles.{role} não configurado")
    primary, fallbacks = entry.get("worker"), entry.get("fallbacks", [])
    if not isinstance(primary, str) or not primary.strip():
        raise ContractError(f"roles.{role}.worker ausente")
    if not isinstance(fallbacks, list) or any(not isinstance(item, str) or not item for item in fallbacks):
        raise ContractError(f"roles.{role}.fallbacks precisa ser lista de textos")
    return [primary, *fallbacks]


def worker_timeout(config: dict[str, object], worker: str) -> int:
    workers = config.get("workers")
    entry = workers.get(worker) if isinstance(workers, dict) else None
    if not isinstance(entry, dict):
        raise ContractError(f"workers.{worker} não configurado")
    timeout = entry.get("timeout_s", DEFAULT_WORKER_TIMEOUT_S)
    if type(timeout) is not int or timeout < 1:
        raise ContractError(f"workers.{worker}.timeout_s inválido")
    return timeout


def topological_cards(cards: list[dict[str, object]]) -> list[dict[str, object]]:
    """Ordena por `dependsOn` (estável pela ordem do arquivo); falha em duplicado, ciclo ou dependência externa."""
    by_id: dict[str, dict[str, object]] = {}
    for card in cards:
        card_id = card.get("id")
        if not isinstance(card_id, str) or not card_id:
            raise ContractError("cartão sem id")
        if card_id in by_id:
            raise ContractError(f"cartão duplicado: {card_id}")
        by_id[card_id] = card
    deps: dict[str, list[str]] = {}
    for card_id, card in by_id.items():
        depends = card.get("dependsOn", [])
        if not isinstance(depends, list) or any(not isinstance(item, str) for item in depends):
            raise ContractError(f"{card_id}: dependsOn inválido")
        missing = [item for item in depends if item not in by_id]
        if missing:
            raise ContractError(f"{card_id}: dependência desconhecida {', '.join(missing)}")
        deps[card_id] = list(depends)
    ordered: list[str] = []
    done: set[str] = set()
    while len(ordered) < len(by_id):
        ready = [card_id for card_id in by_id if card_id not in done and all(dep in done for dep in deps[card_id])]
        if not ready:
            cycle = sorted(card_id for card_id in by_id if card_id not in done)
            raise ContractError(f"ciclo de dependências entre cartões: {', '.join(cycle)}")
        ordered.append(ready[0])
        done.add(ready[0])
    return [by_id[card_id] for card_id in ordered]


def _card_list(card: dict[str, object], field: str) -> list[str]:
    value = card.get(field, [])
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _union(groups: list[list[str]] | list[tuple[str, ...]]) -> list[str]:
    seen: list[str] = []
    for group in groups:
        for item in group:
            if item not in seen:
                seen.append(item)
    return seen


def render_request(
    *,
    feature_id: str,
    stage: str,
    role: str,
    gate: str,
    sdd: dict[str, dict[str, str]],
    plan: dict[str, str],
    cards: list[dict[str, object]],
    stage_cards: list[str],
    allowed_paths: list[str],
    stub_paths: list[str] | None = None,
) -> str:
    """Request determinístico da stage: só caminhos, hashes e campos estruturados (sem segredos)."""
    lines = [
        f"# {feature_id} — stage {stage}",
        "",
        f"Papel: {role}. Gate: commands.{gate}.",
        "",
        "## Fontes aprovadas (SHA-256)",
        *(f"- {kind}: {ref['path']} ({ref['sha256']})" for kind, ref in sdd.items()),
        f"- plano: {plan['path']} ({plan['sha256']})",
        "",
        "## Cartões em ordem topológica",
    ]
    for card in cards:
        card_id = str(card.get("id"))
        marker = " [desta stage]" if card_id in stage_cards else ""
        lines.append(f"### {card_id} — {card.get('title', '')} ({card.get('kind', '')}){marker}")
        for field in ("dependsOn", "allowedPaths", "checks", "dod"):
            values = _card_list(card, field)
            if values:
                lines.append(f"- {field}: " + "; ".join(values))
    lines += [
        "",
        "## Escopo permitido desta stage",
        *(f"- {path}" for path in allowed_paths),
        *(
            [
                "",
                "## Stubs tipados permitidos (somente arquivos novos; arquivo existente não pode ser alterado)",
                *(f"- {path}" for path in stub_paths),
            ]
            if stub_paths else []
        ),
        "",
        "## Regras",
        "- Escreva apenas dentro do escopo permitido; qualquer arquivo fora dele descarta a tentativa.",
        "- Não faça commit, não altere gates, estado do harness nem configuração.",
        "",
    ]
    return "\n".join(lines)


def _map(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def stage_config_sha256(
    project_root: Path, config: dict[str, object], *, stage: str, role: str, chain: list[str], gate: str,
    allowed_paths: list[str], skills: list[str],
) -> str:
    """SHA-256 só do que a stage consome: recorte do yaml, worker/fallbacks, gate e conteúdo das skills.

    Comentários, workers fora da cadeia e skills de outras stages não entram no hash.
    """
    section = _map(config.get("feature_contract"))
    roles, workers = _map(config.get("roles")), _map(config.get("workers"))
    commands, gates = _map(config.get("commands")), _map(config.get("gates"))
    role_skills = _map(config.get("role_skills")).get(role)
    specs = [*(role_skills if isinstance(role_skills, list) else []), *skills]
    configured = config.get("skill_dir")
    base = (
        (project_root / configured.strip()).resolve()
        if isinstance(configured, str) and configured.strip()
        else (Path(__file__).resolve().parents[1] / "skill").resolve()
    )
    skill_files: dict[str, str] = {}
    for spec in dict.fromkeys(str(item).strip() for item in specs):
        name, _, rule = spec.partition("#")
        targets = [(f"{name}/SKILL.md", base / name / "SKILL.md")]
        if rule:
            rule_name = rule.removesuffix(".md")
            targets.append((f"{name}/rules/{rule_name}.md", base / name / "rules" / f"{rule_name}.md"))
        for label, path in targets:
            ok = path.is_file() and path.resolve().is_relative_to(base)
            skill_files[label] = sha256_file(path) if ok else "ausente"
    slice_: dict[str, object] = {
        "stage": stage,
        "spec": _map(_map(section.get("stages")).get(stage)),
        "role": roles.get(role),
        "workers": {name: workers.get(name) for name in chain},
        "gate": {"name": gate, "command": commands.get(gate), "config": gates.get(gate),
                 "lint-fix": commands.get("lint-fix"), "format-write": commands.get("format-write"),
                 "install": commands.get("install")},
        "retry": config.get("retry"),
        "pregate": config.get("pregate"),
        "skills": {"specs": specs, "files": skill_files, "max_bytes": config.get("skills_max_bytes")},
        "allowedPaths": allowed_paths,
    }
    return sha256_bytes(json.dumps(slice_, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))


def build_contract(
    project_root: Path,
    *,
    feature_id: str,
    config: dict[str, object],
    plan_path: Path,
    cards_path: Path,
    request_dir: Path,
) -> tuple[Contract, dict[str, str]]:
    """Monta contrato e requests (conteúdo) de forma determinística a partir dos artefatos publicados.

    `request_dir` é relativo à raiz; o contrato guarda o path e o SHA-256 de cada request.
    """
    if not FEATURE_RE.fullmatch(feature_id):
        raise ContractError("feature_id inválido")
    spec = load_contract_config(config, feature_id)
    root = project_root.resolve()
    sdd: dict[str, dict[str, str]] = {}
    for kind in SDD_KINDS:
        relative = f"{spec.sdd_dir}/{feature_id}/{kind}.md"
        sdd[kind] = {"path": relative, "sha256": sha256_file(root / relative)}
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan_data, dict) or plan_data.get("feature") != feature_id:
        raise ContractError("plano não pertence à feature")
    cards_doc = yaml.safe_load(cards_path.read_text(encoding="utf-8"))
    raw_cards = cards_doc.get("cards") if isinstance(cards_doc, dict) else None
    if not isinstance(raw_cards, list) or not all(isinstance(card, dict) for card in raw_cards):
        raise ContractError("cartões precisam ser lista de mapas")
    ordered = topological_cards(raw_cards)
    gates = resolve_feature_gates(plan_data)
    policy = stage_policy(gates)
    plan_ref = {"path": plan_path.resolve().relative_to(root).as_posix(), "sha256": sha256_file(plan_path)}
    cards_ref = {"path": cards_path.resolve().relative_to(root).as_posix(), "sha256": sha256_file(cards_path)}

    stages: list[dict[str, object]] = []
    requests: dict[str, str] = {}
    for stage in CONTRACT_STAGES:
        stage_spec = spec.stages[stage]
        chain = executor_chain(config, stage_spec.role) if stage_spec.enabled else []
        stage_cards = [card for card in ordered if stage_spec.card_kind and card.get("kind") == stage_spec.card_kind]
        allowed = _union([list(stage_spec.allowed_paths), *(_card_list(card, "allowedPaths") for card in stage_cards)])
        skills = _union([list(stage_spec.skills), *(_card_list(card, "skills") for card in stage_cards)])
        card_ids = [str(card["id"]) for card in stage_cards]
        stub_paths: list[str] = []
        if stage_spec.stubs_from:
            stub_cards = [card for card in ordered if card.get("kind") == stage_spec.stubs_from]
            stub_paths = [
                path for path in _union([_card_list(card, "allowedPaths") for card in stub_cards])
                if "*" not in path and path not in allowed
            ]
            allowed = _union([allowed, stub_paths])
        content = render_request(
            feature_id=feature_id,
            stage=stage,
            role=stage_spec.role,
            gate=stage_spec.gate,
            sdd=sdd,
            plan=plan_ref,
            cards=ordered,
            stage_cards=card_ids,
            allowed_paths=allowed,
            stub_paths=stub_paths,
        )
        request_path = (request_dir / f"{stage}.md").as_posix()
        requests[request_path] = content
        stages.append({
            "stage": stage,
            "role": stage_spec.role,
            "executorChain": chain,
            "maxAttempts": len(chain),
            "request": {"path": request_path, "sha256": sha256_bytes(content.encode("utf-8"))},
            "gate": stage_spec.gate,
            "timeoutS": worker_timeout(config, chain[0]) if chain else 0,
            "allowedPaths": allowed,
            "skills": skills,
            "mode": stage_spec.mode,
            "cards": card_ids,
            "applicable": stage_spec.enabled and policy.get(stage, True),
            "stageSha256": stage_config_sha256(
                root, config, stage=stage, role=stage_spec.role, chain=chain, gate=stage_spec.gate,
                allowed_paths=allowed, skills=skills,
            ),
            **({"stubPaths": stub_paths} if stub_paths else {}),
            **({"enabled": False} if not stage_spec.enabled else {}),
            **({"reactivateWhen": stage_spec.reactivate_when} if stage_spec.reactivate_when else {}),
        })
    contract: Contract = {
        "schemaVersion": CONTRACT_VERSION,
        "feature": feature_id,
        "sdd": sdd,
        "plan": plan_ref,
        "cards": cards_ref,
        "cardOrder": [
            {"id": str(card["id"]), "kind": str(card.get("kind")), "dependsOn": _card_list(card, "dependsOn")}
            for card in ordered
        ],
        "gates": dict(sorted(gates.items())),
        "configSha256": sha256_file(root / CONFIG_NAME),
        "stages": stages,
        "needsInput": {"questionsFile": "questions.json", "planApproval": True},
    }
    errors = lint_contract(contract, config)
    if errors:
        raise ContractError("contrato gerado inválido: " + "; ".join(errors))
    return contract, requests


def contract_bytes(contract: Contract) -> bytes:
    return (json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")


def _ref_errors(value: object, label: str) -> list[str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        return [f"{label}: precisa ter exatamente path e sha256"]
    errors: list[str] = []
    if not safe_relative(value.get("path")):
        errors.append(f"{label}.path inseguro")
    if not isinstance(value.get("sha256"), str) or not SHA_RE.fullmatch(str(value["sha256"])):
        errors.append(f"{label}.sha256 inválido")
    return errors


def _card_order_errors(card_order: object) -> list[str]:
    if not isinstance(card_order, list) or not card_order:
        return ["cardOrder: lista não vazia obrigatória"]
    errors: list[str] = []
    seen: list[str] = []
    entries: list[dict[str, object]] = []
    for index, entry in enumerate(card_order):
        if not isinstance(entry, dict) or set(entry) != {"id", "kind", "dependsOn"}:
            errors.append(f"cardOrder[{index}]: campos esperados id, kind, dependsOn")
            continue
        card_id = entry.get("id")
        if not isinstance(card_id, str) or not card_id:
            errors.append(f"cardOrder[{index}].id inválido")
            continue
        if card_id in seen:
            errors.append(f"cardOrder: cartão duplicado {card_id}")
        if entry.get("kind") not in CARD_KINDS:
            errors.append(f"cardOrder: {card_id} com kind inválido")
        seen.append(card_id)
        entries.append(entry)
    if errors:
        return errors
    try:
        topological_cards(entries)
    except ContractError as exc:
        return [f"cardOrder: {exc}"]
    for position, entry in enumerate(entries):
        depends = entry.get("dependsOn")
        assert isinstance(depends, list)
        if any(seen.index(dep) > position for dep in depends):
            errors.append(f"cardOrder: {entry['id']} aparece antes de uma dependência")
    return errors


def lint_contract(contract: object, config: dict[str, object]) -> list[str]:
    """Validação canônica do contrato contra o schema e a configuração atual."""
    if not isinstance(contract, dict):
        return ["contrato precisa ser mapa"]
    errors: list[str] = []
    missing = TOP_FIELDS - contract.keys()
    if missing:
        errors.append(f"campos ausentes: {', '.join(sorted(missing))}")
    extra = contract.keys() - TOP_FIELDS
    if extra:
        errors.append(f"campos desconhecidos: {', '.join(sorted(map(str, extra)))}")
    if errors:
        return errors
    if contract.get("schemaVersion") != CONTRACT_VERSION:
        errors.append(f"schemaVersion: esperado {CONTRACT_VERSION}")
    feature = contract.get("feature")
    if not isinstance(feature, str) or not FEATURE_RE.fullmatch(feature):
        errors.append("feature inválida")
    sdd = contract.get("sdd")
    if not isinstance(sdd, dict) or set(sdd) != set(SDD_KINDS):
        errors.append("sdd: precisa ter exatamente prd, adr e rfc")
    else:
        for kind in SDD_KINDS:
            errors.extend(_ref_errors(sdd[kind], f"sdd.{kind}"))
    errors.extend(_ref_errors(contract.get("plan"), "plan"))
    errors.extend(_ref_errors(contract.get("cards"), "cards"))
    errors.extend(_card_order_errors(contract.get("cardOrder")))
    gates = contract.get("gates")
    if not isinstance(gates, dict) or any(not isinstance(value, bool) for value in gates.values()):
        errors.append("gates: mapa de booleanos obrigatório")
    if not isinstance(contract.get("configSha256"), str) or not SHA_RE.fullmatch(str(contract["configSha256"])):
        errors.append("configSha256 inválido")
    if contract.get("needsInput") != {"questionsFile": "questions.json", "planApproval": True}:
        errors.append("needsInput: esperado questionsFile=questions.json e planApproval=true")

    card_order = contract.get("cardOrder")
    known_cards = {
        str(entry.get("id")) for entry in card_order if isinstance(entry, dict)
    } if isinstance(card_order, list) else set()
    stages = contract.get("stages")
    if not isinstance(stages, list):
        return [*errors, "stages: lista obrigatória"]
    names = [entry.get("stage") if isinstance(entry, dict) else None for entry in stages]
    duplicated = sorted({str(name) for name in names if names.count(name) > 1})
    if duplicated:
        errors.append(f"stages: stage duplicada {', '.join(duplicated)}")
    unknown = sorted(str(name) for name in names if name not in CONTRACT_STAGES)
    if unknown:
        errors.append(f"stages: stage desconhecida {', '.join(unknown)}")
    if not duplicated and not unknown and tuple(names) != CONTRACT_STAGES:
        errors.append("stages: ordem/conjunto precisa ser " + ", ".join(CONTRACT_STAGES))
    commands = config.get("commands") if isinstance(config.get("commands"), dict) else {}
    assert isinstance(commands, dict)
    for index, entry in enumerate(stages):
        if not isinstance(entry, dict):
            errors.append(f"stages[{index}]: precisa ser mapa")
            continue
        label = f"stages.{entry.get('stage', index)}"
        if not STAGE_FIELDS <= set(entry) <= STAGE_FIELDS | OPTIONAL_STAGE_FIELDS:
            errors.append(f"{label}: campos esperados {', '.join(sorted(STAGE_FIELDS))}")
            continue
        role = entry.get("role")
        chain = entry.get("executorChain")
        enabled = entry.get("enabled", True)
        if type(enabled) is not bool:
            errors.append(f"{label}.enabled precisa ser boolean")
            enabled = True
        if "reactivateWhen" in entry and (
            enabled or not isinstance(entry["reactivateWhen"], str) or not entry["reactivateWhen"].strip()
        ):
            errors.append(f"{label}.reactivateWhen: texto não vazio, só com enabled: false")
        if not enabled:
            if entry.get("applicable") is not False:
                errors.append(f"{label}.applicable precisa ser false em stage desligada")
            if entry.get("stage") == CORE_STAGE:
                errors.append(f"{label}: {CORE_STAGE} é o núcleo do pipeline e não pode ser desligada")
        expected_chain: list[str] | None = None
        if enabled:
            try:
                expected_chain = executor_chain(config, str(role))
            except ContractError as exc:
                errors.append(f"{label}: {exc}")
        if expected_chain is not None and chain != expected_chain:
            errors.append(f"{label}.executorChain diverge de roles.{role} ({expected_chain})")
        if not enabled:
            if chain != [] or entry.get("maxAttempts") != 0 or entry.get("timeoutS") != 0:
                errors.append(f"{label}: stage desligada precisa ter executorChain vazio, maxAttempts 0 e timeoutS 0")
        elif not isinstance(chain, list) or not chain:
            errors.append(f"{label}.executorChain vazio")
        else:
            if entry.get("maxAttempts") != len(chain):
                errors.append(f"{label}.maxAttempts precisa ser {len(chain)} (tamanho da cadeia)")
            try:
                expected_timeout = worker_timeout(config, str(chain[0]))
                if entry.get("timeoutS") != expected_timeout:
                    errors.append(f"{label}.timeoutS diverge de workers.{chain[0]}.timeout_s ({expected_timeout})")
                for worker in chain[1:]:
                    worker_timeout(config, str(worker))
            except ContractError as exc:
                errors.append(f"{label}: {exc}")
        gate = entry.get("gate")
        if enabled and (not isinstance(gate, str) or not isinstance(commands.get(gate), str)):
            errors.append(f"{label}: commands.{gate} não configurado")
        errors.extend(_ref_errors(entry.get("request"), f"{label}.request"))
        allowed = entry.get("allowedPaths")
        stub_paths_value = entry.get("stubPaths", [])
        if not isinstance(stub_paths_value, list) or any(
            not safe_relative(path) or path not in (allowed if isinstance(allowed, list) else [])
            for path in stub_paths_value
        ):
            errors.append(f"{label}.stubPaths: caminho inseguro ou fora de allowedPaths")
        if not isinstance(allowed, list) or any(not safe_relative(path) for path in allowed):
            errors.append(f"{label}.allowedPaths: caminho inseguro")
        elif entry.get("applicable") is True and enabled and not allowed:
            errors.append(f"{label}.allowedPaths: vazio em stage aplicável")
        skills = entry.get("skills")
        if not isinstance(skills, list) or any(not isinstance(skill, str) or not skill for skill in skills):
            errors.append(f"{label}.skills inválido")
        if "stageSha256" in entry and (
            not isinstance(entry["stageSha256"], str) or not SHA_RE.fullmatch(entry["stageSha256"])
        ):
            errors.append(f"{label}.stageSha256 inválido")
        if entry.get("mode") not in {"read", "write"}:
            errors.append(f"{label}.mode inválido")
        if not isinstance(entry.get("applicable"), bool):
            errors.append(f"{label}.applicable precisa ser boolean")
        cards = entry.get("cards")
        if not isinstance(cards, list):
            errors.append(f"{label}.cards precisa ser lista")
        else:
            unknown_cards = [str(card) for card in cards if card not in known_cards]
            if unknown_cards:
                errors.append(f"{label}.cards: cartão desconhecido {', '.join(unknown_cards)}")
    errors.extend(_disabled_dependency_errors(stages, card_order))
    return errors


def _disabled_dependency_errors(stages: list[object], card_order: object) -> list[str]:
    """Stage ligada não pode depender (dependsOn) de cartão cuja stage está desligada: a saída não existiria."""
    if not isinstance(card_order, list):
        return []
    owner: dict[str, str] = {}
    disabled: set[str] = set()
    for entry in stages:
        if not isinstance(entry, dict) or not isinstance(entry.get("cards"), list):
            continue
        name = str(entry.get("stage"))
        if entry.get("enabled", True) is False:
            disabled.add(name)
        for card in entry["cards"]:
            owner[str(card)] = name
    errors: list[str] = []
    for card in card_order:
        if not isinstance(card, dict) or not isinstance(card.get("dependsOn"), list):
            continue
        stage = owner.get(str(card.get("id")))
        if stage is None or stage in disabled:
            continue
        for dep in card["dependsOn"]:
            if owner.get(str(dep)) in disabled:
                errors.append(
                    f"stages.{stage}: cartão {card.get('id')} depende de {dep}, da stage {owner[str(dep)]} "
                    "desligada (enabled: false); reative a stage ou remova a dependência"
                )
    return errors
