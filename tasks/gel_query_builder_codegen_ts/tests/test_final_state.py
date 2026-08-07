import glob
import json
import os
import re
import subprocess

import pytest

PROJECT_DIR = "/home/user/catalog"
SRC_DIR = os.path.join(PROJECT_DIR, "src")
BUILDER_DIR = os.path.join(PROJECT_DIR, "dbschema", "edgeql-js")
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")

FORBIDDEN_SUBSTRINGS = [
    "@ts-ignore",
    "@ts-expect-error",
    "@ts-nocheck",
    ".query(",
    ".querySingle(",
    ".queryRequired(",
    ".queryRequiredSingle(",
    ".queryJSON(",
    ".querySingleJSON(",
    ".queryRequiredJSON(",
    ".queryRequiredSingleJSON(",
    ".execute(",
    ".executeSQL(",
]

EXTRA_ARTICLE_INSERT = (
    "insert Article { title := 'Zeta Extra', "
    "author := assert_exists((select Author filter .name = 'Radia Perlman')), "
    "minutes := 200, level := 'beginner', word_count := 1000 }"
)
DUPLICATE_TITLE_INSERT = (
    "insert Article { title := 'Git Internals', "
    "author := assert_exists((select Author filter .name = 'Radia Perlman')), "
    "minutes := 5, level := 'beginner', word_count := 100 }"
)

EXPECTED_AUTHORS_REPORT = [
    {
        "name": "Linus Torvalds",
        "articles": 1,
        "videos": 1,
        "total_minutes": 110,
        "avg_minutes": 55,
        "top_title": "Git Internals",
    },
    {
        "name": "Grace Hopper",
        "articles": 2,
        "videos": 1,
        "total_minutes": 105,
        "avg_minutes": 35,
        "top_title": "Cobol In Practice",
    },
    {
        "name": "Ada Lovelace",
        "articles": 1,
        "videos": 2,
        "total_minutes": 61,
        "avg_minutes": 20.33,
        "top_title": "Debugging Deep Dive",
    },
    {
        "name": "Rita O'Malley",
        "articles": 0,
        "videos": 1,
        "total_minutes": 40,
        "avg_minutes": 40,
        "top_title": "Type Systems Tour",
    },
    {
        "name": "Radia Perlman",
        "articles": 0,
        "videos": 0,
        "total_minutes": 0,
        "avg_minutes": 0,
        "top_title": None,
    },
]

EXPECTED_LEVELS_REPORT = [
    {
        "level": "advanced",
        "count": 3,
        "articles": 2,
        "videos": 1,
        "total_minutes": 145,
        "total_words": 16400,
        "captioned_videos": 1,
    },
    {
        "level": "beginner",
        "count": 3,
        "articles": 1,
        "videos": 2,
        "total_minutes": 71,
        "total_words": 2400,
        "captioned_videos": 1,
    },
    {
        "level": "intermediate",
        "count": 3,
        "articles": 1,
        "videos": 2,
        "total_minutes": 100,
        "total_words": 5200,
        "captioned_videos": 1,
    },
]


def run(args, cwd=PROJECT_DIR, timeout=600):
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )


def run_cli(args, timeout=600):
    return run(["npx", "tsx", "src/cli.ts", *args], timeout=timeout)


def gel_query(query, timeout=600):
    return run(["gel", "query", "-F", "json", query], timeout=timeout)


def parse_stdout_json(proc, label):
    assert proc.returncode == 0, (
        f"{label} exited with {proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{label} must print exactly one JSON document to stdout, got: {proc.stdout!r} "
            f"({exc})"
        )


