import asyncio
import re
import gel

def clean_term(term: str) -> str:
    # Strip leading non-alphanumeric characters
    term = re.sub(r'^[^a-zA-Z0-9]+', '', term)
    # Strip trailing non-alphanumeric characters
    term = re.sub(r'[^a-zA-Z0-9]+$', '', term)
    return term

def highlight_title(title: str, terms: list[str]) -> str:
    if not terms:
        return title

    intervals = []
    for term in terms:
        # Match whole-word occurrences: neither preceded nor followed by an ASCII letter or digit
        pattern = re.compile(rf'(?<![a-zA-Z0-9])({re.escape(term)})(?![a-zA-Z0-9])', re.IGNORECASE)
        for m in pattern.finditer(title):
            intervals.append((m.start(), m.end()))

    if not intervals:
        return title

    # Sort intervals by start ascending, then by end descending
    intervals.sort(key=lambda x: (x[0], -x[1]))
    
    # Merge overlapping or touching intervals
    merged = []
    for start, end in intervals:
        if not merged:
            merged.append((start, end))
        else:
            last_start, last_end = merged[-1]
            if start <= last_end:
                merged[-1] = (last_start, max(last_end, end))
            else:
                merged.append((start, end))

    # Reconstruct title with <b> and </b> tags
    highlighted = []
    last_idx = 0
    for start, end in merged:
        highlighted.append(title[last_idx:start])
        highlighted.append('<b>')
        highlighted.append(title[start:end])
        highlighted.append('</b>')
        last_idx = end
    highlighted.append(title[last_idx:])
    return ''.join(highlighted)

async def search_articles(
    query: str,
    *,
    status: str | None = None,
    tag: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    # 1. Validate arguments
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if status is not None and (not isinstance(status, str) or status not in ("draft", "published", "archived")):
        raise ValueError("status must be 'draft', 'published', or 'archived'")

    # 2. Process query terms
    raw_terms = query.split()
    cleaned_terms = [clean_term(t) for t in raw_terms]
    cleaned_terms = [t for t in cleaned_terms if t]

    # If the query is empty or has no valid terms, return empty results immediately
    if not cleaned_terms:
        return {
            "query": query,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "results": []
        }

    # Construct the FTS query by joining terms with ' OR '
    fts_query = " OR ".join(cleaned_terms)

    # 3. Connect to the database and query
    client = gel.create_async_client()
    try:
        edgeql_query = """
        with
            res := (
                select fts::search(Article, <str>$search_query, language := 'eng')
            ),
            filtered_res := (
                select res
                filter
                    (<bool>$has_status = false or res.object.status = <ArticleStatus>$status)
                    and (<bool>$has_tag = false or any(res.object.tags = <str>$tag))
            ),
            ordered_res := (
                select filtered_res
                order by filtered_res.score desc then filtered_res.object.slug asc
            ),
            total_count := count(ordered_res)
        select {
            total := total_count,
            results := (
                select (
                    for r in ordered_res union (
                        select {
                            score := r.score,
                            slug := r.object.slug,
                            title := r.object.title,
                            status := <str>r.object.status,
                            tags := array_agg((select r.object.tags order by r.object.tags asc)) ?? <array<str>>[]
                        }
                    )
                )
                offset <int64>$offset
                limit <int64>$limit
            )
        }
        """
        
        db_res = await client.query_single_json(
            edgeql_query,
            search_query=fts_query,
            has_status=(status is not None),
            status=status if status is not None else "draft",
            has_tag=(tag is not None),
            tag=tag if tag is not None else "",
            offset=offset,
            limit=limit
        )
        
        # Parse the JSON response
        import json
        data = json.loads(db_res)
        total = data.get("total", 0)
        db_results = data.get("results") or []
        
        # 4. Post-process the results to add 'rank' and 'highlight'
        results = []
        for i, item in enumerate(db_results):
            score = item["score"]
            slug = item["slug"]
            title = item["title"]
            item_status = item["status"]
            item_tags = item["tags"]
            
            # Highlight title
            highlighted = highlight_title(title, cleaned_terms)
            
            results.append({
                "rank": offset + i + 1,
                "slug": slug,
                "title": title,
                "status": item_status,
                "tags": item_tags,
                "score": score,
                "highlight": highlighted
            })
            
        return {
            "query": query,
            "total": total,
            "limit": limit,
            "offset": offset,
            "results": results
        }
        
    finally:
        await client.aclose()
