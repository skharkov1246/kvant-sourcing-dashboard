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

СМЕШАННЫЙ PDF — ПО СТРАНИЦАМ, СЛИЯНИЕМ. У PDF, где часть страниц текстовые, а
часть сканы (lib_files.pdf_mixed), текстовые страницы уже разобраны. Такой файл
распознаётся постранично: читаются ТОЛЬКО страницы без текстового слоя (до
indexer.СТРАНИЦ_PDF, как у разбора), и их позиции и цены ДОБАВЛЯЮТСЯ к разбору.
Строки и цены разбора, статус и счётчики файла не трогаются; меняется только
отметка ocr_at.

ВОЗОБНОВЛЯЕМОСТЬ. Отметка в lib_files.ocr_at: повторный запуск пропускает уже
распознанное. Работа делится на части так же, как в indexer.py.

ПОВТОР И ОТКАТ. Каждая запись — с ключом прогона и строкой в журнале
lib_ocr_writes: что вставлено, что заменено, каким был файл. Повтор по тому же
файлу помечает (не удаляет) свои прежние строки и выводит из потока свои прежние
цены; чужих — разбора — не касается. Откат по ключу возвращает прежнее состояние.

    SUPABASE_DB_URL=... BITRIX_WEBHOOK_URL=... python library/ocr.py          # вхолостую
    SUPABASE_DB_URL=... BITRIX_WEBHOOK_URL=... APPLY=1 python library/ocr.py  # с записью
    SUPABASE_DB_URL=... OCR_REVERT=<ключ прогона> python library/ocr.py       # откат

