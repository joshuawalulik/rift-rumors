"""Point-in-time examples: one row per player-offseason, using only what was known on `as_of`.

The backtest must mirror the live freeze exactly. If we freeze live on Nov 10,
then every past year is evaluated "as of Nov 10" of that year:

- contract status comes only from announcements (and GCD snapshots) dated <= as_of
- players whose move was already recorded by as_of are flagged `already_known`
  and excluded from scoring, live and in backtests alike. Predicting a move
  that's already public proves nothing.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from rift_rumors.data.gcd import normalize_handle
from rift_rumors.data.outcomes import ContractNews, canonical_team, contract_end_as_of, news_date

CLASSES = ("stayed", "moved", "no_team")
GCD_TAB = {"LCK": "KR", "LEC": "EMEA", "LCS": "AME"}


@dataclass(frozen=True)
class Example:
    year: int
    league: str
    player: str
    handle: str
    team_before: str
    role: str | None
    as_of: str
    contract_end: str | None
    contract_source: str  # gcd | leaguepedia | none
    contract_status: str  # expiring | under_contract | unknown
    outcome: str  # stayed | moved | no_team | pending
    first_move_date: str | None
    already_known: bool  # a roster change was recorded on or before as_of


def contract_status(contract_end: str | None, year: int) -> str:
    """Expiring = the contract ends during this offseason (by Dec 31 of the season's year)."""
    if contract_end is None:
        return "unknown"
    return "expiring" if contract_end <= f"{year}-12-31" else "under_contract"


def parse_sheet_date(text: str | None) -> str | None:
    """GCD tabs stamp updates as '9/9/2026 7:26:51' or 'Nov 3, 2026 0:15:46 PT'."""
    if not text:
        return None
    for fmt, part in (("%m/%d/%Y", text.split()[0]), ("%b %d, %Y", text.split(" ", 3)[:3])):
        candidate = part if isinstance(part, str) else " ".join(part)
        try:
            return datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def resolve_contract(
    lp_news: list[ContractNews],
    gcd_rows: list[sqlite3.Row],
    team: str,
    as_of: str,
    renames: dict[str, str],
) -> tuple[str | None, str]:
    """Pick the contract end known on `as_of`, from the most recent evidence.

    GCD (official) is preferred, unless Leaguepedia has an announcement dated after
    the GCD tab's own last update. Only GCD snapshots we took on/before `as_of`
    count, so past years automatically fall back to Leaguepedia.
    """
    lp_end = contract_end_as_of(lp_news, team, as_of, renames)
    target = canonical_team(team, renames)
    lp_dates = [
        d
        for c in lp_news
        if canonical_team(c.team, renames) == target
        and (d := news_date(c.news_id)) is not None
        and d <= as_of
    ]
    lp_latest = max(lp_dates, default=None)

    usable = [g for g in gcd_rows if g["snapshot_at"][:10] <= as_of and g["contract_end"]]
    if usable:
        newest = max(usable, key=lambda g: g["snapshot_at"])
        gcd_updated = parse_sheet_date(newest["sheet_updated"]) or newest["snapshot_at"][:10]
        if lp_latest is None or lp_latest <= gcd_updated:
            return newest["contract_end"], "gcd"
    if lp_end is not None:
        return lp_end, "leaguepedia"
    return None, "none"


def load_examples(conn: sqlite3.Connection, as_of_month_day: str) -> list[Example]:
    renames = {r["old_name"]: r["new_name"] for r in conn.execute("SELECT * FROM team_renames")}
    handles = {r["player"]: r["handle"] for r in conn.execute("SELECT player, handle FROM players")}

    lp_news: dict[str, list[ContractNews]] = defaultdict(list)
    for r in conn.execute("SELECT * FROM contracts"):
        lp_news[r["player"]].append(
            ContractNews(r["team"], r["contract_end"], r["news_id"], bool(r["is_removal"]))
        )

    gcd: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in conn.execute("SELECT * FROM gcd_contracts"):
        gcd[(r["tab"], normalize_handle(r["handle"]))].append(r)

    changes: dict[str, list[str]] = defaultdict(list)
    for r in conn.execute("SELECT player, date FROM roster_changes ORDER BY date"):
        changes[r["player"]].append(r["date"])

    examples = []
    for o in conn.execute("SELECT * FROM offseason_outcomes ORDER BY year, league, player"):
        as_of = f"{o['year']}-{as_of_month_day}"
        handle = handles.get(o["player"]) or o["player"]
        gcd_rows = gcd.get((GCD_TAB[o["league"]], normalize_handle(handle)), [])
        end, source = resolve_contract(
            lp_news[o["player"]], gcd_rows, o["team_before"], as_of, renames
        )
        known = any(o["season_end"] < d <= as_of for d in changes[o["player"]])
        examples.append(
            Example(
                year=o["year"],
                league=o["league"],
                player=o["player"],
                handle=handle,
                team_before=o["team_before"],
                role=o["role"],
                as_of=as_of,
                contract_end=end,
                contract_source=source,
                contract_status=contract_status(end, o["year"]),
                outcome=o["outcome"],
                first_move_date=o["first_move_date"],
                already_known=known,
            )
        )
    return examples
