import os
import sys
import subprocess
from datetime import datetime, timezone


# ─────────────────────────────────────────────
# АВТОУСТАНОВКА ЗАВИСИМОСТЕЙ
# ─────────────────────────────────────────────

def ensure_package(import_name, package_name):
    try:
        __import__(import_name)
    except ModuleNotFoundError:
        print(f"{package_name} не найден. Устанавливаю...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])


ensure_package("requests", "requests")
ensure_package("PIL", "pillow")

import requests
from PIL import Image, ImageDraw, ImageFont


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
TINVEST_TOKEN = os.environ.get("TINVEST_TOKEN", "")

ACCOUNT_NAME = os.environ.get("ACCOUNT_NAME", "Очень долгий срок")

# Тестовый режим без Т-Инвестиций:
# MOCK_MODE=1 python bot.py
MOCK_MODE = os.environ.get("MOCK_MODE", "0") == "1"

MONTHLY_INVEST = 60_000
YEARS = 17
INFLATION = 0.07

# 0.00 = пополнять всегда по 60 000 ₽
# 0.05 = ежегодно увеличивать пополнение на 5%
CONTRIBUTION_INDEXATION = 0.00

# Издержки модели: комиссии, налоги, спреды, ошибки, неидеальная ребалансировка
ANNUAL_COST_DRAG = 0.005

# Целевая структура под твой текущий портфель и горизонт 17 лет
TARGET_ALLOCATION = {
    "bond": 0.42,
    "stock": 0.23,
    "fund": 0.33,
    "cash": 0.02,
}

# Ожидаемые доходности по классам активов
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

# Ручная классификация твоих инструментов
MANUAL_ASSET_CLASS = {
    "НЛМК": "stock",
    "Норильский никель": "stock",
    "ГМК": "stock",

    "Кредитный поток 4.2": "bond",
    "ОФЗ 26207": "bond",
    "ОФЗ 26218": "bond",
    "ОФЗ 26226": "bond",
    "ОФЗ 26240": "bond",
    "РЖД БО 001Р-44R": "bond",
    "Роснефть 002Р-05": "bond",
    "Сбербанк 002Р-SBER": "bond",
    "SBER": "bond",

    "Пассивный доход": "fund",

    "Рубль": "cash",
    "RUB": "cash",
}


# ─────────────────────────────────────────────
# T-ИНВЕСТИЦИИ API
# ─────────────────────────────────────────────

TINVEST = "https://invest-public-api.tinkoff.ru/rest"


def get_headers():
    if not TINVEST_TOKEN and not MOCK_MODE:
        raise RuntimeError("Не задан TINVEST_TOKEN в GitHub Secrets")

    return {
        "Authorization": f"Bearer {TINVEST_TOKEN}",
        "Content-Type": "application/json",
    }


def ti_post(path, body=None):
    r = requests.post(
        f"{TINVEST}{path}",
        headers=get_headers(),
        json=body or {},
        timeout=25,
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
    raise RuntimeError(f"Счёт '{ACCOUNT_NAME}' не найден. Доступные счета: {names}")


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
        print(f"Ошибка получения купонов/дивидендов: {e}")

    return total


# ─────────────────────────────────────────────
# КЛАССИФИКАЦИЯ АКТИВОВ
# ─────────────────────────────────────────────

def classify_asset(kind, name="", ticker=""):
    name_clean = str(name).strip()
    ticker_clean = str(ticker).strip()

    for manual_name, asset_class in MANUAL_ASSET_CLASS.items():
        if manual_name.lower() in name_clean.lower():
            return asset_class

        if manual_name.lower() in ticker_clean.lower():
            return asset_class

    text = f"{kind} {name} {ticker}".lower()

    if any(w in text for w in ["bond", "облига", "офз", "замещающ", "еврооблиг", "бо ", "002р"]):
        return "bond"

    if any(w in text for w in ["etf", "fund", "бпиф", "пиф", "фонд", "пассивный доход", "ликвидность"]):
        return "fund"

    if any(w in text for w in ["share", "stock", "акци", "нлмк", "норильский никель", "гмк"]):
        return "stock"

    if any(w in text for w in ["currency", "money", "cash", "рубль", "доллар", "юань", "eur", "usd", "cny", "rub"]):
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


# ─────────────────────────────────────────────
# ПРОГНОЗ
# ─────────────────────────────────────────────

def weighted_expected_return(allocation, scenario):
    if abs(sum(allocation.values()) - 1.0) > 0.0001:
        raise RuntimeError("Сумма TARGET_ALLOCATION должна быть равна 1.00")

    gross = 0.0

    for asset_class, weight in allocation.items():
        class_returns = EXPECTED_RETURNS.get(asset_class, EXPECTED_RETURNS["other"])
        gross += weight * class_returns[scenario]

    return max(gross - ANNUAL_COST_DRAG, 0.0)


def real_rate(nominal_rate):
    return (1 + nominal_rate) / (1 + INFLATION) - 1


def future_value(current_value, monthly_invest, years, annual_rate):
    monthly_rate = (1 + annual_rate) ** (1 / 12) - 1

    fv = current_value * ((1 + annual_rate) ** years)

    payment = monthly_invest

    for month in range(1, years * 12 + 1):
        if month > 1 and (month - 1) % 12 == 0:
            payment *= 1 + CONTRIBUTION_INDEXATION

        months_to_grow = years * 12 - month + 1
        fv += payment * ((1 + monthly_rate) ** months_to_grow)

    return fv


def total_contributions(current_value):
    total = current_value
    payment = MONTHLY_INVEST

    for month in range(1, YEARS * 12 + 1):
        if month > 1 and (month - 1) % 12 == 0:
            payment *= 1 + CONTRIBUTION_INDEXATION

        total += payment

    return total


def build_forecast_data(total_now):
    invested = total_contributions(total_now)
    scenarios = []

    for key, name in [
        ("pessimistic", "Пессимистичный"),
        ("base", "Базовый"),
        ("optimistic", "Оптимистичный"),
    ]:
        rate = weighted_expected_return(TARGET_ALLOCATION, key)

        nominal = future_value(
            current_value=total_now,
            monthly_invest=MONTHLY_INVEST,
            years=YEARS,
            annual_rate=rate,
        )

        # Капитал в текущей покупательной способности
        real_value = nominal / ((1 + INFLATION) ** YEARS)

        scenarios.append(
            {
                "key": key,
                "name": name,
                "annual_rate": rate,
                "real_rate": real_rate(rate),

                "nominal": nominal,
                "real": real_value,

                "invested_total": invested,
                "investment_profit": nominal - invested,

                # Доход в будущих рублях — справочно
                "monthly_income_4_nominal": nominal * 0.04 / 12,
                "monthly_income_5_nominal": nominal * 0.05 / 12,

                # Главное: доход в текущих деньгах
                "monthly_income_4_real": real_value * 0.04 / 12,
                "monthly_income_5_real": real_value * 0.05 / 12,
            }
        )

    return {
        "invested_total": invested,
        "scenarios": scenarios,
    }


def build_rebalance_details(current_allocation):
    details = []
    underweight = []
    overweight = []

    for asset_class, target_weight in TARGET_ALLOCATION.items():
        current_weight = current_allocation.get(asset_class, 0.0)
        diff = current_weight - target_weight

        row = {
            "asset_class": asset_class,
            "current": current_weight,
            "target": target_weight,
            "diff": diff,
        }

        details.append(row)

        if diff < -0.03:
            underweight.append(row)

        if diff > 0.03:
            overweight.append(row)

    underweight.sort(key=lambda x: abs(x["diff"]), reverse=True)
    overweight.sort(key=lambda x: abs(x["diff"]), reverse=True)

    return details, underweight, overweight


def build_rebalance_text(current_allocation):
    details, underweight, overweight = build_rebalance_details(current_allocation)

    if not underweight and not overweight:
        return "Структура близка к целевой. Новые пополнения можно распределять по плану."

    parts = []

    if underweight:
        main = underweight[0]
        parts.append(
            f"Главный недовес: {asset_class_ru(main['asset_class'])} "
            f"({abs(main['diff']) * 100:.1f} п.п.)."
        )

    if overweight:
        main_over = overweight[0]
        parts.append(
            f"Главный перевес: {asset_class_ru(main_over['asset_class'])} "
            f"({main_over['diff'] * 100:.1f} п.п.)."
        )

    if underweight:
        parts.append("Новые пополнения лучше направлять в недостающие классы.")

    return " ".join(parts)


def build_next_contribution_plan(current_allocation):
    """
    Распределяет следующее пополнение 60 000 ₽ в классы с недовесом.
    Если недовесов нет — по целевой структуре.
    """

    _, underweight, _ = build_rebalance_details(current_allocation)

    plan = {
        "bond": 0.0,
        "stock": 0.0,
        "fund": 0.0,
        "cash": 0.0,
    }

    if not underweight:
        for asset_class, weight in TARGET_ALLOCATION.items():
            plan[asset_class] = MONTHLY_INVEST * weight

        return plan

    total_gap = sum(abs(row["diff"]) for row in underweight)

    for row in underweight:
        asset_class = row["asset_class"]
        gap = abs(row["diff"])
        plan[asset_class] = MONTHLY_INVEST * gap / total_gap

    return plan


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


def fmt_pct(value):
    return f"{value * 100:.1f}%"


# ─────────────────────────────────────────────
# ШРИФТЫ И РИСОВАНИЕ
# ─────────────────────────────────────────────

def load_font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Arial Bold.ttf" if bold else "Arial.ttf",
    ]

    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue

    return ImageFont.load_default()


def draw_wrapped(draw, text, xy, max_width, font, fill, line_gap=8):
    x, y = xy
    words = str(text).split()
    line = ""

    for word in words:
        test = word if not line else line + " " + word
        bbox = draw.textbbox((0, 0), test, font=font)

        if bbox[2] - bbox[0] <= max_width:
            line = test
        else:
            if line:
                draw.text((x, y), line, font=font, fill=fill)
                y += getattr(font, "size", 24) + line_gap
            line = word

    if line:
        draw.text((x, y), line, font=font, fill=fill)


def draw_card(draw, box, fill, outline, radius=34, width=2):
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=fill,
        outline=outline,
        width=width,
    )


