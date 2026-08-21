"""Currencies and exchange directions (pairs) maintained by administrators."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Currency(TimestampMixin, Base):
    """A currency the exchange office works with.

    `rate_to_base` is the value of one unit in the accounting currency
    (``settings.base_currency``). It powers both the derived cross-rates and the
    conversion of deal volume for referral bonuses.
    """

    __tablename__ = "currencies"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    rate_to_base: Mapped[Decimal] = mapped_column(nullable=False)
    decimals: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")

    def __str__(self) -> str:
        return self.code


class Pair(TimestampMixin, Base):
    """An exchange direction `from -> to`.

    The rate an administrator enters is the final client rate — the margin of the
    exchange office is already baked into it, so the bot never adds a commission
    of its own. `rate` is optional: when empty the cross-rate is derived from the
    currencies' `rate_to_base`.
    """

    __tablename__ = "pairs"
    __table_args__ = (UniqueConstraint("from_currency_id", "to_currency_id", name="uq_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    from_currency_id: Mapped[int] = mapped_column(
        ForeignKey("currencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    to_currency_id: Mapped[int] = mapped_column(
        ForeignKey("currencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rate: Mapped[Decimal | None] = mapped_column(default=None)
    min_amount: Mapped[Decimal | None] = mapped_column(default=None)
    max_amount: Mapped[Decimal | None] = mapped_column(default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")

    from_currency: Mapped[Currency] = relationship(foreign_keys=[from_currency_id], lazy="selectin")
    to_currency: Mapped[Currency] = relationship(foreign_keys=[to_currency_id], lazy="selectin")

    @property
    def title(self) -> str:
        return f"{self.from_currency.code} → {self.to_currency.code}"

    @property
    def effective_rate(self) -> Decimal:
        """Manual rate when set, otherwise the cross-rate through the base currency."""
        if self.rate is not None:
            return self.rate
        return self.from_currency.rate_to_base / self.to_currency.rate_to_base

    @property
    def is_manual_rate(self) -> bool:
        return self.rate is not None
