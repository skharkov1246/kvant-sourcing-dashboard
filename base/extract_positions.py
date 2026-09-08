#!/usr/bin/env python3
"""Извлечение номенклатуры из разобранных вложений в таблицу positions.

Работает по тексту, который уже лежит в base/kvant.db (file_text). Дешёвый
детерминированный проход: находит табличные строки спецификаций и вытаскивает
парт-номер, наименование, количество, единицу. Всё, что не разобралось
правилами, остаётся в поле raw — по нему потом проходят агенты.

Формат строк xlsx в базе — «ячейка | ячейка | …» (так их кладёт fetch_files.py),
поэтому таблица распознаётся без повторного открытия файла.

    python base/extract_positions.py --db base/kvant.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter
from pathlib import Path

# парт-номер: латиница+цифры, не короче 4 знаков, обязательно есть цифра;
# отсекаем чистые числа, даты, единицы и суммы
PN = re.compile(r"(?<![\w/-])((?=[A-Za-z0-9._/-]*\d)(?=[A-Za-z0-9._/-]*[A-Za-z0-9])[A-Z0-9][A-Za-z0-9._/-]{3,29})(?![\w-])")
QTY = re.compile(r"^\d{1,6}([.,]\d{1,3})?$")
UNITS = {"шт", "шт.", "pcs", "pc", "ea", "к-т", "компл", "компл.", "set", "sets", "м", "m",
         "кг", "kg", "л", "l", "уп", "pkg", "м2", "м3", "пар", "пара"}
NOT_PN = re.compile(r"^(20[12]\d|19\d\d|\d{1,3}[.,]\d+|[IVX]+)$")
# даты в любом виде — самый частый ложный парт-номер в спецификациях
DATEISH = re.compile(r"^\d{2,4}[-./]\d{1,2}([-./]\d{1,4})?$|^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}$")
STOP = {"итого", "всего", "total", "ндс", "vat", "sum", "сумма", "п/п", "№", "no", "num"}
# строки договорной «воды», которые лезут из тех же таблиц
BOILER = re.compile(r"(?i)(дней от даты|срок действия|условия оплат|грузополучател|реквизит|"
                    r"настоящ[ий|ая|его]|в соответствии с|приложение №|подпис|печат|"
                    r"гарантийн\w+ срок|валюта контракта|инкотермс|incoterms)")


def looks_like_pn(tok: str) -> bool:
    if len(tok) < 4 or NOT_PN.match(tok) or DATEISH.match(tok):
        return False
    if not any(ch.isdigit() for ch in tok):
        return False
    if tok.lower() in STOP:
        return False
    letters = sum(ch.isalpha() for ch in tok)
    digits = sum(ch.isdigit() for ch in tok)
    # чистое число длиной 4-12 — тоже валидный каталожный номер (например 56017080)
    return digits >= 3 and (letters > 0 or 4 <= digits <= 14)


def parse_row(cells: list[str]) -> dict | None:
    """Строка таблицы → позиция. None, если строка не похожа на позицию."""
    cells = [c.strip() for c in cells if c and c.strip()]
    if len(cells) < 2:
        return None
    low = [c.lower() for c in cells]
    if any(c in STOP for c in low[:2]) and len(cells) < 4:
        return None
    pn, name, qty, unit = "", "", None, ""
    for c in cells:
        if not pn:
            m = PN.match(c.strip())
            if m and looks_like_pn(m.group(1)) and len(c.strip()) <= 32:
                pn = m.group(1)
                continue
        if qty is None and QTY.match(c.replace(" ", "")):
            v = float(c.replace(" ", "").replace(",", "."))
            if 0 < v < 1_000_000:
                qty = v
                continue
        if c.lower() in UNITS and not unit:
            unit = c.lower()
            continue
        if len(c) > len(name) and len(c) > 5 and not QTY.match(c.replace(" ", "")):
            name = c
    if not pn and not name:
        return None
    if not pn and qty is None:
        return None
    if BOILER.search(name):                      # договорная «вода», не номенклатура
        return None
    # без парт-номера позицией считаем только строку с количеством И единицей измерения:
    # иначе в таблицу лезут инструкции из шаблонов («выбираем валюту…», «заполняем столбцы…»)
    if not pn and (qty is None or not unit or len(name) < 8):
        return None
    return {"part_number": pn, "name": name[:300], "qty": qty, "unit": unit,
            "raw": " | ".join(cells)[:500]}


def run(db_path: str, limit: int | None = None) -> dict:
    con = sqlite3.connect(db_path, timeout=120)
    con.execute("PRAGMA busy_timeout=120000")
    done = {r[0] for r in con.execute("SELECT DISTINCT fid FROM positions WHERE fid IS NOT NULL")}
    rows = con.execute("""SELECT f.fid, f.deal_id, f.ext, group_concat(t.text, '')
                          FROM files f JOIN file_text t ON t.fid = f.fid
                          WHERE f.status='parsed' GROUP BY f.fid""").fetchall()
    stats = Counter()
    out = []
    for fid, deal, ext, text in rows:
        if fid in done:
            continue
        if limit and stats["files"] >= limit:
            break
        stats["files"] += 1
        got = 0
        for line in (text or "").split("\n"):
            if "|" not in line:
                continue
            p = parse_row(line.split("|"))
            if not p:
                continue
            out.append((deal, fid, None, p["raw"], p["part_number"] or None, None,
                        p["name"] or None, p["qty"], p["unit"] or None, None, None, f"file:{ext}"))
            got += 1
        stats["positions"] += got
        if got:
            stats["files_with_positions"] += 1
        if len(out) > 5000:
            con.executemany("""INSERT INTO positions
                (deal_id, fid, seg, raw, part_number, manufacturer, name, qty, unit, price, currency, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", out)
            con.commit()
            out = []
    if out:
        con.executemany("""INSERT INTO positions
            (deal_id, fid, seg, raw, part_number, manufacturer, name, qty, unit, price, currency, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", out)
    con.commit()
    con.close()
    return dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    st = run(a.db, a.limit)
    print(f"файлов обработано {st.get('files', 0)}, из них с позициями {st.get('files_with_positions', 0)}, "
          f"позиций извлечено {st.get('positions', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
