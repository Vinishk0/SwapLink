"""Order lifecycle.

An order is created by the user as a request (`PENDING`) and only an admin can
move it to a final state. Confirmation is the single place where the referral
programme actually pays out:

* the referral discount is consumed (one of `REFERRAL_DISCOUNT_LIMIT`);
* the inviter is credited `REFERRAL_BONUS_PERCENT` of the deal volume;
* the user loses the ability to attach a referral code manually.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Order, OrderStatus, Pair, Transaction, User
from app.services import balance as balance_service
from app.services import exchange as exchange_service
from app.services import referrals as referrals_service
from app.services.exceptions import OrderError
from app.utils.format import quantize

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(slots=True)
class ConfirmResult:
    """Outcome of a confirmation — handlers use it to notify both sides."""

    order: Order
    referrer: User | None = None
    bonus_transaction: Transaction | None = None
    discount_revoked: bool = False

    @property
    def bonus_amount(self) -> Decimal:
        return self.bonus_transaction.amount if self.bonus_transaction else ZERO


async def create_order(
    session: AsyncSession,
    *,
    user: User,
    pair: Pair,
    amount_from: Decimal,
    settings: Settings,
) -> Order:
    """Turn a calculation into a pending request for the operators."""
    if user.is_blocked:
        raise OrderError("Ваш аккаунт заблокирован, оформление заявок недоступно.")
    if not pair.is_active:
        raise OrderError("Это направление обмена сейчас недоступно.")

    limit_error = exchange_service.check_limits(pair, amount_from)
    if limit_error:
        raise OrderError(limit_error)

    discount = referrals_service.discount_percent_for(user, settings)
    bonus_percent = settings.referral_bonus_percent if user.referrer_id else ZERO
    quote = exchange_service.calculate_quote(
        pair, amount_from, discount_percent=discount, bonus_percent=bonus_percent
    )

    order = Order(
        user_id=user.id,
        pair_id=pair.id,
        from_code=quote.from_code,
        to_code=quote.to_code,
        amount_from=quote.amount_from,
        amount_to=quote.amount_to,
        rate=quote.rate,
        base_commission_percent=quote.base_commission_percent,
        discount_percent=quote.discount_percent,
        commission_percent=quote.commission_percent,
        discount_amount=quote.discount_amount,
        volume_base=quote.volume_base,
        referrer_id=user.referrer_id,
        bonus_amount=quote.bonus_amount,
        status=OrderStatus.PENDING,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


def _recalculate(
    order: Order,
    *,
    amount_from: Decimal,
    discount_percent: Decimal,
    bonus_percent: Decimal,
) -> None:
    """Rewrite the order numbers in place for a new amount and/or discount.

    Uses the live pair when it still exists, otherwise falls back to the rate
    stored on the order (a pair may be deleted while an order is pending).
    """
    if order.pair is not None and order.pair.is_active:
        quote = exchange_service.calculate_quote(
            order.pair,
            amount_from,
            discount_percent=discount_percent,
            bonus_percent=bonus_percent,
        )
        order.rate = quote.rate
        order.amount_from = quote.amount_from
        order.amount_to = quote.amount_to
        order.base_commission_percent = quote.base_commission_percent
        order.discount_percent = quote.discount_percent
        order.commission_percent = quote.commission_percent
        order.discount_amount = quote.discount_amount
        order.volume_base = quote.volume_base
        order.bonus_amount = quote.bonus_amount
        return

    # Fallback: keep the historical rate, scale the volume proportionally.
    ratio = amount_from / order.amount_from if order.amount_from > ZERO else ZERO
    discount = min(max(discount_percent, ZERO), order.base_commission_percent)
    commission = order.base_commission_percent - discount
    gross = amount_from * order.rate

    order.amount_from = amount_from
    order.amount_to = quantize(gross * (HUNDRED - commission) / HUNDRED, 8)
    order.discount_percent = discount
    order.commission_percent = commission
    order.discount_amount = quantize(gross * discount / HUNDRED, 8)
    order.volume_base = quantize(order.volume_base * ratio, 8)
    order.bonus_amount = quantize(order.volume_base * bonus_percent / HUNDRED, 8)


async def confirm_order(
    session: AsyncSession,
    order: Order,
    *,
    admin_id: int,
    settings: Settings,
    amount_from: Decimal | None = None,
    comment: str | None = None,
) -> ConfirmResult:
    """Mark the deal as executed, consume the discount and pay the inviter."""
    if not order.is_pending:
        raise OrderError(f"Заявка #{order.id} уже обработана ({order.status.title}).")

    user = order.user
    referrer: User | None = None
    if order.referrer_id is not None:
        referrer = await session.get(User, order.referrer_id)

    # Eligibility is re-checked here: several requests may have been pending at
    # the same time, and only confirmations consume the limit.
    eligible = (
        referrer is not None
        and not referrer.is_blocked
        and user.discounts_used < settings.referral_discount_limit
    )
    discount = settings.referral_discount_percent if eligible else ZERO
    discount_revoked = order.discount_percent > ZERO and not eligible
    bonus_percent = settings.referral_bonus_percent if referrer is not None else ZERO

    new_amount = amount_from if amount_from is not None else order.amount_from
    if new_amount <= ZERO:
        raise OrderError("Сумма сделки должна быть больше нуля.")

    _recalculate(
        order, amount_from=new_amount, discount_percent=discount, bonus_percent=bonus_percent
    )

    order.status = OrderStatus.CONFIRMED
    order.admin_id = admin_id
    order.processed_at = datetime.now(UTC)
    order.discount_applied = order.discount_percent > ZERO
    if comment:
        order.admin_comment = comment

    user.deals_count += 1
    if order.discount_applied:
        user.discounts_used += 1

    bonus_tx = None
    if referrer is not None and order.bonus_amount > ZERO and not referrer.is_blocked:
        bonus_tx = await balance_service.credit_referral_bonus(
            session,
            referrer,
            order.bonus_amount,
            order=order,
            source_user=user,
            commit=False,
        )
    else:
        order.bonus_amount = ZERO

    await session.commit()
    return ConfirmResult(
        order=order,
        referrer=referrer,
        bonus_transaction=bonus_tx,
        discount_revoked=discount_revoked,
    )


async def update_pending_amount(
    session: AsyncSession, order: Order, *, amount_from: Decimal, settings: Settings
) -> Order:
    """Let an operator correct the amount before confirming the deal."""
    if not order.is_pending:
        raise OrderError("Изменить сумму можно только у заявки в ожидании.")
    if amount_from <= ZERO:
        raise OrderError("Сумма сделки должна быть больше нуля.")

    user = order.user
    eligible = (
        order.referrer_id is not None and user.discounts_used < settings.referral_discount_limit
    )
    discount = settings.referral_discount_percent if eligible else ZERO
    bonus_percent = settings.referral_bonus_percent if order.referrer_id else ZERO
    _recalculate(
        order, amount_from=amount_from, discount_percent=discount, bonus_percent=bonus_percent
    )
    await session.commit()
    return order


async def reject_order(
    session: AsyncSession, order: Order, *, admin_id: int, comment: str | None = None
) -> Order:
    if not order.is_pending:
        raise OrderError(f"Заявка #{order.id} уже обработана ({order.status.title}).")
    order.status = OrderStatus.REJECTED
    order.admin_id = admin_id
    order.admin_comment = comment
    order.processed_at = datetime.now(UTC)
    await session.commit()
    return order


async def cancel_order(session: AsyncSession, order: Order, *, user: User) -> Order:
    if order.user_id != user.id:
        raise OrderError("Это не ваша заявка.")
    if not order.is_pending:
        raise OrderError("Заявку уже обработал оператор — отменить нельзя.")
    order.status = OrderStatus.CANCELLED
    order.processed_at = datetime.now(UTC)
    await session.commit()
    return order


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    return await session.get(Order, order_id)


def _filtered(stmt: Select, *, status: OrderStatus | None, user_id: int | None) -> Select:
    if status is not None:
        stmt = stmt.where(Order.status == status)
    if user_id is not None:
        stmt = stmt.where(Order.user_id == user_id)
    return stmt


async def list_orders_page(
    session: AsyncSession,
    *,
    status: OrderStatus | None = None,
    user_id: int | None = None,
    page: int = 1,
    per_page: int = 8,
) -> tuple[Sequence[Order], int]:
    total = (
        await session.scalar(
            _filtered(select(func.count(Order.id)), status=status, user_id=user_id)
        )
        or 0
    )
    page = max(page, 1)
    stmt = _filtered(select(Order), status=status, user_id=user_id)
    rows = (
        await session.scalars(
            stmt.order_by(Order.id.desc()).limit(per_page).offset((page - 1) * per_page)
        )
    ).all()
    return rows, total


async def count_pending(session: AsyncSession) -> int:
    stmt = select(func.count(Order.id)).where(Order.status == OrderStatus.PENDING)
    return await session.scalar(stmt) or 0
