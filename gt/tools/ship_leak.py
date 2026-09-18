#!/usr/bin/env python3
"""Наша заявка лежит в открытом доступе: кто её опубликовал и что именно видно.

НАЙДЕНО РАЗВЕДКОЙ 18.09.2026, попутно и не по заданию. Две страницы поставщиков
несут строки заявки ЛУКОЙЛ дословно — с наименованиями, номерами и количествами
до штуки, в том же порядке. Это не вопрос сорсинга, поэтому и вынесено отдельно.

ЧТО ИЗВЕСТНО ТОЧНО. Страницы прочитаны, совпадение проверено по нашим же данным:
номер, количество и порядок строк. Дальше — измерение, а не мнение: инструмент
берёт номера, названные разведкой на этих страницах, и считает, сколько строк
заявки и сколько денег ими закрывается.

ЧЕГО МЫ НЕ ЗНАЕМ, И ЭТО ВАЖНЕЕ. Кто выложил и на каком этапе. Вариантов три, и
различить их открытыми данными нельзя: заказчик опубликовал спецификацию сам
(тендер), наш запрос поставщику ушёл дальше по цепочке, или третья сторона
получила тот же запрос от другого участника. Обвинение здесь не выносится: для
него нет измерения.

ПОЧЕМУ ЭТО НЕ ТОЛЬКО ПРО КОНФИДЕНЦИАЛЬНОСТЬ. Такая страница ломает проверку
источника: она выглядит как независимое подтверждение номера, а является
отражением нашего же текста. Оба домена внесены в список круговых источников
приёмника разведки (gt/tools/rv_merge.py), иначе следующий проход принял бы
наше отражение за свидетеля.

    python gt/tools/ship_leak.py [--print]
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASK = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/data/ship_leak.json"

#: Страницы, где строки заявки найдены дословно. Номера — те, что разведка
#: процитировала со страницы; они и служат уликой совпадения.
SOURCES = [
    {
        "url": "http://www.gas-turbine-parts.com/News/Product_FAQ/151.html",
        "holder": "Hangzhou Gas Turbine Parts Co., Ltd (Китай)",
        "what_is_published": "перечень на 437 строк с наименованиями, номерами и количествами; "
                             "порядок строк совпадает с нашим",
        "language": "английский",
        "quoted_rows": ["366101014", "RM13029"],
        "quoted_verbatim": ["14 366101014 THRUST WASHER - 16MM I/D 18 pcs",
                            "22 RM13029 BOLT - M12 x 35MM 6 pcs"],
        # Улика происхождения, найденная второй разведкой независимо: тот же
        # перечень лежит у Leda Greenpower, подписанный как запрос «RS 2YR OPS &
        # MAintenance». Значит это, скорее всего, КОПИЯ документа Siemens о
        # двухлетнем комплекте эксплуатации, а не наш запрос поставщику. Версия
        # усиливается, но не доказывается: подписи на чужой странице
        # доказательством происхождения не являются.
        "same_list_elsewhere": "Тот же перечень найден у Leda Greenpower с подписью запроса "
                               "«RS 2YR OPS & MAINTENANCE» — похоже на копию документа Siemens "
                               "о двухлетнем комплекте эксплуатации, а не на наш запрос. Это "
                               "версия происхождения, а не доказательство.",
    },
    {
        "url": "https://ausenist.com",
        "holder": "Quanzhou Ausenist Technology Co., Ltd (Китай)",
        "what_is_published": "строки заявки НА РУССКОМ, вместе с позиционными пометками вида "
                             "«п H06» — то есть в том виде, в каком их пишем мы",
        "language": "русский",
        "quoted_rows": ["21887"],
        "quoted_verbatim": ["Фильтр NOV 21887 T4690-D1110-H0001 p H06"],
    },
]


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def build() -> dict:
    rows = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    by = {}
    for r in rows:
        k = key(r.get("pn"))
        if k:
            by.setdefault(k, r)
    srcs = []
    for s in SOURCES:
        hit = [by[key(p)] for p in s["quoted_rows"] if key(p) in by]
        srcs.append(dict(s, rows_matched_in_our_summary=len(hit),
                         usd_on_matched=round(sum(map(expo, hit)), 2)))
    return {
        "updated": date.today().isoformat(),
        "source": "Страницы, найденные разведкой 18.09.2026 при разборе строк заявки ЛУКОЙЛ. "
                  "Считает gt/tools/ship_leak.py.",
        "what_it_is": "Строки нашей заявки, опубликованные в открытом доступе третьими лицами "
                      "дословно — с номерами и количествами.",
        "what_we_do_not_know": "Кто выложил и на каком этапе. Заказчик мог опубликовать "
                               "спецификацию сам, наш запрос мог уйти дальше по цепочке, третья "
                               "сторона могла получить тот же запрос от другого участника. "
                               "Различить это открытыми данными нельзя, поэтому обвинения здесь "
                               "нет: для него нет измерения.",
        "why_it_matters_twice": "Первое — конфиденциальность запроса. Второе — такая страница "
                                "ломает проверку источника: выглядит независимым подтверждением "
                                "номера, а является отражением нашего же текста. Оба домена "
                                "внесены в список круговых источников приёмника разведки.",
        "sources": srcs,
        "rows_cited_total": sum(s["rows_matched_in_our_summary"] for s in srcs),
        "what_to_do": "Решение владельца, не сорсера: нужно ли требовать снятия страниц и менять "
                      "ли порядок рассылки запросов (дробить перечень, убирать количества, "
                      "подписывать соглашение о неразглашении до отправки).",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()
    doc = build()
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"страниц с нашей заявкой в открытом доступе: {len(doc['sources'])}")
    for s in doc["sources"]:
        print(f"  {s['holder']}")
        print(f"    {s['url']}")
        print(f"    опубликовано: {s['what_is_published']}")
        print(f"    сверено с нашей сводкой: {s['rows_matched_in_our_summary']} строк "
              f"на {s['usd_on_matched']:,.0f} USD".replace(",", " "))
    if a.print:
        for s in doc["sources"]:
            for q in s["quoted_verbatim"]:
                print(f"  дословно со страницы: {q}")
    print(f"\n{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
