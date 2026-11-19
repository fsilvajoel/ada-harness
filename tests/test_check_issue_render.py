"""Testes do gate das issues renderizadas (`script/check_issue_render.py`)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script.check_issue_render import MAX_BODY, main  # noqa: E402

CLEAN = """\
<!-- ada-feature:X-F01 -->
# [X-F01] Primeira

## Resumo
Cadastrar pessoa conforme [nota](nota.md) e a tela ![tela](tela.png).

<!--t:abc123def0-->the card must pass with `code`<!--/t-->

Código: `the and with` e URL https://exemplo.com/the/and.

```
the and with must should
```
"""


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "nota.md").write_text("# nota\n", encoding="utf-8")
    (tmp_path / "tela.png").write_bytes(b"png")
    (tmp_path / "X-F01.md").write_text(CLEAN, encoding="utf-8")
    return tmp_path


def test_clean_folder_passes(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(folder)]) == 0
    assert capsys.readouterr().out.startswith("ok")


def test_pending_block_fails_only_when_translated(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(folder), "--translated"]) == 1
    assert "sem tradução" in capsys.readouterr().out


def test_broken_relative_link_and_image(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (folder / "X-F01.md").write_text(CLEAN + "\n[x](nada.md) ![y](img/nada.png)\n", encoding="utf-8")
    assert main([str(folder)]) == 1
    out = capsys.readouterr().out
    assert "nada.md" in out and "img/nada.png" in out


def test_link_resolves_against_root(folder: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    out = tmp_path_factory.mktemp("out")
    (out / "A.md").write_text("[nota](docs/nota.md)\n", encoding="utf-8")
    root = tmp_path_factory.mktemp("root")
    (root / "docs").mkdir()
    (root / "docs" / "nota.md").write_text("x", encoding="utf-8")
    assert main([str(out)]) == 1
    assert main([str(out), "--root", str(root)]) == 0


def test_absolute_links_and_anchors_are_not_checked(folder: Path) -> None:
    (folder / "X-F01.md").write_text(CLEAN + "\n[a](https://x.y/z.md) [b](#topo)\n", encoding="utf-8")
    assert main([str(folder)]) == 0


def test_body_over_limit(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (folder / "X-F01.md").write_text(CLEAN + "palavra " * (MAX_BODY // 8 + 10), encoding="utf-8")
    assert main([str(folder)]) == 1
    assert "limite" in capsys.readouterr().out


def test_residual_english_prose_is_reported_with_line(folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (folder / "X-F01.md").write_text(CLEAN + "\nThis step should be done with care.\n", encoding="utf-8")
    assert main([str(folder)]) == 1
    out = capsys.readouterr().out
    assert "X-F01.md:" in out and "should" in out and "with" in out


def test_h1_title_is_not_checked(folder: Path) -> None:
    (folder / "X-F01.md").write_text("# [X-F01] Title with the words\n\nTexto em português.\n", encoding="utf-8")
    assert main([str(folder)]) == 0


def test_usage_error_exit_2(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nao-existe")]) == 2
