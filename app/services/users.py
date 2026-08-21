"""User registration and lookups."""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, Transaction, TransactionType, User

#: Unambiguous alphabet: no O/0, I/1 — codes get retyped by hand.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
ZERO = Decimal("0")


async def generate_ref_code(session: AsyncSession) -> str:
    """Generate a short referral code that is not taken yet."""
    for _ in range(20):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
        if not await session.scalar(select(User.id).where(User.ref_code == code)):
            return code
    raise RuntimeError("Could not generate a unique referral code")  # pragma: no cover


async def get_by_tg_id(session: AsyncSession, tg_id: int) -> User | None:
    return await session.scalar(select(User).where(User.tg_id == tg_id))


async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_by_ref_code(session: AsyncSession, code: str) -> User | None:
    return await session.scalar(select(User).where(User.ref_code == code.strip().upper()))


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    cleaned = username.strip().lstrip("@")
    if not cleaned:
        return None
    return await session.scalar(select(User).where(func.lower(User.username) == cleaned.lower()))


async def get_or_create(
    session: AsyncSession,
    *,
    tg_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> tuple[User, bool]:
    """Fetch the user by Telegram id, creating them on first contact.

    Returns `(user, created)`. Profile fields are refreshed on every call so the
    admin panel shows the current username.
    """
    user = await get_by_tg_id(session, tg_id)
    if user is not None:
        changed = (
            user.username != username
            or user.first_name != first_name
            or user.last_name != last_name
        )
        if changed:
            user.username, user.first_name, user.last_name = username, first_name, last_name
            await session.commit()
        return user, False

    user = User(
        tg_id=tg_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        ref_code=await generate_ref_code(session),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user, True


async def count_users(session: AsyncSession) -> int:
    return await session.scalar(select(func.count(User.id))) or 0


async def count_referrals(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(select(func.count(User.id)).where(User.referrer_id == user_id)) or 0


async def list_referrals_page(
    session: AsyncSession, user_id: int, *, page: int = 1, per_page: int = 8
) -> tuple[list[tuple[User, Decimal]], int]:
    """One page of referrals, each with how much they earned for the inviter.

    Returns `([(referral, earned), ...], total)`. Sorted by earnings so the
    people who actually bring money are on the first page.
    """
    earned = (
        select(
            Transaction.source_user_id.label("source_user_id"),
            func.sum(Transaction.amount).label("earned"),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.REFERRAL_BONUS,
            Transaction.source_user_id.is_not(None),
        )
        .group_by(Transaction.source_user_id)
        .subquery()
    )

    total = await count_referrals(session, user_id)
    page = max(page, 1)
    stmt = (
        select(User, earned.c.earned)
        .outerjoin(earned, earned.c.source_user_id == User.id)
        .where(User.referrer_id == user_id)
        .order_by(earned.c.earned.desc().nullslast(), User.id.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    rows = (await session.execute(stmt)).all()
    return [
        (user, Decimal(str(value)) if value is not None else ZERO) for user, value in rows
    ], total


def _search_filter(stmt: Select, query: str) -> Select:
    query = query.strip().lstrip("@")
    like = f"%{query.lower()}%"
    conditions = [
        func.lower(User.username).like(like),
        func.lower(User.first_name).like(like),
        func.lower(User.last_name).like(like),
        User.ref_code == query.upper(),
    ]
    if query.isdigit():
        conditions.append(User.tg_id == int(query))
    return stmt.where(or_(*conditions))


async def search_users(session: AsyncSession, query: str, *, limit: int = 20) -> Sequence[User]:
    stmt = _search_filter(select(User), query).order_by(User.id.desc()).limit(limit)
    return (await session.scalars(stmt)).all()


async def list_users_page(
    session: AsyncSession, *, page: int = 1, per_page: int = 8, query: str | None = None
) -> tuple[Sequence[User], int]:
    """Return one page of users (newest first) and the total row count."""
    stmt = select(User)
    count_stmt = select(func.count(User.id))
    if query:
        stmt = _search_filter(stmt, query)
        count_stmt = _search_filter(count_stmt, query)  # type: ignore[arg-type]

    total = await session.scalar(count_stmt) or 0
    page = max(page, 1)
    rows = (
        await session.scalars(
            stmt.order_by(User.id.desc()).limit(per_page).offset((page - 1) * per_page)
        )
    ).all()
    return rows, total


async def count_confirmed_orders(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count(Order.id)).where(
        Order.user_id == user_id, Order.status == OrderStatus.CONFIRMED
    )
    return await session.scalar(stmt) or 0


async def set_blocked(session: AsyncSession, user: User, blocked: bool) -> User:
    user.is_blocked = blocked
    await session.commit()
    return user
