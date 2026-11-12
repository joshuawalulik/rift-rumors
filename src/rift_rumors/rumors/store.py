"""The rumor store: an append-only SQLite database (`data/rumors.db`).

Unlike `ground_truth.db`, which is rebuilt from cache every run, this database is
never rebuilt. It records *when we first saw* each item (`first_seen_at`), and
that can't be reconstructed later. For live grading, "we had this rumor before
the freeze" is only provable if it was written down at the time.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from rift_rumors.rumors.gnews import NewsItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS rumor_items (
    id             TEXT PRIMARY KEY,
    source         TEXT NOT NULL,   -- gnews | gnews_ko | leaguepedia | manual
    outlet         TEXT NOT NULL,   -- e.g. "Sheep Esports"
    title          TEXT NOT NULL,   -- headline only; we never store article bodies
    url            TEXT,
    published_at   TEXT NOT NULL,   -- when the outlet published it (UTC)
    first_seen_at  TEXT NOT NULL,   -- when WE first fetched it (UTC); never updated
    last_seen_at   TEXT NOT NULL,
    note           TEXT             -- manual items: where it came from
);

-- Which searches surfaced each item. Coverage analysis needs to know how we found things.
CREATE TABLE IF NOT EXISTS rumor_query_hits (
    item_id    TEXT NOT NULL REFERENCES rumor_items(id),
    query      TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (item_id, query)
);

-- Structured fields for Leaguepedia rumor rows (source="leaguepedia"), one per item.
-- Deliberately no status column: editors set it after the outcome is known.
CREATE TABLE IF NOT EXISTS rumor_claims (
    item_id    TEXT PRIMARY KEY REFERENCES rumor_items(id),
    player     TEXT NOT NULL,      -- Leaguepedia page name as the editor wrote it
    team_start TEXT,               -- team the player is on
    team_end   TEXT,               -- team they're rumored to join
    role       TEXT
);

-- DERIVED (rebuilt by `link`): which players/teams each item mentions, by string matching.
CREATE TABLE IF NOT EXISTS rumor_mentions (
    item_id    TEXT NOT NULL REFERENCES rumor_items(id),
    year       INTEGER NOT NULL,   -- the offseason the item belongs to
    player     TEXT,               -- Leaguepedia page name (NULL for team-only mentions)
    team       TEXT,               -- population team the player was on, or the team mentioned
    match_kind TEXT NOT NULL,      -- player | player_common_word | team | structured
    matched    TEXT NOT NULL       -- the exact text that matched
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def upsert_items(
    conn: sqlite3.Connection,
    items: list[NewsItem],
    *,
    source: str,
    query: str,
    fetched_at: str,
    note: str | None = None,
) -> int:
    """Insert new items, keep first_seen_at for known ones. Returns how many were new."""
    new = 0
    for it in items:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO rumor_items VALUES (?,?,?,?,?,?,?,?,?)",
            (
                it.id,
                source,
                it.outlet,
                it.title,
                it.url,
                it.published_at,
                fetched_at,
                fetched_at,
                note,
            ),
        )
        if cursor.rowcount:
            new += 1
        else:
            conn.execute(
                "UPDATE rumor_items SET last_seen_at = MAX(last_seen_at, ?) WHERE id = ?",
                (fetched_at, it.id),
            )
        conn.execute(
            "INSERT OR IGNORE INTO rumor_query_hits VALUES (?,?,?)", (it.id, query, fetched_at)
        )
    conn.commit()
    return new
