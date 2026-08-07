# Vault — role-aware document access enforced by the database (TypeScript)

## Background

`/home/user/vault` is a small "Vault" document-collaboration project backed by a **local Gel 6.11 instance** (the directory already holds `gel.toml`, the migrated schema in `dbschema/default.gel` and its migration history, and the branch `main` is already seeded with actors, workspaces, memberships and documents).

Right now the database is wide open: any connected client can read and write every row, and there is no application code at all. Product wants the *database itself* to decide what each actor may see and change, and a TypeScript command-line service that talks to it with the correct actor context.

## Requirements

### 1. Actor context

Introduce a schema-level global named exactly `current_actor_id` of type `uuid`. It must be optional: when it is not set, the connection is **anonymous**.

### 2. Rules enforced inside the database

The following rules must be enforced by object-level access policies in the schema, so that **any** client that sets `current_actor_id` (not only your CLI) observes exactly this behaviour. Filtering inside application queries is not acceptable.

Let *A* be the actor identified by `current_actor_id`.

**`Document` — read (select):** *A* may read a document only if *A* has a `Membership` (any role) in the document's workspace or is the document's `owner`. In addition, an **archived** document (`archived := true`) is readable **only** by its `owner`, whatever memberships other actors hold.

**`Document` — create (insert):** allowed only if *A* holds an `Owner` or `Editor` membership in the new document's workspace **and** the new document's `owner` is *A* itself. Inserting a document owned by somebody else must be rejected by the database.

**`Document` — modify (update):** allowed only if *A* may read the document **and** *A* holds an `Owner` or `Editor` membership in the document's workspace. After the update the same condition must still hold for the document's (possibly new) workspace, so a document can never end up in a workspace where *A* is not an `Owner`/`Editor` member.

**`Document` — delete:** allowed only to the document's `owner`.

**`Workspace` — read:** *A* may read only the workspaces *A* is a member of.

**`Membership` — read:** *A* may read only *A*'s own membership rows.

**`Actor` — read:** *A* may read *A*'s own row and the rows of actors who share at least one workspace with *A*.

An anonymous connection (global unset) must be able to read nothing at all from `Document`, `Workspace`, `Membership` and `Actor`, including through nested shapes, link traversal and backlinks.

`ActivityLog` must stay unrestricted (any connection may read and append rows). Write access to `Actor`, `Workspace` and `Membership` is never needed — the service never modifies them, and the existing seed rows must all still be present when you are done.

### 3. Migration

The schema change must be captured as a new migration in `dbschema/migrations/` and applied to the running branch, leaving the branch in sync with `dbschema/default.gel`. The existing migration must not be edited or removed, and no seeded row may be deleted.

### 4. The `vault` command-line service

Implement the service in TypeScript under `/home/user/vault/src/`, split into at least:

* `/home/user/vault/src/service.ts` — the database access layer,
* `/home/user/vault/src/cli.ts` — argument parsing and output.

`npm run build` must compile the TypeScript sources into `/home/user/vault/dist/`, and the resulting CLI must be runnable as `node dist/cli.js <subcommand> [options]` from `/home/user/vault`. Every npm dependency you need is already installed in `/home/user/vault/node_modules`; do not rely on downloading anything else.

Subcommands and their options:

```
node dist/cli.js list-documents  [--actor <email>]
node dist/cli.js read-document   [--actor <email>] --title <title>
node dist/cli.js create-document  --actor <email> --workspace <name> --title <title> --body <body>
node dist/cli.js update-document  --actor <email> --title <title> --body <body>
node dist/cli.js move-document    --actor <email> --title <title> --to-workspace <name>
node dist/cli.js delete-document  --actor <email> --title <title>
node dist/cli.js audit
```

Omitting `--actor` means the command runs anonymously. `audit` takes no options and must list **every** document stored in the branch, ignoring all object-level rules.

## Implementation Hints

* Project path: `/home/user/vault` (run every command from there).
* The local Gel server (Gel 6.11, branch `main`) may not be running yet; `/usr/local/bin/start-gel.sh` starts it if needed and returns once the branch accepts queries. It is safe to run repeatedly.
* Connection settings for that instance are already exported in the environment, so both the `gel` CLI and the `gel` npm client connect without extra configuration.
* Command: `node dist/cli.js <subcommand> [options]`, after `npm run build`.
* Every invocation must print **exactly one line of JSON to stdout** and nothing else on stdout (diagnostics, if any, go to stderr).
* On success print `{"ok": true, "actor": <email or null>, "action": "<subcommand>", "data": <payload>}` and exit with code `0`.
* On failure print `{"ok": false, "actor": <email or null>, "action": "<subcommand>", "error": {"code": "<CODE>", "message": "<non-empty string>"}}` and exit with the code listed below. `actor` echoes the `--actor` value (or `null` when it was omitted); `action` echoes the subcommand name; for an unknown subcommand `action` must be the string that was passed (or `null` if none was passed).
* `data` payloads, with exactly these keys and no others:
  * `list-documents` and `audit`: a JSON array of document objects with the keys `title`, `workspace` (workspace name), `owner_email`, `archived`, sorted by `title` ascending.
  * `read-document`: a single object with the keys `title`, `workspace`, `owner_email`, `archived`, `body`.
  * `create-document`, `update-document`, `move-document`: a single object with the keys `title`, `workspace`, `owner_email`, `archived` describing the document after the operation.
  * `delete-document`: a single object with the only key `title`.
* Error codes and exit codes:
  * `BAD_REQUEST` / exit `2` — unknown subcommand, a missing or empty required option, an `--actor` email that matches no `Actor` row, or a workspace name that matches no `Workspace` row.
  * `NOT_FOUND` / exit `3` — `read-document` found no document with that title that the acting actor is allowed to read.
  * `NO_MATCH` / exit `3` — `update-document`, `move-document` or `delete-document` matched no document the acting actor is allowed to change.
  * `POLICY_VIOLATION` / exit `4` — the database rejected the write because it violates the object-level rules.
  * `CONFLICT` / exit `5` — the write violated a schema constraint (for example a duplicate document title).
* Resolving an `--actor` email or a `--workspace`/`--to-workspace` name to a database object must succeed regardless of what the acting actor is allowed to read; only the document operation itself is subject to the rules above. A write that the rules reject must leave the database unchanged.
* Every command that successfully creates, updates, moves or deletes a document must append exactly one `ActivityLog` row whose `action` is the subcommand name, `actor_email` is the acting actor's email and `doc_title` is the affected document's title. A command that fails must append no `ActivityLog` row, and for `create-document` the new document and its `ActivityLog` row must be committed atomically in a single transaction.
