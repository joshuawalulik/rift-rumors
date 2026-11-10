"""Lesson 01 pipeline: Leaguepedia → SQLite ground truth.

Steps:
1. Find each league's second-half tournaments (summer split, groups, playoffs).
2. Pull their rosters. A player's "end of season" team is the team in the latest
   of those tournaments they appeared in, kept only if they were still on that team
   on the league's last day of the season. This is the population we predict for.
3. Resolve every player name to one canonical page (PlayerRedirects), then pull
   tenures, roster changes, contracts and bios under ALL of each player's names.
4. Derive `offseason_outcomes`: where each player was when the next season started,
   comparing teams through dated continuity events (renames, acquisitions, mergers).
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from rift_rumors.data import db, gcd
from rift_rumors.data.identity import PlayerIndex, TeamLineage, fold, load_manual_continuity
from rift_rumors.data.leaguepedia import CargoClient
from rift_rumors.data.outcomes import (
    ACADEMY_SUFFIX,
    ContractNews,
    Tenure,
    active_on,
    contract_end_as_of,
    is_own_academy,
    is_playing_role,
    resolve_outcome,
    tenures_from_roster_changes,
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
# Pre-registered cutoff for grading v0 (and the fallback before a season start
# is scheduled). Primary labels use each league's season-start date instead.
CUTOFF_MONTH_DAY = "02-01"
CHUNK = 40  # player names per IN (...) query, to keep URLs a sane length

SIX_HOURS = 6 * 3600
ONE_DAY = 24 * 3600


def league_code(league_name: str) -> str | None:
    return LEAGUE_NAMES.get((league_name or "").strip())


def _quote(value: str) -> str:
    """Quote a value for a Cargo `where` clause. Cargo runs on MySQL, where a
    backslash escapes the next character, so escape backslashes as well as quotes:
    otherwise a value ending in `\\` would swallow its closing quote."""
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


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


# --- 3. player identity + per-player tables --------------------------------------


def fetch_player_aliases(client: CargoClient, names: list[str]) -> list[dict]:
    """Every name each player has used, mapped to their canonical page.

    Two passes: roster names → canonical pages, then canonical pages → ALL of their
    aliases (old in-game names like "SamD" live in older tables). Cargo compares
    names case-insensitively, so case variants match too.
    """
    pages: set[str] = set()
    for chunk in _chunks(names):
        for r in client.query(
            tables="PlayerRedirects=PR",
            fields="PR.AllName=alias,PR.OverviewPage=player",
            where=_in("PR.AllName", chunk),
            max_age_s=ONE_DAY,
        ):
            if r["player"]:
                pages.add(r["player"])

    out = {}
    for chunk in _chunks(sorted(pages)):
        for r in client.query(
            tables="PlayerRedirects=PR",
            fields="PR.AllName=alias,PR.OverviewPage=player",
            where=_in("PR.OverviewPage", chunk),
            max_age_s=ONE_DAY,
        ):
            if r["alias"] and r["player"]:
                out[(r["alias"], r["player"])] = {"alias": r["alias"], "player": r["player"]}
    return list(out.values())


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
                "source": "leaguepedia",
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


# Leaguepedia's verbs, with its typos ("acuire", "reband") normalized. All of them
# mean "same seat" (decision 2026-11-10: acquisitions and mergers keep the seat).
VERB_FIXES = {"acuire": "acquire", "reband": "rebrand"}


def fetch_team_continuity(client: CargoClient) -> list[dict]:
    """Dated renames, rebrands and acquisitions from Leaguepedia's TeamRenames table.

    Replaces `Teams.RenamedTo`, which had no dates and missed links
    (e.g. FearX → BNK FEARX, Kwangdong Freecs → DN Freecs).
    """
    rows = client.query(
        tables="TeamRenames=TR",
        fields="TR.Date=date,TR.OriginalName=old_name,TR.NewName=new_name,TR.Verb=verb",
        order_by="TR.Date",
        max_age_s=ONE_DAY,
    )
    out = []
    for r in rows:
        if not (r["old_name"] and r["new_name"]) or r["old_name"] == r["new_name"]:
            continue
        verb = (r["verb"] or "").strip().lower()
        out.append(
            {
                "date": _date(r["date"]),
                "old_name": r["old_name"],
                "new_name": r["new_name"],
                "verb": VERB_FIXES.get(verb, verb) or None,
                "source": "leaguepedia",
            }
        )
    return out


def _canonicalize(rows: list[dict], index: PlayerIndex) -> list[dict]:
    return [{**r, "player": index.canonical(r["player"])} for r in rows]


def season_starts(tournaments: list[dict]) -> list[dict]:
    """Each league's first tournament of each year: the day its season starts.

    This is the label cutoff: "where was the player when their league's next
    season began?" It's an event, not an arbitrary calendar date. Exercise 4
    showed a Jan 1 vs Feb 1 choice flips 1 in 14 labels.
    """
    first: dict[tuple[str, int], dict] = {}
    for t in tournaments:
        key = (t["league"], t["year"])
        if t["date_start"] and (key not in first or t["date_start"] < first[key]["date_start"]):
            first[key] = t
    return [
        {"league": league, "year": year, "date_start": t["date_start"], "page": t["page"]}
        for (league, year), t in sorted(first.items())
    ]


def fetch_team_orgs(client: CargoClient, teams: list[str]) -> list[dict]:
    """Leaguepedia's organization page per team (often empty), used to spot academies."""
    out = []
    for chunk in _chunks(teams):
        for r in client.query(
            tables="Teams=T",
            fields="T.OverviewPage=team,T.OrganizationPage=org",
            where=_in("T.OverviewPage", chunk),
            max_age_s=ONE_DAY,
        ):
            if r["team"] and r["org"]:
                out.append({"team": r["team"], "org": r["org"]})
    return out


