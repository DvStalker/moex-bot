import os
import requests
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

PURCHASE_DATE = "2025-12-08"
MOEX = "https://iss.moex.com/iss"

# Параметры проверены через debug.py — board/engine/market точные
PORTFOLIO = [
    {"secid": "SU26207RMFS9", "name": "ОФЗ 26207",              "qty": 3,   "board": "TQOB", "engine": "stock", "market": "bonds",  "face": 1000},
    {"secid": "SU26218RMFS6", "name": "ОФЗ 26218",              "qty": 5,   "board": "TQOB", "engine": "stock", "market": "bonds",  "face": 1000},
    {"secid": "SU26226RMFS9", "name": "ОФЗ 26226",              "qty": 1,   "board": "TQOB", "engine": "stock", "market": "bonds",  "face": 1000},
    {"secid": "RU000A0ZYVU5", "name": "Роснефть 002Р-05",       "qty": 3,   "board": "TQCB", "engine": "stock", "market": "bonds",  "face": 1000},
    {"secid": "RU000A106375", "name": "РЖД БО 001Р-44R",        "qty": 9,   "board": "TQCB", "engine": "stock", "market": "bonds",  "face": 1000},
    {"secid": "RU000A1069P3", "name": "Сбербанк 2P-SBER44",     "qty": 4,   "board": "TQCB", "engine": "stock", "market": "bonds",  "face": 1000},
    {"secid": "TPAY",         "name": "Пассивный доход (TPAY)", "qty": 380, "board": "TQTF", "engine": "stock", "market": "shares", "face": 1},
]


