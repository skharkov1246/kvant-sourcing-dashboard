"""Очередь КАМа «что делать сегодня» (kamqueue.build).

Два слоя: ручные минимальные входы — правила по одному; синтетика smoke-сборки
(пресейл + «Реализация» через настоящие presale.compute и contracts.compute) —
сборка целиком. Данные придуманы (правило 18).
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import kamqueue as kq  # noqa: E402
from tests import fixture  # noqa: E402

СЕГОДНЯ = dt.date(2026, 10, 19)


def test_пороги_мяча_у_кама_таблицей_и_предложены():
    assert kq.ball_rule("Экономика готова | Можно подавать")[0] == 3
    assert kq.ball_rule("Техническая часть | Квалификация")[0] == 7
    assert kq.ball_rule("Тендерное предложение выдано | Работаем с клиентом по тендеру")[0] == 7
    assert kq.ball_rule("Переторжка | Торги")[0] == 7
    assert kq.ball_rule("Сорсинг завершен / Ожидаем решение заказчика")[0] == 14
    assert kq.ball_rule("Стадия, которой нет в таблице") == kq.BALL_KAM_DEFAULT
    assert kq.BALL_KAM_DEFAULT[0] == 7 and kq.PROPOSED is True
    r = kq.build(None, None, None, СЕГОДНЯ)["rules"]
    assert r["proposed"] is True and r["ballKam"][-1]["label"] == "прочие стадии"


def _deal(did, *, kam="10", closed=False, sale=50_000, orders=()):
    return {"deal": did, "seq": int(did) % 1000, "customer": f"Заказчик {did}", "title": f"Сделка {did}",
            "kam": "Анна (КАМ)" if kam else "", "kamId": kam, "closed": closed, "lost": False,
            "saleEur": sale, "saleLbl": f"€{sale // 1000}K", "stageDays": 12.4, "stageName": "Производство",
            "orders": list(orders)}


def _q(code, deal, days, *, kam="10", who="Глеб (ОСС)", dl=""):
    return {"code": code, "deal": deal, "days": days, "kamId": kam, "kam": "Анна (КАМ)", "who": who,
            "seq": int(deal) % 1000, "customer": f"Заказчик {deal}", "title": f"{deal}/1", "dl": dl}


def test_пункты_реализации_по_одному_на_сделку_и_вид():
    life = {"queue": [_q("late", "6001", 5), _q("late", "6001", 12), _q("unpaid", "6001", 40),
                      _q("shipSlip", "6001", 3),                    # не КАМу — ведёт ОСС
                      _q("late", "6002", 9, kam="")]}               # без КАМа — придёт пунктом noKam
    rows = [_deal("6001"), _deal("6002", kam="")]
    Q = kq.build(None, life, rows, СЕГОДНЯ)
    by = {(x["deal"], x["code"]): x for x in Q["queue"]}
    assert set(by) == {("6001", "late"), ("6001", "unpaid"), ("6002", "noKam")}
    late = by[("6001", "late")]
    assert late["orders"] == 2 and late["days"] == 12, "дней — наибольшее по заказам"
    assert late["act"] == "письменно согласовать новый срок с клиентом и внести его"
    assert late["money"] == 50_000 and late["title"] == "Сделка 6001", "пункт про сделку: её сумма и название"
    assert by[("6001", "unpaid")]["act"] == "получить у клиента дату оплаты — ведёт ответственный заказа"
    assert by[("6001", "unpaid")]["owner"] == "Глеб (ОСС)"
    нет = by[("6002", "noKam")]
    assert нет["kam"] == kq.HEAD and нет["act"] == "назначить КАМа" and нет["days"] == 12
    assert [x["code"] for x in Q["queue"]] == ["late", "unpaid", "noKam"], "сначала сорванный срок"


@pytest.mark.parametrize("дней, корзина", [(1, "unpaid30"), (30, "unpaid30"), (31, "unpaid90"),
                                          (90, "unpaid90"), (91, "unpaid90p"), (400, "unpaid90p")])
def test_неоплата_по_давности(дней, корзина):
    Q = kq.build(None, {"queue": [_q("unpaid", "6001", дней)]}, [_deal("6001")], СЕГОДНЯ)
    row = next(r for r in Q["byKam"] if r["id"] == "10")
    assert row[корзина] == 1 and row["unpaid"] == 1
    assert not any(k in row for k in ("unpaid30", "unpaid90", "unpaid90p") if k != корзина), "нулевые не пишутся"


def test_сроки_клиента_в_ближайшие_14_дней():
    def o(oid, dl_days, *, live=True, delivered=""):
        return {"id": oid, "title": f"{oid}. Узел", "sup": "Поставщик", "live": live, "delivered": delivered,
                "dl": (СЕГОДНЯ + dt.timedelta(days=dl_days)).strftime("%d.%m.%Y")}
    rows = [_deal("6001", orders=[o("1", 0), o("2", 14), o("3", 15), o("4", -1), o("5", 3, live=False),
                                  o("6", 5, delivered="01.10.2026")])]
    U = kq.build(None, None, rows, СЕГОДНЯ)["upcoming14"]
    assert [(u["order"], u["left"]) for u in U] == [("1", 0), ("2", 14)]
    assert kq.build(None, None, rows, СЕГОДНЯ)["head"]["soon"] == 2


def test_подпись_кама_из_справочника_людей():
    life = {"queue": [_q("noUtd", "6001", 20)]}
    Q = kq.build(None, life, [_deal("6001")], СЕГОДНЯ, people={"10": {"name": "Астахова Анна", "active": False}})
    x = Q["queue"][0]
    assert x["kam"] == "Астахова Анна" and x["kamGone"] is True and x["act"] == "получить подписанный УПД"


# ------------------------------------------------------------- синтетика целиком
@pytest.fixture(scope="module")
def Q():
    return fixture.build_kam_queue()


def test_мяч_у_кама_по_порогу_и_золотому_правилу(Q):
    ball = {x["deal"]: x for x in Q["queue"] if x["code"] == "ballKam"}
    assert set(ball) == {"8001", "8003"}, "8002 и 8005 — в пороге, 8004 — в чате идёт работа"
    assert ball["8001"]["thr"] == 3 and ball["8001"]["act"].startswith("подать ТКП")
    assert ball["8003"]["chat"] == "в чате тихо 30 дн."
    assert Q["head"]["chatActive"] == 1


def test_без_кама_реализация_и_пресейл(Q):
    нет = {x["deal"]: x for x in Q["queue"] if x["code"] == "noKam"}
    assert set(нет) == {"8006", "8008", "6023"}, "ответственный сделки (8008) КАМом не считается"
    assert {x["kam"] for x in нет.values()} == {kq.HEAD} and {x["kamId"] for x in нет.values()} == {""}
    assert нет["8006"]["src"] == "presale" and нет["6023"]["src"] == "real"


def test_сводка_сходится(Q):
    c = Counter(x["code"] for x in Q["queue"])
    assert Q["counts"] == dict(c) and sum(Q["counts"].values()) == len(Q["queue"]) == Q["head"]["n"]
    h = Q["head"]
    assert (h["ballKam"], h["unpaid"], h["noKam"], h["soon"]) == (
        c["ballKam"], c["unpaid"], c["noKam"], len(Q["upcoming14"]))
    assert sum(r.get("n", 0) for r in Q["byKam"]) == len(Q["queue"])
    for r in Q["byKam"]:
        assert r.get("unpaid", 0) == sum(r.get(k, 0) for k in ("unpaid30", "unpaid90", "unpaid90p"))
        assert all(v for k, v in r.items() if isinstance(v, (int, float)) and not isinstance(v, bool)), \
            "нулевые поля в строке КАМа не пишутся"
    assert {x["code"] for x in Q["queue"]} <= set(Q["labels"])
    keys = [(x["deal"], x["code"]) for x in Q["queue"]]
    assert len(keys) == len(set(keys)), "один пункт на сделку и вид"


def test_порядок_важности(Q):
    sev = [x["sev"] for x in Q["queue"]]
    assert sev == sorted(sev) and Q["queue"][0]["code"] == "late" and Q["queue"][-1]["code"] == "noKam"


def test_очередь_едет_во_вкладку_людей():
    P = fixture.build_people()
    assert P["kamQueue"]["queue"], "smoke-сборка рисует раздел «КАМы» с данными"
    assert "kamQueue" not in fixture.build_people(kam_queue=False)
