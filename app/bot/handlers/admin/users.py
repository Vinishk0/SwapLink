"""Admin: user list, user card, balance operations."""

from __future__ import annotations

import math
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import notifications, texts
from app.bot.filters.admin import IsAdmin
from app.bot.keyboards import admin as kb
from app.bot.keyboards.callbacks import AdminCB, AdminUserCB
from app.bot.states import AdminUserSG
from app.config import Settings
from app.db.models import TransactionType, User
from app.services import balance as balance_service
from app.services import referrals as referrals_service
from app.services import users as users_service
from app.services.exceptions import BalanceError
from app.utils.format import MAX_AMOUNT, format_money, parse_amount

router = Router(name="admin-users")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PER_PAGE = 8
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
    message: Message,
    session: AsyncSession,
    *,
    page: int,
    query: str | None = None,
    edit: bool = True,
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
    markup = kb.users_list(rows, page=page, pages=pages)
    if edit:
        await message.edit_text(header, reply_markup=markup)
    else:
        await message.answer(header, reply_markup=markup)


async def _render_card(
    message: Message,
    session: AsyncSession,
    target: User,
    settings: Settings,
    *,
    page: int,
    edit: bool = True,
) -> None:
    summary = await referrals_service.get_summary(session, target, settings)
    text = texts.admin_user_card(target, summary, settings, referrals_count=summary.referrals_count)
    markup = kb.user_card(target, page=page)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(AdminCB.filter(F.section == "users"))
async def cb_users(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        await _render_list(query.message, session, page=1)
    await query.answer()


@router.callback_query(AdminUserCB.filter(F.action == "list"))
async def cb_users_page(
    query: CallbackQuery, callback_data: AdminUserCB, session: AsyncSession, state: FSMContext
) -> None:
    data = await state.get_data()
    if isinstance(query.message, Message):
        await _render_list(
            query.message, session, page=callback_data.page, query=data.get("user_query")
        )
    await query.answer()


@router.callback_query(AdminUserCB.filter(F.action == "open"))
async def cb_user_open(
    query: CallbackQuery,
    callback_data: AdminUserCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    target = await users_service.get_by_id(session, callback_data.user_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    if isinstance(query.message, Message):
        await _render_card(query.message, session, target, settings, page=callback_data.page)
    await query.answer()


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


@router.callback_query(AdminUserCB.filter(F.action == "search"))
async def cb_search(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminUserSG.search)
    if isinstance(query.message, Message):
        await query.message.answer(
            "🔎 Пришлите ID, @username, имя или реферальный код.\n"
            "Пустой запрос (<code>-</code>) сбрасывает фильтр."
        )
    await query.answer()


@router.message(AdminUserSG.search, F.text)
async def process_search(message: Message, session: AsyncSession, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    search = None if raw in {"-", "*"} else raw
    await state.update_data(user_query=search)
    await state.set_state(None)
    await _render_list(message, session, page=1, query=search, edit=False)


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
        "🎫 <b>Зачесть бонусы в скидку</b>\n\nПришлите сумму, которая будет списана с баланса "
        "и учтена скидкой в обмене: <code>10 скидка по заявке #12</code>",
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
    await state.set_state(fsm_state)
    await state.update_data(target_user_id=target.id, page=callback_data.page)
    if isinstance(query.message, Message):
        await query.message.answer(
            f"{prompt}\n\nБаланс пользователя: "
            f"<b>{format_money(target.balance, 4)} {settings.base_currency}</b>"
        )
    await query.answer()


async def _finish_operation(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    bot: Bot,
    *,
    kind: str,
) -> None:
    data = await state.get_data()
    target = await users_service.get_by_id(session, int(data.get("target_user_id", 0)))
    if target is None:
        await state.set_state(None)
        await message.answer("⚠️ Пользователь не найден.")
        return

    raw_amount, comment = _split_amount_comment(message.text or "")
    admin_id = message.from_user.id if message.from_user else 0

    try:
        if kind == "adjust":
            amount = _parse_signed(raw_amount)
            if amount is None:
                await message.answer("⚠️ Пришлите сумму со знаком, например <code>+10</code>.")
                return
            transaction = await balance_service.adjust(
                session, target, amount, admin_id=admin_id, comment=comment
            )
            notice = texts.notify_adjustment(amount, target.balance, settings, comment)
        else:
            amount = parse_amount(raw_amount)
            if amount is None:
                await message.answer("⚠️ Пришлите положительную сумму, например <code>25</code>.")
                return
            tx_type = TransactionType.PAYOUT if kind == "payout" else TransactionType.DISCOUNT
            transaction = await balance_service.withdraw(
                session, target, amount, tx_type=tx_type, admin_id=admin_id, comment=comment
            )
            notice = (
                texts.notify_payout(amount, target.balance, settings, comment)
                if kind == "payout"
                else texts.notify_discount_granted(amount, target.balance, settings, comment)
            )
    except BalanceError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    await state.set_state(None)
    await message.answer(
        f"✅ Готово. {transaction.type.title}: "
        f"<b>{format_money(transaction.amount, 4)} {settings.base_currency}</b>\n"
        f"Новый баланс: <b>{format_money(target.balance, 4)} {settings.base_currency}</b>"
    )
    await notifications.notify_user(bot, target.tg_id, notice)
    await _render_card(
        message, session, target, settings, page=int(data.get("page", 1)), edit=False
    )


@router.message(AdminUserSG.payout, F.text)
async def process_payout(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings, bot: Bot
) -> None:
    await _finish_operation(message, session, state, settings, bot, kind="payout")


@router.message(AdminUserSG.discount, F.text)
async def process_discount(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings, bot: Bot
) -> None:
    await _finish_operation(message, session, state, settings, bot, kind="discount")


@router.message(AdminUserSG.adjust, F.text)
async def process_adjust(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings, bot: Bot
) -> None:
    await _finish_operation(message, session, state, settings, bot, kind="adjust")


# --------------------------------------------------------------------------- #
# Extra views
# --------------------------------------------------------------------------- #


@router.callback_query(AdminUserCB.filter(F.action == "history"))
async def cb_history(
    query: CallbackQuery,
    callback_data: AdminUserCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    target = await users_service.get_by_id(session, callback_data.user_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    transactions = await balance_service.list_transactions(session, target.id, limit=15)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.history(transactions, settings),
            reply_markup=kb.back_to_user(target.id, page=callback_data.page),
        )
    await query.answer()


@router.callback_query(AdminUserCB.filter(F.action == "referrals"))
async def cb_referrals(
    query: CallbackQuery,
    callback_data: AdminUserCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    target = await users_service.get_by_id(session, callback_data.user_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    referrals = await users_service.list_referrals(session, target.id, limit=30)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.referrals_list(referrals, settings),
            reply_markup=kb.back_to_user(target.id, page=callback_data.page),
        )
    await query.answer()


@router.callback_query(AdminUserCB.filter(F.action == "block"))
async def cb_block(
    query: CallbackQuery,
    callback_data: AdminUserCB,
    session: AsyncSession,
    settings: Settings,
    bot: Bot,
) -> None:
    target = await users_service.get_by_id(session, callback_data.user_id)
    if target is None:
        await query.answer("Пользователь не найден.", show_alert=True)
        return
    await users_service.set_blocked(session, target, not target.is_blocked)
    await notifications.notify_user(bot, target.tg_id, texts.notify_user_blocked(target.is_blocked))
    if isinstance(query.message, Message):
        await _render_card(query.message, session, target, settings, page=callback_data.page)
    await query.answer("Заблокирован" if target.is_blocked else "Разблокирован")
