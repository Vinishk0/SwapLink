"""The single-screen bookkeeping that keeps one bot message per chat."""

from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import ui
from app.bot.states import ExchangeSG


def make_state() -> FSMContext:
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=42, user_id=42))


async def test_reset_flow_forgets_the_dialog_but_keeps_the_screen() -> None:
    state = make_state()
    await ui.remember_screen(state, 777)
    await state.set_state(ExchangeSG.amount)
    await state.update_data(pair_id=3, amount="100")

    await ui.reset_flow(state)

    assert await ui.get_screen_id(state) == 777
    assert await state.get_state() is None
    data = await state.get_data()
    assert "pair_id" not in data and "amount" not in data


async def test_screen_id_starts_empty() -> None:
    assert await ui.get_screen_id(make_state()) is None
