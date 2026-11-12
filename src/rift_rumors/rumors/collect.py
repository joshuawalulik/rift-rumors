"""Run searches and store what they find.

- `backfill(year)`: date-bounded searches for a past offseason. Cached forever,
  because a search for "Nov 2024" returns the same thing tomorrow.
- `collect_live(year)`: the current offseason. Searches re-run when their cache
  is older than `LIVE_MAX_AGE_S`, so new headlines appear. Run it a few times a
  day through the offseason: each search only returns ~25 items, so an item
  pushed out of the results before we look is gone.
- `add_manual(...)`: a rumor you saw yourself (Reddit, X) and paste in, with its URL.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from datetime import UTC, date, datetime, timedelta

from rift_rumors.rumors import gnews, store
from rift_rumors.rumors.fetch import CachedFetcher
from rift_rumors.rumors.plan import RESULT_CAP, plan_searches, split

LIVE_MAX_AGE_S = 3 * 3600


def teams_by_league(gt: sqlite3.Connection, year: int) -> dict[str, list[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for r in gt.execute(
        "SELECT DISTINCT league, team_before FROM offseason_outcomes WHERE year = ?", (year,)
    ):
        out[r[0]].add(r[1])
    return {league: sorted(teams) for league, teams in out.items()}


def run_searches(
    fetcher: CachedFetcher,
    rumors: sqlite3.Connection,
    gt: sqlite3.Connection,
    year: int,
    *,
    today: date,
    max_age_s: float | None,
    log=print,
) -> dict[str, int]:
    queue = deque(plan_searches(year, teams_by_league(gt, year), today))
    planned = len(queue)
    # A window that ended over a week ago won't gain new headlines: keep it cached.
    settled_before = (today - timedelta(days=7)).isoformat()
    totals = defaultdict(int)
    while queue:
        search = queue.popleft()
        settled = search.before is not None and search.before <= settled_before
        body, fetched_at = fetcher.get(
            gnews.search_url(search.text(), search.locale),
            max_age_s=None if settled else max_age_s,
        )
        items = gnews.parse(body)
        source = "gnews" if search.locale == "en" else f"gnews_{search.locale}"
        new = store.upsert_items(
            rumors, items, source=source, query=search.text(), fetched_at=fetched_at
        )
        totals["searches"] += 1
        totals["results"] += len(items)
        totals["new_items"] += new
        if len(items) >= RESULT_CAP:
            # Probably truncated: search each half of the window instead.
            halves = split(search)
            if halves:
                queue.extend(halves)
                totals["splits"] += 1
            else:
                totals["still_capped_at_min_window"] += 1
        if totals["searches"] % 25 == 0:
            log(
                f"  {totals['searches']} searches ({planned} planned + splits), "
                f"{totals['new_items']} new items"
            )
    return dict(totals)


def backfill(fetcher, rumors, gt, year: int, log=print) -> dict[str, int]:
    return run_searches(
        fetcher, rumors, gt, year, today=date(year + 1, 2, 1), max_age_s=None, log=log
    )


def collect_live(fetcher, rumors, gt, year: int, log=print) -> dict[str, int]:
    """Re-fetch searches for recent months; months that ended over a week ago stay cached."""
    return run_searches(
        fetcher, rumors, gt, year, today=date.today(), max_age_s=LIVE_MAX_AGE_S, log=log
    )


def add_manual(
    rumors: sqlite3.Connection,
    *,
    title: str,
    url: str,
    outlet: str,
    published_at: str | None,
    note: str | None,
) -> str:
    """Record a rumor seen by hand. `published_at` defaults to now. Be honest about it."""
    published = published_at or datetime.now(UTC).isoformat(timespec="seconds")
    item = gnews.NewsItem(
        id=gnews.item_id(outlet, title), outlet=outlet, title=title, url=url, published_at=published
    )
    store.upsert_items(
        rumors, [item], source="manual", query="manual", fetched_at=store.now_utc(), note=note
    )
    return item.id
