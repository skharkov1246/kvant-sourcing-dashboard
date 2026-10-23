"""Свод чатов и договоров → закрытый ключ KV realctx:v1 (scripts/realization_ctx.py).

  · свод проходит тот же разбор, что читает сборка (dealctx.clean), без отброшенных;
  · исход и флаги прогона сверяются с пересчётом по строкам: не сошлось — «разошлось»,
    без флагов, код 1 после записи остального;
  · прогон до 23.10.2026 (без исхода) — пересчётом;
  · запись не по форме — стоп, ключ не тронут;
  · в ключе нет условий договоров и цитат;
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

    def __init__(self, чаты=None, выбор=None, строки=None, флаги=None):
        self._чаты, self._выбор = чаты or [], выбор or []
        self._строки, self._флаги = строки or [], флаги or []

    def чаты(self):
        return self._чаты

    def договоры(self):
        return self._выбор, self._строки, self._флаги


def _чат(deal, **kw):
    r = {"deal": deal, "checked": T, "human": T - dt.timedelta(days=1), "at": T, "state": "ждёт_нас",
         "direction": "стоит", "risk": "высокий", "ball": "кам", "reply": True, "due": dt.date(2026, 10, 25),
         "dueKind": "отгрузка", "kinds": ["срок"], "blocker": {"kind": "ответ", "on_role": "кам"}}
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
    assert свод["chats"]["11"]["due"] == "2026-10-25"
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
    for имя in ("deal_chat_schema.sql", "contract_terms_schema.sql"):
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
                     evidence=None, usd=0)
        база_записи.записать_условия(строки_старые)
        база_записи.записать_флаги(флаги_старые, "ct-old")
        база_записи.записать_условия(строки_новые)
        база_записи.записать_итог([], {102: "нет пары", 103: "нет договоров"}, "ct-new")
        # позже прогон, где сделке 102 не хватило бюджета: «недочитана» не затирает разбор
        база_записи.записать_итог([], {102: "недочитана"}, "ct-newer")
    finally:
        база_записи.закрыть()
    база = rc.База(DSN, схема=СХЕМА)
    try:
        s = Store()
        журнал: list[str] = []
        assert rc.publish(база, s, dry=False, ct=ct, печать=журнал.append) == 0
    finally:
        база.закрыть()
    свод = json.loads(s.raw)
    assert dealctx.clean(свод)["bad"] == 0
    ч = свод["chats"]
    assert ч["11"]["state"] == "стоит" and ч["11"]["kinds"] == ["срок", "оплата"] and ч["11"]["due"] == "2026-10-20"
    assert ч["11"]["blocker"] == {"kind": "поставка", "on": "поставщик"} and ч["11"]["reply"] is False
    assert ч["12"]["at"] is None and ч["12"]["human"] is None
    # свежесть — по удачному чтению: у 11 read_at пуст (строка до 23.10) — время оценки,
    # а не checked_at, который двигает и неудачное чтение
    assert ч["11"]["checked"] == T.isoformat() and "13" not in ч
    д = свод["contracts"]
    assert д["101"]["outcome"] == "пара" and len(д["101"]["flags"]) == len(флаги_старые)
    assert д["102"] == {"at": д["102"]["at"], "outcome": "нет пары", "read": {"customer": 1, "supplier": 0},
                        "flags": []}
    assert д["103"]["outcome"] == "нет договоров"
    assert any("исход прогона 2, пересчёт прежних 1" in x for x in журнал)


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
