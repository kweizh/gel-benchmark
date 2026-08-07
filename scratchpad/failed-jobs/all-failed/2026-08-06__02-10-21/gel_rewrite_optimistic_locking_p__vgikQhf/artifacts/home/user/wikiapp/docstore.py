"""Storage layer for the wiki document store.

The version counter and all timestamp bookkeeping are enforced by the Gel
schema itself (mutation rewrites + a history trigger), so that no client --
not even a raw query typed into the shell -- can forge or skip a version.
This module provides a small, typed, optimistic-locking API on top of that
schema.

Every public coroutine takes the async Gel ``client`` as its only positional
argument; all other parameters are keyword-only.
"""

from __future__ import annotations

import gel
from gel import options


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class DocStoreError(Exception):
    """Base class for all docstore errors."""


class DocumentNotFound(DocStoreError):
    """Raised when a document slug does not exist."""

    def __init__(self, slug: str):
        super().__init__(slug)
        self.slug = slug


class SlugConflict(DocStoreError):
    """Raised when creating a document whose slug already exists."""

    def __init__(self, slug: str):
        super().__init__(slug)
        self.slug = slug


class StaleRevision(DocStoreError):
    """Raised when a compare-and-set write targets an outdated revision."""

    def __init__(self, slug: str, expected_revision: int,
                 actual_revision: int):
        super().__init__(slug, expected_revision, actual_revision)
        self.slug = slug
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
# Exact property set returned for a document.
_DOC_SHAPE = (
    "{ slug, title, body, revision, last_editor, "
    "created_at, modified_at, title_modified_at }"
)

# Exact property set returned for a history entry.
_HIST_SHAPE = "{ revision, title, body, author, recorded_at }"


def _zero_backoff(_attempt: int) -> float:
    """Immediate retry: the conflicting transaction already committed."""
    return 0.0


def _doc_to_dict(d) -> dict:
    return {
        "slug": d.slug,
        "title": d.title,
        "body": d.body,
        "revision": int(d.revision),
        "last_editor": d.last_editor,
        "created_at": d.created_at,
        "modified_at": d.modified_at,
        "title_modified_at": d.title_modified_at,
    }


def _hist_to_dict(h) -> dict:
    return {
        "revision": int(h.revision),
        "title": h.title,
        "body": h.body,
        "author": h.author,
        "recorded_at": h.recorded_at,
    }


async def _current_revision(client, slug: str):
    """Return ``(exists, revision)`` for the document with ``slug``."""
    doc = await client.query_single(
        "select Document { revision } filter .slug = <str>$slug",
        slug=slug,
    )
    if doc is None:
        return False, None
    return True, int(doc.revision)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
async def create_document(client, *, slug: str, title: str, body: str,
                          author: str) -> dict:
    """Create a new document authored by ``author`` and return it.

    The schema stamps ``revision = 1`` and all timestamps automatically and
    records the first history entry via the ``record_history`` trigger.  If the
    slug already exists :class:`SlugConflict` is raised and neither the stored
    document nor the history is touched.
    """
    try:
        result = await client.query_single(
            f"select (insert Document {{"
            f"    slug := <str>$slug,"
            f"    title := <str>$title,"
            f"    body := <str>$body,"
            f"    last_editor := <str>$author"
            f"}}) {_DOC_SHAPE}",
            slug=slug, title=title, body=body, author=author,
        )
    except gel.ConstraintViolationError:
        raise SlugConflict(slug=slug)
    return _doc_to_dict(result)


async def get_document(client, *, slug: str) -> dict:
    """Return the stored document, or raise :class:`DocumentNotFound`."""
    result = await client.query_single(
        f"select Document {_DOC_SHAPE} filter .slug = <str>$slug",
        slug=slug,
    )
    if result is None:
        raise DocumentNotFound(slug=slug)
    return _doc_to_dict(result)


