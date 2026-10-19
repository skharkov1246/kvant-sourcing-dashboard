"""Активность в чате сделки: «подвисла» только при тишине людей. Корпус придуман (правило 18)."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_activity as ca  # noqa: E402

СЕГОДНЯ = dt.date(2026, 10, 19)


class Чаты:
    def __init__(self, ответы):
        self.ответы, self.вызовы = ответы, 0

    def call(self, method, params, **kw):
        assert method == "im.dialog.messages.get"
        self.вызовы += 1
        r = self.ответы[params["DIALOG_ID"]]
        if isinstance(r, Exception):
            raise r
        return r


def test_человек_служебная_запись_и_система():
    msgs = [{"author_id": 0, "date": "2026-10-18T10:00:00+03:00"},
            {"author_id": "542", "date": "2026-10-18"},
            {"author_id": 77, "date": "2026-10-01T09:00:00+03:00"}]
    assert ca.last_human(msgs, {"542"}) == dt.date(2026, 10, 1)
    assert ca.classify(dt.date(2026, 10, 12), СЕГОДНЯ, True) == "active"
    assert ca.classify(dt.date(2026, 10, 11), СЕГОДНЯ, True) == "quiet"
    assert ca.classify(None, СЕГОДНЯ, True) == "quiet"
    assert ca.classify(None, СЕГОДНЯ, False) == "unread"
    assert ca.chat_id({ca.CHAT_FIELD: "123"}) == "123" and ca.chat_id({ca.CHAT_FIELD: "0"}) == ""


def test_проверка_бюджет_и_нечитаемый_ответ():
    к = Чаты({"chat1": {"messages": [{"author_id": 5, "date": "2026-10-18"}]},
              "chat2": {"messages": []},
              "chat3": RuntimeError("ACCESS_DENIED"),
              "chat4": None})
    res = ca.check(к, [("d1", "1", 100), ("d2", "2", 90), ("d3", "3", 80), ("d4", "4", 70),
                       ("d5", "", 60), ("d6", "6", 1)], СЕГОДНЯ, set(), budget=4)
    assert res["d1"]["status"] == "active" and res["d1"]["days"] == 1
    assert res["d2"]["status"] == "quiet", "прочитан, людей нет — тихо"
    assert res["d3"]["status"] == "unread" and res["d4"]["status"] == "unread", "ошибка и пустой ответ — не «тишина»"
    assert res["d5"]["status"] == "unread", "нет id чата — не проверен"
    assert res["d6"]["status"] == "skipped" and к.вызовы == 4
    assert ca.summary(res) == {"active": 1, "quiet": 1, "unread": 3, "skipped": 1, "denied": 1, "checked": 2}


class BitrixLimitError(RuntimeError):
    """Как bitrix_client.BitrixLimitError — шлюз узнаёт её по имени класса."""


def test_лимит_портала_останавливает_чтение_и_нет_доступа_отдельно():
    к = Чаты({"chat1": BitrixLimitError("QUERY_LIMIT_EXCEEDED"), "chat2": {"messages": []},
              "chat3": RuntimeError("im.dialog.messages.get: ACCESS_ERROR нет доступа")})
    к.limit_wait_budget = 1800.0
    res = ca.check(к, [("d1", "1", 100), ("d2", "2", 90)], СЕГОДНЯ, set())
    assert res["d1"]["status"] == "unread" and res["d2"]["status"] == "skipped" and к.вызовы == 1
    assert к.limit_wait_budget == 1800.0, "бюджет ожидания клиента восстановлен"
    res = ca.check(к, [("d3", "3", 1)], СЕГОДНЯ, set())
    assert res["d3"] == {"status": "unread", "last": "", "days": None, "why": "denied"}
    assert ca.summary(res)["denied"] == 1
    # порядок сообщений не задан: берём самое позднее сообщение человека
    msgs = [{"author_id": 3, "date": "2026-10-01"}, {"author_id": 3, "date": "2026-10-15"}, {"author_id": 3, "date": "2026-09-01"}]
    assert ca.last_human(msgs, set()) == dt.date(2026, 10, 15)


def test_один_читатель_на_сборку_общий_бюджет_и_память():
    """main.py заводит одного читателя: реализация читает первой, пресейл — остатком.
    Бюджет — запросы к порталу; сделка без id чата его не тратит; прочитанная сделка
    второй раз портал не спрашивает."""
    к = Чаты({"chat1": {"messages": [{"author_id": 5, "date": "2026-10-18"}]},
              "chat2": {"messages": []}, "chat3": {"messages": []}})
    r = ca.Reader(к, set(), budget=2)
    a = r.check([("d1", "1", 10), ("d0", "", 99)], СЕГОДНЯ)
    assert a["d0"]["why"] == "nochat" and r.reads == 1 and r.left == 1
    b = r.check([("d1", "1", 10), ("d2", "2", 5), ("d3", "3", 1)], СЕГОДНЯ)
    assert b["d1"]["status"] == "active" and к.вызовы == 2, "d1 из памяти, d2 — последним запросом бюджета"
    assert b["d2"]["status"] == "quiet" and b["d3"]["status"] == "skipped"
    assert r.stats() == {"reads": 2, "budget": 2, "left": 0, "stopped": False}
