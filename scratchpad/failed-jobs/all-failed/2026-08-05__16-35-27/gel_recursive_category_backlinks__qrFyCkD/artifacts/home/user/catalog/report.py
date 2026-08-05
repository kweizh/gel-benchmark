#!/usr/bin/env python3
"""Category hierarchy reporting CLI for a Gel 7.1 product catalog.

Usage:
    python3 report.py --slug <slug>                 # report mode
    python3 report.py --slug <slug> --reparent <p>  # move mode
"""

import argparse
import json
import sys

import gel


# --------------------------------------------------------------------------- #
# Data fetching
# --------------------------------------------------------------------------- #

def fetch_data(client):
    """Pull every object the report needs in a handful of round-trips.

    Everything is loaded into memory once so that all tree-walks, cycle
    detection, and aggregations run in plain Python without further queries.
    The total dataset (~2,500 objects) is tiny, so this is both simple and
    fast.
    """
    node_slugs = set(json.loads(client.query_json("select Node.slug")))

    cats = json.loads(client.query_json(
        "select Category { slug, parent: {slug} }"
    ))
    prods = json.loads(client.query_json(
        "select Product { category: {slug}, price_cents, in_stock }"
    ))
    bunds = json.loads(client.query_json(
        "select Bundle { category: {slug}, price_cents }"
    ))
    auds = json.loads(client.query_json(
        "select CategoryAudit { category: {slug}, checked_by }"
    ))

    category_slugs = set()
    parent_map = {}          # slug -> parent slug (or None for a root)
    children_map = {}        # slug -> [child slugs]

    for cat in cats:
        s = cat["slug"]
        category_slugs.add(s)
        p = cat["parent"]
        ps = p["slug"] if p is not None else None
        parent_map[s] = ps
        children_map.setdefault(s, [])
        if ps is not None:
            children_map.setdefault(ps, []).append(s)

    products_by_cat = {}     # cat slug -> [{price_cents, in_stock}, ...]
    for p in prods:
        cs = p["category"]["slug"]
        products_by_cat.setdefault(cs, []).append(
            {"price_cents": p["price_cents"], "in_stock": p["in_stock"]}
        )

    bundles_by_cat = {}      # cat slug -> [{price_cents}, ...]
    for b in bunds:
        cs = b["category"]["slug"]
        bundles_by_cat.setdefault(cs, []).append(
            {"price_cents": b["price_cents"]}
        )

    audit_map = {}           # cat slug -> checked_by
    for a in auds:
        audit_map[a["category"]["slug"]] = a["checked_by"]

    return {
        "node_slugs": node_slugs,
        "category_slugs": category_slugs,
        "parent_map": parent_map,
        "children_map": children_map,
        "products_by_cat": products_by_cat,
        "bundles_by_cat": bundles_by_cat,
        "audit_map": audit_map,
    }


# --------------------------------------------------------------------------- #
# Tree-walk helpers (all cycle-safe)
# --------------------------------------------------------------------------- #

def walk_up(target, parent_map):
    """Return the slug path from the top-most ancestor down to *target*.

    Returns ``None`` when a cycle is encountered while walking upward (i.e.
    the chain never reaches a category without a parent).
    """
    chain = []
    cur = target
    seen = set()
    while cur is not None:
        if cur in seen:
            return None                       # cycle detected
        seen.add(cur)
        chain.append(cur)
        cur = parent_map.get(cur)
    chain.reverse()
    return chain


def collect_descendants(target, children_map):
    """Return the set containing *target* and every descendant at every depth.

    Uses a visited set so it can never infinite-loop, even on legacy looped
    data.
    """
    result = set()
    stack = [target]
    while stack:
        cur = stack.pop()
        if cur in result:
            continue
        result.add(cur)
        for child in children_map.get(cur, []):
            if child not in result:
                stack.append(child)
    return result


