# Lesson 00: Setup, your first LLM calls, and what they cost

**Goal:** make one LLM call to three Claude models and one local model through the same function, and log what each call cost.
**Ends with:** a comparison table (answer, tokens, latency, $) and a ledger file that will track spend for the rest of the project.

---

## 1. The toolchain (and why each piece)

| Tool | What it does | Why this one |
|---|---|---|
| **uv** | Installs Python, manages the virtualenv and dependencies, runs commands | One fast tool replaces pyenv + venv + pip + pip-tools. `uv.lock` pins exact versions so the project installs the same way on any machine. |
| **Python 3.12** | Language | The Anthropic SDK needs ≥3.10; macOS ships 3.9. uv installed 3.12 just for this project, so your system Python is untouched. |
| **anthropic** | Official Claude SDK | Handles auth, retries (429/5xx), timeouts, and typed responses. Don't hand-roll HTTP. |
| **ollama** | Runs open-weight models on your Mac | Free and offline. It's the "cheap baseline" row in every eval. |
| **pytest / ruff** | Tests / lint + format | Same habits as your other repos. CI comes later. |

Setup from scratch:

```bash
brew install uv ollama
ollama serve            # leave running in another terminal (or: brew services start ollama)
ollama pull llama3.2:3b # ~2 GB; sized for an 8 GB M2
uv sync                 # creates .venv from uv.lock
cp .env.example .env    # then paste your ANTHROPIC_API_KEY
uv run pytest
uv run rift-rumors hello
```

## 2. What an LLM API call actually is

An LLM call is **one HTTP request** that contains:

- **`model`**: which model, e.g. `claude-opus-5-5`.
- **`system`**: standing instructions ("You read LoL roster rumors..."). Think of it as the model's job description.
- **`messages`**: the conversation so far, as alternating `user` / `assistant` turns. The API is **stateless**: it remembers nothing between calls, so you send the full history every time.
- **`max_tokens`**: a hard cap on reply length. If you hit it, the answer is cut off mid-sentence (`stop_reason == "max_tokens"`).
- **`output_config.effort`**: how hard Claude thinks before answering (`low` → `max`). Our extraction-style prompt doesn't need deep reasoning, so we use `low`.

The response contains **content blocks** (text, thinking, tool calls), a **`stop_reason`** (why it stopped), and **`usage`** (token counts). See `src/rift_rumors/llm/client.py`; every line is commented.

### Tokens
Models read and write **tokens**, which are word pieces (roughly ¾ of an English word). Billing, context limits, and speed are all measured in tokens, not characters. Our prompt is ~130 tokens.

### Why output costs more than input
Input is processed in parallel in one pass. Output is generated **one token at a time**, each step a full pass through the model. That's why output is priced ~5× higher (Opus 5.5: $4 in / $20 out per 1M) and why latency grows with answer length.

### Thinking tokens
At effort above the minimum, Claude may reason before answering. That reasoning is billed as **output tokens** even though you don't see it. Compare `out tok` across models: some of it is invisible thinking.

## 3. Design decisions in this lesson

**One `complete()` function for every provider.** Callers pass a model string; `"ollama:..."` routes locally and everything else goes to Claude. Every later eval is "run the same task on N models and compare", so this abstraction pays off from Lesson 04 onward.

**A cost ledger from day one** (`data/llm_calls.jsonl`). Every call appends model, tokens, latency, and $. Later we'll answer "what did the backtest cost?" and "which model is cheapest *per correct answer*?" directly from this file. Note that it logs stats, not prompt text, so it stays small and safe to inspect.

**Refusal fallback on.** Opus 5.5 and Sonnet 5.5 calls send `fallbacks="default"` (beta `server-side-fallback-2026-07-01`). If a safety classifier wrongly declines, a roster rumor gets re-run on another model instead of coming back empty. `served_model` in the result tells you if that happened.

**Errors that say how to fix them.** Missing key, Ollama not running, model not pulled: each raises `LLMError` with the exact command to run. With no key at all, the SDK raises a bare `TypeError` while building headers (we found this by running it). The client converts that, and there's a test for it.

## 4. Results

Prompt: summarize a **made-up** rumor (fictional player "Kestrel", fictional teams) into four `key: value` lines. Fictional names mean no model can answer from memory, so everything must come from the text.

Run 2026-11-09, effort `low`, no fallbacks triggered (every call was served by the model requested).

