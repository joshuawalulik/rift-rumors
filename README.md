# Rift Rumors

Predict where League of Legends tier-one pros (LCK, LEC, LCS) play next season by combining
in-season stats, contract status and social-media rumors, then **grade the predictions
against real roster announcements**.

> Status: **Lesson 01 done**: 426 labeled backtest player-seasons (2023–25) plus the live 2026 offseason. Built in public as a lesson series.

## Lessons

| # | Lesson | Result |
|---|---|---|
| 00 | [Setup, first LLM calls, cost tracking](lessons/00-setup.md) | Claude Opus/Sonnet/Haiku 5.5 + local Llama 3.2 3B behind one interface. All Claude models followed the format; the 3B model failed 4/4. Haiku: 100× cheaper than Opus. Total spend: $0.005 |
| 01 | [Ground truth from Leaguepedia](lessons/01-ground-truth.md) | 559 player-seasons labeled stayed/moved/no_team. Only 46% stay; expiring contracts → 71% leave. Caught temporal leakage in the source's contract data; contracts match Riot's official GCD 99% |

## Quickstart

```bash
brew install uv ollama
ollama serve &               # or: brew services start ollama
ollama pull llama3.2:3b
uv sync
cp .env.example .env         # add ANTHROPIC_API_KEY + Leaguepedia bot password
uv run pytest
uv run rift-rumors hello     # compare models on one rumor post
uv run rift-rumors ground-truth  # build data/ground_truth.db
```

## Layout

```
src/rift_rumors/
  llm/client.py    complete(): one call path for Claude and Ollama models
  llm/pricing.py   $ per token, per model
  llm/ledger.py    append-only JSONL log of every LLM call
  hello.py         Lesson 00 comparison
  data/leaguepedia.py  logged-in, throttled, cached Cargo API client
  data/ground_truth.py Leaguepedia → SQLite pipeline
  data/outcomes.py     label logic + point-in-time contract lookup
  data/db.py           schema
lessons/           one write-up per lesson: concept, decision, measured result
tests/
```
