"""FSM state groups."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class ExchangeSG(StatesGroup):
    """Calculator: waiting for the amount, then holding the last quote."""

    amount = State()
    quote = State()


class ReferralSG(StatesGroup):
    """Manual entry of a referral code before the first deal."""

    code = State()


class AdminUserSG(StatesGroup):
    search = State()
    payout = State()
    discount = State()
    adjust = State()


class AdminOrderSG(StatesGroup):
    amount = State()
    reject_comment = State()


class AdminRateSG(StatesGroup):
    currency_code = State()
    currency_name = State()
    currency_rate = State()
    currency_edit_rate = State()
    pair_commission = State()
    pair_rate = State()
    pair_limits = State()
    pair_new_commission = State()
