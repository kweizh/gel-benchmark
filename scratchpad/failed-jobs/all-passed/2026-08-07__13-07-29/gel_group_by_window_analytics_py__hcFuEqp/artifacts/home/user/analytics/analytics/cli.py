"""Command line entry point for the analytics package.

Usage:
    python3 -m analytics.cli ingest-refunds --file <path>
    python3 -m analytics.cli report [--month YYYY-MM]
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Optional

import gel

from analytics.rollups import MONTH_RE, build_report, ingest_refunds


def _parse_args(argv: list[str]):
    """Parse argv.

    Returns a tuple ``(subcommand, options)`` on success. On any parsing
    problem this prints nothing to stdout, prints a message to stderr and
    exits with code 2.
    """
    if not argv:
        sys.exit(2)

    subcommand = argv[0]
    rest = argv[1:]

    if subcommand == "ingest-refunds":
        options: dict[str, Optional[str]] = {"file": None}
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg == "--file":
                if i + 1 >= len(rest):
                    sys.exit(2)
                options["file"] = rest[i + 1]
                i += 2
            else:
                sys.exit(2)
        if options["file"] is None:
            sys.exit(2)
        return subcommand, options

    if subcommand == "report":
        options = {"month": None}
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg == "--month":
                if i + 1 >= len(rest):
                    sys.exit(2)
                options["month"] = rest[i + 1]
                i += 2
            else:
                sys.exit(2)
        return subcommand, options

    sys.exit(2)


async def _run_ingest(path: str) -> dict:
    import os

    if not os.path.exists(path):
        print("refunds file not found", file=sys.stderr)
        sys.exit(3)

    try:
        with open(path, "r", encoding="utf-8") as fh:
            records = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid refunds file: {exc}", file=sys.stderr)
        sys.exit(4)

    client = gel.create_async_client()
    try:
        try:
            result = await ingest_refunds(client, records)
        except ValueError as exc:
            print(f"invalid refunds file: {exc}", file=sys.stderr)
            sys.exit(4)
    finally:
        await client.aclose()

    return result


async def _run_report(month: Optional[str]) -> dict:
    if month is not None and not MONTH_RE.match(month):
        print(f"invalid month: {month!r}", file=sys.stderr)
        sys.exit(5)

    client = gel.create_async_client()
    try:
        try:
            result = await build_report(client, month=month)
        except ValueError as exc:
            print(f"invalid month: {exc}", file=sys.stderr)
            sys.exit(5)
    finally:
        await client.aclose()

    return result


def main(argv: Optional[list[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    subcommand, options = _parse_args(argv)

    if subcommand == "ingest-refunds":
        result = asyncio.run(_run_ingest(options["file"]))
    elif subcommand == "report":
        result = asyncio.run(_run_report(options["month"]))
    else:  # pragma: no cover - _parse_args already exits on bad subcommand
        sys.exit(2)

    sys.stdout.write(json.dumps(result))
    sys.stdout.write("\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