В журнал идут только агрегаты: ни распознанного текста, ни имён файлов.
"""
from __future__ import annotations

import json
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
import ocr_table  # noqa: E402  (таблица скана по координатам слов — под каскадом)
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


#: Два режима записи. ЦЕЛИКОМ — разбор файла не дал ничего, и распознавание
#: пишет файл само: позиции, статус, причину. ПОСТРАНИЧНО — смешанный PDF, у
#: которого текстовые страницы уже дали позиции: распознаются только страницы
#: без текста, их позиции добавляются к разбору, а файл остаётся разбору.
ЦЕЛИКОМ = "файл"
ПОСТРАНИЧНО = "страницы"

CANDIDATES = """
select file_id,
       case when pdf_mixed is true and coalesce(rows_found, 0) > 0
            then '{ПОСТРАНИЧНО}' else '{ЦЕЛИКОМ}' end,
       coalesce(rows_found, 0)
  from lib_files
 where ocr_at is null
   and status <> 'не скачался'
   and (
     -- ЦЕЛИКОМ: разбор не дал позиций, писать распознаванию нечего портить.
     (coalesce(rows_found, 0) = 0
      and (status = 'пусто' or kind = 'изображение'
           -- СМЕШАННЫЙ PDF, у которого разбор текстовых страниц ничего не дал:
           -- «текст без спецификации», где спецификация — в сканах.
           or pdf_mixed is true
           -- СКАНЫ ВНУТРИ ДОКУМЕНТА, АРХИВА ИЛИ ПИСЬМА. По виду это не скан, и
           -- прежний отбор не брал такой файл никогда; каскад разбора помечает
           -- его причиной, начало которой — indexer.КАРТИНКИ_ВНУТРИ.
           or reason like '{КАРТИНКИ}%')
      and (coalesce(kind, '') in ('изображение', 'pdf', '')
           or reason like '{КАРТИНКИ}%'))
     -- ПОСТРАНИЧНО: смешанный PDF с позициями разбора. До 23.09.2026 такой файл
     -- сюда не входил вовсе: распознавание писало файл целиком, читало первые
     -- PAGES страниц из шестидесяти, которые читает разбор, и снимало все цены
     -- файла. Теперь читаются только сканы, а запись добавляет, не заменяя.
     -- Файл с позициями и сканами ВНУТРИ документа (КАРТИНКИ_ВНУТРИ) по-прежнему
     -- не берётся: номеров страниц у картинки в документе нет, слить нечем.
     or (pdf_mixed is true and kind = 'pdf' and status = 'разобран'
         and coalesce(rows_found, 0) > 0))""".replace(
    "{КАРТИНКИ}", indexer.КАРТИНКИ_ВНУТРИ).replace(
    "{ПОСТРАНИЧНО}", ПОСТРАНИЧНО).replace("{ЦЕЛИКОМ}", ЦЕЛИКОМ)


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
select file_id, '""" + ЦЕЛИКОМ + """', 0 from lib_files
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


def распознать_картинку(path: str) -> tuple[list[list[str]], str, str]:
    """Картинка → (строки таблицы, текст, почему пусто).

    ПОД КАСКАДОМ — ТАБЛИЦА ПО КООРДИНАТАМ СЛОВ (library/ocr_table.py). Сплошной
    текст tesseract склеивает колонки строки через пробел, и цена отделяется от
    количества только арифметикой «кол-во × цена = сумма»: строка без суммы
    теряет цену. Разрез по координатам ставит каждое число под свой заголовок.
    Строки возвращаются БЕЗ ворот шапки: таблица скана продолжается на следующих
    страницах без заголовка, и ворота ставит вызывающий — по всем страницам сразу.

    Без каскада — прежний путь: блочный режим, при пустоте повтор другим.
    """
    if indexer.КАСКАД:
        rows, text, _сводка = ocr_table.распознать_таблицу(path, env=СРЕДА)
        # Сводка при успехе — служебная (уверенность, поворот), а не отказ:
        # пусто или нет, решает сам текст.
        if rows or text.strip():
            return rows, text, ""
    text, причина = ocr_image(path, PSM_MAIN)
    if not text.strip() and "таймаут" not in причина:
        # Вторая попытка другим режимом сегментации: на фотографии листа блочный
        # режим иногда молчит. После таймаута не повторяем — не уложится и она.
        text, причина2 = ocr_image(path, PSM_RETRY)
        причина = "" if text.strip() else f"{причина}; повтор: {причина2}"
    return [], text, причина


def ocr_pdf(blob: bytes, tmp: str) -> tuple[str, str]:
    """PDF без текстового слоя: разворачиваем страницы в картинки и читаем их."""
    _строки, текст, причина = ocr_pdf_подробно(blob, tmp)
    return текст, причина


def ocr_pdf_подробно(blob: bytes, tmp: str) -> tuple[list[list[str]], str, str]:
    """То же, что ocr_pdf, плюс строки таблицы всех страниц подряд (под каскадом)."""
    src = os.path.join(tmp, "in.pdf")
    with open(src, "wb") as f:
        f.write(blob)
    try:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-l", str(PAGES), src,
                        os.path.join(tmp, "p")], capture_output=True, timeout=TIMEOUT * 2)
    # ТРИ ЗНАЧЕНИЯ НА ЛЮБОМ ВЫХОДЕ. Здесь стояло два: вызывающий распаковывает
    # три, и первый же таймаут разворота ронял пул, а с ним весь прогон части.
    except subprocess.TimeoutExpired:
        return [], "", "таймаут разворота PDF в картинки"
    except FileNotFoundError:
        return [], "", "pdftoppm не установлен"
    except Exception as e:
        return [], "", f"сбой pdftoppm: {type(e).__name__}"
    страницы = [n for n in sorted(os.listdir(tmp))
                if n.startswith("p") and n.endswith(".png")]
    if not страницы:
        return [], "", "pdftoppm не дал ни одной страницы"
    return прочитать_страницы([os.path.join(tmp, n) for n in страницы])


def прочитать_страницы(пути: list[str]) -> tuple[list[list[str]], str, str]:
    """Картинки страниц подряд → (строки таблицы, текст, почему пусто)."""
    parts, причины, строки = [], [], []
    for путь in пути:
        if indexer.КАСКАД:
            с, текст, причина = распознать_картинку(путь)
            строки += с
        else:
            текст, причина = ocr_image(путь)
        parts.append(текст)
        if причина:
            причины.append(причина)
    итог = "\n".join(parts)
    if итог.strip() or строки:
        return строки, итог, ""
    return [], *почему_пусто(причины, len(пути))


def ocr_pdf_страниц(blob: bytes, tmp: str, номера: list[int],
                    ) -> tuple[list[list[str]], str, str]:
    """Только названные страницы PDF (номера с единицы) — для смешанного файла.

    Каждая страница разворачивается отдельно (-f N -l N): сканы в смешанном PDF
    идут вперемешку с текстом, и разворот диапазона ради трёх сканов из сорока
    страниц — это распознавание тридцати семи страниц, уже прочитанных разбором.
    Отказ разворота на любой странице — отказ окружения за весь файл: запись по
    половине сканов закрыла бы отметкой и вторую половину.
    """
    src = os.path.join(tmp, "in.pdf")
    with open(src, "wb") as f:
        f.write(blob)
    пути = []
    for n in номера:
        префикс = os.path.join(tmp, f"s{n:04d}")
        try:
            subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-f", str(n), "-l", str(n),
                            "-singlefile", src, префикс],
                           capture_output=True, timeout=TIMEOUT * 2)
        except subprocess.TimeoutExpired:
            return [], "", "таймаут разворота PDF в картинки"
        except FileNotFoundError:
            return [], "", "pdftoppm не установлен"
        except Exception as e:
            return [], "", f"сбой pdftoppm: {type(e).__name__}"
        if os.path.exists(префикс + ".png"):
            пути.append(префикс + ".png")
    if not пути:
        return [], "", "pdftoppm не дал ни одной страницы"
    return прочитать_страницы(пути)


def распознать_сканы(сканы: list[tuple[str, bytes]], tmp: str,
                     ) -> tuple[list[list[str]], str, str]:
    """Сканы из документа, архива или письма: каждый — своим путём, итог подряд."""
    строки: list[list[str]] = []
    тексты: list[str] = []
    причины: list[str] = []
    for i, (вид, байты) in enumerate(сканы):
        каталог = os.path.join(tmp, f"s{i}")
        os.makedirs(каталог, exist_ok=True)
        if вид == "pdf":
            с, т, п = ocr_pdf_подробно(байты, каталог)
        else:
            путь = os.path.join(каталог, "img")
            with open(путь, "wb") as f:
                f.write(байты)
            с, т, п = распознать_картинку(путь)
        строки += с
        if т.strip():
            тексты.append(т)
        if п:
            причины.append(п)
    текст = "\n".join(тексты)
    if текст.strip() or строки:
        return строки, текст, ""
    return [], *почему_пусто(причины, len(сканы))


def таблица_скана(rec: dict, ref: dict, строки: list[list[str]], text: str) -> list[dict]:
    """Позиции из таблицы скана — тем же разбором строк, что и у файла с таблицей.

    Цена берётся из своей колонки (indexer.items_from_rows), а не арифметикой по
    сплошной строке. Ворота шапки пройдены у вызывающего; ворота спецификации
    таблице не нужны — у обычного разбора табличный путь их тоже не проходит.
    """
    items = indexer.items_from_rows(строки, шире=False)
    rec["segment_id"] = classify(text) if text else None
    вф = quotes.валюта_файла(text)
    fid = rec["file_id"]
    for it in items:
        own = classify(it.get("_row", ""))
        it["segment_id"] = own or rec["segment_id"]
        it["segment_rule"] = "строка" if own else ("файл" if rec["segment_id"] else None)
        it["deal_id"] = ref["deal"]
        it["source_file"] = fid
        it["company"] = ref.get("company")
        if indexer.SOURCE == "rfq":
            quotes.подставить_валюту(it.get("_цена"), вф)
        else:
            it["_цена"] = None          # цены пишутся только из карточек запросов
    rec["rows_found"] = len(items)
    rec["status"] = "разобран по скану" if items else "пусто"
    if not items:
        rec["reason"] = "таблица скана без позиций"
    if indexer.SOURCE == "rfq" and items:
        indexer.применить_условия(items, text)
    return items


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
    режим = ref.get("режим") or ЦЕЛИКОМ
    rec = {"file_id": fid, "deal_id": ref["deal"], "status": None, "chars": 0,
           "rows_found": 0, "segment_id": None, "kind": None, "reason": None,
           "режим": режим, "страницы": None}
    blob = indexer.download(fo)
    if not blob:
        rec["status"] = "не скачался"
        return rec, []
    kind = indexer.sniff(blob)
    rec["kind"] = kind
    строки: list[list[str]] = []
    # Текст всего документа — для того, что относится к файлу, а не к строке:
    # сегмент файла, валюта, условия под таблицей. У смешанного PDF блок условий
    # часто стоит на текстовой странице, а позиции — на скане.
    контекст = ""
    with tempfile.TemporaryDirectory() as tmp:
        if режим == ПОСТРАНИЧНО:
            if kind != "pdf":
                # Отбор назвал файл смешанным PDF, а байты — не PDF. Сливать
                # нечего, файл остаётся разбору; это ответ, а не отказ окружения.
                rec["reason"] = "постранично: байты не PDF"
                return rec, []
            # СТРАНИЦЫ ТЕ ЖЕ, ЧТО У РАЗБОРА: тот же читатель и тот же предел
            # indexer.СТРАНИЦ_PDF, а не первые PAGES. Иначе сканы после
            # двенадцатой страницы не прочёл бы никто.
            страницы, _всего, _потеряно = indexer.страницы_pdf(blob)
            номера = indexer.страницы_сканов(страницы)
            rec["страницы"] = номера
            if not номера:
                rec["status"] = "пусто"
                rec["reason"] = "страниц без текстового слоя не нашлось"
                return rec, []
            строки, text, причина = ocr_pdf_страниц(blob, tmp, номера)
            контекст = "\n".join(с for с in страницы if с.strip())
        elif kind == "pdf":
            строки, text, причина = ocr_pdf_подробно(blob, tmp)
        elif kind == "изображение":
            p = os.path.join(tmp, "img")
            with open(p, "wb") as f:
                f.write(blob)
            строки, text, причина = распознать_картинку(p)
        elif сканы := indexer.картинки_файла(blob):
            # Сканы внутри документа, архива или письма (indexer.картинки_файла):
            # каскад разбора пометил файл причиной «картинки внутри:».
            строки, text, причина = распознать_сканы(сканы, tmp)
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
    if not text.strip() and not строки:
        rec["status"] = "пусто"
        rec["reason"] = причина or "распознавание не дало текста"
        return rec, []
    весь = "\n".join(т for т in (контекст, text) if т.strip())
    # Таблица скана — как таблица PDF: ослабленное правило шапки к ней не
    # применяется (его мерили только на офисных файлах, PDF от него теряет цены).
    if строки and indexer.header_map(строки, шире=False)[0] >= 0:
        return rec, таблица_скана(rec, ref, строки, весь)

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
    rec["segment_id"] = classify(весь)
    # Валюта всего файла — запасной довод, когда строка своей не назвала.
    вф = quotes.валюта_файла(весь)
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
        indexer.применить_условия(items, весь)
    return rec, items


# ───────────────────────────── запись ─────────────────────────────

#: Источник строки спроса со скана — тот же, что у цены со скана: по нему разбор
#: и переразбор узнают чужие строки и не трогают их (reparse.OLD_ROWS).
ИСТОЧНИК_СТРОКИ = price_store.ИСТОЧНИК_СКАНА
#: Правила пометок в lib_row_junk. Словарь переразметки их не снимает
#: (reclassify.НЕ_СНИМАТЬ_СЛОВАРЁМ): заменённая строка скана узнаётся словарём
#: как номенклатура, и снятие пометки оживило бы её дублем рядом с новой.
RULE_ЗАМЕНА = "распознавание заменено"
RULE_ОТКАТ = "откат распознавания"
#: Журнал записей распознавания: по строке на файл и прогон (schema_junk.sql).
ЖУРНАЛ = "lib_ocr_writes"
REVERT = os.environ.get("OCR_REVERT", "").strip()

#: Какие поля lib_files меняет запись в каждом режиме. Их прежние значения идут в
#: журнал, и откат возвращает ровно их: постраничная запись файл разбору
#: оставляет, и откат не имеет права вернуть переразбору прежний статус.
ПОЛЯ_ФАЙЛА = {
    ЦЕЛИКОМ: ("status", "kind", "chars", "rows_found", "segment_id", "reason",
              "ocr_at", "ocr_chars"),
    ПОСТРАНИЧНО: ("ocr_at", "ocr_chars"),
}


def снимок_файла(режим: str) -> str:
    """Запрос: поля файла, которые запись изменит, одним jsonb."""
    пары = ", ".join(f"'{к}', {к}" for к in ПОЛЯ_ФАЙЛА[режим])
    return f"select jsonb_build_object({пары}) from lib_files where file_id = %s"


# Свои прежние строки файла — все строки скана, кроме уже заменённых или снятых
# откатом распознавания.
#
# СТРОКА С ЧУЖОЙ ПОМЕТКОЙ ТОЖЕ СВОЯ. Пометку «проза» словарь снимает, когда узнаёт
# в строке номенклатуру (reclassify.py); не пометь повтор такую строку заменённой —
# и после снятия «прозы» прежняя редакция ожила бы дублем рядом с новой. Поэтому
# замена переписывает любую пометку, а прежнюю пометку помнит журнал, и откат
# возвращает её как была.
СВОИ_СТРОКИ = """
select d.id from lib_demand d
 where d.source_file = %s and d.source = %s
   and not exists (select 1 from lib_row_junk j
                    where j.demand_id = d.id and j.revoked_at is null
                      and j.rule = any(%s))"""

ПРЕЖНИЕ_ПОМЕТКИ = """
select demand_id, rule, run_id, marks, marked_at, revoked_at, revoked_by
  from lib_row_junk where demand_id = any(%s)"""

# Пометка, а не удаление (CLAUDE.md, правило 5). Переписывает прежнюю пометку
# строки — действующую или снятую (см. СВОИ_СТРОКИ); прежняя — в журнале.
ПОМЕТИТЬ = """
insert into lib_row_junk (demand_id, rule, run_id, marks)
select unnest(%s::bigint[]), %s, %s, %s
on conflict (demand_id) do update
   set rule = excluded.rule, run_id = excluded.run_id, marks = excluded.marks,
       marked_at = now(), revoked_at = null, revoked_by = null"""

ВСТАВИТЬ_СТРОКИ = """
insert into lib_demand
  (segment_id, deal_id, item_name, oem, part_number, qty, unit, source, source_file,
   segment_rule)
