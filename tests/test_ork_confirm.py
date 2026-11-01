"""Шаг 0 «Подтверждение передачи в ОРК» (решения владельца 31.10.2026, docs/agents/DECISIONS.md):
задача Битрикса «Подтверждение готовности передачи…» → заказ (ork_confirm), шаг 0 в ork.STEPS,
пункты s0Over и confirmOpen, приёмка (t0) с закрытия задачи, отказ без последствий; исполнитель
заказа, не переданного в ОРК (ответственный не из ОРК, в поле сделки — руководитель ОРК).

Три слоя: разбор задач (ork_confirm) на придуманных ответах tasks.task.list; правила ork, lifecycle
и kamqueue на придуманных заказах; сборка целиком — fixture.ПодтверждениеПередачи через настоящий
contracts.compute. Корпус придуман (CLAUDE.md, правило 18): ни реальных людей, ни компаний.
Сегодня — пн 19.10.2026 (fixture.CT_TODAY): ср 14.10 — 3 р.д. назад, пт 09.10 — 6 р.д.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import contracts as contracts_mod  # noqa: E402
import dealctx  # noqa: E402
import kamqueue  # noqa: E402
import lifecycle  # noqa: E402
import ork  # noqa: E402
import ork_confirm  # noqa: E402
import orkview  # noqa: E402
from bitrix_client import BitrixClient, BitrixError, BitrixLimitError  # noqa: E402
from tests import fixture  # noqa: E402

T = fixture.CT_TODAY
D = dt.date


def ts(day: str, hm: str = "10:00") -> str:
    return f"2026-{day}T{hm}:00+03:00"


# ── разбор задач ───────────────────────────────────────────────────────────────────────────
class Клиент:
    """Придуманный портал: tasks.task.list постранично (по 50, next) — тем же кодом, что у
    BitrixClient.tasks_list; fail — отказ портала на первом же вызове."""

    def __init__(self, tasks: list[dict], fail: Exception | None = None):
        self.tasks, self.fail, self.calls = tasks, fail, 0

    def call_envelope(self, method, params=None, **kw):
        if self.fail is not None:
            raise self.fail
        assert method == "tasks.task.list"
        self.calls += 1
        return fixture.ct_tasks_page(self.tasks, params)

    def tasks_list(self, filter=None, select=None, *, max_pages=10, info=None):
        return BitrixClient.tasks_list(self, filter, select, max_pages=max_pages, info=info)


def test_задачи_постранично_по_next_и_по_названию():
    """60 задач по фразе — две страницы (next); название не с фразы и задача до SINCE — не в счёте."""
    tasks = [fixture.ct_task(100 + i, 8000 + i, ts("10-05"), status="2" if i % 3 else "5",
                             closed="" if i % 3 else ts("10-06")) for i in range(60)]
    tasks.append(fixture.ct_task(900, 8999, ts("10-05"), title="Перешлите: Подтверждение готовности передачи 1/1"))
    tasks.append(fixture.ct_task(901, 8998, "2026-08-10T10:00:00+03:00"))          # раньше SINCE
    c = Клиент(tasks)
    by, st = ork_confirm.load(c)
    assert c.calls == 2, "61 задача по фильтру — две страницы по next"
    assert st["read"] is True and st["raw"] == 61 and st["n"] == 60 and st["skipped"] == 1
    assert st["order"] == 60 and st["orders"] == 60 and len(by) == 60
    assert st["open"] == 40 and st["closed"] == 20
    assert "8999" not in by and "8998" not in by
    assert by["8001"]["open"] is True and by["8001"]["at"] == "" and by["8001"]["spans"] == [[ts("10-05"), ""]]
    assert by["8000"]["open"] is False and by["8000"]["at"] == ts("10-06") and by["8000"]["status"] == "5"


def test_ключи_обоих_регистров_привязки_и_две_задачи():
    """camelCase (tasks.task.list) и UPPER (снимок, старые ответы) — один разбор; «Tac_» — заказ,
    «D_» и без привязки — только счёт; две задачи заказа: открыта хоть одна — заказ на шаге 0."""
    rows = [
        {"ID": "1", "TITLE": "Подтверждение готовности передачи заказа 1/1", "STATUS": "5",
         "CREATED_DATE": ts("10-01"), "CLOSED_DATE": ts("10-02", "12:00"), "RESPONSIBLE_ID": "7",
         "UF_CRM_TASK": ["Tac_501"]},
        {"id": "2", "title": "подтверждение  готовности передачи заказа 1/1", "status": "2",
         "createdDate": ts("10-08"), "closedDate": None, "responsibleId": "8", "ufCrmTask": ["Tac_501", "D_77"]},
        {"id": "2", "title": "Подтверждение готовности передачи заказа 1/1", "status": "2",       # повтор id
         "createdDate": ts("10-08"), "responsibleId": "8", "ufCrmTask": ["Tac_501"]},
        {"id": "3", "title": "Подтверждение готовности передачи сделки", "status": "2", "createdDate": ts("10-08"),
         "responsibleId": "9", "ufCrmTask": ["D_77"]},
        {"id": "4", "title": "Подтверждение готовности передачи", "status": "2", "createdDate": ts("10-08"),
         "responsibleId": "9", "ufCrmTask": []},
        {"ID": "5", "TITLE": "Подтверждение готовности передачи 2/1", "STATUS": "5", "CREATED_DATE": ts("10-03"),
         "CLOSED_DATE": ts("10-04"), "RESPONSIBLE_ID": "7", "UF_CRM_TASK": ["Tac_502"]},
        {"ID": "6", "TITLE": "Подтверждение готовности передачи 2/1", "STATUS": "5", "CREATED_DATE": ts("10-06"),
         "CLOSED_DATE": ts("10-07"), "RESPONSIBLE_ID": "9", "UF_CRM_TASK": "Tac_502"},
    ]
    by, st = ork_confirm.parse(rows)
    # к заказу — задачи 1, 2, 5, 6; только к сделке — 3; без привязки — 4; у заказов 501 и 502 по две
    assert (st["raw"], st["n"], st["order"], st["deal"], st["none"], st["multi"]) == (6, 6, 4, 1, 1, 2)
    a = by["501"]
    assert a["open"] is True and a["from"] == ts("10-08") and a["at"] == "" and a["n"] == 2 and a["resp"] == "8"
    assert a["spans"] == [[ts("10-01"), ts("10-02", "12:00")], [ts("10-08"), ""]]
    b = by["502"]                                   # обе закрыты — по последней закрытой
    assert b["open"] is False and b["from"] == ts("10-06") and b["at"] == ts("10-07") and b["resp"] == "9"
    assert "77" not in by


def test_статус_4_открыта_и_закрыта_только_5():
    """Решение владельца 31.10.2026, б: «Ждёт контроля» (4) — открыта, даже с датой закрытия;
    закрыта — только 5. Закрытая без даты закрытия дней шага 0 не даёт (конец неизвестен)."""
    rows = [fixture.ct_task(1, 601, ts("10-05"), status="4", closed=ts("10-06")),
            fixture.ct_task(2, 602, ts("10-05"), status="5", closed=""),
            fixture.ct_task(3, 603, ts("10-05"), status="6")]
    by, _st = ork_confirm.parse(rows)
    assert by["601"]["open"] is True and by["603"]["open"] is True
    assert by["602"]["open"] is False and by["602"]["at"] == "" and by["602"]["spans"] == []
    assert ork_confirm.CONFIRM_CLOSED == {"5"}


def test_предел_страниц_не_молчит(monkeypatch):
    """Правило 15: портал отдал не всё (next после MAX_PAGES страниц) — stats.truncated, head read
    «partial» с оговоркой, строка журнала предупреждает; прочитанные задачи шаг 0 дают."""
    monkeypatch.setattr(ork_confirm, "MAX_PAGES", 2)
    tasks = [fixture.ct_task(100 + i, 8000 + i, ts("10-05")) for i in range(120)]
    c = Клиент(tasks)
    by, st = ork_confirm.load(c)
    assert c.calls == 2 and st["raw"] == 100 and st["truncated"] is True and len(by) == 100
    h = ork_confirm.head(st, 50)
    assert h["read"] == "partial" and h["n"] == 100 and "не все" in h["why"]
    assert "НЕ ВСЕ" in ork_confirm.summary(st)
    # всё уместилось — обрезки нет
    by, st = ork_confirm.load(Клиент(tasks[:90]))
    assert "truncated" not in st and ork_confirm.head(st, 50)["read"] is True
    # строка 0 ведомости при «partial» — счёт с оговоркой, не «Нет данных»
    led, _m = orkview.build([], [], [], T, confirm=h)
    l0 = next(x for x in led["week"]["ledger"] if x["no"] == 0)
    assert l0.get("partial") is True and "nodata" not in l0 and l0["why"] == h["why"]


@pytest.mark.parametrize("err", [BitrixError("ACCESS_DENIED"), BitrixLimitError("лимит"), AssertionError("заглушка"),
                                 ValueError("разбор")])
def test_отказ_без_последствий(err):
    by, st = ork_confirm.load(Клиент([], fail=err))
    assert by is None and st == {"read": False, "why": type(err).__name__}
    h = ork_confirm.head(st, 50)
    assert h["read"] is False and type(err).__name__ in h["why"] and h["n"] is None
    assert "не прочитаны" in ork_confirm.summary(st)


def test_клиент_без_метода_задач_отказ_без_последствий():
    by, st = ork_confirm.load(object())
    assert by is None and st["read"] is False and st["why"] == "AttributeError"


def test_ноль_задач_при_живых_заказах_нет_данных():
    """Прочитано 0 при EMPTY_MIN и больше живых заказах с SINCE — «empty» (видимость задач вебхуку
    не проверена), а не «задач нет»; меньше — прочитано честно."""
    _by, st = ork_confirm.parse([])
    assert ork_confirm.head(st, ork_confirm.EMPTY_MIN)["read"] == "empty"
    assert "видимость" in ork_confirm.head(st, ork_confirm.EMPTY_MIN)["why"]
    assert ork_confirm.head(st, ork_confirm.EMPTY_MIN - 1)["read"] is True


def test_журнал_только_счёт():
    """Правило 17: в строке журнала — ни названий, ни id задач и заказов, ни исполнителей."""
    rows = [fixture.ct_task(7771, 99123, ts("10-05"), resp="4242")]
    _by, st = ork_confirm.parse(rows)
    line = ork_confirm.summary(st)
    for bad in ("7771", "99123", "4242", "Подтверждение готовности передачи заказа"):
        assert bad not in line, bad
    assert "прочитано 1" in line


# ── правила шага 0 (ork) ───────────────────────────────────────────────────────────────────
def _o(**kw) -> dict:
    """Придуманный живой заказ «Заказ создан», создан ср 07.10.2026."""
    base = {"live": True, "stage": "Заказ создан", "created": D(2026, 10, 7), "t0": ts("10-07"),
            "t0Raw": ts("10-07"), "stageSince": ts("10-07"), "stepSince": ts("10-07")}
    base.update(kw)
    return base


def _open(day: str) -> dict:
    return {"open": True, "from": ts(day, "11:00"), "at": "", "spans": [[ts(day, "11:00"), ""]], "n": 1,
            "resp": "33", "status": "2"}


def _closed(day0: str, day1: str, hm="15:00") -> dict:
    return {"open": False, "from": ts(day0, "11:00"), "at": ts(day1, hm), "spans": [[ts(day0, "11:00"), ts(day1, hm)]],
            "n": 1, "resp": "33", "status": "5"}


def test_шаг_сейчас_задача_открыта_шаг_0_стадия_и_факты_сильнее():
    assert ork.step_now(_o(confirm=_open("10-09"))) == ork.PRE_STEP == 0
    # назначение при открытой задаче — всё равно шаг 0: приёмки ещё нет
    o = _o(confirm=_open("10-09"), assignDate=D(2026, 10, 14), orkMgr="Олег")
    assert ork.assign_at(o) is None and ork.step_now(o) == 0
    assert ork.step_now(_o(stage="Заказ направлен", confirm=_open("10-09"))) == 4
    assert ork.step_now(_o(confirm=_open("10-09"), supPaidPct=100)) == 6     # факты сильнее задачи
    assert ork.step_now(_o(confirm=_closed("10-07", "10-09"))) == 1
    assert ork.step_now(_o()) == 1 and ork.confirm_open(_o()) is False


def test_срок_шага_0_от_создания_задачи_и_превышение_не_сверх_нормы():
    o = _o(confirm=_open("10-09"))
    assert ork.clock(o, 0) == (D(2026, 10, 9), "задача подтверждения")
    v = ork.judged(o, T)
    assert (v["step"], v["role"], v["norm"], v["wd"]) == (0, "confirmResp", 5, 6)
    # не «сверх нормы» ОРК: over False, preOver True — как waitUtd у шага 12
    assert v["over"] is False and v["preOver"] is True and v["rawOver"] is True and v["counted"] is True
    assert v["since"] == "2026-10-09" and v["sinceBy"] == "задача подтверждения"
    v3 = ork.judged(_o(confirm=_open("10-14")), T)
    assert v3["wd"] == 3 and v3["over"] is False and v3["preOver"] is False and v3["counted"]
    # отсечка «приёмка вне Реализации» к шагу 0 не применяется
    assert ork.judged(o, T, in_cat0=False)["preOver"] is True


def test_хозяин_шага_0_исполнитель_задачи_кам_в_копии():
    pp = {"confirmResp": "Сергей (снабжение)", "kam": "Анна (КАМ)", "orkHead": "Роза (рук. ОРК)"}
    assert ork.owner(0, pp) == {"role": "confirmResp", "who": "Сергей (снабжение)", "copy": "Анна (КАМ)",
                                "copyRole": "kam"}
    assert ork.owner(0, {})["who"] == "не назначен"
    it = ork.item_owner("s0Over", 0, pp)
    assert (it["step"], it["role"], it["who"], it["copyRole"], it["copy"]) == (0, "confirmResp", "Сергей (снабжение)",
                                                                                "kam", "Анна (КАМ)")
    it = ork.item_owner("confirmOpen", 4, pp)
    assert (it["step"], it["role"], it["copyRole"], it["copy"]) == (4, "confirmResp", "orkHead", "Роза (рук. ОРК)")
    assert ork.role_id("confirmResp", pp, {"confirmResp": "33"}) == "33"


def test_путь_1_0_1_хвост_как_шаг_и_срок():
    """Заказ создан пн 05.10, задача открыта ср 07.10 и закрыта пт 09.10 15:00: путь 1 → 0 → 1;
    последняя запись — шаг сейчас (step_now) с днём clock (приёмка — с закрытия задачи)."""
    cf = {"open": False, "from": ts("10-07"), "at": ts("10-09", "15:00"), "spans": [[ts("10-07"), ts("10-09", "15:00")]],
          "n": 1, "resp": "33", "status": "5"}
    o = _o(created=D(2026, 10, 5), t0Raw=ts("10-05"), t0=ts("10-09", "15:00"), t0Src="confirm", confirm=cf,
           stageSince=ts("10-05"), stepSince=ts("10-05"))
    p = ork.path(o, [("NEW", ts("10-05"))], {"NEW": "Заказ создан"}, T)
    assert [x[:2] for x in p] == [[1, "2026-10-05"], [0, "2026-10-07"], [1, "2026-10-09"]]
    assert p[-1][0] == ork.step_now(o) and p[-1][1] == ork.clock(o, 1)[0].isoformat() and p[-1][2] == 6
    # открытая задача: хвост — шаг 0 от создания задачи
    o2 = _o(created=D(2026, 10, 5), t0Raw=ts("10-05"), t0=ts("10-05"), confirm=_open("10-07"))
    p2 = ork.path(o2, [("NEW", ts("10-05"))], {"NEW": "Заказ создан"}, T)
    assert [x[:2] for x in p2] == [[1, "2026-10-05"], [0, "2026-10-07"]] and p2[-1][2] == 8


def test_сегменты_шаг_0_приёмка_не_начата_и_назначение_с_закрытия():
    tr, names = [("NEW", ts("10-12"))], {"NEW": "Заказ создан"}
    g = ork.segments(tr, names, t0=ts("10-12"), pre_open=True)
    assert "S1" not in g["segs"] and g["open"] is None
    assert ork.segments(tr, names, t0=ts("10-12"))["open"] == {"seg": "S1", "start": ts("10-12")}
    # назначение 13.10 раньше закрытия задачи 14.10 15:00 — с закрытия: S1 0 р.д., конец — назначение
    o = _o(created=D(2026, 10, 12), t0Raw=ts("10-12"), t0=ts("10-14", "15:00"), t0Src="confirm",
           confirm=_closed("10-12", "10-14"), assignDate=D(2026, 10, 13), orkMgr="Олег (ОРК)")
    assert ork.seg_assign(o) == D(2026, 10, 14) and ork.assign_at(o) == D(2026, 10, 14)
    s1 = ork.segments(tr, names, t0=o["t0"], facts={"assignAt": ork.seg_assign(o)})["segs"]["S1"]
    assert s1["wd"] == 0 and s1["endBy"] == "назначение" and s1["start"] == ts("10-14", "15:00") and not s1["neg"]
    # без задачи — прежняя дата; раньше начала заказа — сырая (segments пометит assignIgnored)
    assert ork.seg_assign(_o(assignDate=D(2026, 10, 8), orkMgr="Олег")) == D(2026, 10, 8)
    assert ork.seg_assign(_o(assignDate=D(2026, 10, 1), orkMgr="Олег")) == D(2026, 10, 1)
    assert ork.seg_assign(_o(assignDate=D(2026, 10, 8))) is None
    assert ork.seg_assign(_o(assignDate=D(2026, 10, 8), orkMgr="Олег", confirm=_open("10-07"))) is None


def test_гигиена_шаг_0_не_брошенный_и_задача_при_ушедшей_стадии():
    """Норма шага 0 — 5 р.д. > abandoned_norm_max: больше 60 р.д. на шаге 0 — не «брошенная»;
    задача открыта, а стадия ушла — confirmOpen (решение 31.10.2026, а), из счёта не исключает."""
    old = _o(created=D(2026, 5, 4), t0Raw=ts("05-04"), t0=ts("05-04"), confirm=_open("05-04"),
             stageSince=ts("05-04"), stepSince=ts("05-04"))
    h = ork.hygiene(old, T)
    assert ork.step_now(old) == 0 and not h["abandoned"] and not h["exclude"] and not h["confirmOpen"]
    assert ork.judged(old, T)["preOver"] is True
    gone = _o(stage="Заказ направлен", confirm=_open("10-09"), stageSince=ts("10-13"), stepSince=ts("10-13"))
    hg = ork.hygiene(gone, T)
    assert hg["confirmOpen"] is True and not hg["exclude"] and "задача подтверждения" in hg["why"]
    assert ork.judged(gone, T)["counted"] is True


def test_таблицы_шага_0():
    assert ork.STEPS[0]["no"] == 0 and ork.STEP[0]["name"] == "Подтверждение передачи в ОРК"
    assert ork.NORM_DOC[0] == ("5 р.д.", "norm", "") and ork.ROLES["confirmResp"]
    assert ork.TERM_CODE[0] == "s0Over" and "s0Over" in ork.TERM_CODES and 0 not in ork.OVER_CODE
    assert "confirmResp" in ork.ROLE_KEYS and ork.ITEM["s0Over"] == (0, "confirmResp", "kam")
    sev, what, act = lifecycle.EXC["s0Over"]
    assert sev == lifecycle.EXC["s1Over"][0] and "5 р.д." in what and "закрыть задачу подтверждения" in act
    assert lifecycle.unit_bucket("s0Over") == ("order", "exec")
    assert lifecycle.unit_bucket("confirmOpen") == ("order", "hyg")
    assert "вернуть стадию" in lifecycle.EXC["confirmOpen"][2]
    assert kamqueue.CODES["s0Over"][0] == kamqueue.CODES["s1Over"][0]
    assert kamqueue.REAL_ACT["s0Over"] == "добиться закрытия задачи подтверждения передачи в ОРК"
    assert "pre" in orkview.CAUSES and orkview.PULSE_ORDER["pre"] == 3
    assert orkview.order_cause(["noPlanDates"], 0, False) == ("pre", "")


def test_слова_с_какого_дня():
    o = _o(t0=ts("10-14", "15:00"), t0Src="confirm")
    assert lifecycle.since_text("2026-10-14", "начало приёмки", o, T) == "с 14.10 (подтверждена передача)"
    assert lifecycle.since_text("2026-10-07", "начало приёмки", _o(), T) == "с 07.10 (создан заказ)"
    assert lifecycle.since_text("2026-10-09", "задача подтверждения", _o(), T) == "с 09.10 (создана задача подтверждения)"


def test_пункты_заказа_шаг_0():
    ex = dict(lifecycle.order_exceptions(_o(confirm=_open("10-09"), dl=D(2026, 12, 1)), T))
    assert ex.get("s0Over") == 6 and "s1Over" not in ex and "ordAssign" not in ex
    assert "s0Over" not in dict(lifecycle.order_exceptions(_o(confirm=_open("10-14"), dl=D(2026, 12, 1)), T))
    ex2 = dict(lifecycle.order_exceptions(_o(stage="Заказ направлен", confirm=_open("10-09"), dl=D(2026, 12, 1),
                                             stageSince=ts("10-13"), stepSince=ts("10-13")), T))
    assert ex2.get("confirmOpen") == 10 and "s0Over" not in ex2


def test_пункт_сделки_заказа_шаг_0_у_dealctx():
    """dealctx: пункт заказа на шаге 0 — хозяин исполнитель задачи (confirmWho строки заказа)."""
    r = {"deal": "1", "kam": "Анна (КАМ)", "orders": []}
    ppl = dealctx._people(r, "Роза (рук. ОРК)")
    # chatRisk — менеджеру ОРК по ITEM, но заказ на шаге 0 ещё не в ОРК: исполнитель задачи, КАМ в копии
    own = dealctx._reg("chatRisk", r, ppl, order={"step": 0, "confirmWho": "Сергей (снабжение)", "orkMgr": "Олег (ОРК)"})
    assert (own["step"], own["role"], own["who"], own["copyRole"]) == (0, "confirmResp", "Сергей (снабжение)", "kam")
    own = dealctx._reg("s0Over", r, ppl, order={"step": 0, "confirmWho": "Сергей (снабжение)"})
    assert (own["role"], own["who"], own["copyRole"], own["escalateTo"]) == ("confirmResp", "Сергей (снабжение)",
                                                                             "kam", "Анна (КАМ)")


# ── исполнитель заказа, не переданного в ОРК (решение 31.10.2026, заказ 968/2) ─────────────
def test_исполнитель_ответственный_не_из_орк():
    pp = {"orkMgr": "", "dealOrk": "", "resp": "Сергей (снабжение)", "orkHead": "Роза (рук. ОРК)", "kam": "Анна (КАМ)"}
    ow = ork.owner(4, pp)
    assert ow["who"] == "Сергей (снабжение)" and ow["out"] is True and ow["copyRole"] == "orkHead"
    assert ork.role_id("orkMgr", pp, {"resp": "33"}) == "33"
    late = ork.item_owner("late", 7, pp)                       # своя копия у пункта (КАМ) — остаётся
    assert late["who"] == "Сергей (снабжение)" and late["out"] and late["copyRole"] == "kam"
    # руководитель ОРК — вторым в копии (ему — «принять заказ и назначить менеджера ОРК»)
    assert (late["alsoCopy"], late["alsoCopyRole"]) == ("Роза (рук. ОРК)", "orkHead")
    assert "alsoCopy" not in ow and "alsoCopy" not in ork.owner(4, pp)
    # менеджер ОРК в поле сделки — менеджер заказа, как прежде
    ow = ork.owner(4, {**pp, "dealOrk": "Пётр (ОРК)"})
    assert ow["who"] == "Пётр (ОРК)" and "out" not in ow
    assert contracts_mod._is_ork_head("30", "Роза (рук. ОРК)", "30", "Роза (рук. ОРК)")
    assert not contracts_mod._is_ork_head("32", "Пётр (ОРК)", "30", "Роза (рук. ОРК)")
    assert contracts_mod._is_ork_head("", "Роза (рук. ОРК)", "", "Роза (рук. ОРК)")       # без id — по имени
    assert not contracts_mod._is_ork_head("", "", "", "")


def test_заказ_на_шаге_0_не_у_менеджера_орк_и_не_whoout():
    """Решение 31.10.2026, пп. 1–2: заказ на шаге 0 ещё не в ОРК — пункты менеджера ОРК (срок клиента,
    производство, чат) у исполнителя задачи, КАМ в копии; «принять заказ и назначить менеджера ОРК»
    (out) до закрытия задачи не ставится — ни при ответственном не из ОРК, ни при менеджере ОРК."""
    pp = {"orkMgr": "", "dealOrk": "", "resp": "Сергей (снабжение)", "orkHead": "Роза (рук. ОРК)",
          "kam": "Анна (КАМ)", "confirmResp": "Вера (снабжение)"}
    for code in ("late", "risk", "forecast", "prodDev", "planAfterDl", "chatRisk"):
        it = ork.item_owner(code, 0, pp)
        assert (it["step"], it["role"], it["who"], it["copyRole"], it["copy"]) == (
            0, "confirmResp", "Вера (снабжение)", "kam", "Анна (КАМ)"), code
        assert "out" not in it and "alsoCopy" not in it, code
        it = ork.item_owner(code, 0, {**pp, "orkMgr": "Олег (ОРК)"})
        assert it["role"] == "confirmResp" and it["who"] == "Вера (снабжение)", code
    # договор поставщика (шаг 4 по ITEM) у заказа шага 0 — тоже исполнитель задачи
    it = ork.item_owner("contractMissing", 0, pp, key="contractMissing:supplier")
    assert it["role"] == "confirmResp" and "out" not in it
    # пункты других ролей шаг 0 не трогает (приёмка, сроки без даты — руководитель ОРК)
    assert ork.item_owner("noDl", 0, pp)["role"] == "orkHead"
    # тот же пункт на шаге 4 — прежнее правило 968/2
    it = ork.item_owner("late", 4, pp)
    assert it["role"] == "orkMgr" and it["who"] == "Сергей (снабжение)" and it["out"] is True


def test_пропуск_менеджера_орк_у_сделки_только_шага_0():
    """orkview.owner_gaps: все живые заказы сделки на шаге 0 и менеджера ОРК нет — пропуска нет
    (не «ушёл»: all([]) — не повод); заказ шага 0 рядом с заказом вне ОРК — «out» по остальным."""
    d = {"kam": "Анна", "ossDeal": "Оля", "orkResp": "", "orders": [{"id": "5", "live": True, "orkMgr": ""}]}
    assert orkview.owner_gaps(d, {"5": "pre"}) == []
    assert [g["state"] for g in orkview.owner_gaps(d, {"5": "none"})] == ["none"]
    d2 = {**d, "orders": [{"id": "5", "live": True, "orkMgr": ""}, {"id": "6", "live": True, "orkMgr": ""}]}
    g = orkview.owner_gaps(d2, {"5": "pre", "6": "out"})
    assert [(x["role"], x["state"], x["ordOut"], x["ordNone"]) for x in g] == [("orkMgr", "out", 1, 0)]
    assert [x["state"] for x in orkview.owner_gaps({**d, "orkResp": "Пётр (уволен)"}, {"5": "pre"})] == ["gone"]
    assert [x["state"] for x in orkview.owner_gaps({**d, "orders": []}, {})] == ["none"]


def test_назначение_при_открытой_задаче_и_ушедшей_стадии():
    """Решение 31.10.2026, а: стадия дальше шага 1 при открытой задаче — шаг по стадии, задача —
    только замечание confirmOpen; назначение и сегменты — как без задачи (срок шага 4 не сдвигается)."""
    o = _o(stage="Заказ направлен", stageSince=ts("10-14"), stepSince=ts("10-14"), orkMgr="Олег (ОРК)",
           assignDate=D(2026, 10, 9))
    assert ork.assign_at(o) == D(2026, 10, 9) and ork.seg_assign(o) == D(2026, 10, 9)
    oc = {**o, "confirm": _open("10-07")}
    assert ork.step_now(oc) == 4 and not ork.pre_open(oc)
    assert ork.assign_at(oc) == ork.assign_at(o) and ork.seg_assign(oc) == ork.seg_assign(o)
    assert ork.clock(oc, 4) == ork.clock(o, 4)
    assert ork.judged(oc, T)["wd"] == ork.judged(o, T)["wd"]
    # стадия шага 1 и задача открыта — шаг 0: назначения нет
    o1 = _o(orkMgr="Олег (ОРК)", assignDate=D(2026, 10, 9), confirm=_open("10-07"))
    assert ork.pre_open(o1) and ork.assign_at(o1) is None and ork.seg_assign(o1) is None


# ── сборка целиком: fixture.ПодтверждениеПередачи ─────────────────────────────────────────
def _build(stub, **kw):
    """contracts.compute (как fixture.build_contracts) и заказы, которые он отдал orkview."""
    got: dict = {}
    orig = orkview.build

    def spy(orders, deals, queue, *a, **k):
        got.setdefault("o", orders)
        got.setdefault("d", deals)
        got.setdefault("q", queue)
        return orig(orders, deals, queue, *a, **k)
    orkview.build = spy
    try:
        ct = fixture.build_contracts(stub=stub, people=getattr(stub, "roster", fixture.CT_ROSTER), **kw)
    finally:
        orkview.build = orig
    return ct, got


@pytest.fixture(scope="module")
def B():
    st = fixture.ПодтверждениеПередачи()
    ct, got = _build(st)
    return {"ct": ct, "lo": {o["id"]: o for o in got["o"]}, "got": got, "stub": st}


def _ord(ct, oid: str) -> dict:
    return next(o for r in ct["rows"] for o in r["orders"] if o["id"] == oid)


def _q(ct, oid: str | None = None, code: str | None = None, deal: str | None = None) -> list[dict]:
    out = []
    for q in lifecycle.per_order(ct["life"]["queue"]):
        if oid is not None and str(q.get("order")) != oid:
            continue
        if deal is not None and str(q.get("deal")) != deal:
            continue
        if code is not None and q["code"] != code:
            continue
        out.append(q)
    return out


def _week_rows(ct) -> dict:
    w = ct["ork"]["week"]
    return {r["k"]: r for r in orkview.unpack(w["orders"], w.get("strs") or [])}


def test_сводка_задач_в_шапке_и_доверии(B):
    ct = B["ct"]
    c = ct["head"]["confirm"]
    assert c["read"] is True and c["n"] >= 14 and c["orders"] >= 12 and c["why"] == ""
    assert c["t0Moved"] == 3                        # 942, 943, 946 — закрыты позже начала
    assert ct["ork"]["trust"]["confirmRead"] is True
    _by, st = ork_confirm.load(B["stub"])
    # приманки 948: задача к сделке, без привязки, «Перешлите: …» (не по названию), до SINCE (не прочитана)
    assert st["deal"] == 1 and st["none"] == 1 and st["skipped"] == 1 and "99781" not in _by


def test_случай_1_открыта_3_рд_шаг_0_без_пунктов(B):
    ct = B["ct"]
    o = _ord(ct, "99701")
    assert o["step"] == 0 and o["stepRole"] == "confirmResp" and o["stepWho"] == "Пётр (ОРК)" and o["stepWd"] == 3
    assert o["stepFrom"] == "2026-10-14" and o["stepFromBy"] == "задача подтверждения" and o["stepPreOver"] is False
    assert o["stepOver"] is False and o["confirmWho"] == "Пётр (ОРК)"
    assert not _q(ct, deal="6300"), "ни s1Over, ни ordAssign, ни noOrder, ни s0Over"
    w = ct["ork"]["week"]
    assert "d:6300" not in w["drill"]["W1"]["k"] and "99701" not in w["drill"]["W1o"]["k"]
    assert "99701" not in w["drill"]["step"]["1"]["orders"]["k"] and "d:6300" not in w["drill"]["step"]["1"]["now"]["k"]
    assert "99701" in w["drill"]["step"]["0"]["now"]["k"] and "99701" not in w["drill"]["step"]["0"]["over"]["k"]
    # сделка, где все живые заказы на шаге 0, — не на приёмке и не «срок неизвестен» (DS.pre)
    g = B["got"]
    U = [x for x in g["o"] if x.get("live")]
    step = {x["id"]: ork.step_now(x) for x in U}
    DS = orkview.deal_steps(g["o"], g["d"], U, step, g["q"])
    assert "6300" in DS["pre"] and "6300" not in DS["acceptL"] and "6300" not in DS["unk"]
    assert "6300" not in DS["accepted"] and "6301" in DS["pre"]


def test_случай_2_открыта_6_рд_один_s0over(B):
    ct = B["ct"]
    qs = _q(ct, "99711")
    assert [q["code"] for q in qs] == ["s0Over"]
    q = qs[0]
    assert q["who"] == "Сергей (снабжение)" and q["whoId"] == "33" and q["role"] == "confirmResp" and q["step"] == 0
    assert q["escalateTo"] == "Анна (КАМ)" and q["copyRole"] == "kam" and q["inWd"] is True and q["days"] == 6
    assert q["ref"] == "с 09.10 (создана задача подтверждения)" and (q["unit"], q["bucket"]) == ("order", "exec")
    assert q["since"] == "2026-10-09" and not q.get("whoOut")


def test_случай_3_закрыта_приёмка_с_закрытия(B):
    ct, lo = B["ct"], B["lo"]
    x = lo["99721"]
    assert x["t0Src"] == "confirm" and x["t0"].startswith("2026-10-14T15:00") and x["t0Raw"].startswith("2026-10-13")
    assert x["_segs"]["open"] == {"seg": "S1", "start": x["t0"]}
    o = _ord(ct, "99721")
    assert o["step"] == 1 and o["stepFrom"] == "2026-10-14" and o["stepFromBy"] == "начало приёмки"
    s1 = _q(ct, "99721", "s1Over")
    assert len(s1) == 1 and s1[0]["days"] == 3 and s1[0]["ref"] == "с 14.10 (подтверждена передача)"
    # закрыта в пятницу 16.10 — 1 р.д., в норме: пункта нет
    assert not _q(ct, "99731", "s1Over") and _ord(ct, "99731")["stepWd"] == 1


def test_краевые_случаи(B):
    ct, lo = B["ct"], B["lo"]
    assert _ord(ct, "99741")["step"] == 0, "открыта и есть дата назначения — шаг 0"
    o = _ord(ct, "99751")
    assert o["step"] == 4, "открыта, а стадия «Заказ направлен» — шаг по стадии"
    co = _q(ct, "99751", "confirmOpen")
    assert len(co) == 1 and (co[0]["unit"], co[0]["bucket"]) == ("order", "hyg")
    assert co[0]["who"] == "Сергей (снабжение)" and co[0]["copyRole"] == "orkHead" and not co[0]["inWd"]
    hyg = {x["order"]: x for x in ct["ork"]["week"]["hygiene"]["items"]}
    assert "confirmOpen" in hyg["99751"]["kinds"] and not hyg["99751"]["excl"]
    assert ct["ork"]["week"]["drill"]["hyg"]["confirmOpen"]["k"] == ["99751"]
    s1 = lo["99761"]["_segs"]["segs"]["S1"]
    assert s1["wd"] == 0 and s1["endBy"] == "назначение", "назначение до закрытия задачи — с закрытия"
    o = _ord(ct, "99761")
    assert o["step"] == 4 and o["stepFrom"] == "2026-10-14" and o["stepFromBy"] == "назначение"
    o = _ord(ct, "99771")
    assert o["step"] == 0 and o["stepFrom"] == "2026-10-15", "две задачи, открыта хоть одна — шаг 0"
    assert [p[:2] for p in o["path"]] == [[0, "2026-10-08"], [1, "2026-10-09"], [0, "2026-10-15"]]
    o = _ord(ct, "99781")
    assert o["step"] == 1 and "confirmWho" not in o, "приманки — пропуск"
    assert _ord(ct, "99791")["step"] == 0, "статус 4 — открыта"
    assert _ord(ct, "99801")["confirmWho"] == "Ульяна (снабжение) (уволен)"
    assert _ord(ct, "99801")["stepWho"] == "Ульяна (снабжение) (уволен)"
    assert _ord(ct, "99812")["step"] == 0 and not _q(ct, "99812", "ordAssign"), "принятая сделка: ordAssign нет"
    assert not lifecycle._deal_accept([lo["99801"]]), "открытая задача — сделка не принята"


def test_замедлено_шаг_0_при_тихом_чате(B):
    """Решение владельца 31.10.2026, г: долгий шаг 0 при тихом чате — «Замедлено» сделки; причина —
    подтверждение передачи (исполнитель задачи), а в W2 и «сверх нормы» ОРК — нет."""
    r = next(r for r in B["ct"]["rows"] if r["deal"] == "6301")
    assert r["slow"] is True and contracts_mod.PRE_SLOW_WHY in r["why"] and "сверх нормы шага" not in r["why"]
    c = r["stepCell"]
    assert (c["step"], c["preOver"], c["over"], c["role"], c["wd"]) == (0, True, False, "confirmResp", 6)
    o = _ord(B["ct"], "99711")
    assert o["slow"] is True and o["stepPreOver"] is True and o["stepOver"] is False
    assert "99711" not in B["ct"]["ork"]["week"]["drill"]["W2"]["k"]


def test_строка_0_ведомости_пульс_и_списки(B):
    ct = B["ct"]
    w = ct["ork"]["week"]
    led0 = next(x for x in w["ledger"] if x["no"] == 0)
    rows = _week_rows(ct)
    at0 = [k for k, r in rows.items() if r["step"] == 0]
    pre = [p for p in w["pulse"]["rows"] if p["kind"] == "pre"]
    s0 = [q for q in lifecycle.per_order(ct["life"]["queue"]) if q["code"] == "s0Over"]
    assert led0["unit"] == "order" and led0["role"] == "confirmResp" and led0["norm"] == 5 and "nodata" not in led0
    assert led0["now"] == len(at0) == pre[0]["n"] == len(w["drill"]["step"]["0"]["now"]["k"]) == ct["ork"]["trust"]["pre"]
    assert led0["over"] == len(s0) == len(w["drill"]["step"]["0"]["over"]["k"]) == pre[0]["preOver"] >= 2
    assert pre[0]["who"] == orkview.PULSE_PRE and pre[0]["over"] == 0
    assert not [k for k in at0 if rows[k].get("over")], "у строк шага 0 нет over"
    assert all(rows[k]["cause"] == "pre" for k in at0)
    assert w["pulse"]["over"] == w["tiles"]["W2"]["value"], "Σ «Пульса» = W2"
    assert all(q["role"] == "confirmResp" and q["copyRole"] == "kam" for q in s0)
    how = ct["ork"]["how"]
    assert how["steps"][0]["no"] == 0 and how["steps"][0]["measure"] == "task" and how["norms"]["0"] == 5
    assert sum(r["now"] if r["unit"] == "order" else r["ordersNow"] for r in w["ledger"]) + w["unknown"] == w["live"]


def test_очередь_кама_s0over(B):
    """Решение владельца 31.10.2026, в: s0Over — и в очереди КАМа сделки, важность как у приёмки."""
    ct = B["ct"]
    kq = kamqueue.build(None, contracts_mod.life_all(ct), ct["rows"], T, people=fixture.CT_PEOPLE)
    s0 = [q for q in kq["queue"] if q["code"] == "s0Over"]
    assert {q["deal"] for q in s0} >= {"6301", "6034"}
    q = next(q for q in s0 if q["deal"] == "6301")
    assert q["kam"] == "Анна (КАМ)" and q["owner"] == "Сергей (снабжение)" and q["kamCopy"] is True
    assert q["act"] == "добиться закрытия задачи подтверждения передачи в ОРК" and q["step"] == 0 and q["inWd"]


def test_заказ_не_передан_в_орк_исполнитель_ответственный(B):
    """Решение 31.10.2026 (заказ 968/2): в поле сделки — руководитель ОРК, ответственный заказа не из
    ОРК → исполнитель шага — ответственный (whoOut), руководитель ОРК — в копии, менеджером заказа
    не числится; «Кто сколько ведёт» — строка «ответственный вне ОРК», не руководитель."""
    ct = B["ct"]
    r = next(r for r in ct["rows"] if r["deal"] == "6312")
    assert r["orkResp"] == "" and r["orkRespHead"] is True
    o = r["orders"][0]
    assert o["orkMgr"] == "" and o["stepWho"] == "Сергей (снабжение)" and o["stepWhoOut"] is True
    q = _q(ct, "99821", "s2Over")[0]
    assert q["who"] == "Сергей (снабжение)" and q["whoOut"] is True and q["whoId"] == "33"
    assert q["escalateTo"] == "Роза (рук. ОРК)" and q["escalateToId"] == "30" and q["outAct"] == ork.OUT_ACT
    rw = _week_rows(ct)["99821"]
    assert rw["mgr"] == orkview.PULSE_OUT and rw["ownOut"] is True
    assert not any(p["who"] == "Роза (рук. ОРК)" and p["kind"] == "mgr" for p in ct["ork"]["week"]["pulse"]["rows"])
    nm = next(m for m in ct["ork"]["week"]["overByWho"] if m["who"] == "не назначен")
    assert "99821" in ct["ork"]["week"]["drill"]["who"]["не назначен"]["over"] and nm["n"] >= 1


def test_w2_и_пульс_не_растут_от_задач(B):
    """Инвариант (plan, раздел 4): задачи W2 и Σ pulse.over не увеличивают — из W2 уходят только
    заказы, которые теперь на шаге 0 (открытая задача при назначении: приёмки ещё нет); Σ pulse.over
    = W2 и с задачами, и без; W3, живые заказы и строки сделок — те же."""
    st = fixture.ПодтверждениеПередачи()
    без, _g = _build(st, confirm_tasks=None)
    с = B["ct"]
    w0, w1 = без["ork"]["week"], с["ork"]["week"]
    k0, k1 = set(w0["drill"]["W2"]["k"]), set(w1["drill"]["W2"]["k"])
    rows1 = _week_rows(с)
    moved = {k for k in k0 if rows1[k]["step"] == 0}
    assert k1 == k0 - moved and moved == {"99741"}
    assert w0["pulse"]["over"] == w0["tiles"]["W2"]["value"] and w1["pulse"]["over"] == w1["tiles"]["W2"]["value"]
    assert sum(m["n"] for m in w1["overByWho"]) == w1["tiles"]["W2"]["value"]
    assert w0["tiles"]["W3"]["value"] == w1["tiles"]["W3"]["value"] and w0["live"] == w1["live"]
    assert len(без["rows"]) == len(с["rows"]) and без["deadlines"]["liveOrders"] == с["deadlines"]["liveOrders"]
    assert без["head"]["confirm"]["read"] is False and not [k for k, r in _week_rows(без).items() if r["step"] == 0]


# ── отказ без последствий на сборке целиком ───────────────────────────────────────────────
class Глухой(fixture.ContractsStub):
    """Портал без права task: tasks.task.list отказывает (как AssertionError заглушки)."""

    def tasks_list(self, *a, **k):
        raise BitrixError("ACCESS_DENIED")


def test_задачи_не_прочитаны_нет_данных_и_шага_0_нет():
    ct = fixture.build_contracts(stub=Глухой())
    c = ct["head"]["confirm"]
    assert c["read"] is False and "BitrixError" in c["why"]
    w = ct["ork"]["week"]
    led0 = next(x for x in w["ledger"] if x["no"] == 0)
    assert led0["nodata"] is True and "не прочитаны" in led0["why"] and led0["now"] == 0
    assert ct["ork"]["trust"]["confirmRead"] is False and ct["ork"]["trust"]["pre"] == 0
    assert not [o for r in ct["rows"] for o in r["orders"] if o["step"] == 0]
    assert not [q for q in ct["life"]["queue"] if q["code"] in ("s0Over", "confirmOpen")]
    assert not w["drill"]["step"]["0"]["now"]["k"]


def _без_шага_0(mp) -> None:
    """Таблицы, какими они были до шага 0 (ork.STEPS без строки 0, коды без s0Over/confirmOpen):
    сборка без задач обязана дать те же числа, что и с шагом 0 в таблицах."""
    steps = tuple(s for s in ork.STEPS if s["no"] != 0)
    mp.setattr(ork, "STEPS", steps)
    mp.setattr(ork, "STEP", {s["no"]: s for s in steps})
    for name in ("NORM_WD", "EXECUTOR", "NORM_DOC", "TERM_CODE"):
        mp.setattr(ork, name, {k: v for k, v in getattr(ork, name).items() if k != 0})
    mp.setattr(ork, "PRE_CODE", {})
    mp.setattr(ork, "TERM_CODES", frozenset(ork.TERM_CODE.values()) | frozenset(ork.TERM_EXTRA))
    mp.setattr(lifecycle, "EXC", {k: v for k, v in lifecycle.EXC.items() if k not in ("s0Over", "confirmOpen")})


def _diff(a, b, path="", out=None) -> list[str]:
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            if k not in a or k not in b:
                out.append(f"{path}.{k}")
            else:
                _diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(path)
    return out


def _strip_new(ct: dict) -> dict:
    """Ключи, которые шаг 0 добавляет и при непрочитанных задачах (их и перечисляет план):
    ledger[0], ledgerUnits.order 0, drill.step["0"], how.steps[0], how.norms["0"],
    life.rules.norms["0"], life.labels s0Over/confirmOpen."""
    import copy
    ct = copy.deepcopy(ct)
    for v in (ct, ct.get("sep") or {}):
        life = v.get("life") or {}
        (life.get("rules") or {}).get("norms", {}).pop(0, None)
        for k in ("s0Over", "confirmOpen"):
            (life.get("labels") or {}).pop(k, None)
        o = v.get("ork") or {}
        w = o.get("week") or {}
        w["ledger"] = [r for r in w.get("ledger") or [] if r["no"] != 0]
        lu = w.get("ledgerUnits") or {}
        lu["order"] = [x for x in lu.get("order") or [] if x != 0]
        ((w.get("drill") or {}).get("step") or {}).pop("0", None)
        h = o.get("how") or {}
        h["steps"] = [s for s in h.get("steps") or [] if s["no"] != 0]
        (h.get("norms") or {}).pop("0", None)
        # списки по исполнителю приёмки и назначения — из множеств: порядок — от хэша прогона
        for key in ("whoAccept", "whoAssign"):
            for vv in ((w.get("drill") or {}).get(key) or {}).values():
                if isinstance(vv, list):
                    vv.sort()
    return ct


def test_без_задач_числа_прежние(monkeypatch):
    """Отказ без последствий (plan, раздел 5): задачи не прочитаны — payload тот же, что был бы без
    шага 0 в таблицах, кроме списка новых ключей (_strip_new). Числа — все прежние."""
    now = _strip_new(fixture.build_contracts(confirm_tasks=None))
    with monkeypatch.context() as mp:
        _без_шага_0(mp)
        old = _strip_new(fixture.build_contracts(confirm_tasks=None))
    d = _diff(now, old)
    assert not d, d[:20]


# ── ревью шага 0 (01.11.2026): сборка целиком ─────────────────────────────────────────────
class ПослеРевью(fixture.ПодтверждениеПередачи):
    """Поверх ПодтверждениеПередачи — придуманные сделки 935, 936, 950 (6395, 6390, 6396):
      • 936 — в поле сделки руководитель ОРК, единственный живой заказ на шаге 0 (задача открыта с
        пт 09.10), ответственный '33' не из ОРК, срок клиента близко: пропуска «менеджер ОРК» нет (не
        «ушёл»), пункты заказа — у исполнителя задачи, ни одного whoOut;
      • 935 — заказ шага 4 сверх нормы и заказ шага 0 дольше 5 р.д.: ячейка шага сделки — сверх нормы ОРК;
      • 950 — задача открыта, стадия «Заказ направлен» с 14.10, назначение 05.10: срок шага 4 — от
        назначения, как без задачи (стадия сильнее, решение 31.10.2026, а)."""

    def __init__(self):
        super().__init__()
        import contracts as ct
        T_ = lambda m, day, hm="10:00": f"2026-{m}-{day}T{hm}:00+03:00"  # noqa: E731
        d = lambda a: (fixture.CT_TODAY - dt.timedelta(days=a)).isoformat()  # noqa: E731
        plan = {ct.PROD_START: d(-5), ct.PROD_END: d(-40), ct.DEL_BUDGET: d(-80), ct.DL_CUSTOMER: d(-90)}
        for did, seq, title, extra in (("6390", 936, "Не передан, всё на шаге 0", {ct.ORK_RESP: "30"}),
                                       ("6395", 935, "Сверх нормы и шаг 0", {}),
                                       ("6396", 950, "Назначен, стадия ушла, задача открыта", {})):
            self.deals.append({"ID": did, "TITLE": f"{seq}. {title}", "OPPORTUNITY": "30000", "CURRENCY_ID": "EUR",
                               "COMPANY_ID": "305", "STAGE_ID": "C0:EXECUTING", "STAGE_SEMANTIC_ID": "P",
                               "CATEGORY_ID": "0", "DATE_CREATE": T_("09", 10), "ASSIGNED_BY_ID": "11",
                               ct.OSS_DEAL: ["21"], ct.KAM_F: self.KAM, **extra})
            self.hist[2] += [(did, "C0:NEW", T_("09", 12)), (did, "C0:EXECUTING", T_("09", 14))]

        def order(oid, did, lbl, created, resp="31", stages=(("NEW", None),), **extra):
            self.orders.append({"id": oid, "title": f"{lbl}. Узел", "companyId": "404", "parentId2": int(did),
                                "opportunity": 9_000, "currencyId": "EUR", "stageId": f"DT172_26:{stages[-1][0]}",
                                "createdTime": created, "movedTime": stages[-1][1] or created, ct.OSS_F: "21",
                                "assignedById": resp, **{**plan, **extra}})
            for st_, at in stages:
                self.hist[172].append((str(oid), f"DT172_26:{st_}", at or created))

        order(99901, "6390", "936/1", T_(10, "09"), resp="33", **{ct.DL_CUSTOMER: d(-2), ct.PROD_END: d(-20)})
        self.tasks.append(fixture.ct_task(5301, 99901, T_(10, "09", "11:00"), resp="33"))
        order(99951, "6395", "935/1", T_("09", 20), stages=(("NEW", T_("09", 20)), ("SENT", T_(10, "05"))))
        order(99952, "6395", "935/2", T_(10, "09"))
        self.tasks.append(fixture.ct_task(5302, 99952, T_(10, "09", "11:00"), resp="33"))
        order(99961, "6396", "950/1", T_("09", 28), stages=(("NEW", T_("09", 28)), ("SENT", T_(10, 14))),
              **{ct.ASSIGN_AT: "2026-10-05"})
        self.tasks.append(fixture.ct_task(5303, 99961, T_("09", 28, "11:00"), resp="33"))


@pytest.fixture(scope="module")
def R():
    st = ПослеРевью()
    ct, got = _build(st)
    без, _g = _build(ПослеРевью(), confirm_tasks=None)
    return {"ct": ct, "без": без, "lo": {o["id"]: o for o in got["o"]}}


def test_ревью_все_заказы_на_шаге_0_без_менеджера_нет_пропуска(R):
    ct = R["ct"]
    r = next(r for r in ct["rows"] if r["deal"] == "6390")
    assert r["orkResp"] == "" and r["orders"][0]["step"] == 0
    no = ct["ork"]["week"]["noOwner"]
    assert "d:6390" not in ct["ork"]["week"]["drill"]["noOwner"]["k"]
    assert not [x for x in no["rows"] if str(x.get("deal")) == "6390"]
    qs = _q(ct, "99901")
    assert qs and {q["role"] for q in qs} == {"confirmResp"}, [(q["code"], q["role"]) for q in qs]
    assert not [q for q in qs if q.get("whoOut") or q.get("outAct")]
    assert all(q["who"] == "Сергей (снабжение)" and q["copyRole"] == "kam" for q in qs)
    # ни одного whoOut у заказа шага 0 во всей очереди
    assert not [q for q in lifecycle.per_order(ct["life"]["queue"]) if q.get("whoOut") and q.get("step") == 0]


def test_ревью_ячейка_шага_сверх_нормы_орк_раньше_шага_0(R):
    c = next(r for r in R["ct"]["rows"] if r["deal"] == "6395")["stepCell"]
    assert (c["step"], c["over"], c["order"]) == (4, True, "99951")
    assert _ord(R["ct"], "99952")["stepPreOver"] is True


def test_ревью_назначение_при_ушедшей_стадии_как_без_задачи(R):
    o, o0 = _ord(R["ct"], "99961"), _ord(R["без"], "99961")
    assert o["step"] == o0["step"] == 4
    assert (o["stepFrom"], o["stepFromBy"], o["stepWd"]) == (o0["stepFrom"], o0["stepFromBy"], o0["stepWd"])
    assert o["stepFromBy"] == "назначение" and o["stepFrom"] == "2026-10-05"
    assert _q(R["ct"], "99961", "confirmOpen")


def test_ревью_whoout_руководитель_орк_вторым_в_копии(R):
    """Решение 31.10.2026 (заказ 968/2): у whoOut-пункта со своей копией (КАМ) руководитель ОРК —
    вторым в копии с id (alsoCopyId: «Смотреть как» руководителя ОРК пункт не теряет)."""
    wo = [q for q in lifecycle.per_order(R["ct"]["life"]["queue"]) if q.get("whoOut")]
    assert wo
    for q in wo:
        assert q["outAct"] == ork.OUT_ACT
        if q["copyRole"] != "orkHead":
            assert (q["alsoCopy"], q["alsoCopyRole"], q["alsoCopyId"]) == ("Роза (рук. ОРК)", "orkHead", "30"), q["code"]


def test_ревью_пункт_заказа_dealctx_не_переданного_в_орк(R):
    """Решение 31.10.2026 (заказ 968/2) и для пунктов заказа dealctx (чат, договор поставщика):
    исполнитель — ответственный заказа не из ОРК (respOut строки заказа), whoOut, руководитель ОРК
    в копии (или вторым в копии), как у пункта очереди s2Over того же заказа."""
    r = next(r for r in R["ct"]["rows"] if r["deal"] == "6312")
    o = next(o for o in r["orders"] if o["id"] == "99821")
    assert o["respOut"] == "Сергей (снабжение)" and o["orkMgr"] == ""
    ppl = dealctx._people(r, "Роза (рук. ОРК)")
    own = dealctx._reg("contractMissing", r, ppl, key="contractMissing:supplier", order=o)
    assert (own["who"], own["whoOut"], own["outAct"], own["copyRole"], own["escalateTo"]) == (
        "Сергей (снабжение)", True, ork.OUT_ACT, "orkHead", "Роза (рук. ОРК)")
    own = dealctx._reg("chatDue", r, ppl, key="chatDue", order=o)
    assert own["whoOut"] and own["copyRole"] == "kam" and own["alsoCopy"] == "Роза (рук. ОРК)"
    # ответственный из ОРК — подписи в строке нет
    assert not [x for rr in R["ct"]["rows"] for x in rr["orders"] if x.get("respOut") and x.get("orkMgr")]



class ЗакрытаПослеСтадии(fixture.ПодтверждениеПередачи):
    """Живые 01.11.2026: у 9 заказов задачу подтверждения закрыли, когда стадия уже ушла с шага 1
    (ОРК взял заказ при открытой задаче). Приёмка (t0) сдвигалась закрытием — S1 «конец раньше
    начала» и выпадал из M1. Решение владельца 01.11.2026: шаг 0 до ухода стадии, приёмка 0 р.д.
    Придумано (правило 18), сегодня — пн 19.10:
      • 934 — заказ заведён пн 12.10, «Заказ направлен» вт 13.10, задача закрыта ср 14.10 15:00;
      • 933 — «Заказ направлен» вт 13.10, возврат в «Заказ создан» ср 14.10, задача закрыта пт 16.10:
        в момент закрытия — шаг 1, приёмка с закрытия (как без правки);
      • 932 — назначение вт 13.10 при открытой задаче, «Заказ направлен» чт 15.10, задача закрыта пт
        16.10: шаг 0 до 15.10, назначение — с 15.10 (не 13.10), срок шага 4 — с 15.10."""

    def __init__(self):
        super().__init__()
        import contracts as ct
        T_ = lambda day, hm="10:00": f"2026-10-{day}T{hm}:00+03:00"  # noqa: E731
        d = lambda a: (fixture.CT_TODAY - dt.timedelta(days=a)).isoformat()  # noqa: E731
        plan = {ct.PROD_START: d(-5), ct.PROD_END: d(-40), ct.DEL_BUDGET: d(-80), ct.DL_CUSTOMER: d(-90)}
        for did, seq, oid, stages, close, extra in (
                ("6397", 934, 99971, (("NEW", T_(12)), ("SENT", T_(13))), T_(14, "15:00"), {}),
                ("6398", 933, 99972, (("NEW", T_(12)), ("SENT", T_(13)), ("NEW", T_(14))), T_(16, "15:00"), {}),
                ("6399", 932, 99973, (("NEW", T_(12)), ("SENT", T_(15))), T_(16, "15:00"), {ct.ASSIGN_AT: "2026-10-13"})):
            self.deals.append({"ID": did, "TITLE": f"{seq}. Задача закрыта после ухода стадии", "OPPORTUNITY": "30000",
                               "CURRENCY_ID": "EUR", "COMPANY_ID": "305", "STAGE_ID": "C0:EXECUTING",
                               "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0", "DATE_CREATE": T_("01"),
                               "ASSIGNED_BY_ID": "11", ct.OSS_DEAL: ["21"], ct.KAM_F: self.KAM})
            self.hist[2] += [(did, "C0:NEW", T_("02")), (did, "C0:EXECUTING", T_("05"))]
            self.orders.append({"id": oid, "title": f"{seq}/1. Узел", "companyId": "404", "parentId2": int(did),
                                "opportunity": 9_000, "currencyId": "EUR", "stageId": f"DT172_26:{stages[-1][0]}",
                                "createdTime": T_(12), "movedTime": stages[-1][1], ct.OSS_F: "21",
                                "assignedById": "31", **plan, **extra})
            self.hist[172] += [(str(oid), f"DT172_26:{st_}", at) for st_, at in stages]
            self.tasks.append(fixture.ct_task(5400 + oid % 100, oid, T_(12, "11:00"), status="5", closed=close))


@pytest.fixture(scope="module")
def L():
    st = ЗакрытаПослеСтадии()
    ct, got = _build(st)
    return {"ct": ct, "lo": {o["id"]: o for o in got["o"]}}


def test_задача_закрыта_после_ухода_стадии_приёмка_0_рд(L):
    """934: в момент закрытия стадия на шаге 4 — t0 = уход стадии (вт 13.10), S1 0 р.д., в норме, без
    «конец раньше начала» — входит в M1 (решение владельца 01.11.2026)."""
    lo = L["lo"]["99971"]
    assert lo["t0Src"] == "confirmLate" and lo["t0"] == "2026-10-13T10:00:00+03:00"
    s1 = lo["_segs"]["segs"]["S1"]
    assert not s1["neg"] and s1["wd"] == 0 and s1["inNorm"] is True and orkview._clean(s1)
    assert _ord(L["ct"], "99971")["step"] == 4
    assert not _q(L["ct"], "99971", "confirmOpen") and not _q(L["ct"], "99971", "s0Over")


def test_возврат_на_шаг_1_до_закрытия_приёмка_с_закрытия(L):
    """933: вернули в «Заказ создан» до закрытия задачи — в момент закрытия шаг 1: приёмка с закрытия
    (решение 31.10.2026, 1), срок шага 1 — 1 р.д., пунктов приёмки нет."""
    lo = L["lo"]["99972"]
    assert lo["t0Src"] == "confirm" and lo["t0"] == "2026-10-16T15:00:00+03:00"
    assert ork.clock(lo, 1)[0] == dt.date(2026, 10, 16)
    assert not _q(L["ct"], "99972", "s1Over") and not _q(L["ct"], "99972", "ordAssign")


def test_назначение_при_открытой_задаче_с_ухода_стадии(L):
    """932: назначение вт 13.10 при открытой задаче — это ещё шаг 0; приёмка и назначение — с ухода
    стадии чт 15.10, S1 0 р.д., срок шага 4 — с 15.10, s2Over нет (пт 16.10 и пн 19.10 — 2 р.д.)."""
    lo = L["lo"]["99973"]
    assert lo["t0Src"] == "confirmLate" and lo["t0"] == "2026-10-15T10:00:00+03:00"
    assert ork.assign_at(lo) == dt.date(2026, 10, 15) and ork.seg_assign(lo) == dt.date(2026, 10, 15)
    s1 = lo["_segs"]["segs"]["S1"]
    assert s1["wd"] == 0 and not s1["neg"]
    assert not _q(L["ct"], "99973", "s2Over")


def test_счёт_сдвига_приёмки_в_шапке(L):
    """t0Late — 934 и 932; t0Moved — 942, 943, 946 и 933. «Конец раньше начала» остаётся только у
    возвращённого 933: первый выход на шаг ≥ 2 (13.10) раньше приёмки с закрытия (16.10) — возврат,
    S1 вне M1, как без правки."""
    c = L["ct"]["head"]["confirm"]
    assert c["t0Late"] == 2 and c["t0Moved"] == 4 and c["s1Neg"] == 1
    assert L["lo"]["99972"]["_segs"]["segs"]["S1"]["neg"]


def test_confirm_late_t0_граничные_случаи():
    names = {"N": "Заказ создан", "S": "Заказ направлен"}
    T_ = lambda day, hm="10:00": f"2026-10-{day}T{hm}:00+03:00"  # noqa: E731
    at = dt.datetime.fromisoformat
    # переходов нет / задача не закрыта — None (приёмка как без правки)
    assert ork.confirm_late_t0([], names, T_(14)) is None
    assert ork.confirm_late_t0([("N", T_(12)), ("S", T_(13))], names, None) is None
    # заказ заведён сразу на шаге ≥ 2 — t0 = первый переход, не раньше t0Raw
    assert ork.confirm_late_t0([("S", T_(12))], names, T_(14), T_(11)) == at(T_(12))
    # переход в тот же день, что закрытие, но позже — в момент закрытия шаг 1
    assert ork.confirm_late_t0([("N", T_(12)), ("S", T_(14, "16:00"))], names, T_(14, "15:00")) is None
    # t0Raw позже ухода стадии (сделка вошла в реализацию позже) — t0Raw
    assert ork.confirm_late_t0([("N", T_(12)), ("S", T_(13))], names, T_(16), T_(14)) == at(T_(14))
