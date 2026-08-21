"""Outgoing notifications that must never break the calling handler."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from app.config import Settings

logger = logging.getLogger(__name__)


async def notify_user(
    bot: Bot,
    tg_id: int,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Send a message to a user; `False` when they blocked the bot."""
    try:
        await bot.send_message(tg_id, text, reply_markup=reply_markup)
    except TelegramRetryAfter as exc:  # pragma: no cover - depends on Telegram
        await asyncio.sleep(exc.retry_after)
        return await notify_user(bot, tg_id, text, reply_markup=reply_markup)
    except TelegramForbiddenError:
        logger.info("User %s has blocked the bot", tg_id)
        return False
    except Exception:
        logger.exception("Failed to notify user %s", tg_id)
        return False
    return True


async def notify_admins(
    bot: Bot,
    settings: Settings,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    for admin_id in settings.admin_ids:
        await notify_user(bot, admin_id, text, reply_markup=reply_markup)
