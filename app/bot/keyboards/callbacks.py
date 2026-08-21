"""Typed callback data factories — no hand-parsed callback strings anywhere."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class MenuCB(CallbackData, prefix="menu"):
    """Top-level navigation: main | exchange | profile | referral | orders | help."""

    action: str


class PairCB(CallbackData, prefix="pair"):
    """User picked an exchange direction."""

    pair_id: int


class QuoteCB(CallbackData, prefix="quote"):
    """Actions on a calculated quote: submit | amount | pairs | code."""

    action: str


class OrderCB(CallbackData, prefix="order"):
    """User-side actions on their own order: open | cancel | list."""

    action: str
    order_id: int = 0
    page: int = 1


class ProfileCB(CallbackData, prefix="profile"):
    """Profile screen: refresh | history | referrals | code."""

    action: str
    page: int = 1


# --------------------------------------------------------------------------- #
# Admin panel
# --------------------------------------------------------------------------- #


class AdminCB(CallbackData, prefix="adm"):
    """Admin sections: main | users | orders | rates | stats."""

    section: str


class AdminUserCB(CallbackData, prefix="admu"):
    """Admin actions on a user.

    Actions: list | open | search | block | payout | discount | adjust |
    history | referrals.
    """

    action: str
    user_id: int = 0
    page: int = 1


class AdminOrderCB(CallbackData, prefix="admo"):
    """Admin actions on an order: list | open | confirm | reject | amount | client.

    `src` says where the button lives: `panel` — the admin screen, `note` — the
    standalone "new order" card, which must not be repurposed by navigation.
    """

    action: str
    order_id: int = 0
    page: int = 1
    status: str = "pending"
    src: str = "panel"


class AdminRateCB(CallbackData, prefix="admr"):
    """Rates section.

    Actions: currencies | pairs | cur_open | cur_rate | cur_toggle | cur_del |
    cur_add | pair_open | pair_rate | pair_auto | pair_comm | pair_limits |
    pair_toggle | pair_del | pair_add | pair_add_to.
    """

    action: str
    currency_id: int = 0
    pair_id: int = 0
    page: int = 1


class ConfirmCB(CallbackData, prefix="cnf"):
    """Generic yes/no confirmation: `scope` tells the handler what is confirmed."""

    scope: str
    object_id: int = 0
    answer: str = "no"


class NoopCB(CallbackData, prefix="noop"):
    """Non-interactive button (page counters, headers)."""

    tag: str = "x"
