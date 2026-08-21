"""Referral programme screen and manual entry of an invite code."""

from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts, ui
from app.bot.handlers.profile import links, render_profile
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


async def _render(
    event: Message | CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    settings: Settings,
    bot_username: str,
) -> None:
    await ui.reset_flow(state)
    summary = await referrals_service.get_summary(session, user, settings)
    link, share_url = links(bot_username, user)
    await ui.show(
        event,
        state,
        texts.referral_program(summary, settings, link),
        kb.referral(
            ref_link=link,
            share_url=share_url,
            can_enter_code=referrals_service.can_bind_manually(user),
        ),
    )


@router.message(Command("ref"))
async def open_referral(
    message: Message,
    session: AsyncSession,
    user: User,
    settings: Settings,
    state: FSMContext,
    bot_username: str,
) -> None:
    await _render(message, session, state, user, settings, bot_username)


@router.callback_query(MenuCB.filter(F.action == "referral"))
async def cb_referral(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    settings: Settings,
    state: FSMContext,
    bot_username: str,
) -> None:
    await _render(query, session, state, user, settings, bot_username)
    await query.answer()


@router.message(ReferralSG.code, F.text)
async def process_code(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    settings: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
    bot_username: str,
) -> None:
    """Attach the user to an inviter by a pasted code or link."""
    data = await state.get_data()
    try:
        referrer = await referrals_service.bind_referrer(
            session, user, message.text or "", source=ReferralSource.MANUAL
        )
    except ReferralError as exc:
        await ui.show(
            message,
            state,
            f"⚠️ {exc}\n\n{texts.ask_ref_code()}",
            kb.cancel_input(str(data.get("return_to", "main"))),
        )
        return

    applied = texts.ref_code_applied(referrer, settings)
    await notifications.push(
        bot, dispatcher, referrer.tg_id, texts.notify_new_referral(user, settings)
    )

    if data.get("return_to") != "quote":
        await ui.consume(message)
        await ui.reset_flow(state)
        summary = await referrals_service.get_summary(session, user, settings)
        link, share_url = links(bot_username, user)
        await ui.render(
            bot,
            message.chat.id,
            state,
            f"{applied}\n\n{texts.referral_program(summary, settings, link)}",
            kb.referral(ref_link=link, share_url=share_url, can_enter_code=False),
        )
        return

    # The user was in the middle of a calculation — repeat it with the discount.
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    amount = parse_amount(str(data.get("amount", "")))
    if pair is None or amount is None:
        await render_profile(message, session, state, user, settings, bot_username)
        return

    quote = exchange_service.calculate_quote(
        pair,
        amount,
        discount_percent=referrals_service.discount_percent_for(user, settings),
        bonus_percent=settings.referral_bonus_percent if user.referrer_id else Decimal("0"),
    )
    await state.set_state(ExchangeSG.quote)
    await state.update_data(pair_id=pair.id, amount=str(amount))
    quote_text = texts.quote_text(
        quote, settings, discounts_left=user.discounts_left(settings.referral_discount_limit)
    )
    await ui.show(
        message,
        state,
        f"{applied}\n\n{quote_text}",
        kb.quote(can_enter_code=False),
    )
