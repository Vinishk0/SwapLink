"""Admin: order queue, confirmation and rejection.

Confirming an order is what actually pays the referral programme, so all the
notifications (client, inviter) are fired from here.
"""

from __future__ import annotations

import math

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts
from app.bot.filters.admin import IsAdmin
from app.bot.keyboards import admin as kb
from app.bot.keyboards.callbacks import AdminCB, AdminOrderCB, ConfirmCB
from app.bot.states import AdminOrderSG
from app.config import Settings
from app.db.models import Order, OrderStatus
from app.services import orders as orders_service
from app.services import users as users_service
from app.services.exceptions import OrderError
from app.utils.format import format_money, parse_amount

router = Router(name="admin-orders")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PER_PAGE = 8


def _status_filter(status: str) -> OrderStatus | None:
    return OrderStatus.PENDING if status == "pending" else None


async def _render_list(
    message: Message,
    session: AsyncSession,
    *,
    page: int,
    status: str,
    user_id: int | None = None,
    edit: bool = True,
) -> None:
    rows, total = await orders_service.list_orders_page(
        session,
        status=_status_filter(status),
        user_id=user_id,
        page=page,
        per_page=PER_PAGE,
    )
    pages = max(math.ceil(total / PER_PAGE), 1)
    title = "⏳ <b>Заявки в ожидании</b>" if status == "pending" else "📚 <b>Все заявки</b>"
    header = f"{title} · всего: {total}"
    if not rows:
        header += "\n\nПусто."
    markup = kb.orders_list(rows, page=page, pages=pages, status=status)
    if edit:
        await message.edit_text(header, reply_markup=markup)
    else:
        await message.answer(header, reply_markup=markup)


async def _render_card(
    message: Message,
    order: Order,
    settings: Settings,
    *,
    page: int,
    status: str,
    edit: bool = True,
) -> None:
    text = texts.admin_order_card(order, settings)
    markup = kb.order_card(order, page=page, status=status)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(AdminCB.filter(F.section == "orders"))
