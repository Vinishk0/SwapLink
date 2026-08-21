"""Formatting helpers for money, percents and dates shown to users."""

from __future__ import annotations

import os
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = ZoneInfo("UTC")


def _display_timezone() -> ZoneInfo:
    """Timezone used to show dates; falls back to UTC when tzdata is missing."""
    name = os.getenv("TIMEZONE", "Europe/Moscow")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


DISPLAY_TZ = _display_timezone()

#: Upper bound for any amount typed by a human — guards against `1e999999`.
MAX_AMOUNT = Decimal("1e15")


def quantize(value: Decimal, decimals: int = 2, *, round_down: bool = True) -> Decimal:
    """Round a decimal to `decimals` places (down by default — never over-promise)."""
    exponent = Decimal(1).scaleb(-decimals)
    return Decimal(value).quantize(exponent, rounding=ROUND_DOWN if round_down else ROUND_HALF_UP)


def format_money(value: Decimal | float | int, decimals: int = 2) -> str:
    """`1234.5` -> `1 234.5`; trailing zeros are trimmed, integers stay integers."""
    number = quantize(Decimal(str(value)), decimals, round_down=False)
    sign = "-" if number < 0 else ""
    integral, _, fractional = format(abs(number), "f").partition(".")
    grouped = f"{int(integral):,}".replace(",", " ")
    fractional = fractional.rstrip("0")
    return f"{sign}{grouped}.{fractional}" if fractional else f"{sign}{grouped}"


def format_amount(value: Decimal, code: str, decimals: int = 2) -> str:
    return f"{format_money(value, decimals)} {code}"


def format_percent(value: Decimal | float | int) -> str:
    return f"{format_money(Decimal(str(value)), 4)}%"


def format_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(DISPLAY_TZ).strftime("%d.%m.%Y %H:%M")


def parse_amount(raw: str) -> Decimal | None:
    """Parse user input like `1 000,50` / `1000.5` into a positive Decimal."""
    cleaned = raw.strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except (ArithmeticError, ValueError):
        return None
    if not value.is_finite() or value <= 0 or value > MAX_AMOUNT:
        return None
    return value


__all__ = [
    "MAX_AMOUNT",
    "ROUND_HALF_UP",
    "format_amount",
    "format_dt",
    "format_money",
    "format_percent",
    "parse_amount",
    "quantize",
]
