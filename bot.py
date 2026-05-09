import os
import sys
import subprocess
from datetime import datetime, timezone


# ─────────────────────────────────────────────
# АВТОУСТАНОВКА PILLOW, ЕСЛИ В GITHUB ACTIONS ЕГО НЕТ
# ─────────────────────────────────────────────

def ensure_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa
        return
    except ModuleNotFoundError:
        print("Pillow не найден. Устанавливаю pillow...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])


ensure_pillow()

import requests
from PIL import Image, ImageDraw, ImageFont


# ─────────────────────────────────────────────
# НАСТРОЙКИ
# ─────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
TINVEST_TOKEN = os.environ.get("TINVEST_TOKEN", "")

ACCOUNT_NAME = os.environ.get("ACCOUNT_NAME", "Очень долгий срок")

# Тестовый режим без API Т-Инвестиций:
# MOCK_MODE=1 python bot.py
MOCK_MODE = os.environ.get("MOCK_MODE", "0") == "1"

MONTHLY_INVEST = 60_000
YEARS = 17
INFLATION = 0.07

# 0.00 = всегда 60 000 ₽/мес
# 0.05 = ежегодно увеличивать пополнение на 5%
CONTRIBUTION_INDEXATION = 0.00

# Условный минус к доходности на комиссии, налоги, спреды и ошибки
ANNUAL_COST_DRAG = 0.005

# Целевая структура будущих покупок
TARGET_ALLOCATION = {
    "bond": 0.50,
    "stock": 0.30,
    "fund": 0.15,
    "cash": 0.05,
}

# Сценарные доходности по классам активов
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


def headers():
    if not TINVEST_TOKEN and not MOCK_MODE:
        raise RuntimeError("Не задан секрет TINVEST_TOKEN")

    return {
        "Authorization": f"Bearer {TINVEST_TOKEN}",
        "Content-Type": "application/json",
    }


def ti_post(path, body=None):
    r = requests.post(
        f"{TINVEST}{path}",
        headers=headers(),
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
    raise RuntimeError(f"Счёт '{ACCOUNT_NAME}' не найден. Доступные: {names}")


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
    text = f"{kind} {name} {ticker}".lower()

    if any(w in text for w in ["bond", "облига", "офз", "замещающ", "еврооблиг"]):
        return "bond"

    if any(w in text for w in ["etf", "fund", "бпиф", "пиф", "фонд", "ликвидность"]):
        return "fund"

    if any(w in text for w in ["share", "stock", "акци", "ао ", "ап ", "обыкнов", "привилег"]):
        return "stock"

    if any(w in text for w in ["currency", "money", "cash", "рубль", "доллар", "юань", "eur", "usd", "cny"]):
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
        raise RuntimeError("Сумма TARGET_ALLOCATION должна быть 1.00")

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
                "monthly_rent_4": nominal * 0.04 / 12,
                "monthly_rent_5": nominal * 0.05 / 12,
            }
        )

    return {
        "invested_total": invested,
        "scenarios": scenarios,
    }


def build_rebalance_text(current_allocation):
    underweight = []

    for asset_class, target_weight in TARGET_ALLOCATION.items():
        current_weight = current_allocation.get(asset_class, 0.0)
        diff = current_weight - target_weight

        if diff < -0.03:
            underweight.append((asset_class, abs(diff)))

    if not underweight:
        return "Структура близка к целевой. Новые пополнения можно распределять по плану."

    underweight.sort(key=lambda x: x[1], reverse=True)

    main_asset_class = underweight[0][0]

    return f"Главный недовес: {asset_class_ru(main_asset_class)}. Новые пополнения логично направлять туда."


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


# ─────────────────────────────────────────────
# РИСОВАНИЕ PNG
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
            pass

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
            draw.text((x, y), line, font=font, fill=fill)
            y += getattr(font, "size", 24) + line_gap
            line = word

    if line:
        draw.text((x, y), line, font=font, fill=fill)


def card(draw, box, fill, outline, radius=32):
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=fill,
        outline=outline,
        width=2,
    )


def metric(draw, x, y, w, h, title, value, note, value_color, fonts, colors):
    card(draw, (x, y, x + w, y + h), colors["card"], colors["border"])

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


def bar(draw, x, y, w, label, value, color, fonts, colors):
    value = max(0, min(value, 1))

    draw.text(
        (x, y),
        label,
        font=fonts["text"],
        fill=colors["dark"],
    )

    draw.text(
        (x + w - 110, y),
        f"{value * 100:.1f}%",
        font=fonts["text"],
        fill=colors["muted"],
    )

    draw.rounded_rectangle(
        (x, y + 42, x + w, y + 66),
        radius=12,
        fill=colors["bar_bg"],
    )

    if value > 0:
        draw.rounded_rectangle(
            (x, y + 42, x + int(w * value), y + 66),
            radius=12,
            fill=color,
        )


