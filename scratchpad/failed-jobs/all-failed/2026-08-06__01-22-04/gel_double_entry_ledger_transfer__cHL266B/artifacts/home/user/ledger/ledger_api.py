"""Public API of the ledger service.

Every function receives an already configured ``gel.AsyncIOClient`` as its first
positional argument; all remaining arguments are keyword-only.  Monetary values are
handed in as ``decimal.Decimal`` and handed back as strings with exactly two
fractional digits.

Money is represented as a proper double-entry ledger: ``Account.balance`` is a
computed value derived from the ``LedgerEntry`` rows linked to the account, and
every successful transfer creates exactly two balanced ``LedgerEntry`` rows for
its ``Transfer``.  Concurrency safety relies on Gel's serializable transactions:
``create_transfer`` re-reads the sender's balance inside a retryable transaction
that also performs the insert, so Gel/Postgres's serializable snapshot isolation
detects and retries any conflicting concurrent transfers instead of allowing an
account to go negative.  Idempotency is enforced by an exclusive database
constraint on ``Transfer.idempotency_key``; a losing concurrent insert is
detected via the resulting ``ConstraintViolationError`` and turned into a
``created=False`` response referencing the winning transfer.
"""

from __future__ import annotations

from decimal import Context, Decimal, Inexact, InvalidOperation
from typing import Any

import gel

TWO_PLACES = Decimal("0.01")
# Effectively unbounded precision, we only rely on the context to detect
# whether rounding would be required by quantize (via the Inexact trap).
_QUANT_CTX = Context(prec=1000, traps=[Inexact, InvalidOperation])

# create_transfer needs to survive many concurrent, conflicting transactions
# without ever letting a serialization failure escape as an unhandled error,
# so retries are much more generous than the client's default of 3 attempts.
_TRANSFER_RETRY_OPTIONS = gel.RetryOptions(
    attempts=100,
    backoff=lambda attempt: min(0.05 * (attempt + 1), 1.0),
)


class LedgerError(Exception):
    """Base class of every error raised by this module."""


class UnknownAccount(LedgerError):
    """No account exists for the given code."""


class DuplicateAccount(LedgerError):
    """An account with the given code already exists."""


class InvalidAmount(LedgerError):
    """The given amount is not a usable monetary value."""


class SameAccountTransfer(LedgerError):
    """Sender and recipient are the same account."""


class InsufficientFunds(LedgerError):
    """The sender does not hold enough money."""


def _validate_amount(value: Decimal, *, allow_zero: bool) -> Decimal:
    """Validate a monetary input and return it quantized to two places.

    Rejects (with ``InvalidAmount``) values that are not ``Decimal``, are not
    finite, cannot be represented exactly with two fractional digits, or are
    negative (and, unless ``allow_zero``, are not strictly positive).
    """
    if not isinstance(value, Decimal):
        raise InvalidAmount("amount must be a decimal.Decimal")
    if not value.is_finite():
        raise InvalidAmount("amount must be a finite number")
    try:
        quantized = value.quantize(TWO_PLACES, context=_QUANT_CTX)
    except (Inexact, InvalidOperation):
        raise InvalidAmount(
            "amount cannot be represented exactly with two fractional digits"
        ) from None
    if allow_zero:
        if quantized < 0:
            raise InvalidAmount("amount must not be negative")
    else:
        if quantized <= 0:
            raise InvalidAmount("amount must be positive")
    return quantized


def _fmt(value: Decimal) -> str:
    """Format a monetary value as a string with exactly two fractional digits."""
    return str(value.quantize(TWO_PLACES, context=_QUANT_CTX))


def _account_dict(row: Any) -> dict:
    return {
        "id": str(row.id),
        "code": row.code,
        "balance": _fmt(row.balance),
    }


def _transfer_dict(row: Any, *, created: bool) -> dict:
    return {
        "id": str(row.id),
        "sender": row.sender.code,
        "recipient": row.recipient.code,
        "amount": _fmt(row.amount),
        "idempotency_key": row.idempotency_key,
        "created": created,
    }


_TRANSFER_BY_KEY_QUERY = """
    select Transfer {
        id, idempotency_key, amount,
        sender: { code },
        recipient: { code },
    }
    filter .idempotency_key = <str>$key
    limit 1
"""


async def _fetch_transfer_by_key(executor, key: str):
    return await executor.query_single(_TRANSFER_BY_KEY_QUERY, key=key)


async def create_account(client, *, code: str, opening_balance: Decimal) -> dict:
    """Create an account and return ``{"id", "code", "balance"}``."""
    quantized = _validate_amount(opening_balance, allow_zero=True)
    try:
        row = await client.query_single(
            """
            select (
                insert Account {
                    code := <str>$code,
                    opening_balance := <decimal>$opening_balance,
                }
            ) { id, code, balance }
            """,
            code=code,
            opening_balance=quantized,
        )
    except gel.ConstraintViolationError:
        raise DuplicateAccount(f"account already exists: {code!r}") from None
    return _account_dict(row)


