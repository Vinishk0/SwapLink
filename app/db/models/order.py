"""Exchange requests.

The bot never moves money — an order is a calculation the user asked to be
executed, which an administrator then confirms or rejects. Confirmation is the
only event that consumes a referral discount and credits the inviter.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.currency import Pair
    from app.db.models.user import User

ZERO = Decimal("0")


class OrderStatus(enum.StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    @property
    def title(self) -> str:
        return {
            OrderStatus.PENDING: "⏳ Ожидает подтверждения",
            OrderStatus.CONFIRMED: "✅ Проведена",
            OrderStatus.REJECTED: "❌ Отклонена",
            OrderStatus.CANCELLED: "🚫 Отменена пользователем",
        }[self]

    @property
    def short_title(self) -> str:
        return {
            OrderStatus.PENDING: "⏳",
            OrderStatus.CONFIRMED: "✅",
            OrderStatus.REJECTED: "❌",
            OrderStatus.CANCELLED: "🚫",
        }[self]


class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pair_id: Mapped[int | None] = mapped_column(ForeignKey("pairs.id", ondelete="SET NULL"))

    # Snapshot of the direction: pairs may be renamed or removed later.
    from_code: Mapped[str] = mapped_column(String(16), nullable=False)
    to_code: Mapped[str] = mapped_column(String(16), nullable=False)

    amount_from: Mapped[Decimal] = mapped_column(nullable=False)
    amount_to: Mapped[Decimal] = mapped_column(nullable=False)
    rate: Mapped[Decimal] = mapped_column(nullable=False)

    base_commission_percent: Mapped[Decimal] = mapped_column(nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")
    commission_percent: Mapped[Decimal] = mapped_column(nullable=False)
    #: How much the user saved thanks to the discount, in the target currency.
    discount_amount: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")
    discount_applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    #: Deal volume converted to the accounting currency — the bonus base.
    volume_base: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")
    referrer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    bonus_amount: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, native_enum=False, length=16, validate_strings=True),
        default=OrderStatus.PENDING,
        server_default=OrderStatus.PENDING.value,
        index=True,
        nullable=False,
    )
    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    admin_comment: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(
        foreign_keys=[user_id], back_populates="orders", lazy="selectin"
    )
    referrer: Mapped[User | None] = relationship(foreign_keys=[referrer_id], lazy="selectin")
    pair: Mapped[Pair | None] = relationship(lazy="selectin")

    @property
    def is_pending(self) -> bool:
        return self.status is OrderStatus.PENDING

    @property
    def direction(self) -> str:
        return f"{self.from_code} → {self.to_code}"
