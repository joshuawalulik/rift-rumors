"""Turn raw tenures into labels: where did each player go after the season?

The question being labeled: "A player was on a tier-one roster at the end of
season Y. Which team were they on when season Y+1 started?"

Three edge cases the label logic must handle:

- **Rebrands.** "Kwangdong Freecs" becoming "DN Freecs" is the same org. A player
  who stayed through a rename *stayed*. We resolve team names through the
  rename chain before comparing.
- **Multiple active tenures.** A player can be listed on a main roster and an
  academy roster at once. We prefer the stint that continues the old team, and
  otherwise the one joined most recently.
- **No team at the cutoff.** A free agent, retired, or moved to a role we don't
  track. That's `no_team`, a real outcome to predict, not missing data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

NEWS_DATE = re.compile(r"News/(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Tenure:
    player: str
    team: str
    date_join: str | None  # ISO date strings compare correctly as text
    date_leave: str | None  # None = still on the team


@dataclass(frozen=True)
class Outcome:
    team_after: str | None
    outcome: str  # stayed | moved | no_team


def canonical_team(name: str, renames: dict[str, str]) -> str:
    """Follow the rename chain to the newest name (with a cycle guard)."""
    seen = set()
    while name in renames and name not in seen:
        seen.add(name)
        name = renames[name]
    return name


def active_on(tenure: Tenure, date: str) -> bool:
    joined = tenure.date_join is None or tenure.date_join <= date
    not_left = tenure.date_leave is None or tenure.date_leave > date
    return joined and not_left


def resolve_outcome(
    team_before: str,
    tenures: Iterable[Tenure],
    cutoff: str,
    renames: dict[str, str],
) -> Outcome:
    before = canonical_team(team_before, renames)
    active = [t for t in tenures if active_on(t, cutoff)]
    if not active:
        return Outcome(team_after=None, outcome="no_team")

    for t in active:
        if canonical_team(t.team, renames) == before:
            return Outcome(team_after=t.team, outcome="stayed")

    newest = max(active, key=lambda t: t.date_join or "")
    return Outcome(team_after=newest.team, outcome="moved")


# --- point-in-time contract lookup ---------------------------------------------
#
# Temporal leakage: Leaguepedia's tenure table stores a stint's *latest* contract
# end. Faker's T1 stint says "2029", but at the end of 2023 the known deal ran to
# 2025. The 2029 extension was announced in November 2025. A model fed the 2029
# value would be learning from the future. Contracts are rebuilt from dated
# announcements instead, keeping only what was public on the as-of date.


@dataclass(frozen=True)
class ContractNews:
    team: str
    contract_end: str | None
    news_id: str | None
    is_removal: bool


def news_date(news_id: str | None) -> str | None:
    """'News/2022-11-29_28' or 'News/2018-02-24/KR_6' → '2022-11-29'."""
    match = NEWS_DATE.search(news_id or "")
    return match.group(1) if match else None


def contract_end_as_of(
    contracts: Iterable[ContractNews], team: str, as_of: str, renames: dict[str, str]
) -> str | None:
    """The contract end date for `team` as publicly known on `as_of`.

    Announcements without a parseable date are skipped. If we can't tell when it
    became public, we can't prove it was known at the time.
    """
    target = canonical_team(team, renames)
    known = [
        (date, c)
        for c in contracts
        if canonical_team(c.team, renames) == target
        and (date := news_date(c.news_id)) is not None
        and date <= as_of
    ]
    if not known:
        return None
    _, latest = max(known, key=lambda pair: pair[0])
    return None if latest.is_removal else latest.contract_end
