import glob
import json
import os
import re
import shutil
import subprocess

import pytest

PROJECT_DIR = "/home/user/crm"
MIGRATIONS_DIR = os.path.join(PROJECT_DIR, "dbschema", "migrations")
REPORT_SCRIPT = os.path.join(PROJECT_DIR, "branch_report.py")

# Migration ids baked into the image (initial migration, then the "domain" migration).
INITIAL_MIGRATION_ID = "m1jfgezqqkznwxijetyevwiica5xkj4aix7wrv4pzonao5ktlpx2qa"
DOMAIN_MIGRATION_ID = "m1zcz7fikru2sczeluuuokfgq6ftjkgtcc5b7mkotxey3kot45triq"

# email -> (first_name, last_name, domain, stage, note)
EXPECTED_CONTACTS = {
    "ada@Example.COM": ("Ada", "Lovelace", "example.com", "active", "q3 pipeline"),
    "ann@ops.io": ("Ann-Marie", "de la Cruz", "ops.io", "lead", None),
    "grace@navy.mil": ("Grace", "Hopper", "navy.mil", "active", "renewal"),
    "jl@fleet.ORG": ("Jean", "Luc Picard", "fleet.org", "customer", None),
    "kim@studio.kr": ("Kim", "", "studio.kr", "lead", None),
    "ludwig@musik.DE": ("Ludwig", "van Beethoven", "musik.de", "customer", None),
    "mary@daily.NET": ("Mary", "Jane Watson", "daily.net", "churned", "lost to competitor"),
    "prince@music.io": ("Prince", "", "music.io", "lead", None),
    "renee@studio.fr": ("Renée", "Dupont-Laurent", "studio.fr", "lead", None),
    "sean@pub.IE": ("Seán", "O'Connor", "pub.ie", "lead", None),
    "wei@lab.CN": ("Wei", "Zhang", "lab.cn", "active", "trial extended"),
    "zed@ops.IO": ("Zed", "", "ops.io", "churned", None),
}

CONTACT_QUERY = (
    "select Contact { email, first_name, last_name, domain, stage, note } order by .email"
)

POINTER_QUERY = (
    "select schema::ObjectType { pointers: { name, required, target: { name }, "
    "constraints: { name } } } filter .name = 'default::Contact'"
)


