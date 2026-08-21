"""Exchange requests.

The bot never moves money — an order is a calculation the user asked to be
executed, which an administrator then confirms or rejects. Confirmation is the
only event that consumes a referral discount and credits the inviter.
"""

from __future__ import annotations

import enum
import json
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
    to_decimals: Mapped[int] = mapped_column(default=2, server_default="2")

    amount_from: Mapped[Decimal] = mapped_column(nullable=False)
    #: What the client receives, discount included.
    amount_to: Mapped[Decimal] = mapped_column(nullable=False)
    rate: Mapped[Decimal] = mapped_column(nullable=False)

    #: Referral discount: a bonus on top of the rate, in percent.
    discount_percent: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")
    #: What the discount added, in the target currency.
    discount_amount: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")
    discount_applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    #: Referral balance an admin wrote off into this deal (accounting currency)…
    bonus_spent: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")
    #: …and the same amount converted to the target currency at write-off time.
    bonus_spent_to: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")

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

    #: Where the "new order" cards live: JSON list of [chat_id, message_id].
    #: They are the one kind of bot message that outlives the single-screen UI —
    #: until the order is confirmed or rejected and they get deleted.
    admin_messages: Mapped[str | None] = mapped_column(Text)

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

    @property
    def total_to(self) -> Decimal:
        """Everything the client walks away with, bonuses included."""
        return self.amount_to + self.bonus_spent_to

    @property
    def has_bonus_spent(self) -> bool:
        return self.bonus_spent > ZERO

    # --- admin notification bookkeeping ---

    @property
    def admin_message_refs(self) -> list[tuple[int, int]]:
        if not self.admin_messages:
            return []
        try:
            return [(int(chat), int(message)) for chat, message in json.loads(self.admin_messages)]
        except (ValueError, TypeError):  # pragma: no cover - corrupted payload
            return []

    @admin_message_refs.setter
    def admin_message_refs(self, refs: list[tuple[int, int]]) -> None:
        self.admin_messages = json.dumps([[chat, message] for chat, message in refs])

    def add_admin_message(self, chat_id: int, message_id: int) -> None:
        self.admin_message_refs = [*self.admin_message_refs, (chat_id, message_id)]
