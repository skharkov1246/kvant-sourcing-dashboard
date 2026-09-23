"""Распознавание файла, который уже разобран, не портит разбор.

ЗАЧЕМ. В отбор распознавания попадает смешанный PDF (pdf_mixed): текстовые
страницы разобраны, сканы — нет. Распознавание разворачивает в картинки ВСЕ
страницы, а запись обращалась с таким файлом как с пустым: price_store снимал
все цены файла, включая цены текстовых страниц, строки распознавания ложились
поверх строк разбора дублями, статус «разобран» затирался итогом распознавания,
вплоть до «пусто». Найдено 23.09.2026 до первого распознавания смешанных файлов.

Теперь распознанное заменяет разбор, только если он хуже; иначе ставится лишь
отметка ocr_at. Проверяется решение, отбор и пометка на настоящей базе.

Здесь же — «три значения на любом выходе» у разворота PDF в картинки: при
таймауте функция возвращала два, распаковка падала, и падение пула роняло
весь прогон части.

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

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
СХЕМА = "ocr_mixed_test"


def позиции(цен: int, без_цены: int = 0) -> list[dict]:
    return ([{"item_name": f"Насос ВЫДУМ-{i}", "_цена": {"price": 100.0 + i}}
             for i in range(цен)]
            + [{"item_name": f"Подпись {i}", "_цена": None} for i in range(без_цены)])


def запись(status="разобран по скану", reason=None) -> dict:
    return {"file_id": "7", "deal_id": "3", "status": status, "chars": 900,
            "rows_found": 5, "segment_id": "насосы", "kind": "pdf", "reason": reason}


# ───────────────────────────── решение ─────────────────────────────

def test_больше_цен_у_предложения_заменяет_разбор(monkeypatch):
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    rec, items, итог = ocr.решить_за_разбор(запись(), позиции(4), цен_было=3, строк_было=10)
    assert итог == ocr.ЗАМЕНЕНО and rec.get("_заменить") and len(items) == 4
    assert rec["status"] == "разобран по скану"


@pytest.mark.parametrize("цен", [0, 2, 3])
def test_не_больше_цен_оставляет_разбор_нетронутым(monkeypatch, цен):
    """Равенство — тоже «оставить»: замена стоит строк разбора, а цены не прибавляет."""
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    rec, items, итог = ocr.решить_за_разбор(запись(), позиции(цен, 7), цен_было=3,
                                            строк_было=10)
    assert итог == ocr.ОСТАВЛЕНО and items == [] and not rec.get("_заменить")
    # Пустой статус — знак «не моё дело»: запись lib_files сохраняет статус,
    # строки, знаки и причину разбора.
    assert rec["status"] is None and rec["reason"] is None
    assert rec["segment_id"] is None and rec["rows_found"] == 0


def test_пустое_распознавание_не_затирает_разобранный_файл(monkeypatch):
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    rec, items, итог = ocr.решить_за_разбор(
        запись(status="пусто", reason="распознавание не дало текста"), [], 3, 10)
    assert итог == ocr.ОСТАВЛЕНО and rec["status"] is None and items == []


def test_отказ_окружения_переживает_оставленный_разбор(monkeypatch):
    """Иначе причина стирается, отметка ocr_at встаёт, и файл выпадает из
    очереди распознавания навсегда."""
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    rec, _, _ = ocr.решить_за_разбор(
        запись(status="пусто", reason="таймаут распознавания (120 с)"), [], 3, 10)
    assert rec["reason"] is None
    assert ocr.виновато_окружение(rec["_отказ_окружения"])
    rec, _, _ = ocr.решить_за_разбор(запись(status="пусто", reason="битый файл"), [], 3, 10)
    assert rec["_отказ_окружения"] is None


def test_у_сделки_строки_разбора_не_заменяются(monkeypatch):
    """Цены у сделки нет, а позиции распознанной прозы с таблицей не сравнимы."""
    monkeypatch.setattr(ocr.indexer, "SOURCE", "deals")
    rec, items, итог = ocr.решить_за_разбор(запись(), позиции(0, 50), 0, строк_было=4)
    assert итог == ocr.ОСТАВЛЕНО and items == []


@pytest.mark.parametrize("источник", ["rfq", "deals"])
def test_без_строк_разбора_распознанное_берётся(monkeypatch, источник):
    """«Текст без спецификации»: строк нет, дублировать и терять нечего."""
    monkeypatch.setattr(ocr.indexer, "SOURCE", источник)
    rec, items, итог = ocr.решить_за_разбор(запись(), позиции(0, 6), 0, строк_было=0)
    assert итог == ocr.ЗАМЕНЕНО and len(items) == 6


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
    c.execute("""create table lib_files (file_id text primary key, status text, kind text,
                   rows_found int, reason text, ocr_at timestamptz, pdf_mixed boolean)""")
    c.execute("""create table lib_demand (id bigserial primary key, source_file text,
                   source text, item_name text)""")
    c.execute("""create table lib_row_junk (
                   demand_id bigint primary key references lib_demand(id) on delete cascade,
                   rule text not null, run_id text not null, marks text,
                   marked_at timestamptz not null default now(), revoked_at timestamptz,
                   revoked_by text)""")
    try:
        yield c
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        conn.close()


def test_отбор_на_базе_помечает_разобранные(cur):
    """Отбор выполняется с параметром — одиночный «%» в LIKE уронил бы его."""
    cur.execute("""insert into lib_files values
      ('1', 'разобран', 'pdf', 12, null, null, true),
      ('2', 'пусто', 'pdf', 0, 'PDF без текстового слоя', null, null),
      ('3', 'текст без спецификации', 'pdf', 0, null, null, true),
      ('4', 'пусто', 'docx', 0, %s, null, null),
      ('5', 'разобран', 'pdf', 9, null, null, false),
      ('6', 'разобран', 'pdf', 9, null, now(), true)""",
                (f"{ocr.indexer.КАРТИНКИ_ВНУТРИ} 2 — читаются распознаванием",))
    cur.execute(ocr.CANDIDATES, (list(ocr.СТАТУСЫ_РАЗБОРА),))
    got = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    assert got == {"1": (True, 12), "2": (False, 0), "3": (True, 0), "4": (False, 0)}


def test_повтор_отказавших_на_базе(cur):
    cur.execute("""insert into lib_files values
      ('1', 'пусто', 'pdf', 0, 'таймаут распознавания (120 с)', now(), null),
      ('2', 'пусто', 'pdf', 0, 'битый файл', now(), null)""")
    cur.execute(ocr.ПОВТОР_ОТКАЗАВШИХ,
                (list(ocr.СТАТУСЫ_РАЗБОРА), list(ocr.ПРИЧИНЫ_ОКРУЖЕНИЯ)))
    assert cur.fetchall() == [("1", False, 0)]


def test_пометка_берёт_только_строки_разбора_заменяемых_файлов(cur):
    cur.execute("""insert into lib_demand (source_file, source, item_name) values
      ('7', 'котировка поставщика', 'Насос ВЫДУМ-1'),
      ('7', 'котировка поставщика', 'Насос ВЫДУМ-2'),
      ('7', 'распознавание скана', 'Насос ВЫДУМ-3'),
      ('8', 'котировка поставщика', 'Насос ВЫДУМ-4'),
      ('9', null, 'Насос ВЫДУМ-5')""")
    cur.execute(ocr.ПОМЕТИТЬ_РАЗБОР, (ocr.ПРАВИЛО_ЗАМЕНЫ, "ocr-тест", ["7", "9"]))
    cur.execute("""select d.item_name, j.rule, j.run_id from lib_row_junk j
                    join lib_demand d on d.id = j.demand_id order by 1""")
    assert cur.fetchall() == [("Насос ВЫДУМ-1", ocr.ПРАВИЛО_ЗАМЕНЫ, "ocr-тест"),
                              ("Насос ВЫДУМ-2", ocr.ПРАВИЛО_ЗАМЕНЫ, "ocr-тест"),
                              ("Насос ВЫДУМ-5", ocr.ПРАВИЛО_ЗАМЕНЫ, "ocr-тест")]
    # Повтор того же прогона ничего не удваивает.
    cur.execute(ocr.ПОМЕТИТЬ_РАЗБОР, (ocr.ПРАВИЛО_ЗАМЕНЫ, "ocr-тест", ["7"]))
    cur.execute("select count(*) from lib_row_junk")
    assert cur.fetchone() == (3,)


# ───────────────────────────── порядок записи ─────────────────────────────

def test_пометка_стоит_до_вставки_строк_распознавания():
    """Код, а не комментарии: пометка после вставки не захватит строки
    распознавания (условие по источнику), но порядок «снять — вставить» — тот же,
    что у переразбора, и держится явно."""
    код = "\n".join(ln.split("#")[0] for ln in
                    (ROOT / "library" / "ocr.py").read_text(encoding="utf-8").splitlines())
    тело = код[код.index("def flush()"):]
    assert тело.index("ПОМЕТИТЬ_РАЗБОР") < тело.index("insert into lib_demand")
    assert "buf_replace.append" in код


# ───────────────────── сквозной прогон записи на базе ─────────────────────

def _сквозная_схема(cur) -> None:
    cur.execute("drop table if exists lib_row_junk, lib_demand, lib_files cascade")
    cur.execute("""create table lib_files (file_id text primary key, deal_id text,
                   status text, kind text, chars int, rows_found int, segment_id text,
                   reason text, ocr_at timestamptz, ocr_chars int, parser_version smallint,
                   processed_at timestamptz, pdf_mixed boolean)""")
    cur.execute("""create table lib_demand (id bigserial primary key, segment_id text,
                   deal_id text, item_name text, oem text, part_number text, qty numeric,
                   unit text, source text, source_file text, segment_rule text)""")
    cur.execute("""create table lib_row_junk (
                   demand_id bigint primary key references lib_demand(id) on delete cascade,
                   rule text not null, run_id text not null, marks text,
                   marked_at timestamptz not null default now(), revoked_at timestamptz,
                   revoked_by text)""")
    cur.execute("create table lib_prices ("
                + ", ".join(f"{к} text" for к in ocr.price_store.КОЛОНКИ) + ")")


def _цены(cur, файл: str) -> int:
    cur.execute("select count(*) from lib_prices where source_url = %s", (файл,))
    return cur.fetchone()[0]


def test_запись_распознавания_бережёт_разобранные_файлы(cur, monkeypatch):
    """Настоящий ocr.main с настоящей записью; портал и tesseract заглушены.

    7 — смешанный, распознанное даёт цен больше: заменяет, строки разбора помечены.
    8 — смешанный, цен меньше: разбор нетронут, стоит только отметка.
    9 — пустой скан: обычный путь, как прежде.
    10 — смешанный, отказ окружения: разбор нетронут, отметки нет (файл в очереди).
    """
    import psycopg2
    _сквозная_схема(cur)
    cur.execute("""insert into lib_files (file_id, deal_id, status, kind, chars, rows_found,
                   segment_id, reason, pdf_mixed) values
      ('7', '1', 'разобран', 'pdf', 500, 2, 'насосы', null, true),
      ('8', '1', 'разобран', 'pdf', 500, 3, 'насосы', null, true),
      ('9', '2', 'пусто', 'pdf', 0, 0, null, 'PDF без текстового слоя', null),
      ('10', '2', 'разобран', 'pdf', 500, 1, 'насосы', null, true)""")
    for файл, строк in (("7", 2), ("8", 3), ("10", 1)):
        for i in range(строк):
            cur.execute("insert into lib_demand (source_file, source, item_name) values "
                        "(%s, 'котировка поставщика', %s)", (файл, f"Насос ВЫДУМ-{файл}-{i}"))
            cur.execute("insert into lib_prices (source_url, feed, item_name, price) "
                        "values (%s, %s, %s, '1')",
                        (файл, ocr.price_store.FEED, f"Насос ВЫДУМ-{файл}-{i}"))

    def поз(файл: str, n: int, с_ценой: int) -> list[dict]:
        return [{"item_name": f"Распознанный насос ВЫДУМ-{файл}-{i}", "part_number": "",
                 "oem": "", "unit": "", "qty": 1, "segment_id": "насосы",
                 "segment_rule": "файл", "deal_id": "1", "source_file": файл,
                 "company": None,
                 "_цена": ({"price": 10.0 + i, "currency": "RUB", "confidence": 0.9}
                           if i < с_ценой else None)}
                for i in range(n)]

    итоги = {
        "7": ("разобран по скану", None, поз("7", 4, 3)),
        "8": ("разобран по скану", None, поз("8", 5, 1)),
        "9": ("разобран по скану", None, поз("9", 2, 2)),
        "10": ("пусто", "таймаут распознавания (120 с)", []),
    }

    def распознать(ref):
        fid = ref["fo"]["id"]
        статус, причина, items = итоги[fid]
        return ({"file_id": fid, "deal_id": ref["deal"], "status": статус,
                 "chars": 700, "rows_found": len(items), "segment_id": "насосы",
                 "kind": "pdf", "reason": причина}, items)

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

    def файл(fid):
        cur.execute("select status, rows_found, ocr_at is not null from lib_files "
                    "where file_id = %s", (fid,))
        return cur.fetchone()

    def помечено(fid):
        cur.execute("select count(*) from lib_row_junk j join lib_demand d "
                    "on d.id = j.demand_id where d.source_file = %s", (fid,))
        return cur.fetchone()[0]

    def распознанных(fid):
        cur.execute("select count(*) from lib_demand where source_file = %s "
                    "and source = 'распознавание скана'", (fid,))
        return cur.fetchone()[0]

    # 7: заменён — цены распознавания вместо цен разбора, строки разбора помечены.
    assert файл("7") == ("разобран по скану", 4, True)
    assert (помечено("7"), распознанных("7"), _цены(cur, "7")) == (2, 4, 3)
    # 8: разбор нетронут — статус, строки, цены; только отметка распознавания.
    assert файл("8") == ("разобран", 3, True)
    assert (помечено("8"), распознанных("8"), _цены(cur, "8")) == (0, 0, 3)
    # 9: пустой скан — как прежде.
    assert файл("9") == ("разобран по скану", 2, True)
    assert (распознанных("9"), _цены(cur, "9")) == (2, 2)
    # 10: отказ окружения — разбор нетронут, отметки нет.
    assert файл("10") == ("разобран", 1, False)
    assert (помечено("10"), _цены(cur, "10")) == (0, 1)
