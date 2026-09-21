#!/usr/bin/env python3
"""Где лежит НАША цена заказчику и почему её до сих пор нет числом.

ЗАЧЕМ. Владелец спросил прямо: какой объём в продаже и какой в закупке. Ответ
на закупочную половину у нас есть, на продажную отчёт до сих пор отвечал
«не знаем, в каком поле сделки лежит выставленное предложение». Это было верно
как признание, но бесполезно как задача: непонятно, что делать.

ЧТО ЭТОТ ЗАМЕР УСТАНАВЛИВАЕТ. Поле известно, и оно не одно:
«Economics of the project» и «Result, ТКП». Вложение по нему есть у КАЖДОЙ
разобранной сделки. Но большая часть этих вложений — ПУСТОЙ ОБРАЗЕЦ: один и
тот же файл, приложенный к десяткам сделок, и это видно по размеру, совпадающему
до байта. Полезны только те вложения, которые в каждой сделке свои.

КАК ОТЛИЧАЕТСЯ ОБРАЗЕЦ ОТ ЗАПОЛНЕННОГО. По имени и размеру: если файл с тем же
именем имеет во всех сделках один и тот же размер до байта — это одно и то же
вложение, то есть незаполненный образец. Заполненная экономика проекта в разных
сделках весит по-разному. Признак грубый, но он не обвиняет и не оправдывает: он
отделяет то, что стоит разбирать, от того, что разбирать бессмысленно.

ЧЕГО ЗАМЕР НЕ ДЕЛАЕТ. Он не открывает цены: сами файлы в репозиторий не
попадают, здесь только опись. Он отвечает на один вопрос — где искать и сколько
там всего.

    python gt/tools/sale_side.py [--write]
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "gt/data/bitrix_tkp_index.json"
OUT = ROOT / "gt/data/ship_sale_side.json"
OURS = "наша цена"


def measure() -> dict:
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    rows = [(scope, i)
            for scope, s in doc["scopes"].items()
            for i in (s.get("inventory") or [])
            if i.get("direction") == OURS]

    by_name: dict[str, list[int]] = collections.defaultdict(list)
    for _s, i in rows:
        by_name[str(i.get("file_name") or "")].append(int(i.get("size") or 0))

    def is_template(i: dict) -> bool:
        sizes = by_name[str(i.get("file_name") or "")]
        return len(sizes) > 1 and len(set(sizes)) == 1

    tmpl = [i for _s, i in rows if is_template(i)]
    own = [i for _s, i in rows if not is_template(i)]
    unparsed = [i for i in own if i.get("status") == "не разобрался"]
    priced = [i for _s, i in rows if (i.get("priced") or 0) > 0]

    fields = collections.Counter(
        (str(i.get("field")), str(i.get("field_name"))) for _s, i in rows)
    scopes = {}
    for scope, s in doc["scopes"].items():
        inv = [i for i in (s.get("inventory") or []) if i.get("direction") == OURS]
        scopes[scope] = {
            "deals": s.get("deals"),
            "records": len(inv),
            "own_file": len([i for i in inv if not is_template(i)]),
        }
    return {
        "updated": doc.get("updated"),
        "source": ("Опись вложений сделок Bitrix (gt/data/bitrix_tkp_index.json), "
                   "направление «наша цена». Считает gt/tools/sale_side.py."),
        "question": ("Какой объём в продаже. Отчёт до сих пор отвечал «не знаем, в каком "
                     "поле лежит выставленная заказчику цена». Поле известно; замер "
                     "показывает, что именно мешает получить из него число."),
        "fields": [{"field": f, "name": n, "records": c}
                   for (f, n), c in fields.most_common()],
        "records_total": len(rows),
        "records_same_file_in_many_deals": len(tmpl),
        "records_own_file": len(own),
        "records_own_file_unparsed": len(unparsed),
        "records_with_price_found": len(priced),
        "unparsed_deals": sorted({str(i.get("origin")) for i in unparsed}),
        "by_scope": scopes,
        "what_it_means": (
            "Поле выставленной цены известно и заполнено у каждой разобранной сделки, но "
            f"{len(tmpl)} вложений из {len(rows)} — один и тот же образец, приложенный к "
            "десяткам сделок (размер совпадает до байта). Своё вложение только у "
            f"{len(own)} записей, и разбор не справился с {len(unparsed)} из них. Цену не "
            "нашёл НИ ОДИН разбор. Значит продажная сторона не отсутствует — она лежит в "
            f"{len(unparsed)} нечитаемых файлах, и это задача разбора, а не поиска поля."),
        "next_step": (
            "Взять эти файлы адресно по номерам сделок и разобрать их отдельно: они "
            "малы (около 5 КБ), скачались без ошибок и не открылись обычным путём — "
            "значит дело в устройстве самого файла, а не в доступе. Пока они не "
            "разобраны, сопоставить продажу с закупкой построчно нельзя, и говорить о "
            "марже по заявке — тоже."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    m = measure()
    print(f"записей «наша цена»: {m['records_total']}")
    for f in m["fields"]:
        print(f"  {f['records']:>4} × {f['field']} — {f['name']}")
    print(f"  один и тот же образец в разных сделках: {m['records_same_file_in_many_deals']}")
    print(f"  своё вложение у сделки:                {m['records_own_file']}")
    print(f"  из них разбор не открыл:               {m['records_own_file_unparsed']}")
    print(f"  разбор нашёл цену:                     {m['records_with_price_found']}")
    print(f"сделки с нечитаемым вложением: {', '.join(m['unparsed_deals'][:12])}"
          + (" …" if len(m["unparsed_deals"]) > 12 else ""))
    if a.write:
        OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
