"""Shared fixtures: an in-memory database and small object factories."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.base import Base
from app.db.models import Currency, Pair, User
from app.services import users as users_service


@pytest.fixture
def settings() -> Settings:
    """Settings isolated from the developer's local `.env`."""
    return Settings(
        _env_file=None,
        bot_token="123456:TEST-TOKEN",
        admin_ids=[1],
        database_url="sqlite+aiosqlite://",
        base_currency="USDT",
        referral_discount_percent=Decimal("0.5"),
        referral_bonus_percent=Decimal("0.5"),
        referral_discount_limit=3,
        default_commission_percent=Decimal("2"),
    )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


@pytest.fixture
async def make_user(session: AsyncSession):
    async def _make(tg_id: int, username: str | None = None) -> User:
        user, _ = await users_service.get_or_create(
            session, tg_id=tg_id, username=username, first_name=f"User{tg_id}"
        )
        return user

    return _make


@pytest.fixture
async def pair(session: AsyncSession) -> Pair:
    """USDT -> RUB at 100 RUB per USDT with a 2% commission."""
    usdt = Currency(code="USDT", name="Tether", rate_to_base=Decimal("1"), decimals=2)
    rub = Currency(code="RUB", name="Рубль", rate_to_base=Decimal("0.01"), decimals=2)
    session.add_all([usdt, rub])
    await session.flush()

    exchange_pair = Pair(
        from_currency_id=usdt.id,
        to_currency_id=rub.id,
        commission_percent=Decimal("2"),
    )
    session.add(exchange_pair)
    await session.commit()
    await session.refresh(exchange_pair)
    return exchange_pair
