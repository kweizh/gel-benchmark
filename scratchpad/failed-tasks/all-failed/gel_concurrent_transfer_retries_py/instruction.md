# Concurrency-safe transactional ledger on Gel 7.1 (Python)

## Background

This container runs a local **Gel 7.1** database server (the graph-relational database formerly
known as EdgeDB). Branch `main` of that instance already contains a `default::Account` object type
and a set of seeded production accounts.

Treasury operations need a money-transfer service on top of it. Transfers arrive from many
callers at the same time, frequently touching the very same accounts, and the ledger they produce
is used for audits: it must be possible to replay it and arrive at exactly the balances stored in
the database. Getting this right under simultaneous conflicting writes is the whole point of the
task.

## Requirements

### 1. Schema

Extend the schema of branch `main` (module `default`) with an object type `LedgerEntry` that holds
exactly one entry per successfully applied transfer:

- `transfer_id` — required `str`, unique across all `LedgerEntry` objects
- `amount_cents` — required `int64`
- `source` — required link to `Account`
- `target` — required link to `Account`
- `source_balance_after` — required `int64`
- `target_balance_after` — required `int64`
- `applied_at` — required `datetime`, populated automatically when the entry is created

Any additional pointer you put on `LedgerEntry` must be optional or have a default. `Account` and
its `code` (unique `str`) and `balance_cents` (`int64`) properties must keep their names, types and
required/unique characteristics, and you must not add required pointers without defaults to
`Account`.

`LedgerEntry` objects are append-only: once created they are never modified and never deleted.

### 2. Transfer service

Expose an HTTP service (details in *Interface* below) that moves money between two accounts and
appends one ledger entry per applied transfer.

### 3. Invariants

These must hold no matter how many transfers are submitted at the same time. Note that your
service is **not** the only writer: while it is serving requests, other independent processes may
apply their own transfers against the same accounts and append their own `LedgerEntry` objects
directly to the same branch. Your implementation must remain correct in their presence.

1. **No lost updates.** After a batch of transfers, every account's `balance_cents` must equal its
   starting balance plus/minus every applied transfer that touched it — nothing more, nothing less.
2. **Conservation.** A transfer changes no total: the sum of the two accounts' balances is the same
   before and after it.
3. **No overdraft.** `balance_cents` must never become negative; a transfer whose amount exceeds
   the source's current balance is rejected instead.
4. **Exactly one entry per success.** Every transfer reported as applied has exactly one
   `LedgerEntry`; a rejected transfer has none, and leaves every balance untouched.
5. **Truthful audit chain.** For each account, the balances recorded on the ledger entries that
   touch it must form an unbroken chain. Formally: for an account, take every entry that references
   it, and from each entry take the balance it records for that account after the transfer
   (`source_balance_after` or `target_balance_after`) plus the balance implied before the transfer
   (that same value plus the amount when the account is the source, minus the amount when it is the
   target). Then the multiset of implied "before" values together with the account's current
   balance must equal the multiset of recorded "after" values together with the account's balance
   as it was before any of those entries existed. In short: each entry must record the balances the
   transfer actually produced, not a balance read at some other moment.
6. **At most once per `transfer_id`.** A given `transfer_id` may be applied at most once, even when
   several identical requests are submitted simultaneously; the losers are rejected.
7. **No unstable failures.** Under contention the service must still answer with one of the
   documented status codes and reasons — never a 5xx, never a hung request (every request must be
   answered within 30 seconds), never `applied` for a transfer that was not persisted, and never a
   rejection for a transfer that was persisted.

## Implementation Hints

- Project path: `/home/user/ledger`. It already contains `gel.toml` and `dbschema/default.gel`.
- The Gel 7.1 server runs inside this container. Run `bash /usr/local/bin/start-gel.sh` to make
  sure it is up; the script is idempotent and only returns once the server is ready to accept
  queries. Connection settings for both the `gel` CLI and the client libraries are already present
  in the environment (host `localhost`, port `5656`, branch `main`, user `admin`, no password, TLS
  verification disabled), so no connection arguments are needed.
- Preinstalled and importable: the `gel` CLI, `python3`, and the Python packages `gel`, `fastapi`,
  `uvicorn`, `flask`, `gunicorn`, `requests` and `pytest`. Assume there is no internet access.
- Start command: `bash /home/user/ledger/start.sh`. It must run the service in the foreground and
  serve HTTP on port `8080`, reachable at `http://127.0.0.1:8080`. It will be invoked with the Gel
  server already running, and it must be re-runnable: stopping the process and running the command
  again must bring the service back up against the same data, with all previously applied transfers
  still readable.
- Every response body is a JSON object containing **exactly** the keys listed below, **in the order
  listed**.

  - `GET /health` -> `200`

    ```json
    { "status": "ok" }
    ```

  - `POST /transfers` — request body must be a JSON object with exactly these four keys:

    ```json
    {
      "transfer_id": string,
      "source_code": string,
      "target_code": string,
      "amount_cents": integer
    }
    ```

    Applied -> `201`:

    ```json
    {
      "status": "applied",
      "transfer_id": string,
      "source_code": string,
      "target_code": string,
      "amount_cents": integer,
      "source_balance_after": integer,
      "target_balance_after": integer
    }
    ```

    Rejected -> status code from the table below:

    ```json
    {
      "status": "rejected",
      "transfer_id": string or null,
      "reason": string
    }
    ```

    `transfer_id` in a rejection echoes the submitted value when it was a non-empty string, and is
    `null` otherwise. The rejection reasons, their status codes, and the order in which the
    conditions are evaluated (first match wins) are:

    1. `400` `invalid_request` — the body is not a JSON object, or does not have exactly the four
       required keys (any missing key or any unexpected extra key), or `transfer_id`, `source_code`
       or `target_code` is not a non-empty string.
    2. `422` `invalid_amount` — `amount_cents` is not a JSON integer (booleans, strings and numbers
       written with a decimal point are not integers) or is smaller than `1`.
    3. `422` `same_account` — `source_code` equals `target_code`.
    4. `404` `unknown_account` — no `Account` exists for `source_code`, or none for `target_code`.
    5. `409` `duplicate_transfer` — a `LedgerEntry` with that `transfer_id` already exists.
    6. `409` `insufficient_funds` — the source account's `balance_cents` is smaller than
       `amount_cents`.

  - `GET /accounts/<code>` -> `200`

    ```json
    { "code": string, "balance_cents": integer }
    ```

    or `404` when no such account exists:

    ```json
    { "status": "rejected", "reason": "unknown_account" }
    ```

  - `GET /transfers/<transfer_id>` -> `200`

    ```json
    {
      "transfer_id": string,
      "source_code": string,
      "target_code": string,
      "amount_cents": integer,
      "source_balance_after": integer,
      "target_balance_after": integer
    }
    ```

    or `404` when no ledger entry has that `transfer_id`:

    ```json
    { "status": "rejected", "reason": "unknown_transfer" }
    ```

- Seeded accounts are production data: do not create, delete or edit any `Account` whose `code`
  starts with `ACC-` or `RSV-`, and never use one as the source or the target of a transfer, not
  even while you are testing. Create your own accounts, with codes outside those two prefixes, if
  you need something to experiment with.

