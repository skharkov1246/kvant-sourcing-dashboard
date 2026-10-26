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


def test_пороги_мяча_у_кама_таблицей_и_утверждены():
    assert kq.ball_rule("Экономика готова | Можно подавать")[0] == 3
    assert kq.ball_rule("Техническая часть | Квалификация")[0] == 7
    assert kq.ball_rule("Тендерное предложение выдано | Работаем с клиентом по тендеру")[0] == 7
    assert kq.ball_rule("Переторжка | Торги")[0] == 7
    assert kq.ball_rule("Сорсинг завершен / Ожидаем решение заказчика")[0] == 14
    assert kq.ball_rule("Стадия, которой нет в таблице") == kq.BALL_KAM_DEFAULT
    assert kq.BALL_KAM_DEFAULT[0] == 7 and kq.PROPOSED is False
    r = kq.build(None, None, None, СЕГОДНЯ)["rules"]
    assert r["proposed"] is False and r["ballKam"][-1]["label"] == "прочие стадии"


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
                      _q("noPlanDates", "6001", 3),                 # не КАМу — ведёт ОСС
                      _q("late", "6002", 9, kam="")]}               # без КАМа — сигнал в пункте noKam
    rows = [_deal("6001"), _deal("6002", kam="")]
    Q = kq.build(None, life, rows, СЕГОДНЯ)
    by = {(x["deal"], x["code"]): x for x in Q["queue"]}
    assert set(by) == {("6001", "late"), ("6001", "unpaid"), ("6002", "noKam")}
    late = by[("6001", "late")]
    assert late["orders"] == 2 and late["days"] == 12, "дней — наибольшее по заказам"
    assert late["act"] == "письменно согласовать новый срок с клиентом и внести его"
    assert late["money"] == 50_000 and late["title"] == "Сделка 6001", "пункт про сделку: её сумма и название"
    # долг клиента урегулирует КАМ (решение владельца 26.10.2026, п. 4)
    assert by[("6001", "unpaid")]["act"] == "урегулировать долг: получить у клиента оплату или дату оплаты"
    assert by[("6001", "unpaid")]["owner"] == "Глеб (ОСС)"
    нет = by[("6002", "noKam")]
    assert нет["kam"] == kq.HEAD and нет["act"] == "назначить КАМа" and нет["stageDays"] == 12
    assert [x["code"] for x in Q["queue"]] == ["late", "noKam", "unpaid"], \
        "сначала сорванный срок — и у сделки без КАМа тоже"


def test_без_кама_сорванный_срок_не_тонет_внизу():
    """Открытая сделка без КАМа с сорванным сроком: пункт noKam с важностью «срок сорван»,
    дни — сигнала, сигнал — строкой пункта; вниз очереди он не уходит."""
    life = {"queue": [_q("late", "6002", 9, kam=""), _q("late", "6002", 4, kam=""),
                      _q("unpaid", "6002", 40, kam=""), _q("noUtd", "6001", 30)]}
    Q = kq.build(None, life, [_deal("6001"), _deal("6002", kam="")], СЕГОДНЯ)
    нет = next(x for x in Q["queue"] if x["code"] == "noKam")
    assert нет["deal"] == "6002" and нет["sev"] == kq.CODES["late"][0] and нет["days"] == 9
    assert [(a["code"], a["days"]) for a in нет["also"]] == [("late", 9), ("unpaid", 40)], "по важности"
    assert нет["also"][0]["orders"] == 2 and "orders" not in нет["also"][1], "заказы — счётом, один не пишется"
    assert нет["owner"] == "Глеб (ОСС)" and нет["kam"] == kq.HEAD and нет["kamId"] == ""
    assert Q["queue"][0] is нет, "сорванный срок без КАМа — наверху, раньше УПД с КАМом"
    assert sum(1 for x in Q["queue"] if x["deal"] == "6002") == 1, "один пункт на сделку без КАМа"


