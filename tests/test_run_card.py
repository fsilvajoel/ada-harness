"""Testes do laço `script/run_card.py`: o dispatch é simulado (sem rede, sem API real)."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from script import run_card

CONFIG = """\
roles:
  implementer: { agent: a.md, worker: bia }
  test-writer: { agent: b.md, worker: bia }
  planner: { agent: c.md }
workers:
  bia: { adapter: api-chat, base_url: "http://x", model: m, key_env: K }
"""

CARDS = """\
delivery: F1
cards:
  - id: T1
    kind: test-author
    allowedPaths: [src/a.test.txt]
    checks:
      - '! grep -q GOOD src/a.test.txt'
      - test -f src/a.test.txt
    budget: {attempts: 3}
  - id: I1
    kind: implement
    allowedPaths: [src/a.txt, src/b.txt]
    protectedTests: [src/a.test.txt]
    readFirst: [hints.md]
    checks:
      - grep -q GOOD src/a.txt
      - grep -q OK src/b.txt
    budget: {attempts: 3}
  - id: P1
    kind: plan
    allowedPaths: [x]
"""


def block(path: str, content: str) -> str:
    return f"texto solto\n=== FILE: {path} ===\n{content}\n=== END FILE ===\n"


class Fake:
    """Dispatch simulado: devolve as respostas na ordem e guarda os pedidos recebidos."""

    def __init__(self, responses: list[str | int]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def __call__(self, command: list[str]) -> int:
        request = Path(command[command.index("--request") + 1])
        output = Path(command[command.index("--output") + 1])
        self.requests.append(request.read_text(encoding="utf-8"))
        item = self.responses[len(self.requests) - 1]
        if isinstance(item, int):
            return item
        output.write_text(item, encoding="utf-8")
        return 0


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "harness.project.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "docs" / "plano" / "F1").mkdir(parents=True)
    (tmp_path / "docs" / "plano" / "F1.yaml").write_text(CARDS, encoding="utf-8")
    (tmp_path / "docs" / "plano" / "F1" / "plano.md").write_text("PLANO-XYZ\n", encoding="utf-8")
    (tmp_path / "hints.md").write_text("DICA-LINT\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.test.txt").write_text("teste assinado\n", encoding="utf-8")
    (tmp_path / "src" / "a.txt").write_text("original\n", encoding="utf-8")
    return tmp_path


def run(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: Fake, card: str, *extra: str) -> tuple[int, str]:
    monkeypatch.setattr(run_card, "run_dispatch", fake)
    outputs: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: outputs.append(" ".join(str(x) for x in a)))
    code = run_card.main([str(project), "--card", card, "--log-dir", str(tmp_path / "log"), *extra])
    return code, "\n".join(outputs)


def test_pass_on_first_attempt(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake([block("src/a.txt", "GOOD") + block("src/b.txt", "OK")])
    code, out = run(project, tmp_path, monkeypatch, fake, "I1")
    assert code == 0
    assert out.startswith("PASS")
    assert "tentativas: 1/3" in out
    assert (project / "src" / "b.txt").read_text() == "OK\n"
    first = fake.requests[0]
    for needle in ("PLANO-XYZ", "DICA-LINT", "teste assinado", "original", "(arquivo novo)", "Tentativa 1 de 3"):
        assert needle in first


def test_fail_then_pass_feeds_error_back(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake([block("src/a.txt", "BAD") + block("src/b.txt", "OK"), block("src/a.txt", "GOOD") + block("src/b.txt", "OK")])
    code, out = run(project, tmp_path, monkeypatch, fake, "I1")
    assert code == 0 and "tentativas: 2/3" in out
    second = fake.requests[1]
    assert "Tentativa 2 de 3" in second
    assert "grep -q GOOD src/a.txt" in second
    assert "=== FILE: src/a.txt ===\nBAD\n" in second  # arquivo gravado volta no pedido
    assert run_card.FIX_INSTRUCTION in second
    assert run_card.REPEAT_WARNING not in second


def test_path_outside_allowed_becomes_feedback(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake([block("src/other.txt", "x"), block("src/a.txt", "GOOD") + block("src/b.txt", "OK")])
    code, _ = run(project, tmp_path, monkeypatch, fake, "I1")
    assert code == 0
    assert "caminho fora de allowedPaths: src/other.txt" in fake.requests[1]
    assert not (project / "src" / "other.txt").exists()


@pytest.mark.parametrize(
    "response,reason",
    [
        ("sem blocos", "nenhum bloco"),
        ("=== FILE: src/a.txt ===\nGOOD\n", "sem '=== END FILE ==='"),
        ("=== FILE: src/a.txt ===\n```ts\nGOOD\n```\n=== END FILE ===\n", "cerca de código"),
        (block("src/a.txt", "GOOD") + block("src/a.txt", "GOOD"), "caminho repetido"),
    ],
)
def test_rejections_write_nothing(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: str, reason: str) -> None:
    fake = Fake([response, block("src/a.txt", "GOOD") + block("src/b.txt", "OK")])
    code, _ = run(project, tmp_path, monkeypatch, fake, "I1")
    assert code == 0
    assert reason in fake.requests[1]
    assert "Nada foi gravado" in fake.requests[1]


def test_repeated_error_adds_warning(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = block("src/a.txt", "BAD") + block("src/b.txt", "OK")
    fake = Fake([bad, bad, bad, bad])
    code, _ = run(project, tmp_path, monkeypatch, fake, "I1", "--max-attempts", "4")
    assert code == 1
    assert run_card.REPEAT_WARNING not in fake.requests[1]
    assert run_card.REPEAT_WARNING not in fake.requests[2]
    assert run_card.REPEAT_WARNING in fake.requests[3]


def test_exhausted_restores_files(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = block("src/a.txt", "BAD") + block("src/b.txt", "OK")
    fake = Fake([bad, bad])
    code, out = run(project, tmp_path, monkeypatch, fake, "I1", "--max-attempts", "2")
    assert code == 1
    assert out.startswith("FAIL")
    assert "grep -q GOOD src/a.txt" in out
    assert (project / "src" / "a.txt").read_text() == "original\n"
    assert not (project / "src" / "b.txt").exists()
    assert (tmp_path / "log" / "attempt-2" / "request.md").is_file()


def test_negated_check(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # '! grep -q GOOD': passa quando o arquivo NÃO tem GOOD; falha quando tem.
    fake = Fake([block("src/a.test.txt", "GOOD"), block("src/a.test.txt", "nada")])
    code, _ = run(project, tmp_path, monkeypatch, fake, "T1")
    assert code == 0
    assert "deveria FALHAR" in fake.requests[1]
    assert "Tentativa 2 de 3" in fake.requests[1]


def test_quota_exhausted_restores(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = block("src/a.txt", "BAD") + block("src/b.txt", "OK")
    fake = Fake([bad, run_card.API_QUOTA])
    code, out = run(project, tmp_path, monkeypatch, fake, "I1")
    assert code == 3
    assert out.startswith("COTA")
    assert (project / "src" / "a.txt").read_text() == "original\n"
    assert not (project / "src" / "b.txt").exists()


def test_repeated_worker_failure_exits_4(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake([6, 6, 6])
    code, out = run(project, tmp_path, monkeypatch, fake, "I1", "--max-attempts", "5")
    assert code == 4 and out.startswith("WORKER")


def test_usage_errors(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(project, tmp_path, monkeypatch, Fake([]), "NAO-EXISTE")[0] == 2
    assert run(project, tmp_path, monkeypatch, Fake([]), "P1")[0] == 2


def test_formatter_runs_on_written_files(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "harness.project.yaml").write_text(
        CONFIG + "commands:\n  format: sh -c 'echo formatado >> src/a.txt' --check <arquivos>\n", encoding="utf-8"
    )
    fake: Callable[[list[str]], int] = Fake([block("src/a.txt", "GOOD") + block("src/b.txt", "OK")])
    code, _ = run(project, tmp_path, monkeypatch, fake, "I1")  # type: ignore[arg-type]
    assert code == 0
    assert "formatado" in (project / "src" / "a.txt").read_text()


def test_trim_output_keeps_head_and_tail() -> None:
    text = "\n".join(f"linha {i}" for i in range(500))
    trimmed = run_card.trim_output(text, 600)
    assert len(trimmed) <= 800
    assert trimmed.startswith("linha 0") and trimmed.endswith("linha 499") and "omitidas" in trimmed
    assert json.dumps(trimmed)


def test_retry_request_does_not_grow(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = block("src/a.txt", "BAD") + block("src/b.txt", "OK")
    fake = Fake([bad, bad, bad, bad])
    code, _ = run(project, tmp_path, monkeypatch, fake, "I1", "--max-attempts", "4")
    assert code == 1
    assert all(len(r) <= len(fake.requests[1]) + len(run_card.REPEAT_WARNING) + 5 for r in fake.requests[1:])
    assert fake.requests[3].count("PLANO-XYZ") == 1 and fake.requests[3].count("=== FILE: src/a.txt ===") == 1
    assert "assignee" not in fake.requests[0] and "budget" not in fake.requests[0]


def test_dry_run_writes_nothing_and_skips_api(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake([])
    code, out = run(project, tmp_path, monkeypatch, fake, "I1", "--dry-run")
    assert code == 0 and fake.requests == []
    assert "caracteres" in out and "tokens" in out
    assert (tmp_path / "log" / "attempt-1" / "request.md").is_file()
    assert not (project / "src" / "b.txt").exists()


def test_keep_on_fail_keeps_files(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = block("src/a.txt", "BAD") + block("src/b.txt", "OK")
    code, out = run(project, tmp_path, monkeypatch, Fake([bad, bad]), "I1", "--max-attempts", "2", "--keep-on-fail")
    assert code == 1 and "mantidos" in out
    assert (project / "src" / "a.txt").read_text() == "BAD\n"


@pytest.mark.parametrize("value", ["0", "-3"])
def test_invalid_max_attempts(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    assert run(project, tmp_path, monkeypatch, Fake([]), "I1", "--max-attempts", value)[0] == 2


def test_role_without_api_chat_worker(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "harness.project.yaml").write_text(CONFIG.replace("api-chat", "cli-agent"), encoding="utf-8")
    code, out = run(project, tmp_path, monkeypatch, Fake([]), "I1")
    assert code == 2 and out.startswith("ERRO")
    (project / "harness.project.yaml").write_text(CONFIG.replace("worker: bia", "worker: nenhum"), encoding="utf-8")
    assert run(project, tmp_path, monkeypatch, Fake([]), "I1")[0] == 2


def test_log_dir_is_a_file(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "log").write_text("x", encoding="utf-8")
    assert run(project, tmp_path, monkeypatch, Fake([]), "I1")[0] == 2


@pytest.mark.parametrize("bad_path", ["../fora.txt", "/etc/passwd", "src/../../fora.txt", "link/x.txt", "src"])
def test_unsafe_allowed_paths(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_path: str) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (project / "link").symlink_to(outside)
    text = CARDS.replace("allowedPaths: [src/a.txt, src/b.txt]", f"allowedPaths: [{bad_path}]")
    (project / "docs" / "plano" / "F1.yaml").write_text(text, encoding="utf-8")
    code, out = run(project, tmp_path, monkeypatch, Fake([]), "I1")
    assert code == 2 and out.startswith("ERRO")
    assert not (outside / "x.txt").exists()


def test_second_run_is_blocked_by_lock(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_card, "LOCK_DIR", tmp_path / "locks")
    fd = run_card.acquire_lock(project.resolve(), "I1")
    try:
        code, out = run(project, tmp_path, monkeypatch, Fake([]), "I1")
        assert code == 2 and "já está rodando" in out
    finally:
        import os

        os.close(fd)
    assert run(project, tmp_path, monkeypatch, Fake([block("src/a.txt", "GOOD") + block("src/b.txt", "OK")]), "I1")[0] == 0


def test_check_timeout_is_feedback(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = CARDS.replace("grep -q GOOD src/a.txt", "sleep 30")
    (project / "docs" / "plano" / "F1.yaml").write_text(text, encoding="utf-8")
    good = block("src/a.txt", "GOOD") + block("src/b.txt", "OK")
    fake = Fake([good, good])
    code, out = run(project, tmp_path, monkeypatch, fake, "I1", "--max-attempts", "2", "--check-timeout", "1")
    assert code == 1 and "TIMEOUT" in fake.requests[1]
    assert (project / "src" / "a.txt").read_text() == "original\n"


def test_interrupt_restores_files(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = block("src/a.txt", "BAD") + block("src/b.txt", "OK")
    calls: list[int] = []

    def dispatch(command: list[str]) -> int:
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return Fake([bad])(command)

    code, out = run(project, tmp_path, monkeypatch, dispatch, "I1")  # type: ignore[arg-type]
    assert code == 130 and out.startswith("INTERROMPIDO")
    assert (project / "src" / "a.txt").read_text() == "original\n"
    assert not (project / "src" / "b.txt").exists()
    assert not list((project / "src").glob(".*run-card-tmp"))


def test_new_directories_are_removed_on_restore(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = CARDS.replace("allowedPaths: [src/a.txt, src/b.txt]", "allowedPaths: [novo/sub/a.txt]")
    (project / "docs" / "plano" / "F1.yaml").write_text(text, encoding="utf-8")
    code, _ = run(project, tmp_path, monkeypatch, Fake([block("novo/sub/a.txt", "BAD")]), "I1", "--max-attempts", "1")
    assert code == 1 and not (project / "novo").exists()


# ---------------------------------------------------------------- roteamento e api-tools

TOOLS_CONFIG = """\
roles:
  implementer: { agent: a.md, worker: baidu, worker_paths: [src] }
  test-writer: { agent: b.md, worker: bia, worker_stages: [tests] }
