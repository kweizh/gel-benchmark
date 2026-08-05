"""Final-state verification for the weighted collaboration graph task.

Everything is checked against the live Gel 7.1 server that runs inside this
container: the schema is read out of the database's own introspection data, and
every expected answer is recomputed in this test from a snapshot of the actual
graph stored in the database.
"""

import collections
import datetime
import json
import os
import shutil
import subprocess
import time

import gel
import pytest

PROJECT_DIR = "/home/user/socialgraph"
GRAPH_PY = os.path.join(PROJECT_DIR, "graph.py")
SEED_FILE = os.path.join(PROJECT_DIR, "data", "seed.json")
MISSING_FILE = os.path.join(PROJECT_DIR, "data", "missing.json")
ATOMIC_FILE = "/tmp/atomic_load.json"
BROKEN_FILE = "/tmp/broken_load.json"

GEL_START = "gel-start"
LOAD_TIMEOUT = 240
CMD_TIMEOUT = 45

EXPECTED_MEMBERS = 200
EXPECTED_CONNECTIONS = 1914
EXPECTED_CONFIRMED = 1332
EXPECTED_MUTUAL_PAIRS = 270

LINK_PROP_TYPES = {
    "weight": "std::int64",
    "role": "std::str",
    "established": "std::datetime",
    "confirmed": "std::bool",
}

SNAPSHOT_QUERY = """
select Member {
    handle,
    display_name,
    connections: {
        handle,
        @weight,
        @role,
        @established,
        @confirmed
    }
}
"""

INTROSPECT_QUERY = """
select schema::ObjectType {
    name,
    properties: { name, cardinality, required, target: { name } },
    links: {
        name,
        cardinality,
        required,
        target: { name },
        properties: { name, cardinality, target: { name } }
    }
}
filter .name = 'default::Member'
"""

UPSERT_EDGE = """
update Member
filter .handle = <str>$src
set {
    connections += (
        select detached Member {
            @weight := <int64>$weight,
            @role := <str>$role,
            @established := <datetime>$established,
            @confirmed := <bool>$confirmed
        }
        filter .handle = <str>$dst
    )
}
"""

REMOVE_EDGE = """
update Member
filter .handle = <str>$src
set {
    connections -= (select .connections filter .handle = <str>$dst)
}
"""


# ---------------------------------------------------------------------------
# infrastructure helpers
# ---------------------------------------------------------------------------
def _start_server() -> None:
    binary = shutil.which(GEL_START)
    if binary is not None:
        try:
            subprocess.run([binary], capture_output=True, text=True, timeout=1200)
        except subprocess.TimeoutExpired:
            pass


def _wait_for_client(deadline_seconds: int = 600) -> gel.Client:
    last_error = None
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        try:
            candidate = gel.create_client(timeout=180)
            candidate.query_single("select 1")
            return candidate
        except Exception as exc:  # noqa: BLE001 - the server may still be booting
            last_error = exc
            time.sleep(3)
    raise AssertionError(
        "The local Gel server never became ready for queries. Last error: %r" % (last_error,)
    )


@pytest.fixture(scope="session")
def client():
    _start_server()
    connection = _wait_for_client()
    try:
        yield connection
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass


