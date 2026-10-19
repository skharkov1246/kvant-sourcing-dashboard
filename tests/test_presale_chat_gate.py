"""Золотое правило чата для пресейла (решение владельца 19.10.2026, как в реализации):
сделка «стоит» — только если и в её чате тихо. Плюс КАМ сделки — только из поля «КАМ»
и полный список открытых сделок для очереди КАМа.

Портал придуман (правило 18): ответы im.dialog.messages.get — той же формы, что в Bitrix.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import chat_activity  # noqa: E402
import presale  # noqa: E402
from tests import fixture  # noqa: E402

СЕГОДНЯ = dt.date(2026, 10, 19)
ЧАТ = chat_activity.CHAT_FIELD


def _d(days_ago: int) -> str:
    return (СЕГОДНЯ - dt.timedelta(days=days_ago)).isoformat() + "T10:00:00+03:00"


class Чаты:
    """Ответы чатов: {номер чата: дней назад писал человек | None (тишина) | исключение}."""

    def __init__(self, чаты: dict):
        self.чаты, self.вызовы = чаты, []

    def call(self, method, params=None, **kw):
        assert method == "im.dialog.messages.get"
        n = str(params["DIALOG_ID"]).removeprefix("chat")
        self.вызовы.append(n)
        v = self.чаты.get(n)
        if isinstance(v, Exception):
            raise v
        msgs = [] if v is None else [{"author_id": 31, "date": (СЕГОДНЯ - dt.timedelta(days=v)).isoformat()}]
        return {"messages": msgs}


class BitrixLimitError(RuntimeError):
    """Как bitrix_client.BitrixLimitError — читатель узнаёт её по имени класса."""


СТАДИИ = {"C32:SEARCH": {"name": "Поиск поставщиков и сбор КП", "sem": "P", "sort": 40, "cat": "32"},
          "C32:WAIT": {"name": "Сорсинг завершен / Ожидаем решение заказчика", "sem": "P", "sort": 90, "cat": "32"}}


def сделка(did: str, дней: int, *, чат: str = "", кам: str = "", ответственный: str = "50") -> dict:
    d = {"ID": did, "TITLE": f"Узел {did}", "STAGE_ID": "C32:WAIT", "DATE_CREATE": _d(дней + 5),
         "MOVED_TIME": _d(дней), "ASSIGNED_BY_ID": ответственный, ЧАТ: чат}
    if кам:
        d[presale.KAM_F] = кам
    return d


СДЕЛКИ = [
    сделка("1", 30, чат="101", кам="50"),    # стоит, в чате тихо месяц — стоит
    сделка("2", 20, чат="102", кам="50"),    # стоит, человек писал 2 дня назад — работа в чате
    сделка("3", 18, чат="103", кам="51"),    # стоит, чат недоступен — не проверен
    сделка("4", 16, кам="51"),               # стоит, id чата нет — не проверен
    сделка("5", 3, чат="105", кам="50"),     # не стоит — чат не читается вовсе
]
ЧАТЫ = {"101": 30, "102": 2, "103": RuntimeError("ACCESS_DENIED"), "105": 0}
ЛЮДИ = {"50": {"name": "Астахова Вера", "active": True}, "51": {"name": "Ежов Тарас", "active": True}}


def _ps(reader=None, deals=None) -> dict:
    return presale.compute(cid="32", cat_name="Пресейл", deals=deals or СДЕЛКИ, stage_meta=СТАДИИ, rfqs=[],
                           people=ЛЮДИ, names={}, today=СЕГОДНЯ, chat_reader=reader)


def test_тишина_стоит_работа_в_чате_нет_непрочитанный_серый():
    портал = Чаты(ЧАТЫ)
    ps = _ps(chat_activity.Reader(портал, set()))
    flags = {r["id"]: r["flags"] for r in ps["openDeals"]}
    assert "stale" in flags["1"]
    assert "staleChat" in flags["2"] and "stale" not in flags["2"]
    assert "staleUnverified" in flags["3"] and "staleUnverified" in flags["4"]
    assert flags["5"] == []
    h = ps["head"]
    assert (h["stale"], h["staleChat"], h["staleUnverified"]) == (1, 1, 2)
    assert h["staleStage"] == h["stale"] + h["staleChat"] + h["staleUnverified"] == 4
    assert h["chatGate"] is True
    assert sorted(портал.вызовы) == ["101", "102", "103"], "читаются только чаты застоявшихся сделок"
    чат = {r["id"]: r["chat"] for r in ps["openDeals"]}
    assert чат["2"]["status"] == "active" and чат["2"]["days"] == 2
    assert чат["5"] is None


def test_работа_в_чате_не_требует_действия_а_непроверенный_виден():
    ps = _ps(chat_activity.Reader(Чаты(ЧАТЫ), set()))
    act = {r["id"] for r in ps["act"]}
    assert "2" not in act, "в чате идёт работа — в «Требует действия» не попадает"
    assert {"1", "3", "4"} <= act, "тихий и непроверенный чат — в списке"
    by = {s["name"]: s for s in ps["stages"]}
    assert by["Сорсинг завершен / Ожидаем решение заказчика"]["stale"] == 1, "по стадии «стоят» — подтверждённые"


def test_без_читателя_прежнее_поведение():
    ps = _ps(None)
    h = ps["head"]
    assert (h["stale"], h["staleChat"], h["staleUnverified"], h["staleStage"]) == (4, 0, 0, 4)
    assert h["chatGate"] is False
    assert all(r["chat"] is None for r in ps["openDeals"])


def test_бюджет_общий_с_реализацией_пресейл_читает_остатком():
    портал = Чаты(ЧАТЫ)
    читатель = chat_activity.Reader(портал, set(), budget=2)
    # реализация читает первой и тратит оба запроса
    реал = читатель.check([("900", "901", 1e6), ("901", "902", 1e5)], СЕГОДНЯ)
    assert {r["status"] for r in реал.values()} == {"quiet"} and читатель.left == 0
    ps = _ps(читатель)
    h = ps["head"]
    assert (h["stale"], h["staleChat"], h["staleUnverified"]) == (0, 0, 4), "бюджета нет — все не проверены"
    assert портал.вызовы == ["901", "902"], "пресейл не сделал ни одного запроса сверх бюджета"


def test_лимит_портала_у_реализации_останавливает_и_пресейл():
    портал = Чаты({"901": BitrixLimitError("QUERY_LIMIT_EXCEEDED"), **ЧАТЫ})
    портал.limit_wait_budget = 1800.0
    читатель = chat_activity.Reader(портал, set())
    читатель.check([("900", "901", 1.0)], СЕГОДНЯ)
    assert читатель.stopped and портал.limit_wait_budget == 1800.0
    ps = _ps(читатель)
    assert ps["head"]["staleUnverified"] == 4 and портал.вызовы == ["901"]
    assert читатель.stats() == {"reads": 1, "budget": chat_activity.MAX_CHATS,
                                "left": chat_activity.MAX_CHATS - 1, "stopped": True}


def test_кам_только_из_поля_а_не_ответственный():
    ps = _ps(None)
    r = {x["id"]: x for x in ps["openDeals"]}
    assert r["4"]["kamId"] == "51" and r["4"]["kam"] == "Ежов Тарас"
    без = presale.compute(cid="32", cat_name="Пресейл", deals=[сделка("9", 20, ответственный="50")],
                          stage_meta=СТАДИИ, rfqs=[], people=ЛЮДИ, names={}, today=СЕГОДНЯ)
    x = без["openDeals"][0]
    assert x["kamId"] == "" and x["kam"] == presale.KAM_NONE, "ответственный сделки не подставляется вместо КАМа"
    assert x["who"] == presale.KAM_NONE, "мяч у КАМа, а КАМа нет — так и написано"


def test_список_открытых_полный_а_таблица_действий_обрезана():
    много = [сделка(str(100 + i), 20 + i, кам="50") for i in range(75)]
    ps = _ps(None, deals=много)
    assert len(ps["act"]) == 60
    assert len(ps["openDeals"]) == 75, "очереди КАМа нужны все открытые сделки"
    assert ps["openDeals"][0]["days"] >= ps["openDeals"][-1]["days"], "сначала дольше всех стоящие"


def test_в_выборке_сделок_есть_поле_чата():
    assert ЧАТ in presale.DEAL_SELECT and presale.KAM_F in presale.DEAL_SELECT


def test_синтетика_smoke_сходится():
    h = fixture.build_presale()["head"]
    assert h["staleStage"] == h["stale"] + h["staleChat"] + h["staleUnverified"]
    assert h["stale"] and h["staleChat"] and h["staleUnverified"], "smoke рисует все три корзины"


def test_чаты_кандидатов_очереди_кама_читаются_флаги_не_меняются():
    """chat_also: сделка с мячом у КАМа сверх порога очереди (5 дн. > 3) — меньше простоя
    пресейла (14), но её чат читается: золотое правило снимает и пункт «мяч у КАМа»."""
    import kamqueue
    стадии = dict(СТАДИИ, **{"C32:READY": {"name": "Экономика готова | Можно подавать", "sem": "P",
                                           "sort": 70, "cat": "32"}})
    сделки = [dict(сделка("21", 5, чат="121", кам="50"), STAGE_ID="C32:READY"),     # 5 > 3 — читать
              dict(сделка("22", 2, чат="122", кам="50"), STAGE_ID="C32:READY"),     # 2 ≤ 3 — не читать
              сделка("23", 30, чат="123", кам="51")]                                # простой по стадии
    портал = Чаты({"121": 1, "123": 40})

    def ps(reader):
        return presale.compute(cid="32", cat_name="Пресейл", deals=сделки, stage_meta=стадии, rfqs=[],
                               people=ЛЮДИ, names={}, today=СЕГОДНЯ, chat_reader=reader,
                               chat_also=kamqueue.chat_wanted)
    out = ps(chat_activity.Reader(портал, set()))
    r = {x["id"]: x for x in out["openDeals"]}
    assert sorted(портал.вызовы) == ["121", "123"], "в пороге (22) чат не читается"
    assert r["21"]["chat"]["status"] == "active" and r["21"]["flags"] == [], "флагов простоя не добавляет"
    assert r["22"]["chat"] is None
    assert r["23"]["flags"] == ["stale"] and r["23"]["chat"]["status"] == "quiet"
    h = out["head"]
    assert (h["stale"], h["staleChat"], h["staleUnverified"], h["staleStage"]) == (1, 0, 0, 1), \
        "сверка простоя по стадии не тронута"
    # бюджет на один запрос: простой по стадии (дольше стоит) читается первым
    портал2 = Чаты({"121": 1, "123": 40})
    out2 = ps(chat_activity.Reader(портал2, set(), budget=1))
    r2 = {x["id"]: x for x in out2["openDeals"]}
    assert портал2.вызовы == ["123"] and r2["21"]["chat"]["status"] == "skipped"
    q2 = {x["deal"]: x for x in kamqueue.build(out2, None, None, СЕГОДНЯ)["queue"] if x["code"] == "ballKam"}
    assert q2["21"]["chat"] == "чат не проверен" and q2["23"]["chat"] == "в чате тихо 40 дн."
    # без читателя — ничего не читается, пункт помечен
    q = kamqueue.build(ps(None), None, None, СЕГОДНЯ)["queue"]
    assert {x["deal"]: x["chat"] for x in q if x["code"] == "ballKam"}["21"] == "чат не проверен"
