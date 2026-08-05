import json
import os
import stat
import subprocess

import pytest

PROJECT_DIR = "/home/user/branchlab"
DBSCHEMA_DIR = os.path.join(PROJECT_DIR, "dbschema")
MIGRATIONS_DIR = os.path.join(DBSCHEMA_DIR, "migrations")
SCRIPT_PATH = os.path.join(PROJECT_DIR, "reconcile.sh")
REPORT_PATH = os.path.join(PROJECT_DIR, "reconcile-report.json")
FIXTURES_DIR = "/opt/task-fixtures"

EXPECTED_BRANCHES = ["feat_review", "main"]

# title -> word_count of the twelve seeded articles
SEEDED_ARTICLES = {
    "Analytical Engines": 1500,
    "Notes on Bernoulli": 900,
    "Compiler Origins": 1200,
    "Debugging Moths": 400,
    "Machine Intelligence": 2400,
    "Morphogenesis Notes": 1100,
    "Orbital Mechanics": 1000,
    "Trajectory Tables": 750,
    "Punch Card Rituals": 1199,
    "Enigma Sketches": 999,
    "Lunar Rendezvous": 1201,
    "Difference Engine Redux": 1600,
}

EXPECTED_REVIEW_STATE = {
    title: ("needs_review" if wc >= 1200 else "archived")
    for title, wc in SEEDED_ARTICLES.items()
}
EXPECTED_LONGFORM = {title for title, wc in SEEDED_ARTICLES.items() if wc >= 1000}


