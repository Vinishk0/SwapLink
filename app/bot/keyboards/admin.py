"""Keyboards of the admin panel."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.callbacks import (
    AdminCB,
    AdminOrderCB,
    AdminRateCB,
    AdminUserCB,
    ConfirmCB,
    NoopCB,
)
from app.db.models import Currency, Order, OrderStatus, Pair, User
from app.utils.format import format_money


def main(pending_orders: int = 0) -> InlineKeyboardMarkup:
    pending_badge = f" ({pending_orders})" if pending_orders else ""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📋 Заявки{pending_badge}", callback_data=AdminCB(section="orders"))
    builder.button(text="👥 Пользователи", callback_data=AdminCB(section="users"))
    builder.button(text="💱 Курсы и направления", callback_data=AdminCB(section="rates"))
    builder.button(text="📊 Статистика", callback_data=AdminCB(section="stats"))
    builder.adjust(2, 2)
    return builder.as_markup()


def back_to_main() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В админ-панель", callback_data=AdminCB(section="main"))
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


def users_list(users: Sequence[User], *, page: int, pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user in users:
        label = f"{user.full_name[:24]} · {format_money(user.balance)}"
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=AdminUserCB(action="open", user_id=user.id, page=page).pack(),
            )
        )
    builder.row(*_pager(AdminUserCB, action="list", page=page, pages=pages))
    builder.row(
        InlineKeyboardButton(text="🔎 Поиск", callback_data=AdminUserCB(action="search").pack()),
        InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminCB(section="main").pack()),
    )
    return builder.as_markup()


def user_card(user: User, *, page: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💸 Выплатить с баланса",
        callback_data=AdminUserCB(action="payout", user_id=user.id, page=page),
    )
    builder.button(
        text="🎫 Зачесть в скидку",
        callback_data=AdminUserCB(action="discount", user_id=user.id, page=page),
    )
    builder.button(
        text="✏️ Корректировка баланса",
        callback_data=AdminUserCB(action="adjust", user_id=user.id, page=page),
    )
    builder.button(
        text="📜 История операций",
        callback_data=AdminUserCB(action="history", user_id=user.id, page=page),
    )
    builder.button(
        text="👥 Рефералы",
        callback_data=AdminUserCB(action="referrals", user_id=user.id, page=page),
    )
    builder.button(
        text="📋 Заявки",
        callback_data=AdminOrderCB(action="by_user", order_id=user.id, status="all"),
    )
    builder.button(
        text="🔓 Разблокировать" if user.is_blocked else "🚫 Заблокировать",
        callback_data=AdminUserCB(action="block", user_id=user.id, page=page),
    )
    builder.button(text="⬅️ К списку", callback_data=AdminUserCB(action="list", page=page))
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


def back_to_user(user_id: int, *, page: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⬅️ К пользователю",
        callback_data=AdminUserCB(action="open", user_id=user_id, page=page),
    )
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Orders
# --------------------------------------------------------------------------- #


def orders_list(
    orders: Sequence[Order], *, page: int, pages: int, status: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in orders:
        label = f"{order.status.short_title} #{order.id} · {order.direction}"
        builder.row(
            InlineKeyboardButton(
                text=label,
                callback_data=AdminOrderCB(
                    action="open", order_id=order.id, page=page, status=status
                ).pack(),
            )
        )
    builder.row(*_pager(AdminOrderCB, action="list", page=page, pages=pages, status=status))
    builder.row(
        InlineKeyboardButton(
            text="⏳ Только новые" if status != "pending" else "📚 Все заявки",
            callback_data=AdminOrderCB(
                action="list", page=1, status="pending" if status != "pending" else "all"
            ).pack(),
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminCB(section="main").pack()))
    return builder.as_markup()


def order_card(order: Order, *, page: int = 1, status: str = "pending") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if order.status is OrderStatus.PENDING:
        builder.button(
            text="✅ Подтвердить",
            callback_data=AdminOrderCB(
                action="confirm", order_id=order.id, page=page, status=status
            ),
        )
        builder.button(
            text="❌ Отклонить",
            callback_data=AdminOrderCB(
                action="reject", order_id=order.id, page=page, status=status
            ),
        )
        builder.button(
            text="✏️ Изменить сумму",
            callback_data=AdminOrderCB(
                action="amount", order_id=order.id, page=page, status=status
            ),
        )
    builder.button(
        text="👤 Клиент",
        callback_data=AdminUserCB(action="open", user_id=order.user_id),
    )
    builder.button(
        text="⬅️ К списку",
        callback_data=AdminOrderCB(action="list", page=page, status=status),
    )
    builder.adjust(2, 1, 2)
    return builder.as_markup()


def confirm_order(order_id: int, *, page: int, status: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, провести",
        callback_data=ConfirmCB(scope="order_confirm", object_id=order_id, answer="yes"),
    )
    builder.button(
        text="⬅️ Отмена",
        callback_data=AdminOrderCB(action="open", order_id=order_id, page=page, status=status),
    )
    builder.adjust(1)
    return builder.as_markup()


# --------------------------------------------------------------------------- #
# Rates
# --------------------------------------------------------------------------- #


def rates_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🪙 Валюты", callback_data=AdminRateCB(action="currencies"))
    builder.button(text="🔁 Направления", callback_data=AdminRateCB(action="pairs"))
    builder.button(text="⬅️ Назад", callback_data=AdminCB(section="main"))
    builder.adjust(2, 1)
    return builder.as_markup()


def currencies_list(currencies: Sequence[Currency]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for currency in currencies:
        mark = "" if currency.is_active else "⛔️ "
        builder.row(
            InlineKeyboardButton(
                text=f"{mark}{currency.code} · {format_money(currency.rate_to_base, 6)}",
                callback_data=AdminRateCB(action="cur_open", currency_id=currency.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить валюту", callback_data=AdminRateCB(action="cur_add").pack()
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminCB(section="rates").pack()))
    return builder.as_markup()


def currency_card(currency: Currency) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✏️ Изменить курс",
        callback_data=AdminRateCB(action="cur_rate", currency_id=currency.id),
    )
    builder.button(
        text="🔕 Выключить" if currency.is_active else "🔔 Включить",
        callback_data=AdminRateCB(action="cur_toggle", currency_id=currency.id),
    )
    builder.button(
        text="🗑 Удалить", callback_data=AdminRateCB(action="cur_del", currency_id=currency.id)
    )
    builder.button(text="⬅️ К валютам", callback_data=AdminRateCB(action="currencies"))
    builder.adjust(2, 2)
    return builder.as_markup()


def pairs_list(pairs: Sequence[Pair]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for pair in pairs:
        mark = "" if pair.is_active else "⛔️ "
        builder.row(
            InlineKeyboardButton(
                text=f"{mark}{pair.title} · {format_money(pair.commission_percent, 2)}%",
                callback_data=AdminRateCB(action="pair_open", pair_id=pair.id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить направление", callback_data=AdminRateCB(action="pair_add").pack()
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminCB(section="rates").pack()))
    return builder.as_markup()


def pair_card(pair: Pair) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✏️ Комиссия", callback_data=AdminRateCB(action="pair_comm", pair_id=pair.id)
    )
    builder.button(text="✏️ Курс", callback_data=AdminRateCB(action="pair_rate", pair_id=pair.id))
    if pair.is_manual_rate:
        builder.button(
            text="♻️ Авто-курс", callback_data=AdminRateCB(action="pair_auto", pair_id=pair.id)
        )
    builder.button(
        text="📏 Лимиты", callback_data=AdminRateCB(action="pair_limits", pair_id=pair.id)
    )
    builder.button(
        text="🔕 Выключить" if pair.is_active else "🔔 Включить",
        callback_data=AdminRateCB(action="pair_toggle", pair_id=pair.id),
    )
    builder.button(text="🗑 Удалить", callback_data=AdminRateCB(action="pair_del", pair_id=pair.id))
    builder.button(text="⬅️ К направлениям", callback_data=AdminRateCB(action="pairs"))
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def choose_currency(
    currencies: Sequence[Currency], *, action: str, back_action: str
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for currency in currencies:
        builder.button(
            text=currency.code,
            callback_data=AdminRateCB(action=action, currency_id=currency.id),
        )
    builder.adjust(3)
    builder.row(
        InlineKeyboardButton(text="⬅️ Отмена", callback_data=AdminRateCB(action=back_action).pack())
    )
    return builder.as_markup()


def confirm_delete(scope: str, object_id: int, *, back: AdminRateCB) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗑 Да, удалить",
        callback_data=ConfirmCB(scope=scope, object_id=object_id, answer="yes"),
    )
    builder.button(text="⬅️ Отмена", callback_data=back)
    builder.adjust(1)
    return builder.as_markup()


def _pager(factory, *, action: str, page: int, pages: int, **extra) -> list[InlineKeyboardButton]:
    pages = max(pages, 1)
    page = min(max(page, 1), pages)
    prev_page = page - 1 if page > 1 else pages
    next_page = page + 1 if page < pages else 1
    return [
        InlineKeyboardButton(
            text="◀️", callback_data=factory(action=action, page=prev_page, **extra).pack()
        ),
        InlineKeyboardButton(text=f"{page}/{pages}", callback_data=NoopCB().pack()),
        InlineKeyboardButton(
            text="▶️", callback_data=factory(action=action, page=next_page, **extra).pack()
        ),
    ]
