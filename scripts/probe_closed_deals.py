"""Зонд: сколько сделок закрыто и как легла бы премия КАМов по новой схеме — только агрегаты.

ВОПРОС ВЛАДЕЛЬЦА. Сколько сделок закрыто, и что ретроспективно получил бы каждый КАМ
по новой системе премирования (премия от маржи после нагрузки, зоны плана, порог
безубыточности) против текущей (1,5 % выручки).

ЧТО ПЕЧАТАЕТ. Журнал прогона публичный, поэтому — только счётчики, доли и корзины:
число закрытых сделок по годам и стадиям, число КАМов, распределение премий по
корзинам сумм, отношение маржи компании к марже безубыточности. Ни имён, ни номеров
пользователей, ни сумм продаж по людям, ни итоговых оборотов.

МОДЕЛЬ (допущения, те же, что в документе «Система премирования», ред. 12):
  • закрытая сделка — воронка «Реализация» (кат. 0), стадия с семантикой S;
  • закрытая — также стадия «Оплата получена | Закрытие сделки»; окно — 12 месяцев по MOVED_TIME;
  • продажа — OPPORTUNITY сделки, закупка — Σ заказов СП-172 (кроме FAIL), в рублях
    по курсам Bitrix; прямые расходы — 6,5 % закупки; нагрузка — 28 % выручки;
  • маржа безубыточности — 540 млн руб. в год; план КАМа — её доля по доле продаж;
  • ставки старшего КАМа: 1,5 / 12 / 15 % маржи по зонам; зоны 2–3 — 25 %, если
    компания не прошла безубыток; текущая схема — 1,5 % выручки.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitrix_client import BitrixClient  # noqa: E402

KAM_FIELD = "UF_CRM_1740390857"
PL_FIELD = "UF_CRM_1779187425"
# методика финдиректора (калькулятор маржи сделки v3): ПЗ ≈ закупка × 1,10 (медиана
# доп. затрат 10 %), финансирование ≈ 5 % выручки, платёжный агент 2 % закупки
PZ_OVER = 0.10
FIN = 0.05
PAY_AGENT = 0.02
FIXED = (540e6, 1350e6)   # две гипотезы постоянных расходов: со слов владельца и по ТБЗ ×2,06
RATES = (0.02, 0.10, 0.13)
BUCKETS = [0, 250e3, 500e3, 1e6, 2e6, 5e6, float("inf")]
LABELS = ["до 250 тыс.", "250–500 тыс.", "0,5–1 млн", "1–2 млн", "2–5 млн", "от 5 млн"]


def корзина(v: float) -> str:
    for i in range(len(BUCKETS) - 1):
        if BUCKETS[i] <= v < BUCKETS[i + 1]:
            return LABELS[i]
    return LABELS[0]


def ид(v) -> str:
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v or "").strip()


def премия_новая(m: float, plan: float, прошли: bool) -> float:
    if m <= 0 or plan <= 0:
        return 0.0
    g = 1.0 if прошли else 0.25
    z1 = min(m, plan) * RATES[0]
    z2 = max(0.0, min(m, 1.3 * plan) - plan) * RATES[1] * g
    z3 = max(0.0, m - 1.3 * plan) * RATES[2] * g
    return z1 + z2 + z3


def main() -> int:
    url = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not url:
        print("нет BITRIX_WEBHOOK_URL", file=sys.stderr)
        return 1
    c = BitrixClient(url)
    curlist = c.call("crm.currency.list", {}) or []
    rate = {x.get("CURRENCY"): float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1) for x in curlist}
    rub = rate.get("RUB") or 1.0

    def в_руб(v, cu) -> float:
        return float(v or 0) * rate.get(cu, 1.0) / rub

    stages = c.deal_stages_cat(0)
    deals = c.list_deals_fast(filter={"CATEGORY_ID": 0},
                              select=["ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID",
                                      "MOVED_TIME", "DATE_CREATE", KAM_FIELD, PL_FIELD])
    orders = c.list_items(172, select=["id", "stageId", "opportunity", "currencyId", "parentId2"])

    print(f"Сделок в воронке «Реализация»: {len(deals)}")
    по_семантике = Counter(d.get("STAGE_SEMANTIC_ID") or "?" for d in deals)
    print("По семантике стадии (P — в работе, S — успешно, F — провал):", dict(по_семантике))
    print("Число сделок по стадиям:")
    по_стадии = Counter(d.get("STAGE_ID") for d in deals)
    for sid, name in stages.items():
        if по_стадии.get(sid):
            print(f"  {name}: {по_стадии[sid]}")

    оплачено = {sid for sid, name in stages.items() if str(name).startswith("Оплата получена")}
    print("Стадии «оплата получена»:", len(оплачено))
    закрытые = [d for d in deals if d.get("STAGE_SEMANTIC_ID") == "S" or d.get("STAGE_ID") in оплачено]
    print(f"Закрытых (успешна или оплата получена): {len(закрытые)}")
    по_году = Counter(str(d.get("MOVED_TIME") or "")[:4] for d in закрытые)
    print("Закрытых по году перехода в стадию:", dict(sorted(по_году.items())))

    buy = defaultdict(float)
    успешные_заказы = set()
    for o in orders:
        if str(o.get("stageId", "")).endswith(":FAIL"):
            continue
        p = ид(o.get("parentId2"))
        if p:
            buy[p] += в_руб(o.get("opportunity"), o.get("currencyId"))
            if str(o.get("stageId", "")).endswith(":SUCCESS"):
                успешные_заказы.add(p)
    print(f"Сделок «Реализации» с выполненным заказом поставщику (СП-172 SUCCESS): "
          f"{sum(1 for d in deals if str(d['ID']) in успешные_заказы)}")

    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    окно = [d for d in закрытые if str(d.get("MOVED_TIME") or "") >= since]
    print(f"Закрыто успешно за 12 месяцев: {len(окно)}; с полем «КАМ»: "
          f"{sum(1 for d in окно if ид(d.get(KAM_FIELD)))}; с закупкой из СП-172: "
          f"{sum(1 for d in окно if buy.get(str(d['ID'])))}; с полем «Product leader»: "
          f"{sum(1 for d in окно if ид(d.get(PL_FIELD)))}")

    продажа = defaultdict(float)
    маржа = defaultdict(float)
    сделок = Counter()
    без_закупки = 0
    for d in окно:
        k = ид(d.get(KAM_FIELD))
        if not k:
            continue
        s = в_руб(d.get("OPPORTUNITY"), d.get("CURRENCY_ID"))
        b = buy.get(str(d["ID"]), 0.0)
        if b <= 0:
            без_закупки += 1
            continue
        продажа[k] += s
        маржа[k] += s - b * (1 + PZ_OVER) - s * FIN - b * PAY_AGENT
        сделок[k] += 1
    if not продажа:
        print("Нет закрытых сделок с КАМом и закупкой — моделировать не на чем.")
        return 0
    всего_продаж = sum(продажа.values())
    всего_маржи = sum(маржа.values())
    print(f"КАМов с закрытыми сделками за 12 месяцев: {len(продажа)}; сделок без закупки (пропущены): {без_закупки}")
    print(f"Результат сделок (методика ФД) в доле продаж: {всего_маржи / всего_продаж:.1%}; "
          f"средний коэффициент продажа / закупка: {всего_продаж / max(1.0, sum(buy.get(str(d['ID']), 0) for d in окно if ид(d.get(KAM_FIELD)))):.2f}")
    for BREAKEVEN in FIXED:
        прошли = всего_маржи >= BREAKEVEN
        print(f"--- Гипотеза постоянных расходов {BREAKEVEN / 1e6:.0f} млн: результат к постоянным расходам "
              f"{всего_маржи / BREAKEVEN:.2f} ({'безубыток пройден' if прошли else 'безубыток не пройден'})")
        cur_b, new_b, ratio_b, att_b = Counter(), Counter(), Counter(), Counter()
        for k in продажа:
            plan = BREAKEVEN * продажа[k] / всего_продаж
            cur = продажа[k] * 0.015
            new = премия_новая(маржа[k], plan, прошли)
            cur_b[корзина(cur)] += 1
            new_b[корзина(new)] += 1
            att = маржа[k] / plan if plan else 0
            att_b["ниже 0 (убыток)" if att < 0 else "0–50 %" if att < .5 else "50–100 %" if att < 1
                  else "100–130 %" if att < 1.3 else "от 130 %"] += 1
            r = new / cur if cur else 0
            ratio_b["меньше половины" if r < .5 else "50–100 %" if r < 1 else "100–200 %" if r < 2
                    else "в 2 раза и больше"] += 1
        print("Премия КАМа за 12 месяцев, число людей по корзинам (текущая 1,5 % выручки | новая):")
        for lab in LABELS:
            print(f"  {lab}: {cur_b.get(lab, 0)} | {new_b.get(lab, 0)}")
        print("Выполнение плана по результату, число КАМов:", dict(att_b))
        print("Новая премия к текущей, число КАМов:", dict(ratio_b))
    print("Медиана сделок на КАМа:", sorted(сделок.values())[len(сделок) // 2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
