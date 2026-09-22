"""Предложение целиком: от байтов PDF до строки, легшей в базу.

ЗАЧЕМ ИМЕННО СКВОЗНАЯ ПРОВЕРКА. 22.09.2026 она нашла ошибку, которую тридцать
модульных тестов пропустили, и пропустили закономерно: каждая формулировка по
отдельности читалась верно, а вместе — нет. Блок условий КП выглядит так:

    Условия поставки: DAP Москва.
    Условия оплаты: 30/70 — 30% предоплата.
    Срок изготовления 8 недель.

Отсев чужого срока смотрел ±20 знаков вокруг слова и перескакивал через точку: у
«Срок изготовления» слева находилось «оплат» из ПРЕДЫДУЩЕЙ строки, и срок
изготовления глушился. В базу ложилось «проверено, не написано» при том, что в
тексте он написан. Строка об оплате перед строкой о сроке — обычная форма блока
условий, так что теряться могло у большой доли файлов.

Вывод, из которого и сделан этот файл: проверять надо СОСЕДСТВО формулировок, а не
формулировки по одной.

ДВЕ ПРОВЕРКИ РАЗНОЙ ПРИРОДЫ.

1. Согласие кода и схемы. Всякая колонка, в которую пишет price_store, обязана
   быть в library/supabase/schema.sql. 22.09.2026 в живой базе не оказалось
   четырёх колонок, добавленных мержем: запись цены идёт всеми колонками сразу,
   и ночной разбор предложений упал бы целиком. Гейт этого не видел, потому что
   такой проверки не было. Она текстовая и не требует базы.

2. Сквозной прогон. Придуманное КП собирается в PDF, читается табличным путём,
   цена и условия сводятся, строка пишется в PostgreSQL и читается обратно.

Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))
sys.path.insert(0, str(ROOT))

import indexer  # noqa: E402
import offer_terms  # noqa: E402
import price_store  # noqa: E402
import quotes  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
нужна_база = pytest.mark.skipif(
    not DSN, reason="одноразовая база PostgreSQL не настроена")
нужен_pypdf = pytest.mark.skipif(
    importlib.util.find_spec("pypdf") is None, reason="pypdf не установлен")

СХЕМА = "тест_сквозной_кп"

# КОРПУС. Английская шапка — китайских КП у нас больше всех. Условия ОДНОЙ записью
# под таблицей, тремя строками подряд: базис, оплата, срок. Именно это соседство и
# ломало разбор.
КП = [
    "                    QUOTATION No. 114 dated 18.09.2026",
    "",
    " No  Description               Part No      Qty  Unit  Unit Price, USD   Amount, USD",
    " 1   Roller bearing            22315 EK       4  pcs           312,50      1 250,00",
    " 2   Mechanical seal           TRZ-4471/2    10  pcs            85,00        850,00",
    " 3   Spacer ring               6205-2RS       2  pcs            47,25         94,50",
    " 4   Rotor shaft assembly      NM-125/07      1  pcs        12 400,00     12 400,00",
    "",
    " Terms of delivery: DAP Moscow.",
    " Payment terms: 30/70 - 30% in advance.",
    " Production time 8 weeks.",
]


def test_все_колонки_цены_есть_в_схеме():
    """Колонка, в которую пишет код, обязана быть в схеме.

    22.09.2026 в живой базе не оказалось pay_terms, total, make_days и basis_src,
    добавленных мержем. Запись цены идёт всеми колонками сразу, поэтому ночной
    разбор предложений упал бы целиком, а не потерял бы одно поле. Проверка
    текстовая: схему применяет отдельный прогон, и между мержем и применением
    есть промежуток — но расхождение КОДА И ФАЙЛА схемы ловится здесь и сразу.
    """
    схема = (ROOT / "library" / "supabase" / "schema.sql").read_text()
    # Колонки lib_prices: и объявленные в create table, и добавленные alter-ами.
    создание = re.search(r"create table if not exists lib_prices\s*\((.*?)\n\);",
                         схема, re.S)
    assert создание, "объявление таблицы lib_prices в схеме не найдено"
    имена = set(re.findall(r"^\s*([a-z_]+)\s", создание.group(1), re.M))
    имена |= set(re.findall(
        r"alter table lib_prices add column if not exists\s+([a-z_]+)", схема))
    нет = [k for k in price_store.КОЛОНКИ if k not in имена]
    assert not нет, f"код пишет в колонки, которых нет в схеме: {нет}"


def test_соседство_условий_не_глушит_поля():
    """Каждая строка блока условий читается и в присутствии остальных.

    По одной все три читались верно и до правки — ошибка была именно в соседстве.
    """
    текст = ("Terms of delivery: DAP Moscow.\n"
             "Payment terms: 30/70 - 30% in advance.\n"
             "Production time 8 weeks.")
    у = offer_terms.условия_файла(текст)
    assert у["basis"] == "DAP"
    assert у["pay_terms"] == "30/70"
    assert у["pay_advance_pct"] == 30
    assert у["make_days"] == 56, "срок изготовления глохнет от соседней строки"
    # Срока ПОСТАВКИ в этом КП нет — и это верифицированное отсутствие.
    assert у["lead_days"] is None
    # То же по-русски: порядок строк тот же, слова другие.
    ру = ("Условия поставки: DAP Москва.\n"
          "Условия оплаты: 30/70 — 30% предоплата.\n"
          "Срок изготовления 8 недель.")
    assert offer_terms.условия_файла(ру)["make_days"] == 56


def _собрать_pdf(строки: list[str]) -> bytes:
    """Одностраничный PDF моноширинным шрифтом (как в tests/test_pdftable.py)."""
    части = ["BT", "/F1 9 Tf"]
    for i, ln in enumerate(строки):
        t = ln.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        части.append(f"1 0 0 1 40 {560 - i * 14} Tm ({t}) Tj")
    части.append("ET")
    поток = "\n".join(части).encode("latin-1", "replace")
    объекты = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(поток) + поток + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    смещения = []
    for i, o in enumerate(объекты, 1):
        смещения.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    начало = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(объекты) + 1)
    for off in смещения:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(объекты) + 1, начало))
    return bytes(out)


def _позиции_из(кп: list[str]) -> list[dict]:
    """Позиции с ценой и условиями — как их собирает разбор предложения."""
    rows = indexer.rows_from_pdf(_собрать_pdf(кп))
    hi, cols = indexer.header_map(rows)
    assert hi >= 0
    цк = quotes.колонки_цены(rows[hi])
    вк = quotes.валюта_заголовка(rows[hi], цк)
    позиции = []
    for r in rows[hi + 1:]:
        q = quotes.число_из(r[cols["qty"]])
        ц = quotes.цена_строки(r, цк, q, вк)
        if ц is None:
            continue
        позиции.append({"segment_id": None, "item_name": r[cols["item_name"]],
                        "part_number": r[cols["part_number"]], "oem": "",
                        "qty": q, "unit": r[cols["unit"]],
                        "source_file": "ТЕСТ-ФАЙЛ", "deal_id": "ТЕСТ-СДЕЛКА",
                        "company": "ТЕСТ-КОМПАНИЯ", "brands": "",
                        "_цена": ц, "_из_строки": offer_terms.из_строки(r, цк)})
    весь = "\n".join(" ".join(c for c in r if c) for r in rows)
    indexer.применить_условия(позиции, весь)
    return позиции


# Таблица строится по самому price_store.КОЛОНКИ: если код добавит колонку и
# забудет схему, тест выше покраснеет, а этот останется рабочим.
ТАБЛИЦА = ("create table lib_prices (id bigserial primary key, "
           + ", ".join(f"{k} text" for k in price_store.КОЛОНКИ
                       if k not in ("price", "qty", "total", "lead_days",
                                    "make_days", "pay_advance_pct", "price_date"))
           + ", price numeric, qty numeric, total numeric, lead_days int,"
             " make_days int, pay_advance_pct smallint, price_date date)")


@нужен_pypdf
def test_маленькая_таблица_идёт_текстовым_путём():
    """Таблица меньше четырёх строк таблицей НЕ читается, и это известно.

    pdftable.МИН_СТРОК_БЛОКА = 4: на двух-трёх строках жёлобы случайны, и разметка
    колонок была бы гаданием. Поймано этим же файлом — первая версия корпуса имела
    две позиции, и шапка не опознавалась вовсе.

    Следствие для дела: у КП на одну-три позиции цена по-прежнему опознаётся
    арифметикой в тексте, а не берётся из колонки. Это ограничение, а не ошибка, и
    закреплено здесь, чтобы оно было известным, а не всплывало в отчёте.
    """
    коротко = [КП[0], КП[1], КП[2], КП[3], КП[4]] + КП[7:]
    rows = indexer.rows_from_pdf(_собрать_pdf(коротко))
    # Либо таблицы нет вовсе, либо шапка не опознана — в обоих случаях разбор
    # уходит на текстовый путь, и хуже прежнего поведения не становится.
    assert not rows or indexer.header_map(rows)[0] < 0


@нужен_pypdf
@нужна_база
def test_сквозная_запись_предложения():
    """От байтов PDF до строки в базе — и обратно.

    Проверяется именно то, что уехало в базу: модульный тест смотрит на словарь в
    памяти, а ошибка 22.09.2026 была видна только в прочитанной обратно строке.
    """
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
        cur.execute(f'create schema "{СХЕМА}"')
        cur.execute(f'set search_path to "{СХЕМА}"')
        cur.execute(ТАБЛИЦА)

        позиции = _позиции_из(КП)
        assert len(позиции) == 4
        буфер = [price_store.строка(п, п["_цена"], indexer.pg) for п in позиции]
        price_store.записать(cur, буфер, psycopg2.extras.execute_values)

        cur.execute("select " + ", ".join(price_store.КОЛОНКИ)
                    + " from lib_prices order by price desc")
        строки = [dict(zip(price_store.КОЛОНКИ, r)) for r in cur.fetchall()]
        assert len(строки) == 4

        # Самая дорогая позиция: вал, 12 400 за штуку, количество одна.
        assert float(строки[0]["price"]) == 12400.00
        первая = next(r for r in строки if r["part_number"] == "22315 EK")
        assert float(первая["price"]) == 312.50
        assert float(первая["total"]) == 1250.00
        assert первая["currency"] == "USD"
        assert первая["part_number"] == "22315 EK"
        assert первая["qty_unit"] == "pcs"
        # Условия из блока под таблицей — с источником «файл».
        assert (первая["basis"], первая["basis_src"]) == ("DAP", offer_terms.ФАЙЛ)
        assert первая["pay_terms"] == "30/70"
        assert первая["pay_advance_pct"] == 30
        # ВОТ ЭТО И БЫЛО СЛОМАНО: «нет» вместо 56 из-за соседней строки об оплате.
        assert (первая["make_days"], первая["make_src"]) == (56, offer_terms.ФАЙЛ)
        # А срока поставки в КП правда нет — проверено дважды.
        assert (первая["lead_days"], первая["lead_src"]) == (None, offer_terms.НЕТ)
        # Самопроверка ценовой строки: цена × количество = сумма.
        assert abs(float(первая["price"]) * float(первая["qty"])
                   - float(первая["total"])) < 0.01

        # Идемпотентность по файлу: переразбор того же КП не удваивает строки.
        price_store.записать(cur, буфер, psycopg2.extras.execute_values)
        cur.execute("select count(*) from lib_prices")
        assert cur.fetchone()[0] == 4
    finally:
        cur.execute(f'drop schema if exists "{СХЕМА}" cascade')
        cur.close()
        conn.close()
