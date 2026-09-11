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

Дубли: 32 % разобранных вложений — копии одного файла (27 940 из 87 485),
поэтому обрабатывается один файл
на каждый sha1, а позиции привязываются ко всем сделкам, где эта копия лежит.

    python base/extract_positions.py --db base/kvant.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
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
# Валюта строки распознаётся редко: в таблице её пишут один раз в шапке или в
# названии колонки. Без валюты цены несравнимы — отношение «наша к поставщику»
# оказывается курсом, а не наценкой. Поэтому валюта определяется по документу.
CUR_MAP = {"RUB": "RUB", "РУБ": "RUB", "₽": "RUB", "RUR": "RUB", "РУБЛ": "RUB",
           "EUR": "EUR", "ЕВРО": "EUR", "€": "EUR",
           "USD": "USD", "$": "USD", "ДОЛЛАР": "USD",
           "CNY": "CNY", "¥": "CNY", "ЮАН": "CNY", "RMB": "CNY"}
CUR_DOC = re.compile(r"(RUB|RUR|РУБЛ\w*|РУБ\.?|₽|EUR|ЕВРО|€|USD|ДОЛЛАР\w*|\$|CNY|RMB|ЮАН\w*|¥)", re.I)


def norm_cur(tok: str | None) -> str | None:
    """Код валюты из того, как её написали в строке: «евро», «€», «руб» → EUR, RUB.

    Без этого в базе соседствуют EUR и ЕВРО, RUB и РУБ — сравнение цен по валюте
    молча пропускает часть позиций."""
    if not tok:
        return None
    tok = tok.upper()
    for k, v in CUR_MAP.items():
        if tok.startswith(k):
            return v
    return None


# Производитель в спецификации пишется отдельной ячейкой или внутри наименования,
# но заголовка «производитель» в тексте уже нет: пустые ячейки xlsx выброшены.
# Поэтому опознаём по словарю марок. Источник — справочник портала, смарт-процесс
# 176 «Brands» (2 449 записей): он не зависит от того, чем наполнены карточки.
# Поле «Brands» у сделок оставлено запасным вариантом — после пересборки снимка
# оно пустует, и словарь из него не собирается вовсе.
BRANDS: dict[str, str] = {}
BRAND_RE: re.Pattern | None = None          # марки от четырёх знаков, регистр не важен
BRAND_SHORT_RE: re.Pattern | None = None    # ABB, SKF, MAN — только заглавными,
                                            # иначе «MAN» ловит английское «man»
SHORT_LEN = 3
# Марки, совпадающие с обычным словом спецификации. «Seal» дал 3 806 ложных
# срабатываний на строках вида «LABYRINTH SEAL 200-R», «Total» — на итоговых
# строках таблиц («Total EXW Price», «Итого»).
# Часть записей справочника — «Getriebebau Nord, ГЕРМАНИЯ»: марка со страной через
# запятую. Разбор по запятой делает из страны отдельную марку, и она садится на
# каждую строку с указанием происхождения — поэтому страны отсеиваются отдельно.
COUNTRY_WORDS = {
    "германия", "россия", "китай", "италия", "сша", "япония", "франция", "швеция",
    "финляндия", "австрия", "швейцария", "испания", "индия", "корея", "южная корея",
    "турция", "чехия", "польша", "великобритания", "англия", "нидерланды", "бельгия",
    "дания", "норвегия", "канада", "бразилия", "тайвань", "сингапур", "вьетнам",
    "соединенные штаты америки", "соединённые штаты америки", "united states",
    "united states of america", "germany", "russia", "china", "italy", "usa", "japan",
    "france", "sweden", "finland", "austria", "switzerland", "spain", "india", "korea",
    "turkey", "czech republic", "poland", "united kingdom", "netherlands", "belgium",
}
BRAND_STOP = {"новый", "прочее", "другое", "разные", "нет", "оригинал", "аналог",
              "россия", "seal", "total", "value",
              # слова из описания позиции, заведённые в справочнике портала как
              # марки: «Test» цеплялся к «Test port» и «Material Test Certificate»
              # и выходил на 118 сделок, «Bush» — к втулке, «Ltd» — к любому
              # английскому названию компании
              "test", "bush", "ltd", "advance", "профиль",
              # материал уплотнения, а не поставщик строки
              "viton",
              # в справочнике портала эти записи заведены наравне с марками, но
              # маркой не являются: «ГОСТ» иначе цепляется к любой ссылке на стандарт
              "гост", "ост", "бренд", "бренд отсутствует", "марка", "отсутствует",
              "без бренда", "не определён", "не определен", "уточняется"}


