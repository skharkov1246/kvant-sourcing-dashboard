"""Цены из писем поставщиков (indexer.MAIL_PRICES, вход prices прогона писем).

Что закреплено:
  • письмо поставщика с КП в xlsx и в PDF-таблице даёт строки цены с верной
    валютой, поставщиком (компанией письма) и привязкой к владельцу письма —
    своим потоком price_store.FEED_ПИСЬМА, а не «разбор КП»;
  • письмо заказчика и наше письмо цен не дают никогда, даже со входом;
  • без входа строк цены нет, а то, что пишется в lib_files и lib_demand, —
    то же, что со входом у файла не поставщика (вход трогает только цену);
  • вставка цены одна на все источники (CLAUDE.md, правило 14), и переразбор
    писем снимает только свой поток;
  • вход prices прогона действует только для mail-supplier и говорит об этом.

Корпус придуман (CLAUDE.md, правило 18): номера писем, компаний, файлов и
позиции выдуманы. Портал и сеть не опрашиваются.
"""
from __future__ import annotations

import functools
import importlib.util
import io
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
for каталог in ("library", "scripts"):
    sys.path.insert(0, str(ROOT / каталог))

from library import doc_side, mail_source as ms, price_store  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "library-mail.yml"
DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")

# Выдуманное КП: шапка с валютой, четыре позиции (таблица PDF читается от
# четырёх строк, pdftable.МИН_СТРОК_БЛОКА), условия под таблицей.
КП_СТРОКИ = [
    ["No", "Description", "Part No", "Qty", "Unit", "Unit Price, USD", "Amount, USD"],
    ["1", "Roller bearing VYD", "VYD-22315", "4", "pcs", "312,50", "1 250,00"],
    ["2", "Mechanical seal VYD", "VYD-4471/2", "10", "pcs", "85,00", "850,00"],
    ["3", "Spacer ring VYD", "VYD-6205", "2", "pcs", "47,25", "94,50"],
    ["4", "Rotor shaft VYD", "VYD-125/07", "1", "pcs", "12 400,00", "12 400,00"],
]
КП_PDF = [
    "                    QUOTATION No. 914 dated 18.09.2026",
    "",
    " No  Description               Part No      Qty  Unit  Unit Price, USD   Amount, USD",
    " 1   Roller bearing VYD        VYD-22315      4  pcs           312,50      1 250,00",
    " 2   Mechanical seal VYD       VYD-4471/2    10  pcs            85,00        850,00",
    " 3   Spacer ring VYD           VYD-6205       2  pcs            47,25         94,50",
    " 4   Rotor shaft VYD           VYD-125/07     1  pcs        12 400,00     12 400,00",
    "",
    " Terms of delivery: DAP Moscow.",
    " Payment terms: 30/70 - 30% in advance.",
]


def индексатор(monkeypatch, цены: bool, группа: str = "mail-supplier"):
    """Свежий модуль разбора: SOURCE и вход читаются при импорте, как в прогоне."""
    pytest.importorskip("psycopg2", reason="индексатор импортирует psycopg2")
    monkeypatch.setenv("SOURCE", "mail")
    monkeypatch.setenv("MAIL_GROUP", группа)
    if цены:
        monkeypatch.setenv("MAIL_PRICES", "1")
    else:
        monkeypatch.delenv("MAIL_PRICES", raising=False)
    spec = importlib.util.spec_from_file_location(
        f"indexer_mail_prices_{int(цены)}_{группа}", ROOT / "library" / "indexer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "bx", lambda m, _p: {"result": []})
    return mod


# ОДНИ БАЙТЫ НА ВЕСЬ ПРОГОН. openpyxl пишет в docProps время создания книги, и
# две сборки по разные стороны секунды дают разные sha256 и размер: сравнение
# «с входом и без» краснело на ровном месте (поймано 26.09.2026 в preflight).
@functools.lru_cache(maxsize=1)
def xlsx_кп() -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Коммерческое предложение № 914 от 18.09.2026"])
    ws.append([])
    for r in КП_СТРОКИ:
        ws.append(r)
    ws.append([])
    ws.append(["Условия поставки: DAP Москва. Условия оплаты: 30/70."])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def pdf_кп() -> bytes:
    pytest.importorskip("pypdf")
    from tests.test_offer_end_to_end import _собрать_pdf
    return _собрать_pdf(КП_PDF)


def письмо(n: int, файл: int, тип: int = ms.КОМПАНИЯ, направление: int = ms.ВХОДЯЩЕЕ,
           владелец: int = 55) -> dict:
    return {"ID": str(n), "OWNER_ID": str(владелец), "OWNER_TYPE_ID": str(тип),
            "DIRECTION": str(направление), "CREATED": "2026-09-19T10:00:00+03:00",
            "FILES": [{"id": str(файл)}]}


def разобрать(ix, monkeypatch, байты: bytes, п: dict, группа: str):
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: байты)
    ref = ms.ссылки_письма(п, группа)[0]
    rec, items = ix.handle(ref)
    return rec, items, ix.строки_цен(rec, items)


