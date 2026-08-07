import glob
import hashlib
import json
import os
import re
import subprocess

import pytest

PROJECT_DIR = "/home/user/kb-search"
CORPUS_FILE = os.path.join(PROJECT_DIR, "data", "corpus.json")
CHECKSUM_FILE = "/opt/kb-fixtures/corpus.sha256"

TRAIL_ORDER = [
    "alpine-trail-safety",
    "desert-trail-water",
    "forest-trail-markers",
    "trail-food-planning",
    "winter-trail-traction",
    "dog-friendly-outings",
    "mountain-bike-setup",
]
RESULT_KEYS = {"rank", "slug", "title", "section", "score"}
TOP_LEVEL_KEYS = {"query", "limit", "offset", "total", "results"}
USAGE_TEXT = "usage: search.sh QUERY [LIMIT] [OFFSET]"


def _env():
    env = os.environ.copy()
    env.setdefault("GEL_DSN", "gel://admin@127.0.0.1:5656/main")
    env.setdefault("GEL_CLIENT_TLS_SECURITY", "insecure")
    return env


def run(args, cwd=PROJECT_DIR, timeout=300):
    return subprocess.run(
        args,
        cwd=cwd,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def gel_query(edgeql):
    """Run an EdgeQL query with the gel CLI and return the parsed JSON result."""
    proc = run(["gel", "query", "-F", "json", edgeql])
    assert proc.returncode == 0, (
        f"gel query failed for {edgeql!r}: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    return json.loads(proc.stdout)


def search(*args):
    """Invoke the search CLI, assert the success contract and return the payload."""
    argv = ["bash", "scripts/search.sh"] + [str(a) for a in args]
    proc = run(argv)
    assert proc.returncode == 0, (
        f"{' '.join(argv)} exited with {proc.returncode}; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{' '.join(argv)} must print exactly one JSON object on stdout, "
            f"got {proc.stdout!r} ({exc})"
        )
    assert isinstance(payload, dict), (
        f"{' '.join(argv)} must print a single JSON object on stdout, got {type(payload).__name__}: "
        f"{proc.stdout!r}"
    )
    assert set(payload.keys()) == TOP_LEVEL_KEYS, (
        f"{' '.join(argv)} response keys must be exactly {sorted(TOP_LEVEL_KEYS)}, "
        f"got {sorted(payload.keys())}"
    )
    assert isinstance(payload["results"], list), (
        f"'results' must be a JSON array, got {payload['results']!r}"
    )
    for item in payload["results"]:
        assert isinstance(item, dict) and set(item.keys()) == RESULT_KEYS, (
            f"every result object must have exactly the keys {sorted(RESULT_KEYS)}, got {item!r}"
        )
    return payload


def slugs(payload):
    return [item["slug"] for item in payload["results"]]


@pytest.fixture(scope="session")
def gel_server():
    """Make sure the local Gel instance answers queries before any DB-backed check."""
    proc = run(["gel-start"], cwd="/", timeout=600)
    assert proc.returncode == 0, (
        f"'gel-start' failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    probe = run(["gel", "query", "-F", "json", "select 1"])
    assert probe.returncode == 0, (
        f"local Gel instance is not reachable: stdout={probe.stdout!r} stderr={probe.stderr!r}"
    )
    return True


def test_project_files_present():
    for relative in ("gel.toml", "dbschema/default.gel", "scripts/load.sh", "scripts/search.sh"):
        path = os.path.join(PROJECT_DIR, relative)
        assert os.path.isfile(path), f"Expected {path} to exist."
    migrations = sorted(glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql")))
    assert migrations, (
        f"Expected at least one migration file in {PROJECT_DIR}/dbschema/migrations/, found none."
    )


def test_corpus_file_unmodified():
    expected = open(CHECKSUM_FILE).read().split()[0].strip()
    actual = hashlib.sha256(open(CORPUS_FILE, "rb").read()).hexdigest()
    assert actual == expected, (
        f"{CORPUS_FILE} was modified: sha256 {actual} != recorded {expected}."
    )


def test_migration_status_reports_up_to_date(gel_server):
    proc = run(["gel", "migration", "status"])
    assert proc.returncode == 0, (
        f"'gel migration status' failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert "up to date" in combined, (
        f"Expected the migration status to report the database is up to date, got {combined!r}"
    )


def test_article_type_has_required_properties(gel_server):
    rows = gel_query(
        "select schema::ObjectType { "
        "properties: { name, required, cardinality, target: { name } } } "
        "filter .name = 'default::Article'"
    )
    assert len(rows) == 1, f"Expected exactly one 'default::Article' object type, got {rows!r}"
    props = {p["name"]: p for p in rows[0]["properties"]}
    expected = {
        "slug": "std::str",
        "title": "std::str",
        "summary": "std::str",
        "body": "std::str",
        "section": "std::str",
        "published": "std::bool",
    }
    for name, target in expected.items():
        assert name in props, f"Article is missing the property {name!r}; found {sorted(props)}"
        prop = props[name]
        assert prop["required"] is True, f"Article property {name!r} must be required."
        assert prop["cardinality"] == "One", (
            f"Article property {name!r} must be single, got cardinality {prop['cardinality']!r}"
        )
        assert prop["target"]["name"] == target, (
            f"Article property {name!r} must be of type {target}, got {prop['target']['name']}"
        )


def test_slug_uniqueness_is_enforced(gel_server):
    proc = run(
        [
            "gel",
            "query",
            "insert Article { slug := 'alpine-trail-safety', title := 'Duplicate Probe', "
            "summary := 'Duplicate probe.', body := 'Duplicate probe.', "
            "section := 'zz-probe', published := false }",
        ]
    )
    combined = proc.stdout + proc.stderr
    try:
        assert proc.returncode != 0, (
            "Inserting a second Article with an existing slug must fail, but it succeeded: "
            f"{combined!r}"
        )
        assert "ConstraintViolation" in combined, (
            f"Expected a constraint violation when reusing a slug, got {combined!r}"
        )
    finally:
        run(["gel", "query", "delete Article filter .section = 'zz-probe'"])


def test_weighted_fts_index_is_declared(gel_server):
    rows = gel_query(
        "select schema::ObjectType { indexes: { name, expr } } filter .name = 'default::Article'"
    )
    assert len(rows) == 1, f"Expected exactly one 'default::Article' object type, got {rows!r}"
    fts_indexes = [ix for ix in rows[0]["indexes"] if ix["name"].endswith("fts::index")]
    assert len(fts_indexes) == 1, (
        "Article must declare exactly one full-text search index, found: "
        f"{[ix['name'] for ix in rows[0]['indexes']]}"
    )
    expr = fts_indexes[0]["expr"] or ""
    chunks = expr.split("with_options")[1:]
    assert len(chunks) >= 3, (
        f"The full-text index must configure title, summary and body separately, got expr: {expr!r}"
    )
    for field, weight in (("title", "A"), ("summary", "B"), ("body", "C")):
        candidates = [chunk for chunk in chunks if f".{field}" in chunk]
        assert candidates, (
            f"The full-text index does not cover the {field!r} property; expr: {expr!r}"
        )
        chunk = candidates[0]
        assert re.search(r"Language\.eng\b|Language>\s*'eng'|'eng'", chunk), (
            f"The indexed {field!r} property must use the English language; expr: {expr!r}"
        )
        assert re.search(r"Weight\.%s\b|Weight>\s*'%s'" % (weight, weight), chunk), (
            f"The indexed {field!r} property must use weight category {weight}; expr: {expr!r}"
        )


def _corpus_record(slug):
    with open(CORPUS_FILE) as fh:
        corpus = json.load(fh)
    for record in corpus:
        if record["slug"] == slug:
            return record
    raise AssertionError(f"corpus record {slug!r} not found")


def _article_counts():
    return gel_query(
        "select { total := count(Article), "
        "published := count((select Article filter .published)) }"
    )[0]


def _stored_article(slug):
    rows = gel_query(
        "select Article { title, summary, body, section, published } "
        f"filter .slug = '{slug}'"
    )
    assert len(rows) == 1, f"Expected exactly one Article with slug {slug!r}, got {rows!r}"
    return rows[0]


def test_corpus_is_loaded(gel_server):
    counts = _article_counts()
    assert counts["total"] == 31, (
        f"Expected 31 Article objects loaded from the corpus, got {counts['total']}."
    )
    assert counts["published"] == 29, (
        f"Expected 29 published Article objects, got {counts['published']}."
    )
    drafts = gel_query(
        "select Article { slug, published } "
        "filter .slug in {'secret-kayak-clinic', 'zephyr-jacket-review'} "
        "order by .slug"
    )
    assert [d["slug"] for d in drafts] == ["secret-kayak-clinic", "zephyr-jacket-review"], (
        f"Both draft articles must be loaded, got {drafts!r}"
    )
    assert all(d["published"] is False for d in drafts), (
        f"Draft articles must keep published = false, got {drafts!r}"
    )
    record = _corpus_record("harbor-fog-signals")
    stored = _stored_article("harbor-fog-signals")
    for field in ("title", "summary", "body", "section"):
        assert stored[field] == record[field], (
            f"Article 'harbor-fog-signals' has {field}={stored[field]!r}, "
            f"expected the corpus value {record[field]!r}"
        )


def test_loader_is_idempotent(gel_server):
    before = _stored_article("harbor-fog-signals")
    proc = run(["bash", "scripts/load.sh"])
    assert proc.returncode == 0, (
        f"'bash scripts/load.sh' failed on re-run: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    counts = _article_counts()
    assert counts["total"] == 31, (
        f"Re-running the loader changed the article count to {counts['total']}, expected 31."
    )
    assert counts["published"] == 29, (
        f"Re-running the loader changed the published count to {counts['published']}, expected 29."
    )
    assert _stored_article("harbor-fog-signals") == before, (
        "Re-running the loader changed the stored values of 'harbor-fog-signals'."
    )


def test_default_paging_and_tie_break(gel_server):
    payload = search("trail")
    assert payload["query"] == "trail", f"'query' must echo the search text, got {payload['query']!r}"
    assert payload["limit"] == 5, f"Default limit must be 5, got {payload['limit']!r}"
    assert payload["offset"] == 0, f"Default offset must be 0, got {payload['offset']!r}"
    assert payload["total"] == 7, (
        f"Expected 7 matching articles for 'trail', got {payload['total']}"
    )
    assert [item["rank"] for item in payload["results"]] == [1, 2, 3, 4, 5], (
        f"Ranks of the first page must be 1..5, got {[i['rank'] for i in payload['results']]}"
    )
    assert slugs(payload) == TRAIL_ORDER[:5], (
        f"Expected the first page to be {TRAIL_ORDER[:5]} (ties broken by slug ascending), "
        f"got {slugs(payload)}"
    )
    titles = {item["slug"]: item["title"] for item in payload["results"]}
    assert titles["alpine-trail-safety"] == _corpus_record("alpine-trail-safety")["title"], (
        f"Result titles must come from the stored articles, got {titles!r}"
    )
    sections = {item["slug"]: item["section"] for item in payload["results"]}
    assert sections["trail-food-planning"] == _corpus_record("trail-food-planning")["section"], (
        f"Result sections must come from the stored articles, got {sections!r}"
    )


def test_title_beats_summary_beats_body(gel_server):
    payload = search("aurora", 10)
    assert payload["total"] == 3, f"Expected 3 matches for 'aurora', got {payload['total']}"
    assert slugs(payload) == [
        "northern-aurora-watch",
        "ridge-camera-settings",
        "sunrise-summit-plan",
    ], (
        "Expected title match before summary match before body match for 'aurora', "
        f"got {slugs(payload)}"
    )
    scores = [item["score"] for item in payload["results"]]
    assert scores[0] > scores[1] > scores[2], (
        f"Scores for 'aurora' must strictly decrease from title to summary to body, got {scores}"
    )


def test_title_match_outranks_body_match(gel_server):
    payload = search("harbor", 10)
    assert payload["total"] == 2, f"Expected 2 matches for 'harbor', got {payload['total']}"
    assert slugs(payload) == ["harbor-fog-signals", "night-navigation"], (
        f"Expected the title match to rank first for 'harbor', got {slugs(payload)}"
    )
    scores = [item["score"] for item in payload["results"]]
    assert scores[0] > scores[1], (
        f"The title match must score strictly higher than the body match, got {scores}"
    )


@pytest.mark.parametrize("term", ["trail", "aurora", "harbor"])
def test_scores_are_positive_rounded_and_non_increasing(gel_server, term):
    payload = search(term, 10)
    scores = [item["score"] for item in payload["results"]]
    assert scores, f"Expected at least one result for {term!r}"
    for score in scores:
        assert isinstance(score, (int, float)) and not isinstance(score, bool), (
            f"'score' must be a JSON number, got {score!r}"
        )
        assert score > 0, f"Only articles with a positive score may be returned, got {score!r}"
        assert abs(score - round(float(score), 4)) < 1e-12, (
            f"'score' must be rounded to 4 decimal places, got {score!r}"
        )
    assert scores == sorted(scores, reverse=True), (
        f"Results must be ordered by descending score, got {scores}"
    )


def test_unpublished_articles_are_never_returned(gel_server):
    hidden = search("zephyr", 10)
    assert hidden["total"] == 0 and hidden["results"] == [], (
        f"'zephyr' only appears in an unpublished article, expected no matches, got {hidden!r}"
    )
    payload = search("kayak", 10)
    assert payload["total"] == 3, f"Expected 3 published kayak matches, got {payload['total']}"
    assert slugs(payload) == [
        "beginner-kayak-basics",
        "coastal-kayak-tips",
        "river-paddle-routes",
    ], f"Unexpected ranking for 'kayak': {slugs(payload)}"
    assert "secret-kayak-clinic" not in slugs(payload), (
        "The unpublished article 'secret-kayak-clinic' must never appear in search results."
    )


def test_query_without_any_match(gel_server):
    payload = search("quantum")
    assert payload["total"] == 0, f"Expected no matches for 'quantum', got {payload['total']}"
    assert payload["results"] == [], f"Expected an empty results array, got {payload['results']!r}"


def test_pagination_pages_rebuild_the_full_ordering(gel_server):
    full = search("trail", 10)
    assert slugs(full) == TRAIL_ORDER, (
        f"Expected the full ranking for 'trail' to be {TRAIL_ORDER}, got {slugs(full)}"
    )
    collected = []
    for offset, expected_ranks in ((0, [1, 2]), (2, [3, 4]), (4, [5, 6]), (6, [7])):
        page = search("trail", 2, offset)
        assert page["total"] == 7, (
            f"'total' must stay 7 regardless of pagination, got {page['total']} at offset {offset}"
        )
        assert page["limit"] == 2 and page["offset"] == offset, (
            f"Expected limit=2 offset={offset} echoed back, got "
            f"limit={page['limit']!r} offset={page['offset']!r}"
        )
        assert [item["rank"] for item in page["results"]] == expected_ranks, (
            f"Expected ranks {expected_ranks} at offset {offset}, "
            f"got {[i['rank'] for i in page['results']]}"
        )
        collected.extend(slugs(page))
    assert collected == TRAIL_ORDER, (
        f"Concatenated pages must rebuild the full ranking {TRAIL_ORDER}, got {collected}"
    )


def test_offset_past_the_end_returns_empty_page(gel_server):
    payload = search("trail", 3, 20)
    assert payload["total"] == 7, (
        f"'total' must still be 7 for an out-of-range offset, got {payload['total']}"
    )
    assert payload["results"] == [], (
        f"An offset past the last match must return an empty page, got {payload['results']!r}"
    )
    assert payload["offset"] == 20 and payload["limit"] == 3, (
        f"Expected limit=3 offset=20 echoed back, got {payload['limit']!r}/{payload['offset']!r}"
    )


def test_search_reflects_live_database_state(gel_server):
    insert_title = (
        "insert Article { slug := 'zz-probe-title', title := 'Wombat Field Notes', "
        "summary := 'Neutral summary text.', body := 'Neutral body text.', "
        "section := 'zz-probe', published := true }"
    )
    insert_body = (
        "insert Article { slug := 'zz-probe-body', title := 'Neutral Title', "
        "summary := 'Neutral summary text.', body := 'A short note about a wombat.', "
        "section := 'zz-probe', published := true }"
    )
    try:
        gel_query(insert_title)
        gel_query(insert_body)
        payload = search("wombat", 10)
        assert payload["total"] == 2, (
            "Search must read the live database: expected the two probe articles to match "
            f"'wombat', got {payload!r}"
        )
        assert slugs(payload) == ["zz-probe-title", "zz-probe-body"], (
            f"Expected the title probe to outrank the body probe, got {slugs(payload)}"
        )
        gel_query("update Article filter .slug = 'zz-probe-body' set { published := false }")
        payload = search("wombat", 10)
        assert payload["total"] == 1 and slugs(payload) == ["zz-probe-title"], (
            f"Unpublishing the body probe must remove it from the results, got {payload!r}"
        )
    finally:
        run(["gel", "query", "delete Article filter .section = 'zz-probe'"])
    counts = _article_counts()
    assert counts["total"] == 31, (
        f"After removing the probe articles the corpus must hold 31 articles, got {counts['total']}"
    )


@pytest.mark.parametrize("term", ["TRAIL", "trails"])
def test_search_is_case_and_form_insensitive(gel_server, term):
    payload = search(term, 10)
    assert payload["total"] == 7, f"Expected 7 matches for {term!r}, got {payload['total']}"
    assert slugs(payload) == TRAIL_ORDER, (
        f"Expected {term!r} to rank exactly like 'trail' ({TRAIL_ORDER}), got {slugs(payload)}"
    )


def test_query_with_apostrophe_is_echoed_and_matched(gel_server):
    payload = search("dog's")
    assert payload["query"] == "dog's", (
        f"'query' must echo the search text verbatim, got {payload['query']!r}"
    )
    assert payload["total"] == 1, f"Expected 1 match for \"dog's\", got {payload['total']}"
    assert slugs(payload) == ["dog-friendly-outings"], (
        f"Expected only 'dog-friendly-outings' to match \"dog's\", got {slugs(payload)}"
    )


@pytest.mark.parametrize(
    "argv",
    [
        [],
        [""],
        ["trail", "abc"],
        ["trail", "0"],
        ["trail", "5", "-1"],
        ["a", "b", "c", "d"],
    ],
    ids=["no-args", "empty-query", "non-integer-limit", "zero-limit", "negative-offset", "too-many"],
)
def test_argument_errors(gel_server, argv):
    proc = run(["bash", "scripts/search.sh"] + argv)
    printable = " ".join(repr(a) for a in argv)
    assert proc.returncode == 2, (
        f"scripts/search.sh {printable} must exit with status 2, got {proc.returncode} "
        f"(stdout={proc.stdout!r} stderr={proc.stderr!r})"
    )
    assert proc.stdout.strip() == "", (
        f"scripts/search.sh {printable} must print nothing on stdout, got {proc.stdout!r}"
    )
    assert USAGE_TEXT in proc.stderr, (
        f"scripts/search.sh {printable} must print {USAGE_TEXT!r} on stderr, got {proc.stderr!r}"
    )