def _cli(args, timeout=CMD_TIMEOUT):
    started = time.monotonic()
    proc = subprocess.run(
        ["python3", "graph.py", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc, time.monotonic() - started


def _cli_json(args, timeout=CMD_TIMEOUT):
    proc, elapsed = _cli(args, timeout=timeout)
    assert proc.returncode == 0, (
        f"`python3 graph.py {' '.join(args)}` exited with {proc.returncode}.\n"
        f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"`python3 graph.py {' '.join(args)}` did not print a single JSON document on "
            f"stdout ({exc}). stdout was: {proc.stdout!r}"
        ) from exc
    return payload, elapsed


def _seed_payload():
    with open(SEED_FILE, encoding="utf-8") as handle:
        return json.load(handle)


def _norm_ts(value):
    if value is None:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc).isoformat()


def _pick(entry, name):
    """Read a link property from a JSON shape result (`@name` or `name`)."""
    if "@" + name in entry:
        return entry["@" + name]
    return entry.get(name)


def _snapshot(client):
    rows = json.loads(client.query_json(SNAPSHOT_QUERY))
    members = {}
    edges = {}
    for row in rows:
        members[row["handle"]] = row["display_name"]
        for target in row.get("connections") or []:
            edges[(row["handle"], target["handle"])] = {
                "weight": _pick(target, "weight"),
                "role": _pick(target, "role"),
                "established": _norm_ts(_pick(target, "established")),
                "confirmed": _pick(target, "confirmed") is True,
            }
    incomplete = [
        key
        for key, edge in edges.items()
        if edge["weight"] is None or edge["role"] is None or edge["established"] is None
    ]
    assert not incomplete, (
        "Every stored connection must carry a weight, a role and an established timestamp, "
        f"but {len(incomplete)} of them do not, for example: {sorted(incomplete, key=str)[:5]}"
    )
    return {"members": members, "edges": edges}


def _fingerprint(snapshot):
    return (
        sorted(snapshot["members"].items(), key=str),
        sorted(
            (
                (
                    source,
                    target,
                    edge["weight"],
                    edge["role"],
                    edge["established"],
                    edge["confirmed"],
                )
                for (source, target), edge in snapshot["edges"].items()
            ),
            key=str,
        ),
    )


# ---------------------------------------------------------------------------
# oracles recomputed from the database snapshot
# ---------------------------------------------------------------------------
def _out_edges(snapshot, handle):
    return {
        target: edge for (source, target), edge in snapshot["edges"].items() if source == handle
    }


def _in_edges(snapshot, handle):
    return {
        source: edge for (source, target), edge in snapshot["edges"].items() if target == handle
    }


def _expected_top(snapshot, handle, limit):
    ordered = sorted(
        _out_edges(snapshot, handle).items(), key=lambda item: (-item[1]["weight"], item[0])
    )
    return [
        {
            "handle": target,
            "display_name": snapshot["members"][target],
            "weight": edge["weight"],
            "role": edge["role"],
            "confirmed": edge["confirmed"],
        }
        for target, edge in ordered[:limit]
    ]


def _expected_mutual(snapshot, handle):
    result = []
    for target, edge in _out_edges(snapshot, handle).items():
        reverse = snapshot["edges"].get((target, handle))
        if edge["confirmed"] and reverse is not None and reverse["confirmed"]:
            result.append(target)
    return sorted(result)


def _suggest_scores(snapshot, handle, confirmed_only=True):
    scores = collections.defaultdict(int)
    bridges = collections.defaultdict(list)
    for bridge, first in _out_edges(snapshot, handle).items():
        if confirmed_only and not first["confirmed"]:
            continue
        for candidate, second in _out_edges(snapshot, bridge).items():
            if confirmed_only and not second["confirmed"]:
                continue
            if candidate == handle or (handle, candidate) in snapshot["edges"]:
                continue
            scores[candidate] += first["weight"] + second["weight"]
            bridges[candidate].append(bridge)
    return scores, bridges


def _expected_suggest(snapshot, handle, limit):
    scores, bridges = _suggest_scores(snapshot, handle)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [
        {
            "handle": candidate,
            "display_name": snapshot["members"][candidate],
            "score": score,
            "via": sorted(bridges[candidate]),
        }
        for candidate, score in ordered
    ]


def _expected_stats(snapshot):
    members = []
    for handle in sorted(snapshot["members"]):
        outgoing = _out_edges(snapshot, handle)
        incoming = _in_edges(snapshot, handle)
        members.append(
            {
                "handle": handle,
                "out_degree": len(outgoing),
                "in_degree": len(incoming),
                "out_weight_total": sum(edge["weight"] for edge in outgoing.values()),
                "in_weight_total": sum(edge["weight"] for edge in incoming.values()),
            }
        )
    mutual_pairs = 0
    for (source, target), edge in snapshot["edges"].items():
        if source >= target:
            continue
        reverse = snapshot["edges"].get((target, source))
        if edge["confirmed"] and reverse is not None and reverse["confirmed"]:
            mutual_pairs += 1
    return {
        "member_count": len(snapshot["members"]),
        "connection_count": len(snapshot["edges"]),
        "confirmed_connection_count": sum(
            1 for edge in snapshot["edges"].values() if edge["confirmed"]
        ),
        "mutual_pair_count": mutual_pairs,
        "members": members,
    }


def _upsert(client, src, dst, weight, role, established, confirmed):
    client.query(
        UPSERT_EDGE,
        src=src,
        dst=dst,
        weight=weight,
        role=role,
        established=established,
        confirmed=confirmed,
    )


def _parse_ts(value):
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# session setup: reset the data and import the seed dataset once
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def seeded(client):
    assert os.path.isfile(GRAPH_PY), f"The CLI entrypoint {GRAPH_PY} does not exist."

    for path in (MISSING_FILE, ATOMIC_FILE, BROKEN_FILE):
        if os.path.exists(path):
            os.remove(path)

    member_type_exists = client.query_single(
        "select exists (select schema::ObjectType filter .name = 'default::Member')"
    )
    assert member_type_exists, (
        "No object type 'default::Member' exists in the database, so the schema was never "
        "created."
    )

    try:
        client.execute("update Member set { connections := {} }")
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"Could not clear Member.connections before seeding: {exc!r}"
        ) from exc
    try:
        client.execute("delete Member")
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"Could not delete the existing members before seeding: {exc!r}") from exc

    remaining = client.query_single("select count(Member)")
    assert remaining == 0, f"Expected an empty database before seeding, {remaining} members left."

    payload, elapsed = _cli_json(["load", "--file", SEED_FILE], timeout=LOAD_TIMEOUT)
    assert elapsed <= LOAD_TIMEOUT, f"`load` took {elapsed:.1f}s, the budget is {LOAD_TIMEOUT}s."
    assert set(payload) == {
        "members_total",
        "members_created",
        "connections_total",
        "connections_created",
    }, f"Unexpected keys in the `load` output: {sorted(payload)}"
    assert payload["members_total"] == EXPECTED_MEMBERS, (
        f"`load` reported members_total={payload['members_total']}, expected {EXPECTED_MEMBERS}."
    )
    assert payload["connections_total"] == EXPECTED_CONNECTIONS, (
        f"`load` reported connections_total={payload['connections_total']}, "
        f"expected {EXPECTED_CONNECTIONS}."
    )
    assert payload["members_created"] == EXPECTED_MEMBERS, (
        f"`load` into an empty database reported members_created="
        f"{payload['members_created']}, expected {EXPECTED_MEMBERS}."
    )
    assert payload["connections_created"] == EXPECTED_CONNECTIONS, (
        f"`load` into an empty database reported connections_created="
        f"{payload['connections_created']}, expected {EXPECTED_CONNECTIONS}."
    )
    return payload


# ---------------------------------------------------------------------------
# 1. schema contract
# ---------------------------------------------------------------------------
def test_cli_entrypoint_exists(client):
    assert os.path.isfile(GRAPH_PY), f"The CLI entrypoint {GRAPH_PY} does not exist."


def test_default_module_has_exactly_one_object_type(client, seeded):
    rows = json.loads(
        client.query_json(
            "select schema::ObjectType { name } filter .name like 'default::%'"
        )
    )
    names = sorted(row["name"] for row in rows)
    assert names == ["default::Member"], (
        "The 'default' module must contain exactly one object type, 'default::Member', but "
        f"introspection reports: {names}. Per-edge metadata must not be modelled with an "
        "extra object type or join table."
    )


def test_member_declares_exactly_handle_and_display_name(client, seeded):
    rows = json.loads(client.query_single_json(INTROSPECT_QUERY))
    assert rows is not None, "Introspection did not return the 'default::Member' object type."
    properties = {
        prop["name"]: prop for prop in rows["properties"] if prop["name"] != "id"
    }
    assert sorted(properties) == ["display_name", "handle"], (
        "Member must declare exactly the properties 'handle' and 'display_name' besides the "
        f"implicit 'id', but introspection reports: {sorted(properties)}"
    )
    for name in ("handle", "display_name"):
        assert properties[name]["target"]["name"] == "std::str", (
            f"Member.{name} must target std::str, got "
            f"{properties[name]['target']['name']}."
        )
        assert properties[name]["cardinality"] == "One", (
            f"Member.{name} must be single, got cardinality "
            f"{properties[name]['cardinality']}."
        )
        assert properties[name]["required"] is True, f"Member.{name} must be required."


def test_edge_metadata_is_not_duplicated_on_member(client, seeded):
    rows = json.loads(client.query_single_json(INTROSPECT_QUERY))
    property_names = {prop["name"] for prop in rows["properties"]}
    leaked = sorted(property_names & set(LINK_PROP_TYPES))
    assert leaked == [], (
        "Per-edge metadata must not be stored as properties of Member, but these appeared as "
        f"Member properties: {leaked}"
    )


def test_connections_link_shape(client, seeded):
    rows = json.loads(client.query_single_json(INTROSPECT_QUERY))
    links = {link["name"]: link for link in rows["links"]}
    assert "connections" in links, (
        f"Member must have a link named 'connections'; found links: {sorted(links)}"
    )
    link = links["connections"]
    assert link["target"]["name"] == "default::Member", (
        f"Member.connections must target default::Member, got {link['target']['name']}."
    )
    assert link["cardinality"] == "Many", (
        f"Member.connections must have cardinality 'Many', got {link['cardinality']}."
    )


