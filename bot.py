import os
import requests
from datetime import datetime, timedelta, timezone


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TINVEST_TOKEN = os.environ["TINVEST_TOKEN"]

ACCOUNT_NAME = "Очень долгий срок"

PURCHASE_DATE = datetime(2025, 12, 8, tzinfo=timezone.utc)

MONTHLY_INVEST = 60_000
YEARS = 17
INFLATION = 0.07

# Индексация ежемесячных пополнений.
# 0.00 = каждый месяц по 60 000 ₽ без роста.
# 0.05 = каждый год увеличивать пополнение на 5%.
CONTRIBUTION_INDEXATION = 0.00

# Сколько годовой доходности условно съедают комиссии, налоги, ошибки, спреды.
# Для долгосрочного прогноза лучше закладывать небольшой "тормоз".
ANNUAL_COST_DRAG = 0.005

# Целевая структура будущего портфеля.
# Именно по этой структуре считаются будущие пополнения.
TARGET_ALLOCATION = {
    "bond": 0.50,   # облигации
    "stock": 0.30,  # акции
    "fund": 0.15,   # фонды / ETF / БПИФ
    "cash": 0.05,   # деньги / фонды ликвидности
}

# Модельные ожидаемые доходности по классам активов.
# Это не гарантия, а сценарная модель.
EXPECTED_RETURNS = {
    "bond": {
        "pessimistic": 0.08,
        "base": 0.11,
        "optimistic": 0.13,
    },
    "stock": {
        "pessimistic": 0.04,
        "base": 0.13,
        "optimistic": 0.18,
    },
    "fund": {
        "pessimistic": 0.06,
        "base": 0.12,
        "optimistic": 0.16,
    },
    "cash": {
        "pessimistic": 0.04,
        "base": 0.07,
        "optimistic": 0.09,
    },
    "other": {
        "pessimistic": 0.05,
        "base": 0.10,
        "optimistic": 0.14,
    },
}


# ─────────────────────────────────────────────
# T-ИНВЕСТИЦИИ API
# ─────────────────────────────────────────────

TINVEST = "https://invest-public-api.tinkoff.ru/rest"
HEADERS = {
    "Authorization": f"Bearer {TINVEST_TOKEN}",
    "Content-Type": "application/json",
}


def ti_post(path, body=None):
    r = requests.post(
        f"{TINVEST}{path}",
        headers=HEADERS,
        json=body or {},
        timeout=15,
    )
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
        {"accountId": account_id},
    )


def get_instrument_info(figi):
    try:
        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.InstrumentsService/GetInstrumentBy",
            {
                "idType": "ID_TYPE_FIGI",
                "id": figi,
            },
        )
        inst = data.get("instrument", {})
        name = inst.get("name") or inst.get("ticker") or figi
        kind = inst.get("instrumentKind", "")
        ticker = inst.get("ticker", "")
        return name, kind, ticker
    except Exception:
        return figi, "", ""


def get_candles_week(figi):
    try:
        now = datetime.now(timezone.utc)
        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles",
            {
                "figi": figi,
                "from": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "interval": "CANDLE_INTERVAL_DAY",
            },
        )

        candles = data.get("candles", [])

        if len(candles) >= 2:
            return (
                quotation(candles[0].get("close")),
                quotation(candles[-1].get("close")),
            )

    except Exception:
        pass

    return None, None


def get_historical_cagr(figi):
    """
    Справочная историческая доходность цены.
    Используется только как информационный блок, не как основа прогноза на 17 лет.
    """
    now = datetime.now(timezone.utc)

    intervals = [
        ("CANDLE_INTERVAL_MONTH", 365 * 17, 12.0),
        ("CANDLE_INTERVAL_WEEK", 365 * 5, 52.0),
        ("CANDLE_INTERVAL_DAY", 365 * 3, 365.0),
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
                    "interval": interval,
                },
            )

            candles = data.get("candles", [])

            if len(candles) < 2:
                continue

            p_first = quotation(candles[0].get("close"))
            p_last = quotation(candles[-1].get("close"))

            if not p_first or not p_last or p_first <= 0:
                continue

            years_avail = len(candles) / per_year

            if years_avail < 0.05:
                continue

            price_cagr = (p_last / p_first) ** (1 / years_avail) - 1

            return price_cagr, round(years_avail, 1)

        except Exception:
            continue

    return None, None


