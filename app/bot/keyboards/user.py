"""Keyboards for the user-facing part of the bot.

Everything is inline: the bot lives in a single message, so a reply keyboard
(which would need a second one) is not used. Labels are kept short — Telegram
truncates long captions on narrow screens.
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.callbacks import (
    AdminCB,
    MenuCB,
    NoopCB,
    OrderCB,
    PairCB,
    PairsCB,
    ProfileCB,
    QuoteCB,
)
from app.db.models import Order, Pair


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💱 Обмен", callback_data=MenuCB(action="exchange"))
    builder.button(text="👤 Кабинет", callback_data=MenuCB(action="profile"))
    builder.button(text="🎁 Рефералка", callback_data=MenuCB(action="referral"))
    builder.button(text="📋 Заявки", callback_data=MenuCB(action="orders"))
    builder.button(text="ℹ️ Помощь", callback_data=MenuCB(action="help"))
    if is_admin:
        builder.button(text="🛠 Админка", callback_data=AdminCB(section="main"))
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def back_to_menu(text: str = "⬅️ Меню") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=text, callback_data=MenuCB(action="main"))
    return builder.as_markup()


def pairs(pairs_list: Sequence[Pair], *, page: int = 1, pages: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for pair in pairs_list:
        builder.button(text=pair.title, callback_data=PairCB(pair_id=pair.id))
    builder.adjust(2)
    if pages > 1:
        builder.row(*_pager(PairsCB, page=page, pages=pages))
    builder.row(InlineKeyboardButton(text="⬅️ Меню", callback_data=MenuCB(action="main").pack()))
    return builder.as_markup()


def amount_input(*, can_enter_code: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_enter_code:
        builder.button(text="🎟 Есть реф. код", callback_data=QuoteCB(action="code"))
    builder.button(text="⬅️ Направления", callback_data=QuoteCB(action="pairs"))
    builder.adjust(1)
    return builder.as_markup()


def quote(*, can_enter_code: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Оформить", callback_data=QuoteCB(action="submit"))
    builder.button(text="✏️ Сумма", callback_data=QuoteCB(action="amount"))
    builder.button(text="🔄 Направление", callback_data=QuoteCB(action="pairs"))
    if can_enter_code:
        builder.button(text="🎟 Есть реф. код", callback_data=QuoteCB(action="code"))
    builder.button(text="⬅️ Меню", callback_data=MenuCB(action="main"))
    builder.adjust(1, 2, 1, 1)
    return builder.as_markup()


def order_card(order: Order, *, page: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if order.is_pending:
        builder.button(
            text="🚫 Отменить",
            callback_data=OrderCB(action="cancel", order_id=order.id, page=page),
        )
    builder.button(text="📋 Заявки", callback_data=OrderCB(action="list", page=page))
    builder.button(text="⬅️ Меню", callback_data=MenuCB(action="main"))
    builder.adjust(1, 2)
    return builder.as_markup()


def orders_list(orders: Sequence[Order], *, page: int, pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in orders:
        builder.row(
            InlineKeyboardButton(
                text=f"{order.status.short_title} #{order.id} · {order.direction}",
                callback_data=OrderCB(action="open", order_id=order.id, page=page).pack(),
            )
        )
    if pages > 1:
        builder.row(*_pager(OrderCB, page=page, pages=pages, action="list"))
    builder.row(InlineKeyboardButton(text="⬅️ Меню", callback_data=MenuCB(action="main").pack()))
    return builder.as_markup()


def profile(*, ref_link: str, share_url: str, page: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Копировать", copy_text=CopyTextButton(text=ref_link)),
        InlineKeyboardButton(text="📤 Поделиться", url=share_url),
    )
    builder.row(
        InlineKeyboardButton(
            text="💸 История", callback_data=ProfileCB(action="history", page=page).pack()
        ),
        InlineKeyboardButton(
            text="👥 Рефералы", callback_data=ProfileCB(action="referrals").pack()
        ),
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data=ProfileCB(action="refresh").pack()),
        InlineKeyboardButton(text="⬅️ Меню", callback_data=MenuCB(action="main").pack()),
    )
    return builder.as_markup()


def referral(*, ref_link: str, share_url: str, can_enter_code: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Копировать", copy_text=CopyTextButton(text=ref_link)),
        InlineKeyboardButton(text="📤 Поделиться", url=share_url),
    )
    if can_enter_code:
        builder.row(
            InlineKeyboardButton(
                text="🎟 Ввести реф. код", callback_data=ProfileCB(action="code").pack()
            )
        )
    builder.row(
        InlineKeyboardButton(text="👤 Кабинет", callback_data=MenuCB(action="profile").pack()),
        InlineKeyboardButton(text="⬅️ Меню", callback_data=MenuCB(action="main").pack()),
    )
    return builder.as_markup()


def referrals(*, page: int, pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if pages > 1:
        builder.row(*_pager(ProfileCB, page=page, pages=pages, action="referrals"))
    builder.row(
        InlineKeyboardButton(text="⬅️ Кабинет", callback_data=ProfileCB(action="refresh").pack()),
        InlineKeyboardButton(text="🏠 Меню", callback_data=MenuCB(action="main").pack()),
    )
    return builder.as_markup()


def back_to_profile() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Кабинет", callback_data=ProfileCB(action="refresh"))
    builder.button(text="🏠 Меню", callback_data=MenuCB(action="main"))
    builder.adjust(2)
    return builder.as_markup()


def history(*, page: int, pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if pages > 1:
        builder.row(*_pager(ProfileCB, page=page, pages=pages, action="history"))
    builder.row(
        InlineKeyboardButton(text="⬅️ Кабинет", callback_data=ProfileCB(action="refresh").pack()),
        InlineKeyboardButton(text="🏠 Меню", callback_data=MenuCB(action="main").pack()),
    )
    return builder.as_markup()


def cancel_input(target: str = "main") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Отмена", callback_data=MenuCB(action=target))
    return builder.as_markup()


def _pager(factory, *, page: int, pages: int, **extra) -> list[InlineKeyboardButton]:
    """Prev / counter / next row shared by every paginated list."""
    pages = max(pages, 1)
    page = min(max(page, 1), pages)
    prev_page = page - 1 if page > 1 else pages
    next_page = page + 1 if page < pages else 1
    return [
        InlineKeyboardButton(text="◀️", callback_data=factory(page=prev_page, **extra).pack()),
        InlineKeyboardButton(text=f"{page}/{pages}", callback_data=NoopCB().pack()),
        InlineKeyboardButton(text="▶️", callback_data=factory(page=next_page, **extra).pack()),
    ]
