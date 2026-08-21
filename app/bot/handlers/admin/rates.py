"""Admin: currencies, cross-rates, exchange directions and commissions."""

from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import texts
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


async def _show_currencies(message: Message, session: AsyncSession, *, edit: bool = True) -> None:
    currencies = await exchange_service.list_currencies(session)
    text = (
        "🪙 <b>Валюты</b>\n"
        "Курс указывается к базовой валюте — из него считаются кросс-курсы "
        "и объём сделки для реферальных начислений."
    )
    markup = kb.currencies_list(currencies)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def _show_pairs(message: Message, session: AsyncSession, *, edit: bool = True) -> None:
    pairs = await exchange_service.list_pairs(session)
    text = "🔁 <b>Направления обмена</b>\nВыберите направление, чтобы изменить курс или комиссию."
    markup = kb.pairs_list(pairs)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(AdminCB.filter(F.section == "rates"))
async def cb_rates(query: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        await query.message.edit_text("💱 <b>Курсы и направления</b>", reply_markup=kb.rates_menu())
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "currencies"))
async def cb_currencies(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        await _show_currencies(query.message, session)
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "pairs"))
async def cb_pairs(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    if isinstance(query.message, Message):
        await _show_pairs(query.message, session)
    await query.answer()


# --------------------------------------------------------------------------- #
# Currencies
# --------------------------------------------------------------------------- #


@router.callback_query(AdminRateCB.filter(F.action == "cur_open"))
async def cb_currency_open(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    currency = await exchange_service.get_currency(session, callback_data.currency_id)
    if currency is None:
        await query.answer("Валюта не найдена.", show_alert=True)
        return
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.admin_currency_card(currency, settings), reply_markup=kb.currency_card(currency)
        )
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "cur_add"))
async def cb_currency_add(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminRateSG.currency_code)
    if isinstance(query.message, Message):
        await query.message.answer("🪙 Пришлите код валюты, например <code>USDT</code>:")
    await query.answer()


@router.message(AdminRateSG.currency_code, F.text)
async def process_currency_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    if not code.isalnum() or len(code) > 16:
        await message.answer("⚠️ Код должен быть буквенно-цифровым, до 16 символов.")
        return
    await state.update_data(code=code)
    await state.set_state(AdminRateSG.currency_name)
    await message.answer(f"Название для <b>{code}</b> (например «Доллар США»):")


@router.message(AdminRateSG.currency_name, F.text)
async def process_currency_name(message: Message, state: FSMContext, settings: Settings) -> None:
    await state.update_data(name=(message.text or "").strip()[:64])
    await state.set_state(AdminRateSG.currency_rate)
    data = await state.get_data()
    await message.answer(
        f"Курс: сколько <b>{settings.base_currency}</b> стоит 1 "
        f"<b>{data['code']}</b>?\nНапример: <code>1</code> или <code>0.0107</code>"
    )


@router.message(AdminRateSG.currency_rate, F.text)
async def process_currency_rate(message: Message, session: AsyncSession, state: FSMContext) -> None:
    rate = parse_amount(message.text or "")
    if rate is None:
        await message.answer("⚠️ Пришлите положительное число.")
        return
    data = await state.get_data()
    try:
        currency = await exchange_service.create_currency(
            session, code=data["code"], name=data.get("name", data["code"]), rate_to_base=rate
        )
    except RateError as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    await message.answer(f"✅ Валюта <b>{currency.code}</b> добавлена.")
    await _show_currencies(message, session, edit=False)


@router.callback_query(AdminRateCB.filter(F.action == "cur_rate"))
async def cb_currency_rate(
    query: CallbackQuery, callback_data: AdminRateCB, state: FSMContext, settings: Settings
) -> None:
    await state.set_state(AdminRateSG.currency_edit_rate)
    await state.update_data(currency_id=callback_data.currency_id)
    if isinstance(query.message, Message):
        await query.message.answer(f"Пришлите новый курс к <b>{settings.base_currency}</b>:")
    await query.answer()


