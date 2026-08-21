"""User-side view of their own requests."""

from __future__ import annotations

import math

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.keyboards import user as kb
from app.bot.keyboards.callbacks import MenuCB, OrderCB
from app.db.models import User
from app.services import orders as orders_service
from app.services.exceptions import OrderError

router = Router(name="orders")

PER_PAGE = 8


async def _render_list(
    message: Message, session: AsyncSession, user: User, page: int, *, edit: bool = False
) -> None:
    rows, total = await orders_service.list_orders_page(
        session, user_id=user.id, page=page, per_page=PER_PAGE
    )
    if not rows:
        await message.answer(texts.orders_empty())
        return
    pages = max(math.ceil(total / PER_PAGE), 1)
    text = texts.orders_list(rows)
    markup = kb.orders_list(rows, page=page, pages=pages)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(Command("orders"))
@router.message(F.text == kb.BTN_ORDERS)
async def open_orders(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    await state.clear()
    await _render_list(message, session, user, page=1)


@router.callback_query(MenuCB.filter(F.action == "orders"))
async def cb_open_orders(query: CallbackQuery, session: AsyncSession, user: User) -> None:
    if isinstance(query.message, Message):
        await _render_list(query.message, session, user, page=1)
    await query.answer()


@router.callback_query(OrderCB.filter(F.action == "list"))
async def cb_orders_page(
    query: CallbackQuery, callback_data: OrderCB, session: AsyncSession, user: User
) -> None:
    if isinstance(query.message, Message):
        await _render_list(query.message, session, user, page=callback_data.page, edit=True)
    await query.answer()


@router.callback_query(OrderCB.filter(F.action == "open"))
async def cb_open_order(
    query: CallbackQuery, callback_data: OrderCB, session: AsyncSession, user: User
) -> None:
    order = await orders_service.get_order(session, callback_data.order_id)
    if order is None or order.user_id != user.id:
        await query.answer("Заявка не найдена.", show_alert=True)
        return
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.order_card(order), reply_markup=kb.order_card(order, page=callback_data.page)
        )
    await query.answer()


@router.callback_query(OrderCB.filter(F.action == "cancel"))
async def cb_cancel_order(
    query: CallbackQuery, callback_data: OrderCB, session: AsyncSession, user: User
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
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.order_card(order), reply_markup=kb.order_card(order, page=callback_data.page)
        )
    await query.answer("Заявка отменена")