async def create_transfer(
    client,
    *,
    sender: str,
    recipient: str,
    amount: Decimal,
    idempotency_key: str,
) -> dict:
    """Move money and return
    ``{"id", "sender", "recipient", "amount", "idempotency_key", "created"}``.
    """
    existing = await _fetch_transfer_by_key(client, idempotency_key)
    if existing is not None:
        return _transfer_dict(existing, created=False)

    quantized_amount = _validate_amount(amount, allow_zero=False)

    if sender == recipient:
        raise SameAccountTransfer(
            f"sender and recipient must differ: {sender!r}"
        )

    retrying_client = client.with_retry_options(_TRANSFER_RETRY_OPTIONS)
    # IMPORTANT: we must not `return`/`break` out of the `async with tx:` block
    # on the success path. Gel's retrying-transaction machinery detects a
    # serialization failure at COMMIT time by intercepting the exception
    # raised while the `async with` block exits; if that exit was triggered by
    # a `return`, the (stale, rolled-back) return value would still win the
    # race and escape to the caller before the retry can happen. Stashing the
    # row in an outer variable and returning only after the `for` loop ends
    # naturally (i.e. after a commit that truly succeeded) avoids that trap.
    row = None
    try:
        async for tx in retrying_client.transaction():
            async with tx:
                accounts = await tx.query(
                    """
                    select Account { code, balance }
                    filter .code in {<str>$sender, <str>$recipient}
                    """,
                    sender=sender,
                    recipient=recipient,
                )
                by_code = {a.code: a for a in accounts}
                if sender not in by_code or recipient not in by_code:
                    missing = {sender, recipient} - set(by_code)
                    raise UnknownAccount(
                        f"unknown account(s): {', '.join(sorted(missing))}"
                    )
                if by_code[sender].balance < quantized_amount:
                    raise InsufficientFunds(
                        f"{sender!r} does not have sufficient funds"
                    )

                row = await tx.query_single(
                    """
                    with
                        t := (insert Transfer {
                            idempotency_key := <str>$key,
                            amount := <decimal>$amount,
                            sender := (
                                select Account filter .code = <str>$sender
                            ),
                            recipient := (
                                select Account filter .code = <str>$recipient
                            ),
                        }),
                        debit := (insert LedgerEntry {
                            amount := -<decimal>$amount,
                            account := t.sender,
                            transfer := t,
                        }),
                        credit := (insert LedgerEntry {
                            amount := <decimal>$amount,
                            account := t.recipient,
                            transfer := t,
                        })
                    select t {
                        id, idempotency_key, amount,
                        sender: { code },
                        recipient: { code },
                    }
                    """,
                    key=idempotency_key,
                    amount=quantized_amount,
                    sender=sender,
                    recipient=recipient,
                )
    except gel.ConstraintViolationError:
        # Someone else won the race for this idempotency_key; the unique
        # constraint guarantees their transaction has already committed by
        # the time our insert fails, so a fresh read is guaranteed to find it.
        existing = await _fetch_transfer_by_key(client, idempotency_key)
        if existing is not None:
            return _transfer_dict(existing, created=False)
        raise

    return _transfer_dict(row, created=True)


async def get_balance(client, *, account: str) -> str:
    """Return the current balance of ``account``."""
    row = await client.query_single(
        "select Account { balance } filter .code = <str>$code",
        code=account,
    )
    if row is None:
        raise UnknownAccount(f"unknown account: {account!r}")
    return _fmt(row.balance)


async def get_statement(client, *, account: str, limit: int = 50) -> list[dict]:
    """Return at most ``limit`` statement rows for ``account``, newest first."""
    acc = await client.query_single(
        "select Account { id } filter .code = <str>$code",
        code=account,
    )
    if acc is None:
        raise UnknownAccount(f"unknown account: {account!r}")

    rows = await client.query(
        """
        select LedgerEntry {
            amount,
            transfer: {
                id,
                idempotency_key,
                created_at,
                sender: { code },
                recipient: { code },
            },
        }
        filter .account.code = <str>$code
        order by .transfer.created_at desc then .transfer.idempotency_key desc
        limit <int64>$limit
        """,
        code=account,
        limit=limit,
    )

    statement = []
    for entry in rows:
        transfer = entry.transfer
        if transfer.sender.code == account:
            counterparty = transfer.recipient.code
        else:
            counterparty = transfer.sender.code
        direction = "debit" if entry.amount < 0 else "credit"
        statement.append(
            {
                "transfer_id": str(transfer.id),
                "idempotency_key": transfer.idempotency_key,
                "counterparty": counterparty,
                "direction": direction,
                "amount": _fmt(entry.amount),
            }
        )
    return statement
