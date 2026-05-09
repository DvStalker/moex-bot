import os
import requests
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
TINVEST_TOKEN = os.environ["TINVEST_TOKEN"]

ACCOUNT_NAME = "Очень долгий срок"

MONTHLY_INVEST = 60_000
YEARS = 17
INFLATION = 0.07

# Индексация пополнений.
# 0.00 = всегда по 60 000 ₽ в месяц.
# 0.05 = каждый год увеличивать пополнение на 5%.
CONTRIBUTION_INDEXATION = 0.00

# Условные ежегодные издержки: комиссии, налоги, спреды, ошибки.
ANNUAL_COST_DRAG = 0.005

# Целевая структура будущих покупок.
# Сумма должна быть 1.00.
TARGET_ALLOCATION = {
    "bond": 0.50,
    "stock": 0.30,
    "fund": 0.15,
    "cash": 0.05,
}

# Сценарные ожидаемые доходности по классам активов.
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
        timeout=20,
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
    raise Exception(f"Счёт '{ACCOUNT_NAME}' не найден. Доступные счета: {names}")


def get_portfolio(account_id):
    return ti_post(
        "/tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
        {
            "accountId": account_id,
        },
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
        print(f"Ошибка получения купонов и дивидендов: {e}")

    return total


# ─────────────────────────────────────────────
# КЛАССИФИКАЦИЯ АКТИВОВ
# ─────────────────────────────────────────────

def classify_asset(kind, name="", ticker=""):
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
        "cash": "Деньги",
        "other": "Прочее",
    }

    return names.get(asset_class, asset_class)


def allocation_sum_is_valid(allocation):
    return abs(sum(allocation.values()) - 1.0) < 0.0001


# ─────────────────────────────────────────────
# ПРОГНОЗ НА 17 ЛЕТ
# ─────────────────────────────────────────────

def weighted_expected_return(allocation, scenario):
    result = 0.0

    for asset_class, weight in allocation.items():
        class_returns = EXPECTED_RETURNS.get(asset_class, EXPECTED_RETURNS["other"])
        result += weight * class_returns[scenario]

    result_after_costs = max(result - ANNUAL_COST_DRAG, 0)

    return result_after_costs


def real_rate(nominal_rate, inflation):
    return (1 + nominal_rate) / (1 + inflation) - 1


def future_value_with_monthly_contributions(
    current_value,
    monthly_invest,
    years,
    annual_rate,
    contribution_indexation=0.0,
):
    monthly_rate = (1 + annual_rate) ** (1 / 12) - 1

    fv_existing = current_value * ((1 + annual_rate) ** years)

    fv_contributions = 0.0
    current_monthly_invest = monthly_invest

    for month in range(1, years * 12 + 1):
        if month > 1 and (month - 1) % 12 == 0:
            current_monthly_invest *= 1 + contribution_indexation

        months_to_grow = years * 12 - month + 1
        fv_contributions += current_monthly_invest * (
            (1 + monthly_rate) ** months_to_grow
        )

    return fv_existing + fv_contributions


def total_contributions(
    current_value,
    monthly_invest,
    years,
    contribution_indexation=0.0,
):
    total = current_value
    current_monthly_invest = monthly_invest

    for month in range(1, years * 12 + 1):
        if month > 1 and (month - 1) % 12 == 0:
            current_monthly_invest *= 1 + contribution_indexation

        total += current_monthly_invest

    return total


