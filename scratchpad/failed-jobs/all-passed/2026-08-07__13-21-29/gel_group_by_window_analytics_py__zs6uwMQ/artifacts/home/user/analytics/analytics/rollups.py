"""Async analytics rollup functions for the coffee-equipment retailer."""

from __future__ import annotations

import datetime
import json
import math
import re

import gel


_MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")


def _validate_month(month: str | None) -> None:
    if month is not None and not _MONTH_RE.match(month):
        raise ValueError(f"invalid month: {month!r}")


async def build_report(
    client: gel.AsyncIOClient, month: str | None = None
) -> dict:
    """Return the multi-key analytics report as a plain Python dict."""
    _validate_month(month)

    # Build a filter expression for the optional month scope.
    if month is not None:
        year, mon = month.split("-")
        start = f"{year}-{mon}-01T00:00:00Z"
        y, m = int(year), int(mon)
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
        end = f"{y:04d}-{m:02d}-01T00:00:00Z"
        scope_filter = f'FILTER .occurred_at >= <datetime>"{start}" AND .occurred_at < <datetime>"{end}"'
    else:
        scope_filter = ""

    # ------------------------------------------------------------------
    # 1. grand_total (single object)
    # ------------------------------------------------------------------
    grand_query = f"""
    WITH
      in_scope := (SELECT Sale {scope_filter}),
    SELECT (
      sale_count := count(in_scope),
      unit_count := sum(in_scope.units),
      gross_cents := sum(in_scope.amount_cents),
      net_cents := sum(in_scope.net_cents),
      refund_cents := sum(in_scope.amount_cents) - sum(in_scope.net_cents),
      month_count := count(DISTINCT (FOR s IN {{in_scope}} UNION (
        datetime_get(s.occurred_at, 'year') * 100 + datetime_get(s.occurred_at, 'month')
      ))),
      channel_count := count(DISTINCT in_scope.channel),
      category_count := count(DISTINCT in_scope.category),
    )
    """
    grand_row = await client.query_single(grand_query)
    grand = {
        "sale_count": int(grand_row.sale_count),
        "unit_count": int(grand_row.unit_count or 0),
        "gross_cents": int(grand_row.gross_cents or 0),
        "refund_cents": int(grand_row.refund_cents or 0),
        "net_cents": int(grand_row.net_cents or 0),
        "month_count": int(grand_row.month_count),
        "channel_count": int(grand_row.channel_count),
        "category_count": int(grand_row.category_count),
    }

    # ------------------------------------------------------------------
    # 2. monthly_by_channel (group by month + channel)
    # ------------------------------------------------------------------
    monthly_query = f"""
    WITH
      in_scope := (SELECT Sale {scope_filter}),
      groups := (
        GROUP in_scope
        USING month := to_str(.occurred_at, 'YYYY-MM'),
              channel := .channel
        BY (month, channel)
      ),
    SELECT groups {{
      key: {{ month, channel }},
      elements := .elements {{
        order_ref,
        net_cents,
      }},
      sale_count := count(.elements),
      unit_count := sum(.elements.units),
      gross_cents := sum(.elements.amount_cents),
      net_cents := sum(.elements.net_cents),
      refund_cents := sum(.elements.amount_cents) - sum(.elements.net_cents),
      min_net_cents := min(.elements.net_cents),
      max_net_cents := max(.elements.net_cents),
      mean_net_cents := sum(.elements.net_cents) / <float64>count(.elements),
    }}
    ORDER BY .key.month THEN .key.channel
    """
    monthly_rows = await client.query(monthly_query)

    monthly_by_channel = []
    for row in monthly_rows:
        elements = list(row.elements)
        # top_orders: at most 3, sorted by net_cents desc, then order_ref asc
        elements_sorted = sorted(
            elements,
            key=lambda s: (-s.net_cents, s.order_ref),
        )
        top_orders = [
            {"order_ref": s.order_ref, "net_cents": int(s.net_cents)}
            for s in elements_sorted[:3]
        ]

        sale_count = int(row.sale_count)

        # Compute sample stddev in Python when sale_count >= 2
        stddev_val = None
        if sale_count >= 2:
            net_cents_list = [s.net_cents for s in elements]
            mean = sum(net_cents_list) / sale_count
            variance = sum((x - mean) ** 2 for x in net_cents_list) / (sale_count - 1)
            stddev_val = round(math.sqrt(variance), 2)

        monthly_by_channel.append({
            "month": row.key.month,
            "channel": row.key.channel,
            "sale_count": sale_count,
            "unit_count": int(row.unit_count or 0),
            "gross_cents": int(row.gross_cents or 0),
            "refund_cents": int(row.refund_cents or 0),
            "net_cents": int(row.net_cents or 0),
            "mean_net_cents": round(row.mean_net_cents, 2),
            "min_net_cents": int(row.min_net_cents or 0),
            "max_net_cents": int(row.max_net_cents or 0),
            "stddev_net_cents": stddev_val,
            "top_orders": top_orders,
        })

    # ------------------------------------------------------------------
    # 3. channel_totals (group by channel)
    # ------------------------------------------------------------------
    channel_query = f"""
    WITH
      in_scope := (SELECT Sale {scope_filter}),
      groups := (
        GROUP in_scope
        USING channel := .channel
        BY channel
      ),
    SELECT groups {{
      key: {{ channel }},
      sale_count := count(.elements),
      net_cents := sum(.elements.net_cents),
    }}
    ORDER BY sum(.elements.net_cents) DESC THEN .key.channel
    """
    channel_rows = await client.query(channel_query)

    grand_net = grand["net_cents"]
    channel_totals = []
    for row in channel_rows:
        nc = int(row.net_cents or 0)
        share = round((nc / grand_net * 100), 2) if grand_net != 0 else 0.0
        channel_totals.append({
            "channel": row.key.channel,
            "sale_count": int(row.sale_count),
            "net_cents": nc,
            "share_pct": share,
        })

    # ------------------------------------------------------------------
    # 4. category_rank (group by category)
    # ------------------------------------------------------------------
    category_query = f"""
    WITH
      in_scope := (SELECT Sale {scope_filter}),
      groups := (
        GROUP in_scope
        USING category := .category
        BY category
      ),
    SELECT groups {{
      key: {{ category: {{ name, region }} }},
      sale_count := count(.elements),
      net_cents := sum(.elements.net_cents),
    }}
    """
    cat_rows = await client.query(category_query)

    # Build list with category details
    cat_data = []
    for row in cat_rows:
        cat_data.append({
            "category": row.key.category.name,
            "region": row.key.category.region,
            "sale_count": int(row.sale_count),
            "net_cents": int(row.net_cents or 0),
        })

    # Sort by net_cents desc for ranking
    cat_data.sort(key=lambda c: (-c["net_cents"], c["category"]))

    total_cats = len(cat_data)
    category_rank = []
    if total_cats > 0:
        # Compute rank (tied categories share rank, subsequent ranks skipped)
        ranks = []
        current_rank = 1
        for i, c in enumerate(cat_data):
            if i == 0:
                ranks.append(current_rank)
            elif c["net_cents"] == cat_data[i - 1]["net_cents"]:
                ranks.append(current_rank)
            else:
                current_rank = i + 1
                ranks.append(current_rank)

        for i, c in enumerate(cat_data):
            # percentile: 100 * (number of cats with net_cents <= this one) / total
            le_count = sum(1 for x in cat_data if x["net_cents"] <= c["net_cents"])
            percentile = round(100 * le_count / total_cats, 2)
            category_rank.append({
                "category": c["category"],
                "region": c["region"],
                "sale_count": c["sale_count"],
                "net_cents": c["net_cents"],
                "rank": ranks[i],
                "percentile": percentile,
            })

    # ------------------------------------------------------------------
    # 5. empty_categories
    # ------------------------------------------------------------------
    empty_query = f"""
    WITH
      in_scope := (SELECT Sale {scope_filter}),
      cats_with_sales := (SELECT DISTINCT in_scope.category),
    SELECT Category {{ name }} FILTER .name NOT IN (SELECT cats_with_sales.name)
    ORDER BY .name
    """
    empty_rows = await client.query(empty_query)
    empty_categories = [row.name for row in empty_rows]

    return {
        "window": {"month": month},
        "grand_total": grand,
        "monthly_by_channel": monthly_by_channel,
        "channel_totals": channel_totals,
        "category_rank": category_rank,
        "empty_categories": empty_categories,
    }


