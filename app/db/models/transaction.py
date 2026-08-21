"""Ledger of every balance movement.

`User.balance` is a denormalised running total; this table is the audit trail
that explains how it got there.
"""

from __future__ import annotations

import enum
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.order import Order
    from app.db.models.user import User

ZERO = Decimal("0")


class TransactionType(enum.StrEnum):
    REFERRAL_BONUS = "referral_bonus"  # + credited from a referral's deal
    PAYOUT = "payout"  # - paid out in cash by an admin
    DISCOUNT = "discount"  # - spent as a discount on the user's own deal
    ADJUSTMENT = "adjustment"  # +/- manual correction by an admin

    @property
    def title(self) -> str:
        return {
            TransactionType.REFERRAL_BONUS: "Реферальное начисление",
            TransactionType.PAYOUT: "Выплата",
            TransactionType.DISCOUNT: "Списание в скидку",
            TransactionType.ADJUSTMENT: "Корректировка",
        }[self]


class Transaction(TimestampMixin, Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, native_enum=False, length=24, validate_strings=True),
        nullable=False,
        index=True,
    )
    #: Signed amount in the accounting currency: positive credits, negative debits.
    amount: Mapped[Decimal] = mapped_column(nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(default=ZERO, server_default="0")

    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    #: Whose deal generated the bonus (for REFERRAL_BONUS rows).
    source_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    admin_id: Mapped[int | None] = mapped_column(BigInteger)
    comment: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(
        foreign_keys=[user_id], back_populates="transactions", lazy="selectin"
    )
    source_user: Mapped[User | None] = relationship(foreign_keys=[source_user_id], lazy="selectin")
    order: Mapped[Order | None] = relationship(lazy="noload")
