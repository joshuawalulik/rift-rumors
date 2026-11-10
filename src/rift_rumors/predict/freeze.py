"""Freeze a model's live predictions so they can be graded honestly later.

A frozen prediction is only worth something if it provably existed *before* the
outcome was public. So a freeze:

1. records exactly what it knew: as-of date, code commit, data snapshot times
2. writes predictions + metadata under `predictions/<year>/<version>.*`
3. **refuses to overwrite** an existing freeze. New ideas become a new version
   with its own file, scored only on players still unresolved at its own
   freeze time.
4. hashes the CSV (sha256) into the metadata, so any later edit is detectable

The caller then commits the files and tags the commit. The git history (and a
push to a remote) is the timestamp.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from rift_rumors.evals.backtest import leave_one_year_out, scoreable
from rift_rumors.predict.features import load_examples
from rift_rumors.predict.v0 import VERSION, ContractBaseline

COLUMNS = [
    "player", "handle", "league", "team_before", "role", "contract_end", "contract_source",
    "contract_status", "p_stayed", "p_moved", "p_no_team", "p_leave", "scored", "not_scored_reason",
]  # fmt: skip


class FreezeExistsError(RuntimeError):
    pass


def git_state(repo: Path) -> dict:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def freeze_v0(
    conn: sqlite3.Connection,
    out_dir: Path,
    repo: Path,
    live_year: int,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    now = now or datetime.now(UTC)
    as_of_month_day = now.strftime("%m-%d")
    csv_path = out_dir / str(live_year) / f"{VERSION}.csv"
    meta_path = csv_path.with_suffix(".meta.json")
    if csv_path.exists() or meta_path.exists():
        raise FreezeExistsError(f"{csv_path} already exists. Frozen predictions are immutable.")

    git = git_state(repo)
    if git["dirty"]:
        raise RuntimeError("Commit your code first: a freeze must point at an exact commit.")

    examples = load_examples(conn, as_of_month_day)
    history = [e for e in examples if e.year < live_year]
    live = [e for e in examples if e.year == live_year]

    model = ContractBaseline(alpha=10).fit(scoreable(history))
    backtest = leave_one_year_out(history, lambda: ContractBaseline(alpha=10))

    rows = []
    for e in live:
        p = model.predict(e)
        reason = "move already recorded before freeze" if e.already_known else ""
        rows.append(
            {
                "player": e.player,
                "handle": e.handle,
                "league": e.league,
                "team_before": e.team_before,
                "role": e.role or "",
                "contract_end": e.contract_end or "",
                "contract_source": e.contract_source,
                "contract_status": e.contract_status,
                "p_stayed": f"{p['stayed']:.4f}",
                "p_moved": f"{p['moved']:.4f}",
                "p_no_team": f"{p['no_team']:.4f}",
                "p_leave": f"{1 - p['stayed']:.4f}",
                "scored": str(not e.already_known).lower(),
                "not_scored_reason": reason,
            }
        )

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["league"], r["team_before"], r["handle"])))

    gcd_tabs = {
        r["tab"]: {"sheet_updated": r["sheet_updated"], "snapshot_at": r["snapshot_at"]}
        for r in conn.execute(
            "SELECT tab, sheet_updated, MAX(snapshot_at) AS snapshot_at FROM gcd_contracts "
            "GROUP BY tab"
        )
    }
    meta = {
        "version": VERSION,
        "live_year": live_year,
        "frozen_at": now.isoformat(),
        "as_of": f"{live_year}-{as_of_month_day}",
        "code": git,
        "model": {
            "description": "Historical outcome mix by (league, contract status), "
            "shrunk toward the all-league mix (alpha=10, chosen before seeing results).",
            "alpha": model.alpha,
            "trained_on_years": model.trained_on,
            "n_train": model.n_train,
            "rates_by_league_status": {
                f"{league}/{status}": rates
                for (league, status), rates in sorted(model.by_league_status.items())
            },
        },
        "backtest_leave_one_year_out": backtest,
        "data": {"gcd_tabs": gcd_tabs},
        "population": {
            "total": len(rows),
            "scored": sum(r["scored"] == "true" for r in rows),
            "not_scored_already_known": sum(r["scored"] == "false" for r in rows),
        },
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "grading": "Scored rows are graded on their outcome as of Feb 1 of the next year, "
        "using the same label rules as offseason_outcomes.",
    }
    meta_path.write_text(json.dumps(meta, indent=2, default=_jsonable) + "\n")
    return csv_path, meta_path


def _jsonable(value):
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(f"not JSON serializable: {type(value)}")
