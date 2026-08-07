"""CLI for analytics: ingest-refunds and report subcommands."""

from __future__ import annotations

import json
import sys
import os.path

import gel

from .rollups import build_report, ingest_refunds


def _usage() -> None:
    print(
        "usage: python3 -m analytics.cli <subcommand> [options]",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> None:
    args = sys.argv[1:]

    if not args:
        _usage()

    subcommand = args[0]

    if subcommand == "ingest-refunds":
        _ingest_refunds(args[1:])
    elif subcommand == "report":
        _report(args[1:])
    else:
        _usage()


async def _run_ingest(filepath: str) -> None:
    if not os.path.isfile(filepath):
        print("refunds file not found", file=sys.stderr)
        sys.exit(3)

    try:
        with open(filepath, "r") as f:
            records = json.load(f)
    except (json.JSONDecodeError, OSError):
        print("invalid refunds file", file=sys.stderr)
        sys.exit(4)

    if not isinstance(records, list):
        print("invalid refunds file", file=sys.stderr)
        sys.exit(4)

    async with gel.create_async_client() as client:
        try:
            result = await ingest_refunds(client, records)
        except ValueError:
            print("invalid refunds file", file=sys.stderr)
            sys.exit(4)

    print(json.dumps(result))


def _ingest_refunds(rest: list[str]) -> None:
    filepath = None
    i = 0
    while i < len(rest):
        if rest[i] == "--file":
            if i + 1 < len(rest):
                filepath = rest[i + 1]
                i += 2
            else:
                _usage()
        else:
            _usage()

    if filepath is None:
        _usage()

    import asyncio
    asyncio.run(_run_ingest(filepath))


async def _run_report(month: str | None) -> None:
    import re
    if month is not None and not re.match(r"^[0-9]{4}-(0[1-9]|1[0-2])$", month):
        print("invalid month", file=sys.stderr)
        sys.exit(5)

    async with gel.create_async_client() as client:
        try:
            result = await build_report(client, month)
        except ValueError:
            print("invalid month", file=sys.stderr)
            sys.exit(5)

    print(json.dumps(result))


def _report(rest: list[str]) -> None:
    month = None
    i = 0
    while i < len(rest):
        if rest[i] == "--month":
            if i + 1 < len(rest):
                month = rest[i + 1]
                i += 2
            else:
                _usage()
        else:
            _usage()

    import asyncio
    asyncio.run(_run_report(month))


if __name__ == "__main__":
    main()
