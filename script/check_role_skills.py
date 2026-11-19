#!/usr/bin/env python3
"""Gate role-skills: `role_skills` do projeto e a seção "Skills obrigatórias" dos moldes de agente têm de coincidir.

Para cada papel em `roles.<papel>.agent` (caminho do molde, relativo à raiz do projeto), as skills listadas na
seção `## Skills obrigatórias` do molde (itens com o caminho de um `SKILL.md`, ou `rules/<regra>.md`) precisam ser
as mesmas de `role_skills.<papel>`, como conjunto (a ordem não conta). Papel com molde e sem `role_skills` conta
como lista vazia, e papel em `role_skills` sem molde (`agent` ausente ou nulo) é divergência.

Uso: `check_role_skills.py <raiz do projeto>`. Saída: uma linha por divergência; 0 passou, 2 falhou ou
configuração inválida.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import Config, ConfigValue, load_config

SECTION: Final = re.compile(r"^##\s+Skills obrigat[óo]rias\s*$", re.MULTILINE)
NEXT_SECTION: Final = re.compile(r"^##\s", re.MULTILINE)
BULLET: Final = re.compile(r"^\s*[-*]\s+`([^`]+)`", re.MULTILINE)
RULE_PATH: Final = re.compile(r"(?:^|/)([^/]+)/rules/([^/]+)\.md$")
INDEX_PATH: Final = re.compile(r"(?:^|/)([^/]+)/SKILL\.md$")


def template_skills(text: str) -> set[str] | None:
    """Itens (`nome` ou `nome#regra`) da seção de skills obrigatórias; None se a seção não existe."""
    start = SECTION.search(text)
    if start is None:
        return None
    rest = text[start.end() :]
    following = NEXT_SECTION.search(rest)
    body = rest[: following.start()] if following else rest
    found: set[str] = set()
    for match in BULLET.finditer(body):
        path = match.group(1).strip()
        rule = RULE_PATH.search(path)
        if rule is not None:
            found.add(f"{rule.group(1)}#{rule.group(2)}")
            continue
        index = INDEX_PATH.search(path)
        if index is not None:
            found.add(index.group(1))
    return found


def _items(value: ConfigValue) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()} if isinstance(value, str) and value.strip() else set()


def compare(config: Config, project_root: Path) -> list[str]:
    roles = config.get("roles")
    declared = config.get("role_skills")
    role_table = roles if isinstance(roles, dict) else {}
    skills_table = declared if isinstance(declared, dict) else {}
    problems: list[str] = []
    for role, entry in role_table.items():
        agent = entry.get("agent") if isinstance(entry, dict) else None
        if not isinstance(agent, str) or not agent.strip():
            continue
        path = project_root / agent.strip()
        if not path.is_file():
            problems.append(f"papel {role}: molde '{agent}' não existe")
            continue
        in_template = template_skills(path.read_text(encoding="utf-8"))
        in_config = _items(skills_table.get(role))
        if in_template is None:
            if in_config:
                problems.append(f"papel {role}: molde '{agent}' sem a seção 'Skills obrigatórias'")
            continue
        if in_template != in_config:
            only_template = ", ".join(sorted(in_template - in_config)) or "-"
            only_config = ", ".join(sorted(in_config - in_template)) or "-"
            problems.append(
                f"papel {role}: role_skills diverge do molde '{agent}' "
                f"(só no molde: {only_template}; só em role_skills: {only_config})"
            )
    for role in skills_table:
        entry = role_table.get(role)
        agent = entry.get("agent") if isinstance(entry, dict) else None
        if not isinstance(agent, str) or not agent.strip():
            problems.append(f"role_skills.{role} não tem molde (roles.{role}.agent)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate role-skills.")
    parser.add_argument("project_root", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    root = args.project_root.resolve()
    try:
        config = load_config(root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        print(f"erro: harness.project.yaml inválido: {exc}", file=sys.stderr)
        return 2
    problems = compare(config, root)
    if problems:
        print("Gate role-skills falhou.")
        for line in problems:
            print(f"- {line}")
        return 2
    print("Gate role-skills passou.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