def build_forecast_data(current_portfolio_value):
    if not allocation_sum_is_valid(TARGET_ALLOCATION):
        raise Exception("Ошибка: сумма TARGET_ALLOCATION должна быть равна 1.00")

    scenarios = [
        ("pessimistic", "Пессимистичный"),
        ("base", "Базовый"),
        ("optimistic", "Оптимистичный"),
    ]

    invested_total = total_contributions(
        current_value=current_portfolio_value,
        monthly_invest=MONTHLY_INVEST,
        years=YEARS,
        contribution_indexation=CONTRIBUTION_INDEXATION,
    )

    result = {
        "invested_total": invested_total,
        "scenarios": [],
    }

    for scenario_key, scenario_name in scenarios:
        annual_rate = weighted_expected_return(TARGET_ALLOCATION, scenario_key)

        nominal = future_value_with_monthly_contributions(
            current_value=current_portfolio_value,
            monthly_invest=MONTHLY_INVEST,
            years=YEARS,
            annual_rate=annual_rate,
            contribution_indexation=CONTRIBUTION_INDEXATION,
        )

        real_value = nominal / ((1 + INFLATION) ** YEARS)
        investment_profit = nominal - invested_total

        result["scenarios"].append(
            {
                "key": scenario_key,
                "name": scenario_name,
                "annual_rate": annual_rate,
                "real_rate": real_rate(annual_rate, INFLATION),
                "nominal": nominal,
                "real": real_value,
                "invested_total": invested_total,
                "investment_profit": investment_profit,
                "monthly_rent_4": nominal * 0.04 / 12,
                "monthly_rent_5": nominal * 0.05 / 12,
            }
        )

    return result


def build_rebalance_text(current_allocation):
    underweight = []

    for asset_class, target_weight in TARGET_ALLOCATION.items():
        current_weight = current_allocation.get(asset_class, 0.0)
        diff = current_weight - target_weight

        if diff < -0.03:
            underweight.append((asset_class, abs(diff)))

    if not underweight:
        return "Структура близка к целевой. Пополнять можно пропорционально плану."

    underweight.sort(key=lambda x: x[1], reverse=True)

    main = underweight[0][0]

    return f"Главный недовес: {asset_class_ru(main)}. Новые пополнения логично направлять туда."


# ─────────────────────────────────────────────
# ФОРМАТИРОВАНИЕ
# ─────────────────────────────────────────────

def fmt_money(n):
    return f"{n:,.0f} ₽".replace(",", " ")


def fmt_short(n):
    sign = "-" if n < 0 else ""
    n = abs(n)

    if n >= 1_000_000_000:
        return f"{sign}{n / 1_000_000_000:.2f} млрд ₽"

    if n >= 1_000_000:
        return f"{sign}{n / 1_000_000:.2f} млн ₽"

    if n >= 1_000:
        return f"{sign}{n / 1_000:.0f} тыс ₽"

    return f"{sign}{n:.0f} ₽"


def fmt_pct(x):
    return f"{x * 100:.1f}%"


# ─────────────────────────────────────────────
# ШРИФТЫ И РИСОВАНИЕ
# ─────────────────────────────────────────────

def load_font(size, bold=False):
    candidates = []

    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "DejaVuSans-Bold.ttf",
            "Arial Bold.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "DejaVuSans.ttf",
            "Arial.ttf",
        ]

    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue

    return ImageFont.load_default()


def draw_text_fit(draw, text, box, font, fill):
    x1, y1, x2, y2 = box
    max_width = x2 - x1

    words = str(text).split()
    lines = []
    current = ""

    for word in words:
        test = word if not current else current + " " + word
        bbox = draw.textbbox((0, 0), test, font=font)

        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    y = y1
    line_height = font.size + 8 if hasattr(font, "size") else 24

    for line in lines:
        if y + line_height > y2:
            break

        draw.text((x1, y), line, font=font, fill=fill)
        y += line_height


def draw_card(draw, x, y, w, h, fill, outline):
    draw.rounded_rectangle(
        (x, y, x + w, y + h),
        radius=32,
        fill=fill,
        outline=outline,
        width=2,
    )


def draw_metric_card(
    draw,
    x,
    y,
    w,
    h,
    title,
    value,
    note,
    value_color,
    fonts,
    colors,
):
    draw_card(draw, x, y, w, h, colors["card"], colors["border"])

    draw.text(
        (x + 30, y + 26),
        title,
        font=fonts["card_title"],
        fill=colors["muted"],
    )

    draw.text(
        (x + 30, y + 78),
        value,
        font=fonts["card_value"],
        fill=value_color,
    )

    draw_text_fit(
        draw,
        note,
        (x + 30, y + 145, x + w - 30, y + h - 20),
        fonts["small"],
        colors["muted"],
    )