def draw_metric(draw, x, y, w, h, title, value, note, value_color, fonts, colors):
    draw_card(draw, (x, y, x + w, y + h), colors["card"], colors["border"])

    draw.text(
        (x + 30, y + 24),
        title,
        font=fonts["card_title"],
        fill=colors["muted"],
    )

    draw.text(
        (x + 30, y + 76),
        value,
        font=fonts["card_value"],
        fill=value_color,
    )

    draw_wrapped(
        draw,
        note,
        (x + 30, y + 142),
        w - 60,
        fonts["small"],
        colors["muted"],
    )


def draw_bar(draw, x, y, w, label, value, color, fonts, colors):
    value = max(0, min(value, 1))

    draw.text(
        (x, y),
        label,
        font=fonts["text"],
        fill=colors["text"],
    )

    draw.text(
        (x + w - 120, y),
        f"{value * 100:.1f}%",
        font=fonts["text"],
        fill=colors["muted"],
    )

    draw.rounded_rectangle(
        (x, y + 42, x + w, y + 68),
        radius=13,
        fill=colors["bar_bg"],
    )

    if value > 0:
        draw.rounded_rectangle(
            (x, y + 42, x + int(w * value), y + 68),
            radius=13,
            fill=color,
        )


def draw_contribution_plan(draw, x, y, w, plan, fonts, colors):
    draw_card(draw, (x, y, x + w, y + 230), colors["card_soft"], colors["border"])

    draw.text(
        (x + 32, y + 28),
        "Следующее пополнение",
        font=fonts["card_title"],
        fill=colors["text"],
    )

    draw.text(
        (x + 32, y + 76),
        f"{fmt_money(MONTHLY_INVEST)}",
        font=fonts["card_value"],
        fill=colors["green"],
    )

    line_y = y + 140

    items = [
        ("bond", "Облигации"),
        ("stock", "Акции"),
        ("fund", "Фонды"),
        ("cash", "Деньги"),
    ]

    col_x = x + 32

    for asset_class, label in items:
        amount = plan.get(asset_class, 0.0)

        if amount < 1:
            continue

        draw.text(
            (col_x, line_y),
            f"{label}: {fmt_short(amount)}",
            font=fonts["small"],
            fill=colors["muted"],
        )

        col_x += 310

        if col_x > x + w - 260:
            col_x = x + 32
            line_y += 38


