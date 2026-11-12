"""Project settings. Secrets come from .env (never committed); everything else lives here."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = Path(os.getenv("RIFT_DATA_DIR", REPO_ROOT / "data"))
LEDGER_PATH = DATA_DIR / "llm_calls.jsonl"
GROUND_TRUTH_DB = DATA_DIR / "ground_truth.db"
KO_HANDLES_CSV = REPO_ROOT / "config" / "ko_handles.csv"  # reviewed by hand
RUMORS_DB = DATA_DIR / "rumors.db"  # append-only: never rebuilt (keeps first_seen_at)

# The models Lesson 00 compares. Override with a comma-separated RIFT_MODELS in .env.
DEFAULT_MODELS = [
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "ollama:llama3.2:3b",
]


def models() -> list[str]:
    raw = os.getenv("RIFT_MODELS")
    return [m.strip() for m in raw.split(",") if m.strip()] if raw else DEFAULT_MODELS
