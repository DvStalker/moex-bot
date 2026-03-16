import os
import requests
from datetime import datetime, timedelta

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

PURCHASE_DATE = "2025-12-08"

PORTFOLIO = [
    {"secid": "SU26207RMFS9", "name": "ОФЗ 26207",              "qty": 3,   "engine": "stock", "market": "bonds"},
    {"secid": "SU26218RMFS6", "name": "ОФЗ 26218",              "qty": 5,   "engine": "stock", "market": "bonds"},
    {"secid": "SU26226RMFS9", "name": "ОФЗ 26226",              "qty": 1,   "engine": "stock", "market": "bonds"},
    {"secid": "RU000A0ZYVU5", "name": "Роснефть 002Р-05",       "qty": 3,   "engine": "stock", "market": "bonds"},
    {"secid": "RU000A106375", "name": "РЖД БО 001Р-44R",        "qty": 9,   "engine": "stock", "market": "bonds"},
    {"secid": "RU000A1069P3", "name": "Сбербанк 2P-SBER44",     "qty": 4,   "engine": "stock", "market": "bonds"},
    {"secid": "TPAY",         "name": "Пассивный доход (TPAY)", "qty": 380, "engine": "stock", "market": "shares"},
]

MOEX_BASE = "https://iss.moex.com/iss"


def find_primary_board(secid):
    """Найти основную торгуемую площадку для бумаги."""
    url = f"{MOEX_BASE}/securities/{secid}.json?iss.meta=off&iss.only=boards"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    boards = data.get("boards", {})
    cols = boards.get("columns", [])
    rows = boards.get("data", [])
    
    # Ищем основную торгуемую площадку
    primary = None
    for row in rows:
        d = dict(zip(cols, row))
        if d.get("is_primary") == 1 and d.get("is_traded") == 1:
            return d["boardid"], d.get("engine", "stock"), d.get("market", "bonds")
        if d.get("is_traded") == 1 and primary is None:
            primary = (d["boardid"], d.get("engine", "stock"), d.get("market", "bonds"))
    
    return primary if primary else (None, None, None)


def get_current_price(secid, engine, market, board):
    """Получить текущую цену."""
    url = (f"{MOEX_BASE}/engines/{engine}/markets/{market}/boards/{board}"
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


def get_history(secid, engine, market, board, date_from, date_to):
    """Получить историю цен."""
    url = (f"{MOEX_BASE}/history/engines/{engine}/markets/{market}/boards/{board}"
           f"/securities/{secid}.json?from={date_from}&till={date_to}&iss.meta=off&iss.only=history")
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    history = data.get("history", {})
    cols = history.get("columns", [])
    rows = history.get("data", [])
    return cols, rows


def extract_close(d):
    return d.get("CLOSE") or d.get("LEGALCLOSEPRICE") or d.get("WAPRICE") or d.get("OPEN")


def get_week_prices(secid, engine, market, board):
    today = datetime.now()
    week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    today_str = today.strftime("%Y-%m-%d")
    cols, rows = get_history(secid, engine, market, board, week_ago, today_str)
    if not rows:
        return None, None
    p1 = extract_close(dict(zip(cols, rows[0])))
    p2 = extract_close(dict(zip(cols, rows[-1])))
    return (float(p1) if p1 else None, float(p2) if p2 else None)


def get_purchase_price(secid, engine, market, board):
    dt = datetime.strptime(PURCHASE_DATE, "%Y-%m-%d")
    date_end = (dt + timedelta(days=5)).strftime("%Y-%m-%d")
    cols, rows = get_history(secid, engine, market, board, PURCHASE_DATE, date_end)
    if not rows:
        return None
    p = extract_close(dict(zip(cols, rows[0])))
    return float(p) if p else None


def get_ai_analysis(name, current_price, purchase_price, week_pct, since_pct):
    if not ANTHROPIC_API_KEY:
        return None
    since_info = f"{since_pct:+.2f}% с момента покупки (08.12.2025)" if purchase_price else "данных о покупке нет"
    prompt = f"""Ты дружелюбный финансовый помощник. Напиши короткий понятный комментарий по облигации/фонду "{name}" для обычного человека без финансового образования.

Данные:
- Текущая цена: {current_price:.2f} ₽
- За неделю: {week_pct:+.2f}%
- {since_info}

Напиши 3 коротких предложения: как ведёт себя инструмент, хорошо это или нет, что делать (держать/докупить/присмотреться). Пиши просто, без терминов. Только текст."""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
        json={"model": "claude-sonnet-4-20250514", "max_tokens": 250, "messages": [{"role": "user", "content": prompt}]},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()


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
        engine = item["engine"]
        market = item["market"]

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"*{name}* ({qty} шт.)")

        week_pct = 0.0
        since_pct = 0.0
        real_price = 0.0
        purchase_price = None

        try:
            # Автоматически найти правильную площадку
            board, eng, mkt = find_primary_board(secid)
            if not board:
                lines.append("  ⚠️ Площадка не найдена")
                lines.append("")
                continue

            # Используем найденные параметры
            engine = eng
            market = mkt

            current = get_current_price(secid, engine, market, board)

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
            p_start, p_end = get_week_prices(secid, engine, market, board)
            if p_start and p_end:
                ps = p_start * multiplier
                pe = p_end * multiplier
                change = pe - ps
                week_pct = (change / ps * 100) if ps else 0
                pos_change = change * qty
                total_week_change += pos_change
                arrow = "🟢" if change >= 0 else "🔴"
                lines.append(f"  {arrow} За неделю: {change:+.2f} ₽ ({week_pct:+.2f}%)  |  по позиции: {pos_change:+.2f} ₽")
            else:
                lines.append("  📉 За неделю: нет данных")

            # С даты покупки
            p_buy_raw = get_purchase_price(secid, engine, market, board)
            if p_buy_raw and current:
                purchase_price = p_buy_raw * multiplier
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
