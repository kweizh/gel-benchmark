"""Main entry point for the Gel semantics report."""

import asyncio
import json
import sys

from semantics import build_report


async def main() -> None:
    report = await build_report()
    with open("/home/user/gelproj/report.json", "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
