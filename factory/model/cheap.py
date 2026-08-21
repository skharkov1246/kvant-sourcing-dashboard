# -*- coding: utf-8 -*-
"""Варианты снижения капитала: что реально можно убрать и чем за это платим.

Запуск: python3 cheap.py (из каталога factory/model)
Все сценарии считаются при спот-ценах 17.08.2026.
"""
import importlib.util
spec = importlib.util.spec_from_file_location("dcf", "dcf.py")
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

PLAN76 = [
    (2029, 1.90e6, 0,      0.42, 0.205, 2.54, False), (2030, 6.46e6, 0,      0.42, 0.205, 2.54, False),
    (2031, 7.54e6, 0,      0.42, 0.210, 2.53, False), (2032, 7.60e6, 0,      0.41, 0.205, 2.50, False),
    (2033, 7.60e6, 0,      0.41, 0.205, 2.50, False), (2034, 7.60e6, 0,      0.36, 0.160, 2.20, False),
    (2035, 4.96e6, 0.64e6, 0.61, 0.145, 2.34, True),  (2036, 4.96e6, 0.64e6, 0.61, 0.145, 2.34, True),
    (2037, 4.96e6, 0.30e6, 0.62, 0.142, 2.34, True),  (2038, 4.96e6, 0,      0.63, 0.140, 2.34, True),
    (2039, 4.96e6, 0,      0.63, 0.140, 2.34, True),  (2040, 4.96e6, 0,      0.63, 0.140, 2.34, True),
]
# завод 5 млн т/год: избыток карьерной руды идёт на склад и дорабатывается после 2040 г.
PLAN50 = [(y, min(tm, 5.0e6 - ts), ts, au, cu, ag, u) for (y, tm, ts, au, cu, ag, u) in PLAN76]
PLAN50 += [(y, 5.0e6, 0, 0.50, 0.17, 2.4, True) for y in range(2041, 2045)]


def run(total_capex, plan=PLAN76, cap=7.6e6, sust=28.0, contract=False):
    """contract=True — подрядная добыча: капитал ниже, затраты на горную массу +25 %."""
    k = total_capex/1227.0   # ряд 220/620/387 суммируется в 1227
    capex = {2027: 220.0*k, 2028: 620.0*k, 2029: 387.0*k}
    end = max(p[0] for p in plan) + 1
    flows = []
    for y in sorted(set(list(capex) + [p[0] for p in plan] + list(d.UG_CAPEX) + [end])):
        row = next((p for p in plan if p[0] == y), None)
        cf = -capex.get(y, 0.0) - d.UG_CAPEX.get(y, 0.0)
        if row:
            _, tm, ts, au, cu, ag, ug = row
            r = d.nsr(tm + ts, au, cu, ag, 'spot')[0]
            mining = tm/1e6*26.0 if ug else tm*4.5/1e6*2.0*(1.25 if contract else 1.0)
            o = (mining + ts/1e6*1.5
                 + 72.0*(cap/7.6e6)*(0.30 + 0.70*min((tm + ts)/cap, 1.0))
                 + 22.0*(0.85 if cap < 7e6 else 1.0))
            cf += r - o - r*0.07 - sust*min((tm + ts)/cap*1.2, 1.0)
        if y == end:
            cf -= 50.0
        flows.append(cf)
    return d.npv(flows, 0.10), d.irr(flows)


print(f"{'ВАРИАНТ':<54}{'CAPEX':>7}{'NPV@10%':>10}{'IRR':>8}")
print("-" * 79)
for name, cap_v, kw in [
    ("Базовый: свой парк, площадка неизвестна",   1227, dict()),
    ("Подрядная добыча вместо своего парка",      1108, dict(contract=True)),
    ("Китайская комплектация мельниц и флотации", 1163, dict()),
    ("Площадка рядом с готовой инфраструктурой",  1142, dict()),
    ("Резерв 15 % вместо 18 %",                   1196, dict()),
    ("ВСЁ ВМЕСТЕ — низкий случай",                 914, dict(contract=True)),
]:
    n, i = run(cap_v, **kw)
    print(f"{name:<54}{cap_v:>7}{n:>10.0f}{i*100:>7.1f}%")

n, i = run(950, plan=PLAN50, cap=5.0e6, sust=20.0)
print(f"{'Завод 5 млн т/год, базовая комплектация':<54}{950:>7}{n:>10.0f}{i*100:>7.1f}%")
n, i = run(708, plan=PLAN50, cap=5.0e6, sust=20.0, contract=True)
print(f"{'Завод 5 млн т/год + низкий случай':<54}{708:>7}{n:>10.0f}{i*100:>7.1f}%")
