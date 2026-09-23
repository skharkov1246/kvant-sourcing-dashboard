"""Распознавание не переписывает файлы, у которых уже есть позиции.

ЗАЧЕМ. В отбор распознавания взяты смешанные PDF (pdf_mixed): текстовые
страницы разобраны, сканы — нет. Прежняя запись распознавания писала файл
целиком: price_store снимал все цены файла, строки ложились рядом со строками
разбора, статус затирался. Первая попытка — «заменять разбор, если
распознанное даёт больше цен» — не прошла перепроверку 23.09.2026: распознавание
читает первые PAGES страниц (12), а разбор — до шестидесяти, и замена стирала
цены страниц 13+. Затем файл с позициями из отбора убрали вовсе, и сканы в нём
терялись. Теперь он берётся ПОСТРАНИЧНО: читаются только сканы, их позиции
добавляются к разбору (tests/test_ocr_pages.py), а файл с позициями и сканами
внутри документа по-прежнему не берётся.

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
    c.execute("create table lib_prices (id bigserial primary key, "
              + ", ".join(f"{к} text" for к in ocr.price_store.КОЛОНКИ) + ")")
    from tests.test_ocr_pages import ddl_журнала
    for оператор in ddl_журнала():
        c.execute(оператор)
    try:
        yield c
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        conn.close()


def файлы(cur, строки: str, *параметры) -> None:
    cur.execute("insert into lib_files (file_id, deal_id, status, kind, rows_found, reason, "
                "pdf_mixed, ocr_at) values " + строки, параметры)


def test_отбор_смешанный_с_позициями_берёт_по_страницам(cur):
    файлы(cur, """
      ('1', '1', 'разобран', 'pdf', 12, null, true, null),
      ('2', '1', 'текст без спецификации', 'pdf', 0, null, true, null),
      ('3', '1', 'пусто', 'pdf', 0, 'PDF без текстового слоя', null, null),
      ('4', '1', 'пусто', 'docx', 0, %s, null, null),
      ('5', '1', 'пусто', 'изображение', 0, 'нет текстового слоя', null, null),
      ('6', '1', 'пусто', 'pdf', 0, 'PDF без текстового слоя', null, now()),
      ('7', '1', 'не скачался', 'pdf', 0, null, null, null),
      ('8', '1', 'разобран', 'docx', 3, %s, null, null),
      ('9', '1', 'разобран', 'pdf', 12, null, true, now()),
      ('10', '1', 'разобран', 'pdf', 12, null, null, null)""",
          f"{ocr.indexer.КАРТИНКИ_ВНУТРИ} 2 — читаются распознаванием",
          f"{ocr.indexer.КАРТИНКИ_ВНУТРИ} 1 — читаются распознаванием")
    cur.execute(ocr.CANDIDATES)
    Ц, П = ocr.ЦЕЛИКОМ, ocr.ПОСТРАНИЧНО
    # 1 — смешанный с позициями: по страницам. 8 — сканы внутри документа с
    # позициями: слить нечем, не берётся. 9 — уже распознан. 10 — не смешанный.
    assert sorted(cur.fetchall()) == [("1", П, 12), ("2", Ц, 0), ("3", Ц, 0),
                                      ("4", Ц, 0), ("5", Ц, 0)]


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

def test_запись_распознавания_на_базе(cur, monkeypatch, capsys):
    """Настоящий ocr.main: холостой прогон, запись и откат; портал и tesseract заглушены.

    1 — смешанный PDF с позициями: распознаются сканы, их строки и цены
        ДОБАВЛЯЮТСЯ, разбор и файл целы.
    2 — смешанный, разбор позиций не дал: распознаётся целиком, как обычный скан.
    3 — скан, который не скачался: без отметки, вид сохранён, повтор закачки
        вернёт его разбору, а распознавание — в очередь.
    4 — скан с отказом окружения: без отметки, как прежде.
    """
    import psycopg2
    файлы(cur, """
      ('1', '1', 'разобран', 'pdf', 2, 'причина разбора', true, null),
      ('2', '1', 'текст без спецификации', 'pdf', 0, null, true, null),
      ('3', '2', 'пусто', 'pdf', 0, 'PDF без текстового слоя', null, null),
      ('4', '2', 'пусто', 'pdf', 0, 'PDF без текстового слоя', null, null)""")
    cur.execute("insert into lib_demand (source_file, source, item_name) values "
                "('1', 'котировка поставщика', 'Насос ВЫДУМ-1'), "
                "('1', 'котировка поставщика', 'Насос ВЫДУМ-2')")
    cur.execute("insert into lib_prices (source_url, feed, source, item_name, price) values "
                "('1', %s, %s, 'Насос ВЫДУМ-1', '1')",
                (ocr.price_store.FEED, ocr.price_store.ИСТОЧНИК))

    def поз(файл: str, n: int) -> list[dict]:
        return [{"item_name": f"Распознанный насос ВЫДУМ-{файл}-{i}", "part_number": "",
                 "oem": "", "unit": "", "qty": 1, "segment_id": "насосы",
                 "segment_rule": "файл", "deal_id": "1", "source_file": файл,
                 "company": None,
                 "_цена": {"price": 10.0 + i, "currency": "RUB", "confidence": 0.9}}
                for i in range(n)]

    итоги = {
        "1": ("разобран по скану", "pdf", None, поз("1", 5), [3, 13]),
        "2": ("разобран по скану", "pdf", None, поз("2", 3), None),
        "3": ("не скачался", None, None, [], None),
        "4": ("пусто", "pdf", "таймаут распознавания (120 с)", [], None),
    }
    распознаны: list[tuple[str, str]] = []

    def распознать(ref):
        fid = ref["fo"]["id"]
        распознаны.append((fid, ref["режим"]))
        статус, вид, причина, items, страницы = итоги[fid]
        return ({"file_id": fid, "deal_id": ref["deal"], "status": статус,
                 "chars": 700 if items else 0, "rows_found": len(items),
                 "segment_id": "насосы" if items else None, "kind": вид,
                 "reason": причина, "режим": ref["режим"], "страницы": страницы}, items)

    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal.example.test/rest/1/x")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused.example.test/x")
    monkeypatch.setenv("GITHUB_RUN_ID", "777")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setattr(ocr, "ПОВТОР", False)
    monkeypatch.setattr(ocr.indexer, "SOURCE", "rfq")
    monkeypatch.setattr(ocr, "recognise", распознать)
    # Файл 1 приложен к двум карточкам: распознаётся и пишется один раз, иначе
    # вторая запись пометила бы первую как «свою прежнюю».
    monkeypatch.setattr(ocr.indexer, "collect_refs_rfq", lambda *a, **k: [
        {"fo": {"id": ф}, "deal": "1"} for ф in [*итоги, "1"]])

    def соединение(*a, **k):
        c = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
        c.autocommit = False
        return c
    monkeypatch.setattr(ocr.indexer, "connect", соединение)

    def файл(fid):
        cur.execute("select status, kind, rows_found, reason, ocr_at is not null "
                    "from lib_files where file_id = %s", (fid,))
        return cur.fetchone()

    def строк_и_цен(fid, источник=None):
        """Живые строки и цены потока «разбор КП»; с источником — только его."""
        cur.execute("select count(*) from lib_demand d where source_file = %s"
                    " and (%s::text is null or source = %s) and not exists (select 1 from"
                    " lib_row_junk j where j.demand_id = d.id and j.revoked_at is null)",
                    (fid, источник, источник))
        строк = cur.fetchone()[0]
        cur.execute("select count(*) from lib_prices where source_url = %s and feed = %s"
                    " and (%s::text is null or source = %s)",
                    (fid, ocr.price_store.FEED, источник, источник))
        return строк, cur.fetchone()[0]

    def снимок():
        cur.execute("select * from lib_files order by file_id")
        ф = cur.fetchall()
        cur.execute("select id, source from lib_demand order by id")
        д = cur.fetchall()
        cur.execute("select id, feed, source from lib_prices order by id")
        return ф, д, cur.fetchall()

    # ── холостой прогон: таблица слияния, в базе ничего ──
    до = снимок()
    monkeypatch.setattr(ocr, "APPLY", False)
    assert ocr.main() == 0
    вывод = capsys.readouterr().out
    assert до == снимок()
    assert "ключ прогона: ocr-777.1" in вывод
    таблица = вывод[вывод.index("СЛИЯНИЕ СО СКАНАМИ"):]
    строка_разбора = next(s for s in таблица.splitlines() if "разбор, лежит в базе" in s)
    строка_сканов = next(s for s in таблица.splitlines() if "сканы распознаны" in s)
    assert строка_разбора.split()[-4:] == ["1", "—", "2", "1"]
    assert строка_сканов.split()[-4:] == ["1", "2", "5", "5"]

    # ── запись ──
    распознаны.clear()
    monkeypatch.setattr(ocr, "APPLY", True)
    assert ocr.main() == 0
    assert sorted(распознаны) == [("1", ocr.ПОСТРАНИЧНО), ("2", ocr.ЦЕЛИКОМ),
                                  ("3", ocr.ЦЕЛИКОМ), ("4", ocr.ЦЕЛИКОМ)]
    скан = ocr.price_store.ИСТОЧНИК_СКАНА
    # Файл 1: разбор цел — статус, счётчик, причина, две строки и цена; сканы добавлены.
    assert файл("1") == ("разобран", "pdf", 2, "причина разбора", True)
    assert строк_и_цен("1", "котировка поставщика")[0] == 2
    assert строк_и_цен("1", ocr.price_store.ИСТОЧНИК)[1] == 1
    assert строк_и_цен("1", скан) == (5, 5)
    assert файл("2") == ("разобран по скану", "pdf", 3, "", True)
    assert строк_и_цен("2", скан) == (3, 3)
    assert файл("3") == ("не скачался", "pdf", 0, "", False)
    assert файл("4") == ("пусто", "pdf", 0, "таймаут распознавания (120 с)", False)
    assert "откат: OCR_REVERT=ocr-777.1" in capsys.readouterr().out

    # ── откат по ключу прогона: всё как до записи, ничего не удалено ──
    monkeypatch.setattr(ocr, "REVERT", "ocr-777.1")
    assert ocr.main() == 0
    assert "файлов откачено" in capsys.readouterr().out
    assert файл("1") == ("разобран", "pdf", 2, "причина разбора", False)
    assert строк_и_цен("1") == (2, 1)
    assert файл("2") == ("текст без спецификации", "pdf", 0, None, False)
    assert строк_и_цен("2") == (0, 0)
    assert файл("3") == ("пусто", "pdf", 0, "PDF без текстового слоя", False)
    assert файл("4") == ("пусто", "pdf", 0, "PDF без текстового слоя", False)
    cur.execute("select count(*) from lib_demand")
    assert cur.fetchone()[0] == 2 + 5 + 3
    cur.execute("select count(*) from lib_prices")
    assert cur.fetchone()[0] == 1 + 5 + 3
    cur.execute(ocr.CANDIDATES)
    assert sorted(r[0] for r in cur.fetchall()) == ["1", "2", "3", "4"]
