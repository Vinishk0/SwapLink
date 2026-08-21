"""All user-facing copy in one place (HTML parse mode).

There is no commission anywhere: the rate an admin sets is the final client
rate, and the only percentage a user ever sees is their referral discount.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from html import escape

from app.config import Settings
from app.db.models import Currency, Order, Pair, Transaction, User
from app.services.exchange import Quote
from app.services.referrals import ReferralSummary
from app.services.stats import Stats
from app.utils.format import format_amount, format_dt, format_money, format_percent

ZERO = Decimal("0")
SEP = "➖➖➖➖➖➖➖➖➖➖➖"


def plural(number: int, one: str, few: str, many: str) -> str:
    """Russian plural forms: 1 сделка, 2 сделки, 5 сделок."""
    if 11 <= number % 100 <= 14:
        return many
    last = number % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def deals_word(count: int) -> str:
    return plural(count, "сделку", "сделки", "сделок")


# --------------------------------------------------------------------------- #
# Onboarding
# --------------------------------------------------------------------------- #


def greeting(user: User, settings: Settings, *, bound_to: User | None = None) -> str:
    lines = [
        f"👋 <b>Привет, {escape(user.first_name or 'друг')}!</b>",
        "",
        "Это калькулятор обмена валют с реферальной программой.",
        "Рассчитайте сумму и оставьте заявку — сделку проводит оператор, бот только считает.",
    ]
    if bound_to is not None:
        limit = settings.referral_discount_limit
        lines += [
            "",
            f"🎁 Вы перешли по ссылке {bound_to.mention} — реферальная связь закреплена.",
            f"Ваша скидка <b>{format_percent(settings.referral_discount_percent)}</b> "
            f"действует на первые <b>{limit} {deals_word(limit)}</b>.",
        ]
    else:
        lines += [
            "",
            f"🎁 Приглашайте друзей и получайте "
            f"<b>{format_percent(settings.referral_bonus_percent)}</b> с каждой их сделки — "
            "без ограничения по времени.",
        ]
    lines += ["", "Выберите действие 👇"]
    return "\n".join(lines)


def help_text(settings: Settings) -> str:
    limit = settings.referral_discount_limit
    lines = [
        "ℹ️ <b>Как всё устроено</b>",
        "",
        "💱 <b>Расчёт обмена</b>",
        "Выберите направление и введите сумму — бот покажет курс и итог к получению. "
        "Курс уже финальный: никаких скрытых комиссий бот не добавляет. "
        "Заявку подтверждает оператор, деньги бот не переводит.",
        "",
        "🎁 <b>Реферальная программа</b>",
        f"• Приглашённый получает <b>{format_percent(settings.referral_discount_percent)}</b> "
        f"сверху к сумме обмена на первые <b>{limit} {deals_word(limit)}</b>.",
        f"• Пригласивший получает <b>{format_percent(settings.referral_bonus_percent)}</b> "
        "от объёма каждой сделки своего реферала — постоянно, без лимита.",
        "• Реферальная связь закрепляется навсегда при первом переходе по ссылке.",
        f"• Начисления копятся на балансе в {settings.base_currency}: их можно получить "
        "деньгами или зачесть в обмен — по согласованию с оператором.",
        "",
        "👤 <b>Личный кабинет</b> — ваша ссылка, статистика и история начислений.",
    ]
    if settings.support_username:
        lines += ["", f"💬 Вопросы: @{settings.support_username}"]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Exchange
# --------------------------------------------------------------------------- #


def choose_pair() -> str:
    return "💱 <b>Выберите направление обмена:</b>"


def no_pairs() -> str:
    return (
        "😔 Сейчас нет доступных направлений обмена.\n"
        "Загляните позже — оператор скоро добавит курсы."
    )


def ask_amount(pair: Pair, *, discount_percent: Decimal, discounts_left: int) -> str:
    lines = [
        f"💱 <b>{pair.title}</b>",
        f"Курс: <b>1 {pair.from_currency.code} = "
        f"{format_money(pair.effective_rate, 6)} {pair.to_currency.code}</b>",
    ]
    if discount_percent > ZERO:
        lines.append(
            f"🎁 Ваша реферальная скидка: <b>+{format_percent(discount_percent)}</b> "
            f"(осталось {discounts_left} {plural(discounts_left, 'сделка', 'сделки', 'сделок')})"
        )
    limits = []
    if pair.min_amount is not None:
        limits.append(f"от {format_money(pair.min_amount, pair.from_currency.decimals)}")
    if pair.max_amount is not None:
        limits.append(f"до {format_money(pair.max_amount, pair.from_currency.decimals)}")
    if limits:
        lines.append(f"Лимиты: {' '.join(limits)} {pair.from_currency.code}")
    lines += ["", f"Введите сумму в <b>{pair.from_currency.code}</b>:"]
    return "\n".join(lines)


def invalid_amount() -> str:
    return "⚠️ Не понял сумму. Введите положительное число, например <code>1000</code>."


def quote_text(quote: Quote, settings: Settings, *, discounts_left: int) -> str:
    lines = [
        f"💱 <b>{quote.from_code} → {quote.to_code}</b>",
        SEP,
        f"Отдаёте: <b>{format_amount(quote.amount_from, quote.from_code, quote.from_decimals)}</b>",
        f"Получаете: <b>{format_amount(quote.amount_to, quote.to_code, quote.to_decimals)}</b>",
        "",
        f"Курс: 1 {quote.from_code} = {format_money(quote.rate, 6)} {quote.to_code}",
    ]
    if quote.has_discount:
        lines += [
            f"🎁 Реферальная скидка <b>{format_percent(quote.discount_percent)}</b>: "
            f"<b>+{format_amount(quote.discount_amount, quote.to_code, quote.to_decimals)}</b> "
            "к получению",
            f"Осталось скидочных сделок: <b>{max(discounts_left - 1, 0)}</b> из "
            f"{settings.referral_discount_limit}",
        ]
    lines += [SEP, "Расчёт справочный: финальные условия подтверждает оператор."]
    return "\n".join(lines)


def discount_exhausted(settings: Settings) -> str:
    limit = settings.referral_discount_limit
    return (
        f"ℹ️ Вы уже использовали все <b>{limit} "
        f"{plural(limit, 'скидку', 'скидки', 'скидок')}</b> по реферальной программе — "
        "дальше обмен идёт по обычному курсу.\n\n"
        "Но ваша ссылка продолжает работать: приглашайте друзей и получайте "
        f"<b>{format_percent(settings.referral_bonus_percent)}</b> с каждой их сделки. 🎁"
    )


def _deal_amounts(order: Order) -> list[str]:
    """Money lines shared by every card that shows a deal."""
    lines = [
        f"Отдаёт: <b>{format_money(order.amount_from)} {order.from_code}</b>",
        f"Получает: <b>{format_money(order.amount_to, order.to_decimals)} {order.to_code}</b>",
    ]
    if order.discount_percent > ZERO:
        lines.append(
            f"🎁 Скидка реферала {format_percent(order.discount_percent)}: "
            f"+{format_money(order.discount_amount, order.to_decimals)} {order.to_code}"
        )
    if order.has_bonus_spent:
        lines.append(
            f"🎫 Зачтено с баланса: {format_money(order.bonus_spent, 4)} "
            f"(+{format_money(order.bonus_spent_to, order.to_decimals)} {order.to_code})"
        )
        lines.append(
            f"💰 Итого к выдаче: <b>{format_money(order.total_to, order.to_decimals)} "
            f"{order.to_code}</b>"
        )
    return lines


def order_created(order: Order, settings: Settings) -> str:
    lines = [
        f"✅ <b>Заявка #{order.id} создана</b>",
        SEP,
        f"Направление: <b>{order.direction}</b>",
        *_deal_amounts(order),
        SEP,
        "Оператор свяжется с вами для проведения обмена.",
    ]
    if settings.support_username:
        lines.append(f"💬 Связаться: @{settings.support_username}")
    return "\n".join(lines)


def order_card(order: Order) -> str:
    lines = [
        f"📋 <b>Заявка #{order.id}</b> · {order.status.title}",
        SEP,
        f"Направление: <b>{order.direction}</b>",
        *_deal_amounts(order),
        f"Курс: 1 {order.from_code} = {format_money(order.rate, 6)} {order.to_code}",
        f"Создана: {format_dt(order.created_at)}",
    ]
    if order.admin_comment:
        lines.append(f"💬 Комментарий оператора: {escape(order.admin_comment)}")
    return "\n".join(lines)


def orders_empty() -> str:
    return "📭 У вас пока нет заявок.\nНажмите «💱 Обмен», чтобы создать первую."


def orders_list(orders: Sequence[Order]) -> str:
    lines = ["📋 <b>Ваши заявки</b>", SEP]
    for order in orders:
        lines.append(
            f"{order.status.short_title} <b>#{order.id}</b> · {order.direction} · "
            f"{format_money(order.amount_from)} {order.from_code} · {format_dt(order.created_at)}"
        )
    lines += [SEP, "Выберите заявку, чтобы посмотреть детали."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Profile & referral programme
# --------------------------------------------------------------------------- #


def profile(user: User, summary: ReferralSummary, settings: Settings, ref_link: str) -> str:
    base = settings.base_currency
    lines = [
        "👤 <b>Личный кабинет</b>",
        SEP,
        f"ID: <code>{user.tg_id}</code>",
        f"Сделок проведено: <b>{user.deals_count}</b>",
        f"В программе с: {format_dt(user.created_at)}",
        "",
        "🎁 <b>Реферальная программа</b>",
        f"Приглашено: <b>{summary.referrals_count}</b> "
        f"{plural(summary.referrals_count, 'человек', 'человека', 'человек')} "
        f"(с обменами: {summary.active_referrals_count})",
        f"Всего заработано: <b>{format_money(summary.total_earned, 4)} {base}</b>",
        f"Доступно на балансе: <b>{format_money(summary.balance, 4)} {base}</b>",
        f"Получено/зачтено: <b>{format_money(summary.paid_out, 4)} {base}</b>",
    ]
    if summary.last_bonus_at:
        lines.append(f"Последнее начисление: {format_dt(summary.last_bonus_at)}")

    lines += ["", "🔗 <b>Ваша реферальная ссылка</b>", f"<code>{escape(ref_link)}</code>"]

    if summary.is_referral and summary.referrer is not None:
        lines += [
            "",
            SEP,
            f"🤝 <b>Вас пригласил:</b> {summary.referrer.mention}",
            "Заработок пригласившего по программе: "
            f"<b>{format_money(summary.referrer_total_earned, 4)} {base}</b>",
        ]
        if summary.discounts_left > 0:
            lines.append(
                f"Ваша скидка <b>{format_percent(settings.referral_discount_percent)}</b> "
                f"действует ещё на <b>{summary.discounts_left}</b> "
                f"{deals_word(summary.discounts_left)} "
                f"(использовано {summary.discounts_used} из {summary.discount_limit})"
            )
        else:
            lines.append(
                f"Скидки по программе исчерпаны ({summary.discounts_used} из "
                f"{summary.discount_limit}) — обмен по обычному курсу, "
                "но ваша ссылка продолжает приносить доход."
            )
    return "\n".join(lines)


def referral_program(summary: ReferralSummary, settings: Settings, ref_link: str) -> str:
    base = settings.base_currency
    limit = settings.referral_discount_limit
    lines = [
        "🎁 <b>Реферальная программа</b>",
        SEP,
        "• Друг переходит по вашей ссылке и навсегда закрепляется за вами.",
        f"• Приглашённый получает <b>{format_percent(settings.referral_discount_percent)}</b> "
        f"сверху к обмену на первые <b>{limit} {deals_word(limit)}</b>.",
        f"• Вы получаете <b>{format_percent(settings.referral_bonus_percent)}</b> "
        "от объёма <b>каждой</b> сделки приглашённого — бессрочно.",
        SEP,
        f"Приглашено: <b>{summary.referrals_count}</b> · "
        f"заработано: <b>{format_money(summary.total_earned, 4)} {base}</b> · "
        f"баланс: <b>{format_money(summary.balance, 4)} {base}</b>",
        "",
        "🔗 <b>Ваша ссылка:</b>",
        f"<code>{escape(ref_link)}</code>",
    ]
    if summary.is_referral and summary.referrer is not None:
        lines += ["", f"🤝 Вас пригласил: {summary.referrer.mention}"]
    return "\n".join(lines)


def ask_ref_code() -> str:
    return (
        "🎟 <b>Ввод реферального кода</b>\n\n"
        "Пришлите реферальную ссылку или код друга — например <code>ABCD2345</code>.\n\n"
        "⚠️ Сделать это можно только <b>до первой проведённой сделки</b>, "
        "и сменить реферера потом нельзя."
    )


def ref_code_applied(referrer: User, settings: Settings) -> str:
    limit = settings.referral_discount_limit
    return (
        f"✅ Готово! Теперь вы реферал {referrer.mention}.\n\n"
        f"🎁 Скидка <b>{format_percent(settings.referral_discount_percent)}</b> "
        f"действует на первые <b>{limit} {deals_word(limit)}</b>."
    )


def referrals_list(
    rows: Sequence[tuple[User, Decimal]],
    settings: Settings,
    *,
    total: int = 0,
    page: int = 1,
    per_page: int = 8,
    owner: User | None = None,
) -> str:
    """One page of referrals with what each of them earned for the inviter."""
    if not rows:
        if total:
            return f"👥 На этой странице пусто — всего рефералов: {total}."
        who = "У этого пользователя" if owner is not None else "У вас"
        return (
            f"👥 {who} пока нет рефералов.\n\n"
            "Поделитесь ссылкой из личного кабинета — и начисления пойдут "
            "с каждой сделки приглашённых."
        )

    base = settings.base_currency
    earned_total = sum((earned for _, earned in rows), ZERO)
    start = (max(page, 1) - 1) * per_page
    lines = [f"👥 <b>Рефералы</b> · всего: {total}", SEP]
    for index, (user, earned) in enumerate(rows, start=start + 1):
        lines.append(
            f"{index}. {user.mention} — сделок: <b>{user.deals_count}</b>, "
            f"заработано: <b>{format_money(earned, 4)} {base}</b>"
        )
    lines += [
        SEP,
        f"На этой странице: <b>{format_money(earned_total, 4)} {base}</b> · "
        f"начисление {format_percent(settings.referral_bonus_percent)} с каждой сделки.",
    ]
    return "\n".join(lines)


def history(transactions: Sequence[Transaction], settings: Settings, *, total: int = 0) -> str:
    if not transactions:
        if total:
            return f"💸 На этой странице пусто — всего операций: {total}."
        return "💸 Операций по балансу пока не было."
    base = settings.base_currency
    header = "💸 <b>История операций</b>"
    if total:
        header += f" · всего: {total}"
    lines = [header, SEP]
    for tx in transactions:
        sign = "+" if tx.amount > ZERO else ""
        who = f" · от {escape(tx.source_user.full_name)}" if tx.source_user is not None else ""
        lines.append(
            f"{format_dt(tx.created_at)} · <b>{sign}{format_money(tx.amount, 4)} {base}</b> · "
            f"{tx.type.title}{who}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #


def notify_order_confirmed(order: Order, settings: Settings) -> str:
    lines = [
        f"✅ <b>Заявка #{order.id} проведена</b>",
        SEP,
        f"{order.direction}",
        *_deal_amounts(order),
    ]
    if order.admin_comment:
        lines.append(f"💬 {escape(order.admin_comment)}")
    lines += ["", "Спасибо, что выбрали нас!"]
    return "\n".join(lines)


def notify_discount_limit_reached(settings: Settings) -> str:
    limit = settings.referral_discount_limit
    return (
        f"ℹ️ Это была ваша <b>{limit}-я</b> сделка со скидкой. "
        "Следующие обмены пройдут по обычному курсу.\n\n"
        "Ваша реферальная ссылка продолжает работать: "
        f"{format_percent(settings.referral_bonus_percent)} с каждой сделки приглашённых. 🎁"
    )


def notify_order_rejected(order: Order) -> str:
    lines = [f"❌ <b>Заявка #{order.id} отклонена</b>", f"{order.direction}"]
    if order.admin_comment:
        lines.append(f"💬 Причина: {escape(order.admin_comment)}")
    lines.append("Вы можете создать новую заявку в любой момент.")
    return "\n".join(lines)


def notify_referral_bonus(
    order: Order, referral: User, amount: Decimal, balance: Decimal, settings: Settings
) -> str:
    base = settings.base_currency
    return (
        "🎉 <b>Начисление по реферальной программе</b>\n"
        f"{SEP}\n"
        f"Ваш реферал {referral.mention} провёл обмен {order.direction}.\n"
        f"Начислено: <b>+{format_money(amount, 4)} {base}</b>\n"
        f"Баланс: <b>{format_money(balance, 4)} {base}</b>"
    )


def notify_payout(
    amount: Decimal, balance: Decimal, settings: Settings, comment: str | None
) -> str:
    base = settings.base_currency
    lines = [
        "💸 <b>Выплата с реферального баланса</b>",
        f"Сумма: <b>{format_money(amount, 4)} {base}</b>",
        f"Остаток на балансе: <b>{format_money(balance, 4)} {base}</b>",
    ]
    if comment:
        lines.append(f"💬 {escape(comment)}")
    return "\n".join(lines)


def notify_discount_granted(
    amount: Decimal, balance: Decimal, settings: Settings, comment: str | None
) -> str:
    base = settings.base_currency
    lines = [
        "🎫 <b>Бонусы зачтены в обмен</b>",
        f"Списано с баланса: <b>{format_money(amount, 4)} {base}</b>",
        f"Остаток: <b>{format_money(balance, 4)} {base}</b>",
    ]
    if comment:
        lines.append(f"💬 {escape(comment)}")
    return "\n".join(lines)


def notify_adjustment(
    amount: Decimal, balance: Decimal, settings: Settings, comment: str | None
) -> str:
    base = settings.base_currency
    sign = "+" if amount > ZERO else ""
    lines = [
        "✏️ <b>Баланс скорректирован оператором</b>",
        f"Изменение: <b>{sign}{format_money(amount, 4)} {base}</b>",
        f"Текущий баланс: <b>{format_money(balance, 4)} {base}</b>",
    ]
    if comment:
        lines.append(f"💬 {escape(comment)}")
    return "\n".join(lines)


def notify_new_referral(referral: User, settings: Settings) -> str:
    return (
        "🤝 <b>У вас новый реферал!</b>\n"
        f"По вашей ссылке зарегистрирован: {referral.mention}\n\n"
        f"С каждой сделки этого пользователя вам начисляется "
        f"<b>{format_percent(settings.referral_bonus_percent)}</b> "
        f"от объёма в {settings.base_currency}."
    )


def notify_user_blocked(blocked: bool) -> str:
    return (
        "🚫 Ваш аккаунт заблокирован администратором."
        if blocked
        else "🔓 Ваш аккаунт снова активен."
    )


def blocked_notice() -> str:
    return "🚫 Ваш аккаунт заблокирован. Обратитесь к оператору."


# --------------------------------------------------------------------------- #
# Admin panel
# --------------------------------------------------------------------------- #


def admin_main(stats: Stats, settings: Settings) -> str:
    return (
        "🛠 <b>Админ-панель</b>\n"
        f"{SEP}\n"
        f"Пользователей: <b>{stats.users_total}</b> (+{stats.users_new_24h} за сутки)\n"
        f"Заявок в ожидании: <b>{stats.orders_pending}</b>\n"
        f"К выплате рефералам: <b>{format_money(stats.balances_outstanding, 4)} "
        f"{settings.base_currency}</b>"
    )


def admin_stats(stats: Stats, settings: Settings) -> str:
    base = settings.base_currency
    return "\n".join(
        [
            "📊 <b>Статистика</b>",
            SEP,
            f"👥 Пользователей: <b>{stats.users_total}</b> (+{stats.users_new_24h} за 24 ч)",
            f"🤝 Из них рефералов: <b>{stats.referrals_total}</b>",
            "",
            f"📋 Заявок всего: <b>{stats.orders_total}</b>",
            f"⏳ В ожидании: <b>{stats.orders_pending}</b>",
            f"✅ Проведено: <b>{stats.orders_confirmed}</b>",
            f"💰 Объём проведённых: <b>{format_money(stats.volume_base, 2)} {base}</b>",
            "",
            f"🎁 Начислено рефералам: <b>{format_money(stats.bonuses_accrued, 4)} {base}</b>",
            f"💸 Выплачено/зачтено: <b>{format_money(stats.bonuses_paid, 4)} {base}</b>",
            f"🏦 Остаток на балансах: <b>{format_money(stats.balances_outstanding, 4)} {base}</b>",
        ]
    )


def admin_user_card(
    user: User, summary: ReferralSummary, settings: Settings, *, referrals_count: int
) -> str:
    base = settings.base_currency
    lines = [
        f"👤 <b>{escape(user.full_name)}</b>",
        SEP,
        f"Telegram ID: <code>{user.tg_id}</code>",
        f"Username: {'@' + escape(user.username) if user.username else '—'}",
        f"Реф. код: <code>{user.ref_code}</code>",
        f"Регистрация: {format_dt(user.created_at)}",
        f"Статус: {'🚫 заблокирован' if user.is_blocked else '✅ активен'}",
        "",
        f"Сделок: <b>{user.deals_count}</b> · скидок использовано: "
        f"<b>{user.discounts_used}/{settings.referral_discount_limit}</b>",
        f"Рефералов: <b>{referrals_count}</b>",
        "",
        f"💰 Баланс: <b>{format_money(user.balance, 4)} {base}</b>",
        f"🎁 Всего заработано: <b>{format_money(user.total_earned, 4)} {base}</b>",
        f"💸 Выплачено/зачтено: <b>{format_money(user.total_paid_out, 4)} {base}</b>",
    ]
    if summary.referrer is not None:
        lines += [
            "",
            f"🤝 Пригласил: {summary.referrer.mention} "
            f"(<code>{summary.referrer.tg_id}</code>, код "
            f"<code>{summary.referrer.ref_code}</code>)",
        ]
    return "\n".join(lines)


def admin_order_card(order: Order, settings: Settings, *, is_new: bool = False) -> str:
    base = settings.base_currency
    header = (
        f"🔔 <b>Новая заявка #{order.id}</b>"
        if is_new
        else f"📋 <b>Заявка #{order.id}</b> · {order.status.title}"
    )
    lines = [
        header,
        SEP,
        f"Клиент: {order.user.mention} (<code>{order.user.tg_id}</code>)",
        f"Направление: <b>{order.direction}</b>",
        *_deal_amounts(order),
        f"Курс: 1 {order.from_code} = {format_money(order.rate, 6)} {order.to_code}",
        f"Объём: <b>{format_money(order.volume_base, 2)} {base}</b>",
        f"Создана: {format_dt(order.created_at)}",
    ]
    if order.referrer is not None:
        lines.append(
            f"🤝 Реферер: {order.referrer.mention} · бонус "
            f"<b>{format_money(order.bonus_amount, 4)} {base}</b>"
        )
    if order.processed_at:
        lines.append(f"Обработана: {format_dt(order.processed_at)} (админ {order.admin_id})")
    if order.admin_comment:
        lines.append(f"💬 {escape(order.admin_comment)}")
    return "\n".join(lines)


def admin_currency_card(currency: Currency, settings: Settings) -> str:
    return "\n".join(
        [
            f"🪙 <b>{currency.code}</b> — {escape(currency.name)}",
            SEP,
            f"1 {currency.code} = <b>{format_money(currency.rate_to_base, 8)} "
            f"{settings.base_currency}</b>",
            f"Знаков после запятой: {currency.decimals}",
            f"Статус: {'✅ активна' if currency.is_active else '⛔️ выключена'}",
        ]
    )


def admin_pair_card(pair: Pair, settings: Settings) -> str:
    rate_source = "задан вручную" if pair.is_manual_rate else "авто (из курсов валют)"
    return "\n".join(
        [
            f"🔁 <b>{pair.title}</b>",
            SEP,
            f"Курс: <b>1 {pair.from_currency.code} = "
            f"{format_money(pair.effective_rate, 8)} {pair.to_currency.code}</b> ({rate_source})",
            f"Лимиты: {format_money(pair.min_amount) if pair.min_amount else '—'} … "
            f"{format_money(pair.max_amount) if pair.max_amount else '—'} "
            f"{pair.from_currency.code}",
            f"Статус: {'✅ активно' if pair.is_active else '⛔️ выключено'}",
            "",
            "Курс указывается финальный, с вашей маржой — бот комиссию не добавляет. "
            f"Рефералам начисляется {format_percent(settings.referral_discount_percent)} "
            "сверху к сумме.",
        ]
    )
