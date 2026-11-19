#!/usr/bin/env python3
"""Laço autônomo de um cartão para trabalhadores api-chat e api-tools (`adapter/README.md`): o worker se corrige sozinho.

Uso:
    run_card.py <raiz do projeto> --card ID [--max-attempts N] [--dry-run] [--keep-on-fail] [--log-dir PASTA]
                [--cards-file ARQ] [--check-timeout S] [--max-output N]   (veja --help)

Por tentativa: monta o pedido, chama `script/dispatch.py`, valida a resposta (formato do adaptador), grava os
arquivos, roda o formatador do projeto e os `checks` do cartão. Falha (resposta rejeitada ou check FAIL) vira
feedback do pedido seguinte. Sem Claude no meio: o orquestrador só lê o resultado curto no stdout.
Pedidos, respostas e saídas completas ficam no `--log-dir`.

Papel e etapa pelo `kind`: `test-author` -> `test-writer`, etapa `tests`; `implement` -> `implementer`, etapa
`implement`. Todo despacho leva `--stage` e um `--allowed-path` por item de `allowedPaths`, então
`roles.<papel>.worker_stages` e `worker_paths` valem aqui como no dispatch. Se a rota manda o papel para o agent
(dispatch sairia 8), o script termina com 2 e não roda: o cartão não cabe num worker.

Adaptador do worker (descoberto pela mesma rota do dispatch):
- api-chat (sem ferramentas): o script faz parse dos blocos `=== FILE ===`, grava, formata e roda os checks.
- api-tools (com ferramentas; papel de escrita `implementer`): o worker grava os arquivos sozinho dentro de
  `allowedPaths`. O script só confere que nada fora do escopo mudou (senão restaura tudo e devolve como erro),
  formata e roda os checks; na nova tentativa reenvia o pedido com o erro (o worker lê os arquivos pelo disco).

Não grava `estado.json`, não faz commit, não roda gates do harness: isso segue com o orquestrador.

Códigos de saída: 0 PASS; 1 FAIL (tentativas esgotadas); 2 uso ou configuração; 3 cota esgotada (ou 429);
4 falha de rede/worker repetida. Em qualquer saída diferente de 0 (ou Ctrl-C/SIGTERM) os arquivos voltam ao estado
anterior, salvo `--keep-on-fail` no FAIL. Um travamento (flock) por projeto+cartão impede duas execuções juntas.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # type: ignore[import-untyped]

from script.dispatch import WRITE_ROLES, DispatchError, default_system, resolve_worker
from script.project_config import load_config

EXIT_PASS: Final = 0
EXIT_FAIL: Final = 1
EXIT_USAGE: Final = 2
EXIT_QUOTA: Final = 3
EXIT_WORKER: Final = 4

# Códigos do api_chat.py (via dispatch.py).
API_NO_KEY: Final = 3
API_QUOTA: Final = 4
API_RATE_LIMIT: Final = 5

ROLE_BY_KIND: Final[dict[str, str]] = {"test-author": "test-writer", "implement": "implementer"}
# Nomes de etapa de `stages.<etapa>` do estado.json (workflow/feature-artefatos/estado.md); o `worker_stages` os usa.
STAGE_BY_KIND: Final[dict[str, str]] = {"test-author": "tests", "implement": "implement"}
ADAPTERS: Final = ("api-chat", "api-tools")
SNAPSHOT_FILE_LIMIT: Final = 5_000_000  # backup em memória por arquivo (acima disso, não restaura)
SNAPSHOT_TOTAL_LIMIT: Final = 200_000_000
SCAN_SKIP_DIRS: Final = frozenset({".git", "node_modules", ".venv", "__pycache__", ".turbo", ".next", "dist", "coverage"})
LOCK_DIR: Final = Path(tempfile.gettempdir()) / "run-card-locks"
EXIT_INTERRUPT: Final = 130
DEFAULT_ATTEMPTS: Final = 6
SAME_ERROR_LIMIT: Final = 3
WORKER_FAIL_LIMIT: Final = 3
HEAD_LINES: Final = 20
FINAL_TAIL_LINES: Final = 15

FIX_INSTRUCTION: Final = (
    "Corrija só o que o erro indica e devolva o arquivo inteiro de novo, no mesmo formato "
    "(`=== FILE: <caminho> ===` ... `=== END FILE ===`, sem cercas de código)."
)
FIX_TOOLS_INSTRUCTION: Final = (
    "Corrija só o que o erro indica: leia os arquivos atuais com read_file e regrave com write_file, "
    "só dentro dos caminhos permitidos. Termine com finish."
)
REPEAT_WARNING: Final = "O mesmo erro persiste; mude a abordagem, não repita o trecho anterior."

TEST_WRITER_SYSTEM_HEAD: Final = """\
Você é o papel test-writer de um harness de desenvolvimento. Você não tem ferramentas: recebe texto e devolve texto.
Sua tarefa: escrever os arquivos de teste do cartão, seguindo o plano, os critérios do cartão e as regras do projeto.