def _run(argv, timeout=900):
    """Run a command with the Gel project as the working directory."""
    env = dict(os.environ)
    if os.geteuid() == 0:
        # the project link and instance credentials were created for root at build time
        env["HOME"] = "/root"
    return subprocess.run(
        argv,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _query(query, branch=None):
    """One-shot EdgeQL query; never holds a connection open across tests."""
    argv = ["gel"]
    if branch:
        argv += ["-b", branch]
    argv += ["query", "-F", "json", query]
    proc = _run(argv)
    assert proc.returncode == 0, "gel query failed on branch {}: {}\n{}".format(
        branch or "<current>", query, proc.stderr
    )
    return json.loads(proc.stdout)


def _branch_names():
    return sorted(_query("select sys::Branch.name"))


def _migration_chain(branch):
    """Return migration names ordered from root to leaf, asserting a linear history."""
    rows = _query("select schema::Migration { name, parents: { name } }", branch=branch)
    parents = {r["name"]: [p["name"] for p in r["parents"]] for r in rows}
    roots = [n for n, ps in parents.items() if len(ps) == 0]
    assert len(roots) == 1, (
        "Branch {} must have exactly one root migration, found {}.".format(branch, roots)
    )
    for name, ps in parents.items():
        assert len(ps) <= 1, (
            "Migration {} on branch {} has {} parents; the history must be linear.".format(
                name, branch, len(ps)
            )
        )
    children = {}
    for name, ps in parents.items():
        for p in ps:
            children.setdefault(p, []).append(name)
    for parent, kids in children.items():
        assert len(kids) == 1, (
            "Migration {} on branch {} has {} children ({}); the history must be linear.".format(
                parent, branch, len(kids), kids
            )
        )
    chain = [roots[0]]
    while chain[-1] in children:
        chain.append(children[chain[-1]][0])
    assert len(chain) == len(parents), (
        "Migration history of branch {} is not a single chain: {} vs {}.".format(
            branch, chain, sorted(parents)
        )
    )
    return chain


def _articles(branch="main"):
    return _query(
        "select Article { id, title, word_count, review_state, "
        "author: { id, name, country }, tags: { label } } order by .title",
        branch=branch,
    )


def _load_fixture(name):
    with open(os.path.join(FIXTURES_DIR, name)) as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def gel_server():
    """Start the local Gel instance; every CLI/database test depends on this."""
    proc = _run(["gel-start"], timeout=900)
    assert proc.returncode == 0, "gel-start failed to bring up instance 'devinst': {}{}".format(
        proc.stdout, proc.stderr
    )
    return True


def test_branch_set_is_exactly_main_and_feat_review(gel_server):
    branches = _branch_names()
    assert branches == EXPECTED_BRANCHES, (
        "Expected exactly the branches {} to survive, found {}. `feat_tags` must be dropped "
        "and no temporary branch may be left behind.".format(EXPECTED_BRANCHES, branches)
    )


def test_active_branch_is_main(gel_server):
    current = _query("select sys::get_current_branch()")
    assert current == ["main"], (
        "The project's active branch must be `main`, found {}.".format(current)
    )


def test_main_has_tag_object_type(gel_server):
    rows = _query(
        "select schema::ObjectType { name } filter .name = 'default::Tag'", branch="main"
    )
    assert len(rows) == 1, "Object type `default::Tag` is missing on branch `main`."


def test_main_tag_label_is_required_str_and_exclusive(gel_server):
    rows = _query(
        "select schema::Property { name, required, target: { name }, constraints: { name } } "
        "filter .source.name = 'default::Tag' and .name = 'label'",
        branch="main",
    )
    assert len(rows) == 1, "Property `Tag.label` is missing on branch `main`."
    prop = rows[0]
    assert prop["required"] is True, "`Tag.label` must be a required property."
    assert prop["target"]["name"] == "std::str", (
        "`Tag.label` must be of type std::str, found {}.".format(prop["target"]["name"])
    )
    constraints = {c["name"] for c in prop["constraints"]}
    assert "std::exclusive" in constraints, (
        "`Tag.label` must carry an exclusive constraint, found {}.".format(sorted(constraints))
    )


def test_main_article_tags_link(gel_server):
    rows = _query(
        "select schema::Link { name, cardinality, target: { name } } "
        "filter .source.name = 'default::Article' and .name = 'tags'",
        branch="main",
    )
    assert len(rows) == 1, "Link `Article.tags` is missing on branch `main`."
    link = rows[0]
    assert link["cardinality"] == "Many", (
        "`Article.tags` must be a multi link, found cardinality {}.".format(link["cardinality"])
    )
    assert link["target"]["name"] == "default::Tag", (
        "`Article.tags` must point at `default::Tag`, found {}.".format(link["target"]["name"])
    )


def test_main_article_review_state_property(gel_server):
    rows = _query(
        "select schema::Property { name, required, cardinality, target: { name } } "
        "filter .source.name = 'default::Article' and .name = 'review_state'",
        branch="main",
    )
    assert len(rows) == 1, "Property `Article.review_state` is missing on branch `main`."
    prop = rows[0]
    assert prop["required"] is True, "`Article.review_state` must be required."
    assert prop["cardinality"] == "One", (
        "`Article.review_state` must be single-valued, found {}.".format(prop["cardinality"])
    )
    assert prop["target"]["name"] == "std::str", (
        "`Article.review_state` must be of type std::str, found {}.".format(prop["target"]["name"])
    )


def test_feat_review_carries_both_features(gel_server):
    tags_type = _query(
        "select schema::ObjectType { name } filter .name = 'default::Tag'", branch="feat_review"
    )
    assert len(tags_type) == 1, "Object type `default::Tag` is missing on branch `feat_review`."

    label = _query(
        "select schema::Property { required, target: { name }, constraints: { name } } "
        "filter .source.name = 'default::Tag' and .name = 'label'",
        branch="feat_review",
    )
    assert len(label) == 1 and label[0]["required"] is True, (
        "`Tag.label` must exist and be required on branch `feat_review`."
    )
    assert label[0]["target"]["name"] == "std::str", (
        "`Tag.label` must be of type std::str on branch `feat_review`."
    )
    assert "std::exclusive" in {c["name"] for c in label[0]["constraints"]}, (
        "`Tag.label` must be exclusive on branch `feat_review`."
    )

    link = _query(
        "select schema::Link { cardinality, target: { name } } "
        "filter .source.name = 'default::Article' and .name = 'tags'",
        branch="feat_review",
    )
    assert len(link) == 1, "Link `Article.tags` is missing on branch `feat_review`."
    assert link[0]["cardinality"] == "Many", (
        "`Article.tags` must be a multi link on branch `feat_review`."
    )
    assert link[0]["target"]["name"] == "default::Tag", (
        "`Article.tags` must point at `default::Tag` on branch `feat_review`."
    )

    prop = _query(
        "select schema::Property { required, cardinality, target: { name } } "
        "filter .source.name = 'default::Article' and .name = 'review_state'",
        branch="feat_review",
    )
    assert len(prop) == 1, "Property `Article.review_state` is missing on branch `feat_review`."
    assert prop[0]["required"] is True and prop[0]["cardinality"] == "One", (
        "`Article.review_state` must be a required single property on branch `feat_review`."
    )
    assert prop[0]["target"]["name"] == "std::str", (
        "`Article.review_state` must be of type std::str on branch `feat_review`."
    )


def test_main_migration_history_is_linear_with_three_migrations(gel_server):
    count = _query("select count(schema::Migration)", branch="main")
    assert count == [3], (
        "Branch `main` must end with exactly three migrations, found {}.".format(count)
    )
    chain = _migration_chain("main")
    assert len(chain) == 3, "Branch `main` must have a three-migration chain, found {}.".format(
        chain
    )
    with open(os.path.join(FIXTURES_DIR, "initial_migration.txt")) as fh:
        root = fh.read().strip()
    assert chain[0] == root, (
        "The pre-existing migration {} must remain the root of `main`'s history, found {}.".format(
            root, chain[0]
        )
    )


def test_each_feature_contributed_one_migration(gel_server):
    rows = _query("select schema::Migration { name, script }", branch="main")
    scripts = {r["name"]: r["script"] for r in rows}
    chain = _migration_chain("main")
    feature_migrations = chain[1:]
    tag_migrations = [n for n in feature_migrations if "default::Tag" in scripts[n]]
    review_migrations = [n for n in feature_migrations if "review_state" in scripts[n]]
    assert len(tag_migrations) == 1, (
        "Exactly one non-root migration on `main` must introduce `default::Tag`, found {}.".format(
            tag_migrations
        )
    )
    assert len(review_migrations) == 1, (
        "Exactly one non-root migration on `main` must introduce `review_state`, found {}.".format(
            review_migrations
        )
    )
    assert tag_migrations[0] != review_migrations[0], (
        "The tagging feature and the review feature must be delivered by two different "
        "migrations, but both were found in {}.".format(tag_migrations[0])
    )


def test_feat_review_history_matches_main(gel_server):
    count = _query("select count(schema::Migration)", branch="feat_review")
    assert count == [3], (
        "Branch `feat_review` must carry exactly three migrations, found {}.".format(count)
    )
    assert _migration_chain("feat_review") == _migration_chain("main"), (
        "Branch `feat_review` must carry the same linear migration history as `main`."
    )


def test_migration_status_in_sync_on_main(gel_server):
    proc = _run(["gel", "-b", "main", "migration", "status", "--quiet"])
    assert proc.returncode == 0, (
        "`gel migration status` on `main` must exit 0, got {}: {}{}".format(
            proc.returncode, proc.stdout, proc.stderr
        )
    )


def test_migration_status_in_sync_on_feat_review(gel_server):
    proc = _run(["gel", "-b", "feat_review", "migration", "status", "--quiet"])
    assert proc.returncode == 0, (
        "`gel migration status` on `feat_review` must exit 0, got {}: {}{}".format(
            proc.returncode, proc.stdout, proc.stderr
        )
    )


def test_migrations_directory_has_exactly_three_files():
    files = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".edgeql"))
    assert len(files) == 3, (
        "dbschema/migrations must contain exactly three .edgeql files, found {}.".format(files)
    )


