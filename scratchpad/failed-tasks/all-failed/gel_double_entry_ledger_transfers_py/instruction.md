# Concurrency-Safe Double-Entry Ledger on Gel (async Python client)

## Background
`/home/user/ledger` holds the backend of a small payments service that keeps money in a
double-entry ledger stored in **Gel 6** (a local, single-node instance; the `gel` CLI and the
`gel` Python client 3.1.0 are already installed, and there is **no network access**).

The current state of the project is unfinished:

- `dbschema/default.gel` only models a bare `Account` type, and the first migration for it has
  already been created and applied.
- Four accounts are already stored: `ACC-1001`, `ACC-1002`, `ACC-1003`, `ACC-1004`.
- `ledger_api.py` is a stub whose functions raise `NotImplementedError`.

Your job is to finish the data model and the transfer API so that the ledger is always balanced,
even when many transfers are executed at the same time.

## Requirements
1. Extend the Gel schema so that money movements are recorded as double-entry bookkeeping:
   every transfer materialises balanced ledger entries, account balances are *derived* from those
   entries instead of being stored a second time, and the integrity rules listed under
   "Schema contract" below are enforced by the database itself.
2. Create and apply a migration for the new schema. The existing migration file must stay
   untouched, `gel migration status` must report that the branch is up to date, and the four
   pre-existing accounts (and their `opening_balance` values) must survive.
3. Implement the public API described under "Module contract" in `/home/user/ledger/ledger_api.py`.
4. Money must never be created or destroyed: for any set of transfers, the sum of the amounts of
   all ledger entries they produced is exactly zero, and no account balance may ever become
   negative.
5. Replaying an operation with an idempotency key that has already been used must not move money
   a second time.
6. The API must stay correct under concurrent use (up to 20 simultaneous in-flight operations,
   including several that share one idempotency key).
7. Operations that fail must leave no trace whatsoever in the database.

## Implementation Hints
- Project path: `/home/user/ledger` (schema in `dbschema/`, migrations in `dbschema/migrations/`).
- The local Gel server is started by the idempotent script `/usr/local/bin/gel-start.sh`; it is
  safe to run it repeatedly and it is also what the verification harness uses. Connection
  parameters for both the CLI and the Python client are already exported in the environment, so
  `gel.create_async_client()` and plain `gel ...` commands connect without extra arguments.
- Only the `default` module may be used, and all names below are literal and case-sensitive.

### Schema contract
- `Account`
  - `code: str` — required; no two accounts may share a code (rejected by the database).
  - `opening_balance: decimal` — required (already present).
  - `balance` — a single, computed value equal to `opening_balance` plus the sum of the `amount`
    of every `LedgerEntry` linked to that account. It must not be a stored property.
- `Transfer`
  - `idempotency_key: str` — required; no two transfers may share a key (rejected by the database).
  - `amount: decimal` — required; values `<= 0` are rejected by the database.
  - `sender: Account` — required, single link.
  - `recipient: Account` — required, single link; a transfer whose `sender` and `recipient` are the
    same account is rejected by the database.
  - `created_at: datetime` — required, defaulting to the time of the inserting statement.
  - `entries` — a computed multi value pointing at the `LedgerEntry` objects of this transfer.
- `LedgerEntry`
  - `amount: decimal` — required; the value `0` is rejected by the database. Negative values are
    debits, positive values are credits.
  - `account: Account` — required, single link.
  - `transfer: Transfer` — required, single link.
  - Two entries for the same `(account, transfer)` pair are rejected by the database.
- Each successful transfer of `A` from `X` to `Y` results in exactly two `LedgerEntry` objects for
  that `Transfer`: `-A` on `X` and `+A` on `Y`.

### Module contract
`/home/user/ledger/ledger_api.py` must be importable as the top-level module `ledger_api` and must
define exactly these coroutine functions (all arguments after `client` are keyword-only; `client`
is a `gel.AsyncIOClient` supplied by the caller and is the only database handle the functions may
use):

```python
async def create_account(client, *, code: str, opening_balance: Decimal) -> dict
async def create_transfer(client, *, sender: str, recipient: str, amount: Decimal,
                          idempotency_key: str) -> dict
async def get_balance(client, *, account: str) -> str
async def get_statement(client, *, account: str, limit: int = 50) -> list[dict]
```

`sender`, `recipient` and `account` are always account *codes*.

Monetary values: every amount handed to the API is a `decimal.Decimal`; every amount returned by
the API is a `str` carrying exactly two fractional digits and a leading `-` for negative values
(for example `"1000.00"`, `"0.00"`, `"-3.50"`). An input amount that cannot be represented exactly
with two fractional digits (e.g. `Decimal("0.005")`) must be refused rather than rounded.

Return shapes (each dict must contain exactly the listed keys):
- `create_account` → `id` (the account's UUID as a `str`), `code`, `balance`.
- `create_transfer` → `id` (the transfer's UUID as a `str`), `sender`, `recipient`, `amount`,
  `idempotency_key`, `created` (a `bool`: `True` when this call stored the transfer, `False` when
  the key had already been used).
- `get_balance` → the account's balance.
- `get_statement` → one dict per transfer that touches the account, with keys `transfer_id`
  (`str`), `idempotency_key`, `counterparty` (the code of the other account), `direction`
  (`"debit"` when money left the account, `"credit"` when it arrived) and `amount` (signed:
  negative for `"debit"`, positive for `"credit"`). The list is ordered by the transfer's
  `created_at` descending, ties broken by `idempotency_key` descending, and contains at most
  `limit` items.

### Error contract
`ledger_api` must also define the exception classes `LedgerError`, `UnknownAccount`,
`DuplicateAccount`, `InvalidAmount`, `SameAccountTransfer` and `InsufficientFunds`; all of them
except `LedgerError` are subclasses of `LedgerError`, and `LedgerError` derives from `Exception`.
No other exception type may escape the four functions during normal operation.

- `create_account`: `DuplicateAccount` if the code is already taken; `InvalidAmount` if
  `opening_balance` is negative or not exactly representable with two fractional digits.
- `get_balance` / `get_statement`: `UnknownAccount` if the code does not exist.
- `create_transfer`: if a transfer with `idempotency_key` already exists, the stored transfer is
  returned with `created=False`, the remaining arguments are ignored, nothing is validated and
  nothing is written. Otherwise the arguments are checked in exactly this order, and the first
  failing check decides the exception: `InvalidAmount` (amount `<= 0`, or not exactly
  representable with two fractional digits) → `SameAccountTransfer` (`sender == recipient`) →
  `UnknownAccount` (either code unknown) → `InsufficientFunds` (the sender's balance is smaller
  than `amount`; a balance of exactly `0` after the transfer is allowed).

### Concurrency contract
- `N` concurrent `create_transfer` calls with `N` distinct idempotency keys must produce exactly
  `N` `Transfer` objects, each with exactly two entries, with no lost, duplicated or negative-
  balance-producing effect, and each call must return `created=True`.
- `M` concurrent `create_transfer` calls that share one idempotency key must produce exactly one
  `Transfer` (with exactly two entries), and every one of the `M` calls must return that
  transfer's `id`; exactly one of them reports `created=True`.

