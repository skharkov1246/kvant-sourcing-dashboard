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

import os
import struct
import subprocess
from datetime import datetime, timezone
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import docfilter  # noqa: E402  (после sys.path)
import indexer  # noqa: E402
import price_store  # noqa: E402  (запись цены — одна на все разборы)
import quotes  # noqa: E402  (цена из распознанного текста)
from segments import classify, name_of  # noqa: E402

APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
SHARDS = int(os.environ.get("SHARDS", "1"))
SHARD = int(os.environ.get("SHARD", "0"))
# ПОТОКОВ НЕ БОЛЬШЕ, ЧЕМ ЯДЕР, СКОЛЬКО БЫ НИ ПОПРОСИЛИ.
#
# ПОПРАВКА 23.09.2026: число потоков НЕ БЫЛО причиной таймаутов, как здесь
# утверждалось. Шаг прогона `library-index.yml` вызывает распознавание строкой
# `WORKERS=4 python library/ocr.py` — четвёрка прибита в самом шаге и перекрывает
# `WORKERS` из окружения задания. Значит и прогон 35790672035 с его 69 % таймаутов
# шёл на четырёх потоках, а не на двенадцати: вход `workers` до распознавания
# никогда не доходил. Повторный холостой прогон 35833770032 это и показал — журнал
# части молчит об обрезке (просили 4, ядер 4, обрезать нечего), а таймаутов
# 8 файлов из 10. Причина в другом и ищется отдельно.
#
# Предел тем не менее ОСТАЁТСЯ, но как страховка, а не как починка: он держит
# второго вызывающего, который прибитой четвёрки не унаследует и передаст
# `workers` целиком. Распознавание упирается в процессор, и потоки сверх числа
# ядер дают не скорость, а таймауты. Предел стоит ЗДЕСЬ, а не в прогоне: про
# процессорную природу распознавания знает этот модуль, а не вызывающий.
ЗАПРОШЕНО = int(os.environ.get("WORKERS", "4"))
WORKERS = max(1, min(ЗАПРОШЕНО, os.cpu_count() or 2))
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

