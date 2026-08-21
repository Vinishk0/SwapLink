"""End-to-end check of the single-screen UI.

Real updates are fed to the real dispatcher; only the Telegram transport is
faked, so what these tests assert is exactly the sequence of API calls the bot
would make: one message per chat, edits instead of new messages, and the user's
own messages deleted.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.bot.handlers import common, exchange, orders, profile, referral, start
from app.bot.handlers.admin import menu as admin_menu
from app.bot.handlers.admin import orders as admin_orders
from app.bot.handlers.admin import rates as admin_rates
from app.bot.handlers.admin import users as admin_users
from app.bot.keyboards.callbacks import MenuCB
from app.bot.runner import build_dispatcher
from app.config import Settings
from app.db.base import Base
from app.db.models import Currency, Pair

CHAT_ID = 42
ADMIN_ID = 1

#: Handler routers are module-level singletons; aiogram refuses to attach one to
#: a second dispatcher, so each test detaches them before wiring a fresh one.
MODULE_ROUTERS = (
    start.router,
    exchange.router,
    profile.router,
    referral.router,
    orders.router,
    common.router,
    admin_menu.router,
    admin_orders.router,
    admin_users.router,
    admin_rates.router,
)


def detach_routers() -> None:
    for router in MODULE_ROUTERS:
        router._parent_router = None


class FakeSession(BaseSession):
    """Records every API call and answers with plausible objects."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod[Any]] = []
        self._message_id = 100

    def names(self) -> list[str]:
        return [type(call).__name__ for call in self.calls]

    def of(self, name: str) -> list[Any]:
        return [call for call in self.calls if type(call).__name__ == name]

    async def close(self) -> None:  # pragma: no cover - nothing to close
        return None

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout=None) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name == "GetMe":
            return TgUser(id=1, is_bot=True, first_name="SwapLink", username="swaplink_bot")
        if name in {"SendMessage", "EditMessageText"}:
            if name == "SendMessage":
                self._message_id += 1
                message_id = self._message_id
            else:
                message_id = method.message_id
            return Message(
                message_id=message_id,
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        return True

    async def stream_content(  # pragma: no cover - never used
        self, url: str, headers=None, timeout: int = 30, chunk_size: int = 65536, **kwargs
    ) -> AsyncGenerator[bytes, None]:
        yield b""


@pytest.fixture
async def flow():
    """A dispatcher wired to an in-memory database and a fake Telegram."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        usdt = Currency(code="USDT", name="Tether", rate_to_base=Decimal("1"))
        rub = Currency(code="RUB", name="Рубль", rate_to_base=Decimal("0.01"))
        session.add_all([usdt, rub])
        await session.flush()
        session.add(Pair(from_currency_id=usdt.id, to_currency_id=rub.id))
        await session.commit()

    detach_routers()
    settings = Settings(
        _env_file=None,
        bot_token="123456789:TEST-TOKEN",
        admin_ids=[ADMIN_ID],
        database_url="sqlite+aiosqlite://",
        throttle_interval=0,
    )
    session_transport = FakeSession()
    bot = Bot(token=settings.bot_token, session=session_transport)
    dispatcher = build_dispatcher(settings, factory)
    dispatcher["bot_username"] = "swaplink_bot"

    yield bot, dispatcher, session_transport, factory

    await engine.dispose()


def message_update(text: str, *, update_id: int = 1, message_id: int = 10) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=CHAT_ID, type="private"),
            from_user=TgUser(id=CHAT_ID, is_bot=False, first_name="Тест"),
            text=text,
        ),
    )


def callback_update(data: str, *, update_id: int = 2, message_id: int = 101) -> Update:
    return Update(
        update_id=update_id,
        callback_query={
            "id": str(update_id),
            "from": {"id": CHAT_ID, "is_bot": False, "first_name": "Тест"},
            "chat_instance": "instance",
            "data": data,
            "message": {
                "message_id": message_id,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": CHAT_ID, "type": "private"},
                "text": "…",
            },
        },
    )


async def test_start_sends_one_message_and_deletes_the_command(flow) -> None:
    bot, dispatcher, transport, _ = flow

    await dispatcher.feed_update(bot, message_update("/start"))

    names = transport.names()
    # The reply-keyboard cleanup message is sent and immediately removed,
    # then the screen itself is sent.
    assert names.count("SendMessage") == 2
    assert names.count("DeleteMessage") == 2  # the cleanup message + "/start"
    assert "EditMessageText" not in names


async def test_button_edits_the_same_message(flow) -> None:
    bot, dispatcher, transport, _ = flow
    await dispatcher.feed_update(bot, message_update("/start"))
    transport.calls.clear()

    await dispatcher.feed_update(bot, callback_update(MenuCB(action="profile").pack()))

    names = transport.names()
    assert "EditMessageText" in names
    assert "SendMessage" not in names


async def test_typing_replaces_the_screen_and_removes_the_user_message(flow) -> None:
    bot, dispatcher, transport, _ = flow
    await dispatcher.feed_update(bot, message_update("/start"))
    transport.calls.clear()

    # An unexpected message: the bot swallows it and redraws the same screen.
    await dispatcher.feed_update(bot, message_update("привет", update_id=3, message_id=11))

    names = transport.names()
    assert names.count("DeleteMessage") == 1
    assert "EditMessageText" in names
    assert "SendMessage" not in names


async def test_whole_exchange_flow_keeps_a_single_message(flow) -> None:
    bot, dispatcher, transport, factory = flow
    await dispatcher.feed_update(bot, message_update("/start"))
    transport.calls.clear()

    # menu -> directions -> pick a pair -> type an amount -> submit
    await dispatcher.feed_update(bot, callback_update(MenuCB(action="exchange").pack()))
    from app.bot.keyboards.callbacks import PairCB, QuoteCB

    await dispatcher.feed_update(bot, callback_update(PairCB(pair_id=1).pack(), update_id=4))
    await dispatcher.feed_update(bot, message_update("100", update_id=5, message_id=12))
    await dispatcher.feed_update(bot, callback_update(QuoteCB(action="submit").pack(), update_id=6))

    names = transport.names()
    # Every step edited the screen; the only new message is the admin card.
    sent = transport.of("SendMessage")
    assert len(sent) == 1
    assert sent[0].chat_id == ADMIN_ID
    assert "Новая заявка" in sent[0].text
    assert names.count("DeleteMessage") == 1  # the typed amount

    async with factory() as session:
        from app.services import orders as orders_service

        rows, total = await orders_service.list_orders_page(session)
        assert total == 1
        assert rows[0].amount_to == Decimal("10000.00")
        # The admin card is remembered so it can be deleted once the deal is done.
        refs = rows[0].admin_message_refs
        assert len(refs) == 1
        assert refs[0][0] == ADMIN_ID
