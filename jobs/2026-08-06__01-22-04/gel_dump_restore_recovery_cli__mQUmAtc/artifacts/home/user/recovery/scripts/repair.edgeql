# R1 - warehouse codes: trim + upper
update Warehouse
set { code := str_upper(str_trim(.code)) };

# R2 - tracking codes: remove all whitespace, upper-case
update Shipment
set { tracking := str_upper(re_replace(r'\s', '', .tracking, flags := 'g')) };

# R3 - statuses: trim, lower, spaces/hyphens -> underscore, legacy mapping, null -> pending
update Shipment
set {
  status := (
    with raw := str_lower(str_trim(.status ?? '')),
         norm := re_replace(r'[ -]', '_', raw, flags := 'g')
    select (
      'pending'    if norm = '' else
      'pending'    if norm = 'awaiting' else
      'in_transit' if norm = 'transit' else
      'delivered'  if norm = 'complete' else
      'delivered'  if norm = 'done' else
      'returned'   if norm = 'returned_to_sender' else
      'returned'   if norm = 'return' else
      norm
    )
  )
};

# R4 - weights: negative -> abs; zero/absent -> delete
update Shipment
filter .weight_kg < 0
set { weight_kg := math::abs(.weight_kg) };

delete Shipment
filter not exists .weight_kg or .weight_kg ?= 0;

# R5 - origins: link shipments without origin to matching warehouse by normalized origin_code;
# delete shipments that have no origin_code or no matching warehouse.
update Shipment
filter not exists .origin and exists .origin_code
set {
  origin := (
    with code := str_upper(str_trim(.origin_code))
    select Warehouse
    filter .code = code
    limit 1
  )
};

delete Shipment
filter not exists .origin;

# after linking, origin_code must exactly equal the linked warehouse's code
update Shipment
set { origin_code := .origin.code };

# R6 - duplicates: among shipments sharing the same tracking, keep smallest seq
with keep_seqs := (
  select (group Shipment by .tracking) {
    keep_seq := min(.elements.seq)
  }
).keep_seq
delete Shipment
filter .seq not in keep_seqs;

# R7 - stale warehouses: delete warehouses with no surviving shipment linked
delete Warehouse
filter not exists (select Shipment filter Shipment.origin = Warehouse);