def test_закрытая_сделка_без_кама_без_упд_не_пропадает():
    """lifecycle держит noUtd и у закрытых сделок; без КАМа она — пункт noKam, а не ничто."""
    life = {"queue": [_q("noUtd", "6003", 20, kam="")]}
    Q = kq.build(None, life, [_deal("6003", kam="", closed=True)], СЕГОДНЯ)
    assert [(x["code"], x["deal"]) for x in Q["queue"]] == [("noKam", "6003")]
    нет = Q["queue"][0]
    assert нет["sev"] == kq.CODES["noUtd"][0] and нет["days"] == 20
    assert [a["code"] for a in нет["also"]] == ["noUtd"] and нет["seq"] == 3 and нет["title"] == "Сделка 6003"
    # сделки нет в строках «Реализации» — подписи из самого пункта
    Q2 = kq.build(None, life, [], СЕГОДНЯ)
    assert Q2["queue"][0]["customer"] == "Заказчик 6003" and Q2["queue"][0]["title"] == "6003/1"
    assert Q2["head"]["noKam"] == 1 and Q2["head"]["n"] == 1


def _ps_open(did, stage, days, *, kam="10", chat=None, ball="kam"):
    return {"id": did, "t": f"Узел {did}", "stage": "C32:X", "stageName": stage, "days": days, "ball": ball,
            "who": "", "kam": "Анна (КАМ)" if kam else "КАМ не указан", "kamId": kam, "kamGone": False,
            "flags": [], "chat": chat, "created": ""}


def test_мяч_у_кама_без_прочитанного_чата_помечен():
    """Чат не читался (нет читателя или сделка не попала в чтение) — пункт с пометкой."""
    ps = {"openDeals": [_ps_open("8001", "Экономика готова | Можно подавать", 5)]}
    x = kq.build(ps, None, None, СЕГОДНЯ)["queue"][0]
    assert x["code"] == "ballKam" and x["chat"] == "чат не проверен"
    ps = {"openDeals": [_ps_open("8001", "Экономика готова | Можно подавать", 5,
                                 chat={"status": "skipped", "days": None, "last": ""})]}
    assert kq.build(ps, None, None, СЕГОДНЯ)["queue"][0]["chat"] == "чат не проверен"


def test_без_кама_мяч_у_кама_сверх_порога_сигналом_и_по_золотому_правилу():
    тихо = {"status": "quiet", "days": 20, "last": ""}
    живо = {"status": "active", "days": 1, "last": ""}
    ps = {"openDeals": [_ps_open("8101", "Экономика готова | Можно подавать", 6, kam="", chat=тихо),
                        _ps_open("8102", "Экономика готова | Можно подавать", 6, kam="", chat=живо),
                        _ps_open("8103", "Экономика готова | Можно подавать", 2, kam=""),
                        _ps_open("8104", "Поиск поставщиков", 30, kam="", ball="src")]}
    Q = kq.build(ps, None, None, СЕГОДНЯ)
    by = {x["deal"]: x for x in Q["queue"]}
    assert {x["code"] for x in Q["queue"]} == {"noKam"} and set(by) == {"8101", "8102", "8103", "8104"}
    assert by["8101"]["sev"] == kq.CODES["ballKam"][0] and by["8101"]["also"][0]["thr"] == 3
    assert by["8101"]["chat"] == "в чате тихо 20 дн."
    assert "also" not in by["8102"] and by["8102"]["sev"] == kq.CODES["noKam"][0], "в чате работа — сигнала нет"
    assert "also" not in by["8103"] and "also" not in by["8104"], "в пороге / мяч не у КАМа"
    assert by["8104"]["chat"] == "", "мяч не у КАМа — чат пункта не решает"
    assert Q["head"]["chatActive"] == 1
    assert Q["queue"][0]["deal"] == "8101"


