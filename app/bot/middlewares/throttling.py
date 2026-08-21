"""Naive per-user rate limiting — enough to survive a button-mashing user."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject, Update


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, min_interval: float = 0.35) -> None:
        self.min_interval = min_interval
        self._last_seen: dict[int, float] = defaultdict(float)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        now = time.monotonic()
        if now - self._last_seen[tg_user.id] < self.min_interval:
            await self._silence(event)
            return None

        self._last_seen[tg_user.id] = now
        return await handler(event, data)

    @staticmethod
    async def _silence(event: TelegramObject) -> None:
        """Callback queries must always be answered or the client spins."""
        query = event.callback_query if isinstance(event, Update) else None
        if isinstance(query, CallbackQuery):
            await query.answer("Слишком быстро 🙂")
