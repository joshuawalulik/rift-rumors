import json
import sqlite3
import subprocess
from datetime import UTC, datetime

import pytest

from rift_rumors.data import db
from rift_rumors.data.identity import TeamLineage
from rift_rumors.data.outcomes import ContractNews
from rift_rumors.evals import metrics
from rift_rumors.evals.backtest import leave_one_year_out, scoreable
from rift_rumors.predict.features import (
    Example,
    contract_status,
    parse_sheet_date,
    resolve_contract,
)
from rift_rumors.predict.freeze import FreezeExistsError, freeze_v0
from rift_rumors.predict.v0 import BaseRate, ContractBaseline

# --- metrics -----------------------------------------------------------------

SURE_STAY = {"stayed": 1.0, "moved": 0.0, "no_team": 0.0}
UNIFORM = {"stayed": 1 / 3, "moved": 1 / 3, "no_team": 1 / 3}


def test_perfect_prediction_scores_zero():
    assert metrics.brier([SURE_STAY], ["stayed"]) == 0
    assert metrics.leave_brier([SURE_STAY], ["stayed"]) == 0


def test_confident_miss_is_worst():
    assert metrics.brier([SURE_STAY], ["moved"]) == pytest.approx(2.0)
    assert metrics.log_loss([SURE_STAY], ["moved"]) > metrics.log_loss([UNIFORM], ["moved"])


def test_calibration_buckets():
    probs = [{"stayed": 0.2, "moved": 0.8, "no_team": 0.0}] * 4
    (row,) = metrics.calibration(probs, ["moved", "moved", "no_team", "stayed"])
    assert row["predicted"] == pytest.approx(0.8) and row["observed"] == pytest.approx(0.75)


# --- features ----------------------------------------------------------------


def test_contract_status():
    assert contract_status("2026-11-16", 2026) == "expiring"
    assert contract_status("2027-11-15", 2026) == "under_contract"
    assert contract_status(None, 2026) == "unknown"


def test_parse_sheet_date_formats():
    assert parse_sheet_date("9/9/2026 7:26:51") == "2026-09-09"
    assert parse_sheet_date("Nov 3, 2026 0:15:46 PT") == "2026-11-03"
    assert parse_sheet_date("") is None


def gcd_row(end, snapshot_at="2026-11-10T00:00:00+00:00", updated="9/9/2026 7:26:51"):
    return {"contract_end": end, "snapshot_at": snapshot_at, "sheet_updated": updated}


def test_newer_gcd_beats_older_leaguepedia():  # the Lyncas case
    lp = [ContractNews("Team Vitality", "2026-11-26", "News/2024-05-21_55", False)]
    end, source = resolve_contract(
        lp, [gcd_row("2026-11-16")], "Team Vitality", "2026-11-10", TeamLineage()
    )
    assert (end, source) == ("2026-11-16", "gcd")


def test_newer_leaguepedia_beats_stale_gcd():
    lp = [ContractNews("Team Vitality", "2028-11-20", "News/2026-10-01_1", False)]
    end, source = resolve_contract(
        lp, [gcd_row("2026-11-16")], "Team Vitality", "2026-11-10", TeamLineage()
    )
    assert (end, source) == ("2028-11-20", "leaguepedia")


def test_gcd_snapshots_taken_after_as_of_are_invisible():
    lp = [ContractNews("T1", "2023-11-20", "News/2022-06-09_29", False)]
    end, source = resolve_contract(lp, [gcd_row("2029-11-19")], "T1", "2023-11-10", TeamLineage())
    assert (end, source) == ("2023-11-20", "leaguepedia")  # no peeking at a 2026 snapshot


# --- model -------------------------------------------------------------------


def ex(year, league, status, outcome, known=False):
    return Example(year, league, f"p{year}{league}{status}{outcome}", "h", "T", "Mid",
                   f"{year}-11-10", None, "none", status, outcome, None, known)  # fmt: skip


TRAIN = (
    [ex(2023, "LCK", "expiring", "moved")] * 8
    + [ex(2023, "LCK", "expiring", "stayed")] * 2
    + [ex(2023, "LCK", "under_contract", "stayed")] * 9
    + [ex(2023, "LCK", "under_contract", "moved")] * 1
)


def test_v0_learns_contract_signal_and_probs_sum_to_one():
    model = ContractBaseline(alpha=2).fit(TRAIN)
    expiring = model.predict(ex(2026, "LCK", "expiring", "pending"))
    under = model.predict(ex(2026, "LCK", "under_contract", "pending"))
    assert expiring["moved"] > under["moved"]
    assert sum(expiring.values()) == pytest.approx(1.0)


def test_unseen_league_falls_back_to_all_league_rates():
    model = ContractBaseline(alpha=2).fit(TRAIN)
    lec = model.predict(ex(2026, "LEC", "expiring", "pending"))
    assert lec == model.by_status["expiring"]


def test_already_known_examples_are_not_scored():
    data = TRAIN + [ex(2023, "LCK", "expiring", "moved", known=True)]
    assert len(scoreable(data)) == len(TRAIN)


def test_leave_one_year_out_never_trains_on_test_year():
    data = [e for e in TRAIN] + [
        Example(
            2024,
            e.league,
            e.player + "b",
            "h",
            "T",
            "Mid",
            "2024-11-10",
            None,
            "none",
            e.contract_status,
            e.outcome,
            None,
            False,
        )  # fmt: skip
        for e in TRAIN
    ]
    seen = []

    class Spy(BaseRate):
        def fit(self, examples):
            seen.append({e.year for e in examples})
            return super().fit(examples)

    leave_one_year_out(data, Spy)
    assert seen == [{2024}, {2023}]


# --- freeze ------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "init"],
        cwd=tmp_path, check=True,
    )  # fmt: skip
    return tmp_path


@pytest.fixture
def conn(tmp_path_factory):
    c = db.connect(tmp_path_factory.mktemp("db") / "gt.db")  # outside the git repo
    rows = [("LCK", 2025, "stayed"), ("LCK", 2025, "moved"), ("LCK", 2026, "pending")]
    for i, (league, year, outcome) in enumerate(rows):
        c.execute(
            "INSERT INTO offseason_outcomes VALUES (?,?,?,?,?,?,?,?,?,?)",
            (year, league, f"P{i}", "Mid", "T1", f"{year}-09-13", None, None, outcome, None),
        )
    return c


def test_freeze_writes_hash_and_refuses_overwrite(repo, conn):
    now = datetime(2026, 11, 10, tzinfo=UTC)
    csv_path, meta_path = freeze_v0(conn, repo / "predictions", repo, 2026, now=now)
    meta = json.loads(meta_path.read_text())
    assert meta["as_of"] == "2026-11-10" and meta["population"]["total"] == 1
    assert len(meta["csv_sha256"]) == 64 and meta["code"]["dirty"] is False

    with pytest.raises(FreezeExistsError):
        freeze_v0(conn, repo / "predictions", repo, 2026, now=now)


def test_freeze_refuses_uncommitted_code(repo, conn):
    (repo / "scratch.py").write_text("x = 1\n")
    with pytest.raises(RuntimeError, match="Commit your code first"):
        freeze_v0(conn, repo / "predictions", repo, 2026)


def test_sqlite_row_factory_not_required_by_tests(conn):
    assert isinstance(conn, sqlite3.Connection)
