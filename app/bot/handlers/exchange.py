"""The calculator: pick a direction, enter an amount, submit a request."""

from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts
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


async def _show_pairs(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    pairs = await exchange_service.list_available_pairs(session)
    if not pairs:
        await message.answer(texts.no_pairs())
        return
    await message.answer(texts.choose_pair(), reply_markup=kb.pairs(pairs))


@router.message(Command("exchange"))
@router.message(F.text == kb.BTN_EXCHANGE)
async def open_exchange(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await _show_pairs(message, session, state)


@router.callback_query(MenuCB.filter(F.action == "exchange"))
@router.callback_query(QuoteCB.filter(F.action == "pairs"))
async def cb_open_exchange(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    if isinstance(query.message, Message):
        await _show_pairs(query.message, session, state)
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

    discount = referrals_service.discount_percent_for(user, settings)
    await state.set_state(ExchangeSG.amount)
    await state.update_data(pair_id=pair.id)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.ask_amount(
                pair,
                discount_percent=discount,
                discounts_left=user.discounts_left(settings.referral_discount_limit),
            )
        )
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
    await state.set_state(ExchangeSG.amount)
    if isinstance(query.message, Message):
        await query.message.answer(
            texts.ask_amount(
                pair,
                discount_percent=referrals_service.discount_percent_for(user, settings),
                discounts_left=user.discounts_left(settings.referral_discount_limit),
            )
        )
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
    if amount is None:
        await message.answer(texts.invalid_amount())
        return

    data = await state.get_data()
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    if pair is None or not pair.is_active:
        await message.answer(texts.no_pairs())
        await state.clear()
        return

    limit_error = exchange_service.check_limits(pair, amount)
    if limit_error:
        await message.answer(f"⚠️ {limit_error}")
        return

    discount = referrals_service.discount_percent_for(user, settings)
    bonus_percent = settings.referral_bonus_percent if user.referrer_id else Decimal("0")
    try:
        quote = exchange_service.calculate_quote(
            pair, amount, discount_percent=discount, bonus_percent=bonus_percent
        )
    except RateError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    discounts_left = user.discounts_left(settings.referral_discount_limit)
    await state.set_state(ExchangeSG.quote)
    await state.update_data(pair_id=pair.id, amount=str(amount))
    await message.answer(
        texts.quote_text(quote, settings, discounts_left=discounts_left),
        reply_markup=kb.quote(can_enter_code=referrals_service.can_bind_manually(user)),
    )

    # Tell a referral once that the discount quota is over (requirement: the
    # 4th deal must not silently lose the discount).
    if user.is_referral and discounts_left == 0 and not data.get("limit_warned"):
        await state.update_data(limit_warned=True)
        await message.answer(texts.discount_exhausted(settings))


@router.callback_query(QuoteCB.filter(F.action == "code"))
async def cb_enter_code(query: CallbackQuery, state: FSMContext, user: User) -> None:
    if not referrals_service.can_bind_manually(user):
        await query.answer("Код уже нельзя изменить.", show_alert=True)
        return
    data = await state.get_data()
    await state.set_state(ReferralSG.code)
    await state.update_data(**data, return_to="quote")
    if isinstance(query.message, Message):
        await query.message.answer(texts.ask_ref_code(), reply_markup=kb.cancel_input("exchange"))
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
        await state.clear()
        return

    try:
        order = await orders_service.create_order(
            session, user=user, pair=pair, amount_from=amount, settings=settings
        )
    except (OrderError, ServiceError) as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await state.clear()
    if isinstance(query.message, Message):
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.answer(
            texts.order_created(order, settings),
            reply_markup=kb.order_card(order),
        )
    await query.answer("Заявка отправлена оператору")

    await notifications.notify_admins(
        bot, settings, texts.notify_admin_new_order(order, user, settings)
    )
