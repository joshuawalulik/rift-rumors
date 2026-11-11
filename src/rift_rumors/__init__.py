"""Rift Rumors: predict LoL pro roster moves from stats, contracts and rumors."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="rift-rumors")
    sub = parser.add_subparsers(dest="command", required=True)

    hello = sub.add_parser("hello", help="Lesson 00: compare models on one rumor post")
    hello.add_argument(
        "--models",
        help="comma-separated model names (default: config.DEFAULT_MODELS)",
    )
    hello.add_argument(
        "--effort",
        default="low",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="how hard Claude thinks before answering (ignored by Ollama)",
    )
    hello.add_argument("--max-tokens", type=int, default=4096, help="hard cap on reply length")
    hello.add_argument("--no-system", action="store_true", help="drop the system prompt")
    hello.add_argument("--repeat", type=int, default=1, help="call each model N times")

    sub.add_parser("ground-truth", help="Lesson 01: build data/ground_truth.db from Leaguepedia")
    sub.add_parser("backtest", help="Leave-one-year-out backtest: base rate vs v0")
    freeze = sub.add_parser("freeze", help="Freeze v0 live predictions (immutable)")
    freeze.add_argument("--year", type=int, default=2026, help="offseason being predicted")

    rumors = sub.add_parser("rumors", help="Lesson 02: collect and link rumor headlines")
    rsub = rumors.add_subparsers(dest="action", required=True)
    rsub.add_parser("backfill", help="search past offseasons").add_argument(
        "years", type=int, nargs="+"
    )
    rsub.add_parser("collect", help="search the live offseason").add_argument(
        "--year", type=int, default=2026
    )
    lp = rsub.add_parser("leaguepedia", help="ingest Leaguepedia's RosterRumors table")
    lp.add_argument("years", type=int, nargs="+")
    lp.add_argument("--live-year", type=int, default=2026, help="this offseason re-fetches")
    ko = rsub.add_parser("ko-handles", help="draft/check the Hangul handle map")
    ko.add_argument("--model", default="claude-opus-5-5")
    ko.add_argument("--check", action="store_true", help="only report support, no LLM call")
    rsub.add_parser("link", help="match headlines to players and teams")
    rsub.add_parser("stats", help="items per offseason and top outlets")
    cov = rsub.add_parser("coverage", help="do rumors before today's date cover the moves?")
    cov.add_argument(
        "--source", action="append", help="gnews | gnews_ko | leaguepedia | manual (repeatable)"
    )
    add = rsub.add_parser("add", help="record a rumor you saw yourself")
    add.add_argument("--title", required=True, help="the claim, in one line")
    add.add_argument("--url", required=True)
    add.add_argument("--outlet", required=True, help='e.g. "Reddit r/lck" or "X @handle"')
    add.add_argument("--published", help="UTC ISO time it was posted (default: now)")
    add.add_argument("--note")

    args = parser.parse_args()
    if args.command == "rumors":
        _rumors(args)
    elif args.command in ("backtest", "freeze"):
        import sqlite3

        from rift_rumors import config

        conn = sqlite3.connect(config.GROUND_TRUTH_DB)
        conn.row_factory = sqlite3.Row
        if args.command == "backtest":
            _print_backtest(conn)
        else:
            from rift_rumors.predict.freeze import freeze_v0

            csv_path, meta_path = freeze_v0(
                conn, config.REPO_ROOT / "predictions", config.REPO_ROOT, args.year
            )
            print(f"Froze {csv_path}\n      {meta_path}")
            print("Now commit both files and tag the commit.")
    elif args.command == "ground-truth":
        from rift_rumors import config
        from rift_rumors.data import db, ground_truth
        from rift_rumors.data.leaguepedia import CargoClient

        client = CargoClient(
            config.DATA_DIR / "raw" / "leaguepedia",
            username=os.getenv("LEAGUEPEDIA_USERNAME") or None,
            bot_password=os.getenv("LEAGUEPEDIA_BOT_PASSWORD") or None,
        )
        # The DB is fully derived from the raw cache, so rebuild it from scratch each run.
        config.GROUND_TRUTH_DB.unlink(missing_ok=True)
        conn = db.connect(config.GROUND_TRUTH_DB)
        counts = ground_truth.build(
            client,
            conn,
            gcd_dir=config.DATA_DIR / "raw" / "gcd",
            continuity_path=config.REPO_ROOT / "config" / "team_continuity.csv",
        )
        print(f"\nWrote {config.GROUND_TRUTH_DB} ({client.requests_made} API requests)")
        for table, n in counts.items():
            print(f"  {table:<20} {n:>6} rows")
    elif args.command == "hello":
        from rift_rumors import hello as lesson

        models = [m.strip() for m in args.models.split(",")] if args.models else None
        lesson.run(
            models,
            effort=args.effort,
            max_tokens=args.max_tokens,
            use_system=not args.no_system,
            repeat=args.repeat,
        )


def _print_backtest(conn) -> None:
    from datetime import date

    from rift_rumors.evals.backtest import leave_one_year_out
    from rift_rumors.predict.features import load_examples
    from rift_rumors.predict.v0 import BaseRate, ContractBaseline

    as_of = date.today().strftime("%m-%d")
    history = [e for e in load_examples(conn, as_of) if e.outcome != "pending"]
    print(f"Leave-one-year-out, as of {as_of} each year (lower is better except accuracy)\n")
    print(f"{'model':<12}{'n':>5}{'brier':>8}{'leave':>8}{'logloss':>9}{'acc':>6}")
    for name, make in (("base-rate", BaseRate), ("v0", lambda: ContractBaseline(alpha=10))):
        p = leave_one_year_out(history, make)["pooled"]
        print(
            f"{name:<12}{p['n']:>5}{p['brier']:>8.3f}{p['leave_brier']:>8.3f}"
            f"{p['log_loss']:>9.3f}{p['accuracy']:>6.2f}"
        )


def _rumors(args) -> None:
    import sqlite3

    from rift_rumors import config
    from rift_rumors.rumors import collect, link, store
    from rift_rumors.rumors.fetch import CachedFetcher

    rumors = store.connect(config.RUMORS_DB)
    gt = sqlite3.connect(config.GROUND_TRUTH_DB)
    gt.row_factory = sqlite3.Row
    fetcher = CachedFetcher(config.DATA_DIR / "raw" / "gnews")

    if args.action == "backfill":
        for year in args.years:
            print(f"Backfilling the {year} offseason...")
            print(f"  {collect.backfill(fetcher, rumors, gt, year)}")
    elif args.action == "collect":
        print(f"  {collect.collect_live(fetcher, rumors, gt, args.year)}")
    elif args.action == "leaguepedia":
        import os

        from rift_rumors.data.leaguepedia import CargoClient
        from rift_rumors.rumors import lp_rumors

        client = CargoClient(
            config.DATA_DIR / "raw" / "leaguepedia",
            username=os.getenv("LEAGUEPEDIA_USERNAME") or None,
            bot_password=os.getenv("LEAGUEPEDIA_BOT_PASSWORD") or None,
        )
        totals = lp_rumors.collect(client, rumors, args.years, live_year=args.live_year)
        for year, t in totals.items():
            print(f"  {year}: {t}")
        print(f"({client.requests_made} Leaguepedia requests)")
    elif args.action == "add":
        item = collect.add_manual(
            rumors,
            title=args.title,
            url=args.url,
            outlet=args.outlet,
            published_at=args.published,
            note=args.note,
        )
        print(f"Recorded {item}")
    elif args.action == "ko-handles":
        _ko_handles(args, rumors, gt)
    if args.action in ("backfill", "collect", "add", "link", "leaguepedia", "ko-handles"):
        from rift_rumors.rumors import ko_handles

        print(f"Linked: {link.link(rumors, gt, ko_handles.active(config.KO_HANDLES_CSV))}")
        print(f"({fetcher.requests_made} network requests)")
    if args.action == "stats":
        _print_rumor_stats(rumors)
    if args.action == "coverage":
        from datetime import date

        from rift_rumors.rumors.coverage import coverage

        as_of = date.today().strftime("%m-%d")
        sources = tuple(args.source) if args.source else None
        print(f"Rumors published on/before {as_of} of each year; unresolved players only")
        print(f"Sources: {', '.join(sources) if sources else 'all'}\n")
        print(
            f"{'year':<6}{'lg':<5}{'n':>4}{'cov':>6}{'left|hl':>9}{'left|none':>11}"
            f"{'leavers cov':>13}{'lead d':>8}"
        )
        for r in coverage(rumors, gt, as_of, sources=sources):

            def pct(x):
                return "  -" if x is None else f"{x:.0%}"

            lead = "-" if r["median_lead_days"] is None else f"{r['median_lead_days']:.0f}"
            print(
                f"{r['year']:<6}{r['league']:<5}{r['players']:>4}{pct(r['coverage']):>6}"
                f"{pct(r['leave_rate_with']):>9}{pct(r['leave_rate_without']):>11}"
                f"{pct(r['leavers_covered']):>13}{lead:>8}"
            )


def _ko_handles(args, rumors, gt) -> None:
    from rift_rumors import config
    from rift_rumors.llm.client import complete
    from rift_rumors.llm.ledger import Ledger
    from rift_rumors.rumors import ko_handles as ko

    path = config.KO_HANDLES_CSV
    if not args.check:
        candidates = ko.harvest(rumors)
        pop = ko.population(gt)
        print(f"{len(candidates)} quoted Hangul words, {len(pop)} handles; asking {args.model}...")
        result = complete(
            ko.build_prompt(pop, candidates),
            model=args.model,
            system=ko.SYSTEM,
            max_tokens=16000,
            effort="medium",
        )
        Ledger(config.LEDGER_PATH).record("ko-handles", result)
        if result.truncated:
            raise SystemExit("Reply was cut off at max_tokens; nothing written.")
        rows, dropped = ko.validate(ko.parse_reply(result.text), pop)
        added = ko.merge_and_write(path, rows, args.model)
        cost = f"${result.cost_usd:.3f}" if result.cost_usd is not None else "unknown cost"
        print(f"Valid mappings: {len(rows)}; new rows added: {added}; dropped: {dict(dropped)}")
        print(f"({result.input_tokens}+{result.output_tokens} tokens, {cost})")
    rows = ko.load(path)
    sup = ko.support(rumors, gt, {r["hangul"]: r["player"] for r in rows})
    seen = sum(1 for r in rows if sup[r["hangul"]][0])
    print(f"\n{path.name}: {len(rows)} rows; {seen} appear in headlines (rest untested)")
    print(f"{'hangul':<10}{'handle':<16}{'verified':<10}{'headlines':>10}{'w/ team':>9}")
    for r in sorted(rows, key=lambda r: -sup[r["hangul"]][0]):
        hits, with_team = sup[r["hangul"]]
        if hits:
            verified = r["verified"] or "-"
            print(f"{r['hangul']:<10}{r['handle']:<16}{verified:<10}{hits:>10}{with_team:>9}")


def _print_rumor_stats(rumors) -> None:
    print(f"{'year':<6}{'items':>7}{'linked':>8}{'players':>9}  top outlets")
    for r in rumors.execute(
        """
        SELECT CAST(strftime('%Y', published_at) AS INT)
               - (CAST(strftime('%m', published_at) AS INT) < 9) AS year,
               COUNT(*) AS items,
               COUNT(DISTINCT m.item_id) AS linked,
               COUNT(DISTINCT m.player) AS players
        FROM rumor_items i LEFT JOIN rumor_mentions m ON m.item_id = i.id
        GROUP BY 1 ORDER BY 1
        """
    ):
        outlets = rumors.execute(
            """SELECT outlet, COUNT(*) n FROM rumor_items
               WHERE CAST(strftime('%Y', published_at) AS INT)
                     - (CAST(strftime('%m', published_at) AS INT) < 9) = ?
               GROUP BY 1 ORDER BY 2 DESC LIMIT 3""",
            (r["year"],),
        ).fetchall()
        top = ", ".join(f"{o['outlet']} ({o['n']})" for o in outlets)
        print(f"{r['year']:<6}{r['items']:>7}{r['linked']:>8}{r['players']:>9}  {top}")
