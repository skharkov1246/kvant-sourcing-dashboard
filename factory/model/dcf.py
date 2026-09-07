# -*- coding: utf-8 -*-
"""Аудит экономики ГОК Л-1. Все цифры считаются, ни одна не вписывается руками."""

OZ = 31.1035  # г в тройской унции

# ── исходные данные (отчёт ЦНИГРИ + календарный план) ──────────────────────
REC_FLOT_CN = dict(au=0.8664, cu=0.837, ag=0.792)   # флотация + цианирование
REC_FMS     = dict(au=0.8288, cu=0.760, ag=0.711)   # + фотометрическая сепарация
REC_AU_CONC = 0.755   # доля Au руды в медный концентрат (остальное — в Доре)
REC_AG_CONC = 0.703
CU_GRADE_CONC = 0.153 # содержание Cu в концентрате

PRICE = {
    'base': dict(au=3500, ag=50,  cu=11000),
    'spot': dict(au=4400, ag=66,  cu=14000),
}
PAY = dict(cu=0.935, au_conc=0.925, ag_conc=0.90, au_dore=0.995, ag_dore=0.98)
TC, RC_CU, RC_AU, RC_AG = 70.0, 176.0, 18.0, 1.5   # $/т к-та, $/т Cu, $/унц, $/унц
FREIGHT, MOISTURE = 100.0, 0.09                     # $/влажную т
DORE_REFINING = 0.75                                # $ млн/год

def nsr(t_ore, au_gt, cu_pct, ag_gt, prices, rec=REC_FLOT_CN):
    """Выручка нетто, $ млн/год."""
    p = PRICE[prices]
    au_in, cu_in, ag_in = t_ore*au_gt/1000, t_ore*cu_pct/100, t_ore*ag_gt/1000  # кг, т, кг
    k = rec['au']/REC_FLOT_CN['au']   # масштабирование при другой схеме
    au_conc = au_in * REC_AU_CONC * k
    au_dore = au_in * (rec['au'] - REC_AU_CONC*k)
    cu_conc = cu_in * rec['cu']
    ag_conc = ag_in * REC_AG_CONC * (rec['ag']/REC_FLOT_CN['ag'])
    ag_dore = ag_in * (rec['ag'] - REC_AG_CONC*(rec['ag']/REC_FLOT_CN['ag']))
    m_conc  = cu_conc / CU_GRADE_CONC                       # т концентрата
    gross = (cu_conc*PAY['cu']*p['cu']
             + au_conc/OZ*1000*PAY['au_conc']*p['au']
             + ag_conc/OZ*1000*PAY['ag_conc']*p['ag']) / 1e6
    ded = (TC*m_conc + RC_CU*cu_conc*PAY['cu']
           + RC_AU*au_conc/OZ*1000*PAY['au_conc']
           + RC_AG*ag_conc/OZ*1000*PAY['ag_conc']
           + FREIGHT*m_conc/(1-MOISTURE)) / 1e6
    dore = (au_dore/OZ*1000*PAY['au_dore']*p['au']
            + ag_dore/OZ*1000*PAY['ag_dore']*p['ag'])/1e6 - DORE_REFINING
    return gross - ded + dore, m_conc, au_conc+au_dore, cu_conc, ag_conc+ag_dore

def npv(flows, r, first_year_discounted=1):
    return sum(f/(1+r)**(i+first_year_discounted) for i, f in enumerate(flows))

def irr(flows):
    lo, hi = -0.5, 1.0
    for _ in range(200):
        mid = (lo+hi)/2
        if npv(flows, mid) > 0: lo = mid
        else: hi = mid
    return (lo+hi)/2

# ── операционные затраты ───────────────────────────────────────────────────
def opex(t_ore, strip=3.5, underground=False, plant_cap=7.6e6):
    """$ млн/год. Постоянная часть фабрики не масштабируется с загрузкой."""
    if underground:
        mining = t_ore/1e6 * 26.0          # подземная добыча, $/т руды
    else:
        mining = t_ore*(1+strip)/1e6 * 2.0 # $/т горной массы
    # переработка: 70 % переменных + 30 % постоянных от полной загрузки
    proc_full = 72.0 * (plant_cap/7.6e6)
    load = t_ore/plant_cap
    proc = proc_full*(0.30 + 0.70*min(load, 1.0))
    ga = 22.0 * (0.85 if plant_cap < 7e6 else 1.0)
    return mining + proc + ga

