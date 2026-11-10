from rift_rumors.data.identity import TeamLineage
from rift_rumors.data.outcomes import (
    ContractNews,
    Tenure,
    contract_end_as_of,
    news_date,
    resolve_outcome,
    tenures_from_roster_changes,
)

CUTOFF = "2025-02-01"
NONE = TeamLineage()  # no continuity events


def t(team, join, leave=None, player="Kestrel"):
    return Tenure(player=player, team=team, date_join=join, date_leave=leave)


def test_still_on_same_team_is_stayed():
    out = resolve_outcome("Team Aurora", [t("Team Aurora", "2023-11-20")], CUTOFF, NONE)
    assert (out.outcome, out.team_after) == ("stayed", "Team Aurora")


def test_left_and_joined_elsewhere_is_moved():
    tenures = [t("Team Aurora", "2023-11-20", "2024-11-20"), t("Nova Esports", "2024-11-25")]
    out = resolve_outcome("Team Aurora", tenures, CUTOFF, NONE)
    assert (out.outcome, out.team_after) == ("moved", "Nova Esports")


def test_no_active_tenure_is_no_team():
    out = resolve_outcome(
        "Team Aurora", [t("Team Aurora", "2023-11-20", "2024-11-20")], CUTOFF, NONE
    )
    assert (out.outcome, out.team_after) == ("no_team", None)


def test_rebrand_counts_as_stayed():
    teams = TeamLineage([("Kwangdong Freecs", "DN Freecs")])
    tenures = [t("Kwangdong Freecs", "2023-11-20", "2024-12-01"), t("DN Freecs", "2024-12-01")]
    out = resolve_outcome("Kwangdong Freecs", tenures, CUTOFF, teams)
    assert out.outcome == "stayed"


def test_two_step_rebrand_counts_as_stayed():  # the Liiv SANDBOX bug
    teams = TeamLineage([("Liiv SANDBOX", "FearX"), ("FearX", "BNK FEARX")])
    tenures = [t("BNK FEARX", "2023-11-20")]
    assert resolve_outcome("Liiv SANDBOX", tenures, CUTOFF, teams).outcome == "stayed"


def test_team_names_compare_case_insensitively():
    assert (
        resolve_outcome("Team BDS", [t("TEAM BDS", "2023-11-20")], CUTOFF, NONE).outcome == "stayed"
    )


# --- rebuilding stints from roster changes (BrokenBlade has no tenure rows) ----


BROKENBLADE = [  # real pattern: every re-signing is a same-day Leave + Join
    ("2023-11-08", "G2 Esports", "Leave"),
    ("2023-11-08", "G2 Esports", "Join"),
    ("2024-12-11", "G2 Esports", "Leave"),
    ("2024-12-11", "G2 Esports", "Join"),
]


def test_resigning_pairs_rebuild_one_continuous_stint():
    tenures = tenures_from_roster_changes("BrokenBlade", BROKENBLADE)
    for season_end in ("2023-08-20", "2024-09-01", "2025-09-28"):  # active at every season end
        assert resolve_outcome("G2 Esports", tenures, season_end, NONE).outcome == "stayed"


def test_rebuilt_stints_capture_a_real_move():
    events = [("2024-11-20", "Team Aurora", "Leave"), ("2024-11-25", "Nova Esports", "Join")]
    tenures = tenures_from_roster_changes("Kestrel", events)
    out = resolve_outcome("Team Aurora", tenures, CUTOFF, NONE)
    assert (out.outcome, out.team_after) == ("moved", "Nova Esports")


def test_join_on_cutoff_day_counts_and_leave_on_cutoff_day_does_not():
    assert resolve_outcome("X", [t("Y", CUTOFF)], CUTOFF, NONE).outcome == "moved"
    assert resolve_outcome("X", [t("X", "2024-01-01", CUTOFF)], CUTOFF, NONE).outcome == "no_team"


def test_main_plus_academy_prefers_continuing_team():
    tenures = [t("Nova Academy", "2025-01-10"), t("Team Aurora", "2023-11-20")]
    assert resolve_outcome("Team Aurora", tenures, CUTOFF, NONE).outcome == "stayed"


def test_two_new_teams_picks_most_recent_join():
    tenures = [t("Old Org", "2024-12-01"), t("Newest Org", "2025-01-15")]
    assert resolve_outcome("Team Aurora", tenures, CUTOFF, NONE).team_after == "Newest Org"


# --- point-in-time contracts (the Faker case, from real Leaguepedia rows) ------


FAKER = [
    ContractNews("SK Telecom T1", "2021-11-15", "News/2019-11-17_31", False),
    ContractNews("T1", "2022-11-21", "News/2021-12-02_35", False),
    ContractNews("T1", "2025-11-17", "News/2022-11-29_28", False),
    ContractNews("T1", "2029-11-19", "News/2025-11-04_15", False),
]
RENAMES = TeamLineage([("SK Telecom T1", "T1")])


def test_news_date_handles_both_id_formats():
    assert news_date("News/2022-11-29_28") == "2022-11-29"
    assert news_date("News/2018-02-24/KR_6") == "2018-02-24"
    assert news_date(None) is None and news_date("garbage") is None


def test_contract_as_of_end_of_2023_ignores_the_2025_extension():
    assert contract_end_as_of(FAKER, "T1", "2023-08-20", RENAMES) == "2025-11-17"


def test_contract_as_of_after_extension_sees_it():
    assert contract_end_as_of(FAKER, "T1", "2025-11-04", RENAMES) == "2029-11-19"


def test_contract_follows_renames():
    assert contract_end_as_of(FAKER, "T1", "2020-06-01", RENAMES) == "2021-11-15"


def test_contract_removal_means_no_known_contract():
    rows = FAKER + [ContractNews("T1", None, "News/2024-01-01_1", True)]
    assert contract_end_as_of(rows, "T1", "2024-06-01", RENAMES) is None


def test_undated_announcements_are_never_trusted():
    rows = [ContractNews("T1", "2030-01-01", None, False)]
    assert contract_end_as_of(rows, "T1", "2026-01-01", NONE) is None
