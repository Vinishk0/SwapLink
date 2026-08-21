"""`/start` (including referral deep links), main menu and help."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts
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
    is_admin: bool = False,
) -> None:
    """Greet the user and bind them to an inviter when they came by a link."""
    await state.clear()

    bound_to: User | None = None
    payload = (command.args or "").strip()
    if payload and referrals_service.can_bind_manually(user):
        try:
            bound_to = await referrals_service.bind_referrer(
                session, user, payload, source=ReferralSource.LINK
            )
        except ReferralError as exc:
            logger.info("Deep link binding refused for %s: %s", user.tg_id, exc)

    await message.answer(
        texts.greeting(user, settings, bound_to=bound_to),
        reply_markup=kb.main_menu(is_admin),
    )

    if bound_to is not None:
        await notifications.notify_user(
            bot, bound_to.tg_id, texts.notify_new_referral(user, settings)
        )


@router.message(Command("help"))
@router.message(F.text == kb.BTN_HELP)
async def cmd_help(message: Message, settings: Settings) -> None:
    await message.answer(texts.help_text(settings))


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, is_admin: bool = False) -> None:
    await state.clear()
    await message.answer("Главное меню 👇", reply_markup=kb.main_menu(is_admin))


@router.callback_query(MenuCB.filter(F.action == "main"))
async def cb_main_menu(query: CallbackQuery, state: FSMContext, is_admin: bool = False) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        await query.message.answer("Главное меню 👇", reply_markup=kb.main_menu(is_admin))
    await query.answer()
