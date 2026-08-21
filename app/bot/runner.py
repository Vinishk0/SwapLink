"""Composition root: wire settings, database, middlewares and routers together."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import get_root_router
from app.bot.middlewares import DbSessionMiddleware, ThrottlingMiddleware, UserMiddleware
from app.config import BASE_DIR, Settings, get_settings
from app.db.engine import create_session_factory, dispose_engine, get_engine
from app.db.seed import create_schema, seed_demo_data
from app.logging_config import setup_logging

logger = logging.getLogger(__name__)


class StartupError(RuntimeError):
    """Misconfiguration that must be shown to a human, not as a traceback."""


USER_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="exchange", description="Рассчитать обмен"),
    BotCommand(command="profile", description="Личный кабинет"),
    BotCommand(command="ref", description="Реферальная программа"),
    BotCommand(command="orders", description="Мои заявки"),
    BotCommand(command="help", description="Как это работает"),
]
ADMIN_COMMANDS = [*USER_COMMANDS, BotCommand(command="admin", description="Админ-панель")]


def build_storage(settings: Settings) -> BaseStorage:
    """Redis keeps dialog state across restarts; memory is fine for one instance."""
    if not settings.redis_url:
        return MemoryStorage()
    from aiogram.fsm.storage.redis import RedisStorage

    logger.info("Using Redis FSM storage")
    return RedisStorage.from_url(settings.redis_url)


def build_dispatcher(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> Dispatcher:
    dispatcher = Dispatcher(storage=build_storage(settings))
    dispatcher["settings"] = settings

    # Order matters: drop flood before touching the database, register the user
    # only for updates that survived.
    dispatcher.update.outer_middleware(ThrottlingMiddleware())
    dispatcher.update.outer_middleware(DbSessionMiddleware(session_factory))
    dispatcher.update.outer_middleware(UserMiddleware(settings))

    dispatcher.include_router(get_root_router())
    return dispatcher


async def set_commands(bot: Bot, settings: Settings) -> None:
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            # Usually just means the admin has not opened a chat with the bot yet.
            logger.info(
                "Admin commands for %s will be set after they send /start to the bot",
                admin_id,
            )


async def start_bot(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    engine = get_engine()
    try:
        await create_schema(engine)
    except (OperationalError, OSError) as exc:
        # asyncpg surfaces a bare ConnectionRefusedError, psycopg an OperationalError.
        reason = getattr(exc, "orig", None) or exc
        raise StartupError(
            "Не удалось подключиться к базе данных.\n"
            f"  DATABASE_URL: {engine.url.render_as_string(hide_password=True)}\n"
            f"  Причина: {reason}\n\n"
            "Запустите Postgres командой `docker compose up -d db` "
            "или укажите в .env локальную базу:\n"
            "  DATABASE_URL=sqlite+aiosqlite:///data/swaplink.db"
        ) from exc

    session_factory = create_session_factory(engine)
    if settings.seed_demo_data:
        async with session_factory() as session:
            await seed_demo_data(session, settings)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dispatcher = build_dispatcher(settings, session_factory)

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError as exc:
        await bot.session.close()
        await dispose_engine()
        raise StartupError(
            "Telegram отклонил BOT_TOKEN — проверьте значение в .env "
            "(получить новый можно у @BotFather)."
        ) from exc
    except TelegramNetworkError as exc:
        await bot.session.close()
        await dispose_engine()
        raise StartupError(
            f"Нет связи с Telegram API: {exc}\nПроверьте интернет-соединение или прокси."
        ) from exc

    dispatcher["bot_username"] = me.username
    await set_commands(bot, settings)

    logger.info(
        "Starting @%s | admins: %s | base currency: %s",
        me.username,
        settings.admin_ids or "none configured!",
        settings.base_currency,
    )
    if not settings.admin_ids:
        logger.warning("ADMIN_IDS is empty — the admin panel is unreachable")

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=settings.drop_pending_updates,
        )
    finally:
        await dispatcher.storage.close()
        await bot.session.close()
        await dispose_engine()
        logger.info("Bot stopped")


def _load_settings() -> Settings:
    """Read the configuration, explaining what exactly is missing in `.env`."""
    try:
        return get_settings()
    except ValidationError as exc:
        problems = "\n".join(
            f"  • {'.'.join(str(part) for part in error['loc']).upper() or 'CONFIG'}: "
            f"{error['msg']}"
            for error in exc.errors()
        )
        env_file = BASE_DIR / ".env"
        hint = (
            f"Файл {env_file} не найден — скопируйте .env.example в .env и заполните его."
            if not env_file.exists()
            else f"Проверьте значения в {env_file}."
        )
        raise StartupError(f"Конфигурация неполна или некорректна:\n{problems}\n{hint}") from exc


def run() -> None:
    """Console entry point (`swaplink` / `python -m app`)."""
    try:
        settings = _load_settings()
        asyncio.run(start_bot(settings))
    except StartupError as exc:
        print(f"\n[SwapLink] Запуск невозможен.\n{exc}\n", file=sys.stderr)
        raise SystemExit(1) from exc
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Interrupted by user")
