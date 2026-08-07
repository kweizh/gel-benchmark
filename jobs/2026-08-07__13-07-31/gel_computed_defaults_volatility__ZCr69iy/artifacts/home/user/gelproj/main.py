import asyncio
import json
from semantics import build_report

async def main():
    report = await build_report()
    
    # Write the report to /home/user/gelproj/report.json
    with open("report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print("Report written successfully.")

if __name__ == "__main__":
    asyncio.run(main())
