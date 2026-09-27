"""Связи сквозного свода и поток цен нашего КП — без базы и без портала.

Что проверяется:
  · карточка СП-166 берёт сделку (parentId2) и название в ТОМ ЖЕ select, что и
    файлы КП: число запросов к порталу не растёт ни на один;
  · сделка берёт название, компанию, стадию и дату в том же crm.deal.list;
  · поле, которого портал не отдал, — не «связи нет»: строки нет вовсе;
  · цену из поля сделки пишет только наше КП, опознанное ТОЧНЫМ кодом, и только
    своим потоком «наше КП заказчику» — не «разбор КП» ни при каком входе;
  · все читатели «всех потоков, кроме …» исключают наш поток одним списком;
  · прогоны: досчёт в очереди портала только при чтении портала, миграция
    подключена после схемы поставщиков, вход нашего КП передаётся разбору.

Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import deal_links as dl  # noqa: E402
import doc_folder  # noqa: E402
import indexer as ix  # noqa: E402
import price_store  # noqa: E402

НАШЕ = "ufCrm_1585568303498"          # Offer from us
RESULT_FILE = "ufCrm_1780061070"      # направление не установлено
СПЕЦИФИКАЦИЯ = "ufCrm_1633502831"     # заявка заказчика


def без_комментариев(текст: str, знак: str = "#") -> str:
    return re.sub(rf"(?m){re.escape(знак)}.*$", "", текст)


# ── строки связей ────────────────────────────────────────────────────────────

def test_строка_карточки_и_сделки():
    assert dl.строка_карточки({"id": 101, "parentId2": 7, "title": "Запрос выдуманный",
                               "createdTime": "2026-09-01T10:00:00+03:00"}) == (
        "101", "7", "Запрос выдуманный", "2026-09-01T10:00:00+03:00")
    # Карточка без сделки — строка есть, связи нет.
    assert dl.строка_карточки({"id": "102", "parentId2": None})[1] is None
    # Портал поле не отдал — строки нет: «связи нет» писать нельзя.
    assert dl.строка_карточки({"id": 103, "title": "x"}) is None
    assert dl.строка_карточки({"parentId2": 5}) is None
    # «0» портала — нет компании; мусор даты — пусто, а не падение.
    assert dl.строка_сделки({"ID": "41", "TITLE": "Сделка\x00 выдуманная", "COMPANY_ID": "0",
                             "STAGE_ID": "C1:NEW", "DATE_CREATE": "не дата"}) == (
        "41", "Сделка выдуманная", None, "C1:NEW", None)
    assert dl.строка_сделки({"ID": "42", "COMPANY_ID": "0077"})[2] == "77"
    assert dl.строка_сделки({"ID": "43"}) is None
    assert dl.без_повторов([("1", "a"), ("1", "b"), None, ("2", "c")]) == [("1", "b"), ("2", "c")]


def _портал_карточек(monkeypatch, карточки):
    """Подставной портал СП-166 по ключу «>id»; журнал вызовов."""
    вызовы = []

    def bx(method, params):
        вызовы.append((method, params))
        if method == "crm.item.list":
            f = params.get("filter") or {}
            после, до = int(f.get(">id", 0)), f.get("<=id")
            items = [x for x in карточки if x["id"] > после and (до is None or x["id"] <= до)]
            return {"result": {"items": items[:50]}}
        return {"result": {}}

    monkeypatch.setattr(ix, "bx", bx)
    monkeypatch.setattr(ix, "наша_компания", lambda _v: None)
    monkeypatch.setattr(ix, "_СВЯЗИ", {"карточки": {}, "сделки": {}})
    return вызовы


def test_сделка_карточки_в_том_же_select_без_лишних_запросов(monkeypatch):
    поле = next(iter(ix.ПОЛЯ_КП))
    карточки = [{"id": i, "createdTime": "2026-09-01T10:00:00+03:00",
                 "parentId2": (i % 7) or None, "title": f"Запрос {i}"} for i in range(1, 121)]
    карточки[4][поле] = [{"id": "f5", "urlMachine": "https://пример/f5"}]
    вызовы = _портал_карточек(monkeypatch, карточки)
    refs = ix.collect_refs_rfq(0)
    assert [r["fo"]["id"] for r in refs] == ["f5"]
    # 120 карточек — три страницы по 50, и ни одного вызова сверх них.
    assert [м for м, _ in вызовы] == ["crm.item.list"] * 3
    for _м, p in вызовы:
        assert "parentId2" in p["select"] and "title" in p["select"]
    связи = ix._СВЯЗИ["карточки"]
    assert len(связи) == 120
    assert sum(1 for r in связи.values() if r[1]) == sum(1 for x in карточки if x["parentId2"])
    assert связи["14"] == ("14", None, "Запрос 14", "2026-09-01T10:00:00+03:00")


def test_ссылки_карточек_не_ходят_в_портал(monkeypatch):
    """ссылки_карточек разбирает уже прочитанное — портал ей не нужен вовсе."""
    monkeypatch.setattr(ix, "bx", lambda *_a: pytest.fail("ссылки_карточек пошли в портал"))
    monkeypatch.setattr(ix, "наша_компания", lambda _v: None)
    поле = next(iter(ix.ПОЛЯ_КП))
    refs = ix.ссылки_карточек([{"id": 9, "parentId2": 3, поле: [{"id": "f9", "urlMachine": "u"}]}],
                              [поле])
    assert [r["deal"] for r in refs] == ["9"]


def test_сделки_берут_заказчика_в_том_же_crm_deal_list(monkeypatch):
    вызовы = []

    def bx(method, params):
        вызовы.append((method, params))
        f = dict(params.get("filter") or {})
        if method == "crm.deal.list":
            return {"result": [{"ID": "41", "TITLE": "Сделка выдуманная", "COMPANY_ID": "901",
                                "STAGE_ID": "NEW", "DATE_CREATE": "2026-09-02T09:00:00+03:00"}]
                    if f.get(">ID", 0) < 41 else []}
        if method == "crm.item.fields":
            return {"result": {"fields": {"ufF": {"type": "file", "title": "Спецификация"}}}}
        if method == "crm.item.list":
            return {"result": {"items": [{"id": i, "ufF": [{"id": f"d{i}", "urlMachine": "u"}]}
                                         for i in f["@id"]]}}
        return {"result": {}}

    monkeypatch.setattr(ix, "bx", bx)
    monkeypatch.setattr(ix, "наша_компания", lambda _v: None)
    monkeypatch.setattr(ix, "_СВЯЗИ", {"карточки": {}, "сделки": {}})
    refs = ix.collect_refs(3650)
    assert [r["deal"] for r in refs] == ["41"]
    # Тот же набор вызовов, что и прежде: список сделок, описание полей, файлы.
    assert sorted(м for м, _ in вызовы) == ["crm.deal.list", "crm.item.fields", "crm.item.list"]
    assert set(dl.ПОЛЯ_СДЕЛКИ) <= set(next(p for м, p in вызовы if м == "crm.deal.list")["select"])
    assert ix._СВЯЗИ["сделки"]["41"] == ("41", "Сделка выдуманная", "901", "NEW",
                                          "2026-09-02T09:00:00+03:00")


def test_запись_связей_не_роняет_разбор_без_таблиц(monkeypatch, capsys):
    class Курсор:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            pass

        def fetchone(self):
            return (False,)

    class Соединение:
        def cursor(self):
            return Курсор()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(ix, "connect", lambda: Соединение())
    monkeypatch.setattr(ix, "_СВЯЗИ", {"карточки": {"1": ("1", "2", None, None)}, "сделки": {}})
    ix.записать_связи()
    assert "::warning::нет таблиц lib_rfq_cards" in capsys.readouterr().out


# ── наше КП ──────────────────────────────────────────────────────────────────

def test_поля_нашего_кп_только_точным_кодом():
    assert set(doc_folder.ПОЛЯ_НАШЕГО_КП) == {"ufCrm_1585568303498", "ufCrm_1733957302549",
                                              "ufCrm_1783934184627"}
    assert doc_folder.наше_кп_сделки("поле сделки", НАШЕ)
    assert doc_folder.наше_кп_сделки("поле сделки", "UF_CRM_1585568303498")   # crm.deal.*
    assert not doc_folder.наше_кп_сделки("поле сделки", RESULT_FILE)
    assert not doc_folder.наше_кп_сделки("поле сделки", СПЕЦИФИКАЦИЯ)
    assert not doc_folder.наше_кп_сделки("поле запроса", НАШЕ)
    # Код неизвестен — по названию «Offer from us» цены не будет.
    assert not doc_folder.наше_кп_сделки("поле сделки", "ufCrm_999")


def _позиция(fid="77", deal="41"):
    return {"segment_id": None, "item_name": "Клапан выдуманный", "part_number": "KL-7",
            "oem": "", "unit": "шт", "qty": 2, "source_file": fid, "deal_id": deal,
            "_цена": {"price": 120.0, "currency": "USD", "confidence": "high", "total": 240.0}}


def test_цена_сделки_только_нашего_кп_и_только_своим_потоком(monkeypatch):
    monkeypatch.setattr(ix, "SOURCE", "deals")
    rec = {"origin": "поле сделки", "field": НАШЕ, "side": "мы"}
    monkeypatch.setattr(ix, "НАШЕ_КП", False)
    assert ix.цены_файла(rec) is False and ix.строки_цен(rec, [_позиция()]) == []
    monkeypatch.setattr(ix, "НАШЕ_КП", True)
    assert ix.цены_файла(rec) is True
    for чужое in (RESULT_FILE, СПЕЦИФИКАЦИЯ, "ufCrm_1577091983333"):
        assert ix.цены_файла({"origin": "поле сделки", "field": чужое, "side": "поставщик"}) is False
    строки = ix.строки_цен(rec, [_позиция()])
    assert len(строки) == 1
    к = dict(zip(price_store.КОЛОНКИ, строки[0]))
    assert (к["feed"], к["source"]) == (price_store.FEED_НАШЕ_КП, price_store.ИСТОЧНИК_НАШЕ_КП)
    assert к["rfq_id"] == "41" and к["rfq_company"] is None
    assert price_store.FEED_НАШЕ_КП != price_store.FEED
    # Карточка запроса по-прежнему пишет «разбор КП».
    monkeypatch.setattr(ix, "SOURCE", "rfq")
    assert ix.поток_цены() == (price_store.ИСТОЧНИК, price_store.FEED)


def test_незакупочные_потоки_одним_списком_у_всех_читателей():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cwp", ROOT / "scripts" / "codes_with_prices.py")
    cwp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cwp)
    assert set(cwp.НЕ_ЗАКУПОЧНЫЕ) == set(price_store.НЕ_ЗАКУПОЧНЫЕ)
    assert price_store.FEED_НАШЕ_КП in price_store.НЕ_ЗАКУПОЧНЫЕ
    import load_parts
    import load_tkp_prices
    assert load_tkp_prices.FEED == price_store.FEED_ТКП_КВАНТ
    assert load_parts.FEED_КОНКУРЕНТЫ == price_store.FEED_КОНКУРЕНТЫ
    import codes_sql
    buy = codes_sql.PRICE_SETS[codes_sql.PRICE_SETS.index("buy_codes"):]
    for поток in price_store.НЕ_ЗАКУПОЧНЫЕ:
        assert f"'{поток}'" in buy, поток
    lookup = без_комментариев((ROOT / "scripts" / "part_lookup.py").read_text(encoding="utf-8"))
    for имя in ("ЦЕНЫ", "УСЛОВИЯ", "ПОДРОБНОСТИ"):
        sql = re.search(имя + r' = """(.*?)"""', lookup, re.S).group(1)
        for поток in price_store.НЕ_ЗАКУПОЧНЫЕ:
            assert f"'{поток}'" in sql, (имя, поток)


def test_читатели_предложений_берут_только_разбор_кп():
    """Кто читает «предложения поставщиков», тот называет поток явно."""
    import crossref
    assert crossref.FEED == price_store.FEED
    for путь in ("library/supabase/portal_schema.sql", "library/supabase/portal_entity_schema.sql",
                 "library/supabase/suppliers_schema.sql"):
        текст = без_комментариев((ROOT / путь).read_text(encoding="utf-8"), "--")
        assert price_store.FEED_НАШЕ_КП not in текст
        for m in re.finditer(r"from lib_prices(?:_live)? p\b(.{0,400})", текст, re.S):
            assert "p.feed = 'разбор КП'" in m.group(1), (путь, m.group(0)[:120])


# ── переразбор нашего КП ─────────────────────────────────────────────────────

def test_переразбор_нашего_кп_отбирает_по_коду_и_судит_ценой(monkeypatch):
    import reparse
    monkeypatch.setattr(reparse, "ПАПКА", doc_folder.НАШЕ_ПРЕДЛОЖЕНИЕ)
    monkeypatch.setattr(reparse, "НАШЕ_КП_РЕЖИМ", True)
    assert reparse.в_папке(НАШЕ, "Offer from us")
    assert not reparse.в_папке(RESULT_FILE, "Result file")
    assert not reparse.в_папке("ufCrm_999", "Offer from us")      # только по коду
    assert reparse.стало_хуже("deals", 3, 2, 10, 50)                # цен меньше — хуже
    assert not reparse.стало_хуже("deals", 3, 3, 50, 10)            # позиции не мерило
    monkeypatch.setattr(reparse, "НАШЕ_КП_РЕЖИМ", False)
    monkeypatch.setattr(reparse, "ПАПКА", doc_folder.ЗАПРОС_ЗАКАЗЧИКА)
    assert reparse.в_папке(СПЕЦИФИКАЦИЯ, None) and not reparse.в_папке(НАШЕ, None)
    код = без_комментариев((ROOT / "library" / "reparse.py").read_text(encoding="utf-8"))
    # Режим пишет только цены: ветка записи выходит до пометок спроса и UPDATE файла.
    i = код.index("cur.execute(price_store.СНЯТЬ")
    ветка = код[i:код.index("return", i)]
    assert "price_store.записать" in ветка and "lib_demand" not in ветка and "lib_files" not in ветка


# ── прогоны ──────────────────────────────────────────────────────────────────

def _вход(wf):
    return (wf.get("on") or wf.get(True))["workflow_dispatch"]["inputs"]


def test_прогон_досчёта_связей():
    wf = yaml.safe_load((ROOT / ".github/workflows/library-deal-links.yml").read_text(encoding="utf-8"))
    триггеры = wf.get("on") or wf.get(True)
    assert set(триггеры) == {"workflow_dispatch"}, "расписание — решение владельца"
    from tests.test_backfill_file_dates import группа_очереди
    for plan, rollback, группа in ((False, "", "bitrix-portal"), (True, "", "library-deal-links-db"),
                                   (False, "lk-1", "library-deal-links-db")):
        assert группа_очереди(wf, plan, rollback) == группа
    assert wf["concurrency"]["cancel-in-progress"] is False
    входы = _вход(wf)
    assert входы["apply"]["default"] is False and входы["plan"]["default"] is False
    assert входы["shards"]["options"] == ["10", "25", "50"]
    работа = wf["jobs"]["backfill"]
    assert работа["env"]["BITRIX_PARALLEL"] == "1"
    шаг = работа["steps"][-1]
    assert шаг["run"].strip() == "python library/backfill_deal_links.py"
    for имя, вход in (("ENTITIES", "entities"), ("SHARDS", "shards"), ("LIMIT", "limit"),
                      ("ROLLBACK", "rollback")):
        assert шаг["env"][имя] == "${{ inputs." + вход + " }}", имя
    assert шаг["env"]["RUN_ID"].startswith("lk-")


def test_миграция_связей_подключена_после_поставщиков():
    миграции = (ROOT / ".github/workflows/zip-db.yml").read_text(encoding="utf-8")
    assert "apply library/supabase/deal_links_schema.sql" in миграции
    assert (миграции.index("apply library/supabase/suppliers_schema.sql")
            < миграции.index("apply library/supabase/deal_links_schema.sql")
            < миграции.index("apply zip/supabase/migrations_rls_stage1.sql"))


def test_вход_нашего_кп_в_разборе():
    wf = yaml.safe_load((ROOT / ".github/workflows/library-index.yml").read_text(encoding="utf-8"))
    входы = _вход(wf)
    assert входы["our_offer"]["default"] is False
    assert len(входы) <= 25, "у workflow_dispatch не больше 25 входов"
    шаг = next(s for s in wf["jobs"]["index"]["steps"] if s.get("name", "").startswith("Разбор части"))
    assert шаг["env"]["OUR_OFFER_PRICES"] == "${{ inputs.our_offer && '1' || '' }}"
    assert doc_folder.НАШЕ_ПРЕДЛОЖЕНИЕ in шаг["env"]["REPARSE_FOLDER"]
