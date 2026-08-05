#!/usr/bin/env python3
import sys
import json
import argparse
import gel

def print_error_and_exit(code, err_dict):
    sys.stdout.write("")
    sys.stderr.write(json.dumps(err_dict) + "\n")
    sys.exit(code)

def main():
    parser = argparse.ArgumentParser(description="Category Hierarchy Reporting CLI")
    parser.add_argument("--slug", required=True, help="The target category slug")
    parser.add_argument("--reparent", help="The parent category slug to move under")
    args = parser.parse_args()

    slug = args.slug
    reparent = args.reparent

    # Initialize Gel client
    client = gel.create_client()

    # 1. Check if target slug exists in the database
    target_nodes = json.loads(client.query_json(
        "select Node { slug, type_name := .__type__.name } filter .slug = <str>$slug",
        slug=slug
    ))
    if not target_nodes:
        print_error_and_exit(4, {"error": "unknown_slug", "slug": slug})

    # 2. Check if target slug is a Category
    target_node = target_nodes[0]
    if target_node["type_name"] != "default::Category":
        print_error_and_exit(5, {"error": "not_a_category", "slug": slug})

    # 3 & 4. If in move mode, check if reparent slug exists and is a Category
    if reparent is not None:
        reparent_nodes = json.loads(client.query_json(
            "select Node { slug, type_name := .__type__.name } filter .slug = <str>$reparent",
            reparent=reparent
        ))
        if not reparent_nodes:
            print_error_and_exit(4, {"error": "unknown_slug", "slug": reparent})

        reparent_node = reparent_nodes[0]
        if reparent_node["type_name"] != "default::Category":
            print_error_and_exit(5, {"error": "not_a_category", "slug": reparent})

    # Fetch all categories to build the parent map and check for cycles
    categories = json.loads(client.query_json("""
        select Category {
            slug,
            parent_slug := .parent.slug,
            audit_checked_by := .audit.checked_by
        }
    """))
    parent_map = {c['slug']: c['parent_slug'] for c in categories}

    # 5. Check for cycle in target slug's ancestry
    visited = set()
    curr = slug
    while curr is not None:
        if curr in visited:
            print_error_and_exit(3, {"error": "cycle_detected", "slug": slug})
        visited.add(curr)
        curr = parent_map.get(curr)

    # 6. If in move mode, check for cycle in reparent slug's ancestry
    if reparent is not None:
        visited_reparent = set()
        curr = reparent
        while curr is not None:
            if curr in visited_reparent:
                print_error_and_exit(3, {"error": "cycle_detected", "slug": reparent})
            visited_reparent.add(curr)
            curr = parent_map.get(curr)

    # 7. If in move mode, check if move would create a cycle (place target in its own subtree)
    if reparent is not None:
        curr = reparent
        while curr is not None:
            if curr == slug:
                print_error_and_exit(6, {"error": "would_create_cycle", "slug": slug, "reparent": reparent})
            curr = parent_map.get(curr)

    # If in move mode, perform the move operation
    if reparent is not None:
        client.query("""
            with p := (select Category filter .slug = <str>$reparent)
            update Category
            filter .slug = <str>$slug
            set {
                parent := p
            }
        """, reparent=reparent, slug=slug)

        # Reload categories to reflect the updated parent-child links
        categories = json.loads(client.query_json("""
            select Category {
                slug,
                parent_slug := .parent.slug,
                audit_checked_by := .audit.checked_by
            }
        """))
        parent_map = {c['slug']: c['parent_slug'] for c in categories}

    # Fetch listings
    listings = json.loads(client.query_json("""
        select Listing {
            category_slug := .category.slug,
            price_cents,
            is_product := exists [is Product],
            in_stock := [is Product].in_stock
        }
    """))

    # Build the category tree structure in-memory
    cat_by_slug = {}
    for c in categories:
        cat_by_slug[c['slug']] = {
            'slug': c['slug'],
            'parent_slug': c['parent_slug'],
            'audit_checked_by': c['audit_checked_by'],
            'children_slugs': [],
            'products': [],
            'bundles': []
        }

    for c_slug, cat in cat_by_slug.items():
        pslug = cat['parent_slug']
        if pslug and pslug in cat_by_slug:
            cat_by_slug[pslug]['children_slugs'].append(c_slug)

    for cat in cat_by_slug.values():
        cat['children_slugs'].sort()

    for lst in listings:
        cslug = lst['category_slug']
        if cslug in cat_by_slug:
            if lst['is_product']:
                cat_by_slug[cslug]['products'].append(lst)
            else:
                cat_by_slug[cslug]['bundles'].append(lst)

    # Compute report fields
    # Path
    upward_path = []
    curr = slug
    while curr is not None:
        upward_path.append(curr)
        curr = parent_map.get(curr)
    path = list(reversed(upward_path))

    depth = len(path) - 1
    parent = parent_map.get(slug)
    children = cat_by_slug[slug]['children_slugs']
    audit_checked_by = cat_by_slug[slug]['audit_checked_by']

    # Rollup
    descendants = []
    queue = [slug]
    while queue:
        curr = queue.pop(0)
        descendants.append(curr)
        queue.extend(cat_by_slug[curr]['children_slugs'])

    category_count = len(descendants)
    product_count = 0
    bundle_count = 0
    price_cents_total = 0
    in_stock_product_count = 0

    for d_slug in descendants:
        cat = cat_by_slug[d_slug]
        for prod in cat['products']:
            product_count += 1
            price_cents_total += prod['price_cents']
            if prod['in_stock']:
                in_stock_product_count += 1
        for bnd in cat['bundles']:
            bundle_count += 1
            price_cents_total += bnd['price_cents']

    listing_count = product_count + bundle_count
    rollup = {
        "category_count": category_count,
        "product_count": product_count,
        "bundle_count": bundle_count,
        "listing_count": listing_count,
        "price_cents_total": price_cents_total,
        "in_stock_product_count": in_stock_product_count
    }

    # Deepest Branch
    memo = {}
    def get_deepest_branch(u):
        if u in memo:
            return memo[u]
        c_slugs = cat_by_slug[u]['children_slugs']
        if not c_slugs:
            res = [u]
        else:
            best_child_branch = min(
                (get_deepest_branch(child) for child in c_slugs),
                key=lambda b: (-len(b), b)
            )
            res = [u] + best_child_branch
        memo[u] = res
        return res

    deepest_branch = get_deepest_branch(slug)

    # Assemble report
    report = {
        "slug": slug,
        "path": path,
        "depth": depth,
        "parent": parent,
        "children": children,
        "audit_checked_by": audit_checked_by,
        "rollup": rollup,
        "deepest_branch": deepest_branch
    }

    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
