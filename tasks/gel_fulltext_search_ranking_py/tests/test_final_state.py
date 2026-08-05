"""Final-state verification for the gel_fulltext_search_ranking_py task."""

import asyncio
import glob
import importlib
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

PROJECT_DIR = "/home/user/kbsearch"
SEED_DATA = os.path.join(PROJECT_DIR, "seed_data.json")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
SEARCH_CLI = os.path.join(PROJECT_DIR, "search_cli.py")
SEED_SCRIPT = os.path.join(PROJECT_DIR, "seed.py")
START_SCRIPT = "start-gel.sh"

EXPECTED_TOTAL_ARTICLES = 30
STATUS_LABELS = {"draft", "published", "archived"}
TOP_LEVEL_KEYS = {"query", "total", "limit", "offset", "results"}
RESULT_KEYS = {"rank", "slug", "title", "status", "tags", "score", "highlight"}
SCORE_EPS = 1e-9


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def gel_server():
    """Bring the local Gel server up (idempotent) before anything touches the database."""
    script = shutil.which(START_SCRIPT)
    assert script is not None, f"{START_SCRIPT} is not available in PATH."
    proc = subprocess.run([script], capture_output=True, text=True, timeout=420)
    assert proc.returncode == 0, (
        f"Could not start the local Gel server via {START_SCRIPT} "
        f"(exit code {proc.returncode}).\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def seed_records():
    assert os.path.isfile(SEED_DATA), f"The corpus file {SEED_DATA} is missing."
    with open(SEED_DATA, encoding="utf-8") as handle:
        records = json.load(handle)
    assert len(records) == EXPECTED_TOTAL_ARTICLES, (
        f"{SEED_DATA} must still contain {EXPECTED_TOTAL_ARTICLES} records, "
        f"found {len(records)} - the corpus file must not be modified."
    )
    return records


@pytest.fixture(scope="session")
def service(gel_server):
    """Import the solution's search service module from the project directory."""
    assert os.path.isfile(os.path.join(PROJECT_DIR, "search_service.py")), (
        f"{PROJECT_DIR}/search_service.py does not exist."
    )
    os.chdir(PROJECT_DIR)
    if PROJECT_DIR not in sys.path:
        sys.path.insert(0, PROJECT_DIR)
    module = importlib.import_module("search_service")
    assert hasattr(module, "search_articles"), (
        "search_service.py does not expose a search_articles function."
    )
    return module


def search(service_module, query, **kwargs):
    """Call the async search entry point and return its payload."""
    payload = asyncio.run(service_module.search_articles(query, **kwargs))
    assert isinstance(payload, dict), (
        f"search_articles({query!r}, {kwargs!r}) must return a dict, got {type(payload)!r}."
    )
    return payload


def slugs_of(payload):
    return [item["slug"] for item in payload["results"]]


def scores_of(payload):
    return [item["score"] for item in payload["results"]]


def run_edgeql(query, expect_single=False, **kwargs):
    """Execute EdgeQL against the live database with the Gel Python client."""
    import gel

    async def _run():
        client = gel.create_async_client()
        try:
            if expect_single:
                return await client.query_single(query, **kwargs)
            return await client.query(query, **kwargs)
        finally:
            await client.aclose()

    return asyncio.run(_run())


def gel_cli(args):
    gel_bin = shutil.which("gel")
    assert gel_bin is not None, "The gel CLI is not available in PATH."
    return subprocess.run(
        [gel_bin] + args,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=PROJECT_DIR,
    )


def run_cli(args):
    return subprocess.run(
        [sys.executable, SEARCH_CLI] + args,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=PROJECT_DIR,
    )


def whole_word_matches(record, term):
    pattern = re.compile(
        r"(?<![0-9A-Za-z])" + re.escape(term) + r"(?![0-9A-Za-z])", re.IGNORECASE
    )
    haystack = " ".join([record["title"], record["summary"], record["body"]])
    return bool(pattern.search(haystack))


def expected_slugs(records, term, status=None, tag=None):
    out = set()
    for record in records:
        if not whole_word_matches(record, term):
            continue
        if status is not None and record["status"] != status:
            continue
        if tag is not None and tag not in record["tags"]:
            continue
        out.add(record["slug"])
    return out


def assert_payload_shape(payload):
    assert set(payload) == TOP_LEVEL_KEYS, (
        f"The payload keys must be exactly {sorted(TOP_LEVEL_KEYS)}, got {sorted(payload)}."
    )
    assert isinstance(payload["results"], list), "'results' must be a list."
    for item in payload["results"]:
        assert set(item) == RESULT_KEYS, (
            f"Each result object must have exactly the keys {sorted(RESULT_KEYS)}, "
            f"got {sorted(item)}."
        )
        assert isinstance(item["score"], (int, float)) and not isinstance(
            item["score"], bool
        ), f"'score' must be a number, got {type(item['score'])!r} for {item['slug']!r}."
        assert item["score"] > 0, (
            f"Only matching articles may be returned, but {item['slug']!r} has "
            f"score {item['score']!r}."
        )
        assert item["status"] in STATUS_LABELS, (
            f"'status' must be one of {sorted(STATUS_LABELS)}, got {item['status']!r} "
            f"for {item['slug']!r}."
        )
        assert isinstance(item["tags"], list) and all(
            isinstance(tag, str) for tag in item["tags"]
        ), f"'tags' must be a list of strings, got {item['tags']!r} for {item['slug']!r}."
        assert item["tags"] == sorted(item["tags"]), (
            f"'tags' must be sorted ascending, got {item['tags']!r} for {item['slug']!r}."
        )


@pytest.fixture(scope="session")
def full_ledger(service):
    payload = search(service, "ledger", limit=100)
    return payload


# ---------------------------------------------------------------------------
# 1. migration history in sync
# ---------------------------------------------------------------------------


def test_migration_status_is_in_sync(gel_server):
    proc = gel_cli(["migration", "status"])
    combined = f"{proc.stdout}\n{proc.stderr}"
    assert proc.returncode == 0, (
        "'gel migration status' must report an in-sync database (exit code 0), got "
        f"{proc.returncode}.\n{combined}"
    )
    assert re.search(r"up to date", combined, re.IGNORECASE), (
        f"'gel migration status' did not report that the database is up to date:\n{combined}"
    )


def test_migration_files_are_committed_to_the_project(gel_server):
    assert os.path.isdir(MIGRATIONS_DIR), (
        f"Expected the migration history directory {MIGRATIONS_DIR} to exist."
    )
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert files, f"No .edgeql migration file found in {MIGRATIONS_DIR}."


# ---------------------------------------------------------------------------
# 2. schema shape
# ---------------------------------------------------------------------------


def test_article_status_enum_declaration(gel_server):
    rows = run_edgeql(
        """
        select schema::ScalarType { name, enum_values }
        filter .name = 'default::ArticleStatus'
        """
    )
    assert len(rows) == 1, (
        "Expected exactly one scalar type named 'default::ArticleStatus', found "
        f"{len(rows)}."
    )
    values = list(rows[0].enum_values or [])
    assert values == ["draft", "published", "archived"], (
        "default::ArticleStatus must be an enum with the values "
        f"['draft', 'published', 'archived'] in that order, got {values}."
    )


def test_article_type_pointers(gel_server):
    rows = run_edgeql(
        """
        select schema::ObjectType {
          pointers: { name, required, cardinality, target: { name } }
        } filter .name = 'default::Article'
        """
    )
    assert len(rows) == 1, "Object type 'default::Article' was not found in the schema."
    pointers = {ptr.name: ptr for ptr in rows[0].pointers}

    for name in ("slug", "title", "summary", "body"):
        assert name in pointers, f"default::Article is missing the property {name!r}."
        ptr = pointers[name]
        assert ptr.required, f"Property {name!r} of default::Article must be required."
        assert str(ptr.cardinality) == "One", (
            f"Property {name!r} must be single, got cardinality {str(ptr.cardinality)!r}."
        )
        assert ptr.target.name == "std::str", (
            f"Property {name!r} must be a str, got {ptr.target.name!r}."
        )

    assert "status" in pointers, "default::Article is missing the property 'status'."
    status_ptr = pointers["status"]
    assert status_ptr.required, "Property 'status' must be required."
    assert str(status_ptr.cardinality) == "One", "Property 'status' must be single."
    assert status_ptr.target.name == "default::ArticleStatus", (
        "Property 'status' must be typed by default::ArticleStatus, got "
        f"{status_ptr.target.name!r}."
    )

    assert "tags" in pointers, "default::Article is missing the property 'tags'."
    tags_ptr = pointers["tags"]
    assert str(tags_ptr.cardinality) == "Many", (
        "Property 'tags' must be a multi property, got cardinality "
        f"{str(tags_ptr.cardinality)!r}."
    )
    assert tags_ptr.target.name == "std::str", (
        f"Property 'tags' must hold str values, got {tags_ptr.target.name!r}."
    )


# ---------------------------------------------------------------------------
# 3. slug uniqueness enforced by the database
# ---------------------------------------------------------------------------


def test_duplicate_slug_is_rejected(gel_server):
    import gel

    before = run_edgeql("select count(Article)", expect_single=True)
    with pytest.raises(gel.errors.ConstraintViolationError):
        run_edgeql(
            """
            insert Article {
              slug := 'ledger-glossary',
              title := 'duplicate slug probe',
              summary := 'duplicate slug probe',
              body := 'duplicate slug probe',
              status := <ArticleStatus>'draft'
            }
            """
        )
    after = run_edgeql("select count(Article)", expect_single=True)
    assert after == before, (
        f"A rejected duplicate-slug insert must not change the article count "
        f"({before} -> {after})."
    )


# ---------------------------------------------------------------------------
# 4. corpus loaded exactly
# ---------------------------------------------------------------------------


def _assert_corpus_matches(seed_records):
    count = run_edgeql("select count(Article)", expect_single=True)
    assert count == EXPECTED_TOTAL_ARTICLES, (
        f"Expected exactly {EXPECTED_TOTAL_ARTICLES} Article objects, found {count}."
    )
    rows = run_edgeql(
        "select Article { slug, title, summary, body, status, tags }"
    )
    stored = {}
    for row in rows:
        assert row.slug not in stored, f"Slug {row.slug!r} is stored more than once."
        stored[row.slug] = row
    expected = {record["slug"]: record for record in seed_records}
    assert set(stored) == set(expected), (
        "The stored slugs do not match seed_data.json. Missing: "
        f"{sorted(set(expected) - set(stored))}; unexpected: "
        f"{sorted(set(stored) - set(expected))}."
    )
    for slug, record in expected.items():
        row = stored[slug]
        assert row.title == record["title"], f"title mismatch for {slug!r}."
        assert row.summary == record["summary"], f"summary mismatch for {slug!r}."
        assert row.body == record["body"], f"body mismatch for {slug!r}."
        assert str(row.status) == record["status"], (
            f"status mismatch for {slug!r}: stored {str(row.status)!r}, "
            f"expected {record['status']!r}."
        )
        assert sorted(str(tag) for tag in row.tags) == sorted(record["tags"]), (
            f"tags mismatch for {slug!r}: stored {sorted(str(t) for t in row.tags)}, "
            f"expected {sorted(record['tags'])}."
        )


def test_corpus_is_loaded_exactly(gel_server, seed_records):
    _assert_corpus_matches(seed_records)


# ---------------------------------------------------------------------------
# 5. loader idempotence
# ---------------------------------------------------------------------------


def test_seed_script_is_idempotent(gel_server, seed_records):
    assert os.path.isfile(SEED_SCRIPT), f"{SEED_SCRIPT} does not exist."
    proc = subprocess.run(
        [sys.executable, "seed.py"],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=PROJECT_DIR,
    )
    assert proc.returncode == 0, (
        "Re-running 'python3 seed.py' must succeed, got exit code "
        f"{proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    _assert_corpus_matches(seed_records)


# ---------------------------------------------------------------------------
# 6. weighted ranking: title > summary > body
# ---------------------------------------------------------------------------


def test_field_weighting_orders_title_above_summary_above_body(service):
    payload = search(service, "quokka")
    assert_payload_shape(payload)
    assert payload["total"] == 3, (
        f"Searching 'quokka' must match exactly 3 articles, got {payload['total']}."
    )
    assert slugs_of(payload) == [
        "quokka-cluster-provisioning",
        "fleet-preparation-window",
        "spare-hardware-notes",
    ], (
        "A title match must outrank a summary match, which must outrank a body match. "
        f"Got order {slugs_of(payload)}."
    )
    scores = scores_of(payload)
    assert scores[0] > scores[1] > scores[2], (
        f"Scores must strictly decrease from title to summary to body match, got {scores}."
    )


# ---------------------------------------------------------------------------
# 7. multi-term ranking
# ---------------------------------------------------------------------------


def test_multi_term_query_ranking(service):
    payload = search(service, "canary rollout")
    assert_payload_shape(payload)
    assert payload["total"] == 3, (
        f"Searching 'canary rollout' must match exactly 3 articles, got {payload['total']}."
    )
    assert slugs_of(payload) == [
        "canary-rollout-guide",
        "canary-alerting-thresholds",
        "release-freeze-policy",
    ], f"Unexpected multi-term ranking: {slugs_of(payload)}."
    scores = scores_of(payload)
    assert scores[0] > scores[1] > scores[2], (
        "An article matching both terms in the title must outrank one matching a single "
        f"term in the title, which must outrank a summary-only match, got {scores}."
    )


# ---------------------------------------------------------------------------
# 8. deterministic tie-break by slug
# ---------------------------------------------------------------------------


def test_identical_articles_are_tie_broken_by_slug(service):
    payload = search(service, "wombat")
    assert_payload_shape(payload)
    assert payload["total"] == 2, (
        f"Searching 'wombat' must match exactly 2 articles, got {payload['total']}."
    )
    assert slugs_of(payload) == ["tie-breaker-alpha", "tie-breaker-beta"], (
        "Two byte-identical articles must be ordered by slug ascending, got "
        f"{slugs_of(payload)}."
    )
    scores = scores_of(payload)
    assert abs(scores[0] - scores[1]) <= SCORE_EPS, (
        f"Two byte-identical articles must score equally, got {scores}."
    )


def test_ordering_is_score_desc_then_slug_asc(full_ledger):
    results = full_ledger["results"]
    assert len(results) > 1, "Expected several matches for the query 'ledger'."
    for previous, current in zip(results, results[1:]):
        assert previous["score"] >= current["score"] - SCORE_EPS, (
            "Results must be ordered by score descending, but "
            f"{previous['slug']!r} ({previous['score']}) precedes "
            f"{current['slug']!r} ({current['score']})."
        )
        if abs(previous["score"] - current["score"]) <= SCORE_EPS:
            assert previous["slug"] < current["slug"], (
                "Articles with equal scores must be ordered by slug ascending, got "
                f"{previous['slug']!r} before {current['slug']!r}."
            )


def test_search_is_deterministic_across_calls(service):
    first = search(service, "ledger", limit=100)
    second = search(service, "ledger", limit=100)
    assert slugs_of(first) == slugs_of(second), (
        "Repeated identical searches must return the same order, got "
        f"{slugs_of(first)} then {slugs_of(second)}."
    )


# ---------------------------------------------------------------------------
# 9. language-aware matching
# ---------------------------------------------------------------------------


def test_language_aware_stemming(service):
    payload = search(service, "kangaroo", limit=100)
    assert_payload_shape(payload)
    assert "kangaroos-load-harness" in slugs_of(payload), (
        "Searching 'kangaroo' must find the article whose text only contains "
        f"'Kangaroos', got {slugs_of(payload)}."
    )


# ---------------------------------------------------------------------------
# 10. filters combined with search
# ---------------------------------------------------------------------------


def test_unfiltered_ledger_match_set(full_ledger, seed_records):
    expected = expected_slugs(seed_records, "ledger")
    assert len(expected) == 18, (
        f"Sanity check on seed_data.json failed: expected 18 articles mentioning "
        f"'ledger', derived {len(expected)}."
    )
    assert set(slugs_of(full_ledger)) == expected, (
        "Searching 'ledger' must return every article mentioning the term. Missing: "
        f"{sorted(expected - set(slugs_of(full_ledger)))}; unexpected: "
        f"{sorted(set(slugs_of(full_ledger)) - expected)}."
    )
    assert full_ledger["total"] == len(expected), (
        f"'total' must be {len(expected)}, got {full_ledger['total']}."
    )


def test_status_filter(service, seed_records):
    expected = expected_slugs(seed_records, "ledger", status="draft")
    assert len(expected) == 5, (
        f"Sanity check failed: expected 5 draft articles mentioning 'ledger', "
        f"derived {len(expected)}."
    )
    payload = search(service, "ledger", status="draft", limit=100)
    assert_payload_shape(payload)
    assert set(slugs_of(payload)) == expected, (
        f"status='draft' must restrict the matches to {sorted(expected)}, got "
        f"{sorted(slugs_of(payload))}."
    )
    assert payload["total"] == len(expected), (
        f"'total' must reflect the filtered match count {len(expected)}, got "
        f"{payload['total']}."
    )
    assert all(item["status"] == "draft" for item in payload["results"]), (
        f"Every result must be a draft, got {[i['status'] for i in payload['results']]}."
    )


def test_tag_filter(service, seed_records):
    expected = expected_slugs(seed_records, "ledger", tag="security")
    assert len(expected) == 5, (
        f"Sanity check failed: expected 5 security-tagged articles mentioning 'ledger', "
        f"derived {len(expected)}."
    )
    payload = search(service, "ledger", tag="security", limit=100)
    assert_payload_shape(payload)
    assert set(slugs_of(payload)) == expected, (
        f"tag='security' must restrict the matches to {sorted(expected)}, got "
        f"{sorted(slugs_of(payload))}."
    )
    assert payload["total"] == len(expected), (
        f"'total' must be {len(expected)}, got {payload['total']}."
    )
    assert all("security" in item["tags"] for item in payload["results"]), (
        "Every result must carry the 'security' tag, got "
        f"{[i['tags'] for i in payload['results']]}."
    )


def test_status_and_tag_filters_combined(service, seed_records):
    expected = expected_slugs(seed_records, "ledger", status="draft", tag="security")
    assert expected == {"ledger-data-retention", "ledger-secrets-rotation"}, (
        f"Sanity check failed: derived {sorted(expected)} for the draft+security subset."
    )
    payload = search(service, "ledger", status="draft", tag="security", limit=100)
    assert_payload_shape(payload)
    assert set(slugs_of(payload)) == expected, (
        f"Both filters must apply together, expected {sorted(expected)}, got "
        f"{sorted(slugs_of(payload))}."
    )
    assert payload["total"] == 2, (
        f"'total' must be 2 for the draft+security subset, got {payload['total']}."
    )


def test_unknown_tag_yields_no_matches(service):
    payload = search(service, "ledger", tag="no-such-tag", limit=100)
    assert_payload_shape(payload)
    assert payload["total"] == 0, (
        f"An unknown tag must yield no matches, got total {payload['total']}."
    )
    assert payload["results"] == [], (
        f"An unknown tag must yield an empty results list, got {payload['results']}."
    )


# ---------------------------------------------------------------------------
# 11. pagination consistency
# ---------------------------------------------------------------------------


def test_pagination_reproduces_the_full_ordered_list(service, full_ledger):
    reference = slugs_of(full_ledger)
    assert len(reference) == 18, (
        f"Expected 18 matches for 'ledger' with limit=100, got {len(reference)}."
    )
    collected = []
    for offset in (0, 4, 8, 12, 16):
        page = search(service, "ledger", limit=4, offset=offset)
        assert_payload_shape(page)
        assert page["total"] == 18, (
            f"'total' must stay 18 regardless of pagination, got {page['total']} at "
            f"offset {offset}."
        )
        assert page["limit"] == 4, f"'limit' must be echoed as 4, got {page['limit']}."
        assert page["offset"] == offset, (
            f"'offset' must be echoed as {offset}, got {page['offset']}."
        )
        expected_slice = reference[offset : offset + 4]
        assert slugs_of(page) == expected_slice, (
            f"Page at offset {offset} must be {expected_slice}, got {slugs_of(page)}."
        )
        for position, item in enumerate(page["results"]):
            assert item["rank"] == offset + position + 1, (
                f"'rank' must be the 1-based global position: expected "
                f"{offset + position + 1} for {item['slug']!r}, got {item['rank']}."
            )
        collected.extend(slugs_of(page))
    assert collected == reference, (
        f"Paging through the result set must reproduce the full order exactly, got "
        f"{collected} instead of {reference}."
    )
    assert len(set(collected)) == len(collected), (
        f"Pagination must not repeat an article, got {collected}."
    )


def test_offset_beyond_last_match(service):
    for offset in (18, 999):
        payload = search(service, "ledger", limit=4, offset=offset)
        assert_payload_shape(payload)
        assert payload["results"] == [], (
            f"offset={offset} must yield an empty results list, got "
            f"{slugs_of(payload)}."
        )
        assert payload["total"] == 18, (
            f"offset={offset} must still report total 18, got {payload['total']}."
        )


def test_zero_limit(service):
    payload = search(service, "ledger", limit=0)
    assert_payload_shape(payload)
    assert payload["results"] == [], (
        f"limit=0 must yield an empty results list, got {slugs_of(payload)}."
    )
    assert payload["total"] == 18, (
        f"limit=0 must still report total 18, got {payload['total']}."
    )
    assert payload["limit"] == 0, f"'limit' must be echoed as 0, got {payload['limit']}."


# ---------------------------------------------------------------------------
# 12. empty and non-matching queries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "zzzunmatchableterm"])
def test_queries_without_matches(service, query):
    payload = search(service, query)
    assert_payload_shape(payload)
    assert payload["total"] == 0, (
        f"Query {query!r} must not match anything, got total {payload['total']}."
    )
    assert payload["results"] == [], (
        f"Query {query!r} must yield an empty results list, got {slugs_of(payload)}."
    )
    assert payload["query"] == query, (
        f"'query' must echo the original string {query!r}, got {payload['query']!r}."
    )


# ---------------------------------------------------------------------------
# 13. payload shape
# ---------------------------------------------------------------------------


def test_payload_shape_and_echoed_arguments(service):
    payload = search(service, "ledger", limit=3, offset=2)
    assert_payload_shape(payload)
    assert payload["query"] == "ledger", (
        f"'query' must echo the search string, got {payload['query']!r}."
    )
    assert payload["limit"] == 3, f"'limit' must be echoed as 3, got {payload['limit']}."
    assert payload["offset"] == 2, f"'offset' must be echoed as 2, got {payload['offset']}."
    assert len(payload["results"]) == 3, (
        f"limit=3 must return at most 3 results, got {len(payload['results'])}."
    )


# ---------------------------------------------------------------------------
# 14. highlighting
# ---------------------------------------------------------------------------


def test_highlight_wraps_matched_title_words(service):
    payload = search(service, "ledger api", limit=100)
    assert_payload_shape(payload)
    by_slug = {item["slug"]: item for item in payload["results"]}
    assert "ledger-api-pagination" in by_slug, (
        f"Searching 'ledger api' must match 'ledger-api-pagination', got "
        f"{sorted(by_slug)}."
    )
    assert by_slug["ledger-api-pagination"]["highlight"] == (
        "<b>Ledger</b> <b>API</b> pagination rules"
    ), (
        "Every whole-word query-term occurrence in the title must be wrapped in <b>...</b>, "
        f"got {by_slug['ledger-api-pagination']['highlight']!r}."
    )


def test_highlight_is_unchanged_title_when_nothing_matches(service):
    payload = search(service, "quokka", limit=100)
    by_slug = {item["slug"]: item for item in payload["results"]}
    assert "fleet-preparation-window" in by_slug, (
        f"Searching 'quokka' must match 'fleet-preparation-window', got {sorted(by_slug)}."
    )
    assert by_slug["fleet-preparation-window"]["highlight"] == "Fleet preparation window", (
        "When no query term occurs in the title, 'highlight' must be the unchanged title, "
        f"got {by_slug['fleet-preparation-window']['highlight']!r}."
    )


# ---------------------------------------------------------------------------
# 15. argument validation
# ---------------------------------------------------------------------------


def test_negative_limit_raises_value_error(service):
    with pytest.raises(ValueError):
        asyncio.run(service.search_articles("ledger", limit=-1))


def test_negative_offset_raises_value_error(service):
    with pytest.raises(ValueError):
        asyncio.run(service.search_articles("ledger", offset=-1))


def test_unknown_status_raises_value_error(service):
    with pytest.raises(ValueError):
        asyncio.run(service.search_articles("ledger", status="deleted"))


# ---------------------------------------------------------------------------
# 16. live database contents
# ---------------------------------------------------------------------------


def test_search_reflects_live_database(service, seed_records):
    insert = """
    insert Article {
      slug := <str>$slug,
      title := <str>$title,
      summary := <str>$summary,
      body := <str>$body,
      status := <ArticleStatus>'published',
      tags := {'ops'}
    }
    """
    try:
        run_edgeql(
            insert,
            slug="live-probe-title",
            title="Pangolin runtime probe",
            summary="A summary without the rare word.",
            body="A body without the rare word.",
        )
        run_edgeql(
            insert,
            slug="live-probe-body",
            title="Runtime probe",
            summary="A summary without the rare word.",
            body="This body mentions a pangolin exactly once.",
        )
        payload = search(service, "pangolin", limit=100)
        assert_payload_shape(payload)
        assert payload["total"] == 2, (
            "Articles inserted by another process must be searchable on the next call, "
            f"expected total 2, got {payload['total']} ({slugs_of(payload)})."
        )
        assert slugs_of(payload) == ["live-probe-title", "live-probe-body"], (
            "The article with the term in its title must rank first, got "
            f"{slugs_of(payload)}."
        )
    finally:
        run_edgeql(
            "delete Article filter .slug in {'live-probe-title', 'live-probe-body'}"
        )
    payload = search(service, "pangolin", limit=100)
    assert payload["total"] == 0, (
        f"Deleted articles must disappear from search results, got {slugs_of(payload)}."
    )
    count = run_edgeql("select count(Article)", expect_single=True)
    assert count == EXPECTED_TOTAL_ARTICLES, (
        f"After the cleanup the database must hold {EXPECTED_TOTAL_ARTICLES} articles, "
        f"found {count}."
    )
    _assert_corpus_matches(seed_records)


# ---------------------------------------------------------------------------
# 17-20. command line front end
# ---------------------------------------------------------------------------


def test_cli_exists(gel_server):
    assert os.path.isfile(SEARCH_CLI), f"{SEARCH_CLI} does not exist."


def test_cli_matches_the_service_payload(service):
    proc = run_cli(["--query", "canary rollout", "--limit", "2", "--offset", "1"])
    assert proc.returncode == 0, (
        "The CLI must exit 0 on a successful search, got "
        f"{proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"The CLI must print a single JSON object on stdout ({exc}): {proc.stdout!r}"
        )
    assert_payload_shape(payload)
    expected = search(service, "canary rollout", limit=2, offset=1)
    for key in ("query", "total", "limit", "offset"):
        assert payload[key] == expected[key], (
            f"The CLI payload must agree with search_articles on {key!r}: "
            f"CLI {payload[key]!r} vs service {expected[key]!r}."
        )
    cli_rows = [
        {key: item[key] for key in RESULT_KEYS if key != "score"}
        for item in payload["results"]
    ]
    service_rows = [
        {key: item[key] for key in RESULT_KEYS if key != "score"}
        for item in expected["results"]
    ]
    assert cli_rows == service_rows, (
        "The CLI must print the same result objects as search_articles.\n"
        f"CLI: {cli_rows}\nservice: {service_rows}"
    )
    for cli_item, service_item in zip(payload["results"], expected["results"]):
        assert abs(cli_item["score"] - service_item["score"]) <= 1e-6, (
            f"The CLI score for {cli_item['slug']!r} ({cli_item['score']}) must match the "
            f"score returned by search_articles ({service_item['score']})."
        )
    assert payload["total"] == 3, (
        f"Expected total 3 for 'canary rollout', got {payload['total']}."
    )
    assert [(item["rank"], item["slug"]) for item in payload["results"]] == [
        (2, "canary-alerting-thresholds"),
        (3, "release-freeze-policy"),
    ], f"Unexpected page content: {payload['results']}."


def test_cli_applies_both_filters(gel_server):
    proc = run_cli(
        ["--query", "ledger", "--status", "draft", "--tag", "security", "--limit", "10"]
    )
    assert proc.returncode == 0, (
        f"The CLI must exit 0, got {proc.returncode}.\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert_payload_shape(payload)
    assert payload["total"] == 2, (
        f"Expected total 2 for the draft+security subset, got {payload['total']}."
    )
    assert sorted(item["slug"] for item in payload["results"]) == [
        "ledger-data-retention",
        "ledger-secrets-rotation",
    ], f"Unexpected filtered CLI results: {[i['slug'] for i in payload['results']]}."


@pytest.mark.parametrize(
    "args",
    [
        ["--query", "ledger", "--status", "bogus"],
        ["--query", "ledger", "--limit", "-1"],
        ["--query", "ledger", "--offset", "-3"],
        ["--query", "ledger", "--limit", "abc"],
        [],
    ],
)
def test_cli_rejects_invalid_arguments(gel_server, args):
    proc = run_cli(args)
    assert proc.returncode == 2, (
        f"'search_cli.py {' '.join(args)}' must exit with status 2, got "
        f"{proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"Nothing may be printed on stdout for invalid arguments, got {proc.stdout!r}."
    )
    assert proc.stderr.strip() != "", (
        "An error message must be written to stderr for invalid arguments."
    )


def test_cli_empty_result(gel_server):
    proc = run_cli(["--query", "zzzunmatchableterm"])
    assert proc.returncode == 0, (
        f"A search without matches must still exit 0, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert_payload_shape(payload)
    assert payload["total"] == 0, (
        f"Expected total 0 for an unmatchable term, got {payload['total']}."
    )
    assert payload["results"] == [], (
        f"Expected an empty results list, got {payload['results']}."
    )
