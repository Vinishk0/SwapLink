"""Schema bootstrap and optional demo data."""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.config import Settings
from app.db.base import Base
from app.db.models import Currency, Pair

logger = logging.getLogger(__name__)

#: code -> (name, rate to the base currency, decimals)
DEMO_CURRENCIES: dict[str, tuple[str, str, int]] = {
    "USDT": ("Tether USD", "1", 2),
    "USD": ("Доллар США", "1", 2),
    "EUR": ("Евро", "1.08", 2),
    "RUB": ("Российский рубль", "0.0107", 2),
}
DEMO_PAIRS = [("USDT", "RUB"), ("RUB", "USDT"), ("USD", "RUB"), ("RUB", "USD")]


async def create_schema(engine: AsyncEngine) -> None:
    """Create missing tables.

    Alembic owns migrations in production (`alembic upgrade head` runs before the
    bot in Docker); this keeps a fresh local database usable with zero setup.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def seed_demo_data(session: AsyncSession, settings: Settings) -> None:
    """Populate a few currencies and directions so the bot is usable at once."""
    existing = await session.scalar(select(Currency.id).limit(1))
    if existing:
        return

    currencies: dict[str, Currency] = {}
    for index, (code, (name, rate, decimals)) in enumerate(DEMO_CURRENCIES.items()):
        currency = Currency(
            code=code,
            name=name,
            rate_to_base=Decimal(rate),
            decimals=decimals,
            sort_order=index,
        )
        session.add(currency)
        currencies[code] = currency
    await session.flush()

    for from_code, to_code in DEMO_PAIRS:
        session.add(
            Pair(
                from_currency_id=currencies[from_code].id,
                to_currency_id=currencies[to_code].id,
            )
        )
    await session.commit()
    logger.info("Demo currencies and pairs created")
