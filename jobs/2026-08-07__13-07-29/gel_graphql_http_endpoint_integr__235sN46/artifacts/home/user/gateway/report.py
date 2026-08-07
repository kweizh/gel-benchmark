"""Recreate out/report.json from gateway.build_report()."""

import json
import os
import sys

import gateway


def main() -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(root, "out")
    os.makedirs(out_dir, exist_ok=True)

    report = gateway.build_report()

    out_path = os.path.join(out_dir, "report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