Regras:
- Escreva só os arquivos listados em "Arquivos que você pode escrever". Nunca escreva código de produção.
- Os valores esperados vêm do plano e do cartão; nunca os calcule no teste.
- Menor mudança correta; nada além dos critérios do cartão; nenhuma dependência nova.
- Devolva cada arquivo inteiro (não diff, não trecho), no formato abaixo, sem cercas de código (```) dentro do bloco.

"""
FORMAT_ANCHOR: Final = "Formato de saída (obrigatório):"

ANSI: Final = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
TIMING: Final = re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|m)\b|\b\d{1,2}:\d{2}:\d{2}\b")


class RunCardError(Exception):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class Card:
    id: str
    kind: str
    role: str
    data: dict[str, object]
    allowed: list[str]
    protected: list[str]
    read_first: list[str]
    checks: list[str]
    skills: list[str]
    budget_attempts: int | None
    feature: str | None
    source_file: Path
    stage: str = ""


@dataclass
class Attempt:
    number: int
    ok: bool
    kind: str  # pass | rejected | check-failed | worker-failed
    detail: str  # motivo da rejeição ou nome do check
    output: str = ""
    written: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- cartão


def _str_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def load_card(root: Path, card_id: str, cards_file: Path | None) -> Card:
    files = [cards_file] if cards_file else sorted((root / "docs" / "plano").glob("*.yaml"))
    for path in files:
        try:
            doc: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            if cards_file:
                raise RunCardError(EXIT_USAGE, f"não consegui ler {path}: {exc}") from exc
            continue
        cards = doc.get("cards") if isinstance(doc, dict) else None
        for item in cards if isinstance(cards, list) else []:
            if isinstance(item, dict) and str(item.get("id")) == card_id:
                return _build_card(path, doc, item)
    raise RunCardError(EXIT_USAGE, f"cartão {card_id} não encontrado nos YAMLs de cartões")


def _build_card(path: Path, doc: dict[str, object], item: dict[str, object]) -> Card:
    kind = str(item.get("kind", ""))
    role = ROLE_BY_KIND.get(kind)
    if role is None:
        raise RunCardError(EXIT_USAGE, f"kind '{kind}' sem worker de api-chat (só {', '.join(ROLE_BY_KIND)})")
    allowed = _str_list(item.get("allowedPaths"))
    if not allowed:
        raise RunCardError(EXIT_USAGE, "cartão sem allowedPaths")
    budget = item.get("budget")
    attempts = budget.get("attempts") if isinstance(budget, dict) else None
    feature = doc.get("delivery") or doc.get("feature")
    return Card(
        id=str(item["id"]),
        kind=kind,
        role=role,
        data=item,
        allowed=allowed,
        protected=_str_list(item.get("protectedTests")),
        read_first=_str_list(item.get("readFirst")),
        checks=_str_list(item.get("checks")),
        skills=_str_list(item.get("skills")),
        budget_attempts=attempts if isinstance(attempts, int) and attempts > 0 else None,
        feature=str(feature) if feature else path.stem,
        source_file=path,
        stage=STAGE_BY_KIND[kind],
    )


def safe_path(root: Path, rel: str) -> Path:
    """Caminho relativo dentro da raiz; rejeita absoluto, `..` que escapa e link simbólico que aponta para fora."""
    if not rel.strip() or Path(rel).is_absolute():
        raise RunCardError(EXIT_USAGE, f"caminho inválido (vazio ou absoluto): {rel!r}")
    path = (root / rel).resolve()
    if root.resolve() not in path.parents:
        raise RunCardError(EXIT_USAGE, f"caminho fora da raiz do projeto (.., absoluto ou link simbólico): {rel}")
    if path.is_dir():
        raise RunCardError(EXIT_USAGE, f"caminho é uma pasta, não um arquivo: {rel}")
    return path


def find_plan(root: Path, card: Card) -> Path | None:
    for name in dict.fromkeys([card.feature or "", card.source_file.stem]):
        candidate = root / "docs" / "plano" / name / "plano.md"
        if name and candidate.is_file():
            return candidate
    return None


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- pedido


def file_block(label: str, rel: str, content: str, end: str) -> str:
    return f"=== {label}: {rel} ===\n{content.rstrip(chr(10))}\n=== {end} ===\n"


def current_blocks(root: Path, paths: list[str]) -> str:
    parts: list[str] = []
    for rel in paths:
        path = safe_path(root, rel)
        content = read_text(path) if path.is_file() else "(arquivo novo)"
        parts.append(file_block("FILE", rel, content, "END FILE"))
    return "\n".join(parts)


def role_context(config: dict[str, object], role: str) -> list[str]:
    section = config.get("role_context")
    return _str_list(section.get(role)) if isinstance(section, dict) else []


CARD_DROP: Final = ("assignee", "dependsOn", "budget", "protectedTests", "readFirst", "skills")


def card_yaml(card: Card) -> str:
    """Cartão sem o que não ajuda a Bia (responsável, dependências, orçamento, listas já enviadas à parte)."""
    data = {k: v for k, v in card.data.items() if k not in CARD_DROP and v not in (None, [], {}, "")}
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000)


def build_head(root: Path, card: Card, config: dict[str, object]) -> str:
    """Parte fixa do pedido (igual em todas as tentativas): cartão, plano, leitura, testes assinados."""
    out = ["# Cartão\n" + card_yaml(card)]
    context = list(dict.fromkeys(card.read_first + role_context(config, card.role)))
    plan = find_plan(root, card)
    if plan is not None and str(plan.relative_to(root)) not in context:
        out.append(f"# Plano ({plan.relative_to(root)})\n{read_text(plan)}")
    if context:
        parts = []
        for rel in context:
            path = safe_path(root, rel)
            if path.is_file():
                parts.append(file_block("RECURSO", rel, read_text(path), "END RECURSO"))
        out.append("# Leia primeiro\n" + "\n".join(parts))
    if card.protected:
        parts = []
        for rel in card.protected:
            path = safe_path(root, rel)
            if path.is_file():
                parts.append(file_block("TEST", rel, read_text(path), "END TEST"))
        out.append("# Testes assinados (somente leitura)\n" + "\n".join(parts))
    return "\n".join(out)


def build_request(
    root: Path, card: Card, head: str, number: int, max_attempts: int, last: Attempt | None, warn_repeat: bool, tools: bool = False
) -> str:
    """Pedido da tentativa `number`: tamanho do original + arquivos atuais + saída do último check (não acumula).

    Com `tools` (api-tools) não vai o conteúdo dos arquivos: o worker os lê pelo disco e grava com write_file."""
    out = [f"# Tarefa\nCartão {card.id} ({card.kind}), papel {card.role}. Tentativa {number} de {max_attempts}.\n", head]
    if tools:
        listed = "\n".join(f"- {rel}" for rel in card.allowed)
        out.append(
            "# Arquivos que você pode escrever\nGrave com write_file (arquivo inteiro), só nestes caminhos; "
            "leia o conteúdo atual com read_file.\n" + listed
        )
    else:
        out.append("# Arquivos que você pode escrever (conteúdo atual)\n" + current_blocks(root, card.allowed))
    if last is not None:
        if last.kind == "rejected":
            note = "Os arquivos voltaram ao estado original. " if tools else "Nada foi gravado. "
            out.append(f"# Falha da tentativa anterior: resposta rejeitada\n{note}Motivo: {last.detail}")
        elif last.kind == "worker-failed":
            out.append(f"# Falha da tentativa anterior: o worker falhou\n{last.detail}")
        else:
            out.append(f"# Falha da tentativa anterior: check `{last.detail}`\n{last.output}")
        out.append("# Instrução\n" + (FIX_TOOLS_INSTRUCTION if tools else FIX_INSTRUCTION))
        if warn_repeat:
            out.append(REPEAT_WARNING)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- resposta


def parse_response(text: str, allowed: list[str]) -> dict[str, str]:
    """Blocos `=== FILE: ... ===`; `ValueError` com a causa curta se a resposta toda deve ser rejeitada."""
    lines = text.splitlines()
    blocks: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    header = re.compile(r"^=== FILE: (.+?) ===\s*$")
    for line in lines:
        match = header.match(line)
        if current is None:
            if match is None:
                continue
            current, body = match.group(1).strip(), []
            if current not in allowed:
                raise ValueError(f"caminho fora de allowedPaths: {current}; permitidos: {', '.join(allowed)}")
            if current in blocks:
                raise ValueError(f"caminho repetido: {current}")
        elif line.strip() == "=== END FILE ===":
            content = "\n".join(body)
            if not content.strip():
                raise ValueError(f"bloco vazio: {current}")
            if content.lstrip().startswith("```") or content.rstrip().endswith("```"):
                raise ValueError(f"cerca de código (```) dentro do bloco de {current}; devolva só o conteúdo do arquivo")
            blocks[current] = content + "\n"
            current = None
        elif match is not None:
            raise ValueError(f"bloco de {current} não foi fechado com '=== END FILE ===' antes do próximo bloco")
        else:
            body.append(line)
    if current is not None:
        raise ValueError(f"bloco de {current} sem '=== END FILE ===' (resposta cortada?)")
    if not blocks:
        raise ValueError("nenhum bloco '=== FILE: <caminho> ===' na resposta")
    return blocks


