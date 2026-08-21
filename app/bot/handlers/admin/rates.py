"""Admin: currencies, cross-rates and exchange directions.

Rates are entered as final client rates — the margin of the office is already in
them, and the bot adds nothing on top.
"""

from __future__ import annotations

import math
from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts, ui
from app.bot.filters.admin import IsAdmin
from app.bot.keyboards import admin as kb
from app.bot.keyboards.callbacks import AdminCB, AdminRateCB, ConfirmCB
from app.bot.states import AdminRateSG
from app.config import Settings
from app.services import exchange as exchange_service
from app.services.exceptions import RateError
from app.utils.format import parse_amount

router = Router(name="admin-rates")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

ZERO = Decimal("0")
PER_PAGE = 10


async def _show_currencies(
    event: Message | CallbackQuery, session: AsyncSession, state: FSMContext, *, page: int = 1
) -> None:
    await ui.reset_flow(state)
    currencies = await exchange_service.list_currencies(session)
    pages = max(math.ceil(len(currencies) / PER_PAGE), 1)
    page = min(max(page, 1), pages)
    chunk = currencies[(page - 1) * PER_PAGE : page * PER_PAGE]
    await ui.show(
        event,
        state,
        f"🪙 <b>Валюты</b> · всего: {len(currencies)}\n"
        "Курс указывается к базовой валюте — из него считаются кросс-курсы "
        "и объём сделки для реферальных начислений.",
        kb.currencies_list(chunk, page=page, pages=pages),
    )


async def _show_pairs(
    event: Message | CallbackQuery, session: AsyncSession, state: FSMContext, *, page: int = 1
) -> None:
    await ui.reset_flow(state)
    pairs = await exchange_service.list_pairs(session)
    pages = max(math.ceil(len(pairs) / PER_PAGE), 1)
    page = min(max(page, 1), pages)
    chunk = pairs[(page - 1) * PER_PAGE : page * PER_PAGE]
    await ui.show(
        event,
        state,
        f"🔁 <b>Направления обмена</b> · всего: {len(pairs)}\n"
        "Выберите направление, чтобы изменить курс или лимиты.",
        kb.pairs_list(chunk, page=page, pages=pages),
    )


async def _show_currency(
    event: Message | CallbackQuery, state: FSMContext, currency, settings: Settings
) -> None:
    await ui.reset_flow(state)
    await ui.show(
        event, state, texts.admin_currency_card(currency, settings), kb.currency_card(currency)
    )


async def _show_pair(
    event: Message | CallbackQuery, state: FSMContext, pair, settings: Settings
) -> None:
    await ui.reset_flow(state)
    await ui.show(event, state, texts.admin_pair_card(pair, settings), kb.pair_card(pair))


@router.callback_query(AdminCB.filter(F.section == "rates"))
async def cb_rates(query: CallbackQuery, state: FSMContext) -> None:
    await ui.reset_flow(state)
    await ui.show(query, state, "💱 <b>Курсы и направления</b>", kb.rates_menu())
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "currencies"))
async def cb_currencies(
    query: CallbackQuery, callback_data: AdminRateCB, session: AsyncSession, state: FSMContext
) -> None:
    await _show_currencies(query, session, state, page=callback_data.page)
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "pairs"))
async def cb_pairs(
    query: CallbackQuery, callback_data: AdminRateCB, session: AsyncSession, state: FSMContext
) -> None:
    await _show_pairs(query, session, state, page=callback_data.page)
    await query.answer()


# --------------------------------------------------------------------------- #
# Currencies
# --------------------------------------------------------------------------- #


@router.callback_query(AdminRateCB.filter(F.action == "cur_open"))
async def cb_currency_open(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    currency = await exchange_service.get_currency(session, callback_data.currency_id)
    if currency is None:
        await query.answer("Валюта не найдена.", show_alert=True)
        return
    await _show_currency(query, state, currency, settings)
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "cur_add"))
async def cb_currency_add(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminRateSG.currency_code)
    await ui.show(
        query,
        state,
        "🪙 <b>Новая валюта</b>\n\nПришлите код, например <code>USDT</code>:",
        kb.back_to_main(),
    )
    await query.answer()


@router.message(AdminRateSG.currency_code, F.text)
async def process_currency_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    if not code.isalnum() or len(code) > 16:
        await ui.show(
            message,
            state,
            "⚠️ Код должен быть буквенно-цифровым, до 16 символов.",
            kb.back_to_main(),
        )
        return
    await state.update_data(code=code)
    await state.set_state(AdminRateSG.currency_name)
    await ui.show(
        message,
        state,
        f"Название для <b>{code}</b> (например «Доллар США»):",
        kb.back_to_main(),
    )