def get_dividends_and_coupons(account_id):
    total = 0.0

    try:
        now = datetime.now(timezone.utc)

        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.OperationsService/GetOperations",
            {
                "accountId": account_id,
                "from": datetime(2000, 1, 1, tzinfo=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "state": "OPERATION_STATE_EXECUTED",
            },
        )

        income_types = {
            "OPERATION_TYPE_DIVIDEND",
            "OPERATION_TYPE_COUPON",
            "OPERATION_TYPE_BOND_REPAYMENT_FULL",
        }

        for op in data.get("operations", []):
            if op.get("operationType", "") in income_types:
                total += moneyval(op.get("payment"))

    except Exception as e:
        print(f"Ошибка выплат: {e}")

    return total


# ─────────────────────────────────────────────
# КЛАССИФИКАЦИЯ АКТИВОВ
# ─────────────────────────────────────────────

def classify_asset(kind, name="", ticker=""):
    """
    Классифицирует инструмент по типу.
    Используется для анализа текущего портфеля и сравнения с целевой структурой.
    """

    text = f"{kind} {name} {ticker}".lower()

    bond_words = [
        "bond",
        "облига",
        "офз",
        "замещающ",
        "еврооблиг",
    ]

    stock_words = [
        "share",
        "stock",
        "акци",
        "ао ",
        "ап ",
        "обыкнов",
        "привилег",
    ]

    fund_words = [
        "etf",
        "fund",
        "бпиф",
        "пиф",
        "фонд",
        "тмош",
        "ликвидность",
    ]

    cash_words = [
        "currency",
        "money",
        "cash",
        "рубль",
        "доллар",
        "юань",
        "eur",
        "usd",
        "cny",
    ]

    if any(w in text for w in bond_words):
        return "bond"

    if any(w in text for w in fund_words):
        return "fund"

    if any(w in text for w in stock_words):
        return "stock"

    if any(w in text for w in cash_words):
        return "cash"

    return "other"


def asset_class_ru(asset_class):
    names = {
        "bond": "Облигации",
        "stock": "Акции",
        "fund": "Фонды",
        "cash": "Деньги / ликвидность",
        "other": "Прочее",
    }
    return names.get(asset_class, asset_class)


def allocation_sum_is_valid(allocation):
    total = sum(allocation.values())
    return abs(total - 1.0) < 0.0001


# ─────────────────────────────────────────────
# ДОЛГОСРОЧНЫЙ ПРОГНОЗ
# ─────────────────────────────────────────────

def weighted_expected_return(allocation, scenario):
    """
    Считает доходность портфеля по целевой структуре.
    """

    result = 0.0

    for asset_class, weight in allocation.items():
        class_returns = EXPECTED_RETURNS.get(asset_class, EXPECTED_RETURNS["other"])
        result += weight * class_returns[scenario]

    result_after_costs = max(result - ANNUAL_COST_DRAG, 0)

    return result_after_costs


def real_rate(nominal_rate, inflation):
    """
    Реальная доходность с учётом инфляции.
    """
    return (1 + nominal_rate) / (1 + inflation) - 1


def future_value_with_monthly_contributions(
    current_value,
    monthly_invest,
    years,
    annual_rate,
    contribution_indexation=0.0,
):
    """
    Будущая стоимость портфеля.

    Учитывает:
    1. Рост уже накопленного портфеля.
    2. Ежемесячные пополнения.
    3. Возможную ежегодную индексацию пополнений.
    """

    monthly_rate = (1 + annual_rate) ** (1 / 12) - 1

    fv_existing = current_value * ((1 + annual_rate) ** years)

    fv_contributions = 0.0
    current_monthly_invest = monthly_invest

    for month in range(1, years * 12 + 1):
        # Индексация пополнений раз в год.
        if month > 1 and (month - 1) % 12 == 0:
            current_monthly_invest *= (1 + contribution_indexation)

        months_to_grow = years * 12 - month + 1
        fv_contributions += current_monthly_invest * ((1 + monthly_rate) ** months_to_grow)

    return fv_existing + fv_contributions


def total_contributions(
    current_value,
    monthly_invest,
    years,
    contribution_indexation=0.0,
):
    """
    Считает сумму собственных вложений:
    текущий портфель + будущие пополнения.
    """

    total = current_value
    current_monthly_invest = monthly_invest

    for month in range(1, years * 12 + 1):
        if month > 1 and (month - 1) % 12 == 0:
            current_monthly_invest *= (1 + contribution_indexation)

        total += current_monthly_invest

    return total


