# -*- coding: utf-8 -*-
"""Сценарии и чувствительность поверх модели dcf.py."""
import importlib.util
spec = importlib.util.spec_from_file_location("dcf", "dcf.py")
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

CAPEX0 = {2027: 220.0, 2028: 620.0, 2029: 387.0}


def opex2(t_mine, t_stock, underground=False, plant_cap=7.6e6, strip=3.5):
    """Руда со склада не несёт затрат на добычу — только перелопачивание."""
    t = t_mine + t_stock
    mining = t_mine/1e6*26.0 if underground else t_mine*(1+strip)/1e6*2.0
    mining += t_stock/1e6*1.5
    proc = 72.0*(plant_cap/7.6e6)*(0.30 + 0.70*min(t/plant_cap, 1.0))
    return mining + proc + 22.0*(0.85 if plant_cap < 7e6 else 1.0)


# (год, руда с рудника, руда со склада, Au г/т, Cu %, Ag г/т, подземка)
PLAN = [
    (2029, 1.90e6, 0,      0.42, 0.205, 2.54, False),
    (2030, 6.46e6, 0,      0.42, 0.205, 2.54, False),
    (2031, 7.54e6, 0,      0.42, 0.210, 2.53, False),
    (2032, 7.60e6, 0,      0.41, 0.205, 2.50, False),
    (2033, 7.60e6, 0,      0.41, 0.205, 2.50, False),
    (2034, 7.60e6, 0,      0.36, 0.160, 2.20, False),
    (2035, 4.96e6, 0.64e6, 0.61, 0.145, 2.34, True),
    (2036, 4.96e6, 0.64e6, 0.61, 0.145, 2.34, True),
    (2037, 4.96e6, 0.30e6, 0.62, 0.142, 2.34, True),
    (2038, 4.96e6, 0,      0.63, 0.140, 2.34, True),
    (2039, 4.96e6, 0,      0.63, 0.140, 2.34, True),
    (2040, 4.96e6, 0,      0.63, 0.140, 2.34, True),
]


def run(prices, plan=PLAN, capex=None, cap=7.6e6, rec=None, sust=28.0,
        ug=None, grade_mult=1.0):
    capex = capex or CAPEX0
    rec = rec or d.REC_FLOT_CN
    ug = d.UG_CAPEX if ug is None else ug
    years = sorted(set(list(capex) + [p[0] for p in plan] + list(ug)
                       + [max(p[0] for p in plan) + 1]))
    flows, rows = [], []
    for y in years:
        row = next((p for p in plan if p[0] == y), None)
        cf = -capex.get(y, 0.0) - ug.get(y, 0.0)
        r = o = 0.0
        if row:
            _, tm, ts, au, cu, ag, isug = row
            gm = grade_mult if y <= 2034 else 1.0
            r = d.nsr(tm + ts, au*gm, cu, ag, prices, rec)[0]
            o = opex2(tm, ts, isug, cap)
            cf += r - o - r*0.07 - sust*min((tm + ts)/cap*1.2, 1.0)
        if y == max(years):
            cf -= 50.0
        rows.append((y, r, o, cf)); flows.append(cf)
    return d.npv(flows, 0.10), d.irr(flows), flows, rows


def line(name, res):
    print(f"{name:<50}{res[0]:>9.0f}{res[1]*100:>8.1f}%")


base = run('base')
spot = run('spot')

print(f"{'СЦЕНАРИЙ':<50}{'NPV@10%':>9}{'IRR':>9}")
print("-" * 68)
line("Базовые цены (Au 3500 / Cu 11000 / Ag 50)", base)
line("Спот 17.08.2026 (Au 4400 / Cu 14000 / Ag 66)", spot)

fms = run('spot', rec=d.REC_FMS, capex={2027: 225.0, 2028: 635.0, 2029: 396.0})
line("Спот + ФМС в основной линии", fms)
print(f"{'   -> цена решения по ФМС, млн $':<50}{fms[0]-spot[0]:>9.0f}")

r90 = run('spot', rec=dict(au=0.90, cu=0.90, ag=0.80))
line("Спот + извлечение 0,90 как в плане заказчика", r90)

plan5 = [(y, min(tm, 5.0e6 - ts), ts, au, cu, ag, u)
         for (y, tm, ts, au, cu, ag, u) in PLAN]
plan5 += [(y, 5.0e6, 0, 0.50, 0.17, 2.4, True) for y in range(2041, 2045)]
p5 = run('spot', plan=plan5, capex={2027: 170.0, 2028: 480.0, 2029: 300.0},
         cap=5.0e6, sust=20.0)
line("Спот + завод 5 млн т/год (капитал 950)", p5)
print(f"{'   -> цена решения по мощности, млн $':<50}{p5[0]-spot[0]:>9.0f}")

cz = run('spot', grade_mult=2.38)
line("Спот + центральная зона (1,0 г/т Au до 2034)", cz)

print(f"\nЧУВСТВИТЕЛЬНОСТЬ (база: спот, NPV {spot[0]:+.0f} млн $)")
print("-" * 68)
print(f"{'фактор':<44}{'минус':>11}{'плюс':>11}")


def sens(name, lo, hi):
    print(f"{name:<44}{lo-spot[0]:>+11.0f}{hi-spot[0]:>+11.0f}")


d.PRICE['t'] = dict(au=3520, ag=52.8, cu=11200); lo = run('t')
d.PRICE['t'] = dict(au=5280, ag=79.2, cu=16800); hi = run('t')
sens("Цены всех металлов ±20 %", lo[0], hi[0])

d.PRICE['t'] = dict(au=3520, ag=66, cu=14000); lo = run('t')
d.PRICE['t'] = dict(au=5280, ag=66, cu=14000); hi = run('t')
sens("Цена золота ±20 %", lo[0], hi[0])

sens("Капитал ±25 %",
     run('spot', capex={k: v*1.25 for k, v in CAPEX0.items()})[0],
     run('spot', capex={k: v*0.75 for k, v in CAPEX0.items()})[0])
sens("Содержание Au в руде ±15 %",
     run('spot', grade_mult=0.85)[0], run('spot', grade_mult=1.15)[0])
sens("Извлечение Au ±5 п.п.",
     run('spot', rec=dict(au=0.8164, cu=0.837, ag=0.792))[0],
     run('spot', rec=dict(au=0.9164, cu=0.837, ag=0.792))[0])

# вскрыша: пересчёт через прямую правку удельной ставки добычи
import types
for label, strip in (("Коэффициент вскрыши 5,0 / 2,5", None),):
    orig = opex2
    def mk(s):
        def f(tm, ts, u=False, cap=7.6e6, strip=3.5):
            return orig(tm, ts, u, cap, s)
        return f
    globals()['opex2'] = mk(5.0); lo = run('spot')
    globals()['opex2'] = mk(2.5); hi = run('spot')
    globals()['opex2'] = orig
    sens(label, lo[0], hi[0])

print(f"\nДЕНЕЖНЫЙ ПОТОК ПО ГОДАМ, спот-сценарий, млн $")
print("-" * 68)
for y, r, o, cf in spot[3]:
    print(f"{y}  NSR {r:>7.1f}   OPEX {o:>7.1f}   ДП {cf:>+8.1f}")
print(f"сумма денежного потока: {sum(spot[2]):+.0f}")
