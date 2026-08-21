"""Registers the Telegram user in the database and blocks banned accounts."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.bot import texts
from app.config import Settings
from app.services import users as users_service

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """Puts `user` (our ORM model) and `is_admin` into the handler context."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        session = data["session"]
        user, created = await users_service.get_or_create(
            session,
            tg_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
        )
        if created:
            logger.info("New user registered: %s (%s)", tg_user.id, tg_user.username)

        data["user"] = user
        data["is_admin"] = self.settings.is_admin(tg_user.id)

        if user.is_blocked and not data["is_admin"]:
            await self._reject(event)
            return None

        return await handler(event, data)

    @staticmethod
    async def _reject(event: TelegramObject) -> None:
        if not isinstance(event, Update):
            return
        if event.message is not None:
            await event.message.answer(texts.blocked_notice())
        elif event.callback_query is not None:
            await event.callback_query.answer(texts.blocked_notice(), show_alert=True)
