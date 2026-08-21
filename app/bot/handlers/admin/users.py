"""Admin: user list, user card, balance operations."""

from __future__ import annotations

import math
from decimal import Decimal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts, ui
from app.bot.filters.admin import IsAdmin
from app.bot.keyboards import admin as kb
from app.bot.keyboards.callbacks import AdminCB, AdminUserCB
from app.bot.states import AdminUserSG
from app.config import Settings
from app.db.models import TransactionType, User
from app.services import balance as balance_service
from app.services import orders as orders_service
from app.services import referrals as referrals_service
from app.services import users as users_service
from app.services.exceptions import BalanceError
from app.utils.format import MAX_AMOUNT, format_money, parse_amount

router = Router(name="admin-users")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PER_PAGE = 8
SUBVIEW_PER_PAGE = 8
ZERO = Decimal("0")


def _split_amount_comment(raw: str) -> tuple[str, str | None]:
    parts = (raw or "").strip().split(maxsplit=1)
    if not parts:
        return "", None
    return parts[0], (parts[1].strip() if len(parts) > 1 else None)


def _parse_signed(raw: str) -> Decimal | None:
    cleaned = raw.strip().replace(" ", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except ArithmeticError:
        return None
    if not value.is_finite() or value == ZERO or abs(value) > MAX_AMOUNT:
        return None
    return value


async def _render_list(
    event: Message | CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    *,
    page: int,
    query: str | None = None,
) -> None:
    rows, total = await users_service.list_users_page(
        session, page=page, per_page=PER_PAGE, query=query
    )
    pages = max(math.ceil(total / PER_PAGE), 1)
    header = f"👥 <b>Пользователи</b> · всего: {total}"
    if query:
        header += f"\nПоиск: <code>{query}</code>"
    if not rows:
        header += "\n\nНичего не найдено."
    await ui.reset_flow(state)
    if query:
        await state.update_data(user_query=query)
    await ui.show(event, state, header, kb.users_list(rows, page=page, pages=pages))


async def render_card(
    event: Message | CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    target: User,
    settings: Settings,
    *,
    page: int = 1,
    order_id: int = 0,
    adopt: bool = True,
) -> None:
    """Client card. `order_id` links balance write-offs to a specific deal."""
    summary = await referrals_service.get_summary(session, target, settings)
    await ui.reset_flow(state)
    await state.update_data(
        target_user_id=target.id, page=page, list_page=page, ctx_order_id=order_id
    )
    await ui.show(
        event,
        state,
        texts.admin_user_card(target, summary, settings, referrals_count=summary.referrals_count),
        kb.user_card(target, page=page, order_id=order_id),
        adopt=adopt,
    )


@router.callback_query(AdminCB.filter(F.section == "users"))
async def cb_users(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await _render_list(query, session, state, page=1)
    await query.answer()


@router.callback_query(AdminUserCB.filter(F.action == "list"))
async def cb_users_page(
    query: CallbackQuery, callback_data: AdminUserCB, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    await _render_list(query, session, state, page=callback_data.page, query=data.get("user_query"))
    await query.answer()


@router.callback_query(AdminUserCB.filter(F.action == "open"))
async def cb_user_open(
    query: CallbackQuery,
    callback_data: AdminUserCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    target = await users_service.get_by_id(session, callback_data.user_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    data = await state.get_data()
    await render_card(
        query,
        session,
        state,
        target,
        settings,
        page=callback_data.page,
        order_id=int(data.get("ctx_order_id", 0)),
    )
    await query.answer()


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


@router.callback_query(AdminUserCB.filter(F.action == "search"))
async def cb_search(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminUserSG.search)
    await ui.show(
        query,
        state,
        "🔎 <b>Поиск пользователя</b>\n\n"
        "Пришлите ID, @username, имя или реферальный код.\n"
        "Отправьте <code>-</code>, чтобы сбросить фильтр.",
        kb.back_to_main(),
    )
    await query.answer()


@router.message(AdminUserSG.search, F.text)
async def process_search(message: Message, session: AsyncSession, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    search = None if raw in {"-", "*"} else raw
    await _render_list(message, session, state, page=1, query=search)


# --------------------------------------------------------------------------- #
# Balance operations
# --------------------------------------------------------------------------- #

_OPERATIONS = {
    "payout": (
        AdminUserSG.payout,
        "💸 <b>Выплата с баланса</b>\n\nПришлите сумму (можно с комментарием через пробел), "
        "например: <code>25.5 наличными в офисе</code>",
    ),
    "discount": (
        AdminUserSG.discount,
        "🎫 <b>Зачесть бонусы в обмен</b>\n\nПришлите сумму, которая будет списана с баланса "
        "и добавлена клиенту к выдаче: <code>10 бонусы по заявке</code>",
    ),
    "adjust": (
        AdminUserSG.adjust,
        "✏️ <b>Корректировка баланса</b>\n\nПришлите сумму со знаком: <code>+15 бонус</code> "
        "или <code>-5 ошибочное начисление</code>",
    ),
}


@router.callback_query(AdminUserCB.filter(F.action.in_(_OPERATIONS.keys())))
async def cb_operation(
    query: CallbackQuery,
    callback_data: AdminUserCB,
    session: AsyncSession,
    settings: Settings,
    state: FSMContext,
) -> None:
    target = await users_service.get_by_id(session, callback_data.user_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return

    fsm_state, prompt = _OPERATIONS[callback_data.action]
    data = await state.get_data()
    order_id = int(data.get("ctx_order_id", 0))

    await state.set_state(fsm_state)
    await state.update_data(
        target_user_id=target.id, page=callback_data.page, ctx_order_id=order_id
    )

    lines = [
        prompt,
        "",
        f"Клиент: {target.mention}",
        f"Баланс: <b>{format_money(target.balance, 4)} {settings.base_currency}</b>",
    ]
    if order_id and callback_data.action == "discount":
        lines.append(f"Спишется в счёт заявки <b>#{order_id}</b>.")
    await ui.show(
        query, state, "\n".join(lines), kb.back_to_user(target.id, page=callback_data.page)
    )
    await query.answer()


async def _finish_operation(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
    *,
    kind: str,
) -> None:
    data = await state.get_data()
    target = await users_service.get_by_id(session, int(data.get("target_user_id", 0)))
    page = int(data.get("page", 1))
    order_id = int(data.get("ctx_order_id", 0))

    if target is None:
        await ui.reset_flow(state)
        await ui.show(message, state, "⚠️ Пользователь не найден.", kb.back_to_main())
        return

    raw_amount, comment = _split_amount_comment(message.text or "")
    admin_id = message.from_user.id if message.from_user else 0
    order = await orders_service.get_order(session, order_id) if order_id else None

    try:
        if kind == "adjust":
            amount = _parse_signed(raw_amount)
            if amount is None:
                await ui.show(
                    message,
                    state,
                    "⚠️ Пришлите сумму со знаком, например <code>+10</code>.",
                    kb.back_to_user(target.id, page=page),
                )
                return
            transaction = await balance_service.adjust(
                session, target, amount, admin_id=admin_id, comment=comment
            )
            notice = texts.notify_adjustment(amount, target.balance, settings, comment)
        else:
            amount = parse_amount(raw_amount)
            if amount is None:
                await ui.show(
                    message,
                    state,
                    "⚠️ Пришлите положительную сумму, например <code>25</code>.",
                    kb.back_to_user(target.id, page=page),
                )
                return
            is_payout = kind == "payout"
            tx_type = TransactionType.PAYOUT if is_payout else TransactionType.DISCOUNT
            transaction = await balance_service.withdraw(
                session,
                target,
                amount,
                tx_type=tx_type,
                admin_id=admin_id,
                comment=comment,
                order=order if not is_payout else None,
            )
            notice = (
                texts.notify_payout(amount, target.balance, settings, comment)
                if is_payout
                else texts.notify_discount_granted(amount, target.balance, settings, comment)
            )
            if not is_payout and order is not None:
                # The deal card must show what the client really walks away with.
                await orders_service.apply_bonus_write_off(session, order, amount_base=amount)
                await notifications.refresh_order_cards(bot, order, settings)
    except BalanceError as exc:
        await ui.show(message, state, f"⚠️ {exc}", kb.back_to_user(target.id, page=page))
        return

    await notifications.push(bot, dispatcher, target.tg_id, notice)
    await ui.consume(message)
    await ui.reset_flow(state)

    summary = await referrals_service.get_summary(session, target, settings)
    done = (
        f"✅ {transaction.type.title}: "
        f"<b>{format_money(transaction.amount, 4)} {settings.base_currency}</b>"
    )
    await ui.render(
        bot,
        message.chat.id,
        state,
        f"{done}\n\n"
        + texts.admin_user_card(target, summary, settings, referrals_count=summary.referrals_count),
        kb.user_card(target, page=page, order_id=order_id),
    )
    await state.update_data(target_user_id=target.id, page=page, ctx_order_id=order_id)


@router.message(AdminUserSG.payout, F.text)
async def process_payout(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    await _finish_operation(message, session, state, settings, bot, dispatcher, kind="payout")


@router.message(AdminUserSG.discount, F.text)
async def process_discount(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    await _finish_operation(message, session, state, settings, bot, dispatcher, kind="discount")


@router.message(AdminUserSG.adjust, F.text)
async def process_adjust(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    await _finish_operation(message, session, state, settings, bot, dispatcher, kind="adjust")


# --------------------------------------------------------------------------- #
# Extra views
# --------------------------------------------------------------------------- #


@router.callback_query(AdminUserCB.filter(F.action == "history"))
async def cb_history(
    query: CallbackQuery,
    callback_data: AdminUserCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    target = await users_service.get_by_id(session, callback_data.user_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    data = await state.get_data()
    list_page = int(data.get("list_page", 1))
    total = await balance_service.count_transactions(session, target.id)
    pages = max(math.ceil(total / SUBVIEW_PER_PAGE), 1)
    page = min(max(callback_data.page, 1), pages)
    transactions = await balance_service.list_transactions(
        session, target.id, limit=SUBVIEW_PER_PAGE, offset=(page - 1) * SUBVIEW_PER_PAGE
    )
    await ui.show(
        query,
        state,
        texts.history(transactions, settings, total=total),
        kb.user_history(target.id, list_page=list_page, page=page, pages=pages),
    )
    await query.answer()


@router.callback_query(AdminUserCB.filter(F.action == "referrals"))
async def cb_referrals(
    query: CallbackQuery,
    callback_data: AdminUserCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    target = await users_service.get_by_id(session, callback_data.user_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    data = await state.get_data()
    list_page = int(data.get("list_page", 1))
    rows, total = await users_service.list_referrals_page(
        session, target.id, page=callback_data.page, per_page=SUBVIEW_PER_PAGE
    )
    pages = max(math.ceil(total / SUBVIEW_PER_PAGE), 1)
    page = min(max(callback_data.page, 1), pages)
    await ui.show(
        query,
        state,
        texts.referrals_list(
            rows, settings, total=total, page=page, per_page=SUBVIEW_PER_PAGE, owner=target
        ),
        kb.user_referrals(target.id, list_page=list_page, page=page, pages=pages),
    )
    await query.answer()


@router.callback_query(AdminUserCB.filter(F.action == "block"))
async def cb_block(
    query: CallbackQuery,
    callback_data: AdminUserCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
) -> None:
    target = await users_service.get_by_id(session, callback_data.user_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return

    data = await state.get_data()
    await users_service.set_blocked(session, target, not target.is_blocked)
    await notifications.push(
        bot, dispatcher, target.tg_id, texts.notify_user_blocked(target.is_blocked)
    )
    await render_card(
        query,
        session,
        state,
        target,
        settings,
        page=callback_data.page,
        order_id=int(data.get("ctx_order_id", 0)),
    )
    await query.answer("Заблокирован" if target.is_blocked else "Разблокирован")
