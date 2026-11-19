#!/usr/bin/env python3
"""Renderizador determinístico (sem IA) do corpo das issues de feature e de defeito.

Lê os docs do projeto (cartões, `plano.md`, PRD, ADRs, RFCs, `D<n>.md`) e o estado calculado por `status.py` e
gera um `.md` por feature e por defeito, com seções fixas em pt-BR: resumo e escopo, regras e critérios, contrato,
cartões (DoR e DoD), referências com link, telas e estado. O que já está em pt-BR (plano, PRD, defeitos) é copiado
sem marca. O texto em inglês dos cartões (título, `done`, `hints`) entra embrulhado em
`<!--t:HASH-->texto<!--/t-->` (HASH = sha256 curto do texto), para a etapa de tradução achar e cachear os blocos.

Links relativos de doc ou imagem que não existem fazem o comando falhar (saída 1, nada é gravado). Com `--repo-url`,
links e imagens viram links absolutos do repositório (`<url>/blob/<branch>/<caminho>`; imagem com `?raw=true`),
pois a API do `gh` não envia anexo. Corpo acima de 65000 caracteres é truncado, com aviso e link do doc.

Chave opcional do `harness.project.yaml`: `docs_dir` (padrão `docs`), com as pastas `adr`, `rfc`, `prd` e
`defeitos`; a pasta de cada feature é a pasta dos cartões (`cards_glob`) mais o ID da feature.

Uso: `render_issue.py <raiz do projeto> [--feature ID | --defeito Dn | --all] [--out pasta] [--repo-url URL]
[--branch NOME]`. Sem `--out`, um único alvo é impresso; `--all` exige `--out`. Saída: 0 gerou, 1 doc ou link
inválido, 2 configuração inválida.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import get_str, load_config
from script.status import STEP_LABELS, Feature, compute, load_cards, load_states

type Row = dict[str, object]

MAX_BODY = 65000
HASH_LEN = 10
IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"})
FEATURE_MARKER = "<!-- ada-feature:{id} -->"
DEFECT_MARKER = "<!-- ada-defeito:{id} -->"
FEATURE_DOCS: tuple[str, ...] = ("plano.md", "revisao.md", "testes.md", "auditoria.md", "revisao-final.md")
LINK_RE = re.compile(
    r"(?P<code>```.*?```|`[^`\n]+`)|(?P<bang>!?)\[(?P<text>[^\]\n]*)\]\((?P<target>[^)\s]+)\)", re.DOTALL
)
REF_RE = re.compile(r"^(?P<kind>ADR|RFC|PRD)-(?P<num>\d+)\b\s*(?P<rest>.*)$")
RULE_RE = re.compile(r"^(?:RF|RN|CA)-[\w-]+$")
HEADING_RE = re.compile(r"^## +(.+?) *$", re.MULTILINE)
TABLE_ROW_RE = re.compile(r"^\|\s*(?P<key>[^|]+?)\s*\|\s*(?P<value>.*?)\s*\|\s*$")


class RenderError(Exception):
    """Doc ausente, link quebrado ou configuração inválida."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Rendered:
    """Uma issue renderizada: tipo (`feature` ou `defeito`), ID, título, corpo e rótulos sugeridos."""

    kind: str
    id: str
    title: str
    body: str
    labels: tuple[str, ...] = ()


@dataclass
class Context:
    """Dados de uma execução: raiz, pastas dos docs, URL do repositório e problemas achados."""

    root: Path
    docs: Path
    plano: Path
    repo_url: str | None = None
    branch: str = "main"
    errors: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    def rel(self, path: Path) -> str:
        """Caminho de `path` relativo à raiz, em formato POSIX."""
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def url(self, rel: str, image: bool = False) -> str:
        """Link absoluto do repositório (ou o caminho relativo, sem `--repo-url`)."""
        if not self.repo_url:
            return rel
        link = f"{self.repo_url.rstrip('/')}/blob/{self.branch}/{quote(rel)}"
        return f"{link}?raw=true" if image else link


