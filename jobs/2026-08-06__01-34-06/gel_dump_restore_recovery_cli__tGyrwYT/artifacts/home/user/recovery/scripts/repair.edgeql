# R1: warehouse codes - trim and uppercase
update Warehouse
set { code := str_upper(str_trim(.code)) };

# R2: tracking codes - remove all whitespace (spaces, tabs, newlines) and uppercase
update Shipment
set { tracking := str_upper(re_replace(r'[ \t\n\r]+', '', .tracking, flags := 'g')) };

# R3: statuses - null/empty -> pending
with to_fix := (select Shipment filter not exists .status or .status = ''),
update Shipment
filter .id in to_fix.id
set { status := 'pending' };

# R3: statuses - trim, lowercase
update Shipment
set { status := str_lower(str_trim(.status)) };

# R3: statuses - replace spaces with underscore (global)
update Shipment
set { status := re_replace(r'[ ]+', '_', .status, flags := 'g') };

# R3: statuses - replace hyphens with underscore (global)
update Shipment
set { status := re_replace(r'[-]+', '_', .status, flags := 'g') };

# R3: statuses - translate legacy spellings
with to_fix := (select Shipment filter .status = 'awaiting'),
update Shipment filter .id in to_fix.id set { status := 'pending' };
with to_fix := (select Shipment filter .status = 'transit'),
update Shipment filter .id in to_fix.id set { status := 'in_transit' };
with to_fix := (select Shipment filter .status = 'complete'),
update Shipment filter .id in to_fix.id set { status := 'delivered' };
with to_fix := (select Shipment filter .status = 'done'),
update Shipment filter .id in to_fix.id set { status := 'delivered' };
with to_fix := (select Shipment filter .status = 'return'),
update Shipment filter .id in to_fix.id set { status := 'returned' };
with to_fix := (select Shipment filter .status = 'returned_to_sender'),
update Shipment filter .id in to_fix.id set { status := 'returned' };

# R4: weights - negative -> absolute value
with to_fix := (select Shipment filter exists .weight_kg and .weight_kg < 0),
update Shipment
filter .id in to_fix.id
set { weight_kg := -(.weight_kg) };

# R5: origins - first handle shipments WITH origin link: update origin_code to match
with to_fix := (select Shipment filter exists .origin),
update Shipment
filter .id in to_fix.id
set { origin_code := .origin.code };

# R5: origins - shipments WITHOUT origin link but WITH origin_code matching a warehouse
with
  to_link := (
    select Shipment 
    filter not exists .origin 
      and exists .origin_code
  ),
for t in to_link union (
  with
    norm_code := str_upper(str_trim(t.origin_code)),
    wh := (select Warehouse filter .code = norm_code limit 1),
  update Shipment
  filter .id = t.id and exists (select wh)
  set { origin := (select Warehouse filter .code = norm_code limit 1) }
);

# Update origin_code for those we just linked
with to_fix := (select Shipment filter exists .origin),
update Shipment
filter .id in to_fix.id
set { origin_code := .origin.code };

# R4: delete shipments with weight 0 or absent (do this before R5 origin deletion)
delete Shipment
filter (not exists .weight_kg) or .weight_kg = 0;

# R5: delete shipments still without origin (including those with no matching warehouse or no origin_code)
delete Shipment
filter not exists .origin;

# R6: duplicates - for each tracking, keep smallest seq
with
  dup_trackings := (
    select Shipment
    filter count((select Shipment filter .tracking = Shipment.tracking)) > 1
  ),
for d in dup_trackings union (
  with
    same_tracking := (select Shipment filter .tracking = d.tracking),
    min_seq := min(same_tracking.seq),
    to_delete := (select same_tracking filter .seq > min_seq),
  delete Shipment filter .id in to_delete.id
);

# R7: delete stale warehouses (no surviving shipment linked)
delete Warehouse
filter not exists (select Shipment filter .origin = Warehouse);
