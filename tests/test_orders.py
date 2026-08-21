"""End-to-end of the money rules: discounts, accruals, limits."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import OrderStatus, Pair, TransactionType
from app.services import balance as balance_service
from app.services import orders as orders_service
from app.services import referrals as referrals_service
from app.services.exceptions import BalanceError, OrderError


async def _make_pair_of_users(make_user):
    return await make_user(1), await make_user(2)


async def _deal(session: AsyncSession, user, pair: Pair, settings: Settings, amount: str = "100"):
    order = await orders_service.create_order(
        session, user=user, pair=pair, amount_from=Decimal(amount), settings=settings
    )
    return await orders_service.confirm_order(session, order, admin_id=1, settings=settings)


async def test_plain_order_has_no_discount_and_no_bonus(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    user = await make_user(1)
    order = await orders_service.create_order(
        session, user=user, pair=pair, amount_from=Decimal("100"), settings=settings
    )

    assert order.status is OrderStatus.PENDING
    assert order.discount_percent == Decimal("0")
    assert order.bonus_amount == Decimal("0")
    assert order.amount_to == Decimal("10000.00")


async def test_referral_gets_the_discount_and_the_inviter_gets_paid(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    inviter, invitee = await _make_pair_of_users(make_user)
    await referrals_service.bind_referrer(session, invitee, inviter.ref_code)

    result = await _deal(session, invitee, pair, settings)
    order = result.order

    assert order.status is OrderStatus.CONFIRMED
    assert order.discount_applied is True
    assert order.discount_percent == Decimal("0.5")
    # 10 000 RUB at the plain rate + 0.5% for the referral
    assert order.amount_to == Decimal("10050.00")

    # 0.5% of a 100 USDT volume
    assert order.bonus_amount == Decimal("0.5")
    assert inviter.balance == Decimal("0.5")
    assert inviter.total_earned == Decimal("0.5")
    assert invitee.discounts_used == 1
    assert invitee.deals_count == 1

    transactions = await balance_service.list_transactions(session, inviter.id)
    assert len(transactions) == 1
    assert transactions[0].type is TransactionType.REFERRAL_BONUS
    assert transactions[0].source_user_id == invitee.id


async def test_discount_stops_after_three_deals_but_bonus_continues(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    inviter, invitee = await _make_pair_of_users(make_user)
    await referrals_service.bind_referrer(session, invitee, inviter.ref_code)

    for _ in range(settings.referral_discount_limit):
        result = await _deal(session, invitee, pair, settings)
        assert result.order.discount_applied is True

    assert invitee.discounts_used == 3

    fourth = await _deal(session, invitee, pair, settings)

    assert fourth.order.discount_applied is False
    assert fourth.order.discount_percent == Decimal("0")
    assert fourth.order.amount_to == Decimal("10000.00")
    # The inviter still earns from the fourth deal
    assert fourth.order.bonus_amount == Decimal("0.5")
    assert inviter.total_earned == Decimal("2.0")
    assert invitee.discounts_used == 3
    assert invitee.deals_count == 4


async def test_discount_is_revoked_when_the_limit_is_hit_between_request_and_confirm(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    inviter, invitee = await _make_pair_of_users(make_user)
    await referrals_service.bind_referrer(session, invitee, inviter.ref_code)

    pending = await orders_service.create_order(
        session, user=invitee, pair=pair, amount_from=Decimal("100"), settings=settings
    )
    assert pending.discount_percent == Decimal("0.5")

    # Three other deals eat the whole quota before the operator gets to this one.
    for _ in range(3):
        await _deal(session, invitee, pair, settings)

    result = await orders_service.confirm_order(session, pending, admin_id=1, settings=settings)

    assert result.discount_revoked is True
    assert result.order.discount_applied is False
    assert result.order.amount_to == Decimal("10000.00")


async def test_operator_can_correct_the_amount(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    inviter, invitee = await _make_pair_of_users(make_user)
    await referrals_service.bind_referrer(session, invitee, inviter.ref_code)

    order = await orders_service.create_order(
        session, user=invitee, pair=pair, amount_from=Decimal("100"), settings=settings
    )
    await orders_service.update_pending_amount(
        session, order, amount_from=Decimal("250"), settings=settings
    )

    assert order.amount_from == Decimal("250")
    assert order.volume_base == Decimal("250")
    assert order.bonus_amount == Decimal("1.25")

    await orders_service.confirm_order(session, order, admin_id=1, settings=settings)
    assert inviter.balance == Decimal("1.25")


async def test_rejected_order_pays_nobody(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    inviter, invitee = await _make_pair_of_users(make_user)
    await referrals_service.bind_referrer(session, invitee, inviter.ref_code)

    order = await orders_service.create_order(
        session, user=invitee, pair=pair, amount_from=Decimal("100"), settings=settings
    )
    await orders_service.reject_order(session, order, admin_id=1, comment="нет наличных")

    assert order.status is OrderStatus.REJECTED
    assert inviter.balance == Decimal("0")
    assert invitee.discounts_used == 0
    assert invitee.deals_count == 0

    with pytest.raises(OrderError):
        await orders_service.confirm_order(session, order, admin_id=1, settings=settings)


async def test_user_can_cancel_only_their_own_pending_order(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    owner, stranger = await _make_pair_of_users(make_user)
    order = await orders_service.create_order(
        session, user=owner, pair=pair, amount_from=Decimal("100"), settings=settings
    )

    with pytest.raises(OrderError):
        await orders_service.cancel_order(session, order, user=stranger)

    await orders_service.cancel_order(session, order, user=owner)
    assert order.status is OrderStatus.CANCELLED


async def test_admin_payout_and_discount_write_off(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    inviter, invitee = await _make_pair_of_users(make_user)
    await referrals_service.bind_referrer(session, invitee, inviter.ref_code)
    await _deal(session, invitee, pair, settings, amount="10000")  # bonus = 50

    assert inviter.balance == Decimal("50")

    await balance_service.withdraw(
        session, inviter, Decimal("20"), tx_type=TransactionType.PAYOUT, admin_id=1
    )
    await balance_service.withdraw(
        session, inviter, Decimal("5"), tx_type=TransactionType.DISCOUNT, admin_id=1
    )

    assert inviter.balance == Decimal("25")
    assert inviter.total_paid_out == Decimal("25")
    # Lifetime earnings are not touched by payouts
    assert inviter.total_earned == Decimal("50")

    with pytest.raises(BalanceError):
        await balance_service.withdraw(
            session, inviter, Decimal("100"), tx_type=TransactionType.PAYOUT, admin_id=1
        )


async def test_manual_adjustment(session: AsyncSession, make_user, settings: Settings) -> None:
    user = await make_user(1)

    await balance_service.adjust(session, user, Decimal("10"), admin_id=1, comment="бонус")
    assert user.balance == Decimal("10")

    await balance_service.adjust(session, user, Decimal("-4"), admin_id=1)
    assert user.balance == Decimal("6")

    with pytest.raises(BalanceError):
        await balance_service.adjust(session, user, Decimal("-100"), admin_id=1)


async def test_blocked_user_cannot_create_orders(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    user = await make_user(1)
    user.is_blocked = True

    with pytest.raises(OrderError):
        await orders_service.create_order(
            session, user=user, pair=pair, amount_from=Decimal("100"), settings=settings
        )


async def test_bonus_write_off_is_shown_on_the_deal(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    """An admin spending a client's balance must change what the deal pays out."""
    client = await make_user(1)
    order = await orders_service.create_order(
        session, user=client, pair=pair, amount_from=Decimal("100"), settings=settings
    )
    assert order.total_to == order.amount_to

    # 5 USDT of balance, RUB is 0.01 of the base -> +500 RUB for the client
    await orders_service.apply_bonus_write_off(session, order, amount_base=Decimal("5"))

    assert order.bonus_spent == Decimal("5")
    assert order.bonus_spent_to == Decimal("500.00")
    assert order.total_to == Decimal("10500.00")
    assert order.has_bonus_spent is True


async def test_admin_cards_are_tracked_and_cleared(
    session: AsyncSession, make_user, pair: Pair, settings: Settings
) -> None:
    user = await make_user(1)
    order = await orders_service.create_order(
        session, user=user, pair=pair, amount_from=Decimal("100"), settings=settings
    )

    await orders_service.remember_admin_message(session, order, 111, 222)
    await orders_service.remember_admin_message(session, order, 333, 444)
    assert order.admin_message_refs == [(111, 222), (333, 444)]

    refs = await orders_service.clear_admin_messages(session, order)
    assert refs == [(111, 222), (333, 444)]
    assert order.admin_message_refs == []