def по_имени(строка: tuple) -> dict:
    return dict(zip(price_store.КОЛОНКИ, строка))


# ─────────────────────────────────────────────── письмо поставщика
@pytest.mark.parametrize("формат", ["xlsx", "pdf"])
def test_кп_поставщика_даёт_строки_цены(monkeypatch, формат):
    ix = индексатор(monkeypatch, цены=True)
    байты = xlsx_кп() if формат == "xlsx" else pdf_кп()
    rec, items, цены = разобрать(ix, monkeypatch, байты, письмо(7001, 9101), "mail-supplier")
    assert rec["side"] == doc_side.ПОСТАВЩИК and rec["status"] == "разобран"
    assert rec["parse_path"] == "таблица", "КП должно читаться таблицей"
    assert len(цены) == 4, [по_имени(c)["item_name"] for c in цены]
    строки = [по_имени(c) for c in цены]
    assert {с["currency"] for с in строки} == {"USD"}
    assert sorted(с["price"] for с in строки) == [47.25, 85.0, 312.5, 12400.0]
    for с in строки:
        assert с["feed"] == price_store.FEED_ПИСЬМА != price_store.FEED
        assert с["source"] == price_store.ИСТОЧНИК_ПИСЬМА
        assert с["rfq_company"] == "55", "поставщик — компания, на которой висит письмо"
        assert с["rfq_id"] == "C55", "владелец письма — как в lib_files.deal_id"
        assert с["source_url"] == "mail:9101"
        assert с["price_date"], "дата квотации — из КП или из письма"
    по_коду = {с["part_number"]: с for с in строки}
    assert по_коду["VYD-4471/2"]["qty"] == 10


def test_письмо_контакта_поставщик_не_указан(monkeypatch):
    """Компанию контакта знает только сам контакт — лишнего запроса нет, поставщик пуст."""
    ix = индексатор(monkeypatch, цены=True)
    _rec, _items, цены = разобрать(ix, monkeypatch, xlsx_кп(),
                                   письмо(7002, 9102, тип=ms.КОНТАКТ, владелец=77),
                                   "mail-supplier")
    assert цены
    assert {по_имени(c)["rfq_company"] for c in цены} == {None}
    assert {по_имени(c)["rfq_id"] for c in цены} == {"K77"}


# ─────────────────────────────────────────────── письмо не поставщика
@pytest.mark.parametrize("группа, тип, направление", [
    ("mail-deal", ms.СДЕЛКА, ms.ВХОДЯЩЕЕ),      # заказчик
    ("mail-deal", ms.СДЕЛКА, ms.ИСХОДЯЩЕЕ),     # наше
    ("mail-lead", ms.ЛИД, ms.ВХОДЯЩЕЕ),         # заказчик
    ("mail-supplier", ms.КОМПАНИЯ, ms.ИСХОДЯЩЕЕ),  # сторона не определена
])
def test_письмо_не_поставщика_цен_не_даёт(monkeypatch, группа, тип, направление):
    ix = индексатор(monkeypatch, цены=True, группа=группа)
    rec, items, цены = разобрать(ix, monkeypatch, xlsx_кп(),
                                 письмо(7003, 9103, тип=тип, направление=направление),
                                 группа)
    assert rec["side"] != doc_side.ПОСТАВЩИК
    assert any(it.get("_цена") for it in items), "цена в колонке есть — и всё же не пишется"
    assert цены == []


# ─────────────────────────────────────────────── вход выключен
@pytest.mark.parametrize("формат", ["xlsx", "pdf"])
def test_без_входа_цен_нет_и_запись_прежняя(monkeypatch, формат):
    байты = xlsx_кп() if формат == "xlsx" else pdf_кп()
    выкл = индексатор(monkeypatch, цены=False)
    rec, items, цены = разобрать(выкл, monkeypatch, байты, письмо(7004, 9104), "mail-supplier")
    assert цены == []
    # Шаги котировки (свод условий, дата квотации) без входа не идут вовсе.
    assert not any("_условия" in it or "price_date" in it for it in items)
    assert items, "позиции спроса пишутся как прежде"


