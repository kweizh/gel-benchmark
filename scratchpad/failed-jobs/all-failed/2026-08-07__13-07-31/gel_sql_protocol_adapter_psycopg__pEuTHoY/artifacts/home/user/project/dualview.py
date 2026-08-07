import sys
import os
import json
import argparse
import decimal
import psycopg
import gel

def clean_val(val):
    if val is None:
        return None
    if isinstance(val, (decimal.Decimal, float)):
        return int(val)
    return val

def handle_load(args):
    try:
        client = gel.create_client()
    except Exception as e:
        print(json.dumps({
            "error": "connection",
            "protocol": "edgeql",
            "message": str(e)
        }), file=sys.stderr)
        sys.exit(2)

    try:
        with open(args.input, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(json.dumps({
            "error": "input",
            "message": f"Failed to read input file: {e}"
        }), file=sys.stderr)
        sys.exit(1)

    # 1. Load artists
    for art in data.get("artists", []):
        client.query('''
            insert catalog::Artist {
                handle := <str>$handle,
                name := <str>$name,
                country := <str>$country,
                aliases := array_unpack(<array<str>>$aliases)
            }
            unless conflict on .handle
            else (
                update catalog::Artist
                set {
                    name := <str>$name,
                    country := <str>$country,
                    aliases := array_unpack(<array<str>>$aliases)
                }
            )
        ''', handle=art["handle"], name=art["name"], country=art["country"], aliases=art.get("aliases", []))

    # 2. Load albums
    for alb in data.get("albums", []):
        album = client.query_single("select catalog::Album { id } filter .slug = <str>$slug", slug=alb["slug"])
        if album:
            client.query('''
                update catalog::Album filter .slug = <str>$slug set {
                    title := <str>$title,
                    year := <int32>$year,
                    label := <optional str>$label
                }
            ''', slug=alb["slug"], title=alb["title"], year=alb["year"], label=alb.get("label"))
        else:
            client.query('''
                insert catalog::Album {
                    slug := <str>$slug,
                    title := <str>$title,
                    year := <int32>$year,
                    label := <optional str>$label
                }
            ''', slug=alb["slug"], title=alb["title"], year=alb["year"], label=alb.get("label"))

    # 3. Load tracks
    for trk in data.get("tracks", []):
        track = client.query_single("select catalog::Track { id } filter .slug = <str>$slug", slug=trk["slug"])
        tags = trk.get("tags", [])
        contributors_json = [
            json.dumps({"artist": c["artist"], "role": c["role"], "share_bp": c["share_bp"]})
            for c in trk.get("contributors", [])
        ]
        royalty_rate = decimal.Decimal(trk["royalty_rate"]) if "royalty_rate" in trk else decimal.Decimal("0.0")
        
        if track:
            client.query('''
                update catalog::Track filter .slug = <str>$slug set {
                    title := <str>$title,
                    duration_ms := <int64>$duration_ms,
                    royalty_rate := <decimal>$royalty_rate,
                    album := (select catalog::Album filter .slug = <str>$album_slug),
                    tags := array_unpack(<array<str>>$tags),
                    contributors := (
                        distinct (
                            for item_str in array_unpack(<array<str>>$contributors_json) union (
                                with item := to_json(item_str)
                                select catalog::Artist {
                                    @role := <str>item['role'],
                                    @share_bp := <int64>item['share_bp']
                                } filter .handle = <str>item['artist']
                            )
                        )
                    )
                }
            ''', slug=trk["slug"], title=trk["title"], duration_ms=trk["duration_ms"], royalty_rate=royalty_rate, album_slug=trk["album"], tags=tags, contributors_json=contributors_json)
        else:
            client.query('''
                insert catalog::Track {
                    slug := <str>$slug,
                    title := <str>$title,
                    duration_ms := <int64>$duration_ms,
                    royalty_rate := <decimal>$royalty_rate,
                    album := (select catalog::Album filter .slug = <str>$album_slug),
                    tags := array_unpack(<array<str>>$tags),
                    contributors := (
                        distinct (
                            for item_str in array_unpack(<array<str>>$contributors_json) union (
                                with item := to_json(item_str)
                                select catalog::Artist {
                                    @role := <str>item['role'],
                                    @share_bp := <int64>item['share_bp']
                                } filter .handle = <str>item['artist']
                            )
                        )
                    )
                }
            ''', slug=trk["slug"], title=trk["title"], duration_ms=trk["duration_ms"], royalty_rate=royalty_rate, album_slug=trk["album"], tags=tags, contributors_json=contributors_json)

    # 4. Count final stats
    artists_count = client.query_single("select count(catalog::Artist)")
    albums_count = client.query_single("select count(catalog::Album)")
    tracks_count = client.query_single("select count(catalog::Track)")
    contributions_count = client.query_single("select count(catalog::Track.contributors@role)")

    print(json.dumps({
        "artists": artists_count,
        "albums": albums_count,
        "tracks": tracks_count,
        "contributions": contributions_count
    }))
    sys.exit(0)

def handle_reconcile(args):
    # 1. Connect to EdgeQL
    edgeql_ok = False
    edgeql_err_msg = ""
    client = None
    try:
        client = gel.create_client()
        client.query("select 1")
        edgeql_ok = True
    except Exception as e:
        edgeql_err_msg = str(e)

    # 2. Connect to SQL
    sql_ok = False
    sql_err_msg = ""
    conn = None
    try:
        conn = psycopg.connect(
            host=os.environ.get("GEL_SQL_HOST"),
            port=os.environ.get("GEL_SQL_PORT"),
            user=os.environ.get("GEL_SQL_USER"),
            password=os.environ.get("GEL_SQL_PASSWORD"),
            dbname=os.environ.get("GEL_SQL_DBNAME")
        )
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        sql_ok = True
    except Exception as e:
        sql_err_msg = str(e)

    # Error handling
    if not edgeql_ok:
        print(json.dumps({
            "error": "connection",
            "protocol": "edgeql",
            "message": edgeql_err_msg or "Failed to connect to EdgeQL"
        }), file=sys.stderr)
        sys.exit(2)

    if not sql_ok:
        print(json.dumps({
            "error": "connection",
            "protocol": "sql",
            "message": sql_err_msg or "Failed to connect to SQL"
        }), file=sys.stderr)
        sys.exit(2)

    # 3. Compute Metrics
    edgeql_metrics_queries = {
        'count.artists_without_contributions': 'select count(catalog::Artist filter not .id in catalog::Track.contributors.id)',
        'count.catalog::Album': 'select count(catalog::Album)',
        'count.catalog::Artist': 'select count(catalog::Artist)',
        'count.catalog::Artist.aliases': 'select count(catalog::Artist.aliases)',
        'count.catalog::Asset': 'select count(catalog::Asset)',
        'count.catalog::Track': 'select count(catalog::Track)',
        'count.catalog::Track.contributors': 'select count(catalog::Track.contributors@role)',
        'count.catalog::Track.tags': 'select count(catalog::Track.tags)',
        'count.distinct.catalog::Track.album': 'select count(distinct catalog::Track.album)',
        'count.distinct.catalog::Track.contributors@target': 'select count(distinct catalog::Track.contributors)',
        'count.tracks_without_tags': 'select count(catalog::Track filter not exists .tags)',
        'max.catalog::Album.year': 'select max(catalog::Album.year)',
        'min.catalog::Album.year': 'select min(catalog::Album.year)',
        'sum.catalog::Track.contributors@share_bp': 'select sum(catalog::Track.contributors@share_bp)',
        'sum.catalog::Track.duration_ms': 'select sum(catalog::Track.duration_ms)',
        'sum.catalog::Track.payout_micros': 'select sum(catalog::Track.payout_micros)',
        'sum.catalog::Track.title_length': 'select sum(len(catalog::Track.title))'
    }

    sql_metrics_queries = {
        'count.artists_without_contributions': 'SELECT COUNT(*)::bigint FROM catalog."Artist" a WHERE NOT EXISTS (SELECT 1 FROM catalog."Track.contributors" c WHERE c.target = a.id)',
        'count.catalog::Album': 'SELECT COUNT(*)::bigint FROM catalog."Album"',
        'count.catalog::Artist': 'SELECT COUNT(*)::bigint FROM catalog."Artist"',
        'count.catalog::Artist.aliases': 'SELECT COUNT(*)::bigint FROM catalog."Artist.aliases"',
        'count.catalog::Asset': 'SELECT COUNT(*)::bigint FROM catalog."Asset"',
        'count.catalog::Track': 'SELECT COUNT(*)::bigint FROM catalog."Track"',
        'count.catalog::Track.contributors': 'SELECT COUNT(*)::bigint FROM catalog."Track.contributors"',
        'count.catalog::Track.tags': 'SELECT COUNT(*)::bigint FROM catalog."Track.tags"',
        'count.distinct.catalog::Track.album': 'SELECT COUNT(DISTINCT album_id)::bigint FROM catalog."Track"',
        'count.distinct.catalog::Track.contributors@target': 'SELECT COUNT(DISTINCT target)::bigint FROM catalog."Track.contributors"',
        'count.tracks_without_tags': 'SELECT COUNT(*)::bigint FROM catalog."Track" t WHERE NOT EXISTS (SELECT 1 FROM catalog."Track.tags" tg WHERE tg.source = t.id)',
        'max.catalog::Album.year': 'SELECT MAX(year)::integer FROM catalog."Album"',
        'min.catalog::Album.year': 'SELECT MIN(year)::integer FROM catalog."Album"',
        'sum.catalog::Track.contributors@share_bp': 'SELECT SUM(share_bp)::bigint FROM catalog."Track.contributors"',
        'sum.catalog::Track.duration_ms': 'SELECT SUM(duration_ms)::bigint FROM catalog."Track"',
        'sum.catalog::Track.payout_micros': 'SELECT SUM(payout_micros::bigint)::bigint FROM catalog."Track"',
        'sum.catalog::Track.title_length': 'SELECT SUM(char_length(title))::bigint FROM catalog."Track"'
    }

    metrics_eq = {}
    for m_id, q in edgeql_metrics_queries.items():
        res = client.query_single(q)
        metrics_eq[m_id] = clean_val(res)

    metrics_sql = {}
    with conn.cursor() as cur:
        for m_id, q in sql_metrics_queries.items():
            cur.execute(q)
            res = cur.fetchone()[0]
            metrics_sql[m_id] = clean_val(res)

    # Form metrics list
    metrics_list = []
    for m_id in sorted(edgeql_metrics_queries.keys()):
        eq_v = metrics_eq[m_id]
        sql_v = metrics_sql[m_id]
        metrics_list.append({
            "id": m_id,
            "edgeql": eq_v,
            "sql": sql_v,
            "agrees": eq_v == sql_v
        })

    # 4. Fetch rows
    # EdgeQL rows
    eq_rows_res = client.query('''
        select catalog::Track {
            slug,
            title,
            duration_ms,
            payout_micros,
            album_slug := .album.slug,
            album_year := .album.year,
            contributor_count := count(.contributors),
            share_bp_total := sum(.contributors@share_bp),
            tag_count := count(.tags)
        }
    ''')
    eq_rows = {}
    for r in eq_rows_res:
        eq_rows[r.slug] = {
            "album_slug": r.album_slug,
            "album_year": clean_val(r.album_year),
            "contributor_count": clean_val(r.contributor_count),
            "duration_ms": clean_val(r.duration_ms),
            "payout_micros": clean_val(r.payout_micros),
            "share_bp_total": clean_val(r.share_bp_total),
            "tag_count": clean_val(r.tag_count),
            "title_length": len(r.title) if r.title is not None else 0
        }

    # SQL rows
    sql_rows = {}
    with conn.cursor() as cur:
        cur.execute('''
            SELECT 
                t.slug,
                a.slug AS album_slug,
                a.year AS album_year,
                COALESCE(tc.contributor_count, 0)::bigint AS contributor_count,
                t.duration_ms,
                t.payout_micros::bigint,
                COALESCE(tc.share_bp_total, 0)::bigint AS share_bp_total,
                COALESCE(tg.tag_count, 0)::bigint AS tag_count,
                char_length(t.title)::bigint AS title_length
            FROM catalog."Track" t
            LEFT JOIN catalog."Album" a ON t.album_id = a.id
            LEFT JOIN (
                SELECT source, COUNT(*)::bigint AS contributor_count, SUM(share_bp)::bigint AS share_bp_total
                FROM catalog."Track.contributors"
                GROUP BY source
            ) tc ON t.id = tc.source
            LEFT JOIN (
                SELECT source, COUNT(*)::bigint AS tag_count
                FROM catalog."Track.tags"
                GROUP BY source
            ) tg ON t.id = tg.source
        ''')
        for r in cur.fetchall():
            slug = r[0]
            sql_rows[slug] = {
                "album_slug": r[1],
                "album_year": clean_val(r[2]),
                "contributor_count": clean_val(r[3]),
                "duration_ms": clean_val(r[4]),
                "payout_micros": clean_val(r[5]),
                "share_bp_total": clean_val(r[6]),
                "tag_count": clean_val(r[7]),
                "title_length": clean_val(r[8])
            }

    # Form rows list
    all_slugs = sorted(list(set(eq_rows.keys()) | set(sql_rows.keys())))
    rows_list = []
    for slug in all_slugs:
        eq_val = eq_rows.get(slug, None)
        sql_val = sql_rows.get(slug, None)
        rows_list.append({
            "slug": slug,
            "edgeql": eq_val,
            "sql": sql_val,
            "agrees": eq_val == sql_val
        })

    # 5. Compute Mismatches
    mismatches = []
    # Metric mismatches
    for m_id in sorted(edgeql_metrics_queries.keys()):
        eq_v = metrics_eq[m_id]
        sql_v = metrics_sql[m_id]
        if eq_v != sql_v:
            mismatches.append({
                "scope": "metric",
                "id": m_id,
                "field": "value",
                "edgeql": eq_v,
                "sql": sql_v
            })

    # Row mismatches
    row_fields = [
        "album_slug",
        "album_year",
        "contributor_count",
        "duration_ms",
        "payout_micros",
        "share_bp_total",
        "tag_count",
        "title_length"
    ]
    for slug in all_slugs:
        eq_val = eq_rows.get(slug, None)
        sql_val = sql_rows.get(slug, None)
        if eq_val != sql_val:
            for f in row_fields:
                v_eq = eq_val[f] if eq_val is not None else None
                v_sql = sql_val[f] if sql_val is not None else None
                if v_eq != v_sql:
                    mismatches.append({
                        "scope": "row",
                        "id": slug,
                        "field": f,
                        "edgeql": v_eq,
                        "sql": v_sql
                    })

    mismatches.sort(key=lambda x: (x["scope"], x["id"], x["field"]))
    mismatch_count = len(mismatches)
    agrees = (mismatch_count == 0)

    # 6. Compute Drift
    drift = []
    drift_count = 0
    if args.baseline:
        try:
            with open(args.baseline, "r") as f:
                baseline_report = json.load(f)
        except Exception as e:
            print(json.dumps({
                "error": "baseline",
                "message": f"Failed to read baseline file: {e}"
            }), file=sys.stderr)
            sys.exit(1)

        # Metric drift
        baseline_metrics = {m["id"]: m["edgeql"] for m in baseline_report.get("metrics", [])}
        for m_id in sorted(edgeql_metrics_queries.keys()):
            b_val = baseline_metrics.get(m_id, None)
            c_val = metrics_eq[m_id]
            if b_val != c_val:
                drift.append({
                    "scope": "metric",
                    "id": m_id,
                    "field": "value",
                    "baseline": b_val,
                    "current": c_val
                })

        # Row drift
        baseline_rows = {r["slug"]: r["edgeql"] for r in baseline_report.get("rows", [])}
        all_drift_slugs = sorted(list(set(baseline_rows.keys()) | set(eq_rows.keys())))
        for slug in all_drift_slugs:
            b_row = baseline_rows.get(slug, None)
            c_row = eq_rows.get(slug, None)
            if b_row != c_row:
                for f in row_fields:
                    v_b = b_row[f] if b_row is not None else None
                    v_c = c_row[f] if c_row is not None else None
                    if v_b != v_c:
                        drift.append({
                            "scope": "row",
                            "id": slug,
                            "field": f,
                            "baseline": v_b,
                            "current": v_c
                        })

        drift.sort(key=lambda x: (x["scope"], x["id"], x["field"]))
        drift_count = len(drift)

    # Create report dict
    report = {
        "schema_version": 1,
        "agrees": agrees,
        "mismatch_count": mismatch_count,
        "drift_count": drift_count,
        "metrics": metrics_list,
        "rows": rows_list,
        "mismatches": mismatches,
        "drift": drift
    }

    # Write report
    try:
        out_dir = os.path.dirname(args.output)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
    except Exception as e:
        print(json.dumps({
            "error": "output",
            "message": f"Failed to write output file: {e}"
        }), file=sys.stderr)
        sys.exit(1)

    # Print success output
    print(json.dumps({
        "output": args.output,
        "agrees": agrees,
        "mismatch_count": mismatch_count,
        "drift_count": drift_count
    }))

    # Exit codes
    if mismatch_count > 0:
        sys.exit(3)
    elif drift_count > 0:
        sys.exit(4)
    else:
        sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="Dual-protocol reconciliation tool for Gel catalog")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Load command
    load_parser = subparsers.add_parser("load", help="Load dataset into Gel catalog")
    load_parser.add_argument("--input", required=True, help="Path to input seed.json")

    # Reconcile command
    reconcile_parser = subparsers.add_parser("reconcile", help="Reconcile and generate report")
    reconcile_parser.add_argument("--output", required=True, help="Path to write report JSON")
    reconcile_parser.add_argument("--baseline", help="Path to baseline report JSON for drift analysis")

    args = parser.parse_args()

    if args.command == "load":
        handle_load(args)
    elif args.command == "reconcile":
        handle_reconcile(args)

if __name__ == "__main__":
    main()
