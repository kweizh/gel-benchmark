# Land a Gel feature branch: rebase, merge, and a data-preserving name split

## Background
`/home/user/crm` is a Gel 6 project (module `default`, type `Contact`) that is version-controlled with git and linked to the local Gel instance `crm_instance`. The team maps every git branch to a Gel branch of the same name.

Current state of the instance:

- Gel branch `main` holds the production dataset: 12 `Contact` objects. Its applied migration history is the initial migration plus a second migration that added the `domain` property.
- Gel branch `split_names` is the feature branch and is the project's current branch. It only has the initial migration applied and holds 3 throwaway contacts whose e-mail addresses end in `@sandbox.test`.
- Gel branch `stale_prototype` is an abandoned experiment.

Git mirrors that divergence: the checked-out git branch is `split_names`, which was cut before the `domain` work landed on git branch `main`, so `dbschema/` on disk currently only knows about the initial migration.

The Gel server for this project listens on `127.0.0.1:5656`. If it is not running, start it with `gel-start.sh` (it is on `PATH`, is safe to re-run, and takes care of starting the server under the unprivileged `user` account). Everything happens locally: no internet access is needed or available.

## Requirements

### 1. Data-preserving schema change (feature work)
On the feature side, `Contact.full_name` must be replaced by two new properties `required first_name: str` and `required last_name: str`. Existing rows must be converted, never re-typed by hand or reinserted. For a given contact, let `t` be its `full_name` with leading and trailing whitespace removed:

- if `t` contains no space character: `first_name` is `t` and `last_name` is the empty string `''`;
- otherwise: `first_name` is the part of `t` before its first space character, and `last_name` is the part of `t` after that first space character with leading and trailing whitespace removed.

Nothing else in the schema may change: `email` (with its exclusive constraint), `domain`, `stage` and `note` must survive with their current definitions, and the `email`, `stage` and `note` values of the 12 production contacts must be untouched.

### 2. End state of the Gel instance
- The instance has exactly two branches: `main` and `split_names`. `stale_prototype` and any temporary branch must be gone.
- The project's current branch is `main`.
- `main` and `split_names` have an identical applied migration history: the same migration ids in the same order. The two migrations that already exist today must remain the first two entries of that history, unchanged (no squashing, no rewriting, no `--dev-mode` shortcuts).
- Both branches are up to date with respect to `dbschema/migrations`, and `dbschema/migrations` holds exactly one file per applied migration, with unique consecutive indexes starting at `00001` in history order.
- `dbschema/default.gel` declares the final schema: the SDL on disk matches what is applied on `main`, so no further migration is pending.
- Both branches contain exactly the 12 production contacts, with `first_name`/`last_name` converted as described above and `domain` preserved. `full_name` must no longer exist in the schema of either branch. The 3 `@sandbox.test` contacts must not exist on any branch.

### 3. End state of the git repository
- The git branches `main` and `split_names` point at the same commit, and the two commits that exist today are still ancestors of it.
- `git status --porcelain` in `/home/user/crm` is empty: every file that belongs to the project, including the final set of migration files, is committed and nothing is left untracked.

### 4. Branch/migration report script
Provide `/home/user/crm/branch_report.py`, runnable as `python3 /home/user/crm/branch_report.py [BRANCH]`, which reports live state read from the instance and from `dbschema/migrations`.

## Implementation Hints
- Project path: `/home/user/crm`
- Command: `python3 /home/user/crm/branch_report.py [BRANCH]`
- With no argument the command must print to stdout a single JSON object with exactly the keys `current_branch`, `migrations` and `branches`, and exit with code 0:
  - `current_branch`: the name of the project's current Gel branch.
  - `migrations`: the applied migration ids of the current Gel branch as a JSON array of strings, oldest first.
  - `branches`: a JSON array with one object per Gel branch on the instance, sorted by `name` ascending, each object having exactly the keys `name` (string), `migration_count` (integer number of applied migrations on that branch), `contact_count` (integer number of `Contact` objects on that branch) and `in_sync` (boolean, true only when that branch's applied migration history is identical to the migration history stored in `dbschema/migrations`).
- With a `BRANCH` argument that names an existing Gel branch, the output is the same JSON object except that `branches` contains only that one branch's object; exit code 0.
- With a `BRANCH` argument that does not name an existing Gel branch, stdout must stay empty, stderr must contain the line `unknown branch: <BRANCH>`, and the exit code must be 3.
- Stdout must contain nothing but the JSON object, and the script must be read-only: running it any number of times must not alter branches, schema, migrations or data, and repeated runs must print identical output.
- Do not delete or recreate the `main` branch's data, and do not solve the conversion by dropping and re-inserting contacts.

