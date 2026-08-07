#!/usr/bin/env python3
"""Verification CLI for the Gel disaster-recovery task.

Restores the produced backup into a throw-away branch, compares it with the
target branch, and prints a single JSON report to stdout. See the task
description for the exact contract (keys, ordering, exit codes).
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

INSTANCE = "recovery"
DUMP_PATH = "/home/user/recovery/backups/recovered.dump"
ROUNDTRIP_BRANCH = "verify_roundtrip"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

METRICS_QUERY = """
select {
  warehouse_count := count(Warehouse),
  shipment_count := count(Shipment),
  status_delivered := count(Shipment filter .status = 'delivered'),
  status_in_transit := count(Shipment filter .status = 'in_transit'),
  status_pending := count(Shipment filter .status = 'pending'),
  status_returned := count(Shipment filter .status = 'returned'),
  total_weight := sum(Shipment.weight_kg),
  warehouses := (
    select Warehouse { code, n := count(.<origin[is Shipment]) }
    order by .code
  ),
  trackings := (select Shipment.tracking),
  migration_count := count(schema::Migration),
}
"""


def run(cmd, input_text=None):
    return subprocess.run(
        cmd, capture_output=True, text=True, input=input_text
    )


def die_stderr(msg):
    print(msg, file=sys.stderr)


def ensure_server_running():
    r = run(["gel-start"])
    if r.returncode != 0:
        die_stderr(f"gel-start failed: {r.stderr.strip()}")


def list_branches():
    r = run(["gel", "-I", INSTANCE, "branch", "list"])
    if r.returncode != 0:
        die_stderr(f"gel branch list failed: {r.stderr.strip()}")
        return []
    names = []
    for line in r.stdout.splitlines():
        line = ANSI_RE.sub("", line).strip()
        if not line:
            continue
        token = line.split()[0]
        names.append(token)
    return names


def branch_exists(name):
    return name in list_branches()


def drop_branch_if_exists(name):
    if branch_exists(name):
        r = run(
            ["gel", "-I", INSTANCE, "branch", "drop", name, "--non-interactive", "--force"]
        )
        if r.returncode != 0:
            die_stderr(f"failed to drop branch {name}: {r.stderr.strip()}")


def collect_metrics(branch):
    r = run(
        ["gel", "-I", INSTANCE, "-b", branch, "query", "-F", "json", METRICS_QUERY]
    )
    if r.returncode != 0:
        raise RuntimeError(f"query failed for branch {branch!r}: {r.stderr.strip()}")
    rows = json.loads(r.stdout)
    data = rows[0]

    status_counts = {
        "delivered": data["status_delivered"],
        "in_transit": data["status_in_transit"],
        "pending": data["status_pending"],
        "returned": data["status_returned"],
    }
    warehouse_counts = {w["code"]: w["n"] for w in data["warehouses"]}
    total_weight_kg = round((data["total_weight"] or 0.0) + 0.0, 3)
    trackings_sorted = sorted(data["trackings"])
    tracking_text = "\n".join(trackings_sorted)
    tracking_checksum = hashlib.sha256(tracking_text.encode("utf-8")).hexdigest()
    counts = {"Warehouse": data["warehouse_count"], "Shipment": data["shipment_count"]}

    return {
        "counts": counts,
        "status_counts": status_counts,
        "warehouse_counts": warehouse_counts,
        "total_weight_kg": total_weight_kg,
        "tracking_checksum": tracking_checksum,
        "migration_count": data["migration_count"],
    }


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", default="recovered")
    args = parser.parse_args()
    branch = args.branch

    if not os.path.isfile(DUMP_PATH):
        print(json.dumps({"error": "dump_not_found"}))
        sys.exit(2)

    ensure_server_running()

    if not branch_exists(branch):
        print(json.dumps({"error": "branch_not_found"}))
        sys.exit(3)

    dump_size_bytes = os.path.getsize(DUMP_PATH)
    dump_sha256 = sha256_file(DUMP_PATH)

    target_metrics = collect_metrics(branch)

    # Round-trip validation via a throw-away branch.
    drop_branch_if_exists(ROUNDTRIP_BRANCH)
    roundtrip_metrics = None
    try:
        r = run(["gel", "-I", INSTANCE, "branch", "create", ROUNDTRIP_BRANCH, "--empty"])
        if r.returncode != 0:
            die_stderr(f"failed to create {ROUNDTRIP_BRANCH}: {r.stderr.strip()}")
        else:
            r = run(
                ["gel", "-I", INSTANCE, "restore", "-b", ROUNDTRIP_BRANCH, DUMP_PATH]
            )
            if r.returncode != 0:
                die_stderr(f"failed to restore into {ROUNDTRIP_BRANCH}: {r.stderr.strip()}")
            else:
                roundtrip_metrics = collect_metrics(ROUNDTRIP_BRANCH)
    finally:
        drop_branch_if_exists(ROUNDTRIP_BRANCH)

    if roundtrip_metrics is None:
        roundtrip_ok = False
    else:
        roundtrip_ok = (
            target_metrics["counts"] == roundtrip_metrics["counts"]
            and target_metrics["status_counts"] == roundtrip_metrics["status_counts"]
            and target_metrics["warehouse_counts"] == roundtrip_metrics["warehouse_counts"]
            and target_metrics["total_weight_kg"] == roundtrip_metrics["total_weight_kg"]
            and target_metrics["tracking_checksum"] == roundtrip_metrics["tracking_checksum"]
        )

    result = {
        "branch": branch,
        "dump_path": DUMP_PATH,
        "dump_size_bytes": dump_size_bytes,
        "dump_sha256": dump_sha256,
        "migration_count": target_metrics["migration_count"],
        "counts": target_metrics["counts"],
        "status_counts": target_metrics["status_counts"],
        "warehouse_counts": target_metrics["warehouse_counts"],
        "total_weight_kg": target_metrics["total_weight_kg"],
        "tracking_checksum": target_metrics["tracking_checksum"],
        "roundtrip_branch": ROUNDTRIP_BRANCH,
        "roundtrip_ok": roundtrip_ok,
    }

    print(json.dumps(result))
    sys.exit(0 if roundtrip_ok else 4)


if __name__ == "__main__":
    main()
