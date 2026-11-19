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
    """Modelo simulado: devolve as respostas em ordem e registra cada prompt."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[str] = []

    def __call__(self, text: str) -> str:
        self.calls.append(text)
        return self.answers.pop(0)


TA = "Escreva o teste para `RF-01` em src/a.ts"
TB = "passa em 3 passos"
BATCH_OK = f"[1] {TA}\n[2] {TB}"


def test_without_url_only_reports(issue: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert main([str(issue)]) == 0
    assert "2 de 2" in capsys.readouterr().out
    assert not (issue.parent / "issue.pt-br.md").exists()


def test_translates_writes_output_and_cache(issue: Path) -> None:
    fake = Fake([BATCH_OK])
    assert main([str(issue)], ask=fake) == 0
    assert len(fake.calls) == 1 and "[1] " + A in fake.calls[0] and "[2] " + B in fake.calls[0]
    out = (issue.parent / "issue.pt-br.md").read_text(encoding="utf-8")
    assert f"- {TA}" in out and "<!--t:" not in out
    cache = json.loads((issue.parent / "issue.cache.json").read_text(encoding="utf-8"))
    assert sorted(cache.values()) == sorted([TA, TB])


def test_cache_hit_skips_the_model(issue: Path) -> None:
    from script.render_issue import short_hash

    cache = {short_hash(A): TA, short_hash(B): TB}
    (issue.parent / "issue.cache.json").write_text(json.dumps(cache), encoding="utf-8")
    fake = Fake({})
    assert main([str(issue)], ask=fake) == 0 and fake.calls == []
    assert "passa em 3 passos" in (issue.parent / "issue.pt-br.md").read_text(encoding="utf-8")


def test_rejected_item_falls_back_to_individual_call(issue: Path) -> None:
    fake = Fake([f"[1] Escreva o teste para `RF-02` em src/a.ts\n[2] {TB}", TA])
    assert main([str(issue)], ask=fake) == 0
    assert len(fake.calls) == 2 and fake.calls[1] == A
    assert TA in (issue.parent / "issue.pt-br.md").read_text(encoding="utf-8")


def test_batch_out_of_order_is_parsed_by_marker(issue: Path) -> None:
    fake = Fake([f"[2] {TB}\n[1] {TA}"])
    assert main([str(issue)], ask=fake) == 0 and len(fake.calls) == 1
    out = (issue.parent / "issue.pt-br.md").read_text(encoding="utf-8")
    assert f"- {TA}\n- {TB}" in out


def test_batch_missing_item_is_retried_alone(issue: Path) -> None:
    fake = Fake([f"[1] {TA}", TB])
    assert main([str(issue)], ask=fake) == 0
    assert len(fake.calls) == 2 and fake.calls[1] == B


def test_rejected_block_stays_english_and_exit_is_1(issue: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = "passa em 4 passos"
    fake = Fake([f"[1] {TA}\n[2] {bad}", bad, bad, bad])
    assert main([str(issue)], ask=fake) == 1
    assert [c.split("\n\n(Atenção")[0] for c in fake.calls[1:]] == [B, B, B]
    assert "1 reprovado" in capsys.readouterr().err
    out = (issue.parent / "issue.pt-br.md").read_text(encoding="utf-8")
    assert TA in out and B in out and bad not in out
    cache = json.loads((issue.parent / "issue.cache.json").read_text(encoding="utf-8"))
    assert list(cache.values()) == [TA]


def test_cache_is_written_incrementally(tmp_path: Path) -> None:
    texts = [f"task {n} here" for n in range(1, 10)]  # 9 blocos: lote de 8 + 1
    path = tmp_path / "issue.md"
    path.write_text("\n".join(f"- {mark(t)}" for t in texts) + "\n", encoding="utf-8")
    first = "\n".join(f"[{n}] tarefa {n} aqui" for n in range(1, 9))
    seen: list[int] = []

    def ask(text: str) -> str:
        if not seen:
            seen.append(0)
            return first
        cache_file = tmp_path / "issue.cache.json"
        seen.append(len(json.loads(cache_file.read_text(encoding="utf-8"))))  # cache já gravado no 1o lote
        return "resposta sem os tokens"  # reprova o 9o bloco

    assert main([str(path)], ask=ask) == 1
    assert seen[:2] == [0, 8]  # na 1a chamada do 2o lote o cache já tinha os 8 do 1o
    cache = json.loads((tmp_path / "issue.cache.json").read_text(encoding="utf-8"))
    assert len(cache) == 8


def test_ollama_http_call_uses_fixed_prompt(issue: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, object]] = []

    class Response(io.BytesIO):
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: object, timeout: float = 0) -> Response:
        sent.append({"url": getattr(request, "full_url"), "body": json.loads(getattr(request, "data"))})
        return Response(json.dumps({"message": {"content": BATCH_OK}}).encode())

    monkeypatch.setattr(translate_blocks.urllib.request, "urlopen", fake_urlopen)
    assert main([str(issue), "--ollama-url", "http://modelo.invalido:1/", "--model", "m"]) == 0
    assert len(sent) == 1 and sent[0]["url"] == "http://modelo.invalido:1/api/chat"
    body = sent[0]["body"]
    assert isinstance(body, dict) and body["model"] == "m" and body["stream"] is False
    assert body["think"] is False and body["options"] == {"temperature": 0.2}
    assert str(body["messages"][0]["content"]).startswith(PROMPT)  # type: ignore[index]


def test_model_is_required_with_url(issue: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assert main([str(issue), "--ollama-url", "http://x"]) == 2