def short_hash(text: str) -> str:
    """sha256 curto do texto, usado no marcador de tradução."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_LEN]


def header(issue: "Rendered") -> str:
    """Linha `# título` do arquivo; no título de feature o texto em inglês vai em bloco de tradução."""
    if issue.kind == "feature":
        prefix, _, rest = issue.title.partition("] ")
        return f"# {prefix}] {mark(rest)}"
    return f"# {issue.title}"


def mark(text: str) -> str:
    """Embrulha texto em inglês no marcador de tradução `<!--t:HASH-->...<!--/t-->`."""
    clean = text.strip()
    return f"<!--t:{short_hash(clean)}-->{clean}<!--/t-->"


def _map(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def _strs(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def is_image(target: str) -> bool:
    """O alvo termina numa extensão de imagem?"""
    return Path(target.split("#", 1)[0].split("?", 1)[0]).suffix.lower() in IMAGE_EXTS


def process_links(ctx: Context, text: str, base: Path, origin: str) -> str:
    """Valida e reescreve os links relativos do texto; coleta imagens em `ctx.images`.

    Código (cercado ou em linha) não é tocado. Link relativo que não existe vira erro em `ctx.errors`.
    """

    def repl(match: re.Match[str]) -> str:
        if match.group("code") is not None:
            return match.group(0)
        target = match.group("target")
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|#)", target, re.IGNORECASE):
            return match.group(0)
        path_part, _, fragment = target.partition("#")
        path_part = path_part.split("?", 1)[0]
        resolved = (ctx.root / path_part.lstrip("/")) if path_part.startswith("/") else (base / path_part)
        resolved = resolved.resolve()
        if not resolved.exists():
            ctx.errors.append(f"{origin}: link quebrado '{target}'")
            return match.group(0)
        try:
            rel = ctx.rel(resolved)
        except ValueError:
            ctx.errors.append(f"{origin}: link fora da raiz do projeto '{target}'")
            return match.group(0)
        image = is_image(path_part)
        if image:
            ctx.images.append(f"{match.group('text')}\t{rel}")
        link = ctx.url(rel, image) + (f"#{fragment}" if fragment else "")
        return f"{match.group('bang')}[{match.group('text')}]({link})"

    return LINK_RE.sub(repl, text)


def sections(text: str) -> dict[str, str]:
    """Seções `## Título` de um markdown, por título em minúsculas (fora de blocos de código)."""
    cleaned = re.sub(r"```.*?```", lambda m: m.group(0).replace("\n## ", "\n⁣## "), text, flags=re.DOTALL)
    matches = list(HEADING_RE.finditer(cleaned))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        result.setdefault(match.group(1).strip().lower(), cleaned[match.end() : end].replace("⁣", "").strip())
    return result


def pick(found: dict[str, str], prefix: str) -> str:
    """Primeira seção cujo título começa com `prefix`."""
    return next((body for title, body in found.items() if title.startswith(prefix)), "")


def first_heading(text: str) -> str:
    """Texto do primeiro `# título`."""
    match = re.search(r"^# +(.+?) *$", text, re.MULTILINE)
    return match.group(1) if match else ""


def read_doc(ctx: Context, path: Path) -> str:
    """Lê um doc obrigatório; ausência vira erro."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        ctx.errors.append(f"doc ausente: {ctx.rel(path) if path.is_absolute() else path}")
        return ""


# ---------------------------------------------------------------- referências


def find_numbered(folder: Path, prefix: str, number: str) -> Path | None:
    """Doc `<prefix>-<número>*.md` (ou `<número>-*.md` para ADR) dentro de `folder`."""
    if not folder.is_dir():
        return None
    patterns = (f"{prefix}-{number}*.md", f"{number}-*.md") if prefix == "ADR" else (f"{prefix}-{number}*.md",)
    for pattern in patterns:
        hits = sorted(folder.glob(pattern))
        if hits:
            return hits[0]
    return None


def rule_text(ctx: Context, prd: Path, rule: str) -> str:
    """Texto da regra `rule` no PRD: item de lista (com continuação indentada) ou linha de tabela."""
    lines = prd.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if re.match(rf"^\s*[-*]\s+\*\*{re.escape(rule)}\*\*", line):
            block = [line.strip()]
            for follow in lines[index + 1 :]:
                if follow.startswith("  ") and follow.strip():
                    block.append(follow.strip())
                else:
                    break
            return "\n".join(block)
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and cells and cells[0] == rule:
            return f"- **{rule}** " + " — ".join(c for c in cells[1:] if c)
    return ""


