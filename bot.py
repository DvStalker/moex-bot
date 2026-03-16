import os
import requests
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

PURCHASE_DATE = "2025-12-08"
MOEX = "https://iss.moex.com/iss"

PORTFOLIO = [
    {"secid": "SU26207RMFS9", "name": "ОФЗ 26207",              "qty": 3},
    {"secid": "SU26218RMFS6", "name": "ОФЗ 26218",              "qty": 5},
    {"secid": "SU26226RMFS9", "name": "ОФЗ 26226",              "qty": 1},
    {"secid": "RU000A0ZYVU5", "name": "Роснефть 002Р-05",       "qty": 3},
    {"secid": "RU000A106375", "name": "РЖД БО 001Р-44R",        "qty": 9},
    {"secid": "RU000A1069P3", "name": "Сбербанк 2P-SBER44",     "qty": 4},
    {"secid": "TPAY",         "name": "Пассивный доход (TPAY)", "qty": 380},
]


def moex_get(url):
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def get_price(secid):
    """Универсальный запрос цены — MOEX сам выбирает основную площадку."""
    data = moex_get(f"{MOEX}/securities/{secid}.json?iss.meta=off&iss.only=marketdata")
    md = data.get("marketdata", {})
    cols = md.get("columns", [])
    rows = md.get("data", [])
    for row in rows:
        d = dict(zip(cols, row))
        p = d.get("LAST") or d.get("MARKETPRICE") or d.get("LCURRENTPRICE") or d.get("PREVPRICE")
        if p:
            return float(p)
    return None


def get_history_price(secid, date_from, date_to):
    """История цен через универсальный endpoint без указания board."""
    # Пробуем bonds
    for market in ["bonds", "shares"]:
        url = (f"{MOEX}/history/engines/stock/markets/{market}/securities/{secid}.json"
               f"?from={date_from}&till={date_to}&iss.meta=off&iss.only=history"
               f"&history.columns=TRADEDATE,CLOSE,LEGALCLOSEPRICE,WAPRICE,OPEN")
        try:
            data = moex_get(url)
            history = data.get("history", {})
            cols = history.get("columns", [])
            rows = history.get("data", [])
            if rows:
                return cols, rows
        except Exception:
            continue
    return [], []


def extract_price(d):
    return d.get("CLOSE") or d.get("LEGALCLOSEPRICE") or d.get("WAPRICE") or d.get("OPEN")


def get_week_change(secid):
    today = datetime.now()
    week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    cols, rows = get_history_price(secid, week_ago, today.strftime("%Y-%m-%d"))
    if len(rows) < 2:
        return None, None
    p1 = extract_price(dict(zip(cols, rows[0])))
    p2 = extract_price(dict(zip(cols, rows[-1])))
    return (float(p1) if p1 else None), (float(p2) if p2 else None)


def get_purchase_price(secid):
    dt = datetime.strptime(PURCHASE_DATE, "%Y-%m-%d")
    date_end = (dt + timedelta(days=5)).strftime("%Y-%m-%d")
    cols, rows = get_history_price(secid, PURCHASE_DATE, date_end)
    if not rows:
        return None
    p = extract_price(dict(zip(cols, rows[0])))
    return float(p) if p else None


def normalize(price, secid):
    """Облигации отдают цену в % от номинала — конвертируем в рубли."""
    if price and price < 200:
        return price * 10.0  # 98.5% * 10 = 985 руб
    return price


def ai_analysis(name, price, purchase, week_pct, since_pct):
    if not ANTHROPIC_API_KEY:
        return None
    since_txt = f"{since_pct:+.2f}% с покупки" if purchase else "нет данных о покупке"
    prompt = (
        f'Напиши 3 предложения про облигацию/фонд "{name}" для домохозяйки без финансового образования.\n'
        f"Цена: {price:.2f}₽, за неделю: {week_pct:+.2f}%, {since_txt}.\n"
        f"1. Что происходит с ценой. 2. Хорошо это или плохо. 3. Что делать: держать, докупить или следить.\n"
        f"Только простой текст, без списков и заголовков."
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 200, "messages": [{"role": "user", "content": prompt}]},
            timeout=30
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()
    except Exception as e:
        return f"Анализ недоступен: {e}"


def build_report():
    lines = ["📊 *Еженедельный отчёт по портфелю*",
             f"📅 {datetime.now().strftime('%d.%m.%Y')}  |  Куплено: 08.12.2025\n"]

    total_now = 0.0
    total_buy = 0.0
    total_week = 0.0

    for item in PORTFOLIO:
        secid = item["secid"]
        name = item["name"]
        qty = item["qty"]

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"*{name}* ({qty} шт.)")

        week_pct = 0.0
        since_pct = 0.0
        real_price = 0.0
        purchase_price = None

        try:
            raw = get_price(secid)
            real_price = normalize(raw, secid) if raw else 0.0

            if real_price:
                pos = real_price * qty
                total_now += pos
                lines.append(f"  💰 Цена: *{real_price:.2f} ₽*  |  Позиция: *{pos:,.2f} ₽*")
            else:
                lines.append("  💰 Цена: нет данных")

            # За неделю
            p1, p2 = get_week_change(secid)
            p1 = normalize(p1, secid) if p1 else None
            p2 = normalize(p2, secid) if p2 else None
            if p1 and p2:
                diff = p2 - p1
                week_pct = diff / p1 * 100
                pos_diff = diff * qty
                total_week += pos_diff
                arr = "🟢" if diff >= 0 else "🔴"
                lines.append(f"  {arr} За неделю: {diff:+.2f} ₽ ({week_pct:+.2f}%)  |  по позиции: {pos_diff:+.2f} ₽")
            else:
                lines.append("  📉 За неделю: нет данных")

            # С покупки
            buy_raw = get_purchase_price(secid)
            purchase_price = normalize(buy_raw, secid) if buy_raw else None
            if purchase_price and real_price:
                total_buy += purchase_price * qty
                since_diff = real_price - purchase_price
                since_pct = since_diff / purchase_price * 100
                since_pos = since_diff * qty
                arr2 = "🟢" if since_diff >= 0 else "🔴"
                lines.append(f"  {arr2} С 08.12.25: {since_diff:+.2f} ₽ ({since_pct:+.2f}%)  |  по позиции: {since_pos:+.2f} ₽")
            else:
                lines.append("  📊 С покупки: нет данных")

            # AI аналитика
            if real_price:
                analysis = ai_analysis(name, real_price, purchase_price, week_pct, since_pct)
                if analysis:
                    lines.append(f"\n  🤖 _{analysis}_")

        except Exception as e:
            lines.append(f"  ⚠️ Ошибка: {e}")

        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Итого: {total_now:,.2f} ₽*")
    arr_w = "🟢" if total_week >= 0 else "🔴"
    lines.append(f"{arr_w} *За неделю: {total_week:+,.2f} ₽*")
    if total_buy > 0:
        since_total = total_now - total_buy
        since_total_pct = since_total / total_buy * 100
        arr_s = "🟢" if since_total >= 0 else "🔴"
        lines.append(f"{arr_s} *С 08.12.2025: {since_total:+,.2f} ₽ ({since_total_pct:+.2f}%)*")

    lines.append("\n_Данные: Московская Биржа (ISS MOEX API)_")
    return "\n".join(lines)


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15
    )
    r.raise_for_status()
    print("✅ Отправлено!")


if __name__ == "__main__":
    report = build_report()
    print(report)
    send(report)