def test_schema_source_file_exists():
    schema = os.path.join(DBSCHEMA_DIR, "default.gel")
    assert os.path.isfile(schema), "Schema source file {} is missing.".format(schema)


def test_authors_preserved_in_place(gel_server):
    rows = _query("select Author { id, name, country } order by .name", branch="main")
    assert len(rows) == 4, "Expected the four pre-existing authors, found {}.".format(len(rows))
    expected = _load_fixture("initial_authors.json")
    actual = {r["id"]: (r["name"], r["country"]) for r in rows}
    expected_map = {r["id"]: (r["name"], r["country"]) for r in expected}
    assert actual == expected_map, (
        "Author objects must be preserved in place (same ids, names and countries). "
        "Expected {}, found {}.".format(expected_map, actual)
    )


def test_articles_preserved_in_place(gel_server):
    rows = _articles("main")
    assert len(rows) == 12, "Expected the twelve pre-existing articles, found {}.".format(len(rows))
    expected = _load_fixture("initial_articles.json")
    expected_map = {
        r["id"]: (r["title"], r["word_count"], r["author"]["id"]) for r in expected
    }
    actual_map = {r["id"]: (r["title"], r["word_count"], r["author"]["id"]) for r in rows}
    assert actual_map == expected_map, (
        "Articles must survive in place with unchanged ids, titles, word counts and authors. "
        "Expected {}, found {}.".format(expected_map, actual_map)
    )
    titles = {r["title"]: r["word_count"] for r in rows}
    assert titles == SEEDED_ARTICLES, (
        "Article titles/word counts changed: expected {}, found {}.".format(
            SEEDED_ARTICLES, titles
        )
    )


