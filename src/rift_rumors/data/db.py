"""The ground-truth database (SQLite).

One file, no server, queryable with plain SQL. Tables mirror the Leaguepedia
tables they come from, with snake_case columns. Derived tables (like
`offseason_outcomes`) are rebuilt from them, never edited by hand.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments (
    page        TEXT PRIMARY KEY,   -- Leaguepedia OverviewPage, e.g. "LCK/2025 Season/Rounds 3-5"
    league      TEXT NOT NULL,      -- our code: LCK | LEC | LCS
    league_name TEXT NOT NULL,      -- Leaguepedia's league name
    year        INTEGER NOT NULL,
    date_start  TEXT,
    date_end    TEXT,
    is_playoffs INTEGER NOT NULL
);

-- Who was on each team's roster for a tournament (players only, not staff).
CREATE TABLE IF NOT EXISTS tournament_rosters (
    tournament  TEXT NOT NULL REFERENCES tournaments(page),
    team        TEXT NOT NULL,
    player      TEXT NOT NULL,      -- player page name (stable ID)
    role        TEXT,
    PRIMARY KEY (tournament, team, player)
);

-- First tournament of each league-year: the day its season starts (label cutoff).
CREATE TABLE IF NOT EXISTS season_starts (
    league     TEXT NOT NULL,
    year       INTEGER NOT NULL,
    date_start TEXT NOT NULL,
    page       TEXT NOT NULL,
    PRIMARY KEY (league, year)
);

-- Leaguepedia organization page per team (often empty); used to spot academies.
CREATE TABLE IF NOT EXISTS team_orgs (
    team TEXT PRIMARY KEY,
    org  TEXT NOT NULL
);

-- Every name a player has used → their canonical page (from PlayerRedirects).
-- Every `player` column in this database holds the canonical page name.
CREATE TABLE IF NOT EXISTS player_aliases (
    alias   TEXT NOT NULL,
    player  TEXT NOT NULL,
    PRIMARY KEY (alias, player)
);

-- One row per uninterrupted stint of a player on a team.
CREATE TABLE IF NOT EXISTS tenures (
    source          TEXT NOT NULL,  -- leaguepedia | roster_changes (rebuilt when missing)
    player          TEXT NOT NULL,
    team            TEXT NOT NULL,
    date_join       TEXT,
    date_leave      TEXT,           -- NULL = still there
    contract_end    TEXT,
    next_team       TEXT,
    next_is_retired INTEGER,
    is_current      INTEGER,
    PRIMARY KEY (player, team, date_join)
);

-- Individual roster moves, with the date each one happened.
CREATE TABLE IF NOT EXISTS roster_changes (
    id          TEXT PRIMARY KEY,
    date        TEXT NOT NULL,
    player      TEXT NOT NULL,
    team        TEXT NOT NULL,
    direction   TEXT NOT NULL,      -- Join | Leave
    role        TEXT,
    status      TEXT
);

CREATE TABLE IF NOT EXISTS contracts (
    player       TEXT NOT NULL,
    team         TEXT NOT NULL,
    contract_end TEXT,
    news_id      TEXT,
    is_removal   INTEGER
);

CREATE TABLE IF NOT EXISTS players (
    player      TEXT PRIMARY KEY,   -- page name
    handle      TEXT,               -- in-game name, e.g. "Faker"
    real_name   TEXT,
    country     TEXT,
    birthdate   TEXT,
    role        TEXT,
    team        TEXT                -- current team per Leaguepedia, right now
);

-- Team continuity events: renames, rebrands, acquisitions, mergers. Teams linked
-- by these (as of a date) are the same seat, so keeping it isn't a "move".
CREATE TABLE IF NOT EXISTS team_renames (
    date     TEXT,           -- when it took effect; applied only to later offseasons
    old_name TEXT NOT NULL,
    new_name TEXT NOT NULL,
    verb     TEXT,           -- rename | rebrand | acquire | merge | partner
    source   TEXT NOT NULL   -- leaguepedia | manual: <citation>
);

-- Riot's Global Contract Database, one row per player per snapshot WE took.
-- The sheet itself keeps no history; these snapshots are our own history.
CREATE TABLE IF NOT EXISTS gcd_contracts (
    snapshot_at       TEXT NOT NULL,  -- when we fetched it (UTC)
    sheet_updated     TEXT,           -- the tab's own "last updated" stamp (stale tabs show here)
    tab               TEXT NOT NULL,  -- AME | EMEA | KR
    league            TEXT,
    team              TEXT NOT NULL,
    handle            TEXT NOT NULL,  -- in-game name; join to players via normalized handle
    role              TEXT,
    contract_end      TEXT,           -- ISO date, NULL for coach terms
    contract_end_text TEXT,           -- as written, e.g. "Day After Worlds 2026"
    residency         TEXT,
    status            TEXT
);

-- DERIVED: for each player on a tier-one roster at the end of season `year`,
-- which team they were on when the next season started. These are the labels
-- every prediction gets graded against.
CREATE TABLE IF NOT EXISTS offseason_outcomes (
    year             INTEGER NOT NULL,  -- season that just ended
    league           TEXT NOT NULL,
    player           TEXT NOT NULL,
    role             TEXT,
    team_before      TEXT NOT NULL,
    season_end       TEXT NOT NULL,     -- league's last match day; predictions are "as of" here
    contract_end     TEXT,              -- POINT-IN-TIME: as publicly known on season_end
    cutoff           TEXT NOT NULL,     -- the day the league's NEXT season started
    team_after       TEXT,              -- NULL = not on any team at the cutoff
    outcome          TEXT NOT NULL,     -- stayed | moved | no_team | pending (playing stints only)
    move_type        TEXT,              -- moved only: transfer | demotion (own org's academy)
    team_after_feb1  TEXT,              -- same label on Feb 1: v0's PRE-REGISTERED grading rule
    outcome_feb1     TEXT NOT NULL,
    first_move_date  TEXT,              -- first roster change after the season ended
    PRIMARY KEY (year, player)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def replace_rows(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """Delete everything in `table` and insert `rows`. Loads are idempotent: run twice, same DB."""
    conn.execute(f"DELETE FROM {table}")
    if rows:
        columns = list(rows[0])
        placeholders = ", ".join("?" for _ in columns)
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [tuple(row[c] for c in columns) for row in rows],
        )
    return len(rows)
