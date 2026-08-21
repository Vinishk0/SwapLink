"""Async engine and session factory."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import BASE_DIR, get_settings

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None


def normalize_database_url(url: str) -> str:
    """Anchor a file-based SQLite path to the project root and create its folder.

    `sqlite+aiosqlite:///data/swaplink.db` is relative to the *current working
    directory*, so the bot would look for a different file depending on where it
    was started from (PyCharm, a service, a shell in another folder). Resolving
    it against the project root makes the run location irrelevant.
    """
    scheme, separator, raw_path = url.partition(":///")
    if not separator or not scheme.startswith("sqlite"):
        return url
    if not raw_path or raw_path.startswith(":memory:"):
        return url

    path = Path(raw_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"{scheme}:///{path.resolve().as_posix()}"


def get_engine() -> AsyncEngine:
    """Create (once) and return the process-wide async engine."""
    global _engine
    if _engine is not None:
        return _engine

    settings = get_settings()
    database_url = normalize_database_url(settings.database_url)

    kwargs: dict[str, object] = {"echo": settings.db_echo, "pool_pre_ping": True}
    if not settings.is_sqlite:
        kwargs |= {"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800}

    _engine = create_async_engine(database_url, **kwargs)  # type: ignore[arg-type]

    if settings.is_sqlite:
        enable_sqlite_pragmas(_engine)

    logger.info("Database engine created (%s)", _engine.url.render_as_string(hide_password=True))
    return _engine


def enable_sqlite_pragmas(engine: AsyncEngine) -> None:
    """WAL + foreign keys — SQLite is not safe for concurrent writes without them."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def create_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine or get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
