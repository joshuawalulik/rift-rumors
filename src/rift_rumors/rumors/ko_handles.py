"""A Hangul handle map, so Korean headlines link to players.

Korean outlets write handles in Hangul, inside quotes: "'제우스' 영입, 한화생명…"
('Zeus' signed by Hanwha Life). Our English matcher can't see that, so Korean
headlines linked to teams but almost never to players.

How the map is built (Lesson 02, §7), with an LLM as a *drafting tool*, not an oracle:

1. **Harvest** quoted Hangul words from the Korean headlines we already have.
   Most are handles; some are ordinary words (롤드컵 "Worlds", 주목 "spotlight").
2. **Draft**: Claude gets our player list and the harvested words, and proposes
   Hangul spellings for handles. It may only answer with handles from our list.
   It must never give legal names: we don't store them.
3. **Validate** in code: the handle must exist, the Hangul must be 2+ syllables,
   and one spelling can't map to two players. Then each mapping is checked against
   the headlines: how often it appears, and how often alongside the player's team.
4. **Review**: a person spot-checks `config/ko_handles.csv` and sets `verified` to
   yes/no. Rows marked `no` are ignored. Re-running keeps every existing row and
   only adds new ones, so review work is never lost.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

QUOTES = "'‘’\"“”`"
HANGUL = re.compile(r"^[가-힣]{2,10}$")
_QUOTED = re.compile(rf"[{QUOTES}]([가-힣][가-힣A-Za-z0-9]{{0,9}})[{QUOTES}]")
FIELDS = ["hangul", "player", "handle", "proposed_by", "verified"]

SYSTEM = (
    "You are helping build a lookup table for an esports research project. "
    "Answer only with JSON. Never include anyone's legal name."
)

PROMPT = """Korean esports outlets write League of Legends pro players' handles in Hangul,
e.g. 'Zeus' -> '제우스', 'Gumayusi' -> '구마유시', 'Faker' -> '페이커'.

Below are (1) the players in our dataset and (2) quoted Hangul words that appeared in
Korean roster headlines, with counts. Some quoted words are handles; others are ordinary
words, team names or nicknames for groups of players.

Task: for every player in list (1) whose handle Korean media would write in Hangul,
give the Hangul spelling(s) Korean outlets actually use. Prefer spellings seen in list (2);
include common variants (e.g. '스매쉬' and '스매시'). Rules:
- "handle" must be copied exactly from list (1).
- Only handle transliterations. Never a legal name (e.g. never '이상혁' for Faker).
- Skip a player if you are not confident; a missing row is better than a wrong one.

Return: {{"mappings": [{{"handle": "Zeus", "hangul": ["제우스"]}}, ...]}}

(1) Players (handle | teams):
{players}

(2) Quoted Hangul words in headlines (word count):
{candidates}
"""


def harvest(rumors: sqlite3.Connection) -> Counter:
    """Quoted Hangul words in Korean headlines, with how many times each appears."""
    counts = Counter()
    for (title,) in rumors.execute("SELECT title FROM rumor_items WHERE source = 'gnews_ko'"):
        counts.update(_QUOTED.findall(title))
    return counts


def population(gt: sqlite3.Connection) -> dict[str, dict]:
    """{handle: {"players": {page names}, "teams": {team (league)}}} across all years."""
    handles = {r[0]: r[1] for r in gt.execute("SELECT player, handle FROM players")}
    out: dict[str, dict] = defaultdict(lambda: {"players": set(), "teams": set()})
    for r in gt.execute("SELECT player, league, team_before FROM offseason_outcomes"):
        handle = handles.get(r[0]) or r[0].split(" (")[0]
        out[handle]["players"].add(r[0])
        out[handle]["teams"].add(f"{r[2]} ({r[1]})")
    return dict(out)


def build_prompt(pop: dict[str, dict], candidates: Counter) -> str:
    players = "\n".join(
        f"{h} | {', '.join(sorted(v['teams']))}"
        for h, v in sorted(pop.items(), key=lambda x: x[0].lower())
    )
    words = ", ".join(f"{w} {n}" for w, n in candidates.most_common())
    return PROMPT.format(players=players, candidates=words)


def parse_reply(text: str) -> list[tuple[str, str]]:
    """[(handle, hangul)] from the model's JSON. Tolerates text around the JSON object."""
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start : end + 1])
    pairs = []
    for m in data.get("mappings", []):
        spellings = m.get("hangul", [])
        for hangul in [spellings] if isinstance(spellings, str) else spellings:
            pairs.append((str(m.get("handle", "")).strip(), str(hangul).strip()))
    return pairs


def validate(pairs, pop: dict[str, dict]) -> tuple[list[dict], Counter]:
    """Keep mappings that pass the rules. Returns rows and a count of why others were dropped."""
    dropped = Counter()
    by_hangul: dict[str, set[str]] = defaultdict(set)
    for handle, hangul in pairs:
        if handle not in pop:
            dropped["handle_not_in_dataset"] += 1
        elif not HANGUL.match(hangul):
            dropped["not_2plus_hangul_syllables"] += 1
        elif len(pop[handle]["players"]) > 1:
            dropped["handle_shared_by_two_players"] += 1
        else:
            by_hangul[hangul].add(handle)
    rows = []
    for hangul, handles in sorted(by_hangul.items()):
        if len(handles) > 1:
            dropped["hangul_maps_to_two_handles"] += 1
            continue
        (handle,) = handles
        (player,) = pop[handle]["players"]
        rows.append({"hangul": hangul, "player": player, "handle": handle})
    return rows, dropped


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def merge_and_write(path: Path, new_rows: list[dict], proposed_by: str) -> int:
    """Add rows for Hangul spellings not already in the file. Existing rows (and reviews) stay."""
    rows = load(path)
    known = {r["hangul"] for r in rows}
    added = [
        {**r, "proposed_by": proposed_by, "verified": ""}
        for r in new_rows
        if r["hangul"] not in known
    ]
    rows += added
    rows.sort(key=lambda r: (r["handle"].lower(), r["hangul"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(added)


def active(path: Path) -> dict[str, str]:
    """{hangul: player} for rows not rejected in review."""
    return {r["hangul"]: r["player"] for r in load(path) if r.get("verified", "").lower() != "no"}


def support(rumors: sqlite3.Connection, gt: sqlite3.Connection, mapping: dict[str, str]):
    """For each mapping: headlines quoting it, and how many also name the player's team.

    A handle that keeps appearing next to the player's own team is very likely right.
    One that never appears is untested: it came from the model's knowledge alone.
    """
    from rift_rumors.rumors.plan import match_names

    teams = defaultdict(set)
    for r in gt.execute("SELECT player, team_before FROM offseason_outcomes"):
        teams[r[0]].update(match_names(r[1]))
    titles = [t for (t,) in rumors.execute("SELECT title FROM rumor_items WHERE source='gnews_ko'")]
    out = {}
    for hangul, player in mapping.items():
        quoted = re.compile(rf"[{QUOTES}]{re.escape(hangul)}[{QUOTES}]")
        hits = [t for t in titles if quoted.search(t)]
        with_team = sum(1 for t in hits if any(name in t for name in teams[player]))
        out[hangul] = (len(hits), with_team)
    return out