@router.message(AdminRateSG.currency_edit_rate, F.text)
async def process_currency_edit_rate(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    rate = parse_amount(message.text or "")
    if rate is None:
        await message.answer("⚠️ Пришлите положительное число.")
        return
    data = await state.get_data()
    currency = await exchange_service.get_currency(session, int(data.get("currency_id", 0)))
    if currency is None:
        await state.clear()
        await message.answer("⚠️ Валюта не найдена.")
        return
    await exchange_service.update_currency_rate(session, currency, rate)
    await state.clear()
    await message.answer(
        texts.admin_currency_card(currency, settings), reply_markup=kb.currency_card(currency)
    )


@router.callback_query(AdminRateCB.filter(F.action == "cur_toggle"))
async def cb_currency_toggle(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    currency = await exchange_service.get_currency(session, callback_data.currency_id)
    if currency is None:
        await query.answer("Валюта не найдена.", show_alert=True)
        return
    await exchange_service.toggle_currency(session, currency)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.admin_currency_card(currency, settings), reply_markup=kb.currency_card(currency)
        )
    await query.answer("Включена" if currency.is_active else "Выключена")


@router.callback_query(AdminRateCB.filter(F.action == "cur_del"))
async def cb_currency_delete_ask(
    query: CallbackQuery, callback_data: AdminRateCB, session: AsyncSession
) -> None:
    currency = await exchange_service.get_currency(session, callback_data.currency_id)
    if currency is None:
        await query.answer("Валюта не найдена.", show_alert=True)
        return
    if isinstance(query.message, Message):
        await query.message.edit_text(
            f"🗑 Удалить валюту <b>{currency.code}</b>?\n"
            "Все направления с этой валютой тоже будут удалены.",
            reply_markup=kb.confirm_delete(
                "cur_del",
                currency.id,
                back=AdminRateCB(action="cur_open", currency_id=currency.id),
            ),
        )
    await query.answer()


@router.callback_query(ConfirmCB.filter((F.scope == "cur_del") & (F.answer == "yes")))
async def cb_currency_delete(
    query: CallbackQuery, callback_data: ConfirmCB, session: AsyncSession
) -> None:
    currency = await exchange_service.get_currency(session, callback_data.object_id)
    if currency is not None:
        await exchange_service.delete_currency(session, currency)
    if isinstance(query.message, Message):
        await _show_currencies(query.message, session)
    await query.answer("Удалено")


# --------------------------------------------------------------------------- #
# Pairs
# --------------------------------------------------------------------------- #


@router.callback_query(AdminRateCB.filter(F.action == "pair_open"))
async def cb_pair_open(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None:
        await query.answer("Направление не найдено.", show_alert=True)
        return
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.admin_pair_card(pair, settings), reply_markup=kb.pair_card(pair)
        )
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "pair_add"))
async def cb_pair_add(query: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    currencies = await exchange_service.list_currencies(session)
    if len(currencies) < 2:
        await query.answer("Сначала добавьте минимум две валюты.", show_alert=True)
        return
    if isinstance(query.message, Message):
        await query.message.edit_text(
            "Выберите валюту, которую <b>отдаёт</b> клиент:",
            reply_markup=kb.choose_currency(currencies, action="pair_from", back_action="pairs"),
        )
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "pair_from"))
async def cb_pair_from(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    await state.update_data(from_currency_id=callback_data.currency_id)
    currencies = [
        c
        for c in await exchange_service.list_currencies(session)
        if c.id != callback_data.currency_id
    ]
    if isinstance(query.message, Message):
        await query.message.edit_text(
            "Выберите валюту, которую клиент <b>получает</b>:",
            reply_markup=kb.choose_currency(currencies, action="pair_to", back_action="pairs"),
        )
    await query.answer()


@router.callback_query(AdminRateCB.filter(F.action == "pair_to"))
async def cb_pair_to(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    state: FSMContext,
    settings: Settings,
) -> None:
    await state.update_data(to_currency_id=callback_data.currency_id)
    await state.set_state(AdminRateSG.pair_new_commission)
    if isinstance(query.message, Message):
        await query.message.answer(
            "Пришлите комиссию обменника в процентах для этого направления, "
            f"например <code>{settings.default_commission_percent}</code>:"
        )
    await query.answer()


@router.message(AdminRateSG.pair_new_commission, F.text)
async def process_pair_commission_new(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    commission = parse_amount(message.text or "")
    if commission is None or commission >= 100:
        await message.answer("⚠️ Комиссия должна быть числом от 0 до 100.")
        return
    data = await state.get_data()
    from_currency = await exchange_service.get_currency(session, int(data["from_currency_id"]))
    to_currency = await exchange_service.get_currency(session, int(data["to_currency_id"]))
    if from_currency is None or to_currency is None:
        await state.clear()
        await message.answer("⚠️ Валюта не найдена.")
        return
    try:
        pair = await exchange_service.create_pair(
            session,
            from_currency=from_currency,
            to_currency=to_currency,
            commission_percent=commission,
        )
    except RateError as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    await message.answer(
        f"✅ Направление <b>{pair.title}</b> добавлено.\n"
        "Курс считается автоматически из курсов валют — при необходимости задайте его вручную.",
    )
    await message.answer(texts.admin_pair_card(pair, settings), reply_markup=kb.pair_card(pair))


@router.callback_query(AdminRateCB.filter(F.action == "pair_comm"))
async def cb_pair_commission(
    query: CallbackQuery, callback_data: AdminRateCB, state: FSMContext
) -> None:
    await state.set_state(AdminRateSG.pair_commission)
    await state.update_data(pair_id=callback_data.pair_id)
    if isinstance(query.message, Message):
        await query.message.answer("Пришлите новую комиссию в процентах:")
    await query.answer()


@router.message(AdminRateSG.pair_commission, F.text)
async def process_pair_commission(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    commission = parse_amount(message.text or "")
    if commission is None or commission >= 100:
        await message.answer("⚠️ Комиссия должна быть числом от 0 до 100.")
        return
    data = await state.get_data()
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    if pair is None:
        await state.clear()
        await message.answer("⚠️ Направление не найдено.")
        return
    await exchange_service.update_pair(session, pair, commission_percent=commission)
    await state.clear()
    await message.answer(texts.admin_pair_card(pair, settings), reply_markup=kb.pair_card(pair))


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
    if isinstance(query.message, Message):
        await query.message.answer(
            f"Пришлите курс: сколько <b>{pair.to_currency.code}</b> за 1 "
            f"<b>{pair.from_currency.code}</b>?"
        )
    await query.answer()


@router.message(AdminRateSG.pair_rate, F.text)
async def process_pair_rate(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    rate = parse_amount(message.text or "")
    if rate is None:
        await message.answer("⚠️ Пришлите положительное число.")
        return
    data = await state.get_data()
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    if pair is None:
        await state.clear()
        await message.answer("⚠️ Направление не найдено.")
        return
    await exchange_service.update_pair(session, pair, rate=rate)
    await state.clear()
    await message.answer(texts.admin_pair_card(pair, settings), reply_markup=kb.pair_card(pair))


@router.callback_query(AdminRateCB.filter(F.action == "pair_auto"))
async def cb_pair_auto(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None:
        await query.answer("Направление не найдено.", show_alert=True)
        return
    await exchange_service.update_pair(session, pair, rate=None)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.admin_pair_card(pair, settings), reply_markup=kb.pair_card(pair)
        )
    await query.answer("Курс снова считается автоматически")


@router.callback_query(AdminRateCB.filter(F.action == "pair_limits"))
async def cb_pair_limits(
    query: CallbackQuery, callback_data: AdminRateCB, state: FSMContext
) -> None:
    await state.set_state(AdminRateSG.pair_limits)
    await state.update_data(pair_id=callback_data.pair_id)
    if isinstance(query.message, Message):
        await query.message.answer(
            "Пришлите лимиты через пробел: <code>мин макс</code>.\n"
            "Например <code>100 100000</code>. Прочерк снимает ограничение: "
            "<code>100 -</code> или <code>- -</code>."
        )
    await query.answer()


@router.message(AdminRateSG.pair_limits, F.text)
async def process_pair_limits(
    message: Message, session: AsyncSession, state: FSMContext, settings: Settings
) -> None:
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("⚠️ Нужны два значения через пробел.")
        return

    def _limit(raw: str) -> Decimal | None:
        return None if raw in {"-", "0"} else parse_amount(raw)

    minimum, maximum = _limit(parts[0]), _limit(parts[1])
    if minimum is not None and maximum is not None and minimum > maximum:
        await message.answer("⚠️ Минимум не может быть больше максимума.")
        return

    data = await state.get_data()
    pair = await exchange_service.get_pair(session, int(data.get("pair_id", 0)))
    if pair is None:
        await state.clear()
        await message.answer("⚠️ Направление не найдено.")
        return
    await exchange_service.update_pair(session, pair, min_amount=minimum, max_amount=maximum)
    await state.clear()
    await message.answer(texts.admin_pair_card(pair, settings), reply_markup=kb.pair_card(pair))


@router.callback_query(AdminRateCB.filter(F.action == "pair_toggle"))
async def cb_pair_toggle(
    query: CallbackQuery,
    callback_data: AdminRateCB,
    session: AsyncSession,
    settings: Settings,
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None:
        await query.answer("Направление не найдено.", show_alert=True)
        return
    await exchange_service.toggle_pair(session, pair)
    if isinstance(query.message, Message):
        await query.message.edit_text(
            texts.admin_pair_card(pair, settings), reply_markup=kb.pair_card(pair)
        )
    await query.answer("Включено" if pair.is_active else "Выключено")


@router.callback_query(AdminRateCB.filter(F.action == "pair_del"))
async def cb_pair_delete_ask(
    query: CallbackQuery, callback_data: AdminRateCB, session: AsyncSession
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.pair_id)
    if pair is None:
        await query.answer("Направление не найдено.", show_alert=True)
        return
    if isinstance(query.message, Message):
        await query.message.edit_text(
            f"🗑 Удалить направление <b>{pair.title}</b>?",
            reply_markup=kb.confirm_delete(
                "pair_del", pair.id, back=AdminRateCB(action="pair_open", pair_id=pair.id)
            ),
        )
    await query.answer()


@router.callback_query(ConfirmCB.filter((F.scope == "pair_del") & (F.answer == "yes")))
async def cb_pair_delete(
    query: CallbackQuery, callback_data: ConfirmCB, session: AsyncSession
) -> None:
    pair = await exchange_service.get_pair(session, callback_data.object_id)
    if pair is not None:
        await exchange_service.delete_pair(session, pair)
    if isinstance(query.message, Message):
        await _show_pairs(query.message, session)
    await query.answer("Удалено")
