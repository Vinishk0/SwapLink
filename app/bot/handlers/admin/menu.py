"""Admin panel entry point and statistics."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
from app.bot.filters.admin import IsAdmin
from app.bot.keyboards import admin as kb
from app.bot.keyboards import user as user_kb
from app.bot.keyboards.callbacks import AdminCB
from app.config import Settings
from app.services import stats as stats_service

router = Router(name="admin-menu")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.message(Command("admin"))
@router.message(F.text == user_kb.BTN_ADMIN)
async def open_admin(
    message: Message, session: AsyncSession, settings: Settings, state: FSMContext
) -> None:
    await state.clear()
    stats = await stats_service.collect(session)
    await message.answer(
        texts.admin_main(stats, settings), reply_markup=kb.main(stats.orders_pending)
    )


@router.callback_query(AdminCB.filter(F.section == "main"))
async def cb_admin_main(
    query: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext
) -> None:
    await state.clear()
    stats = await stats_service.collect(session)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.admin_main(stats, settings), reply_markup=kb.main(stats.orders_pending)
        )
    await query.answer()


@router.callback_query(AdminCB.filter(F.section == "stats"))
async def cb_admin_stats(query: CallbackQuery, session: AsyncSession, settings: Settings) -> None:
    stats = await stats_service.collect(session)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.admin_stats(stats, settings), reply_markup=kb.back_to_main()
        )
    await query.answer()


async def refresh_dashboard(message: Message, session: AsyncSession, settings: Settings) -> None:
    """Redraw the dashboard — used after an order changes the pending count."""
    stats = await stats_service.collect(session)
    await message.answer(
        texts.admin_main(stats, settings), reply_markup=kb.main(stats.orders_pending)
    )