# ─────────────────────────────────────────────
# ИНФОГРАФИКА
# ─────────────────────────────────────────────

def create_infographic(data, filename="portfolio_infographic.png"):
    width = 1600
    height = 2300

    colors = {
        "bg": "#0F1117",
        "card": "#181B23",
        "card_soft": "#1F2430",
        "card_gold": "#241E14",
        "text": "#F3F4F6",
        "muted": "#9CA3AF",
        "muted2": "#6B7280",
        "border": "#2D3340",
        "green": "#22C55E",
        "red": "#EF4444",
        "gold": "#F59E0B",
        "blue": "#60A5FA",
        "purple": "#A78BFA",
        "bar_bg": "#2A2F3B",
    }

    fonts = {
        "title": load_font(66, True),
        "subtitle": load_font(30),
        "h2": load_font(40, True),
        "card_title": load_font(30, True),
        "card_value": load_font(42, True),
        "text": load_font(27),
        "text_bold": load_font(27, True),
        "small": load_font(22),
        "tiny": load_font(18),
        "scenario_title": load_font(32, True),
        "scenario_value": load_font(34, True),
    }

    img = Image.new("RGB", (width, height), colors["bg"])
    draw = ImageDraw.Draw(img)

    # Декор
    draw.ellipse((1120, -260, 1820, 460), fill="#182033")
    draw.ellipse((-260, 1580, 380, 2240), fill="#1A261E")
    draw.ellipse((1080, 1680, 1780, 2380), fill="#201A2E")

    # Заголовок
    draw.text(
        (80, 70),
        "Портфель: долгий срок",
        font=fonts["title"],
        fill=colors["text"],
    )

    draw.text(
        (80, 152),
        f"Инфографика • {datetime.now().strftime('%d.%m.%Y')}",
        font=fonts["subtitle"],
        fill=colors["muted"],
    )

    draw.text(
        (80, 198),
        "Прогноз считает доход в месяц в текущей покупательной способности",
        font=fonts["small"],
        fill=colors["gold"],
    )

    total_now = data["total_now"]
    total_pnl = data["total_pnl"]
    dividends_total = data["dividends_total"]
    total_income = data["total_income"]

    # Верхние метрики
    draw_metric(
        draw,
        80,
        280,
        680,
        185,
        "Текущий капитал",
        fmt_short(total_now),
        "Стоимость портфеля сейчас",
        colors["text"],
        fonts,
        colors,
    )

    draw_metric(
        draw,
        840,
        280,
        680,
        185,
        "Доход от цены",
        fmt_short(total_pnl),
        "Результат по позициям",
        colors["green"] if total_pnl >= 0 else colors["red"],
        fonts,
        colors,
    )

    draw_metric(
        draw,
        80,
        505,
        680,
        185,
        "Купоны и дивиденды",
        fmt_short(dividends_total),
        "Получено за всё время",
        colors["gold"],
        fonts,
        colors,
    )

    draw_metric(
        draw,
        840,
        505,
        680,
        185,
        "Общий доход",
        fmt_short(total_income),
        "Цена + выплаты",
        colors["green"] if total_income >= 0 else colors["red"],
        fonts,
        colors,
    )

    # Структура портфеля
    draw_card(draw, (80, 760, 1520, 1260), colors["card"], colors["border"])

    palette = {
        "bond": colors["blue"],
        "stock": colors["green"],
        "fund": colors["purple"],
        "cash": colors["gold"],
        "other": "#94A3B8",
    }

    draw.text(
        (120, 800),
        "Текущая структура",
        font=fonts["h2"],
        fill=colors["text"],
    )

    y = 870

    for asset_class in ["bond", "stock", "fund", "cash", "other"]:
        if asset_class == "other" and data["current_allocation"].get(asset_class, 0) <= 0.005:
            continue

        draw_bar(
            draw,
            120,
            y,
            620,
            asset_class_ru(asset_class),
            data["current_allocation"].get(asset_class, 0),
            palette[asset_class],
            fonts,
            colors,
        )

        y += 82

    draw.text(
        (840, 800),
        "Целевая структура",
        font=fonts["h2"],
        fill=colors["text"],
    )

    y = 870

    for asset_class, value in TARGET_ALLOCATION.items():
        draw_bar(
            draw,
            840,
            y,
            620,
            asset_class_ru(asset_class),
            value,
            palette.get(asset_class, "#94A3B8"),
            fonts,
            colors,
        )

        y += 82

    # Рекомендация
    draw_card(draw, (80, 1305, 1520, 1465), colors["card_gold"], "#5D4320")

    draw.text(
        (120, 1340),
        "Что делать с новыми пополнениями",
        font=fonts["card_title"],
        fill=colors["gold"],
    )

    draw_wrapped(
        draw,
        data["rebalance_text"],
        (120, 1393),
        1320,
        fonts["text"],
        colors["text"],
    )

    # План следующего пополнения
    draw_contribution_plan(
        draw,
        80,
        1500,
        1440,
        data["next_contribution_plan"],
        fonts,
        colors,
    )

    # Прогноз
    draw.text(
        (80, 1770),
        f"Прогноз на {YEARS} лет",
        font=fonts["h2"],
        fill=colors["text"],
    )

    draw.text(
        (80, 1825),
        f"Пополнение {fmt_money(MONTHLY_INVEST)}/мес • инфляция {INFLATION * 100:.1f}% • доход/мес показан в ценах сегодня",
        font=fonts["small"],
        fill=colors["muted"],
    )

    scenario_colors = {
        "pessimistic": colors["red"],
        "base": colors["gold"],
        "optimistic": colors["green"],
    }

    y = 1895

    for item in data["forecast_data"]["scenarios"]:
        draw_card(draw, (80, y, 1520, y + 150), colors["card"], colors["border"])

        accent = scenario_colors[item["key"]]

        draw.rounded_rectangle(
            (80, y, 100, y + 150),
            radius=8,
            fill=accent,
        )

        draw.text(
            (120, y + 24),
            item["name"],
            font=fonts["scenario_title"],
            fill=accent,
        )

        draw.text(
            (120, y + 76),
            f"{item['annual_rate'] * 100:.1f}% ном. / {item['real_rate'] * 100:.1f}% реал.",
            font=fonts["small"],
            fill=colors["muted"],
        )

        draw.text(
            (425, y + 24),
            f"Капитал через {YEARS} лет",
            font=fonts["small"],
            fill=colors["muted"],
        )

        draw.text(
            (425, y + 64),
            fmt_short(item["nominal"]),
            font=fonts["scenario_value"],
            fill=colors["text"],
        )

        draw.text(
            (760, y + 24),
            "Капитал сегодня",
            font=fonts["small"],
            fill=colors["muted"],
        )

        draw.text(
            (760, y + 64),
            fmt_short(item["real"]),
            font=fonts["scenario_value"],
            fill=colors["text"],
        )

        draw.text(
            (1135, y + 24),
            "Доход/мес сегодня",
            font=fonts["small"],
            fill=colors["muted"],
        )

        draw.text(
            (1135, y + 64),
            fmt_short(item["monthly_income_4_real"]),
            font=fonts["scenario_value"],
            fill=colors["green"],
        )

        draw.text(
            (1135, y + 108),
            "по правилу 4%",
            font=fonts["tiny"],
            fill=colors["muted2"],
        )

        y += 180

    # Вложения
    draw_card(draw, (80, y + 20, 1520, y + 160), "#13251A", "#295C38")

    draw.text(
        (120, y + 52),
        "Собственные вложения за весь период",
        font=fonts["card_title"],
        fill=colors["green"],
    )

    draw.text(
        (120, y + 100),
        fmt_short(data["forecast_data"]["invested_total"]),
        font=fonts["scenario_value"],
        fill=colors["text"],
    )

    draw.text(
        (540, y + 104),
        "текущий портфель + будущие ежемесячные пополнения",
        font=fonts["small"],
        fill=colors["muted"],
    )

    # Подвал
    draw.text(
        (80, height - 90),
        "⚠️ Сценарная модель, не гарантия доходности. Доход/мес рассчитан в текущих деньгах.",
        font=fonts["small"],
        fill=colors["muted"],
    )

    img.save(filename, quality=95)
    return filename


