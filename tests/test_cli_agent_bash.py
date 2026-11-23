"""Bash restrito do cli-agent (`allowedBashCommands`): validação, montagem dos argumentos e padrão desligado."""

from __future__ import annotations

from pathlib import Path

import pytest

from script.cli_agent import RunError, bash_args, load_worker, parse_allowed_bash, validate_bash_pattern

BASE = (
    "-p --model sonnet --permission-mode acceptEdits --allowedTools Read Grep Glob Edit Write "
    "--disallowedTools Bash NotebookEdit WebFetch WebSearch"
)
GOOD = ["pnpm exec eslint *", "pnpm exec vitest run *", "pnpm exec tsc --noEmit*"]


def _project(tmp_path: Path, extra: str = "") -> Path:
    (tmp_path / "harness.project.yaml").write_text(
        f"""workers:
  w:
    adapter: cli-agent
    command: claude
    read_args: "-p --allowedTools Read --disallowedTools Bash"
    write_args: "{BASE}"
{extra}""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize(
    "bad",
    [
        "*",
        "",
        "bash -c x",
        "sh -c 'x'",
        "curl *",
        "rm -rf *",
        "git push*",
        "git push origin *",
        "python3 -c *",
        "pnpm exec eslint * ; rm x",
        "pnpm exec eslint $(x)",
        "cat ../x",
        "cat /etc/passwd",
        "pnpm exec x | sh",
        "sudo ls",
        "pnpm install",
    ],
)
def test_rejeita_padroes_perigosos(bad: str) -> None:
    with pytest.raises(RunError) as err:
        validate_bash_pattern(bad)
    assert err.value.exit_code == 2


def test_aceita_padroes_de_lint_teste_e_tipos() -> None:
    assert [validate_bash_pattern(p) for p in GOOD] == GOOD


def test_parse_aceita_lista_e_texto_e_vazio_desliga() -> None:
    assert parse_allowed_bash(None) == ()
    assert parse_allowed_bash("") == ()
    assert parse_allowed_bash(list(GOOD)) == tuple(GOOD)
    assert parse_allowed_bash(", ".join(GOOD)) == tuple(GOOD)


def test_desligado_por_padrao_args_inalterados(tmp_path: Path) -> None:
    worker = load_worker(_project(tmp_path), "w", "write")
    assert worker.args == BASE.split()
    assert worker.allowed_bash == ()


def test_ligado_monta_allow_e_deny_e_libera_bash_do_deny_total(tmp_path: Path) -> None:
    extra = '    allowedBashCommands: "pnpm exec eslint *, pnpm exec vitest run *"\n'
    args = load_worker(_project(tmp_path, extra), "w", "write").args
    split = args.index("--disallowedTools")
    allow = args[args.index("--allowedTools") + 1 : split]
    deny = args[split + 1 :]
    assert "Bash(pnpm exec eslint *)" in allow and "Bash(pnpm exec vitest run *)" in allow
    assert "Bash" not in deny
    assert {"NotebookEdit", "WebFetch", "WebSearch", "Bash(curl *)", "Bash(rm *)", "Bash(git push *)"} <= set(deny)
    assert args[0] == "-p" and "acceptEdits" in args


def test_modo_read_nunca_recebe_bash(tmp_path: Path) -> None:
    extra = '    allowedBashCommands: "pnpm exec eslint *"\n'
    args = load_worker(_project(tmp_path, extra), "w", "read").args
    assert args == "-p --allowedTools Read --disallowedTools Bash".split()


def test_padrao_perigoso_na_config_falha_com_2(tmp_path: Path) -> None:
    with pytest.raises(RunError) as err:
        load_worker(_project(tmp_path, '    allowedBashCommands: "*"\n'), "w", "write")
    assert err.value.exit_code == 2


def test_bash_args_sem_padroes_devolve_o_mesmo() -> None:
    args = BASE.split()
    assert bash_args(args, ()) == args


def test_bash_args_cria_flags_ausentes() -> None:
    out = bash_args(["-p"], ("pnpm exec eslint *",))
    assert out[:2] == ["-p", "--allowedTools"]
    assert "--disallowedTools" in out