def forecast_milestones(current_portfolio_value, annual_rate):
    """
    Контрольные точки прогноза: 5 / 10 / 15 / 17 лет.
    """

    milestones = [5, 10, 15, YEARS]
    result = []

    for year in milestones:
        nominal = future_value_with_monthly_contributions(
            current_value=current_portfolio_value,
            monthly_invest=MONTHLY_INVEST,
            years=year,
            annual_rate=annual_rate,
            contribution_indexation=CONTRIBUTION_INDEXATION,
        )

        real_value = nominal / ((1 + INFLATION) ** year)

        invested = total_contributions(
            current_value=current_portfolio_value,
            monthly_invest=MONTHLY_INVEST,
            years=year,
            contribution_indexation=CONTRIBUTION_INDEXATION,
        )

        result.append(
            {
                "year": year,
                "nominal": nominal,
                "real": real_value,
                "invested": invested,
                "profit": nominal - invested,
            }
        )

    return result


def build_rebalance_recommendation(current_allocation):
    """
    Показывает, куда лучше направлять новые пополнения,
    если фактическая структура отличается от целевой.
    """

    lines = []

    lines.append("*Сравнение с целевой структурой:*")

    underweight = []

    for asset_class, target_weight in TARGET_ALLOCATION.items():
        current_weight = current_allocation.get(asset_class, 0.0)
        diff = current_weight - target_weight

        if diff >= 0:
            sign = "+"
        else:
            sign = ""

        lines.append(
            f"  • {asset_class_ru(asset_class)}: "
            f"факт {current_weight * 100:.1f}% / "
            f"цель {target_weight * 100:.1f}% "
            f"({sign}{diff * 100:.1f} п.п.)"
        )

        if diff < -0.03:
            underweight.append((asset_class, abs(diff)))

    if underweight:
        underweight.sort(key=lambda x: x[1], reverse=True)

        lines.append("")
        lines.append("*Куда логично направлять новые пополнения:*")

        for asset_class, diff in underweight:
            lines.append(
                f"  • {asset_class_ru(asset_class)} — недовес примерно {diff * 100:.1f} п.п."
            )
    else:
        lines.append("")
        lines.append("_Структура близка к целевой. Можно пополнять пропорционально плану._")

    return "\n".join(lines)


