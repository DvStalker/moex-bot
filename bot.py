import os
import requests
from datetime import datetime, timedelta, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TINVEST_TOKEN = os.environ["TINVEST_TOKEN"]

ACCOUNT_NAME = "Очень долгий срок"
PURCHASE_DATE = datetime(2025, 12, 8, tzinfo=timezone.utc)
MONTHLY_INVEST = 60_000
YEARS = 17
INFLATION = 0.07

TINVEST = "https://invest-public-api.tinkoff.ru/rest"
HEADERS = {"Authorization": f"Bearer {TINVEST_TOKEN}", "Content-Type": "application/json"}


def ti_post(path, body=None):
    r = requests.post(f"{TINVEST}{path}", headers=HEADERS, json=body or {}, timeout=15)
    r.raise_for_status()
    return r.json()


def moneyval(mv):
    if not mv:
        return 0.0
    return int(mv.get("units", 0)) + int(mv.get("nano", 0)) / 1e9


def quotation(q):
    if not q:
        return 0.0
    return int(q.get("units", 0)) + int(q.get("nano", 0)) / 1e9


def get_account_id():
    data = ti_post("/tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts")
    for acc in data.get("accounts", []):
        if acc.get("name") == ACCOUNT_NAME:
            return acc["id"]
    names = [a.get("name") for a in data.get("accounts", [])]
    raise Exception(f"Счёт '{ACCOUNT_NAME}' не найден. Доступные: {names}")


def get_portfolio(account_id):
    return ti_post(
        "/tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
        {"accountId": account_id}
    )


def get_instrument_info(figi):
    try:
        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.InstrumentsService/GetInstrumentBy",
            {"idType": "ID_TYPE_FIGI", "id": figi}
        )
        inst = data.get("instrument", {})
        return inst.get("name") or inst.get("ticker") or figi, inst.get("instrumentKind", "")
    except Exception:
        return figi, ""


def get_candles_week(figi):
    try:
        now = datetime.now(timezone.utc)
        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles",
            {
                "figi": figi,
                "from": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "interval": "CANDLE_INTERVAL_DAY"
            }
        )
        candles = data.get("candles", [])
        if len(candles) >= 2:
            return quotation(candles[0].get("close")), quotation(candles[-1].get("close"))
    except Exception:
        pass
    return None, None


def get_historical_cagr(figi):
    """
    Пробуем получить CAGR в порядке: месячные → недельные → дневные свечи.
    Берём максимально доступный период, минимум 2 точки.
    """
    now = datetime.now(timezone.utc)

    intervals = [
        ("CANDLE_INTERVAL_MONTH",  365 * 17, 12.0),   # месячные, делитель 12
        ("CANDLE_INTERVAL_WEEK",   365 * 5,  52.0),    # недельные, делитель 52
        ("CANDLE_INTERVAL_DAY",    365 * 3,  365.0),   # дневные, делитель 365
    ]

    for interval, days_back, per_year in intervals:
        try:
            from_date = now - timedelta(days=days_back)
            data = ti_post(
                "/tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles",
                {
                    "figi": figi,
                    "from": from_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "interval": interval
                }
            )
            candles = data.get("candles", [])
            if len(candles) < 2:
                continue

            p_first = quotation(candles[0].get("close"))
            p_last  = quotation(candles[-1].get("close"))
            if not p_first or not p_last or p_first <= 0:
                continue

            years_avail = len(candles) / per_year
            if years_avail < 0.05:  # меньше ~18 дней — не считаем
                continue

            price_cagr = (p_last / p_first) ** (1 / years_avail) - 1
            return price_cagr, round(years_avail, 1)
        except Exception:
            continue

    return None, None


def pension_forecast(annual_rate, current_portfolio_value=0):
    """
    Считает итоговую сумму через YEARS лет при пополнении MONTHLY_INVEST/мес.
    Формула аннуитета с реинвестированием + уже накопленное.
    Возвращает (номинал, реальная стоимость в ценах сегодня).
    """
    monthly_rate = annual_rate / 12
    months = YEARS * 12

    # Будущая стоимость пополнений (аннуитет)
    if monthly_rate > 0:
        fv_contributions = MONTHLY_INVEST * ((1 + monthly_rate) ** months - 1) / monthly_rate
    else:
        fv_contributions = MONTHLY_INVEST * months

    # Рост текущего портфеля
    fv_existing = current_portfolio_value * (1 + annual_rate) ** YEARS

    nominal = fv_contributions + fv_existing

    # Реальная стоимость с учётом инфляции 7%
    real = nominal / (1 + INFLATION) ** YEARS

    return nominal, real


def get_dividends_and_coupons(account_id):
    total = 0.0
    try:
        now = datetime.now(timezone.utc)
        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.OperationsService/GetOperations",
            {
                "accountId": account_id,
                "from": datetime(2000, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "state": "OPERATION_STATE_EXECUTED"
            }
        )
        INCOME_TYPES = {
            "OPERATION_TYPE_DIVIDEND",
            "OPERATION_TYPE_COUPON",
            "OPERATION_TYPE_BOND_REPAYMENT_FULL",
        }
        for op in data.get("operations", []):
            if op.get("operationType", "") in INCOME_TYPES:
                total += moneyval(op.get("payment"))
    except Exception as e:
        print(f"Ошибка выплат: {e}")
    return total


