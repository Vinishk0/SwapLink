"""Fallbacks: decorative buttons, stray messages and unhandled errors."""

from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.bot import texts, ui
from app.bot.keyboards import user as kb
from app.bot.keyboards.callbacks import NoopCB
from app.config import Settings
from app.db.models import User
from app.services.exceptions import ServiceError

logger = logging.getLogger(__name__)
router = Router(name="common")


@router.callback_query(NoopCB.filter())
async def cb_noop(query: CallbackQuery) -> None:
    """Page counters and other non-interactive buttons."""
    await query.answer()


@router.message()
async def stray_message(
    message: Message,
    state: FSMContext,
    user: User,
    settings: Settings,
    is_admin: bool = False,
) -> None:
    """Anything the bot did not ask for: swallow it and redraw the menu."""
    await ui.reset_flow(state)
    await ui.show(message, state, texts.greeting(user, settings), kb.main_menu(is_admin))


@router.errors()
async def on_error(event: ErrorEvent, bot: Bot | None = None, state: FSMContext | None = None):
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
            # An alert keeps the screen intact — no extra message in the chat.
            await update.callback_query.answer(text[:200], show_alert=True)
        elif update.message is not None:
            await ui.consume(update.message)
            if bot is not None and state is not None:
                await ui.render(
                    bot, update.message.chat.id, state, text, kb.back_to_menu("🏠 Меню")
                )
    except Exception:
        logger.debug("Could not deliver the error message", exc_info=True)
    return True