@dataclass
class ResolvedRef:
    """Uma referência de cartão resolvida: texto do link, caminho relativo (se for arquivo) e regra citada."""

    label: str
    rel: str | None
    rule_block: str = ""
    external: str | None = None


def resolve_ref(ctx: Context, ref: str, origin: str) -> ResolvedRef | None:
    """Resolve `ADR-0013`, `RFC-005 §3`, `PRD-001 RF-01`, caminho de doc ou URL. Texto livre vira `None`."""
    text = ref.strip()
    if not text:
        return None
    if re.match(r"^https?://", text):
        return ResolvedRef(label=text, rel=None, external=text)
    match = REF_RE.match(text)
    if match:
        kind, number, rest = match.group("kind"), match.group("num"), match.group("rest").strip()
        folder = ctx.docs / kind.lower()
        doc = find_numbered(folder, kind, number)
        if doc is None:
            ctx.errors.append(f"{origin}: referência '{text}' sem doc em {ctx.rel(folder) if folder.exists() else folder}")
            return None
        block = ""
        if kind == "PRD" and RULE_RE.match(rest):
            block = rule_text(ctx, doc, rest)
            if not block:
                ctx.errors.append(f"{origin}: regra '{rest}' não está em {ctx.rel(doc)}")
        return ResolvedRef(label=text, rel=ctx.rel(doc), rule_block=block)
    if "/" in text and "." in text.rsplit("/", 1)[-1]:
        target = (ctx.root / text).resolve()
        if not target.exists():
            ctx.errors.append(f"{origin}: referência '{text}' não existe")
            return None
        return ResolvedRef(label=text, rel=ctx.rel(target))
    return None


def ref_link(ctx: Context, item: ResolvedRef) -> str:
    """Markdown da referência: link para o doc, ou texto quando o doc só tem caminho local."""
    if item.external is not None:
        return f"<{item.external}>"
    assert item.rel is not None
    return f"[{item.label}]({ctx.url(item.rel)})" if ctx.repo_url else f"{item.label} (`{item.rel}`)"


# ---------------------------------------------------------------- feature


def render_card(ctx: Context, card: Row, state: str, origin: str) -> tuple[str, list[ResolvedRef]]:
    """Bloco de um cartão: cabeçalho, DoR, DoD e dicas; devolve também as referências resolvidas."""
    cid = str(card.get("id"))
    refs = [r for r in (resolve_ref(ctx, str(x), f"{origin} {cid}") for x in _strs(card.get("refs"))) if r]
    deps = ", ".join(f"`{d}`" for d in _strs(card.get("dependsOn"))) or "nenhuma"
    lines = [
        f"### {cid} — {mark(str(card.get('title') or cid))}",
        "",
        f"- Tipo: `{card.get('kind')}` · Responsável: `{card.get('assignee') or '-'}` · Estado: {state}",
        "",
        "**DoR (pronto para começar)**",
        f"- Depende de: {deps}",
        "- Referências: " + (", ".join(ref_link(ctx, r) for r in refs) or "nenhuma"),
    ]
    read_first = _strs(card.get("readFirst"))
    if read_first:
        lines.append("- Ler antes: " + ", ".join(f"`{p}`" for p in read_first))
    skills = _strs(card.get("skills"))
    if skills:
        lines.append("- Skills: " + ", ".join(f"`{s}`" for s in skills))
    lines += ["", "**DoD (definição de pronto)**"]
    lines += [f"- {mark(item)}" for item in _strs(card.get("done"))] or ["- (sem itens)"]
    paths = _strs(card.get("allowedPaths"))
    lines.append("- Pode alterar: " + (", ".join(f"`{p}`" for p in paths) or "nada"))
    protected = _strs(card.get("protectedTests"))
    if protected:
        lines.append("- Testes protegidos: " + ", ".join(f"`{p}`" for p in protected))
    checks = _strs(card.get("checks"))
    if checks:
        lines += ["- Verificações:"] + [f"  - `{c}`" for c in checks]
    hints = _strs(card.get("hints"))
    if hints:
        lines += ["", "**Dicas**"] + [f"- {mark(h)}" for h in hints]
    return "\n".join(lines), refs


