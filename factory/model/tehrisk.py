# -*- coding: utf-8 -*-
"""Денежная оценка технологического риска: масштабирование, реагенты, недостижение мощности.

Проверяет опасения владельца: работоспособность вне лаборатории, масштабируемость,
сходимость материальных балансов по расходу реагентов.
Курс 85,85 руб./долл. на 31.08.2026.
"""
import importlib.util
spec = importlib.util.spec_from_file_location("staged", "staged.py")
S = importlib.util.module_from_spec(spec)
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(S)

KURS = 85.85
BAZA = S.npv(S.model('spot')[0])

def scenariy(nazv, k_izvl=1.0, k_reagent=1.0, k_moshch=1.0, dop_capex=0.0):
    """k_izvl — множитель извлечения; k_reagent — множитель расхода реагентов;
    k_moshch — множитель достигнутой производительности; dop_capex — доп. капитал, млн долл."""
    flows, rows = S.model('spot')
    new = []
    for (y, r, o, cf), f in zip(rows, flows):
        if r > 0:
            # реагенты принимаем как 17 % операционных затрат передела
            reag = o * 0.17
            o2 = (o - reag) * k_moshch + reag * k_reagent * k_moshch
            r2 = r * k_izvl * k_moshch
            cf = (f - r + o) + r2 - o2 - (r2 - r) * S.NDPI
        new.append(cf)
    if dop_capex:
        new[1] -= dop_capex
    return nazv, S.npv(new), S.npv(new) - BAZA

print(f'Базовый сценарий (обе очереди, котировки 31.08.2026): ЧДД {BAZA:.0f} млн долл.')
print(f'Курс 85,85 руб./долл.  Ожидание заказчика 50 млрд руб. = {50e9/KURS/1e6:.0f} млн долл.')
print(f'Наш расчёт пика потребности в финансировании: 580 млн долл. = {580*KURS/1000:.1f} млрд руб.\n')

print(f"{'СЦЕНАРИЙ ТЕХНОЛОГИЧЕСКОГО РИСКА':<58}{'ЧДД':>9}{'потеря':>10}")
print('-' * 78)
for args in [
    ('Извлечение ниже лабораторного на 2 п.п.',            dict(k_izvl=1 - 0.02/0.80)),
    ('Извлечение ниже лабораторного на 5 п.п.',            dict(k_izvl=1 - 0.05/0.80)),
    ('Расход реагентов выше расчётного в 1,5 раза',        dict(k_reagent=1.5)),
    ('Расход реагентов выше расчётного в 2 раза',          dict(k_reagent=2.0)),
    ('Производительность 90 % от проектной',               dict(k_moshch=0.90)),
    ('Производительность 80 % от проектной',               dict(k_moshch=0.80)),
    ('Пуск затянут, доп. капитал 150 млн',                 dict(dop_capex=150.0)),
    ('РЕАЛИСТИЧНЫЙ ПЕССИМИЗМ: −3 п.п., реагенты ×1,4, 90 %', dict(k_izvl=1-0.03/0.80, k_reagent=1.4, k_moshch=0.90)),
    ('ТЯЖЁЛЫЙ: −5 п.п., реагенты ×1,7, 85 %, +150 капитала', dict(k_izvl=1-0.05/0.80, k_reagent=1.7, k_moshch=0.85, dop_capex=150.0)),
]:
    n, v, d = scenariy(args[0], **args[1])
    print(f'{n:<58}{v:>9.0f}{d:>10.0f}')

print('\nЦЕНА ОДНОГО ПРОЦЕНТНОГО ПУНКТА И ЕДИНИЦЫ РАСХОДА')
_, v1, d1 = scenariy('', k_izvl=1 - 0.01/0.80)
print(f'  1 процентный пункт извлечения золота          = {abs(d1):.0f} млн долл. ЧДД')
_, v2, d2 = scenariy('', k_reagent=1.1)
print(f'  10 % перерасхода реагентов                    = {abs(d2):.0f} млн долл. ЧДД')
_, v3, d3 = scenariy('', k_moshch=0.95)
print(f'  5 % недостижения проектной мощности           = {abs(d3):.0f} млн долл. ЧДД')

print('\nРАЗРЫВ В ФИНАНСИРОВАНИИ ПЕРВОГО ЭТАПА')
for est in (10, 15, 20):
    razryv = 50 - est
    print(f'  есть {est} млрд руб. -> разрыв {razryv} млрд руб. = {razryv*1e9/KURS/1e6:.0f} млн долл.'
          f' ({razryv/50*100:.0f} % потребности)')