def test_metadata_lives_on_the_connections_link(client, seeded):
    rows = json.loads(client.query_single_json(INTROSPECT_QUERY))
    links = {link["name"]: link for link in rows["links"]}
    assert "connections" in links, "Member has no link named 'connections'."
    own = {
        prop["name"]: prop
        for prop in links["connections"]["properties"]
        if prop["name"] not in ("source", "target")
    }
    assert sorted(own) == sorted(LINK_PROP_TYPES), (
        "The 'connections' link itself must carry exactly the properties "
        f"{sorted(LINK_PROP_TYPES)} (besides the implicit source/target), but introspection "
        f"reports: {sorted(own)}"
    )
    for name, expected_type in LINK_PROP_TYPES.items():
        assert own[name]["target"]["name"] == expected_type, (
            f"The '{name}' property of the connections link must target {expected_type}, got "
            f"{own[name]['target']['name']}."
        )
        assert own[name]["cardinality"] == "One", (
            f"The '{name}' property of the connections link must be single, got "
            f"{own[name]['cardinality']}."
        )


def test_handle_is_unique(client, seeded):
    before = client.query_single("select count(Member)")
    assert before == EXPECTED_MEMBERS, (
        f"Expected {EXPECTED_MEMBERS} members right after seeding, found {before}."
    )
    duplicated = False
    try:
        client.query(
            "insert Member { handle := 'mbr_000', display_name := 'duplicate probe' }"
        )
        duplicated = True
    except Exception:  # noqa: BLE001 - a uniqueness violation is what we want
        pass
    finally:
        if duplicated:
            client.execute("delete Member filter .display_name = 'duplicate probe'")
    assert not duplicated, (
        "Inserting a second member with handle 'mbr_000' succeeded; Member.handle must be "
        "unique."
    )
    after = client.query_single("select count(Member)")
    assert after == EXPECTED_MEMBERS, (
        f"The member count changed from {EXPECTED_MEMBERS} to {after} after the rejected "
        "duplicate insert."
    )


# ---------------------------------------------------------------------------
# 2. seed fidelity
# ---------------------------------------------------------------------------
def test_seed_import_is_faithful(client, seeded):
    payload = _seed_payload()
    snapshot = _snapshot(client)

    assert len(snapshot["members"]) == EXPECTED_MEMBERS, (
        f"Expected {EXPECTED_MEMBERS} members in the database, found "
        f"{len(snapshot['members'])}."
    )
    assert len(snapshot["edges"]) == EXPECTED_CONNECTIONS, (
        f"Expected {EXPECTED_CONNECTIONS} directed connections in the database, found "
        f"{len(snapshot['edges'])}."
    )

    for member in payload["members"]:
        assert member["handle"] in snapshot["members"], (
            f"Member {member['handle']} from the seed file is missing in the database."
        )
        assert snapshot["members"][member["handle"]] == member["display_name"], (
            f"Member {member['handle']} has display_name "
            f"{snapshot['members'][member['handle']]!r}, expected "
            f"{member['display_name']!r}."
        )

    for entry in payload["connections"]:
        key = (entry["from"], entry["to"])
        assert key in snapshot["edges"], f"The seeded connection {key} is missing."
        edge = snapshot["edges"][key]
        assert edge["weight"] == entry["weight"], (
            f"Connection {key} has weight {edge['weight']}, expected {entry['weight']}."
        )
        assert edge["role"] == entry["role"], (
            f"Connection {key} has role {edge['role']!r}, expected {entry['role']!r}."
        )
        assert edge["confirmed"] == entry["confirmed"], (
            f"Connection {key} has confirmed={edge['confirmed']}, expected "
            f"{entry['confirmed']}."
        )
        assert _parse_ts(edge["established"]) == _parse_ts(entry["established"]), (
            f"Connection {key} has established={edge['established']}, expected "
            f"{entry['established']}."
        )


def test_spot_check_seeded_edge(client, seeded):
    snapshot = _snapshot(client)
    edge = snapshot["edges"].get(("mbr_000", "mbr_003"))
    assert edge is not None, "The seeded connection mbr_000 -> mbr_003 is missing."
    assert edge["weight"] == 10, f"mbr_000 -> mbr_003 should have weight 10, got {edge['weight']}."
    assert edge["role"] == "co_author", (
        f"mbr_000 -> mbr_003 should have role 'co_author', got {edge['role']!r}."
    )
    assert edge["confirmed"] is True, "mbr_000 -> mbr_003 should be confirmed."
    assert _parse_ts(edge["established"]) == _parse_ts("2024-01-01T08:21:00+00:00"), (
        f"mbr_000 -> mbr_003 should have established 2024-01-01T08:21:00+00:00, got "
        f"{edge['established']}."
    )


def test_load_is_idempotent(client, seeded):
    before = _fingerprint(_snapshot(client))
    payload, elapsed = _cli_json(["load", "--file", SEED_FILE], timeout=LOAD_TIMEOUT)
    assert elapsed <= LOAD_TIMEOUT, f"`load` took {elapsed:.1f}s, the budget is {LOAD_TIMEOUT}s."
    assert payload["members_total"] == EXPECTED_MEMBERS, (
        f"members_total={payload['members_total']}, expected {EXPECTED_MEMBERS}."
    )
    assert payload["connections_total"] == EXPECTED_CONNECTIONS, (
        f"connections_total={payload['connections_total']}, expected {EXPECTED_CONNECTIONS}."
    )
    assert payload["members_created"] == 0, (
        f"Re-running `load` reported members_created={payload['members_created']}, expected 0."
    )
    assert payload["connections_created"] == 0, (
        f"Re-running `load` reported connections_created={payload['connections_created']}, "
        "expected 0."
    )
    assert _fingerprint(_snapshot(client)) == before, (
        "Re-running `load` with the same file changed the graph; it must be idempotent."
    )


# ---------------------------------------------------------------------------
# 3. aggregate query
# ---------------------------------------------------------------------------
def test_stats_matches_recomputation(client, seeded):
    snapshot = _snapshot(client)
    expected = _expected_stats(snapshot)
    payload, elapsed = _cli_json(["stats"])
    assert elapsed <= CMD_TIMEOUT, f"`stats` took {elapsed:.1f}s, the budget is {CMD_TIMEOUT}s."

    assert set(payload) == {
        "member_count",
        "connection_count",
        "confirmed_connection_count",
        "mutual_pair_count",
        "members",
    }, f"Unexpected keys in the `stats` output: {sorted(payload)}"

    assert payload["member_count"] == EXPECTED_MEMBERS == expected["member_count"], (
        f"member_count={payload['member_count']}, expected {expected['member_count']}."
    )
    assert payload["connection_count"] == EXPECTED_CONNECTIONS == expected["connection_count"], (
        f"connection_count={payload['connection_count']}, expected "
        f"{expected['connection_count']}."
    )
    assert (
        payload["confirmed_connection_count"]
        == EXPECTED_CONFIRMED
        == expected["confirmed_connection_count"]
    ), (
        f"confirmed_connection_count={payload['confirmed_connection_count']}, expected "
        f"{expected['confirmed_connection_count']}."
    )
    assert payload["mutual_pair_count"] == EXPECTED_MUTUAL_PAIRS == expected["mutual_pair_count"], (
        f"mutual_pair_count={payload['mutual_pair_count']}, expected "
        f"{expected['mutual_pair_count']}."
    )

    assert len(payload["members"]) == len(expected["members"]), (
        f"The members array has {len(payload['members'])} entries, expected "
        f"{len(expected['members'])}."
    )
    assert [entry["handle"] for entry in payload["members"]] == [
        entry["handle"] for entry in expected["members"]
    ], "The members array is not ordered by handle ascending, or contains wrong handles."
    for actual, wanted in zip(payload["members"], expected["members"]):
        assert set(actual) == set(wanted), (
            f"Unexpected keys for member {wanted['handle']}: {sorted(actual)}"
        )
        assert actual == wanted, (
            f"Aggregates for {wanted['handle']} are {actual}, expected {wanted}."
        )


