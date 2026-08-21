"""Admin access filter."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import Settings


class IsAdmin(BaseFilter):
    """Passes only for Telegram ids listed in `ADMIN_IDS`."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    async def __call__(self, event: TelegramObject, **data) -> bool:
        settings: Settings = self.settings or data["settings"]
        user = getattr(event, "from_user", None)
        if user is None and isinstance(event, Message | CallbackQuery):  # pragma: no cover
            return False
        return user is not None and settings.is_admin(user.id)
