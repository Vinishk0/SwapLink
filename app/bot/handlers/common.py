"""Fallbacks: decorative buttons, unknown messages and unhandled errors."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.bot.keyboards import user as kb
from app.bot.keyboards.callbacks import NoopCB
from app.services.exceptions import ServiceError

logger = logging.getLogger(__name__)
router = Router(name="common")


@router.callback_query(NoopCB.filter())
async def cb_noop(query: CallbackQuery) -> None:
    """Page counters and other non-interactive buttons."""
    await query.answer()


@router.message(F.text)
async def unknown_message(message: Message, is_admin: bool = False) -> None:
    await message.answer(
        "Не понял команду 🤔\nВоспользуйтесь меню ниже или отправьте /help.",
        reply_markup=kb.main_menu(is_admin),
    )


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Never let an exception silently kill an update."""
    exception = event.exception
    update = event.update

    if isinstance(exception, ServiceError):
        logger.info("Service error: %s", exception)
        text = f"⚠️ {exception}"
    else:
        logger.exception("Unhandled error while processing update %s", update.update_id)
        text = "⚠️ Что-то пошло не так. Попробуйте ещё раз или напишите оператору."

    try:
        if update.callback_query is not None:
            await update.callback_query.answer(text[:200], show_alert=True)
        elif update.message is not None:
            await update.message.answer(text)
    except Exception:
        logger.debug("Could not deliver the error message", exc_info=True)
    return True