def describe_state(row: Row, card_states: dict[str, str], card_ids: list[str]) -> str:
    """Seção Estado: etapa, progresso, bloqueio e estado de cada cartão."""
    step = str(row.get("step"))
    block = _map(row.get("blocked")) if row.get("blocked") else {}
    lines = [
        f"- Etapa: {STEP_LABELS.get(step, step)}",
        f"- Progresso: {row.get('percent')}%",
        "- Bloqueio: " + (f"{block.get('status')}" + (f" ({block['message']})" if block.get("message") else "") if block else "não"),
    ]
    lines += [f"- `{cid}`: {card_states.get(cid, '-')}" for cid in card_ids]
    return "\n".join(lines)


def render_feature(
    ctx: Context,
    fid: str,
    row: Row,
    cards: dict[str, Row],
    card_states: dict[str, str],
) -> Rendered:
    """Corpo da issue de uma feature."""
    ctx.images = []
    folder = ctx.plano / fid
    plano_path = folder / "plano.md"
    plano_rel = ctx.rel(plano_path) if plano_path.is_file() else None
    found = sections(read_doc(ctx, plano_path)) if plano_rel else {}
    plan_base = folder
    escopo = process_links(ctx, pick(found, "escopo"), plan_base, f"{fid} plano.md") or "_Sem escopo no plano._"
    contrato = process_links(ctx, pick(found, "contrato"), plan_base, f"{fid} plano.md")
    criterios = process_links(ctx, pick(found, "critérios de aceite"), plan_base, f"{fid} plano.md")

    card_ids = _strs(row.get("cards"))
    card_blocks: list[str] = []
    ordered_refs: list[ResolvedRef] = []
    for cid in card_ids:
        card = cards.get(cid)
        if card is None:
            ctx.errors.append(f"{fid}: cartão {cid} listado na feature mas ausente dos cartões")
            continue
        block, refs = render_card(ctx, card, card_states.get(cid, "-"), fid)
        card_blocks.append(block)
        ordered_refs += refs

    seen: set[str] = set()
    ref_lines: list[str] = []
    rule_blocks: list[str] = []
    for item in ordered_refs:
        key = item.label
        if key in seen:
            continue
        seen.add(key)
        if item.rule_block:
            prd_dir = ctx.root / (item.rel or "")
            rule_blocks.append(process_links(ctx, item.rule_block, prd_dir.parent, f"{fid} {item.label}"))
        else:
            ref_lines.append(f"- {ref_link(ctx, item)}")
    for doc in FEATURE_DOCS:
        if (folder / doc).is_file():
            link = ctx.rel(folder / doc)
            ref_lines.append(f"- {doc}: " + (f"[{link}]({ctx.url(link)})" if ctx.repo_url else f"`{link}`"))
    prd_refs = sorted({item.rel for item in ordered_refs if item.rel and item.rel.startswith(ctx.rel(ctx.docs / "prd"))})
    for rel in prd_refs:
        ref_lines.insert(0, f"- PRD: [{rel}]({ctx.url(rel)})" if ctx.repo_url else f"- PRD: `{rel}`")

    for pattern in ("*.png", "*.jpg", "*.jpeg", "*.svg"):
        for image in sorted(folder.glob(pattern)):
            ctx.images.append(f"{image.stem}\t{ctx.rel(image)}")
    screens = unique_screens(ctx)

    rules = "\n\n".join(rule_blocks)
    if criterios:
        rules = (rules + "\n\n" if rules else "") + criterios
    delivery = str(row.get("delivery") or "")
    header = f"> Entrega **{delivery}** · Etapa: {STEP_LABELS.get(str(row.get('step')), row.get('step'))} · Progresso: {row.get('percent')}%"
    body = "\n\n".join(
        [
            FEATURE_MARKER.format(id=fid),
            header + "\n> Gerado dos docs do repositório; não edite à mão (o próximo sync sobrescreve).",
            "## Resumo e escopo\n\n" + escopo,
            "## Regras e critérios\n\n" + (rules or "_Sem regras citadas._"),
            "## Contrato\n\n" + (contrato or "_Sem contrato no plano._"),
            "## Cartões\n\n" + ("\n\n".join(card_blocks) or "_Sem cartões._"),
            "## Referências\n\n" + ("\n".join(ref_lines) or "_Nenhuma._"),
            "## Telas\n\n" + (screens or "_Sem telas._"),
            "## Estado\n\n" + describe_state(row, card_states, card_ids),
        ]
    )
    body = truncate(ctx, body, plano_rel)
    title = f"[{fid}] {row.get('title') or fid}"
    return Rendered("feature", fid, title, body + "\n", (f"entrega:{delivery}",) if delivery else ())


