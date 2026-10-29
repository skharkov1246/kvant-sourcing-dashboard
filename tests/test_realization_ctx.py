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
    V = ct.ВЕРСИЯ_ИСХОДА
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
    """До 25.10.2026 «не читается» (файла нет у портала, формат, предел) писалось
    «недочитана»: пересчёт нынешним кодом точнее — вкладка показывает его, а не
    «разошлось», и шаг не краснеет. Обратного схождения нет. Временный отказ закачки
    тогда тоже писался download: по заметке он и сейчас «недочитана», а не «не
    читается» с пунктом «перезалить файл»."""
    V = ct.ВЕРСИЯ_ИСХОДА
    строки = [_строка(101, "ct-2", 1, "customer", terms=ЗАКАЗЧИК), _строка(101, "ct-2", 2, "supplier", status="download")]
    строки[1]["note"] = "urlMachine: код 404"
    база = База(выбор=[{"deal": 101, "run": "ct-2", "outcome": "недочитана", "at": T, "ver": V}], строки=строки)
    s = Store()
    assert rc.publish(база, s, dry=False, ct=ct, печать=lambda x: None) == 0
    assert json.loads(s.raw)["contracts"]["101"]["outcome"] == "не читается"
    for временный in ("urlMachine: код 503 (лимит портала дольше бюджета ожидания)", "urlMachine: сеть: ReadTimeout",
                      "urlMachine: вместо файла страница входа", "urlMachine: код 502", None):
        строки[1]["note"] = временный
        s = Store()
        assert rc.publish(база, s, dry=False, ct=ct, печать=lambda x: None) == 0, временный
        assert json.loads(s.raw)["contracts"]["101"]["outcome"] == "недочитана", временный
    # непредвиденный сбой подготовки прежде писался format — теперь api: «недочитана»
    строки[1].update(status="format", note="документ не подготовлен (ValueError)")
    s = Store()
    assert rc.publish(база, s, dry=False, ct=ct, печать=lambda x: None) == 0
    assert json.loads(s.raw)["contracts"]["101"]["outcome"] == "недочитана"
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


def test_недочитана_заменяется_прежним_разбором_только_при_тех_же_файлах():
    """Недельный прогон обрывает старые сделки сроком: «недочитана» с теми же файлами —
    прежний полный разбор верен. Нашёлся новый документ (рамочный, Purchase order) или
    пропал прежний — прежние флаги собраны не из того набора: «недочитана». «Не
    читается» (файла нет, формат, предел) не заменяется никогда: нужен человек."""
    def и(run, outcome):
        return {"deal": 101, "run": run, "outcome": outcome, "at": T, "ver": "v"}
    те = frozenset({(1174, 1, "f1"), (1174, 2, "f2")})
    с_рамочным = те | {(175, 9, "f9")}
    исходы = [и("ct-3", "недочитана"), и("ct-2", "недочитана"), и("ct-1", "пара")]
    assert rc.выбор_прогона(исходы, {(101, "ct-3"): те, (101, "ct-1"): те})["run"] == "ct-1"
    assert rc.выбор_прогона(исходы, {(101, "ct-3"): с_рамочным, (101, "ct-1"): те})["run"] == "ct-3"
    assert rc.выбор_прогона(исходы, {(101, "ct-3"): те, (101, "ct-1"): с_рамочным})["run"] == "ct-3"
    for не_читается in ([и("ct-3", "не читается"), и("ct-1", "пара")],
                        [и("ct-3", "не читается"), и("ct-2", "недочитана"), и("ct-1", "пара")]):
        assert rc.выбор_прогона(не_читается, {(101, "ct-3"): те, (101, "ct-1"): те})["run"] == "ct-3"
    assert rc.выбор_прогона([и("ct-2", "недочитана"), и("ct-1", "не читается")], {})["run"] == "ct-2"
    assert rc.выбор_прогона([и("ct-2", "нет пары"), и("ct-1", "пара")], {})["run"] == "ct-2"


