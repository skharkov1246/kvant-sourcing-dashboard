#!/usr/bin/env python3
"""Дефекты из текстов технических заданий: знание, которое разборщик выбрасывал.

ЗАЧЕМ. Ворота спецификации (library/docfilter.py) относят тендерные документы к
«текст без спецификации» и не берут из них ни строки — правильно для спроса и
неправильно для инженерного портала: именно в этих текстах написано, что
прогорает, трескается, вибрирует и какой ресурс выработал узел. Звено «дефект»
в цепочке портала закрыто шестнадцатью записями из разведки; настоящий объём
лежит здесь.

ЧТО ДЕЛАЕТ. Берёт из lib_files файлы с текстом, скачивает, режет на предложения
и оставляет те, где есть И слово дефекта, И указание на узел или машину
(library/defects.py). Повторы склеиваются по ключу без цифр и пунктуации, и у
записи растёт счётчик встречаемости: типовое требование из сотни ТЗ и единичная
находка — разные вещи, и в таблице это должно быть видно.

ВОЗОБНОВЛЯЕМОСТЬ. Отметка lib_files.defects_at: повторный запуск пропускает уже
обработанное. Части — как в indexer.py.

    SUPABASE_DB_URL=... BITRIX_WEBHOOK_URL=... python library/harvest_defects.py
    SUPABASE_DB_URL=... BITRIX_WEBHOOK_URL=... APPLY=1 python library/harvest_defects.py

В журнал идут только агрегаты: ни одного предложения, ни одного имени файла.
"""
from __future__ import annotations

import hashlib
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import defects as df  # noqa: E402  (после sys.path)
import equipment as eq  # noqa: E402
import indexer  # noqa: E402

APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
SHARDS = int(os.environ.get("SHARDS", "1"))
SHARD = int(os.environ.get("SHARD", "0"))
WORKERS = int(os.environ.get("WORKERS", "8"))
LIMIT = int(os.environ.get("LIMIT", "0"))
DAYS = int(os.environ.get("DAYS", "400"))
# Сколько дефектных предложений брать из одного файла. Ограничение против
# документа, где список требований на сорок страниц: он один перекосил бы всю
# встречаемость.
PER_FILE = int(os.environ.get("PER_FILE", "60"))

# Кандидаты: текстовый слой есть. Документы закупки — в первую очередь, но и
# разобранные спецификации часто несут вводную часть с описанием состояния узла.
CANDIDATES = """
select file_id from lib_files
 where defects_at is null
   and coalesce(chars, 0) > 400
   and status in ('текст без спецификации', 'разобран')"""


def num(v, w=10):
    return f"{v:,}".replace(",", " ").rjust(w)


def text_of(blob: bytes, kind: str) -> str:
    """Весь текст файла — без ворот спецификации: здесь ищется как раз проза.

    zip-контейнер это и .docx, и .xlsx; различать их по имени нельзя (имя не
    всегда честное), поэтому пробуем оба разбора и берём тот, что дал текст.
    «Прочее» по сигнатуре — чаще всего обычный текстовый файл; принимаем его,
    только если он действительно читается как текст, а не как двоичный мусор."""
    try:
        if kind == "pdf":
            return indexer.text_from_pdf(blob)
        if kind == "xlsx/docx":
            t = indexer.text_from_docx(blob)
            if t.strip():
                return t
            return "\n".join(" ".join(c for c in row if c)
                              for row in indexer.rows_from_xlsx(blob))
        if kind == "прочее":
            t = blob.decode("utf-8", "ignore")
            проба = t[:2000]
            печатных = sum(c.isprintable() or c.isspace() for c in проба)
            return t if проба and печатных > 0.9 * len(проба) else ""
    except Exception:
        return ""
    return ""


def harvest(ref: dict) -> tuple[str, str, list[dict]]:
    fo = ref["fo"]
    fid = str(fo.get("id") or fo.get("ID"))
    blob = indexer.download(fo)
    if not blob:
        return fid, "не скачался", []
    kind = indexer.sniff(blob)
    text = text_of(blob, kind)
    if not text.strip():
        return fid, "без текста", []
    найдено = []
    for s in df.sentences(text):
        виды, есть_узел = df.defect_of(s)
        if not (виды and есть_узел):
            continue
        найдено.append({"key": df.key_of(s), "text": s, "terms": виды,
                        "unit": eq.unit_of(s), "deal": ref["deal"], "file": fid})
        if len(найдено) >= PER_FILE:
            break
    return fid, ("есть дефекты" if найдено else "чисто"), найдено