@router.message(AdminRateSG.currency_name, F.text)
async def process_currency_name(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.update_data(name=(message.text or "").strip()[:64])
    await state.set_state(AdminRateSG.currency_rate)
    data = await state.get_data()
    await ui.show(
        message,
        state,
        f"Курс: сколько <b>{settings.base_currency}</b> стоит 1 "
        f"<b>{data['code']}</b>?\nНапример: <code>1</code> или <code>0.0107</code>",
        kb.back_to_main(),
    )


@router.message(AdminRateSG.currency_rate, F.text)
async def process_currency_rate(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    rate = parse_amount(message.text or "")
    if rate is None:
        await ui.show(message, state, "⚠️ Пришлите положительное число.", kb.back_to_main())
        return
    data = await state.get_data()
    try:
        currency = await exchange_service.create_currency(
            session, code=data["code"], name=data.get("name", data["code"]), rate_to_base=rate
        )
    except RateError as exc:
        await ui.reset_flow(state)
        await ui.show(message, state, f"⚠️ {exc}", kb.back_to_main())
        return
    await _show_currency(message, state, currency, settings)


@router.callback_query(AdminRateCB.filter(F.action == "cur_rate"))
async def cb_currency_rate(
    query: CallbackQuery, callback_data: AdminRateCB, state: FSMContext, settings: Settings
) -> None:
    await state.set_state(AdminRateSG.currency_edit_rate)
    await state.update_data(currency_id=callback_data.currency_id)
    await ui.show(
        query,
        state,
        f"✏️ Пришлите новый курс к <b>{settings.base_currency}</b>:",
        kb.back_to_main(),
    )
    await query.answer()


@router.message(AdminRateSG.currency_edit_rate, F.text)
async def process_currency_edit_rate(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    rate = parse_amount(message.text or "")
    if rate is None:
        await ui.show(message, state, "⚠️ Пришлите положительное число.", kb.back_to_main())
        return
    data = await state.get_data()
    currency = await exchange_service.get_currency(session, int(data.get("currency_id", 0)))
    if currency is None:
        await ui.reset_flow(state)
        await ui.show(message, state, "⚠️ Валюта не найдена.", kb.back_to_main())
        return
    await exchange_service.update_currency_rate(session, currency, rate)
    await _show_currency(message, state, currency, settings)


@router.callback_query(AdminRateCB.filter(F.action == "cur_toggle"))
async def cb_currency_toggle(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    currency = await exchange_service.get_currency(session, callback_data.currency_id)
    if currency is None:
        await query.answer("Валюта не найдена.", show_alert=True)
        return
    await exchange_service.toggle_currency(session, currency)
    await _show_currency(query, state, currency, settings)
    await query.answer("Включена" if currency.is_active else "Выключена")


@router.callback_query(AdminRateCB.filter(F.action == "cur_del"))
async def cb_currency_delete_ask(
    query: CallbackQuery, callback_data: AdminRateCB, session: AsyncSession, state: FSMContext
) -> None:
    currency = await exchange_service.get_currency(session, callback_data.currency_id)
    if currency is None:
        await query.answer("Валюта не найдена.", show_alert=True)
        return
    await ui.show(
        query,
        state,
        f"🗑 Удалить валюту <b>{currency.code}</b>?\n"
        "Все направления с этой валютой тоже будут удалены.",
        kb.confirm_delete(
            "cur_del", currency.id, back=AdminRateCB(action="cur_open", currency_id=currency.id)
        ),
    )
    await query.answer()


@router.callback_query(ConfirmCB.filter((F.scope == "cur_del") & (F.answer == "yes")))
async def cb_currency_delete(
    query: CallbackQuery, callback_data: ConfirmCB, session: AsyncSession, state: FSMContext
) -> None:
    currency = await exchange_service.get_currency(session, callback_data.object_id)
    if currency is not None:
        await exchange_service.delete_currency(session, currency)
    await _show_currencies(query, session, state)
    await query.answer("Удалено")


# --------------------------------------------------------------------------- #
# Pairs
# --------------------------------------------------------------------------- #


@router.callback_query(AdminRateCB.filter(F.action == "pair_open"))
async def cb_pair_open(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None:
        await query.answer("Направление не найдено.", show_alert=True)
        return
    await _show_pair(query, state, pair, settings)
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "pair_add"))
async def cb_pair_add(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await ui.reset_flow(state)
    currencies = await exchange_service.list_currencies(session)
    if len(currencies) < 2:
        await query.answer("Сначала добавьте минимум две валюты.", show_alert=True)
        return
    await ui.show(
        query,
        state,
        "Выберите валюту, которую <b>отдаёт</b> клиент:",
        kb.choose_currency(currencies, action="pair_from", back_action="pairs"),
    )
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "pair_from"))
async def cb_pair_from(
    query: CallbackQuery, callback_data: AdminRateCB, session: AsyncSession, state: FSMContext
) -> None:
    await state.update_data(from_currency_id=callback_data.currency_id)
    currencies = [
        c
        for c in await exchange_service.list_currencies(session)
        if c.id != callback_data.currency_id
    ]
    await ui.show(
        query,
        state,
        "Выберите валюту, которую клиент <b>получает</b>:",
        kb.choose_currency(currencies, action="pair_to", back_action="pairs"),
    )
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "pair_to"))
async def cb_pair_to(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    data = await state.get_data()
    from_currency = await exchange_service.get_currency(
        session, int(data.get("from_currency_id", 0))
    )
    to_currency = await exchange_service.get_currency(session, callback_data.currency_id)
    if from_currency is None or to_currency is None:
        await query.answer("Валюта не найдена.", show_alert=True)
        return
    try:
        pair = await exchange_service.create_pair(
            session, from_currency=from_currency, to_currency=to_currency
        )
    except RateError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await _show_pair(query, state, pair, settings)
    await query.answer("Направление добавлено")


@router.callback_query(AdminRateCB.filter(F.action == "pair_rate"))
async def cb_pair_rate(
    query: CallbackQuery, callback_data: AdminRateCB, session: AsyncSession, state: FSMContext
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None:
        await query.answer("Направление не найдено.", show_alert=True)
        return
    await state.set_state(AdminRateSG.pair_rate)
    await state.update_data(pair_id=pair.id)
    await ui.show(
        query,
        state,
        f"✏️ <b>{pair.title}</b>\n\nПришлите курс: сколько <b>{pair.to_currency.code}</b> "
        f"за 1 <b>{pair.from_currency.code}</b>?\n"
        "Курс указывается финальный — комиссию бот не добавляет.",
        kb.back_to_main(),
    )
    await query.answer()


@router.message(AdminRateSG.pair_rate, F.text)
async def process_pair_rate(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    rate = parse_amount(message.text or "")
    if rate is None:
        await ui.show(message, state, "⚠️ Пришлите положительное число.", kb.back_to_main())
        return
    data = await state.get_data()
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    if pair is None:
        await ui.reset_flow(state)
        await ui.show(message, state, "⚠️ Направление не найдено.", kb.back_to_main())
        return
    await exchange_service.update_pair(session, pair, rate=rate)
    await _show_pair(message, state, pair, settings)


@router.callback_query(AdminRateCB.filter(F.action == "pair_auto"))
async def cb_pair_auto(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None:
        await query.answer("Направление не найдено.", show_alert=True)
        return
    await exchange_service.update_pair(session, pair, rate=None)
    await _show_pair(query, state, pair, settings)
    await query.answer("Курс снова считается автоматически")


@router.callback_query(AdminRateCB.filter(F.action == "pair_limits"))
async def cb_pair_limits(
    query: CallbackQuery, callback_data: AdminRateCB, state: FSMContext
) -> None:
    await state.set_state(AdminRateSG.pair_limits)
    await state.update_data(pair_id=callback_data.pair_id)
    await ui.show(
        query,
        state,
        "📏 <b>Лимиты направления</b>\n\n"
        "Пришлите два значения через пробел: <code>мин макс</code>.\n"
        "Например <code>100 100000</code>. Прочерк снимает ограничение: "
        "<code>100 -</code> или <code>- -</code>.",
        kb.back_to_main(),
    )
    await query.answer()


@router.message(AdminRateSG.pair_limits, F.text)
async def process_pair_limits(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2:
        await ui.show(message, state, "⚠️ Нужны два значения через пробел.", kb.back_to_main())
        return

    def _limit(raw: str) -> Decimal | None:
        return None if raw in {"-", "0"} else parse_amount(raw)

    minimum, maximum = _limit(parts[0]), _limit(parts[1])
    if minimum is not None and maximum is not None and minimum > maximum:
        await ui.show(
            message, state, "⚠️ Минимум не может быть больше максимума.", kb.back_to_main()
        )
        return

    data = await state.get_data()
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    if pair is None:
        await ui.reset_flow(state)
        await ui.show(message, state, "⚠️ Направление не найдено.", kb.back_to_main())
        return
    await exchange_service.update_pair(session, pair, min_amount=minimum, max_amount=maximum)
    await _show_pair(message, state, pair, settings)


@router.callback_query(AdminRateCB.filter(F.action == "pair_toggle"))
async def cb_pair_toggle(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None:
        await query.answer("Направление не найдено.", show_alert=True)
        return
    await exchange_service.toggle_pair(session, pair)
    await _show_pair(query, state, pair, settings)
    await query.answer("Включено" if pair.is_active else "Выключено")


@router.callback_query(AdminRateCB.filter(F.action == "pair_del"))
async def cb_pair_delete_ask(
    query: CallbackQuery, callback_data: AdminRateCB, session: AsyncSession, state: FSMContext
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None:
        await query.answer("Направление не найдено.", show_alert=True)
        return
    await ui.show(
        query,
        state,
        f"🗑 Удалить направление <b>{pair.title}</b>?",
        kb.confirm_delete(
            "pair_del", pair.id, back=AdminRateCB(action="pair_open", pair_id=pair.id)
        ),
    )
    await query.answer()


@router.callback_query(ConfirmCB.filter((F.scope == "pair_del") & (F.answer == "yes")))
async def cb_pair_delete(
    query: CallbackQuery, callback_data: ConfirmCB, session: AsyncSession, state: FSMContext
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.object_id)
    if pair is not None:
        await exchange_service.delete_pair(session, pair)
    await _show_pairs(query, session, state)
    await query.answer("Удалено")
