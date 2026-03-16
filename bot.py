import os
import requests
from datetime import datetime, timedelta, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TINVEST_TOKEN = os.environ["TINVEST_TOKEN"]

ACCOUNT_NAME = "Очень долгий срок"
PURCHASE_DATE = datetime(2025, 12, 8, tzinfo=timezone.utc)

TINVEST = "https://invest-public-api.tinkoff.ru/rest"
HEADERS = {
    "Authorization": f"Bearer {TINVEST_TOKEN}",
    "Content-Type": "application/json",
}


def ti_post(path, body=None):
    r = requests.post(f"{TINVEST}{path}", headers=HEADERS, json=body or {}, timeout=15)
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
    accounts = data.get("accounts", [])
    names = [a.get("name") for a in accounts]
    raise Exception(f"Счёт '{ACCOUNT_NAME}' не найден. Доступные: {names}")


def get_portfolio(account_id):
    return ti_post(
        "/tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
        {"accountId": account_id}
    )


def get_instrument_info(figi):
    """Получить название и тип инструмента."""
    try:
        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.InstrumentsService/GetInstrumentBy",
            {"idType": "ID_TYPE_FIGI", "id": figi}
        )
        inst = data.get("instrument", {})
        name = inst.get("name") or inst.get("ticker") or figi
        kind = inst.get("instrumentKind", "")
        return name, kind
    except Exception:
        return figi, ""


def get_candles_week(figi):
    """Цена неделю назад и сейчас."""
    try:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.MarketDataService/GetCandles",
            {
                "figi": figi,
                "from": week_ago.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "interval": "CANDLE_INTERVAL_DAY"
            }
        )
        candles = data.get("candles", [])
        if len(candles) >= 2:
            return quotation(candles[0].get("close")), quotation(candles[-1].get("close"))
    except Exception:
        pass
    return None, None


def get_dividends_and_coupons(account_id):
    """Получить все выплаты (дивиденды + купоны) с даты покупки."""
    total = 0.0
    try:
        now = datetime.now(timezone.utc)
        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.OperationsService/GetOperations",
            {
                "accountId": account_id,
                "from": PURCHASE_DATE.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "state": "OPERATION_STATE_EXECUTED"
            }
        )
        # Типы операций: DIVIDEND, COUPON, DIV_TAX (налог на дивиденды)
        INCOME_TYPES = {
            "OPERATION_TYPE_DIVIDEND",
            "OPERATION_TYPE_COUPON",
            "OPERATION_TYPE_BOND_REPAYMENT_FULL",
        }
        for op in data.get("operations", []):
            op_type = op.get("operationType", "")
            if op_type in INCOME_TYPES:
                total += moneyval(op.get("payment"))
    except Exception as e:
        print(f"Ошибка получения выплат: {e}")
    return total


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
        f"📅 {datetime.now().strftime('%d.%m.%Y')}  |  Счёт: {ACCOUNT_NAME}\n"
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

    for pos in positions:
        figi = pos.get("figi", "")
        qty = quotation(pos.get("quantity"))
        if qty == 0:
            continue

        name, kind = get_instrument_info(figi)
        current_price = moneyval(pos.get("currentPrice"))
        avg_price = moneyval(pos.get("averagePositionPrice"))
        pnl = moneyval(pos.get("expectedYield"))
        since_pct = (pnl / (avg_price * qty) * 100) if avg_price and qty else 0.0
        pos_val = current_price * qty

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"*{name}* ({qty:.0f} шт.)")
        lines.append(f"  💰 Цена: *{current_price:.2f} ₽*  |  Позиция: *{pos_val:,.2f} ₽*")

        if avg_price:
            arr = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"  {arr} С покупки: {pnl:+.2f} ₽ ({since_pct:+.2f}%)")

        # За неделю
        p1, p2 = get_candles_week(figi)
        week_pct = 0.0
        if p1 and p2 and p1 > 0:
            diff = p2 - p1
            week_pct = diff / p1 * 100
            pos_diff = diff * qty
            total_week += pos_diff
            arr_w = "🟢" if diff >= 0 else "🔴"
            lines.append(f"  {arr_w} За неделю: {diff:+.2f} ₽ ({week_pct:+.2f}%)  |  по позиции: {pos_diff:+.2f} ₽")
        else:
            lines.append("  📉 За неделю: нет данных")

        # AI анализ
        analysis = ai_analysis(name, current_price, avg_price, week_pct, since_pct)
        if analysis:
            lines.append(f"\n  🤖 _{analysis}_")

        lines.append("")

    # Итоги портфеля
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Итого: {total_now:,.2f} ₽*")

    arr_pnl = "🟢" if total_pnl >= 0 else "🔴"
    lines.append(f"{arr_pnl} *Доход с 08.12.2025 (рост цен): {total_pnl:+,.2f} ₽*")

    arr_div = "💰" if dividends_total > 0 else "📭"
    lines.append(f"{arr_div} *Купоны и дивиденды с 08.12.2025: {dividends_total:+,.2f} ₽*")

    total_income = total_pnl + dividends_total
    arr_total = "🟢" if total_income >= 0 else "🔴"
    lines.append(f"{arr_total} *Общий доход: {total_income:+,.2f} ₽*")

    if total_week != 0:
        arr_w = "🟢" if total_week >= 0 else "🔴"
        lines.append(f"{arr_w} *За неделю: {total_week:+,.2f} ₽*")

    lines.append("\n_Данные: Т-Инвестиции API_")
    return "\n".join(lines)


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
            json={"chat_id": CHAT_ID, "text": part, "parse_mode": "Markdown"},
            timeout=15
        )
        r.raise_for_status()
    print(f"✅ Отправлено ({len(parts)} сообщений)!")


if __name__ == "__main__":
    report = build_report()
    print(report)
    send(report)