def записи(ix, rec, items) -> tuple:
    """Ровно то, что main() кладёт в lib_files (все колонки) и lib_demand."""
    спрос = [(it["segment_id"], it["deal_id"], ix.pg(it["item_name"])[:500],
              ix.pg(it.get("oem"))[:200], ix.pg(it.get("part_number"))[:120],
              it.get("qty"), ix.pg(it.get("unit"))[:40], ix.ИСТОЧНИК_СТРОКИ,
              it["source_file"], it.get("segment_rule")) for it in items]
    return ix.кортеж_файла(rec, ix.КОЛОНКИ_ВСТАВКИ), спрос


@pytest.mark.parametrize("группа, тип", [("mail-deal", ms.СДЕЛКА), ("mail-lead", ms.ЛИД)])
def test_вход_не_меняет_записи_файла_не_поставщика(monkeypatch, группа, тип):
    """Для заказчика вход не значит ничего: lib_files и lib_demand байт в байт."""
    итоги = []
    for цены in (False, True):
        ix = индексатор(monkeypatch, цены=цены, группа=группа)
        rec, items, строки = разобрать(ix, monkeypatch, pdf_кп(),
                                       письмо(7005, 9105, тип=тип), группа)
        assert строки == [] and items
        итоги.append(записи(ix, rec, items))
    assert итоги[0] == итоги[1]


def test_табличное_кп_поставщика_пишет_тот_же_спрос_с_входом_и_без(monkeypatch):
    """Вход добавляет только строки цены: спрос и учёт файла таблицы — те же."""
    итоги = []
    for цены in (False, True):
        ix = индексатор(monkeypatch, цены=цены)
        rec, items, _ = разобрать(ix, monkeypatch, xlsx_кп(), письмо(7006, 9106), "mail-supplier")
        итоги.append(записи(ix, rec, items))
    assert итоги[0] == итоги[1]


# ─────────────────────────────────────────────── одна вставка (правило 14)
def test_вставка_цены_одна_на_все_источники():
    """Строка письма — тот же кортеж и та же вставка, что у карточки запроса."""
    поз = {"segment_id": None, "item_name": "Подшипник выдуманный", "part_number": "VYD-1",
           "qty": 2, "unit": "шт", "source_file": "mail:1", "deal_id": "C9", "company": "9"}
    ц = {"price": 10.0, "currency": "EUR", "confidence": "med", "note": None}
    письмо_ = price_store.строка(поз, ц, lambda v: str(v or ""),
                                 price_store.ИСТОЧНИК_ПИСЬМА, price_store.FEED_ПИСЬМА)
    карточка = price_store.строка({**поз, "source_file": "1", "deal_id": "9"}, ц,
                                  lambda v: str(v or ""))
    assert len(письмо_) == len(карточка) == len(price_store.КОЛОНКИ)
    разница = {к for к, a, b in zip(price_store.КОЛОНКИ, письмо_, карточка) if a != b}
    assert разница == {"source", "feed", "source_url", "rfq_id"}
    # Без новых аргументов строка прежняя — поток «разбор КП».
    assert по_имени(карточка)["feed"] == price_store.FEED


class Курсор:
    def __init__(self):
        self.вызовы: list[tuple] = []

    def execute(self, sql, params=None):
        self.вызовы.append((sql, params))

    def fetchall(self):
        return [(к,) for к in price_store.КОЛОНКИ]


def test_переразбор_снимает_только_свой_поток():
    """Буфер одного потока — ровно один прежний оператор снятия; смешанный —
    по оператору на поток, каждый со своими файлами."""
    ц = {"price": 1.0, "currency": "EUR", "confidence": "med", "note": None}
    г = lambda v: str(v or "")  # noqa: E731
    кп = price_store.строка({"source_file": "11", "deal_id": "1"}, ц, г)
    пис = price_store.строка({"source_file": "mail:12", "deal_id": "C1"}, ц, г,
                             price_store.ИСТОЧНИК_ПИСЬМА, price_store.FEED_ПИСЬМА)
    вставки = []

    def execute_values(cur, запрос, строки, page_size):
        вставки.append(строки)

    c = Курсор()
    price_store.записать(c, [кп], execute_values)
    снятия = [p for sql, p in c.вызовы if sql == price_store.СНЯТЬ]
    assert снятия == [(price_store.FEED, price_store.ИСТОЧНИК_СКАНА, ["11"])]

    c = Курсор()
    price_store.записать(c, [кп, пис], execute_values)
    снятия = sorted(p for sql, p in c.вызовы if sql == price_store.СНЯТЬ)
    assert снятия == sorted([(price_store.FEED, price_store.ИСТОЧНИК_СКАНА, ["11"]),
                             (price_store.FEED_ПИСЬМА, price_store.ИСТОЧНИК_СКАНА, ["mail:12"])])
    assert вставки[-1] == [кп, пис]


@pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")
def test_цены_письма_ложатся_в_настоящую_таблицу(monkeypatch):
    """Сквозь PostgreSQL: письмо и карточка одной вставкой, повтор письма не
    трогает цену карточки."""
    import psycopg2
    import psycopg2.extras
    ix = индексатор(monkeypatch, цены=True)
    _rec, _items, цены = разобрать(ix, monkeypatch, xlsx_кп(), письмо(7007, 9107), "mail-supplier")
    ц = {"price": 5.0, "currency": "EUR", "confidence": "med", "note": None}
    карточка = price_store.строка({"source_file": "31", "deal_id": "3"}, ц, lambda v: str(v or ""))
    схема = "mail_prices_test"
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    try:
        c.execute(f"drop schema if exists {схема} cascade")
        c.execute(f"create schema {схема}")
        c.execute(f"set search_path to {схема}")
        from tests.test_offer_end_to_end import ТАБЛИЦА
        c.execute(ТАБЛИЦА)
        price_store.записать(c, цены + [карточка], psycopg2.extras.execute_values)
        price_store.записать(c, цены, psycopg2.extras.execute_values)
        c.execute("select feed, count(*), count(distinct currency) from lib_prices group by feed"
                  " order by feed")
        assert c.fetchall() == sorted([(price_store.FEED, 1, 1),
                                       (price_store.FEED_ПИСЬМА, 4, 1)])
        c.execute("select distinct rfq_company, rfq_id from lib_prices where feed = %s",
                  (price_store.FEED_ПИСЬМА,))
        assert c.fetchall() == [("55", "C55")]
    finally:
        c.execute(f"drop schema if exists {схема} cascade")
        conn.close()


# ─────────────────────────────────────────────── замер
def test_холостой_замер_печатает_только_числа(monkeypatch):
    ix = индексатор(monkeypatch, цены=True)
    замер = ix.ЗамерЦен()
    rec, items, _ = разобрать(ix, monkeypatch, xlsx_кп(), письмо(7008, 9108), "mail-supplier")
    замер.учесть(rec, items)
    чужой = индексатор(monkeypatch, цены=True, группа="mail-deal")
    rec2, items2, _ = разобрать(чужой, monkeypatch, xlsx_кп(), письмо(7009, 9109, тип=ms.СДЕЛКА),
                                "mail-deal")
    замер.учесть(rec2, items2)
    текст = "\n".join(замер.строки(False))
    assert "было бы записано (холостой): 4" in текст
    assert "'USD': 4" in текст
    assert "с поставщиком (компания письма) 4" in текст
    assert "не пишется): файлов 1 · строк 4" in текст
    # Ни наименований, ни кодов позиций (правило 17).
    assert "VYD" not in текст and "bearing" not in текст


# ─────────────────────────────────────────────── прогон
def шаг_разбора() -> dict:
    wf = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return next(ш for ш in wf["jobs"]["index"]["steps"] if "indexer.py" in (ш.get("run") or ""))


def выполнить_шаг(группа: str, цены: str) -> tuple[str, str]:
    ш = шаг_разбора()
    скрипт = (ш["run"].replace("${{ inputs.group }}", группа)
              .replace("python library/indexer.py", 'echo "MAIL_PRICES=[${MAIL_PRICES:-}]"'))
    env = {"PATH": os.environ.get("PATH", ""), "INPUT_RPS": "1.2", "SHARDS": "10",
           "INPUT_PRICES": цены}
    out = subprocess.run(["bash", "-c", скрипт], env=env, capture_output=True, text=True,
                         check=True).stdout
    значение = [s for s in out.splitlines() if s.startswith("MAIL_PRICES=")][-1]
    return значение, out


def test_вход_prices_объявлен_выключенным():
    wf = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    вход = wf["on"]["workflow_dispatch"]["inputs"]["prices"]
    assert вход["type"] == "boolean" and вход["default"] == "false"
    assert "inputs.prices" in шаг_разбора()["env"]["INPUT_PRICES"]


def test_вход_prices_действует_для_поставщиков():
    assert выполнить_шаг("mail-supplier", "1")[0] == "MAIL_PRICES=[1]"
    assert выполнить_шаг("mail-supplier", "")[0] == "MAIL_PRICES=[]"


@pytest.mark.parametrize("группа", ["mail-deal", "mail-lead"])
def test_вход_prices_не_действует_для_сделок_и_лидов(группа):
    значение, журнал = выполнить_шаг(группа, "1")
    assert значение == "MAIL_PRICES=[]"
    assert "вход prices не действует" in журнал
    # Без входа — молчание: печатать нечего.
    значение, журнал = выполнить_шаг(группа, "")
    assert значение == "MAIL_PRICES=[]" and "prices" not in журнал
