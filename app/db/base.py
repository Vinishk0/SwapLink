"""Declarative base, shared mixins and the money column type."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import DateTime, Numeric, String, TypeDecorator, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONEY_PRECISION = 28
MONEY_SCALE = 8


class Money(TypeDecorator):  # type: ignore[type-arg]
    """`Decimal` money column that keeps full precision on SQLite too.

    Postgres stores it as NUMERIC; SQLite has no decimal type and would silently
    round-trip through float, so there the value is kept as a plain string.
    """

    impl = Numeric(MONEY_PRECISION, MONEY_SCALE)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(48))
        return dialect.type_descriptor(Numeric(MONEY_PRECISION, MONEY_SCALE, asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        decimal_value = Decimal(str(value))
        if dialect.name == "sqlite":
            # Zero-padded fixed point keeps lexicographic order == numeric order.
            return format(decimal_value.quantize(Decimal(1).scaleb(-MONEY_SCALE)), "020.8f")
        return decimal_value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(str(value))


class Base(DeclarativeBase):
    """Base class for every ORM model."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {Decimal: Money}

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


class TimestampMixin:
    """`created_at` / `updated_at` maintained by the database."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
