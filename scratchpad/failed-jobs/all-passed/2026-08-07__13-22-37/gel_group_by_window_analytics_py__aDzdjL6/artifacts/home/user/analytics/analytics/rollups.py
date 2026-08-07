"""Async analytics rollups for the coffee-equipment retailer.

This module exposes two coroutine functions:

* :func:`build_report`  – produce the multi-key ``group`` analytics report.
* :func:`ingest_refunds` – idempotently upsert refund records.

All heavy aggregation is performed inside the database using EdgeQL's
top-level ``group`` statement; Python is only used for post-processing
(rounding, ranking and share/percentile derivation).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

__all__ = ["build_report", "ingest_refunds"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 datetime string and normalise it to UTC."""
    if not isinstance(value, str):
        raise ValueError("refunded_at must be a string")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - exercised via tests
        raise ValueError(f"invalid refunded_at: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _round2(value):
    """Round a numeric value to 2 decimal places, returning a float."""
    return round(float(value), 2)


# ---------------------------------------------------------------------------
# Report query (uses EdgeQL's top-level ``group`` statement)
# ---------------------------------------------------------------------------

_REPORT_QUERY = r"""
with
  m := <str>$month,
  scoped := (select Sale filter m = "" or to_str(.occurred_at, "YYYY-MM") = m),
  grand := (
    select {
      sale_count := count(scoped),
      unit_count := sum(scoped.units),
      gross_cents := sum(scoped.amount_cents),
      refund_cents := sum(scoped.amount_cents) - sum(scoped.net_cents),
      net_cents := sum(scoped.net_cents),
      month_count := count(distinct (to_str(scoped.occurred_at, "YYYY-MM"))),
      channel_count := count(distinct (scoped.channel)),
      category_count := count(distinct (scoped.category)),
    }
  ),
  monthly := (
    select (
      group scoped using mo := to_str(.occurred_at, "YYYY-MM") by .channel, mo
    ) {
      month := .key.mo,
      channel := .key.channel,
      sale_count := count(.elements),
      unit_count := sum(.elements.units),
      gross_cents := sum(.elements.amount_cents),
      refund_cents := sum(.elements.amount_cents) - sum(.elements.net_cents),
      net_cents := sum(.elements.net_cents),
      min_net_cents := min(.elements.net_cents),
      max_net_cents := max(.elements.net_cents),
      mean_net_cents := std::math::mean(.elements.net_cents),
      stddev_net_cents := (if count(.elements) >= 2 then std::math::stddev(.elements.net_cents) else <float64>{}),
      top_orders := (select .elements { order_ref, net_cents } order by .net_cents desc then .order_ref asc limit 3),
    }
    order by .month then .channel
  ),
  channels := (
    select (
      group scoped by .channel
    ) {
      channel := .key.channel,
      sale_count := count(.elements),
      net_cents := sum(.elements.net_cents),
    }
    order by .net_cents desc then .channel
  ),
  categories := (
    select (
      group scoped by .category
    ) {
      category := .key.category.name,
      region := .key.category.region,
      sale_count := count(.elements),
      net_cents := sum(.elements.net_cents),
    }
    order by .net_cents desc then .category
  ),
  empties := (
    (select Category filter Category not in (select scoped.category) order by .name).name
  )
select {
  `window` := { month := (if m = "" then <str>{} else m) },
  grand_total := grand {
    sale_count,
    unit_count,
    gross_cents,
    refund_cents,
    net_cents,
    month_count,
    channel_count,
    category_count,
  },
  monthly_by_channel := monthly {
    month,
    channel,
    sale_count,
    unit_count,
    gross_cents,
    refund_cents,
    net_cents,
    min_net_cents,
    max_net_cents,
    mean_net_cents,
    stddev_net_cents,
    top_orders: { order_ref, net_cents },
  },
  channel_totals := channels {
    channel,
    sale_count,
    net_cents,
  },
  category_rank := categories {
    category,
    region,
    sale_count,
    net_cents,
  },
  empty_categories := empties,
}
"""


async def build_report(client, month=None):
    """Build the analytics report as a plain JSON-serialisable structure.

    Parameters
    ----------
    client : gel.AsyncIOClient
    month : None | str
        ``None`` for all sales, or a ``"YYYY-MM"`` string restricting the
        report to a single calendar month (UTC).

    Returns
    -------
    dict
    """
    if month is not None:
        if not isinstance(month, str) or not _MONTH_RE.match(month):
            raise ValueError(f"invalid month: {month!r}")

    param = month if month is not None else ""
    raw = await client.query_required_single_json(_REPORT_QUERY, month=param)
    report = json.loads(raw)
    _postprocess(report)
    return report


def _postprocess(report: dict) -> None:
    """Round float fields and derive share/rank/percentile in place."""
    # --- monthly_by_channel: round mean & stddev ---------------------------
    for group in report["monthly_by_channel"]:
        group["mean_net_cents"] = _round2(group["mean_net_cents"])
        if group["stddev_net_cents"] is not None:
            group["stddev_net_cents"] = _round2(group["stddev_net_cents"])

    # --- channel_totals: share_pct -----------------------------------------
    grand_net = report["grand_total"]["net_cents"]
    for ch in report["channel_totals"]:
        if grand_net > 0:
            ch["share_pct"] = _round2(ch["net_cents"] / grand_net * 100)
        else:
            ch["share_pct"] = 0.0

    # --- category_rank: rank & percentile ----------------------------------
    cats = report["category_rank"]
    total = len(cats)
    for c in cats:
        c["rank"] = 1 + sum(
            1 for o in cats if o["net_cents"] > c["net_cents"]
        )
        if total > 0:
            le = sum(1 for o in cats if o["net_cents"] <= c["net_cents"])
            c["percentile"] = _round2(100 * le / total)
        else:
            c["percentile"] = 0.0
    cats.sort(key=lambda c: (c["rank"], c["category"]))


# ---------------------------------------------------------------------------
# Refund ingestion
# ---------------------------------------------------------------------------

_EXISTING_REFUNDS_QUERY = r"""
select Refund {
  external_id,
  sale: { order_ref },
  amount_cents,
  refunded_at,
} filter .external_id in array_unpack(<array<str>>$ext_ids)
"""

_SALES_QUERY = r"""
select Sale { order_ref } filter .order_ref in array_unpack(<array<str>>$order_refs)
"""

_INSERT_QUERY = r"""
for r in json_array_unpack(<json>$data) union (
  insert Refund {
    external_id := <str>json_get(r, "external_id"),
    sale := (select Sale filter .order_ref = <str>json_get(r, "order_ref") limit 1),
    amount_cents := <int64>json_get(r, "amount_cents"),
    refunded_at := <datetime>json_get(r, "refunded_at"),
  }
)
"""

_UPDATE_QUERY = r"""
for r in json_array_unpack(<json>$data) union (
  update Refund
  filter .external_id = <str>json_get(r, "external_id")
  set {
    sale := (select Sale filter .order_ref = <str>json_get(r, "order_ref") limit 1),
    amount_cents := <int64>json_get(r, "amount_cents"),
    refunded_at := <datetime>json_get(r, "refunded_at"),
  }
)
"""

_TOTALS_QUERY = r"""
select {
  refund_total_count := count(Refund),
  refund_total_cents := sum(Refund.amount_cents),
}
"""


def _validate_and_parse(records):
    """Validate *records* and return a list of normalised record dicts.

    Raises :class:`ValueError` on the first problem encountered, before any
    database interaction.
    """
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    seen: set[str] = set()
    parsed: list[dict] = []
    for r in records:
        if not isinstance(r, dict):
            raise ValueError("each record must be an object")
        for key in ("external_id", "order_ref", "amount_cents", "refunded_at"):
            if key not in r:
                raise ValueError(f"missing key: {key}")
        amount = r["amount_cents"]
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1:
            raise ValueError("amount_cents must be an integer >= 1")
        ext_id = r["external_id"]
        if not isinstance(ext_id, str):
            raise ValueError("external_id must be a string")
        if ext_id in seen:
            raise ValueError(f"duplicate external_id: {ext_id}")
        seen.add(ext_id)
        if not isinstance(r["order_ref"], str):
            raise ValueError("order_ref must be a string")
        rfd_dt = _parse_dt(r["refunded_at"])
        parsed.append(
            {
                "external_id": ext_id,
                "order_ref": r["order_ref"],
                "amount_cents": amount,
                "refunded_at": r["refunded_at"],
                "_dt": rfd_dt,
            }
        )
    return parsed


async def _do_ingest(tx, parsed_records):
    """Run the ingestion logic inside a transaction iteration."""
    ext_ids = [r["external_id"] for r in parsed_records]
    order_refs = list({r["order_ref"] for r in parsed_records})

    # Existing refunds keyed by external_id.
    existing: dict[str, dict] = {}
    if ext_ids:
        raw = await tx.query_json(_EXISTING_REFUNDS_QUERY, ext_ids=ext_ids)
        for row in json.loads(raw):
            existing[row["external_id"]] = {
                "order_ref": row["sale"]["order_ref"],
                "amount_cents": row["amount_cents"],
                "refunded_at": _parse_dt(row["refunded_at"]),
            }

    # Existing sales, as a set of order_refs.
    sale_order_refs: set[str] = set()
    if order_refs:
        raw = await tx.query_json(_SALES_QUERY, order_refs=order_refs)
        sale_order_refs = {row["order_ref"] for row in json.loads(raw)}

    to_insert: list[dict] = []
    to_update: list[dict] = []
    unchanged = 0
    skipped = 0

    for r in parsed_records:
        order_ref = r["order_ref"]
        ext_id = r["external_id"]

        if order_ref not in sale_order_refs:
            skipped += 1
            continue

        if ext_id not in existing:
            to_insert.append(
                {
                    "external_id": ext_id,
                    "order_ref": order_ref,
                    "amount_cents": r["amount_cents"],
                    "refunded_at": r["refunded_at"],
                }
            )
            continue

        ex = existing[ext_id]
        if (
            ex["order_ref"] == order_ref
            and ex["amount_cents"] == r["amount_cents"]
            and ex["refunded_at"] == r["_dt"]
        ):
            unchanged += 1
        else:
            to_update.append(
                {
                    "external_id": ext_id,
                    "order_ref": order_ref,
                    "amount_cents": r["amount_cents"],
                    "refunded_at": r["refunded_at"],
                }
            )

    if to_insert:
        await tx.query_json(_INSERT_QUERY, data=json.dumps(to_insert))
    if to_update:
        await tx.query_json(_UPDATE_QUERY, data=json.dumps(to_update))

    totals_raw = await tx.query_required_single_json(_TOTALS_QUERY)
    totals = json.loads(totals_raw)

    return {
        "inserted": len(to_insert),
        "updated": len(to_update),
        "unchanged": unchanged,
        "skipped": skipped,
        "refund_total_count": totals["refund_total_count"],
        "refund_total_cents": totals["refund_total_cents"],
    }


async def ingest_refunds(client, records):
    """Idempotently upsert refund *records* into the database.

    Parameters
    ----------
    client : gel.AsyncIOClient
    records : list[dict]
        Already-parsed refund dicts with keys ``external_id``, ``order_ref``,
        ``amount_cents`` and ``refunded_at``.

    Returns
    -------
    dict
    """
    parsed_records = _validate_and_parse(records)

    result = None
    async for tx in client.transaction():
        async with tx:
            result = await _do_ingest(tx, parsed_records)
    return result
