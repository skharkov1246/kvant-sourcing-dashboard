"""Зонд v38: базовая валюта портала и порядок сумм.

Прогон v37 дал по роли КАМ пайплайн €1 571,9 млн. Либо в карточках такие суммы,
либо базовая валюта портала — не евро, и весь дашборд подписывает рубли значком €.
Вопрос стоит дороже вкладки: под этим значком считаются все деньги во всех вкладках.

Печатаются курсы, признак базовой валюты, раскладка открытых сделок по валютам и
порядок сумм (перцентили, крупнейшие — числом, без названий сделок и клиентов).
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitrix_client import BitrixClient  # noqa: E402


def head(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main() -> int:
    c = BitrixClient(os.environ["BITRIX_WEBHOOK_URL"])

    head("1. СПРАВОЧНИК ВАЛЮТ (crm.currency.list)")
    cur = c.call("crm.currency.list", {}) or []
    base = None
    for x in cur:
        is_base = str(x.get("BASE")).upper() in ("Y", "1", "TRUE")
        if is_base:
            base = x.get("CURRENCY")
        print(f"{str(x.get('CURRENCY')):<5} AMOUNT={x.get('AMOUNT')} AMOUNT_CNT={x.get('AMOUNT_CNT')} "
              f"BASE={x.get('BASE')} {'← базовая' if is_base else ''}")
    print(f"\nБАЗОВАЯ ВАЛЮТА ПОРТАЛА: {base or 'не определена'}")

    head("2. ОТКРЫТЫЕ СДЕЛКИ ПО ВАЛЮТАМ КАРТОЧКИ")
    op = c.list_deals_fast(filter={"STAGE_SEMANTIC_ID": "P"},
                           select=["ID", "CATEGORY_ID", "OPPORTUNITY", "CURRENCY_ID", "STAGE_ID"])
    by = Counter(str(d.get("CURRENCY_ID")) for d in op)
    sums = defaultdict(float)
    for d in op:
        sums[str(d.get("CURRENCY_ID"))] += float(d.get("OPPORTUNITY") or 0)
    for k, n in by.most_common():
        print(f"{k:<6} сделок {n:>5}  Σ в валюте карточки {sums[k]:,.0f}".replace(",", " "))

    head("3. ПОРЯДОК СУММ (в валюте карточки, без пересчёта)")
    vals = sorted(float(d.get("OPPORTUNITY") or 0) for d in op if float(d.get("OPPORTUNITY") or 0) > 0)
    if vals:
        q = lambda p: vals[min(len(vals) - 1, int(len(vals) * p / 100))]
        print(f"сделок с суммой: {len(vals)} из {len(op)}")
        print(f"P50 {q(50):,.0f} · P75 {q(75):,.0f} · P90 {q(90):,.0f} · P99 {q(99):,.0f} · max {vals[-1]:,.0f}"
              .replace(",", " "))
        print("десять крупнейших сумм: " + " · ".join(f"{v:,.0f}".replace(",", " ") for v in vals[-10:]))
        big = [d for d in op if float(d.get("OPPORTUNITY") or 0) >= 1e9]
        print(f"карточек с суммой ≥ 1 млрд в валюте карточки: {len(big)}")
        cb = Counter(str(d.get("CURRENCY_ID")) for d in big)
        print("их валюты: " + (" · ".join(f"{k}×{v}" for k, v in cb.most_common()) or "—"))
        cc = Counter(str(d.get("CATEGORY_ID")) for d in big)
        print("их воронки: " + (" · ".join(f"cat{k}×{v}" for k, v in cc.most_common()) or "—"))

    head("4. ИТОГ")
    print(f"Базовая валюта: {base}. Если это не EUR, подпись «€» во всех вкладках неверна: "
          f"суммы приводятся к базовой валюте портала, а называются евро.")
    print("\nГОТОВО")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
