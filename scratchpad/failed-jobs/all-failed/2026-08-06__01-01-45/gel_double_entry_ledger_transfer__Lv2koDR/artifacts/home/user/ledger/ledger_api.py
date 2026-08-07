"""Public API of the ledger service.

Every function receives an already configured ``gel.AsyncIOClient`` as its first
positional argument; all remaining arguments are keyword-only.  Monetary values are
handed in as ``decimal.Decimal`` and handed back as strings with exactly two
fractional digits.
"""

from __future__ import annotations

from decimal import Decimal
import functools
import gel


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


def format_amount(val: Decimal) -> str:
    """Format a Decimal to exactly two fractional digits."""
    return str(val.quantize(Decimal("0.01")))


def wrap_errors(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except LedgerError:
            # Already a LedgerError subclass, let it propagate
            raise
        except Exception as e:
            # Wrap any other exception in LedgerError
            raise LedgerError(f"Database or system error: {e}") from e
    return wrapper


@wrap_errors
async def create_account(client, *, code: str, opening_balance: Decimal) -> dict:
    """Create an account and return ``{"id", "code", "balance"}``."""
    # Check InvalidAmount
    if opening_balance < 0 or (opening_balance * 100 != int(opening_balance * 100)):
        raise InvalidAmount("opening_balance is negative or not exactly representable with two fractional digits")
        
    try:
        res = await client.query_single('''
            insert Account {
                code := <str>$code,
                opening_balance := <decimal>$opening_balance
            }
        ''', code=code, opening_balance=opening_balance)
        
        return {
            "id": str(res.id),
            "code": code,
            "balance": format_amount(opening_balance)
        }
    except gel.errors.ConstraintViolationError as e:
        raise DuplicateAccount(f"Account with code '{code}' already exists") from e


@wrap_errors
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
    # 1. Check if transfer with idempotency_key already exists
    existing = await client.query_single('''
        select Transfer {
            id,
            sender: { code },
            recipient: { code },
            amount,
            idempotency_key
        } filter .idempotency_key = <str>$idempotency_key
    ''', idempotency_key=idempotency_key)
    
    if existing is not None:
        return {
            "id": str(existing.id),
            "sender": existing.sender.code,
            "recipient": existing.recipient.code,
            "amount": format_amount(existing.amount),
            "idempotency_key": existing.idempotency_key,
            "created": False
        }

    # 2. Check arguments in order:
    # Check 1: InvalidAmount (amount <= 0 or not exactly representable with two fractional digits)
    if amount <= 0 or (amount * 100 != int(amount * 100)):
        raise InvalidAmount("Amount is negative, zero, or has more than two decimal places")
        
    # Check 2: SameAccountTransfer
    if sender == recipient:
        raise SameAccountTransfer("Sender and recipient are the same account")

    result = None
    try:
        async for tx in client.transaction():
            async with tx:
                # Double check inside transaction if transfer was created concurrently
                existing = await tx.query_single('''
                    select Transfer {
                        id,
                        sender: { code },
                        recipient: { code },
                        amount,
                        idempotency_key
                    } filter .idempotency_key = <str>$idempotency_key
                ''', idempotency_key=idempotency_key)
                
                if existing is not None:
                    result = {
                        "id": str(existing.id),
                        "sender": existing.sender.code,
                        "recipient": existing.recipient.code,
                        "amount": format_amount(existing.amount),
                        "idempotency_key": existing.idempotency_key,
                        "created": False
                    }
                else:
                    # Check 3: UnknownAccount
                    accounts = await tx.query('''
                        select Account {
                            code,
                            balance
                        } filter .code in {<str>$sender, <str>$recipient}
                    ''', sender=sender, recipient=recipient)
                    
                    account_map = {a.code: a for a in accounts}
                    if sender not in account_map or recipient not in account_map:
                        raise UnknownAccount("One or both accounts do not exist")
                        
                    # Check 4: InsufficientFunds
                    sender_acc = account_map[sender]
                    if sender_acc.balance < amount:
                        raise InsufficientFunds("Sender has insufficient funds")
                        
                    # Perform insertions
                    transfer_res = await tx.query_single('''
                        insert Transfer {
                            idempotency_key := <str>$idempotency_key,
                            amount := <decimal>$amount,
                            sender := (select Account filter .code = <str>$sender),
                            recipient := (select Account filter .code = <str>$recipient)
                        }
                    ''', idempotency_key=idempotency_key, amount=amount, sender=sender, recipient=recipient)
                    
                    await tx.execute('''
                        insert LedgerEntry {
                            amount := -<decimal>$amount,
                            account := (select Account filter .code = <str>$sender),
                            transfer := (select Transfer filter .id = <uuid>$transfer_id)
                        };
                        insert LedgerEntry {
                            amount := <decimal>$amount,
                            account := (select Account filter .code = <str>$recipient),
                            transfer := (select Transfer filter .id = <uuid>$transfer_id)
                        };
                    ''', amount=amount, sender=sender, recipient=recipient, transfer_id=transfer_res.id)
                    
                    result = {
                        "id": str(transfer_res.id),
                        "sender": sender,
                        "recipient": recipient,
                        "amount": format_amount(amount),
                        "idempotency_key": idempotency_key,
                        "created": True
                    }
    except gel.errors.ConstraintViolationError:
        # Query again outside transaction to fetch concurrently created transfer
        existing = await client.query_single('''
            select Transfer {
                id,
                sender: { code },
                recipient: { code },
                amount,
                idempotency_key
            } filter .idempotency_key = <str>$idempotency_key
        ''', idempotency_key=idempotency_key)
        
        if existing is not None:
            return {
                "id": str(existing.id),
                "sender": existing.sender.code,
                "recipient": existing.recipient.code,
                "amount": format_amount(existing.amount),
                "idempotency_key": existing.idempotency_key,
                "created": False
            }
        else:
            raise

    return result


@wrap_errors
async def get_balance(client, *, account: str) -> str:
    """Return the current balance of ``account``."""
    res = await client.query_single('''
        select Account {
            balance
        } filter .code = <str>$account
    ''', account=account)
    
    if res is None:
        raise UnknownAccount(f"Account with code '{account}' does not exist")
        
    return format_amount(res.balance)


@wrap_errors
async def get_statement(client, *, account: str, limit: int = 50) -> list[dict]:
    """Return at most ``limit`` statement rows for ``account``, newest first."""
    acc_exists = await client.query_single('''
        select Account filter .code = <str>$account
    ''', account=account)
    if acc_exists is None:
        raise UnknownAccount(f"Account with code '{account}' does not exist")
        
    transfers = await client.query('''
        select Transfer {
            id,
            idempotency_key,
            sender: { code },
            recipient: { code },
            amount
        } filter .sender.code = <str>$account or .recipient.code = <str>$account
        order by .created_at desc then .idempotency_key desc
        limit <int64>$limit
    ''', account=account, limit=limit)
    
    results = []
    for t in transfers:
        if t.sender.code == account:
            direction = "debit"
            signed_amount = -t.amount
            counterparty = t.recipient.code
        else:
            direction = "credit"
            signed_amount = t.amount
            counterparty = t.sender.code
            
        results.append({
            "transfer_id": str(t.id),
            "idempotency_key": t.idempotency_key,
            "counterparty": counterparty,
            "direction": direction,
            "amount": format_amount(signed_amount)
        })
        
    return results