def test_purchase_order_в_пересчёте_как_у_прогона():
    """Флаги «по отсутствию» у заказа, где у поставщика только Purchase order, прогон не
    ставит; пересчёт видит link_source строк и сходится, а не «разошлось»."""
    ЗАКАЗЧИК_Д = {**ЗАКАЗЧИК, "documents": ["паспорт ВЫДУМКА"]}
    строки = [_строка(101, "ct-2", 1, "customer", terms=ЗАКАЗЧИК_Д),
              _строка(101, "ct-2", 77, "supplier", order=77, terms=ПОСТАВЩИК)]
    строки[1].update(card_entity=172, link_source="purchase_order")
    флаги, исход = ct.флаги_сделки(строки)
    assert исход == "пара" and not {f["code"] for f in флаги} & {"penaltyUncovered", "docsGap"}
    флаги = [dict(f, run_id="ct-2") for f in флаги]
    база = База(выбор=[{"deal": 101, "run": "ct-2", "outcome": "пара", "at": T, "ver": ct.ВЕРСИЯ_ИСХОДА}],
                строки=строки, флаги=флаги)
    s = Store()
    assert rc.publish(база, s, dry=False, ct=ct, печать=lambda x: None) == 0
    assert json.loads(s.raw)["contracts"]["101"]["outcome"] == "пара"


def test_рамочный_двух_заказов_считается_один_раз():
    """Рамочный поставщика над спецификациями двух заказов — строка на заказ, документ один."""
    строки = [_строка(101, "ct-2", 1, "customer", terms=ЗАКАЗЧИК),
              _строка(101, "ct-2", 9, "supplier", order=77, terms=ПОСТАВЩИК),
              _строка(101, "ct-2", 9, "supplier", order=78, terms=ПОСТАВЩИК)]
    for r in строки[1:]:
        r.update(card_entity=175, link_source="framework")
    флаги, исход = ct.флаги_сделки(строки)
    assert исход == "пара" and {f["order_id"] for f in флаги} == {77, 78}
    база = База(выбор=[{"deal": 101, "run": "ct-2", "outcome": "пара", "at": T, "ver": ct.ВЕРСИЯ_ИСХОДА}],
                строки=строки, флаги=[dict(f, run_id="ct-2") for f in флаги])
    s = Store()
    assert rc.publish(база, s, dry=False, ct=ct, печать=lambda x: None) == 0
    assert json.loads(s.raw)["contracts"]["101"]["read"] == {"customer": 1, "supplier": 1}


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


def test_колонки_выгрузки_по_схеме_договоров():
    """Первый живой прогон экономики 25.10.2026 упал на UndefinedColumn: у рабочей базы схема
    договоров до v2. Нет колонки v2 — null вместо неё и её имя журналу; колонки старше v2 —
    как есть, и их имена — стопу (ревизия: side_link пустым дал бы ложные флаги)."""
    все = set(rc.КОЛОНКИ_УСЛОВИЙ) | {"order_key"}
    выбор, нет, старше = rc.колонки_выгрузки(все)
    assert нет == старше == [] and "null" not in выбор and выбор.split(", ") == list(rc.КОЛОНКИ_УСЛОВИЙ)
    выбор, нет, старше = rc.колонки_выгрузки(все - {"link_source", "order_key"})
    assert (нет, старше) == (["link_source", "order_key"], [])
    assert "null as link_source" in выбор and выбор.count("null") == 1
    assert len(выбор.split(", ")) == len(rc.КОЛОНКИ_УСЛОВИЙ), "порядок и число колонок — как у ключей строки"
    # схема до 22.10.2026 (нет side_link) и без ключа строки — не пустыми: стоп
    for без in ({"side_link"}, {"status"}, {"note"}):
        выбор, нет, старше = rc.колонки_выгрузки(все - без - {"link_source", "order_key"})
        assert старше == sorted(без) and нет == ["link_source", "order_key"]
        assert f"null as {next(iter(без))}" not in выбор
    assert set(rc.СХЕМА_ДОГОВОРОВ_V2) & set(rc.КОЛОНКИ_УСЛОВИЙ) == {"link_source"}
    # side_source (25.10.2026: сторона по карточке) — в выгрузке; без колонки — пустой
    # (сторона модели, как у строк до 25.10.2026), а не стоп
    assert "side_source" in rc.КОЛОНКИ_УСЛОВИЙ
    выбор, нет, старше = rc.колонки_выгрузки(все - {"side_source"})
    assert "null as side_source" in выбор and нет == ["side_source"] and старше == []


