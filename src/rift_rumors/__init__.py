"""Rift Rumors: predict LoL pro roster moves from stats, contracts and rumors."""

from __future__ import annotations

import argparse


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

    args = parser.parse_args()
    if args.command == "hello":
        from rift_rumors import hello as lesson

        models = [m.strip() for m in args.models.split(",")] if args.models else None
        lesson.run(
            models,
            effort=args.effort,
            max_tokens=args.max_tokens,
            use_system=not args.no_system,
            repeat=args.repeat,
        )
