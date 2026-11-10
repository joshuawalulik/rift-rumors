# Rift Rumors

Predict where League of Legends tier-one pros (LCK, LEC, LCS) play next season by combining
in-season stats, contract status and social-media rumors, then **grade the predictions
against real roster announcements**.

> Status: **Lesson 00 done** (toolchain, unified LLM client, cost ledger). Built in public as a lesson series.

## Lessons

| # | Lesson | Result |
|---|---|---|
| 00 | [Setup, first LLM calls, cost tracking](lessons/00-setup.md) | Claude Opus/Sonnet/Haiku 5.5 + local Llama 3.2 3B behind one interface. All Claude models followed the format; the 3B model failed 4/4. Haiku: 100× cheaper than Opus. Total spend: $0.005 |
| 01 | Ground-truth data from Leaguepedia | _next_ |

## Quickstart

```bash
brew install uv ollama
ollama serve &               # or: brew services start ollama
ollama pull llama3.2:3b
uv sync
cp .env.example .env         # add ANTHROPIC_API_KEY
uv run pytest
uv run rift-rumors hello     # compare models on one rumor post
```

## Layout

```
src/rift_rumors/
  llm/client.py    complete(): one call path for Claude and Ollama models
  llm/pricing.py   $ per token, per model
  llm/ledger.py    append-only JSONL log of every LLM call
  hello.py         Lesson 00 comparison
lessons/           one write-up per lesson: concept, decision, measured result
tests/
```
