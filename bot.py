import os
import requests
from datetime import datetime, timedelta, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TINVEST_TOKEN = os.environ["TINVEST_TOKEN"]

ACCOUNT_NAME = "Очень долгий срок"
PURCHASE_DATE = "2025-12-08"

TINVEST = "https://invest-public-api.tinkoff.ru/rest"
HEADERS = {
    "Authorization": f"Bearer {TINVEST_TOKEN}",
    "Content-Type": "application/json",
}


def ti_post(path, body=None):
    r = requests.post(f"{TINVEST}{path}", headers=HEADERS, json=body or {}, timeout=15)
    r.raise_for_status()
    return r.json()


def get_account_id():
    """Найти счёт по имени."""
    data = ti_post("/tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts")
    for acc in data.get("accounts", []):
        if acc.get("name") == ACCOUNT_NAME:
            return acc["id"]
    # Если не нашли по имени — берём первый счёт и выводим список
    accounts = data.get("accounts", [])
    names = [a.get("name") for a in accounts]
    raise Exception(f"Счёт '{ACCOUNT_NAME}' не найден. Доступные счета: {names}")


def get_portfolio(account_id):
    """Получить портфель."""
    data = ti_post(
        "/tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
        {"accountId": account_id}
    )
    return data


def moneyval(mv):
    """Конвертировать MoneyValue в float."""
    if not mv:
        return 0.0
    units = int(mv.get("units", 0))
    nano = int(mv.get("nano", 0))
    return units + nano / 1e9


def quotation(q):
    """Конвертировать Quotation в float."""
    if not q:
        return 0.0
    units = int(q.get("units", 0))
    nano = int(q.get("nano", 0))
    return units + nano / 1e9


def get_instrument_name(figi):
    """Получить название инструмента по FIGI."""
    try:
        data = ti_post(
            "/tinkoff.public.invest.api.contract.v1.InstrumentsService/GetInstrumentBy",
            {"idType": "ID_TYPE_FIGI", "id": figi}
        )
        inst = data.get("instrument", {})
        return inst.get("name") or inst.get("ticker") or figi
    except Exception:
        return figi


def ai_analysis(name, current_rub, avg_rub, week_pct, since_pct):
    """AI анализ для домохозяйки."""
    if not ANTHROPIC_API_KEY:
        return None
    since_txt = f"{since_pct:+.2f}% с момента покупки" if avg_rub else "нет данных о покупке"
    prompt = (
        f'Напиши анализ для домохозяйки (3 простых предложения) про "{name}".\n'
        f"Текущая цена: {current_rub:.2f}₽. За неделю: {week_pct:+.2f}%. {since_txt}.\n"
        f"1) что происходит с ценой, 2) хорошо это или плохо, 3) что делать — держать, докупить или следить.\n"
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


def get_candles_week(figi):
    """Получить цену неделю назад через T-Invest API."""
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
            first = quotation(candles[0].get("close"))
            last = quotation(candles[-1].get("close"))
            return first, last
    except Exception:
        pass
    return None, None


def build_report():
    lines = [
        "📊 *Еженедельный отчёт по портфелю*",
        f"📅 {datetime.now().strftime('%d.%m.%Y')}  |  Счёт: {ACCOUNT_NAME}\n"
    ]

    try:
        account_id = get_account_id()
        portfolio = get_portfolio(account_id)
    except Exception as e:
        lines.append(f"⚠️ Ошибка получения портфеля: {e}")
        return "\n".join(lines)

    positions = portfolio.get("positions", [])
    if not positions:
        lines.append("Портфель пуст или нет позиций.")
        return "\n".join(lines)

    # Итоги из портфеля
    total_now = moneyval(portfolio.get("totalAmountPortfolio"))
    expected_yield = moneyval(portfolio.get("expectedYield"))  # общий P&L

    total_week_change = 0.0
    position_count = 0

    for pos in positions:
        figi = pos.get("figi", "")
        qty = quotation(pos.get("quantity"))
        if qty == 0:
            continue

        position_count += 1
        inst_type = pos.get("instrumentType", "")

        # Название
        name = get_instrument_name(figi)

        # Текущая цена за 1 штуку
        current_price = moneyval(pos.get("currentPrice"))
        current_rub = current_price * qty  # стоимость позиции

        # Средняя цена покупки
        avg_price = moneyval(pos.get("averagePositionPrice"))

        # P&L по позиции
        pnl = moneyval(pos.get("expectedYield"))
        since_pct = (pnl / (avg_price * qty) * 100) if avg_price and qty else 0

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"*{name}* ({qty:.0f} шт.)")
        lines.append(f"  💰 Цена: *{current_price:.2f} ₽*  |  Позиция: *{current_rub:,.2f} ₽*")

        if avg_price:
            arr = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"  {arr} С покупки: {pnl:+.2f} ₽ ({since_pct:+.2f}%)")

        # Изменение за неделю
        p1, p2 = get_candles_week(figi)
        week_pct = 0.0
        if p1 and p2 and p1 > 0:
            diff = p2 - p1
            week_pct = diff / p1 * 100
            pos_diff = diff * qty
            total_week_change += pos_diff
            arr_w = "🟢" if diff >= 0 else "🔴"
            lines.append(f"  {arr_w} За неделю: {diff:+.2f} ₽ ({week_pct:+.2f}%)  |  по позиции: {pos_diff:+.2f} ₽")
        else:
            lines.append("  📉 За неделю: нет данных")

        # AI анализ
        analysis = ai_analysis(name, current_price, avg_price, week_pct, since_pct)
        if analysis:
            lines.append(f"\n  🤖 _{analysis}_")

        lines.append("")

    # Итоги
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💼 *Итого: {total_now:,.2f} ₽*")
    arr_total = "🟢" if expected_yield >= 0 else "🔴"
    lines.append(f"{arr_total} *Общий P&L: {expected_yield:+,.2f} ₽*")
    if total_week_change != 0:
        arr_w = "🟢" if total_week_change >= 0 else "🔴"
        lines.append(f"{arr_w} *За неделю: {total_week_change:+,.2f} ₽*")

    lines.append("\n_Данные: Т-Инвестиции API_")
    return "\n".join(lines)


def send(text):
    """Отправляет сообщение, разбивая на части если длиннее 4000 символов."""
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