def assert_rows_equal(actual, expected, label):
    assert isinstance(actual, list), f"{label} must print a JSON array, got: {actual!r}"
    assert len(actual) == len(expected), (
        f"{label} must contain {len(expected)} entries, got {len(actual)}: {actual!r}"
    )
    for index, (got, want) in enumerate(zip(actual, expected)):
        assert isinstance(got, dict), f"{label}[{index}] must be a JSON object, got: {got!r}"
        assert set(got.keys()) == set(want.keys()), (
            f"{label}[{index}] must have exactly the keys {sorted(want.keys())}, "
            f"got {sorted(got.keys())}"
        )
        for key, want_value in want.items():
            got_value = got[key]
            if isinstance(want_value, (int, float)) and not isinstance(want_value, bool):
                assert got_value == pytest.approx(want_value, abs=1e-6), (
                    f"{label}[{index}]['{key}'] should be {want_value}, got {got_value!r}"
                )
            else:
                assert got_value == want_value, (
                    f"{label}[{index}]['{key}'] should be {want_value!r}, got {got_value!r}"
                )


@pytest.fixture(scope="session")
def server():
    proc = run(["gel-start.sh"], cwd="/tmp", timeout=600)
    assert proc.returncode == 0, (
        f"Could not start the local Gel server: {proc.stdout}\n{proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def loaded(server):
    proc = run_cli(["load"])
    assert proc.returncode == 0, (
        f"'npx tsx src/cli.ts load' failed: {proc.stdout}\n{proc.stderr}"
    )
    return True


def test_query_builder_artifacts_exist():
    assert os.path.isdir(BUILDER_DIR), (
        f"Expected the generated query builder directory {BUILDER_DIR} to exist."
    )
    for pattern in ["index.*", "__spec__.*", os.path.join("modules", "default.*")]:
        matches = glob.glob(os.path.join(BUILDER_DIR, pattern))
        assert matches, (
            f"Expected a generated query builder file matching {pattern} inside {BUILDER_DIR}."
        )


def test_query_builder_reflects_final_schema():
    blob = ""
    for path in glob.glob(os.path.join(BUILDER_DIR, "**", "*.*"), recursive=True):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                blob += handle.read()
        except UnicodeDecodeError:
            continue
    for needle in [
        "Article",
        "Video",
        "Resource",
        "has_captions",
        "word_count",
        "resource_count",
    ]:
        assert needle in blob, (
            f"The generated query builder in {BUILDER_DIR} does not mention '{needle}'; "
            "it must be regenerated from the final schema."
        )


def test_new_migration_recorded(server):
    migrations = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    assert len(migrations) >= 2, (
        f"Expected at least two migration files in {MIGRATIONS_DIR}, found: {migrations}"
    )


def test_migration_status_in_sync(server):
    proc = run(["gel", "migration", "status"])
    assert proc.returncode == 0, (
        f"'gel migration status' reports the database is not up to date: "
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_resource_hierarchy_in_schema(server):
    proc = gel_query(
        "select schema::ObjectType { name, abstract, ancestors: { name }, "
        "pointers: { name, required, cardinality, expr, target: { name } } } "
        "filter .name in {'default::Resource', 'default::Article', 'default::Video'}"
    )
    assert proc.returncode == 0, f"Schema introspection failed: {proc.stderr}"
    types = {entry["name"]: entry for entry in json.loads(proc.stdout)}
    for name in ["default::Resource", "default::Article", "default::Video"]:
        assert name in types, f"Object type {name} is missing from the schema."

    assert types["default::Resource"]["abstract"] is True, (
        "default::Resource must be declared as an abstract object type."
    )
    for name in ["default::Article", "default::Video"]:
        ancestors = {a["name"] for a in types[name]["ancestors"]}
        assert "default::Resource" in ancestors, (
            f"{name} must extend default::Resource, ancestors were: {sorted(ancestors)}"
        )

    resource_pointers = {p["name"]: p for p in types["default::Resource"]["pointers"]}
    expected_resource = {
        "title": "std::str",
        "minutes": "std::int64",
        "level": "std::str",
        "author": "default::Author",
    }
    for pointer, target in expected_resource.items():
        assert pointer in resource_pointers, (
            f"default::Resource must declare '{pointer}', found: {sorted(resource_pointers)}"
        )
        info = resource_pointers[pointer]
        assert info["required"] is True, f"default::Resource.{pointer} must be required."
        assert info["cardinality"] == "One", (
            f"default::Resource.{pointer} must be single, got cardinality {info['cardinality']}."
        )
        assert info["target"]["name"] == target, (
            f"default::Resource.{pointer} must target {target}, got {info['target']['name']}."
        )

    article_pointers = {p["name"]: p for p in types["default::Article"]["pointers"]}
    assert "word_count" in article_pointers, "default::Article must declare 'word_count'."
    assert article_pointers["word_count"]["required"] is True, (
        "default::Article.word_count must be required."
    )
    assert article_pointers["word_count"]["target"]["name"] == "std::int64", (
        "default::Article.word_count must be an int64."
    )

    video_pointers = {p["name"]: p for p in types["default::Video"]["pointers"]}
    assert "has_captions" in video_pointers, "default::Video must declare 'has_captions'."
    assert video_pointers["has_captions"]["required"] is True, (
        "default::Video.has_captions must be required."
    )
    assert video_pointers["has_captions"]["target"]["name"] == "std::bool", (
        "default::Video.has_captions must be a bool."
    )


def test_author_computeds_in_schema(server):
    proc = gel_query(
        "select schema::ObjectType { name, "
        "pointers: { name, cardinality, expr, target: { name } } } "
        "filter .name = 'default::Author'"
    )
    assert proc.returncode == 0, f"Schema introspection failed: {proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload, "default::Author is missing from the schema."
    pointers = {p["name"]: p for p in payload[0]["pointers"]}

    assert "resources" in pointers, (
        f"default::Author must expose a computed 'resources', found: {sorted(pointers)}"
    )
    resources = pointers["resources"]
    assert resources["expr"], "default::Author.resources must be a computed pointer."
    assert resources["cardinality"] == "Many", (
        f"default::Author.resources must be multi, got {resources['cardinality']}."
    )
    assert resources["target"]["name"] == "default::Resource", (
        f"default::Author.resources must target default::Resource, "
        f"got {resources['target']['name']}."
    )

    assert "resource_count" in pointers, (
        f"default::Author must expose a computed 'resource_count', found: {sorted(pointers)}"
    )
    resource_count = pointers["resource_count"]
    assert resource_count["expr"], "default::Author.resource_count must be a computed pointer."
    assert resource_count["target"]["name"] == "std::int64", (
        f"default::Author.resource_count must be an int64, "
        f"got {resource_count['target']['name']}."
    )


def test_load_is_idempotent(loaded):
    first = run_cli(["load"])
    payload_first = parse_stdout_json(first, "'npx tsx src/cli.ts load'")
    second = run_cli(["load"])
    payload_second = parse_stdout_json(second, "'npx tsx src/cli.ts load' (second run)")

    expected = {"articles": 4, "videos": 5, "total": 9}
    for payload, label in ((payload_first, "first"), (payload_second, "second")):
        assert isinstance(payload, dict), f"The {label} load run must print a JSON object."
        assert set(payload.keys()) == set(expected.keys()), (
            f"The {label} load run must print exactly the keys {sorted(expected)}, "
            f"got {sorted(payload.keys())}"
        )
        assert payload == expected, (
            f"The {label} load run should print {expected}, got {payload}"
        )

    proc = gel_query("select count(Resource)")
    assert proc.returncode == 0, f"Counting resources failed: {proc.stderr}"
    assert json.loads(proc.stdout) == [9], (
        f"Repeated loads must not duplicate resources, got: {proc.stdout}"
    )


def test_title_exclusive_across_subtypes(loaded):
    proc = run(["gel", "query", DUPLICATE_TITLE_INSERT])
    assert proc.returncode != 0, (
        "Inserting an Article with the title of an existing Video must violate the "
        "exclusivity constraint on Resource.title."
    )
    combined = (proc.stdout + proc.stderr).lower()
    assert "exclusiv" in combined or "constraint" in combined, (
        f"Expected an exclusivity constraint error, got: {proc.stdout}\n{proc.stderr}"
    )


def test_report_authors(loaded):
    proc = run_cli(["report", "authors"])
    rows = parse_stdout_json(proc, "'npx tsx src/cli.ts report authors'")
    assert_rows_equal(rows, EXPECTED_AUTHORS_REPORT, "report authors")


def test_report_levels(loaded):
    proc = run_cli(["report", "levels"])
    rows = parse_stdout_json(proc, "'npx tsx src/cli.ts report levels'")
    assert_rows_equal(rows, EXPECTED_LEVELS_REPORT, "report levels")


def test_report_author_with_resources(loaded):
    proc = run_cli(["report", "author", "--name", "Grace Hopper"])
    row = parse_stdout_json(proc, "'report author --name \"Grace Hopper\"'")
    expected = {
        "name": "Grace Hopper",
        "country": "USA",
        "resource_count": 3,
        "total_minutes": 105,
        "titles": ["Bug Hunting", "Cobol In Practice", "Compilers Explained"],
    }
    assert set(row.keys()) == set(expected.keys()), (
        f"'report author' must print exactly the keys {sorted(expected)}, got {sorted(row.keys())}"
    )
    assert row == expected, f"'report author --name \"Grace Hopper\"' should print {expected}, got {row}"


def test_report_author_name_with_apostrophe(loaded):
    proc = run_cli(["report", "author", "--name", "Rita O'Malley"])
    row = parse_stdout_json(proc, "'report author --name \"Rita O'Malley\"'")
    expected = {
        "name": "Rita O'Malley",
        "country": "Ireland",
        "resource_count": 1,
        "total_minutes": 40,
        "titles": ["Type Systems Tour"],
    }
    assert row == expected, (
        f"'report author' must handle apostrophes in names; expected {expected}, got {row}"
    )


def test_report_author_without_resources(loaded):
    proc = run_cli(["report", "author", "--name", "Radia Perlman"])
    row = parse_stdout_json(proc, "'report author --name \"Radia Perlman\"'")
    expected = {
        "name": "Radia Perlman",
        "country": "USA",
        "resource_count": 0,
        "total_minutes": 0,
        "titles": [],
    }
    assert row == expected, (
        f"An author with no resources should report {expected}, got {row}"
    )


def test_report_author_not_found(loaded):
    proc = run_cli(["report", "author", "--name", "Nobody Here"])
    assert proc.returncode == 3, (
        f"An unknown author must exit with code 3, got {proc.returncode}: "
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"An unknown author must print nothing on stdout, got: {proc.stdout!r}"
    )
    assert "author not found: Nobody Here" in proc.stderr, (
        f"Expected 'author not found: Nobody Here' on stderr, got: {proc.stderr!r}"
    )


def test_report_author_hostile_name(loaded):
    proc = run_cli(["report", "author", "--name", "x'; drop"])
    assert proc.returncode == 3, (
        "A name containing EdgeQL punctuation must be treated as an unknown author "
        f"(exit 3), got {proc.returncode}: {proc.stdout}\n{proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"An unknown author must print nothing on stdout, got: {proc.stdout!r}"
    )


def test_usage_errors(loaded):
    unknown = run_cli(["frobnicate"])
    assert unknown.returncode == 2, (
        f"An unrecognised subcommand must exit with code 2, got {unknown.returncode}: "
        f"{unknown.stdout}\n{unknown.stderr}"
    )
    assert unknown.stdout.strip() == "", (
        f"An unrecognised subcommand must print nothing on stdout, got: {unknown.stdout!r}"
    )
    assert unknown.stderr.strip() != "", (
        "An unrecognised subcommand must write a message to stderr."
    )

    missing_name = run_cli(["report", "author"])
    assert missing_name.returncode == 2, (
        f"'report author' without --name must exit with code 2, got {missing_name.returncode}: "
        f"{missing_name.stdout}\n{missing_name.stderr}"
    )
    assert missing_name.stdout.strip() == "", (
        f"'report author' without --name must print nothing on stdout, got: {missing_name.stdout!r}"
    )
    assert missing_name.stderr.strip() != "", (
        "'report author' without --name must write a message to stderr."
    )


def test_reports_reflect_live_database(loaded):
    inserted = run(["gel", "query", EXTRA_ARTICLE_INSERT])
    assert inserted.returncode == 0, (
        f"Could not insert the extra article fixture: {inserted.stdout}\n{inserted.stderr}"
    )
    try:
        authors = parse_stdout_json(
            run_cli(["report", "authors"]), "'report authors' (with extra article)"
        )
        assert authors[0]["name"] == "Radia Perlman", (
            "After inserting a 200 minute article for Radia Perlman she must sort first, "
            f"got: {[row['name'] for row in authors]}"
        )
        assert authors[0]["articles"] == 1, (
            f"Radia Perlman should now report 1 article, got {authors[0]['articles']}"
        )
        assert authors[0]["total_minutes"] == 200, (
            f"Radia Perlman should now report 200 total minutes, got {authors[0]['total_minutes']}"
        )
        assert authors[0]["avg_minutes"] == pytest.approx(200, abs=1e-6), (
            f"Radia Perlman should now report 200 average minutes, got {authors[0]['avg_minutes']}"
        )
        assert authors[0]["top_title"] == "Zeta Extra", (
            f"Radia Perlman's longest resource should be 'Zeta Extra', got {authors[0]['top_title']}"
        )

        levels = parse_stdout_json(
            run_cli(["report", "levels"]), "'report levels' (with extra article)"
        )
        beginner = [row for row in levels if row["level"] == "beginner"]
        assert beginner, f"Expected a 'beginner' bucket in the levels report, got: {levels}"
        assert beginner[0] == {
            "level": "beginner",
            "count": 4,
            "articles": 2,
            "videos": 2,
            "total_minutes": 271,
            "total_words": 3400,
            "captioned_videos": 1,
        }, f"Unexpected 'beginner' bucket after inserting the extra article: {beginner[0]}"
    finally:
        cleanup = run(["gel", "query", "delete Article filter .title = 'Zeta Extra'"])
        assert cleanup.returncode == 0, (
            f"Could not remove the extra article fixture: {cleanup.stdout}\n{cleanup.stderr}"
        )

    restored = parse_stdout_json(run_cli(["report", "authors"]), "'report authors' (restored)")
    assert_rows_equal(restored, EXPECTED_AUTHORS_REPORT, "report authors (restored)")


def test_strict_typecheck_passes():
    proc = run(["npx", "tsc", "--noEmit", "-p", "tsconfig.json"])
    assert proc.returncode == 0, (
        f"'npx tsc --noEmit -p tsconfig.json' must exit 0, got {proc.returncode}:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


def test_tsconfig_still_strict():
    with open(os.path.join(PROJECT_DIR, "tsconfig.json"), encoding="utf-8") as handle:
        tsconfig = json.load(handle)
    assert tsconfig.get("compilerOptions", {}).get("strict") is True, (
        "tsconfig.json must keep compilerOptions.strict set to true."
    )


def test_source_has_no_escape_hatches_or_raw_edgeql():
    sources = sorted(glob.glob(os.path.join(SRC_DIR, "**", "*.ts"), recursive=True))
    assert sources, f"Expected at least one TypeScript source file under {SRC_DIR}."
    any_pattern = re.compile(r"\bany\b", re.IGNORECASE)
    for path in sources:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        match = any_pattern.search(content)
        assert match is None, (
            f"{path} contains the forbidden word 'any' at offset {match.start() if match else -1}."
        )
        for needle in FORBIDDEN_SUBSTRINGS:
            assert needle not in content, (
                f"{path} contains the forbidden text '{needle}'; the CLI must reach the "
                "database only through the generated query builder."
            )


def test_source_uses_generated_query_builder():
    sources = sorted(glob.glob(os.path.join(SRC_DIR, "**", "*.ts"), recursive=True))
    blob = ""
    for path in sources:
        with open(path, encoding="utf-8") as handle:
            blob += handle.read()
    assert "dbschema/edgeql-js" in blob, (
        f"No file under {SRC_DIR} imports the generated query builder from dbschema/edgeql-js."
    )