def test_свод_по_карточке_гейт_меряет_модель():
    """Строка «по карточке» в своде — не сторона модели: пересчёт для вкладки читает
    side_source и сверяет её так же, как прогон (contract_terms.сторона_модели)."""
    строки, флаги = _пара()
    for r in строки:
        r.setdefault("side_source", "model")
    поставщик = next(r for r in строки if r["side"] == "supplier")
    поставщик.update(side_source="card", side_company="supplier", terms={**поставщик["terms"], "side": None})
    флаги_пересчёта, исход = ct.флаги_сделки(строки)
    assert исход == "пара" and not ct.сторона_спорная(поставщик) and ct.сторона_модели(поставщик) is None
    assert {f["code"] for f in флаги_пересчёта} == {f["code"] for f in флаги}


def test_схема_договоров_старше_v2_стоп():
    """Нет side_link (схема до 22.10.2026) — свод не собирается: CONTRACT_SCHEMA_TOO_OLD с
    именами колонок из постоянного списка, а не флаги по стороне прежнего смысла."""
    class Б(rc.База):
        def __init__(self, колонки):
            self._колонки = колонки

        def есть(self, *таблицы):
            return True

        def колонки(self, таблица):
            return self._колонки

        def _все(self, sql, p=()):
            raise AssertionError("до запроса строк не доходит")

    with pytest.raises(rc.CtxError, match="^CONTRACT_SCHEMA_TOO_OLD:side_link$"):
        Б(set(rc.КОЛОНКИ_УСЛОВИЙ) - {"side_link", "link_source"}).договоры()