# ---------------------------------------------------------------- execução


LAST_STDERR = ""


def run_dispatch(command: list[str]) -> int:
    """Chama o despachante (ponto único que os testes substituem). O stderr (`dispatch: rota ...`) fica em LAST_STDERR."""
    global LAST_STDERR
    done = subprocess.run(command, check=False, capture_output=True, text=True)
    LAST_STDERR = done.stderr
    return done.returncode


def _kill_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_shell(command: str, cwd: Path, timeout: float) -> tuple[int, str]:
    """Roda no shell, em grupo próprio; estouro de tempo mata o grupo. Devolve (código, stdout+stderr)."""
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, _ = proc.communicate()
        return 124, f"{out or ''}\n[TIMEOUT: passou de {timeout:g}s]"
    except BaseException:  # Ctrl-C, SIGTERM: não deixa o check órfão
        _kill_group(proc)
        raise
    return proc.returncode, out or ""


def trim_output(text: str, limit: int) -> str:
    """Tira ANSI e, acima de `limit`, mantém as primeiras linhas do erro e o final."""
    text = ANSI.sub("", text).strip("\n")
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    head: list[str] = []
    size = 0
    for line in lines[:HEAD_LINES]:
        if size + len(line) + 1 > limit // 4:
            break
        head.append(line)
        size += len(line) + 1
    tail: list[str] = []
    room = limit - size
    for line in reversed(lines[len(head) :]):
        if room - len(line) - 1 < 0:
            break
        tail.append(line)
        room -= len(line) + 1
    tail.reverse()
    skipped = len(lines) - len(head) - len(tail)
    return "\n".join([*head, f"[... {skipped} linhas omitidas ...]", *tail])


