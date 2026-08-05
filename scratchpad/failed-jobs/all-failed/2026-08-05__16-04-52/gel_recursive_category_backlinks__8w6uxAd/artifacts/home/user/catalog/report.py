#!/usr/bin/env python3
"""Category hierarchy reporting CLI for Gel 7.1 catalog."""

import argparse
import json
import sys

import gel


def error(code: int, msg: str, slug: str, reparent: str | None = None) -> None:
    """Write error to stderr and exit with given code."""
    payload = {"error": msg, "slug": slug}
    if reparent is not None:
        payload["reparent"] = reparent
    print(json.dumps(payload), file=sys.stderr)
    sys.exit(code)


def get_node(client: gel.Client, slug: str) -> gel.Object | None:
    """Fetch any Node by slug, or None if not found."""
    return client.query_single(
        """
        WITH MODULE default
        SELECT Node { slug, __type__: { name } }
        FILTER .slug = <str>$slug
        """,
        slug=slug,
    )


def is_category(client: gel.Client, slug: str) -> bool:
    """Check if a slug names a Category."""
    result = client.query_single(
        """
        WITH MODULE default
        SELECT Category FILTER .slug = <str>$slug
        """,
        slug=slug,
    )
    return result is not None


def get_parent_slug(client: gel.Client, slug: str) -> str | None:
    """Return parent slug or None."""
    return client.query_single(
        """
        WITH MODULE default
        SELECT (SELECT Category FILTER .slug = <str>$slug).parent.slug
        """,
        slug=slug,
    )


def detect_cycle(client: gel.Client, slug: str) -> bool:
    """Return True if walking upward from slug never reaches a root (cycle detected)."""
    seen: set[str] = set()
    current = slug
    while True:
        if current in seen:
            return True  # cycle
        seen.add(current)
        parent_slug = get_parent_slug(client, current)
        if parent_slug is None:
            return False  # reached root
        current = parent_slug


def get_ancestor_path(client: gel.Client, slug: str) -> list[str]:
    """Return slugs from top-most ancestor down to target (inclusive)."""
    path: list[str] = []
    current = slug
    while True:
        path.append(current)
        parent_slug = get_parent_slug(client, current)
        if parent_slug is None:
            break
        current = parent_slug
    path.reverse()
    return path


def get_depth(client: gel.Client, slug: str) -> int:
    """Return number of ancestors above target."""
    depth = 0
    current = slug
    while True:
        parent_slug = get_parent_slug(client, current)
        if parent_slug is None:
            break
        depth += 1
        current = parent_slug
    return depth


def get_children_slugs(client: gel.Client, slug: str) -> list[str]:
    """Return child category slugs sorted by Unicode code point."""
    result = client.query(
        """
        WITH MODULE default
        SELECT (SELECT Category FILTER .slug = <str>$slug).children.slug
        """,
        slug=slug,
    )
    # Sort in Python by Unicode code point
    return sorted(list(result))


def get_audit_checked_by(client: gel.Client, slug: str) -> str | None:
    """Return checked_by of audit row, or None."""
    return client.query_single(
        """
        WITH MODULE default
        SELECT (SELECT Category FILTER .slug = <str>$slug).audit.checked_by
        """,
        slug=slug,
    )


def get_descendant_slugs(client: gel.Client, slug: str) -> list[str]:
    """Return all descendant category slugs (including target itself), BFS to avoid deep recursion issues."""
    result: list[str] = []
    queue = [slug]
    while queue:
        current = queue.pop(0)
        result.append(current)
        children = client.query(
            """
            WITH MODULE default
            SELECT (SELECT Category FILTER .slug = <str>$slug).children.slug
            """,
            slug=current,
        )
        queue.extend(list(children))
    return result


def compute_rollup(client: gel.Client, descendant_slugs: list[str]) -> dict:
    """Compute rollup stats over all descendant categories."""
    result = client.query_single(
        """
        WITH MODULE default,
            cats := (SELECT Category FILTER .slug IN array_unpack(<array<str>>$slugs)),
        SELECT {
            category_count := count(cats),
            product_count := count(cats.products),
            bundle_count := count(cats.<category[IS Bundle]),
            listing_count := count(cats.products) + count(cats.<category[IS Bundle]),
            price_cents_total := sum(cats.products.price_cents) + sum(cats.<category[IS Bundle].price_cents),
            in_stock_product_count := count(cats.products FILTER .in_stock = true),
        }
        """,
        slugs=descendant_slugs,
    )
    return {
        "category_count": int(result.category_count),
        "product_count": int(result.product_count),
        "bundle_count": int(result.bundle_count),
        "listing_count": int(result.listing_count),
        "price_cents_total": int(result.price_cents_total),
        "in_stock_product_count": int(result.in_stock_product_count),
    }