async def ingest_refunds(
    client: gel.AsyncIOClient, records: list[dict]
) -> dict:
    """Idempotent upsert of refund records. Returns summary dict."""
    # --- Validation (must happen before any writes) ---
    seen_ids: set[str] = set()
    for rec in records:
        ext_id = rec.get("external_id")
        if not isinstance(ext_id, str) or not ext_id:
            raise ValueError("invalid refunds file")
        if ext_id in seen_ids:
            raise ValueError("invalid refunds file")
        seen_ids.add(ext_id)

        amt = rec.get("amount_cents")
        if not isinstance(amt, int) or amt < 1:
            raise ValueError("invalid refunds file")

        if not isinstance(rec.get("order_ref"), str) or not rec["order_ref"]:
            raise ValueError("invalid refunds file")
        if not isinstance(rec.get("refunded_at"), str) or not rec["refunded_at"]:
            raise ValueError("invalid refunds file")

    # --- Fetch existing refunds and sales ---
    existing_refunds = {}
    if seen_ids:
        ids_json = json.dumps(list(seen_ids))
        ext_rows = await client.query(f"""
            SELECT Refund {{
                external_id,
                sale: {{ order_ref }},
                amount_cents,
                refunded_at,
            }}
            FILTER .external_id IN array_unpack(<array<str>>to_json('{ids_json}'))
        """)
        for r in ext_rows:
            dt = r.refunded_at
            utc_dt = dt.astimezone(datetime.timezone.utc)
            existing_refunds[r.external_id] = {
                "order_ref": r.sale.order_ref,
                "amount_cents": r.amount_cents,
                "refunded_at": utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

    # Fetch sale order_refs for matching
    order_refs_in_file = {rec["order_ref"] for rec in records}
    existing_sales = {}
    if order_refs_in_file:
        refs_json = json.dumps(list(order_refs_in_file))
        sale_rows = await client.query(f"""
            SELECT Sale {{
                order_ref,
            }}
            FILTER .order_ref IN array_unpack(<array<str>>to_json('{refs_json}'))
        """)
        for s in sale_rows:
            existing_sales[s.order_ref] = True

    # --- Classify records ---
    inserted = 0
    updated = 0
    unchanged = 0
    skipped = 0

    inserts_list: list[dict] = []
    updates_list: list[tuple[str, dict]] = []

    for rec in records:
        ext_id = rec["external_id"]
        order_ref = rec["order_ref"]

        if order_ref not in existing_sales:
            skipped += 1
            continue

        if ext_id not in existing_refunds:
            inserted += 1
            inserts_list.append(rec)
        else:
            existing = existing_refunds[ext_id]
            if (
                existing["order_ref"] == order_ref
                and existing["amount_cents"] == rec["amount_cents"]
                and existing["refunded_at"] == rec["refunded_at"]
            ):
                unchanged += 1
            else:
                updated += 1
                updates_list.append((ext_id, rec))

    # --- Perform inserts ---
    for rec in inserts_list:
        refunded_at_dt = datetime.datetime.fromisoformat(
            rec["refunded_at"].replace("Z", "+00:00")
        )
        await client.query("""
            INSERT Refund {
                external_id := <str>$ext_id,
                sale := (SELECT Sale FILTER .order_ref = <str>$order_ref LIMIT 1),
                amount_cents := <int64>$amount_cents,
                refunded_at := <datetime>$refunded_at,
            }
        """, **{
            "ext_id": rec["external_id"],
            "order_ref": rec["order_ref"],
            "amount_cents": rec["amount_cents"],
            "refunded_at": refunded_at_dt,
        })

    # --- Perform updates ---
    for ext_id, rec in updates_list:
        refunded_at_dt = datetime.datetime.fromisoformat(
            rec["refunded_at"].replace("Z", "+00:00")
        )
        await client.query("""
            UPDATE Refund
            FILTER .external_id = <str>$ext_id
            SET {
                sale := (SELECT Sale FILTER .order_ref = <str>$order_ref LIMIT 1),
                amount_cents := <int64>$amount_cents,
                refunded_at := <datetime>$refunded_at,
            }
        """, **{
            "ext_id": ext_id,
            "order_ref": rec["order_ref"],
            "amount_cents": rec["amount_cents"],
            "refunded_at": refunded_at_dt,
        })

    # --- Database-wide totals ---
    totals_row = await client.query_single("""
        SELECT (
            refund_total_count := count(Refund),
            refund_total_cents := sum(Refund.amount_cents),
        )
    """)

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "refund_total_count": int(totals_row.refund_total_count),
        "refund_total_cents": int(totals_row.refund_total_cents or 0),
    }