def run_checks(root: Path, checks: list[str], timeout: float, limit: int, log: Path) -> tuple[str, str] | None:
    """Roda em ordem; devolve (comando, saída aparada) do primeiro que falha, ou None. `!` nega o resultado."""
    for index, raw in enumerate(checks):
        negate = raw.lstrip().startswith("!")
        command = raw.lstrip()[1:].lstrip() if negate else raw
        code, out = run_shell(command, root, timeout)
        (log / f"check-{index}.log").write_text(f"$ {raw}\n[exit {code}]\n{out}", encoding="utf-8")
        passed = code != 0 if negate and code != 124 else code == 0
        if not passed:
            if negate and code == 0:
                out = f"{out}\n[o check começa com '!' e deveria FALHAR, mas passou (exit 0)]"
            return raw, trim_output(out, limit)
    return None


def format_command(config: dict[str, object], files: list[str]) -> str | None:
    section = config.get("commands")
    template = section.get("format") if isinstance(section, dict) else None
    if not isinstance(template, str) or not template.strip():
        return None
    template = template.replace("--check", "--write")
    quoted = " ".join(shlex.quote(f) for f in files)
    command = template.replace("<arquivos>", quoted) if "<arquivos>" in template else f"{template} {quoted}"
    return None if re.search(r"<[a-z-]+>", command) else command


def signature(attempt: Attempt) -> str:
    base = attempt.detail if attempt.kind == "rejected" else f"{attempt.detail}\n{attempt.output}"
    return hashlib.sha256(TIMING.sub("", base).encode("utf-8")).hexdigest()


def tail_lines(text: str, count: int) -> str:
    return "\n".join(text.splitlines()[-count:])


# ---------------------------------------------------------------- laço


def write_atomic(path: Path, data: bytes) -> None:
    """Grava em arquivo temporário vizinho e troca (sem arquivo parcial se for interrompido)."""
    tmp = path.with_name(f".{path.name}.run-card-tmp")
    tmp.write_bytes(data)
    if path.exists():
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


