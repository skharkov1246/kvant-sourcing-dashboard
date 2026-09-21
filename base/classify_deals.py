#!/usr/bin/env python3
"""Сегмент, марка и предмет сделки — по названию и по тому, что в ней запрашивали.

Зачем. В базе три поля сделки пустые у всех 8 856 записей: seg, brand, item.
Из-за этого не работает ни один разрез по оборудованию: справочник артикулов
показывает пустой сегмент, дашборд не может сказать «сколько мы теряем в
насосах против компрессоров», а досье сделки не знает, к какому рынку она
относится. Сегменты в портале заведены (таблица segments, 11 кодов с
маркерами), но проставлять их было нечем.

Модуль считает по каждой сделке: название карточки, наименования позиций,
марки из документов — и выбирает сегмент по числу совпавших маркеров.
Название весит втрое: в нём предмет закупки написан прямо, а в позициях —
вперемешку с крепежом и прокладками, которые есть в любой спецификации.

    python base/classify_deals.py --db base/kvant.db
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

TITLE_WEIGHT = 3        # маркер в названии сделки весомее, чем в позициях
MIN_SCORE = 2           # ниже — «прочее»: одно случайное слово сегментом не делает
MAX_NAMES = 60          # столько наименований с сделки хватает, дальше повторы

# Мусор в начале названия: номер лота, код площадки, порядковый номер.
LEAD_JUNK = re.compile(r"^[\s№#]*(\d[\d/._-]*|[A-ZА-Я]{1,3}-?\d+[/\d]*)[\s.,;:)-]+", re.I)
VERB = re.compile(r"^(закупка|поставка|запрос|заявка|покупка|приобретение|тендер|лот|"
                  r"предложение|расценк\w*)\s+(на\s+|для\s+)?", re.I)
TAIL = re.compile(r"\s*[(\[][^)\]]*[)\]]\s*$")


def subject(title: str) -> str:
    """Предмет сделки из названия карточки.

    Названия заводят по-разному: «638861 Поставка выключателей поплавковых
    Grundfos 96003332 по лоту 327944», «НН-833 Запрос расценки». Снимаем
    номер в начале, служебный глагол и хвост в скобках — остаётся то, что
    закупают."""
    s = re.sub(r"\s+", " ", title or "").strip()
    for _ in range(3):
        s2 = LEAD_JUNK.sub("", s)
        s2 = VERB.sub("", s2)
        if s2 == s:
            break
        s = s2
    s = TAIL.sub("", s).strip(" .,-—/")
    return s[:120]


def load_segments(con: sqlite3.Connection) -> list[tuple[str, list[str]]]:
    out = []
    for code, markers in con.execute("SELECT code, markers FROM segments"):
        try:
            words = json.loads(markers) if markers else []
        except json.JSONDecodeError:
            words = []
        # маркеры короче четырёх знаков ищем как отдельное слово: «СДО» иначе
        # находится внутри «средство», а «ГОК» — внутри «ГОКа» и «глюкозы»
        out.append((code, [w.lower() for w in words if len(w) >= 2]))
    return out


def score(text: str, markers: list[str]) -> int:
    n = 0
    for w in markers:
        if len(w) <= 3:
            if re.search(rf"(?<![а-яa-z0-9]){re.escape(w)}(?![а-яa-z0-9])", text):
                n += 1
        elif w in text:
            n += 1
    return n


def run(db_path: str) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    segs = load_segments(con)
    if not segs:
        raise SystemExit("таблица segments пуста: сегменты задаются там")

    names: dict[int, list[str]] = defaultdict(list)
    for did, nm, oem in con.execute("""SELECT deal_id, name, manufacturer FROM positions
                                       WHERE deal_id IS NOT NULL"""):
        if len(names[did]) >= MAX_NAMES:
            continue
        names[did].append(f"{nm or ''} {oem or ''}")
    brands: dict[int, Counter] = defaultdict(Counter)
    for did, oem in con.execute("""SELECT deal_id, manufacturer FROM positions
                                   WHERE manufacturer IS NOT NULL AND deal_id IS NOT NULL"""):
        brands[did][oem] += 1

    rows = []
    stat: Counter = Counter()
    for did, title in con.execute("SELECT id, title FROM deals"):
        head = (title or "").lower()
        body = " ".join(names.get(did, [])).lower()
        best, best_n = "other", 0
        for code, markers in segs:
            if code == "other":
                continue
            n = score(head, markers) * TITLE_WEIGHT + score(body, markers)
            if n > best_n:
                best, best_n = code, n
        seg = best if best_n >= MIN_SCORE else "other"
        brand = brands[did].most_common(1)[0][0] if brands.get(did) else None
        rows.append((seg, brand, subject(title or ""), did))
        stat[seg] += 1
    con.executemany("UPDATE deals SET seg=?, brand=?, item=? WHERE id=?", rows)
    # сегмент сделки переносим в позиции: по нему потом режут справочник
    con.execute("UPDATE positions SET seg=(SELECT seg FROM deals WHERE deals.id=positions.deal_id)")
    con.commit()

    named = con.execute("SELECT count(*) FROM deals WHERE seg<>'other'").fetchone()[0]
    withbrand = con.execute("SELECT count(*) FROM deals WHERE brand IS NOT NULL").fetchone()[0]
    print(f"сегмент определён у {named} сделок из {sum(stat.values())}, марка — у {withbrand}", flush=True)
    for code, n in stat.most_common():
        name = con.execute("SELECT name FROM segments WHERE code=?", (code,)).fetchone()
        print(f"  {(name[0] if name else code)[:56]:58}{n}", flush=True)
    con.close()
    return {"segments": dict(stat), "named": named, "brands": withbrand}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    a = ap.parse_args()
    run(a.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
