"""Testes da tradução de blocos (`script/translate_blocks.py`), com o Ollama simulado."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from script import translate_blocks  # noqa: E402
from script.render_issue import mark  # noqa: E402
from script.translate_blocks import PROMPT, main  # noqa: E402

A = "Write the test for `RF-01` in src/a.ts"
B = "it passes in 3 steps"


@pytest.fixture
def issue(tmp_path: Path) -> Path:
    path = tmp_path / "issue.md"
    path.write_text(f"# [X-F01] T\n\n- {mark(A)}\n- {mark(B)}\n", encoding="utf-8")
    return path


class Fake:
    def __init__(self, answers: dict[str, list[str]]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, text: str) -> str:
        self.calls.append(text)
        return self.answers[text].pop(0)


GOOD = {
    A: ["Escreva o teste para `RF-01` em src/a.ts"],
    B: ["passa em 3 passos"],
}


def test_without_url_only_reports(issue: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert main([str(issue)]) == 0
    assert "2 de 2" in capsys.readouterr().out
    assert not (issue.parent / "issue.pt-br.md").exists()


def test_translates_writes_output_and_cache(issue: Path) -> None:
    fake = Fake({k: list(v) for k, v in GOOD.items()})
    assert main([str(issue)], ask=fake) == 0
    out = (issue.parent / "issue.pt-br.md").read_text(encoding="utf-8")
    assert "- Escreva o teste para `RF-01` em src/a.ts" in out and "<!--t:" not in out
    cache = json.loads((issue.parent / "issue.cache.json").read_text(encoding="utf-8"))
    assert sorted(cache.values()) == sorted(v[0] for v in GOOD.values())


def test_cache_hit_skips_the_model(issue: Path) -> None:
    from script.render_issue import short_hash

    cache = {short_hash(A): GOOD[A][0], short_hash(B): GOOD[B][0]}
    (issue.parent / "issue.cache.json").write_text(json.dumps(cache), encoding="utf-8")
    fake = Fake({})
    assert main([str(issue)], ask=fake) == 0 and fake.calls == []
    assert "passa em 3 passos" in (issue.parent / "issue.pt-br.md").read_text(encoding="utf-8")


def test_rejected_translation_is_retried(issue: Path) -> None:
    fake = Fake({A: ["Escreva o teste para `RF-02` em src/a.ts", GOOD[A][0]], B: list(GOOD[B])})
    assert main([str(issue)], ask=fake) == 0
    assert fake.calls.count(A) == 2


def test_fails_after_retries_and_writes_nothing(issue: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = "passa em 4 passos"
    fake = Fake({A: list(GOOD[A]), B: [bad, bad, bad]})
    assert main([str(issue)], ask=fake) == 1
    assert fake.calls.count(B) == 3
    assert "nada foi gravado" in capsys.readouterr().err
    assert not (issue.parent / "issue.pt-br.md").exists() and not (issue.parent / "issue.cache.json").exists()


def test_ollama_http_call_uses_fixed_prompt(issue: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, object]] = []
    replies = iter(GOOD[A] + GOOD[B])

    class Response(io.BytesIO):
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: object, timeout: float = 0) -> Response:
        sent.append({"url": getattr(request, "full_url"), "body": json.loads(getattr(request, "data"))})
        return Response(json.dumps({"message": {"content": next(replies)}}).encode())

    monkeypatch.setattr(translate_blocks.urllib.request, "urlopen", fake_urlopen)
    assert main([str(issue), "--ollama-url", "http://modelo.invalido:1/", "--model", "m"]) == 0
    assert sent[0]["url"] == "http://modelo.invalido:1/api/chat"
    body = sent[0]["body"]
    assert isinstance(body, dict) and body["model"] == "m" and body["stream"] is False
    assert str(body["messages"][0]["content"]).startswith(PROMPT)  # type: ignore[index]


def test_model_is_required_with_url(issue: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assert main([str(issue), "--ollama-url", "http://x"]) == 2
