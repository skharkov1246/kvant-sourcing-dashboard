#!/usr/bin/env python3
"""Связать накопленные статьи с узлом и машиной: 18 тысяч статей знают только сегмент.

ЗАЧЕМ. В lib_knowledge лежат статьи разведки — устройство, подбор, отказы,
аналоги, рынок. У каждой есть сегмент («ГТУ»), но спрашивают не так: спрашивают
«что мы знаем про горячий тракт SGT-400». Пока узла и машины у статьи нет,
библиотека отвечает только целыми направлениями.

ЧЕМ СТАВИТСЯ СВЯЗЬ. Теми же правилами, что размечают позиции каталога
(library/equipment.py): узел — по тексту, машина — по написаниям из справочника
машин. Правило одно на оба места: развести их — значит получить статью в одном
узле и деталь в другом при одинаковом тексте.

МАШИНА ИЩЕТСЯ ЦЕЛЫМ СЛОВОМ. «ST14» подстрокой найдётся внутри «ST1400», а
«Mars» — внутри «Marshall». Поэтому написания собираются в один регулярный
шаблон с границами слова, а не проверяются вхождением.

БЕЗ APPLY=1 идёт вхолостую: считает охват и печатает, ничего не пишет.

    SUPABASE_DB_URL=... python library/link_knowledge.py
    SUPABASE_DB_URL=... APPLY=1 python library/link_knowledge.py

В журнал идут только агрегаты: ни одного заголовка статьи.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import equipment as eq  # noqa: E402  (после sys.path)

APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
# Сколько знаков тела статьи смотреть. Начало статьи — про предмет; к концу идут
# ссылки и оговорки, и по ним машина находится ложно.
BODY = int(os.environ.get("BODY_CHARS", "1200"))
BATCH = 2000


def num(v, w=9):
    return f"{v:,}".replace(",", " ").rjust(w)


def машинный_шаблон(машины: list[tuple[str, list[str]]]):
    """Один шаблон на все написания: по длинным сначала, чтобы «SGT-400» не
    съедалось «SGT». Возвращает список (шаблон, ключ машины)."""
    пары = []
    for key, написания in машины:
        for w in написания:
            w = (w or "").strip()
            if len(w) < 4:            # «ST8» найдётся где угодно, пропускаем
                continue
            пары.append((w, key))
    пары.sort(key=lambda p: -len(p[0]))
    return [(re.compile(rf"(?<![0-9A-Za-zА-Яа-я]){re.escape(w)}(?![0-9A-Za-zА-Яа-я])",
                        re.I), key) for w, key in пары]


def main() -> int:
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url, connect_timeout=20, options="-c statement_timeout=900000")
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("select id, coalesce(aliases, array[]::text[]), name from lib_models")
        машины = [(r[0], list(r[1]) + [r[2]]) for r in cur.fetchall()]
        шаблоны = машинный_шаблон(машины)
        cur.execute("select count(*) from lib_knowledge")
        всего = cur.fetchone()[0]
        print(f"статей в базе: {num(всего)} · машин в справочнике: {len(машины)} · "
              f"написаний в поиске: {len(шаблоны)}", flush=True)
        if not всего:
            print("статей нет — нечего связывать")
            conn.close()
            return 0

        cur.execute("select id, title, left(body, %s) from lib_knowledge order by id", (BODY,))
        статьи = cur.fetchall()

    узлы: Counter = Counter()
    машины_счёт: Counter = Counter()
    правила: Counter = Counter()
    обновления = []
    for sid, title, body in статьи:
        текст = f"{title or ''} {body or ''}"
        unit = eq.unit_of(title or "") or eq.unit_of(текст)
        rule_unit = "заголовок" if eq.unit_of(title or "") else ("текст" if unit else None)
        model = None
        for rx, key in шаблоны:
            if rx.search(текст):
                model = key
                break
        узлы[unit or "—"] += 1
        машины_счёт[model or "—"] += 1
        правила[f"{'узел' if unit else '—'}/{'машина' if model else '—'}"] += 1
        if unit or model:
            обновления.append((sid, unit, model, rule_unit))

    с_узлом = всего - узлы["—"]
    с_машиной = всего - машины_счёт["—"]
    print(f"\nузел определён:   {num(с_узлом)}   {с_узлом / всего * 100:.1f}%")
    print(f"машина определена:{num(с_машиной)}   {с_машиной / всего * 100:.1f}%")
    print(f"по сочетаниям: {dict(правила.most_common())}")
    print("\nтоп узлов:")
    for u, n in узлы.most_common(9):
        if u != "—":
            print(f"    {u:34}{num(n)}")
    print("топ машин:")
    for m, n in машины_счёт.most_common(9):
        if m != "—":
            print(f"    {m:34}{num(n)}")

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        conn.close()
        return 0

    with conn.cursor() as cur:
        for i in range(0, len(обновления), BATCH):
            psycopg2.extras.execute_values(cur, """
                update lib_knowledge k set unit_id = v.unit_id, model_id = v.model_id,
                       link_rule = v.rule, updated_at = now()
                  from (values %s) as v(id, unit_id, model_id, rule)
                 -- Приведение обязательно: в values-списке тип выводится как text,
                 -- а k.id — bigint, и сравнение без каста падает.
                 where k.id = v.id::bigint""", обновления[i:i + BATCH],
                template="(%s, %s, %s, %s)", page_size=500)
            conn.commit()
            print(f"  записано {min(i + BATCH, len(обновления))} из {len(обновления)}",
                  flush=True)
    conn.close()
    print("\n✓ статьи связаны с узлом и машиной")
    return 0


if __name__ == "__main__":
    sys.exit(main())