def compute_deepest_branch(client: gel.Client, slug: str, descendant_slugs: list[str]) -> list[str]:
    """Find the longest downward chain from target.

    For each descendant, walk upward to the target to find the depth,
    then pick the deepest one. Break ties by lexicographic comparison
    of the full slug path.
    """
    if len(descendant_slugs) <= 1:
        return [slug]

    # Build a parent map for all descendants
    parent_map: dict[str, str | None] = {}

    others = [s for s in descendant_slugs if s != slug]

    # Batch query parent slugs
    rows = client.query(
        """
        WITH MODULE default
        SELECT Category { slug, parent_slug := .parent.slug }
        FILTER .slug IN array_unpack(<array<str>>$slugs)
        """,
        slugs=others,
    )
    for row in rows:
        parent_map[row.slug] = row.parent_slug

    target_parent = get_parent_slug(client, slug)
    parent_map[slug] = target_parent

    candidates: list[tuple[int, list[str]]] = []  # (depth, full_path)

    for desc in descendant_slugs:
        path_up = [desc]
        current = desc
        while current != slug:
            p = parent_map.get(current)
            if p is None:
                break
            path_up.append(p)
            current = p
        if current == slug:
            depth = len(path_up) - 1  # number of steps down from target
            full_path = list(reversed(path_up))  # target -> ... -> desc
            candidates.append((depth, full_path))

    if not candidates:
        return [slug]

    max_depth = max(c[0] for c in candidates)
    best_paths = [c[1] for c in candidates if c[0] == max_depth]
    best_paths.sort()
    return best_paths[0]


def is_in_subtree(client: gel.Client, target_slug: str, ancestor_slug: str) -> bool:
    """Check if target_slug is in the subtree of ancestor_slug."""
    current = target_slug
    while True:
        if current == ancestor_slug:
            return True
        parent_slug = get_parent_slug(client, current)
        if parent_slug is None:
            return False
        current = parent_slug


def do_report(client: gel.Client, slug: str) -> dict:
    """Generate the full report document for a category."""
    path = get_ancestor_path(client, slug)
    depth = len(path) - 1
    parent = get_parent_slug(client, slug)
    children = get_children_slugs(client, slug)
    audit_checked_by = get_audit_checked_by(client, slug)

    descendant_slugs = get_descendant_slugs(client, slug)
    rollup = compute_rollup(client, descendant_slugs)
    deepest_branch = compute_deepest_branch(client, slug, descendant_slugs)

    return {
        "slug": slug,
        "path": path,
        "depth": depth,
        "parent": parent,
        "children": children,
        "audit_checked_by": audit_checked_by,
        "rollup": rollup,
        "deepest_branch": deepest_branch,
    }


def reparent(client: gel.Client, slug: str, parent_slug: str) -> None:
    """Move the category under a new parent."""
    client.query(
        """
        WITH MODULE default,
            target := (SELECT Category FILTER .slug = <str>$slug),
            new_parent := (SELECT Category FILTER .slug = <str>$parent_slug)
        UPDATE target
        SET {
            parent := new_parent
        }
        """,
        slug=slug,
        parent_slug=parent_slug,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--reparent", default=None)
    args = parser.parse_args()

    slug: str = args.slug
    reparent_slug: str | None = args.reparent

    client = gel.create_client()

    # 1. Check --slug exists
    node = get_node(client, slug)
    if node is None:
        error(4, "unknown_slug", slug)

    # 2. Check --slug is a Category
    if not is_category(client, slug):
        error(5, "not_a_category", slug)

    # 3 & 4. In move mode, check --reparent
    if reparent_slug is not None:
        reparent_node = get_node(client, reparent_slug)
        if reparent_node is None:
            error(4, "unknown_slug", reparent_slug)
        if not is_category(client, reparent_slug):
            error(5, "not_a_category", reparent_slug)

    # 5. Check cycle on --slug
    if detect_cycle(client, slug):
        error(3, "cycle_detected", slug)

    # 6. In move mode, check cycle on --reparent
    if reparent_slug is not None:
        if detect_cycle(client, reparent_slug):
            error(3, "cycle_detected", reparent_slug)

    # 7. In move mode, check would_create_cycle
    if reparent_slug is not None:
        if slug == reparent_slug:
            error(6, "would_create_cycle", slug, reparent_slug)
        if is_in_subtree(client, reparent_slug, slug):
            error(6, "would_create_cycle", slug, reparent_slug)

    # Perform reparent if in move mode
    if reparent_slug is not None:
        reparent(client, slug, reparent_slug)

    # Generate report
    report = do_report(client, slug)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
