#!/usr/bin/env python3
"""Apply repair rules R1-R7 to the 'recovered' branch.

Assumes the branch already exists, has the loose schema, and contains the
restored pre-incident data.  Rules are applied strictly in order; each rule
sees the result of the previous ones.
"""
import json
import subprocess
import sys

BRANCH = "recovered"
PROJECT = "/home/user/recovery"


def gel(edgeql, output="json-lines"):
    cmd = ["gel", "-b", BRANCH, "query", "-F", output, edgeql]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"gel query failed (exit {r.returncode}):\n{edgeql}\n")
    return r.stdout


def exec_dml(edgeql):
    gel(edgeql, output="json")


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# R1 - warehouse codes: trim + upper-case
log("R1: normalizing warehouse codes")
exec_dml(
    r"update Warehouse set { code := str_upper(re_replace(r'^\s+|\s+$', '', Warehouse.code, flags := 'g')) }"
)

# R2 - tracking codes: remove ALL whitespace + upper-case
log("R2: normalizing tracking codes")
exec_dml(
    r"update Shipment set { tracking := str_upper(re_replace(r'\s', '', Shipment.tracking, flags := 'g')) }"
)

# R3a - statuses: trim, lower-case, spaces/hyphens -> underscore (null/empty -> '')
log("R3a: normalizing statuses (trim/lower/replace)")
exec_dml(
    r"update Shipment set { status := re_replace(r'[ -]', '_', str_lower(re_replace(r'^\s+|\s+$', '', Shipment.status ?? '', flags := 'g')), flags := 'g') }"
)

# R3b - statuses: translate legacy spellings, empty -> pending
log("R3b: translating legacy statuses")
exec_dml(
    "update Shipment set {\n"
    "  status := (\n"
    "    if .status = '' then 'pending'\n"
    "    else if .status = 'awaiting' then 'pending'\n"
    "    else if .status = 'transit' then 'in_transit'\n"
    "    else if .status = 'complete' then 'delivered'\n"
    "    else if .status = 'done' then 'delivered'\n"
    "    else if .status = 'return' then 'returned'\n"
    "    else if .status = 'returned_to_sender' then 'returned'\n"
    "    else .status\n"
    "  )\n"
    "}"
)

# R4 - weights: delete 0/absent, abs the negatives
# NOTE: in EdgeQL `{} or true` yields {} (empty), so an `or`-based filter would
# miss the absent-weight rows.  Coalescing to 0 first collapses both cases.
log("R4: deleting zero/absent weights, absing negatives")
exec_dml(r"delete Shipment filter (.weight_kg ?? 0) = 0")
exec_dml(
    r"update Shipment filter .weight_kg < 0 set { weight_kg := math::abs(.weight_kg) }"
)

# R5 - origins: link null-origin shipments to matching warehouse, delete the rest, sync origin_code
log("R5: linking origins")
exec_dml(
    r"update Shipment filter not exists(.origin) set { origin := (select Warehouse filter .code = str_upper(re_replace(r'^\s+|\s+$', '', Shipment.origin_code, flags := 'g'))) }"
)
exec_dml(r"delete Shipment filter not exists(.origin)")
exec_dml(r"update Shipment set { origin_code := .origin.code }")

# R6 - duplicates: among shipments sharing a tracking, keep smallest seq, delete the rest
log("R6: removing duplicate tracking codes")
rows = gel("select Shipment { seq, tracking } order by .seq")
shipments = [json.loads(line) for line in rows.strip().splitlines() if line.strip()]
by_tracking = {}
for s in shipments:
    by_tracking.setdefault(s["tracking"], []).append(s["seq"])
to_delete = []
for tracking, seqs in by_tracking.items():
    if len(seqs) > 1:
        keep = min(seqs)
        to_delete.extend(seq for seq in seqs if seq != keep)
if to_delete:
    seqs_str = ", ".join(str(s) for s in to_delete)
    exec_dml(f"delete Shipment filter .seq in {{{seqs_str}}}")
    log(f"  deleted duplicate seqs: {sorted(to_delete)}")
else:
    log("  no duplicates found")

# R7 - stale warehouses: delete warehouses with no surviving shipments
log("R7: deleting stale warehouses")
exec_dml(r"delete Warehouse filter not exists(.<origin[is Shipment])")

log("repair complete")
