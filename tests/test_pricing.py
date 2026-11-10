import pytest

from rift_rumors.llm.pricing import cost_usd


def test_opus_cost_is_input_plus_output():
    # 1M input at $4 + 1M output at $20
    assert cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)


def test_output_tokens_cost_more_than_input():
    assert cost_usd("claude-sonnet-5-5", 0, 1000) > cost_usd("claude-sonnet-5-5", 1000, 0)


def test_cache_reads_are_cheaper_than_fresh_input():
    fresh = cost_usd("claude-opus-5-5", 10_000, 0)
    cached = cost_usd("claude-opus-5-5", 0, 0, cache_read_tokens=10_000)
    assert cached < fresh / 10 + 1e-12


def test_haiku_long_prompts_switch_to_long_context_price():
    short = cost_usd("claude-haiku-5-5", 100_000, 0)
    long = cost_usd("claude-haiku-5-5", 100_001, 0)
    assert short == pytest.approx(0.01)
    assert long == pytest.approx(100_001 * 0.50 / 1_000_000)


def test_unknown_model_has_no_price():
    assert cost_usd("some-future-model", 10, 10) is None
