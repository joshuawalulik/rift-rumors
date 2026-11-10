"""Lesson 01 pipeline: Leaguepedia → SQLite ground truth.

Steps:
1. Find each league's second-half tournaments (summer split, groups, playoffs).
2. Pull their rosters. A player's "end of season" team is the team in the latest
   of those tournaments they appeared in, kept only if they were still on that team
   on the league's last day of the season. This is the population we predict for.
3. For just those players, pull tenures, roster changes, contracts and bios.
4. Derive `offseason_outcomes`: where each player was when the next season started.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from rift_rumors.data import db, gcd
from rift_rumors.data.leaguepedia import CargoClient
from rift_rumors.data.outcomes import (
    ContractNews,
    Tenure,
    active_on,
    canonical_team,
    contract_end_as_of,
    resolve_outcome,
)

# Leaguepedia league name → our league code. Exact names, not patterns: an early
# regex for "Championship Series" also matched Vietnam's VCS and the Pacific PCS.
# The Americas league ran as "LTA North" in 2025 before the LCS returned in 2026.
LEAGUE_NAMES = {
    "LoL Champions Korea": "LCK",
    "LoL EMEA Championship": "LEC",
    "League of Legends Championship Series": "LCS",
    "League of Legends Championship of The Americas North": "LCS",
}
# Tournaments starting on or after this date make up the "second half" of a season.
SECOND_HALF_MONTH_DAY = "06-01"
FIRST_YEAR = 2023
IN_GAME_ROLES = {"Top", "Jungle", "Mid", "Bot", "Support"}
# Next season's rosters are set by early February in all three leagues.
CUTOFF_MONTH_DAY = "02-01"
CHUNK = 40  # player names per IN (...) query, to keep URLs a sane length

SIX_HOURS = 6 * 3600
ONE_DAY = 24 * 3600


def league_code(league_name: str) -> str | None:
    return LEAGUE_NAMES.get((league_name or "").strip())


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _in(column: str, values: list[str]) -> str:
    return f"{column} IN ({', '.join(_quote(v) for v in values)})"


def _chunks(values: list[str], size: int = CHUNK):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _date(value: str | None) -> str | None:
    """Cargo returns '2024-11-20' or '2024-11-20 00:00:00' or ''. Normalize to 'YYYY-MM-DD'."""
    return value[:10] if value else None


def _flag(value) -> int:
    return 1 if str(value).strip().lower() in {"1", "yes", "true"} else 0


# --- 1. tournaments -------------------------------------------------------------


def fetch_tournaments(client: CargoClient) -> list[dict]:
    rows = client.query(
        tables="Tournaments=T",
        fields=(
            "T.OverviewPage=page,T.League=league_name,T.Year=year,T.DateStart=date_start,"
            "T.Date=date_end,T.IsPlayoffs=is_playoffs,T.IsQualifier=is_qualifier"
        ),
        where=f"T.Year>={FIRST_YEAR} AND T.TournamentLevel='Primary'",
        order_by="T.DateStart",
        max_age_s=ONE_DAY,
    )
    out = []
    for r in rows:
        code = league_code(r["league_name"])
        if code is None or _flag(r["is_qualifier"]):
            continue
        out.append(
            {
                "page": r["page"],
                "league": code,
                "league_name": r["league_name"],
                "year": int(r["year"]),
                "date_start": _date(r["date_start"]),
                "date_end": _date(r["date_end"]),
                "is_playoffs": _flag(r["is_playoffs"]),
            }
        )
    return out


def second_half_tournaments(tournaments: list[dict]) -> list[dict]:
    """Each league-year's tournaments from June on: the summer split, groups and playoffs.

    The final tournament alone isn't enough. Playoffs only include the top teams,
    so taking just the last event would silently drop every non-playoff roster.
    """
    return [
        t
        for t in tournaments
        if t["year"] >= FIRST_YEAR
        and (t["date_start"] or "") >= f"{t['year']}-{SECOND_HALF_MONTH_DAY}"
    ]


# --- 2. rosters -----------------------------------------------------------------


def fetch_rosters(client: CargoClient, pages: list[str]) -> list[dict]:
    rows = client.query(
        tables="TournamentPlayers=TP",
        fields="TP.OverviewPage=tournament,TP.Team=team,TP.Link=player,TP.Role=role",
        where=_in("TP.OverviewPage", pages),
        max_age_s=ONE_DAY,
    )
    out = {}
    for r in rows:
        role = (r["role"] or "").strip()
        if role not in IN_GAME_ROLES or not r["player"]:
            continue  # coaches, analysts, and sub-role variants are out of scope for now
        out[(r["tournament"], r["team"], r["player"])] = {
            "tournament": r["tournament"],
            "team": r["team"],
            "player": r["player"],
            "role": role,
        }
    return list(out.values())


# --- 3. per-player tables -------------------------------------------------------


def fetch_tenures(client: CargoClient, players: list[str]) -> list[dict]:
    out = {}
    for chunk in _chunks(players):
        for r in client.query(
            tables="TenuresUnbroken=T",
            fields=(
                "T.Player=player,T.Team=team,T.DateJoin=date_join,T.DateLeave=date_leave,"
                "T.ContractEnd=contract_end,T.NextTeam=next_team,"
                "T.NextIsRetired=next_is_retired,T.IsCurrent=is_current"
            ),
            where=_in("T.Player", chunk),
            max_age_s=SIX_HOURS,
        ):
            row = {
                "player": r["player"],
                "team": r["team"],
                "date_join": _date(r["date_join"]),
                "date_leave": _date(r["date_leave"]),
                "contract_end": _date(r["contract_end"]),
                "next_team": r["next_team"] or None,
                "next_is_retired": _flag(r["next_is_retired"]),
                "is_current": _flag(r["is_current"]),
            }
            out[(row["player"], row["team"], row["date_join"])] = row
    return list(out.values())


def fetch_roster_changes(client: CargoClient, players: list[str]) -> list[dict]:
    out = {}
    for chunk in _chunks(players):
        for r in client.query(
            tables="RosterChanges=RC",
            fields=(
                "RC.RosterChangeId=id,RC.Date_Sort=date,RC.Player=player,RC.Team=team,"
                "RC.Direction=direction,RC.Role=role,RC.Status=status"
            ),
            where=f"{_in('RC.Player', chunk)} AND RC.Date_Sort>='{FIRST_YEAR}-01-01'",
            max_age_s=SIX_HOURS,
        ):
            if not r["id"]:
                continue
            out[r["id"]] = {
                "id": r["id"],
                "date": _date(r["date"]),
                "player": r["player"],
                "team": r["team"],
                "direction": r["direction"],
                "role": r["role"] or None,
                "status": r["status"] or None,
            }
    return list(out.values())


def fetch_contracts(client: CargoClient, players: list[str]) -> list[dict]:
    out = []
    for chunk in _chunks(players):
        for r in client.query(
            tables="Contracts=C",
            fields=(
                "C.Player=player,C.Team=team,C.ContractEnd=contract_end,"
                "C.NewsId=news_id,C.IsRemoval=is_removal"
            ),
            where=_in("C.Player", chunk),
            max_age_s=SIX_HOURS,
        ):
            out.append(
                {
                    "player": r["player"],
                    "team": r["team"],
                    "contract_end": _date(r["contract_end"]),
                    "news_id": r["news_id"] or None,
                    "is_removal": _flag(r["is_removal"]),
                }
            )
    return out


def fetch_players(client: CargoClient, players: list[str]) -> list[dict]:
    out = {}
    for chunk in _chunks(players):
        for r in client.query(
            tables="Players=P",
            fields=(
                "P.OverviewPage=player,P.ID=handle,P.Name=real_name,P.Country=country,"
                "P.Birthdate=birthdate,P.Role=role,P.Team=team"
            ),
            where=_in("P.OverviewPage", chunk),
            max_age_s=SIX_HOURS,
        ):
            out[r["player"]] = {
                "player": r["player"],
                "handle": r["handle"] or None,
                "real_name": r["real_name"] or None,
                "country": r["country"] or None,
                "birthdate": _date(r["birthdate"]),
                "role": r["role"] or None,
                "team": r["team"] or None,
            }
    return list(out.values())


def fetch_renames(client: CargoClient) -> list[dict]:
    rows = client.query(
        tables="Teams=T",
        fields="T.OverviewPage=old_name,T.RenamedTo=new_name",
        where="T.RenamedTo IS NOT NULL AND T.RenamedTo != ''",
        max_age_s=ONE_DAY,
    )
    return [
        {"old_name": r["old_name"], "new_name": r["new_name"]}
        for r in rows
        if r["old_name"] and r["new_name"] and r["old_name"] != r["new_name"]
    ]


# --- 4. derived labels ----------------------------------------------------------


def compute_outcomes(conn: sqlite3.Connection, today: date | None = None, log=print) -> list[dict]:
    today_iso = (today or date.today()).isoformat()
    renames = {r["old_name"]: r["new_name"] for r in conn.execute("SELECT * FROM team_renames")}

    tenures_by_player: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute("SELECT * FROM tenures ORDER BY date_join"):
        tenures_by_player[r["player"]].append(dict(r))

    changes_by_player: dict[str, list[str]] = defaultdict(list)
    for r in conn.execute("SELECT player, date FROM roster_changes ORDER BY date"):
        changes_by_player[r["player"]].append(r["date"])

    contracts_by_player: dict[str, list[ContractNews]] = defaultdict(list)
    for r in conn.execute("SELECT * FROM contracts"):
        contracts_by_player[r["player"]].append(
            ContractNews(r["team"], r["contract_end"], r["news_id"], bool(r["is_removal"]))
        )

    # The last day of each league's season = end of its last tournament that year.
    season_end_by_league_year = {
        (r["league"], r["year"]): r["season_end"]
        for r in conn.execute(
            "SELECT league, year, MAX(date_end) AS season_end FROM tournaments GROUP BY 1, 2"
        )
    }

    # Each player's team in the LATEST second-half tournament they appeared in.
    latest: dict[tuple[int, str], sqlite3.Row] = {}
    for row in conn.execute(
        """
        SELECT t.league, t.year, t.date_start, r.team, r.player, r.role
        FROM tournament_rosters r JOIN tournaments t ON t.page = r.tournament
        ORDER BY t.date_start
        """
    ):
        latest[(row["year"], row["player"])] = row  # later rows overwrite earlier ones

    outcomes, left_mid_season = [], 0
    for (year, player), row in sorted(latest.items()):
        season_end = season_end_by_league_year[(row["league"], year)]
        before = canonical_team(row["team"], renames)

        # Keep only players still on that team when the season ended. Someone released
        # in July didn't make an *offseason* move, so they aren't in this population.
        stint = next(
            (
                t
                for t in tenures_by_player[player]
                if canonical_team(t["team"], renames) == before
                and active_on(
                    Tenure(player, t["team"], t["date_join"], t["date_leave"]), season_end
                )
            ),
            None,
        )
        if stint is None:
            left_mid_season += 1
            continue

        cutoff = f"{year + 1}-{CUTOFF_MONTH_DAY}"
        # Roster changes inside this offseason window only (not, say, a 2025 status
        # change attached to a player who stayed through the 2023 offseason).
        window_end = min(cutoff, today_iso)
        first_move = next(
            (d for d in changes_by_player[player] if season_end < d <= window_end), None
        )
        if cutoff > today_iso:
            team_after, outcome = None, "pending"  # this offseason: labels not known yet
        else:
            tenures = [
                Tenure(player, t["team"], t["date_join"], t["date_leave"])
                for t in tenures_by_player[player]
            ]
            resolved = resolve_outcome(row["team"], tenures, cutoff, renames)
            team_after, outcome = resolved.team_after, resolved.outcome

        outcomes.append(
            {
                "year": year,
                "league": row["league"],
                "player": player,
                "role": row["role"],
                "team_before": row["team"],
                "season_end": season_end,
                # Point-in-time: NOT stint["contract_end"], which may be a later re-sign.
                "contract_end": contract_end_as_of(
                    contracts_by_player[player], row["team"], season_end, renames
                ),
                "team_after": team_after,
                "outcome": outcome,
                "first_move_date": first_move,
            }
        )

    log(f"excluded {left_mid_season} players who left their team before the season ended")
    return outcomes


# --- orchestration --------------------------------------------------------------


def build(
    client: CargoClient,
    conn: sqlite3.Connection,
    gcd_dir: Path | None = None,
    log=print,
) -> dict[str, int]:
    second_half = second_half_tournaments(fetch_tournaments(client))
    log(f"second-half tournaments: {len(second_half)}")
    for t in second_half:
        log(f"  {t['league']} {t['year']}  {t['date_start']} → {t['date_end']}  {t['page']}")

    rosters = fetch_rosters(client, [t["page"] for t in second_half])
    players = sorted({r["player"] for r in rosters})
    log(f"players on second-half rosters: {len(players)}")

    counts = {
        "tournaments": db.replace_rows(conn, "tournaments", second_half),
        "tournament_rosters": db.replace_rows(conn, "tournament_rosters", rosters),
        "team_renames": db.replace_rows(conn, "team_renames", fetch_renames(client)),
        "tenures": db.replace_rows(conn, "tenures", fetch_tenures(client, players)),
        "roster_changes": db.replace_rows(
            conn, "roster_changes", fetch_roster_changes(client, players)
        ),
        "contracts": db.replace_rows(conn, "contracts", fetch_contracts(client, players)),
        "players": db.replace_rows(conn, "players", fetch_players(client, players)),
    }
    if gcd_dir is not None:
        gcd.snapshot(gcd_dir, log=log)  # no-op if the latest snapshot is < 6h old
        counts["gcd_contracts"] = db.replace_rows(
            conn, "gcd_contracts", gcd.load_snapshots(gcd_dir)
        )
    counts["offseason_outcomes"] = db.replace_rows(
        conn, "offseason_outcomes", compute_outcomes(conn, log=log)
    )
    conn.commit()
    return counts
