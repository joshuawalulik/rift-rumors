"""GCD parser tests. Fixtures mimic each region's real layout, with fake people."""

import httpx

from rift_rumors.data.gcd import (
    load_snapshots,
    normalize_handle,
    parse_end_date,
    parse_tab,
    snapshot,
)

AME = """LAST UPDATE:,"Oct 27, 2026 11:52:20 PT",5,Blue - Reserve,,,,,,,
League,Team,Official Summoner Name,Position,Legal Given Name,Legal Family Name,"End Date (Month, Day, Year)",Residency,Status,QuadCode,Team Contact
LCS TEAMS,,,,,,,,,,
LCS,Team Aurora,Kestrel,Jungle,Private,Person,"November 20, 2028",NA Resident,Active,AUR,boss <boss@aurora.gg>
LCS,Team Aurora,Coachy,LCS Head Coach,Private,Person,Day After Worlds 2028,NA Resident,Active,,
,,,,,,,,,,
"""

EMEA = """THIS SHEET WAS LAST UPDATED ON:,9/9/2026 7:26:51,,,,,,,,,,
League,Team,Official Summoner Name,Main Role,Legal First Name,Legal Family Name,"Contract End (Month, Day, Year) at 23:59 UTC",IMP Residency,LTR Status,,Bi- / Tri-Code,Team Contact Information
,,,,,,,,,,,
LEC,Nova Esports,Wren,MID,Private,Person,"November 16, 2026",Resident,,,NOV,x <x@nova.gg>
LEC,Nova Esports,,,,,,,,,,
"""

KR = """THIS SHEET WAS LAST UPDATED ON:,"Nov 3, 2026 0:15:46 PT",,,,,,,,
League,Team,Official Summoner Name,,Legal First Name,Legal Family Name,"End Date (Month, Day, Year)",,Team Contact Information,
LCK,Gale Gaming,Hollow,,Private,Person,"November 15, 2027",,contact@gale.kr,GG
"""


def test_americas_layout():
    rows = parse_tab(AME)
    assert [r["handle"] for r in rows] == ["Kestrel", "Coachy"]  # section title skipped
    k = rows[0]
    assert (k["role"], k["contract_end"], k["status"]) == ("Jungle", "2028-11-20", "Active")
    assert k["sheet_updated"] == "Oct 27, 2026 11:52:20 PT"


def test_coach_terms_keep_text_but_no_date():
    coach = parse_tab(AME)[1]
    assert coach["contract_end"] is None
    assert coach["contract_end_text"] == "Day After Worlds 2028"


def test_emea_layout_and_blank_rows():
    rows = parse_tab(EMEA)
    assert len(rows) == 1
    assert (rows[0]["role"], rows[0]["contract_end"], rows[0]["residency"]) == (
        "MID",
        "2026-11-16",
        "Resident",
    )


def test_korea_layout_has_no_role_column():
    (row,) = parse_tab(KR)
    assert row["role"] is None and row["contract_end"] == "2027-11-15"


def test_personal_data_is_never_kept():
    for tab in (AME, EMEA, KR):
        for row in parse_tab(tab):
            text = " ".join(str(v) for v in row.values())
            assert "Private" not in text and "Person" not in text and "@" not in text


def test_end_date_parsing():
    assert parse_end_date("November  16, 2026") == "2026-11-16"
    assert parse_end_date("Day After Worlds 2026") is None
    assert parse_end_date("") is None


def test_handle_normalization():
    assert normalize_handle("Hype (Byeon Jeong-hyeon)") == normalize_handle("HYPE")
    assert normalize_handle("B.D.D") == normalize_handle("Bdd")


def test_snapshot_round_trip_and_freshness(tmp_path):
    calls = []
    tabs = {"0": AME, "148326031": EMEA, "905624073": KR}

    def handler(request):
        calls.append(1)
        return httpx.Response(200, text=tabs[request.url.params["gid"]])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    first = snapshot(tmp_path, http=http, log=lambda m: None)
    again = snapshot(tmp_path, http=http, log=lambda m: None)  # fresh: no new fetch
    assert first == again and len(calls) == 3

    rows = load_snapshots(tmp_path)
    assert {r["tab"] for r in rows} == {"AME", "EMEA", "KR"}
    assert all(r["snapshot_at"] for r in rows)
    on_disk = "".join(p.read_text() for p in first.glob("*.csv"))
    assert "Private" not in on_disk and "@" not in on_disk
