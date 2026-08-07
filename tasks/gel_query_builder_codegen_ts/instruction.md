# Typed catalog analytics on Gel with a generated TypeScript query builder

## Background
`/home/user/catalog` is a TypeScript (Node.js) project wired to a local **Gel 6** instance. The database currently only knows about writers (`Author` objects, already seeded); the learning-content catalog itself has not been modelled yet, and the reporting CLI is an unimplemented stub. Your job is to model the catalog, load it, and ship a strictly-typed analytics CLI that talks to Gel exclusively through the code-generated, fully-typed query builder produced by `@gel/generate` — no hand-written EdgeQL strings anywhere in the application source.

Everything runs locally and the machine must be treated as offline: `gel`, `@gel/generate`, `typescript`, `tsx` and `@types/node` are already installed under `node_modules` at pinned versions, and nothing else may be downloaded.

## Requirements
1. Extend the schema in `dbschema/default.gel` so the database exposes:
   - an **abstract** object type `Resource` with required single properties `title` (`str`, unique across *all* resources), `minutes` (`int64`) and `level` (`str`), plus a required single link `author` pointing at `Author`;
   - a concrete type `Article` extending `Resource` with a required single property `word_count` (`int64`);
   - a concrete type `Video` extending `Resource` with a required single property `has_captions` (`bool`);
   - two computeds on `Author`: `resources`, the (possibly empty) set of every `Resource` whose `author` link points at that author, and `resource_count` (`int64`), how many such resources exist.
2. Record that schema change as a new migration file in `dbschema/migrations/` and apply it, so `gel migration status` reports that the database is up to date.
3. Regenerate the `@gel/generate` query builder into `dbschema/edgeql-js/` so it reflects the final schema.
4. Implement the catalog CLI in `src/cli.ts` with the subcommands described below. Every database read and write it performs must be expressed through the generated query builder, and each report must reflect the database contents at the moment the command runs.

## Implementation Hints
- Project path: `/home/user/catalog`. Run every command with that directory as the working directory.
- A local Gel 6 instance is preconfigured (data directory, branch `main`, connection settings exported in the image environment). Run `gel-start.sh` to make sure the server is up; it is idempotent and returns once the server answers queries.
- Command: `npx tsx src/cli.ts <subcommand> [flags]`. Successful subcommands print exactly one JSON document to stdout (nothing else on stdout) and exit `0`.
- Catalog input lives at `data/resources.json`: an array of entries with the keys `kind` (`"article"` or `"video"`), `title`, `author` (the `name` of an existing `Author`), `minutes`, `level`, and either `word_count` (articles) or `has_captions` (videos).
- `npx tsx src/cli.ts load` inserts every entry of `data/resources.json` that is not in the database yet, as the matching `Article`/`Video` object linked to the named author. It must be safe to run repeatedly: re-running it must neither fail nor duplicate anything. It prints an object with exactly the keys `articles`, `videos` and `total` — the number of `Article`, `Video` and `Resource` objects in the database after the load.
- `npx tsx src/cli.ts report authors` prints an array with one object per `Author` in the database (authors with no resources included), each having exactly the keys `name`, `articles`, `videos`, `total_minutes`, `avg_minutes` and `top_title`: the counts of that author's articles and videos, the sum of their resources' `minutes`, that sum divided by their resource count rounded to 2 decimal places (`0` when the author has no resources), and the `title` of their longest resource by `minutes` — ties broken by title ascending, `null` when the author has no resources. Order the array by `total_minutes` descending, then `name` ascending.
- `npx tsx src/cli.ts report levels` prints an array with one object per distinct `level` value present among the resources, ordered by `level` ascending, each having exactly the keys `level`, `count`, `articles`, `videos`, `total_minutes`, `total_words` and `captioned_videos`: the number of resources in the bucket, how many of them are articles, how many are videos, the sum of their `minutes`, the sum of `word_count` over the bucket's articles, and how many of the bucket's videos have `has_captions` true.
- `npx tsx src/cli.ts report author --name <name>` prints one object with exactly the keys `name`, `country`, `resource_count`, `total_minutes` and `titles` for the author whose `name` equals `<name>` exactly (names may contain apostrophes and spaces); `titles` is the array of that author's resource titles sorted ascending (empty array when there are none). When no author has that name, print nothing on stdout, write `author not found: <name>` to stderr and exit `3`.
- Any unrecognised subcommand, or `report author` without a usable `--name` value, must print nothing on stdout, write a message to stderr and exit `2`.
- `npx tsc --noEmit -p tsconfig.json` must exit `0`, and `strict` must stay enabled in `tsconfig.json`.
- Escape hatches are forbidden in the application source: no file under `src/` may contain the whole word `any` (case-insensitive), nor the text `@ts-ignore`, `@ts-expect-error` or `@ts-nocheck`, nor any of the substrings `.query(`, `.querySingle(`, `.queryRequired(`, `.queryRequiredSingle(`, `.queryJSON(`, `.querySingleJSON(`, `.queryRequiredJSON(`, `.queryRequiredSingleJSON(`, `.execute(`, `.executeSQL(` — the generated query builder is the only way the CLI may reach the database.

