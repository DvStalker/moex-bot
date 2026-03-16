import os
import requests
from datetime import datetime, timedelta

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Дата покупки всего портфеля
PURCHASE_DATE = "2025-12-08"

# Портфель: secid = ISIN для облигаций, тикер для акций/фондов
# board = торговая площадка на MOEX
PORTFOLIO = [
    {"secid": "SU26207RMFS9", "name": "ОФЗ 26207",              "qty": 3,   "market": "bonds", "board": "TQOB"},
    {"secid": "SU26218RMFS6", "name": "ОФЗ 26218",              "qty": 5,   "market": "bonds", "board": "TQOB"},
    {"secid": "SU26226RMFS9", "name": "ОФЗ 26226",              "qty": 1,   "market": "bonds", "board": "TQOB"},
    {"secid": "RU000A0ZYVU5", "name": "Роснефть 002Р-05",       "qty": 3,   "market": "bonds", "board": "TQCB"},
    {"secid": "RU000A106375", "name": "РЖД БО 001Р-44R",        "qty": 9,   "market": "bonds", "board": "TQCB"},
    {"secid": "RU000A1069P3", "name": "Сбербанк 2P-SBER44",     "qty": 4,   "market": "bonds", "board": "TQCB"},
    {"secid": "TPAY",         "name": "Пассивный доход (TPAY)", "qty": 380, "market": "shares", "board": "TQTF"},
]

MOEX_BASE = "https://iss.moex.com/iss"


def get_current_price(secid, market, board):
    """Получить текущую цену через конкретную площадку."""
    if market == "bonds":
        url = (f"{MOEX_BASE}/engines/stock/markets/bonds/boards/{board}"
               f"/securities/{secid}.json?iss.meta=off&iss.only=marketdata,securities")
    else:
        url = (f"{MOEX_BASE}/engines/stock/markets/shares/boards/{board}"
               f"/securities/{secid}.json?iss.meta=off&iss.only=marketdata,securities")

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


def get_history_prices(secid, market, board, date_from, date_to):
    """Получить историю цен за период."""
    if market == "bonds":
        url = (f"{MOEX_BASE}/history/engines/stock/markets/bonds/boards/{board}"
               f"/securities/{secid}.json?from={date_from}&till={date_to}&iss.meta=off&iss.only=history")
    else:
        url = (f"{MOEX_BASE}/history/engines/stock/markets/shares/boards/{board}"
               f"/securities/{secid}.json?from={date_from}&till={date_to}&iss.meta=off&iss.only=history")

    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    history = data.get("history", {})
    cols = history.get("columns", [])
    rows = history.get("data", [])
    return cols, rows


def get_week_prices(secid, market, board):
    today = datetime.now()
    week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")

    cols, rows = get_history_prices(secid, market, board, week_ago, today_str)
    if not rows:
        return None, None

    d_first = dict(zip(cols, rows[0]))
    d_last = dict(zip(cols, rows[-1]))

    def extract_price(d):
        return d.get("CLOSE") or d.get("LEGALCLOSEPRICE") or d.get("WAPRICE") or d.get("OPEN")

    p1 = extract_price(d_first)
    p2 = extract_price(d_last)
    return (float(p1) if p1 else None, float(p2) if p2 else None)


def get_purchase_price(secid, market, board):
    """Цена на дату покупки 08.12.2025."""
    dt = datetime.strptime(PURCHASE_DATE, "%Y-%m-%d")
    date_end = (dt + timedelta(days=5)).strftime("%Y-%m-%d")

    cols, rows = get_history_prices(secid, market, board, PURCHASE_DATE, date_end)
    if not rows:
        return None

    d = dict(zip(cols, rows[0]))
    price = d.get("CLOSE") or d.get("LEGALCLOSEPRICE") or d.get("OPEN") or d.get("WAPRICE")
    return float(price) if price else None


