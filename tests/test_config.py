"""Settings parsing — mostly a guard around `ADMIN_IDS`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

TOKEN = "123456789:TEST-TOKEN"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8090745188", [8090745188]),
        (8090745188, [8090745188]),
        ("1,2", [1, 2]),
        ("1; 2", [1, 2]),
        ("[1, 2]", [1, 2]),
        ("", []),
        ([1, 2], [1, 2]),
    ],
)
def test_admin_ids_accepts_every_reasonable_spelling(raw: object, expected: list[int]) -> None:
    settings = Settings(_env_file=None, bot_token=TOKEN, admin_ids=raw)
    assert settings.admin_ids == expected


def test_admin_ids_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single id is valid JSON — it must not be decoded into a bare int."""
    monkeypatch.setenv("ADMIN_IDS", "8090745188")
    monkeypatch.setenv("BOT_TOKEN", TOKEN)

    settings = Settings(_env_file=None)

    assert settings.admin_ids == [8090745188]
    assert settings.is_admin(8090745188) is True
    assert settings.is_admin(1) is False


def test_admin_ids_rejects_non_numeric() -> None:
    with pytest.raises(ValidationError, match="ADMIN_IDS"):
        Settings(_env_file=None, bot_token=TOKEN, admin_ids="abc")


def test_missing_token_is_a_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    with pytest.raises(ValidationError, match="bot_token"):
        Settings(_env_file=None)


def test_support_username_and_base_currency_are_normalised() -> None:
    settings = Settings(
        _env_file=None, bot_token=TOKEN, support_username="@operator", base_currency=" usdt "
    )

    assert settings.support_username == "operator"
    assert settings.base_currency == "USDT"
    assert settings.is_sqlite is True
