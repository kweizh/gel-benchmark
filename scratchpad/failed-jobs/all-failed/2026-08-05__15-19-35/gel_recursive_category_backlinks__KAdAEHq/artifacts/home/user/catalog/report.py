#!/usr/bin/env python3
"""Category hierarchy reporting / reparenting CLI for the catalog database.

Usage:
    python3 report.py --slug <slug>
    python3 report.py --slug <slug> --reparent <parent_slug>
"""

import argparse
import json
import sys

import gel


def fail(code, payload):
    sys.stderr.write(json.dumps(payload))
    sys.exit(code)


def build_arg_parser():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--reparent", required=False, default=None)
    return parser


def load_category_graph(client):
    """Fetch the full category graph once: id <-> slug, parent, children."""
    cats = client.query(
        """
        select default::Category {
            id,
            slug,
            parent: { id },
        }
        """
    )

    slug_to_id = {}
    id_to_slug = {}
    parent_map = {}
    children_map = {}

    for c in cats:
        cid = str(c.id)
        slug_to_id[c.slug] = cid
        id_to_slug[cid] = c.slug
        pid = str(c.parent.id) if c.parent is not None else None
        parent_map[cid] = pid
        if pid is not None:
            children_map.setdefault(pid, []).append(cid)

    return slug_to_id, id_to_slug, parent_map, children_map


def resolve_slug(client, slug_to_id, slug_value):
    """Return the category id for slug_value, or call fail() appropriately."""
    if slug_value in slug_to_id:
        return slug_to_id[slug_value]

    node = client.query(
        """
        select default::Node { slug }
        filter .slug = <str>$slug
        limit 1
        """,
        slug=slug_value,
    )
    if not node:
        fail(4, {"error": "unknown_slug", "slug": slug_value})
    else:
        fail(5, {"error": "not_a_category", "slug": slug_value})


def walk_up_is_acyclic(parent_map, start_id):
    """Walk upward from start_id. Return True if a parentless node is
    reached, False if a cycle is detected."""
    seen = set()
    cur = start_id
    while cur is not None:
        if cur in seen:
            return False
        seen.add(cur)
        cur = parent_map.get(cur)
    return True


def compute_subtree(children_map, root_id):
    """BFS over children_map starting at root_id (inclusive). Guarded
    against cycles via a visited set, even though a valid root can never
    actually contain one (proven by the fact any category reachable this
    way has, by construction, an acyclic ancestor chain back to root_id)."""
    visited = {root_id}
    order = [root_id]
    stack = [root_id]
    while stack:
        cur = stack.pop()
        for ch in children_map.get(cur, ()):
            if ch not in visited:
                visited.add(ch)
                order.append(ch)
                stack.append(ch)
    return visited, order


def compute_deepest_branch(children_map, id_to_slug, root_id):
    """Longest chain of slugs starting at root_id following child links
    downward. Ties broken by lexicographically smallest slug sequence."""
    best = None  # (length, tuple_of_slugs)

    # Each stack frame: (current_id, path_so_far (tuple of slugs), visited ids on this path)
    stack = [(root_id, (id_to_slug[root_id],), frozenset((root_id,)))]
    while stack:
        cur, path_so_far, visited_on_path = stack.pop()
        children = [
            ch for ch in children_map.get(cur, ()) if ch not in visited_on_path
        ]
        if not children:
            candidate = (len(path_so_far), path_so_far)
            if best is None or candidate[0] > best[0] or (
                candidate[0] == best[0] and candidate[1] < best[1]
            ):
                best = candidate
        else:
            for ch in children:
                stack.append(
                    (
                        ch,
                        path_so_far + (id_to_slug[ch],),
                        visited_on_path | {ch},
                    )
                )

    return list(best[1])


