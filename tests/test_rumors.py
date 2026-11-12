"""Rumor pipeline tests: parsing, planning, append-only storage, linking. No network."""

from datetime import date

import httpx
import pytest

from rift_rumors.data import db
from rift_rumors.rumors import collect, gnews, link, store
from rift_rumors.rumors.fetch import CachedFetcher
from rift_rumors.rumors.plan import display_names, offseason_windows, plan_searches

FEED = """<?xml version="1.0"?><rss><channel>
<item><title>Sources: Zyrex set to join Nova Esports for 2027 - Sheep Esports</title>
<link>https://news.google.com/rss/articles/abc</link>
<pubDate>Sun, 08 Nov 2026 23:16:00 GMT</pubDate><source url="https://www.sheepesports.com">Sheep Esports</source></item>
<item><title>Clear outlook for Team Aurora &amp; fans - Dexerto</title>
<link>https://news.google.com/rss/articles/def</link>
<pubDate>Mon, 09 Nov 2026 10:00:00 GMT</pubDate><source url="https://www.dexerto.com">Dexerto</source></item>
<item><title>No date, skipped - X</title><source>X</source></item>
</channel></rss>"""


def test_parse_strips_outlet_suffix_and_normalizes_time():
    items = gnews.parse(FEED)
    assert [i.title for i in items] == [
        "Sources: Zyrex set to join Nova Esports for 2027",
        "Clear outlook for Team Aurora & fans",
    ]
    assert items[0].outlet == "Sheep Esports"
    assert items[0].published_at == "2026-11-08T23:16:00+00:00"


def test_item_id_is_stable_across_link_and_whitespace_changes():
    assert gnews.item_id("Sheep Esports", "A  b") == gnews.item_id("sheep esports", "a b")


def test_display_names_strip_disambiguation_and_add_aliases():
    assert display_names("LYON (2024 American Team)") == ["LYON"]
    assert "NAVI" in display_names("Natus Vincere")


def test_windows_stop_at_today():
    assert offseason_windows(2026, date(2026, 11, 10)) == [
        ("2026-09-01", "2026-10-01"),
        ("2026-10-01", "2026-11-01"),
        ("2026-11-01", "2026-12-01"),
    ]
    assert len(offseason_windows(2025, date(2026, 2, 1))) == 5


def test_plan_has_league_and_team_searches_per_window():
    searches = plan_searches(2025, {"LEC": ["G2 Esports", "Fnatic"]}, date(2026, 2, 1))
    assert len(searches) == 5 * 3  # 5 months × (1 league + 2 teams)
    assert "after:2025-11-01 before:2025-12-01" in searches[6].text()


def test_offseason_year():
    assert link.offseason_year("2025-11-16T00:00:00+00:00") == 2025
    assert link.offseason_year("2026-01-20T00:00:00+00:00") == 2025
    assert link.offseason_year("2026-09-02T00:00:00+00:00") == 2026


# --- append-only store --------------------------------------------------------


def test_first_seen_is_never_overwritten(tmp_path):
    conn = store.connect(tmp_path / "r.db")
    items = gnews.parse(FEED)
    assert store.upsert_items(conn, items, source="gnews", query="q1", fetched_at="T1") == 2
    assert store.upsert_items(conn, items, source="gnews", query="q2", fetched_at="T2") == 0
    row = conn.execute("SELECT first_seen_at, last_seen_at FROM rumor_items LIMIT 1").fetchone()
    assert (row[0], row[1]) == ("T1", "T2")
    assert conn.execute("SELECT COUNT(*) FROM rumor_query_hits").fetchone()[0] == 4


def test_manual_rumor_is_marked(tmp_path):
    conn = store.connect(tmp_path / "r.db")
    collect.add_manual(conn, title="Zyrex to Nova", url="https://x.com/1", outlet="X @leaker",
                       published_at="2026-11-09T12:00:00+00:00", note="seen on X")  # fmt: skip
    row = conn.execute("SELECT source, note FROM rumor_items").fetchone()
    assert (row[0], row[1]) == ("manual", "seen on X")


# --- linking -------------------------------------------------------------------


@pytest.fixture
def gt(tmp_path):
    conn = db.connect(tmp_path / "gt.db")
    conn.execute("INSERT INTO players (player, handle) VALUES ('Zyrex', 'Zyrex')")
    conn.execute("INSERT INTO players (player, handle) VALUES ('Clear (Song)', 'Clear')")
    conn.execute("INSERT INTO player_aliases VALUES ('Zyrexx', 'Zyrex')")
    for player, team in (("Zyrex", "Team Aurora"), ("Clear (Song)", "Team Aurora")):
        conn.execute(
            "INSERT INTO offseason_outcomes (year, league, player, role, team_before, season_end,"
            " cutoff, outcome, outcome_feb1)"
            " VALUES (2026, 'LEC', ?, 'Mid', ?, '2026-09-13', '2027-01-17', 'pending', 'pending')",
            (player, team),
        )
    conn.commit()
    return conn