| Model | Follows format? | `certainty` it chose | In tok | Out tok | Latency | Cost |
|---|---|---|---|---|---|---|
| claude-opus-5-5 | ✓ exact | rumor: "talks only, Aurora counter-offered" | 186 | 191 | 3.4 s | $0.00456 |
| claude-sonnet-5-5 | ✓ exact | **likely**: "multiple sources, nothing signed" | 186 | 50 | 1.3 s | $0.00087 |
| claude-haiku-5-5 | ✓ exact | rumor: "talks reported, nothing signed yet" | 186 | 52 | **0.8 s** | **$0.00004** |
| ollama:llama3.2:3b, cold | ✗ free-form prose | likely: "due to counter-offer" ✗ | 132 | 40 | 8.3 s | $0 |
| ollama:llama3.2:3b, earlier run 1 (cold) | ✗ capitalized keys (`Player:`) | rumor | 132 | 29 | 8.2 s | $0 |
| ollama:llama3.2:3b, earlier run 2 | ✗ partial (dropped `player:` prefix) | rumor | 132 | 27 | 1.1 s | $0 |
| ollama:llama3.2:3b, earlier run 3 | ✗ added a preamble line | n/a | 132 | 44 | 1.5 s | $0 |

Total spend for the whole lesson: **half a cent** (from the ledger).

### What to notice
1. **All three Claude models followed the format exactly. The local 3B model never did,** and gave four different formats in four runs. LLM output is *sampled*, so it's non-deterministic. This is why Lesson 04 replaces "please answer in four lines" with **structured outputs** (a Pydantic schema the API enforces), and why every claim about quality needs **many** examples, not one.
2. **The models disagreed on `certainty`, and that's a labeling problem, not a model problem.** Is "multiple sources, in talks, counter-offer, nothing signed" a *rumor* or *likely*? Sonnet said likely; Opus and Haiku said rumor. Neither is wrong, because the prompt never defined the categories. Before you can eval extraction, you need a **written rubric** (e.g. *likely* = a reputable reporter plus a named destination, *rumor* = anything less). Writing that rubric is the first step of Lesson 04. The 3B model is a different case: it said "likely *due to* counter-offer", which is backwards. A counter-offer makes a move *less* likely. That's a reasoning error, not a rubric gap.
3. **Hidden thinking tokens.** Opus returned the same ~50 visible tokens as Sonnet and Haiku but billed **191** output tokens. The extra ~140 are reasoning it did before answering, even at `low` effort. That's why Opus cost 5× Sonnet here rather than the 2× the price sheet suggests.
4. **Haiku was 100× cheaper than Opus and the fastest, with identical format compliance.** For bulk extraction over thousands of Reddit posts, that gap decides the architecture. It's only a fair choice if Haiku's *accuracy* holds up on a labeled set, which is what Lesson 04 measures.
5. **Token counts aren't comparable across providers.** The same prompt was 186 tokens for Claude and 132 for Llama: different tokenizers, different chat templates. Compare costs in dollars, not tokens.
6. **Cold starts.** Ollama unloads an idle model after 5 minutes, so the first call reloads 2 GB (≈8 s) and warm calls take ~1 s. Warm up before benchmarking latency, or you'll measure the wrong thing.
7. **Free isn't free.** The local model costs $0 per token but got the format wrong 4/4 times and made a reasoning error. The metric that matters is **cost per correct answer**, which Lesson 04 can compute once we have labels.

## 5. Exercises (do these and you'll own the concepts)

**Write your prediction down before each run.** The learning is in the gap between what you expected and what happened. Run everything from `~/Repos/rift-rumors`. The whole set costs about 3¢.

**1. Effort.** Predict: how many output tokens will Opus use at `high` vs. `low`? Will the answer get better?
```bash
uv run rift-rumors hello --models claude-opus-5-5 --effort high
```
*Then:* compare against the 191 tokens / $0.00456 baseline. Was the extra spend worth it *for this task*? Which tasks later in the project might deserve `high`?

**2. Truncation.** Predict: what happens when the reply is capped at 10 tokens?
```bash
uv run rift-rumors hello --models claude-haiku-5-5,ollama:llama3.2:3b --max-tokens 10
```
*Then:* look at the `stop` column. Do both providers report truncation the same way? Why does that matter for code that has to detect a cut-off answer?

