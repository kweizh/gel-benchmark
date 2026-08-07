import asyncio
import re
from datetime import datetime, timezone
import gel

def to_plain(obj):
    import decimal
    if obj is None:
        return None
    elif isinstance(obj, (int, float, str, bool)):
        return obj
    elif isinstance(obj, decimal.Decimal):
        return float(obj)
    elif isinstance(obj, list):
        return [to_plain(x) for x in obj]
    elif isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    elif hasattr(obj, '__dir__'):
        keys = obj.__dir__()
        res = {}
        for k in keys:
            if k.startswith('_'):
                continue
            val = getattr(obj, k)
            if callable(val):
                continue
            res[k] = to_plain(val)
        return res
    else:
        return obj

def parse_utc_datetime(dt_str):
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt

async def ingest_refunds(client: gel.AsyncIOClient, records: list) -> dict:
    # 1. Validation before anything is written
    if not isinstance(records, list):
        raise ValueError("records must be a list")

    external_ids = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        for key in ['external_id', 'order_ref', 'amount_cents', 'refunded_at']:
            if key not in record:
                raise ValueError(f"missing key: {key}")
        
        ext_id = record['external_id']
        if not isinstance(ext_id, str) or not ext_id:
            raise ValueError("external_id must be a non-empty string")
        
        if ext_id in external_ids:
            raise ValueError(f"duplicate external_id in file: {ext_id}")
        external_ids.add(ext_id)
        
        order_ref = record['order_ref']
        if not isinstance(order_ref, str) or not order_ref:
            raise ValueError("order_ref must be a non-empty string")
            
        amount_cents = record['amount_cents']
        if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents < 1:
            raise ValueError("amount_cents must be an integer >= 1")
            
        refunded_at = record['refunded_at']
        if not isinstance(refunded_at, str) or not refunded_at:
            raise ValueError("refunded_at must be a non-empty string")
        try:
            parse_utc_datetime(refunded_at)
        except Exception:
            raise ValueError("refunded_at must be a valid ISO datetime")

    # 2. Ingestion logic in a transaction
    inserted = 0
    updated = 0
    unchanged = 0
    skipped = 0

    async for tx in client.transaction():
        async with tx:
            # Query all existing Sales' order_ref
            sales = await tx.query("select Sale { id, order_ref }")
            sales_by_ref = {s.order_ref: s.id for s in sales}

            # Query all existing Refunds
            refunds = await tx.query("select Refund { id, external_id, amount_cents, refunded_at, sale: { order_ref } }")
            refunds_by_ext_id = {r.external_id: r for r in refunds}

            # Reset counts for the transaction retry (just in case of retries)
            inserted = 0
            updated = 0
            unchanged = 0
            skipped = 0

            for record in records:
                ext_id = record['external_id']
                order_ref = record['order_ref']
                amount_cents = record['amount_cents']
                ref_dt = parse_utc_datetime(record['refunded_at'])

                # If order_ref matches no existing Sale, skip
                if order_ref not in sales_by_ref:
                    skipped += 1
                    continue

                if ext_id not in refunds_by_ext_id:
                    # Insert
                    await tx.execute(
                        """
                        insert Refund {
                            external_id := <str>$external_id,
                            sale := (select Sale filter .order_ref = <str>$order_ref),
                            amount_cents := <int64>$amount_cents,
                            refunded_at := <datetime>$refunded_at
                        }
                        """,
                        external_id=ext_id,
                        order_ref=order_ref,
                        amount_cents=amount_cents,
                        refunded_at=ref_dt
                    )
                    inserted += 1
                else:
                    existing = refunds_by_ext_id[ext_id]
                    # Check if unchanged
                    if (existing.sale.order_ref == order_ref and
                        existing.amount_cents == amount_cents and
                        existing.refunded_at == ref_dt):
                        unchanged += 1
                    else:
                        # Update
                        await tx.execute(
                            """
                            update Refund
                            filter .external_id = <str>$external_id
                            set {
                                sale := (select Sale filter .order_ref = <str>$order_ref),
                                amount_cents := <int64>$amount_cents,
                                refunded_at := <datetime>$refunded_at
                            }
                            """,
                            external_id=ext_id,
                            order_ref=order_ref,
                            amount_cents=amount_cents,
                            refunded_at=ref_dt
                        )
                        updated += 1

            # Get final DB counts
            final_stats = await tx.query_single(
                """
                select {
                    total_count := count(Refund),
                    total_cents := sum(Refund.amount_cents)
                }
                """
            )
            refund_total_count = final_stats.total_count
            refund_total_cents = final_stats.total_cents if final_stats.total_cents is not None else 0

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": skipped,
        "refund_total_count": refund_total_count,
        "refund_total_cents": refund_total_cents
    }