def get(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def get_price(secid, engine, market, board):
    """Текущая цена в % от номинала (для облигаций) или в рублях (для фондов)."""
    url = (f"{MOEX}/engines/{engine}/markets/{market}/boards/{board}"
           f"/securities/{secid}.json?iss.meta=off&iss.only=securities,marketdata")
    data = get(url)

    # Сначала пробуем marketdata — там есть LAST (цена последней сделки)
    md = data.get("marketdata", {})
    if md.get("data"):
        d = dict(zip(md["columns"], md["data"][0]))
        p = d.get("LAST") or d.get("LCURRENTPRICE")
        if p:
            return float(p)

    # Fallback: securities — PREVPRICE (цена предыдущего дня)
    sec = data.get("securities", {})
    if sec.get("data"):
        d = dict(zip(sec["columns"], sec["data"][0]))
        p = d.get("PREVPRICE") or d.get("PREVLEGALCLOSEPRICE")
        if p:
            return float(p)

    return None


def get_history(secid, engine, market, board, date_from, date_to):
    """История торгов за период."""
    url = (f"{MOEX}/history/engines/{engine}/markets/{market}/boards/{board}"
           f"/securities/{secid}.json"
           f"?from={date_from}&till={date_to}&iss.meta=off&iss.only=history")
    data = get(url)
    history = data.get("history", {})
    cols = history.get("columns", [])
    rows = history.get("data", [])
    return cols, rows


def close_price(d):
    return d.get("LEGALCLOSEPRICE") or d.get("CLOSE") or d.get("WAPRICE") or d.get("OPEN")


def get_week_prices(secid, engine, market, board):
    today = datetime.now()
    week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    cols, rows = get_history(secid, engine, market, board, week_ago, today.strftime("%Y-%m-%d"))
    if len(rows) < 2:
        return None, None
    p1 = close_price(dict(zip(cols, rows[0])))
    p2 = close_price(dict(zip(cols, rows[-1])))
    return (float(p1) if p1 else None), (float(p2) if p2 else None)


def get_purchase_price(secid, engine, market, board):
    dt = datetime.strptime(PURCHASE_DATE, "%Y-%m-%d")
    date_end = (dt + timedelta(days=5)).strftime("%Y-%m-%d")
    cols, rows = get_history(secid, engine, market, board, PURCHASE_DATE, date_end)
    if not rows:
        return None
    p = close_price(dict(zip(cols, rows[0])))
    return float(p) if p else None


def to_rub(price, face):
    """Конвертировать цену в рубли. Облигации торгуются в % от номинала."""
    if face > 1:
        return price * face / 100
    return price


def ai_analysis(name, price_rub, purchase_rub, week_pct, since_pct):
    if not ANTHROPIC_API_KEY:
        return None
    since_txt = f"{since_pct:+.2f}% с момента покупки 08.12.2025" if purchase_rub else "данных о цене покупки нет"
    prompt = (
        f'Напиши анализ для домохозяйки (3 простых предложения) про "{name}".\n'
        f"Текущая цена: {price_rub:.2f}₽. За неделю: {week_pct:+.2f}%. {since_txt}.\n"
        f"Объясни: 1) что происходит с ценой, 2) хорошо это или плохо, 3) что делать — держать, докупить или просто следить.\n"
        f"Пиши очень просто, без терминов, как подруге. Только текст."
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()
    except Exception as e:
        return f"Анализ недоступен: {e}"


def build_report():
    lines = [
        "📊 *Еженедельный отчёт по портфелю*",
        f"📅 {datetime.now().strftime('%d.%m.%Y')}  |  Куплено: 08.12.2025\n"
    ]

    total_now = 0.0
    total_buy = 0.0
    total_week = 0.0

    for item in PORTFOLIO:
        secid  = item["secid"]
        name   = item["name"]
        qty    = item["qty"]
        board  = item["board"]
        engine = item["engine"]
        market = item["market"]
        face   = item["face"]

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"*{name}* ({qty} шт.)")

        week_pct     = 0.0
        since_pct    = 0.0
        price_rub    = 0.0
        purchase_rub = None

        try:
            raw = get_price(secid, engine, market, board)
            if raw:
                price_rub = to_rub(raw, face)
                pos_val = price_rub * qty
                total_now += pos_val
                lines.append(f"  💰 Цена: *{price_rub:.2f} ₽*  |  Позиция: *{pos_val:,.2f} ₽*")
            else:
                lines.append("  💰 Цена: нет данных")

            # За неделю
            p1_raw, p2_raw = get_week_prices(secid, engine, market, board)
            if p1_raw and p2_raw:
                p1 = to_rub(p1_raw, face)
                p2 = to_rub(p2_raw, face)
                diff = p2 - p1
                week_pct = diff / p1 * 100
                pos_diff = diff * qty
                total_week += pos_diff
                arr = "🟢" if diff >= 0 else "🔴"
                lines.append(f"  {arr} За неделю: {diff:+.2f} ₽ ({week_pct:+.2f}%)  |  по позиции: {pos_diff:+.2f} ₽")
            else:
                lines.append("  📉 За неделю: нет данных")

            # С даты покупки
            buy_raw = get_purchase_price(secid, engine, market, board)
            if buy_raw:
                purchase_rub = to_rub(buy_raw, face)
                buy_pos = purchase_rub * qty
                total_buy += buy_pos
                since_diff = price_rub - purchase_rub
                since_pct = since_diff / purchase_rub * 100
                since_pos = since_diff * qty
                arr2 = "🟢" if since_diff >= 0 else "🔴"
                lines.append(f"  {arr2} С 08.12.25: {since_diff:+.2f} ₽ ({since_pct:+.2f}%)  |  по позиции: {since_pos:+.2f} ₽")
            else:
                lines.append("  📊 С покупки: нет данных")

            # AI аналитика
            if price_rub:
                analysis = ai_analysis(name, price_rub, purchase_rub, week_pct, since_pct)
                if analysis:
                    lines.append(f"\n  🤖 _{analysis}_")

        except Exception as e:
            lines.append(f"  ⚠️ Ошибка: {e}")

        lines.append("")

    # Итоги
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Итого сейчас: {total_now:,.2f} ₽*")
    arr_w = "🟢" if total_week >= 0 else "🔴"
    lines.append(f"{arr_w} *За неделю: {total_week:+,.2f} ₽*")
    if total_buy > 0:
        since_total = total_now - total_buy
        since_pct_total = since_total / total_buy * 100
        arr_s = "🟢" if since_total >= 0 else "🔴"
        lines.append(f"{arr_s} *С 08.12.2025: {since_total:+,.2f} ₽ ({since_pct_total:+.2f}%)*")

    lines.append("\n_Данные: Московская Биржа (ISS MOEX API)_")
    return "\n".join(lines)


def send(text):
    """Отправляет сообщение, разбивая на части если длиннее 4000 символов."""
    max_len = 4000
    parts = []
    while len(text) > max_len:
        # Разбиваем по разделителю ━━━ чтобы не резать посередине бумаги
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
