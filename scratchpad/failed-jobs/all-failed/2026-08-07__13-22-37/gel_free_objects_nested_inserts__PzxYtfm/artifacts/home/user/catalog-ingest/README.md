# catalog-ingest

Node.js 22 + TypeScript project skeleton wired to the local Gel 6 server.

* Start the database (idempotent): `/usr/local/bin/start-gel.sh`
* Query it from the shell: `gel query -F json 'select 1'`
* Dependencies (`gel`, `tsx`, `typescript`, `@types/node`) are already installed
  in `node_modules`; the container has no network access.
