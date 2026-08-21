"""User-side view of their own requests."""

from __future__ import annotations

import math

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts, ui
from app.bot.keyboards import user as kb
from app.bot.keyboards.callbacks import MenuCB, OrderCB
from app.db.models import User
from app.services import orders as orders_service
from app.services.exceptions import OrderError

router = Router(name="orders")

PER_PAGE = 8


async def _render_list(
    event: Message | CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    page: int,
) -> None:
    await ui.reset_flow(state)
    rows, total = await orders_service.list_orders_page(
        session, user_id=user.id, page=page, per_page=PER_PAGE
    )
    if not rows:
        await ui.show(event, state, texts.orders_empty(), kb.back_to_menu())
        return
    pages = max(math.ceil(total / PER_PAGE), 1)
    await ui.show(
        event, state, texts.orders_list(rows), kb.orders_list(rows, page=page, pages=pages)
    )


@router.message(Command("orders"))
async def open_orders(
    message: Message, session: AsyncSession, state: FSMContext, user: User
) -> None:
    await _render_list(message, session, state, user, page=1)


@router.callback_query(MenuCB.filter(F.action == "orders"))
async def cb_open_orders(
    query: CallbackQuery, session: AsyncSession, state: FSMContext, user: User
) -> None:
    await _render_list(query, session, state, user, page=1)
    await query.answer()


@router.callback_query(OrderCB.filter(F.action == "list"))
async def cb_orders_page(
    query: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    state: FSMContext,
    user: User,
) -> None:
    await _render_list(query, session, state, user, page=callback_data.page)
    await query.answer()


@router.callback_query(OrderCB.filter(F.action == "open"))
async def cb_open_order(
    query: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    state: FSMContext,
    user: User,
) -> None:
    order = await orders_service.get_order(session, callback_data.order_id)
    if order is None or order.user_id != user.id:
        await query.answer("Заявка не найдена.", show_alert=True)
        return
    await ui.show(
        query, state, texts.order_card(order), kb.order_card(order, page=callback_data.page)
    )
    await query.answer()


@router.callback_query(OrderCB.filter(F.action == "cancel"))
async def cb_cancel_order(
    query: CallbackQuery,
    callback_data: OrderCB,
    session: AsyncSession,
    state: FSMContext,
    user: User,
    bot: Bot,
) -> None:
    order = await orders_service.get_order(session, callback_data.order_id)
    if order is None or order.user_id != user.id:
        await query.answer("Заявка не найдена.", show_alert=True)
        return
    try:
        await orders_service.cancel_order(session, order, user=user)
    except OrderError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    # The deal is resolved — the standing admin cards must go.
    await notifications.delete_order_cards(bot, session, order)
    await ui.show(
        query, state, texts.order_card(order), kb.order_card(order, page=callback_data.page)
    )
    await query.answer("Заявка отменена")