def get_ai_analysis(name, current_price, purchase_price, week_pct, since_pct):
    """Мини-анализ от Claude простым языком."""
    if not ANTHROPIC_API_KEY:
        return None

    since_info = f"{since_pct:+.2f}% с момента покупки (08.12.2025)" if purchase_price else "данных о покупке нет"

    prompt = f"""Ты дружелюбный финансовый помощник. Напиши короткий понятный комментарий по облигации/фонду "{name}" для обычного человека без финансового образования.

Данные:
- Текущая цена: {current_price:.2f} ₽
- За неделю: {week_pct:+.2f}%
- {since_info}

Напиши 3 коротких предложения:
1. Как ведёт себя инструмент сейчас
2. Хорошо это или нет для владельца  
3. Что делать: держать, докупить или присмотреться

Пиши просто, без терминов, как другу. Только текст, без списков и заголовков."""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01"
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 250,
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=30
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"].strip()


def build_report():
    lines = []
    lines.append("📊 *Еженедельный отчёт по портфелю*")
    lines.append(f"📅 {datetime.now().strftime('%d.%m.%Y')}  |  Куплено: 08.12.2025\n")

    total_value = 0.0
    total_purchase_value = 0.0
    total_week_change = 0.0

    for item in PORTFOLIO:
        secid = item["secid"]
        name = item["name"]
        qty = item["qty"]
        market = item["market"]
        board = item["board"]

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"*{name}* ({qty} шт.)")

        week_pct = 0.0
        since_pct = 0.0
        real_price = 0.0
        purchase_price = None

        try:
            current = get_current_price(secid, market, board)

            # Облигации: цена в % от номинала 1000 ₽
            multiplier = 10.0 if (market == "bonds" and current and current < 200) else 1.0

            if current:
                real_price = current * multiplier
                position_value = real_price * qty
                total_value += position_value
                lines.append(f"  💰 Цена: *{real_price:.2f} ₽*  |  Позиция: *{position_value:,.2f} ₽*")
            else:
                lines.append("  💰 Цена: нет данных")

            # За неделю
            p_week_start, p_week_end = get_week_prices(secid, market, board)
            if p_week_start and p_week_end:
                ps = p_week_start * multiplier
                pe = p_week_end * multiplier
                change = pe - ps
                week_pct = (change / ps * 100) if ps else 0
                pos_change = change * qty
                total_week_change += pos_change
                arrow = "🟢" if change >= 0 else "🔴"
                lines.append(f"  {arrow} За неделю: {change:+.2f} ₽ ({week_pct:+.2f}%)  |  по позиции: {pos_change:+.2f} ₽")
            else:
                lines.append("  📉 За неделю: нет данных")

            # С даты покупки
            purchase_raw = get_purchase_price(secid, market, board)
            if purchase_raw and current:
                purchase_price = purchase_raw * multiplier
                total_purchase_value += purchase_price * qty
                since_change = real_price - purchase_price
                since_pct = (since_change / purchase_price * 100) if purchase_price else 0
                since_pos = since_change * qty
                arrow2 = "🟢" if since_change >= 0 else "🔴"
                lines.append(f"  {arrow2} С 08.12.25: {since_change:+.2f} ₽ ({since_pct:+.2f}%)  |  по позиции: {since_pos:+.2f} ₽")
            else:
                lines.append("  📊 С покупки: нет данных")

            # AI анализ
            try:
                analysis = get_ai_analysis(name, real_price, purchase_price, week_pct, since_pct)
                if analysis:
                    lines.append(f"\n  🤖 _{analysis}_")
            except Exception:
                pass

        except Exception as e:
            lines.append(f"  ⚠️ Ошибка: {e}")

        lines.append("")

    # Итог
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Итого: {total_value:,.2f} ₽*")
    arrow_w = "🟢" if total_week_change >= 0 else "🔴"
    lines.append(f"{arrow_w} *За неделю: {total_week_change:+,.2f} ₽*")

    if total_purchase_value > 0:
        total_since = total_value - total_purchase_value
        total_since_pct = (total_since / total_purchase_value * 100)
        arrow_s = "🟢" if total_since >= 0 else "🔴"
        lines.append(f"{arrow_s} *С 08.12.2025: {total_since:+,.2f} ₽ ({total_since_pct:+.2f}%)*")

    lines.append("")
    lines.append("_Данные: Московская Биржа (ISS MOEX API)_")
    return "\n".join(lines)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    print("✅ Отчёт отправлен!")


if __name__ == "__main__":
    report = build_report()
    print(report)
    send_telegram(report)
