"""Application settings loaded from environment variables / `.env`."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed access to every knob of the bot. See `.env.example` for docs."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram ---
    bot_token: str = Field(min_length=10)
    #: `NoDecode` keeps the raw string: a single id like `123` is valid JSON and
    #: would otherwise arrive as an int instead of a list.
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    drop_pending_updates: bool = True
    #: Minimum seconds between two updates from the same user (0 disables it).
    throttle_interval: float = 0.35

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///data/swaplink.db"
    db_echo: bool = False

    # --- FSM storage ---
    redis_url: str | None = None

    # --- Referral programme ---
    referral_discount_percent: Decimal = Decimal("0.5")
    referral_bonus_percent: Decimal = Decimal("0.5")
    referral_discount_limit: int = 3

    # --- Exchange ---
    base_currency: str = "USDT"
    seed_demo_data: bool = False

    # --- Misc ---
    log_level: str = "INFO"
    support_username: str | None = None

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        """Accept `123`, `123,456`, `123; 456`, `[123, 456]` and real lists."""
        if value is None:
            return []
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            cleaned = value.strip().strip("[]")
            chunks = [c.strip() for c in cleaned.replace(";", ",").split(",") if c.strip()]
            try:
                return [int(chunk) for chunk in chunks]
            except ValueError as exc:
                raise ValueError(
                    "ADMIN_IDS must contain numeric Telegram ids separated by commas, "
                    f"got: {value!r}"
                ) from exc
        return value

    @field_validator("redis_url", "support_username", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("base_currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("support_username")
    @classmethod
    def _strip_at(cls, value: str | None) -> str | None:
        return value.lstrip("@") if value else None

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance — import this instead of building `Settings()`."""
    return Settings()  # type: ignore[call-arg]