def deepest_branch(target, children_map):
    """Longest slug chain starting at *target* and following children down.

    Ties are broken by choosing the sequence that is smallest when the two
    slug lists are compared element-by-element by Unicode code point (which is
    exactly what Python's built-in list/string comparison does).
    """
    memo = {}
    on_path = set()

    def dfs(slug):
        if slug in memo:
            return memo[slug]
        if slug in on_path:          # cycle guard – treat as a leaf
            return [slug]
        on_path.add(slug)
        children = children_map.get(slug, [])
        if not children:
            best = [slug]
        else:
            best = None
            for child in sorted(children):
                cand = [slug] + dfs(child)
                if best is None:
                    best = cand
                elif len(cand) > len(best):
                    best = cand
                elif len(cand) == len(best) and cand < best:
                    best = cand
        on_path.discard(slug)
        memo[slug] = best
        return best

    return dfs(target)


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #

def build_report(slug, data):
    parent_map = data["parent_map"]
    children_map = data["children_map"]

    path = walk_up(slug, parent_map)
    if path is None:
        # Should not happen – cycle check runs before this – but stay safe.
        path = [slug]

    descendants = collect_descendants(slug, children_map)

    product_count = 0
    bundle_count = 0
    price_cents_total = 0
    in_stock_product_count = 0

    for cat_slug in descendants:
        for p in data["products_by_cat"].get(cat_slug, []):
            product_count += 1
            price_cents_total += p["price_cents"]
            if p["in_stock"]:
                in_stock_product_count += 1
        for b in data["bundles_by_cat"].get(cat_slug, []):
            bundle_count += 1
            price_cents_total += b["price_cents"]

    return {
        "slug": slug,
        "path": path,
        "depth": len(path) - 1,
        "parent": parent_map.get(slug),
        "children": sorted(children_map.get(slug, [])),
        "audit_checked_by": data["audit_map"].get(slug),
        "rollup": {
            "category_count": len(descendants),
            "product_count": product_count,
            "bundle_count": bundle_count,
            "listing_count": product_count + bundle_count,
            "price_cents_total": price_cents_total,
            "in_stock_product_count": in_stock_product_count,
        },
        "deepest_branch": deepest_branch(slug, children_map),
    }


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #

def fail(code, obj):
    """Emit a single JSON error object on stderr (stdout stays empty) and exit."""
    sys.stderr.write(json.dumps(obj))
    sys.exit(code)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--reparent", default=None)
    args = parser.parse_args()

    slug = args.slug
    reparent = args.reparent
    move_mode = reparent is not None

    client = gel.create_client()
    data = fetch_data(client)

    # -- failure checks, applied in the exact order specified ---------------- #

    # 1. --slug names no object at all
    if slug not in data["node_slugs"]:
        fail(4, {"error": "unknown_slug", "slug": slug})

    # 2. --slug names an object that is not a Category
    if slug not in data["category_slugs"]:
        fail(5, {"error": "not_a_category", "slug": slug})

    # 3. --reparent names no object at all
    if move_mode and reparent not in data["node_slugs"]:
        fail(4, {"error": "unknown_slug", "slug": reparent})

    # 4. --reparent names an object that is not a Category
    if move_mode and reparent not in data["category_slugs"]:
        fail(5, {"error": "not_a_category", "slug": reparent})

    # 5. walking upward from --slug never reaches a root
    if walk_up(slug, data["parent_map"]) is None:
        fail(3, {"error": "cycle_detected", "slug": slug})

    # 6. walking upward from --reparent never reaches a root
    if move_mode and walk_up(reparent, data["parent_map"]) is None:
        fail(3, {"error": "cycle_detected", "slug": reparent})

    # 7. the move would place the target inside its own subtree
    if move_mode and reparent in collect_descendants(slug, data["children_map"]):
        fail(6, {"error": "would_create_cycle", "slug": slug, "reparent": reparent})

    # -- perform the move (if requested) ------------------------------------ #
    if move_mode:
        # The parent lookup MUST be resolved in an outer WITH scope; an inline
        # sub-query inside the UPDATE body is scoped to the object being
        # updated and would silently match nothing.
        client.execute(
            "with p := (select Category filter .slug = <str>$parent_slug), "
            "update Category filter .slug = <str>$slug set { parent := p }",
            slug=slug,
            parent_slug=reparent,
        )
        data = fetch_data(client)

    # -- build and emit the report ------------------------------------------ #
    report = build_report(slug, data)
    sys.stdout.write(json.dumps(report))
    sys.exit(0)


if __name__ == "__main__":
    main()