def build_report(client, id_to_slug, parent_map, children_map, target_id, target_slug):
    # path / depth / parent
    path_ids = []
    cur = target_id
    while cur is not None:
        path_ids.append(cur)
        cur = parent_map.get(cur)
    path_ids.reverse()
    path = [id_to_slug[i] for i in path_ids]
    depth = len(path) - 1
    parent_id = parent_map.get(target_id)
    parent_slug_val = id_to_slug[parent_id] if parent_id is not None else None

    # children
    children_slugs = sorted(
        id_to_slug[c] for c in children_map.get(target_id, ())
    )

    # audit
    audit_rows = client.query(
        """
        select default::CategoryAudit { checked_by }
        filter .category.slug = <str>$slug
        """,
        slug=target_slug,
    )
    audit_checked_by = audit_rows[0].checked_by if audit_rows else None

    # subtree for rollup / deepest_branch
    subtree_ids, _ = compute_subtree(children_map, target_id)

    products = client.query(
        "select default::Product { price_cents, in_stock, category: { id } }"
    )
    bundles = client.query(
        "select default::Bundle { price_cents, category: { id } }"
    )

    product_count = 0
    bundle_count = 0
    price_cents_total = 0
    in_stock_product_count = 0

    for p in products:
        cid = str(p.category.id)
        if cid in subtree_ids:
            product_count += 1
            price_cents_total += p.price_cents
            if p.in_stock:
                in_stock_product_count += 1

    for b in bundles:
        cid = str(b.category.id)
        if cid in subtree_ids:
            bundle_count += 1
            price_cents_total += b.price_cents

    rollup = {
        "category_count": len(subtree_ids),
        "product_count": product_count,
        "bundle_count": bundle_count,
        "listing_count": product_count + bundle_count,
        "price_cents_total": price_cents_total,
        "in_stock_product_count": in_stock_product_count,
    }

    deepest_branch = compute_deepest_branch(children_map, id_to_slug, target_id)

    return {
        "slug": target_slug,
        "path": path,
        "depth": depth,
        "parent": parent_slug_val,
        "children": children_slugs,
        "audit_checked_by": audit_checked_by,
        "rollup": rollup,
        "deepest_branch": deepest_branch,
    }


def main():
    args = build_arg_parser().parse_args()
    slug = args.slug
    reparent_slug = args.reparent

    client = gel.create_client()
    try:
        slug_to_id, id_to_slug, parent_map, children_map = load_category_graph(client)

        # checks 1 & 2
        target_id = resolve_slug(client, slug_to_id, slug)

        # checks 3 & 4 (move mode only)
        reparent_id = None
        if reparent_slug is not None:
            reparent_id = resolve_slug(client, slug_to_id, reparent_slug)

        # check 5
        if not walk_up_is_acyclic(parent_map, target_id):
            fail(3, {"error": "cycle_detected", "slug": slug})

        # check 6 (move mode only)
        if reparent_slug is not None:
            if not walk_up_is_acyclic(parent_map, reparent_id):
                fail(3, {"error": "cycle_detected", "slug": reparent_slug})

        # check 7 (move mode only) - would this move create a cycle?
        if reparent_slug is not None:
            subtree_ids, _ = compute_subtree(children_map, target_id)
            if reparent_id in subtree_ids:
                fail(
                    6,
                    {
                        "error": "would_create_cycle",
                        "slug": slug,
                        "reparent": reparent_slug,
                    },
                )

        # perform the move, if requested
        if reparent_slug is not None:
            client.query(
                """
                update default::Category
                filter .slug = <str>$slug
                set {
                    parent := (
                        select detached default::Category
                        filter .slug = <str>$reparent_slug
                    )
                }
                """,
                slug=slug,
                reparent_slug=reparent_slug,
            )
            parent_map[target_id] = reparent_id

        result = build_report(
            client, id_to_slug, parent_map, children_map, target_id, slug
        )

        sys.stdout.write(json.dumps(result))
        sys.stdout.write("\n")
        sys.exit(0)
    finally:
        client.close()


if __name__ == "__main__":
    main()