def test_stats_reports_isolated_members_with_zeroes(client, seeded):
    payload, _ = _cli_json(["stats"])
    entries = {entry["handle"]: entry for entry in payload["members"]}
    for handle in ("mbr_198", "mbr_199"):
        assert handle in entries, f"{handle} is missing from the `stats` members array."
        entry = entries[handle]
        assert entry["out_degree"] == 0 and entry["in_degree"] == 0, (
            f"{handle} has no connections in the seed dataset, but `stats` reports {entry}."
        )
        assert entry["out_weight_total"] == 0 and entry["in_weight_total"] == 0, (
            f"{handle} should report zero weight totals, but `stats` reports {entry}."
        )


# ---------------------------------------------------------------------------
# 4. strongest connections
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("handle", ["mbr_000", "mbr_007", "mbr_042", "mbr_137"])
def test_top_matches_recomputation(client, seeded, handle):
    snapshot = _snapshot(client)
    expected = _expected_top(snapshot, handle, 5)
    payload, elapsed = _cli_json(["top", "--handle", handle, "--limit", "5"])
    assert elapsed <= CMD_TIMEOUT, f"`top` took {elapsed:.1f}s, the budget is {CMD_TIMEOUT}s."
    assert isinstance(payload, list), f"`top` must print a JSON array, got {type(payload)}."
    assert len(payload) == len(expected), (
        f"`top --handle {handle} --limit 5` returned {len(payload)} items, expected "
        f"{len(expected)}."
    )
    for actual, wanted in zip(payload, expected):
        assert set(actual) == set(wanted), (
            f"Unexpected keys in a `top` element for {handle}: {sorted(actual)}"
        )
        assert isinstance(actual["confirmed"], bool), (
            f"'confirmed' must be a JSON boolean, got {actual['confirmed']!r}."
        )
    assert payload == expected, (
        f"`top --handle {handle} --limit 5` returned {payload}, expected {expected} "
        "(weight descending, ties broken by target handle ascending)."
    )


def test_top_tie_break_is_by_handle(client, seeded):
    snapshot = _snapshot(client)
    outgoing = _out_edges(snapshot, "mbr_042")
    weights = collections.Counter(edge["weight"] for edge in outgoing.values())
    assert any(count > 1 for count in weights.values()), (
        "The seeded data for mbr_042 no longer contains a weight tie; the tie-break case "
        "cannot be exercised."
    )
    payload, _ = _cli_json(["top", "--handle", "mbr_042", "--limit", "5"])
    assert payload == _expected_top(snapshot, "mbr_042", 5), (
        "`top` for mbr_042 must break equal weights by target handle ascending; got "
        f"{payload}."
    )


def test_top_limit_boundaries(client, seeded):
    snapshot = _snapshot(client)
    single, _ = _cli_json(["top", "--handle", "mbr_000", "--limit", "1"])
    assert single == _expected_top(snapshot, "mbr_000", 1), (
        f"`top --handle mbr_000 --limit 1` returned {single}."
    )
    everything, _ = _cli_json(["top", "--handle", "mbr_000", "--limit", "500"])
    expected_all = _expected_top(snapshot, "mbr_000", 500)
    assert everything == expected_all, (
        f"`top --handle mbr_000 --limit 500` must return all {len(expected_all)} outgoing "
        f"connections, got {len(everything)}."
    )


def test_top_of_isolated_member_is_empty(client, seeded):
    payload, _ = _cli_json(["top", "--handle", "mbr_198", "--limit", "5"])
    assert payload == [], f"`top --handle mbr_198 --limit 5` must return [], got {payload}."


# ---------------------------------------------------------------------------
# 5. mutual neighbourhoods
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("handle", ["mbr_000", "mbr_011", "mbr_090", "mbr_198"])
def test_mutual_matches_recomputation(client, seeded, handle):
    snapshot = _snapshot(client)
    expected = _expected_mutual(snapshot, handle)
    payload, elapsed = _cli_json(["mutual", "--handle", handle])
    assert elapsed <= CMD_TIMEOUT, f"`mutual` took {elapsed:.1f}s, budget {CMD_TIMEOUT}s."
    assert payload == expected, (
        f"`mutual --handle {handle}` returned {payload}, expected {expected}."
    )


def test_mutual_ignores_half_confirmed_pairs(client, seeded):
    snapshot = _snapshot(client)
    half = sorted(
        (source, target)
        for (source, target), edge in snapshot["edges"].items()
        if source < target
        and (target, source) in snapshot["edges"]
        and edge["confirmed"] != snapshot["edges"][(target, source)]["confirmed"]
    )
    assert half, (
        "The database no longer contains a reciprocal pair with only one confirmed "
        "direction, so this case cannot be exercised."
    )
    left, right = half[0]
    from_left, _ = _cli_json(["mutual", "--handle", left])
    from_right, _ = _cli_json(["mutual", "--handle", right])
    assert right not in from_left, (
        f"{right} must not appear in `mutual --handle {left}`: only one direction of that "
        "pair is confirmed."
    )
    assert left not in from_right, (
        f"{left} must not appear in `mutual --handle {right}`: only one direction of that "
        "pair is confirmed."
    )
    assert from_left == _expected_mutual(snapshot, left), (
        f"`mutual --handle {left}` returned {from_left}, expected "
        f"{_expected_mutual(snapshot, left)}."
    )


# ---------------------------------------------------------------------------
# 6. second-degree suggestions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("handle", ["mbr_000", "mbr_055", "mbr_123"])
def test_suggest_matches_recomputation(client, seeded, handle):
    snapshot = _snapshot(client)
    expected = _expected_suggest(snapshot, handle, 5)
    payload, elapsed = _cli_json(["suggest", "--handle", handle, "--limit", "5"])
    assert elapsed <= CMD_TIMEOUT, f"`suggest` took {elapsed:.1f}s, budget {CMD_TIMEOUT}s."
    assert isinstance(payload, list), f"`suggest` must print a JSON array, got {type(payload)}."
    assert len(payload) == len(expected), (
        f"`suggest --handle {handle} --limit 5` returned {len(payload)} items, expected "
        f"{len(expected)}."
    )
    for actual, wanted in zip(payload, expected):
        assert set(actual) == set(wanted), (
            f"Unexpected keys in a `suggest` element for {handle}: {sorted(actual)}"
        )
    assert payload == expected, (
        f"`suggest --handle {handle} --limit 5` returned {payload}, expected {expected}."
    )


