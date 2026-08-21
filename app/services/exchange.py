"""Rate maintenance and the exchange calculator.

The bot never executes a deal and never adds a commission of its own: the rate
an administrator sets is the final client rate. `calculate_quote` is the single
source of truth for what a user gets, how much the referral discount added and
how large the bonus of the inviter would be.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Currency, Pair
from app.services.exceptions import RateError
from app.utils.format import format_amount, quantize

ZERO = Decimal("0")
HUNDRED = Decimal("100")
#: Bonuses are tiny fractions of a percent — keep more precision than money display.
BONUS_DECIMALS = 8


@dataclass(frozen=True, slots=True)
class Quote:
    """Full breakdown of a calculation, ready to be shown and/or stored."""

    pair_id: int
    from_code: str
    to_code: str
    from_decimals: int
    to_decimals: int
    amount_from: Decimal
    rate: Decimal
    #: What the client would get at the plain rate, before the referral discount.
    base_to: Decimal
    #: What the client actually gets — `base_to` plus the discount.
    amount_to: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    volume_base: Decimal
    bonus_amount: Decimal

    @property
    def has_discount(self) -> bool:
        return self.discount_percent > ZERO


def calculate_quote(
    pair: Pair,
    amount_from: Decimal,
    *,
    discount_percent: Decimal = ZERO,
    bonus_percent: Decimal = ZERO,
) -> Quote:
    """Convert `amount_from` through `pair`, adding the referral discount.

    The discount is a bonus on top of the rate: with 0.5% a referral receives
    0.5% more of the target currency than the published rate gives.
    """
    if amount_from <= ZERO:
        raise RateError("Сумма обмена должна быть больше нуля.")

    rate = pair.effective_rate
    if rate <= ZERO:
        raise RateError(f"Для направления {pair.title} не задан корректный курс.")

    discount = max(discount_percent, ZERO)
    base_to = amount_from * rate
    discount_amount = base_to * discount / HUNDRED
    amount_to = base_to + discount_amount

    volume_base = amount_from * pair.from_currency.rate_to_base
    bonus_amount = volume_base * bonus_percent / HUNDRED

    to_decimals = pair.to_currency.decimals
    return Quote(
        pair_id=pair.id,
        from_code=pair.from_currency.code,
        to_code=pair.to_currency.code,
        from_decimals=pair.from_currency.decimals,
        to_decimals=to_decimals,
        amount_from=amount_from,
        rate=rate,
        base_to=quantize(base_to, to_decimals),
        amount_to=quantize(amount_to, to_decimals),
        discount_percent=discount,
        discount_amount=quantize(discount_amount, to_decimals),
        volume_base=quantize(volume_base, BONUS_DECIMALS),
        bonus_amount=quantize(bonus_amount, BONUS_DECIMALS),
    )


def check_limits(pair: Pair, amount_from: Decimal) -> str | None:
    """Return a human-readable error when the amount is outside the pair limits."""
    code, decimals = pair.from_currency.code, pair.from_currency.decimals
    if pair.min_amount is not None and amount_from < pair.min_amount:
        return f"Минимальная сумма обмена — {format_amount(pair.min_amount, code, decimals)}."
    if pair.max_amount is not None and amount_from > pair.max_amount:
        return f"Максимальная сумма обмена — {format_amount(pair.max_amount, code, decimals)}."
    return None


# --------------------------------------------------------------------------- #
# Currencies
# --------------------------------------------------------------------------- #


async def list_currencies(
    session: AsyncSession, *, only_active: bool = False
) -> Sequence[Currency]:
    stmt = select(Currency).order_by(Currency.sort_order, Currency.code)
    if only_active:
        stmt = stmt.where(Currency.is_active.is_(True))
    return (await session.scalars(stmt)).all()


async def get_currency(session: AsyncSession, currency_id: int) -> Currency | None:
    return await session.get(Currency, currency_id)


async def get_currency_by_code(session: AsyncSession, code: str) -> Currency | None:
    return await session.scalar(select(Currency).where(Currency.code == code.strip().upper()))


async def create_currency(
    session: AsyncSession,
    *,
    code: str,
    name: str,
    rate_to_base: Decimal,
    decimals: int = 2,
) -> Currency:
    code = code.strip().upper()
    if await get_currency_by_code(session, code):
        raise RateError(f"Валюта {code} уже существует.")
    if rate_to_base <= ZERO:
        raise RateError("Курс к базовой валюте должен быть больше нуля.")
    currency = Currency(code=code, name=name.strip(), rate_to_base=rate_to_base, decimals=decimals)
    session.add(currency)
    await session.commit()
    return currency


async def update_currency_rate(
    session: AsyncSession, currency: Currency, rate_to_base: Decimal
) -> Currency:
    if rate_to_base <= ZERO:
        raise RateError("Курс к базовой валюте должен быть больше нуля.")
    currency.rate_to_base = rate_to_base
    await session.commit()
    return currency


async def toggle_currency(session: AsyncSession, currency: Currency) -> Currency:
    currency.is_active = not currency.is_active
    await session.commit()
    return currency


async def delete_currency(session: AsyncSession, currency: Currency) -> None:
    await session.delete(currency)
    await session.commit()


# --------------------------------------------------------------------------- #
# Pairs
# --------------------------------------------------------------------------- #


def _ordered_pairs_stmt():
    src = aliased(Currency)
    dst = aliased(Currency)
    return (
        select(Pair)
        .join(src, Pair.from_currency_id == src.id)
        .join(dst, Pair.to_currency_id == dst.id)
        .order_by(src.sort_order, src.code, dst.sort_order, dst.code)
    )


async def list_pairs(session: AsyncSession, *, only_active: bool = False) -> Sequence[Pair]:
    stmt = _ordered_pairs_stmt()
    if only_active:
        stmt = stmt.where(Pair.is_active.is_(True))
    return (await session.scalars(stmt)).all()


async def list_available_pairs(session: AsyncSession) -> list[Pair]:
    """Active pairs whose both currencies are active — what users may choose."""
    pairs = await list_pairs(session, only_active=True)
    return [p for p in pairs if p.from_currency.is_active and p.to_currency.is_active]


async def get_pair(session: AsyncSession, pair_id: int) -> Pair | None:
    return await session.get(Pair, pair_id)


async def create_pair(
    session: AsyncSession,
    *,
    from_currency: Currency,
    to_currency: Currency,
    rate: Decimal | None = None,
) -> Pair:
    if from_currency.id == to_currency.id:
        raise RateError("Направление обмена должно состоять из двух разных валют.")
    exists = await session.scalar(
        select(Pair).where(
            Pair.from_currency_id == from_currency.id,
            Pair.to_currency_id == to_currency.id,
        )
    )
    if exists:
        raise RateError(f"Направление {from_currency.code} → {to_currency.code} уже существует.")
    pair = Pair(
        from_currency_id=from_currency.id,
        to_currency_id=to_currency.id,
        rate=rate,
    )
    session.add(pair)
    await session.commit()
    await session.refresh(pair)
    return pair


async def update_pair(
    session: AsyncSession,
    pair: Pair,
    *,
    rate: Decimal | type[Ellipsis] | None = ...,
    min_amount: Decimal | type[Ellipsis] | None = ...,
    max_amount: Decimal | type[Ellipsis] | None = ...,
) -> Pair:
    """Update a pair. Pass `None` explicitly to clear rate/limits; omit to keep."""
    if rate is not ...:
        if rate is not None and rate <= ZERO:
            raise RateError("Курс должен быть больше нуля.")
        pair.rate = rate  # type: ignore[assignment]
    if min_amount is not ...:
        pair.min_amount = min_amount  # type: ignore[assignment]
    if max_amount is not ...:
        pair.max_amount = max_amount  # type: ignore[assignment]
    await session.commit()
    return pair


async def toggle_pair(session: AsyncSession, pair: Pair) -> Pair:
    pair.is_active = not pair.is_active
    await session.commit()
    return pair


async def delete_pair(session: AsyncSession, pair: Pair) -> None:
    await session.delete(pair)
    await session.commit()
