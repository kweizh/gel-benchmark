import sys
import json
import asyncio
import argparse
from search_service import search_articles

class CustomArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f"error: {message}\n")
        sys.exit(2)

def main():
    parser = CustomArgumentParser(description="Ranked Full-Text Search CLI")
    parser.add_argument('--query', type=str, required=True, help="Search query")
    parser.add_argument('--status', type=str, choices=['draft', 'published', 'archived'], help="Article status")
    parser.add_argument('--tag', type=str, help="Article tag")
    parser.add_argument('--limit', type=str, default='10', help="Limit")
    parser.add_argument('--offset', type=str, default='0', help="Offset")

    try:
        args = parser.parse_args()
    except SystemExit:
        sys.exit(2)

    # Validate limit
    try:
        if not args.limit.isdigit():
            raise ValueError()
        limit_val = int(args.limit)
    except ValueError:
        sys.stderr.write("error: limit must be a non-negative integer\n")
        sys.exit(2)

    # Validate offset
    try:
        if not args.offset.isdigit():
            raise ValueError()
        offset_val = int(args.offset)
    except ValueError:
        sys.stderr.write("error: offset must be a non-negative integer\n")
        sys.exit(2)

    # Execute search
    try:
        result = asyncio.run(search_articles(
            query=args.query,
            status=args.status,
            tag=args.tag,
            limit=limit_val,
            offset=offset_val
        ))
        # Print payload as a single JSON object on stdout
        sys.stdout.write(json.dumps(result) + "\n")
        sys.exit(0)
    except ValueError as e:
        sys.stderr.write(f"error: {str(e)}\n")
        sys.exit(2)
    except Exception as e:
        sys.stderr.write(f"error: {str(e)}\n")
        sys.exit(2)

if __name__ == '__main__':
    main()