values %s
returning id"""

ФАЙЛ_ЦЕЛИКОМ = """
insert into lib_files (file_id, deal_id, status, kind, chars, rows_found,
                       segment_id, reason, ocr_at, ocr_chars, parser_version)
values %s
on conflict (file_id) do update set
  -- ПУСТОЙ СТАТУС ОЗНАЧАЕТ «НЕ МОЁ ДЕЛО»: файл оказался не
  -- сканом, и статус, поставленный разбором, сохраняется.
  -- Прежде распознавание писало сюда «формат не читаем» и
  -- затирало чужой результат: 1 022 файла xlsx/docx.
  status = coalesce(excluded.status, lib_files.status),
  -- Файл, который не скачался, вида не знает: пустое значение
  -- затёрло бы известный вид, и файл выпал бы из отборов по виду.
  kind = coalesce(excluded.kind, lib_files.kind),
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
  processed_at = now()"""

# ПОСТРАНИЧНАЯ ЗАПИСЬ ФАЙЛ НЕ ПЕРЕПИСЫВАЕТ. Статус, счётчики, причина и сегмент —
# разбора; распознавание добавляет только свою отметку и число своих знаков.
ФАЙЛ_ПОСТРАНИЧНО = "update lib_files set ocr_at = %s, ocr_chars = %s where file_id = %s"

ЗАПИСЬ_ЖУРНАЛА = f"""
insert into {ЖУРНАЛ} (run_id, file_id, mode, pages, demand_ids, price_ids,
                      replaced_demand_ids, replaced_price_ids, replaced_marks,
                      prev_file, note)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)"""


def отметка_попытки(rec: dict) -> datetime | None:
    """Отметка ocr_at — только за настоящую попытку.

    При отказе окружения (нет tesseract, таймаут) отметка не ставится, и файл
    остаётся в очереди: иначе один неудачный прогон выбрасывает его навсегда. Не
    скачался — распознавания не было вовсе: отметка его закрыла бы навсегда, хотя
    повтор закачки (RETRY_FAILED) вернёт файл в очередь.
    """
    if rec["status"] == "не скачался" or виновато_окружение(rec.get("reason")):
        return None
    return datetime.now(timezone.utc)


def строка_спроса(it: dict) -> tuple:
    """Позиция скана → строка lib_demand; колонки те же, что у переразбора."""
    return (it["segment_id"], it["deal_id"], indexer.pg(it["item_name"])[:500],
            indexer.pg(it.get("oem"))[:200], indexer.pg(it.get("part_number"))[:120],
            it.get("qty"), indexer.pg(it.get("unit"))[:40], ИСТОЧНИК_СТРОКИ,
            it["source_file"], it.get("segment_rule"))


def записать_файл(cur, run_id: str, rec: dict, items: list[dict],
                  execute_values) -> Counter:
    """Один файл: свои прежние строки и цены — в пометку, новые — в базу, след — в журнал.

    ЧУЖОГО НЕ КАСАЕТСЯ. Свои — строки с источником ИСТОЧНИК_СТРОКИ и цены с
    источником price_store.ИСТОЧНИК_СКАНА этого файла. Строки и цены разбора
    остаются как были в обоих режимах; в постраничном — и сам файл в lib_files.

    НИЧЕГО НЕ УДАЛЯЕТ. Прежние строки помечаются в lib_row_junk правилом
    RULE_ЗАМЕНА с ключом прогона, прежние цены уходят в поток FEED_ВЫВЕДЕНО. Что
    вставлено и что заменено — в журнале: по нему откат (откатить) возвращает
    прежнее состояние.

    Постраничная запись при отказе окружения и незакачке не пишет ничего: файл
    остаётся в очереди, а разбору нечего возвращать.
    """
    счёт: Counter = Counter()
    fid = rec["file_id"]
    режим = rec.get("режим") or ЦЕЛИКОМ
    отметка = отметка_попытки(rec)
    if режим == ПОСТРАНИЧНО and отметка is None:
        счёт["файлов без записи: отказ или незакачка"] += 1
        return счёт
    cur.execute(снимок_файла(режим), (fid,))
    r = cur.fetchone()
    прежде = r[0] if r else None

    заменены: list[int] = []
    пометки: list[dict] = []
    новые_строки: list[int] = []
    новые_цены: list[int] = []
    выведены: list[int] = []
    if отметка is not None:
        # Настоящая попытка: её итог заменяет прежний итог распознавания файла.
        cur.execute(СВОИ_СТРОКИ, (fid, ИСТОЧНИК_СТРОКИ, [RULE_ЗАМЕНА, RULE_ОТКАТ]))
        заменены = [x[0] for x in cur.fetchall()]
        if заменены:
            cur.execute(ПРЕЖНИЕ_ПОМЕТКИ, (заменены,))
            имена = [к.name for к in cur.description]
            пометки = [dict(zip(имена, x)) for x in cur.fetchall()]
            cur.execute(ПОМЕТИТЬ, (заменены, RULE_ЗАМЕНА, run_id, "повтор распознавания"))
        if items:
            новые_строки = [x[0] for x in execute_values(
                cur, ВСТАВИТЬ_СТРОКИ, [строка_спроса(it) for it in items],
                page_size=500, fetch=True)]
        цены = [price_store.строка(it, it["_цена"], indexer.pg, price_store.ИСТОЧНИК_СКАНА)
                for it in items if it.get("_цена") and indexer.SOURCE == "rfq"]
        новые_цены, выведены = price_store.записать_скан(cur, fid, цены, execute_values)

    if режим == ПОСТРАНИЧНО:
        cur.execute(ФАЙЛ_ПОСТРАНИЧНО, (отметка, rec["chars"], fid))
    else:
        execute_values(cur, ФАЙЛ_ЦЕЛИКОМ, [(
            fid, rec["deal_id"], rec["status"], rec["kind"], rec["chars"],
            rec["rows_found"], rec["segment_id"], indexer.pg(rec["reason"]),
            отметка, rec["chars"], indexer.PARSER_VERSION)])
    cur.execute(ЗАПИСЬ_ЖУРНАЛА, (
        run_id, fid, режим, rec.get("страницы"), новые_строки, новые_цены, заменены,
        выведены, json.dumps(пометки, ensure_ascii=False, default=str) if пометки else None,
        json.dumps(прежде, ensure_ascii=False, default=str) if прежде else None,
        (rec.get("reason") or "")[:200] or None))
    счёт["файлов записано"] += 1
    счёт["строк вставлено"] += len(новые_строки)
    счёт["цен вставлено"] += len(новые_цены)
    счёт["своих прежних строк помечено"] += len(заменены)
    счёт["своих прежних цен выведено"] += len(выведены)
    счёт["файлов, где своих строк стало меньше"] += len(новые_строки) < len(заменены)
    return счёт


# ───────────────────────────── откат ─────────────────────────────

# КЛЮЧ ПРОГОНА — ЦЕЛИКОМ ИЛИ ВСЕ ЧАСТИ СРАЗУ. Часть прогона пишет с ключом
# «<ключ>-p<номер части>»; откат по «<ключ>» снимает все части.
ЖУРНАЛ_ПРОГОНА = f"""
select w.run_id, w.file_id, w.mode, w.demand_ids, w.price_ids,
       w.replaced_demand_ids, w.replaced_price_ids, w.replaced_marks, w.prev_file,
       exists (select 1 from {ЖУРНАЛ} l
                where l.file_id = w.file_id and l.written_at > w.written_at
                  and l.reverted_at is null and l.run_id <> w.run_id)
  from {ЖУРНАЛ} w
 where (w.run_id = %s or w.run_id like %s) and w.reverted_at is null"""

СНЯТЬ_ПОМЕТКИ_ЗАМЕНЫ = """
update lib_row_junk set revoked_at = now(), revoked_by = %s
 where demand_id = any(%s) and run_id = %s and rule = %s and revoked_at is null"""
# Пометка, которую переписала замена, возвращается как была — со своим правилом,
# ключом прогона и снятием, если оно было.
ВЕРНУТЬ_ПОМЕТКИ = """
update lib_row_junk j
   set rule = p.rule, run_id = p.run_id, marks = p.marks, marked_at = p.marked_at,
       revoked_at = p.revoked_at, revoked_by = p.revoked_by
  from jsonb_to_recordset(%s::jsonb) as p(demand_id bigint, rule text, run_id text,
       marks text, marked_at timestamptz, revoked_at timestamptz, revoked_by text)
 where j.demand_id = p.demand_id and j.run_id = %s and j.rule = %s"""
ПЕРЕВЕСТИ_ЦЕНЫ = "update lib_prices set feed = %s where id = any(%s) and feed = %s"
ОТМЕТИТЬ_ОТКАТ = f"""
update {ЖУРНАЛ} set reverted_at = now() where run_id = %s and file_id = %s"""


def откатить(cur, ключ: str) -> Counter:
    """Откат прогона распознавания по ключу. Ничего не удаляет.

    Строки прогона помечаются (RULE_ОТКАТ), его цены уходят в FEED_ВЫВЕДЕНО;
    пометки замены, поставленные прогоном, снимаются (revoked_at), а если замена
    переписала чужую пометку — та возвращается как была; выведенные прогоном
    цены возвращаются в поток, а поля файла — к значениям из журнала.

    ФАЙЛ С БОЛЕЕ ПОЗДНЕЙ ЗАПИСЬЮ НЕ ОТКАТЫВАЕТСЯ. Вернуть файлу состояние до
    прогона поверх более позднего повтора значило бы стереть этот повтор, не
    откатив его. Такие файлы считаются отдельно: откатывать — с позднего.
    """
    счёт: Counter = Counter()
    cur.execute(ЖУРНАЛ_ПРОГОНА, (ключ, ключ + "-p%"))
    for (run_id, fid, режим, строки, цены, заменены, выведены, пометки, прежде,
         есть_позже) in cur.fetchall():
        if есть_позже:
            счёт["файлов пропущено: есть более поздняя запись"] += 1
            continue
        if строки:
            cur.execute(ПОМЕТИТЬ, (строки, RULE_ОТКАТ, run_id, "откат прогона"))
        if пометки:
            cur.execute(ВЕРНУТЬ_ПОМЕТКИ, (json.dumps(пометки, ensure_ascii=False), run_id,
                                          RULE_ЗАМЕНА))
        были = {п["demand_id"] for п in пометки or ()}
        снять = [d for d in заменены or () if d not in были]
        if снять:
            cur.execute(СНЯТЬ_ПОМЕТКИ_ЗАМЕНЫ, (f"откат {run_id}"[:60], снять, run_id,
                                              RULE_ЗАМЕНА))
        if цены:
            cur.execute(ПЕРЕВЕСТИ_ЦЕНЫ, (price_store.FEED_ВЫВЕДЕНО, цены, price_store.FEED))
        if выведены:
            cur.execute(ПЕРЕВЕСТИ_ЦЕНЫ, (price_store.FEED, выведены, price_store.FEED_ВЫВЕДЕНО))
        поля = [к for к in ПОЛЯ_ФАЙЛА.get(режим, ()) if прежде and к in прежде]
        if поля:
            cur.execute("update lib_files set " + ", ".join(f"{к} = %s" for к in поля)
                        + " where file_id = %s", [прежде[к] for к in поля] + [fid])
        cur.execute(ОТМЕТИТЬ_ОТКАТ, (run_id, fid))
        счёт["файлов откачено"] += 1
        счёт["строк прогона помечено"] += len(строки or ())
        счёт["цен прогона выведено"] += len(цены or ())
        счёт["заменённых строк возвращено"] += len(заменены or ())
        счёт["выведенных цен возвращено"] += len(выведены or ())
    return счёт


def ключ_прогона() -> str:
    """Ключ прогона: один на все части прогона Actions, с номером части в хвосте."""
    база = (f"{os.environ['GITHUB_RUN_ID']}.{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
            if os.environ.get("GITHUB_RUN_ID")
            else datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    return f"ocr-{база}" + (f"-p{SHARD + 1}" if SHARDS > 1 else "")


def журнал_есть(cur) -> bool:
    cur.execute("select to_regclass(%s) is not null", (ЖУРНАЛ,))
    return bool(cur.fetchone()[0])


def main() -> int:
    if REVERT:
        return main_откат()
    for var in ("BITRIX_WEBHOOK_URL", "SUPABASE_DB_URL"):
        if not os.environ.get(var):
            print(f"нет переменной {var}", file=sys.stderr)
            return 2
    import psycopg2
    import psycopg2.extras

    if SHARDS > 1:
        print(f"часть {SHARD + 1} из {SHARDS}", flush=True)
    print(f"режим: {'ЗАПИСЬ В БАЗУ' if APPLY else 'холостой, без записи'}", flush=True)

    run_id = ключ_прогона()
    print(f"ключ прогона: {run_id}", flush=True)

    conn = indexer.connect()
    with conn.cursor() as cur:
        # ЖУРНАЛ — УСЛОВИЕ ЗАПИСИ, А НЕ ЗАМЕРА. Без него запись не оставит следа,
        # и откатывать будет нечем (правило 6); холостому прогону он не нужен.
        if APPLY and not журнал_есть(cur):
            print(f"в базе нет журнала {ЖУРНАЛ}: запись без него не откатить. Примените "
                  "library/supabase/schema_junk.sql прогоном «ZIP base — apply DB "
                  "migrations» и повторите. Прогон остановлен до обхода портала.",
                  file=sys.stderr)
            conn.close()
            return 2
        cur.execute(ПОВТОР_ОТКАЗАВШИХ if ПОВТОР else CANDIDATES,
                    (list(ПРИЧИНЫ_ОКРУЖЕНИЯ),) if ПОВТОР else None)
        want = {r[0]: r[1] for r in cur.fetchall()}
        # ЧТО УЖЕ ЛЕЖИТ У СМЕШАННЫХ ФАЙЛОВ: строки и цены разбора (их запись не
        # трогает) и свои прежние строки и цены скана (их заменит повтор). Без
        # этого таблица холостого прогона говорит только «сколько добавится», но
        # не «к чему» и не «что заменится».
        постранично = sorted(f for f, м in want.items() if м == ПОСТРАНИЧНО)
        было = прежнее_по_файлам(cur, постранично)
    conn.close()
    print(f"кандидатов на распознавание: {len(want)}"
          f" (из них смешанных PDF с позициями разбора: {len(постранично)})", flush=True)
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
    # ОДИН ФАЙЛ — ОДНА ЗАПИСЬ ЗА ПРОГОН. Файл, приложенный к двум карточкам, иначе
    # распознавался бы дважды, и вторая запись пометила бы первую как «прежнюю».
    по_файлу: dict[str, dict] = {}
    for r in refs:
        fid = str(r["fo"].get("id") or r["fo"].get("ID"))
        if fid in want and fid not in по_файлу:
            по_файлу[fid] = {**r, "режим": want[fid]}
    mine = list(по_файлу.values())
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
    слияние: Counter = Counter()
    записано: Counter = Counter()
    буфер: list[tuple[dict, list[dict]]] = []

    def flush() -> None:
        nonlocal буфер
        if not APPLY or not буфер:
            буфер = []
            return
        c = indexer.connect()
        try:
            with c.cursor() as cur:
                for rec, items in буфер:
                    записано.update(записать_файл(cur, run_id, rec, items,
                                                  psycopg2.extras.execute_values))
            c.commit()
        except psycopg2.Error as e:
            # Откатываем ВСЁ: файлы, отмеченные распознанными, при потерянных
            # строках и ценах — молчаливая потеря, а упавший прогон повторяется.
            c.rollback()
            raise RuntimeError(f"{price_store.ПОДСКАЗКА}. Ошибка: {e}") from e
        finally:
            c.close()
        буфер = []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, (rec, items) in enumerate(pool.map(recognise, mine), 1):
            с_ценой = sum(1 for it in items
                          if it.get("_цена") and indexer.SOURCE == "rfq")
            if rec["режим"] == ПОСТРАНИЧНО:
                учесть_слияние(слияние, rec, items, с_ценой, было)
            else:
                stat[rec["status"] or "не скан: статус оставлен разбору"] += 1
                if rec["status"] in ("пусто", "формат не читаем"):
                    причины[(rec["reason"] or "—")[:60]] += 1
            if rec["kind"]:
                kinds[rec["kind"]] += 1
            total_items += len(items)
            цен += с_ценой
            for it in items:
                segs[it["segment_id"] or "—"] += 1
            буфер.append((rec, items))
            if len(буфер) >= 100 or sum(len(i) for _, i in буфер) >= 3000:
                flush()
            if n % 50 == 0:
                print(f"  распознано {n} из {len(mine)} · позиций {total_items}", flush=True)
    flush()

    print("\n=== ИТОГ ЧАСТИ ===")
    print(f"файлов: {len(mine)} · позиций из сканов: {total_items}")
    for строка in таблица_времени():
        print(строка)
    if indexer.SOURCE == "rfq":
        print(f"строк с ценой: {цен}"
              + (f" ({цен * 100 // total_items} % позиций)" if total_items else ""))
    print(f"по состоянию (распознаны целиком): {dict(stat.most_common())}")
    print(f"по формату:   {dict(kinds.most_common())}")
    if причины:
        print("почему ничего не вышло (это и есть указание, что чинить):")
        for причина, n in причины.most_common(8):
            print(f"    {причина:62}{n:>6}")
    for строка in таблица_слияния(слияние):
        print(строка)
    print("позиции по сегментам:")
    for sid, n in segs.most_common(20):
        print(f"    {name_of(None if sid == '—' else sid):32s} {n:>8d}")
    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
    else:
        print("\nзаписано:")
        for k, v in записано.items():
            print(f"    {k:44s}{v:>8d}")
        print(f"откат: OCR_REVERT={run_id} python library/ocr.py")
    print("\n✓ распознавание части завершено")
    return 0


# ─────────────────────── таблица слияния и откат ───────────────────────

ПРЕЖНЕЕ_ЦЕНЫ = """
select source_url,
       count(*) filter (where source is distinct from %s),
       count(*) filter (where source = %s)
  from lib_prices
 where feed = %s and source_url = any(%s)
 group by 1"""
ПРЕЖНЕЕ_СТРОКИ = """
select source_file, count(*) from lib_demand
 where source = %s and source_file = any(%s)
 group by 1"""
ПРЕЖНЕЕ_ФАЙЛЫ = """
select file_id, coalesce(rows_found, 0) from lib_files where file_id = any(%s)"""


def прежнее_по_файлам(cur, файлы: list[str]) -> dict[str, list[int]]:
    """По файлу: [строк разбора, цен разбора, своих строк скана, своих цен скана]."""
    было: dict[str, list[int]] = {f: [0, 0, 0, 0] for f in файлы}
    if not файлы:
        return было
    cur.execute(ПРЕЖНЕЕ_ФАЙЛЫ, (файлы,))
    for f, n in cur.fetchall():
        было[f][0] = int(n)
    cur.execute(ПРЕЖНЕЕ_ЦЕНЫ, (price_store.ИСТОЧНИК_СКАНА, price_store.ИСТОЧНИК_СКАНА,
                               price_store.FEED, файлы))
    for f, разбора, скана in cur.fetchall():
        было[f][1], было[f][3] = int(разбора), int(скана)
    cur.execute(ПРЕЖНЕЕ_СТРОКИ, (ИСТОЧНИК_СТРОКИ, файлы))
    for f, n in cur.fetchall():
        было[f][2] = int(n)
    return было


def учесть_слияние(сч: Counter, rec: dict, items: list[dict], с_ценой: int,
                   было: dict[str, list[int]]) -> None:
    """Постраничный файл — в счётчики таблицы слияния (только числа, правило 17)."""
    строк_разбора, цен_разбора, свои_строки, свои_цены = было.get(rec["file_id"], [0] * 4)
    сч["файлов"] += 1
    сч["строк разбора"] += строк_разбора
    сч["цен разбора"] += цен_разбора
    if rec["status"] == "не скачался":
        сч["не скачался"] += 1
    elif виновато_окружение(rec.get("reason")):
        сч["отказ окружения"] += 1
    elif rec.get("страницы") is None:
        сч["байты не PDF"] += 1
    elif not rec.get("страницы"):
        сч["сканов не нашлось"] += 1
    else:
        сч["распознано файлов"] += 1
        сч["распознано страниц"] += len(rec["страницы"])
        сч["строк со сканов"] += len(items)
        сч["цен со сканов"] += с_ценой
        сч["файлов с позициями"] += bool(items)
        сч["файлов с ценами"] += bool(с_ценой)
        сч["своих прежних строк"] += свои_строки
        сч["своих прежних цен"] += свои_цены
        сч["своих стало меньше"] += len(items) < свои_строки or с_ценой < свои_цены


def таблица_слияния(сч: Counter) -> list[str]:
    """Холостой прогон судится этой таблицей: сколько файлов, страниц, строк и
    цен дадут сканы смешанных PDF — и что при этом лежит у тех же файлов от
    разбора. Разбор запись не трогает, поэтому «стало хуже у разбора» здесь не
    бывает по построению; своё прежнее — бывает, и считается отдельно."""
    if not сч["файлов"]:
        return []
    ш = f"    {'':38s}{'файлов':>8}{'страниц':>9}{'строк':>9}{'цен':>8}"
    out = ["", "СЛИЯНИЕ СО СКАНАМИ (смешанные PDF с позициями разбора):", ш,
           f"    {'разбор, лежит в базе (не трогается)':38s}{сч['файлов']:>8}{'—':>9}"
           f"{сч['строк разбора']:>9}{сч['цен разбора']:>8}",
           f"    {'сканы распознаны':38s}{сч['распознано файлов']:>8}"
           f"{сч['распознано страниц']:>9}{сч['строк со сканов']:>9}{сч['цен со сканов']:>8}",
           f"    {'  из них дали позиции':38s}{сч['файлов с позициями']:>8}",
           f"    {'  из них дали цены':38s}{сч['файлов с ценами']:>8}"]
    for имя in ("сканов не нашлось", "байты не PDF", "отказ окружения", "не скачался"):
        if сч[имя]:
            хвост = " (остаются в очереди)" if имя in ("отказ окружения", "не скачался") else ""
            out.append(f"    {имя + хвост:38s}{сч[имя]:>8}")
    if сч["своих прежних строк"] or сч["своих прежних цен"]:
        out.append(f"    {'свои прежние строки скана (заменятся)':38s}{'':>8}{'':>9}"
                   f"{сч['своих прежних строк']:>9}{сч['своих прежних цен']:>8}")
        out.append(f"    {'  файлов, где своего станет меньше':38s}{сч['своих стало меньше']:>8}")
    return out


def main_откат() -> int:
    """OCR_REVERT=<ключ>: откат прогона одной транзакцией. Портал не нужен."""
    if not os.environ.get("SUPABASE_DB_URL"):
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    conn = indexer.connect()
    try:
        with conn.cursor() as cur:
            if not журнал_есть(cur):
                print(f"в базе нет журнала {ЖУРНАЛ} — откатывать нечего", file=sys.stderr)
                return 2
            счёт = откатить(cur, REVERT)
        conn.commit()
    finally:
        conn.close()
    print(f"откат прогона {REVERT}:")
    for k, v in счёт.items():
        print(f"    {k:44s}{v:>8d}")
    if not счёт["файлов откачено"] and not счёт["файлов пропущено: есть более поздняя запись"]:
        print("    записей прогона не найдено (или уже откачены)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
