"""Link rumor headlines to players and teams by string matching.

This is *candidate generation*: cheap and high-recall. It finds headlines that
might be about a player, and Lesson 03's LLM decides what each one actually
claims. Matching rules:

- Each headline belongs to one offseason: published between Sep 1 of year Y and
  Feb 1 of Y+1 → year Y. It's matched only against that year's player population.
- A player matches on their handle or any alias (from Leaguepedia redirects, so
  renamed players still match). Matching is case-sensitive, on word boundaries,
  4+ characters.
- Handles that are also English words ("Faker", "Clear", "Career") are flagged
  `common_word`, so we can measure whether those matches are less precise.
- A name used by two players in the same year is ambiguous: skipped, and counted.
- Leaguepedia rumor rows (`rumor_claims`) name their player exactly, so they're
  linked directly (`structured`), not by text matching.
- Hangul handles (from `config/ko_handles.csv`, see ko_handles.py) are matched
  as `player_ko`. They're short and some are ordinary words (지우 "Jiwoo" vs
  지우다 "to erase"), so they must stand alone: quoted, in a "·" list, or followed
  only by a particle (은/는/이/가...).
- Word boundaries only look at Latin letters and digits: Korean attaches particles
  directly to names ("T1이", "젠지는"), so a Unicode `\b` would miss them.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from rift_rumors.rumors.plan import match_names

MIN_NAME_LEN = 4
DICTIONARY = Path("/usr/share/dict/words")


@lru_cache(maxsize=1)
def english_words() -> frozenset[str]:
    if not DICTIONARY.exists():
        return frozenset()
    return frozenset(w.strip().lower() for w in DICTIONARY.read_text().splitlines())


def offseason_year(published_at: str) -> int:
    """'2025-11-16T…' → 2025; '2026-01-20T…' → 2025 (still the 2025 offseason)."""
    year, month = int(published_at[:4]), int(published_at[5:7])
    return year - 1 if month < 9 else year


def word_pattern(name: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])")


_KO_BEFORE = r"(?:^|(?<=[\s'‘’\"“”`·,(\[]))"
_KO_AFTER = r"(?=$|[\s'‘’\"“”`·,)\]…]|(?:이|가|은|는|을|를|와|과|의|도|로|에|까지|만)(?![가-힣]))"


def hangul_pattern(name: str) -> re.Pattern:
    return re.compile(f"{_KO_BEFORE}{re.escape(name)}{_KO_AFTER}")


_HAS_HANGUL = re.compile(r"[가-힣]")


def build_index(gt: sqlite3.Connection) -> tuple[dict, dict, dict]:
    """Per year: {name: player}, {player: team_before}, {team display name: team}."""
    aliases = defaultdict(set)
    for r in gt.execute("SELECT alias, player FROM player_aliases"):
        aliases[r[1]].add(r[0])
    handles = {r[0]: r[1] for r in gt.execute("SELECT player, handle FROM players")}

    names_by_year: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    team_of: dict[tuple[int, str], str] = {}
    teams_by_year: dict[int, dict[str, str]] = defaultdict(dict)
    for r in gt.execute("SELECT year, player, team_before FROM offseason_outcomes"):
        year, player, team = r[0], r[1], r[2]
        team_of[(year, player)] = team
        base = player.split(" (")[0]  # page names like "Hype (Byeon Jeong-hyeon)"
        for name in {base, handles.get(player) or base, *aliases.get(player, ())}:
            if name and len(name) >= MIN_NAME_LEN:
                names_by_year[year][name].add(player)
        for display in match_names(team):  # English, Hangul and short forms
            if len(display) >= 2:
                teams_by_year[year][display] = team
    return names_by_year, team_of, teams_by_year


def link(
    rumors: sqlite3.Connection, gt: sqlite3.Connection, ko_names: dict[str, str] | None = None
) -> dict[str, int]:
    """`ko_names`: {Hangul handle: player page}, e.g. from ko_handles.active()."""
    names_by_year, team_of, teams_by_year = build_index(gt)
    ko_patterns = {name: hangul_pattern(name) for name in (ko_names or {})}
    words = english_words()
    patterns: dict[str, re.Pattern] = {}

    canonical = {r[0]: r[1] for r in gt.execute("SELECT alias, player FROM player_aliases")}
    claims = {r["item_id"]: r for r in rumors.execute("SELECT * FROM rumor_claims")}

    rumors.execute("DELETE FROM rumor_mentions")
    stats = defaultdict(int)
    items = rumors.execute("SELECT id, title, published_at FROM rumor_items").fetchall()
    for item in items:
        year = offseason_year(item["published_at"])
        names = names_by_year.get(year)
        if not names:
            stats["items_outside_known_years"] += 1
            continue
        if item["id"] in claims:
            stats[_link_claim(rumors, item["id"], year, claims[item["id"]], canonical,
                              team_of, set(teams_by_year[year].values()))] += 1  # fmt: skip
            continue
        title = item["title"]
        matched_any = False
        for name, players in names.items():
            pattern = patterns.setdefault(name, word_pattern(name))
            if not pattern.search(title):
                continue
            if len(players) > 1:
                stats["ambiguous_name_skipped"] += 1
                continue
            (player,) = players
            kind = "player_common_word" if name.lower() in words else "player"
            rumors.execute(
                "INSERT INTO rumor_mentions VALUES (?,?,?,?,?,?)",
                (item["id"], year, player, team_of[(year, player)], kind, name),
            )
            stats[kind] += 1
            matched_any = True
        if ko_patterns and _HAS_HANGUL.search(title):
            for name, pattern in ko_patterns.items():
                player = ko_names[name]
                if (year, player) in team_of and pattern.search(title):
                    rumors.execute(
                        "INSERT INTO rumor_mentions VALUES (?,?,?,?,?,?)",
                        (item["id"], year, player, team_of[(year, player)], "player_ko", name),
                    )
                    stats["player_ko"] += 1
                    matched_any = True
        for display, team in teams_by_year[year].items():
            pattern = patterns.setdefault(display, word_pattern(display))
            if pattern.search(title):
                rumors.execute(
                    "INSERT INTO rumor_mentions VALUES (?,?,?,?,?,?)",
                    (item["id"], year, None, team, "team", display),
                )
                stats["team"] += 1
                matched_any = True
        stats["items_with_a_match" if matched_any else "items_with_no_match"] += 1
    rumors.commit()
    return dict(stats)


def _link_claim(rumors, item_id, year, claim, canonical, team_of, teams) -> str:
    """Link a structured Leaguepedia rumor. Returns the stats key it counts under."""
    player = canonical.get(claim["player"], claim["player"])
    if (year, player) in team_of:
        rumors.execute(
            "INSERT INTO rumor_mentions VALUES (?,?,?,?,?,?)",
            (item_id, year, player, team_of[(year, player)], "structured", claim["player"]),
        )
        return "structured"
    # Not one of our players (e.g. an ERL player rumored to join an LEC team):
    # still useful as a team-level signal if either team is in the population.
    hit = False
    for team in {claim["team_start"], claim["team_end"]} & teams:
        rumors.execute(
            "INSERT INTO rumor_mentions VALUES (?,?,?,?,?,?)",
            (item_id, year, None, team, "team", team),
        )
        hit = True
    return "structured_team_only" if hit else "structured_outside_population"
