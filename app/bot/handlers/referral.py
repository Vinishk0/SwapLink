"""Referral programme screen and manual entry of an invite code."""

from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts
from app.bot.keyboards import user as kb
from app.bot.keyboards.callbacks import MenuCB
from app.bot.states import ExchangeSG, ReferralSG
from app.config import Settings
from app.db.models import ReferralSource, User
from app.services import exchange as exchange_service
from app.services import referrals as referrals_service
from app.services.exceptions import ReferralError
from app.utils.format import parse_amount

router = Router(name="referral")

SHARE_TEXT = "Обмен валют с реферальной скидкой — заходи по моей ссылке 👇"


async def _render(
    message: Message,
    session: AsyncSession,
    user: User,
    settings: Settings,
    bot_username: str,
) -> None:
    summary = await referrals_service.get_summary(session, user, settings)
    link = referrals_service.build_ref_link(bot_username, user.ref_code)
    share_url = referrals_service.build_share_url(link, SHARE_TEXT)
    await message.answer(
        texts.referral_program(summary, settings, link),
        reply_markup=kb.referral(
            ref_link=link,
            share_url=share_url,
            can_enter_code=referrals_service.can_bind_manually(user),
        ),
        disable_web_page_preview=True,
    )


@router.message(Command("ref"))
@router.message(F.text == kb.BTN_REFERRAL)
async def open_referral(
    message: Message,
    session: AsyncSession,
    user: User,
    settings: Settings,
    state: FSMContext,
    bot_username: str,
) -> None:
    await state.clear()
    await _render(message, session, user, settings, bot_username)


@router.callback_query(MenuCB.filter(F.action == "referral"))
async def cb_referral(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    settings: Settings,
    bot_username: str,
) -> None:
    if isinstance(query.message, Message):
        await _render(query.message, session, user, settings, bot_username)
    await query.answer()


@router.message(ReferralSG.code, F.text)
async def process_code(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    settings: Settings,
    bot: Bot,
) -> None:
    """Attach the user to an inviter by a pasted code or link."""
    try:
        referrer = await referrals_service.bind_referrer(
            session, user, message.text or "", source=ReferralSource.MANUAL
        )
    except ReferralError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    data = await state.get_data()
    await state.clear()
    await message.answer(texts.ref_code_applied(referrer, settings))
    await notifications.notify_user(bot, referrer.tg_id, texts.notify_new_referral(user, settings))

    if data.get("return_to") != "quote":
        return

    # The user was in the middle of a calculation — repeat it with the discount.
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    amount = parse_amount(str(data.get("amount", "")))
    if pair is None or amount is None:
        return

    discount = referrals_service.discount_percent_for(user, settings)
    bonus_percent = settings.referral_bonus_percent if user.referrer_id else Decimal("0")
    quote = exchange_service.calculate_quote(
        pair, amount, discount_percent=discount, bonus_percent=bonus_percent
    )
    await state.set_state(ExchangeSG.quote)
    await state.update_data(pair_id=pair.id, amount=str(amount))
    await message.answer(
        texts.quote_text(
            quote, settings, discounts_left=user.discounts_left(settings.referral_discount_limit)
        ),
        reply_markup=kb.quote(can_enter_code=False),
    )
