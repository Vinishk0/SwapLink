"""Referral programme: deep links, binding rules and per-user summaries.

Rules implemented here (see README for the product description):

* opening the bot through `?start=ref_CODE` binds the invitee permanently;
* a user who came on their own may enter a code manually, but only until their
  first confirmed deal;
* the invitee gets a discount on a limited number of deals;
* the inviter keeps earning a share of every deal of their referrals forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote_plus, urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Order, OrderStatus, ReferralSource, Transaction, TransactionType, User
from app.services import users as users_service
from app.services.exceptions import ReferralError

PAYLOAD_PREFIX = "ref_"
ZERO = Decimal("0")


def build_payload(code: str) -> str:
    return f"{PAYLOAD_PREFIX}{code}"


def build_ref_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start={build_payload(code)}"


def build_share_url(link: str, text: str) -> str:
    """`t.me/share` URL for the "share with a friend" button."""
    return f"https://t.me/share/url?url={quote_plus(link)}&text={quote_plus(text)}"


def extract_code(raw: str) -> str | None:
    """Pull a referral code out of anything the user may paste.

    Accepts `ABCD2345`, `ref_ABCD2345`, `https://t.me/bot?start=ref_ABCD2345`
    and `@bot?start=ABCD2345`.
    """
    value = raw.strip()
    if not value:
        return None

    if "t.me" in value or value.startswith("http"):
        parsed = urlparse(value if "//" in value else f"https://{value}")
        query = parsed.query
        if "start=" in query:
            value = query.split("start=", 1)[1].split("&", 1)[0]
        else:
            return None

    if value.lower().startswith(PAYLOAD_PREFIX):
        value = value[len(PAYLOAD_PREFIX) :]

    value = value.strip().upper()
    return value if value.isalnum() else None


def can_bind_manually(user: User) -> bool:
    """A code may be entered by hand only before the first confirmed deal."""
    return user.referrer_id is None and user.deals_count == 0


async def bind_referrer(
    session: AsyncSession,
    user: User,
    code: str,
    *,
    source: ReferralSource = ReferralSource.LINK,
) -> User:
    """Attach `user` to the owner of `code`. Raises `ReferralError` when refused."""
    if user.referrer_id is not None:
        raise ReferralError("У вас уже есть реферер — сменить его нельзя.")
    if source is not ReferralSource.ADMIN and user.deals_count > 0:
        raise ReferralError("Реферальный код можно ввести только до первой проведённой сделки.")

    normalized = extract_code(code)
    if not normalized:
        raise ReferralError("Не похоже на реферальную ссылку или код.")

    referrer = await users_service.get_by_ref_code(session, normalized)
    if referrer is None:
        raise ReferralError("Такой реферальный код не найден.")
    if referrer.id == user.id:
        raise ReferralError("Нельзя использовать собственную реферальную ссылку.")
    if referrer.is_blocked:
        raise ReferralError("Этот реферальный код больше не действует.")
    if referrer.referrer_id == user.id:
        raise ReferralError("Нельзя стать рефералом собственного реферала.")

    user.referrer_id = referrer.id
    user.referral_source = source
    user.referred_at = datetime.now(UTC)
    await session.commit()
    return referrer


@dataclass(slots=True)
class ReferralSummary:
    """Everything the profile screen needs about the referral programme."""

    referrals_count: int
    active_referrals_count: int
    total_earned: Decimal
    balance: Decimal
    paid_out: Decimal
    discounts_used: int
    discounts_left: int
    discount_limit: int
    referrer: User | None
    referrer_total_earned: Decimal
    last_bonus_at: datetime | None

    @property
    def is_referral(self) -> bool:
        return self.referrer is not None

    @property
    def has_discount(self) -> bool:
        return self.is_referral and self.discounts_left > 0


async def get_summary(session: AsyncSession, user: User, settings: Settings) -> ReferralSummary:
    referrals_count = await users_service.count_referrals(session, user.id)

    active_stmt = (
        select(func.count(func.distinct(Order.user_id)))
        .join(User, User.id == Order.user_id)
        .where(User.referrer_id == user.id, Order.status == OrderStatus.CONFIRMED)
    )
    active_count = await session.scalar(active_stmt) or 0

    last_bonus_at = await session.scalar(
        select(func.max(Transaction.created_at)).where(
            Transaction.user_id == user.id,
            Transaction.type == TransactionType.REFERRAL_BONUS,
        )
    )

    # Loaded explicitly: the relationship may be stale right after binding.
    referrer = await session.get(User, user.referrer_id) if user.referrer_id else None
    return ReferralSummary(
        referrals_count=referrals_count,
        active_referrals_count=active_count,
        total_earned=user.total_earned,
        balance=user.balance,
        paid_out=user.total_paid_out,
        discounts_used=user.discounts_used,
        discounts_left=user.discounts_left(settings.referral_discount_limit),
        discount_limit=settings.referral_discount_limit,
        referrer=referrer,
        referrer_total_earned=referrer.total_earned if referrer else ZERO,
        last_bonus_at=last_bonus_at,
    )


def discount_percent_for(user: User, settings: Settings) -> Decimal:
    """Discount the user is entitled to on their next deal (0 when exhausted)."""
    if user.referrer_id is None:
        return ZERO
    if user.discounts_used >= settings.referral_discount_limit:
        return ZERO
    return settings.referral_discount_percent