def ai_analysis(name, current_price, avg_price, week_pct, since_pct):
    if not ANTHROPIC_API_KEY:
        return None
    since_txt = f"{since_pct:+.2f}% с момента покупки" if avg_price else "нет данных"
    prompt = (
        f'Напиши анализ для домохозяйки (3 простых предложения) про "{name}".\n'
        f"Цена: {current_price:.2f}₽. За неделю: {week_pct:+.2f}%. {since_txt}.\n"
        f"1) что происходит с ценой, 2) хорошо это или плохо, 3) держать/докупить/следить.\n"
        f"Пиши очень просто, без терминов, как подруге. Только текст."
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 200,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()
    except Exception as e:
        return f"Анализ недоступен: {e}"


def fmt(n):
    """Форматировать большое число в млн/тыс."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f} млн ₽"
    return f"{n:,.0f} ₽"


def build_report():
    lines = [
        "📊 *Еженедельный отчёт по портфелю*",
        f"📅 {datetime.now().strftime('%d.%m.%Y')}  |  Счёт: {ACCOUNT_NAME}\n"
    ]

    try:
        account_id = get_account_id()
        portfolio = get_portfolio(account_id)
        dividends_total = get_dividends_and_coupons(account_id)
    except Exception as e:
        lines.append(f"⚠️ Ошибка: {e}")
        return "\n".join(lines)

    positions = portfolio.get("positions", [])
    if not positions:
        lines.append("Портфель пуст.")
        return "\n".join(lines)

    total_now = moneyval(portfolio.get("totalAmountPortfolio"))
    total_pnl = moneyval(portfolio.get("expectedYield"))
    total_week = 0.0

    # Для пенсионного прогноза — собираем доходности по активам
    cagr_list = []  # список (name, cagr, years_data, weight)
    position_values = {}

    for pos in positions:
        figi = pos.get("figi", "")
        qty = quotation(pos.get("quantity"))
        if qty == 0:
            continue

        name, kind = get_instrument_info(figi)
        current_price = moneyval(pos.get("currentPrice"))
        avg_price = moneyval(pos.get("averagePositionPrice"))
        pnl = moneyval(pos.get("expectedYield"))
        since_pct = (pnl / (avg_price * qty) * 100) if avg_price and qty else 0.0
        pos_val = current_price * qty
        position_values[figi] = pos_val

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"*{name}* ({qty:.0f} шт.)")
        lines.append(f"  💰 Цена: *{current_price:.2f} ₽*  |  Позиция: *{pos_val:,.2f} ₽*")

        if avg_price:
            arr = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"  {arr} С покупки: {pnl:+.2f} ₽ ({since_pct:+.2f}%)")

        # За неделю
        p1, p2 = get_candles_week(figi)
        week_pct = 0.0
        if p1 and p2 and p1 > 0:
            diff = p2 - p1
            week_pct = diff / p1 * 100
            pos_diff = diff * qty
            total_week += pos_diff
            arr_w = "🟢" if diff >= 0 else "🔴"
            lines.append(f"  {arr_w} За неделю: {diff:+.2f} ₽ ({week_pct:+.2f}%)  |  по позиции: {pos_diff:+.2f} ₽")
        else:
            lines.append("  📉 За неделю: нет данных")

        # AI анализ
        analysis = ai_analysis(name, current_price, avg_price, week_pct, since_pct)
        if analysis:
            lines.append(f"\n  🤖 _{analysis}_")

        lines.append("")

        # Историческая доходность для пенсионного прогноза
        cagr, years_data = get_historical_cagr(figi)
        if cagr is not None:
            cagr_list.append((name, cagr, years_data, pos_val))

    # ── Итоги портфеля ──
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Итого: {total_now:,.2f} ₽*")
    arr_pnl = "🟢" if total_pnl >= 0 else "🔴"
    lines.append(f"{arr_pnl} *Доход (рост цен): {total_pnl:+,.2f} ₽*")
    arr_div = "💰" if dividends_total > 0 else "📭"
    lines.append(f"{arr_div} *Купоны и дивиденды за всё время: {dividends_total:+,.2f} ₽*")
    total_income = total_pnl + dividends_total
    arr_tot = "🟢" if total_income >= 0 else "🔴"
    lines.append(f"{arr_tot} *Общий доход: {total_income:+,.2f} ₽*")
    if total_week != 0:
        arr_w = "🟢" if total_week >= 0 else "🔴"
        lines.append(f"{arr_w} *За неделю: {total_week:+,.2f} ₽*")

    # ── Пенсионный прогноз ──
    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🏦 *Пенсионный прогноз на {YEARS} лет*")
    lines.append(f"_Пополнение: {MONTHLY_INVEST:,} ₽/мес + реинвестирование доходов_")
    lines.append(f"_Инфляция: {INFLATION*100:.0f}% в год (реальная стоимость в ценах сегодня)_\n")

    if cagr_list:
        # Взвешенная средняя CAGR по портфелю
        total_weight = sum(w for _, _, _, w in cagr_list)
        if total_weight > 0:
            weighted_cagr = sum(c * w for _, c, _, w in cagr_list) / total_weight
        else:
            weighted_cagr = 0.10  # fallback 10%

        # Добавляем купонную доходность ~6% к цене
        # (цена облигаций почти не растёт, основной доход — купон)
        # Для облигаций в портфеле CAGR цены ~0-2%, реальная ~8-10% с купоном
        # Берём среднюю по портфелю + купонная надбавка уже учтена в pnl

        lines.append("*Доходность по активам (история):*")
        for name, cagr, years_data, _ in cagr_list:
            # Полная доходность = рост цены + ~6% купон/дивиденд (реинвест)
            total_return = cagr + 0.06
            lines.append(f"  • {name}: {cagr*100:+.1f}% цена + ~6% купон = *{total_return*100:.1f}%/год* (данные за {years_data:.0f} лет)")

        # Средняя полная доходность
        avg_full_return = weighted_cagr + 0.06

        # Три сценария
        pessimistic = avg_full_return * 0.6   # -40% от базы
        base = avg_full_return                 # базовый
        optimistic = avg_full_return * 1.4    # +40% от базы

        lines.append(f"\n*Средняя доходность портфеля: {avg_full_return*100:.1f}%/год*")
        lines.append(f"_База {YEARS} лет | пополнение {MONTHLY_INVEST:,} ₽/мес | текущий портфель {total_now:,.0f} ₽_\n")

        for label, rate, emoji in [
            ("Пессимистичный", pessimistic, "🔴"),
            ("Базовый", base, "🟡"),
            ("Оптимистичный", optimistic, "🟢"),
        ]:
            nominal, real = pension_forecast(rate, total_now)
            invested = MONTHLY_INVEST * 12 * YEARS
            profit = nominal - invested - total_now
            lines.append(f"{emoji} *{label}* ({rate*100:.1f}%/год):")
            lines.append(f"  💰 Номинал: *{fmt(nominal)}*")
            lines.append(f"  📉 В ценах сегодня: *{fmt(real)}*")
            lines.append(f"  📈 Прибыль сверх вложений: *{fmt(profit)}*")
            lines.append(f"  🗓 Вложено за {YEARS} лет: {fmt(invested + total_now)}\n")

        lines.append(f"_Вложено своих денег за {YEARS} лет: {fmt(MONTHLY_INVEST * 12 * YEARS)}_")
        lines.append("_⚠️ Прогноз на основе исторических данных, не гарантия_")
    else:
        # Нет данных по свечам — используем консервативную оценку для облигационного портфеля
        # ОФЗ и корп. облигации: купон ~10-12%, цена стабильна → полная доходность ~10%
        fallback_cagr = 0.10
        avg_full_return = fallback_cagr
        lines.append(f"_Исторические свечи недоступны — используем консервативную оценку {avg_full_return*100:.0f}%/год для облигаций_
")

        pessimistic = avg_full_return * 0.6
        base        = avg_full_return
        optimistic  = avg_full_return * 1.4

        lines.append(f"*Оценочная доходность: {avg_full_return*100:.1f}%/год*")
        lines.append(f"_База {YEARS} лет | пополнение {MONTHLY_INVEST:,} ₽/мес | текущий портфель {total_now:,.0f} ₽_
")

        for label, rate, emoji in [
            ("Пессимистичный", pessimistic, "🔴"),
            ("Базовый",        base,        "🟡"),
            ("Оптимистичный",  optimistic,  "🟢"),
        ]:
            nominal, real = pension_forecast(rate, total_now)
            invested = MONTHLY_INVEST * 12 * YEARS
            profit   = nominal - invested - total_now
            lines.append(f"{emoji} *{label}* ({rate*100:.1f}%/год):")
            lines.append(f"  💰 Номинал: *{fmt(nominal)}*")
            lines.append(f"  📉 В ценах сегодня: *{fmt(real)}*")
            lines.append(f"  📈 Прибыль сверх вложений: *{fmt(profit)}*")
            lines.append(f"  🗓 Вложено за {YEARS} лет: {fmt(invested + total_now)}
")

        lines.append(f"_Вложено своих денег за {YEARS} лет: {fmt(MONTHLY_INVEST * 12 * YEARS)}_")
        lines.append("_⚠️ Прогноз оценочный, не гарантия_")

    lines.append("\n_Данные: Т-Инвестиции API_")
    return "\n".join(lines)


def send(text):
    max_len = 4000
    parts = []
    while len(text) > max_len:
        split_at = text.rfind("━━━", 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:]
    parts.append(text)
    for part in parts:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": part, "parse_mode": "Markdown"},
            timeout=15
        )
        r.raise_for_status()
    print(f"✅ Отправлено ({len(parts)} сообщений)!")


if __name__ == "__main__":
    report = build_report()
    print(report)
    send(report)
