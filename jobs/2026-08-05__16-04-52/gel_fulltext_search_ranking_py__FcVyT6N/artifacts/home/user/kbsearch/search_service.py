"""Asynchronous search API for the knowledge base.

Exposes `search_articles()` which performs ranked full-text search over
Articles using Gel's built-in FTS capabilities.
"""

import json
import re
from typing import Any

import gel

VALID_STATUSES = {"draft", "published", "archived"}


def _validate_params(
    status: str | None,
    tag: str | None,
    limit: int,
    offset: int,
) -> None:
    """Validate search parameters, raising ValueError on invalid input."""
    if not isinstance(limit, int) or limit < 0:
        raise ValueError(f"limit must be a non-negative integer, got {limit!r}")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError(f"offset must be a non-negative integer, got {offset!r}")
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(VALID_STATUSES)!r}, got {status!r}"
        )


def _highlight_title(title: str, query: str) -> str:
    """Wrap case-insensitive whole-word occurrences of query terms in <b> tags.

    An occurrence counts as whole-word when it is neither directly preceded
    nor directly followed by an ASCII letter or digit. Only literal
    occurrences are wrapped (no morphological matching).
    """
    if not query.strip():
        return title

    # Split on whitespace, strip leading/trailing non-alphanumeric chars
    terms = []
    for piece in query.split():
        stripped = piece.strip()
        # Strip leading non-alphanumeric
        while stripped and not stripped[0].isalnum():
            stripped = stripped[1:]
        # Strip trailing non-alphanumeric
        while stripped and not stripped[-1].isalnum():
            stripped = stripped[:-1]
        if stripped:
            terms.append(stripped)

    if not terms:
        return title

    # Build a regex that matches any term as a whole word (case-insensitive)
    # A "whole word" means not preceded or followed by an ASCII letter or digit
    pattern = re.compile(
        r"(?<![a-zA-Z0-9])("
        + "|".join(re.escape(t) for t in terms)
        + r")(?![a-zA-Z0-9])",
        re.IGNORECASE,
    )

    return pattern.sub(r"<b>\1</b>", title)


async def search_articles(
    query: str,
    *,
    status: str | None = None,
    tag: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict[str, Any]:
    """Search articles by full-text query with optional filters and pagination.

    Returns a dict with keys: query, total, limit, offset, results.
    """
    _validate_params(status, tag, limit, offset)

    # Empty query matches nothing
    if not query.strip():
        return {
            "query": query,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "results": [],
        }

    async with gel.create_async_client() as client:
        # Build the EdgeQL query dynamically based on filters
        # Base: FTS search with custom weights
        # We use explicit weights [1.0, 0.5, 0.25] for A(title), B(summary), C(body)
        edgeql = """
            WITH matches := fts::search(
                Article,
                <str>$q,
                language := 'eng',
                weights := [1.0, 0.5, 0.25],
            )
            SELECT (
                FOR m IN {matches}
                UNION (
                    WITH obj := m.object
                    SELECT {
                        slug := obj.slug,
                        title := obj.title,
                        status := obj.status,
                        tags := obj.tags,
                        score := m.score,
                    }
        """

        # Build filter conditions
        filter_parts = []
        if status is not None:
            filter_parts.append(
                f"obj.status = ArticleStatus.{status}"
            )
        if tag is not None:
            filter_parts.append(
                f"<str>$tag IN obj.tags"
            )

        if filter_parts:
            edgeql += "\n                    FILTER " + " AND ".join(filter_parts)

        edgeql += """
                )
            )
            ORDER BY .score DESC THEN .slug ASC
        """

        # Execute the query
        query_vars: dict[str, Any] = {"q": query}
        if tag is not None:
            query_vars["tag"] = tag

        rows = await client.query_json(edgeql, **query_vars)
        all_results = json.loads(rows)

    # Compute total (before pagination)
    total = len(all_results)

    # Apply pagination
    paginated = all_results[offset : offset + limit] if limit > 0 else []

    # Build result objects
    results = []
    for i, row in enumerate(paginated):
        # row is a dict from query_json
        row_dict = row if isinstance(row, dict) else {}
        tags_list = sorted(row_dict.get("tags", []))
        highlight = _highlight_title(row_dict.get("title", ""), query)
        results.append(
            {
                "rank": offset + i + 1,
                "slug": row_dict.get("slug", ""),
                "title": row_dict.get("title", ""),
                "status": row_dict.get("status", ""),
                "tags": tags_list,
                "score": row_dict.get("score", 0.0),
                "highlight": highlight,
            }
        )

    return {
        "query": query,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
    }
