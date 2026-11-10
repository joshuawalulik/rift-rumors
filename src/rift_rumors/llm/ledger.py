"""Append-only log of every LLM call.

Later lessons answer questions like "what did the backtest cost?" and "which model
is cheapest per correct answer?". Those answers come from this file, so log every
call from day one.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from rift_rumors.llm.client import LLMResult


class Ledger:
    def __init__(self, path: Path):
        self.path = path

    def record(self, task: str, result: LLMResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(UTC).isoformat(),
            "task": task,
            **{k: v for k, v in asdict(result).items() if k != "text"},
        }
        with self.path.open("a") as f:
            f.write(json.dumps(row) + "\n")

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

    def total_cost(self) -> float:
        return sum(r["cost_usd"] or 0.0 for r in self.rows())
