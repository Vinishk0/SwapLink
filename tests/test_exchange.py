"""Calculator maths: rates, commission, referral discount, bonus base."""

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


def test_quote_without_discount(pair: Pair) -> None:
    quote = calculate_quote(pair, Decimal("100"))

    assert quote.gross_to == Decimal("10000.00")
    # 2% commission
    assert quote.amount_to == Decimal("9800.00")
    assert quote.commission_percent == Decimal("2")
    assert quote.discount_amount == Decimal("0.00")
    assert quote.bonus_amount == Decimal("0")


def test_discount_lowers_the_commission(pair: Pair) -> None:
    quote = calculate_quote(pair, Decimal("100"), discount_percent=Decimal("0.5"))

    assert quote.commission_percent == Decimal("1.5")
    assert quote.amount_to == Decimal("9850.00")
    # The referral keeps 0.5% of the gross amount
    assert quote.discount_amount == Decimal("50.00")
    assert quote.has_discount is True


def test_bonus_is_half_a_percent_of_the_volume_in_base_currency(pair: Pair) -> None:
    quote = calculate_quote(pair, Decimal("100"), bonus_percent=Decimal("0.5"))

    assert quote.volume_base == Decimal("100")
    assert quote.bonus_amount == Decimal("0.5")


def test_discount_never_exceeds_the_commission(pair: Pair) -> None:
    pair.commission_percent = Decimal("0.2")
    quote = calculate_quote(pair, Decimal("100"), discount_percent=Decimal("0.5"))

    assert quote.commission_percent == Decimal("0")
    assert quote.discount_percent == Decimal("0.2")


def test_zero_amount_is_rejected(pair: Pair) -> None:
    with pytest.raises(RateError):
        calculate_quote(pair, Decimal("0"))


def test_limits(pair: Pair) -> None:
    pair.min_amount = Decimal("10")
    pair.max_amount = Decimal("1000")

    assert check_limits(pair, Decimal("5")) is not None
    assert check_limits(pair, Decimal("5000")) is not None
    assert check_limits(pair, Decimal("100")) is None