def run(args, cwd=PROJECT_DIR, timeout=300):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="session")
def gel_server():
    """Every DB-dependent check must depend on this fixture (server may be down)."""
    start = shutil.which("gel-start.sh")
    assert start is not None, "gel-start.sh not found in PATH."
    proc = run([start], timeout=300)
    assert proc.returncode == 0, (
        f"gel-start.sh failed (exit {proc.returncode}).\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    probe = run(["gel", "query", "-F", "json", "select 1"])
    assert probe.returncode == 0, (
        "Could not reach the Gel instance from the project directory.\n"
        f"stdout: {probe.stdout}\nstderr: {probe.stderr}"
    )
    return True


def gel_json(query, branch=None):
    args = ["gel", "query"]
    if branch is not None:
        args += ["--branch", branch]
    args += ["-F", "json", query]
    proc = run(args)
    assert proc.returncode == 0, (
        f"Query failed ({' '.join(args[:5])} ...):\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return json.loads(proc.stdout)


def db_history(branch):
    proc = run(["gel", "migration", "log", "--from-db", "--branch", branch])
    assert proc.returncode == 0, (
        f"gel migration log --from-db --branch {branch} failed:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def pointer_map(branch):
    result = gel_json(POINTER_QUERY, branch=branch)
    assert result, f"default::Contact was not found on branch {branch}."
    return {p["name"]: p for p in result[0]["pointers"]}


def contact_rows(branch):
    rows = gel_json(CONTACT_QUERY, branch=branch)
    return {row["email"]: row for row in rows}


def report(args, timeout=300):
    return subprocess.run(
        ["python3", REPORT_SCRIPT] + args,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_branch_set_is_exactly_main_and_split_names(gel_server):
    names = sorted(gel_json("select sys::Branch.name"))
    assert names == ["main", "split_names"], (
        f"The instance must end up with exactly the branches main and split_names, found {names}."
    )
    listing = run(["gel", "branch", "list"])
    assert listing.returncode == 0, f"gel branch list failed: {listing.stderr}"
    plain = re.sub(r"\x1b\[[0-9;]*m", "", listing.stdout)
    assert "stale_prototype" not in plain, (
        f"stale_prototype must be dropped, still listed:\n{plain}"
    )


def test_project_current_branch_is_main(gel_server):
    current = gel_json("select sys::get_current_branch()")
    assert current == ["main"], (
        f"The project's current branch must be main at the end, got {current}."
    )


def test_branches_share_identical_migration_history(gel_server):
    main_history = db_history("main")
    feature_history = db_history("split_names")
    assert len(main_history) >= 3, (
        f"main must have at least 3 applied migrations, got {main_history}."
    )
    assert main_history == feature_history, (
        "main and split_names must have identical applied migration histories.\n"
        f"main: {main_history}\nsplit_names: {feature_history}"
    )
    assert main_history[0] == INITIAL_MIGRATION_ID, (
        f"The first applied migration must remain {INITIAL_MIGRATION_ID}, got {main_history[0]}."
    )
    assert main_history[1] == DOMAIN_MIGRATION_ID, (
        f"The second applied migration must remain {DOMAIN_MIGRATION_ID}, got {main_history[1]}."
    )


def test_migration_status_reports_in_sync_on_both_branches(gel_server):
    for branch in ("main", "split_names"):
        proc = run(["gel", "migration", "status", "--branch", branch])
        assert proc.returncode == 0, (
            f"gel migration status --branch {branch} failed (exit {proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
        combined = (proc.stdout + proc.stderr).lower()
        assert "up to date" in combined, (
            f"Branch {branch} is not up to date with dbschema/migrations:\n{combined}"
        )


def test_migration_files_match_applied_history(gel_server):
    files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    history = db_history("main")
    assert len(files) == len(history), (
        f"dbschema/migrations must hold exactly one file per applied migration "
        f"({len(history)}), found {len(files)}: {[os.path.basename(f) for f in files]}"
    )
    indexes = []
    ids = []
    for path in files:
        name = os.path.basename(path)
        match = re.match(r"^(\d+)", name)
        assert match, f"Migration file {name} does not start with a numeric index."
        indexes.append(int(match.group(1)))
        content = open(path, encoding="utf-8").read()
        id_match = re.search(r"CREATE\s+MIGRATION\s+([A-Za-z0-9_]+)", content)
        assert id_match, f"No CREATE MIGRATION statement found in {name}."
        ids.append(id_match.group(1))
    assert indexes == list(range(1, len(files) + 1)), (
        f"Migration file indexes must be unique and consecutive from 00001, got {indexes}."
    )
    assert ids == history, (
        "The migration ids stored in dbschema/migrations (ordered by index) must equal the "
        f"applied history.\nfiles: {ids}\nhistory: {history}"
    )


def test_schema_file_matches_the_migrated_schema(gel_server):
    """dbschema/default.gel must declare exactly what is applied on main."""
    before = set(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql")))
    proc = subprocess.run(
        ["gel", "migration", "create", "--non-interactive"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=300,
    )
    created = sorted(set(glob.glob(os.path.join(MIGRATIONS_DIR, "*.edgeql"))) - before)
    for path in created:
        os.remove(path)
    combined = proc.stdout + proc.stderr
    assert not created, (
        "dbschema/default.gel does not match the schema applied on main: "
        f"a new migration was proposed ({[os.path.basename(p) for p in created]}).\n{combined}"
    )
    assert "no schema changes detected" in combined.lower(), (
        "dbschema/default.gel must declare the final schema with no pending changes, "
        f"but gel migration create reported:\n{combined}"
    )


@pytest.mark.parametrize("branch", ["main", "split_names"])
def test_schema_uses_split_name_properties(gel_server, branch):
    pointers = pointer_map(branch)
    assert "full_name" not in pointers, (
        f"full_name must no longer exist on branch {branch}: {sorted(pointers)}"
    )
    for expected in ("email", "domain", "stage", "note", "first_name", "last_name"):
        assert expected in pointers, (
            f"Property {expected} is missing from Contact on branch {branch}: {sorted(pointers)}"
        )
    for expected in ("first_name", "last_name"):
        info = pointers[expected]
        assert info["required"] is True, f"{expected} must be required on branch {branch}."
        assert info["target"]["name"] == "std::str", (
            f"{expected} must be of type std::str on branch {branch}, got {info['target']['name']}."
        )
    email_constraints = {c["name"] for c in pointers["email"]["constraints"]}
    assert "std::exclusive" in email_constraints, (
        f"The exclusive constraint on email must survive on branch {branch}, got {email_constraints}."
    )


@pytest.mark.parametrize("branch", ["main", "split_names"])
def test_contacts_are_converted(gel_server, branch):
    rows = contact_rows(branch)
    assert sorted(rows) == sorted(EXPECTED_CONTACTS), (
        f"Branch {branch} must hold exactly the 12 production contacts.\n"
        f"got: {sorted(rows)}"
    )
    for email, (first, last, domain, stage, note) in EXPECTED_CONTACTS.items():
        row = rows[email]
        assert row["first_name"] == first, (
            f"{email} on {branch}: expected first_name {first!r}, got {row['first_name']!r}"
        )
        assert row["last_name"] == last, (
            f"{email} on {branch}: expected last_name {last!r}, got {row['last_name']!r}"
        )
        assert row["domain"] == domain, (
            f"{email} on {branch}: expected domain {domain!r}, got {row['domain']!r}"
        )
        assert row["stage"] == stage, (
            f"{email} on {branch}: stage must be unchanged ({stage!r}), got {row['stage']!r}"
        )
        assert row["note"] == note, (
            f"{email} on {branch}: note must be unchanged ({note!r}), got {row['note']!r}"
        )


@pytest.mark.parametrize("branch", ["main", "split_names"])
def test_no_data_lost_and_sandbox_contacts_gone(gel_server, branch):
    total = gel_json("select count(Contact)", branch=branch)
    assert total == [12], f"Branch {branch} must hold exactly 12 contacts, got {total}."
    sandbox = gel_json(
        "select count(Contact filter .email like '%@sandbox.test')", branch=branch
    )
    assert sandbox == [0], (
        f"Branch {branch} must not contain any @sandbox.test contact, got {sandbox}."
    )


def test_git_branches_are_merged_and_tree_is_clean():
    main_tip = run(["git", "rev-parse", "main"])
    feature_tip = run(["git", "rev-parse", "split_names"])
    assert main_tip.returncode == 0 and feature_tip.returncode == 0, (
        f"git rev-parse failed: {main_tip.stderr} {feature_tip.stderr}"
    )
    assert main_tip.stdout.strip() == feature_tip.stdout.strip(), (
        "git branches main and split_names must point at the same commit "
        f"({main_tip.stdout.strip()} vs {feature_tip.stdout.strip()})."
    )
    status = run(["git", "status", "--porcelain"])
    assert status.returncode == 0, f"git status failed: {status.stderr}"
    assert status.stdout.strip() == "", (
        f"The git working tree must be clean (nothing modified or untracked):\n{status.stdout}"
    )
    root = run(["git", "rev-list", "--max-parents=0", "HEAD"])
    assert root.returncode == 0 and root.stdout.strip(), (
        f"Could not resolve the root commit: {root.stderr}"
    )
    for commit in root.stdout.split():
        ancestor = run(["git", "merge-base", "--is-ancestor", commit, "HEAD"])
        assert ancestor.returncode == 0, (
            f"The original commit {commit} must remain an ancestor of the final tip."
        )
    domain_commit = run(["git", "log", "--reverse", "--format=%H", "main"])
    commits = domain_commit.stdout.split()
    assert len(commits) >= 3, (
        f"main should keep its two original commits plus the feature work, got {len(commits)}."
    )


def test_report_script_default_output(gel_server):
    assert os.path.isfile(REPORT_SCRIPT), f"{REPORT_SCRIPT} does not exist."
    proc = report([])
    assert proc.returncode == 0, (
        f"python3 {REPORT_SCRIPT} failed (exit {proc.returncode}):\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout is not a single JSON object: {exc}\nstdout: {proc.stdout}")
    assert isinstance(payload, dict), f"Expected a JSON object, got {type(payload)}."
    assert sorted(payload) == ["branches", "current_branch", "migrations"], (
        f"Top-level keys must be exactly current_branch, migrations, branches; got {sorted(payload)}."
    )
    history = db_history("main")
    assert payload["current_branch"] == "main", (
        f"current_branch must be main, got {payload['current_branch']!r}."
    )
    assert payload["migrations"] == history, (
        f"migrations must be the current branch history {history}, got {payload['migrations']}."
    )
    expected_branches = [
        {
            "name": "main",
            "migration_count": len(history),
            "contact_count": 12,
            "in_sync": True,
        },
        {
            "name": "split_names",
            "migration_count": len(history),
            "contact_count": 12,
            "in_sync": True,
        },
    ]
    assert payload["branches"] == expected_branches, (
        f"branches must be {expected_branches}, got {payload['branches']}."
    )


def test_report_script_single_branch_output(gel_server):
    proc = report(["split_names"])
    assert proc.returncode == 0, (
        f"python3 {REPORT_SCRIPT} split_names failed (exit {proc.returncode}):\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    history = db_history("main")
    assert sorted(payload) == ["branches", "current_branch", "migrations"], (
        f"Top-level keys must be exactly current_branch, migrations, branches; got {sorted(payload)}."
    )
    assert payload["current_branch"] == "main", (
        f"current_branch must still be main, got {payload['current_branch']!r}."
    )
    assert payload["migrations"] == history, (
        f"migrations must be {history}, got {payload['migrations']}."
    )
    assert payload["branches"] == [
        {
            "name": "split_names",
            "migration_count": len(history),
            "contact_count": 12,
            "in_sync": True,
        }
    ], f"branches must contain only split_names, got {payload['branches']}."


def test_report_script_rejects_unknown_branch(gel_server):
    proc = report(["nope_zzz"])
    assert proc.returncode == 3, (
        f"An unknown branch must exit with code 3, got {proc.returncode}.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip() == "", (
        f"stdout must stay empty for an unknown branch, got {proc.stdout!r}."
    )
    assert "unknown branch: nope_zzz" in proc.stderr, (
        f"stderr must contain 'unknown branch: nope_zzz', got {proc.stderr!r}."
    )


def test_report_script_is_idempotent_and_read_only(gel_server):
    first = report([])
    second = report([])
    assert first.returncode == 0 and second.returncode == 0, (
        "Repeated report runs must both succeed:\n"
        f"first: {first.returncode} {first.stderr}\nsecond: {second.returncode} {second.stderr}"
    )
    assert first.stdout == second.stdout, (
        "Repeated report runs must print identical output.\n"
        f"first: {first.stdout}\nsecond: {second.stdout}"
    )
    names = sorted(gel_json("select sys::Branch.name"))
    assert names == ["main", "split_names"], (
        f"Running the report must not change the branch set, found {names}."
    )
    assert db_history("main") == db_history("split_names"), (
        "Running the report must not change the migration histories."
    )
    for branch in ("main", "split_names"):
        total = gel_json("select count(Contact)", branch=branch)
        assert total == [12], (
            f"Running the report must not change data on {branch}, count is {total}."
        )