class Snapshot:
    """Cópias dos arquivos antes da primeira gravação; `restore` desfaz tudo (apaga os criados e as pastas novas)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.before: dict[str, bytes | None] = {}
        self.new_dirs: list[Path] = []

    def remember(self, rel: str) -> None:
        if rel in self.before:
            return
        path = self.root / rel
        self.before[rel] = path.read_bytes() if path.is_file() else None
        missing = [p for p in path.parents if not p.exists() and self.root in p.parents]
        self.new_dirs.extend(d for d in missing if d not in self.new_dirs)

    def restore(self) -> None:
        for rel, content in self.before.items():
            path = self.root / rel
            if content is None:
                path.unlink(missing_ok=True)
            else:
                write_atomic(path, content)
            path.with_name(f".{path.name}.run-card-tmp").unlink(missing_ok=True)
        for directory in sorted(self.new_dirs, key=lambda d: len(d.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass


def system_for(role: str, tmp: Path, adapter: str = "api-chat") -> Path | None:
    """Implementer usa o system padrão do adaptador (dispatch); test-writer (api-chat) ganha o dele, com o mesmo formato."""
    if role != "test-writer" or adapter != "api-chat":
        return None
    _, _, fmt = default_system().partition(FORMAT_ANCHOR)
    path = tmp / "system.txt"
    path.write_text(TEST_WRITER_SYSTEM_HEAD + FORMAT_ANCHOR + fmt, encoding="utf-8")
    return path


def dispatch_command(root: Path, card: Card, request: Path, output: Path, meta: Path, system: Path | None) -> list[str]:
    command = [sys.executable, str(ROOT / "script" / "dispatch.py"), str(root), "--role", card.role, "--stage", card.stage]
    for rel in card.allowed:
        command += ["--allowed-path", rel]
    for skill in card.skills:
        command += ["--skills", skill]
    command += ["--request", str(request), "--output", str(output), "--meta", str(meta)]
    if system is not None:
        command += ["--system", str(system)]
    return command


def resolve_log_dir(root: Path, card_id: str, given: Path | None) -> Path:
    path = given if given is not None else Path(tempfile.gettempdir()) / "run-card" / f"{root.name}-{card_id}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        for old in path.glob("attempt-*"):
            shutil.rmtree(old, ignore_errors=True)
        probe = path / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RunCardError(EXIT_USAGE, f"--log-dir inutilizável ({path}): {exc}") from exc
    return path


def acquire_lock(root: Path, card_id: str) -> int:
    """Um flock por projeto+cartão fora do git (em /tmp); devolve o descritor (vive até o fim do processo)."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(str(root).encode()).hexdigest()[:12] + "-" + re.sub(r"[^\w.-]", "_", card_id)
    fd = os.open(LOCK_DIR / f"{name}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise RunCardError(EXIT_USAGE, f"o cartão {card_id} já está rodando neste projeto (lock {LOCK_DIR / name}.lock)") from exc
    return fd


def read_meta(path: Path) -> dict[str, object]:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def in_scope(rel: str, allowed: list[str]) -> bool:
    return any(rel == item or rel.startswith(item.rstrip("/") + "/") for item in allowed)


FileSig = tuple[int, int, int]  # tamanho, mtime_ns, modo


class ProjectSnapshot:
    """api-tools: o worker grava sozinho, então vigiamos o projeto todo (arquivos do git, ou a árvore sem pastas pesadas).

    Guarda assinatura (tamanho, mtime, modo) e cópia dos bytes do estado inicial, para restaurar tudo no fim."""

    def __init__(self, root: Path, exclude: list[Path]) -> None:
        self.root = root.resolve()
        self.exclude = [p.resolve() for p in exclude]
        self.initial = self.scan()
        self.backup: dict[str, bytes] = {}
        self.unrestorable: list[str] = []
        total = 0
        for rel, sig in self.initial.items():
            if sig[0] > SNAPSHOT_FILE_LIMIT or total + sig[0] > SNAPSHOT_TOTAL_LIMIT:
                self.unrestorable.append(rel)
                continue
            try:
                self.backup[rel] = (self.root / rel).read_bytes()
                total += sig[0]
            except OSError:
                self.unrestorable.append(rel)

    def _listing(self) -> list[str]:
        done = subprocess.run(
            ["git", "-C", str(self.root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            check=False,
            capture_output=True,
        )
        if done.returncode == 0 and (self.root / ".git").exists():
            return [name for name in done.stdout.decode("utf-8", errors="replace").split("\0") if name]
        found: list[str] = []
        for base, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in SCAN_SKIP_DIRS]
            found += [str((Path(base) / name).relative_to(self.root)) for name in files]
        return found

    def scan(self) -> dict[str, FileSig]:
        sigs: dict[str, FileSig] = {}
        for rel in self._listing():
            path = self.root / rel
            if any(path == ex or ex in path.parents for ex in self.exclude) or path.name.endswith(".run-card-tmp"):
                continue
            try:
                stat = path.lstat()
            except OSError:
                continue
            sigs[rel] = (stat.st_size, stat.st_mtime_ns, stat.st_mode)
        return sigs

    @staticmethod
    def diff(before: dict[str, FileSig], after: dict[str, FileSig]) -> list[str]:
        return sorted(rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel))

    def restore_all(self) -> list[str]:
        """Devolve tudo ao estado inicial; devolve os caminhos que não deu para restaurar (arquivo grande demais)."""
        failed: list[str] = []
        for rel in self.diff(self.initial, self.scan()):
            path = self.root / rel
            if rel in self.initial:
                if rel in self.backup:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.unlink(missing_ok=True)
                    write_atomic(path, self.backup[rel])
                    path.chmod(self.initial[rel][2] & 0o7777)
                else:
                    failed.append(rel)
            else:
                path.unlink(missing_ok=True)
                parent = path.parent
                while parent != self.root:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
        return failed


def evaluate_tools(
    root: Path,
    card: Card,
    code: int,
    meta: dict[str, object],
    before: dict[str, FileSig],
    snap: ProjectSnapshot,
    adir: Path,
    config: dict[str, object],
    timeout: float,
    limit: int,
    number: int,
) -> Attempt:
    """api-tools: o worker já gravou; confere o escopo, formata e roda os checks (sem parse de blocos)."""
    changed = snap.diff(before, snap.scan())
    outside = [rel for rel in changed if not in_scope(rel, card.allowed)]
    if outside:
        snap.restore_all()
        return Attempt(
            number,
            False,
            "rejected",
            f"o worker mudou arquivos fora de allowedPaths ({', '.join(outside[:10])}); permitidos: {', '.join(card.allowed)}",
        )
    if code != 0:
        return Attempt(number, False, "worker-failed", f"api_tools saiu {code}: {meta.get('error') or 'sem resposta'}")
    written = [rel for rel in changed if (root / rel).is_file()]
    if not changed:
        return Attempt(number, False, "rejected", "o worker não gravou nenhum arquivo em allowedPaths (use write_file)")
    fmt = format_command(config, written)
    if fmt is not None and written:
        fcode, fout = run_shell(fmt, root, timeout)
        (adir / "format.log").write_text(f"$ {fmt}\n[exit {fcode}]\n{fout}", encoding="utf-8")
    failed = run_checks(root, card.checks, timeout, limit, adir)
    if failed is None:
        return Attempt(number, True, "pass", "", written=written)
    return Attempt(number, False, "check-failed", failed[0], failed[1], written)


def route_for(root: Path, card: Card) -> tuple[str, str]:
    """Mesma regra do dispatch (stage + allowedPaths); fora da rota o papel segue com o agent e o run_card não roda."""
    try:
        name, adapter = resolve_worker(root, card.role, False, card.stage, card.allowed)
    except DispatchError as exc:
        raise RunCardError(EXIT_USAGE, f"papel {card.role}, etapa {card.stage}: {exc}. O run_card só roda quando o cartão cabe num worker") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise RunCardError(EXIT_USAGE, f"harness.project.yaml inválido: {exc}") from exc
    if adapter not in ADAPTERS:
        raise RunCardError(EXIT_USAGE, f"workers.{name} é {adapter}; run_card só cobre {' e '.join(ADAPTERS)}")
    if adapter == "api-tools" and card.role not in WRITE_ROLES:
        raise RunCardError(EXIT_USAGE, f"workers.{name} é api-tools, mas o papel {card.role} não grava (só {', '.join(sorted(WRITE_ROLES))})")
    return name, adapter


def run_loop(
    root: Path, card: Card, max_attempts: int, log_dir: Path, timeout: float, limit: int, dry_run: bool = False, keep_on_fail: bool = False
) -> tuple[int, str, list[str]]:
    """Devolve (código de saída, resumo curto, arquivos escritos). Restaura os arquivos se o código não for 0."""
    try:
        config = load_config(root, required=True)
    except (OSError, ValueError, TypeError) as exc:
        raise RunCardError(EXIT_USAGE, f"harness.project.yaml inválido: {exc}") from exc
    worker_name, adapter = route_for(root, card)
    tools = adapter == "api-tools"
    for rel in card.allowed + card.protected:
        safe_path(root, rel)

    head = build_head(root, card, config)
    request = build_request(root, card, head, 1, max_attempts, None, False, tools)
    if dry_run:
        adir = log_dir / "attempt-1"
        adir.mkdir(exist_ok=True)
        (adir / "request.md").write_text(request, encoding="utf-8")
        chars = len(request)
        route = f"rota: {card.role}/{card.stage} -> worker {worker_name} ({adapter}); dispatch aceitaria"
        return EXIT_PASS, f"dry-run: {route}\ndry-run: pedido da tentativa 1 com {chars} caracteres (~{chars // 3} tokens, estimativa); nada enviado, nada gravado", []
    snapshot = Snapshot(root)
    project_snap = ProjectSnapshot(root, [log_dir]) if tools else None
    last: Attempt | None = None
    same_count = 0
    worker_fails = 0
    written_all: list[str] = []
    final_code = EXIT_FAIL
    summary = ""
    passed = False
    keep = False
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            system = system_for(card.role, Path(tmp_name), adapter)
            for number in range(1, max_attempts + 1):
                global LAST_STDERR
                adir = log_dir / f"attempt-{number}"
                adir.mkdir(exist_ok=True)
                req_path, out_path, meta_path = adir / "request.md", adir / "response.md", adir / "meta.json"
                req_path.write_text(request, encoding="utf-8")
                before = project_snap.scan() if project_snap is not None else {}
                LAST_STDERR = ""
                code = run_dispatch(dispatch_command(root, card, req_path, out_path, meta_path, system))
                if LAST_STDERR:
                    (adir / "dispatch.stderr").write_text(LAST_STDERR, encoding="utf-8")
                meta = read_meta(meta_path)

                if code in (API_QUOTA, API_RATE_LIMIT):
                    raise RunCardError(EXIT_QUOTA, f"cota esgotada ou limite de taxa (worker {code}); veja {meta_path}")
                if code in (API_NO_KEY, EXIT_USAGE, 8):
                    note = f"; {LAST_STDERR.strip().splitlines()[-1]}" if LAST_STDERR.strip() else ""
                    raise RunCardError(EXIT_USAGE, f"dispatch/worker saiu {code} (chave, configuração ou papel fora da rota){note}; veja {meta_path}")

                if project_snap is not None:
                    attempt = evaluate_tools(root, card, code, meta, before, project_snap, adir, config, timeout, limit, number)
                else:
                    attempt = _evaluate(root, card, code, meta, out_path, adir, snapshot, config, timeout, limit, number)
                (adir / "result.json").write_text(
                    json.dumps({"kind": attempt.kind, "detail": attempt.detail, "written": attempt.written}, ensure_ascii=False),
                    encoding="utf-8",
                )
                for rel in attempt.written:
                    if rel not in written_all:
                        written_all.append(rel)
                if attempt.ok:
                    passed = True
                    return EXIT_PASS, "", written_all
                worker_fails = worker_fails + 1 if attempt.kind == "worker-failed" else 0
                same_count = same_count + 1 if last is not None and signature(last) == signature(attempt) else 1
                last = attempt
                if worker_fails >= WORKER_FAIL_LIMIT:
                    raise RunCardError(EXIT_WORKER, f"worker falhou {worker_fails} vezes seguidas: {attempt.detail}")
                if number < max_attempts:
                    request = build_request(root, card, head, number + 1, max_attempts, attempt, same_count >= SAME_ERROR_LIMIT, tools)
            assert last is not None
            summary = f"último check: {last.detail}" if last.kind == "check-failed" else f"última falha: {last.kind}: {last.detail}"
            if last.output:
                summary += "\n" + tail_lines(last.output, FINAL_TAIL_LINES)
            keep = keep_on_fail
            return final_code, summary, written_all
    finally:
        if not passed and not keep:
            snapshot.restore()
            if project_snap is not None:
                lost = project_snap.restore_all()
                if lost:
                    print(f"AVISO: não restaurei (arquivo grande demais para o backup): {', '.join(lost[:10])}")


def _evaluate(
    root: Path,
    card: Card,
    code: int,
    meta: dict[str, object],
    out_path: Path,
    adir: Path,
    snapshot: Snapshot,
    config: dict[str, object],
    timeout: float,
    limit: int,
    number: int,
) -> Attempt:
    if code != 0 or not out_path.is_file():
        return Attempt(number, False, "worker-failed", f"api_chat saiu {code}: {meta.get('error') or 'sem resposta'}")
    if meta.get("finish_reason") == "length":
        return Attempt(number, False, "rejected", "resposta cortada (finish_reason=length); devolva menos texto fora dos blocos")
    try:
        blocks = parse_response(read_text(out_path), card.allowed)
    except ValueError as exc:
        return Attempt(number, False, "rejected", str(exc))
    for rel, content in blocks.items():
        snapshot.remember(rel)
        path = safe_path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, content.encode("utf-8"))
    written = list(blocks)
    fmt = format_command(config, written)
    if fmt is not None:
        fcode, fout = run_shell(fmt, root, timeout)
        (adir / "format.log").write_text(f"$ {fmt}\n[exit {fcode}]\n{fout}", encoding="utf-8")
    failed = run_checks(root, card.checks, timeout, limit, adir)
    if failed is None:
        return Attempt(number, True, "pass", "", written=written)
    return Attempt(number, False, "check-failed", failed[0], failed[1], written)


