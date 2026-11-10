"""One function, `complete()`, that calls any model this project uses.

- Claude models go through the Anthropic API (paid, strongest).
- Names prefixed with "ollama:" (e.g. "ollama:llama3.2:3b") run locally (free, weaker).

Every eval later in the project compares models, so callers never need to know
which provider is behind a name. They pass a model string and get the same
`LLMResult` back either way.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import anthropic
import ollama

from rift_rumors.llm.pricing import cost_usd

OLLAMA_PREFIX = "ollama:"

# If Claude's safety classifiers decline a request, `fallbacks="default"` has the
# API re-run it on a model Anthropic picks, instead of returning an empty refusal.
# On the Claude API this is accepted by these models:
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


NO_CREDENTIALS = (
    "No Claude credentials found. Copy .env.example to .env and set ANTHROPIC_API_KEY "
    "(create a key at https://platform.claude.com/settings/keys)."
)


class LLMError(RuntimeError):
    """A failure with a message that says how to fix it."""


@dataclass
class LLMResult:
    provider: str  # "anthropic" or "ollama"
    requested_model: str
    served_model: str  # can differ from requested if a fallback ran
    text: str
    input_tokens: int
    output_tokens: int  # for Claude this includes (hidden) thinking tokens
    cache_read_tokens: int
    cache_write_tokens: int
    latency_s: float
    cost_usd: float | None  # None = model missing from the price table
    stop_reason: str | None  # raw provider value: Claude "max_tokens" == Ollama "length"
    truncated: bool  # provider-neutral: the reply hit max_tokens and was cut off


def complete(
    prompt: str,
    *,
    model: str,
    system: str | None = None,
    max_tokens: int = 4096,
    effort: str = "low",
) -> LLMResult:
    """Send one user message and return the reply plus its token/cost/latency stats.

    `effort` (low | medium | high | xhigh | max) controls how hard Claude thinks
    before answering. Higher effort means better answers on hard tasks, but more
    tokens and more time. Ollama models ignore it.
    """
    if model.startswith(OLLAMA_PREFIX):
        return _complete_ollama(prompt, model, system, max_tokens)
    return _complete_claude(prompt, model, system, max_tokens, effort)


# --- Claude --------------------------------------------------------------------

_claude_client: anthropic.Anthropic | None = None


def _get_claude_client() -> anthropic.Anthropic:
    global _claude_client
    if _claude_client is None:
        try:
            # Reads ANTHROPIC_API_KEY (or an `ant auth login` profile) from the environment.
            _claude_client = anthropic.Anthropic()
        except anthropic.AnthropicError as e:
            raise LLMError(NO_CREDENTIALS) from e
    return _claude_client


def _complete_claude(
    prompt: str, model: str, system: str | None, max_tokens: int, effort: str
) -> LLMResult:
    client = _get_claude_client()
    request: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"effort": effort},
    }
    if system:
        request["system"] = system

    start = time.perf_counter()
    try:
        if model in FALLBACK_MODELS:
            response = client.beta.messages.create(
                betas=[FALLBACK_BETA], fallbacks="default", **request
            )
        else:
            response = client.messages.create(**request)
    except anthropic.AuthenticationError as e:
        raise LLMError("Claude rejected the API key. Check ANTHROPIC_API_KEY in .env.") from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"Claude doesn't know the model {model!r}.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Rate limited by the Claude API. Wait a minute and retry.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Couldn't reach the Claude API. Check your network.") from e
    except anthropic.AnthropicError as e:
        raise LLMError(f"Claude client error: {e}") from e
    except TypeError as e:
        # With no credentials at all, the SDK fails while building headers
        # (a TypeError), before any request is sent.
        if "authentication" not in str(e):
            raise
        raise LLMError(NO_CREDENTIALS) from e
    latency = time.perf_counter() - start

    # The reply is a list of content blocks (thinking, text, tool calls...). We want the text.
    text = "".join(block.text for block in response.content if block.type == "text")

    usage = response.usage
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cache_read = usage.cache_read_input_tokens or 0
    cache_write = usage.cache_creation_input_tokens or 0

    return LLMResult(
        provider="anthropic",
        requested_model=model,
        served_model=response.model,
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        latency_s=latency,
        cost_usd=cost_usd(response.model, input_tokens, output_tokens, cache_read, cache_write),
        stop_reason=response.stop_reason,
        truncated=response.stop_reason == "max_tokens",
    )


# --- Ollama (local) ------------------------------------------------------------


def _complete_ollama(prompt: str, model: str, system: str | None, max_tokens: int) -> LLMResult:
    name = model.removeprefix(OLLAMA_PREFIX)
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})

    start = time.perf_counter()
    try:
        response = ollama.chat(model=name, messages=messages, options={"num_predict": max_tokens})
    except ollama.ResponseError as e:
        if e.status_code == 404:
            raise LLMError(f"Local model {name!r} isn't downloaded. Run: ollama pull {name}") from e
        raise LLMError(f"Ollama error: {e.error}") from e
    except ConnectionError as e:
        raise LLMError("Ollama isn't running. Start it with: ollama serve") from e
    latency = time.perf_counter() - start

    return LLMResult(
        provider="ollama",
        requested_model=model,
        served_model=model,
        text=response.message.content or "",
        input_tokens=response.prompt_eval_count or 0,
        output_tokens=response.eval_count or 0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        latency_s=latency,
        cost_usd=0.0,  # runs on your Mac: no per-token bill
        stop_reason=response.done_reason,
        truncated=response.done_reason == "length",
    )