def test_suggest_excludes_direct_targets_and_self(client, seeded):
    snapshot = _snapshot(client)
    payload, _ = _cli_json(["suggest", "--handle", "mbr_000", "--limit", "20"])
    direct = set(_out_edges(snapshot, "mbr_000"))
    for entry in payload:
        assert entry["handle"] != "mbr_000", "`suggest` must never suggest the member itself."
        assert entry["handle"] not in direct, (
            f"{entry['handle']} is already a direct connection of mbr_000 and must not be "
            "suggested."
        )
    assert payload == _expected_suggest(snapshot, "mbr_000", 20), (
        "`suggest --handle mbr_000 --limit 20` does not match the recomputation."
    )


def test_suggest_only_traverses_confirmed_edges(client, seeded):
    snapshot = _snapshot(client)
    confirmed_scores, _ = _suggest_scores(snapshot, "mbr_000", confirmed_only=True)
    all_scores, _ = _suggest_scores(snapshot, "mbr_000", confirmed_only=False)
    only_unconfirmed = set(all_scores) - set(confirmed_scores)
    assert only_unconfirmed, (
        "The database no longer has a candidate that is reachable only through an "
        "unconfirmed edge, so this case cannot be exercised."
    )
    payload, _ = _cli_json(["suggest", "--handle", "mbr_000", "--limit", "500"])
    returned = {entry["handle"] for entry in payload}
    leaked = sorted(returned & only_unconfirmed)
    assert leaked == [], (
        "These candidates are only reachable through unconfirmed connections and must not be "
        f"suggested for mbr_000: {leaked}"
    )


def test_suggest_of_isolated_member_is_empty(client, seeded):
    payload, _ = _cli_json(["suggest", "--handle", "mbr_199", "--limit", "5"])
    assert payload == [], f"`suggest --handle mbr_199 --limit 5` must return [], got {payload}."


# ---------------------------------------------------------------------------
# 7. mutations through the CLI
# ---------------------------------------------------------------------------
def test_connect_creates_edge(client, seeded):
    payload, _ = _cli_json(
        ["connect", "--from", "mbr_198", "--to", "mbr_000", "--weight", "44", "--role", "scout"]
    )
    assert set(payload) == {"from", "to", "weight", "role", "confirmed", "created"}, (
        f"Unexpected keys in the `connect` output: {sorted(payload)}"
    )
    assert payload["created"] is True, "`connect` must report created=true for a new connection."
    assert payload["weight"] == 44 and payload["role"] == "scout", (
        f"`connect` reported {payload}, expected weight 44 and role 'scout'."
    )
    assert payload["confirmed"] is False, "A brand-new connection must not be confirmed."

    snapshot = _snapshot(client)
    edge = snapshot["edges"].get(("mbr_198", "mbr_000"))
    assert edge is not None, "The connection mbr_198 -> mbr_000 was not stored in the database."
    assert edge["weight"] == 44, f"Stored weight is {edge['weight']}, expected 44."
    assert edge["role"] == "scout", f"Stored role is {edge['role']!r}, expected 'scout'."
    assert edge["confirmed"] is False, "The stored connection must not be confirmed."
    assert edge["established"] is not None, "The stored connection has no 'established' value."
    age = abs(
        (datetime.datetime.now(datetime.timezone.utc) - _parse_ts(edge["established"])).total_seconds()
    )
    assert age <= 86400, (
        f"'established' of the new connection is {edge['established']}, which is not close to "
        "the current time."
    )

    stats, _ = _cli_json(["stats"])
    assert stats["connection_count"] == EXPECTED_CONNECTIONS + 1, (
        f"connection_count is {stats['connection_count']}, expected "
        f"{EXPECTED_CONNECTIONS + 1} after adding one connection."
    )
    entry = next(item for item in stats["members"] if item["handle"] == "mbr_198")
    assert entry["out_degree"] == 1 and entry["out_weight_total"] == 44, (
        f"`stats` reports {entry} for mbr_198, expected out_degree 1 and out_weight_total 44."
    )


def test_connect_reweight_preserves_established_and_confirmed(client, seeded):
    before = _snapshot(client)
    original = before["edges"][("mbr_198", "mbr_000")]
    payload, _ = _cli_json(
        ["connect", "--from", "mbr_198", "--to", "mbr_000", "--weight", "91", "--role", "lead"]
    )
    assert payload["created"] is False, (
        "`connect` on an existing connection must report created=false."
    )
    assert payload["weight"] == 91 and payload["role"] == "lead", (
        f"`connect` reported {payload}, expected weight 91 and role 'lead'."
    )

    after = _snapshot(client)
    edge = after["edges"][("mbr_198", "mbr_000")]
    assert edge["weight"] == 91, f"Stored weight is {edge['weight']}, expected 91."
    assert edge["role"] == "lead", f"Stored role is {edge['role']!r}, expected 'lead'."
    assert edge["confirmed"] is False, "Re-weighting must not change the confirmation flag."
    assert _parse_ts(edge["established"]) == _parse_ts(original["established"]), (
        f"Re-weighting changed 'established' from {original['established']} to "
        f"{edge['established']}; it must be preserved."
    )

    untouched_before = {
        key: value for key, value in before["edges"].items() if key != ("mbr_198", "mbr_000")
    }
    untouched_after = {
        key: value for key, value in after["edges"].items() if key != ("mbr_198", "mbr_000")
    }
    assert untouched_before == untouched_after, (
        "Re-weighting one connection changed other connections in the database."
    )


def test_connect_reweight_preserves_seeded_metadata(client, seeded):
    before = _snapshot(client)["edges"][("mbr_000", "mbr_003")]
    payload, _ = _cli_json(
        ["connect", "--from", "mbr_000", "--to", "mbr_003", "--weight", "7", "--role", "reviewer"]
    )
    assert payload["created"] is False, "mbr_000 -> mbr_003 already exists; created must be false."
    assert payload["confirmed"] is True, (
        "mbr_000 -> mbr_003 is a confirmed seeded connection; re-weighting must keep it "
        f"confirmed, but the CLI reported {payload}."
    )
    edge = _snapshot(client)["edges"][("mbr_000", "mbr_003")]
    assert edge["weight"] == 7 and edge["role"] == "reviewer", (
        f"Stored values are {edge}, expected weight 7 and role 'reviewer'."
    )
    assert edge["confirmed"] is True, "Re-weighting cleared the confirmation flag."
    assert _parse_ts(edge["established"]) == _parse_ts("2024-01-01T08:21:00+00:00"), (
        f"Re-weighting changed 'established' to {edge['established']}; the seeded value "
        "2024-01-01T08:21:00+00:00 must survive."
    )


