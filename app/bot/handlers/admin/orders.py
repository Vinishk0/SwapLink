"""Admin: order queue, confirmation and rejection.

Confirming an order is what actually pays the referral programme, so all the
notifications (client, inviter) are fired from here. The standing "new order"
cards in the admin chats are refreshed on every change and deleted as soon as
the deal is resolved.
"""

from __future__ import annotations

import math

from aiogram import Bot, Dispatcher, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts, ui
from app.bot.filters.admin import IsAdmin
from app.bot.handlers.admin.users import render_card as render_user_card
from app.bot.keyboards import admin as kb
from app.bot.keyboards.admin import SRC_NOTE, SRC_PANEL, SRC_SCREEN
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
    event: Message | CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    *,
    page: int,
    status: str,
    user_id: int | None = None,
) -> None:
    await ui.reset_flow(state)
    rows, total = await orders_service.list_orders_page(
        session, status=_status_filter(status), user_id=user_id, page=page, per_page=PER_PAGE
    )
    pages = max(math.ceil(total / PER_PAGE), 1)
    title = "⏳ <b>Заявки в ожидании</b>" if status == "pending" else "📚 <b>Все заявки</b>"
    header = f"{title} · всего: {total}"
    if not rows:
        header += "\n\nПусто."
    await ui.show(event, state, header, kb.orders_list(rows, page=page, pages=pages, status=status))


async def render_card(
    event: Message | CallbackQuery,
    state: FSMContext,
    order: Order,
    settings: Settings,
    *,
    page: int = 1,
    status: str = "pending",
    src: str = SRC_SCREEN,
    adopt: bool = True,
) -> None:
    """Draw the order on the admin screen (never on a standing card)."""
    await ui.show(
        event,
        state,
        texts.admin_order_card(order, settings),
        kb.order_card(order, page=page, status=status, src=src),
        adopt=adopt and src != SRC_NOTE,
    )


