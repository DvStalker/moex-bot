import os
import requests
from datetime import datetime, timedelta

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Портфель облигаций и фондов
PORTFOLIO = [
    {"secid": "SU26207RMFS9", "name": "ОФЗ 26207",              "qty": 3,   "market": "bonds"},
    {"secid": "SU26218RMFS6", "name": "ОФЗ 26218",              "qty": 5,   "market": "bonds"},
    {"secid": "SU26226RMFS9", "name": "ОФЗ 26226",              "qty": 1,   "market": "bonds"},
    {"secid": "RU000A101QC1", "name": "Роснефть 002Р",          "qty": 3,   "market": "bonds"},
    {"secid": "RU000A106375", "name": "РЖД БО 001Р-44R",        "qty": 9,   "market": "bonds"},
    {"secid": "RU000A105RQ5", "name": "Сбербанк 2P-SBER44",     "qty": 4,   "market": "bonds"},
    {"secid": "TPAY",         "name": "Пассивный доход (TPAY)", "qty": 380, "market": "shares"},
]

MOEX_BASE = "https://iss.moex.com/iss"


def get_current_price(secid, market):
    """Получить текущую/последнюю цену инструмента."""
    if market == "bonds":
        url = (
            f"{MOEX_BASE}/engines/stock/markets/bonds/securities/{secid}.json"
            f"?iss.meta=off&iss.only=marketdata,securities"
        )
    else:
        url = (
            f"{MOEX_BASE}/engines/stock/markets/shares/securities/{secid}.json"
            f"?iss.meta=off&iss.only=marketdata,securities"
        )

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


def get_week_prices(secid, market):
    """Получить цены за последние 7 дней."""
    today = datetime.now()
    week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    if market == "bonds":
        url = (
            f"{MOEX_BASE}/history/engines/stock/markets/bonds/securities/{secid}.json"
            f"?from={week_ago}&till={today_str}&iss.meta=off&iss.only=history"
        )
    else:
        url = (
            f"{MOEX_BASE}/history/engines/stock/markets/shares/securities/{secid}.json"
            f"?from={week_ago}&till={today_str}&iss.meta=off&iss.only=history"
        )

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


def get_bond_events(secid):
    """Получить ближайшие события по облигации."""
    url = f"{MOEX_BASE}/securities/{secid}/events.json?iss.meta=off"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        events = data.get("events", {})
        cols = events.get("columns", [])
        rows = events.get("data", [])

        result = []
        for row in rows[:3]:
            d = dict(zip(cols, row))
            title = d.get("title") or d.get("event_type") or "Событие"
            date = d.get("event_date") or d.get("announce_date") or ""
            result.append(f"    • {date}: {title}")

        return result if result else ["    • Событий нет"]
    except Exception:
        return ["    • Не удалось загрузить события"]


def build_report():
    lines = []
    lines.append("📊 *Еженедельный отчёт по портфелю*")
    lines.append(f"📅 {datetime.now().strftime('%d.%m.%Y, %H:%M')}\n")

    total_value = 0.0
    total_change = 0.0

    for item in PORTFOLIO:
        secid = item["secid"]
        name = item["name"]
        qty = item["qty"]
        market = item["market"]

        lines.append(f"*{name}* ({qty} шт.)")

        try:
            current = get_current_price(secid, market)
            price_start, price_end = get_week_prices(secid, market)

            # Для облигаций цена приходит в % от номинала 1000 ₽
            multiplier = (1000 / 100) if (market == "bonds" and current and current < 200) else 1

            if current:
                real_price = current * multiplier
                position_value = real_price * qty
                total_value += position_value
                lines.append(f"  💰 Цена: *{real_price:.2f} ₽*  |  Позиция: *{position_value:,.2f} ₽*")
            else:
                lines.append("  💰 Цена: нет данных")

            if price_start and price_end:
                ps = price_start * multiplier
                pe = price_end * multiplier
                change = pe - ps
                pct = (change / ps * 100) if ps else 0
                position_change = change * qty
                total_change += position_change
                arrow = "🟢" if change >= 0 else "🔴"
                lines.append(
                    f"  {arrow} За неделю: {change:+.2f} ₽ ({pct:+.2f}%)  |  по позиции: {position_change:+.2f} ₽"
                )
            else:
                lines.append("  📉 Изменение за неделю: нет данных")

            if market == "bonds":
                events = get_bond_events(secid)
                lines.append("  📰 События:")
                lines.extend(events)

        except Exception as e:
            lines.append(f"  ⚠️ Ошибка: {e}")

        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Стоимость портфеля: {total_value:,.2f} ₽*")
    arrow_total = "🟢" if total_change >= 0 else "🔴"
    lines.append(f"{arrow_total} *Изменение за неделю: {total_change:+,.2f} ₽*")
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
