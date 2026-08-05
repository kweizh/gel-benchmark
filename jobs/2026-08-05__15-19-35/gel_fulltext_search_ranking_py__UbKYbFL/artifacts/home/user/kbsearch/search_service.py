"""Asynchronous ranked full-text search over the handbook knowledge base."""
from __future__ import annotations

import json
import re

import gel

VALID_STATUSES = ("draft", "published", "archived")

_SEARCH_QUERY = """
with
  q := <str>$q_param,
  status_filter := <optional str>$status,
  tag_filter := <optional str>$tag,
  matches := (
    select fts::search(Article, q, language := 'eng')
    filter (
      true if not exists status_filter
      else .object.status = <ArticleStatus>status_filter
    )
    and (
      true if not exists tag_filter
      else tag_filter in .object.tags
    )
  )
select (
  total := count(matches),
  items := array_agg((
    select (
      for m in matches union (
        select (
          slug := m.object.slug,
          title := m.object.title,
          status := m.object.status,
          tags := array_agg(m.object.tags),
          score := m.score,
        )
      )
    )
    order by .score desc then .slug asc
    offset <int64>$offset
    limit <int64>$limit
  ))
)
"""

# Characters considered part of a "word" for highlighting purposes.
_WORD_CHAR_RE = r"[A-Za-z0-9]"
_STRIP_RE = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")


def _extract_highlight_terms(query: str) -> list[str]:
    terms = []
    for piece in query.split():
        stripped = _STRIP_RE.sub("", piece)
        if stripped:
            terms.append(stripped)
    return terms


def _highlight(title: str, terms: list[str]) -> str:
    if not terms:
        return title
    unique_terms = sorted({t for t in terms}, key=len, reverse=True)
    escaped = [re.escape(t) for t in unique_terms]
    pattern = re.compile(
        r"(?<!" + _WORD_CHAR_RE + r")(" + "|".join(escaped) + r")(?!" + _WORD_CHAR_RE + r")",
        re.IGNORECASE,
    )
    return pattern.sub(lambda m: f"<b>{m.group(0)}</b>", title)


def _validate_non_negative_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


async def search_articles(
    query: str,
    *,
    status: str | None = None,
    tag: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    if not isinstance(query, str):
        raise ValueError("query must be a string")

    limit = _validate_non_negative_int(limit, "limit")
    offset = _validate_non_negative_int(offset, "offset")

    if status is not None and status not in VALID_STATUSES:
        raise ValueError(
            f"status must be one of {VALID_STATUSES!r}, got {status!r}"
        )

    if tag is not None and not isinstance(tag, str):
        raise ValueError("tag must be a string")

    terms = query.split()
    if not terms:
        return {
            "query": query,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "results": [],
        }

    client = gel.create_async_client()
    try:
        raw = await client.query_single_json(
            _SEARCH_QUERY,
            q_param=query,
            status=status,
            tag=tag,
            offset=offset,
            limit=limit,
        )
    finally:
        await client.aclose()

    data = json.loads(raw)
    total = data["total"]
    items = data["items"]

    highlight_terms = _extract_highlight_terms(query)

    results = []
    for idx, item in enumerate(items):
        results.append(
            {
                "rank": offset + idx + 1,
                "slug": item["slug"],
                "title": item["title"],
                "status": item["status"],
                "tags": sorted(item["tags"]),
                "score": item["score"],
                "highlight": _highlight(item["title"], highlight_terms),
            }
        )

    return {
        "query": query,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
    }