def main() -> int:
    for var in ("BITRIX_WEBHOOK_URL", "SUPABASE_DB_URL"):
        if not os.environ.get(var):
            print(f"нет переменной {var}", file=sys.stderr)
            return 2
    import psycopg2
    import psycopg2.extras

    if SHARDS > 1:
        print(f"часть {SHARD + 1} из {SHARDS}", flush=True)
    print(f"режим: {'ЗАПИСЬ В БАЗУ' if APPLY else 'холостой, без записи'} · "
          f"правило {df.RULE_VERSION}", flush=True)

    conn = indexer.connect()
    with conn.cursor() as cur:
        cur.execute(CANDIDATES)
        want = {r[0] for r in cur.fetchall()}
    conn.close()
    print(f"кандидатов: {len(want)}", flush=True)
    if not want:
        print("нечего разбирать")
        return 0

    refs = indexer.collect_refs(DAYS)
    mine = [r for r in refs
            if str(r["fo"].get("id") or r["fo"].get("ID")) in want
            and int(hashlib.sha1(str(r["fo"].get("id") or r["fo"].get("ID")).encode()).hexdigest(), 16)
            % SHARDS == SHARD]
    if LIMIT:
        mine = mine[:LIMIT]
    print(f"в этой части: {len(mine)}\n", flush=True)
    if not mine:
        print("нечего делать")
        return 0

    сводка: dict[str, dict] = {}
    стат: Counter = Counter()
    виды: Counter = Counter()
    узлы: Counter = Counter()
    готовые: list[str] = []
    всего_предложений = 0

    def запиши() -> None:
        """Пишем разом в конце части: записей десятки тысяч, но они склеены по
        ключу, и объём на порядок меньше числа предложений."""
        if not APPLY or not сводка:
            return
        c = indexer.connect()
        with c.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                insert into lib_defects (id, name, unit_id, model, consequence, source,
                                         seen, terms, deal_id, source_file)
                values %s
                on conflict (id) do update set
                  seen = lib_defects.seen + excluded.seen, updated_at = now()""",
                [(d["id"], indexer.pg(d["text"])[:300], d["unit"], None,
                  indexer.pg(d["text"])[:4000], "извлечено из текста ТЗ", d["seen"],
                  d["terms"], d["deal"], d["file"]) for d in сводка.values()],
                page_size=500)
            if готовые:
                cur.execute("update lib_files set defects_at = now() "
                            "where file_id = any(%s)", (готовые,))
        c.commit()
        c.close()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, (fid, состояние, найдено) in enumerate(pool.map(harvest, mine), 1):
            стат[состояние] += 1
            готовые.append(fid)
            всего_предложений += len(найдено)
            for d in найдено:
                ключ = hashlib.sha1(d["key"].encode()).hexdigest()[:16]
                рек = сводка.get(ключ)
                if рек is None:
                    сводка[ключ] = dict(d, id=ключ, seen=1)
                else:
                    рек["seen"] += 1
                    if рек["unit"] is None and d["unit"]:
                        рек["unit"] = d["unit"]
                for t in d["terms"]:
                    виды[t] += 1
                узлы[d["unit"] or "—"] += 1
            if n % 100 == 0:
                print(f"  обработано {n} из {len(mine)} · предложений {всего_предложений} ·"
                      f" различных {len(сводка)}", flush=True)
    запиши()

    print("\n=== ИТОГ ЧАСТИ ===")
    print(f"файлов:            {num(len(mine))}")
    print(f"по состоянию: {dict(стат.most_common())}")
    print(f"дефектных предложений:{num(всего_предложений)}")
    print(f"различных после склейки:{num(len(сводка))}")
    if сводка:
        повторы = sum(1 for d in сводка.values() if d["seen"] > 1)
        print(f"встречается больше одного раза: {повторы} "
              f"({повторы / len(сводка) * 100:.0f}%) — это типовые требования")
    print("\nпо видам дефектов:")
    for t, n in виды.most_common(12):
        print(f"    {t:24}{num(n)}")
    print("по узлам:")
    for u, n in узлы.most_common(8):
        print(f"    {u:24}{num(n)}")
    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