# --- 4. derived labels ----------------------------------------------------------


def compute_outcomes(conn: sqlite3.Connection, today: date | None = None, log=print) -> list[dict]:
    today_iso = (today or date.today()).isoformat()
    continuity = [dict(r) for r in conn.execute("SELECT * FROM team_renames")]
    lineages: dict[str, TeamLineage] = {}

    def lineage(as_of: str) -> TeamLineage:
        if as_of not in lineages:
            lineages[as_of] = TeamLineage.as_of(continuity, as_of)
        return lineages[as_of]

    starts = {
        (r["league"], r["year"]): r["date_start"]
        for r in conn.execute("SELECT * FROM season_starts")
    }
    orgs = {r["team"]: r["org"] for r in conn.execute("SELECT * FROM team_orgs")}

    changes_by_player: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    join_role: dict[tuple[str, str, str], str | None] = {}
    for r in conn.execute("SELECT * FROM roster_changes ORDER BY date"):
        changes_by_player[r["player"]].append((r["date"], r["team"], r["direction"]))
        if r["direction"] == "Join":
            join_role[(r["player"], fold(r["team"]), r["date"])] = r["role"]

    # Only PLAYING stints count. A stint's role is the role on the Join that
    # opened it; analysts, coaches, casters and streamers are dropped.
    tenures_by_player: dict[str, list[Tenure]] = defaultdict(list)
    non_playing = 0
    for r in conn.execute("SELECT * FROM tenures ORDER BY date_join"):
        role = join_role.get((r["player"], fold(r["team"]), r["date_join"]))
        if not is_playing_role(role):
            non_playing += 1
            continue
        tenures_by_player[r["player"]].append(
            Tenure(r["player"], r["team"], r["date_join"], r["date_leave"])
        )
    log(f"ignored {non_playing} non-playing stints (analyst, coach, caster, streamer...)")

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

    def label(team_before: str, tenures: list[Tenure], cutoff: str) -> tuple[str | None, str]:
        if cutoff > today_iso:
            return None, "pending"  # not known yet
        resolved = resolve_outcome(team_before, tenures, cutoff, lineage(cutoff))
        return resolved.team_after, resolved.outcome

    outcomes, excluded = [], Counter()
    for (year, player), row in sorted(latest.items()):
        season_end = season_end_by_league_year[(row["league"], year)]
        feb1 = f"{year + 1}-{CUTOFF_MONTH_DAY}"
        # Event-based cutoff: the day this league's next season starts. Before that
        # is scheduled (the live offseason), fall back to Feb 1, which is still pending.
        cutoff = starts.get((row["league"], year + 1)) or feb1
        tenures = tenures_by_player[player]

        if not tenures and not changes_by_player[player]:
            excluded["no stint or roster-change data at all"] += 1
            continue

        # Keep only players still on that team when the season ended. Someone released
        # in July didn't make an *offseason* move, so they aren't in this population.
        teams = lineage(cutoff)
        if not any(teams.same(t.team, row["team"]) and active_on(t, season_end) for t in tenures):
            excluded["no active stint on that team at season end (left mid-season, "
                     "or an academy call-up)"] += 1  # fmt: skip
            continue

        team_after, outcome = label(row["team"], tenures, cutoff)
        team_after_feb1, outcome_feb1 = label(row["team"], tenures, feb1)
        move_type = None
        if outcome == "moved":
            demoted = is_own_academy(row["team"], team_after, teams, orgs)
            move_type = "demotion" if demoted else "transfer"

        # Roster changes inside this offseason window only (not, say, a 2025 status
        # change attached to a player who stayed through the 2023 offseason).
        window_end = min(cutoff, today_iso)
        first_move = next(
            (d for d, _, _ in changes_by_player[player] if season_end < d <= window_end), None
        )
        outcomes.append(
            {
                "year": year,
                "league": row["league"],
                "player": player,
                "role": row["role"],
                "team_before": row["team"],
                "season_end": season_end,
                # Point-in-time: only contracts announced by season_end.
                "contract_end": contract_end_as_of(
                    contracts_by_player[player], row["team"], season_end, lineage(season_end)
                ),
                "cutoff": cutoff,
                "team_after": team_after,
                "outcome": outcome,
                "move_type": move_type,
                "team_after_feb1": team_after_feb1,
                "outcome_feb1": outcome_feb1,
                "first_move_date": first_move,
            }
        )

    for reason, n in excluded.most_common():
        log(f"excluded {n:>3}: {reason}")
    return outcomes


