import sys
import os
import json
import asyncio
import gel
from analytics.rollups import build_report, ingest_refunds

async def main_async():
    args = sys.argv[1:]
    if not args:
        print("Missing subcommand", file=sys.stderr)
        sys.exit(2)
        
    subcommand = args[0]
    if subcommand == 'ingest-refunds':
        file_path = None
        i = 1
        while i < len(args):
            arg = args[i]
            if arg == '--file':
                if i + 1 < len(args):
                    file_path = args[i+1]
                    i += 2
                else:
                    print("Missing value for --file", file=sys.stderr)
                    sys.exit(2)
            else:
                print(f"Unrecognised option or argument: {arg}", file=sys.stderr)
                sys.exit(2)
                
        if file_path is None:
            print("Missing --file option", file=sys.stderr)
            sys.exit(2)
            
        if not os.path.exists(file_path):
            print("refunds file not found", file=sys.stderr)
            sys.exit(3)
            
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
        except Exception:
            print("invalid refunds file", file=sys.stderr)
            sys.exit(4)
            
        client = gel.create_async_client()
        try:
            res = await ingest_refunds(client, records)
        except ValueError:
            print("invalid refunds file", file=sys.stderr)
            sys.exit(4)
        except Exception as e:
            print("invalid refunds file", file=sys.stderr)
            sys.exit(4)
            
        print(json.dumps(res))
        sys.exit(0)
        
    elif subcommand == 'report':
        month = None
        i = 1
        while i < len(args):
            arg = args[i]
            if arg == '--month':
                if i + 1 < len(args):
                    month = args[i+1]
                    i += 2
                else:
                    print("Missing value for --month", file=sys.stderr)
                    sys.exit(2)
            else:
                print(f"Unrecognised option or argument: {arg}", file=sys.stderr)
                sys.exit(2)
                
        if month is not None:
            import re
            if not isinstance(month, str) or not re.match(r"^[0-9]{4}-(0[1-9]|1[0-2])$", month):
                print("invalid month", file=sys.stderr)
                sys.exit(5)
                
        client = gel.create_async_client()
        try:
            res = await build_report(client, month)
        except ValueError:
            print("invalid month", file=sys.stderr)
            sys.exit(5)
            
        print(json.dumps(res))
        sys.exit(0)
        
    else:
        print(f"Unrecognised subcommand: {subcommand}", file=sys.stderr)
        sys.exit(2)

def main():
    try:
        asyncio.run(main_async())
    except SystemExit as e:
        sys.exit(e.code)
    except Exception as e:
        print(f"Unhandled error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
