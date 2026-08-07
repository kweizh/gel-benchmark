"""Report command — writes out/report.json with the build_report() output."""

import json
import os

import gateway


def main():
    report = gateway.build_report()

    os.makedirs("out", exist_ok=True)
    with open("out/report.json", "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
