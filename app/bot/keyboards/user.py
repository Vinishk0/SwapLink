"""Keyboards for the user-facing part of the bot."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from app.bot.keyboards.callbacks import (
    MenuCB,
    NoopCB,
    OrderCB,
    PairCB,
    ProfileCB,
    QuoteCB,
)
from app.db.models import Order, Pair

BTN_EXCHANGE = "💱 Рассчитать обмен"
BTN_PROFILE = "👤 Личный кабинет"
BTN_REFERRAL = "🎁 Реферальная программа"
BTN_ORDERS = "📋 Мои заявки"
BTN_HELP = "ℹ️ Помощь"
BTN_ADMIN = "🛠 Админ-панель"


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=BTN_EXCHANGE))
    builder.row(KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_REFERRAL))
    builder.row(KeyboardButton(text=BTN_ORDERS), KeyboardButton(text=BTN_HELP))
    if is_admin:
        builder.row(KeyboardButton(text=BTN_ADMIN))
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def pairs(pairs_list: Sequence[Pair]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for pair in pairs_list:
        builder.button(text=pair.title, callback_data=PairCB(pair_id=pair.id))
    builder.adjust(2)
    return builder.as_markup()


def quote(*, can_enter_code: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Оформить заявку", callback_data=QuoteCB(action="submit"))
    builder.button(text="✏️ Другая сумма", callback_data=QuoteCB(action="amount"))
    builder.button(text="🔄 Другое направление", callback_data=QuoteCB(action="pairs"))
    if can_enter_code:
        builder.button(text="🎟 У меня есть реферальный код", callback_data=QuoteCB(action="code"))
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def order_card(order: Order, *, page: int = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if order.is_pending:
        builder.button(
            text="🚫 Отменить заявку",
            callback_data=OrderCB(action="cancel", order_id=order.id, page=page),
        )
    builder.button(text="📋 К списку заявок", callback_data=OrderCB(action="list", page=page))
    builder.adjust(1)
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
    builder.row(*_pager(OrderCB, page=page, pages=pages, action="list"))
    return builder.as_markup()


def profile(
    *, ref_link: str, share_url: str, page: int = 1, has_history: bool = True
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=ref_link))
    )
    builder.row(InlineKeyboardButton(text="📤 Поделиться ссылкой", url=share_url))
    if has_history:
        builder.row(
            InlineKeyboardButton(
                text="💸 История начислений",
                callback_data=ProfileCB(action="history", page=page).pack(),
            ),
            InlineKeyboardButton(
                text="👥 Мои рефералы", callback_data=ProfileCB(action="referrals").pack()
            ),
        )
    builder.row(
        InlineKeyboardButton(text="🔄 Обновить", callback_data=ProfileCB(action="refresh").pack())
    )
    return builder.as_markup()


def referral(*, ref_link: str, share_url: str, can_enter_code: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=ref_link))
    )
    builder.row(InlineKeyboardButton(text="📤 Поделиться ссылкой", url=share_url))
    if can_enter_code:
        builder.row(
            InlineKeyboardButton(
                text="🎟 Ввести реферальный код",
                callback_data=ProfileCB(action="code").pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="👤 Личный кабинет", callback_data=MenuCB(action="profile").pack()
        )
    )
    return builder.as_markup()


def back_to_profile() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ В личный кабинет", callback_data=ProfileCB(action="refresh"))
    return builder.as_markup()


def history(*, page: int, pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(*_pager(ProfileCB, page=page, pages=pages, action="history"))
    builder.row(
        InlineKeyboardButton(
            text="⬅️ В личный кабинет", callback_data=ProfileCB(action="refresh").pack()
        )
    )
    return builder.as_markup()


def cancel_input(target: str = "profile") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Отмена", callback_data=MenuCB(action=target))
    return builder.as_markup()


def _pager(factory, *, page: int, pages: int, action: str, **extra) -> list[InlineKeyboardButton]:
    """Prev / counter / next row shared by paginated lists."""
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
