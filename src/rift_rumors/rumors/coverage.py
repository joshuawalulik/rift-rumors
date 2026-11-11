"""Does the rumor data cover the moves, and is there signal in it?

For each labeled offseason, take the players v0 is scored on (not already known
on the as-of date) and ask, using only headlines published *before* as-of:

- coverage: what share of players had at least one headline naming them?
- signal:   did players with a headline leave more often than those without?
- lead time: for players who left, how many days before their first recorded
  roster change did the first headline appear?

`player`, `player_ko` (Hangul handles) and `structured` (Leaguepedia rows) count by default.
`player_common_word` matches ("Upset", "Quad") are too noisy until Lesson 03's LLM
reads them. `sources` restricts to some sources, to compare what each one adds.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from statistics import median


def coverage(
    rumors: sqlite3.Connection,
    gt: sqlite3.Connection,
    as_of_month_day: str,
    kinds: tuple[str, ...] = ("player", "player_ko", "structured"),
    sources: tuple[str, ...] | None = None,
) -> list[dict]:
    first_headline: dict[tuple[int, str], str] = {}
    kind_marks = ",".join("?" for _ in kinds)
    source_filter = ""
    if sources:
        source_filter = f"AND i.source IN ({','.join('?' for _ in sources)})"
    for r in rumors.execute(
        f"""SELECT m.year, m.player, MIN(i.published_at) AS first_pub
            FROM rumor_mentions m JOIN rumor_items i ON i.id = m.item_id
            WHERE m.player IS NOT NULL AND m.match_kind IN ({kind_marks}) {source_filter}
              AND substr(i.published_at, 1, 10) <= m.year || '-' || ?
            GROUP BY 1, 2""",
        (*kinds, *(sources or ()), as_of_month_day),
    ):
        first_headline[(r["year"], r["player"])] = r["first_pub"][:10]

    rows = defaultdict(list)
    for o in gt.execute(
        "SELECT year, league, player, outcome, first_move_date FROM offseason_outcomes "
        "WHERE outcome IN ('stayed', 'moved', 'no_team')"
    ):
        as_of = f"{o['year']}-{as_of_month_day}"
        if o["first_move_date"] and o["first_move_date"] <= as_of:
            continue  # already known at as-of: not scored, same rule as the freeze
        rows[(o["year"], o["league"])].append(
            (
                o["outcome"] != "stayed",
                first_headline.get((o["year"], o["player"])),
                o["first_move_date"],
            )  # fmt: skip
        )

    out = []
    for (year, league), group in sorted(rows.items()):
        with_h = [left for left, h, _ in group if h]
        without_h = [left for left, h, _ in group if not h]
        leads = [
            (date.fromisoformat(move) - date.fromisoformat(h)).days
            for left, h, move in group
            if left and h and move
        ]
        out.append(
            {
                "year": year,
                "league": league,
                "players": len(group),
                "with_headline": len(with_h),
                "coverage": len(with_h) / len(group) if group else 0.0,
                "leave_rate_with": sum(with_h) / len(with_h) if with_h else None,
                "leave_rate_without": sum(without_h) / len(without_h) if without_h else None,
                "leavers_covered": sum(1 for left, h, _ in group if left and h)
                / max(1, sum(1 for left, _, _ in group if left)),
                "median_lead_days": median(leads) if leads else None,
            }
        )
    return out
