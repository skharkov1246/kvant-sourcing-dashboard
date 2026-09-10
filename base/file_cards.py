#!/usr/bin/env python3
"""Карточка на каждый документ: что это за файл, чей он и о чём.

Зачем. В базе 87 485 разобранных вложений на 1,65 млрд знаков. Полнотекстовый
поиск по ним есть, но ориентироваться в них нельзя: строка таблицы files знает
только имя файла и размер. На вопросы «сколько у нас коммерческих предложений
поставщиков», «где закупочная документация по проигранным сделкам», «в каких
файлах фигурирует ИНН этого завода» база не отвечала — приходилось грепать
полтора миллиарда знаков.

Модуль читает начало каждого документа и складывает по строке на документ:
род документа, сторона (заказчик / мы / поставщик), язык, даты внутри текста,
валюта, ИНН упомянутых юрлиц, число позиций и цен из positions, марки, а также
исход сделки, к которой документ приложен. Это и есть тот срез, который
выгружается в базу знаний.

Дубли. 46 % вложений — копии: одно и то же ТЗ лежит в десятке сделок. Карточка
заводится на содержимое (sha1), а сделки, поля и имена копий сносятся в неё
списком. 87 485 вложений сворачиваются в 59 545 документов.

    python base/file_cards.py --db base/kvant.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HEAD = 6000          # столько знаков от начала документа хватает на шапку и тип
TITLE_MAX = 200

# ── сторона: кто автор документа ───────────────────────────────────────────
# Поле карточки говорит о происхождении файла надёжнее любого содержимого:
# «Offer from us» — наше КП, даже если внутри оно называется «предложение».
SIDE_BY_FIELD = {
    "Техническая спецификация": "заказчик",
    "Customer request for automatic processing": "заказчик",
    "Technical data from customer": "заказчик",
    "Documents, Bot": "заказчик",
    "Request file": "заказчик",
    "Specification (file)": "заказчик",
    "Offer from us": "мы",
    "Result, ТКП": "мы",
    "Result file": "мы",
    "Образец ТКП": "мы",
    "(старое) Result of automatic request processing": "мы",
    "Processed file for supplier": "мы",
    "Offer from supplier(s)": "поставщик",
    "Offer from supplier": "поставщик",
    "Offer from supplier (Техническое поле.Заполняется автоматически)": "поставщик",
    "КП поставщика": "поставщик",
    "Supplier offer": "поставщик",
    "Offer, old": "поставщик",
    "Processed offer": "поставщик",
    "Processed offer with descriptions / archive": "поставщик",
    "Agreement with supplier": "поставщик",
    "Order confirmation": "поставщик",
    "Economics of the project": "внутренний",
    "Результат сравнения предложений поставщиков": "внутренний",
    "Result of search for manufacturer and type, Perplexity": "внутренний",
    "Bank Details": "внутренний",
    "Delivery agreement (with the client)": "заказчик",
    "Scan of the signed contract": "заказчик",
    "Мануал, чертеж, шильд": "заказчик",
}

# ── род документа ──────────────────────────────────────────────────────────
# Порядок проверок важен: сначала узкие роды (счёт, сертификат), потом широкие
# (спецификация, оферта). Иначе договор поставки с приложением-спецификацией
# уедет в спецификации, а счёт-фактура — в финансовые документы вообще.
KIND_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    # (род, слова в имени файла, слова в тексте)
    ("сравнение", ("suppliers_", "сравнение предложен", "сводная таблица"),
     ("сводка по поставщикам",)),
    ("реквизиты", ("реквизит", "bank details", "карточка предприятия", "карточка орг"),
     ("банковские реквизиты", "расчетный счет", "корреспондентский счет", "к/с ")),
    ("доверенность", ("доверенн", "power of attorney"), ("доверенность",)),
    ("счёт-акт", ("счет", "счёт", "invoice", "упд", "торг-12", "торг12", "накладн", "акт "),
     ("счет на оплату", "счёт на оплату", "счет-фактура", "универсальный передаточный",
      "товарная накладная", "акт приема-передачи", "акт выполненных работ", "к оплате")),
    ("сертификат", ("сертификат", "деклараци", "паспорт", "certificate", "коррупц", "gost"),
     ("сертификат соответствия", "декларация о соответствии", "паспорт качества",
      "свидетельство о", "certificate of")),
    ("договор", ("договор", "контракт", "contract", "соглашен", "agreement", "дог_", "_дог",
                 "оговорк", "антикоррупц", "коррупц"),
     ("именуемое в дальнейшем", "именуемый в дальнейшем", "настоящий договор",
      "договор поставки", "предмет договора", "the seller", "the buyer")),
    ("тендер", ("извещен", "закупочн", "документация", "тендер", "аукцион", "конкурс",
                "лот", "protokol", "протокол", "инструкц", "rfx", "rfq", "процедур"),
     ("извещение о проведении", "закупочная документация", "аукционная документация",
      "запрос предложений", "запрос котировок", "44-фз", "223-фз", "единая информационная",
      "участник закупки", "начальная (максимальная) цена")),
    ("прайс", ("прайс", "price list", "pricelist"), ("прайс-лист", "price list")),
    ("чертёж-мануал", ("чертеж", "чертёж", "drawing", "шильд", "мануал", "manual",
                       "инструкц по экспл", "схема", "габарит"),
     ("руководство по эксплуатации", "инструкция по эксплуатации", "сборочный чертеж")),
    ("оферта", ("кп ", "кп_", "ткп", "коммерческое", "предложен", "offer", "quotation",
                "quote", "оферт", "proposal", "angebot"),
     # «срок поставки» и «условия оплаты» в признаки не годятся: они есть в каждом
     # техническом задании заказчика, и по ним ТЗ уезжали в оферты
     ("коммерческое предложение", "технико-коммерческое предложение", "мы предлагаем",
      "предлагаем вам", "our offer", "we offer", "срок действия предложения",
      "quotation", "validity of offer")),
    ("спецификация", ("специф", "тз ", "тз_", "техническое задание", "опросн", "перечень",
                      "потребност", "заявка", "форма тп", "form", "ол ", "ол_",
                      "техническ", "requirement", "scope of supply", "bom", "list"),
     ("техническое задание", "опросный лист", "техническая спецификация",
      "перечень оборудования", "наименование товара", "требуемые технические")),
    ("письмо", ("письмо", "letter", ".eml", ".msg", "запрос"),
     ("уважаем", "просим вас", "направляем в ваш адрес", "dear ")),
]
KIND_BY_FIELD = {
    "Economics of the project": "экономика",
    "Bank Details": "реквизиты",
    "Delivery agreement (with the client)": "договор",
    "Agreement with supplier": "договор",
    "Scan of the signed contract": "договор",
    "Order confirmation": "подтверждение заказа",
    "Мануал, чертеж, шильд": "чертёж-мануал",
    "Result of search for manufacturer and type, Perplexity": "справка",
    "Результат сравнения предложений поставщиков": "сравнение",
}
# Поля, где род документа задан самим полем и содержимое его не переспорит:
# файл в «Offer from supplier» — оферта, даже если внутри одна таблица без слов.
KIND_HINT_FIELD = {
    "Offer from supplier(s)": "оферта", "Offer from supplier": "оферта",
    "Offer from supplier (Техническое поле.Заполняется автоматически)": "оферта",
    "КП поставщика": "оферта", "Supplier offer": "оферта", "Offer, old": "оферта",
    "Processed offer": "оферта", "Offer from us": "оферта", "Result, ТКП": "оферта",
    "Образец ТКП": "оферта",
    "Техническая спецификация": "спецификация",
    "Customer request for automatic processing": "спецификация",
    "Technical data from customer": "спецификация",
    "Specification (file)": "спецификация",
    "Documents, Bot": "тендер",
}

INN = re.compile(r"ИНН[\s:№]*([0-9]{10,12})")
DATE1 = re.compile(r"\b([0-3]?\d)[.\-/]([01]?\d)[.\-/](20[12]\d)\b")
DATE2 = re.compile(r"\b(20[12]\d)-([01]\d)-([0-3]\d)\b")
CUR_TOK = re.compile(r"(RUB|RUR|РУБЛ\w*|РУБ\.|₽|EUR|ЕВРО|€|USD|ДОЛЛАР\w*|\$|CNY|RMB|ЮАН\w*|¥)", re.I)
CUR_NORM = {"RUB": "RUB", "RUR": "RUB", "РУБ": "RUB", "₽": "RUB",
            "EUR": "EUR", "ЕВР": "EUR", "€": "EUR",
            "USD": "USD", "ДОЛ": "USD", "$": "USD",
            "CNY": "CNY", "RMB": "CNY", "ЮАН": "CNY", "¥": "CNY"}
ORG = re.compile(r'(?<![А-Яа-яA-Za-z])(?:ООО|ПАО|ЗАО|ОАО|АО)\s*[«"\']?([А-ЯЁA-Z][^«»"\'\n,;|]{2,40})')
# ── коммерческие условия ───────────────────────────────────────────────────
# Условия — то, ради чего документ читают целиком: срок поставки, аванс,
# отсрочка, гарантия, штраф. Их вытаскиваем отдельно, потому что в поиске они
# тонут: слово «гарантия» встречается в каждом втором договоре, а нужно число.
COND = {
    "delivery_days": re.compile(
        r"срок\w*\s+поставк\w*[^.\n]{0,80}?(\d{1,3})\s*(?:календарн\w*|рабоч\w*)?\s*(дн|недел|месяц)", re.I),
    "prepay_pct": re.compile(r"(?:предоплат\w*|аванс\w*)[^.\n]{0,60}?(\d{1,3})\s*%", re.I),
    "defer_days": re.compile(
        r"отсрочк\w*[^.\n]{0,60}?(\d{1,3})\s*(?:календарн\w*|банковск\w*|рабоч\w*)?\s*дн", re.I),
    "warranty_mo": re.compile(r"гаранти\w*[^.\n]{0,80}?(\d{1,3})\s*(мес|год|лет)", re.I),
    "penalty_pct": re.compile(r"(?:штраф\w*|пен[яи]\w*|неустойк\w*)[^.\n]{0,80}?(\d{1,2}(?:[.,]\d{1,2})?)\s*%", re.I),
}
NMCK = re.compile(r"начальн\w*\s*\(?максимальн\w*\)?\s*цен\w*[^\n]{0,100}?([\d][\d\s.,]{4,18})", re.I)
COND_SCAN = 60_000        # условия ищем глубже шапки: в договоре они в середине
GENERIC_SHEET = re.compile(r"^#+\s*лист:\s*(лист|sheet|table|таблица|стр)?\s*\d*(\.xml)?$", re.I)
BLANK_MARK = re.compile(r"(заполняется участником|заполнить|указать|_{6,}|\.{10,})", re.I)


JUNK_NAME = re.compile(r"^\s*(количество|кол-во|цена|итого|всего|примечание|№|поз)\b", re.I)
HEXISH = re.compile(r"^[0-9A-Fa-f]{16,}$")


def junk_name(nm: str) -> bool:
    """Наименование, которое ничего не говорит о содержимом файла.

    В колонку «о чём» попадали обрывки таблиц («количество: 5 шт.») и хеши
    из служебных листов 1С — по ним документ не узнать."""
    s = re.sub(r"\s+", " ", nm).strip()
    if len(re.findall(r"[А-Яа-яA-Za-z]", s)) < 4:
        return True
    return bool(JUNK_NAME.match(s) or HEXISH.match(s.replace(" ", "")))


def norm_cur(tok: str) -> str | None:
    up = unicodedata.normalize("NFKC", tok).upper()
    for k, v in CUR_NORM.items():
        if up.startswith(k):
            return v
    return None


def language(text: str) -> str:
    """ru / en / mixed — по буквам, а не по кодировке файла.

    Нужно для поиска поставщика: китайские и европейские оферты приходят
    по-английски, и отдельная колонка позволяет их отобрать одним условием."""
    cyr = sum(1 for ch in text if "А" <= ch <= "я" or ch in "ЁёІіЇїЄє")
    lat = sum(1 for ch in text if "A" <= ch <= "z" and ch.isalpha())
    tot = cyr + lat
    if tot < 30:
        return "?"
    if cyr / tot > 0.85:
        return "ru"
    if lat / tot > 0.85:
        return "en"
    return "mixed"


def doc_title(text: str) -> str:
    """Первая содержательная строка — то, как документ назвал себя сам.

    Имя файла врёт чаще: «Приложение 1 (2).pdf», «doc0001.PDF», «Копия Копия».
    Пропускаем шапки бланков (номера страниц, «Приложение №»), берём первую
    строку с буквами длиннее пяти знаков."""
    for line in text.split("\n")[:40]:
        s = re.sub(r"\s+", " ", line.replace("|", " ")).strip(" .-—_\t")
        if len(s) < 6 or len(s) > TITLE_MAX:
            continue
        # «### лист: Лист1» — служебная шапка таблицы: имя листа по умолчанию
        # ничего не говорит, а осмысленное («лист: RFQ») оставляем
        if GENERIC_SHEET.match(s):
            continue
        if not re.search(r"[А-Яа-яA-Za-z]{4}", s):
            continue
        if re.match(r"^(стр|страница|page|лист)\b", s, re.I):
            continue
        return s[:TITLE_MAX]
    return ""


def kind_of(field: str, filename: str, text: str) -> str:
    """Род документа: поле карточки, потом имя файла, потом содержимое."""
    hard = KIND_BY_FIELD.get(field)
    if hard:
        return hard
    fname = (filename or "").lower()
    inner = fname.split(" :: ")[-1]          # для вложенных — имя внутри архива
    low = text[:HEAD].lower()
    for kind, in_name, in_text in KIND_RULES:
        if any(w in inner for w in in_name):
            return kind
        if any(w in low for w in in_text):
            return kind
    return KIND_HINT_FIELD.get(field, "прочее")


def dates_in(text: str) -> tuple[str | None, str | None]:
    """Крайние даты внутри документа: когда он датирован и до какого срока годен.

    Дата файла в CRM — это дата загрузки, а не документа: тендерную
    документацию 2023 года прикладывают в 2026-м. Даты из текста дают
    настоящий возраст сведений (и срок действия цены в оферте)."""
    got = set()
    for d, m, y in DATE1.findall(text):
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            got.add(f"{y}-{int(m):02d}-{int(d):02d}")
    for y, m, d in DATE2.findall(text):
        if 1 <= int(m) <= 12 and 1 <= int(d) <= 31:
            got.add(f"{y}-{m}-{d}")
    got = {g for g in got if "2015" <= g[:4] <= "2027"}
    if not got:
        return None, None
    return min(got), max(got)


def conditions(text: str) -> dict:
    """Числа коммерческих условий из текста документа.

    Берём первое вхождение: в договоре условие сформулировано один раз, а
    дальше идут ссылки на пункт. Сроки приводим к дням, гарантию — к месяцам,
    чтобы колонку можно было сравнивать между документами."""
    out: dict[str, float | None] = dict.fromkeys(COND, None)
    for key, rx in COND.items():
        m = rx.search(text)
        if not m:
            continue
        try:
            val = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if key == "delivery_days":
            unit = m.group(2).lower()
            val *= 7 if unit.startswith("недел") else (30 if unit.startswith("месяц") else 1)
        if key == "warranty_mo":
            unit = m.group(2).lower()
            val *= 12 if unit.startswith(("год", "лет")) else 1
        out[key] = val
    m = NMCK.search(text)
    if m:
        raw = m.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")
        raw = raw.rstrip(".")
        try:
            out["nmck"] = float(raw) if raw.count(".") <= 1 else float(raw.replace(".", "", raw.count(".") - 1))
        except ValueError:
            out["nmck"] = None
    else:
        out["nmck"] = None
    return out


def run(db_path: str, limit: int | None = None) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    con.executescript("""
      DROP TABLE IF EXISTS file_cards;
      CREATE TABLE file_cards (
        sha1     TEXT PRIMARY KEY,
        fid      TEXT,          -- представитель: по нему лежит текст
        filename TEXT,
        ext      TEXT,
        bytes    INTEGER,
        pages    INTEGER,
        chars    INTEGER,
        kind     TEXT,          -- род документа
        side     TEXT,          -- чей: заказчик / мы / поставщик / внутренний
        field    TEXT,          -- поле карточки, где лежит
        title    TEXT,          -- как документ назвал себя сам
        lang     TEXT,
        copies   INTEGER,       -- сколько раз этот же файл приложен в портале
        deals    INTEGER,       -- в скольких сделках
        deal_id  INTEGER,       -- одна из них, для перехода
        rfq_id   INTEGER,       -- запрос поставщику, если файл оттуда
        supplier TEXT,
        company  TEXT,          -- заказчик сделки
        won      INTEGER,       -- исход сделки: 1 выиграна, 0 проиграна, NULL в работе
        deal_sum REAL,
        seg      TEXT,          -- сегмент оборудования
        positions INTEGER,      -- позиций номенклатуры извлечено
        priced   INTEGER,       -- из них с ценой
        brands   TEXT,
        items    TEXT,          -- о чём документ: три наименования из него
        currency TEXT,
        date_min TEXT,          -- крайние даты внутри текста
        date_max TEXT,
        inn      TEXT,          -- ИНН юрлиц, упомянутых в документе
        orgs     TEXT,          -- их названия
        blank    INTEGER,       -- 1 — незаполненный бланк
        created  TEXT,          -- когда файл появился в CRM (дата сделки)
        delivery_days REAL,     -- условия из текста: срок поставки, дней
        prepay_pct    REAL,     -- аванс, %
        defer_days    REAL,     -- отсрочка платежа, дней
        warranty_mo   REAL,     -- гарантия, месяцев
        penalty_pct   REAL,     -- штраф/пеня, %
        nmck          REAL      -- начальная (максимальная) цена договора
      );
    """)
    con.commit()

    # ── позиции по документу: считаны заранее, чтобы не ходить в таблицу
    #    построчно 60 тысяч раз
    pos = {}
    names: dict[str, list[str]] = defaultdict(list)
    # наименования берём отдельным проходом по первым позициям документа: они и
    # отвечают на вопрос «о чём файл» лучше имени файла и заголовка
    for fid, nm in con.execute("""SELECT fid, name FROM positions
                                  WHERE fid IS NOT NULL AND name IS NOT NULL AND length(name)>6"""):
        if len(names[fid]) < 3 and not junk_name(nm):
            names[fid].append(re.sub(r"\s+", " ", nm).strip()[:60])
    for fid, n, np_, brands, curs in con.execute("""
            SELECT fid, count(*), sum(price IS NOT NULL),
                   group_concat(DISTINCT manufacturer), group_concat(DISTINCT currency)
            FROM positions WHERE fid IS NOT NULL GROUP BY fid"""):
        bs = [b for b in (brands or "").split(",") if b][:8]
        cs = [c for c in (curs or "").split(",") if c]
        pos[fid] = (n, np_ or 0, ", ".join(dict.fromkeys(bs)), cs[0] if cs else None)

    rfq_of = dict(con.execute("SELECT fid, rfq_id FROM rfq_files"))
    rfq_sup = dict(con.execute("SELECT id, supplier FROM rfq"))
    deal_of = {}
    for did, comp, won, closed, sm, seg, dc in con.execute(
            "SELECT id, company, won, closed, sum_eur, seg, date_create FROM deals"):
        outcome = 1 if won else (0 if closed == "Y" else None)
        deal_of[did] = (comp, outcome, sm, seg, (dc or "")[:10])

    # ── группировка вложений по содержимому
    groups: dict[str, list] = defaultdict(list)
    for fid, deal, field, fname, ext, b, pg, ch, sha in con.execute("""
            SELECT fid, deal_id, coalesce(field_name, field), filename, ext, bytes, pages, chars, sha1
            FROM files WHERE status='parsed' AND sha1 IS NOT NULL AND sha1<>''"""):
        groups[sha].append((fid, deal, field, fname, ext, b, pg, ch))
    items = list(groups.items())
    if limit:
        items = items[:limit]
    print(f"документов уникальных: {len(items)}", flush=True)

    rows = []
    n = 0
    for sha, copies in items:
        # представитель — копия с самым длинным текстом: у одного и того же
        # файла разбор мог сорваться в одной сделке и удаться в другой
        copies.sort(key=lambda c: -(c[7] or 0))
        fid, deal, field, fname, ext, b, pg, ch = copies[0]
        full = "".join(t for t, in con.execute(
            "SELECT text FROM file_text WHERE fid=? AND part=0", (fid,)))
        text = full[:HEAD]
        cond = conditions(full[:COND_SCAN])
        field = field or ""
        kind = kind_of(field, fname, text)
        side = SIDE_BY_FIELD.get(field, "?")
        inns = list(dict.fromkeys(INN.findall(text)))[:5]
        orgs = list(dict.fromkeys(o.strip() for o in ORG.findall(text)))[:5]
        d0, d1 = dates_in(text)
        curs = Counter(c for c in (norm_cur(t) for t in CUR_TOK.findall(text)) if c)
        np_, npr, brands, pcur = pos.get(fid, (0, 0, "", None))
        deals = sorted({c[1] for c in copies if c[1]})
        comp, won, sm, seg, created = deal_of.get(deal, (None, None, None, None, None))
        rid = rfq_of.get(fid)
        rows.append((
            sha, fid, fname, ext, b, pg, ch, kind, side, field, doc_title(text),
            language(text), len(copies), len(deals), deal, rid, rfq_sup.get(rid),
            comp, won, sm, seg, np_, npr, brands,
            " · ".join(names.get(fid, [])),
            pcur or (curs.most_common(1)[0][0] if curs else None),
            d0, d1, ", ".join(inns), "; ".join(orgs)[:200],
            1 if (BLANK_MARK.search(text) and np_ == 0) else 0, created,
            cond["delivery_days"], cond["prepay_pct"], cond["defer_days"],
            cond["warranty_mo"], cond["penalty_pct"], cond["nmck"]))
        n += 1
        if len(rows) >= 2000:
            con.executemany(f"INSERT OR REPLACE INTO file_cards VALUES ({','.join('?'*38)})", rows)
            con.commit()
            rows.clear()
            print(f"  {n}/{len(items)}", flush=True)
    if rows:
        con.executemany(f"INSERT OR REPLACE INTO file_cards VALUES ({','.join('?'*38)})", rows)
    con.execute("CREATE INDEX IF NOT EXISTS ix_cards_kind ON file_cards(kind)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_cards_deal ON file_cards(deal_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_cards_side ON file_cards(side)")
    con.commit()

    stat = dict(con.execute("SELECT kind, count(*) FROM file_cards GROUP BY 1 ORDER BY 2 DESC"))
    print("\nроды документов:", flush=True)
    for k, v in stat.items():
        print(f"  {k:22} {v}", flush=True)
    con.close()
    return {"cards": n, "kinds": stat}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    run(a.db, a.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
