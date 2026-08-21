"""Bot users and the referral link between them."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.order import Order
    from app.db.models.transaction import Transaction

ZERO = Decimal("0")


class ReferralSource(enum.StrEnum):
    """How the referral link between two users was established."""

    LINK = "link"  # opened the bot through a `?start=ref_XXX` deep link
    MANUAL = "manual"  # typed the code/link manually before the first deal
    ADMIN = "admin"  # attached by an administrator


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))

    # --- referral programme ---
    ref_code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    referrer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    referral_source: Mapped[ReferralSource | None] = mapped_column(
        Enum(ReferralSource, native_enum=False, length=16, validate_strings=True)
    )
    referred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Number of confirmed deals that consumed the referral discount.
    discounts_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Number of confirmed deals in total — also locks manual code entry.
    deals_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # --- money, in the base (accounting) currency ---
    balance: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")
    total_earned: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")
    total_paid_out: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")

    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    referrer: Mapped[User | None] = relationship(
        remote_side="User.id", back_populates="referrals", lazy="selectin"
    )
    referrals: Mapped[list[User]] = relationship(back_populates="referrer", lazy="noload")
    orders: Mapped[list[Order]] = relationship(
        back_populates="user", foreign_keys="Order.user_id", lazy="noload"
    )
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="user", foreign_keys="Transaction.user_id", lazy="noload"
    )

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.last_name]
        name = " ".join(part for part in parts if part)
        return name or (f"@{self.username}" if self.username else f"ID {self.tg_id}")

    @property
    def mention(self) -> str:
        """HTML mention, safe to put into a message."""
        from html import escape

        return f'<a href="tg://user?id={self.tg_id}">{escape(self.full_name)}</a>'

    @property
    def is_referral(self) -> bool:
        return self.referrer_id is not None

    def discounts_left(self, limit: int) -> int:
        return max(limit - self.discounts_used, 0)
