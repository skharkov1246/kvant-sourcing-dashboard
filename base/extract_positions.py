#!/usr/bin/env python3
"""Извлечение номенклатуры из разобранных вложений в таблицу positions.

Работает по тексту, который уже лежит в base/kvant.db (file_text) — заново
ничего не скачивается.

Два режима на файл:
  * ТАБЛИЧНЫЙ — для xlsx и docx, где строки сохранены как «ячейка | ячейка».
    Ищем строку заголовков и раскладываем колонки по смыслу (наименование,
    артикул, производитель, количество, единица, цена). Так берутся цены и
    изготовители, которых построчным разбором не видно.
  * ТЕКСТОВЫЙ — для PDF: таблица в тексте не сохраняет границы ячеек, поэтому
    позиция собирается регулярными выражениями из строки. Без него терялся
    самый большой ресурс корпуса: 4 979 разобранных PDF на 31 тыс. страниц,
    из которых прежняя версия не доставала ни одной позиции.

Дубли: 46 % вложений — копии одного файла, поэтому обрабатывается один файл
на каждый sha1, а позиции привязываются ко всем сделкам, где эта копия лежит.

    python base/extract_positions.py --db base/kvant.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter
from pathlib import Path

# Заголовки колонок спецификаций. Формы у всех заказчиков свои, слова повторяются.
COLS = {
    "name": ["наименование", "номенклатура", "описание", "предмет", "позиция", "материал",
             "запчаст", "description", "item", "наимен", "товар", "продукц"],
    "pn": ["артикул", "парт", "part", "p/n", "обозначение", "каталожн", "код", "№ по каталогу",
           "item no", "ref", "материал №"],
    "oem": ["производител", "изготовител", "бренд", "марка", "oem", "завод", "manufacturer", "make"],
    "qty": ["кол-во", "количество", "кол.", "qty", "quantity", "объем", "объём"],
    "unit": ["ед.изм", "ед. изм", "единица", "unit", "ед-ца", "ед.", "изм."],
    "price": ["цена", "стоимость", "price", "сумма", "amount", "cost", "тариф"],
}
CUR = re.compile(r"\b(RUB|EUR|USD|CNY|руб|евро|₽|€|\$|¥)\b", re.I)
NUM = re.compile(r"^-?\d[\d  ]*([.,]\d+)?$")
PN_TXT = re.compile(r"(?<![\w/-])((?=[A-Za-z0-9._/-]*\d)[A-Z0-9][A-Za-z0-9._/-]{4,26})(?![\w-])")
QTY_TXT = re.compile(r"(\d{1,6}(?:[.,]\d{1,3})?)\s*(шт|шт\.|компл|к-т|pcs|pc|ea|set|м|кг|л|уп)\b", re.I)
PRICE_TXT = re.compile(r"(\d[\d  ]{2,}(?:[.,]\d{2})?)\s*(руб|₽|eur|€|usd|\$|cny|¥)", re.I)
DATEISH = re.compile(r"^\d{2,4}[-./]\d{1,2}([-./]\d{1,4})?$|^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}$")
STOP = {"итого", "всего", "total", "ндс", "vat", "sum", "сумма", "п/п", "№", "no", "num", "подпись"}
BOILER = re.compile(r"(?i)(дней от даты|срок действия|условия оплат|грузополучател|реквизит|"
                    r"в соответствии с|приложение №|подпис|печат|гарантийн\w+ срок|"
                    r"валюта контракта|инкотермс|incoterms|выбираем|заполняем|переносим)")
# адреса и реквизиты сторон: в текстовом режиме их индексы и КПП выглядят как артикулы
ADDR = re.compile(r"(?i)(инн|кпп|огрн|р/с|к/с|бик|юридическ\w+ адрес|почтовый адрес|"
                  r"г\.\s?[А-Я]|ул\.|пер\.|область|респ\b|район|снип|сп \d|гост|тр тс|"
                  r"покупатель|поставщик:|заказчик:|банк)")
MAX_ROWS_PER_FILE = 4000

# Поля карточки, где лежит номенклатура. Остальные — шаблон «экономики проекта»,
# вложения бота и файлы тендерной площадки: позиций там нет, а мусора много.
GOOD_FIELDS = (
    "Техническая спецификация", "Offer from supplier(s)",
    "Offer from supplier (Техническое поле.Заполняется автоматически)",
    "Offer from us", "Customer request for automatic processing",
    "Technical data from customer", "Processed file for supplier", "Result file",
    "(старое) Result of automatic request processing",
)

# Реквизиты и ссылки на нормативы, которые выглядят как артикулы. Без этого верх
# списка «самых частых артикулов» занимают ИНН КВАНТа, расчётный счёт и ГОСТы.
JUNK_PN = re.compile(
    r"^(?:\d{20}"                      # расчётный и корреспондентский счёт
    r"|\d{10}|\d{12,13}"               # ИНН и ОГРН
    r"|\d{4,5}-\d{2,4}"                # ГОСТ 33259-2015
    r"|\d{3}/\d{4}"                    # ТР ТС 010/2011
    r"|\d{1,2}\.\d{1,2}\.?"            # пункт договора 12.3.
    r"|[78]\d{10}"                     # телефон
    r"|20\d\d|19\d\d"                  # год
    r")$")


def _num(s: str) -> float | None:
    s = str(s).replace(" ", " ").strip()
    if not NUM.match(s):
        return None
    try:
        return float(s.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def header_map(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Строка заголовков — та, где нашлось хотя бы два известных названия колонок."""
    for i, row in enumerate(rows[:40]):
        low = [c.lower() for c in row]
        found: dict[str, int] = {}
        for key, words in COLS.items():
            for j, cell in enumerate(low):
                if cell and any(w in cell for w in words):
                    found.setdefault(key, j)
                    break
        if "name" in found and len(found) >= 2:
            return i, found
    return -1, {}


