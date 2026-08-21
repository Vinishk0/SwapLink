"""Calculator maths: rates, referral discount, bonus base.

There is no commission in the model at all — the rate an admin sets is what the
client gets, and the referral discount is a bonus on top of it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.db.models import Pair
from app.services.exceptions import RateError
from app.services.exchange import calculate_quote, check_limits


def test_cross_rate_is_derived_from_currency_rates(pair: Pair) -> None:
    # 1 USDT = 1 base, 1 RUB = 0.01 base -> 1 USDT = 100 RUB
    assert pair.effective_rate == Decimal("100")
    assert pair.is_manual_rate is False


def test_manual_rate_overrides_the_cross_rate(pair: Pair) -> None:
    pair.rate = Decimal("95")
    assert pair.effective_rate == Decimal("95")
    assert pair.is_manual_rate is True


def test_quote_without_discount_is_the_plain_rate(pair: Pair) -> None:
    quote = calculate_quote(pair, Decimal("100"))

    assert quote.base_to == Decimal("10000.00")
    assert quote.amount_to == Decimal("10000.00")
    assert quote.discount_amount == Decimal("0.00")
    assert quote.bonus_amount == Decimal("0")
    assert quote.has_discount is False


def test_discount_adds_on_top_of_the_rate(pair: Pair) -> None:
    quote = calculate_quote(pair, Decimal("100"), discount_percent=Decimal("0.5"))

    assert quote.base_to == Decimal("10000.00")
    # +0.5% for the referral
    assert quote.discount_amount == Decimal("50.00")
    assert quote.amount_to == Decimal("10050.00")
    assert quote.has_discount is True


def test_bonus_is_half_a_percent_of_the_volume_in_base_currency(pair: Pair) -> None:
    quote = calculate_quote(pair, Decimal("100"), bonus_percent=Decimal("0.5"))

    assert quote.volume_base == Decimal("100")
    assert quote.bonus_amount == Decimal("0.5")


def test_manual_rate_is_used_for_the_quote(pair: Pair) -> None:
    pair.rate = Decimal("90")
    quote = calculate_quote(pair, Decimal("10"))

    assert quote.rate == Decimal("90")
    assert quote.amount_to == Decimal("900.00")


def test_zero_amount_is_rejected(pair: Pair) -> None:
    with pytest.raises(RateError):
        calculate_quote(pair, Decimal("0"))


def test_limits(pair: Pair) -> None:
    pair.min_amount = Decimal("10")
    pair.max_amount = Decimal("1000")

    assert check_limits(pair, Decimal("5")) is not None
    assert check_limits(pair, Decimal("5000")) is not None
    assert check_limits(pair, Decimal("100")) is None