def test_link_players_teams_and_common_words(tmp_path, gt):
    rumors = store.connect(tmp_path / "r.db")
    store.upsert_items(rumors, gnews.parse(FEED), source="gnews", query="q", fetched_at="T")
    stats = link.link(rumors, gt)
    rows = {
        (r["matched"], r["match_kind"], r["player"])
        for r in rumors.execute("SELECT * FROM rumor_mentions")
    }
    assert ("Zyrex", "player", "Zyrex") in rows
    assert ("Team Aurora", "team", None) in rows
    # "Clear" is an English word: matched, but flagged for precision analysis
    if link.english_words():
        assert ("Clear", "player_common_word", "Clear (Song)") in rows
    assert stats["items_with_a_match"] == 2


def test_relinking_replaces_mentions(tmp_path, gt):
    rumors = store.connect(tmp_path / "r.db")
    store.upsert_items(rumors, gnews.parse(FEED), source="gnews", query="q", fetched_at="T")
    link.link(rumors, gt)
    first = rumors.execute("SELECT COUNT(*) FROM rumor_mentions").fetchone()[0]
    link.link(rumors, gt)
    assert rumors.execute("SELECT COUNT(*) FROM rumor_mentions").fetchone()[0] == first


# --- fetcher -------------------------------------------------------------------


def test_fetcher_caches_and_backs_off(tmp_path):
    responses = iter([httpx.Response(503), httpx.Response(200, text="<rss/>")])
    sleeps = []
    fetcher = CachedFetcher(
        tmp_path, http=httpx.Client(transport=httpx.MockTransport(lambda r: next(responses))),
        sleep=sleeps.append, log=lambda m: None, min_interval_s=0,
    )  # fmt: skip
    body, _ = fetcher.get("https://example.test/feed")
    assert body == "<rss/>" and sleeps == [15]
    again, _ = fetcher.get("https://example.test/feed")  # cached: StopIteration if it re-fetched
    assert again == "<rss/>" and fetcher.requests_made == 2


def test_split_halves_window_until_minimum():
    from rift_rumors.rumors.plan import Search, split

    month = Search(2026, "G2", "q", "2026-11-01", "2026-12-01")
    a, b = split(month)
    assert (a.after, a.before, b.after, b.before) == (
        "2026-11-01", "2026-11-16", "2026-11-16", "2026-12-01"
    )  # fmt: skip
    assert split(Search(2026, "G2", "q", "2026-11-01", "2026-11-07")) == []


def test_capped_search_is_split_and_rerun(tmp_path, gt):
    import re as _re

    def feed(n):
        rows = "".join(
            f"<item><title>Headline {n}-{i} - Sheep Esports</title><link>u</link>"
            f"<pubDate>Sun, 08 Nov 2026 23:16:00 GMT</pubDate><source>Sheep Esports</source></item>"
            for i in range(n)
        )
        return f"<rss><channel>{rows}</channel></rss>"

    def handler(request):
        q = request.url.params["q"]
        window = _re.search(r"after:(\S+) before:(\S+)", q).groups()
        return httpx.Response(200, text=feed(25 if window == ("2026-11-01", "2026-12-01") else 3))

    fetcher = CachedFetcher(tmp_path / "c", http=httpx.Client(transport=httpx.MockTransport(handler)),
                            sleep=lambda s: None, log=lambda m: None, min_interval_s=0)  # fmt: skip
    rumors = store.connect(tmp_path / "r.db")
    totals = collect.run_searches(
        fetcher, rumors, gt, 2026, today=date(2026, 11, 10), max_age_s=None, log=lambda m: None
    )
    assert totals["splits"] >= 1
    assert totals["searches"] > 3 * 2  # 3 windows × (1 league + 1 team) planned, plus split halves


