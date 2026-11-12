# Rift Rumors

Predict where League of Legends tier-one pros (LCK, LEC, LCS) play next season by combining
in-season stats, contract status and social-media rumors, then **grade the predictions
against real roster announcements**.

> Status: **v0 frozen for the live 2026 offseason** ([predictions](predictions/README.md), tag `freeze/2026-v0`): contract-only baseline, backtest leave-Brier 0.218 vs 0.256 base rate. Built in public as a lesson series.

## Lessons

| # | Lesson | Result |
|---|---|---|
| 00 | [Setup, first LLM calls, cost tracking](lessons/00-setup.md) | Claude Opus/Sonnet/Haiku 5.5 + local Llama 3.2 3B behind one interface. All Claude models followed the format; the 3B model failed 4/4. Haiku: 100× cheaper than Opus. Total spend: $0.005 |
| 01 | [Ground truth from Leaguepedia](lessons/01-ground-truth.md) | 559 player-seasons labeled stayed/moved/no_team. Only 46% stay; expiring contracts → 71% leave. Caught temporal leakage in the source's contract data; contracts match Riot's official GCD 99% |
| 02 | [Rumor ingestion](lessons/02-rumors.md) | 5,984 rumors from Google News (EN + KO) and Leaguepedia, in an append-only store. LEC players with no rumor by Nov 11 left 8% of the time vs 55% with one; 85% of 2025 LEC leavers were rumored first. LCK headlines are mostly Worlds coverage: a mention isn't a rumor, which sets up Lesson 03 |

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
uv run rift-rumors backtest      # leave-one-year-out: base rate vs v0
uv run rift-rumors rumors leaguepedia 2023 2024 2025 2026  # structured wiki rumors
uv run rift-rumors rumors collect --year 2026              # live Google News (EN + KO)
uv run rift-rumors rumors coverage                         # do rumors cover the moves?
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
  rumors/gnews.py      Google News RSS search + parsing (EN and KO editions)
  rumors/plan.py       which searches to run; adaptive window splitting
  rumors/lp_rumors.py  Leaguepedia RosterRumors (status fields never stored)
  rumors/ko_handles.py Hangul handle map: LLM draft → code checks → human review
  rumors/link.py       match items to players/teams (candidate generation)
  rumors/coverage.py   coverage, signal and lead time per league-year
  rumors/store.py      append-only rumors.db (first_seen_at)
config/            team continuity, Hangul handle map (reviewed by hand)
docs/              Reddit API application draft
lessons/           one write-up per lesson: concept, decision, measured result
tests/
```
