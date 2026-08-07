"""Final-state verification for the Gel dual-protocol reconciliation task."""

import copy
import glob
import json
import os
import re
import shutil
import subprocess
from decimal import ROUND_FLOOR, Decimal  # noqa: F401

import pytest

PROJECT_DIR = "/home/user/project"
SEED_PATH = os.path.join(PROJECT_DIR, "data", "seed.json")
ENTRYPOINT = "dualview.py"
GEL_START = "/usr/local/bin/gel-start"
REPORT_DIR = "/tmp/recon-verify"

ROW_FIELDS = (
    "album_slug",
    "album_year",
    "contributor_count",
    "duration_ms",
    "payout_micros",
    "share_bp_total",
    "tag_count",
    "title_length",
)

METRIC_IDS = (
    "count.artists_without_contributions",
    "count.catalog::Album",
    "count.catalog::Artist",
    "count.catalog::Artist.aliases",
    "count.catalog::Asset",
    "count.catalog::Track",
    "count.catalog::Track.contributors",
    "count.catalog::Track.tags",
    "count.distinct.catalog::Track.album",
    "count.distinct.catalog::Track.contributors@target",
    "count.tracks_without_tags",
    "max.catalog::Album.year",
    "min.catalog::Album.year",
    "sum.catalog::Track.contributors@share_bp",
    "sum.catalog::Track.duration_ms",
    "sum.catalog::Track.payout_micros",
    "sum.catalog::Track.title_length",
)

REPORT_TOP_KEYS = {
    "schema_version",
    "agrees",
    "mismatch_count",
    "drift_count",
    "metrics",
    "rows",
    "mismatches",
    "drift",
}

VERIFY_ALBUM_SLUG = "zz-verify-album"
VERIFY_TRACK_SLUG = "zz-verify-track"
VERIFY_TRACK_TITLE = "Zz Verify"
VERIFY_TRACK_DURATION = 123457
VERIFY_TRACK_PAYOUT = 30864
VERIFY_TRACK_TITLE_LEN = 9
VERIFY_ALBUM_YEAR = 2024
VERIFY_SHARE_BP = 4321
VERIFY_TAGS = ("zz-tag-a", "zz-tag-b")
VERIFY_SQL_TAG = "zz-sql-tag"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def payout_of(duration_ms, royalty_rate):
    product = Decimal(int(duration_ms)) * Decimal(str(royalty_rate))
    return int(product.to_integral_value(rounding=ROUND_FLOOR))


