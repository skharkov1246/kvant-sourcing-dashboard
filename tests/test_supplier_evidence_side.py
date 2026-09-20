"""Доказательством «возит эту марку» может быть только документ ПОСТАВЩИКА.

История. Джойн positions → file_cards в base/suppliers.py шёл без стороны
документа. Поле «Request file» помечено в file_cards стороной «заказчик»
(SIDE_BY_FIELD, base/file_cards.py:44) — это исходящий файл, который отправили
поставщику мы. Его позиции попадали в brand_suppliers и в счётчики positions /
priced / brands, то есть доказательством работы поставщика с маркой служило то,
что прислали ему мы. По такому доказательству сорсинг выбирает, кому писать.

Корпус придуман здесь же (правило 18 CLAUDE.md): в базе ничего не берётся.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "base"))

SPEC = importlib.util.spec_from_file_location("suppliers", ROOT / "base" / "suppliers.py")
suppliers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(suppliers)

ПОСТАВЩИК = "Придуманный Поставщик ГмбХ"


def база(tmp_path) -> str:
    """Один запрос поставщику и два документа: его ответ и наш же запрос.

    В нашем файле — марка, которую поставщик не предлагал. Если сторона
    документа не учитывается, эта марка окажется в его активе.
    """
    путь = tmp_path / "kvant.db"
    con = sqlite3.connect(путь)
    con.executescript((ROOT / "base" / "schema.sql").read_text(encoding="utf-8"))
    con.executescript("""
      CREATE TABLE rfq (
        id INTEGER PRIMARY KEY, deal_id INTEGER, title TEXT,
        stage_id TEXT, stage TEXT, prev_stage_id TEXT,
        supplier_id INTEGER, supplier TEXT, contact_id INTEGER,
        company_id INTEGER, currency TEXT, amount REAL,
        created TEXT, updated TEXT, moved TEXT, closed TEXT,
        assigned_id INTEGER, sender_email TEXT, comment TEXT, chosen INTEGER,
        files INTEGER);
      CREATE TABLE file_cards (
        sha1 TEXT PRIMARY KEY, fid TEXT, kind TEXT, side TEXT, field TEXT,
        rfq_id INTEGER, supplier TEXT, deal_id INTEGER);
    """)
    con.execute("INSERT INTO deals (id, won, date_create) VALUES (?,?,?)",
                (500, 1, "2026-03-01"))
    con.execute("""INSERT INTO rfq (id, deal_id, stage, supplier_id, supplier,
                                    created, moved, chosen, files)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (10, 500, "КП получено", 9001, ПОСТАВЩИК,
                 "2026-03-01", "2026-03-05", 1, 1))
    # ответ поставщика: марка, которую он действительно предложил
    con.execute("""INSERT INTO file_cards (sha1, fid, kind, side, field, rfq_id, supplier, deal_id)
                   VALUES ('aa','f-ответ','оферта','поставщик','КП поставщика',10,?,500)""",
                (ПОСТАВЩИК,))
    # наш исходящий запрос: марка, которой у поставщика нет
    con.execute("""INSERT INTO file_cards (sha1, fid, kind, side, field, rfq_id, supplier, deal_id)
                   VALUES ('bb','f-наш','запрос','заказчик','Request file',10,?,500)""",
                (ПОСТАВЩИК,))
    con.executemany("""INSERT INTO positions (deal_id, fid, part_number, manufacturer,
                                              price, currency)
                       VALUES (?,?,?,?,?,?)""", [
        (500, "f-ответ", "NU2216", "ПРЕДЛОЖЕНО", 120.0, "EUR"),
        (500, "f-наш",   "NU2216", "ТОЛЬКО-В-НАШЕМ-ЗАПРОСЕ", 0.0, "EUR"),
        (500, "f-наш",   "NU2217", "ТОЛЬКО-В-НАШЕМ-ЗАПРОСЕ", 0.0, "EUR"),
    ])
    con.commit()
    con.close()
    return str(путь)


def test_марка_из_нашего_запроса_не_попадает_поставщику(tmp_path):
    db = база(tmp_path)
    suppliers.run(db)
    con = sqlite3.connect(db)
    марки = {r[0] for r in con.execute("SELECT brand FROM brand_suppliers")}
    assert "ПРЕДЛОЖЕНО" in марки, "марка из ответа поставщика потерялась"
    assert "ТОЛЬКО-В-НАШЕМ-ЗАПРОСЕ" not in марки, (
        "марка из нашего исходящего запроса зачтена поставщику как его канал")


def test_счётчик_позиций_считает_только_оферту(tmp_path):
    db = база(tmp_path)
    suppliers.run(db)
    con = sqlite3.connect(db)
    poz, priced = con.execute(
        "SELECT positions, priced FROM supplier_stats WHERE supplier = ?",
        (ПОСТАВЩИК,)).fetchone()
    assert poz == 1, f"позиций {poz}, ожидалась одна — только из оферты поставщика"
    assert priced == 1, f"с ценой {priced}, ожидалась одна"


def test_метрика_срока_названа_честно(tmp_path):
    """days_med переименована: она измеряет движение стадии нашей рукой."""
    db = база(tmp_path)
    suppliers.run(db)
    con = sqlite3.connect(db)
    колонки = {r[1] for r in con.execute("PRAGMA table_info(supplier_stats)")}
    assert "days_to_stage_move" in колонки
    assert "days_med" not in колонки, (
        "имя days_med читается как срок ответа поставщика, которым оно не является")
