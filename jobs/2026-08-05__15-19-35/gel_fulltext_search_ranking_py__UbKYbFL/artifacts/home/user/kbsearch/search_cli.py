#!/usr/bin/env python3
"""Command line front end for the knowledge base search service."""
import argparse
import asyncio
import json
import sys

from search_service import VALID_STATUSES, search_articles


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid non-negative integer value: {value!r}"
        ) from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(
            f"invalid non-negative integer value: {value!r}"
        )
    return parsed


def _status(value: str) -> str:
    if value not in VALID_STATUSES:
        raise argparse.ArgumentTypeError(
            f"invalid status: {value!r} (choices: {', '.join(VALID_STATUSES)})"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_cli.py",
        description="Search the Orbital Ledger engineering handbook.",
    )
    parser.add_argument("--query", required=True, help="the search query text")
    parser.add_argument("--status", type=_status, default=None, help="filter by status")
    parser.add_argument("--tag", type=str, default=None, help="filter by tag")
    parser.add_argument(
        "--limit", type=_non_negative_int, default=10, help="max results to return"
    )
    parser.add_argument(
        "--offset", type=_non_negative_int, default=0, help="how many leading matches to skip"
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = asyncio.run(
            search_articles(
                args.query,
                status=args.status,
                tag=args.tag,
                limit=args.limit,
                offset=args.offset,
            )
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