workers:
  bia: { adapter: api-chat, base_url: "http://x", model: m, key_env: K }
  baidu: { adapter: api-tools, base_url: "http://x", model: m, key_env: K }
"""


class ToolsFake:
    """Dispatch simulado do api-tools: o 'worker' grava arquivos sozinho (ações por tentativa) e devolve o código."""

    def __init__(self, project: Path, actions: list[Callable[[Path], None]]) -> None:
        self.project = project
        self.actions = actions
        self.commands: list[list[str]] = []
        self.requests: list[str] = []

    def __call__(self, command: list[str]) -> int:
        self.commands.append(command)
        self.requests.append(Path(command[command.index("--request") + 1]).read_text(encoding="utf-8"))
        self.actions[len(self.commands) - 1](self.project)
        return 0


def write_files(**files: str) -> Callable[[Path], None]:
    def act(project: Path) -> None:
        for rel, content in files.items():
            path = project / rel.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    return act


@pytest.fixture
def tools_project(project: Path) -> Path:
    (project / "harness.project.yaml").write_text(TOOLS_CONFIG, encoding="utf-8")
    (project / "src" / "b.txt").write_text("b-original\n", encoding="utf-8")
    (project / "outside.txt").write_text("fora-original\n", encoding="utf-8")
    return project


def option_values(command: list[str], option: str) -> list[str]:
    return [command[i + 1] for i, item in enumerate(command) if item == option]


def run_tools(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: ToolsFake, card: str, *extra: str) -> tuple[int, str]:
    monkeypatch.setattr(run_card, "run_dispatch", fake)
    outputs: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: outputs.append(" ".join(str(x) for x in a)))
    code = run_card.main([str(project), "--card", card, "--log-dir", str(tmp_path / "log"), *extra])
    return code, "\n".join(outputs)


def test_dispatch_receives_stage_and_allowed_paths(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = Fake([block("src/a.txt", "GOOD") + block("src/b.txt", "OK")])
    assert run(project, tmp_path, monkeypatch, fake, "I1")[0] == 0
    seen: list[list[str]] = []
    monkeypatch.setattr(run_card, "run_dispatch", lambda command: (seen.append(command), 4)[1])
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    run_card.main([str(project), "--card", "T1", "--log-dir", str(tmp_path / "log2")])
    assert option_values(seen[0], "--stage") == ["tests"]
    assert option_values(seen[0], "--allowed-path") == ["src/a.test.txt"]
    seen.clear()
    run_card.main([str(project), "--card", "I1", "--log-dir", str(tmp_path / "log3")])
    assert option_values(seen[0], "--stage") == ["implement"]
    assert option_values(seen[0], "--allowed-path") == ["src/a.txt", "src/b.txt"]


def test_tools_worker_writes_inside_scope_passes(tools_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ToolsFake(tools_project, [write_files(**{"src__a.txt": "GOOD\n", "src__b.txt": "OK\n"})])
    code, out = run_tools(tools_project, tmp_path, monkeypatch, fake, "I1")
    assert code == 0 and out.startswith("PASS")
    assert option_values(fake.commands[0], "--allowed-path") == ["src/a.txt", "src/b.txt"]
    assert option_values(fake.commands[0], "--stage") == ["implement"]
    assert "--system" not in fake.commands[0]
    assert "src/a.txt, src/b.txt" in out
    request = fake.requests[0]
    assert "write_file" in request and "- src/a.txt" in request
    assert "=== FILE: src/a.txt" not in request  # sem conteúdo reenviado


def test_tools_write_outside_scope_restores_everything(tools_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = write_files(**{"src__a.txt": "GOOD\n", "src__b.txt": "OK\n", "outside.txt": "mexeu\n", "novo.txt": "n\n"})
    good = write_files(**{"src__a.txt": "GOOD\n", "src__b.txt": "OK\n"})
    fake = ToolsFake(tools_project, [bad, good])
    code, _ = run_tools(tools_project, tmp_path, monkeypatch, fake, "I1")
    assert code == 0
    assert "fora de allowedPaths" in fake.requests[1] and "outside.txt" in fake.requests[1] and "novo.txt" in fake.requests[1]
    assert (tools_project / "outside.txt").read_text() == "fora-original\n"
    assert not (tools_project / "novo.txt").exists()


def test_tools_exhausted_restores_files(tools_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = write_files(**{"src__a.txt": "BAD\n", "src__b.txt": "OK\n", "outside.txt": "x\n"})
    fake = ToolsFake(tools_project, [bad, bad, bad])
    code, out = run_tools(tools_project, tmp_path, monkeypatch, fake, "I1")
    assert code == 1 and "restaurados" in out
    assert (tools_project / "src" / "a.txt").read_text() == "original\n"
    assert (tools_project / "src" / "b.txt").read_text() == "b-original\n"
    assert (tools_project / "outside.txt").read_text() == "fora-original\n"


def test_tools_check_failure_feeds_error_without_content(tools_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = write_files(**{"src__a.txt": "BAD\n", "src__b.txt": "OK\n"})
    second = write_files(**{"src__a.txt": "GOOD\n"})
    fake = ToolsFake(tools_project, [first, second])
    code, out = run_tools(tools_project, tmp_path, monkeypatch, fake, "I1")
    assert code == 0 and "tentativas: 2/3" in out
    again = fake.requests[1]
    assert "Tentativa 2 de 3" in again and "grep -q GOOD src/a.txt" in again
    assert run_card.FIX_TOOLS_INSTRUCTION in again
    assert "=== FILE: src/a.txt" not in again and "BAD" not in again


def test_tools_no_write_is_rejected(tools_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    noop = write_files()
    fake = ToolsFake(tools_project, [noop, write_files(**{"src__a.txt": "GOOD\n", "src__b.txt": "OK\n"})])
    code, _ = run_tools(tools_project, tmp_path, monkeypatch, fake, "I1")
    assert code == 0 and "não gravou nenhum arquivo" in fake.requests[1]


def test_tools_dry_run_shows_route_and_no_content(tools_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ToolsFake(tools_project, [])
    code, out = run_tools(tools_project, tmp_path, monkeypatch, fake, "I1", "--dry-run")
    assert code == 0 and fake.commands == []
    assert "worker baidu (api-tools)" in out and "implementer/implement" in out and "caracteres" in out


def test_route_to_agent_exits_2_without_running(tools_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tools_project / "harness.project.yaml").write_text(TOOLS_CONFIG.replace("[src]", "[apps/api]"), encoding="utf-8")
    fake = ToolsFake(tools_project, [])
    code, out = run_tools(tools_project, tmp_path, monkeypatch, fake, "I1")
    assert code == 2 and "segue com o agent" in out and "cabe num worker" in out
    assert fake.commands == []
    # etapa fora de worker_stages
    (tools_project / "harness.project.yaml").write_text(TOOLS_CONFIG.replace("[tests]", "[testReview]"), encoding="utf-8")
    code, out = run_tools(tools_project, tmp_path, monkeypatch, fake, "T1", "--dry-run")
    assert code == 2 and "worker_stages" in out


def test_dispatch_exit_2_or_8_is_usage_error(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for code_in in (2, 8):
        assert run(project, tmp_path, monkeypatch, Fake([code_in]), "I1")[0] == 2


def test_tools_worker_for_non_writing_role_is_config_error(tools_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tools_project / "harness.project.yaml").write_text(TOOLS_CONFIG.replace("worker: bia", "worker: baidu"), encoding="utf-8")
    code, out = run_tools(tools_project, tmp_path, monkeypatch, ToolsFake(tools_project, []), "T1")
    assert code == 2 and "não grava" in out
