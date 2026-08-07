#!/usr/bin/env python3
"""Command-line front end for the wiki document store.

Usage::

    python3 wikicli.py <subcommand> [options]

Each subcommand writes exactly one JSON value to stdout (and nothing else)
and exits with status 0 on success.  Timestamps are rendered as ISO-8601
strings.  Failures are reported as a JSON object on stdout plus a non-zero
exit status, without a traceback.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from typing import Any

import gel

import docstore


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #
def _json_default(obj: Any) -> Any:
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _dump(value: Any) -> str:
    return json.dumps(value, default=_json_default)


# --------------------------------------------------------------------------- #
# Subcommand handlers
# --------------------------------------------------------------------------- #
async def cmd_create(client, args) -> dict:
    return await docstore.create_document(
        client,
        slug=args.slug,
        title=args.title,
        body=args.body,
        author=args.author,
    )


async def cmd_show(client, args) -> dict:
    return await docstore.get_document(client, slug=args.slug)


async def cmd_update(client, args) -> dict:
    return await docstore.update_document(
        client,
        slug=args.slug,
        expected_revision=args.expected_revision,
        author=args.author,
        title=args.title,
        body=args.body,
    )


async def cmd_history(client, args) -> list[dict]:
    return await docstore.get_history(client, slug=args.slug)


async def cmd_race(client, args) -> dict:
    slug: str = args.slug
    count: int = args.count
    author: str = args.author

    tasks = [
        docstore.append_line(
            client, slug=slug, line=f"{author}#{i}", author=author
        )
        for i in range(1, count + 1)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # A missing document is a hard error even inside the race.
    for r in results:
        if isinstance(r, docstore.DocumentNotFound):
            raise r

    accepted = sum(1 for r in results if not isinstance(r, BaseException))
    doc = await docstore.get_document(client, slug=slug)
    history = await docstore.get_history(client, slug=slug)
    return {
        "slug": slug,
        "requested": count,
        "accepted": accepted,
        "final_revision": doc["revision"],
        "history_length": len(history),
    }


# --------------------------------------------------------------------------- #
# Error -> (json payload, exit code) mapping
# --------------------------------------------------------------------------- #
def _error_payload(exc: Exception):
    if isinstance(exc, docstore.StaleRevision):
        return (
            {
                "error": "stale_revision",
                "slug": exc.slug,
                "expected_revision": exc.expected_revision,
                "actual_revision": exc.actual_revision,
            },
            3,
        )
    if isinstance(exc, docstore.DocumentNotFound):
        return ({"error": "document_not_found", "slug": exc.slug}, 4)
    if isinstance(exc, docstore.SlugConflict):
        return ({"error": "slug_conflict", "slug": exc.slug}, 5)
    # Anything else: report as a generic error (exit 1), no traceback.
    return ({"error": "internal_error", "message": str(exc)}, 1)


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wikicli.py")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("create")
    p.add_argument("--slug", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--author", required=True)

    p = sub.add_parser("show")
    p.add_argument("--slug", required=True)

    p = sub.add_parser("update")
    p.add_argument("--slug", required=True)
    p.add_argument("--expected-revision", required=True, type=int)
    p.add_argument("--author", required=True)
    p.add_argument("--title", default=None)
    p.add_argument("--body", default=None)

    p = sub.add_parser("history")
    p.add_argument("--slug", required=True)

    p = sub.add_parser("race")
    p.add_argument("--slug", required=True)
    p.add_argument("--count", required=True, type=int)
    p.add_argument("--author", required=True)

    return parser


_HANDLERS = {
    "create": cmd_create,
    "show": cmd_show,
    "update": cmd_update,
    "history": cmd_history,
    "race": cmd_race,
}


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
async def _amain(argv: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = _HANDLERS[args.subcommand]

    client = gel.create_async_client()
    try:
        result = await handler(client, args)
    except docstore.DocStoreError as exc:
        payload, code = _error_payload(exc)
        sys.stdout.write(_dump(payload) + "\n")
        return code
    except Exception as exc:  # noqa: BLE001 - report cleanly, no traceback
        payload, code = _error_payload(exc)
        sys.stdout.write(_dump(payload) + "\n")
        return code
    else:
        sys.stdout.write(_dump(result) + "\n")
        return 0
    finally:
        await client.aclose()


def main() -> None:
    sys.exit(asyncio.run(_amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
