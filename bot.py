import os
import requests
from datetime import datetime, timedelta

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Дата покупки всего портфеля
PURCHASE_DATE = "2025-12-08"

# Портфель
PORTFOLIO = [
    {"secid": "SU26207RMFS9", "name": "ОФЗ 26207",              "qty": 3,   "market": "bonds"},
    {"secid": "SU26218RMFS6", "name": "ОФЗ 26218",              "qty": 5,   "market": "bonds"},
    {"secid": "SU26226RMFS9", "name": "ОФЗ 26226",              "qty": 1,   "market": "bonds"},
    {"secid": "RU000A0ZYVU5", "name": "Роснефть 002Р-05",       "qty": 3,   "market": "bonds"},
    {"secid": "RU000A106375", "name": "РЖД БО 001Р-44R",        "qty": 9,   "market": "bonds"},
    {"secid": "RU000A1069P3", "name": "Сбербанк 2P-SBER44",     "qty": 4,   "market": "bonds"},
    {"secid": "TPAY",         "name": "Пассивный доход (TPAY)", "qty": 380, "market": "shares"},
]

MOEX_BASE = "https://iss.moex.com/iss"


def get_current_price(secid, market):
    if market == "bonds":
        url = f"{MOEX_BASE}/engines/stock/markets/bonds/securities/{secid}.json?iss.meta=off&iss.only=marketdata,securities"
    else:
        url = f"{MOEX_BASE}/engines/stock/markets/shares/securities/{secid}.json?iss.meta=off&iss.only=marketdata,securities"

    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    md = data.get("marketdata", {})
    cols = md.get("columns", [])
    rows = md.get("data", [])
    if rows:
        d = dict(zip(cols, rows[0]))
        price = d.get("LAST") or d.get("MARKETPRICE") or d.get("LCURRENTPRICE")
        if price:
            return float(price)

    sec = data.get("securities", {})
    cols = sec.get("columns", [])
    rows = sec.get("data", [])
    if rows:
        d = dict(zip(cols, rows[0]))
        price = d.get("PREVPRICE") or d.get("PREVLEGALCLOSEPRICE")
        if price:
            return float(price)

    return None


def get_price_on_date(secid, market, date_str):
    """Получить цену на конкретную дату."""
    # Берём диапазон +3 дня на случай выходных
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_end = (dt + timedelta(days=5)).strftime("%Y-%m-%d")

    if market == "bonds":
        url = f"{MOEX_BASE}/history/engines/stock/markets/bonds/securities/{secid}.json?from={date_str}&till={date_end}&iss.meta=off&iss.only=history"
    else:
        url = f"{MOEX_BASE}/history/engines/stock/markets/shares/securities/{secid}.json?from={date_str}&till={date_end}&iss.meta=off&iss.only=history"

    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    history = data.get("history", {})
    cols = history.get("columns", [])
    rows = history.get("data", [])

    if not rows:
        return None

    d = dict(zip(cols, rows[0]))
    price = d.get("CLOSE") or d.get("LEGALCLOSEPRICE") or d.get("OPEN") or d.get("WAPRICE")
    return float(price) if price else None


def get_week_prices(secid, market):
    today = datetime.now()
    week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    if market == "bonds":
        url = f"{MOEX_BASE}/history/engines/stock/markets/bonds/securities/{secid}.json?from={week_ago}&till={today_str}&iss.meta=off&iss.only=history"
    else:
        url = f"{MOEX_BASE}/history/engines/stock/markets/shares/securities/{secid}.json?from={week_ago}&till={today_str}&iss.meta=off&iss.only=history"

    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    history = data.get("history", {})
    cols = history.get("columns", [])
    rows = history.get("data", [])

    if not rows:
        return None, None

    d_first = dict(zip(cols, rows[0]))
    d_last = dict(zip(cols, rows[-1]))

    price_start = d_first.get("OPEN") or d_first.get("CLOSE") or d_first.get("LEGALCLOSEPRICE")
    price_end = d_last.get("CLOSE") or d_last.get("LEGALCLOSEPRICE") or d_last.get("WAPRICE")

    return (
        float(price_start) if price_start else None,
        float(price_end) if price_end else None,
    )


