"""Which searches to run.

Google News returns at most ~25 results per search, so one broad search
("LEC roster") misses most of an offseason. Instead: one search per team per
month of the offseason, plus a few league-wide searches. Smaller searches mean
each stays under the cap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta

# Headlines use short names, not Leaguepedia page names.
ALIASES: dict[str, list[str]] = {
    "Dplus Kia": ["Dplus KIA", "DK"],
    "Kiwoom DRX": ["DRX"],
    "DRX": ["DRX"],
    "HANJIN BRION": ["BRION"],
    "OKSavingsBank BRION": ["BRION"],
    "Nongshim RedForce": ["Nongshim", "NS RedForce"],
    "Hanwha Life Esports": ["Hanwha Life", "HLE"],
    "Natus Vincere": ["NAVI"],
    "Movistar KOI": ["KOI"],
    "Team Heretics": ["Heretics"],
    "Team Vitality": ["Vitality"],
    "Shopify Rebellion": ["Shopify Rebellion"],
    "BNK FEARX": ["FEARX", "FearX"],
    "DN SOOPers": ["DN SOOPers", "DN Freecs"],
    "Kwangdong Freecs": ["Kwangdong Freecs", "KDF"],
}
LEAGUE_TERMS = {"LCK": "LCK", "LEC": "LEC", "LCS": '"LCS" OR "LTA North"'}
RESULT_CAP = 25  # Google News RSS rarely returns more per search
MIN_WINDOW_DAYS = 4
ROSTER_TERMS = "(roster OR sources OR signs OR joins OR leaves OR contract OR transfer OR rumor)"
# Team names like "LYON", "NAVI" or "Disguised" also match cricket, Navi Mumbai and finance
# stories, and that noise filled the ~25-result cap. Requiring esports context fixes it.
# Google News matches article text, so terse headlines without "LEC" still qualify.
CONTEXT_TERMS = '(esports OR "League of Legends" OR LoL OR LCK OR LEC OR LCS)'

# Korean-language searches (LCK only). Korean outlets name teams in Hangul, and
# report signings with words like 이적 (transfer), 영입 (signing), 재계약 (re-signing),
# 결별 (parting ways). Keys are the display names `display_names()` returns.
KO_TEAM_NAMES: dict[str, list[str]] = {
    "Gen.G": ["젠지"],
    "Hanwha Life Esports": ["한화생명"],
    "Dplus Kia": ["디플러스 기아", "디플러스"],
    "KT Rolster": ["KT 롤스터", "kt 롤스터"],
    "Nongshim RedForce": ["농심 레드포스", "농심"],
    "DRX": ["DRX"],
    "Kiwoom DRX": ["키움 DRX"],
    "HANJIN BRION": ["한진 브리온", "브리온"],
    "OKSavingsBank BRION": ["OK저축은행 브리온", "브리온"],
    "BNK FEARX": ["BNK 피어엑스", "피어엑스"],
    "DN SOOPers": ["DN 수퍼스"],
    "DN Freecs": ["DN 프릭스"],
    "Kwangdong Freecs": ["광동 프릭스"],
    "Liiv SANDBOX": ["리브 샌드박스"],
}
# Short forms headlines use ("kt", 한화, 광동, DNS). Used for *matching* only: adding
# them to the search queries would change every query and invalidate the cache.
KO_TEAM_SHORT: dict[str, list[str]] = {
    "KT Rolster": ["kt", "KT"],
    "Hanwha Life Esports": ["한화"],
    "Kwangdong Freecs": ["광동"],
    "DN Freecs": ["DN 프릭스", "DNF"],
    "DN SOOPers": ["DNS"],
    "Dplus Kia": ["DK"],
    "Nongshim RedForce": ["농심"],
    "Gen.G": ["젠지"],
}
KO_ROSTER_TERMS = "(이적 OR 영입 OR 계약 OR 재계약 OR 결별 OR FA OR 로스터)"
KO_CONTEXT_TERMS = "(LCK OR 롤 OR LoL OR 리그오브레전드 OR e스포츠)"


@dataclass(frozen=True)
class Search:
    year: int  # offseason the search is for
    label: str  # team or league it targets
    query: str
    after: str | None
    before: str | None
    locale: str = "en"  # Google News edition: "en" or "ko"

    def text(self) -> str:
        parts = [self.query]
        if self.after:
            parts.append(f"after:{self.after}")
        if self.before:
            parts.append(f"before:{self.before}")
        return " ".join(parts)


def display_names(team: str, locale: str = "en") -> list[str]:
    """'LYON (2024 American Team)' → ['LYON']; known teams get their headline aliases."""
    base = re.sub(r"\s*\(.*?\)\s*$", "", team).strip()
    names = [base, *ALIASES.get(base, [])]
    if locale == "ko":
        names += KO_TEAM_NAMES.get(base, [])
    return list(dict.fromkeys(names))  # dedupe, keep order


def match_names(team: str) -> list[str]:
    """Every name a headline might use for `team`: English, Hangul and short forms."""
    base = re.sub(r"\s*\(.*?\)\s*$", "", team).strip()
    return list(dict.fromkeys(display_names(team, "ko") + KO_TEAM_SHORT.get(base, [])))


def offseason_windows(year: int, today: date) -> list[tuple[str, str]]:
    """Monthly [after, before) windows, Sep 1 of `year` to Feb 1 of `year + 1`, up to today."""
    months = [(year, 9), (year, 10), (year, 11), (year, 12), (year + 1, 1), (year + 1, 2)]
    windows = []
    for (y1, m1), (y2, m2) in zip(months, months[1:], strict=False):
        start = date(y1, m1, 1)
        if start > today:
            break
        windows.append((start.isoformat(), date(y2, m2, 1).isoformat()))
    return windows


def plan_searches(year: int, teams_by_league: dict[str, list[str]], today: date) -> list[Search]:
    searches = []
    for after, before in offseason_windows(year, today):
        for league, teams in sorted(teams_by_league.items()):
            searches.append(
                Search(year, league, f"({LEAGUE_TERMS[league]}) {ROSTER_TERMS}", after, before)
            )
            for team in sorted(teams):
                names = " OR ".join(f'"{n}"' for n in display_names(team))
                searches.append(
                    Search(year, team, f"({names}) {ROSTER_TERMS} {CONTEXT_TERMS}", after, before)
                )
        for team in sorted(teams_by_league.get("LCK", [])):
            names = " OR ".join(f'"{n}"' for n in display_names(team, "ko"))
            query = f"({names}) {KO_ROSTER_TERMS} {KO_CONTEXT_TERMS}"
            searches.append(Search(year, team, query, after, before, locale="ko"))
    return searches


def split(search: Search) -> list[Search]:
    """Halve a search's date window (adaptive splitting). [] if it's already small enough."""
    if not (search.after and search.before):
        return []
    start, end = date.fromisoformat(search.after), date.fromisoformat(search.before)
    days = (end - start).days
    if days < 2 * MIN_WINDOW_DAYS:
        return []
    mid = (start + timedelta(days=days // 2)).isoformat()
    return [replace(search, before=mid), replace(search, after=mid)]