# ── сценарии ───────────────────────────────────────────────────────────────
# (год, руда на фабрику т, Au г/т, Cu %, Ag г/т, подземка?)
PLAN_76 = [
    (2029, 1.90e6, 0.42, 0.205, 2.54, False),   # пуск в IV кв. 2029: 25 % года
    (2030, 6.46e6, 0.42, 0.205, 2.54, False),   # выход на режим: 85 %
    (2031, 7.54e6, 0.42, 0.21,  2.53, False),
    (2032, 7.60e6, 0.41, 0.205, 2.50, False),
    (2033, 7.60e6, 0.41, 0.205, 2.50, False),
    (2034, 7.60e6, 0.36, 0.16,  2.20, False),   # переходный, избыток на склад
    (2035, 5.60e6, 0.60, 0.145, 2.34, True),    # подземка + доработка склада
    (2036, 5.60e6, 0.60, 0.145, 2.34, True),
    (2037, 5.20e6, 0.62, 0.142, 2.34, True),
    (2038, 4.96e6, 0.63, 0.14,  2.34, True),
    (2039, 4.96e6, 0.63, 0.14,  2.34, True),
    (2040, 4.96e6, 0.63, 0.14,  2.34, True),    # остаток запасов со склада
]

CAPEX_76 = {2027: 220.0, 2028: 620.0, 2029: 387.0}
UG_CAPEX = {2031: 70.0, 2032: 70.0, 2033: 70.0, 2034: 70.0}
SUSTAIN, CLOSURE, NDPI_RATE = 28.0, 50.0, 0.07

def model(prices, plan=PLAN_76, capex=CAPEX_76, plant_cap=7.6e6,
          rec=REC_FLOT_CN, sustain=SUSTAIN, ug_capex=UG_CAPEX, label=''):
    years = sorted(set(list(capex) + [y[0] for y in plan] + list(ug_capex) + [max(y[0] for y in plan)+1]))
    rows, flows = [], []
    for y in years:
        rec_row = next((p for p in plan if p[0] == y), None)
        cf = -capex.get(y, 0.0) - ug_capex.get(y, 0.0)
        r, o, nd, ss = 0.0, 0.0, 0.0, 0.0
        if rec_row:
            _, t, au, cu, ag, ug = rec_row
            r = nsr(t, au, cu, ag, prices, rec)[0]
            o = opex(t, underground=ug, plant_cap=plant_cap)
            nd = r*NDPI_RATE
            ss = sustain * min(t/plant_cap*1.2, 1.0)
            cf += r - o - nd - ss
        if y == max(years):
            cf -= CLOSURE
        rows.append((y, r, o, nd, ss, cf))
        flows.append(cf)
    return rows, flows

def report(name, prices, **kw):
    rows, flows = model(prices, **kw)
    n10 = npv(flows, 0.10); n8 = npv(flows, 0.08); i = irr(flows)
    cum, payback = 0.0, None
    for y, *_, cf in rows:
        cum += cf
        if cum > 0 and payback is None and y > 2029: payback = y
    print(f"\n{'='*78}\n{name}\n{'='*78}")
    print(f"{'год':<6}{'NSR':>9}{'OPEX':>9}{'НДПИ':>8}{'подд.':>8}{'ДП':>10}")
    for y, r, o, nd, ss, cf in rows:
        print(f"{y:<6}{r:>9.1f}{o:>9.1f}{nd:>8.1f}{ss:>8.1f}{cf:>10.1f}")
    print(f"{'сумма ДП':<6}{sum(flows):>44.1f}")
    print(f"NPV@10% = {n10:>8.0f} млн $   NPV@8% = {n8:>8.0f}   IRR = {i*100:>5.1f} %"
          f"   окупаемость: {payback or 'нет'}")
    return n10, i

if __name__ == '__main__':
    # проверка баланса номинального года
    v, mc, au, cu, ag = nsr(7.6e6, 0.42, 0.205, 2.52, 'base')
    print(f"НОМИНАЛЬНЫЙ ГОД: NSR {v:.1f} млн $ | концентрат {mc:,.0f} т | "
          f"Au {au:.0f} кг | Cu {cu:,.0f} т | Ag {ag:,.0f} кг")
    print(f"OPEX {opex(7.6e6):.1f} млн $ = {opex(7.6e6)*1e6/7.6e6:.2f} $/т руды")
    vs, *_ = nsr(7.6e6, 0.42, 0.205, 2.52, 'spot')
    print(f"NSR при спот-ценах: {vs:.1f} млн $")

    report('БАЗОВЫЕ ЦЕНЫ (Au 3500 / Cu 11000 / Ag 50)', 'base')
    report('СПОТ 17.08.2026 (Au 4400 / Cu 14000 / Ag 66)', 'spot')

    # ФМС: та же руда, но с потерями сортировки
    print("\n--- ФМС в основной линии, спот ---")
    report('СПОТ + ФМС', 'spot', rec=REC_FMS, capex={2027:225.0, 2028:635.0, 2029:396.0})

    # завод 5 млн т/год
    plan5 = []
    for (y, t, au, cu, ag, ug) in PLAN_76:
        plan5.append((y, min(t, 5.0e6), au, cu, ag, ug))
    for y in range(2041, 2045):
        plan5.append((y, 5.0e6, 0.50, 0.17, 2.4, True))
    report('СПОТ + завод 5 млн т/год (капитал 950)', 'spot', plan=plan5,
           capex={2027:170.0, 2028:480.0, 2029:300.0}, plant_cap=5.0e6, sustain=20.0)
