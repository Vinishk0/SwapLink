"""Aggregated numbers for the admin dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, Transaction, TransactionType, User

ZERO = Decimal("0")


def _dec(value: object) -> Decimal:
    return Decimal(str(value)) if value is not None else ZERO


@dataclass(slots=True)
class Stats:
    users_total: int
    users_new_24h: int
    referrals_total: int
    orders_total: int
    orders_pending: int
    orders_confirmed: int
    volume_base: Decimal
    bonuses_accrued: Decimal
    bonuses_paid: Decimal
    balances_outstanding: Decimal


async def collect(session: AsyncSession) -> Stats:
    day_ago = datetime.now(UTC) - timedelta(hours=24)

    users_total = await session.scalar(select(func.count(User.id))) or 0
    users_new = (
        await session.scalar(select(func.count(User.id)).where(User.created_at >= day_ago)) or 0
    )
    referrals_total = (
        await session.scalar(select(func.count(User.id)).where(User.referrer_id.is_not(None))) or 0
    )

    orders_total = await session.scalar(select(func.count(Order.id))) or 0
    orders_pending = (
        await session.scalar(
            select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING)
        )
        or 0
    )
    orders_confirmed = (
        await session.scalar(
            select(func.count(Order.id)).where(Order.status == OrderStatus.CONFIRMED)
        )
        or 0
    )

    volume = await session.scalar(
        select(func.sum(Order.volume_base)).where(Order.status == OrderStatus.CONFIRMED)
    )
    accrued = await session.scalar(
        select(func.sum(Transaction.amount)).where(
            Transaction.type == TransactionType.REFERRAL_BONUS
        )
    )
    paid = await session.scalar(
        select(func.sum(Transaction.amount)).where(
            Transaction.type.in_((TransactionType.PAYOUT, TransactionType.DISCOUNT))
        )
    )
    outstanding = await session.scalar(select(func.sum(User.balance)))

    return Stats(
        users_total=users_total,
        users_new_24h=users_new,
        referrals_total=referrals_total,
        orders_total=orders_total,
        orders_pending=orders_pending,
        orders_confirmed=orders_confirmed,
        volume_base=_dec(volume),
        bonuses_accrued=_dec(accrued),
        bonuses_paid=abs(_dec(paid)),
        balances_outstanding=_dec(outstanding),
    )