@router.callback_query(AdminCB.filter(F.section == "orders"))
async def cb_orders(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _render_list(query, session, state, page=1, status="pending")
    await query.answer()


@router.callback_query(AdminOrderCB.filter(F.action == "list"))
async def cb_orders_page(
    query: CallbackQuery, callback_data: AdminOrderCB, session: AsyncSession, state: FSMContext
) -> None:
    await _render_list(query, session, state, page=callback_data.page, status=callback_data.status)
    await query.answer()


@router.callback_query(AdminOrderCB.filter(F.action == "by_user"))
async def cb_orders_by_user(
    query: CallbackQuery, callback_data: AdminOrderCB, session: AsyncSession, state: FSMContext
) -> None:
    """`order_id` carries the user id here (see the user card keyboard)."""
    target = await users_service.get_by_id(session, callback_data.order_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    await _render_list(query, session, state, page=1, status="all", user_id=target.id)
    await query.answer()


@router.callback_query(AdminOrderCB.filter(F.action == "open"))
async def cb_order_open(
    query: CallbackQuery,
    callback_data: AdminOrderCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    order = await orders_service.get_order(session, callback_data.order_id)
    if order is None:
        await query.answer("Заявка не найдена.", show_alert=True)
        return
    src = SRC_PANEL if callback_data.src == SRC_PANEL else SRC_SCREEN
    await render_card(
        query, state, order, settings, page=callback_data.page, status=callback_data.status, src=src
    )
    await query.answer()


@router.callback_query(AdminOrderCB.filter(F.action == "client"))
async def cb_order_client(
    query: CallbackQuery,
    callback_data: AdminOrderCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    """Open the client card, remembering which deal the operator came from."""
    order = await orders_service.get_order(session, callback_data.order_id)
    if order is None:
        await query.answer("Заявка не найдена.", show_alert=True)
        return
    await render_user_card(
        query,
        session,
        state,
        order.user,
        settings,
        page=1,
        order_id=order.id,
        adopt=callback_data.src != SRC_NOTE,
    )
    await query.answer()


# --------------------------------------------------------------------------- #
# Confirmation
# --------------------------------------------------------------------------- #


async def _confirm(
    query: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
    order: Order,
    *,
    page: int,
    status: str,
    src: str,
    from_note: bool = False,
) -> None:
    admin_id = query.from_user.id
    try:
        result = await orders_service.confirm_order(
            session, order, admin_id=admin_id, settings=settings
        )
    except OrderError as exc:
        await query.answer(str(exc), show_alert=True)
        return

    await notifications.delete_order_cards(bot, session, order)
    await notifications.push(
        bot, dispatcher, order.user.tg_id, texts.notify_order_confirmed(order, settings)
    )
    if order.discount_applied and order.user.discounts_used >= settings.referral_discount_limit:
        await notifications.push(
            bot, dispatcher, order.user.tg_id, texts.notify_discount_limit_reached(settings)
        )
    if result.referrer is not None and result.bonus_transaction is not None:
        await notifications.push(
            bot,
            dispatcher,
            result.referrer.tg_id,
            texts.notify_referral_bonus(
                order, order.user, result.bonus_amount, result.referrer.balance, settings
            ),
        )

    await render_card(
        query,
        state,
        order,
        settings,
        page=page,
        status=status,
        src=SRC_PANEL if src == SRC_PANEL else SRC_SCREEN,
        # The card the button lived on has just been deleted — never edit it.
        adopt=not from_note,
    )
    note = "Проведено"
    if result.discount_revoked:
        note = "Проведено (скидка не применена — лимит исчерпан)"
    await query.answer(note, show_alert=result.discount_revoked)


@router.callback_query(AdminOrderCB.filter(F.action == "confirm"))
async def cb_confirm_ask(
    query: CallbackQuery,
    callback_data: AdminOrderCB,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    order = await orders_service.get_order(session, callback_data.order_id)
    if order is None or not order.is_pending:
        await query.answer("Заявка уже обработана.", show_alert=True)
        return

    if callback_data.src == SRC_NOTE:
        # The standing card must not be turned into a dialog — act at once.
        await _confirm(
            query,
            session,
            state,
            settings,
            bot,
            dispatcher,
            order,
            page=callback_data.page,
            status=callback_data.status,
            src=SRC_SCREEN,
            from_note=True,
        )
        return

    await state.update_data(
        page=callback_data.page, status=callback_data.status, src=callback_data.src
    )
    bonus_line = ""
    if order.referrer is not None:
        bonus_line = (
            f"\nРеферер {order.referrer.mention} получит "
            f"<b>{format_money(order.bonus_amount, 4)} {settings.base_currency}</b>."
        )
    await ui.show(
        query,
        state,
        f"{texts.admin_order_card(order, settings)}\n\n❓ Провести заявку #{order.id}?{bonus_line}",
        kb.confirm_order(
            order.id, page=callback_data.page, status=callback_data.status, src=callback_data.src
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
    dispatcher: Dispatcher,
) -> None:
    order = await orders_service.get_order(session, callback_data.object_id)
    if order is None:
        await query.answer("Заявка не найдена.", show_alert=True)
        return
    data = await state.get_data()
    await _confirm(
        query,
        session,
        state,
        settings,
        bot,
        dispatcher,
        order,
        page=int(data.get("page", 1)),
        status=str(data.get("status", "pending")),
        src=str(data.get("src", SRC_PANEL)),
    )


# --------------------------------------------------------------------------- #
# Rejection and amount correction
# --------------------------------------------------------------------------- #


@router.callback_query(AdminOrderCB.filter(F.action == "reject"))
async def cb_reject_ask(
    query: CallbackQuery, callback_data: AdminOrderCB, state: FSMContext
) -> None:
    await state.set_state(AdminOrderSG.reject_comment)
    await state.update_data(
        order_id=callback_data.order_id,
        page=callback_data.page,
        status=callback_data.status,
        src=callback_data.src,
    )
    await ui.show(
        query,
        state,
        f"❌ <b>Отклонение заявки #{callback_data.order_id}</b>\n\n"
        "Пришлите причину — её увидит клиент.\n"
        "Отправьте <code>-</code>, чтобы отклонить без комментария.",
        adopt=callback_data.src != SRC_NOTE,
    )
    await query.answer()


@router.message(AdminOrderSG.reject_comment, F.text)
async def process_reject(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    data = await state.get_data()
    order = await orders_service.get_order(session, int(data.get("order_id", 0)))
    if order is None:
        await ui.reset_flow(state)
        await ui.show(message, state, "⚠️ Заявка не найдена.", kb.back_to_main())
        return

    raw = (message.text or "").strip()
    comment = None if raw in {"-", ""} else raw
    admin_id = message.from_user.id if message.from_user else 0
    try:
        await orders_service.reject_order(session, order, admin_id=admin_id, comment=comment)
    except OrderError as exc:
        await ui.reset_flow(state)
        await ui.show(message, state, f"⚠️ {exc}", kb.back_to_main())
        return

    await notifications.delete_order_cards(bot, session, order)
    await notifications.push(bot, dispatcher, order.user.tg_id, texts.notify_order_rejected(order))

    page, status = int(data.get("page", 1)), str(data.get("status", "pending"))
    src = SRC_PANEL if data.get("src") == SRC_PANEL else SRC_SCREEN
    await ui.reset_flow(state)
    await render_card(message, state, order, settings, page=page, status=status, src=src)


@router.callback_query(AdminOrderCB.filter(F.action == "amount"))
async def cb_amount_ask(
    query: CallbackQuery, callback_data: AdminOrderCB, state: FSMContext
) -> None:
    await state.set_state(AdminOrderSG.amount)
    await state.update_data(
        order_id=callback_data.order_id,
        page=callback_data.page,
        status=callback_data.status,
        src=callback_data.src,
    )
    await ui.show(
        query,
        state,
        f"✏️ <b>Сумма заявки #{callback_data.order_id}</b>\n\n"
        "Пришлите фактическую сумму, которую отдаёт клиент — "
        "расчёт и реферальный бонус будут пересчитаны.",
        adopt=callback_data.src != SRC_NOTE,
    )
    await query.answer()


@router.message(AdminOrderSG.amount, F.text)
async def process_amount(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
) -> None:
    amount = parse_amount(message.text or "")
    data = await state.get_data()
    order = await orders_service.get_order(session, int(data.get("order_id", 0)))

    if order is None:
        await ui.reset_flow(state)
        await ui.show(message, state, "⚠️ Заявка не найдена.", kb.back_to_main())
        return
    if amount is None:
        await ui.show(
            message,
            state,
            "⚠️ Пришлите положительное число — сумму, которую отдаёт клиент.",
            kb.back_to_main(),
        )
        return

    try:
        await orders_service.update_pending_amount(
            session, order, amount_from=amount, settings=settings
        )
    except OrderError as exc:
        await ui.reset_flow(state)
        await ui.show(message, state, f"⚠️ {exc}", kb.back_to_main())
        return

    await notifications.refresh_order_cards(bot, order, settings)

    page, status = int(data.get("page", 1)), str(data.get("status", "pending"))
    src = SRC_PANEL if data.get("src") == SRC_PANEL else SRC_SCREEN
    await ui.reset_flow(state)
    await render_card(message, state, order, settings, page=page, status=status, src=src)
