"""Свод чатов и договоров → закрытый ключ KV realctx:v1 (scripts/realization_ctx.py).

  · свод проходит тот же разбор, что читает сборка (dealctx.clean), без отброшенных;
  · исход и флаги прогона сверяются с пересчётом по строкам: не сошлось — «разошлось»,
    без флагов, код 1 после записи остального;
  · прогон до 23.10.2026 (без исхода) — пересчётом;
  · запись не по форме — стоп, ключ не тронут;
  · в ключе нет условий договоров и цитат;
  · финальная экономика — только сверенные файлы, второй сверкой; не прошла — код 1;
  · на настоящей базе — запросы к обеим схемам (при LIBRARY_SQL_TEST_DSN).

Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import contract_terms as ct  # noqa: E402
import dealctx  # noqa: E402
import realization_ctx as rc  # noqa: E402

T = dt.datetime(2026, 10, 23, 4, 10, tzinfo=dt.timezone.utc)


def _строка(deal: int, run: str, card: int, side: str, *, status="ok", order=None, terms=None, dropped=None):
    прочитан = status in ct.ПРОЧИТАН
    return {"deal_id": deal, "run_id": run, "card_entity": 1174, "card_id": card, "file_id": f"f{card}",
            "order_id": order, "side": side if прочитан else None, "side_company": side, "side_link": None,
            "side_template": None, "status": status,
            "terms": ({"side": side, **(terms or {})} if прочитан else None), "dropped": dropped or []}


ЗАКАЗЧИК = {"penaltyPctDay": 0.1, "penaltyCapPct": 10, "deliveryDays": 90}
ПОСТАВЩИК = {"deliveryDays": 60}


class Store:
    def __init__(self, raw=None):
        self.raw = raw
        self.puts = 0

    def get_raw(self):
        return self.raw

    def put(self, raw):
        self.puts += 1
        self.raw = raw


class База:
    """Подпорка закрытой базы: чаты и выбор прогонов — как вернули бы запросы."""

    def __init__(self, чаты=None, выбор=None, строки=None, флаги=None, экономика=None):
        self._чаты, self._выбор = чаты or [], выбор or []
        self._строки, self._флаги = строки or [], флаги or []
        self._экономика = экономика or []

    def чаты(self):
        return self._чаты

    def договоры(self):
        return self._выбор, self._строки, self._флаги

    def экономика(self):
        return self._экономика


def _чат(deal, **kw):
    r = {"deal": deal, "checked": T, "human": T - dt.timedelta(days=1), "at": T, "state": "ждёт_нас",
         "direction": "стоит", "risk": "высокий", "ball": "кам", "reply": True, "due": dt.date(2026, 10, 25),
         "dueKind": "отгрузка", "kinds": ["срок"], "blocker": {"kind": "ответ", "on_role": "кам"}, "conf": 0.75}
    r.update(kw)
    return r


def _пара(deal=101, run="ct-2"):
    строки = [_строка(deal, run, 1, "customer", terms=ЗАКАЗЧИК),
              _строка(deal, run, 2, "supplier", order=77, terms=ПОСТАВЩИК)]
    флаги, исход = ct.флаги_сделки(строки)
    assert исход == "пара" and флаги, "корпус должен давать флаги"
    return строки, [dict(f, run_id=run) for f in флаги]


def test_свод_по_форме_и_без_условий():
    строки, флаги = _пара()
    база = База(чаты=[_чат(11), {"deal": 12, "checked": T, "human": None, "at": None}],
                выбор=[{"deal": 101, "run": "ct-2", "outcome": "пара", "at": T},
                       {"deal": 102, "run": "ct-2", "outcome": "нет договоров", "at": T}],
                строки=строки, флаги=флаги)
    store = Store()
    журнал: list[str] = []
    assert rc.publish(база, store, dry=False, ct=ct, печать=журнал.append) == 0
    assert store.puts == 1
    свод = json.loads(store.raw.decode("utf-8"))
    разбор = dealctx.clean(свод)
    assert разбор["bad"] == 0
    assert свод["chats"]["11"]["blocker"] == {"kind": "ответ", "on": "кам"}
    assert свод["chats"]["11"]["due"] == "2026-10-25" and свод["chats"]["11"]["conf"] == 0.75
    assert свод["chats"]["12"]["at"] is None
    assert свод["contracts"]["101"]["outcome"] == "пара"
    assert {f["code"] for f in свод["contracts"]["101"]["flags"]} == {f["code"] for f in флаги}
    assert свод["contracts"]["101"]["read"] == {"customer": 1, "supplier": 1}
    assert свод["contracts"]["102"] == {"at": T.isoformat(), "outcome": "нет договоров",
                                        "read": {"customer": 0, "supplier": 0}, "flags": []}
    текст = store.raw.decode("utf-8")
    for слово in ("penaltyPctDay", "deliveryDays", "terms", "quote"):
        assert слово not in текст, "условия договоров в ключ не идут"
    assert any("исходы: нет договоров 1 · пара 1" in s for s in журнал)
    assert not any("101" in s or "ct-2" in s for s in журнал), "в журнал — только счётчики"


def test_расхождение_с_пересчётом_помечается_и_краснит_шаг():
    строки, флаги = _пара()
    # прогон записал «нет пары», а по строкам — пара
    V = ct.METHOD_VER
    база = База(выбор=[{"deal": 101, "run": "ct-2", "outcome": "нет пары", "at": T, "ver": V}], строки=строки,
                флаги=флаги)
    store = Store()
    журнал: list[str] = []
    assert rc.publish(база, store, dry=False, ct=ct, печать=журнал.append) == 1
    свод = json.loads(store.raw.decode("utf-8"))
    assert свод["contracts"]["101"]["outcome"] == "разошлось" and свод["contracts"]["101"]["flags"] == []
    assert any(s.startswith("::error::договоры: у 1 сделок") for s in журнал)
    # флаг в базе, которого пересчёт не даёт
    лишний = флаги + [{"deal_id": 101, "run_id": "ct-2", "order_id": 77, "code": "cisg", "days": None}]
    база = База(выбор=[{"deal": 101, "run": "ct-2", "outcome": "пара", "at": T, "ver": V}], строки=строки, флаги=лишний)
    assert rc.publish(база, Store(), dry=False, ct=ct, печать=lambda s: None) == 1
    # флаги при исходе не «пара»
    недочит = [_строка(101, "ct-2", 1, "customer", terms=ЗАКАЗЧИК), _строка(101, "ct-2", 2, "supplier", status="time")]
    база = База(выбор=[{"deal": 101, "run": "ct-2", "outcome": "недочитана", "at": T, "ver": V}], строки=недочит,
                флаги=флаги)
    s = Store()
    assert rc.publish(база, s, dry=False, ct=ct, печать=lambda s: None) == 1
    assert json.loads(s.raw)["contracts"]["101"]["outcome"] == "разошлось"
    # прежние правила или прогон до 23.10 — помечается, но шаг не краснеет каждую ночь
    for прежний in ({"outcome": "нет пары", "ver": "старая"}, {"outcome": None}):
        журнал: list[str] = []
        база = База(выбор=[{"deal": 101, "run": "ct-2", "at": T, **прежний}],
                    строки=строки, флаги=лишний)
        s = Store()
        assert rc.publish(база, s, dry=False, ct=ct, печать=журнал.append) == 0
        assert json.loads(s.raw)["contracts"]["101"]["outcome"] == "разошлось"
        assert any(x.startswith("::warning::договоры: у 1 сделок прежних") for x in журнал)


def test_прежняя_недочитана_с_нечитаемым_файлом_сходится_с_пересчётом():
    """До 25.10.2026 «не читается» (закачка, формат, предел) писалось «недочитана»:
    пересчёт нынешним кодом точнее — вкладка показывает его, а не «разошлось», и шаг
    не краснеет. Обратного схождения нет."""
    V = ct.METHOD_VER
    строки = [_строка(101, "ct-2", 1, "customer", terms=ЗАКАЗЧИК), _строка(101, "ct-2", 2, "supplier", status="download")]
    база = База(выбор=[{"deal": 101, "run": "ct-2", "outcome": "недочитана", "at": T, "ver": V}], строки=строки)
    s = Store()
    assert rc.publish(база, s, dry=False, ct=ct, печать=lambda x: None) == 0
    assert json.loads(s.raw)["contracts"]["101"]["outcome"] == "не читается"
    недочит = [_строка(101, "ct-2", 1, "customer", terms=ЗАКАЗЧИК), _строка(101, "ct-2", 2, "supplier", status="budget")]
    база = База(выбор=[{"deal": 101, "run": "ct-2", "outcome": "не читается", "at": T, "ver": V}], строки=недочит)
    s = Store()
    assert rc.publish(база, s, dry=False, ct=ct, печать=lambda x: None) == 1
    assert json.loads(s.raw)["contracts"]["101"]["outcome"] == "разошлось"


def test_порядок_строк_выгрузки_как_у_прогона_чтения():
    """Свод условий берёт первое заполненное: пересчёт в выгрузке обязан видеть строки
    сделки в том же порядке, что прогон чтения (contract_terms.ПОРЯДОК_ВИДОВ,
    порядок_строки), иначе назвал бы сделку «разошлось»."""
    import re
    m = re.fullmatch(r"array_position\(array\[([\d, ]+)\], card_entity\), (.+)", rc.ПОРЯДОК_СТРОК_SQL)
    assert m and tuple(int(x) for x in m.group(1).split(",")) == ct.ПОРЯДОК_ВИДОВ
    assert m.group(2) == "card_id desc, length(file_id) desc, file_id desc"
    src = (ROOT / "scripts" / "realization_ctx.py").read_text(encoding="utf-8")
    assert "order by deal_id, {ПОРЯДОК_СТРОК_SQL}" in src
    assert "(card_entity <> 1174)" not in src, "прежний порядок «разовый раньше рамочного» не знает 132/172/175"
    # Python: ключ порядка документов и строк один
    строки = [{"card_entity": e, "card_id": c, "file_id": f} for e, c, f in (
        (175, 9, "5"), (1192, 3, "7"), (172, 50, "99"), (132, 8, "1"), (1174, 2, "10"), (1174, 2, "9"),
        (1174, 4, ""), (172, 51, "1"))]
    assert [(r["card_entity"], r["card_id"], r["file_id"]) for r in sorted(строки, key=ct.порядок_строки)] == [
        (1174, 4, ""), (1174, 2, "10"), (1174, 2, "9"), (132, 8, "1"), (172, 51, "1"), (172, 50, "99"),
        (1192, 3, "7"), (175, 9, "5")]
    assert rc.НЕ_ЗАТИРАЮТ == ("недочитана", "не читается") and set(rc.НЕ_ЗАТИРАЮТ) <= set(ct.ИСХОДЫ)


def test_прежний_прогон_без_исхода_берётся_пересчётом():
    строки, флаги = _пара(run="ct-old")
    база = База(выбор=[{"deal": 101, "run": "ct-old", "outcome": None, "at": T}], строки=строки, флаги=флаги)
    s = Store()
    журнал: list[str] = []
    assert rc.publish(база, s, dry=False, ct=ct, печать=журнал.append) == 0
    assert json.loads(s.raw)["contracts"]["101"]["outcome"] == "пара"
    assert any("пересчёт прежних 1" in x for x in журнал)


def test_запись_не_по_форме_ключ_не_трогает():
    база = База(чаты=[_чат(11, state="думает")])
    s = Store(b"old")
    assert rc.main(["--publish"], база=база, store=s, ct=ct) == 1
    assert s.raw == b"old" and s.puts == 0


def _эк(deal, **kw):
    r = {"deal": deal, "at": T, "status": "сверено", "cur": "RUB", "sale": 2_600_000, "saleEur": 26_000,
         "buyEur": 20_000, "markup": 0.3, "rebid": False, "items": 3, "fx": {"EUR": 100.0}}
    r.update(kw)
    return r


def test_экономика_в_своде_только_сверенная():
    from decimal import Decimal
    # из базы числа приходят numeric → Decimal
    база = База(экономика=[_эк(201, sale=Decimal("2600000.00"), markup=Decimal("0.300000"), rebid=True)])
    s = Store()
    журнал: list[str] = []
    assert rc.publish(база, s, dry=False, ct=ct, печать=журнал.append) == 0
    свод = json.loads(s.raw)
    assert свод["econ"]["201"] == {"at": T.isoformat(), "sale": 2_600_000.0, "cur": "RUB", "saleEur": 26_000.0,
                                   "buyEur": 20_000.0, "markup": 0.3, "rebid": True, "items": 3, "fx": {"EUR": 100.0},
                                   "kpSale": None, "ratio": None, "note": None}
    assert dealctx.clean(свод)["econ"]["201"]["sale"] == 2_600_000.0
    assert any("финальная экономика: сделок со сверенным файлом 1 (переторжка 1)" in x for x in журнал)
    assert not any("2600000" in x or "201" in x for x in журнал), "в журнал — только счётчики"


def test_экономика_сверка_с_суммой_на_день_чтения_в_своде():
    """Отношение «Сумма» / продажа на день чтения — в своде: по нему сборка видит «Сумму»,
    изменённую после чтения файла (минорное ревизии)."""
    from decimal import Decimal
    база = База(экономика=[_эк(201, ratio=Decimal("1.050000")), _эк(202, ratio=None, note="суммы нет")])
    s = Store()
    assert rc.publish(база, s, dry=False, ct=ct, печать=lambda x: None) == 0
    свод = json.loads(s.raw)
    assert (свод["econ"]["201"]["ratio"], свод["econ"]["201"]["note"]) == (1.05, None)
    assert (свод["econ"]["202"]["ratio"], свод["econ"]["202"]["note"]) == (None, "суммы нет")
    разбор = dealctx.clean(свод)
    assert разбор["bad"] == 0 and разбор["econ"]["201"]["ratioAtRead"] == 1.05


def test_экономика_продажа_кп_у_переторжки_в_своде():
    """Вторая ревизия, 2б: у переторжки — продажа «Нашего КП» (kpSale); у прочих её нет, а
    продажа КП без переторжки или не число — не прошла вторую сверку."""
    from decimal import Decimal
    база = База(экономика=[_эк(201, rebid=True, kpSale=Decimal("3000000.00")), _эк(202, kpSale=3_000_000),
                           _эк(203, rebid=True, kpSale=-5)])
    s = Store()
    журнал: list[str] = []
    assert rc.publish(база, s, dry=False, ct=ct, печать=журнал.append) == 1
    свод = json.loads(s.raw)
    assert set(свод["econ"]) == {"201"} and свод["econ"]["201"]["kpSale"] == 3_000_000.0
    assert dealctx.clean(свод)["econ"]["201"]["kpSale"] == 3_000_000.0


def test_свод_экономики_читает_только_вид_прошедших_гейт():
    """Ревизия, дефект 3: свод (и ночной chat-context.yml) берёт экономику только из вида
    econ_file_latest, а вид — только строки прогонов из econ_file_runs."""
    src = (ROOT / "scripts" / "realization_ctx.py").read_text(encoding="utf-8")
    assert "from econ_file_latest" in src and "from econ_file_reads" not in src
    sql = (ROOT / "library" / "supabase" / "econ_files_schema.sql").read_text(encoding="utf-8")
    вид = sql[sql.index("create view econ_file_latest"):]
    assert "join econ_file_runs g on g.run_id = r.run_id" in вид[:вид.index(";")]
    шаг = _wf("chat-context.yml")["jobs"]["context"]["steps"][-1]
    assert шаг["run"] == "python scripts/realization_ctx.py --publish"


def test_экономика_не_прошла_вторую_сверку():
    плохие = [_эк(202, markup=0.9), _эк(203, saleEur=10_000), _эк(204, status="итоги не сходятся"),
              _эк(205, buyEur=None)]
    s = Store()
    журнал: list[str] = []
    assert rc.publish(База(экономика=[_эк(201)] + плохие), s, dry=False, ct=ct, печать=журнал.append) == 1
    assert set(json.loads(s.raw)["econ"]) == {"201"}, "ключ записан, непроверенного в нём нет"
    assert any(x.startswith("::error::финальная экономика: у 4 сделок") for x in журнал)


def test_вхолостую_ключ_не_пишется():
    s = Store()
    assert rc.publish(База(чаты=[_чат(11)]), s, dry=True, ct=ct, печать=lambda x: None) == 0
    assert s.puts == 0


def test_выгрузка_в_файл(tmp_path):
    p = tmp_path / "ctx.json"
    assert rc.fetch(p, Store(None), печать=lambda s: None) == 0 and not p.exists()
    raw = json.dumps({"version": 1, "at": T.isoformat(), "chats": {}, "contracts": {}}).encode()
    assert rc.fetch(p, Store(raw), печать=lambda s: None) == 0
    assert dealctx.load(str(p))["bad"] == 0
    with pytest.raises(rc.CtxError):
        rc.fetch(p, Store(b'{"version": 9}'), печать=lambda s: None)


def test_ключ_свой_и_единственный():
    assert rc.KV_KEY == "realctx:v1"
    src = (ROOT / "public" / "_worker.js").read_text(encoding="utf-8")
    assert "realctx" not in src, "воркер портала ключ не читает: свод уходит только в сборку"


# ── настоящая база ────────────────────────────────────────────────────────────

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
СХЕМА = "realization_ctx_test"


@pytest.fixture
def conn():
    if not DSN:
        pytest.skip("одноразовая база PostgreSQL не настроена")
    import psycopg2
    from tests.test_library_schema_sql import операторы
    с = psycopg2.connect(DSN)
    с.autocommit = True
    c = с.cursor()
    c.execute(f"drop schema if exists {СХЕМА} cascade")
    c.execute(f"create schema {СХЕМА}")
    c.execute(f"set search_path to {СХЕМА}")
    for имя in ("deal_chat_schema.sql", "contract_terms_schema.sql", "econ_files_schema.sql"):
        for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
            c.execute(оператор)
    try:
        yield с
    finally:
        с.cursor().execute(f"drop schema if exists {СХЕМА} cascade")
        с.close()


def test_на_настоящей_базе(conn):
    c = conn.cursor()
    verdict = {"state": "стоит", "direction": "назад", "risk": "высокий", "risk_kinds": ["срок", "оплата"],
               "blocker": {"kind": "поставка", "on_role": "поставщик"}, "ball_role": "сопровождение",
               "reply_expected": False, "due": {"date": "2026-10-20", "kind": "отгрузка"},
               "evidence_msg_ids": [10], "confidence": 0.8}
    # 11 — оценена; 12 — прочитана, людей нет; 13 — чтение не удалось ни разу (read_at пуст): не в своде
    c.execute("insert into deal_chat_state (deal_id, chat_id, last_msg_id, last_human_at, checked_at, run_id, "
              "method_ver, read_at) values (11, 911, 12, %s, %s, 'chat-ctx-1', 'v1', null), "
              "(12, 912, 0, null, %s, 'chat-ctx-1', 'v1', %s), (13, 913, 0, null, %s, 'chat-ctx-1', 'v1', null)",
              (T - dt.timedelta(days=2), T + dt.timedelta(days=3), T, T, T + dt.timedelta(days=3)))
    c.execute("insert into deal_chat_ctx (deal_id, assessed_at, model, prompt_sha256, method_ver, msg_from_id, "
              "msg_to_id, verdict, run_id) values (11, %s, 'claude-x', %s, 'v1', 10, 12, %s, 'chat-ctx-1')",
              (T, "a" * 64, json.dumps(verdict, ensure_ascii=False)))
    # договоры: прогон до 23.10 (без исхода, с флагами) и новый с исходом
    строки_старые, флаги_старые = _пара(deal=101, run="ct-old")
    строки_новые = [_строка(102, "ct-new", 5, "customer", terms=ЗАКАЗЧИК)]
    база_записи = ct.База(DSN, схема=СХЕМА)
    try:
        for r in строки_старые + строки_новые:
            r.update(method_ver=ct.METHOD_VER, side_source="model" if r["side"] else None, template=None,
                     doc_type=None, file_field="f", sha256=None, pages=None, note=None, model="claude-x",
                     evidence=None, usd=0, link_source="deal_link")
        база_записи.записать_условия(строки_старые)
        база_записи.записать_флаги(флаги_старые, "ct-old")
        база_записи.записать_условия(строки_новые)
        база_записи.записать_итог([], {102: "нет пары", 103: "нет договоров"}, "ct-new")
        # позже прогон, где сделке 102 не хватило бюджета: «недочитана» не затирает разбор,
        # и «не читается» (файл не скачался) — тоже
        база_записи.записать_итог([], {102: "недочитана"}, "ct-newer")
        # у сделки 104 только эти два: берётся последний из них («не читается», файл не скачан)
        for run, статус in (("ct-newest", "budget"), ("ct-newest-2", "download")):
            r = _строка(104, run, 7, "supplier", status=статус, order=77)
            r.update(method_ver=ct.METHOD_VER, side_source=None, template=None, doc_type=None, file_field="f",
                     sha256=None, pages=None, note=None, model=None, evidence=None, usd=0, link_source="order_only")
            база_записи.записать_условия([r])
        база_записи.записать_итог([], {102: "не читается", 104: "недочитана"}, "ct-newest")
        база_записи.записать_итог([], {104: "не читается"}, "ct-newest-2")
    finally:
        база_записи.закрыть()
    # финальная экономика: сверенная строка и строка со сбоем шаблона
    import econ_files as ef
    эк = ef.База(DSN, схема=СХЕМА)
    try:
        общее = {"method_ver": ef.METHOD_VER, "file_key": "f" * 64, "sub_cur": "EUR", "items": 2, "bad_lines": 0,
                 "fx": {"EUR": 1.0}, "deal_cur": "EUR", "bitrix_note": None, "coef_median": 1.4, "totals_dev": 0,
                 "why": None, "kp_sale_sub": None}
        эк.записать({**общее, "run_id": "ef-1", "deal_id": 11, "status": "сверено", "sale_sub": 14_000,
                     "sale_eur": 14_000, "purchase_eur": 10_000, "markup": 0.4, "rebid": True, "bitrix_ratio": 1,
                     "kp_sale_sub": 16_000})
        эк.записать({**общее, "run_id": "ef-1", "deal_id": 12, "status": "шаблон другой", "sale_sub": None,
                     "sale_eur": None, "purchase_eur": None, "markup": None, "rebid": None, "bitrix_ratio": None,
                     "fx": None, "coef_median": None, "totals_dev": None})
        assert эк.отметить_прогон("ef-1") == (2, 1)
        # позже прогон, который упал до гейта: его более новое чтение 11 вкладке не видно
        эк.записать({**общее, "run_id": "ef-2", "deal_id": 11, "status": "сверено", "sale_sub": 15_000,
                     "sale_eur": 15_000, "purchase_eur": 10_000, "markup": 0.5, "rebid": False, "bitrix_ratio": 1})
    finally:
        эк.закрыть()
    база = rc.База(DSN, схема=СХЕМА)
    try:
        s = Store()
        журнал: list[str] = []
        assert rc.publish(база, s, dry=False, ct=ct, печать=журнал.append) == 0
    finally:
        база.закрыть()
    свод = json.loads(s.raw)
    assert dealctx.clean(свод)["bad"] == 0
    assert set(свод["econ"]) == {"11"} and свод["econ"]["11"]["markup"] == 0.4 and свод["econ"]["11"]["rebid"]
    assert свод["econ"]["11"]["kpSale"] == 16_000.0, "продажа «Нашего КП» — из вида"
    ч = свод["chats"]
    assert ч["11"]["state"] == "стоит" and ч["11"]["kinds"] == ["срок", "оплата"] and ч["11"]["due"] == "2026-10-20"
    assert ч["11"]["blocker"] == {"kind": "поставка", "on": "поставщик"} and ч["11"]["reply"] is False
    assert ч["11"]["conf"] == 0.8, "уверенность — из вычисляемой колонки базы"
    assert ч["12"]["at"] is None and ч["12"]["human"] is None
    # свежесть — по удачному чтению: у 11 read_at пуст (строка до 23.10) — время оценки,
    # а не checked_at, который двигает и неудачное чтение
    assert ч["11"]["checked"] == T.isoformat() and "13" not in ч
    д = свод["contracts"]
    assert д["101"]["outcome"] == "пара" and len(д["101"]["flags"]) == len(флаги_старые)
    assert д["102"] == {"at": д["102"]["at"], "outcome": "нет пары", "read": {"customer": 1, "supplier": 0},
                        "flags": []}
    assert д["103"]["outcome"] == "нет договоров"
    assert д["104"]["outcome"] == "не читается" and д["104"]["read"] == {"customer": 0, "supplier": 0}
    assert any("исход прогона 3, пересчёт прежних 1" in x for x in журнал)


def test_выгрузка_по_настоящей_базе_в_порядке_прогона_чтения(conn):
    """Строки одной сделки, записанные вперемешку, выгрузка отдаёт в порядке
    contract_terms.порядок_строки — тем же, в котором их сводил прогон чтения; рамочный
    у двух сделок одного прогона — строка у каждой (ключ с deal_id)."""
    import random
    def r(deal, entity, card, fid, side="customer", order=None):
        x = _строка(deal, "ct-ord", card, side, order=order, terms={"deliveryDays": card % 97})
        x.update(card_entity=entity, file_id=fid, method_ver=ct.METHOD_VER, side_source="model", template=None,
                 doc_type=None, file_field="f", sha256=None, pages=None, note=None, model="claude-x",
                 evidence=None, usd=0, link_source="deal_link")
        return x
    строки = [r(201, 175, 9, "5"), r(201, 1192, 3, "7"), r(201, 172, 50, "99", "supplier", 50),
              r(201, 132, 8, "1"), r(201, 1174, 2, "10"), r(201, 1174, 2, "9"), r(201, 172, 51, "1", "supplier", 51),
              r(202, 175, 9, "5")]
    строки[-1]["link_source"] = "framework"
    random.Random(5).shuffle(строки)
    база_записи = ct.База(DSN, схема=СХЕМА)
    try:
        база_записи.записать_условия(строки)
        база_записи.записать_итог([], {201: "пара", 202: "нет пары"}, "ct-ord")
        assert база_записи.итог("ct-ord")[0] == len(строки), "рамочный второй сделки снят ключом без deal_id"
    finally:
        база_записи.закрыть()
    база = rc.База(DSN, схема=СХЕМА)
    try:
        _выбор, выгружено, _флаги = база.договоры()
    finally:
        база.закрыть()
    у_201 = [(x["card_entity"], x["card_id"], x["file_id"]) for x in выгружено if x["deal_id"] == 201]
    ожидаемо = [(x["card_entity"], x["card_id"], x["file_id"])
                for x in sorted((x for x in строки if x["deal_id"] == 201), key=ct.порядок_строки)]
    assert у_201 == ожидаемо == [(1174, 2, "10"), (1174, 2, "9"), (132, 8, "1"), (172, 51, "1"), (172, 50, "99"),
                                 (1192, 3, "7"), (175, 9, "5")]
    assert [(x["card_entity"], x["card_id"]) for x in выгружено if x["deal_id"] == 202] == [(175, 9)]


# ── расписание и прогоны (решение владельца 23.10.2026) ─────────────────────────

def _wf(имя: str) -> dict:
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / имя).read_text(encoding="utf-8"))
    wf["on"] = wf.get("on", wf.get(True))
    return wf


def test_ночная_оценка_чатов_в_пределах_суток_и_со_сводом():
    wf = _wf("chat-context.yml")
    assert wf["on"]["repository_dispatch"]["types"] == ["chat-context"]
    assert "schedule" not in wf["on"], "время держит планировщик Cloudflare, не крон GitHub"
    env = wf["jobs"]["context"]["env"]
    assert "'live'" in env["MODE"] and "'5'" in env["BUDGET"] and "'10'" in env["PARTS"]
    import re
    assert int(re.search(r"'(\d+)'", env["LIMIT"]).group(1)) <= 400
    assert wf["on"]["workflow_dispatch"]["inputs"]["mode"]["default"] == "dry", "ручной — по-прежнему холостой"
    свод = wf["jobs"]["context"]["steps"][-1]
    assert свод["run"] == "python scripts/realization_ctx.py --publish" and "env.MODE == 'live'" in свод["if"]
    assert "!cancelled()" in свод["if"]
    assert wf["concurrency"]["group"] == "bitrix-portal"


def test_планировщик_зовёт_оба_прогона():
    src = (ROOT / "scheduler" / "worker.js").read_text(encoding="utf-8")
    assert 'if (h === 4) дела.push("chat-context");' in src
    assert 'if (h === 5 && wd === 0) дела.push("contract-terms");' in src
    assert 'if (h === 22) дела.push("econ-files");' in src


def test_все_прогоны_свода_ставят_одно_и_то_же():
    """Свод пишут три прогона (чаты, договоры, экономика) — у всех шаг свода последний,
    только в live и после неудачи тоже."""
    for имя, работа in (("chat-context.yml", "context"), ("contract-terms.yml", "terms"), ("econ-files.yml", "econ")):
        свод = _wf(имя)["jobs"][работа]["steps"][-1]
        assert свод["run"] == "python scripts/realization_ctx.py --publish", имя
        assert "env.MODE == 'live'" in свод["if"] and "!cancelled()" in свод["if"], имя


def test_сборка_читает_свод_а_сверка_правки_ключей_не_получает():
    текст = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "scripts/realization_ctx_kv.py" in текст and "REALCTX_PATH" in текст
    # живая сверка гоняет код правки: ключ Cloudflare ему не отдаётся
    assert "CLOUDFLARE_API_TOKEN" not in (ROOT / ".github" / "workflows" / "live-check.yml").read_text(encoding="utf-8")


def test_подстановка_массивов_одним_проходом():
    """Название сделки «__DEALCTX_JSON__» в общем массиве не втягивает в него закрытую часть."""
    import dashboard
    html = dashboard.render({"kpi": {}}, {}, contracts={"rows": [{"title": "__DEALCTX_JSON__"}]},
                            dealctx={"queue": [], "deals": {"1": {"why": "СЕКРЕТ-ВЫДУМКА"}}})
    строка = next(x for x in html.splitlines() if x.startswith("window.__CONTRACTS__"))
    assert "СЕКРЕТ-ВЫДУМКА" not in строка and "__DEALCTX_JSON__" in строка
    assert next(x for x in html.splitlines() if x.startswith("window.__DEALCTX__")).count("СЕКРЕТ-ВЫДУМКА") == 1