EPILOG = """\
exemplos:
  run_card.py <raiz> --card E5-T12 --max-attempts 20
  run_card.py <raiz> --card E5-T12 --dry-run            # confere a rota (worker_stages/worker_paths), monta o pedido e estima o tamanho
  run_card.py <raiz> --card E5-T12 --keep-on-fail --log-dir /tmp/e5-t12   # deixa os arquivos para depurar

códigos de saída:
  0  PASS
  1  FAIL: tentativas esgotadas (arquivos restaurados, salvo --keep-on-fail)
  2  uso ou configuração (cartão, worker, caminho, lock, --max-attempts); inclui rota que manda o papel para o agent
  3  cota esgotada ou limite de taxa
  4  falha de worker/rede repetida

Não assina gates, não faz commit, não escreve no estado.json.
"""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Roda um cartão test-author/implement com o worker api-chat (Bia) ou api-tools (Baidu), com correção automática.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--card", required=True, help="ID do cartão")
    parser.add_argument("--max-attempts", type=int, help="padrão: budget.attempts do cartão (ou 6); mínimo 1")
    parser.add_argument("--dry-run", action="store_true", help="monta o pedido 1, grava no log e imprime o tamanho; sem API, sem gravar no projeto")
    parser.add_argument("--keep-on-fail", action="store_true", help="ao esgotar as tentativas, não restaura os arquivos (depuração)")
    parser.add_argument("--log-dir", type=Path, help="padrão: <tmp>/run-card/<projeto>-<cartão>")
    parser.add_argument("--cards-file", type=Path, help="YAML de cartões (padrão: docs/plano/*.yaml)")
    parser.add_argument("--check-timeout", type=float, default=900, help="segundos por check (padrão 900)")
    parser.add_argument("--max-output", type=int, default=6000, help="caracteres de saída por check (padrão 6000)")
    return parser.parse_args(argv)


