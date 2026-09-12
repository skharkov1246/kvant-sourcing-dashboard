#!/usr/bin/env python3
"""Распознавание сканов: спрос из файлов, у которых нет текстового слоя.

ЗАЧЕМ. На 12.09.2026 из 24 305 разобранных вложений 4 059 — изображения, а 8 195
помечены «пусто». Это фотографии и сканы спецификаций: текста в них нет, значит
разборщик не извлёк ни одной позиции. По отдаче остальных файлов (в среднем 97
позиций на разобранный) здесь лежит порядка сотни тысяч позиций спроса, которых
в базе нет вовсе. Это самая крупная неохваченная часть корпуса.

ЧТО ДЕЛАЕТ. Берёт из lib_files файлы без текста, скачивает заново из Битрикса,
прогоняет tesseract (rus+eng), полученный текст пропускает через ТЕ ЖЕ ворота
спецификации, что и обычный разбор (library/docfilter.py), и пишет позиции в
lib_demand с источником «распознавание скана».

ПОЧЕМУ ОТДЕЛЬНЫЙ ИСТОЧНИК. Качество распознавания ниже разбора: латиница в
кириллическом контексте путается («DIN» читается как «ОТМ»), единицы теряют
букву. Такие позиции должны быть отличимы в любой выборке, иначе ошибка
распознавания станет неотличима от ошибки поставщика.

ВОЗОБНОВЛЯЕМОСТЬ. Отметка в lib_files.ocr_at: повторный запуск пропускает уже
распознанное. Работа делится на части так же, как в indexer.py.

    SUPABASE_DB_URL=... BITRIX_WEBHOOK_URL=... python library/ocr.py          # вхолостую
    SUPABASE_DB_URL=... BITRIX_WEBHOOK_URL=... APPLY=1 python library/ocr.py  # с записью

В журнал идут только агрегаты: ни распознанного текста, ни имён файлов.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import docfilter  # noqa: E402  (после sys.path)
import indexer  # noqa: E402
from segments import classify, name_of  # noqa: E402

APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
SHARDS = int(os.environ.get("SHARDS", "1"))
SHARD = int(os.environ.get("SHARD", "0"))
WORKERS = int(os.environ.get("WORKERS", "4"))       # tesseract упирается в процессор
LIMIT = int(os.environ.get("LIMIT", "0"))
DAYS = int(os.environ.get("DAYS", "400"))
PAGES = int(os.environ.get("PAGES", "12"))          # страниц PDF на файл
DPI = int(os.environ.get("DPI", "200"))
LANG = os.environ.get("OCR_LANG", "rus+eng")
TIMEOUT = int(os.environ.get("OCR_TIMEOUT", "120"))

# Кандидаты: текста нет, значит разбор не дал ничего. «Формат не читаем» сюда не
# берём — это архивы и экзотика, распознавать в них нечего.
CANDIDATES = """
select file_id from lib_files
 where ocr_at is null
   and (status = 'пусто' or kind = 'изображение')
   and status <> 'не скачался'"""


def num(v, w=12):
    return f"{v:,}".replace(",", " ").rjust(w)


def ocr_image(path: str) -> str:
    try:
        r = subprocess.run(["tesseract", path, "stdout", "-l", LANG, "--psm", "6"],
                           capture_output=True, timeout=TIMEOUT)
        return r.stdout.decode("utf-8", "ignore")
    except Exception:
        return ""


def ocr_pdf(blob: bytes, tmp: str) -> str:
    """PDF без текстового слоя: разворачиваем страницы в картинки и читаем их."""
    src = os.path.join(tmp, "in.pdf")
    with open(src, "wb") as f:
        f.write(blob)
    try:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-l", str(PAGES), src,
                        os.path.join(tmp, "p")], capture_output=True, timeout=TIMEOUT * 2)
    except Exception:
        return ""
    parts = []
    for name in sorted(os.listdir(tmp)):
        if name.startswith("p") and name.endswith(".png"):
            parts.append(ocr_image(os.path.join(tmp, name)))
    return "\n".join(parts)


def recognise(ref: dict) -> tuple[dict, list[dict]]:
    """Один файл: скачать, распознать, пропустить через ворота спецификации."""
    fo = ref["fo"]
    fid = str(fo.get("id") or fo.get("ID"))
    rec = {"file_id": fid, "deal_id": ref["deal"], "status": None, "chars": 0,
           "rows_found": 0, "segment_id": None, "kind": None, "reason": None}
    blob = indexer.download(fo)
    if not blob:
        rec["status"] = "не скачался"
        return rec, []
    kind = indexer.sniff(blob)
    rec["kind"] = kind
    with tempfile.TemporaryDirectory() as tmp:
        if kind == "pdf":
            text = ocr_pdf(blob, tmp)
        elif kind == "изображение":
            p = os.path.join(tmp, "img")
            with open(p, "wb") as f:
                f.write(blob)
            text = ocr_image(p)
        else:
            rec["status"] = "формат не читаем"
            rec["reason"] = "распознавать нечего"
            return rec, []

    rec["chars"] = len(text)
    if not text.strip():
        rec["status"] = "пусто"
        rec["reason"] = "распознавание не дало текста"
        return rec, []

    # Те же ворота, что и в обычном разборе: правило одно на оба места вызова.
    lines = [ln.strip() for ln in text.splitlines()
             if len(ln.strip()) > 8 and not indexer.NOISE_ROW.match(ln.strip())]
    spec_n = prose_n = 0
    for ln in lines:
        sp, pr = docfilter.row_marks(ln[:300])
        spec_n += bool(sp)
        prose_n += bool(pr and not sp)
    if docfilter.file_verdict(len(lines), spec_n, prose_n) == "документация":
        rec["status"] = "текст без спецификации"
        rec["reason"] = f"распознано строк {len(lines)} · с признаками позиции {spec_n}"
        return rec, []

    items = []
    rec["segment_id"] = classify(text)
    for ln in lines[:2000]:
        own = classify(ln)
        items.append({"item_name": ln[:300], "part_number": docfilter.part_number_of(ln),
                      "oem": "", "unit": "", "qty": None,
                      "segment_id": own or rec["segment_id"],
                      "segment_rule": "строка" if own else ("файл" if rec["segment_id"] else None),
                      "deal_id": ref["deal"], "source_file": fid})
    rec["rows_found"] = len(items)
    rec["status"] = "разобран по скану" if items else "пусто"
    return rec, items


def main() -> int:
    for var in ("BITRIX_WEBHOOK_URL", "SUPABASE_DB_URL"):
        if not os.environ.get(var):
            print(f"нет переменной {var}", file=sys.stderr)
            return 2
    import psycopg2
    import psycopg2.extras

    if SHARDS > 1:
        print(f"часть {SHARD + 1} из {SHARDS}", flush=True)
    print(f"режим: {'ЗАПИСЬ В БАЗУ' if APPLY else 'холостой, без записи'}", flush=True)

    conn = indexer.connect()
    with conn.cursor() as cur:
        cur.execute(CANDIDATES)
        want = {r[0] for r in cur.fetchall()}
    conn.close()
    print(f"кандидатов на распознавание: {len(want)}", flush=True)
    if not want:
        print("нечего распознавать")
        return 0

    refs = indexer.collect_refs(DAYS)
    mine = [r for r in refs
            if str(r["fo"].get("id") or r["fo"].get("ID")) in want
            and int(hashlib.sha1(str(r["fo"].get("id") or r["fo"].get("ID")).encode()).hexdigest(), 16)
            % SHARDS == SHARD]
    if LIMIT:
        mine = mine[:LIMIT]
    print(f"к распознаванию в этой части: {len(mine)}\n", flush=True)
    if not mine:
        print("нечего делать")
        return 0

    stat: Counter = Counter()
    kinds: Counter = Counter()
    segs: Counter = Counter()
    total_items = 0
    buf_files: list[tuple] = []
    buf_items: list[tuple] = []

    def flush() -> None:
        nonlocal buf_files, buf_items
        if not APPLY or (not buf_files and not buf_items):
            buf_files, buf_items = [], []
            return
        c = indexer.connect()
        with c.cursor() as cur:
            if buf_items:
                psycopg2.extras.execute_values(cur, """
                    insert into lib_demand
                      (segment_id, deal_id, item_name, oem, part_number, qty, unit, source,
                       source_file, segment_rule)
                    values %s""", buf_items, page_size=500)
            if buf_files:
                psycopg2.extras.execute_values(cur, """
                    insert into lib_files (file_id, deal_id, status, kind, chars, rows_found,
                                           segment_id, reason, ocr_at, ocr_chars, parser_version)
                    values %s
                    on conflict (file_id) do update set
                      status = excluded.status, kind = excluded.kind, chars = excluded.chars,
                      rows_found = excluded.rows_found, segment_id = excluded.segment_id,
                      reason = excluded.reason, ocr_at = now(), ocr_chars = excluded.ocr_chars,
                      processed_at = now()""", buf_files, page_size=500)
        c.commit()
        c.close()
        buf_files, buf_items = [], []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, (rec, items) in enumerate(pool.map(recognise, mine), 1):
            stat[rec["status"]] += 1
            if rec["kind"]:
                kinds[rec["kind"]] += 1
            total_items += rec["rows_found"]
            for it in items:
                segs[it["segment_id"] or "—"] += 1
            buf_files.append((rec["file_id"], rec["deal_id"], rec["status"], rec["kind"],
                              rec["chars"], rec["rows_found"], rec["segment_id"],
                              indexer.pg(rec["reason"]), None, rec["chars"],
                              indexer.PARSER_VERSION))
            for it in items:
                buf_items.append((it["segment_id"], it["deal_id"], indexer.pg(it["item_name"])[:500],
                                  "", indexer.pg(it["part_number"])[:120], None, "",
                                  "распознавание скана", it["source_file"], it["segment_rule"]))
            if len(buf_files) >= 100 or len(buf_items) >= 3000:
                flush()
            if n % 50 == 0:
                print(f"  распознано {n} из {len(mine)} · позиций {total_items}", flush=True)
    flush()

    print("\n=== ИТОГ ЧАСТИ ===")
    print(f"файлов: {sum(stat.values())} · позиций из сканов: {total_items}")
    print(f"по состоянию: {dict(stat.most_common())}")
    print(f"по формату:   {dict(kinds.most_common())}")
    print("позиции по сегментам:")
    for sid, n in segs.most_common(20):
        print(f"    {name_of(None if sid == '—' else sid):32s} {n:>8d}")
    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
    print("\n✓ распознавание части завершено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
