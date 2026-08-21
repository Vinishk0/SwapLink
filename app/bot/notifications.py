"""Messages the bot sends on its own initiative.

Two kinds exist:

* **pushes to a user** — they replace that user's single screen with a fresh
  message, so the chat still holds exactly one bot message but the person
  actually gets a notification (an edit would be silent);
* **order cards for admins** — the one exception to the single-screen rule.
  They stay in the admin chat, are refreshed whenever the deal changes, and are
  deleted the moment the order is confirmed or rejected.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, ui
from app.bot.keyboards import admin as admin_kb
from app.config import Settings
from app.db.models import Order
from app.services import orders as orders_service

logger = logging.getLogger(__name__)


def state_for(bot: Bot, dispatcher: Dispatcher, tg_id: int) -> FSMContext:
    """FSM context of another private chat — needed to find that user's screen."""
    key = StorageKey(bot_id=bot.id, chat_id=tg_id, user_id=tg_id)
    return FSMContext(storage=dispatcher.storage, key=key)


async def push(
    bot: Bot,
    dispatcher: Dispatcher,
    tg_id: int,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Replace the user's screen with a new message; `False` if we are blocked."""
    state = state_for(bot, dispatcher, tg_id)
    try:
        await ui.render(bot, tg_id, state, text, reply_markup, notify=True)
    except TelegramRetryAfter as exc:  # pragma: no cover - depends on Telegram
        await asyncio.sleep(exc.retry_after)
        return await push(bot, dispatcher, tg_id, text, reply_markup=reply_markup)
    except TelegramForbiddenError:
        logger.info("User %s has blocked the bot", tg_id)
        return False
    except Exception:
        logger.exception("Failed to notify user %s", tg_id)
        return False
    return True


# --------------------------------------------------------------------------- #
# Admin order cards
# --------------------------------------------------------------------------- #


async def send_order_cards(
    bot: Bot, session: AsyncSession, order: Order, settings: Settings
) -> None:
    """Deliver the new order to every admin and remember where the cards live."""
    text = texts.admin_order_card(order, settings, is_new=True)
    markup = admin_kb.order_card(order, src=admin_kb.SRC_NOTE)

    refs: list[tuple[int, int]] = []
    for admin_id in settings.admin_ids:
        try:
            message = await bot.send_message(admin_id, text, reply_markup=markup)
        except TelegramForbiddenError:
            logger.info("Admin %s has not started the bot", admin_id)
            continue
        except Exception:
            logger.exception("Could not deliver order #%s to admin %s", order.id, admin_id)
            continue
        refs.append((admin_id, message.message_id))

    if refs:
        order.admin_message_refs = refs
        await session.commit()


async def refresh_order_cards(bot: Bot, order: Order, settings: Settings) -> None:
    """Redraw the standing cards after the deal changed (amount, bonuses, …)."""
    refs = order.admin_message_refs
    if not refs:
        return
    text = texts.admin_order_card(order, settings, is_new=order.is_pending)
    markup = admin_kb.order_card(order, src=admin_kb.SRC_NOTE)
    for chat_id, message_id in refs:
        try:
            await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=message_id, reply_markup=markup
            )
        except Exception as exc:  # message deleted by hand / not modified
            logger.debug("Could not refresh order card %s/%s: %s", chat_id, message_id, exc)


async def delete_order_cards(bot: Bot, session: AsyncSession, order: Order) -> None:
    """The order is resolved — the cards have done their job."""
    for chat_id, message_id in await orders_service.clear_admin_messages(session, order):
        await ui.delete_message(bot, chat_id, message_id)