def _on_sigterm(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = args.project_root.resolve()
    log_dir: Path | None = None
    max_attempts = 0
    try:
        if not root.is_dir():
            raise RunCardError(EXIT_USAGE, f"raiz do projeto inexistente: {root}")
        if args.max_attempts is not None and args.max_attempts < 1:
            raise RunCardError(EXIT_USAGE, "--max-attempts precisa ser >= 1")
        cards_file = args.cards_file if args.cards_file is None or args.cards_file.is_absolute() else root / args.cards_file
        card = load_card(root, args.card, cards_file)
        max_attempts = args.max_attempts or card.budget_attempts or DEFAULT_ATTEMPTS
        log_dir = resolve_log_dir(root, card.id, args.log_dir)
        lock_fd = -1 if args.dry_run else acquire_lock(root, card.id)
        old_handler = signal.signal(signal.SIGTERM, _on_sigterm)
        try:
            code, summary, written = run_loop(
                root, card, max_attempts, log_dir, args.check_timeout, args.max_output, args.dry_run, args.keep_on_fail
            )
        finally:
            signal.signal(signal.SIGTERM, old_handler)
            if lock_fd >= 0:
                os.close(lock_fd)
    except KeyboardInterrupt:
        print("INTERROMPIDO: arquivos restaurados ao estado anterior")
        return EXIT_INTERRUPT
    except RunCardError as exc:
        label = {EXIT_QUOTA: "COTA", EXIT_WORKER: "WORKER"}.get(exc.exit_code, "ERRO")
        print(f"{label}: {exc}")
        if log_dir is not None:
            print(f"log: {log_dir}")
        return exc.exit_code
    if args.dry_run:
        print(summary)
        print(f"log: {log_dir}")
        return EXIT_PASS
    attempts = len([p for p in log_dir.glob("attempt-*") if p.is_dir()])
    print("PASS" if code == EXIT_PASS else "FAIL")
    print(f"tentativas: {attempts}/{max_attempts}")
    if code == EXIT_PASS:
        print("arquivos: " + ", ".join(written))
    else:
        print("arquivos mantidos (--keep-on-fail): " + ", ".join(written) if args.keep_on_fail else "arquivos restaurados ao estado anterior")
        print(summary)
    print(f"log: {log_dir}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
