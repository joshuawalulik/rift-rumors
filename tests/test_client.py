"""Tests that run without network access: routing, the ledger, and error messages."""

import ollama
import pytest

from rift_rumors.llm import client
from rift_rumors.llm.client import LLMError, LLMResult, complete
from rift_rumors.llm.ledger import Ledger


def fake_result(**overrides) -> LLMResult:
    base = dict(
        provider="ollama",
        requested_model="ollama:x",
        served_model="ollama:x",
        text="hi",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        latency_s=0.1,
        cost_usd=0.002,
        stop_reason="stop",
        truncated=False,
    )
    return LLMResult(**(base | overrides))


def test_ollama_prefix_routes_to_ollama(monkeypatch):
    calls = []
    monkeypatch.setattr(client, "_complete_ollama", lambda *a: calls.append(a) or fake_result())
    monkeypatch.setattr(client, "_complete_claude", lambda *a: pytest.fail("went to Claude"))
    complete("hello", model="ollama:llama3.2:3b")
    assert calls[0][1] == "ollama:llama3.2:3b"


def test_other_names_route_to_claude(monkeypatch):
    monkeypatch.setattr(client, "_complete_ollama", lambda *a: pytest.fail("went to Ollama"))
    monkeypatch.setattr(client, "_complete_claude", lambda *a: fake_result(provider="anthropic"))
    assert complete("hello", model="claude-opus-5-5").provider == "anthropic"


def test_missing_local_model_says_how_to_fix(monkeypatch):
    def boom(**kwargs):
        raise ollama.ResponseError("model not found", status_code=404)

    monkeypatch.setattr(client.ollama, "chat", boom)
    with pytest.raises(LLMError, match="ollama pull nope"):
        complete("hello", model="ollama:nope")


def test_ollama_length_stop_counts_as_truncated(monkeypatch):
    # Found in Lesson 00 exercise 2: Claude says "max_tokens", Ollama says "length".
    reply = ollama.ChatResponse(
        message=ollama.Message(role="assistant", content="player: Kes"),
        done_reason="length",
        prompt_eval_count=10,
        eval_count=10,
    )
    monkeypatch.setattr(client.ollama, "chat", lambda **kwargs: reply)
    result = complete("hello", model="ollama:llama3.2:3b", max_tokens=10)
    assert result.truncated is True


def test_missing_claude_key_says_how_to_fix(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "/nonexistent")
    monkeypatch.setattr(client, "_claude_client", None)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        complete("hello", model="claude-haiku-5-5")


def test_ledger_appends_and_totals(tmp_path):
    ledger = Ledger(tmp_path / "calls.jsonl")
    ledger.record("t", fake_result(cost_usd=0.25))
    ledger.record("t", fake_result(cost_usd=None))
    ledger.record("t", fake_result(cost_usd=0.5))
    assert len(ledger.rows()) == 3
    assert ledger.total_cost() == pytest.approx(0.75)
    assert "text" not in ledger.rows()[0]  # log stats, not content