def test_coverage_ignores_headlines_after_as_of(tmp_path):
    from rift_rumors.rumors.coverage import coverage

    gtc = db.connect(tmp_path / "gt.db")
    for player, outcome in (("A", "moved"), ("B", "stayed"), ("C", "moved")):
        gtc.execute(
            "INSERT INTO offseason_outcomes (year, league, player, role, team_before, season_end,"
            " cutoff, outcome, outcome_feb1, first_move_date)"
            " VALUES (2025, 'LEC', ?, 'Mid', 'T', '2025-09-28', '2026-01-17', ?, ?, ?)",
            (player, outcome, outcome, "2025-11-20" if outcome == "moved" else None),
        )
    rumors = store.connect(tmp_path / "r.db")
    for item_id, pub, player in (("i1", "2025-11-01T00:00:00+00:00", "A"),
                                 ("i2", "2025-11-15T00:00:00+00:00", "C")):  # fmt: skip
        rumors.execute(
            "INSERT INTO rumor_items VALUES (?, 'gnews', 'Sheep Esports', 't', 'u', ?, ?, ?, NULL)",
            (item_id, pub, pub, pub),
        )
        rumors.execute(
            "INSERT INTO rumor_mentions VALUES (?, 2025, ?, 'T', 'player', ?)",
            (item_id, player, player),
        )
    (row,) = coverage(rumors, gtc, "11-10")
    assert row["players"] == 3 and row["with_headline"] == 1  # C's headline is after Nov 10
    assert row["leave_rate_with"] == 1.0 and row["leave_rate_without"] == 0.5
    assert row["median_lead_days"] == 19  # Nov 1 → Nov 20


# --- Korean Google News -------------------------------------------------------


def test_korean_searches_use_korean_edition_and_hangul_team_names():
    from urllib.parse import parse_qs, urlparse

    searches = plan_searches(2026, {"LCK": ["Gen.G"]}, date(2026, 9, 15))
    ko = [s for s in searches if s.locale == "ko"]
    assert len(ko) == 1 and '"젠지"' in ko[0].query and "이적" in ko[0].query
    params = parse_qs(urlparse(gnews.search_url(ko[0].text(), "ko")).query)
    assert params["hl"] == ["ko"] and params["ceid"] == ["KR:ko"]


def test_names_match_before_korean_particles():
    assert link.word_pattern("T1").search("T1이 새 시즌 로스터 발표")
    assert link.word_pattern("Zeus").search("'Zeus'는 재계약")
    assert not link.word_pattern("Zeus").search("Zeuss signs")


def test_korean_team_names_link(tmp_path, gt):
    gt.execute(
        "INSERT INTO offseason_outcomes (year, league, player, role, team_before, season_end,"
        " cutoff, outcome, outcome_feb1)"
        " VALUES (2026, 'LCK', 'Chovy', 'Mid', 'Gen.G', '2026-09-13', '2027-01-17', 'pending', 'pending')"
    )
    rumors = store.connect(tmp_path / "r.db")
    item = gnews.NewsItem("k1", "연합뉴스", "젠지, 새 미드 영입", "u", "2026-11-09T00:00:00+00:00")
    store.upsert_items(rumors, [item], source="gnews_ko", query="q", fetched_at="T")
    link.link(rumors, gt)
    assert rumors.execute("SELECT team FROM rumor_mentions").fetchone()[0] == "Gen.G"


# --- Leaguepedia RosterRumors -------------------------------------------------

LP_ROWS = [
    {
        "Date": "2026-11-08",
        "Player": "Zyrexx",
        "TeamStart": "Team Aurora",
        "TeamEnd": "Nova Esports",
        "RoleStart": "Mid",
        "RoleEnd": "Mid",
        "SourceSite": "X",
        "SourceAuthors": "a",
        "SourceLink": "https://x.com/1",
        "Status": "Confirmed",
        "NotHappening": "0",
    },
    {
        "Date": "2026-11-09",
        "Player": "Rookie Kid",
        "TeamStart": "Academy",
        "TeamEnd": "Team Aurora",
        "RoleStart": "Bot",
        "RoleEnd": "Bot",
        "SourceSite": "Sheep Esports",
        "SourceAuthors": "b",
        "SourceLink": "",
    },
    {"Date": "", "Player": "Nobody"},
]


def test_leaguepedia_rows_never_store_status(tmp_path):
    from rift_rumors.rumors import lp_rumors

    rumors = store.connect(tmp_path / "r.db")
    totals = lp_rumors.ingest(rumors, LP_ROWS, fetched_at="T1")
    assert totals == {"rows": 3, "usable": 2, "new_items": 2}
    assert lp_rumors.ingest(rumors, LP_ROWS, fetched_at="T2")["new_items"] == 0  # append-only
    dumped = " ".join(str(tuple(r)) for t in ("rumor_items", "rumor_claims")
                      for r in rumors.execute(f"SELECT * FROM {t}"))  # fmt: skip
    assert "Confirmed" not in dumped
    title = rumors.execute("SELECT title FROM rumor_items WHERE outlet = 'X'").fetchone()[0]
    assert title == "Zyrexx: Team Aurora → Nova Esports (Mid)"


