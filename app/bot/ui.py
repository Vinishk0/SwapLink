"""Single-screen UI.

The bot keeps exactly **one** message per chat — "the screen". Every button
press edits it in place, and every message the user types is deleted right after
it is processed, so the chat never grows.

The only bot messages that live outside the screen are the "new order" cards
sent to administrators: they must survive until the order is confirmed or
rejected (see `app.bot.notifications`).
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

logger = logging.getLogger(__name__)

#: FSM data key holding the id of the bot's single message in this chat.
SCREEN_KEY = "_screen"
#: FSM data keys that must survive `reset_flow()`.
PERSISTENT_KEYS = (SCREEN_KEY,)


async def get_screen_id(state: FSMContext) -> int | None:
    return (await state.get_data()).get(SCREEN_KEY)


async def remember_screen(state: FSMContext, message_id: int) -> None:
    await state.update_data(**{SCREEN_KEY: message_id})


async def reset_flow(state: FSMContext) -> None:
    """Drop the FSM state and scratch data, but keep pointing at the screen."""
    data = await state.get_data()
    keep = {key: data[key] for key in PERSISTENT_KEYS if key in data}
    await state.clear()
    if keep:
        await state.update_data(**keep)


async def render(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    notify: bool = False,
) -> int | None:
    """Draw `text` on the screen.

    `notify=True` replaces the screen with a brand-new message instead of
    editing it — an edit produces no notification, and things like "your deal is
    done" must actually reach the user.
    """
    screen = await get_screen_id(state)

    if notify and screen is not None:
        await delete_message(bot, chat_id, screen)
        screen = None

    if screen is not None:
        try:
            await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=screen, reply_markup=reply_markup
            )
            return screen
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return screen
            # Deleted by the user, too old to edit, or not a text message.
            logger.debug("Screen %s in chat %s is gone: %s", screen, chat_id, exc)

    try:
        message = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except TelegramBadRequest:
        logger.exception("Could not render the screen for chat %s", chat_id)
        return None

    await remember_screen(state, message.message_id)
    return message.message_id


async def show(
    event: Message | CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    notify: bool = False,
    adopt: bool = True,
) -> int | None:
    """Render the screen for whatever kind of update triggered the handler.

    For a message: the user's own message is deleted first.
    For a callback: `adopt` lets an orphaned screen latch onto the message the
    button belongs to — which must be off for the admin order cards, they are
    not the screen and must never be edited by navigation.
    """
    if isinstance(event, Message):
        await consume(event)
        return await render(event.bot, event.chat.id, state, text, reply_markup, notify=notify)

    message = event.message
    if message is None:  # inline mode / too old — nothing to draw on
        return None
    if adopt and await get_screen_id(state) is None:
        await remember_screen(state, message.message_id)
    return await render(event.bot, message.chat.id, state, text, reply_markup, notify=notify)


async def consume(message: Message) -> None:
    """Delete a message the user sent — bots may do this in private chats."""
    try:
        await message.delete()
    except TelegramBadRequest as exc:
        logger.debug("Could not delete the user message: %s", exc)


async def delete_message(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest as exc:
        logger.debug("Could not delete message %s in chat %s: %s", message_id, chat_id, exc)


async def close_screen(bot: Bot, chat_id: int, state: FSMContext) -> None:
    """Remove the screen entirely (used when only an order card should remain)."""
    screen = await get_screen_id(state)
    if screen is not None:
        await delete_message(bot, chat_id, screen)
    data = await state.get_data()
    data.pop(SCREEN_KEY, None)
    await state.set_data(data)


async def strip_reply_keyboard(bot: Bot, chat_id: int) -> None:
    """Remove a leftover reply keyboard from older versions of the bot."""
    try:
        message = await bot.send_message(chat_id, "…", reply_markup=ReplyKeyboardRemove())
    except TelegramBadRequest:
        return
    await delete_message(bot, chat_id, message.message_id)
