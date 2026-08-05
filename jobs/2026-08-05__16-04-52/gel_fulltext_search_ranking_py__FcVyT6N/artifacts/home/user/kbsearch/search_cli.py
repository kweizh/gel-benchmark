"""Command-line front end for the knowledge base search service."""

import argparse
import asyncio
import json
import sys

from search_service import search_articles, VALID_STATUSES


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search the knowledge base.",
        add_help=False,
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="Search query text",
    )
    parser.add_argument(
        "--status",
        type=str,
        default=None,
        choices=sorted(VALID_STATUSES),
        help="Filter by article status",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default=None,
        help="Filter by exact tag",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum results to return (default: 10)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of results to skip (default: 0)",
    )

    try:
        args = parser.parse_args()
    except SystemExit:
        # argparse prints to stderr and exits; we want exit code 2
        sys.exit(2)

    # Validate limit and offset
    if args.limit < 0:
        print(f"limit must be a non-negative integer, got {args.limit}", file=sys.stderr)
        sys.exit(2)
    if args.offset < 0:
        print(f"offset must be a non-negative integer, got {args.offset}", file=sys.stderr)
        sys.exit(2)

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
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