async def build_report(client: gel.AsyncIOClient, month: str = None) -> dict:
    if month is not None:
        if not isinstance(month, str) or not re.match(r"^[0-9]{4}-(0[1-9]|1[0-2])$", month):
            raise ValueError("invalid month format")

    # Query 1: grand_total
    query_grand = """
    with
      in_scope := (
        select Sale
        filter (to_str(.occurred_at, 'YYYY-MM') = <optional str>$month) if exists <optional str>$month else true
      )
    select {
      sale_count := count(in_scope),
      unit_count := sum(in_scope.units) ?? 0,
      gross_cents := sum(in_scope.amount_cents) ?? 0,
      net_cents := sum(in_scope.net_cents) ?? 0,
      month_count := count(distinct to_str(in_scope.occurred_at, 'YYYY-MM')),
      channel_count := count(distinct in_scope.channel),
      category_count := count(distinct in_scope.category.name)
    }
    """

    # Query 2: monthly_by_channel (uses group by month, channel)
    query_monthly = """
    select (
      group (
        select Sale {
          month := to_str(.occurred_at, 'YYYY-MM'),
          channel,
          units,
          amount_cents,
          net_cents,
          refund_cents := .amount_cents - .net_cents,
          order_ref
        }
        filter (to_str(.occurred_at, 'YYYY-MM') = <optional str>$month) if exists <optional str>$month else true
      ) by .month, .channel
    ) {
      month := .key.month,
      channel := .key.channel,
      sale_count := count(.elements),
      unit_count := sum(.elements.units),
      gross_cents := sum(.elements.amount_cents),
      refund_cents := sum(.elements.refund_cents),
      net_cents := sum(.elements.net_cents),
      mean_net_cents := <float64>round(<decimal>math::mean(.elements.net_cents), 2),
      min_net_cents := min(.elements.net_cents),
      max_net_cents := max(.elements.net_cents),
      stddev_net_cents := (
        with elements_net_cents := .elements.net_cents
        select <float64>round(<decimal>math::stddev(elements_net_cents), 2)
        if count(elements_net_cents) >= 2
        else <float64>{}
      ),
      top_orders := (
        select .elements {
          order_ref,
          net_cents
        }
        order by .net_cents desc then .order_ref asc
        limit 3
      )
    }
    order by .month asc then .channel asc
    """

    # Query 3: channel_totals
    query_channel = """
    with
      in_scope := (
        select Sale
        filter (to_str(.occurred_at, 'YYYY-MM') = <optional str>$month) if exists <optional str>$month else true
      ),
      grand_net := sum(in_scope.net_cents) ?? 0,
      grouped := (
        group in_scope by .channel
      )
    select grouped {
      channel := .key.channel,
      sale_count := count(.elements),
      net_cents := sum(.elements.net_cents),
      share_pct := <float64>round(<decimal>(sum(.elements.net_cents) / grand_net * 100), 2) if grand_net > 0 else 0.0
    }
    order by .net_cents desc then .channel asc
    """

    # Query 4: category_rank
    query_category = """
    with
      in_scope := (
        select Sale
        filter (to_str(.occurred_at, 'YYYY-MM') = <optional str>$month) if exists <optional str>$month else true
      ),
      grouped_categories := (
        select (group in_scope by .category) {
          category := .key.category.name,
          region := .key.category.region,
          sale_count := count(.elements),
          net_cents := sum(.elements.net_cents)
        }
      ),
      grouped_categories_2 := (
        select (group in_scope by .category) {
          category := .key.category.name,
          region := .key.category.region,
          sale_count := count(.elements),
          net_cents := sum(.elements.net_cents)
        }
      ),
      total_categories := count(grouped_categories)
    select (
      for cat in grouped_categories union (
        select {
          category := cat.category,
          region := cat.region,
          sale_count := cat.sale_count,
          net_cents := cat.net_cents,
          rank := 1 + count(
            with val := cat.net_cents
            select grouped_categories_2
            filter .net_cents > (select val limit 1)
          ),
          percentile := <float64>round(<decimal>(
            100 * count(
              with val := cat.net_cents
              select grouped_categories_2
              filter .net_cents <= (select val limit 1)
            ) / total_categories
          ), 2) if total_categories > 0 else 0.0
        }
      )
    )
    order by .rank asc then .category asc
    """

    # Query 5: empty_categories
    query_empty = """
    with
      in_scope := (
        select Sale
        filter (to_str(.occurred_at, 'YYYY-MM') = <optional str>$month) if exists <optional str>$month else true
      )
    select Category.name
    filter not (Category.name in in_scope.category.name)
    order by Category.name asc
    """

    # Execute all queries
    res_grand, res_monthly, res_channel, res_category, res_empty = await asyncio.gather(
        client.query_required_single(query_grand, month=month),
        client.query(query_monthly, month=month),
        client.query(query_channel, month=month),
        client.query(query_category, month=month),
        client.query(query_empty, month=month)
    )

    # Format grand_total
    grand_total = {
        "sale_count": int(res_grand.sale_count),
        "unit_count": int(res_grand.unit_count),
        "gross_cents": int(res_grand.gross_cents),
        "refund_cents": int(res_grand.gross_cents - res_grand.net_cents),
        "net_cents": int(res_grand.net_cents),
        "month_count": int(res_grand.month_count),
        "channel_count": int(res_grand.channel_count),
        "category_count": int(res_grand.category_count)
    }

    # Format monthly_by_channel
    monthly_by_channel = []
    for row in res_monthly:
        top_orders = []
        for order in row.top_orders:
            top_orders.append({
                "order_ref": str(order.order_ref),
                "net_cents": int(order.net_cents)
            })
        monthly_by_channel.append({
            "month": str(row.month),
            "channel": str(row.channel),
            "sale_count": int(row.sale_count),
            "unit_count": int(row.unit_count),
            "gross_cents": int(row.gross_cents),
            "refund_cents": int(row.refund_cents),
            "net_cents": int(row.net_cents),
            "mean_net_cents": float(row.mean_net_cents) if row.mean_net_cents is not None else None,
            "min_net_cents": int(row.min_net_cents) if row.min_net_cents is not None else None,
            "max_net_cents": int(row.max_net_cents) if row.max_net_cents is not None else None,
            "stddev_net_cents": float(row.stddev_net_cents) if row.stddev_net_cents is not None else None,
            "top_orders": top_orders
        })

    # Format channel_totals
    channel_totals = []
    for row in res_channel:
        channel_totals.append({
            "channel": str(row.channel),
            "sale_count": int(row.sale_count),
            "net_cents": int(row.net_cents),
            "share_pct": float(row.share_pct) if row.share_pct is not None else 0.0
        })

    # Format category_rank
    category_rank = []
    for row in res_category:
        category_rank.append({
            "category": str(row.category),
            "region": str(row.region),
            "sale_count": int(row.sale_count),
            "net_cents": int(row.net_cents),
            "rank": int(row.rank),
            "percentile": float(row.percentile) if row.percentile is not None else 0.0
        })

    # Format empty_categories
    empty_categories = [str(name) for name in res_empty]

    return {
        "window": {"month": month},
        "grand_total": grand_total,
        "monthly_by_channel": monthly_by_channel,
        "channel_totals": channel_totals,
        "category_rank": category_rank,
        "empty_categories": empty_categories
    }