def test_confirm_requires_both_directions(client, seeded):
    before = _fingerprint(_snapshot(client))
    proc, _ = _cli(["confirm", "--from", "mbr_198", "--to", "mbr_000"])
    assert proc.returncode == 4, (
        "`confirm` must exit 4 when the reciprocal connection is missing, exited "
        f"{proc.returncode} (stdout={proc.stdout!r}, stderr={proc.stderr!r})."
    )
    assert proc.stdout.strip() == "", "A failed `confirm` must not print anything on stdout."
    assert proc.stderr.strip() != "", "A failed `confirm` must print a diagnostic on stderr."
    assert _fingerprint(_snapshot(client)) == before, (
        "A rejected `confirm` must leave the database unchanged."
    )


def test_confirm_updates_both_directions(client, seeded):
    created, _ = _cli_json(
        ["connect", "--from", "mbr_000", "--to", "mbr_198", "--weight", "12", "--role", "peer"]
    )
    assert created["created"] is True, "mbr_000 -> mbr_198 should be a new connection."

    payload, _ = _cli_json(["confirm", "--from", "mbr_198", "--to", "mbr_000"])
    assert set(payload) == {"from", "to", "changed"}, (
        f"Unexpected keys in the `confirm` output: {sorted(payload)}"
    )
    assert payload["changed"] == 2, (
        f"`confirm` reported changed={payload['changed']}, expected 2 (both directions were "
        "unconfirmed)."
    )

    snapshot = _snapshot(client)
    forward = snapshot["edges"][("mbr_198", "mbr_000")]
    backward = snapshot["edges"][("mbr_000", "mbr_198")]
    assert forward["confirmed"] is True, "mbr_198 -> mbr_000 was not confirmed."
    assert backward["confirmed"] is True, (
        "mbr_000 -> mbr_198 was not confirmed; `confirm` must update both directions."
    )
    assert forward["weight"] == 91 and forward["role"] == "lead", (
        f"`confirm` changed mbr_198 -> mbr_000 to {forward}; weight 91 / role 'lead' must "
        "survive."
    )
    assert backward["weight"] == 12 and backward["role"] == "peer", (
        f"`confirm` changed mbr_000 -> mbr_198 to {backward}; weight 12 / role 'peer' must "
        "survive."
    )
    assert forward["established"] is not None and backward["established"] is not None, (
        "`confirm` cleared an 'established' value."
    )

    from_198, _ = _cli_json(["mutual", "--handle", "mbr_198"])
    from_000, _ = _cli_json(["mutual", "--handle", "mbr_000"])
    assert from_198 == _expected_mutual(snapshot, "mbr_198"), (
        f"`mutual --handle mbr_198` returned {from_198}, expected "
        f"{_expected_mutual(snapshot, 'mbr_198')}."
    )
    assert "mbr_000" in from_198, "mbr_000 must now be mutually confirmed with mbr_198."
    assert "mbr_198" in from_000, "mbr_198 must now be mutually confirmed with mbr_000."


def test_confirm_is_idempotent(client, seeded):
    before = _fingerprint(_snapshot(client))
    payload, _ = _cli_json(["confirm", "--from", "mbr_000", "--to", "mbr_198"])
    assert payload["changed"] == 0, (
        f"Re-confirming an already confirmed pair must report changed=0, got "
        f"{payload['changed']}."
    )
    assert _fingerprint(_snapshot(client)) == before, (
        "Re-confirming an already confirmed pair changed the database."
    )


# ---------------------------------------------------------------------------
# 8. verifier-driven database mutations must be reflected
# ---------------------------------------------------------------------------
def test_direct_weight_change_is_reflected_in_top(client, seeded):
    original = _snapshot(client)["edges"][("mbr_000", "mbr_003")]
    _upsert(
        client,
        "mbr_000",
        "mbr_003",
        100,
        original["role"],
        _parse_ts(original["established"]),
        original["confirmed"],
    )
    snapshot = _snapshot(client)
    assert snapshot["edges"][("mbr_000", "mbr_003")]["weight"] == 100, (
        "The verifier could not raise the weight of mbr_000 -> mbr_003 to 100."
    )
    payload, _ = _cli_json(["top", "--handle", "mbr_000", "--limit", "3"])
    expected = _expected_top(snapshot, "mbr_000", 3)
    assert payload == expected, (
        f"`top --handle mbr_000 --limit 3` returned {payload}, expected {expected} after the "
        "weight was changed directly in the database."
    )
    assert payload[0]["handle"] == "mbr_003" and payload[0]["weight"] == 100, (
        f"mbr_003 with weight 100 must now be the strongest connection of mbr_000, got "
        f"{payload[0]}."
    )


def test_direct_unconfirm_is_reflected_in_mutual(client, seeded):
    snapshot = _snapshot(client)
    partners = _expected_mutual(snapshot, "mbr_011")
    assert partners, "mbr_011 has no mutually confirmed partner to un-confirm."
    partner = partners[0]
    stats_before, _ = _cli_json(["stats"])

    edge = snapshot["edges"][("mbr_011", partner)]
    _upsert(
        client,
        "mbr_011",
        partner,
        edge["weight"],
        edge["role"],
        _parse_ts(edge["established"]),
        False,
    )
    after = _snapshot(client)
    assert after["edges"][("mbr_011", partner)]["confirmed"] is False, (
        f"The verifier could not clear the confirmation flag of mbr_011 -> {partner}."
    )
    assert after["edges"][(partner, "mbr_011")]["confirmed"] is True, (
        f"The reverse edge {partner} -> mbr_011 should still be confirmed."
    )

    from_011, _ = _cli_json(["mutual", "--handle", "mbr_011"])
    from_partner, _ = _cli_json(["mutual", "--handle", partner])
    assert partner not in from_011, (
        f"{partner} must disappear from `mutual --handle mbr_011` once one direction is no "
        "longer confirmed."
    )
    assert "mbr_011" not in from_partner, (
        f"mbr_011 must disappear from `mutual --handle {partner}` once one direction is no "
        "longer confirmed."
    )
    assert from_011 == _expected_mutual(after, "mbr_011"), (
        f"`mutual --handle mbr_011` returned {from_011}, expected "
        f"{_expected_mutual(after, 'mbr_011')}."
    )

    stats_after, _ = _cli_json(["stats"])
    assert stats_after["mutual_pair_count"] == stats_before["mutual_pair_count"] - 1, (
        f"mutual_pair_count went from {stats_before['mutual_pair_count']} to "
        f"{stats_after['mutual_pair_count']}, expected a decrease of exactly 1."
    )
    assert stats_after["mutual_pair_count"] == _expected_stats(after)["mutual_pair_count"], (
        "mutual_pair_count does not match the recomputation on the new database state."
    )


