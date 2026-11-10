"""Riot's Global Contract Database (GCD): the official source for contract end dates.

Riot publishes it as a public Google Sheet, one tab per region. Three properties
shape how we use it:

1. **It's a snapshot, not a history.** The sheet shows contracts *now*. There's
   no record of what it said in 2023, so it can't feed the backtest (Leaguepedia's
   dated announcements do that). It's the authority for the *live* offseason.
   Since the source keeps no history, we keep it ourselves: every fetch is saved
   as a timestamped snapshot, so future seasons get real point-in-time data.
2. **Each tab has its own "last updated" time.** Regions update independently
   (in Nov 2026 the EMEA tab was two months older than the Korea tab). Every row
   carries its tab's timestamp, so a stale tab is visible, never silent.
3. **It contains personal data we don't need** (legal names, team staff emails).
   We keep only an allow-list of columns, and the rest is never written to disk.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

SHEET_ID = "1Y7k5kQ2AegbuyiGwEPsa62e883FYVtHqr6UVut9RC4o"
# Tabs for the leagues we cover (the sheet also has CN and LCP).
TABS = {"AME": "0", "EMEA": "148326031", "KR": "905624073"}
KEEP = ["league", "team", "handle", "role", "contract_end", "contract_end_text",
        "residency", "status", "sheet_updated"]  # fmt: skip
SNAPSHOT_MAX_AGE_S = 6 * 3600


def csv_url(gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/pub?gid={gid}&single=true&output=csv"


def parse_end_date(text: str) -> str | None:
    """'November 16, 2026' → '2026-11-16'. Coach terms like 'Day After Worlds 2026' → None."""
    try:
        return datetime.strptime(" ".join(text.split()), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _find(header: list[str], *keywords: str) -> int | None:
    for i, name in enumerate(header):
        if any(k in name.lower() for k in keywords):
            return i
    return None


def parse_tab(text: str) -> list[dict]:
    """Parse one region tab. Layouts differ by region, so columns are found by header name."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    sheet_updated = rows[0][1].strip() if len(rows[0]) > 1 else None

    header_at = next(
        (i for i, r in enumerate(rows) if "Team" in r and any("Summoner" in c for c in r)), None
    )
    if header_at is None:
        raise ValueError("GCD tab has no header row with 'Team' and 'Official Summoner Name'")
    header = rows[header_at]
    col = {
        "league": _find(header, "league"),
        "team": _find(header, "team"),
        "handle": _find(header, "summoner"),
        "role": _find(header, "position", "role"),
        "end": _find(header, "end"),
        "residency": _find(header, "residency"),
        "status": _find(header, "status"),
    }

    def cell(row: list[str], key: str) -> str | None:
        i = col[key]
        value = row[i].strip() if i is not None and i < len(row) else ""
        return value or None

    out = []
    for row in rows[header_at + 1 :]:
        handle, team = cell(row, "handle"), cell(row, "team")
        if not handle or not team:
            continue  # blank spacer rows and section titles like "LCS TEAMS"
        end_text = cell(row, "end") or ""
        out.append(
            {
                "league": cell(row, "league"),
                "team": team,
                "handle": handle,
                "role": cell(row, "role"),
                "contract_end": parse_end_date(end_text),
                "contract_end_text": end_text or None,
                "residency": cell(row, "residency"),
                "status": cell(row, "status"),
                "sheet_updated": sheet_updated,
            }
        )
    return out


def latest_snapshot(root: Path) -> Path | None:
    snapshots = sorted(p for p in root.glob("*") if p.is_dir()) if root.exists() else []
    return snapshots[-1] if snapshots else None


def snapshot(root: Path, http: httpx.Client | None = None, log=print) -> Path:
    """Save a trimmed, timestamped copy of every tab, unless the latest one is fresh."""
    latest = latest_snapshot(root)
    if latest is not None:
        age = datetime.now(UTC) - datetime.strptime(latest.name, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=UTC
        )
        if age.total_seconds() < SNAPSHOT_MAX_AGE_S:
            return latest

    http = http or httpx.Client(timeout=30, follow_redirects=True)
    taken_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parsed = {}
    for tab, gid in TABS.items():
        response = http.get(csv_url(gid))
        response.raise_for_status()
        parsed[tab] = parse_tab(response.text)  # parse first: never write a half snapshot

    folder = root / taken_at
    folder.mkdir(parents=True)
    for tab, rows in parsed.items():
        with (folder / f"{tab}.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=KEEP)
            writer.writeheader()
            writer.writerows(rows)
        updated = rows[0]["sheet_updated"] if rows else "?"
        log(f"GCD {tab}: {len(rows)} rows (sheet last updated {updated})")
    return folder


def load_snapshots(root: Path) -> list[dict]:
    """Every saved snapshot as rows, each tagged with when we took it."""
    out = []
    for folder in sorted(p for p in root.glob("*") if p.is_dir()) if root.exists() else []:
        taken = datetime.strptime(folder.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
        for path in sorted(folder.glob("*.csv")):
            with path.open(newline="") as f:
                for row in csv.DictReader(f):
                    out.append(
                        {
                            "snapshot_at": taken,
                            "tab": path.stem,
                            **{k: row[k] or None for k in KEEP},
                        }
                    )
    return out


_HANDLE_NOISE = re.compile(r"[^a-z0-9]")


def normalize_handle(handle: str | None) -> str:
    """'Bdd' / 'BDD' / 'B.D.D' all match. Leaguepedia pages add ' (Real Name)' disambiguators."""
    base = (handle or "").split(" (")[0]
    return _HANDLE_NOISE.sub("", base.lower())