UNITS = {"шт", "шт.", "штук", "pcs", "pc", "ea", "к-т", "компл", "компл.", "set", "sets",
         "м", "m", "кг", "kg", "л", "l", "уп", "м2", "м3", "пар", "пара", "т", "мм"}


def parse_cells(cells: list[str]) -> dict | None:
    """Позиция из строки таблицы ПО СОДЕРЖИМОМУ ячеек, а не по номерам колонок.

    Разбор по заголовку здесь не работает: при извлечении текста из xlsx пустые
    ячейки выброшены, поэтому строки короче заголовка и индексы колонок разъезжаются.
    Признаки надёжнее: артикул — короткий токен с цифрами и буквами; количество —
    небольшое число рядом с единицей измерения; цена — число с копейками или
    заметно большее количества; наименование — самая длинная словесная ячейка.
    """
    cells = [c.strip() for c in cells if c and c.strip()]
    if len(cells) < 2:
        return None
    joined = " ".join(cells)
    if len(joined) < 8 or BOILER.search(joined):
        return None
    nums = [(i, _num(c)) for i, c in enumerate(cells)]
    nums = [(i, v) for i, v in nums if v is not None]
    unit = next((c.lower() for c in cells if c.lower() in UNITS), None)
    qty = None
    if unit is not None:
        j = next(i for i, c in enumerate(cells) if c.lower() == unit)
        near = [v for i, v in nums if abs(i - j) <= 2 and 0 < v < 1_000_000]
        qty = min(near) if near else None

    # артикул: короткий токен без пробелов, с цифрами; запоминаем его ячейку,
    # иначе то же число уходит и в цену — каталожный номер превращается в рубли
    pn, pn_idx = None, -1
    for i, c in enumerate(cells):
        tok = c.strip()
        if not (4 <= len(tok) <= 32) or DATEISH.match(tok) or tok.lower() in STOP:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/\-]{3,31}", tok):
            continue
        if sum(ch.isdigit() for ch in tok) < 2:
            continue
        if tok.replace(".", "").isdigit() and not (5 <= len(tok.replace(".", "")) <= 14):
            continue
        if JUNK_PN.match(tok):                      # реквизиты и номера нормативов — не артикулы
            continue
        pn, pn_idx = tok, i
        break

    price = None
    cand = [(i, v) for i, v in nums
            if v and v >= 100 and v != qty and i != pn_idx and v < 1e9]
    if cand:
        # цена с копейками надёжнее круглого числа: круглым чаще оказывается код
        dec = [v for i, v in cand if abs(v - round(v)) > 1e-9]
        price = max(dec) if dec else max(v for _i, v in cand)
    words = [c for c in cells if len(c) >= 6 and sum(ch.isalpha() for ch in c) >= 4]
    name = max(words, key=len) if words else None
    if not name or (not pn and qty is None):
        return None
    m = CUR.search(joined)
    return {"name": name[:300], "part_number": (pn or None), "manufacturer": None,
            "qty": qty, "unit": unit, "price": price,
            "currency": (m.group(1).upper() if m else None), "raw": joined[:500]}


