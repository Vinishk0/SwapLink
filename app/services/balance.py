"""Referral balance: every movement goes through this module.

`User.balance` / `User.total_earned` / `User.total_paid_out` are running totals
kept in sync with the `transactions` ledger inside one database transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, Transaction, TransactionType, User
from app.services.exceptions import BalanceError
from app.utils.format import quantize

ZERO = Decimal("0")
#: Balances are kept with 8 decimals — bonuses are fractions of a percent.
DECIMALS = 8

WITHDRAWAL_TYPES = (TransactionType.PAYOUT, TransactionType.DISCOUNT)


async def _apply(
    session: AsyncSession,
    user: User,
    amount: Decimal,
    tx_type: TransactionType,
    *,
    order: Order | None = None,
    source_user: User | None = None,
    admin_id: int | None = None,
    comment: str | None = None,
    commit: bool = True,
) -> Transaction:
    """Move `amount` (signed) on the balance and write the ledger row."""
    amount = quantize(amount, DECIMALS, round_down=False)
    if amount == ZERO:
        raise BalanceError("Сумма операции не может быть нулевой.")

    new_balance = quantize(user.balance + amount, DECIMALS, round_down=False)
    if new_balance < ZERO:
        raise BalanceError("Недостаточно средств на реферальном балансе.")

    user.balance = new_balance
    if tx_type is TransactionType.REFERRAL_BONUS and amount > ZERO:
        user.total_earned = quantize(user.total_earned + amount, DECIMALS, round_down=False)
    if tx_type in WITHDRAWAL_TYPES:
        user.total_paid_out = quantize(user.total_paid_out - amount, DECIMALS, round_down=False)

    transaction = Transaction(
        user_id=user.id,
        type=tx_type,
        amount=amount,
        balance_after=new_balance,
        order_id=order.id if order else None,
        source_user_id=source_user.id if source_user else None,
        admin_id=admin_id,
        comment=comment,
    )
    session.add(transaction)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return transaction


async def credit_referral_bonus(
    session: AsyncSession,
    referrer: User,
    amount: Decimal,
    *,
    order: Order,
    source_user: User,
    commit: bool = True,
) -> Transaction | None:
    """Credit the inviter for a confirmed deal of their referral."""
    if amount <= ZERO:
        return None
    return await _apply(
        session,
        referrer,
        amount,
        TransactionType.REFERRAL_BONUS,
        order=order,
        source_user=source_user,
        comment=f"Сделка #{order.id} · {order.direction}",
        commit=commit,
    )


async def withdraw(
    session: AsyncSession,
    user: User,
    amount: Decimal,
    *,
    tx_type: TransactionType,
    admin_id: int | None = None,
    comment: str | None = None,
    order: Order | None = None,
) -> Transaction:
    """Spend part of the balance: a cash payout or a discount on a deal."""
    if tx_type not in WITHDRAWAL_TYPES:
        raise BalanceError("Недопустимый тип списания.")
    if amount <= ZERO:
        raise BalanceError("Сумма списания должна быть больше нуля.")
    if amount > user.balance:
        raise BalanceError(f"На балансе только {user.balance} — списать {amount} нельзя.")
    return await _apply(
        session, user, -amount, tx_type, admin_id=admin_id, comment=comment, order=order
    )


async def adjust(
    session: AsyncSession,
    user: User,
    amount: Decimal,
    *,
    admin_id: int,
    comment: str | None = None,
) -> Transaction:
    """Manual correction by an administrator; `amount` may be negative."""
    return await _apply(
        session, user, amount, TransactionType.ADJUSTMENT, admin_id=admin_id, comment=comment
    )


async def list_transactions(
    session: AsyncSession, user_id: int, *, limit: int = 10, offset: int = 0
) -> Sequence[Transaction]:
    stmt = (
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return (await session.scalars(stmt)).all()


async def count_transactions(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
    return await session.scalar(stmt) or 0
