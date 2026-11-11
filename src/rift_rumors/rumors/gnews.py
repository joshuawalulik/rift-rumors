"""Google News RSS search: the rumor source that still works without an account.

Why this source (Lesson 02, §2): Reddit's API needs approval that isn't being
granted to new projects, and its RSS feeds return 429 after one anonymous
request. X's API is paid. Google News RSS aggregates esports outlets, most
importantly Sheep Esports, whose "Sources: X set to join Y" headlines are the
main public record of roster rumors. It also supports `after:`/`before:` date
bounds, so past offseasons can be searched too, which makes rumor features
backtestable.

We store **headlines, outlet, link and publish time only**, never article bodies.
"""

from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

SEARCH_URL = "https://news.google.com/rss/search"
_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class NewsItem:
    id: str  # stable: hash of outlet + headline (Google's links vary between queries)
    outlet: str
    title: str  # headline with the " - Outlet" suffix removed
    url: str
    published_at: str  # UTC ISO, from the feed's pubDate


# Google News edition per language. The Korean edition surfaces Korean outlets
# (Yonhap, News1, Inven, Daily Esports), which often report LCK moves first.
LOCALES = {
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "ko": {"hl": "ko", "gl": "KR", "ceid": "KR:ko"},
}


def search_url(query: str, locale: str = "en") -> str:
    params = {"q": query, **LOCALES[locale]}
    return f"{SEARCH_URL}?{urlencode(params)}"


def item_id(outlet: str, title: str) -> str:
    key = f"{outlet.strip().lower()}|{' '.join(title.lower().split())}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def parse(xml_text: str) -> list[NewsItem]:
    root = ET.fromstring(xml_text)
    items = []
    for node in root.iter("item"):
        outlet = (node.findtext("source") or "").strip()
        title = html.unescape(_TAGS.sub("", node.findtext("title") or "")).strip()
        if outlet and title.endswith(f" - {outlet}"):
            title = title[: -len(f" - {outlet}")].strip()
        pub = node.findtext("pubDate")
        if not title or not pub:
            continue
        published = parsedate_to_datetime(pub).astimezone(UTC).isoformat(timespec="seconds")
        items.append(
            NewsItem(
                id=item_id(outlet, title),
                outlet=outlet or "unknown",
                title=title,
                url=(node.findtext("link") or "").strip(),
                published_at=published,
            )
        )
    return items