def from_table(text: str) -> list[dict]:
    out: list[dict] = []
    for ln in text.split("\n"):
        if "|" not in ln:
            continue
        rec = parse_cells(ln.split("|"))
        if rec:
            out.append(rec)
        if len(out) >= MAX_ROWS_PER_FILE:
            break
    return out


def from_text(text: str) -> list[dict]:
    """Строка PDF: собираем позицию по признакам — артикул, количество с единицей, цена."""
    out: list[dict] = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line) < 12 or len(line) > 400 or BOILER.search(line):
            continue
        if ADDR.search(line):
            continue
        q = QTY_TXT.search(line)
        p = PRICE_TXT.search(line)
        pn = None
        for m in PN_TXT.finditer(line):
            tok = m.group(1)
            if DATEISH.match(tok) or tok.lower() in STOP or JUNK_PN.match(tok):
                continue
            if sum(ch.isdigit() for ch in tok) >= 3:
                pn = tok
                break
        # в тексте PDF строка считается позицией только при количестве с единицей
        # или явной цене: одного похожего на артикул токена мало — так в таблицу
        # попадали почтовые индексы, КПП и номера нормативов
        if not (q or p):
            continue
        name = re.sub(r"\s{2,}", " ", line)
        if pn:
            name = name.replace(pn, " ").strip()
        name = re.sub(r"^[\s|·•\-–—.]+", "", name)[:300]
        if len(name) < 6:
            continue
        cur = None
        if p:
            cm = CUR.search(p.group(2))
            cur = cm.group(1).upper() if cm else None
        out.append({"name": name, "part_number": pn, "manufacturer": None,
                    "qty": _num(q.group(1)) if q else None, "unit": (q.group(2) if q else None),
                    "price": _num(p.group(1)) if p else None, "currency": cur,
                    "raw": line[:500]})
        if len(out) >= MAX_ROWS_PER_FILE:
            break
    return out


def run(db_path: str, limit: int | None = None) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    con.execute("DELETE FROM positions")
    # один файл на каждый sha1: 46 % вложений — копии
    marks = ",".join("?" * len(GOOD_FIELDS))
    files = con.execute(f"""SELECT MIN(f.fid), f.sha1, f.ext, group_concat(DISTINCT f.deal_id)
                            FROM files f
                            WHERE f.status='parsed' AND f.sha1!='' AND f.field_name IN ({marks})
                            GROUP BY f.sha1""", GOOD_FIELDS).fetchall()
    stats = Counter()
    buf: list[tuple] = []
    for fid, _sha, ext, deal_ids in files:
        if limit and stats["files"] >= limit:
            break
        stats["files"] += 1
        text = "".join(r[0] or "" for r in con.execute(
            "SELECT text FROM file_text WHERE fid=? ORDER BY part", (fid,)))
        if not text:
            continue
        rows = from_table(text) if "|" in text[:20000] else from_text(text)
        if not rows:
            rows = from_text(text)
        if not rows:
            continue
        stats["files_with_rows"] += 1
        for d in str(deal_ids or "").split(",")[:5]:      # копия может лежать в нескольких сделках
            if not d.strip().isdigit():
                continue
            for r in rows:
                buf.append((int(d), fid, None, r["raw"], r["part_number"], r["manufacturer"],
                            r["name"], r["qty"], r["unit"], r["price"], r["currency"], f"file:{ext}"))
                stats["positions"] += 1
                if r["price"]:
                    stats["with_price"] += 1
        if len(buf) > 20000:
            con.executemany("""INSERT INTO positions
                (deal_id, fid, seg, raw, part_number, manufacturer, name, qty, unit, price, currency, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", buf)
            con.commit()
            buf = []
    if buf:
        con.executemany("""INSERT INTO positions
            (deal_id, fid, seg, raw, part_number, manufacturer, name, qty, unit, price, currency, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", buf)
    con.commit()
    con.execute("UPDATE positions SET seg=(SELECT seg FROM deals WHERE deals.id=positions.deal_id)")
    con.commit()
    con.close()
    return dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    st = run(a.db, a.limit)
    print(f"уникальных файлов обработано {st.get('files', 0)}, с позициями {st.get('files_with_rows', 0)}, "
          f"позиций {st.get('positions', 0)}, из них с ценой {st.get('with_price', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
