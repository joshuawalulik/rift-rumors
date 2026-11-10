"""Player and team identity. Each case is a real bug found in Lesson 01, exercise 3."""

from rift_rumors.data.identity import PlayerIndex, TeamLineage, fold, load_manual_continuity


def test_fold_ignores_case_and_spacing():
    assert fold("Hans  Sama") == fold("hans sama")


# --- players -------------------------------------------------------------------

ALIASES = [
    ("Hans Sama", "Hans Sama"),
    ("SamD", "SamD"),
    ("Hans SamD", "SamD"),  # SamD renamed himself Hans SamD; page stayed "SamD"
    ("Oddielan", "Oddie"),
]


def test_case_variants_resolve_to_one_player():  # G2 lost Hans Sama to "Hans sama"
    index = PlayerIndex(ALIASES)
    assert index.canonical("Hans sama") == index.canonical("Hans Sama") == "Hans Sama"


def test_in_game_renames_resolve_to_one_player():
    index = PlayerIndex(ALIASES)
    assert index.canonical("Hans SamD") == index.canonical("samd") == "SamD"


def test_similar_names_stay_different_people():
    index = PlayerIndex(ALIASES)
    assert index.canonical("Hans Sama") != index.canonical("Hans SamD")


def test_ambiguous_alias_is_left_unresolved():
    index = PlayerIndex([("Smash", "Smash (Shin Geum-jae)"), ("Smash", "Smash (Other Person)")])
    assert "smash" in index.ambiguous
    assert index.canonical("Smash") == "Smash"  # falls back to the name as given


def test_unknown_names_pass_through():
    assert PlayerIndex(ALIASES).canonical("Faker") == "Faker"


# --- teams ---------------------------------------------------------------------


def ev(date, old, new, verb):
    return {"date": date, "old_name": old, "new_name": new, "verb": verb}


EVENTS = [
    ev("2022-12-01", "Rogue (European Team)", "KOI (Spanish Team)", "rename"),
    ev("2023-11-23", "KOI (Spanish Team)", "Rogue (European Team)", "rename"),
    ev("2025-06-13", "Rogue (European Team)", "Natus Vincere", "acquire"),
    ev("2024-12-25", "Kwangdong Freecs", "DN Freecs", "rename"),
    ev("2025-12-21", "DN Freecs", "DN SOOPers", "rename"),
    ev("2023-12-15", "Liiv SANDBOX", "FearX", "rebrand"),
    ev("2024-05-23", "FearX", "BNK FEARX", "rebrand"),
]


def test_name_loops_resolve_consistently():  # Rogue → KOI → Rogue broke the chain-follower
    teams = TeamLineage.as_of(EVENTS, "2024-02-01")
    assert teams.same("Rogue (European Team)", "KOI (Spanish Team)")
    assert teams.key("Rogue (European Team)") == teams.key("KOI (Spanish Team)")


def test_links_only_apply_after_they_happen():
    # NAVI acquired Rogue's LEC spot in June 2025: irrelevant to the 2023 offseason.
    assert not TeamLineage.as_of(EVENTS, "2024-02-01").same(
        "Rogue (European Team)", "Natus Vincere"
    )
    assert TeamLineage.as_of(EVENTS, "2026-02-01").same("Rogue (European Team)", "Natus Vincere")


def test_chains_link_transitively():
    teams = TeamLineage.as_of(EVENTS, "2026-02-01")
    assert teams.same("Kwangdong Freecs", "DN SOOPers")


def test_rebrands_apply_retroactively():
    # Liiv SANDBOX 2023 players' stints are stored under "BNK FEARX" (a May 2024
    # rebrand), so the link must hold for the 2023 offseason (cutoff Feb 2024).
    assert TeamLineage.as_of(EVENTS, "2024-02-01").same("Liiv SANDBOX", "BNK FEARX")


def test_events_without_a_verb_are_treated_as_dated():
    events = [{"date": "2025-01-01", "old_name": "A", "new_name": "B"}]
    assert not TeamLineage.as_of(events, "2024-02-01").same("A", "B")


def test_unrelated_teams_are_different():
    assert not TeamLineage.as_of(EVENTS, "2026-02-01").same("T1", "Gen.G")


def test_manual_continuity_file(tmp_path):
    path = tmp_path / "team_continuity.csv"
    path.write_text(
        "# comment lines are ignored\n"
        "date,old_name,new_name,verb,source\n"
        "2023-12-01,Excel Esports,GIANTX,merge,https://example.org\n"
    )
    (row,) = load_manual_continuity(path)
    assert (row["old_name"], row["new_name"], row["verb"]) == ("Excel Esports", "GIANTX", "merge")
    assert row["source"].startswith("manual:")
    assert load_manual_continuity(tmp_path / "missing.csv") == []
