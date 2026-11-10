"""Lesson 00: send the same roster rumor to every model and compare the results.

The post is made up (fictional player and teams), so no model can answer from memory.
Everything it says has to come from the text. That's a preview of Lesson 04, where
this becomes structured extraction with a real eval.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from rift_rumors import config
from rift_rumors.llm import Ledger, LLMError, complete

SAMPLE_POST = (
    "Hearing from multiple sources that jungler 'Kestrel' is in talks to leave "
    "Team Aurora for Nova Esports once his contract ends Nov 20. Nothing signed yet, "
    "and Aurora reportedly made a counter-offer."
)

SYSTEM = "You read League of Legends esports roster rumors and summarize them precisely."

PROMPT = f"""Read this post and answer in exactly four lines:
player: <name>
from_team: <team>
to_team: <team or unknown>
certainty: <confirmed | likely | rumor> - <five words or fewer on why>

Post: {SAMPLE_POST}"""


def run(
    models: list[str] | None = None,
    *,
    effort: str = "low",
    max_tokens: int = 4096,
    use_system: bool = True,
    repeat: int = 1,
) -> None:
    console = Console()
    ledger = Ledger(config.LEDGER_PATH)
    models = models or config.models()
    system = SYSTEM if use_system else None

    settings = f"effort={effort}, max_tokens={max_tokens}, system={'on' if use_system else 'off'}"
    table = Table(title=f"Lesson 00: same prompt, different models ({settings})")
    for col in ("model", "answer", "in tok", "out tok", "latency", "cost", "stop"):
        table.add_column(col, overflow="fold")

    for model in models:
        for i in range(repeat):
            label = model if repeat == 1 else f"{model} #{i + 1}"
            _run_one(console, table, ledger, label, model, system, effort, max_tokens)

    console.print(table)
    console.print(
        f"Logged to {config.LEDGER_PATH} - total spend so far: ${ledger.total_cost():.5f}"
    )


def _run_one(console, table, ledger, label, model, system, effort, max_tokens) -> None:
    console.print(f"[dim]calling {label}...[/dim]")
    try:
        result = complete(PROMPT, model=model, system=system, effort=effort, max_tokens=max_tokens)
    except LLMError as e:
        table.add_row(label, f"[red]{e}[/red]", "-", "-", "-", "-", "-")
        return

    ledger.record("lesson00-hello", result)
    answer = result.text.strip()
    if result.stop_reason == "refusal":
        answer = "[yellow](refused)[/yellow]"
    if result.served_model != result.requested_model:
        answer += f"\n[dim](served by {result.served_model})[/dim]"
    cost = "?" if result.cost_usd is None else f"${result.cost_usd:.5f}"
    table.add_row(
        label,
        answer,
        str(result.input_tokens),
        str(result.output_tokens),
        f"{result.latency_s:.1f}s",
        cost,
        str(result.stop_reason),
    )
