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


def _prepare_sqlite_path(url: str) -> None:
    """Make sure the directory of a file-based SQLite database exists."""
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return
    raw_path = url[len(prefix) :]
    if not raw_path or raw_path == ":memory:":
        return
    path = Path(raw_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    path.parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> AsyncEngine:
    """Create (once) and return the process-wide async engine."""
    global _engine
    if _engine is not None:
        return _engine

    settings = get_settings()
    _prepare_sqlite_path(settings.database_url)

    kwargs: dict[str, object] = {"echo": settings.db_echo, "pool_pre_ping": True}
    if not settings.is_sqlite:
        kwargs |= {"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800}

    _engine = create_async_engine(settings.database_url, **kwargs)  # type: ignore[arg-type]

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
