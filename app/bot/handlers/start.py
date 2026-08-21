"""`/start` (including referral deep links), main menu and help."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts, ui
from app.bot.keyboards import user as kb
from app.bot.keyboards.callbacks import MenuCB
from app.config import Settings
from app.db.models import ReferralSource, User
from app.services import referrals as referrals_service
from app.services.exceptions import ReferralError

logger = logging.getLogger(__name__)
router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    settings: Settings,
    state: FSMContext,
    bot: Bot,
    dispatcher: Dispatcher,
    is_admin: bool = False,
) -> None:
    """Greet the user and bind them to an inviter when they came by a link."""
    await ui.reset_flow(state)

    bound_to: User | None = None
    payload = (command.args or "").strip()
    if payload and referrals_service.can_bind_manually(user):
        try:
            bound_to = await referrals_service.bind_referrer(
                session, user, payload, source=ReferralSource.LINK
            )
        except ReferralError as exc:
            logger.info("Deep link binding refused for %s: %s", user.tg_id, exc)

    # Older versions used a reply keyboard; drop it so the chat stays clean.
    await ui.strip_reply_keyboard(bot, message.chat.id)
    await ui.show(
        message, state, texts.greeting(user, settings, bound_to=bound_to), kb.main_menu(is_admin)
    )

    if bound_to is not None:
        await notifications.push(
            bot, dispatcher, bound_to.tg_id, texts.notify_new_referral(user, settings)
        )


@router.message(Command("menu"))
async def cmd_menu(
    message: Message,
    user: User,
    settings: Settings,
    state: FSMContext,
    is_admin: bool = False,
) -> None:
    await ui.reset_flow(state)
    await ui.show(message, state, texts.greeting(user, settings), kb.main_menu(is_admin))


@router.callback_query(MenuCB.filter(F.action == "main"))
async def cb_main_menu(
    query: CallbackQuery,
    user: User,
    settings: Settings,
    state: FSMContext,
    is_admin: bool = False,
) -> None:
    await ui.reset_flow(state)
    await ui.show(query, state, texts.greeting(user, settings), kb.main_menu(is_admin))
    await query.answer()


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings, state: FSMContext) -> None:
    await ui.reset_flow(state)
    await ui.show(message, state, texts.help_text(settings), kb.back_to_menu())


@router.callback_query(MenuCB.filter(F.action == "help"))
async def cb_help(query: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    await ui.reset_flow(state)
    await ui.show(query, state, texts.help_text(settings), kb.back_to_menu())
    await query.answer()
