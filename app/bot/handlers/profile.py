"""Personal cabinet: referral link, earnings, history."""

from __future__ import annotations

import math

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.keyboards import user as kb
from app.bot.keyboards.callbacks import MenuCB, ProfileCB
from app.bot.states import ReferralSG
from app.config import Settings
from app.db.models import User
from app.services import balance as balance_service
from app.services import referrals as referrals_service
from app.services import users as users_service

router = Router(name="profile")

HISTORY_PER_PAGE = 10
SHARE_TEXT = "Обмен валют с реферальной скидкой — заходи по моей ссылке 👇"


def _links(bot_username: str, user: User) -> tuple[str, str]:
    link = referrals_service.build_ref_link(bot_username, user.ref_code)
    return link, referrals_service.build_share_url(link, SHARE_TEXT)


async def _render_profile(
    message: Message,
    session: AsyncSession,
    user: User,
    settings: Settings,
    bot_username: str,
    *,
    edit: bool = False,
) -> None:
    summary = await referrals_service.get_summary(session, user, settings)
    ref_link, share_url = _links(bot_username, user)
    text = texts.profile(user, summary, settings, ref_link)
    markup = kb.profile(ref_link=ref_link, share_url=share_url)
    if edit:
        await message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    else:
        await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.message(Command("profile"))
@router.message(F.text == kb.BTN_PROFILE)
async def open_profile(
    message: Message,
    session: AsyncSession,
    user: User,
    settings: Settings,
    state: FSMContext,
    bot_username: str,
) -> None:
    await state.clear()
    await _render_profile(message, session, user, settings, bot_username)


@router.callback_query(MenuCB.filter(F.action == "profile"))
@router.callback_query(ProfileCB.filter(F.action == "refresh"))
async def cb_profile(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    settings: Settings,
    state: FSMContext,
    bot_username: str,
) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        try:
            await _render_profile(query.message, session, user, settings, bot_username, edit=True)
        except Exception:
            await _render_profile(query.message, session, user, settings, bot_username)
    await query.answer()


@router.callback_query(ProfileCB.filter(F.action == "history"))
async def cb_history(
    query: CallbackQuery,
    callback_data: ProfileCB,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    total = await balance_service.count_transactions(session, user.id)
    pages = max(math.ceil(total / HISTORY_PER_PAGE), 1)
    page = min(max(callback_data.page, 1), pages)
    transactions = await balance_service.list_transactions(
        session, user.id, limit=HISTORY_PER_PAGE, offset=(page - 1) * HISTORY_PER_PAGE
    )
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.history(transactions, settings),
            reply_markup=kb.history(page=page, pages=pages),
        )
    await query.answer()


@router.callback_query(ProfileCB.filter(F.action == "referrals"))
async def cb_referrals(
    query: CallbackQuery, session: AsyncSession, user: User, settings: Settings
) -> None:
    referrals = await users_service.list_referrals(session, user.id, limit=30)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.referrals_list(referrals, settings), reply_markup=kb.back_to_profile()
        )
    await query.answer()


@router.callback_query(ProfileCB.filter(F.action == "code"))
async def cb_enter_code(query: CallbackQuery, state: FSMContext, user: User) -> None:
    if not referrals_service.can_bind_manually(user):
        await query.answer("Реферальный код уже нельзя привязать.", show_alert=True)
        return
    await state.set_state(ReferralSG.code)
    await state.update_data(return_to="profile")
    if isinstance(query.message, Message):
        await query.message.answer(texts.ask_ref_code(), reply_markup=kb.cancel_input("profile"))
    await query.answer()