def read_seed():
    with open(SEED_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def expected_from_seed(seed):
    artists = seed["artists"]
    albums = seed["albums"]
    tracks = seed["tracks"]
    album_by_slug = {a["slug"]: a for a in albums}

    rows = {}
    for track in tracks:
        rows[track["slug"]] = {
            "album_slug": track["album"],
            "album_year": album_by_slug[track["album"]]["year"],
            "contributor_count": len(track["contributors"]),
            "duration_ms": track["duration_ms"],
            "payout_micros": payout_of(track["duration_ms"], track["royalty_rate"]),
            "share_bp_total": sum(c["share_bp"] for c in track["contributors"]),
            "tag_count": len(track["tags"]),
            "title_length": len(track["title"]),
        }

    contributing = {c["artist"] for t in tracks for c in t["contributors"]}
    metrics = {
        "count.artists_without_contributions": sum(
            1 for a in artists if a["handle"] not in contributing
        ),
        "count.catalog::Album": len(albums),
        "count.catalog::Artist": len(artists),
        "count.catalog::Artist.aliases": sum(len(a["aliases"]) for a in artists),
        "count.catalog::Asset": len(albums) + len(tracks),
        "count.catalog::Track": len(tracks),
        "count.catalog::Track.contributors": sum(len(t["contributors"]) for t in tracks),
        "count.catalog::Track.tags": sum(len(t["tags"]) for t in tracks),
        "count.distinct.catalog::Track.album": len({t["album"] for t in tracks}),
        "count.distinct.catalog::Track.contributors@target": len(contributing),
        "count.tracks_without_tags": sum(1 for t in tracks if not t["tags"]),
        "max.catalog::Album.year": max(a["year"] for a in albums),
        "min.catalog::Album.year": min(a["year"] for a in albums),
        "sum.catalog::Track.contributors@share_bp": sum(
            c["share_bp"] for t in tracks for c in t["contributors"]
        ),
        "sum.catalog::Track.duration_ms": sum(t["duration_ms"] for t in tracks),
        "sum.catalog::Track.payout_micros": sum(r["payout_micros"] for r in rows.values()),
        "sum.catalog::Track.title_length": sum(len(t["title"]) for t in tracks),
    }
    return {"rows": rows, "metrics": metrics}


def run_tool(args, extra_env=None, timeout=600):
    env = os.environ.copy()
    if extra_env:
        for key, value in extra_env.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    return subprocess.run(
        ["python3", ENTRYPOINT, *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def parse_single_json_object(text, what):
    stripped = (text or "").strip()
    assert stripped, f"Expected a single JSON object on {what}, got nothing."
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{what} is not exactly one JSON object: {stripped!r} ({exc})"
        ) from exc
    assert isinstance(parsed, dict), f"{what} must be a JSON object, got {type(parsed)}."
    return parsed


def load_report(path):
    assert os.path.isfile(path), f"Report file {path} was not written."
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def metrics_map(report):
    return {m["id"]: m for m in report["metrics"]}


def rows_map(report):
    return {r["slug"]: r for r in report["rows"]}


def sort_key(entry):
    return (entry["scope"], entry["id"], entry["field"])


def reconcile(name, baseline=None):
    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, name)
    args = ["reconcile", "--output", out_path]
    if baseline is not None:
        args += ["--baseline", baseline]
    proc = run_tool(args)
    return proc, out_path


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _chdir_to_project():
    previous = os.getcwd()
    os.chdir(PROJECT_DIR)
    try:
        yield
    finally:
        os.chdir(previous)


@pytest.fixture(scope="session")
def server():
    proc = subprocess.run([GEL_START], capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, (
        f"{GEL_START} failed with exit code {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return True


@pytest.fixture(scope="session")
def gel_client(server):
    import gel

    client = gel.create_client()
    try:
        client.ensure_connected()
    except AttributeError:
        client.query_single("select 1")
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="session")
def sql_connect(server):
    import psycopg

    def _connect():
        return psycopg.connect(
            host=os.environ["GEL_SQL_HOST"],
            port=int(os.environ["GEL_SQL_PORT"]),
            user=os.environ["GEL_SQL_USER"],
            password=os.environ["GEL_SQL_PASSWORD"],
            dbname=os.environ["GEL_SQL_DBNAME"],
            connect_timeout=30,
            autocommit=True,
        )

    return _connect


@pytest.fixture(scope="session")
def seed():
    return read_seed()


@pytest.fixture(scope="session")
def expected(seed):
    return expected_from_seed(seed)


@pytest.fixture(scope="session")
def clean_report_dir():
    if os.path.isdir(REPORT_DIR):
        shutil.rmtree(REPORT_DIR)
    os.makedirs(REPORT_DIR, exist_ok=True)
    return REPORT_DIR


@pytest.fixture(scope="session")
def load_runs(gel_client, clean_report_dir):
    """Run `load` twice; the second run proves idempotency."""
    first = run_tool(["load", "--input", "data/seed.json"])
    second = run_tool(["load", "--input", "data/seed.json"])
    return {"first": first, "second": second}


@pytest.fixture(scope="session")
def report_r1(load_runs, gel_client):
    proc, path = reconcile("r1.json")
    return {"proc": proc, "path": path}


@pytest.fixture(scope="session")
def mutation_cycle(report_r1, gel_client, sql_connect):
    """Insert extra objects, reconcile, mutate over SQL, reconcile, clean up."""
    seed_data = read_seed()
    handle = seed_data["artists"][0]["handle"]
    state = {}
    try:
        gel_client.execute(
            """
            insert catalog::Album {
                slug := <str>$slug,
                title := 'Zz Verify Album',
                year := <int32>$year
            }
            """,
            slug=VERIFY_ALBUM_SLUG,
            year=VERIFY_ALBUM_YEAR,
        )
        gel_client.execute(
            """
            with
                alb := (select catalog::Album filter .slug = <str>$album_slug),
                art := (select catalog::Artist filter .handle = <str>$handle)
            insert catalog::Track {
                slug := <str>$slug,
                title := <str>$title,
                duration_ms := <int64>$duration,
                royalty_rate := <decimal>$rate,
                album := alb,
                tags := {<str>$tag_a, <str>$tag_b},
                contributors := (
                    select art { @role := 'verifier', @share_bp := <int64>$share }
                )
            }
            """,
            album_slug=VERIFY_ALBUM_SLUG,
            handle=handle,
            slug=VERIFY_TRACK_SLUG,
            title=VERIFY_TRACK_TITLE,
            duration=VERIFY_TRACK_DURATION,
            rate=Decimal("0.25"),
            tag_a=VERIFY_TAGS[0],
            tag_b=VERIFY_TAGS[1],
            share=VERIFY_SHARE_BP,
        )
        proc2, path2 = reconcile("r2.json")
        state["r2"] = {"proc": proc2, "path": path2}

        with sql_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'select id from catalog."Track" where slug = %s',
                    (VERIFY_TRACK_SLUG,),
                )
                fetched = cur.fetchone()
                assert fetched is not None, (
                    "The track inserted over the binary protocol is not visible "
                    'in the SQL relation catalog."Track".'
                )
                track_id = fetched[0]
                cur.execute(
                    'insert into catalog."Track.tags" (source, target) values (%s, %s)',
                    (track_id, VERIFY_SQL_TAG),
                )
        state["track_id"] = track_id

        proc3, path3 = reconcile("r3.json")
        state["r3"] = {"proc": proc3, "path": path3}
    finally:
        gel_client.execute(
            "delete catalog::Track filter .slug = <str>$slug", slug=VERIFY_TRACK_SLUG
        )
        gel_client.execute(
            "delete catalog::Album filter .slug = <str>$slug", slug=VERIFY_ALBUM_SLUG
        )

    proc4, path4 = reconcile("r4.json")
    state["r4"] = {"proc": proc4, "path": path4}
    return state


# --------------------------------------------------------------------------
# A. migration and schema
# --------------------------------------------------------------------------


def test_migration_status_is_up_to_date(gel_client):
    proc = subprocess.run(
        ["gel", "migration", "status"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, (
        f"`gel migration status` exited with {proc.returncode}: {combined}"
    )
    assert re.search(r"up to date", combined, re.IGNORECASE), (
        f"`gel migration status` does not report the branch as up to date: {combined}"
    )


def test_migration_files_exist(gel_client):
    files = sorted(glob.glob(os.path.join(PROJECT_DIR, "dbschema", "migrations", "*.edgeql")))
    assert files, (
        "No migration files were found in "
        f"{os.path.join(PROJECT_DIR, 'dbschema', 'migrations')}; the schema must be "
        "applied through Gel's migration system."
    )


def test_object_type_hierarchy(gel_client):
    types = gel_client.query(
        """
        select schema::ObjectType {
            name,
            is_abstract := .abstract,
            ancestor_names := (select .ancestors.name)
        }
        filter .name in {
            'catalog::Asset', 'catalog::Album', 'catalog::Track', 'catalog::Artist'
        }
        """
    )
    by_name = {t.name: t for t in types}
    for expected_name in (
        "catalog::Asset",
        "catalog::Album",
        "catalog::Track",
        "catalog::Artist",
    ):
        assert expected_name in by_name, (
            f"Object type {expected_name} does not exist. Found: {sorted(by_name)}"
        )
    assert by_name["catalog::Asset"].is_abstract, "catalog::Asset must be an abstract type."
    for child in ("catalog::Album", "catalog::Track"):
        ancestors = set(by_name[child].ancestor_names)
        assert "catalog::Asset" in ancestors, (
            f"{child} must extend catalog::Asset; ancestors are {sorted(ancestors)}"
        )
    assert "catalog::Asset" not in set(by_name["catalog::Artist"].ancestor_names), (
        "catalog::Artist must not extend catalog::Asset."
    )


@pytest.fixture(scope="session")
def track_pointers(gel_client):
    result = gel_client.query_single(
        """
        select schema::ObjectType {
            pointers: {
                name,
                card := <str>.cardinality,
                expr,
                target: { name },
                [is schema::Link].pointers: { name }
            }
        }
        filter .name = 'catalog::Track'
        limit 1
        """
    )
    assert result is not None, "Object type catalog::Track was not found."
    return {p.name: p for p in result.pointers}


def test_track_pointer_cardinalities_and_targets(track_pointers):
    for name in (
        "title",
        "slug",
        "duration_ms",
        "royalty_rate",
        "album",
        "tags",
        "contributors",
        "payout_micros",
    ):
        assert name in track_pointers, (
            f"catalog::Track is missing the pointer `{name}`. "
            f"Found: {sorted(track_pointers)}"
        )
    assert track_pointers["tags"].card == "Many", "catalog::Track.tags must be a multi property."
    assert track_pointers["contributors"].card == "Many", (
        "catalog::Track.contributors must be a multi link."
    )
    assert track_pointers["album"].card == "One", "catalog::Track.album must be a single link."
    assert track_pointers["royalty_rate"].target.name == "std::decimal", (
        "catalog::Track.royalty_rate must target std::decimal, got "
        f"{track_pointers['royalty_rate'].target.name}"
    )
    assert track_pointers["duration_ms"].target.name == "std::int64", (
        "catalog::Track.duration_ms must target std::int64, got "
        f"{track_pointers['duration_ms'].target.name}"
    )
    assert track_pointers["album"].target.name == "catalog::Album", (
        "catalog::Track.album must target catalog::Album, got "
        f"{track_pointers['album'].target.name}"
    )


def test_payout_micros_is_computed_int64(track_pointers):
    pointer = track_pointers["payout_micros"]
    assert pointer.expr, "catalog::Track.payout_micros must be a computed property."
    assert pointer.target.name == "std::int64", (
        f"catalog::Track.payout_micros must be int64, got {pointer.target.name}"
    )
    assert pointer.card == "One", "catalog::Track.payout_micros must be a single property."


def test_contributors_link_properties(track_pointers):
    link_props = {p.name for p in (getattr(track_pointers["contributors"], "pointers", None) or [])}
    for name in ("role", "share_bp"):
        assert name in link_props, (
            f"catalog::Track.contributors must define the link property `{name}`. "
            f"Found: {sorted(link_props)}"
        )


def test_indexes_and_exclusive_constraints(gel_client):
    album = gel_client.query_single(
        """
        select schema::ObjectType { indexes: { expr } }
        filter .name = 'catalog::Album'
        limit 1
        """
    )
    assert album is not None, "Object type catalog::Album was not found."
    exprs = [ix.expr or "" for ix in album.indexes]
    assert any("year" in e for e in exprs), (
        f"catalog::Album must declare an index on `year`; found index exprs: {exprs}"
    )

    constrained = gel_client.query(
        """
        select schema::ObjectType {
            name,
            properties: {
                name,
                constraint_names := (select .constraints.name)
            }
        }
        filter .name in {'catalog::Asset', 'catalog::Artist'}
        """
    )
    found = {}
    for obj in constrained:
        for prop in obj.properties:
            found[(obj.name, prop.name)] = set(prop.constraint_names)
    slug_constraints = found.get(("catalog::Asset", "slug"), set())
    assert any("exclusive" in c for c in slug_constraints), (
        f"catalog::Asset.slug must carry an exclusive constraint; found {slug_constraints}"
    )
    handle_constraints = found.get(("catalog::Artist", "handle"), set())
    assert any("exclusive" in c for c in handle_constraints), (
        f"catalog::Artist.handle must carry an exclusive constraint; found {handle_constraints}"
    )


def test_invalid_country_is_rejected(gel_client):
    for bad in ("usa", "US1"):
        with pytest.raises(Exception):
            gel_client.execute(
                """
                insert catalog::Artist {
                    handle := <str>$handle, name := 'Neg', country := <str>$country
                }
                """,
                handle=f"zz-neg-{bad}",
                country=bad,
            )
        leftover = gel_client.query(
            "select catalog::Artist { id } filter .handle = <str>$handle",
            handle=f"zz-neg-{bad}",
        )
        assert not leftover, (
            f"An Artist with the invalid country {bad!r} was persisted; the database "
            "must reject it."
        )


def test_valid_country_is_accepted(gel_client):
    handle = "zz-neg-ok"
    gel_client.execute(
        """
        insert catalog::Artist {
            handle := <str>$handle, name := 'Neg Ok', country := 'US'
        }
        """,
        handle=handle,
    )
    try:
        created = gel_client.query(
            "select catalog::Artist { id } filter .handle = <str>$handle", handle=handle
        )
        assert len(created) == 1, "A valid two-letter uppercase country must be accepted."
    finally:
        gel_client.execute(
            "delete catalog::Artist filter .handle = <str>$handle", handle=handle
        )


def test_zero_duration_track_is_rejected(gel_client, expected):
    slug = "zz-neg-duration"
    with pytest.raises(Exception):
        gel_client.execute(
            """
            insert catalog::Track {
                slug := <str>$slug,
                title := 'Neg Duration',
                duration_ms := 0,
                royalty_rate := <decimal>'0.1',
                album := (select catalog::Album limit 1)
            }
            """,
            slug=slug,
        )
    leftover = gel_client.query(
        "select catalog::Track { id } filter .slug = <str>$slug", slug=slug
    )
    assert not leftover, "A Track with duration_ms = 0 was persisted; it must be rejected."


def test_duplicate_slug_is_rejected(gel_client, seed):
    existing_slug = seed["albums"][0]["slug"]
    with pytest.raises(Exception):
        gel_client.execute(
            """
            insert catalog::Album {
                slug := <str>$slug, title := 'Duplicate', year := <int32>1990
            }
            """,
            slug=existing_slug,
        )
    count = gel_client.query_single(
        "select count((select catalog::Album filter .slug = <str>$slug))",
        slug=existing_slug,
    )
    assert count == 1, (
        f"Album slug {existing_slug!r} must remain unique; found {count} objects."
    )


# --------------------------------------------------------------------------
# B. loaded data over the native protocol
# --------------------------------------------------------------------------


def test_object_counts_match_seed(gel_client, seed):
    counts = gel_client.query_single(
        """
        select {
            artists := count(catalog::Artist),
            albums := count(catalog::Album),
            tracks := count(catalog::Track)
        }
        """
    )
    assert counts.artists == len(seed["artists"]), (
        f"Expected {len(seed['artists'])} Artist objects, found {counts.artists}."
    )
    assert counts.albums == len(seed["albums"]), (
        f"Expected {len(seed['albums'])} Album objects, found {counts.albums}."
    )
    assert counts.tracks == len(seed["tracks"]), (
        f"Expected {len(seed['tracks'])} Track objects, found {counts.tracks}."
    )


def test_artists_loaded_exactly(gel_client, seed):
    artists = gel_client.query(
        "select catalog::Artist { handle, name, country, aliases }"
    )
    by_handle = {}
    for artist in artists:
        assert artist.handle not in by_handle, (
            f"Artist handle {artist.handle!r} appears more than once."
        )
        by_handle[artist.handle] = artist
    for entry in seed["artists"]:
        assert entry["handle"] in by_handle, f"Artist {entry['handle']!r} was not loaded."
        artist = by_handle[entry["handle"]]
        assert artist.name == entry["name"], (
            f"Artist {entry['handle']!r} name mismatch: {artist.name!r} != {entry['name']!r}"
        )
        assert artist.country == entry["country"], (
            f"Artist {entry['handle']!r} country mismatch: "
            f"{artist.country!r} != {entry['country']!r}"
        )
        assert set(artist.aliases) == set(entry["aliases"]), (
            f"Artist {entry['handle']!r} aliases mismatch: "
            f"{sorted(artist.aliases)} != {sorted(entry['aliases'])}"
        )


def test_albums_loaded_exactly(gel_client, seed):
    albums = gel_client.query("select catalog::Album { slug, title, year, label }")
    by_slug = {}
    for album in albums:
        assert album.slug not in by_slug, f"Album slug {album.slug!r} appears more than once."
        by_slug[album.slug] = album
    for entry in seed["albums"]:
        assert entry["slug"] in by_slug, f"Album {entry['slug']!r} was not loaded."
        album = by_slug[entry["slug"]]
        assert album.title == entry["title"], (
            f"Album {entry['slug']!r} title mismatch: {album.title!r} != {entry['title']!r}"
        )
        assert album.year == entry["year"], (
            f"Album {entry['slug']!r} year mismatch: {album.year} != {entry['year']}"
        )
        assert album.label == entry["label"], (
            f"Album {entry['slug']!r} label mismatch: {album.label!r} != {entry['label']!r}"
        )


def test_tracks_loaded_exactly(gel_client, seed, expected):
    tracks = gel_client.query(
        """
        select catalog::Track {
            slug,
            title,
            duration_ms,
            payout_micros,
            album_slug := .album.slug,
            tags,
            contribs := (
                select .contributors {
                    handle,
                    c_role := @role,
                    c_share := @share_bp
                }
            )
        }
        """
    )
    by_slug = {}
    for track in tracks:
        assert track.slug not in by_slug, f"Track slug {track.slug!r} appears more than once."
        by_slug[track.slug] = track
    for entry in seed["tracks"]:
        assert entry["slug"] in by_slug, f"Track {entry['slug']!r} was not loaded."
        track = by_slug[entry["slug"]]
        assert track.title == entry["title"], (
            f"Track {entry['slug']!r} title mismatch: {track.title!r} != {entry['title']!r}"
        )
        assert track.duration_ms == entry["duration_ms"], (
            f"Track {entry['slug']!r} duration mismatch: "
            f"{track.duration_ms} != {entry['duration_ms']}"
        )
        assert track.album_slug == entry["album"], (
            f"Track {entry['slug']!r} album mismatch: "
            f"{track.album_slug!r} != {entry['album']!r}"
        )
        assert set(track.tags) == set(entry["tags"]), (
            f"Track {entry['slug']!r} tags mismatch: "
            f"{sorted(track.tags)} != {sorted(entry['tags'])}"
        )
        actual_contribs = {
            (c.handle, c.c_role, c.c_share) for c in track.contribs
        }
        wanted_contribs = {
            (c["artist"], c["role"], c["share_bp"]) for c in entry["contributors"]
        }
        assert actual_contribs == wanted_contribs, (
            f"Track {entry['slug']!r} contributors mismatch: "
            f"{sorted(actual_contribs)} != {sorted(wanted_contribs)}"
        )
        assert track.payout_micros == expected["rows"][entry["slug"]]["payout_micros"], (
            f"Track {entry['slug']!r} payout_micros mismatch: {track.payout_micros} != "
            f"{expected['rows'][entry['slug']]['payout_micros']}"
        )


# --------------------------------------------------------------------------
# C. SQL protocol surface
# --------------------------------------------------------------------------


def test_sql_relations_are_queryable(sql_connect, expected):
    wanted_counts = {
        'catalog."Album"': expected["metrics"]["count.catalog::Album"],
        'catalog."Artist"': expected["metrics"]["count.catalog::Artist"],
        'catalog."Track"': expected["metrics"]["count.catalog::Track"],
        'catalog."Track.tags"': expected["metrics"]["count.catalog::Track.tags"],
        'catalog."Track.contributors"': expected["metrics"][
            "count.catalog::Track.contributors"
        ],
        'catalog."Artist.aliases"': expected["metrics"]["count.catalog::Artist.aliases"],
    }
    with sql_connect() as conn:
        with conn.cursor() as cur:
            for relation, wanted in wanted_counts.items():
                cur.execute(f"select count(*) from {relation}")  # noqa: S608
                row = cur.fetchone()
                assert row is not None, f"Could not count rows in {relation}."
                assert row[0] == wanted, (
                    f"SQL relation {relation} has {row[0]} rows, expected {wanted}."
                )


def test_sql_column_mapping(sql_connect):
    with sql_connect() as conn:
        with conn.cursor() as cur:
            cur.execute('select * from catalog."Track" limit 1')
            track_columns = {d.name for d in (cur.description or [])}
            cur.execute('select * from catalog."Track.contributors" limit 1')
            contrib_columns = {d.name for d in (cur.description or [])}
    assert "album_id" in track_columns, (
        'The single link `album` must appear as the column `album_id` on catalog."Track"; '
        f"found {sorted(track_columns)}"
    )
    for column in ("source", "target", "role", "share_bp"):
        assert column in contrib_columns, (
            f'catalog."Track.contributors" is missing the column {column!r}; '
            f"found {sorted(contrib_columns)}"
        )


# --------------------------------------------------------------------------
# D. load command
# --------------------------------------------------------------------------


def test_load_command_reports_totals(load_runs, expected):
    proc = load_runs["first"]
    assert proc.returncode == 0, (
        f"`python3 {ENTRYPOINT} load --input data/seed.json` exited with "
        f"{proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = parse_single_json_object(proc.stdout, "`load` stdout")
    assert set(payload) == {"artists", "albums", "tracks", "contributions"}, (
        f"`load` must print exactly the keys artists/albums/tracks/contributions, "
        f"got {sorted(payload)}"
    )
    metrics = expected["metrics"]
    assert payload["artists"] == metrics["count.catalog::Artist"], (
        f"`load` reported {payload['artists']} artists, expected "
        f"{metrics['count.catalog::Artist']}"
    )
    assert payload["albums"] == metrics["count.catalog::Album"], (
        f"`load` reported {payload['albums']} albums, expected "
        f"{metrics['count.catalog::Album']}"
    )
    assert payload["tracks"] == metrics["count.catalog::Track"], (
        f"`load` reported {payload['tracks']} tracks, expected "
        f"{metrics['count.catalog::Track']}"
    )
    assert payload["contributions"] == metrics["count.catalog::Track.contributors"], (
        f"`load` reported {payload['contributions']} contributions, expected "
        f"{metrics['count.catalog::Track.contributors']}"
    )


def test_load_command_is_idempotent(load_runs, expected, gel_client):
    proc = load_runs["second"]
    assert proc.returncode == 0, (
        f"The second `load` run exited with {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = parse_single_json_object(proc.stdout, "the second `load` stdout")
    first_payload = parse_single_json_object(load_runs["first"].stdout, "`load` stdout")
    assert payload == first_payload, (
        f"Re-running `load` changed the reported totals: {first_payload} -> {payload}"
    )
    metrics = expected["metrics"]
    totals = {
        "artists": gel_client.query_single("select count(catalog::Artist)"),
        "albums": gel_client.query_single("select count(catalog::Album)"),
        "tracks": gel_client.query_single("select count(catalog::Track)"),
        "aliases": gel_client.query_single(
            "select sum((select catalog::Artist { n := count(.aliases) }).n)"
        ),
        "tags": gel_client.query_single(
            "select sum((select catalog::Track { n := count(.tags) }).n)"
        ),
        "contributions": gel_client.query_single(
            "select sum((select catalog::Track { n := count(.contributors) }).n)"
        ),
    }
    assert totals["artists"] == metrics["count.catalog::Artist"], "Artists duplicated after reload."
    assert totals["albums"] == metrics["count.catalog::Album"], "Albums duplicated after reload."
    assert totals["tracks"] == metrics["count.catalog::Track"], "Tracks duplicated after reload."
    assert totals["aliases"] == metrics["count.catalog::Artist.aliases"], (
        f"Alias rows changed after reload: {totals['aliases']} != "
        f"{metrics['count.catalog::Artist.aliases']}"
    )
    assert totals["tags"] == metrics["count.catalog::Track.tags"], (
        f"Tag rows changed after reload: {totals['tags']} != "
        f"{metrics['count.catalog::Track.tags']}"
    )
    assert totals["contributions"] == metrics["count.catalog::Track.contributors"], (
        f"Contributor rows changed after reload: {totals['contributions']} != "
        f"{metrics['count.catalog::Track.contributors']}"
    )


# --------------------------------------------------------------------------
# E. reconcile happy path
# --------------------------------------------------------------------------


def test_reconcile_exit_code_and_stdout(report_r1):
    proc = report_r1["proc"]
    assert proc.returncode == 0, (
        f"`reconcile` exited with {proc.returncode}, expected 0.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = parse_single_json_object(proc.stdout, "`reconcile` stdout")
    assert set(payload) == {"output", "agrees", "mismatch_count", "drift_count"}, (
        f"`reconcile` stdout must contain exactly the keys output/agrees/"
        f"mismatch_count/drift_count, got {sorted(payload)}"
    )
    assert payload["output"] == report_r1["path"], (
        f"`reconcile` echoed output {payload['output']!r}, expected {report_r1['path']!r}"
    )
    assert payload["agrees"] is True, "`reconcile` must report agrees=true on a healthy database."
    assert payload["mismatch_count"] == 0, (
        f"`reconcile` reported {payload['mismatch_count']} mismatches, expected 0."
    )
    assert payload["drift_count"] == 0, (
        f"`reconcile` reported {payload['drift_count']} drift entries without a baseline."
    )


def test_report_top_level_shape(report_r1):
    report = load_report(report_r1["path"])
    assert set(report) == REPORT_TOP_KEYS, (
        f"The report must have exactly the keys {sorted(REPORT_TOP_KEYS)}, "
        f"got {sorted(report)}"
    )
    assert report["schema_version"] == 1, (
        f"schema_version must be 1, got {report['schema_version']!r}"
    )
    assert report["agrees"] is True, "The report must report agrees=true."
    assert report["mismatches"] == [], f"Expected no mismatches, got {report['mismatches']}"
    assert report["drift"] == [], f"Expected no drift, got {report['drift']}"
    assert report["mismatch_count"] == 0, "mismatch_count must be 0."
    assert report["drift_count"] == 0, "drift_count must be 0."


def test_report_metrics_are_complete_sorted_and_correct(report_r1, expected):
    report = load_report(report_r1["path"])
    ids = [m["id"] for m in report["metrics"]]
    assert ids == sorted(ids), f"`metrics` must be sorted by id ascending, got {ids}"
    assert set(ids) == set(METRIC_IDS), (
        "The metric id set is wrong.\n"
        f"missing: {sorted(set(METRIC_IDS) - set(ids))}\n"
        f"unexpected: {sorted(set(ids) - set(METRIC_IDS))}"
    )
    for metric in report["metrics"]:
        assert set(metric) == {"id", "edgeql", "sql", "agrees"}, (
            f"Metric entry {metric['id']!r} must have exactly the keys "
            f"id/edgeql/sql/agrees, got {sorted(metric)}"
        )
        wanted = expected["metrics"][metric["id"]]
        assert metric["edgeql"] == wanted, (
            f"Metric {metric['id']!r}: edgeql value {metric['edgeql']!r} != expected {wanted!r}"
        )
        assert metric["sql"] == wanted, (
            f"Metric {metric['id']!r}: sql value {metric['sql']!r} != expected {wanted!r}"
        )
        assert metric["agrees"] is True, f"Metric {metric['id']!r} must report agrees=true."


def test_report_rows_are_complete_sorted_and_correct(report_r1, expected):
    report = load_report(report_r1["path"])
    slugs = [r["slug"] for r in report["rows"]]
    assert slugs == sorted(slugs), f"`rows` must be sorted by slug ascending, got {slugs}"
    assert set(slugs) == set(expected["rows"]), (
        "The row slug set is wrong.\n"
        f"missing: {sorted(set(expected['rows']) - set(slugs))}\n"
        f"unexpected: {sorted(set(slugs) - set(expected['rows']))}"
    )
    for row in report["rows"]:
        assert set(row) == {"slug", "edgeql", "sql", "agrees"}, (
            f"Row entry {row['slug']!r} must have exactly the keys slug/edgeql/sql/"
            f"agrees, got {sorted(row)}"
        )
        assert row["agrees"] is True, f"Row {row['slug']!r} must report agrees=true."
        wanted = expected["rows"][row["slug"]]
        for side in ("edgeql", "sql"):
            payload = row[side]
            assert isinstance(payload, dict), (
                f"Row {row['slug']!r} side {side!r} must be an object, got {payload!r}"
            )
            assert set(payload) == set(ROW_FIELDS), (
                f"Row {row['slug']!r} side {side!r} must have exactly the keys "
                f"{sorted(ROW_FIELDS)}, got {sorted(payload)}"
            )
            assert payload == wanted, (
                f"Row {row['slug']!r} side {side!r} mismatch: {payload} != {wanted}"
            )


def test_report_covers_edge_case_rows(report_r1, expected):
    report = load_report(report_r1["path"])
    rows = rows_map(report)
    tagless = [slug for slug, row in expected["rows"].items() if row["tag_count"] == 0]
    assert tagless, "The seed dataset must contain a track with no tags."
    for slug in tagless:
        assert rows[slug]["edgeql"]["tag_count"] == 0, (
            f"Track {slug!r} has no tags but the report says "
            f"{rows[slug]['edgeql']['tag_count']}"
        )
        assert rows[slug]["sql"]["tag_count"] == 0, (
            f"Track {slug!r} has no tags but the SQL side says "
            f"{rows[slug]['sql']['tag_count']}"
        )
    contributorless = [
        slug for slug, row in expected["rows"].items() if row["contributor_count"] == 0
    ]
    assert contributorless, "The seed dataset must contain a track with no contributors."
    for slug in contributorless:
        for side in ("edgeql", "sql"):
            assert rows[slug][side]["share_bp_total"] == 0, (
                f"Track {slug!r} has no contributors, so share_bp_total must be 0 on "
                f"the {side} side, got {rows[slug][side]['share_bp_total']}"
            )


# --------------------------------------------------------------------------
# F. liveness / anti-cheat
# --------------------------------------------------------------------------


def test_reconcile_reflects_new_objects(mutation_cycle, report_r1, expected):
    proc = mutation_cycle["r2"]["proc"]
    assert proc.returncode == 0, (
        f"`reconcile` after inserting new objects exited with {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    report = load_report(mutation_cycle["r2"]["path"])
    assert report["mismatch_count"] == 0, (
        f"The two protocols disagreed after new objects were inserted: {report['mismatches']}"
    )
    base = expected["metrics"]
    deltas = {
        "count.catalog::Track": 1,
        "count.catalog::Album": 1,
        "count.catalog::Asset": 2,
        "count.catalog::Track.tags": len(VERIFY_TAGS),
        "count.catalog::Track.contributors": 1,
        "sum.catalog::Track.duration_ms": VERIFY_TRACK_DURATION,
        "sum.catalog::Track.contributors@share_bp": VERIFY_SHARE_BP,
        "sum.catalog::Track.payout_micros": VERIFY_TRACK_PAYOUT,
        "sum.catalog::Track.title_length": VERIFY_TRACK_TITLE_LEN,
    }
    actual = metrics_map(report)
    for metric_id, delta in deltas.items():
        wanted = base[metric_id] + delta
        assert actual[metric_id]["edgeql"] == wanted, (
            f"Metric {metric_id!r} edgeql value did not move as expected: "
            f"{actual[metric_id]['edgeql']} != {wanted}"
        )
        assert actual[metric_id]["sql"] == wanted, (
            f"Metric {metric_id!r} sql value did not move as expected: "
            f"{actual[metric_id]['sql']} != {wanted}"
        )
    assert actual["max.catalog::Album.year"]["edgeql"] == VERIFY_ALBUM_YEAR, (
        "max.catalog::Album.year must follow the newly inserted album on the edgeql side."
    )
    assert actual["max.catalog::Album.year"]["sql"] == VERIFY_ALBUM_YEAR, (
        "max.catalog::Album.year must follow the newly inserted album on the sql side."
    )


def test_reconcile_row_for_new_track(mutation_cycle):
    report = load_report(mutation_cycle["r2"]["path"])
    rows = rows_map(report)
    assert VERIFY_TRACK_SLUG in rows, (
        f"The newly inserted track {VERIFY_TRACK_SLUG!r} is missing from `rows`."
    )
    wanted = {
        "album_slug": VERIFY_ALBUM_SLUG,
        "album_year": VERIFY_ALBUM_YEAR,
        "contributor_count": 1,
        "duration_ms": VERIFY_TRACK_DURATION,
        "payout_micros": VERIFY_TRACK_PAYOUT,
        "share_bp_total": VERIFY_SHARE_BP,
        "tag_count": len(VERIFY_TAGS),
        "title_length": VERIFY_TRACK_TITLE_LEN,
    }
    row = rows[VERIFY_TRACK_SLUG]
    assert row["edgeql"] == wanted, (
        f"Row {VERIFY_TRACK_SLUG!r} edgeql side mismatch: {row['edgeql']} != {wanted}"
    )
    assert row["sql"] == wanted, (
        f"Row {VERIFY_TRACK_SLUG!r} sql side mismatch: {row['sql']} != {wanted}"
    )
    assert row["agrees"] is True, f"Row {VERIFY_TRACK_SLUG!r} must agree across protocols."


def test_reconcile_reflects_sql_side_mutation(mutation_cycle):
    proc = mutation_cycle["r3"]["proc"]
    assert proc.returncode == 0, (
        f"`reconcile` after the SQL-side insert exited with {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    before = load_report(mutation_cycle["r2"]["path"])
    after = load_report(mutation_cycle["r3"]["path"])
    assert after["mismatch_count"] == 0, (
        f"The two protocols disagreed after the SQL-side insert: {after['mismatches']}"
    )
    before_tags = metrics_map(before)["count.catalog::Track.tags"]
    after_tags = metrics_map(after)["count.catalog::Track.tags"]
    for side in ("edgeql", "sql"):
        assert after_tags[side] == before_tags[side] + 1, (
            f"count.catalog::Track.tags on the {side} side must increase by exactly 1 "
            f"after a tag is inserted over the SQL protocol: "
            f"{before_tags[side]} -> {after_tags[side]}"
        )
    before_row = rows_map(before)[VERIFY_TRACK_SLUG]
    after_row = rows_map(after)[VERIFY_TRACK_SLUG]
    for side in ("edgeql", "sql"):
        assert after_row[side]["tag_count"] == before_row[side]["tag_count"] + 1, (
            f"tag_count for {VERIFY_TRACK_SLUG!r} on the {side} side must increase by "
            f"exactly 1: {before_row[side]['tag_count']} -> {after_row[side]['tag_count']}"
        )


def test_reconcile_returns_to_baseline_after_cleanup(mutation_cycle, expected):
    proc = mutation_cycle["r4"]["proc"]
    assert proc.returncode == 0, (
        f"`reconcile` after cleanup exited with {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    report = load_report(mutation_cycle["r4"]["path"])
    assert report["mismatch_count"] == 0, (
        f"The two protocols disagreed after cleanup: {report['mismatches']}"
    )
    actual = metrics_map(report)
    for metric_id, wanted in expected["metrics"].items():
        assert actual[metric_id]["edgeql"] == wanted, (
            f"After cleanup metric {metric_id!r} (edgeql) is "
            f"{actual[metric_id]['edgeql']}, expected {wanted}"
        )
        assert actual[metric_id]["sql"] == wanted, (
            f"After cleanup metric {metric_id!r} (sql) is "
            f"{actual[metric_id]['sql']}, expected {wanted}"
        )


# --------------------------------------------------------------------------
# G. drift against a baseline
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def drift_setup(mutation_cycle):
    clean = load_report(mutation_cycle["r4"]["path"])
    doctored = copy.deepcopy(clean)

    metric_by_id = {m["id"]: m for m in doctored["metrics"]}
    metric_by_id["count.catalog::Track"]["edgeql"] += 7
    metric_by_id["sum.catalog::Track.duration_ms"]["edgeql"] -= 1

    doctored["rows"].sort(key=lambda r: r["slug"])
    first_row = doctored["rows"][0]
    first_row["edgeql"]["tag_count"] += 3
    removed_row = doctored["rows"].pop()

    baseline_path = os.path.join(REPORT_DIR, "baseline.json")
    with open(baseline_path, "w", encoding="utf-8") as fh:
        json.dump(doctored, fh)

    proc, path = reconcile("r5.json", baseline=baseline_path)
    return {
        "clean": clean,
        "doctored": doctored,
        "baseline_path": baseline_path,
        "proc": proc,
        "path": path,
        "doctored_row_slug": first_row["slug"],
        "removed_row_slug": removed_row["slug"],
    }


def test_drift_exit_code_and_stdout(drift_setup):
    proc = drift_setup["proc"]
    assert proc.returncode == 4, (
        f"`reconcile --baseline <doctored>` must exit 4, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = parse_single_json_object(proc.stdout, "`reconcile --baseline` stdout")
    assert payload["agrees"] is True, "Protocols still agree, so `agrees` must be true."
    assert payload["mismatch_count"] == 0, (
        f"Expected no protocol mismatches, got {payload['mismatch_count']}."
    )
    assert payload["drift_count"] == 11, (
        f"Expected drift_count 11 (2 metrics + 1 row field + 8 for the removed row), "
        f"got {payload['drift_count']}."
    )


def test_drift_entries_are_exact_and_sorted(drift_setup):
    report = load_report(drift_setup["path"])
    assert report["mismatches"] == [], (
        f"Expected no mismatches alongside drift, got {report['mismatches']}"
    )
    drift = report["drift"]
    assert report["drift_count"] == len(drift), (
        f"drift_count ({report['drift_count']}) must equal len(drift) ({len(drift)})."
    )
    for entry in drift:
        assert set(entry) == {"scope", "id", "field", "baseline", "current"}, (
            f"Drift entry must have exactly the keys scope/id/field/baseline/current, "
            f"got {sorted(entry)}"
        )
    assert drift == sorted(drift, key=sort_key), (
        f"`drift` must be sorted by (scope, id, field); got "
        f"{[sort_key(e) for e in drift]}"
    )

    clean_metrics = {m["id"]: m for m in drift_setup["clean"]["metrics"]}
    clean_rows = {r["slug"]: r for r in drift_setup["clean"]["rows"]}
    doctored_metrics = {m["id"]: m for m in drift_setup["doctored"]["metrics"]}
    doctored_rows = {r["slug"]: r for r in drift_setup["doctored"]["rows"]}
    doctored_slug = drift_setup["doctored_row_slug"]
    removed_slug = drift_setup["removed_row_slug"]

    wanted = [
        {
            "scope": "metric",
            "id": "count.catalog::Track",
            "field": "value",
            "baseline": doctored_metrics["count.catalog::Track"]["edgeql"],
            "current": clean_metrics["count.catalog::Track"]["edgeql"],
        },
        {
            "scope": "metric",
            "id": "sum.catalog::Track.duration_ms",
            "field": "value",
            "baseline": doctored_metrics["sum.catalog::Track.duration_ms"]["edgeql"],
            "current": clean_metrics["sum.catalog::Track.duration_ms"]["edgeql"],
        },
        {
            "scope": "row",
            "id": doctored_slug,
            "field": "tag_count",
            "baseline": doctored_rows[doctored_slug]["edgeql"]["tag_count"],
            "current": clean_rows[doctored_slug]["edgeql"]["tag_count"],
        },
    ]
    for field in sorted(ROW_FIELDS):
        wanted.append(
            {
                "scope": "row",
                "id": removed_slug,
                "field": field,
                "baseline": None,
                "current": clean_rows[removed_slug]["edgeql"][field],
            }
        )
    wanted.sort(key=sort_key)

    assert drift == wanted, (
        "The drift report does not match the expected entries.\n"
        f"actual:   {json.dumps(drift, sort_keys=True)}\n"
        f"expected: {json.dumps(wanted, sort_keys=True)}"
    )


def test_no_drift_against_matching_baseline(drift_setup, mutation_cycle):
    proc, path = reconcile("r6.json", baseline=mutation_cycle["r4"]["path"])
    assert proc.returncode == 0, (
        f"`reconcile` against an unmodified baseline must exit 0, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = parse_single_json_object(proc.stdout, "`reconcile --baseline` stdout")
    assert payload["drift_count"] == 0, (
        f"Expected no drift against an unmodified baseline, got {payload['drift_count']}."
    )
    report = load_report(path)
    assert report["drift"] == [], f"Expected an empty drift array, got {report['drift']}"


# --------------------------------------------------------------------------
# H. connection error handling
# --------------------------------------------------------------------------


def _assert_connection_error(proc, out_path, protocol):
    assert proc.returncode == 2, (
        f"Expected exit code 2 for an unreachable {protocol} protocol, got "
        f"{proc.returncode}.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert (proc.stdout or "").strip() == "", (
        f"Nothing may be written to stdout on a connection failure, got {proc.stdout!r}"
    )
    payload = parse_single_json_object(proc.stderr, "`reconcile` stderr")
    assert set(payload) == {"error", "protocol", "message"}, (
        f"The stderr object must have exactly the keys error/protocol/message, "
        f"got {sorted(payload)}"
    )
    assert payload["error"] == "connection", (
        f"`error` must be the string 'connection', got {payload['error']!r}"
    )
    assert payload["protocol"] == protocol, (
        f"`protocol` must be {protocol!r}, got {payload['protocol']!r}"
    )
    assert isinstance(payload["message"], str) and payload["message"], (
        "`message` must be a non-empty string."
    )
    assert not os.path.exists(out_path), (
        f"No report file may be written on a connection failure, but {out_path} exists."
    )


def test_bad_sql_password_reports_connection_error(mutation_cycle):
    out_path = os.path.join(REPORT_DIR, "bad-sql.json")
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = run_tool(
        ["reconcile", "--output", out_path],
        extra_env={"GEL_SQL_PASSWORD": "definitely-not-the-password"},
    )
    _assert_connection_error(proc, out_path, "sql")


def test_closed_sql_port_reports_connection_error(mutation_cycle):
    out_path = os.path.join(REPORT_DIR, "bad-sql-port.json")
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = run_tool(
        ["reconcile", "--output", out_path],
        extra_env={"GEL_SQL_PORT": "1"},
    )
    _assert_connection_error(proc, out_path, "sql")


def test_bad_edgeql_credentials_report_connection_error(mutation_cycle):
    out_path = os.path.join(REPORT_DIR, "bad-edgeql.json")
    if os.path.exists(out_path):
        os.remove(out_path)
    proc = run_tool(
        ["reconcile", "--output", out_path],
        extra_env={
            "GEL_DSN": "gel://admin:definitely-not-the-password@127.0.0.1:5656/main",
            "GEL_CLIENT_TLS_SECURITY": "insecure",
        },
    )
    _assert_connection_error(proc, out_path, "edgeql")