**3. System prompt.** Predict: which models get *worse* at the format without the system prompt?
```bash
uv run rift-rumors hello --no-system
```
*Then:* the format instructions are still in the user message. So what job is the system prompt actually doing?

**4. Your first eval.** Predict: out of 5 runs each, how many outputs match the four-line format *exactly* for Haiku? For Llama?
```bash
uv run rift-rumors hello --models claude-haiku-5-5,ollama:llama3.2:3b --repeat 5
```
*Then:* count the exact matches and write them as `Haiku k/5, Llama k/5`. That's an eval: a fixed input, a pass/fail rule, a score. Also: do all 5 Haiku runs pick the same `certainty`?
*Stretch:* write `matches_format(text) -> bool` in `src/rift_rumors/hello.py` (a regex per line) with a test in `tests/`, and print a pass/fail column. Counting by eye doesn't scale; code does.

**5. Read the ledger.** Using only `data/llm_calls.jsonl`, which model had the best output tokens per second, and what has the whole lesson cost?
```bash
uv run python -c "import json; rows=[json.loads(l) for l in open('data/llm_calls.jsonl')]; print(len(rows), 'calls'); print(rows[-1])"
```
*Then:* extend that one-liner to group by `requested_model` and compute average `output_tokens / latency_s`. Is "tokens per second" a fair speed comparison, given what you learned about hidden thinking tokens and cold starts?

When you're done, put your answers in a `### My results` section under this one (or send them to me and I'll check your reasoning).

### My results (2026-11-10)

Numbers below come from `data/llm_calls.jsonl`: 24 calls, **$0.014 total** for the whole lesson.

| # | What I found | Reviewer notes |
|---|---|---|
| 1 | Opus `high`: 250 out tokens ($0.0057) vs 191 at `low` ($0.0046). Basically the same answer. | ✓ +31% cost for no gain, so effort isn't worth it for simple extraction. Save `high` for tasks that need reasoning, like Lesson 07's synthesis step, which weighs conflicting rumors. Caveat: n=1 per setting. |
| 2 | Both answers cut off mid-line. Haiku reports `max_tokens`, Ollama reports `length`. | ✓ **This found a real bug:** any code checking `stop_reason == "max_tokens"` would silently miss truncated Ollama answers. Fixed by adding a provider-neutral `LLMResult.truncated`, with a test. |
| 3 | Without the system prompt, the Claude models kept the format; Ollama's reasoning got thinner. | ✓ on format, because the format rules were in the *user* message. The system prompt sets *role and context*, not output shape. **Missed:** Opus went from **191 → 49** output tokens (cost −64%) without it, meaning it did almost no hidden thinking. One run can't say whether that's the system prompt or run-to-run variance, which is exactly why exercise 4 exists. |
| 4 | Haiku was uniform across 5 runs; Llama varied in capitalization, the `player:` prefix, and reasoning. | ✓ qualitatively, but an eval needs a **number** (`Haiku k/5, Llama k/5`), not "basically the same". The ledger supports it: Haiku's length stayed at 51–54 tokens, Llama's swung from 22 to 29. **Gap found:** the ledger doesn't store reply text, so these runs can't be re-scored now. Lesson 04 adds an eval-results file that keeps outputs. |
| 5 | Ollama $0, 0.57 s latency. | ✗ The one-liner only prints the *last* row (`rows[-1]`), which was an Ollama call. Grouping by model gives avg output tok/s: **Haiku 57 · Opus 53 · Sonnet 43 · Llama 23**. And it's *not* a fair comparison: Llama's average is dragged down by cold starts (warm runs reach ~40), Opus's tokens include hidden thinking, and API latency includes the network. Spend: Opus $0.0119 · Sonnet $0.0017 · Haiku $0.0003 · Llama $0. |

**Takeaways for the project:** Haiku is the frontrunner for bulk rumor extraction (fastest, cheapest, consistent). Normalize every provider-specific field at the client boundary. Store outputs, not just stats, whenever a run might need to be graded later.

## 6. Interview talking points

- "I route every model through one interface and log tokens/latency/cost per call, so model choice is an empirical, per-task decision."
- "LLM outputs are sampled, so I evaluate on sets, not single examples, and I enforce structure with schemas rather than prompt wording."
- "Output tokens dominate cost and latency, and thinking tokens count as output, so I set effort per task."

**Next, Lesson 01:** pull current rosters, contracts, and 2023–2025 roster-change history from Leaguepedia into SQLite. That's the ground truth everything gets graded against.
