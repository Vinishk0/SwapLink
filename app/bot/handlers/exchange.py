"""The calculator: pick a direction, enter an amount, submit a request."""

from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts, ui
from app.bot.keyboards import user as kb
from app.bot.keyboards.callbacks import MenuCB, PairCB, QuoteCB
from app.bot.states import ExchangeSG, ReferralSG
from app.config import Settings
from app.db.models import User
from app.services import exchange as exchange_service
from app.services import orders as orders_service
from app.services import referrals as referrals_service
from app.services.exceptions import OrderError, RateError, ServiceError
from app.utils.format import parse_amount

logger = logging.getLogger(__name__)
router = Router(name="exchange")


async def _show_pairs(event: Message | CallbackQuery, session: AsyncSession, state: FSMContext):
    await ui.reset_flow(state)
    pairs = await exchange_service.list_available_pairs(session)
    if not pairs:
        return await ui.show(event, state, texts.no_pairs(), kb.back_to_menu())
    return await ui.show(event, state, texts.choose_pair(), kb.pairs(pairs))


async def _ask_amount(
    event: Message | CallbackQuery,
    state: FSMContext,
    user: User,
    settings: Settings,
    pair,
) -> None:
    await state.set_state(ExchangeSG.amount)
    await state.update_data(pair_id=pair.id)
    await ui.show(
        event,
        state,
        texts.ask_amount(
            pair,
            discount_percent=referrals_service.discount_percent_for(user, settings),
            discounts_left=user.discounts_left(settings.referral_discount_limit),
        ),
        kb.amount_input(can_enter_code=referrals_service.can_bind_manually(user)),
    )


@router.message(Command("exchange"))
async def open_exchange(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await _show_pairs(message, session, state)


@router.callback_query(MenuCB.filter(F.action == "exchange"))
@router.callback_query(QuoteCB.filter(F.action == "pairs"))
async def cb_open_exchange(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _show_pairs(query, session, state)
    await query.answer()


@router.callback_query(PairCB.filter())
async def cb_choose_pair(
    query: CallbackQuery,
    callback_data: PairCB,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    settings: Settings,
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None or not pair.is_active:
        await query.answer("Направление больше недоступно.", show_alert=True)
        return
    await _ask_amount(query, state, user, settings, pair)
    await query.answer()


@router.callback_query(QuoteCB.filter(F.action == "amount"))
async def cb_change_amount(
    query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    settings: Settings,
) -> None:
    data = await state.get_data()
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    if pair is None:
        await query.answer("Сначала выберите направление.", show_alert=True)
        return
    await _ask_amount(query, state, user, settings, pair)
    await query.answer()


@router.message(ExchangeSG.amount, F.text)
async def process_amount(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    settings: Settings,
) -> None:
    amount = parse_amount(message.text or "")
    data = await state.get_data()
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))

    if pair is None or not pair.is_active:
        await ui.show(message, state, texts.no_pairs(), kb.back_to_menu())
        await ui.reset_flow(state)
        return

    keyboard = kb.amount_input(can_enter_code=referrals_service.can_bind_manually(user))
    prompt = texts.ask_amount(
        pair,
        discount_percent=referrals_service.discount_percent_for(user, settings),
        discounts_left=user.discounts_left(settings.referral_discount_limit),
    )

    if amount is None:
        await ui.show(message, state, f"{texts.invalid_amount()}\n\n{prompt}", keyboard)
        return

    limit_error = exchange_service.check_limits(pair, amount)
    if limit_error:
        await ui.show(message, state, f"⚠️ {limit_error}\n\n{prompt}", keyboard)
        return

    discount = referrals_service.discount_percent_for(user, settings)
    bonus_percent = settings.referral_bonus_percent if user.referrer_id else Decimal("0")
    try:
        quote = exchange_service.calculate_quote(
            pair, amount, discount_percent=discount, bonus_percent=bonus_percent
        )
    except RateError as exc:
        await ui.show(message, state, f"⚠️ {exc}", kb.back_to_menu())
        return

    discounts_left = user.discounts_left(settings.referral_discount_limit)
    text = texts.quote_text(quote, settings, discounts_left=discounts_left)
    # Tell a referral once that the discount quota is over (the 4th deal must
    # not silently lose the discount).
    if user.is_referral and discounts_left == 0 and not data.get("limit_warned"):
        text = f"{text}\n\n{texts.discount_exhausted(settings)}"
        await state.update_data(limit_warned=True)

    await state.set_state(ExchangeSG.quote)
    await state.update_data(pair_id=pair.id, amount=str(amount))
    await ui.show(
        message, state, text, kb.quote(can_enter_code=referrals_service.can_bind_manually(user))
    )


@router.callback_query(QuoteCB.filter(F.action == "code"))
async def cb_enter_code(query: CallbackQuery, state: FSMContext, user: User) -> None:
    if not referrals_service.can_bind_manually(user):
        await query.answer("Код уже нельзя изменить.", show_alert=True)
        return
    await state.set_state(ReferralSG.code)
    await state.update_data(return_to="quote")
    await ui.show(query, state, texts.ask_ref_code(), kb.cancel_input("exchange"))
    await query.answer()


@router.callback_query(QuoteCB.filter(F.action == "submit"))
async def cb_submit(
    query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    settings: Settings,
    bot: Bot,
) -> None:
    data = await state.get_data()
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    amount = parse_amount(str(data.get("amount", "")))
    if pair is None or amount is None:
        await query.answer("Расчёт устарел — начните заново.", show_alert=True)
        await _show_pairs(query, session, state)
        return

    try:
        order = await orders_service.create_order(
            session, user=user, pair=pair, amount_from=amount, settings=settings
        )
    except (OrderError, ServiceError) as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await ui.reset_flow(state)
    await ui.show(query, state, texts.order_created(order, settings), kb.order_card(order))
    await query.answer("Заявка отправлена оператору")

    await notifications.send_order_cards(bot, session, order, settings)