def test_leaguepedia_rumors_link_structurally(tmp_path, gt):
    from rift_rumors.rumors import lp_rumors

    rumors = store.connect(tmp_path / "r.db")
    lp_rumors.ingest(rumors, LP_ROWS, fetched_at="T")
    stats = link.link(rumors, gt)
    rows = {
        (r["match_kind"], r["player"], r["team"])
        for r in rumors.execute("SELECT * FROM rumor_mentions")
    }
    # "Zyrexx" is a redirect to Zyrex: resolved through player_aliases
    assert ("structured", "Zyrex", "Team Aurora") in rows
    # an outside player joining one of our teams: a team-level signal
    assert ("team", None, "Team Aurora") in rows
    assert stats["structured"] == 1 and stats["structured_team_only"] == 1


def test_coverage_can_filter_by_source(tmp_path):
    from rift_rumors.rumors.coverage import coverage

    gtc = db.connect(tmp_path / "gt.db")
    gtc.execute(
        "INSERT INTO offseason_outcomes (year, league, player, role, team_before, season_end,"
        " cutoff, outcome, outcome_feb1) VALUES (2025, 'LEC', 'A', 'Mid', 'T', '2025-09-28',"
        " '2026-01-17', 'stayed', 'stayed')"
    )
    rumors = store.connect(tmp_path / "r.db")
    rumors.execute("INSERT INTO rumor_items VALUES ('i1', 'leaguepedia', 'X', 't', 'u',"
                   " '2025-10-01T00:00:00+00:00', 'T', 'T', NULL)")  # fmt: skip
    rumors.execute("INSERT INTO rumor_mentions VALUES ('i1', 2025, 'A', 'T', 'structured', 'A')")
    assert coverage(rumors, gtc, "11-10")[0]["with_headline"] == 1
    assert coverage(rumors, gtc, "11-10", sources=("gnews",))[0]["with_headline"] == 0


# --- Hangul handle map --------------------------------------------------------


def test_hangul_names_must_stand_alone():
    p = link.hangul_pattern("지우")
    assert p.search("'지우' 정지우, 농심 떠나 DRX 이적")  # quoted
    assert p.search("농심, 지우·실비 재계약")  # in a list
    assert p.search("지우는 DRX로")  # followed by a particle
    assert not p.search("기록을 지우다")  # ordinary word "to erase"
    assert not p.search("도란도란")  # inside a longer word


def test_validate_drops_unknown_handles_short_names_and_clashes():
    from rift_rumors.rumors import ko_handles as ko

    pop = {"Zeus": {"players": {"Zeus"}, "teams": set()},
           "Zues": {"players": {"Zues"}, "teams": set()}}  # fmt: skip
    pairs = ko.parse_reply(
        'Sure: {"mappings": [{"handle": "Zeus", "hangul": ["제우스", "Zeus"]},'
        ' {"handle": "Nobody", "hangul": "노바디"}, {"handle": "Zues", "hangul": ["제우스", "찬"]}]}'
    )
    rows, dropped = ko.validate(pairs, pop)
    assert rows == []  # 제우스 claimed by two handles: dropped rather than guessed
    assert dropped == {"not_2plus_hangul_syllables": 2, "handle_not_in_dataset": 1,
                       "hangul_maps_to_two_handles": 1}  # fmt: skip


def test_rerun_keeps_reviewed_rows(tmp_path):
    from rift_rumors.rumors import ko_handles as ko

    path = tmp_path / "ko.csv"
    ko.merge_and_write(path, [{"hangul": "제우스", "player": "Zeus", "handle": "Zeus"}], "m1")
    rows = ko.load(path)
    rows[0]["verified"] = "no"
    with path.open("w", newline="", encoding="utf-8") as f:
        import csv

        w = csv.DictWriter(f, fieldnames=ko.FIELDS)
        w.writeheader()
        w.writerows(rows)
    added = ko.merge_and_write(
        path, [{"hangul": "제우스", "player": "X", "handle": "X"},
               {"hangul": "페이커", "player": "Faker", "handle": "Faker"}], "m2",
    )  # fmt: skip
    assert added == 1
    assert ko.active(path) == {"페이커": "Faker"}  # the rejected row stays rejected


def test_hangul_handles_link_as_player_ko(tmp_path, gt):
    rumors = store.connect(tmp_path / "r.db")
    item = gnews.NewsItem("k2", "인벤", "'지렉스' 노바 이적", "u", "2026-11-09T00:00:00+00:00")
    store.upsert_items(rumors, [item], source="gnews_ko", query="q", fetched_at="T")
    stats = link.link(rumors, gt, {"지렉스": "Zyrex"})
    assert stats["player_ko"] == 1
    row = rumors.execute(
        "SELECT player, team FROM rumor_mentions WHERE match_kind='player_ko'"
    ).fetchone()
    assert tuple(row) == ("Zyrex", "Team Aurora")