async def cb_orders(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        await _render_list(query.message, session, page=1, status="pending")
    await query.answer()


@router.callback_query(AdminOrderCB.filter(F.action == "list"))
async def cb_orders_page(
    query: CallbackQuery, callback_data: AdminOrderCB, session: AsyncSession
) -> None:
    if isinstance(query.message, Message):
        await _render_list(
            query.message, session, page=callback_data.page, status=callback_data.status
        )
    await query.answer()


@router.callback_query(AdminOrderCB.filter(F.action == "by_user"))
async def cb_orders_by_user(
    query: CallbackQuery, callback_data: AdminOrderCB, session: AsyncSession
) -> None:
    """`order_id` carries the user id here (see the user card keyboard)."""
    target = await users_service.get_by_id(session, callback_data.order_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    if isinstance(query.message, Message):
        await _render_list(
            query.message, session, page=1, status="all", user_id=target.id, edit=False
        )
    await query.answer()


@router.callback_query(AdminOrderCB.filter(F.action == "open"))
async def cb_order_open(
    query: CallbackQuery,
    callback_data: AdminOrderCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    order = await orders_service.get_order(session, callback_data.order_id)
    if order is None:
        await query.answer("Заявка не найдена.", show_alert=True)
        return
    if isinstance(query.message, Message):
        await _render_card(
            query.message,
            order,
            settings,
            page=callback_data.page,
            status=callback_data.status,
        )
    await query.answer()


# --------------------------------------------------------------------------- #
# Confirmation
# --------------------------------------------------------------------------- #


@router.callback_query(AdminOrderCB.filter(F.action == "confirm"))
async def cb_confirm_ask(
    query: CallbackQuery,
    callback_data: AdminOrderCB,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    order = await orders_service.get_order(session, callback_data.order_id)
    if order is None or not order.is_pending:
        await query.answer("Заявка уже обработана.", show_alert=True)
        return
    await state.update_data(page=callback_data.page, status=callback_data.status)
    bonus_line = ""
    if order.referrer is not None:
        bonus_line = (
            f"\nРеферер {order.referrer.mention} получит "
            f"<b>{format_money(order.bonus_amount, 4)} {settings.base_currency}</b>."
        )
    if isinstance(query.message, Message):
        await query.message.edit_text(
            f"{texts.admin_order_card(order, settings)}\n\n"
            f"❓ Провести заявку #{order.id}?{bonus_line}",
            reply_markup=kb.confirm_order(
                order.id, page=callback_data.page, status=callback_data.status
            ),
        )
    await query.answer()


@router.callback_query(ConfirmCB.filter((F.scope == "order_confirm") & (F.answer == "yes")))
async def cb_confirm(
    query: CallbackQuery,
    callback_data: ConfirmCB,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
    bot: Bot,
) -> None:
    order = await orders_service.get_order(session, callback_data.object_id)
    if order is None:
        await query.answer("Заявка не найдена.", show_alert=True)
        return

    admin_id = query.from_user.id
    try:
        result = await orders_service.confirm_order(
            session, order, admin_id=admin_id, settings=settings
        )
    except OrderError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    data = await state.get_data()
    page, status = int(data.get("page", 1)), str(data.get("status", "pending"))

    await notifications.notify_user(
        bot, order.user.tg_id, texts.notify_order_confirmed(order, settings)
    )
    if order.discount_applied and order.user.discounts_used >= settings.referral_discount_limit:
        await notifications.notify_user(
            bot, order.user.tg_id, texts.notify_discount_limit_reached(settings)
        )

    if result.referrer is not None and result.bonus_transaction is not None:
        await notifications.notify_user(
            bot,
            result.referrer.tg_id,
            texts.notify_referral_bonus(
                order, order.user, result.bonus_amount, result.referrer.balance, settings
            ),
        )

    if isinstance(query.message, Message):
        await _render_card(query.message, order, settings, page=page, status=status)
    note = "Проведено"
    if result.discount_revoked:
        note = "Проведено (скидка не применена — лимит исчерпан)"
    await query.answer(note, show_alert=result.discount_revoked)


# --------------------------------------------------------------------------- #
# Rejection and amount correction
# --------------------------------------------------------------------------- #


@router.callback_query(AdminOrderCB.filter(F.action == "reject"))
async def cb_reject_ask(
    query: CallbackQuery, callback_data: AdminOrderCB, state: FSMContext
) -> None:
    await state.set_state(AdminOrderSG.reject_comment)
    await state.update_data(
        order_id=callback_data.order_id, page=callback_data.page, status=callback_data.status
    )
    if isinstance(query.message, Message):
        await query.message.answer(
            "❌ Пришлите причину отклонения — её увидит клиент.\n"
            "Отправьте <code>-</code>, чтобы отклонить без комментария."
        )
    await query.answer()


@router.message(AdminOrderSG.reject_comment, F.text)
async def process_reject(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
) -> None:
    data = await state.get_data()
    order = await orders_service.get_order(session, int(data.get("order_id", 0)))
    if order is None:
        await state.set_state(None)
        await message.answer("⚠️ Заявка не найдена.")
        return

    raw = (message.text or "").strip()
    comment = None if raw in {"-", ""} else raw
    admin_id = message.from_user.id if message.from_user else 0
    try:
        await orders_service.reject_order(session, order, admin_id=admin_id, comment=comment)
    except OrderError as exc:
        await state.set_state(None)
        await message.answer(f"⚠️ {exc}")
        return

    await state.set_state(None)
    await notifications.notify_user(bot, order.user.tg_id, texts.notify_order_rejected(order))
    await _render_card(
        message,
        order,
        settings,
        page=int(data.get("page", 1)),
        status=str(data.get("status", "pending")),
        edit=False,
    )


@router.callback_query(AdminOrderCB.filter(F.action == "amount"))
async def cb_amount_ask(
    query: CallbackQuery, callback_data: AdminOrderCB, state: FSMContext
) -> None:
    await state.set_state(AdminOrderSG.amount)
    await state.update_data(
        order_id=callback_data.order_id, page=callback_data.page, status=callback_data.status
    )
    if isinstance(query.message, Message):
        await query.message.answer(
            "✏️ Пришлите фактическую сумму, которую отдаёт клиент — "
            "расчёт и реферальный бонус будут пересчитаны."
        )
    await query.answer()


@router.message(AdminOrderSG.amount, F.text)
async def process_amount(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("⚠️ Пришлите положительное число.")
        return

    data = await state.get_data()
    order = await orders_service.get_order(session, int(data.get("order_id", 0)))
    if order is None:
        await state.set_state(None)
        await message.answer("⚠️ Заявка не найдена.")
        return

    try:
        await orders_service.update_pending_amount(
            session, order, amount_from=amount, settings=settings
        )
    except OrderError as exc:
        await state.set_state(None)
        await message.answer(f"⚠️ {exc}")
        return

    await state.set_state(None)
    await _render_card(
        message,
        order,
        settings,
        page=int(data.get("page", 1)),
        status=str(data.get("status", "pending")),
        edit=False,
    )