def _fold(s: str) -> str:
    """Ту же марку в спецификациях пишут без диакритики: Wärtsilä → Wartsila,
    Dräger → Drager. Без свёртки такие строки остаются без производителя."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def brand_source(con: sqlite3.Connection) -> list[str]:
    """Сырые названия марок: справочник портала, иначе поле «Brands» у сделок."""
    try:
        rows = [r[0] for r in con.execute("SELECT title FROM brands WHERE title IS NOT NULL AND title<>''")]
        if rows:
            return rows
    except sqlite3.OperationalError:
        pass                                  # справочник ещё не выгружен
    return [r[0] for r in con.execute(
        "SELECT DISTINCT brand FROM deals WHERE brand IS NOT NULL AND brand<>''")]


def load_brands(con: sqlite3.Connection) -> None:
    global BRANDS, BRAND_RE, BRAND_SHORT_RE
    canon_by_key: dict[str, str] = {}
    for b in brand_source(con):
        for part in re.split(r"[,;/]| и ", b):
            name = part.strip(" .«»\"'()")
            low = name.lower()
            if len(name) < 3 or low in BRAND_STOP or low in COUNTRY_WORDS:
                continue
            if not re.search(r"[A-Za-zа-яА-Я]{3}", name):
                continue
            # одна марка заведена в сделках и как «WILO», и как «Wilo»: в базу
            # позиций должно попасть одно написание, иначе марка двоится
            key = name.lower()
            best = canon_by_key.get(key)
            if best is None or (best.isupper() and not name.isupper()):
                canon_by_key[key] = name
    canon: dict[str, str] = {}
    for name in canon_by_key.values():
        canon[name] = name
        folded = _fold(name)
        if folded != name:
            canon[folded] = name
    BRANDS = canon
    long_names = [b for b in canon if len(b) > SHORT_LEN]
    short_names = [b for b in canon if len(b) <= SHORT_LEN]
    if long_names:
        BRAND_RE = re.compile(r"(?<![A-Za-zа-яА-Я0-9])(" +
                              "|".join(re.escape(b) for b in sorted(long_names, key=len, reverse=True)) +
                              r")(?![A-Za-zа-яА-Я0-9])", re.I)
    if short_names:
        BRAND_SHORT_RE = re.compile(r"(?<![A-Za-zа-яА-Я0-9])(" +
                                    "|".join(re.escape(b.upper()) for b in sorted(short_names)) +
                                    r")(?![A-Za-zа-яА-Я0-9])")


def find_brand(text: str) -> str | None:
    """Каноническое написание марки, как она заведена в справочнике портала."""
    m = BRAND_RE.search(text) if BRAND_RE else None
    if not m and BRAND_SHORT_RE:
        m = BRAND_SHORT_RE.search(text)
    if not m:
        return None
    hit = m.group(1)
    return BRANDS.get(hit) or next((v for k, v in BRANDS.items() if k.lower() == hit.lower()), hit)


def doc_currency(text: str) -> str | None:
    """Валюта документа — самая частая из встреченных в первых страницах текста."""
    hits: Counter = Counter()
    for m in CUR_DOC.finditer(text[:40_000]):
        tok = m.group(1).upper()
        for k, v in CUR_MAP.items():
            if tok.startswith(k):
                hits[v] += 1
                break
    if not hits:
        return None
    top, n = hits.most_common(1)[0]
    return top if n >= 2 else None
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
    # поля карточки сделки
    "Техническая спецификация", "Offer from supplier(s)",
    "Offer from supplier (Техническое поле.Заполняется автоматически)",
    "Offer from us", "Customer request for automatic processing",
    "Technical data from customer", "Processed file for supplier", "Result file",
    "(старое) Result of automatic request processing",
    # поля карточки запроса поставщику (смарт-процесс 166). Называются иначе, и
    # без них не разбиралось 4 854 оферты на 32,4 млн знаков — ровно та сторона,
    # где лежит цена поставщика на конкретный артикул.
    "Offer from supplier", "КП поставщика", "Offer, old", "Processed offer",
    "Processed offer with descriptions / archive", "Request file",
)

# Реквизиты и ссылки на нормативы, которые выглядят как артикулы. Без этого верх
# списка «самых частых артикулов» занимают ИНН КВАНТа, расчётный счёт и ГОСТы.
JUNK_PN = re.compile(
    r"^(?:\d{20}"                      # расчётный и корреспондентский счёт
    r"|\d{10}|\d{12,13}"               # ИНН и ОГРН
    r"|\d{4,5}-\d{2,4}"                # ГОСТ 33259-2015
    r"|\d{3}/\d{4}"                    # ТР ТС 010/2011
    r"|[78]\d{10}"                     # телефон
    r"|20\d\d|19\d\d"                  # год
    r")$")
# Технический параметр по форме неотличим от артикула, и именно параметры лезут
# в верх любого частотного отчёта: «0x0x0» стоял в 385 сделках, «230/400» в 34,
# «7.2.9.» в 20. Настоящие коды запчастей сидят в хвосте, поэтому параметры надо
# отсеивать по форме, иначе картина спроса переворачивается.
PARAM_PN = [
    re.compile(r"^\d+(?:\.\d+)+\.?$"),                   # пункт инструкции, версия
    re.compile(r"^\d+(?:[.,]\d+)?\s*-\s*\d+(?:[.,]\d+)?$"),   # диапазон 0-1.6
    re.compile(r"^\d+[xх*]\d+(?:[xх*]\d+)?$", re.I),      # габарит 100x200x30
    re.compile(r"^IP\d{2}$", re.I),                        # степень защиты
    re.compile(r"^\d+[A-Za-zА-Яа-я]{1,4}$"),               # 60days, 12mm
    re.compile(r"^\d{2,4}/\d{2,4}$"),                      # напряжение 230/400
    re.compile(r"^(?:DN|PN|ДУ|РУ)\s*\d+$", re.I),          # условный проход и давление
    re.compile(r"^0+$"),
]


# Строки бланков: «Форма по ОКУД 0335001» неотличима от семизначного каталожного
# номера, но встречается в двадцати сделках подряд — это шапка типовой формы М-15.
FORM_LINE = re.compile(r"\b(ОКУД|ОКПО|ОКТМО|ОКАТО|Форма\s+по)\b", re.I)


def junk_pn(tok: str) -> bool:
    """Похоже ли на реквизит или технический параметр, а не на артикул."""
    if JUNK_PN.match(tok):
        return True
    if any(p.match(tok) for p in PARAM_PN):
        return True
    if tok.isdigit():
        # у кодов запчастей длина 6-12 знаков: «12000» — это количество или
        # мощность, а круглое число никогда не бывает каталожным номером
        if not (6 <= len(tok) <= 12):
            return True
        if re.fullmatch(r"\d+?0{3,}", tok):
            return True
        # «00000016» — порядковый номер строки в форме 1С: у каталожных номеров
        # столько ведущих нулей не бывает
        if re.match(r"^0{4,}", tok):
            return True
    return False


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
    form_line = bool(FORM_LINE.search(joined))
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
        if junk_pn(tok) or form_line:                # реквизиты, нормативы и параметры — не артикулы
            continue
        pn, pn_idx = tok, i
        break

    # цена за единицу и сумма строки различаются по связи с количеством:
    # если одно число, умноженное на количество, даёт другое — это цена и сумма.
    # Без такого разделения сравнение «цена поставщика против нашей» бессмысленно:
    # в паре оказываются цена за штуку у одного и сумма позиции у другого.
    price = price_total = None
    cand = [v for i, v in nums if v and v >= 1 and v != qty and i != pn_idx and v < 1e9]
    if qty and qty > 0:
        for u in cand:
            for t in cand:
                if t > u and abs(u * qty - t) <= max(1.0, 0.02 * t):
                    price, price_total = u, t
                    break
            if price is not None:
                break
    if price is None and cand:
        big = [v for v in cand if v >= 100]
        dec = [v for v in big if abs(v - round(v)) > 1e-9]
        price = max(dec) if dec else (max(big) if big else None)
    words = [c for c in cells if len(c) >= 6 and sum(ch.isalpha() for ch in c) >= 4]
    name = max(words, key=len) if words else None
    if not name or (not pn and qty is None):
        return None
    m = CUR.search(joined)
    return {"name": name[:300], "part_number": (pn or None), "manufacturer": find_brand(joined),
            "qty": qty, "unit": unit, "price": price, "price_total": price_total,
            "currency": norm_cur(m.group(1) if m else None), "raw": joined[:500]}


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
        if FORM_LINE.search(line):
            continue
        for m in PN_TXT.finditer(line):
            tok = m.group(1)
            if DATEISH.match(tok) or tok.lower() in STOP or junk_pn(tok):
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
            cur = norm_cur(cm.group(1) if cm else None)
        out.append({"name": name, "part_number": pn, "manufacturer": find_brand(line),
                    "qty": _num(q.group(1)) if q else None, "unit": (q.group(2) if q else None),
                    "price": _num(p.group(1)) if p else None, "price_total": None,
                    "currency": cur, "raw": line[:500]})
        if len(out) >= MAX_ROWS_PER_FILE:
            break
    return out


def run(db_path: str, limit: int | None = None) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    con.execute("DELETE FROM positions")
    load_brands(con)
    print(f"брендов в словаре: {len(BRANDS)}", flush=True)
    # один файл на каждый sha1: копией является каждый третий
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
        doc_cur = doc_currency(text)
        if doc_cur:
            for r in rows:
                if not r.get("currency"):
                    r["currency"] = doc_cur
        stats["files_with_rows"] += 1
        for d in str(deal_ids or "").split(",")[:5]:      # копия может лежать в нескольких сделках
            if not d.strip().isdigit():
                continue
            for r in rows:
                buf.append((int(d), fid, None, r["raw"], r["part_number"], r["manufacturer"],
                            r["name"], r["qty"], r["unit"], r["price"], r.get("price_total"),
                            r["currency"], f"file:{ext}"))
                stats["positions"] += 1
                if r["price"]:
                    stats["with_price"] += 1
        if len(buf) > 20000:
            con.executemany("""INSERT INTO positions
                (deal_id, fid, seg, raw, part_number, manufacturer, name, qty, unit, price,
                 price_total, currency, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", buf)
            con.commit()
            buf = []
    if buf:
        con.executemany("""INSERT INTO positions
            (deal_id, fid, seg, raw, part_number, manufacturer, name, qty, unit, price,
             price_total, currency, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", buf)
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
