"""Public API of the ledger service.

Every function receives an already configured ``gel.AsyncIOClient`` as its first
positional argument; all remaining arguments are keyword-only.  Monetary values are
handed in as ``decimal.Decimal`` and handed back as strings with exactly two
fractional digits.

Nothing here is implemented yet.
"""

from __future__ import annotations

from decimal import Decimal


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


async def create_account(client, *, code: str, opening_balance: Decimal) -> dict:
    """Create an account and return ``{"id", "code", "balance"}``."""
    raise NotImplementedError("create_account is not implemented yet")


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
    raise NotImplementedError("create_transfer is not implemented yet")


async def get_balance(client, *, account: str) -> str:
    """Return the current balance of ``account``."""
    raise NotImplementedError("get_balance is not implemented yet")


async def get_statement(client, *, account: str, limit: int = 50) -> list:
    """Return at most ``limit`` statement rows for ``account``, newest first."""
    raise NotImplementedError("get_statement is not implemented yet")
