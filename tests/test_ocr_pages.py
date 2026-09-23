"""Смешанный PDF распознаётся по страницам и сливается с разбором, не заменяя его.

ЗАЧЕМ. У PDF, где часть страниц текстовые, а часть сканы (lib_files.pdf_mixed),
текстовые страницы разобраны, а позиции со сканов теряются: до 23.09.2026
распознавание такой файл не брало вовсе, потому что писало файл целиком —
снимало все цены файла, затирало статус и читало первые 12 страниц из
шестидесяти, которые читает разбор. Теперь:

- распознаются только страницы без текстового слоя, до indexer.СТРАНИЦ_PDF;
- строки и цены сканов ДОБАВЛЯЮТСЯ; разбор (строки, цены, статус, счётчики) цел;
- разбор и переразбор, в свою очередь, не трогают строк и цен сканов;
- повтор распознавания помечает свои прежние строки и выводит свои прежние
  цены, ничего не удаляя; откат по ключу прогона возвращает прежнее;
- отметка ocr_at — только за настоящую попытку.

Корпус придуман (правило 18). SQL-часть работает при LIBRARY_SQL_TEST_DSN.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import ocr  # noqa: E402
import price_store  # noqa: E402
import reclassify  # noqa: E402
import reparse  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
СХЕМА = "ocr_pages_test"

# Три позиции скана: ворота спецификации их пропускают, а цена сходится по
# арифметике «кол-во × цена = сумма». Валюты в строках НЕТ — она на текстовой
# странице, и доехать до цены может только через текст всего документа.
СКАН = {
    3: "1 Подшипник роликовый 22315 EK ВЫДУМ 4 шт 312,50 1250,00",
    13: "2 Уплотнение манжетное ВЫДУМ-0457 10 шт 85,00 850,00",
    14: "3 Втулка распорная ВД-12.40.001 2 шт 1200,00 2400,00",
}
ТЕКСТ = "Commercial offer VYDUM-{n}. All prices are in USD, delivery DAP Moscow."


def собрать_pdf(страницы: list[str]) -> bytes:
    """Многостраничный PDF: непустая строка — страница с текстом, пустая — «скан»."""
    объекты: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    листы = []
    шрифт = 3
    объекты.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    for текст in страницы:
        поток = (f"BT /F1 9 Tf 1 0 0 1 40 540 Tm ({текст}) Tj ET".encode("latin-1")
                 if текст else b"")
        объекты.append(b"<< /Length %d >>\nstream\n" % len(поток) + поток + b"\nendstream")
        содержимое = len(объекты)
        объекты.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
                       b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                       % (шрифт, содержимое))
        листы.append(len(объекты))
    объекты[1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % n for n in листы)
                  + b"] /Count %d >>" % len(листы))
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


def смешанный_pdf() -> bytes:
    """14 страниц: сканы на 3, 13 и 14 — одна внутри, две после двенадцатой."""
    return собрать_pdf(["" if n in СКАН else ТЕКСТ.format(n=n) for n in range(1, 15)])


# ─────────────────────── распознавание по страницам ───────────────────────

@pytest.fixture
def заглушки(monkeypatch):
    """pdftoppm и tesseract заглушены: разворачиваемые страницы записываются."""
    pytest.importorskip("pypdf")
    развёрнуто: list[int] = []

    def pdftoppm(cmd, **_k):
        assert cmd[0] == "pdftoppm" and "-singlefile" in cmd
        первая, последняя = int(cmd[cmd.index("-f") + 1]), int(cmd[cmd.index("-l") + 1])
        assert первая == последняя, "страница разворачивается по одной"
        развёрнуто.append(первая)
        Path(cmd[-1] + ".png").write_bytes(b"\x89PNG stub")

    def прочесть(путь, *_a, **_k):
        n = int(Path(путь).stem[1:])
        return СКАН.get(n, ""), ""

    monkeypatch.setattr(ocr.subprocess, "run", pdftoppm)
    monkeypatch.setattr(ocr, "ocr_image", прочесть)
    monkeypatch.setattr(ocr.indexer, "КАСКАД", False)
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    return развёрнуто


def ссылка(режим: str) -> dict:
    return {"fo": {"id": "77"}, "deal": "5", "company": "4242", "режим": режим}


def test_номера_страниц_без_текста_по_правилу_разбора():
    страницы = ["x" * 40, "", "  \n ", "x" * 39, "Лист 2"]
    assert ocr.indexer.страницы_сканов(страницы) == [2, 3, 4, 5]
    # То же правило, по которому разбор ставит pdf_mixed.
    assert ocr.indexer.счёт_страниц(страницы) == (1, 4)


def test_читаются_только_сканы_и_после_двенадцатой(заглушки, monkeypatch):
    monkeypatch.setattr(ocr.indexer, "download", lambda fo: смешанный_pdf())
    rec, items = ocr.recognise(ссылка(ocr.ПОСТРАНИЧНО))
    assert заглушки == [3, 13, 14], "распознаны не те страницы"
    assert rec["страницы"] == [3, 13, 14] and rec["режим"] == ocr.ПОСТРАНИЧНО
    # Позиции — только со сканов: текстовые страницы разобраны и так.
    assert [it["item_name"] for it in items] == list(СКАН.values())
    assert {it["source_file"] for it in items} == {"77"}
    # Валюта названа на текстовой странице — и доехала до цены скана.
    assert [it["_цена"]["currency"] for it in items] == ["USD"] * 3
    assert [it["_цена"]["price"] for it in items] == [312.5, 85.0, 1200.0]


def test_предел_страниц_тот_же_что_у_разбора(заглушки, monkeypatch):
    monkeypatch.setattr(ocr.indexer, "download", lambda fo: смешанный_pdf())
    monkeypatch.setattr(ocr.indexer, "СТРАНИЦ_PDF", 13)
    rec, items = ocr.recognise(ссылка(ocr.ПОСТРАНИЧНО))
    assert заглушки == [3, 13] and len(items) == 2


def test_без_сканов_распознавать_нечего(заглушки, monkeypatch):
    monkeypatch.setattr(ocr.indexer, "download",
                        lambda fo: собрать_pdf([ТЕКСТ.format(n=n) for n in (1, 2)]))
    rec, items = ocr.recognise(ссылка(ocr.ПОСТРАНИЧНО))
    assert (заглушки, items, rec["страницы"]) == ([], [], [])
    assert not ocr.виновато_окружение(rec["reason"]) and ocr.отметка_попытки(rec)


def test_отказ_разворота_одной_страницы_отказ_окружения(заглушки, monkeypatch):
    monkeypatch.setattr(ocr.indexer, "download", lambda fo: смешанный_pdf())

    def таймаут(cmd, **_k):
        raise ocr.subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(ocr.subprocess, "run", таймаут)
    rec, items = ocr.recognise(ссылка(ocr.ПОСТРАНИЧНО))
    assert items == [] and ocr.виновато_окружение(rec["reason"])
    assert ocr.отметка_попытки(rec) is None


def test_целиком_как_прежде_первые_pages_страниц(заглушки, monkeypatch):
    """Режим «файл» (скан без позиций разбора) не меняется: разворот диапазона."""
    вызовы = []

    def диапазон(cmd, **_k):
        вызовы.append(cmd)
    monkeypatch.setattr(ocr.subprocess, "run", диапазон)
    monkeypatch.setattr(ocr.indexer, "download", lambda fo: смешанный_pdf())
    rec, _ = ocr.recognise(ссылка(ocr.ЦЕЛИКОМ))
    assert len(вызовы) == 1 and "-singlefile" not in вызовы[0]
    assert вызовы[0][вызовы[0].index("-l") + 1] == str(ocr.PAGES)
    assert rec["страницы"] is None


def test_словарь_не_снимает_пометок_распознавания():
    assert ocr.RULE_ЗАМЕНА in reclassify.НЕ_СНИМАТЬ_СЛОВАРЁМ
    assert ocr.RULE_ОТКАТ in reclassify.НЕ_СНИМАТЬ_СЛОВАРЁМ


def test_источник_строки_скана_отличим_и_один_на_строку_и_цену():
    assert ocr.ИСТОЧНИК_СТРОКИ == price_store.ИСТОЧНИК_СКАНА == "распознавание скана"
    assert ocr.ИСТОЧНИК_СТРОКИ != ocr.indexer.ИСТОЧНИК_СТРОКИ
    assert price_store.ИСТОЧНИК_СКАНА != price_store.ИСТОЧНИК


def test_запись_скана_не_берёт_чужих_цен():
    чужая = price_store.строка({"source_file": "8", "item_name": "x"},
                               {"price": 1, "currency": "RUB", "confidence": "low"},
                               lambda s: str(s or ""), price_store.ИСТОЧНИК_СКАНА)
    разбора = price_store.строка({"source_file": "7", "item_name": "x"},
                                 {"price": 1, "currency": "RUB", "confidence": "low"},
                                 lambda s: str(s or ""))
    for строка in (чужая, разбора):
        with pytest.raises(ValueError):
            price_store.записать_скан(None, "7", [строка], None)


# ───────────────────────────── на базе ─────────────────────────────

def ddl_журнала() -> list[str]:
    """Журнал — настоящим DDL из schema_junk.sql, а не копией в тесте."""
    from tests.test_library_schema_sql import операторы
    sql = (ROOT / "library" / "supabase" / "schema_junk.sql").read_text(encoding="utf-8")
    начало = sql.index("create table if not exists lib_ocr_writes")
    конец = sql.index("alter table lib_ocr_writes enable row level security;")
    return операторы(sql[начало:конец])


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
    c.execute("""create table lib_demand (id bigint generated always as identity primary key,
                   segment_id text, deal_id text, item_name text not null, oem text,
                   part_number text, qty numeric, unit text, source text, source_file text,
                   segment_rule text)""")
    c.execute("""create table lib_row_junk (
                   demand_id bigint primary key references lib_demand(id) on delete cascade,
                   rule text not null, run_id text not null, marks text,
                   marked_at timestamptz not null default now(), revoked_at timestamptz,
                   revoked_by text)""")
    числа = {"price": "numeric", "qty": "numeric", "total": "numeric", "lead_days": "int",
             "make_days": "int", "pay_advance_pct": "smallint", "price_date": "date"}
    c.execute("create table lib_prices (id bigint generated always as identity primary key, "
              + ", ".join(f"{к} {числа.get(к, 'text')}" for к in price_store.КОЛОНКИ) + ")")
    for оператор in ddl_журнала():
        c.execute(оператор)
    try:
        yield c
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        conn.close()


def позиции(файл: str, n: int, начало: int = 0) -> list[dict]:
    return [{"item_name": f"Насос скана ВЫДУМ-{файл}-{i}", "part_number": f"VD-{i}",
             "oem": "", "unit": "шт", "qty": 2, "segment_id": None, "segment_rule": None,
             "deal_id": "5", "source_file": файл, "company": "4242",
             "_цена": {"price": 100.0 + i, "currency": "USD", "confidence": "med"}}
            for i in range(начало, начало + n)]


def постраничная(файл: str, n: int, **поля) -> dict:
    rec = {"file_id": файл, "deal_id": "5", "status": "разобран по скану", "kind": "pdf",
           "chars": 700, "rows_found": n, "segment_id": None, "reason": None,
           "режим": ocr.ПОСТРАНИЧНО, "страницы": [3, 13]}
    rec.update(поля)
    return rec


def разбор(cur, файл: str = "10") -> None:
    """Файл, разобранный по текстовым страницам: две строки и цена разбора."""
    cur.execute("insert into lib_files (file_id, deal_id, status, kind, chars, rows_found, "
                "segment_id, reason, pdf_mixed) values "
                "(%s, '5', 'разобран', 'pdf', 900, 2, 'насосы', 'причина разбора', true)",
                (файл,))
    cur.execute("insert into lib_demand (source_file, source, item_name) values "
                "(%s, 'котировка поставщика', 'Насос ВЫДУМ-1'), "
                "(%s, 'котировка поставщика', 'Насос ВЫДУМ-2')", (файл, файл))
    cur.execute("insert into lib_prices (source_url, feed, source, item_name, price) values "
                "(%s, %s, %s, 'Насос ВЫДУМ-1', 1)", (файл, price_store.FEED, price_store.ИСТОЧНИК))


def живые(cur, файл: str, источник: str) -> list[str]:
    cur.execute("""select d.item_name from lib_demand d
                    where d.source_file = %s and d.source = %s
                      and not exists (select 1 from lib_row_junk j
                                       where j.demand_id = d.id and j.revoked_at is null)
                    order by d.item_name""", (файл, источник))
    return [r[0] for r in cur.fetchall()]


def цены(cur, файл: str, источник: str, поток: str = price_store.FEED) -> list[str]:
    cur.execute("select item_name from lib_prices where source_url = %s and source = %s "
                "and feed = %s order by item_name", (файл, источник, поток))
    return [r[0] for r in cur.fetchall()]


def файл(cur, fid: str) -> tuple:
    cur.execute("select status, rows_found, chars, reason, segment_id, ocr_at is not null, "
                "ocr_chars from lib_files where file_id = %s", (fid,))
    return cur.fetchone()


РАЗБОР = ("разобран", 2, 900, "причина разбора", "насосы")


def test_слияние_повтор_и_откат_на_базе(cur, monkeypatch):
    import psycopg2.extras
    ev = psycopg2.extras.execute_values
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    разбор(cur)
    СТРОКИ_РАЗБОРА = ["Насос ВЫДУМ-1", "Насос ВЫДУМ-2"]

    # ── первый прогон: сканы добавились, разбор цел ──
    первые = позиции("10", 3)
    сч = ocr.записать_файл(cur, "ocr-1", постраничная("10", 3), первые, ev)
    assert (сч["строк вставлено"], сч["цен вставлено"]) == (3, 3)
    assert живые(cur, "10", "котировка поставщика") == СТРОКИ_РАЗБОРА
    assert цены(cur, "10", price_store.ИСТОЧНИК) == ["Насос ВЫДУМ-1"]
    assert живые(cur, "10", ocr.ИСТОЧНИК_СТРОКИ) == [п["item_name"] for п in первые]
    assert цены(cur, "10", price_store.ИСТОЧНИК_СКАНА) == [п["item_name"] for п in первые]
    # Файл остаётся разбору: статус, счётчики, причина и сегмент не тронуты.
    assert файл(cur, "10") == РАЗБОР + (True, 700)
    cur.execute("select mode, pages, cardinality(demand_ids), cardinality(price_ids) "
                "from lib_ocr_writes where run_id = 'ocr-1'")
    assert cur.fetchone() == (ocr.ПОСТРАНИЧНО, [3, 13], 3, 3)

    # ── переразбор файла: берёт и снимает только своё ──
    cur.execute(reparse.OLD_ROWS, ("10", price_store.ИСТОЧНИК_СКАНА))
    свои = {r[0] for r in cur.fetchall()}
    cur.execute("select id from lib_demand where source <> %s", (ocr.ИСТОЧНИК_СТРОКИ,))
    assert свои == {r[0] for r in cur.fetchall()}
    cur.execute(reparse.ЦЕНЫ_БЫЛО, (price_store.FEED, price_store.ИСТОЧНИК_СКАНА, ["10"]))
    assert cur.fetchall() == [("10", 1)], "цены сканов посчитаны разбору в «было»"
    новая_цена_разбора = price_store.строка(
        {"source_file": "10", "item_name": "Насос ВЫДУМ-2"},
        {"price": 2, "currency": "RUB", "confidence": "med"}, lambda s: str(s or ""))
    price_store.записать(cur, [новая_цена_разбора], ev)
    assert цены(cur, "10", price_store.ИСТОЧНИК) == ["Насос ВЫДУМ-2"]
    assert цены(cur, "10", price_store.ИСТОЧНИК_СКАНА) == [п["item_name"] for п in первые]

    # ── повтор распознавания: своё прежнее помечено и выведено, чужое цело ──
    вторые = позиции("10", 2, начало=10)
    сч = ocr.записать_файл(cur, "ocr-2", постраничная("10", 2, chars=500), вторые, ev)
    assert (сч["своих прежних строк помечено"], сч["своих прежних цен выведено"]) == (3, 3)
    assert сч["файлов, где своих строк стало меньше"] == 1
    assert живые(cur, "10", ocr.ИСТОЧНИК_СТРОКИ) == [п["item_name"] for п in вторые]
    assert цены(cur, "10", price_store.ИСТОЧНИК_СКАНА) == [п["item_name"] for п in вторые]
    assert цены(cur, "10", price_store.ИСТОЧНИК_СКАНА, price_store.FEED_ВЫВЕДЕНО) == \
        [п["item_name"] for п in первые]
    assert живые(cur, "10", "котировка поставщика") == СТРОКИ_РАЗБОРА
    assert цены(cur, "10", price_store.ИСТОЧНИК) == ["Насос ВЫДУМ-2"]
    assert файл(cur, "10") == РАЗБОР + (True, 500)

    # ── откат первого поверх второго запрещён ──
    assert ocr.откатить(cur, "ocr-1")["файлов пропущено: есть более поздняя запись"] == 1
    assert живые(cur, "10", ocr.ИСТОЧНИК_СТРОКИ) == [п["item_name"] for п in вторые]

    # ── откат второго: вернулся первый ──
    сч = ocr.откатить(cur, "ocr-2")
    assert сч["файлов откачено"] == 1
    assert живые(cur, "10", ocr.ИСТОЧНИК_СТРОКИ) == [п["item_name"] for п in первые]
    assert цены(cur, "10", price_store.ИСТОЧНИК_СКАНА) == [п["item_name"] for п in первые]
    assert файл(cur, "10") == РАЗБОР + (True, 700)

    # ── откат первого: файла снова нет в распознанных, он снова в очереди ──
    assert ocr.откатить(cur, "ocr-1")["файлов откачено"] == 1
    assert живые(cur, "10", ocr.ИСТОЧНИК_СТРОКИ) == []
    assert цены(cur, "10", price_store.ИСТОЧНИК_СКАНА) == []
    assert файл(cur, "10") == РАЗБОР + (False, None)
    cur.execute(ocr.CANDIDATES)
    assert cur.fetchall() == [("10", ocr.ПОСТРАНИЧНО, 2)]
    # Разбор цел, и ничего не удалено: 2 строки разбора + 3 + 2 скана.
    assert живые(cur, "10", "котировка поставщика") == СТРОКИ_РАЗБОРА
    cur.execute("select count(*) from lib_demand")
    assert cur.fetchone()[0] == 7
    cur.execute("select count(*) from lib_prices")
    assert cur.fetchone()[0] == 6
    # Повторный откат — пустой: запись уже откачена.
    assert not ocr.откатить(cur, "ocr-1")


def test_откат_по_ключу_прогона_берёт_все_части(cur, monkeypatch):
    import psycopg2.extras
    ev = psycopg2.extras.execute_values
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    for ф in ("10", "11"):
        разбор(cur, ф)
    ocr.записать_файл(cur, "ocr-9.1-p1", постраничная("10", 1), позиции("10", 1), ev)
    ocr.записать_файл(cur, "ocr-9.1-p2", постраничная("11", 1), позиции("11", 1), ev)
    ocr.записать_файл(cur, "ocr-9.10-p1", постраничная("11", 1), позиции("11", 1, 5), ev)
    # «ocr-9.1» — обе части своего прогона и ни одной части прогона «ocr-9.10».
    сч = ocr.откатить(cur, "ocr-9.1")
    assert сч["файлов откачено"] == 1
    assert сч["файлов пропущено: есть более поздняя запись"] == 1
    assert живые(cur, "10", ocr.ИСТОЧНИК_СТРОКИ) == []
    assert живые(cur, "11", ocr.ИСТОЧНИК_СТРОКИ) == ["Насос скана ВЫДУМ-11-5"]


def test_отметка_только_за_настоящую_попытку(cur, monkeypatch):
    import psycopg2.extras
    ev = psycopg2.extras.execute_values
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    for ф in ("20", "21", "22"):
        разбор(cur, ф)
    # Отказ окружения и незакачка: не пишется ничего, файл в очереди.
    ocr.записать_файл(cur, "ocr-3", постраничная(
        "20", 0, status="пусто", reason="таймаут распознавания (страниц 2)"), [], ev)
    ocr.записать_файл(cur, "ocr-3", постраничная(
        "21", 0, status="не скачался", kind=None, страницы=None), [], ev)
    # Сканов не нашлось — это ответ: отметка есть, файл остаётся разбору.
    ocr.записать_файл(cur, "ocr-3", постраничная(
        "22", 0, status="пусто", reason="страниц без текстового слоя не нашлось",
        страницы=[], chars=0), [], ev)
    assert файл(cur, "20") == РАЗБОР + (False, None)
    assert файл(cur, "21") == РАЗБОР + (False, None)
    assert файл(cur, "22") == РАЗБОР + (True, 0)
    cur.execute("select file_id, note from lib_ocr_writes order by file_id")
    assert cur.fetchall() == [("22", "страниц без текстового слоя не нашлось")]
    cur.execute(ocr.CANDIDATES)
    assert sorted(cur.fetchall()) == [("20", ocr.ПОСТРАНИЧНО, 2), ("21", ocr.ПОСТРАНИЧНО, 2)]


def test_отказ_окружения_не_заменяет_прежнее_распознавание(cur, monkeypatch):
    """Повтор, упавший по вине окружения, не имеет права снять прежний итог."""
    import psycopg2.extras
    ev = psycopg2.extras.execute_values
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    cur.execute("insert into lib_files (file_id, deal_id, status, kind, rows_found) "
                "values ('30', '5', 'пусто', 'изображение', 0)")
    целиком = {"file_id": "30", "deal_id": "5", "status": "разобран по скану",
               "kind": "изображение", "chars": 400, "rows_found": 2, "segment_id": None,
               "reason": None, "режим": ocr.ЦЕЛИКОМ, "страницы": None}
    ocr.записать_файл(cur, "ocr-4", целиком, позиции("30", 2), ev)
    сч = ocr.записать_файл(cur, "ocr-5", {**целиком, "status": "пусто", "chars": 0,
                                          "rows_found": 0,
                                          "reason": "таймаут распознавания (120 с)"}, [], ev)
    assert сч["своих прежних строк помечено"] == сч["своих прежних цен выведено"] == 0
    assert живые(cur, "30", ocr.ИСТОЧНИК_СТРОКИ) == [п["item_name"] for п in позиции("30", 2)]
    assert len(цены(cur, "30", price_store.ИСТОЧНИК_СКАНА)) == 2


def test_повтор_и_откат_берегут_чужую_пометку(cur, monkeypatch):
    """Строка скана с пометкой «проза» — тоже своя: иначе словарь, сняв «прозу»,
    оживил бы прежнюю редакцию дублем. Откат возвращает «прозу» как была."""
    import psycopg2.extras
    ev = psycopg2.extras.execute_values
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    разбор(cur, "40")
    ocr.записать_файл(cur, "ocr-6", постраничная("40", 2), позиции("40", 2), ev)
    cur.execute("select id from lib_demand where source = %s order by id",
                (ocr.ИСТОЧНИК_СТРОКИ,))
    проза, простая = (r[0] for r in cur.fetchall())
    cur.execute("insert into lib_row_junk (demand_id, rule, run_id, marks) "
                "values (%s, 'proza-v1', 'proza-1', 'лексика')", (проза,))

    сч = ocr.записать_файл(cur, "ocr-7", постраничная("40", 1), позиции("40", 1, 7), ev)
    assert сч["своих прежних строк помечено"] == 2
    # Словарь узнаёт обе строки — и не снимает ни одной пометки замены.
    cur.execute(reclassify.UNMARK_SQL, ([проза, простая], list(reclassify.НЕ_СНИМАТЬ_СЛОВАРЁМ)))
    assert живые(cur, "40", ocr.ИСТОЧНИК_СТРОКИ) == ["Насос скана ВЫДУМ-40-7"]

    ocr.откатить(cur, "ocr-7")
    cur.execute("select demand_id, rule, run_id, marks, revoked_at is null from lib_row_junk "
                "where demand_id in (%s, %s) order by demand_id", (проза, простая))
    assert cur.fetchall() == [(проза, "proza-v1", "proza-1", "лексика", True),
                              (простая, ocr.RULE_ЗАМЕНА, "ocr-7", "повтор распознавания",
                               False)]
    assert живые(cur, "40", ocr.ИСТОЧНИК_СТРОКИ) == ["Насос скана ВЫДУМ-40-1"]


def test_откат_запускается_из_прогона_одной_частью():
    """Строка подключения к базе есть только в секретах Actions: без входа в
    прогоне откат, написанный в коде, выполнить негде."""
    import yaml
    wf = yaml.safe_load((ROOT / ".github/workflows/library-index.yml").read_text(
        encoding="utf-8"))
    assert "ocr_revert" in wf[True]["workflow_dispatch"]["inputs"]
    шаг = next(s for s in wf["jobs"]["index"]["steps"]
               if "REVERT_KEY" in (s.get("env") or {}))
    assert шаг["env"]["REVERT_KEY"] == "${{ inputs.ocr_revert }}"
    команда = шаг["run"]
    откат = команда.index('OCR_REVERT="$REVERT_KEY" python library/ocr.py')
    assert команда.rindex('if [ "${{ matrix.shard }}" = "0" ]', 0, откат) > 0
    assert "${{ inputs.ocr_revert }}" not in команда, "ключ подставлен в текст команды"
    # Режим для замера: варианты входа — ровно режимы кода.
    режимы = wf[True]["workflow_dispatch"]["inputs"]["ocr_mode"]["options"]
    assert set(режимы) == {"все", ocr.ПОСТРАНИЧНО, ocr.ЦЕЛИКОМ}
    assert "OCR_MODE" in шаг["env"]
