import json
import subprocess
import sys

def run_gel_query(query, branch='recovered'):
    res = subprocess.run(
        ['gel', '-b', branch, 'query', '--output-format', 'json', query],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        print(f"Error running query: {query}", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(res.stdout)

def main():
    # 1. Fetch warehouses and shipments
    warehouses = run_gel_query("select Warehouse { id, code, name }")
    shipments = run_gel_query("select Shipment { id, seq, tracking, status, weight_kg, origin_code, origin_id := .origin.id }")

    # Keep track of original lists
    orig_warehouse_ids = {w['id'] for w in warehouses}
    orig_shipment_ids = {s['id'] for s in shipments}

    # Apply Repair Rules in memory
    # R1 — warehouse codes
    for w in warehouses:
        w['code'] = w['code'].strip().upper()

    # R2 — tracking codes
    for s in shipments:
        if s['tracking'] is not None:
            s['tracking'] = "".join(c for c in s['tracking'] if not c.isspace()).upper()

    # R3 — statuses
    def normalize_status(status):
        if status is None:
            return 'pending'
        s = status.strip().lower()
        s = s.replace(' ', '_').replace('-', '_')
        mapping = {
            'awaiting': 'pending',
            'transit': 'in_transit',
            'complete': 'delivered',
            'done': 'delivered',
            'return': 'returned',
            'returned_to_sender': 'returned'
        }
        if s in mapping:
            s = mapping[s]
        return s

    for s in shipments:
        s['status'] = normalize_status(s['status'])

    # R4 — weights
    surviving_shipments = []
    for s in shipments:
        w = s['weight_kg']
        if w is None or w == 0:
            continue
        if w < 0:
            s['weight_kg'] = abs(w)
        surviving_shipments.append(s)
    shipments = surviving_shipments

    # R5 — origins
    warehouse_by_id = {w['id']: w for w in warehouses}
    warehouse_by_code = {w['code']: w for w in warehouses}
    surviving_shipments = []
    for s in shipments:
        if s['origin_id'] is not None:
            linked_wh = warehouse_by_id.get(s['origin_id'])
            if linked_wh is not None:
                s['origin_code'] = linked_wh['code']
                surviving_shipments.append(s)
            else:
                s['origin_id'] = None
        
        if s['origin_id'] is None:
            if s['origin_code'] is None:
                continue
            target_code = s['origin_code'].strip().upper()
            matching_wh = warehouse_by_code.get(target_code)
            if matching_wh is not None:
                s['origin_id'] = matching_wh['id']
                s['origin_code'] = matching_wh['code']
                surviving_shipments.append(s)
            else:
                continue
    shipments = surviving_shipments

    # R6 — duplicates
    from collections import defaultdict
    tracking_groups = defaultdict(list)
    for s in shipments:
        tracking_groups[s['tracking']].append(s)

    surviving_shipments = []
    for tracking, group in tracking_groups.items():
        group.sort(key=lambda x: x['seq'])
        surviving_shipments.append(group[0])
    shipments = surviving_shipments

    # R7 — stale warehouses
    surviving_warehouse_ids = set(s['origin_id'] for s in shipments)
    surviving_warehouses = [w for w in warehouses if w['id'] in surviving_warehouse_ids]

    # Compute deletes
    surviving_shipment_ids = {s['id'] for s in shipments}
    shipments_to_delete = orig_shipment_ids - surviving_shipment_ids

    warehouses_to_delete = orig_warehouse_ids - surviving_warehouse_ids

    # Generate EdgeQL script
    edgeql_lines = []

    # 1. Delete shipments
    if shipments_to_delete:
        ids_str = ", ".join(f"<uuid>'{sid}'" for sid in shipments_to_delete)
        edgeql_lines.append(f"delete Shipment filter .id in {{{ids_str}}};")

    # 2. Delete warehouses
    if warehouses_to_delete:
        ids_str = ", ".join(f"<uuid>'{sid}'" for sid in warehouses_to_delete)
        edgeql_lines.append(f"delete Warehouse filter .id in {{{ids_str}}};")

    # 3. Update Warehouses to temporary codes to prevent constraint violations
    for w in surviving_warehouses:
        temp_code = f"TEMP_{w['id'].replace('-', '_')}"
        edgeql_lines.append(f"update Warehouse filter .id = <uuid>'{w['id']}' set {{ code := '{temp_code}' }};")

    # 4. Update Shipments to temporary tracking to prevent constraint violations
    for s in shipments:
        temp_tracking = f"TEMP_{s['id'].replace('-', '_')}"
        edgeql_lines.append(f"update Shipment filter .id = <uuid>'{s['id']}' set {{ tracking := '{temp_tracking}' }};")

    # 5. Update Warehouses to final normalized codes
    for w in surviving_warehouses:
        edgeql_lines.append(f"update Warehouse filter .id = <uuid>'{w['id']}' set {{ code := '{w['code']}' }};")

    # 6. Update Shipments to final values
    for s in shipments:
        edgeql_lines.append(
            f"update Shipment filter .id = <uuid>'{s['id']}' set {{\n"
            f"  tracking := '{s['tracking']}',\n"
            f"  status := '{s['status']}',\n"
            f"  weight_kg := <float64>{s['weight_kg']},\n"
            f"  origin_code := '{s['origin_code']}',\n"
            f"  origin := (select Warehouse filter .id = <uuid>'{s['origin_id']}')\n"
            f"}};"
        )

    # Write EdgeQL to a temporary file
    edgeql_script = "\n".join(edgeql_lines)
    with open('/tmp/repair_script.edgeql', 'w') as f:
        f.write(edgeql_script)

    # Execute the EdgeQL script
    print("Executing repair script...")
    res = subprocess.run(
        ['gel', '-b', 'recovered', 'query', '-f', '/tmp/repair_script.edgeql'],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        print("Error executing repair script!", file=sys.stderr)
        print(res.stderr, file=sys.stderr)
        sys.exit(1)
    print("Repair completed successfully!")

if __name__ == '__main__':
    main()
