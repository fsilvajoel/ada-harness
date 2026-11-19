#!/usr/bin/env python3
"""Gate card-skills: cartão que toca código web precisa declarar `skills:`, e cada item precisa existir.

Configuração no `harness.project.yaml` do projeto:

- `cards_glob`: glob (relativo à raiz do projeto) dos arquivos de cartões (lista `cards`);
- `skills_required_paths`: prefixos de caminho que exigem `skills:` (ex.: o app web). Cartão cujo `allowedPaths`
  tem algum caminho igual ou dentro de um prefixo precisa de `skills:` não vazio;
- `skill_dir`: pasta das skills (padrão: `skill/` do harness).

Todo cartão com `skills:` (exigido ou não) tem cada item `nome` ou `nome#regra` validado contra `skill_dir`:
`<nome>/SKILL.md` e, com regra, `<nome>/rules/<regra>.md`.

Uso: `check_card_skills.py <raiz do projeto>`. Saída: uma linha por problema; 0 passou, 1 falhou, 2 configuração
ou arquivo de cartões inválido.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import get_str, get_str_list, load_config
from script.skill_inject import SkillError, parse_spec, skill_dir


class ConfigProblem(Exception):
    """Configuração ou arquivo de cartões inválido (saída 2)."""


def touches(paths: list[str], prefixes: list[str]) -> bool:
    """True se algum caminho é igual a um prefixo ou está dentro dele."""
    for path in paths:
        clean = path.strip().lstrip("./")
        for prefix in prefixes:
            base = prefix.strip().strip("/")
            if clean == base or clean.startswith(base + "/"):
                return True
    return False


def _strings(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def spec_problem(base: Path, spec: str) -> str | None:
    """Motivo pelo qual o item não resolve em `base`, ou None se existe."""
    try:
        name, rule = parse_spec(spec)
    except SkillError as exc:
        return str(exc)
    if not (base / name / "SKILL.md").is_file():
        return f"skill inexistente: '{name}'"
    if rule is not None and not (base / name / "rules" / f"{rule}.md").is_file():
        return f"regra inexistente: '{name}#{rule}'"
    return None


def check_file(path: Path, label: str, prefixes: list[str], base: Path) -> list[str]:
    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigProblem(f"{label}: ilegível ({type(exc).__name__})") from exc
    cards = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(cards, list):
        raise ConfigProblem(f"{label}: sem lista 'cards'")
    problems: list[str] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        card_id = str(card.get("id", "?"))
        skills = _strings(card.get("skills"))
        if touches(_strings(card.get("allowedPaths")), prefixes) and not skills:
            problems.append(f"{label}: cartão {card_id} toca {', '.join(prefixes)} e não declara 'skills:'")
        for spec in skills:
            reason = spec_problem(base, spec)
            if reason is not None:
                problems.append(f"{label}: cartão {card_id}: {reason}")
    return problems


def run(project_root: Path) -> list[str]:
    try:
        config = load_config(project_root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        raise ConfigProblem(f"harness.project.yaml inválido: {exc}") from exc
    pattern = get_str(config, "cards_glob")
    prefixes = get_str_list(config, "skills_required_paths")
    if pattern is None or not prefixes:
        raise ConfigProblem("harness.project.yaml precisa de cards_glob e skills_required_paths")
    files = sorted(project_root.glob(pattern))
    if not files:
        raise ConfigProblem(f"cards_glob '{pattern}' não casa nenhum arquivo")
    base = skill_dir(config, project_root)
    problems: list[str] = []
    for file in files:
        problems.extend(check_file(file, str(file.relative_to(project_root)), prefixes, base))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate card-skills.")
    parser.add_argument("project_root", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        problems = run(args.project_root.resolve())
    except ConfigProblem as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("Gate card-skills falhou.")
        for line in problems:
            print(f"- {line}")
        return 2
    print("Gate card-skills passou.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