# ─────────────────────────────────────────────
# СБОР ДАННЫХ
# ─────────────────────────────────────────────

def collect_portfolio_data():
    if MOCK_MODE:
        total_now = 130_772.0
        total_pnl = -2_237.42
        dividends_total = 685.0

        current_allocation = {
            "bond": 0.415,
            "stock": 0.176,
            "fund": 0.402,
            "cash": 0.007,
            "other": 0.0,
        }

        return {
            "total_now": total_now,
            "total_pnl": total_pnl,
            "dividends_total": dividends_total,
            "total_income": total_pnl + dividends_total,
            "current_allocation": current_allocation,
            "forecast_data": build_forecast_data(total_now),
            "rebalance_text": build_rebalance_text(current_allocation),
            "next_contribution_plan": build_next_contribution_plan(current_allocation),
        }

    account_id = get_account_id()
    portfolio = get_portfolio(account_id)
    dividends_total = get_dividends_and_coupons(account_id)

    positions = portfolio.get("positions", [])

    if not positions:
        raise RuntimeError("Портфель пуст")

    total_now = moneyval(portfolio.get("totalAmountPortfolio"))
    total_pnl = moneyval(portfolio.get("expectedYield"))

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

        # Для валюты/денег иногда цена может быть 0 или отсутствовать.
        # Тогда берём текущую стоимость позиции из currentNkd / averagePositionPrice / fallback.
        position_value = current_price * qty

        if position_value <= 0:
            average_price = moneyval(pos.get("averagePositionPrice"))
            position_value = average_price * qty

        asset_class = classify_asset(kind, name, ticker)

        if asset_class not in allocation_values:
            asset_class = "other"

        allocation_values[asset_class] += position_value

    if total_now <= 0:
        raise RuntimeError("Не удалось определить стоимость портфеля")

    current_allocation = {
        asset_class: value / total_now
        for asset_class, value in allocation_values.items()
    }

    return {
        "total_now": total_now,
        "total_pnl": total_pnl,
        "dividends_total": dividends_total,
        "total_income": total_pnl + dividends_total,
        "current_allocation": current_allocation,
        "forecast_data": build_forecast_data(total_now),
        "rebalance_text": build_rebalance_text(current_allocation),
        "next_contribution_plan": build_next_contribution_plan(current_allocation),
    }


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────

def send_text(text):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(text)
        return

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
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print(f"Telegram secrets не заданы. Файл создан: {filename}")
        return

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
            timeout=45,
        )

    r.raise_for_status()
    print("✅ Инфографика отправлена в Telegram")


# ─────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────

if __name__ == "__main__":
    try:
        data = collect_portfolio_data()

        image_file = create_infographic(
            data=data,
            filename="portfolio_infographic.png",
        )

        caption = f"📊 Портфель «{ACCOUNT_NAME}» • {datetime.now().strftime('%d.%m.%Y')}"

        send_photo(image_file, caption)

    except Exception as e:
        msg = f"⚠️ Ошибка формирования инфографики: {e}"
        print(msg)
        send_text(msg)