def test_review_state_backfill_values(gel_server):
    rows = _articles("main")
    actual = {r["title"]: r["review_state"] for r in rows}
    assert actual == EXPECTED_REVIEW_STATE, (
        "review_state must be 'needs_review' for word_count >= 1200 and 'archived' otherwise. "
        "Expected {}, found {}.".format(EXPECTED_REVIEW_STATE, actual)
    )


def test_single_longform_tag_exists(gel_server):
    count = _query("select count(Tag)", branch="main")
    assert count == [1], "Exactly one Tag object must exist on `main`, found {}.".format(count)
    labels = _query("select Tag.label", branch="main")
    assert labels == ["longform"], (
        "The only Tag must have label 'longform', found {}.".format(labels)
    )


def test_longform_tagging_matches_word_count_boundary(gel_server):
    rows = _articles("main")
    tagged = {r["title"] for r in rows if any(t["label"] == "longform" for t in r["tags"])}
    untagged = {r["title"] for r in rows if not r["tags"]}
    assert tagged == EXPECTED_LONGFORM, (
        "Articles tagged 'longform' must be exactly those with word_count >= 1000. "
        "Expected {}, found {}.".format(sorted(EXPECTED_LONGFORM), sorted(tagged))
    )
    assert untagged == set(SEEDED_ARTICLES) - EXPECTED_LONGFORM, (
        "Articles with word_count < 1000 must have no tags at all, found untagged set {}.".format(
            sorted(untagged)
        )
    )


def test_reproduction_script_is_present_and_executable():
    assert os.path.isfile(SCRIPT_PATH), "Reproduction script {} is missing.".format(SCRIPT_PATH)
    mode = os.stat(SCRIPT_PATH).st_mode
    assert bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)), (
        "Reproduction script {} must be executable.".format(SCRIPT_PATH)
    )
    with open(SCRIPT_PATH) as fh:
        lines = fh.read().splitlines()
    assert lines and lines[0].startswith("#!"), (
        "Reproduction script must start with a `#!` shebang line."
    )
    comments = [ln for ln in lines[1:] if ln.strip().startswith("#")]
    assert len(comments) >= 3, (
        "Reproduction script must contain at least three documenting comment lines, found "
        "{}.".format(len(comments))
    )


