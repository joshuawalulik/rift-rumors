"""Leaguepedia's RosterRumors table: rumors that wiki editors have already structured.

Editors log each rumor as a row: date, player, team they're on, team they're
rumored to join, role, and the source (Sheep Esports, X/Twitter, Weibo...). That
makes it our indirect window into X, whose API we can't use.

Leakage rules (Lesson 02, §6):

- **We never store `Status`, `IsOver` or `NotHappening`.** Editors update them
  after the outcome is known ("Confirmed", "Disproven"), so they're the answer,
  not a feature. Only date, player, teams, role and source are kept.
- `Date` is when the rumor was reported. Editors sometimes add rows after the
  fact, so a backtest that filters on `Date` is slightly optimistic. Live
  predictions should filter on `first_seen_at` (when *we* saw the row), which
  `rumors.db` records because it's append-only.

Each row becomes a `rumor_items` row (source="leaguepedia") with a synthesized
one-line title, plus a `rumor_claims` row holding the structured fields, so the
linker doesn't have to guess who a row is about.
"""

from __future__ import annotations

import sqlite3

from rift_rumors.data.leaguepedia import CargoClient
from rift_rumors.rumors import gnews, store

LIVE_MAX_AGE_S = 3 * 3600
FIELDS = "Date,Player,TeamStart,TeamEnd,RoleStart,RoleEnd,SourceSite,SourceAuthors,SourceLink"


def fetch(client: CargoClient, year: int, *, live: bool) -> list[dict]:
    """Rumor rows dated within the `year` offseason (Sep 1 of year → Feb 1 of year + 1)."""
    return client.query(
        tables="RosterRumors",
        fields=FIELDS,
        where=f"Date >= '{year}-09-01' AND Date < '{year + 1}-02-01'",
        order_by="Date",
        max_age_s=LIVE_MAX_AGE_S if live else None,
    )


def to_item(row: dict) -> tuple[gnews.NewsItem, dict] | None:
    player, date = (row.get("Player") or "").strip(), (row.get("Date") or "").strip()
    if not player or not date:
        return None
    start, end = (row.get("TeamStart") or "").strip(), (row.get("TeamEnd") or "").strip()
    handle = player.split(" (")[0]
    title = f"{handle}: {start or '?'} → {end or '?'}"
    if row.get("RoleEnd"):
        title += f" ({row['RoleEnd']})"
    outlet = (row.get("SourceSite") or "").strip() or "unknown"
    # Identity is the rumor itself, not the wiki page or line number, which editors reshuffle.
    item = gnews.NewsItem(
        id=gnews.item_id("leaguepedia", f"{date}|{player}|{start}|{end}"),
        outlet=outlet,
        title=title,
        url=(row.get("SourceLink") or "").strip(),
        published_at=f"{date}T00:00:00+00:00",
    )
    claim = {"player": player, "team_start": start or None, "team_end": end or None,
             "role": (row.get("RoleEnd") or row.get("RoleStart") or None)}  # fmt: skip
    return item, claim


def ingest(rumors: sqlite3.Connection, rows: list[dict], *, fetched_at: str) -> dict[str, int]:
    pairs = [p for p in map(to_item, rows) if p]
    new = store.upsert_items(
        rumors, [item for item, _ in pairs], source="leaguepedia",
        query="RosterRumors", fetched_at=fetched_at,
    )  # fmt: skip
    for item, claim in pairs:
        rumors.execute(
            "INSERT OR IGNORE INTO rumor_claims VALUES (?,?,?,?,?)",
            (item.id, claim["player"], claim["team_start"], claim["team_end"], claim["role"]),
        )
    rumors.commit()
    return {"rows": len(rows), "usable": len(pairs), "new_items": new}


def collect(client: CargoClient, rumors: sqlite3.Connection, years: list[int], *, live_year: int):
    totals = {}
    for year in years:
        rows = fetch(client, year, live=year == live_year)
        totals[year] = ingest(rumors, rows, fetched_at=store.now_utc())
    return totals