def draw_bar(draw, x, y, w, h, pct_value, fill, bg, label, value_text, fonts, colors):
    pct_value = max(0, min(pct_value, 1))

    draw.text((x, y), label, font=fonts["text"], fill=colors["dark"])
    draw.text((x + w - 130, y), value_text, font=fonts["text"], fill=colors["muted"])

    bar_y = y + 38

    draw.rounded_rectangle(
        (x, bar_y, x + w, bar_y + h),
        radius=14,
        fill=bg,
    )

    if pct_value > 0:
        draw.rounded_rectangle(
            (x, bar_y, x + int(w * pct_value), bar_y + h),
            radius=14,
            fill=fill,
        )


def draw_target_allocation(draw, x, y, w, fonts, colors):
    draw.text(
        (x, y),
        "Целевая структура будущих покупок",
        font=fonts["h2"],
        fill=colors["dark"],
    )

    y += 70

    bar_colors = {
        "bond": "#3B82F6",
        "stock": "#16A34A",
        "fund": "#A855F7",
        "cash": "#F59E0B",
        "other": "#64748B",
    }

    for asset_class, weight in TARGET_ALLOCATION.items():
        draw_bar(
            draw=draw,
            x=x,
            y=y,
            w=w,
            h=24,
            pct_value=weight,
            fill=bar_colors.get(asset_class, "#64748B"),
            bg=colors["bar_bg"],
            label=asset_class_ru(asset_class),
            value_text=f"{weight * 100:.0f}%",
            fonts=fonts,
            colors=colors,
        )

        y += 82


def draw_current_allocation(draw, x, y, w, current_allocation, fonts, colors):
    draw.text(
        (x, y),
        "Текущая структура портфеля",
        font=fonts["h2"],
        fill=colors["dark"],
    )

    y += 70

    bar_colors = {
        "bond": "#3B82F6",
        "stock": "#16A34A",
        "fund": "#A855F7",
        "cash": "#F59E0B",
        "other": "#64748B",
    }

    for asset_class in ["bond", "stock", "fund", "cash", "other"]:
        weight = current_allocation.get(asset_class, 0.0)

        if weight <= 0.005 and asset_class == "other":
            continue

        draw_bar(
            draw=draw,
            x=x,
            y=y,
            w=w,
            h=24,
            pct_value=weight,
            fill=bar_colors.get(asset_class, "#64748B"),
            bg=colors["bar_bg"],
            label=asset_class_ru(asset_class),
            value_text=f"{weight * 100:.1f}%",
            fonts=fonts,
            colors=colors,
        )

        y += 82


# ─────────────────────────────────────────────
# ИНФОГРАФИКА PNG
# ─────────────────────────────────────────────

