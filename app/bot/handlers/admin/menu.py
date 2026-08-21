"""Admin panel entry point and statistics."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, ui
from app.bot.filters.admin import IsAdmin
from app.bot.keyboards import admin as kb
from app.bot.keyboards.callbacks import AdminCB
from app.config import Settings
from app.services import stats as stats_service

router = Router(name="admin-menu")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def render_dashboard(
    event: Message | CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    await ui.reset_flow(state)
    stats = await stats_service.collect(session)
    await ui.show(event, state, texts.admin_main(stats, settings), kb.main(stats.orders_pending))


@router.message(Command("admin"))
async def open_admin(
    message: Message, session: AsyncSession, settings: Settings, state: FSMContext
) -> None:
    await render_dashboard(message, session, state, settings)


@router.callback_query(AdminCB.filter(F.section == "main"))
async def cb_admin_main(
    query: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext
) -> None:
    await render_dashboard(query, session, state, settings)
    await query.answer()


@router.callback_query(AdminCB.filter(F.section == "stats"))
async def cb_admin_stats(
    query: CallbackQuery, session: AsyncSession, settings: Settings, state: FSMContext
) -> None:
    stats = await stats_service.collect(session)
    await ui.show(query, state, texts.admin_stats(stats, settings), kb.back_to_main())
    await query.answer()
