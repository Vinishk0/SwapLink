"""Rendered screens must fit into a single Telegram message."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.bot import texts
from app.config import Settings
from app.db.models import Transaction, TransactionType, User

#: Telegram rejects anything longer than this.
MESSAGE_LIMIT = 4096

SETTINGS = Settings(_env_file=None, bot_token="123456789:TEST-TOKEN")


def _user(index: int) -> User:
    return User(
        id=index,
        tg_id=1_000_000_000 + index,
        ref_code=f"CODE{index:04d}",
        first_name="Александр-Максимилиан",
        last_name="Ковалевский-Оболенский",
        deals_count=1234,
        referred_at=datetime.now(UTC),
    )


def test_a_full_page_of_referrals_fits_in_one_message() -> None:
    rows = [(_user(i), Decimal("123456.78901234")) for i in range(1, 9)]

    rendered = texts.referrals_list(rows, SETTINGS, total=10_000, page=1250, per_page=8)

    assert len(rendered) < MESSAGE_LIMIT
    assert "всего: 10000" in rendered
    assert "заработано" in rendered


def test_a_full_page_of_history_fits_in_one_message() -> None:
    transactions = []
    for index in range(10):
        tx = Transaction(
            id=index,
            user_id=1,
            type=TransactionType.REFERRAL_BONUS,
            amount=Decimal("9876.54321"),
            balance_after=Decimal("99999.99"),
            created_at=datetime.now(UTC),
        )
        tx.source_user = _user(index + 1)
        transactions.append(tx)

    rendered = texts.history(transactions, SETTINGS, total=10_000)

    assert len(rendered) < MESSAGE_LIMIT
    assert "всего: 10000" in rendered


def test_empty_pages_explain_themselves() -> None:
    assert "всего рефералов: 500" in texts.referrals_list([], SETTINGS, total=500)
    assert "нет рефералов" in texts.referrals_list([], SETTINGS)
    assert "всего операций: 500" in texts.history([], SETTINGS, total=500)
    assert "не было" in texts.history([], SETTINGS)