def create_portfolio_infographic(
    total_now,
    total_pnl,
    dividends_total,
    total_income,
    current_allocation,
    forecast_data,
    rebalance_text,
    filename="portfolio_infographic.png",
):
    width = 1600
    height = 2150

    colors = {
        "bg": "#F4F1EA",
        "card": "#FFFFFF",
        "dark": "#1F2933",
        "muted": "#6B7280",
        "border": "#E5E0D8",
        "green": "#166534",
        "red": "#991B1B",
        "gold": "#B45309",
        "blue": "#1D4ED8",
        "bar_bg": "#ECE7DF",
    }

    fonts = {
        "title": load_font(64, bold=True),
        "subtitle": load_font(30, bold=False),
        "h2": load_font(38, bold=True),
        "card_title": load_font(30, bold=True),
        "card_value": load_font(42, bold=True),
        "text": load_font(27, bold=False),
        "text_bold": load_font(27, bold=True),
        "small": load_font(22, bold=False),
        "scenario_title": load_font(32, bold=True),
        "scenario_value": load_font(34, bold=True),
    }

    img = Image.new("RGB", (width, height), colors["bg"])
    draw = ImageDraw.Draw(img)

    # Фоновые декоративные элементы
    draw.ellipse((1180, -180, 1780, 420), fill="#E8DDC9")
    draw.ellipse((-220, 1450, 360, 2050), fill="#E9E1D4")

    # Заголовок
    draw.text(
        (80, 70),
        "Портфель: долгий срок",
        font=fonts["title"],
        fill=colors["dark"],
    )

    draw.text(
        (80, 150),
        f"Еженедельный отчёт • {datetime.now().strftime('%d.%m.%Y')}",
        font=fonts["subtitle"],
        fill=colors["muted"],
    )

    # Верхние карточки
    pnl_color = colors["green"] if total_pnl >= 0 else colors["red"]
    income_color = colors["green"] if total_income >= 0 else colors["red"]

    draw_metric_card(
        draw,
        80,
        240,
        680,
        185,
        "Текущий капитал",
        fmt_short(total_now),
        "Стоимость портфеля сейчас",
        colors["dark"],
        fonts,
        colors,
    )

    draw_metric_card(
        draw,
        840,
        240,
        680,
        185,
        "Доход от цены",
        fmt_short(total_pnl),
        "Текущий результат по позициям",
        pnl_color,
        fonts,
        colors,
    )

    draw_metric_card(
        draw,
        80,
        465,
        680,
        185,
        "Купоны и дивиденды",
        fmt_short(dividends_total),
        "Получено за всё время",
        colors["gold"],
        fonts,
        colors,
    )

    draw_metric_card(
        draw,
        840,
        465,
        680,
        185,
        "Общий доход",
        fmt_short(total_income),
        "Цена + выплаты",
        income_color,
        fonts,
        colors,
    )

    # Структура портфеля
    draw_card(draw, 80, 720, 1440, 500, colors["card"], colors["border"])

    draw_current_allocation(
        draw=draw,
        x=120,
        y=760,
        w=620,
        current_allocation=current_allocation,
        fonts=fonts,
        colors=colors,
    )

    draw_target_allocation(
        draw=draw,
        x=840,
        y=760,
        w=620,
        fonts=fonts,
        colors=colors,
    )

    # Рекомендация
    draw_card(draw, 80, 1260, 1440, 150, "#FFF8E7", "#EACB7A")

    draw.text(
        (120, 1295),
        "Рекомендация по новым пополнениям",
        font=fonts["card_title"],
        fill=colors["gold"],
    )

    draw_text_fit(
        draw,
        rebalance_text,
        (120, 1345, 1460, 1400),
        fonts["text"],
        colors["dark"],
    )

    # Прогноз
    draw.text(
        (80, 1480),
        f"Прогноз на {YEARS} лет",
        font=fonts["h2"],
        fill=colors["dark"],
    )

    draw.text(
        (80, 1535),
        f"Пополнение: {fmt_money(MONTHLY_INVEST)} / мес • Инфляция: {INFLATION * 100:.1f}% • Индексация: {CONTRIBUTION_INDEXATION * 100:.1f}%",
        font=fonts["small"],
        fill=colors["muted"],
    )

    scenario_colors = {
        "pessimistic": "#991B1B",
        "base": "#B45309",
        "optimistic": "#166534",
    }

    y = 1605

    for item in forecast_data["scenarios"]:
        key = item["key"]

        draw_card(draw, 80, y, 1440, 150, colors["card"], colors["border"])

        accent = scenario_colors.get(key, colors["dark"])

        draw.rounded_rectangle(
            (80, y, 96, y + 150),
            radius=8,
            fill=accent,
        )

        draw.text(
            (120, y + 28),
            item["name"],
            font=fonts["scenario_title"],
            fill=accent,
        )

        draw.text(
            (120, y + 82),
            f"{item['annual_rate'] * 100:.1f}% годовых",
            font=fonts["small"],
            fill=colors["muted"],
        )

        draw.text(
            (420, y + 26),
            "Капитал",
            font=fonts["small"],
            fill=colors["muted"],
        )

        draw.text(
            (420, y + 65),
            fmt_short(item["nominal"]),
            font=fonts["scenario_value"],
            fill=colors["dark"],
        )

        draw.text(
            (760, y + 26),
            "В ценах сегодня",
            font=fonts["small"],
            fill=colors["muted"],
        )

        draw.text(
            (760, y + 65),
            fmt_short(item["real"]),
            font=fonts["scenario_value"],
            fill=colors["dark"],
        )

        draw.text(
            (1130, y + 26),
            "Рента 4%",
            font=fonts["small"],
            fill=colors["muted"],
        )

        draw.text(
            (1130, y + 65),
            f"{fmt_short(item['monthly_rent_4'])}/мес",
            font=fonts["scenario_value"],
            fill=colors["green"],
        )

        y += 180

    # Нижний блок: вложено своих денег
    invested_total = forecast_data["invested_total"]

    draw_card(draw, 80, y + 20, 1440, 145, "#EEF7EE", "#B8D9B8")

    draw.text(
        (120, y + 55),
        "Собственные вложения за весь период",
        font=fonts["card_title"],
        fill=colors["green"],
    )

    draw.text(
        (120, y + 103),
        fmt_short(invested_total),
        font=fonts["scenario_value"],
        fill=colors["dark"],
    )

    draw.text(
        (540, y + 107),
        "текущий портфель + будущие ежемесячные пополнения",
        font=fonts["small"],
        fill=colors["muted"],
    )

    # Footer
    draw.text(
        (80, height - 90),
        "⚠️ Сценарная модель, не гарантия доходности. Данные: Т-Инвестиции API.",
        font=fonts["small"],
        fill=colors["muted"],
    )

    img.save(filename, quality=95)
    return filename