def test_direct_edge_removal_is_reflected(client, seeded):
    snapshot = _snapshot(client)
    targets = sorted(_out_edges(snapshot, "mbr_042"))
    assert targets, "mbr_042 has no outgoing connections to remove."
    victim = targets[0]
    stats_before, _ = _cli_json(["stats"])

    client.query(REMOVE_EDGE, src="mbr_042", dst=victim)
    after = _snapshot(client)
    assert ("mbr_042", victim) not in after["edges"], (
        f"The verifier could not remove the connection mbr_042 -> {victim}."
    )

    stats_after, _ = _cli_json(["stats"])
    assert stats_after["connection_count"] == stats_before["connection_count"] - 1, (
        f"connection_count went from {stats_before['connection_count']} to "
        f"{stats_after['connection_count']}, expected a decrease of exactly 1."
    )
    entry_before = next(e for e in stats_before["members"] if e["handle"] == "mbr_042")
    entry_after = next(e for e in stats_after["members"] if e["handle"] == "mbr_042")
    assert entry_after["out_degree"] == entry_before["out_degree"] - 1, (
        f"mbr_042's out_degree went from {entry_before['out_degree']} to "
        f"{entry_after['out_degree']}, expected a decrease of exactly 1."
    )

    top_payload, _ = _cli_json(["top", "--handle", "mbr_042", "--limit", "500"])
    assert victim not in {item["handle"] for item in top_payload}, (
        f"`top --handle mbr_042` still lists the removed connection to {victim}."
    )
    assert top_payload == _expected_top(after, "mbr_042", 500), (
        "`top --handle mbr_042 --limit 500` does not match the recomputation after the "
        "removal."
    )
    suggest_payload, _ = _cli_json(["suggest", "--handle", "mbr_042", "--limit", "5"])
    assert suggest_payload == _expected_suggest(after, "mbr_042", 5), (
        "`suggest --handle mbr_042 --limit 5` does not match the recomputation after the "
        "removal."
    )


def test_direct_new_member_is_reflected_in_stats(client, seeded):
    existing = client.query_single(
        "select count(Member filter .handle = 'zz_probe_1')"
    )
    if existing == 0:
        client.query(
            "insert Member { handle := <str>$h, display_name := <str>$d }",
            h="zz_probe_1",
            d="Probe One",
        )
    _upsert(
        client,
        "mbr_007",
        "zz_probe_1",
        5,
        "probe",
        datetime.datetime(2025, 5, 5, 12, 0, 0, tzinfo=datetime.timezone.utc),
        False,
    )
    snapshot = _snapshot(client)
    assert ("mbr_007", "zz_probe_1") in snapshot["edges"], (
        "The verifier could not create the connection mbr_007 -> zz_probe_1."
    )

    payload, _ = _cli_json(["stats"])
    expected = _expected_stats(snapshot)
    assert payload["member_count"] == EXPECTED_MEMBERS + 1 == expected["member_count"], (
        f"member_count is {payload['member_count']}, expected {EXPECTED_MEMBERS + 1} after a "
        "member was inserted directly in the database."
    )
    assert payload["members"][-1]["handle"] == "zz_probe_1", (
        "zz_probe_1 sorts last by handle and must be the final entry of the members array, "
        f"but the last entry is {payload['members'][-1]}."
    )
    assert payload["members"][-1] == {
        "handle": "zz_probe_1",
        "out_degree": 0,
        "in_degree": 1,
        "out_weight_total": 0,
        "in_weight_total": 5,
    }, f"Aggregates for zz_probe_1 are {payload['members'][-1]}."
    assert payload["members"] == expected["members"], (
        "The `stats` members array does not match the recomputation after the direct insert."
    )


# ---------------------------------------------------------------------------
# 9. invalid input is rejected without side effects
# ---------------------------------------------------------------------------
INVALID_CASES = [
    (["connect", "--from", "mbr_000", "--to", "nobody_here", "--weight", "5", "--role", "x"], 3),
    (["connect", "--from", "ghost", "--to", "mbr_000", "--weight", "5", "--role", "x"], 3),
    (["connect", "--from", "mbr_000", "--to", "mbr_000", "--weight", "5", "--role", "x"], 2),
    (["connect", "--from", "mbr_000", "--to", "mbr_004", "--weight", "0", "--role", "x"], 2),
    (["connect", "--from", "mbr_000", "--to", "mbr_004", "--weight", "101", "--role", "x"], 2),
    (["connect", "--from", "mbr_000", "--to", "mbr_004", "--weight", "abc", "--role", "x"], 2),
    (["top", "--handle", "nobody_here", "--limit", "3"], 3),
    (["top", "--handle", "mbr_000", "--limit", "0"], 2),
    (["top", "--handle", "mbr_000", "--limit", "-2"], 2),
    (["mutual", "--handle", "nobody_here"], 3),
    (["suggest", "--handle", "nobody_here", "--limit", "3"], 3),
    (["confirm", "--from", "mbr_000", "--to", "ghost"], 3),
    (["confirm", "--from", "mbr_000", "--to", "mbr_000"], 2),
    (["frobnicate"], 2),
    (["load", "--file", MISSING_FILE], 2),
]

INVALID_IDS = [
    "connect_unknown_target",
    "connect_unknown_source",
    "connect_self_edge",
    "connect_weight_zero",
    "connect_weight_too_large",
    "connect_weight_not_a_number",
    "top_unknown_handle",
    "top_limit_zero",
    "top_limit_negative",
    "mutual_unknown_handle",
    "suggest_unknown_handle",
    "confirm_unknown_handle",
    "confirm_same_handle",
    "unknown_subcommand",
    "load_missing_file",
]


@pytest.mark.parametrize("args,expected_code", INVALID_CASES, ids=INVALID_IDS)
def test_invalid_input_is_rejected_without_side_effects(client, seeded, args, expected_code):
    before = _fingerprint(_snapshot(client))
    proc, _ = _cli(args)
    assert proc.returncode == expected_code, (
        f"`python3 graph.py {' '.join(args)}` exited {proc.returncode}, expected "
        f"{expected_code} (stdout={proc.stdout!r}, stderr={proc.stderr!r})."
    )
    assert proc.stdout.strip() == "", (
        f"`python3 graph.py {' '.join(args)}` must print nothing on stdout, got "
        f"{proc.stdout!r}."
    )
    assert proc.stderr.strip() != "", (
        f"`python3 graph.py {' '.join(args)}` must print a diagnostic on stderr."
    )
    assert _fingerprint(_snapshot(client)) == before, (
        f"`python3 graph.py {' '.join(args)}` changed the database even though it failed."
    )