def unique_screens(ctx: Context) -> str:
    """Markdown das imagens coletadas (sem repetir o mesmo arquivo)."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in ctx.images:
        alt, _, rel = entry.partition("\t")
        if rel in seen:
            continue
        seen.add(rel)
        out.append(f"![{alt or Path(rel).stem}]({ctx.url(rel, True)})" if ctx.repo_url else f"- `{rel}`")
    return "\n\n".join(out)


def truncate(ctx: Context, body: str, doc_rel: str | None) -> str:
    """Corta o corpo acima de `MAX_BODY` caracteres, com aviso e link do doc."""
    if len(body) <= MAX_BODY:
        return body
    where = (f" [{doc_rel}]({ctx.url(doc_rel)})" if ctx.repo_url else f" `{doc_rel}`") if doc_rel else " nos docs do repositório"
    notice = f"\n\n> **Texto truncado** ({len(body)} caracteres; limite do GitHub: 65536). Leia o doc completo:{where}."
    cut = body[: MAX_BODY - len(notice)]
    cut = cut[: cut.rfind("\n")] if "\n" in cut else cut
    return cut + notice


# ---------------------------------------------------------------- defeito


def parse_table(text: str) -> dict[str, str]:
    """Linhas `| Campo | Valor |` do primeiro bloco de tabela."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = TABLE_ROW_RE.match(line)
        if match and match.group("key") not in {"Campo", "---"} and not set(match.group("key")) <= {"-", " "}:
            fields[match.group("key")] = match.group("value")
    return fields


def render_defect(ctx: Context, did: str, status_text: str | None = None) -> Rendered:
    """Corpo da issue de um defeito, de `docs/defeitos/<ID>.md`."""
    ctx.images = []
    path = ctx.docs / "defeitos" / f"{did}.md"
    text = read_doc(ctx, path)
    base = path.parent
    origin = f"{did}.md"
    fields = parse_table(text)
    found = sections(text)
    title = re.sub(rf"^{re.escape(did)}\s*[:—-]\s*", "", first_heading(text)) or did

    def copy(part: str) -> str:
        return process_links(ctx, part, base, origin)

    summary = "\n".join(f"- **{key}**: {copy(value)}" for key, value in fields.items()) or "_Sem tabela de campos._"
    rule_parts = [f"### {name}\n\n{copy(body)}" for name, body in (("Qual regra deixou passar?", pick(found, "qual regra")), ("Regra ajustada", pick(found, "regra ajustada"))) if body]
    decisions: list[str] = []
    for rel in re.findall(r"`(docs/[^`\s]+\.md)`", fields.get("Feature de origem", "")):
        target = ctx.root / rel
        if not target.is_file():
            ctx.errors.append(f"{origin}: doc de origem inexistente '{rel}'")
            continue
        decisions.append(f"- [{rel}]({ctx.url(rel)})" if ctx.repo_url else f"- `{rel}`")
    status = status_text or fields.get("Status", "-")
    body = "\n\n".join(
        [
            DEFECT_MARKER.format(id=did),
            f"> Defeito · Severidade: {fields.get('Severidade', '-')} · Status: {status}\n> Gerado dos docs do repositório; não edite à mão (o próximo sync sobrescreve).",
            "## Resumo\n\n" + summary,
            "## Reprodução\n\n" + (copy(pick(found, "reprodução")) or "_Sem reprodução._"),
            "## Regras e critérios\n\n" + ("\n\n".join(rule_parts) or "_Sem análise de regra._"),
            "## Correção e evidência\n\n" + (copy(fields.get("Correção", "")) or "_Sem correção registrada._"),
            "## Decisões e revisão de origem\n\n" + ("\n".join(decisions) or "_Nenhuma._"),
            "## Telas\n\n" + (unique_screens(ctx) or "_Sem telas._"),
            "## Estado\n\n- Status: " + status,
        ]
    )
    return Rendered("defeito", did, f"[{did}] {title}", truncate(ctx, body, ctx.rel(path)) + "\n", ("bug",))