# ─────────────────────────────────────────────
# СБОР ДАННЫХ
# ─────────────────────────────────────────────

def collect_portfolio_data():
    account_id = get_account_id()
    portfolio = get_portfolio(account_id)
    dividends_total = get_dividends_and_coupons(account_id)

    positions = portfolio.get("positions", [])

    if not positions:
        raise Exception("Портфель пуст.")

    total_now = moneyval(portfolio.get("totalAmountPortfolio"))
    total_pnl = moneyval(portfolio.get("expectedYield"))

    allocation_values = {
        "bond": 0.0,
        "stock": 0.0,
        "fund": 0.0,
        "cash": 0.0,
        "other": 0.0,
    }

    top_positions = []

    for pos in positions:
        figi = pos.get("figi", "")
        qty = quotation(pos.get("quantity"))

        if qty == 0:
            continue

        name, kind, ticker = get_instrument_info(figi)

        current_price = moneyval(pos.get("currentPrice"))
        pos_val = current_price * qty

        asset_class = classify_asset(kind, name, ticker)

        if asset_class not in allocation_values:
            asset_class = "other"

        allocation_values[asset_class] += pos_val

        top_positions.append(
            {
                "name": name,
                "ticker": ticker,
                "asset_class": asset_class,
                "value": pos_val,
            }
        )

    if total_now <= 0:
        raise Exception("Не удалось определить текущую стоимость портфеля.")

    current_allocation = {}

    for asset_class, value in allocation_values.items():
        current_allocation[asset_class] = value / total_now if total_now > 0 else 0.0

    total_income = total_pnl + dividends_total

    forecast_data = build_forecast_data(total_now)

    rebalance_text = build_rebalance_text(current_allocation)

    return {
        "total_now": total_now,
        "total_pnl": total_pnl,
        "dividends_total": dividends_total,
        "total_income": total_income,
        "current_allocation": current_allocation,
        "forecast_data": forecast_data,
        "rebalance_text": rebalance_text,
        "top_positions": top_positions,
    }


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────

def send_text(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": text,
        },
        timeout=20,
    )

    r.raise_for_status()


def send_photo(filename, caption=""):
    with open(filename, "rb") as photo:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data={
                "chat_id": CHAT_ID,
                "caption": caption,
            },
            files={
                "photo": photo,
            },
            timeout=40,
        )

    r.raise_for_status()
    print("✅ Инфографика отправлена в Telegram!")


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

if __name__ == "__main__":
    try:
        data = collect_portfolio_data()

        image_file = create_portfolio_infographic(
            total_now=data["total_now"],
            total_pnl=data["total_pnl"],
            dividends_total=data["dividends_total"],
            total_income=data["total_income"],
            current_allocation=data["current_allocation"],
            forecast_data=data["forecast_data"],
            rebalance_text=data["rebalance_text"],
            filename="portfolio_infographic.png",
        )

        caption = f"📊 Портфель «{ACCOUNT_NAME}» • {datetime.now().strftime('%d.%m.%Y')}"

        send_photo(image_file, caption=caption)

    except Exception as e:
        error_text = f"⚠️ Ошибка формирования инфографики: {e}"
        print(error_text)
        send_text(error_text)
