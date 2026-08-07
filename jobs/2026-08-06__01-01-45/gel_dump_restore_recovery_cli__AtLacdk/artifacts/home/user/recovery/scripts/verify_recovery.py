import sys
import os
import hashlib
import json
import subprocess
import re

def strip_ansi(text):
    ansi_escape = re.compile(r'(?:\x1B[@-_][0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def check_branch_exists(branch):
    res = subprocess.run(['gel', 'branch', 'list'], capture_output=True, text=True)
    if res.returncode != 0:
        return False
    branches = []
    for line in res.stdout.splitlines():
        # strip ansi
        cleaned = strip_ansi(line).strip()
        if ' - ' in cleaned:
            cleaned = cleaned.split(' - ')[0].strip()
        if cleaned:
            branches.append(cleaned)
    if branch in branches:
        return True
    
    # Fallback/double-check using query
    res2 = subprocess.run(
        ['gel', '-b', branch, 'query', 'select 1;'],
        capture_output=True, text=True
    )
    if res2.returncode == 0:
        return True
    if "does not exist" in res2.stderr:
        return False
    return False

def get_file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def run_gel_query(query, branch):
    res = subprocess.run(
        ['gel', '-b', branch, 'query', '--output-format', 'json', query],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        print(f"Error running query on {branch}: {query}", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        sys.exit(5) # internal error
    return json.loads(res.stdout)

def get_branch_stats(branch):
    # 1. migration_count
    mig_res = run_gel_query("select count(schema::Migration)", branch)
    migration_count = mig_res[0] if isinstance(mig_res, list) else mig_res

    # 2. counts
    w_count_res = run_gel_query("select count(Warehouse)", branch)
    w_count = w_count_res[0] if isinstance(w_count_res, list) else w_count_res

    s_count_res = run_gel_query("select count(Shipment)", branch)
    s_count = s_count_res[0] if isinstance(s_count_res, list) else s_count_res

    counts = {
        "Warehouse": w_count,
        "Shipment": s_count
    }

    # Fetch all warehouses and shipments to compute the rest in Python
    warehouses = run_gel_query("select Warehouse { code }", branch)
    shipments = run_gel_query("select Shipment { status, weight_kg, tracking, origin: { code } }", branch)

    # status_counts
    status_counts = {"delivered": 0, "in_transit": 0, "pending": 0, "returned": 0}
    for s in shipments:
        status = s.get('status')
        if status in status_counts:
            status_counts[status] += 1

    # warehouse_counts
    warehouse_counts = {w['code']: 0 for w in sorted(warehouses, key=lambda x: x['code'])}
    for s in shipments:
        if s.get('origin') is not None:
            code = s['origin']['code']
            if code in warehouse_counts:
                warehouse_counts[code] += 1

    # total_weight_kg
    total_weight = sum(s.get('weight_kg') or 0.0 for s in shipments)
    total_weight_kg = round(total_weight, 3)

    # tracking_checksum
    trackings = [s['tracking'] for s in shipments if s.get('tracking') is not None]
    trackings.sort()
    joined_text = "\n".join(trackings)
    if not trackings:
        tracking_checksum = hashlib.sha256(b"").hexdigest()
    else:
        tracking_checksum = hashlib.sha256(joined_text.encode('utf-8')).hexdigest()

    return {
        "migration_count": migration_count,
        "counts": counts,
        "status_counts": status_counts,
        "warehouse_counts": warehouse_counts,
        "total_weight_kg": total_weight_kg,
        "tracking_checksum": tracking_checksum
    }

def drop_verify_branch():
    subprocess.run(
        ['gel', 'branch', 'drop', '--non-interactive', '--force', 'verify_roundtrip'],
        capture_output=True
    )

def main():
    # Parse arguments
    branch = 'recovered'
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--branch':
            if i + 1 < len(args):
                branch = args[i+1]
                i += 2
            else:
                print("Error: --branch requires an argument", file=sys.stderr)
                sys.exit(1)
        else:
            i += 1

    dump_path = "/home/user/recovery/backups/recovered.dump"

    # Precedence 1: Check if dump file exists
    if not os.path.exists(dump_path):
        print(json.dumps({"error": "dump_not_found"}))
        sys.exit(2)

    # Precedence 2: Check if target branch exists
    if not check_branch_exists(branch):
        print(json.dumps({"error": "branch_not_found"}))
        sys.exit(3)

    # Gather target branch stats
    target_stats = get_branch_stats(branch)

    # Compute dump info
    dump_size_bytes = os.path.getsize(dump_path)
    dump_sha256 = get_file_sha256(dump_path)

    # Perform roundtrip validation
    roundtrip_ok = False
    try:
        drop_verify_branch()
        # Create empty verify_roundtrip
        subprocess.run(['gel', 'branch', 'create', '-e', 'verify_roundtrip'], check=True, capture_output=True)
        # Restore dump into verify_roundtrip
        subprocess.run(['gel', 'restore', '-b', 'verify_roundtrip', dump_path], check=True, capture_output=True)
        # Get stats of verify_roundtrip
        roundtrip_stats = get_branch_stats('verify_roundtrip')

        # Compare stats
        roundtrip_ok = (
            target_stats['counts'] == roundtrip_stats['counts'] and
            target_stats['status_counts'] == roundtrip_stats['status_counts'] and
            target_stats['warehouse_counts'] == roundtrip_stats['warehouse_counts'] and
            abs(target_stats['total_weight_kg'] - roundtrip_stats['total_weight_kg']) < 1e-5 and
            target_stats['tracking_checksum'] == roundtrip_stats['tracking_checksum']
        )
    except Exception as e:
        print(f"Exception during roundtrip: {e}", file=sys.stderr)
        roundtrip_ok = False
    finally:
        drop_verify_branch()

    # Build the final ordered JSON report
    report = {
        "branch": branch,
        "dump_path": dump_path,
        "dump_size_bytes": dump_size_bytes,
        "dump_sha256": dump_sha256,
        "migration_count": target_stats['migration_count'],
        "counts": target_stats['counts'],
        "status_counts": target_stats['status_counts'],
        "warehouse_counts": target_stats['warehouse_counts'],
        "total_weight_kg": target_stats['total_weight_kg'],
        "tracking_checksum": target_stats['tracking_checksum'],
        "roundtrip_branch": "verify_roundtrip",
        "roundtrip_ok": roundtrip_ok
    }

    print(json.dumps(report, indent=2))

    if roundtrip_ok:
        sys.exit(0)
    else:
        sys.exit(4)

if __name__ == '__main__':
    main()
