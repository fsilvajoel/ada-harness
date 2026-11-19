#!/usr/bin/env python3
"""Gate status-consistency: o `estado.json` acompanha o que os commits dizem.

Falha quando o histórico git cita uma feature (ex.: `feat(web): ... (E7-F01)`) e o estado dela não reflete isso:

- commit `feat`, `fix`, `refactor` ou `perf` que cita a feature exige a etapa `implement` cumprida;
- commit `test` que cita a feature exige a etapa `tests` cumprida;
- esses mesmos commits exigem que o `estado.json` exista;
- etapa `running` num `estado.json` já commitado e sem alteração local é estado parado: o orquestrador fecha a
  etapa (done ou status de falha) antes de seguir; `running` só vale com o arquivo ainda sujo no worktree.

Commits `docs`, `chore`, `style` e `build` não exigem nada (planos e cartões nascem antes do estado).
As features vêm dos cartões (`cards_glob`) e das pastas com `estado.json`; o ID é casado como palavra inteira.

Uso: `check_status_consistency.py <raiz do projeto> [--rev <faixa git>]`. Saída: uma linha por problema; 0 passou,
1 falhou, 2 configuração ou git inválidos.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script.project_config import get_str, load_config
from script.status import Feature, load_cards, load_states, satisfied_steps

IMPLEMENT_TYPES = frozenset({"feat", "fix", "refactor", "perf"})
TEST_TYPES = frozenset({"test"})
SEP = "\x1f"


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str

    @property
    def kind(self) -> str:
        match = re.match(r"^(\w+)(?:\([^)]*\))?!?:", self.subject)
        return match.group(1) if match else ""


class GitProblem(Exception):
    """git indisponível ou projeto sem repositório (saída 2)."""


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise GitProblem(f"git {' '.join(args[:2])} falhou: {proc.stderr.strip()}")
    return proc.stdout


def read_commits(root: Path, rev: str | None) -> list[Commit]:
    out = _git(root, "log", f"--format=%h{SEP}%s", *([rev] if rev else []))
    commits: list[Commit] = []
    for line in out.splitlines():
        sha, _, subject = line.partition(SEP)
        commits.append(Commit(sha, subject))
    return commits


def cites(commit: Commit, feature_id: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(feature_id)}(?![\w-])", commit.subject) is not None


def state_is_dirty(root: Path, state_path: Path) -> bool:
    return bool(_git(root, "status", "--porcelain", "--", str(state_path)).strip())


def check(root: Path, rev: str | None = None) -> list[str]:
    config = load_config(root, required=True)
    cards_glob = get_str(config, "cards_glob")
    if not cards_glob:
        raise GitProblem("harness.project.yaml sem 'cards_glob'")
    _, features = load_cards(root, cards_glob)
    load_states(root, cards_glob, features)
    commits = read_commits(root, rev)
    problems: list[str] = []
    for fid in sorted(features):
        feature: Feature = features[fid]
        citing = [c for c in commits if cites(c, fid)]
        if not citing:
            continue
        done = satisfied_steps(feature.state)
        for commit in citing:
            needs = (
                "implement" if commit.kind in IMPLEMENT_TYPES else "tests" if commit.kind in TEST_TYPES else None
            )
            if needs is None:
                continue
            if feature.state is None:
                problems.append(f"{fid}: commit {commit.sha} ('{commit.subject}') mas não há estado.json")
            elif not done[needs]:
                problems.append(
                    f"{fid}: commit {commit.sha} ('{commit.subject}') exige a etapa '{needs}' cumprida no estado.json, "
                    "que não acompanha"
                )
        if feature.state is not None:
            stages = feature.state.get("stages")
            running = (
                [name for name, stage in stages.items() if isinstance(stage, dict) and stage.get("status") == "running"]
                if isinstance(stages, dict)
                else []
            )
            if running:
                matches = list(root.glob(f"{Path(cards_glob).parent}/{fid}/estado.json"))
                clean = bool(matches) and not state_is_dirty(root, matches[0])
                if clean:
                    problems.append(
                        f"{fid}: etapa(s) {', '.join(running)} em 'running' no estado.json commitado, com commits "
                        f"da feature no histórico; feche a etapa (done ou status de falha)"
                    )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate status-consistency.")
    parser.add_argument("root", type=Path, help="raiz do projeto")
    parser.add_argument("--rev", default=None, help="faixa git a examinar (padrão: todo o histórico)")
    args = parser.parse_args(argv)
    try:
        problems = check(args.root.resolve(), args.rev)
    except (GitProblem, FileNotFoundError, ValueError, TypeError) as exc:
        print(f"status-consistency: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