# ПОТОКИ ВНУТРИ TESSERACT, А НЕ ТОЛЬКО СНАРУЖИ. Замер 23.09.2026 на четырёх ядрах,
# tesseract 5.3.4, выдуманный шумный лист 3024×4032 (12,2 Мпкс):
#
#     1 процесс, потоки OpenMP по умолчанию        1,2 с
#     1 процесс, OMP_THREAD_LIMIT=1                1,2 с
#     4 процесса разом, потоки по умолчанию      400,1 с — ни один не дочитал
#     4 процесса разом, OMP_THREAD_LIMIT=1         1,2 с на все четыре
#
# Причина видна в /proc: у каждого процесса ЧЕТЫРЕ потока — tesseract собран с
# OpenMP и сам берёт все ядра. Четыре процесса по четыре потока на четырёх ядрах
# дают шестнадцать потоков, которые крутятся на барьерах OpenMP вместо работы;
# машина при этом загружена целиком, а полезной работы нет.
#
# Поэтому потоки делятся: снаружи WORKERS процессов, внутри каждого — своя доля ядер.
# Произведение держится около числа ядер, а не в четыре раза выше.
#
# РАЗМЕР КАРТИНКИ НИ ПРИ ЧЁМ, и это стоит записать, чтобы не чинить не то: тот же
# лист в 3,0 и 5,4 Мпкс читается за те же 1,3–1,4 с, а чистая вёрстка на 139 Мпкс —
# за 16,6 с. Уменьшать картинки перед распознаванием незачем.
ЯДЕР = os.cpu_count() or 2
OMP = os.environ.get("OCR_OMP") or str(max(1, ЯДЕР // max(1, WORKERS)))
СРЕДА = {**os.environ, "OMP_THREAD_LIMIT": OMP}

# ЗАМЕР: сколько секунд ушло на картинку и какого она размера. Нужен, чтобы
# следующий прогон отвечал «почему таймаут» цифрами по живым файлам, а не
# рассуждением. В журнал уходят только агрегаты (CLAUDE.md, правило 17):
# число файлов, доля таймаутов и медиана секунд по разрядам мегапикселей.
ЗАМЕРЫ: list[tuple[float, float, bool]] = []   # (мпкс, секунды, таймаут)
ЗАМОК = threading.Lock()


def пикселей(path: str) -> float:
    """Мегапиксели из заголовка файла. Без сторонних библиотек: PNG и JPEG.

    Возвращает 0.0, если размер не прочитался, — разряд «неизвестно» честнее
    выдуманного числа."""
    try:
        with open(path, "rb") as f:
            head = f.read(2)
            if head == b"\x89P":
                f.seek(16)
                w, h = struct.unpack(">II", f.read(8))
                return w * h / 1e6
            if head == b"\xff\xd8":
                f.seek(2)
                while True:
                    m = f.read(2)
                    if len(m) < 2 or m[0] != 0xFF:
                        return 0.0
                    if 0xC0 <= m[1] <= 0xCF and m[1] not in (0xC4, 0xC8, 0xCC):
                        f.read(3)
                        h, w = struct.unpack(">HH", f.read(4))
                        return w * h / 1e6
                    (длина,) = struct.unpack(">H", f.read(2))
                    f.seek(длина - 2, 1)
    except Exception:
        return 0.0
    return 0.0


РАЗРЯДЫ = ((2.0, "до 2 Мпкс"), (6.0, "2–6"), (12.0, "6–12"),
           (24.0, "12–24"), (float("inf"), "больше 24"))


def таблица_времени() -> list[str]:
    """Строки отчёта: разряд размера × файлов × таймаутов × медиана секунд."""
    if not ЗАМЕРЫ:
        return []
    по_разрядам: dict[str, list[tuple[float, bool]]] = {}
    for мпкс, сек, таймаут in ЗАМЕРЫ:
        имя = "размер не прочитан" if мпкс <= 0 else next(
            n for предел, n in РАЗРЯДЫ if мпкс < предел)
        по_разрядам.setdefault(имя, []).append((сек, таймаут))
    out = ["время распознавания по размеру картинки:",
           f"    {'разряд':<20}{'вызовов':>9}{'таймаутов':>11}{'медиана с':>11}"]
    порядок = ["размер не прочитан"] + [n for _, n in РАЗРЯДЫ]
    for имя in порядок:
        если = по_разрядам.get(имя)
        if not если:
            continue
        секунды = sorted(с for с, _ in если)
        медиана = секунды[len(секунды) // 2]
        таймаутов = sum(1 for _, t in если if t)
        out.append(f"    {имя:<20}{len(если):>9}{таймаутов:>11}{медиана:>11.1f}")
    return out

# Кандидаты: текста нет, значит разбор не дал ничего. Форматы, в которых
# распознавать нечего, отсекаются ЗДЕСЬ, а не в обработчике: в первой части
# прогона 118 файлов из 400 оказались xlsx/docx, архивами и экзотикой —
# скачались, дошли до tesseract и вернули «формат не читаем». Это треть
# впустую потраченного времени части.
#: Причины, по которым файл НЕ ВИНОВАТ: это отказ окружения прогона, а не свойство
#: файла. Такой файл обязан остаться в очереди распознавания — иначе один
#: неудачный прогон выбрасывает его навсегда.
#:
#: 23.09.2026 так и вышло: отметка ocr_at ставилась в общем `on conflict do
#: update` независимо от исхода, а распознавание в те дни падало по таймауту на
#: 69 % файлов из-за потоков внутри tesseract. Потоки починены, но 3 666 картинок
#: остались с проставленной отметкой и в очередь больше не попадают. Сбросить её
#: нечем: кода, который это делает, в репозитории нет.
ПРИЧИНЫ_ОКРУЖЕНИЯ = (
    "tesseract не установлен",
    "сбой запуска",
    "таймаут распознавания",
    "таймаут разворота PDF в картинки",
    "pdftoppm не установлен",
)


def виновато_окружение(причина: str | None) -> bool:
    """Отказ окружения, а не свойство файла: отметку ставить нельзя."""
    return bool(причина) and any(причина.startswith(п) for п in ПРИЧИНЫ_ОКРУЖЕНИЯ)


CANDIDATES = """
select file_id from lib_files
 where ocr_at is null
   and (status = 'пусто' or kind = 'изображение'
        -- СМЕШАННЫЙ PDF: часть страниц текстовые, часть сканы. Такой файл имеет
        -- статус «разобран» и chars > 0, поэтому прежний отбор не брал его
        -- НИКОГДА — а сканы в нём это позиции и цены, которых никто не видел.
        or pdf_mixed is true)
   and status <> 'не скачался'
   and coalesce(kind, '') in ('изображение', 'pdf', '')"""


#: Повтор файлов, на которых распознавание отказало ПО ВИНЕ ОКРУЖЕНИЯ. Отдельным
#: входом, а не всегда: обычный отбор смотрит на пустую отметку, и файл с
#: проставленной отметкой в него не попадает никогда.
#:
#: ЗАЧЕМ ЭТО НУЖНО. 23.09.2026 распознавание падало по таймауту на 69 % файлов
#: из-за потоков внутри tesseract. Потоки починены, отметка больше не ставится за
#: отказ окружения — но 3 666 картинок УЖЕ помечены прежними прогонами, и обычный
#: отбор их не берёт. Этим входом их можно и померить холостым прогоном, и
#: перечитать записью.
ПОВТОР = os.environ.get("OCR_RETRY", "") not in ("", "0", "false")

ПОВТОР_ОТКАЗАВШИХ = """
select file_id from lib_files
 where status <> 'не скачался'
   and coalesce(kind, '') in ('изображение', 'pdf', '')
   and coalesce(rows_found, 0) = 0
   and (ocr_at is null
        or exists (select 1 from unnest(%s::text[]) p where reason like p || '%%'))"""


def num(v, w=12):
    return f"{v:,}".replace(",", " ").rjust(w)


def ocr_image(path: str, psm: str = PSM_MAIN) -> tuple[str, str]:
    """Текст и ПОЧЕМУ его столько. Второе важнее первого.

    Раньше любая неудача возвращала пустую строку, и файл получал статус
    «пусто» — тот же, что у настоящей фотографии без надписей. В итоге 233
    пустых файла из 400 не говорили ничего: то ли текста нет, то ли tesseract
    не уложился в таймаут. Статус не должен врать (CLAUDE.md, правило 15),
    поэтому причина возвращается отдельно и доезжает до lib_files.reason."""
    мпкс, начало = пикселей(path), time.monotonic()
    try:
        r = subprocess.run(["tesseract", path, "stdout", "-l", LANG, "--psm", psm],
                           capture_output=True, timeout=TIMEOUT, env=СРЕДА)
    except subprocess.TimeoutExpired:
        with ЗАМОК:
            ЗАМЕРЫ.append((мпкс, time.monotonic() - начало, True))
        return "", "таймаут распознавания"
    except FileNotFoundError:
        return "", "tesseract не установлен"
    except Exception as e:
        return "", f"сбой запуска: {type(e).__name__}"
    with ЗАМОК:
        ЗАМЕРЫ.append((мпкс, time.monotonic() - начало, False))
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
            # РАСПОЗНАВАНИЕ НЕ ИМЕЕТ ПРАВА ПОРТИТЬ ЧУЖОЙ РЕЗУЛЬТАТ. Сюда попадают
            # файлы, у которых вид в базе был пуст: отбор берёт их как возможные
            # сканы, а на деле это книга или архив. Прежде такой файл получал
            # «формат не читаем» — то есть распознавание ЗАТИРАЛО статус, который
            # поставил разбор, и делало вид, будто файл нечитаем в принципе.
            # Замер 23.09.2026: 1 022 файла xlsx/docx с причиной «распознавать
            # нечего», плюс 245 в «прочем» и 129 в архивах.
            #
            # Теперь распознавание записывает только то, что узнало САМО — вид
            # файла, — и оставляет статус разбору. Вид не пустой, значит в отбор
            # сканов файл больше не попадёт, и лишняя закачка не повторится.
            rec["status"] = None
            rec["reason"] = None
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
    # Валюта всего файла — запасной довод, когда строка своей не назвала.
    вф = quotes.валюта_файла(text)
    for ln in lines[:2000]:
        own = classify(ln)
        # Распознанный скан — тот же текст без таблицы, и цена опознаётся так же:
        # арифметикой кол-во × цена = сумма (library/quotes.py). Без этого
        # распознавание сканов КП теряло бы ровно то, ради чего затевается.
        ц = quotes.подставить_валюту(quotes.цена_из_текста(ln), вф) \
            if indexer.SOURCE == "rfq" else None
        items.append({"item_name": ln[:300], "part_number": docfilter.part_number_of(ln),
                      "oem": "", "unit": "", "qty": ц["qty"] if ц else None,
                      "segment_id": own or rec["segment_id"],
                      "segment_rule": "строка" if own else ("файл" if rec["segment_id"] else None),
                      "deal_id": ref["deal"], "source_file": fid,
                      "company": ref.get("company"), "_цена": ц})
    rec["rows_found"] = len(items)
    rec["status"] = "разобран по скану" if items else "пусто"
    # КОММЕРЧЕСКИЕ УСЛОВИЯ И У РАСПОЗНАННОГО СКАНА. Без этого шага цена из скана
    # ложилась в базу без базиса, оплаты и сроков, а колонки источника оставались
    # пустыми — то есть отсутствие было НЕ ОТМЕЧЕНО, тогда как у обычного разбора
    # оно отмечено как «проверено, не написано». Разница видна в отчёте и сбивает:
    # одна и та же пустота значит разное в зависимости от того, каким путём файл
    # прошёл. Распознанный текст — такой же текст, и блок условий под таблицей в
    # нём есть (CLAUDE.md, правило 14: две вставки в одну таблицу правятся вместе).
    if indexer.SOURCE == "rfq" and items:
        indexer.применить_условия(items, text)
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
        cur.execute(ПОВТОР_ОТКАЗАВШИХ if ПОВТОР else CANDIDATES,
                    (list(ПРИЧИНЫ_ОКРУЖЕНИЯ),) if ПОВТОР else None)
        want = {r[0] for r in cur.fetchall()}
    conn.close()
    print(f"кандидатов на распознавание: {len(want)}", flush=True)
    # ЧИСЛО ПОТОКОВ НАЗЫВАЕТСЯ ВСЕГДА, а не только когда обрезано. Обрезка в
    # прогоне не срабатывает (шаг прибивает WORKERS=4, ядер тоже 4), и молчание
    # читалось как «потоков двенадцать», хотя их четыре. Строка в журнале
    # закрывает этот вопрос без чтения кода прогона.
    print(f"потоков распознавания: {WORKERS} (просили {ЗАПРОШЕНО}, ядер"
          f" {os.cpu_count()})"
          + (", обрезано: распознавание процессорное" if WORKERS < ЗАПРОШЕНО else ""),
          flush=True)
    if not want:
        print("нечего распознавать")
        return 0

    # ИСТОЧНИК ТОТ ЖЕ ВХОД, ЧТО У РАЗБОРА. Распознавание ходило только по сделкам,
    # и до сканов КП не добиралось вовсе — та же дыра, что была у индексатора:
    # цена живёт только во вложениях карточек запросов.
    #
    # ОБХОД ПОРТАЛА РАЗБИВАЕТСЯ ДО ЗАПРОСА ПОДРОБНОСТЕЙ, А НЕ ПОСЛЕ. Прежде здесь
    # стоял collect_refs(DAYS) без частей, и отбор шёл потом по хешу файла: то есть
    # все двенадцать частей делали один и тот же полный обход портала и получали
    # HTTP 429 — ровно то, на чём 21.09.2026 умерли двенадцать частей переразбора.
    # Предупреждение об этом стоит в reparse.py; здесь его не было.
    #
    # И ОТБОР ПО ХЕШУ ФАЙЛА СНЯТ. Разбиение уже сделано по сделкам, а файл
    # принадлежит одной сделке: два независимых разбиения подряд выбросили бы файл,
    # попавший в часть 3 по сделке и в часть 7 по хешу, — его не взял бы никто.
    # ОБХОД ДЕЛИТСЯ НА ЧАСТИ У ОБОИХ ИСТОЧНИКОВ. Прежде по предложениям работала
    # одна часть: обход карточек запросов было нечем поделить. Теперь он берёт свой
    # диапазон идентификаторов (indexer.диапазон_части), и один полный обход
    # раскладывается на части, а не повторяется каждой.
    #
    # ОТБОР ПО ХЕШУ ФАЙЛА СНЯТ и не возвращается: разбиение уже сделано обходом, а
    # файл принадлежит одной карточке. Два разбиения подряд выбросили бы файл,
    # попавший в часть 3 по карточке и в часть 7 по хешу, — его не взял бы никто.
    refs = (indexer.collect_refs_rfq(DAYS, SHARD, SHARDS) if indexer.SOURCE == "rfq"
            else indexer.collect_refs(DAYS, SHARD, SHARDS))
    mine = [r for r in refs
            if str(r["fo"].get("id") or r["fo"].get("ID")) in want]
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
    цен = 0
    buf_files: list[tuple] = []
    buf_items: list[tuple] = []
    buf_prices: list[tuple] = []

    def flush() -> None:
        nonlocal buf_files, buf_items, buf_prices
        if not APPLY or (not buf_files and not buf_items and not buf_prices):
            buf_files, buf_items, buf_prices = [], [], []
            return
        c = indexer.connect()
        with c.cursor() as cur:
            if buf_prices:
                # Та же запись, что у обычного разбора: две вставки в одну
                # таблицу расходятся молча (CLAUDE.md, правило 14).
                try:
                    price_store.записать(cur, buf_prices, psycopg2.extras.execute_values)
                except psycopg2.Error as e:
                    c.rollback()
                    c.close()
                    raise RuntimeError(f"{price_store.ПОДСКАЗКА}. Ошибка: {e}") from e
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
                      -- ПУСТОЙ СТАТУС ОЗНАЧАЕТ «НЕ МОЁ ДЕЛО»: файл оказался не
                      -- сканом, и статус, поставленный разбором, сохраняется.
                      -- Прежде распознавание писало сюда «формат не читаем» и
                      -- затирало чужой результат: 1 022 файла xlsx/docx.
                      status = coalesce(excluded.status, lib_files.status),
                      kind = excluded.kind,
                      chars = case when excluded.status is null then lib_files.chars
                                   else excluded.chars end,
                      rows_found = case when excluded.status is null
                                        then lib_files.rows_found
                                        else excluded.rows_found end,
                      segment_id = coalesce(excluded.segment_id, lib_files.segment_id),
                      reason = case when excluded.status is null then lib_files.reason
                                    else excluded.reason end,
                      -- НЕ now(), А ТО, ЧТО ПРИСЛАЛИ. Пустая отметка означает отказ
                      -- окружения: прежнее значение сохраняется, и файл остаётся в
                      -- очереди распознавания.
                      ocr_at = coalesce(excluded.ocr_at, lib_files.ocr_at),
                      ocr_chars = excluded.ocr_chars,
                      processed_at = now()""", buf_files, page_size=500)
        c.commit()
        c.close()
        buf_files, buf_items, buf_prices = [], [], []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, (rec, items) in enumerate(pool.map(recognise, mine), 1):
            stat[rec["status"] or "не скан: статус оставлен разбору"] += 1
            if rec["kind"]:
                kinds[rec["kind"]] += 1
            if rec["status"] in ("пусто", "формат не читаем"):
                причины[(rec["reason"] or "—")[:60]] += 1
            total_items += rec["rows_found"]
            for it in items:
                segs[it["segment_id"] or "—"] += 1
            # ОТМЕТКА СТАВИТСЯ ТОЛЬКО ЗА НАСТОЯЩУЮ ПОПЫТКУ. При отказе окружения
            # (нет tesseract, таймаут) отметка не ставится, и файл остаётся в
            # очереди: иначе один неудачный прогон выбрасывает его навсегда.
            отметка = None if виновато_окружение(rec.get("reason")) else datetime.now(timezone.utc)
            buf_files.append((rec["file_id"], rec["deal_id"], rec["status"], rec["kind"],
                              rec["chars"], rec["rows_found"], rec["segment_id"],
                              indexer.pg(rec["reason"]), отметка, rec["chars"],
                              indexer.PARSER_VERSION))
            for it in items:
                buf_items.append((it["segment_id"], it["deal_id"], indexer.pg(it["item_name"])[:500],
                                  "", indexer.pg(it["part_number"])[:120], None, "",
                                  "распознавание скана", it["source_file"], it["segment_rule"]))
                ц = it.get("_цена")
                if ц and indexer.SOURCE == "rfq":
                    buf_prices.append(price_store.строка(it, ц, indexer.pg))
                    цен += 1
            if len(buf_files) >= 100 or len(buf_items) >= 3000 or len(buf_prices) >= 2000:
                flush()
            if n % 50 == 0:
                print(f"  распознано {n} из {len(mine)} · позиций {total_items}", flush=True)
    flush()

    print("\n=== ИТОГ ЧАСТИ ===")
    print(f"файлов: {sum(stat.values())} · позиций из сканов: {total_items}")
    for строка in таблица_времени():
        print(строка)
    if indexer.SOURCE == "rfq":
        print(f"строк с ценой: {цен}"
              + (f" ({цен * 100 // total_items} % позиций)" if total_items else ""))
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
