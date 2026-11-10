"""What an LLM call costs.

API models charge per token, with input and output tokens priced separately.
Output costs ~5x more than input, so a long answer usually costs more than a long prompt.

Prices are USD per 1 million tokens: Anthropic first-party rates, checked 2026-10.
Re-check https://www.anthropic.com/pricing before trusting a total.
"""

from __future__ import annotations

from dataclasses import dataclass

PER_MILLION = 1_000_000

# A cache write (storing a prompt prefix for reuse) costs 1.25x normal input.
CACHE_WRITE_MULTIPLIER = 1.25


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float


@dataclass(frozen=True)
class ModelPricing:
    standard: Price
    # Some models charge more once the prompt passes a size threshold.
    long_context: Price | None = None
    long_context_threshold: int | None = None


PRICES: dict[str, ModelPricing] = {
    "claude-opus-5-5": ModelPricing(Price(input=4.00, output=20.00, cache_read=0.20)),
    "claude-sonnet-5-5": ModelPricing(Price(input=2.00, output=10.00, cache_read=0.20)),
    "claude-haiku-5-5": ModelPricing(
        # cache_read assumed at the usual 0.1x of input; not separately published.
        standard=Price(input=0.10, output=0.50, cache_read=0.01),
        long_context=Price(input=0.50, output=2.50, cache_read=0.05),
        long_context_threshold=100_000,
    ),
}


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Dollar cost of one call, or None for models we have no price for.

    Local (Ollama) models cost $0 per token; the caller handles that case.
    """
    pricing = PRICES.get(model)
    if pricing is None:
        return None

    prompt_size = input_tokens + cache_read_tokens + cache_write_tokens
    price = pricing.standard
    if (
        pricing.long_context is not None
        and pricing.long_context_threshold is not None
        and prompt_size > pricing.long_context_threshold
    ):
        price = pricing.long_context

    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_write_tokens * price.input * CACHE_WRITE_MULTIPLIER
    )
    return total / PER_MILLION
