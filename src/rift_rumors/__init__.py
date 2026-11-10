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

    args = parser.parse_args()
    if args.command in ("backtest", "freeze"):
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
        counts = ground_truth.build(client, conn, gcd_dir=config.DATA_DIR / "raw" / "gcd")
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