# ---------------------------------------------------------------- montagem


def build_context(root: Path, repo_url: str | None = None, branch: str = "main") -> Context:
    """Contexto da execução a partir do `harness.project.yaml`."""
    config = load_config(root, required=True)
    glob = get_str(config, "cards_glob")
    if not glob:
        raise RenderError("harness.project.yaml sem 'cards_glob'", 2)
    docs = root / (get_str(config, "docs_dir") or "docs")
    plano = (root / glob).parent
    return Context(root=root.resolve(), docs=docs.resolve(), plano=plano.resolve(), repo_url=repo_url, branch=branch)


def defect_ids(ctx: Context) -> list[str]:
    """IDs `D<n>` dos arquivos de defeito, em ordem numérica."""
    folder = ctx.docs / "defeitos"
    ids = [p.stem for p in folder.glob("D*.md") if re.fullmatch(r"D\d+", p.stem)] if folder.is_dir() else []
    return sorted(ids, key=lambda item: int(item[1:]))


def render_selected(
    root: Path,
    feature: str | None = None,
    defect: str | None = None,
    everything: bool = False,
    repo_url: str | None = None,
    branch: str = "main",
) -> list[Rendered]:
    """Renderiza o alvo escolhido (ou tudo); levanta `RenderError` se algum doc ou link estiver inválido."""
    ctx = build_context(root, repo_url, branch)
    config = load_config(root, required=True)
    cards_glob = get_str(config, "cards_glob") or ""
    cards, features = load_cards(root, cards_glob)
    load_states(root, cards_glob, features)
    status = compute(root)
    rows = {str(_map(r)["id"]): _map(r) for r in status.get("features", []) if isinstance(r, dict)}  # type: ignore[attr-defined]
    card_states = {str(_map(c)["id"]): str(_map(c)["state"]) for c in status.get("cards", []) if isinstance(c, dict)}  # type: ignore[attr-defined]
    card_rows: dict[str, Row] = {cid: _map(card) for cid, card in cards.items()}

    if feature is not None and feature not in rows:
        raise RenderError(f"feature '{feature}' não existe", 2)
    if defect is not None and defect not in defect_ids(ctx):
        raise RenderError(f"defeito '{defect}' não existe", 2)
    feature_ids = sorted(rows) if everything else ([feature] if feature else [])
    defect_list = defect_ids(ctx) if everything else ([defect] if defect else [])

    result: list[Rendered] = []
    for fid in feature_ids:
        result.append(render_feature(ctx, fid, rows[fid], card_rows, card_states))
    for did in defect_list:
        result.append(render_defect(ctx, did))
    if ctx.errors:
        raise RenderError("\n".join(sorted(set(ctx.errors))))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Renderiza o corpo das issues de feature e defeito a partir dos docs.")
    parser.add_argument("root", type=Path)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--feature", default=None)
    target.add_argument("--defeito", default=None)
    target.add_argument("--all", action="store_true", dest="everything")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--repo-url", default=None, help="ex.: https://github.com/dono/repo")
    parser.add_argument("--branch", default="main")
    args = parser.parse_args(argv)
    everything: bool = args.everything or (args.feature is None and args.defeito is None)
    if everything and args.out is None:
        print("render_issue: --all exige --out <pasta>", file=sys.stderr)
        return 2
    try:
        issues = render_selected(args.root.resolve(), args.feature, args.defeito, everything, args.repo_url, args.branch)
    except RenderError as exc:
        print(f"render_issue: {exc}", file=sys.stderr)
        return exc.code
    except (OSError, ValueError) as exc:
        print(f"render_issue: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.out is None:
        for issue in issues:
            print(f"{header(issue)}\n\n{issue.body}")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    for issue in issues:
        (args.out / f"{issue.id}.md").write_text(f"{header(issue)}\n\n{issue.body}", encoding="utf-8")
    print(f"{len(issues)} issue(s) em {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
