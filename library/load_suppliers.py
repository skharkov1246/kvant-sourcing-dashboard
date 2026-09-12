#!/usr/bin/env python3
"""Исполнители: перенос накопленных поставщиков в общую таблицу lib_suppliers.

ЗАЧЕМ. Звено «исполнитель» в цепочке портала (CLAUDE.md, «Куда мы идём») пусто:
в lib_suppliers ноль строк. При этом поставщики уже собраны — семью разными
исследованиями, в семи разных файлах, с семью разными наборами полей. Пока они
лежат порознь, ответить «кто чинит этот узел» одним запросом нельзя.

ЧТО ДЕЛАЕТ. Читает файлы репозитория, приводит к одной форме, определяет сегмент
по тому, что поставщик делает, и складывает в lib_suppliers. Источник каждой
строки сохраняется в researched_by — без него нельзя будет понять, откуда взялось
противоречие, когда два исследования разойдутся в оценке одной компании.

ДУБЛИ. Одна компания встречается в нескольких файлах под разными написаниями
(«ООО Ромашка», «Ромашка, ООО», «Romashka LLC»). Ключом служит нормализованное
имя: без кавычек, форм собственности, регистра и пунктуации. При совпадении
выигрывает запись с большей полнотой — у неё больше заполненных полей, — а
источники складываются, чтобы обе ссылки остались.

БЕЗ APPLY=1 идёт вхолостую: считает и печатает, ничего не пишет.

    python library/load_suppliers.py                       # что получится
    SUPABASE_DB_URL=... APPLY=1 python library/load_suppliers.py

В журнал идут только агрегаты: ни одного названия компании, ни одного контакта.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from segments import classify, name_of  # noqa: E402  (после sys.path)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")

# Формы собственности и шум, которые не различают компании.
FORMS = ("ооо", "оао", "зао", "пао", "ао", "ип", "нпо", "нпп", "пкф", "тд", "гк",
         "llc", "ltd", "inc", "co", "corp", "corporation", "gmbh", "srl", "sa",
         "bv", "ag", "spa", "plc", "pte", "sdn", "bhd", "jsc", "cjsc", "oao")
PUNCT = re.compile(r"[^0-9a-zа-яё]+")


def norm(name: str) -> str:
    """Ключ компании: без кавычек, форм собственности, регистра и пунктуации."""
    t = PUNCT.sub(" ", (name or "").lower().replace("ё", "е")).strip()
    words = [w for w in t.split() if w not in FORMS]
    return " ".join(words)


def first(d: dict, *keys) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list) and v:
            return ", ".join(str(x) for x in v if x)[:400]
    return ""


def load(path: str, key: str | None):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return []
    d = json.load(open(full, encoding="utf-8"))
    rows = d if isinstance(d, list) else (d.get(key) if key else d)
    if isinstance(rows, dict):                      # досье: ключ — имя компании
        return [dict(v, name=k) for k, v in rows.items() if isinstance(v, dict)]
    return [r for r in (rows or []) if isinstance(r, dict)]


# Что читаем и как называется поле с именем компании в каждом файле.
SOURCES = [
    ("zip/data/odm_suppliers.json", None, "производство ЗИП"),
    ("gt/data/ship_sellers.json", "rows", "адресаты заявки ЛУКОЙЛ"),
    ("gt/data/ship_sweep.json", "rows", "продавцы по сплошной проверке"),
    ("gt/data/ship_energoseti.json", "rows", "продавцы по заявке Энергосети"),
    ("gt/data/sgt400_checklist.json", "rows", "чек-лист SGT-400"),
    ("gt/data/solar.json", "companies", "разведка Solar"),
    ("gt/data/lm6000.json", "companies", "разведка LM6000"),
    ("gt/data/ms6001b.json", "companies", "разведка Frame 6B"),
    ("gt/data/sgt4000f.json", "companies", "разведка SGT5-4000F"),
    ("gt/data/v643a.json", "companies", "разведка V64.3A"),
    ("gt/data/plugs_world.json", "addressees", "производители свечей"),
    ("gt/data/removed_ru.json", "items", "ушедшие из РФ"),
    ("gt/data/suppliers.json", None, "профили поставщиков ГТУ"),
    ("zip/data/tfs_supply_chain.json", "suppliers", "цепочка поставок ТФС"),
    ("zip/data/material_process.json", None, "обработка материалов"),
    ("gt/data/research_suppliers.json", "rows", "исследование ГТУ"),
    ("gt/data/dossiers.json", "dossiers", "досье компаний"),
    ("zip/data/material_suppliers.json", None, "материалы"),
    ("gt/data/rfq_suppliers.json", "rows", "адресаты запросов ГТУ"),
    ("gt/data/heavy_suppliers.json", "rows", "тяжёлое машиностроение"),
    ("zip/data/bearing_sites_ru.json", "sites", "подшипники РФ"),
    ("zip/data/bearing_sites_cn.json", "sites", "подшипники КНР"),
    ("zip/data/telsmith_suppliers.json", "suppliers", "дробильное оборудование"),
    ("gt/data/tfs_subsuppliers.json", "rows", "субпоставщики ГТУ"),
]


def shape(r: dict, origin: str) -> dict | None:
    """Одна форма из разных наборов полей."""
    name = first(r, "name", "company", "title", "seller", "n")
    if not name or len(name) < 2:
        return None
    what = first(r, "what", "products", "makes", "capability", "production", "real_maker",
                 "equipment", "covers_classes", "covers", "hook", "profile", "angle",
                 "families", "note")
    return {
        "name": name[:300],
        "key": norm(name),
        "country": first(r, "country", "seller_country")[:80] or None,
        "city": first(r, "city")[:120] or None,
        "kind": first(r, "kind", "role", "tier", "relation", "category")[:60] or None,
        "site": first(r, "site", "url", "link", "catalog_url", "seller_url",
                      "rfq_url")[:300] or None,
        "strengths": what[:1000] or None,
        "moq": first(r, "moq")[:120] or None,
        "certificates": first(r, "qc", "certificates", "certs")[:300] or None,
        "sanctions": first(r, "risk", "risks", "sanctions", "ru_access")[:300] or None,
        "confidence": (first(r, "confidence", "conf", "relevance") or "med")[:10],
        "contact_email": first(r, "email", "emails", "contact_email")[:200] or None,
        "contact_phone": first(r, "phone", "phones", "contact_phone", "whatsapp")[:120] or None,
        "segment_id": classify(" ".join(x for x in (what, name) if x)),
        "researched_by": origin,
    }


def fullness(d: dict) -> int:
    return sum(1 for k, v in d.items() if v and k not in ("key", "researched_by"))


def main() -> int:
    merged: dict[str, dict] = {}
    per_source: Counter = Counter()
    dupes = 0
    for path, key, origin in SOURCES:
        rows = load(path, key)
        per_source[origin] = len(rows)
        for r in rows:
            s = shape(r, origin)
            if not s:
                continue
            old = merged.get(s["key"])
            if old is None:
                merged[s["key"]] = s
                continue
            dupes += 1
            # Выигрывает более полная запись; источники складываются, чтобы обе
            # ссылки остались и расхождение оценок было прослеживаемо.
            winner, loser = (s, old) if fullness(s) > fullness(old) else (old, s)
            sources = {*winner["researched_by"].split(" · "), *loser["researched_by"].split(" · ")}
            for k, v in loser.items():
                if not winner.get(k) and v:
                    winner[k] = v
            winner["researched_by"] = " · ".join(sorted(sources))
            merged[s["key"]] = winner

    rows = list(merged.values())
    print("=== источники ===")
    for origin, n in per_source.most_common():
        print(f"  {origin:28}{n:>8}")
    print(f"\nвсего прочитано: {sum(per_source.values())} · после склейки дублей: {len(rows)} "
          f"(схлопнуто {dupes})")

    seg = Counter(r["segment_id"] or "—" for r in rows)
    print("\n=== по сегментам ===")
    for sid, n in seg.most_common():
        print(f"  {name_of(None if sid == '—' else sid):34}{n:>8}")
    заполнено = Counter()
    for r in rows:
        for k in ("country", "city", "site", "strengths", "contact_email", "contact_phone",
                  "kind", "moq", "certificates"):
            заполнено[k] += bool(r.get(k))
    print("\n=== полнота полей ===")
    for k, n in заполнено.most_common():
        print(f"  {k:20}{n:>8}   {n / len(rows) * 100:>5.1f}%")

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        return 0

    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url, connect_timeout=20, options="-c statement_timeout=900000")
    conn.autocommit = False
    with conn.cursor() as cur:
        # Справочник сегментов должен существовать до записи: segment_id ссылается
        # на lib_segments, и при пустом справочнике всё падает по внешнему ключу.
        import indexer
        indexer.ensure_segments(cur)
        psycopg2.extras.execute_values(cur, """
            insert into lib_suppliers
              (segment_id, name, name_key, country, city, kind, site, strengths, moq,
               certificates, sanctions, confidence, contact_email, contact_phone, researched_by)
            values %s
            on conflict do nothing""",
            [(r["segment_id"], r["name"], r["key"], r["country"], r["city"], r["kind"],
              r["site"], r["strengths"], r["moq"], r["certificates"], r["sanctions"],
              r["confidence"], r["contact_email"], r["contact_phone"], r["researched_by"])
             for r in rows], page_size=500)
        conn.commit()
        cur.execute("select count(*) from lib_suppliers")
        print(f"\n✓ в lib_suppliers теперь строк: {cur.fetchone()[0]}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