async def update_document(client, *, slug: str, expected_revision: int,
                          author: str, title: str | None = None,
                          body: str | None = None) -> dict:
    """Compare-and-set write.

    Applies the supplied ``title`` and/or ``body`` only if the document's
    current ``revision`` equals ``expected_revision``.  On a mismatch raises
    :class:`StaleRevision` (without modifying the document or history); an
    unknown slug raises :class:`DocumentNotFound`.  Supplying neither ``title``
    nor ``body`` raises :class:`ValueError` without touching the database.
    """
    if title is None and body is None:
        raise ValueError(
            "update_document requires at least one of title or body"
        )

    # Build the SET shape dynamically so that __specified__.title is true only
    # when the caller actually supplies a title.  This is what drives the
    # title_modified_at rewrite in the schema.
    set_parts = ["last_editor := <str>$author"]
    params: dict = {
        "slug": slug,
        "expected_revision": expected_revision,
        "author": author,
    }
    if title is not None:
        set_parts.append("title := <str>$title")
        params["title"] = title
    if body is not None:
        set_parts.append("body := <str>$body")
        params["body"] = body

    query = (
        f"select (update Document"
        f"    filter .slug = <str>$slug and .revision = <int64>$expected_revision"
        f"    set {{ {', '.join(set_parts)} }}"
        f") {_DOC_SHAPE}"
    )

    result = await client.query_single(query, **params)
    if result is not None:
        return _doc_to_dict(result)

    # Zero rows matched: either the slug is unknown or the revision is stale.
    exists, actual = await _current_revision(client, slug)
    if not exists:
        raise DocumentNotFound(slug=slug)
    raise StaleRevision(
        slug=slug,
        expected_revision=expected_revision,
        actual_revision=actual,
    )


async def append_line(client, *, slug: str, line: str, author: str,
                      max_attempts: int = 16) -> dict:
    """Append ``line`` to the document body using compare-and-set.

    The new body is the old body, a single ``"\\n"``, then ``line``.  The
    compare-and-set is performed inside a REPEATABLE READ transaction: the
    revision is read and the body is rewritten (``body := .body ++ "\\n" ++
    line``) only when the stored revision still matches.  A concurrent writer
    that bumped the revision in the meantime causes a serialisation conflict
    which Gel retries automatically (with no backoff); after ``max_attempts``
    such conflicts :class:`StaleRevision` is raised.
    """
    rc = client.with_retry_options(
        options.RetryOptions(attempts=max_attempts, backoff=_zero_backoff)
    ).with_transaction_options(
        options.TransactionOptions(
            isolation=options.IsolationLevel.RepeatableRead
        )
    )

    last_expected: int | None = None
    try:
        async for tx in rc.transaction():
            async with tx:
                doc = await tx.query_single(
                    "select Document { revision } filter .slug = <str>$slug",
                    slug=slug,
                )
                if doc is None:
                    raise DocumentNotFound(slug=slug)
                last_expected = int(doc.revision)
                updated = await tx.query_single(
                    f"select (update Document"
                    f"    filter .slug = <str>$slug"
                    f"        and .revision = <int64>$rev"
                    f"    set {{ body := .body ++ <str>$sep ++ <str>$line,"
                    f"           last_editor := <str>$author }}"
                    f") {_DOC_SHAPE}",
                    slug=slug, rev=last_expected, sep="\n",
                    line=line, author=author,
                )
                return _doc_to_dict(updated)
    except gel.TransactionConflictError:
        exists, actual = await _current_revision(client, slug)
        raise StaleRevision(
            slug=slug,
            expected_revision=last_expected,
            actual_revision=actual if exists else last_expected,
        )


async def get_history(client, *, slug: str) -> list[dict]:
    """Return the document's history ordered by ascending revision.

    Raises :class:`DocumentNotFound` for an unknown slug.
    """
    exists, _ = await _current_revision(client, slug)
    if not exists:
        raise DocumentNotFound(slug=slug)
    rows = await client.query(
        f"select DocumentRevision {_HIST_SHAPE}"
        f"    filter .document.slug = <str>$slug"
        f"    order by .revision",
        slug=slug,
    )
    return [_hist_to_dict(r) for r in rows]