def build_long_term_forecast(current_portfolio_value, current_allocation):
    """
    Основной блок долгосрочного прогноза на 17 лет.
    """

    if not allocation_sum_is_valid(TARGET_ALLOCATION):
        return (
            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ *Ошибка в TARGET_ALLOCATION*\n"
            "Сумма долей целевого портфеля должна быть равна 100%."
        )

    invested_total = total_contributions(
        current_value=current_portfolio_value,
        monthly_invest=MONTHLY_INVEST,
        years=YEARS,
        contribution_indexation=CONTRIBUTION_INDEXATION,
    )

    scenarios = [
        ("pessimistic", "🔴 *Пессимистичный*"),
        ("base", "🟡 *Базовый*"),
        ("optimistic", "🟢 *Оптимистичный*"),
    ]

    result = []

    result.append("\n━━━━━━━━━━━━━━━━━━━━")
    result.append(f"🏦 *Прогноз капитала на {YEARS} лет*")
    result.append(f"_Пополнение: {MONTHLY_INVEST:,} ₽/мес_")
    result.append(f"_Индексация пополнений: {CONTRIBUTION_INDEXATION * 100:.1f}%/год_")
    result.append(f"_Инфляция: {INFLATION * 100:.1f}%/год_")
    result.append(f"_Издержки модели: {ANNUAL_COST_DRAG * 100:.1f}%/год_")
    result.append("_Модель: целевая структура портфеля + реинвестирование доходов_\n")

    result.append("*Целевая структура будущих покупок:*")

    for asset_class, weight in TARGET_ALLOCATION.items():
        result.append(f"  • {asset_class_ru(asset_class)}: {weight * 100:.0f}%")

    result.append("")

    for scenario_key, scenario_name in scenarios:
        annual_rate = weighted_expected_return(TARGET_ALLOCATION, scenario_key)
        real_annual = real_rate(annual_rate, INFLATION)

        nominal = future_value_with_monthly_contributions(
            current_value=current_portfolio_value,
            monthly_invest=MONTHLY_INVEST,
            years=YEARS,
            annual_rate=annual_rate,
            contribution_indexation=CONTRIBUTION_INDEXATION,
        )

        real_value = nominal / ((1 + INFLATION) ** YEARS)
        investment_profit = nominal - invested_total

        annual_withdrawal_4 = nominal * 0.04
        monthly_withdrawal_4 = annual_withdrawal_4 / 12

        annual_withdrawal_5 = nominal * 0.05
        monthly_withdrawal_5 = annual_withdrawal_5 / 12

        result.append(scenario_name)
        result.append(f"  📈 Доходность: *{annual_rate * 100:.1f}%/год*")
        result.append(f"  📉 Реальная доходность: *{real_annual * 100:.1f}%/год*")
        result.append(f"  💰 Капитал через {YEARS} лет: *{fmt(nominal)}*")
        result.append(f"  🛒 В сегодняшних деньгах: *{fmt(real_value)}*")
        result.append(f"  🧾 Внесено своих денег: *{fmt(invested_total)}*")
        result.append(f"  📊 Инвестдоход: *{fmt(investment_profit)}*")
        result.append(f"  🏖 Рента 4%: *{fmt(monthly_withdrawal_4)}/мес*")
        result.append(f"  🏖 Рента 5%: *{fmt(monthly_withdrawal_5)}/мес*")
        result.append("")

    base_rate = weighted_expected_return(TARGET_ALLOCATION, "base")
    milestones = forecast_milestones(current_portfolio_value, base_rate)

    result.append("*Контрольные точки по базовому сценарию:*")

    for item in milestones:
        result.append(
            f"  • Через {item['year']} лет: "
            f"*{fmt(item['nominal'])}* номинал / "
            f"*{fmt(item['real'])}* в сегодняшних деньгах"
        )

    result.append("")
    result.append(build_rebalance_recommendation(current_allocation))
    result.append("")
    result.append("_⚠️ Это не гарантия доходности, а сценарная модель._")
    result.append("_Для горизонта 17 лет важнее дисциплина пополнений, структура портфеля и ребалансировка._")

    return "\n".join(result)


# ─────────────────────────────────────────────
# AI-АНАЛИЗ ДЛЯ ПРОСТОГО ОБЪЯСНЕНИЯ
# ─────────────────────────────────────────────

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
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 200,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            },
            timeout=30,
        )

        r.raise_for_status()

        return r.json()["content"][0]["text"].strip()

    except Exception as e:
        return f"Анализ недоступен: {e}"


# ─────────────────────────────────────────────
# ФОРМАТИРОВАНИЕ
# ─────────────────────────────────────────────

def fmt(n):
    """
    Форматирует большое число в млн / тыс.
    """

    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f} млрд ₽"

    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f} млн ₽"

    return f"{n:,.0f} ₽".replace(",", " ")


def pct(value):
    return f"{value * 100:.1f}%"


# ─────────────────────────────────────────────
# ОСНОВНОЙ ОТЧЁТ
# ─────────────────────────────────────────────

