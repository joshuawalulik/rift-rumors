"""Scoring rules for probabilistic predictions.

We predict *probabilities*, not just a label, so we score them with proper
scoring rules. These reward honest confidence: you can't game them by always
saying 50/50, or by always being extreme.

- Brier score: mean squared error between predicted probabilities and the
  one-hot truth, summed over classes. 0 = perfect. Lower is better.
- Log loss: -log(probability given to what actually happened). It punishes
  confident misses hard. Lower is better.
- Calibration: when we say 70%, does it happen ~70% of the time?
"""

from __future__ import annotations

import math
from collections.abc import Sequence

CLASSES = ("stayed", "moved", "no_team")
EPS = 1e-6


def brier(probs: Sequence[dict[str, float]], outcomes: Sequence[str]) -> float:
    total = 0.0
    for p, y in zip(probs, outcomes, strict=True):
        total += sum((p[c] - (1.0 if c == y else 0.0)) ** 2 for c in CLASSES)
    return total / len(outcomes)


def log_loss(probs: Sequence[dict[str, float]], outcomes: Sequence[str]) -> float:
    return -sum(math.log(max(p[y], EPS)) for p, y in zip(probs, outcomes, strict=True)) / len(
        outcomes
    )


def accuracy(probs: Sequence[dict[str, float]], outcomes: Sequence[str]) -> float:
    hits = sum(max(p, key=p.get) == y for p, y in zip(probs, outcomes, strict=True))
    return hits / len(outcomes)


def leave_brier(probs: Sequence[dict[str, float]], outcomes: Sequence[str]) -> float:
    """Binary Brier for the headline question: does the player leave (moved or no team)?"""
    total = 0.0
    for p, y in zip(probs, outcomes, strict=True):
        p_leave = 1.0 - p["stayed"]
        total += (p_leave - (0.0 if y == "stayed" else 1.0)) ** 2
    return total / len(outcomes)


def calibration(
    probs: Sequence[dict[str, float]], outcomes: Sequence[str], bins: int = 5
) -> list[dict]:
    """Bucket P(leave); compare the mean prediction to the observed leave rate per bucket."""
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in zip(probs, outcomes, strict=True):
        p_leave = 1.0 - p["stayed"]
        buckets[min(int(p_leave * bins), bins - 1)].append((p_leave, int(y != "stayed")))
    rows = []
    for i, b in enumerate(buckets):
        if b:
            rows.append(
                {
                    "bucket": f"{i / bins:.1f}-{(i + 1) / bins:.1f}",
                    "n": len(b),
                    "predicted": sum(p for p, _ in b) / len(b),
                    "observed": sum(y for _, y in b) / len(b),
                }
            )
    return rows