def get_ai_analysis(name, current_price, purchase_price, week_change_pct, since_purchase_pct):
    """Получить мини-анализ от Claude."""
    prompt = f"""Ты финансовый помощник. Напиши короткий понятный анализ для обычного человека (не финансиста) по облигации/фонду "{name}".

Данные:
- Текущая цена: {current_price:.2f} ₽
- Цена покупки (08.12.2025): {purchase_price:.2f} ₽ (если нет данных — напиши об этом)
- Изменение за неделю: {week_change_pct:+.2f}%
- Изменение с момента покупки: {since_purchase_pct:+.2f}%

Напиши 3-4 предложения в простом стиле:
1. Как ведёт себя инструмент (растёт/падает/стабилен)
2. Хорошо это или плохо для владельца
3. Краткая рекомендация: держать, докупить или обратить внимание

Без сложных терминов. Как будто объясняешь другу. Только текст, без заголовков и списков."""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 300,
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=30
    )
    response.raise_for_status()
    data = response.json()
    return data["content"][0]["text"].strip()


def build_report():
    lines = []
    today_str = datetime.now().strftime("%d.%m.%Y")
    lines.append("📊 *Еженедельный отчёт по портфелю*")
    lines.append(f"📅 {today_str}  |  Куплено: 08.12.2025\n")

    total_value = 0.0
    total_purchase_value = 0.0
    total_week_change = 0.0

    for item in PORTFOLIO:
        secid = item["secid"]
        name = item["name"]
        qty = item["qty"]
        market = item["market"]

        lines.append(f"━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"*{name}* ({qty} шт.)")

        current = None
        purchase_price = None
        week_change_pct = 0
        since_purchase_pct = 0

        try:
            current = get_current_price(secid, market)
            price_start_week, price_end_week = get_week_prices(secid, market)
            purchase_price_raw = get_price_on_date(secid, market, PURCHASE_DATE)

            # Множитель для облигаций (цена в % от номинала 1000 ₽)
            multiplier = (1000 / 100) if (market == "bonds" and current and current < 200) else 1

            if current:
                real_price = current * multiplier
                position_value = real_price * qty
                total_value += position_value
                lines.append(f"  💰 Цена сейчас: *{real_price:.2f} ₽*  |  Позиция: *{position_value:,.2f} ₽*")
            else:
                real_price = 0
                lines.append("  💰 Цена: нет данных")

            # Изменение за неделю
            if price_start_week and price_end_week:
                ps = price_start_week * multiplier
                pe = price_end_week * multiplier
                change = pe - ps
                week_change_pct = (change / ps * 100) if ps else 0
                position_week_change = change * qty
                total_week_change += position_week_change
                arrow = "🟢" if change >= 0 else "🔴"
                lines.append(f"  {arrow} За неделю: {change:+.2f} ₽ ({week_change_pct:+.2f}%)")
            else:
                lines.append("  📉 За неделю: нет данных")

            # Изменение с момента покупки
            if purchase_price_raw and current:
                purchase_price = purchase_price_raw * multiplier
                purchase_position = purchase_price * qty
                total_purchase_value += purchase_position
                since_change = real_price - purchase_price
                since_purchase_pct = (since_change / purchase_price * 100) if purchase_price else 0
                since_position_change = since_change * qty
                arrow2 = "🟢" if since_change >= 0 else "🔴"
                lines.append(f"  {arrow2} С покупки (08.12.25): {since_change:+.2f} ₽ ({since_purchase_pct:+.2f}%)  |  {since_position_change:+.2f} ₽")
            else:
                purchase_price = real_price
                lines.append("  📊 С покупки: нет данных")

            # AI-анализ
            try:
                analysis = get_ai_analysis(
                    name,
                    real_price,
                    purchase_price or real_price,
                    week_change_pct,
                    since_purchase_pct
                )
                lines.append(f"\n  🤖 _{analysis}_")
            except Exception as e:
                lines.append(f"  🤖 _Анализ недоступен: {e}_")

        except Exception as e:
            lines.append(f"  ⚠️ Ошибка загрузки: {e}")

        lines.append("")

    # Итог
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Стоимость портфеля: {total_value:,.2f} ₽*")
    arrow_week = "🟢" if total_week_change >= 0 else "🔴"
    lines.append(f"{arrow_week} *За неделю: {total_week_change:+,.2f} ₽*")

    if total_purchase_value > 0:
        total_since = total_value - total_purchase_value
        total_since_pct = (total_since / total_purchase_value * 100) if total_purchase_value else 0
        arrow_since = "🟢" if total_since >= 0 else "🔴"
        lines.append(f"{arrow_since} *С 08.12.2025: {total_since:+,.2f} ₽ ({total_since_pct:+.2f}%)*")

    lines.append("")
    lines.append("_Данные: Московская Биржа (ISS MOEX API)_")

    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    print("✅ Отчёт отправлен!")


if __name__ == "__main__":
    report = build_report()
    print(report)
    send_telegram(report)