def test_load_rejects_unknown_endpoint_atomically(client, seeded):
    before = _fingerprint(_snapshot(client))
    payload = {
        "members": [
            {"handle": "atomic_a", "display_name": "Atomic A"},
            {"handle": "atomic_b", "display_name": "Atomic B"},
            {"handle": "atomic_c", "display_name": "Atomic C"},
        ],
        "connections": [
            {
                "from": "atomic_a",
                "to": "atomic_b",
                "weight": 5,
                "role": "peer",
                "established": "2025-01-01T00:00:00+00:00",
                "confirmed": True,
            },
            {
                "from": "atomic_b",
                "to": "atomic_c",
                "weight": 6,
                "role": "peer",
                "established": "2025-01-02T00:00:00+00:00",
                "confirmed": False,
            },
            {
                "from": "atomic_c",
                "to": "atomic_ghost",
                "weight": 7,
                "role": "peer",
                "established": "2025-01-03T00:00:00+00:00",
                "confirmed": False,
            },
        ],
    }
    with open(ATOMIC_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    proc, _ = _cli(["load", "--file", ATOMIC_FILE], timeout=LOAD_TIMEOUT)
    assert proc.returncode == 3, (
        "A `load` file whose connection points at the unknown handle 'atomic_ghost' must exit "
        f"3, got {proc.returncode} (stdout={proc.stdout!r}, stderr={proc.stderr!r})."
    )
    assert proc.stdout.strip() == "", "A rejected `load` must print nothing on stdout."
    assert proc.stderr.strip() != "", "A rejected `load` must print a diagnostic on stderr."

    after = _snapshot(client)
    for handle in ("atomic_a", "atomic_b", "atomic_c"):
        assert handle not in after["members"], (
            f"{handle} was created even though the `load` was rejected; the import must be "
            "all-or-nothing."
        )
    assert _fingerprint(after) == before, "A rejected `load` must leave the database unchanged."


def test_load_rejects_malformed_json(client, seeded):
    before = _fingerprint(_snapshot(client))
    with open(BROKEN_FILE, "w", encoding="utf-8") as handle:
        handle.write('{"members": [ {"handle": "broken", ')
    proc, _ = _cli(["load", "--file", BROKEN_FILE], timeout=LOAD_TIMEOUT)
    assert proc.returncode == 2, (
        f"A malformed seed file must be rejected with exit code 2, got {proc.returncode} "
        f"(stdout={proc.stdout!r}, stderr={proc.stderr!r})."
    )
    assert proc.stdout.strip() == "", "A rejected `load` must print nothing on stdout."
    assert proc.stderr.strip() != "", "A rejected `load` must print a diagnostic on stderr."
    assert _fingerprint(_snapshot(client)) == before, (
        "A malformed seed file must leave the database unchanged."
    )


# ---------------------------------------------------------------------------
# 10. determinism and recovery
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "args",
    [
        ["stats"],
        ["top", "--handle", "mbr_000", "--limit", "5"],
        ["mutual", "--handle", "mbr_000"],
        ["suggest", "--handle", "mbr_000", "--limit", "5"],
    ],
    ids=["stats", "top", "mutual", "suggest"],
)
def test_repeated_invocations_are_deterministic(client, seeded, args):
    first, _ = _cli(args)
    second, _ = _cli(args)
    assert first.returncode == 0 and second.returncode == 0, (
        f"`python3 graph.py {' '.join(args)}` must succeed on both runs "
        f"({first.returncode}, {second.returncode})."
    )
    assert first.stdout == second.stdout, (
        f"`python3 graph.py {' '.join(args)}` produced different output on two consecutive "
        "runs; the answers must be deterministic."
    )


def test_final_load_restores_seeded_values(client, seeded):
    payload, elapsed = _cli_json(["load", "--file", SEED_FILE], timeout=LOAD_TIMEOUT)
    assert elapsed <= LOAD_TIMEOUT, f"`load` took {elapsed:.1f}s, the budget is {LOAD_TIMEOUT}s."
    assert payload["members_created"] == 0, (
        f"All seeded members still exist, so members_created must be 0, got "
        f"{payload['members_created']}."
    )
    assert payload["connections_created"] == 1, (
        "Exactly one seeded connection was removed from the database during verification, so "
        f"connections_created must be 1, got {payload['connections_created']}."
    )

    seed = _seed_payload()
    snapshot = _snapshot(client)
    for entry in seed["connections"]:
        key = (entry["from"], entry["to"])
        assert key in snapshot["edges"], f"The seeded connection {key} was not restored."
        edge = snapshot["edges"][key]
        assert edge["weight"] == entry["weight"], (
            f"Connection {key} has weight {edge['weight']} after the final load, expected "
            f"{entry['weight']}."
        )
        assert edge["role"] == entry["role"], (
            f"Connection {key} has role {edge['role']!r} after the final load, expected "
            f"{entry['role']!r}."
        )
        assert edge["confirmed"] == entry["confirmed"], (
            f"Connection {key} has confirmed={edge['confirmed']} after the final load, "
            f"expected {entry['confirmed']}."
        )
        assert _parse_ts(edge["established"]) == _parse_ts(entry["established"]), (
            f"Connection {key} has established={edge['established']} after the final load, "
            f"expected {entry['established']}."
        )

    assert "zz_probe_1" in snapshot["members"], (
        "`load` must not delete members that the seed file does not mention (zz_probe_1)."
    )
    assert ("mbr_007", "zz_probe_1") in snapshot["edges"], (
        "`load` must not delete connections that the seed file does not mention "
        "(mbr_007 -> zz_probe_1)."
    )
    assert ("mbr_198", "mbr_000") in snapshot["edges"], (
        "`load` must not delete the connection mbr_198 -> mbr_000 created during verification."
    )
    assert snapshot["edges"][("mbr_198", "mbr_000")]["weight"] == 91, (
        "The connection mbr_198 -> mbr_000 must keep the weight 91 it was given earlier."
    )
    assert snapshot["edges"][("mbr_198", "mbr_000")]["confirmed"] is True, (
        "The connection mbr_198 -> mbr_000 must stay confirmed after the final load."
    )


def test_queries_still_match_after_final_load(client, seeded):
    snapshot = _snapshot(client)
    stats_payload, stats_elapsed = _cli_json(["stats"])
    assert stats_elapsed <= CMD_TIMEOUT, (
        f"`stats` took {stats_elapsed:.1f}s, the budget is {CMD_TIMEOUT}s."
    )
    assert stats_payload == _expected_stats(snapshot), (
        "`stats` does not match the recomputation after the final load."
    )
    for handle in ("mbr_000", "mbr_042", "mbr_123"):
        top_payload, _ = _cli_json(["top", "--handle", handle, "--limit", "5"])
        assert top_payload == _expected_top(snapshot, handle, 5), (
            f"`top --handle {handle} --limit 5` does not match the recomputation after the "
            "final load."
        )
        mutual_payload, _ = _cli_json(["mutual", "--handle", handle])
        assert mutual_payload == _expected_mutual(snapshot, handle), (
            f"`mutual --handle {handle}` does not match the recomputation after the final "
            "load."
        )
        suggest_payload, _ = _cli_json(["suggest", "--handle", handle, "--limit", "5"])
        assert suggest_payload == _expected_suggest(snapshot, handle, 5), (
            f"`suggest --handle {handle} --limit 5` does not match the recomputation after "
            "the final load."
        )
