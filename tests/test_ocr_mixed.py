"""Распознавание не трогает файлы, у которых уже есть позиции.

ЗАЧЕМ. В отбор распознавания в этой ветке взяты смешанные PDF (pdf_mixed):
текстовые страницы разобраны, сканы — нет. Запись распознавания пишет файл
целиком: price_store снимает все цены файла, строки ложатся рядом со строками
разбора, статус затирается. Первая попытка — «заменять разбор, если
распознанное даёт больше цен» — не прошла перепроверку 23.09.2026: распознавание
читает первые PAGES страниц (12), а разбор — до шестидесяти, и замена стирала
цены страниц 13+; цены при замене удаляются безвозвратно, откат по ключу
прогона невозможен. Поэтому правило проще: файл с позициями распознавание не
берёт вовсе, пока нет постраничного слияния.

Здесь же три поломки записи распознавания:
- разворот PDF в картинки при таймауте возвращал два значения вместо трёх —
  распаковка падала, и пул ронял всю часть;
- незакачанный файл получал отметку ocr_at и больше в очередь не попадал,
  хотя повтор закачки возвращает его разбору;
- незакачанный файл затирал известный вид пустым и выпадал из отборов по виду.

И переразметка (library/reclassify.py): словарь снимал ЛЮБУЮ пометку строки,
которую узнал, — в том числе «строка заменена переразбором», и прежняя
редакция строки оживала дублем рядом с новой.

Корпус придуман (правило 18). SQL-часть работает при LIBRARY_SQL_TEST_DSN.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import ocr  # noqa: E402
import reclassify  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
СХЕМА = "ocr_mixed_test"


# ───────────────────────── разворот PDF в картинки ─────────────────────────

@pytest.mark.parametrize("ошибка,причина,окружение", [
    (subprocess.TimeoutExpired("pdftoppm", 1), "таймаут разворота PDF в картинки", True),
    (FileNotFoundError("pdftoppm"), "pdftoppm не установлен", True),
    (OSError("сломано"), "сбой pdftoppm: OSError", False)])
def test_разворот_pdf_возвращает_три_значения(monkeypatch, tmp_path, ошибка, причина,
                                              окружение):
    def бросить(*a, **k):
        raise ошибка
    monkeypatch.setattr(ocr.subprocess, "run", бросить)
    строки, текст, почему = ocr.ocr_pdf_подробно(b"%PDF-1.4 fiktiv", str(tmp_path))
    assert (строки, текст, почему) == ([], "", причина)
    assert ocr.виновато_окружение(почему) is окружение
    assert ocr.ocr_pdf(b"%PDF-1.4 fiktiv", str(tmp_path)) == ("", причина)


def test_правило_замены_переразбора_словарь_не_снимает():
    """Правило пометки переразбора и список переразметки — одно и то же имя."""
    import library.reparse as r
    assert r.RULE in reclassify.НЕ_СНИМАТЬ_СЛОВАРЁМ


# ───────────────────────────── на базе ─────────────────────────────

@pytest.fixture
def cur():
    if not DSN:
        pytest.skip("одноразовая база PostgreSQL не настроена")
    import psycopg2
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    c.execute(f"drop schema if exists {СХЕМА} cascade")
    c.execute(f"create schema {СХЕМА}")
    c.execute(f"set search_path to {СХЕМА}")
    c.execute("""create table lib_files (file_id text primary key, deal_id text,
                   status text, kind text, chars int, rows_found int, segment_id text,
                   reason text, ocr_at timestamptz, ocr_chars int, parser_version smallint,
                   processed_at timestamptz, pdf_mixed boolean)""")
    c.execute("""create table lib_demand (id bigserial primary key, segment_id text,
                   deal_id text, item_name text, oem text, part_number text, qty numeric,
                   unit text, source text, source_file text, segment_rule text)""")
    c.execute("""create table lib_row_junk (
                   demand_id bigint primary key references lib_demand(id) on delete cascade,
                   rule text not null, run_id text not null, marks text,
                   marked_at timestamptz not null default now(), revoked_at timestamptz,
                   revoked_by text)""")
    c.execute("create table lib_prices ("
              + ", ".join(f"{к} text" for к in ocr.price_store.КОЛОНКИ) + ")")
    try:
        yield c
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        conn.close()


def файлы(cur, строки: str, *параметры) -> None:
    cur.execute("insert into lib_files (file_id, deal_id, status, kind, rows_found, reason, "
                "pdf_mixed, ocr_at) values " + строки, параметры)


def test_отбор_не_берёт_файлы_с_позициями(cur):
    файлы(cur, """
      ('1', '1', 'разобран', 'pdf', 12, null, true, null),
      ('2', '1', 'текст без спецификации', 'pdf', 0, null, true, null),
      ('3', '1', 'пусто', 'pdf', 0, 'PDF без текстового слоя', null, null),
      ('4', '1', 'пусто', 'docx', 0, %s, null, null),
      ('5', '1', 'пусто', 'изображение', 0, 'нет текстового слоя', null, null),
      ('6', '1', 'пусто', 'pdf', 0, 'PDF без текстового слоя', null, now()),
      ('7', '1', 'не скачался', 'pdf', 0, null, null, null),
      ('8', '1', 'разобран', 'docx', 3, %s, null, null)""",
          f"{ocr.indexer.КАРТИНКИ_ВНУТРИ} 2 — читаются распознаванием",
          f"{ocr.indexer.КАРТИНКИ_ВНУТРИ} 1 — читаются распознаванием")
    cur.execute(ocr.CANDIDATES)
    assert sorted(r[0] for r in cur.fetchall()) == ["2", "3", "4", "5"]


def test_переразметка_не_снимает_пометку_замены(cur):
    cur.execute("""insert into lib_demand (source_file, source, item_name) values
      ('7', 'котировка поставщика', 'Насос ВЫДУМ-1'),
      ('7', 'котировка поставщика', 'Насос ВЫДУМ-2')""")
    cur.execute("select id from lib_demand order by id")
    проза, замена = (r[0] for r in cur.fetchall())
    cur.execute("insert into lib_row_junk (demand_id, rule, run_id) values "
                "(%s, 'proza-v1', 'proza-тест'), (%s, 'переразбор v2', 'reparse-тест')",
                (проза, замена))
    cur.execute(reclassify.UNMARK_SQL, ([проза, замена], list(reclassify.НЕ_СНИМАТЬ_СЛОВАРЁМ)))
    cur.execute("select demand_id, revoked_by from lib_row_junk order by demand_id")
    assert cur.fetchall() == [(проза, "словарь"), (замена, None)]


def test_выборка_переразметки_несёт_правило_пометки(cur):
    cur.execute("insert into lib_demand (source_file, item_name) values ('7', 'Насос ВЫДУМ-1')")
    cur.execute("insert into lib_row_junk (demand_id, rule, run_id) "
                "select id, 'переразбор v2', 'reparse-тест' from lib_demand")
    cur.execute(reclassify.UNSEGMENTED)
    строка = cur.fetchone()
    assert len(строка) == 8 and строка[6:] == (True, "переразбор v2")
    cur.execute(reclassify.UNSEGMENTED_PLAIN)
    assert cur.fetchone()[6:] == (False, None)


# ───────────────────── сквозной прогон записи на базе ─────────────────────

def test_запись_распознавания_на_базе(cur, monkeypatch):
    """Настоящий ocr.main с настоящей записью; портал и tesseract заглушены.

    1 — смешанный PDF с позициями: в работу не берётся вовсе, всё нетронуто.
    2 — смешанный, разбор позиций не дал: распознаётся как обычный скан.
    3 — скан, который не скачался: без отметки, вид сохранён, повтор закачки
        вернёт его разбору, а распознавание — в очередь.
    4 — скан с отказом окружения: без отметки, как прежде.
    """
    import psycopg2
    файлы(cur, """
      ('1', '1', 'разобран', 'pdf', 2, null, true, null),
      ('2', '1', 'текст без спецификации', 'pdf', 0, null, true, null),
      ('3', '2', 'пусто', 'pdf', 0, 'PDF без текстового слоя', null, null),
      ('4', '2', 'пусто', 'pdf', 0, 'PDF без текстового слоя', null, null)""")
    cur.execute("insert into lib_demand (source_file, source, item_name) values "
                "('1', 'котировка поставщика', 'Насос ВЫДУМ-1'), "
                "('1', 'котировка поставщика', 'Насос ВЫДУМ-2')")
    cur.execute("insert into lib_prices (source_url, feed, item_name, price) values "
                "('1', %s, 'Насос ВЫДУМ-1', '1')", (ocr.price_store.FEED,))

    def поз(файл: str, n: int) -> list[dict]:
        return [{"item_name": f"Распознанный насос ВЫДУМ-{файл}-{i}", "part_number": "",
                 "oem": "", "unit": "", "qty": 1, "segment_id": "насосы",
                 "segment_rule": "файл", "deal_id": "1", "source_file": файл,
                 "company": None,
                 "_цена": {"price": 10.0 + i, "currency": "RUB", "confidence": 0.9}}
                for i in range(n)]

    итоги = {
        "1": ("разобран по скану", "pdf", None, поз("1", 5)),
        "2": ("разобран по скану", "pdf", None, поз("2", 3)),
        "3": ("не скачался", None, None, []),
        "4": ("пусто", "pdf", "таймаут распознавания (120 с)", []),
    }
    распознаны: list[str] = []

    def распознать(ref):
        fid = ref["fo"]["id"]
        распознаны.append(fid)
        статус, вид, причина, items = итоги[fid]
        return ({"file_id": fid, "deal_id": ref["deal"], "status": статус,
                 "chars": 700 if items else 0, "rows_found": len(items),
                 "segment_id": "насосы" if items else None, "kind": вид,
                 "reason": причина}, items)

    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal.example.test/rest/1/x")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused.example.test/x")
    monkeypatch.setattr(ocr, "APPLY", True)
    monkeypatch.setattr(ocr, "ПОВТОР", False)
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    monkeypatch.setattr(ocr, "recognise", распознать)
    monkeypatch.setattr(ocr.indexer, "collect_refs_rfq", lambda *a, **k: [
        {"fo": {"id": ф}, "deal": "1"} for ф in итоги])

    def соединение(*a, **k):
        c = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
        c.autocommit = False
        return c
    monkeypatch.setattr(ocr.indexer, "connect", соединение)

    assert ocr.main() == 0
    assert sorted(распознаны) == ["2", "3", "4"]

    def файл(fid):
        cur.execute("select status, kind, rows_found, ocr_at is not null from lib_files "
                    "where file_id = %s", (fid,))
        return cur.fetchone()

    def строк_и_цен(fid):
        cur.execute("select count(*) from lib_demand where source_file = %s", (fid,))
        строк = cur.fetchone()[0]
        cur.execute("select count(*) from lib_prices where source_url = %s", (fid,))
        return строк, cur.fetchone()[0]

    assert файл("1") == ("разобран", "pdf", 2, False) and строк_и_цен("1") == (2, 1)
    assert файл("2") == ("разобран по скану", "pdf", 3, True) and строк_и_цен("2") == (3, 3)
    assert файл("3") == ("не скачался", "pdf", 0, False)
    assert файл("4") == ("пусто", "pdf", 0, False)
