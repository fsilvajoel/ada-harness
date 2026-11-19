#!/usr/bin/env python3
"""Injeção automática de skills no pedido de quem não tem ferramentas (api-chat, cli-agent).

O harness, e não o orquestrador, resolve que skills cada agente recebe:

- skills do papel: `role_skills.<papel>` do `harness.project.yaml` (sempre);
- skills do cartão: campo opcional `skills` da tarefa, repassado em `--skills`.

Formato de cada item: `nome` (o `SKILL.md` da skill) ou `nome#regra` (o `SKILL.md`, que é o índice, mais
`<skill_dir>/<nome>/rules/<regra>.md`). Só `SKILL.md` e `rules/*.md` são resolvidos: o `AGENTS.md` compilado
das skills nunca entra. Repetição (mesmo arquivo) entra uma vez.

Teto: `skills_max_bytes` no `harness.project.yaml` (padrão `DEFAULT_MAX_BYTES`). Passou do teto = erro claro,
nada é truncado.

Uso direto (imprime o bloco `# Skills e regras` no stdout e o tamanho de cada recurso no stderr):
    skill_inject.py <raiz do projeto> [--role PAPEL] [--skills a,b#regra ...]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import Config, ConfigValue, load_config

# Teto padrão (bytes) do bloco injetado; configurável em `skills_max_bytes`.
DEFAULT_MAX_BYTES: Final = 40_000
SECTION_TITLE: Final = "# Skills e regras"
HEADING_PREFIX: Final = "skill"

_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class SkillError(Exception):
    """Pedido de skill inválido, inexistente ou acima do teto."""


@dataclass(frozen=True)
class Resource:
    """Um arquivo injetado: caminho de exibição (`skill/<nome>/...`), texto, bytes e linhas."""

    label: str
    text: str
    size_bytes: int
    lines: int


@dataclass(frozen=True)
class Injection:
    """Resultado: recursos na ordem de injeção e o bloco de texto pronto."""

    resources: tuple[Resource, ...]
    block: str
    max_bytes: int

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self.resources)

    def summary(self) -> list[dict[str, str | int]]:
        """Lista (por recurso) de caminho, bytes e linhas, para o `--meta`."""
        return [{"resource": r.label, "bytes": r.size_bytes, "lines": r.lines} for r in self.resources]

    def summary_lines(self) -> list[str]:
        rows = [f"  {r.label}: {r.size_bytes} bytes, {r.lines} linhas" for r in self.resources]
        rows.append(f"  total: {self.total_bytes} bytes de {self.max_bytes}")
        return rows


def _string_list(value: ConfigValue) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def role_skills(config: Config, role: str | None) -> list[str]:
    """Skills obrigatórias do papel (`role_skills.<papel>`); vazio se o papel não declara."""
    if role is None:
        return []
    table = config.get("role_skills")
    if not isinstance(table, dict):
        return []
    return _string_list(table.get(role))


def max_bytes(config: Config) -> int:
    value = config.get("skills_max_bytes")
    if value is None:
        return DEFAULT_MAX_BYTES
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise SkillError("skills_max_bytes precisa ser um inteiro positivo")
    try:
        number = int(value)
    except ValueError as exc:
        raise SkillError("skills_max_bytes precisa ser um inteiro positivo") from exc
    if number <= 0:
        raise SkillError("skills_max_bytes precisa ser um inteiro positivo")
    return number


def skill_dir(config: Config, project_root: Path) -> Path:
    """Pasta de skills: `skill_dir` do projeto (relativa à raiz dele) ou `skill/` do harness."""
    value = config.get("skill_dir")
    if isinstance(value, str) and value.strip():
        return (project_root / value.strip()).resolve()
    return (ROOT / "skill").resolve()


def parse_spec(spec: str) -> tuple[str, str | None]:
    """Divide `nome` ou `nome#regra`; valida os nomes (sem barra nem `..`)."""
    name, sep, rule = spec.strip().partition("#")
    if not _NAME.match(name):
        raise SkillError(f"item de skill inválido: '{spec}' (esperado 'nome' ou 'nome#regra')")
    if not sep:
        return name, None
    rule = rule.removesuffix(".md")
    if not _NAME.match(rule):
        raise SkillError(f"item de skill inválido: '{spec}' (regra depois do '#' é só o nome do arquivo)")
    return name, rule


def _read(path: Path, label: str) -> Resource:
    data = path.read_bytes()
    text = data.decode("utf-8", errors="replace")
    return Resource(label=label, text=text, size_bytes=len(data), lines=text.count("\n") + (not text.endswith("\n")))


def _resolve(base: Path, name: str, rule: str | None) -> list[tuple[Path, str]]:
    """Arquivos de um item: o `SKILL.md` e, se houver `#regra`, `rules/<regra>.md`."""
    folder = base / name
    index = folder / "SKILL.md"
    if not index.is_file():
        available = sorted(p.name for p in base.iterdir() if (p / "SKILL.md").is_file()) if base.is_dir() else []
        raise SkillError(
            f"skill inexistente: '{name}' ({HEADING_PREFIX}/{name}/SKILL.md não existe; "
            f"disponíveis: {', '.join(available) or 'nenhuma'})"
        )
    found: list[tuple[Path, str]] = [(index, f"{HEADING_PREFIX}/{name}/SKILL.md")]
    if rule is not None:
        rule_file = folder / "rules" / f"{rule}.md"
        if not rule_file.is_file():
            raise SkillError(f"regra inexistente: '{name}#{rule}' ({HEADING_PREFIX}/{name}/rules/{rule}.md não existe)")
        found.append((rule_file, f"{HEADING_PREFIX}/{name}/rules/{rule}.md"))
    return found


def build_injection(
    base: Path,
    specs: list[str],
    limit: int = DEFAULT_MAX_BYTES,
) -> Injection:
    """Resolve os itens (na ordem dada, sem repetir arquivo) e monta o bloco; passou do teto = erro."""
    base = base.resolve()
    seen: set[Path] = set()
    resources: list[Resource] = []
    for spec in specs:
        name, rule = parse_spec(spec)
        for path, label in _resolve(base, name, rule):
            real = path.resolve()
            if not real.is_relative_to(base):
                raise SkillError(f"item de skill fora da pasta de skills: '{spec}'")
            if real in seen:
                continue
            seen.add(real)
            resources.append(_read(real, label))
    injection = Injection(resources=tuple(resources), block=_render(resources), max_bytes=limit)
    if injection.total_bytes > limit:
        biggest = max(resources, key=lambda r: r.size_bytes)
        raise SkillError(
            f"skills injetadas somam {injection.total_bytes} bytes, acima do teto de {limit} "
            f"(skills_max_bytes); maior recurso: {biggest.label} ({biggest.size_bytes} bytes). "
            "Peça só as regras necessárias com nome#regra ou suba o teto."
        )
    return injection


def _render(resources: list[Resource]) -> str:
    if not resources:
        return ""
    parts = [SECTION_TITLE, ""]
    for item in resources:
        body = item.text if item.text.endswith("\n") else item.text + "\n"
        parts.append(f"=== RECURSO: {item.label} ===\n{body}=== END RECURSO ===\n")
    return "\n".join(parts)


def merge_specs(role_items: list[str], card_items: list[str]) -> list[str]:
    """Skills do papel primeiro, depois as do cartão; cada item textual aparece uma vez."""
    merged: list[str] = []
    for item in [*role_items, *card_items]:
        clean = item.strip()
        if clean and clean not in merged:
            merged.append(clean)
    return merged


def split_cli_items(values: list[str]) -> list[str]:
    """Aceita `--skills a,b` e `--skills c` (repetido): lista plana."""
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


def injection_for(project_root: Path, role: str | None, card_skills: list[str]) -> Injection:
    """Papel + cartão -> injeção, com a configuração do projeto."""
    try:
        config = load_config(project_root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        raise SkillError(f"harness.project.yaml inválido: {exc}") from exc
    specs = merge_specs(role_skills(config, role), card_skills)
    return build_injection(skill_dir(config, project_root), specs, max_bytes(config))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Imprime o bloco '# Skills e regras' do papel e do cartão.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--role", help="papel (usa role_skills.<papel> do harness.project.yaml)")
    parser.add_argument("--skills", action="append", default=[], help="nome ou nome#regra; separado por vírgula ou repetido")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        injection = injection_for(args.project_root.resolve(), args.role, split_cli_items(args.skills))
    except SkillError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(injection.block)
    print("\n".join(injection.summary_lines()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
