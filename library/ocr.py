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
# Режим сегментации страницы. Основной — блочный (--psm 6), как и был; если он
# не дал ничего, идёт вторая попытка с автоматической сегментацией (--psm 3):
# на фотографии листа блочный режим иногда молчит. Гипотезу, что именно в нём
# причина 233 пустых файлов из 400, проверка на синтетических картинках НЕ
# подтвердила — все режимы читали их одинаково. Поэтому основной режим не
# меняем, а причину пустоты начинаем записывать (ниже).
PSM_MAIN = os.environ.get("OCR_PSM", "6")
PSM_RETRY = os.environ.get("OCR_PSM_RETRY", "3")
TIMEOUT = int(os.environ.get("OCR_TIMEOUT", "120"))

# Кандидаты: текста нет, значит разбор не дал ничего. Форматы, в которых
# распознавать нечего, отсекаются ЗДЕСЬ, а не в обработчике: в первой части
# прогона 118 файлов из 400 оказались xlsx/docx, архивами и экзотикой —
# скачались, дошли до tesseract и вернули «формат не читаем». Это треть
# впустую потраченного времени части.
CANDIDATES = """
select file_id from lib_files
 where ocr_at is null
   and (status = 'пусто' or kind = 'изображение')
   and status <> 'не скачался'
   and coalesce(kind, '') in ('изображение', 'pdf', '')"""


def num(v, w=12):
    return f"{v:,}".replace(",", " ").rjust(w)


def ocr_image(path: str, psm: str = PSM_MAIN) -> tuple[str, str]:
    """Текст и ПОЧЕМУ его столько. Второе важнее первого.

    Раньше любая неудача возвращала пустую строку, и файл получал статус
    «пусто» — тот же, что у настоящей фотографии без надписей. В итоге 233
    пустых файла из 400 не говорили ничего: то ли текста нет, то ли tesseract
    не уложился в таймаут. Статус не должен врать (CLAUDE.md, правило 15),
    поэтому причина возвращается отдельно и доезжает до lib_files.reason."""
    try:
        r = subprocess.run(["tesseract", path, "stdout", "-l", LANG, "--psm", psm],
                           capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return "", "таймаут распознавания"
    except FileNotFoundError:
        return "", "tesseract не установлен"
    except Exception as e:
        return "", f"сбой запуска: {type(e).__name__}"
    текст = r.stdout.decode("utf-8", "ignore")
    if r.returncode != 0:
        return текст, f"tesseract вернул код {r.returncode}"
    return текст, ("текста не найдено" if not текст.strip() else "")


def ocr_pdf(blob: bytes, tmp: str) -> tuple[str, str]:
    """PDF без текстового слоя: разворачиваем страницы в картинки и читаем их."""
    src = os.path.join(tmp, "in.pdf")
    with open(src, "wb") as f:
        f.write(blob)
    try:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-l", str(PAGES), src,
                        os.path.join(tmp, "p")], capture_output=True, timeout=TIMEOUT * 2)
    except subprocess.TimeoutExpired:
        return "", "таймаут разворота PDF в картинки"
    except Exception as e:
        return "", f"сбой pdftoppm: {type(e).__name__}"
    страницы = [n for n in sorted(os.listdir(tmp))
                if n.startswith("p") and n.endswith(".png")]
    if not страницы:
        return "", "pdftoppm не дал ни одной страницы"
    parts, причины = [], []
    for name in страницы:
        текст, причина = ocr_image(os.path.join(tmp, name))
        parts.append(текст)
        if причина:
            причины.append(причина)
    итог = "\n".join(parts)
    if итог.strip():
        return итог, ""
    return почему_пусто(причины, len(страницы))


def почему_пусто(причины: list[str], страниц: int) -> tuple[str, str]:
    """Почему у PDF не вышло: таймаут хотя бы одной страницы важнее пустоты."""
    for p in причины:
        if "таймаут" in p or "код" in p or "не установлен" in p:
            return "", f"{p} (страниц {страниц})"
    return "", f"текста не найдено на {страниц} страницах"


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
            text, причина = ocr_pdf(blob, tmp)
        elif kind == "изображение":
            p = os.path.join(tmp, "img")
            with open(p, "wb") as f:
                f.write(blob)
            text, причина = ocr_image(p, PSM_MAIN)
            if not text.strip() and "таймаут" not in причина:
                # Вторая попытка другим режимом сегментации: на фотографии
                # листа блочный режим иногда молчит. После таймаута не
                # повторяем — вторая попытка тоже не уложится.
                text, причина2 = ocr_image(p, PSM_RETRY)
                причина = "" if text.strip() else f"{причина}; повтор: {причина2}"
        else:
            rec["status"] = "формат не читаем"
            rec["reason"] = f"распознавать нечего: {kind}"
            return rec, []

    rec["chars"] = len(text)
    if not text.strip():
        rec["status"] = "пусто"
        rec["reason"] = причина or "распознавание не дало текста"
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
    причины: Counter = Counter()
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
            if rec["status"] in ("пусто", "формат не читаем"):
                причины[(rec["reason"] or "—")[:60]] += 1
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
    if причины:
        print("почему ничего не вышло (это и есть указание, что чинить):")
        for причина, n in причины.most_common(8):
            print(f"    {причина:62}{n:>6}")
    print("позиции по сегментам:")
    for sid, n in segs.most_common(20):
        print(f"    {name_of(None if sid == '—' else sid):32s} {n:>8d}")
    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
    print("\n✓ распознавание части завершено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
