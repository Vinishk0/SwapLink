"""Referral binding rules and link parsing."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ReferralSource
from app.services import referrals as referrals_service
from app.services.exceptions import ReferralError


@pytest.mark.parametrize(
    "raw",
    [
        "ABCD2345",
        "ref_ABCD2345",
        "abcd2345",
        "https://t.me/swaplink_bot?start=ref_ABCD2345",
        "t.me/swaplink_bot?start=ABCD2345",
    ],
)
def test_extract_code(raw: str) -> None:
    assert referrals_service.extract_code(raw) == "ABCD2345"


@pytest.mark.parametrize("raw", ["", "   ", "https://example.com", "@swaplink_bot"])
def test_extract_code_rejects_garbage(raw: str) -> None:
    assert referrals_service.extract_code(raw) is None


def test_build_ref_link() -> None:
    link = referrals_service.build_ref_link("swaplink_bot", "ABCD2345")
    assert link == "https://t.me/swaplink_bot?start=ref_ABCD2345"


async def test_bind_by_link(session: AsyncSession, make_user) -> None:
    inviter = await make_user(1)
    invitee = await make_user(2)

    referrer = await referrals_service.bind_referrer(session, invitee, inviter.ref_code)

    assert referrer.id == inviter.id
    assert invitee.referrer_id == inviter.id
    assert invitee.referral_source is ReferralSource.LINK
    assert invitee.referred_at is not None


async def test_cannot_bind_twice(session: AsyncSession, make_user) -> None:
    first = await make_user(1)
    second = await make_user(2)
    invitee = await make_user(3)

    await referrals_service.bind_referrer(session, invitee, first.ref_code)
    with pytest.raises(ReferralError):
        await referrals_service.bind_referrer(session, invitee, second.ref_code)

    assert invitee.referrer_id == first.id


async def test_cannot_invite_yourself(session: AsyncSession, make_user) -> None:
    user = await make_user(1)
    with pytest.raises(ReferralError):
        await referrals_service.bind_referrer(session, user, user.ref_code)


async def test_cannot_close_the_loop(session: AsyncSession, make_user) -> None:
    inviter = await make_user(1)
    invitee = await make_user(2)
    await referrals_service.bind_referrer(session, invitee, inviter.ref_code)

    with pytest.raises(ReferralError):
        await referrals_service.bind_referrer(session, inviter, invitee.ref_code)


async def test_unknown_code(session: AsyncSession, make_user) -> None:
    user = await make_user(1)
    with pytest.raises(ReferralError):
        await referrals_service.bind_referrer(session, user, "ZZZZ9999")


async def test_manual_code_is_blocked_after_the_first_deal(
    session: AsyncSession, make_user
) -> None:
    inviter = await make_user(1)
    invitee = await make_user(2)
    invitee.deals_count = 1

    assert referrals_service.can_bind_manually(invitee) is False
    with pytest.raises(ReferralError):
        await referrals_service.bind_referrer(
            session, invitee, inviter.ref_code, source=ReferralSource.MANUAL
        )


async def test_discount_percent_follows_the_limit(
    session: AsyncSession, make_user, settings: Settings
) -> None:
    inviter = await make_user(1)
    invitee = await make_user(2)

    # Not a referral yet -> no discount
    assert referrals_service.discount_percent_for(invitee, settings) == Decimal("0")

    await referrals_service.bind_referrer(session, invitee, inviter.ref_code)
    assert referrals_service.discount_percent_for(invitee, settings) == Decimal("0.5")

    invitee.discounts_used = settings.referral_discount_limit
    assert referrals_service.discount_percent_for(invitee, settings) == Decimal("0")


async def test_summary_reports_the_inviter_earnings(
    session: AsyncSession, make_user, settings: Settings
) -> None:
    inviter = await make_user(1)
    invitee = await make_user(2)
    await referrals_service.bind_referrer(session, invitee, inviter.ref_code)
    inviter.total_earned = Decimal("12.5")
    await session.commit()

    summary = await referrals_service.get_summary(session, invitee, settings)

    assert summary.is_referral is True
    assert summary.referrer is not None
    assert summary.referrer.id == inviter.id
    assert summary.referrer_total_earned == Decimal("12.5")
    assert summary.discounts_left == 3

    inviter_summary = await referrals_service.get_summary(session, inviter, settings)
    assert inviter_summary.referrals_count == 1
    assert inviter_summary.is_referral is False
