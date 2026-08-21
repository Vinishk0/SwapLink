"""Formatting helpers and user queries used by the admin panel."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import users as users_service
from app.utils.format import format_money, format_percent, parse_amount, quantize


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1234.5"), "1 234.5"),
        (Decimal("1000000"), "1 000 000"),
        (Decimal("0.10"), "0.1"),
        (Decimal("-25.25"), "-25.25"),
        (Decimal("9800.00"), "9 800"),
    ],
)
def test_format_money(value: Decimal, expected: str) -> None:
    assert format_money(value) == expected


def test_format_percent() -> None:
    assert format_percent(Decimal("0.5")) == "0.5%"
    assert format_percent(Decimal("2")) == "2%"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1000", Decimal("1000")),
        ("1 000,50", Decimal("1000.50")),
        ("0.5", Decimal("0.5")),
    ],
)
def test_parse_amount(raw: str, expected: Decimal) -> None:
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["", "-5", "0", "abc", "1e999999", "999999999999999999"])
def test_parse_amount_rejects_bad_input(raw: str) -> None:
    """Absurd magnitudes are rejected too — they would overflow the money column."""
    assert parse_amount(raw) is None


def test_quantize_rounds_down_by_default() -> None:
    assert quantize(Decimal("10.999"), 2) == Decimal("10.99")
    assert quantize(Decimal("10.999"), 2, round_down=False) == Decimal("11.00")


async def test_user_search_and_pagination(session: AsyncSession, make_user) -> None:
    alice = await make_user(101, "alice")
    await make_user(102, "bob")
    await make_user(103, "carol")

    rows, total = await users_service.list_users_page(session, page=1, per_page=2)
    assert total == 3
    assert len(rows) == 2

    rows, total = await users_service.list_users_page(session, page=2, per_page=2)
    assert len(rows) == 1

    rows, total = await users_service.list_users_page(session, page=1, query="alice")
    assert total == 1
    assert rows[0].id == alice.id

    rows, total = await users_service.list_users_page(session, page=1, query="103")
    assert total == 1

    rows, total = await users_service.list_users_page(session, page=1, query=alice.ref_code)
    assert total == 1

    assert len(await users_service.search_users(session, "@bob")) == 1
