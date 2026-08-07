"""Command-line interface for the analytics package.

Usage::

    python3 -m analytics.cli ingest-refunds --file <path>
    python3 -m analytics.cli report [--month YYYY-MM]

On success the JSON document is written to **stdout** (trailing newline
allowed) and the exit code is ``0``.  On any failure stdout stays empty and
a message is written to stderr with one of the following exit codes:

    2  missing/unrecognised subcommand or unrecognised option
    3  refunds file not found
    4  invalid refunds file
    5  invalid month
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import gel

from analytics.rollups import build_report, ingest_refunds


def _err(msg: str) -> None:
    sys.stderr.write(msg.rstrip("\n") + "\n")


def _parse_option(args, name, *, required):
    """Extract a ``--name value`` / ``--name=value`` option from *args*.

    Returns ``(value, remaining_args)`` or raises ``_ArgError``.
    """
    value = None
    remaining = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == name:
            if i + 1 >= len(args):
                raise _ArgError(f"option {name} requires a value")
            value = args[i + 1]
            i += 2
        elif a.startswith(name + "="):
            value = a[len(name) + 1:]
            i += 1
        else:
            remaining.append(a)
            i += 1
    if required and value is None:
        raise _ArgError(f"missing required option {name}")
    return value, remaining


class _ArgError(Exception):
    """Raised for CLI argument problems that map to exit code 2."""


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

async def _do_ingest(records):
    client = gel.create_async_client()
    try:
        await client.ensure_connected()
        return await ingest_refunds(client, records)
    finally:
        await client.aclose()


async def _do_report(month):
    client = gel.create_async_client()
    try:
        await client.ensure_connected()
        return await build_report(client, month)
    finally:
        await client.aclose()


def _cmd_ingest(args):
    try:
        file_path, extra = _parse_option(args, "--file", required=True)
    except _ArgError as exc:
        _err(str(exc))
        return 2
    if extra:
        _err(f"unrecognised option: {extra[0]}")
        return 2

    if not os.path.isfile(file_path):
        _err("refunds file not found")
        return 3

    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            records = json.load(fh)
    except (json.JSONDecodeError, ValueError, OSError):
        _err("invalid refunds file")
        return 4
    if not isinstance(records, list):
        _err("invalid refunds file")
        return 4

    try:
        result = asyncio.run(_do_ingest(records))
    except ValueError:
        _err("invalid refunds file")
        return 4

    sys.stdout.write(json.dumps(result) + "\n")
    return 0


def _cmd_report(args):
    try:
        month, extra = _parse_option(args, "--month", required=False)
    except _ArgError as exc:
        _err(str(exc))
        return 2
    if extra:
        _err(f"unrecognised option: {extra[0]}")
        return 2

    try:
        result = asyncio.run(_do_report(month))
    except ValueError:
        _err("invalid month")
        return 5

    sys.stdout.write(json.dumps(result) + "\n")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        _err("missing subcommand")
        return 2
    sub = argv[0]
    rest = argv[1:]
    if sub == "ingest-refunds":
        return _cmd_ingest(rest)
    if sub == "report":
        return _cmd_report(rest)
    _err(f"unrecognised subcommand: {sub}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
