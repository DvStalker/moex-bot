"""
Запусти через GitHub Actions: Actions -> Run workflow
Результат покажет точные board/engine/market для каждой бумаги
"""
import requests

MOEX = "https://iss.moex.com/iss"

SECURITIES = [
    ("SU26207RMFS9", "ОФЗ 26207"),
    ("SU26218RMFS6", "ОФЗ 26218"),
    ("SU26226RMFS9", "ОФЗ 26226"),
    ("RU000A0ZYVU5", "Роснефть 002Р-05"),
    ("RU000A106375", "РЖД БО 001Р-44R"),
    ("RU000A1069P3", "Сбербанк 2P-SBER44"),
    ("TPAY",         "Пассивный доход"),
]

for secid, name in SECURITIES:
    print(f"\n{'='*50}")
    print(f"{name} | {secid}")
    try:
        r = requests.get(
            f"{MOEX}/securities/{secid}.json?iss.meta=off&iss.only=boards",
            timeout=10
        )
        data = r.json()
        boards = data.get("boards", {})
        cols = boards.get("columns", [])
        rows = boards.get("data", [])

        traded = []
        for row in rows:
            d = dict(zip(cols, row))
            if d.get("is_traded") == 1:
                traded.append(d)

        print(f"Торгуемых площадок: {len(traded)}")
        for b in traded:
            print(f"  boardid={b['boardid']} engine={b['engine']} market={b['market']} is_primary={b.get('is_primary')}")

        # Берём primary или первую торгуемую
        primary = next((b for b in traded if b.get("is_primary") == 1), traded[0] if traded else None)
        if primary:
            board = primary["boardid"]
            engine = primary["engine"]
            market = primary["market"]
            print(f">> Используем: board={board} engine={engine} market={market}")

            # Запрашиваем цену
            r2 = requests.get(
                f"{MOEX}/engines/{engine}/markets/{market}/boards/{board}/securities/{secid}.json"
                f"?iss.meta=off&iss.only=securities,marketdata",
                timeout=10
            )
            d2 = r2.json()

            md = d2.get("marketdata", {})
            if md.get("data"):
                row_d = dict(zip(md["columns"], md["data"][0]))
                print(f"  LAST={row_d.get('LAST')} MARKETPRICE={row_d.get('MARKETPRICE')}")

            sec = d2.get("securities", {})
            if sec.get("data"):
                row_d = dict(zip(sec["columns"], sec["data"][0]))
                print(f"  PREVPRICE={row_d.get('PREVPRICE')} PREVLEGALCLOSEPRICE={row_d.get('PREVLEGALCLOSEPRICE')}")

    except Exception as e:
        print(f"  ОШИБКА: {e}")

print("\n\nДиагностика завершена!")