def test_report_file_contents(gel_server):
    assert os.path.isfile(REPORT_PATH), "Report file {} is missing.".format(REPORT_PATH)
    with open(REPORT_PATH) as fh:
        report = json.load(fh)
    assert isinstance(report, dict), "The report file must contain a JSON object."
    expected_keys = {
        "main_branch",
        "feature_branches",
        "surviving_branches",
        "final_migration_count",
        "reproduction_script",
    }
    assert set(report) == expected_keys, (
        "The report must contain exactly the keys {}, found {}.".format(
            sorted(expected_keys), sorted(report)
        )
    )
    assert report["main_branch"] == "main", (
        "`main_branch` must be 'main', found {!r}.".format(report["main_branch"])
    )
    assert report["feature_branches"] == ["feat_review", "feat_tags"], (
        "`feature_branches` must be the two feature branch names sorted ascending, found "
        "{}.".format(report["feature_branches"])
    )
    branches = _branch_names()
    assert report["surviving_branches"] == branches, (
        "`surviving_branches` must list the branches that still exist, sorted ascending: {}, "
        "found {}.".format(branches, report["surviving_branches"])
    )
    assert report["surviving_branches"] == EXPECTED_BRANCHES, (
        "`surviving_branches` must be {}, found {}.".format(
            EXPECTED_BRANCHES, report["surviving_branches"]
        )
    )
    count = _query("select count(schema::Migration)", branch="main")[0]
    assert report["final_migration_count"] == count, (
        "`final_migration_count` must match `main`'s real migration count {}, found {}.".format(
            count, report["final_migration_count"]
        )
    )
    assert report["final_migration_count"] == 3, (
        "`final_migration_count` must be 3, found {}.".format(report["final_migration_count"])
    )
    assert report["reproduction_script"] == SCRIPT_PATH, (
        "`reproduction_script` must be {!r}, found {!r}.".format(
            SCRIPT_PATH, report["reproduction_script"]
        )
    )


def test_zz_reproduction_script_is_rerunnable_and_state_preserved(gel_server):
    """Run last: the script must be safe to re-run on the already reconciled project."""
    proc = _run(["bash", SCRIPT_PATH], timeout=600)
    assert proc.returncode == 0, (
        "Re-running {} must exit 0, got {}:\nSTDOUT:\n{}\nSTDERR:\n{}".format(
            SCRIPT_PATH, proc.returncode, proc.stdout[-4000:], proc.stderr[-4000:]
        )
    )

    branches = _branch_names()
    assert branches == EXPECTED_BRANCHES, (
        "After re-running the script the surviving branches must still be {}, found {}.".format(
            EXPECTED_BRANCHES, branches
        )
    )

    count = _query("select count(schema::Migration)", branch="main")
    assert count == [3], (
        "After re-running the script `main` must still have exactly three migrations, "
        "found {}.".format(count)
    )

    status = _run(["gel", "-b", "main", "migration", "status", "--quiet"])
    assert status.returncode == 0, (
        "After re-running the script `gel migration status` on `main` must still exit 0, "
        "got {}: {}{}".format(status.returncode, status.stdout, status.stderr)
    )

    rows = _articles("main")
    expected = _load_fixture("initial_articles.json")
    expected_map = {r["id"]: (r["title"], r["word_count"]) for r in expected}
    actual_map = {r["id"]: (r["title"], r["word_count"]) for r in rows}
    assert actual_map == expected_map, (
        "Re-running the script must not touch the pre-existing article rows. Expected {}, "
        "found {}.".format(expected_map, actual_map)
    )

    review = {r["title"]: r["review_state"] for r in rows}
    assert review == EXPECTED_REVIEW_STATE, (
        "Re-running the script must not change review_state values. Expected {}, found "
        "{}.".format(EXPECTED_REVIEW_STATE, review)
    )

    tagged = {r["title"] for r in rows if any(t["label"] == "longform" for t in r["tags"])}
    assert tagged == EXPECTED_LONGFORM, (
        "Re-running the script must not change the 'longform' tagging. Expected {}, found "
        "{}.".format(sorted(EXPECTED_LONGFORM), sorted(tagged))
    )
    assert _query("select count(Tag)", branch="main") == [1], (
        "Re-running the script must not create duplicate Tag objects."
    )
