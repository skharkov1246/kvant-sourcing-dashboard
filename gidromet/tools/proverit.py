#!/usr/bin/env python3
"""Проверка собранных разделов на противоречия каноническому своду чисел.

Двадцать разделов пишут двадцать исполнителей. Проверяются две вещи:
  1) не встречаются ли величины, которые были отвергнуты в ходе работы
     (устаревшие оценки, ошибочные допущения) — их быть не должно;
  2) если раздел называет опорную величину, совпадает ли она с канонической.

Запуск:  python3 gidromet/tools/proverit.py
"""
import json, re, sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'

# величины, отвергнутые в ходе работы: если встретились — почти наверняка ошибка
OTVERGNUTO = [
    (r'0,32\s*%\s*(меди|Cu)', 'содержание меди 0,32 % — это проба Л-001 1996 года, '
                              'по запасам 0,178 %, по очередям 0,24 и 0,173 %'),

    (r'31,85\s*г/т.{0,40}концентрат', 'висмут 31,85 г/т относится к РУДЕ, а не к концентрату'),
    # 3,0 г/т законны при перечислении проб рядом с 0,37 или 1,6 — тогда это сопоставление,
    # а не подстановка; ловим только одиночное употребление применительно к очереди
    (r'3,0\s*г/т(?!.{0,200}(0,37|1,6)).{0,60}(перв\w+ очеред|для расч)',
     'золото 3,0 г/т — проба Л-001 1996 года; по паспорту первая очередь 1,6 г/т'),
    (r'(?<![A-Za-z_.])(IRR|NPV|CAPEX|OPEX)(?![A-Za-z_])', 'англицизм: используйте «внутренняя норма '
     'доходности», «чистый дисконтированный доход», «капитальные затраты»'),
]

# опорные величины: если раздел упоминает тему, число обязано быть этим
# (название, обязательное число, тема, разделы, где величина обязана быть названа)
OPORNYE = [
    ('86 621', r'86\s?621', r'концентрат[а-я]*\s+на\s+передел', {'project', 'syrye', 'oborud'}),
    ('2 920', r'2\s?920', r'безубыточн', {'scenarii', 'decision'}),
    ('103,4 %', r'103,4', r'сходимост', {'proverka'}),
    ('332,4 %', r'332,4', r'сходимост', {'proverka'}),
    ('493 млн $', r'493', r'привлечен', {'etapy', 'project'}),
    ('548 млн $', r'548', r'капитал', {'capex', 'etapy', 'gonorar'}),
]


def tekst(p):
    return json.dumps(json.loads(p.read_text(encoding='utf-8')), ensure_ascii=False)


def main():
    files = sorted(DATA.glob('*.json'))
    if not files:
        sys.exit('в gidromet/data ещё нет файлов')
    vsego = 0
    for p in files:
        t = tekst(p)
        zamechaniya = []
        for pat, pochemu in OTVERGNUTO:
            flags = 0 if 'англицизм' in pochemu else re.I
            for m in re.finditer(pat, t, flags):
                kus = t[max(0, m.start() - 60):m.end() + 60].replace('\\n', ' ')
                zamechaniya.append(f'ОТВЕРГНУТАЯ ВЕЛИЧИНА: {pochemu}\n      …{kus}…')
        # 1 227 и 645 млн $ законны только при явном указании конфигурации рядом
        for chislo, chto, kvalifikator in [
            ('1 227', 'полная мощность без разбивки на очереди',
             r'(единовременн|полн\w+ мощност|полномасштабн|без разбивки|audit\.py|cheap\.py)'),
            ('645', 'первая очередь до пересчёта под Карелию',
             r'(исходн\w+ капитал|до пересч|без карельск|staged\.py|вместо|очеред)'),
        ]:
            for m in re.finditer(chislo.replace(' ', r'\s?'), t):
                okno = t[max(0, m.start()-320):m.end()+320]
                if not re.search(kvalifikator, okno, re.I):
                    zamechaniya.append(
                        f'величина {chislo} млн $ без указания конфигурации ({chto})\n'
                        f'      …{okno[260:460]}…')

        for nazv, chislo, tema, gde in OPORNYE:
            if p.stem not in gde:
                continue
            if re.search(tema, t, re.I) and not re.search(chislo, t):
                zamechaniya.append(f'тема затронута, а опорная величина {nazv} не названа')
        if zamechaniya:
            print(f'\n=== {p.name}')
            for z in zamechaniya:
                print('  ·', z)
            vsego += len(zamechaniya)
    print(f'\nфайлов проверено: {len(files)}, замечаний: {vsego}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