def create_infographic(data, filename="portfolio_infographic.png"):
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
        "bar_bg": "#ECE7DF",
    }

    fonts = {
        "title": load_font(64, True),
        "subtitle": load_font(30),
        "h2": load_font(38, True),
        "card_title": load_font(30, True),
        "card_value": load_font(42, True),
        "text": load_font(27),
        "small": load_font(22),
        "scenario_title": load_font(32, True),
        "scenario_value": load_font(34, True),
    }

    img = Image.new("RGB", (width, height), colors["bg"])
    draw = ImageDraw.Draw(img)

    # Декор
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
        f"Инфографика • {datetime.now().strftime('%d.%m.%Y')}",
        font=fonts["subtitle"],
        fill=colors["muted"],
    )

    total_now = data["total_now"]
    total_pnl = data["total_pnl"]
    dividends_total = data["dividends_total"]
    total_income = data["total_income"]

    metric(
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

    metric(
        draw,
        840,
        240,
        680,
        185,
        "Доход от цены",
        fmt_short(total_pnl),
        "Текущий результат по позициям",
        colors["green"] if total_pnl >= 0 else colors["red"],
        fonts,
        colors,
    )

    metric(
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

    metric(
        draw,
        840,
        465,
        680,
        185,
        "Общий доход",
        fmt_short(total_income),
        "Цена + выплаты",
        colors["green"] if total_income >= 0 else colors["red"],
        fonts,
        colors,
    )

    # Структура
    card(draw, (80, 720, 1520, 1220), colors["card"], colors["border"])

    palette = {
        "bond": "#3B82F6",
        "stock": "#16A34A",
        "fund": "#A855F7",
        "cash": "#F59E0B",
        "other": "#64748B",
    }

    draw.text(
        (120, 760),
        "Текущая структура",
        font=fonts["h2"],
        fill=colors["dark"],
    )

    y = 830

    for asset_class in ["bond", "stock", "fund", "cash", "other"]:
        if asset_class == "other" and data["current_allocation"].get(asset_class, 0) <= 0.005:
            continue

        bar(
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
        (840, 760),
        "Целевая структура",
        font=fonts["h2"],
        fill=colors["dark"],
    )

    y = 830

    for asset_class, value in TARGET_ALLOCATION.items():
        bar(
            draw,
            840,
            y,
            620,
            asset_class_ru(asset_class),
            value,
            palette.get(asset_class, "#64748B"),
            fonts,
            colors,
        )

        y += 82

    # Рекомендация
    card(draw, (80, 1260, 1520, 1410), "#FFF8E7", "#EACB7A")

    draw.text(
        (120, 1295),
        "Рекомендация по пополнениям",
        font=fonts["card_title"],
        fill=colors["gold"],
    )

    draw_wrapped(
        draw,
        data["rebalance_text"],
        (120, 1345),
        1320,
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
        f"Пополнение: {fmt_money(MONTHLY_INVEST)}/мес • Инфляция: {INFLATION * 100:.1f}% • Индексация: {CONTRIBUTION_INDEXATION * 100:.1f}%",
        font=fonts["small"],
        fill=colors["muted"],
    )

    scenario_colors = {
        "pessimistic": colors["red"],
        "base": colors["gold"],
        "optimistic": colors["green"],
    }

    y = 1605

    for item in data["forecast_data"]["scenarios"]:
        card(draw, (80, y, 1520, y + 150), colors["card"], colors["border"])

        accent = scenario_colors[item["key"]]

        draw.rounded_rectangle(
            (80, y, 98, y + 150),
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

    # Вложения
    card(draw, (80, y + 20, 1520, y + 165), "#EEF7EE", "#B8D9B8")

    draw.text(
        (120, y + 55),
        "Собственные вложения за весь период",
        font=fonts["card_title"],
        fill=colors["green"],
    )

    draw.text(
        (120, y + 103),
        fmt_short(data["forecast_data"]["invested_total"]),
        font=fonts["scenario_value"],
        fill=colors["dark"],
    )

    draw.text(
        (540, y + 107),
        "текущий портфель + будущие ежемесячные пополнения",
        font=fonts["small"],
        fill=colors["muted"],
    )

    # Подвал
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
    if MOCK_MODE:
        total_now = 850_000.0
        total_pnl = 42_500.0
        dividends_total = 18_200.0

        current_allocation = {
            "bond": 0.68,
            "stock": 0.12,
            "fund": 0.15,
            "cash": 0.05,
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
        qty = quotation(pos.get("quantity"))

        if qty == 0:
            continue

        figi = pos.get("figi", "")

        name, kind, ticker = get_instrument_info(figi)

        current_price = moneyval(pos.get("currentPrice"))
        position_value = current_price * qty

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