def rebuilt_tenures(tenures: list[dict], changes: list[dict]) -> list[dict]:
    """Stints rebuilt from roster changes, for players Leaguepedia has none for."""
    has_tenures = {t["player"] for t in tenures}
    events: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for c in changes:
        if c["player"] not in has_tenures and is_playing_role(c["role"]):
            events[c["player"]].append((c["date"], c["team"], c["direction"]))
    rows = []
    for player, evs in events.items():
        for t in tenures_from_roster_changes(player, evs):
            rows.append(
                {
                    "source": "roster_changes",
                    "player": player,
                    "team": t.team,
                    "date_join": t.date_join,
                    "date_leave": t.date_leave,
                    "contract_end": None,
                    "next_team": None,
                    "next_is_retired": 0,
                    "is_current": int(t.date_leave is None),
                }
            )
    return rows


# --- orchestration --------------------------------------------------------------


def build(
    client: CargoClient,
    conn: sqlite3.Connection,
    gcd_dir: Path | None = None,
    continuity_path: Path | None = None,
    log=print,
) -> dict[str, int]:
    all_tournaments = fetch_tournaments(client)
    second_half = second_half_tournaments(all_tournaments)
    log(f"second-half tournaments: {len(second_half)}")
    for t in second_half:
        log(f"  {t['league']} {t['year']}  {t['date_start']} → {t['date_end']}  {t['page']}")

    raw_rosters = fetch_rosters(client, [t["page"] for t in second_half])
    aliases = fetch_player_aliases(client, sorted({r["player"] for r in raw_rosters}))
    index = PlayerIndex((a["alias"], a["player"]) for a in aliases)
    if index.ambiguous:
        log(f"ambiguous player aliases (left unresolved): {sorted(index.ambiguous)}")

    rosters = _canonicalize(raw_rosters, index)
    canonical = sorted({r["player"] for r in rosters})
    # Query under every name each player has used: old tables keep old names.
    all_names = sorted({a["alias"] for a in aliases} | {r["player"] for r in raw_rosters})
    log(f"players on second-half rosters: {len(canonical)} ({len(all_names)} names incl. aliases)")

    tenures = _canonicalize(fetch_tenures(client, all_names), index)
    changes = _canonicalize(fetch_roster_changes(client, all_names), index)
    tenures += rebuilt_tenures(tenures, changes)
    continuity = fetch_team_continuity(client)
    if continuity_path is not None:
        continuity += load_manual_continuity(continuity_path)
    # Organization pages only matter for spotting academies of our teams.
    org_teams = sorted(
        {r["team"] for r in rosters}
        | {t["team"] for t in tenures if ACADEMY_SUFFIX.search(t["team"] or "")}
    )

    counts = {
        "tournaments": db.replace_rows(conn, "tournaments", second_half),
        "season_starts": db.replace_rows(conn, "season_starts", season_starts(all_tournaments)),
        "tournament_rosters": db.replace_rows(conn, "tournament_rosters", rosters),
        "player_aliases": db.replace_rows(conn, "player_aliases", aliases),
        "team_renames": db.replace_rows(conn, "team_renames", continuity),
        "team_orgs": db.replace_rows(conn, "team_orgs", fetch_team_orgs(client, org_teams)),
        "tenures": db.replace_rows(conn, "tenures", tenures),
        "roster_changes": db.replace_rows(conn, "roster_changes", changes),
        "contracts": db.replace_rows(
            conn, "contracts", _canonicalize(fetch_contracts(client, all_names), index)
        ),
        "players": db.replace_rows(conn, "players", fetch_players(client, canonical)),
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