def test_правило_чтения_чата_для_очереди():
    assert kq.chat_wanted(_ps_open("1", "Экономика готова | Можно подавать", 4))
    assert not kq.chat_wanted(_ps_open("1", "Экономика готова | Можно подавать", 3)), "порог — включительно"
    assert not kq.chat_wanted(_ps_open("1", "Сорсинг завершен / Ожидаем решение заказчика", 14))
    assert kq.chat_wanted(_ps_open("1", "Сорсинг завершен / Ожидаем решение заказчика", 15))
    assert not kq.chat_wanted(_ps_open("1", "Поиск поставщиков", 40, ball="src"))
    assert not kq.chat_wanted(_ps_open("1", "Экономика готова | Можно подавать", None))


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
    # 5 дн. в «Экономика готова» — меньше простоя пресейла (14), но чат прочитан для очереди
    assert ball["8001"]["chat"] == "в чате людей не было"
    assert Q["head"]["chatActive"] == 1


def test_без_кама_реализация_и_пресейл(Q):
    нет = {x["deal"]: x for x in Q["queue"] if x["code"] == "noKam"}
    assert set(нет) == {"8006", "8008", "6023"}, "ответственный сделки (8008) КАМом не считается"
    assert {x["kam"] for x in нет.values()} == {kq.HEAD} and {x["kamId"] for x in нет.values()} == {""}
    assert нет["8006"]["src"] == "presale" and нет["6023"]["src"] == "real"
    # у 6023 есть пункты для КАМа — без КАМа они сигналами пункта noKam, а не потеряны
    assert {a["code"] for a in нет["6023"].get("also", [])} == {"unpaid", "noUtd"}
    assert нет["6023"]["sev"] == kq.CODES["unpaid"][0]


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


def test_кам_хозяин_или_в_копии_по_регламенту():
    """Решение владельца 26.10.2026, п. 1: КАМ получает пункты, где он хозяин (долг клиента)
    или в копии (эскалации: срок клиента, УПД на 11-й р.д., приёмка); пункт менеджера ОРК без
    КАМа в копии (договор с поставщиком) — не его."""
    def x(code, role, copy):
        return {**_q(code, "6001", 5), "role": role, "copyRole": copy, "step": 4}
    life = {"queue": [x("late", "orkMgr", "kam"), x("s2Over", "orkMgr", "orkHead"), x("unpaid", "kam", "oss"),
                      x("s1Over", "orkHead", "kam"), x("noDl", "orkHead", "kam"), x("noPlanDates", "oss", "")]}
    Q = kq.build(None, life, [_deal("6001")], СЕГОДНЯ)
    by = {q["code"]: q for q in Q["queue"]}
    assert set(by) == {"late", "unpaid", "s1Over", "noDl"}
    assert by["late"]["kamCopy"] is True and by["unpaid"]["kamCopy"] is False
    assert by["s1Over"]["act"] == "ответить руководителю ОРК по приёмке сделки" and by["late"]["step"] == 4
    assert Q["rules"]["utdWd"] == 10
    # хозяин — по регламенту, а не «ответственный заказа»: роль хозяина и «вы — хозяин»
    assert by["unpaid"]["kamOwner"] is True and by["late"]["kamOwner"] is False


def test_кам_видит_роль_хозяина_и_рабочие_дни():
    """Решение 26.10.2026, пп. 1–2: у пункта реализации — подпись роли хозяина по регламенту и
    единица дней (р.д. у сроков регламента, иначе календарные)."""
    q = {**_q("noUtd", "6001", 14), "role": "orkMgr", "roleName": "менеджер ОРК", "copyRole": "kam",
         "who": "Илья (ОРК)", "inWd": True, "step": 12}
    q2 = {**_q("late", "6001", 9), "role": "orkMgr", "roleName": "менеджер ОРК", "copyRole": "kam",
          "who": "Илья (ОРК)", "step": 7}
    Q = kq.build(None, {"queue": [q, q2]}, [_deal("6001")], СЕГОДНЯ)
    by = {x["code"]: x for x in Q["queue"]}
    assert (by["noUtd"]["owner"], by["noUtd"]["ownerRole"], by["noUtd"]["inWd"], by["noUtd"]["kamCopy"]) == (
        "Илья (ОРК)", "менеджер ОРК", True, True)
    assert by["late"]["inWd"] is False
