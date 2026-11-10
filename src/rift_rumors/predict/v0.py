"""v0: the contract-only baseline.

The prediction for a player is the historical outcome mix of players in the same
league with the same contract status. For example: "LCK players on expiring
contracts stayed 31% of the time, moved 55%, ended with no team 14%."

Small groups (e.g. LCS players with unknown contracts) are noisy, so each
league's rates are shrunk toward the all-league rate for that contract status.
`alpha` is how many "virtual" examples of the all-league rate get mixed in.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from rift_rumors.predict.features import CLASSES, Example

VERSION = "v0-contract-only"


@dataclass
class ContractBaseline:
    alpha: float = 10.0
    by_status: dict[str, dict[str, float]] = field(default_factory=dict)
    by_league_status: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)
    prior: dict[str, float] = field(default_factory=dict)
    trained_on: list[int] = field(default_factory=list)
    n_train: int = 0

    def fit(self, examples: list[Example]) -> ContractBaseline:
        train = [e for e in examples if e.outcome in CLASSES]
        self.trained_on = sorted({e.year for e in train})
        self.n_train = len(train)
        self.prior = _rates(Counter(e.outcome for e in train), {c: 1 / 3 for c in CLASSES}, 3)

        statuses = {e.contract_status for e in train}
        for s in statuses:
            group = Counter(e.outcome for e in train if e.contract_status == s)
            self.by_status[s] = _rates(group, self.prior, self.alpha)
        for league, s in {(e.league, e.contract_status) for e in train}:
            group = Counter(
                e.outcome for e in train if (e.league, e.contract_status) == (league, s)
            )
            self.by_league_status[(league, s)] = _rates(group, self.by_status[s], self.alpha)
        return self

    def predict(self, e: Example) -> dict[str, float]:
        return (
            self.by_league_status.get((e.league, e.contract_status))
            or self.by_status.get(e.contract_status)
            or self.prior
        )


@dataclass
class BaseRate:
    """The no-features baseline: everyone gets the overall outcome mix. v0 must beat this."""

    prior: dict[str, float] = field(default_factory=dict)

    def fit(self, examples: list[Example]) -> BaseRate:
        train = [e for e in examples if e.outcome in CLASSES]
        self.prior = _rates(Counter(e.outcome for e in train), {c: 1 / 3 for c in CLASSES}, 3)
        return self

    def predict(self, e: Example) -> dict[str, float]:
        return self.prior


def _rates(counts: Counter, prior: dict[str, float], alpha: float) -> dict[str, float]:
    n = sum(counts.values())
    return {c: (counts[c] + alpha * prior[c]) / (n + alpha) for c in CLASSES}
