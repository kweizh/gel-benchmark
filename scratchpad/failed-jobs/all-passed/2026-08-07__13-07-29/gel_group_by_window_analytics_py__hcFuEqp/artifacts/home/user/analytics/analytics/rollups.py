"""Analytics rollups: refund ingestion and the channel/category report.

This module talks to a Gel 7 database through an ``gel.AsyncIOClient``.
The report is produced with EdgeQL's top-level ``group ... by ...``
statement so that all the grouping/aggregation work happens inside the
database rather than in Python.
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime, timezone
from typing import Any, Optional

MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")

_REFUND_KEYS = {"external_id", "order_ref", "amount_cents", "refunded_at"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> datetime:
    """Parse an ISO-8601 datetime string (optionally ending in ``Z``)."""
    if not isinstance(value, str):
        raise ValueError(f"refunded_at must be a string, got {value!r}")
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid refunded_at value: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _validate_records(records: Any) -> None:
    if not isinstance(records, list):
        raise ValueError("refunds file must contain a JSON array of records")

    seen_ext_ids: set[str] = set()
    for i, rec in enumerate(records):
        if not isinstance(rec, dict) or set(rec.keys()) != _REFUND_KEYS:
            raise ValueError(f"record {i} does not have the expected keys")

        ext_id = rec["external_id"]
        order_ref = rec["order_ref"]
        amount_cents = rec["amount_cents"]
        refunded_at = rec["refunded_at"]

        if not isinstance(ext_id, str) or not ext_id:
            raise ValueError(f"record {i}: external_id must be a non-empty string")
        if not isinstance(order_ref, str) or not order_ref:
            raise ValueError(f"record {i}: order_ref must be a non-empty string")
        if (
            not isinstance(amount_cents, int)
            or isinstance(amount_cents, bool)
            or amount_cents < 1
        ):
            raise ValueError(f"record {i}: amount_cents must be an integer >= 1")

        # Raises ValueError on malformed datetimes.
        _parse_dt(refunded_at)

        if ext_id in seen_ext_ids:
            raise ValueError(f"duplicate external_id in file: {ext_id!r}")
        seen_ext_ids.add(ext_id)


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    year, mon = (int(part) for part in month.split("-"))
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    if mon == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, mon + 1, 1, tzinfo=timezone.utc)
    return start, end


_MONTHLY_QUERY_TEMPLATE = """
select (
  group {scope}
  using month := to_str(.occurred_at, 'YYYY-MM'), channel := .channel
  by month, channel
) {{
  month := .key.month,
  channel := .key.channel,
  rows := (
    select .elements {{ order_ref, net_cents, amount_cents, units }}
  ),
}}
"""

_CATEGORY_QUERY_TEMPLATE = """
select (
  group {scope}
  using cat := .category.name, region := .category.region
  by cat, region
) {{
  category := .key.cat,
  region := .key.region,
  sale_count := count(.elements),
  net_cents := sum(.elements.net_cents),
}}
"""

_SCOPE_ALL = "Sale"
_SCOPE_MONTH = (
    "(select Sale filter .occurred_at >= <datetime>$start and "
    ".occurred_at < <datetime>$end)"
)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


async def build_report(client, month: Optional[str] = None) -> dict:
    if month is not None:
        if not MONTH_RE.match(month):
            raise ValueError(f"invalid month: {month!r}")
        start, end = _month_bounds(month)
        args: dict[str, Any] = {"start": start, "end": end}
        scope = _SCOPE_MONTH
    else:
        args = {}
        scope = _SCOPE_ALL

    monthly_query = _MONTHLY_QUERY_TEMPLATE.format(scope=scope)
    category_query = _CATEGORY_QUERY_TEMPLATE.format(scope=scope)

    monthly_raw = await client.query(monthly_query, **args)
    category_raw = await client.query(category_query, **args)
    all_category_names = await client.query("select Category.name")

    # -- monthly_by_channel -------------------------------------------------
    monthly_by_channel = []
    for grp in monthly_raw:
        rows = list(grp.rows)
        net_list = [r.net_cents for r in rows]
        sale_count = len(rows)
        unit_count = sum(r.units for r in rows)
        gross_cents = sum(r.amount_cents for r in rows)
        net_cents = sum(net_list)
        refund_cents = gross_cents - net_cents
        min_net_cents = min(net_list)
        max_net_cents = max(net_list)
        mean_net_cents = round(statistics.fmean(net_list), 2)
        stddev_net_cents = (
            round(statistics.stdev(net_list), 2) if sale_count >= 2 else None
        )
        top_sorted = sorted(rows, key=lambda r: (-r.net_cents, r.order_ref))[:3]
        top_orders = [
            {"order_ref": r.order_ref, "net_cents": r.net_cents} for r in top_sorted
        ]

        monthly_by_channel.append(
            {
                "month": grp.month,
                "channel": grp.channel,
                "sale_count": sale_count,
                "unit_count": unit_count,
                "gross_cents": gross_cents,
                "refund_cents": refund_cents,
                "net_cents": net_cents,
                "mean_net_cents": mean_net_cents,
                "min_net_cents": min_net_cents,
                "max_net_cents": max_net_cents,
                "stddev_net_cents": stddev_net_cents,
                "top_orders": top_orders,
            }
        )

    monthly_by_channel.sort(key=lambda o: (o["month"], o["channel"]))

    # -- grand_total ---------------------------------------------------------
    sale_count_total = sum(m["sale_count"] for m in monthly_by_channel)
    unit_count_total = sum(m["unit_count"] for m in monthly_by_channel)
    gross_cents_total = sum(m["gross_cents"] for m in monthly_by_channel)
    net_cents_total = sum(m["net_cents"] for m in monthly_by_channel)
    refund_cents_total = gross_cents_total - net_cents_total
    month_count = len({m["month"] for m in monthly_by_channel})
    channel_count = len({m["channel"] for m in monthly_by_channel})
    category_count = len(category_raw)

    grand_total = {
        "sale_count": sale_count_total,
        "unit_count": unit_count_total,
        "gross_cents": gross_cents_total,
        "refund_cents": refund_cents_total,
        "net_cents": net_cents_total,
        "month_count": month_count,
        "channel_count": channel_count,
        "category_count": category_count,
    }

    # -- channel_totals --------------------------------------------------
    channel_agg: dict[str, dict[str, int]] = {}
    for m in monthly_by_channel:
        agg = channel_agg.setdefault(m["channel"], {"sale_count": 0, "net_cents": 0})
        agg["sale_count"] += m["sale_count"]
        agg["net_cents"] += m["net_cents"]

    channel_totals = []
    for channel, agg in channel_agg.items():
        if net_cents_total:
            share_pct = round(100 * agg["net_cents"] / net_cents_total, 2)
        else:
            share_pct = 0.0
        channel_totals.append(
            {
                "channel": channel,
                "sale_count": agg["sale_count"],
                "net_cents": agg["net_cents"],
                "share_pct": share_pct,
            }
        )
    channel_totals.sort(key=lambda o: (-o["net_cents"], o["channel"]))

    # -- category_rank -----------------------------------------------------
    cat_list = [
        {
            "category": g.category,
            "region": g.region,
            "sale_count": g.sale_count,
            "net_cents": g.net_cents,
        }
        for g in category_raw
    ]
    n_cats = len(cat_list)
    for c in cat_list:
        higher = sum(1 for o in cat_list if o["net_cents"] > c["net_cents"])
        c["rank"] = higher + 1
        le_count = sum(1 for o in cat_list if o["net_cents"] <= c["net_cents"])
        c["percentile"] = round(100 * le_count / n_cats, 2) if n_cats else 0.0
    cat_list.sort(key=lambda c: (c["rank"], c["category"]))

    # -- empty_categories ----------------------------------------------------
    present = {c["category"] for c in cat_list}
    empty_categories = sorted(
        name for name in all_category_names if name not in present
    )

    return {
        "window": {"month": month},
        "grand_total": grand_total,
        "monthly_by_channel": monthly_by_channel,
        "channel_totals": channel_totals,
        "category_rank": cat_list,
        "empty_categories": empty_categories,
    }


# ---------------------------------------------------------------------------
# ingestion
# ---------------------------------------------------------------------------


async def ingest_refunds(client, records) -> dict:
    _validate_records(records)

    order_refs = sorted({rec["order_ref"] for rec in records})
    ext_ids = sorted({rec["external_id"] for rec in records})

    inserted = updated = unchanged = skipped = 0
    refund_total_count = 0
    refund_total_cents = 0

    async for tx in client.transaction():
        async with tx:
            sale_rows = await tx.query(
                "select Sale { id, order_ref } "
                "filter .order_ref in array_unpack(<array<str>>$order_refs)",
                order_refs=order_refs,
            )
            sale_by_order_ref = {s.order_ref: s.id for s in sale_rows}

            refund_rows = await tx.query(
                """
                select Refund {
                  external_id,
                  amount_cents,
                  refunded_at,
                  sale: { order_ref },
                }
                filter .external_id in array_unpack(<array<str>>$ext_ids)
                """,
                ext_ids=ext_ids,
            )
            existing = {r.external_id: r for r in refund_rows}

            inserted = updated = unchanged = skipped = 0
            to_insert = []
            to_update = []

            for rec in records:
                sale_id = sale_by_order_ref.get(rec["order_ref"])
                if sale_id is None:
                    skipped += 1
                    continue

                rec_dt = _parse_dt(rec["refunded_at"])
                ex = existing.get(rec["external_id"])
                if ex is None:
                    inserted += 1
                    to_insert.append(
                        (rec["external_id"], sale_id, rec["amount_cents"], rec_dt)
                    )
                    continue

                same = (
                    ex.sale.order_ref == rec["order_ref"]
                    and ex.amount_cents == rec["amount_cents"]
                    and ex.refunded_at == rec_dt
                )
                if same:
                    unchanged += 1
                else:
                    updated += 1
                    to_update.append(
                        (rec["external_id"], sale_id, rec["amount_cents"], rec_dt)
                    )

            for ext_id, sale_id, amount_cents, refunded_at in to_insert:
                await tx.query(
                    """
                    insert Refund {
                      external_id := <str>$ext_id,
                      sale := (select Sale filter .id = <uuid>$sale_id),
                      amount_cents := <int64>$amount_cents,
                      refunded_at := <datetime>$refunded_at,
                    }
                    """,
                    ext_id=ext_id,
                    sale_id=sale_id,
                    amount_cents=amount_cents,
                    refunded_at=refunded_at,
                )

            for ext_id, sale_id, amount_cents, refunded_at in to_update:
                await tx.query(
                    """
                    update Refund
                    filter .external_id = <str>$ext_id
                    set {
                      sale := (select Sale filter .id = <uuid>$sale_id),
                      amount_cents := <int64>$amount_cents,
                      refunded_at := <datetime>$refunded_at,
                    }
                    """,
                    ext_id=ext_id,
                    sale_id=sale_id,
                    amount_cents=amount_cents,
                    refunded_at=refunded_at,
                )

            totals = await tx.query_single(
                "select { c := count(Refund), s := sum(Refund.amount_cents) }"
            )
            refund_total_count = totals.c
            refund_total_cents = totals.s

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "refund_total_count": refund_total_count,
        "refund_total_cents": refund_total_cents,
    }