def test_схема_договоров_старая_одной_строкой_журнала():
    б = База(чаты=[_чат(11)])
    б.нет_колонок = ["link_source", "order_key"]
    журнал: list[str] = []
    assert rc.publish(б, Store(), dry=True, ct=ct, печать=журнал.append) == 0
    assert [x for x in журнал if "схема договоров старая" in x] == [
        "  схема договоров старая: колонки link_source, order_key нет — в своде они пустые, пока прогон чтения "
        "договоров не применит library/supabase/contract_terms_schema.sql"]
    журнал.clear()
    assert rc.publish(База(чаты=[_чат(11)]), Store(), dry=True, ct=ct, печать=журнал.append) == 0
    assert not any("схема договоров" in x for x in журнал), "схема новая — строки нет"


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
    строки_новые = [_строка(102, "ct-new", 5, "customer", terms=ЗАКАЗЧИК),
                    _строка(105, "ct-new", 6, "customer", terms=ЗАКАЗЧИК),
                    _строка(106, "ct-new", 8, "customer", terms=ЗАКАЗЧИК)]

    def дописать(r, **kw):
        r.update(method_ver=ct.METHOD_VER, side_source="model" if r["side"] else None, template=None,
                 doc_type=None, file_field="f", sha256=None, pages=None, note=None,
                 model="claude-x" if r["side"] else None, evidence=None, usd=0, link_source="deal_link")
        r.update(kw)
        return r
    база_записи = ct.База(DSN, схема=СХЕМА)
    try:
        for r in строки_старые + строки_новые:
            дописать(r)
        база_записи.записать_условия(строки_старые)
        база_записи.записать_флаги(флаги_старые, "ct-old")
        база_записи.записать_условия(строки_новые)
        база_записи.записать_итог([], {102: "нет пары", 103: "нет договоров", 105: "нет пары", 106: "нет пары"},
                                  "ct-new")
        # позже прогон, оборванный сроком: у 102 файлы те же — показывается прежний разбор;
        # у 105 нашёлся рамочный — прежний разбор собран без него: «недочитана»
        база_записи.записать_условия([
            дописать(_строка(102, "ct-newer", 5, "supplier", status="time")),
            дописать(_строка(105, "ct-newer", 6, "supplier", status="time")),
            дописать(_строка(105, "ct-newer", 9, "supplier", status="time"), card_entity=175,
                     link_source="framework")])
        база_записи.записать_итог([], {102: "недочитана", 105: "недочитана"}, "ct-newer")
        # у 106 файл пропал у портала (404): «не читается» прежним разбором не заменяется
        база_записи.записать_условия([дописать(_строка(106, "ct-newest", 8, "supplier", status="download"),
                                               note="urlMachine: код 404")])
        база_записи.записать_итог([], {106: "не читается"}, "ct-newest")
        # у сделки 104 только неполные: берётся последний («не читается», файла нет у портала)
        for run, статус, заметка in (("ct-newest", "budget", None), ("ct-newest-2", "download", "urlMachine: код 404")):
            база_записи.записать_условия([дописать(_строка(104, run, 7, "supplier", status=статус, order=77),
                                                   note=заметка, link_source="order_only")])
        база_записи.записать_итог([], {104: "недочитана"}, "ct-newest")
        база_записи.записать_итог([], {104: "не читается"}, "ct-newest-2")
        # у 107 у поставщика только Purchase order: пересчёт видит link_source и сходится
        строки_по = [дописать(_строка(107, "ct-new", 1, "customer", terms={**ЗАКАЗЧИК, "documents": ["паспорт"]})),
                     дописать(_строка(107, "ct-new", 77, "supplier", order=77, terms={"deliveryDays": 80}),
                              card_entity=172, link_source="purchase_order")]
        флаги_по, исход_по = ct.флаги_сделки(строки_по)
        assert исход_по == "пара" and {f["code"] for f in флаги_по} == {"lateByDesign"}
        база_записи.записать_условия(строки_по)
        база_записи.записать_итог(флаги_по, {107: "пара"}, "ct-new")
    finally:
        база_записи.закрыть()
    # финальная экономика: сверенная строка и строка со сбоем шаблона
    import econ_files as ef
    эк = ef.База(DSN, схема=СХЕМА)
    try:
        общее = {"method_ver": ef.METHOD_VER, "file_key": "f" * 64, "sub_cur": "EUR", "items": 2, "bad_lines": 0,
                 "fx": {"EUR": 1.0}, "deal_cur": "EUR", "bitrix_note": None, "coef_median": 1.4, "totals_dev": 0,
                 "why": None, "kp_sale_sub": None,
                 # пути чтения v5 (правило 16): валюта подачи из D4, без сдвига; курс сверен —
                 # только у сверенной строки (econ_file_reads_src_chk)
                 "sub_cur_src": "D4", "rate_src": None, "col_shift": 0}
        эк.записать({**общее, "run_id": "ef-1", "deal_id": 11, "status": "сверено", "sale_sub": 14_000, "rate_src": "евро",
                     "sale_eur": 14_000, "purchase_eur": 10_000, "markup": 0.4, "rebid": True, "bitrix_ratio": 1,
                     "kp_sale_sub": 16_000})
        эк.записать({**общее, "run_id": "ef-1", "deal_id": 12, "status": "шаблон другой", "sale_sub": None,
                     "sale_eur": None, "purchase_eur": None, "markup": None, "rebid": None, "bitrix_ratio": None,
                     "fx": None, "coef_median": None, "totals_dev": None})
        assert эк.отметить_прогон("ef-1") == (2, 1)
        # позже прогон, который упал до гейта: его более новое чтение 11 вкладке не видно
        эк.записать({**общее, "run_id": "ef-2", "deal_id": 11, "status": "сверено", "sale_sub": 15_000, "rate_src": "евро",
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
    assert д["105"]["outcome"] == "недочитана", "прежний разбор без нового рамочного показан как верный"
    assert д["106"]["outcome"] == "не читается", "нечитаемый файл спрятан прежним разбором"
    assert д["107"]["outcome"] == "пара" and {f["code"] for f in д["107"]["flags"]} == {f["code"] for f in флаги_по}
    assert any("исход прогона 6, пересчёт прежних 1" in x for x in журнал)


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


def test_свод_по_схеме_договоров_до_v2(conn):
    """Первый живой прогон экономики 25.10.2026: у рабочей базы схема договоров до v2 (нет
    link_source и order_key, ключ без заказа) — свод экономики не падает на UndefinedColumn:
    колонки пустые, исход и флаги пересчитываются, журнал говорит одной строкой, каких нет."""
    c = conn.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute("alter table contract_terms drop constraint contract_terms_link_chk")
    c.execute("alter table contract_terms drop column link_source")
    c.execute("alter table contract_terms drop column order_key cascade")       # с ключом строки
    c.execute("alter table contract_terms add primary key (run_id, deal_id, card_entity, card_id, file_id)")
    строки, флаги = _пара(deal=301, run="ct-v1")
    for r in строки:
        r.update(method_ver=ct.METHOD_VER, side_source="model", template=None, doc_type=None, file_field="f",
                 sha256=None, pages=None, note=None, model="claude-x", evidence=None, usd=0)
    база_записи = ct.База(DSN, схема=СХЕМА)
    try:
        # прогон чтения прежней версии link_source не писал
        база_записи.КОЛОНКИ = tuple(к for к in ct.База.КОЛОНКИ if к != "link_source")
        база_записи.записать_условия(строки)
        база_записи.записать_итог(флаги, {301: "пара"}, "ct-v1")
    finally:
        база_записи.закрыть()
    база = rc.База(DSN, схема=СХЕМА)
    try:
        s = Store()
        журнал: list[str] = []
        assert rc.publish(база, s, dry=False, ct=ct, печать=журнал.append) == 0
        _выбор, выгружено, _флаги = база.договоры()
    finally:
        база.закрыть()
    assert {x["link_source"] for x in выгружено} == {None}
    д = json.loads(s.raw)["contracts"]
    assert д["301"]["outcome"] == "пара" and len(д["301"]["flags"]) == len(флаги)
    assert sum(1 for x in журнал if x.startswith("  схема договоров старая: колонки link_source, order_key нет")) == 1


def test_свод_по_схеме_договоров_до_22_10_стоп(conn):
    """Схема договоров старше (нет и side_link, 22.10.2026) — side там прежнего смысла: свод
    не собирается и ключ не пишется (CONTRACT_SCHEMA_TOO_OLD), а не флаги по ложной стороне."""
    c = conn.cursor()
    c.execute(f"set search_path to {СХЕМА}")
    c.execute("alter table contract_terms drop constraint contract_terms_link_chk")
    c.execute("alter table contract_terms drop column link_source")
    c.execute("alter table contract_terms drop column side_link")
    база = rc.База(DSN, схема=СХЕМА)
    try:
        s = Store()
        with pytest.raises(rc.CtxError, match="^CONTRACT_SCHEMA_TOO_OLD:side_link$"):
            rc.publish(база, s, dry=False, ct=ct, печать=lambda _s: None)
        assert s.raw is None, "ключ не записан"
    finally:
        база.закрыть()


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


# ── факты из чатов (решение владельца 25.10.2026) ─────────────────────────────────────

ТЕКСТ_ГЛАВНОГО = "Поставщик сорвал отгрузку 24.10, нужен новый срок"


class БазаСФактами(База):
    def __init__(self, факты=None, **kw):
        super().__init__(**kw)
        self._факты = факты or {}

    def факты(self):
        return self._факты


def _строки_фактов(**поправки) -> dict:
    строки = {
        "money": [(11, "итог", 1_250_000.0, "RUB", "без_ндс", dt.date(2026, 10, 12), T),
                  (11, "наше_кп", 1_300_000.0, "RUB", "не_указано", None, T - dt.timedelta(days=3)),
                  (11, "счёт", 1.0, "RUB", "с_ндс", None, T - dt.timedelta(days=4)),
                  (11, "оплата", 2.0, "RUB", "с_ндс", None, T - dt.timedelta(days=5))],
        "contract": [(11, "заказчик", "подписан", "эдо", "3/2026", T)],
        "src": [(11, "кп_получено", "сорсер", T)],
        # deal, оценка, проход, вид, важность, строка, уверенность, ключи, дата доказательства,
        # пунктов в ответе до проверок, была ли сводка сборки
        "imp": [(11, T, "сутки", "поставщик", "высокая", ТЕКСТ_ГЛАВНОГО, 0.7, ["o1"], T - dt.timedelta(days=2), 2, False),
                (12, T, "история", None, None, None, None, None, T, 0, True)],
    }
    строки.update(поправки)
    return строки


def test_свод_с_фактами_без_цитат_и_номеров_сообщений():
    база = БазаСФактами(факты=_строки_фактов(), чаты=[_чат(11)])
    store = Store()
    журнал: list[str] = []
    assert rc.publish(база, store, dry=False, ct=ct, печать=журнал.append) == 0
    свод = json.loads(store.raw.decode("utf-8"))
    f = свод["facts"]["11"]
    assert len(f["money"]) == dealctx.CHAT_MONEY_MAX and f["money"][0] == {
        "amount": 1_250_000.0, "cur": "RUB", "kind": "итог", "vat": "без_ндс", "date": "2026-10-12", "at": T.isoformat()}
    assert f["contract"] == [{"side": "заказчик", "status": "подписан", "where": "эдо", "spec": "3/2026",
                              "at": T.isoformat()}]
    assert f["src"] == [{"signal": "кп_получено", "role": "сорсер", "at": T.isoformat()}]
    assert f["imp"] == {"at": T.isoformat(), "src": "сутки", "conf": 0.7, "n": 2, "dg": False,
                        "items": [{"kind": "поставщик", "sev": "высокая", "text": ТЕКСТ_ГЛАВНОГО, "keys": ["o1"],
                                   "ev": (T - dt.timedelta(days=2)).isoformat()}]}
    assert свод["facts"]["12"]["imp"]["items"] == [], "пустой последний вызов — «важного нет», а не прежние пункты"
    assert dealctx.clean(свод)["bad"] == 0 and dealctx.clean(свод)["facts"]["11"]["imp"]["conf"] == 0.7
    текст = store.raw.decode("utf-8")
    for слово in ("quote", "msg_id", "evidence"):
        assert слово not in текст, "цитат и номеров сообщений в ключе нет"
    assert any("факты из чатов: сделок 2 (с суммой 1, с договором 1, с сорсингом 1, с главным 1, из них высокой "
               "важности 1); не по форме 0" in s for s in журнал)
    for s in журнал:
        for утечка in ("1250000", "1 250 000", ТЕКСТ_ГЛАВНОГО, "3/2026", "кп_получено", "подписан"):
            assert утечка not in s, ("в журнал — только счётчики", утечка, s)


def test_плохой_факт_снимается_оценка_чата_остаётся_шаг_красный():
    плохие = _строки_фактов(imp=[(11, T, "сутки", "поставщик", "высокая", "x" * 161, 0.7, [], T, 1, False)])
    база = БазаСФактами(факты=плохие, чаты=[_чат(11)])
    store = Store()
    журнал: list[str] = []
    assert rc.publish(база, store, dry=False, ct=ct, печать=журнал.append) == 1
    свод = json.loads(store.raw.decode("utf-8"))
    assert "11" not in свод["facts"] and свод["chats"]["11"]["state"] == "ждёт_нас", "ключ записан, оценка на месте"
    assert any(s.startswith("::error::факты из чатов: у 1 сделок") for s in журнал)
    # база без фактов (до схемы v3, подпорка) — свод как раньше, без ошибок
    assert rc.publish(База(чаты=[_чат(11)]), Store(), dry=False, ct=ct, печать=lambda s: None) == 0


def test_факты_на_настоящей_базе(conn):
    """Факты пишет chat_context.База (как прогон оценки), свод читает виды базы."""
    import chat_context as cc
    store = cc.База(__import__("psycopg2").connect(DSN, options=f"-c search_path={СХЕМА}"), только_чтение=False)
    verdict = {"state": "идёт", "direction": "вперёд", "risk": "низкий", "risk_kinds": [], "blocker": None,
               "ball_role": "кам", "reply_expected": False, "due": None, "evidence_msg_ids": [12], "confidence": 0.7}
    строка = {"deal_id": "11", "model": "claude-opus-5-5", "prompt_sha256": "a" * 64, "method_ver": cc.METHOD_VER,
              "msg_from_id": 10, "msg_to_id": 12, "verdict": verdict, "tokens_in": 1, "tokens_out": 1, "usd": 0.01,
              "run_id": "chat-ctx-1", "assessed_at": T}
    трата = {"at": T, "run_id": "chat-ctx-1", "part": 1, "deal_id": "11", "model": "claude-opus-5-5",
             "outcome": "записано", "defect": None, "api_status": None, "tokens_in": 1, "tokens_out": 1, "usd": 0.01,
             "estimated": False}
    def факт(вид, msg, data, **kw):
        f = {"kind": вид, "pass": "сутки", "msg_id": msg, "msg_at": T, "evidence_msg_ids": [msg], "evidence_keys": [],
             "evidence_vals": None, "data": data, "quote": "цитата для проверки", "statement": None, "confidence": None}
        f.update(kw)
        return f
    try:
        store.записать_оценку(строка, {"chat_id": "911", "last_msg_id": 12, "last_human_at": T, "checked_at": T}, трата, [
            факт("деньги", 10, {"amount": 1_250_000.0, "currency": "RUB", "money_kind": "итог", "side": "продажа",
                                "vat": "без_ндс", "date": "2026-10-12"}),
            факт("деньги", 11, {"amount": 9_000.0, "currency": "EUR", "money_kind": "наше_кп", "side": "закупка",
                                "vat": "без_ндс", "date": None}),
            факт("договор", 12, {"status": "у_клиента", "side": "заказчик", "where": "почта", "spec_no": None}),
            факт("сорсинг", 11, {"signal": "отказ", "author_role": "сорсер"}),
            факт("главное", 12, {"kind": "поставщик", "severity": "средняя"}, statement=ТЕКСТ_ГЛАВНОГО, confidence=0.6,
                 evidence_keys=["stage"], evidence_vals={"stage": "name=Производство"})])
    finally:
        store.conn.close()
    база = rc.База(DSN, схема=СХЕМА)
    try:
        s = Store()
        assert rc.publish(база, s, dry=False, ct=ct, печать=lambda x: None) == 0
    finally:
        база.закрыть()
    f = json.loads(s.raw)["facts"]["11"]
    assert f["money"] == [{"amount": 1_250_000.0, "cur": "RUB", "kind": "итог", "vat": "без_ндс", "date": "2026-10-12",
                           "at": T.isoformat()}], "закупка в свод продажи не идёт"
    assert f["contract"][0]["where"] == "почта" and f["src"][0] == {"signal": "отказ", "role": "сорсер",
                                                                     "at": T.isoformat()}
    assert f["imp"]["items"] == [{"kind": "поставщик", "sev": "средняя", "text": ТЕКСТ_ГЛАВНОГО, "keys": ["stage"],
                                  "ev": T.isoformat()}], "дата доказательства — дата сообщения пункта"
    assert f["imp"]["conf"] == 0.6 and f["imp"]["src"] == "сутки"
    assert f["imp"]["n"] is None and f["imp"]["dg"] is None, "строка без числа пунктов — как до ревью"
    assert "цитата" not in s.raw.decode("utf-8")
