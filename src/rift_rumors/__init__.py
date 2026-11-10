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

    args = parser.parse_args()
    if args.command == "ground-truth":
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
