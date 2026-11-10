"""Who is who: canonical identities for players and teams.

Found in Lesson 01, exercise 3 (G2 showed 3 of its 5 players): Leaguepedia's
tables don't agree on names.

- **Players** differ by capitalization across tables ("Hans Sama" on a roster,
  "Hans sama" in tenures; 37 players affected) and by in-game renames ("SamD"
  became "Hans SamD"). An exact-match join silently drops them.
  Fix: resolve every name through Leaguepedia's `PlayerRedirects` table, which
  maps every name a player has used to one canonical page, and compare names
  case-insensitively.
- **Teams** rename, rebrand, get acquired and merge. "Did the player stay?" means
  "did they keep their seat?", so teams linked by any of those events are one
  seat. Leaguepedia's dated `TeamRenames` table records most of them. A
  hand-maintained `config/team_continuity.csv` covers known gaps (e.g. Excel
  became GIANTX).

Ownership changes (acquisitions, mergers) are applied **as of their date**: a 2025
acquisition doesn't make two teams "the same" when labeling the 2023 offseason.
Renames and rebrands apply at any date (same org, and Leaguepedia retroactively
writes old stints under the current name). Names can also loop
(Rogue → KOI in 2022, KOI → Rogue in 2023), so teams are grouped with a
union-find instead of by following a chain.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

SAME_ORG_VERBS = {"rename", "rebrand"}


def fold(name: str | None) -> str:
    """Comparison key for a name: case- and whitespace-insensitive."""
    return " ".join((name or "").split()).casefold()


class PlayerIndex:
    """Maps any name a player has used to their canonical Leaguepedia page."""

    def __init__(self, aliases: Iterable[tuple[str, str]]):
        targets: dict[str, set[str]] = defaultdict(set)
        for alias, page in aliases:
            if alias and page:
                targets[fold(alias)].add(page)
                targets[fold(page)].add(page)
        # An alias shared by two different players can't be resolved by name alone.
        self.ambiguous = {a for a, pages in targets.items() if len(pages) > 1}
        self._page = {a: next(iter(p)) for a, p in targets.items() if a not in self.ambiguous}

    def canonical(self, name: str) -> str:
        return self._page.get(fold(name), name)


class TeamLineage:
    """Union-find over team names: teams linked by a continuity event are one seat."""

    def __init__(self, links: Iterable[tuple[str, str]] = ()):
        self._parent: dict[str, str] = {}
        for a, b in links:
            self.link(a, b)

    @classmethod
    def as_of(cls, events: Iterable[dict], date: str) -> TeamLineage:
        """The continuity links that hold for an offseason whose cutoff is `date`.

        - Renames and rebrands are the SAME org under a new name, so they apply at
          any date. Leaguepedia also writes old stints under the team's current
          name (Liiv SANDBOX players in 2023 appear as "BNK FEARX", a May 2024
          rebrand), so a dated rule would break those links.
        - Ownership changes (acquire, merge, partner, unknown) apply only from their
          date: NAVI buying Rogue's spot in 2025 doesn't make them one team in 2023.
        """
        return cls(
            (e["old_name"], e["new_name"])
            for e in events
            if e.get("verb") in SAME_ORG_VERBS or (e.get("date") and e["date"] <= date)
        )

    def _find(self, key: str) -> str:
        self._parent.setdefault(key, key)
        while self._parent[key] != key:
            self._parent[key] = self._parent[self._parent[key]]  # path halving
            key = self._parent[key]
        return key

    def link(self, a: str, b: str) -> None:
        ra, rb = self._find(fold(a)), self._find(fold(b))
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)  # deterministic root

    def key(self, name: str) -> str:
        return self._find(fold(name))

    def same(self, a: str, b: str) -> bool:
        return self.key(a) == self.key(b)


CONTINUITY_COLUMNS = ["date", "old_name", "new_name", "verb", "source"]


def load_manual_continuity(path: Path) -> list[dict]:
    """Hand-curated team continuity events that Leaguepedia's TeamRenames lacks."""
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = [r for r in csv.DictReader(line for line in f if not line.startswith("#"))]
    return [
        {
            "date": r["date"].strip(),
            "old_name": r["old_name"].strip(),
            "new_name": r["new_name"].strip(),
            "verb": r["verb"].strip(),
            "source": f"manual: {r['source'].strip()}",
        }
        for r in rows
        if r.get("old_name") and r.get("new_name")
    ]