def build_report():
    lines = [
        "📊 *Еженедельный отчёт по портфелю*",
        f"📅 {datetime.now().strftime('%d.%m.%Y')}  |  Счёт: {ACCOUNT_NAME}\n",
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

    historical_cagr_list = []

    allocation_values = {
        "bond": 0.0,
        "stock": 0.0,
        "fund": 0.0,
        "cash": 0.0,
        "other": 0.0,
    }

    for pos in positions:
        figi = pos.get("figi", "")
        qty = quotation(pos.get("quantity"))

        if qty == 0:
            continue

        name, kind, ticker = get_instrument_info(figi)

        current_price = moneyval(pos.get("currentPrice"))
        avg_price = moneyval(pos.get("averagePositionPrice"))
        pnl = moneyval(pos.get("expectedYield"))

        pos_val = current_price * qty

        asset_class = classify_asset(kind, name, ticker)

        if asset_class not in allocation_values:
            asset_class = "other"

        allocation_values[asset_class] += pos_val

        since_pct = (pnl / (avg_price * qty) * 100) if avg_price and qty else 0.0

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"*{name}* ({qty:.0f} шт.)")
        lines.append(f"  💰 Цена: *{current_price:.2f} ₽*  |  Позиция: *{pos_val:,.2f} ₽*")

        lines.append(f"  🧩 Класс: *{asset_class_ru(asset_class)}*")

        if avg_price:
            arr = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"  {arr} С покупки: {pnl:+.2f} ₽ ({since_pct:+.2f}%)")

        p1, p2 = get_candles_week(figi)
        week_pct = 0.0

        if p1 and p2 and p1 > 0:
            diff = p2 - p1
            week_pct = diff / p1 * 100
            pos_diff = diff * qty
            total_week += pos_diff

            arr_w = "🟢" if diff >= 0 else "🔴"

            lines.append(
                f"  {arr_w} За неделю: "
                f"{diff:+.2f} ₽ ({week_pct:+.2f}%)  |  "
                f"по позиции: {pos_diff:+.2f} ₽"
            )

        else:
            lines.append("  📉 За неделю: нет данных")

        analysis = ai_analysis(name, current_price, avg_price, week_pct, since_pct)

        if analysis:
            lines.append(f"\n  🤖 _{analysis}_")

        cagr, years_data = get_historical_cagr(figi)

        if cagr is not None:
            historical_cagr_list.append(
                {
                    "name": name,
                    "cagr": cagr,
                    "years_data": years_data,
                    "asset_class": asset_class,
                    "value": pos_val,
                }
            )

        lines.append("")

    # ─────────────────────────────────────────
    # ИТОГИ ПОРТФЕЛЯ
    # ─────────────────────────────────────────

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Итого: {total_now:,.2f} ₽*")

    arr_pnl = "🟢" if total_pnl >= 0 else "🔴"
    lines.append(f"{arr_pnl} *Доход от изменения цены: {total_pnl:+,.2f} ₽*")

    arr_div = "💰" if dividends_total > 0 else "📭"
    lines.append(f"{arr_div} *Купоны и дивиденды за всё время: {dividends_total:+,.2f} ₽*")

    total_income = total_pnl + dividends_total
    arr_tot = "🟢" if total_income >= 0 else "🔴"
    lines.append(f"{arr_tot} *Общий доход: {total_income:+,.2f} ₽*")

    if total_week != 0:
        arr_w = "🟢" if total_week >= 0 else "🔴"
        lines.append(f"{arr_w} *За неделю: {total_week:+,.2f} ₽*")

    # ─────────────────────────────────────────
    # ТЕКУЩАЯ СТРУКТУРА ПОРТФЕЛЯ
    # ─────────────────────────────────────────

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append("🧩 *Текущая структура портфеля*")

    current_allocation = {}

    if total_now > 0:
        for asset_class, value in allocation_values.items():
            weight = value / total_now
            current_allocation[asset_class] = weight

            if value > 0:
                lines.append(
                    f"  • {asset_class_ru(asset_class)}: "
                    f"*{weight * 100:.1f}%*  |  {fmt(value)}"
                )
    else:
        current_allocation = {
            "bond": 0.0,
            "stock": 0.0,
            "fund": 0.0,
            "cash": 0.0,
            "other": 0.0,
        }

    # ─────────────────────────────────────────
    # ИСТОРИЧЕСКАЯ ДОХОДНОСТЬ — ТОЛЬКО СПРАВОЧНО
    # ─────────────────────────────────────────

    if historical_cagr_list:
        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("📈 *Историческая динамика цены*")
        lines.append("_Справочно. Не используется как главный прогноз на 17 лет._")

        historical_cagr_list.sort(key=lambda x: x["value"], reverse=True)

        for item in historical_cagr_list[:10]:
            lines.append(
                f"  • {item['name']}: "
                f"{item['cagr'] * 100:+.1f}%/год "
                f"по цене за {item['years_data']:.1f} лет"
            )

    # ─────────────────────────────────────────
    # ДОЛГОСРОЧНЫЙ ПРОГНОЗ
    # ─────────────────────────────────────────

    lines.append(build_long_term_forecast(total_now, current_allocation))

    lines.append("\n_Данные: Т-Инвестиции API_")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# ОТПРАВКА В TELEGRAM
# ─────────────────────────────────────────────

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
            json={
                "chat_id": CHAT_ID,
                "text": part,
                "parse_mode": "Markdown",
            },
            timeout=15,
        )

        r.raise_for_status()

    print(f"✅ Отправлено ({len(parts)} сообщений)!")


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

if __name__ == "__main__":
    report = build_report()
    print(report)
    send(report)
