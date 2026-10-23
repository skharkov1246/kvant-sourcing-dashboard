#!/usr/bin/env python3
"""Зонд v55 «Деньги реализации»: можно ли верить суммам вкладки «Реализация».

ПОВОД (23.10.2026): в карточке сделки продажа €107K и закупка €645K при настоящих
«поставка ~30к, закуп ~15к». Две гипотезы из кода contracts.py:
  • валюта без курса в crm.currency.list считалась 1:1 к базовой (eur, _parse_econ_money);
  • закупка из money-полей экономики сделки («Cost (rub)», «Offer sum and currency of
    selected supplier») — поля ведут руками, они бывают от ранней итерации расчёта.

ЧТО МЕРЯЕМ (только счётчики и доли; ни сумм, ни номеров, ни названий):
  • базовая валюта портала, коды валют справочника и дата курса;
  • непроигранные сделки реализации и заказы СП-172 по валюте: сколько в валюте без курса;
  • money-поля экономики: заполненность, формат «сумма|ВАЛЮТА», валюта без курса, мусор;
  • отношение закупки к продаже по сделкам, где обе известны, — по корзинам; отдельно —
    когда закупка взята из заказов и когда из полей экономики;
  • сколько сделок вкладка показала бы с закупкой из полей экономики (у заказов сумм нет).

НАГРУЗКА: ≈ 30 запросов (справочник валют, сделки, заказы).
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import contracts as ct  # noqa: E402
from bitrix_client import BitrixClient  # noqa: E402

КОРЗИНЫ = ((0.0, 0.3, "< 0,3"), (0.3, 1.0, "0,3–1"), (1.0, 1.5, "1–1,5"), (1.5, 3.0, "1,5–3"),
           (3.0, float("inf"), "> 3"))


def _доля(a: int, b: int) -> str:
    return f"{a} из {b} ({100 * a / b:.0f} %)" if b else "0 из 0"


def _корзина(x: float) -> str:
    return next(n for lo, hi, n in КОРЗИНЫ if lo <= x < hi)


def _поле(raw) -> tuple[str, float | None, str]:
    """money-поле «сумма|ВАЛЮТА» → (класс, сумма, валюта)."""
    s = str(raw or "").strip()
    if not s:
        return "пусто", None, ""
    if "|" not in s:
        return "без валюты", None, ""
    amt, cur = s.split("|", 1)
    try:
        v = float(amt or 0)
    except ValueError:
        return "не число", None, cur
    return ("ноль" if v == 0 else "сумма"), v, cur


def main() -> int:
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    курсы_список = client.call("crm.currency.list", {}) or []
    база = next((x.get("CURRENCY") for x in курсы_список if x.get("BASE") == "Y"), None)
    курс = {x.get("CURRENCY"): float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)
            for x in курсы_список}
    print(f"базовая валюта портала: {база}; валют в справочнике: {len(курс)} — {', '.join(sorted(курс))}")
    даты = sorted({str(x.get("DATE_UPDATE") or "")[:10] for x in курсы_список if x.get("DATE_UPDATE")})
    print(f"даты курсов: от {даты[0] if даты else '—'} до {даты[-1] if даты else '—'}")

    sel = ["ID", "OPPORTUNITY", "CURRENCY_ID", "STAGE_SEMANTIC_ID", "CATEGORY_ID",
           ct.BUY_ECON_RUB, ct.BUY_ECON_VAL, ct.ECON_LINK]
    сделки = [d for d in client.list_deals_fast(filter={"CATEGORY_ID": 0, "!STAGE_SEMANTIC_ID": "F"}, select=sel)
              if str(d.get("CATEGORY_ID") or "0") == "0"]
    открытые = [d for d in сделки if str(d.get("STAGE_SEMANTIC_ID") or "").upper() == "P"]
    print(f"сделки реализации (кат. 0): не проигранных {len(сделки)}, из них открытых {len(открытые)}")

    по_валюте = collections.Counter(str(d.get("CURRENCY_ID") or "") for d in сделки)
    без_курса = {c for c in по_валюте if c and c not in курс}
    print("валюта сделки: " + " · ".join(f"{c or 'пусто'} {n}" for c, n in по_валюте.most_common()))
    n_без = sum(n for c, n in по_валюте.items() if c in без_курса or not c)
    n_без_откр = sum(1 for d in открытые if (str(d.get("CURRENCY_ID") or "") in без_курса
                                              or not d.get("CURRENCY_ID")))
    print(f"сумма сделки в валюте без курса (или без валюты): {_доля(n_без, len(сделки))}; "
          f"среди открытых — {_доля(n_без_откр, len(открытые))}; валюты: {', '.join(sorted(без_курса)) or 'нет'}")

    заказы = client.list_items(172, filter={}, select=["id", "parentId2", "opportunity", "currencyId", "stageId"])
    сделки_ids = {str(d["ID"]) for d in сделки}
    заказы = [o for o in заказы if str(o.get("parentId2") or "") in сделки_ids]
    з_валюта = collections.Counter(str(o.get("currencyId") or "") for o in заказы)
    з_без = sum(n for c, n in з_валюта.items() if not c or c not in курс)
    з_ноль = sum(1 for o in заказы if not float(o.get("opportunity") or 0))
    print(f"заказы СП-172 этих сделок: {len(заказы)}; валюта: "
          + " · ".join(f"{c or 'пусто'} {n}" for c, n in з_валюта.most_common())
          + f"; в валюте без курса {_доля(з_без, len(заказы))}; без суммы {_доля(з_ноль, len(заказы))}")

    for код, имя in ((ct.BUY_ECON_RUB, "Cost (rub)"), (ct.BUY_ECON_VAL, "Offer sum of selected supplier")):
        классы = collections.Counter()
        валюты = collections.Counter()
        for d in сделки:
            кл, _v, cur = _поле(d.get(код))
            классы[кл] += 1
            if кл == "сумма":
                валюты[cur] += 1
        print(f"поле «{имя}»: " + " · ".join(f"{k} {v}" for k, v in классы.most_common())
              + "; валюта у заполненных: " + (" · ".join(f"{c or 'пусто'} {n}" for c, n in валюты.most_common()) or "—")
              + f"; без курса {sum(n for c, n in валюты.items() if c not in курс)}")

    by_deal = collections.defaultdict(list)
    for o in заказы:
        by_deal[str(o.get("parentId2"))].append(o)
    отн = {"orders": collections.Counter(), "econ": collections.Counter()}
    источник = collections.Counter()
    не_посчитать = collections.Counter()
    for d in открытые:
        cu = str(d.get("CURRENCY_ID") or "")
        if cu not in курс or not float(d.get("OPPORTUNITY") or 0):
            не_посчитать["продажа неизвестна"] += 1
            continue
        sale = float(d["OPPORTUNITY"]) * курс[cu]
        ords = by_deal.get(str(d["ID"]), [])
        if any(str(o.get("currencyId") or "") not in курс for o in ords if float(o.get("opportunity") or 0)):
            не_посчитать["заказ в валюте без курса"] += 1
            continue
        buy_o = sum(float(o.get("opportunity") or 0) * курс.get(str(o.get("currencyId") or ""), 0) for o in ords)
        if buy_o > 0:
            источник["заказы"] += 1
            отн["orders"][_корзина(buy_o / sale)] += 1
            continue
        econ = None
        for код in (ct.BUY_ECON_RUB, ct.BUY_ECON_VAL):
            кл, v, cur = _поле(d.get(код))
            if кл == "сумма":
                if cur not in курс:
                    econ = "без курса"
                    break
                econ = v * курс[cur]
                break
        if econ == "без курса":
            не_посчитать["поле экономики в валюте без курса"] += 1
        elif econ:
            источник["поля экономики"] += 1
            отн["econ"][_корзина(econ / sale)] += 1
        else:
            источник["нет закупки"] += 1
    print(f"открытые: закупка из {' · '.join(f'{k} {v}' for k, v in источник.most_common())}; "
          f"не посчитать: {' · '.join(f'{k} {v}' for k, v in не_посчитать.most_common()) or 'нет'}")
    for src, имя in (("orders", "из заказов"), ("econ", "из полей экономики")):
        c = отн[src]
        print(f"закупка/продажа, закупка {имя}: " + " · ".join(f"{n} {c[n]}" for _lo, _hi, n in КОРЗИНЫ)
              + f" (всего {sum(c.values())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
