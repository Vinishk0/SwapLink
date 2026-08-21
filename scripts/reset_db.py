"""Wipe the database — a development convenience, not a production tool.

    python scripts/reset_db.py              # drop everything, recreate, ask first
    python scripts/reset_db.py --yes        # …without the confirmation
    python scripts/reset_db.py --yes --seed # …and recreate the demo currencies
    python scripts/reset_db.py --orders     # keep people and rates, clear deals

`--orders` is the one you usually want while testing the referral logic: orders,
transactions and every balance/counter go back to zero, but users, their referral
links, currencies and directions survive.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.base import Base  # noqa: E402
from app.db.engine import normalize_database_url  # noqa: E402
from app.db.models import Currency, Order, Pair, Transaction, User  # noqa: E402

ZERO = Decimal("0")


def database_url() -> str:
    """Same URL the bot uses, without requiring a BOT_TOKEN to be present."""
    import os

    url = os.getenv("DATABASE_URL")
    if not url:
        try:
            from app.config import get_settings

            url = get_settings().database_url
        except Exception:  # settings need BOT_TOKEN, this script does not
            url = "sqlite+aiosqlite:///data/swaplink.db"
    return normalize_database_url(url)


async def _stamp_alembic_head(connection: AsyncConnection) -> None:
    """Mark a freshly created schema as being at the latest migration."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parent.parent
        head = ScriptDirectory.from_config(Config(str(root / "alembic.ini"))).get_current_head()
    except Exception as exc:  # pragma: no cover - alembic is optional here
        print(f"  ! Could not read the alembic head: {exc}")
        return
    if not head:
        return

    await connection.execute(
        text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
    )
    await connection.execute(text("DELETE FROM alembic_version"))
    await connection.execute(
        text("INSERT INTO alembic_version (version_num) VALUES (:head)"), {"head": head}
    )
    print(f"  · alembic stamped at {head}")


async def show_counts(engine: AsyncEngine, title: str) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with async_sessionmaker(engine)() as session:
        rows = []
        for model in (User, Order, Transaction, Currency, Pair):
            try:
                count = await session.scalar(select(func.count()).select_from(model))
            except Exception:
                count = "—"
            rows.append(f"{model.__tablename__}={count}")
    print(f"{title}: " + ", ".join(rows))


async def full_reset(engine: AsyncEngine, *, seed: bool) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
        await _stamp_alembic_head(connection)
    print("  · schema dropped and recreated")

    if not seed:
        return

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.config import Settings
    from app.db.seed import seed_demo_data

    settings = Settings(_env_file=None, bot_token="0" * 12)
    async with async_sessionmaker(engine)() as session:
        await seed_demo_data(session, settings)
    print("  · demo currencies and directions created")


async def wipe_orders(engine: AsyncEngine) -> None:
    """Clear deals, ledger and every derived counter; keep people and rates."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with async_sessionmaker(engine)() as session:
        await session.execute(delete(Transaction))
        await session.execute(delete(Order))
        await session.execute(
            update(User).values(
                balance=ZERO,
                total_earned=ZERO,
                total_paid_out=ZERO,
                deals_count=0,
                discounts_used=0,
            )
        )
        await session.commit()
    print("  · orders, transactions and balances cleared")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Reset the SwapLink database")
    parser.add_argument("--yes", "-y", action="store_true", help="skip the confirmation")
    parser.add_argument(
        "--orders",
        action="store_true",
        help="only clear orders/transactions/balances, keep users and rates",
    )
    parser.add_argument("--seed", action="store_true", help="recreate demo currencies and pairs")
    args = parser.parse_args()

    url = database_url()
    engine = create_async_engine(url)
    safe_url = engine.url.render_as_string(hide_password=True)
    scope = "orders, transactions and balances" if args.orders else "ALL DATA"

    print(f"Database: {safe_url}")
    await show_counts(engine, "Before")

    if not args.yes:
        answer = input(f"\nThis will erase {scope}. Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            print("Cancelled.")
            await engine.dispose()
            return 1

    if args.orders:
        await wipe_orders(engine)
    else:
        await full_reset(engine, seed=args.seed)

    await show_counts(engine, "After")
    await engine.dispose()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
