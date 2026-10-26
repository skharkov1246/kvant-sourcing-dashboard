"""Вкладка «Реализация» (contracts.compute) на придуманном портале.

Формы ответов повторяют Bitrix (crm.deal.list, crm.item.list СП-172,
crm.stagehistory.list), поэтому считает настоящий contracts.compute. Проверено
то, что ломалось: уволенный ответственный был заглушкой «user#N», возраст
стадии при возврате в неё считался от первого визита, стадии заказов других
воронок показывались кодами.
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

СЕЙЧАС = "2026-10-09T12:00:00+03:00"
DL = contracts_mod.DL_CUSTOMER
OSS = contracts_mod.OSS_F


def _t(days_ago: float) -> str:
    return (dt.datetime.fromisoformat(СЕЙЧАС) - dt.timedelta(days=days_ago)).isoformat()


class Портал:
    """Ровно те методы клиента, которые зовёт contracts.compute."""

    def __init__(self):
        self.users = {"10": "Вера Астахова (KAM)", "11": "Пётр Вьюгин (OSS)"}
        # чат сделки 1: человек писал вчера; чат сделки 2 — только служебная запись #542
        self.chats = {"chat501": [{"author_id": 11, "date": _t(1)}],
                      "chat502": [{"author_id": 542, "date": _t(1)}, {"author_id": 0, "date": _t(0)}]}
        self.deals = [
            # сделка ведёт ушедший ответственный №20; вернулась в «Закупку» после «Поставки»
            {"ID": "1", "TITLE": "901. Насос", "OPPORTUNITY": "100000", "CURRENCY_ID": "EUR",
             "COMPANY_ID": "", "STAGE_ID": "C0:PURCHASE", "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0",
             "DATE_CREATE": _t(200), "ASSIGNED_BY_ID": "20", "UF_CRM_1734380921": "501"},
            {"ID": "2", "TITLE": "902. Компрессор", "OPPORTUNITY": "50000", "CURRENCY_ID": "EUR",
             "COMPANY_ID": "", "STAGE_ID": "C0:PURCHASE", "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0",
             "DATE_CREATE": _t(100), "ASSIGNED_BY_ID": "10", "UF_CRM_1734380921": "502"},
            # оплата получена, сделка не закрыта — по решению владельца это успех
            {"ID": "3", "TITLE": "903. Клапан", "OPPORTUNITY": "20000", "CURRENCY_ID": "EUR",
             "COMPANY_ID": "", "STAGE_ID": "C0:PAID", "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0",
             "DATE_CREATE": _t(300), "ASSIGNED_BY_ID": "10"},
        ]
        self.orders = [
            # заказ в воронке 30: стадия должна получить имя, а не остаться кодом
            {"id": 501, "title": "901/1. Насос", "companyId": "", "parentId2": 1, "opportunity": 60000,
             "currencyId": "EUR", "stageId": "DT172_30:PREPARATION", "createdTime": _t(150),
             "movedTime": _t(3), DL: _t(40)[:10], OSS: "21"},
            {"id": 502, "title": "902/1. Компрессор", "companyId": "", "parentId2": 2, "opportunity": 30000,
             "currencyId": "EUR", "stageId": "DT172_26:PREPARATION", "createdTime": _t(90),
             "movedTime": _t(10), DL: _t(-30)[:10], OSS: "11"},
            # своя стадия провала (UC_*, семантика F): заказ не живой и не в расчёте
            {"id": 503, "title": "902/2. Компрессор", "companyId": "", "parentId2": 2, "opportunity": 5000,
             "currencyId": "EUR", "stageId": "DT172_26:UC_LOST", "createdTime": _t(80),
             "movedTime": _t(20), DL: _t(30)[:10], OSS: "11"},
            # доставлен клиенту 25 дней назад, заказ не закрыт: это закрытие, а не просрочка
            {"id": 504, "title": "902/3. Компрессор", "companyId": "", "parentId2": 2, "opportunity": 5000,
             "currencyId": "EUR", "stageId": "DT172_26:PREPARATION", "createdTime": _t(80),
             "movedTime": _t(20), DL: _t(20)[:10], OSS: "11", contracts_mod.DEL_FACT: _t(25)[:10]},
        ]
        # история сделки 1: Закупка (120 дн назад) → Поставка (60) → снова Закупка (5)
        self.hist = {
            2: [("1", "C0:PURCHASE", _t(120)), ("1", "C0:DELIVERY", _t(60)), ("1", "C0:PURCHASE", _t(5)),
                ("2", "C0:PURCHASE", _t(50))],
            # 502 — 20 дн. на шаге 5 (норма 9 р.д.): сверх нормы шага — кандидат в «Замедлено»
            172: [("501", "DT172_30:NEW", _t(150)), ("501", "DT172_30:PREPARATION", _t(3)),
                  ("502", "DT172_26:PREPARATION", _t(20))],
        }

    def call(self, method, params=None, **kw):
        if method == "im.dialog.messages.get":
            return {"messages": self.chats.get(params["DIALOG_ID"], [])}
        if method == "crm.item.fields":
            return {"fields": {contracts_mod.SCHEME_F: {"items": [{"ID": "7", "VALUE": "Через агента"}]}}}
        assert method == "crm.currency.list"
        return [{"CURRENCY": "EUR", "AMOUNT": "1", "AMOUNT_CNT": "1", "BASE": "Y", "DATE_UPDATE": "2026-10-01"},
                {"CURRENCY": "RUB", "AMOUNT": "0.01", "AMOUNT_CNT": "1", "DATE_UPDATE": "2026-10-01"},
                {"CURRENCY": "USD", "AMOUNT": "0.9", "AMOUNT_CNT": "1", "DATE_UPDATE": "2026-10-01"}]

    def list_items(self, entity, *, filter=None, select=None, max_items=None):
        assert entity == 172
        return [dict(o) for o in self.orders]

    def companies_by_ids(self, ids):
        return {}

    def list_deals_fast(self, *, filter=None, select=None, **kw):
        # как живой Битрикс: только выбранные поля — поле, забытое в select, в расчёт не придёт
        return [{k: d[k] for k in select if k in d} if select else dict(d) for d in self.deals]

    def deals_by_ids(self, ids, *, select=None):
        return {}

    def deal_stages_cat(self, cid=0):
        return {"C0:PURCHASE": "Закупка", "C0:DELIVERY": "Поставка",
                "C0:PAID": "Оплата получена | Закрытие сделки", "C0:WON": "Успех"}

    def deal_stages_process(self, cid=0):
        return {"C0:PURCHASE": "Закупка", "C0:DELIVERY": "Поставка", "C0:PAID": "Оплата получена | Закрытие сделки"}

    def spa_stage_meta(self, entity, cid):
        if cid == 26:                       # воронка заказов — имена портала (шаги ork.STAGE_STEP)
            return {"DT172_26:NEW": {"name": "Заказ создан", "sem": "P"},
                    "DT172_26:PREPARATION": {"name": "Согласование спецификации и оплаты", "sem": "P"},
                    "DT172_26:UC_LOST": {"name": "Отменён (26)", "sem": "F"}}
        return {f"DT172_{cid}:NEW": {"name": f"Новый ({cid})", "sem": "P"},
                f"DT172_{cid}:PREPARATION": {"name": f"Подготовка ({cid})", "sem": "P"},
                f"DT172_{cid}:UC_LOST": {"name": f"Отменён ({cid})", "sem": "F"}}

    def stage_history(self, entity, *, category_id=None, since=None, last=None, transitions=None):
        out = {}
        for oid, st, t in self.hist[entity]:
            if last is not None:
                last[oid] = (st, t)
            if transitions is not None:
                transitions.setdefault(oid, []).append((st, t))
            if st not in {s for s, _ in out.get(oid, [])}:
                out.setdefault(oid, []).append((st, t))
        return out

    def user_name(self, uid):
        return self.users.get(str(uid), f"user#{uid}")


@pytest.fixture(scope="module")
def ct():
    import os
    os.environ["KVANT_NOW"] = СЕЙЧАС
    try:
        люди = {"10": {"name": "Астахова Вера", "active": True},
                "20": {"name": "Донцов Юрий", "active": False},
                "21": {"name": "Ежова Лада", "active": False}}
        return contracts_mod.compute(Портал(), as_of=dt.date(2026, 10, 9), people=люди, service_ids={"542"})
    finally:
        os.environ.pop("KVANT_NOW", None)


def _row(ct, deal):
    return next(r for r in ct["rows"] if r["deal"] == deal)


def test_уволенный_ответственный_и_осс_подписаны_а_не_заглушкой(ct):
    r = _row(ct, "1")
    assert r["manager"] == "Донцов Юрий (уволен)" and r["managerGone"] is True
    assert r["oss"] == "Ежова Лада (уволен)" and r["ossGone"] is True
    assert "user#" not in r["manager"] + r["oss"]
    r2 = _row(ct, "2")
    assert r2["managerGone"] is False and r2["ossGone"] is False


def test_работа_уволенных_собрана_на_переназначение(ct):
    g = ct["gone"]
    assert (g["deals"], g["byManager"], g["byOss"], g["orders"], g["late"], g["people"]) == (1, 1, 1, 1, 1, 2)
    assert g["saleNum"] == 100000


def test_возраст_стадии_от_возврата_а_не_от_первого_визита(ct):
    """Сделка 1 была в «Закупке» 120 дней назад, ушла в «Поставку» и вернулась
    5 дней назад: в стадии она 5 дней, а не 120."""
    assert _row(ct, "1")["stageDays"] == pytest.approx(5, abs=0.1)
    # заказ 501 вошёл в текущую стадию 3 дня назад
    assert _row(ct, "1")["orders"][0]["days"] == pytest.approx(3, abs=0.1)


def test_стадии_заказов_другой_воронки_получают_имя(ct):
    assert _row(ct, "1")["orders"][0]["stageName"] == "Подготовка (30)"


def test_разрез_по_людям_помечает_уволенного_и_называет_сделку(ct):
    by = {m["name"]: m for m in ct["deadlines"]["byOss"]}
    m = by["Ежова Лада (уволен)"]
    assert m["gone"] is True and m["lateOrders"] == 1 and m["lateDeals"] == 1
    assert m["top"]["seq"] == 901 and m["top"]["days"] == 40


def test_окно_бенчмарков_задано(ct):
    assert ct["speed"]["benchDays"] == contracts_mod.BENCH_DAYS


def test_провал_по_семантике_и_доставленный_не_просрочен(ct):
    r = _row(ct, "2")
    ids = {o["id"] for o in r["orders"]}
    assert "503" not in ids, "своя стадия провала UC_* с семантикой F выпадает, как и :FAIL"
    o504 = next(o for o in r["orders"] if o["id"] == "504")
    assert o504["late"] is False and o504["delivered"] and r["late"] is False


def test_жизненный_цикл_на_вкладке(ct):
    life = ct["life"]
    assert {p["key"] for p in life["phases"]} == {"place", "make", "move", "close", "warranty"}
    assert life["counts"].get("noUtd") == 1 and life["counts"].get("late") == 1
    q = next(x for x in life["queue"] if x["code"] == "noUtd")
    assert q["order"] == "504" and q["seq"] == 902



@pytest.mark.parametrize("сообщения, подвисла", [
    ([{"author_id": 11, "date": _t(1)}], False),                                          # человек писал вчера
    ([{"author_id": 542, "date": _t(1)}, {"author_id": 0, "date": _t(0)}], True),         # служебная и система
    ([{"author_id": 11, "date": _t(30)}], True),                                          # человек — месяц назад
])
def test_подвисла_только_если_в_чате_тихо(сообщения, подвисла):
    """Решение владельца 19.10.2026: простой по стадии снимает живой чат сделки.
    Сделка 2: заказ 502 — 20 дн. на шаге 5 при норме 9 р.д. (решение 26.10.2026, п. 2),
    срок клиента не сорван."""
    import os
    os.environ["KVANT_NOW"] = СЕЙЧАС
    try:
        п = Портал()
        п.chats = {"chat502": сообщения}
        ct = contracts_mod.compute(п, as_of=dt.date(2026, 10, 9), people={}, service_ids={"542"})
    finally:
        os.environ.pop("KVANT_NOW", None)
    r = _row(ct, "2")
    assert r["chat"] is not None and r["late"] is False
    assert r["stuck"] is подвисла and r["slow"] is подвисла
    тихо = "в чате тихо" in r["why"] or "в чате людей не было" in r["why"]
    assert тихо is подвисла and ("в чате работа" in r["why"]) is (not подвисла)
    assert ct["head"]["chat"]["checked"] == 1


def test_сумма_прячет_потерю_замедлено_хуже_и_лучше_счётом(monkeypatch):
    """«Замедлено» по норме шага (решение 26.10.2026, п. 2) против прежних 2× медианы — у
    скольких сделок картина стала хуже и у скольких лучше, счётом (CLAUDE.md, «сумма прячет
    потерю»). Сделка 2: заказ 502 восемь дней на шаге 5 — 6 р.д. при норме 9, прежде (порог
    7 дн.) — замедлена: лучше. Сделка 4: заказ 505 три дня на шаге 1 — 3 р.д. при норме 1,
    прежде (меньше недели) — нет: хуже."""
    monkeypatch.setenv("KVANT_NOW", СЕЙЧАС)
    п = Портал()
    п.orders = [o for o in п.orders if o["id"] != 504]          # 504 держал бы сделку 2 «по-старому»
    п.deals.append({"ID": "4", "TITLE": "904. Редуктор", "OPPORTUNITY": "10000", "CURRENCY_ID": "EUR",
                    "COMPANY_ID": "", "STAGE_ID": "C0:PURCHASE", "STAGE_SEMANTIC_ID": "P",
                    "CATEGORY_ID": "0", "DATE_CREATE": _t(4), "ASSIGNED_BY_ID": "10"})
    п.orders.append({"id": 505, "title": "904/1. Редуктор", "companyId": "", "parentId2": 4,
                     "opportunity": 6000, "currencyId": "EUR", "stageId": "DT172_26:NEW",
                     "createdTime": _t(3), "movedTime": _t(3), DL: _t(-60)[:10], OSS: "11"})
    п.hist = {2: [("1", "C0:PURCHASE", _t(120)), ("1", "C0:DELIVERY", _t(60)), ("1", "C0:PURCHASE", _t(5)),
                  ("2", "C0:PURCHASE", _t(5)), ("4", "C0:PURCHASE", _t(3))],
              172: [("501", "DT172_30:NEW", _t(150)), ("501", "DT172_30:PREPARATION", _t(3)),
                    ("502", "DT172_26:PREPARATION", _t(8)), ("505", "DT172_26:NEW", _t(3))]}
    ct = contracts_mod.compute(п, as_of=dt.date(2026, 10, 9), people={}, service_ids={"542"})
    c = ct["ork"]["slowCmp"]
    assert c == {"old": 1, "new": 1, "worse": 1, "better": 1}, c
    # кандидат — до чата; «подвисла» по-прежнему решает чат (у сделки 4 чата нет — не проверено)
    assert "сверх нормы шага" in _row(ct, "4")["why"] and not _row(ct, "4")["stuck"]
    assert "сверх нормы шага" not in _row(ct, "2")["why"] and not _row(ct, "2")["stuck"]


def test_чаты_читает_общий_читатель_сборки():
    """main.py отдаёт реализации общий читатель чатов: её запросы списываются с бюджета
    сборки, пресейлу остаётся остаток (chat_activity.Reader)."""
    import os

    import chat_activity
    os.environ["KVANT_NOW"] = СЕЙЧАС
    try:
        п = Портал()
        читатель = chat_activity.Reader(п, {"542"})
        ct = contracts_mod.compute(п, as_of=dt.date(2026, 10, 9), people={}, chat_reader=читатель)
    finally:
        os.environ.pop("KVANT_NOW", None)
    прочитано = sum(1 for r in ct["rows"] if r["chat"] and r["chat"].get("why") != "nochat")
    assert прочитано >= 1 and читатель.reads == прочитано
    assert all("kamId" in r for r in ct["rows"]), "id КАМа — ключ очереди КАМа"


def test_оплата_получена_это_успех(ct):
    """Решение владельца 19.10.2026, п. 5: стадия «Оплата получена | Закрытие сделки» — успех."""
    r = _row(ct, "3")
    assert r["paidStage"] and r["closed"] and r["done"] and not r["lost"] and not r["slow"]
    assert ct["head"]["paidStage"] == 1 and ct["head"]["live"] == 2


def _посчитать(п, люди=None):
    import os
    os.environ["KVANT_NOW"] = СЕЙЧАС
    try:
        return contracts_mod.compute(п, as_of=dt.date(2026, 10, 9), people=люди or {}, service_ids={"542"})
    finally:
        os.environ.pop("KVANT_NOW", None)


ЛЮДИ = {"10": {"name": "Астахова Вера", "active": True},
        "20": {"name": "Донцов Юрий", "active": False},
        "21": {"name": "Ежова Лада", "active": False},
        "22": {"name": "Жаров Олег", "active": False}}


def test_уволенный_ответственный_заказа_ведёт_уволенный():
    """Ревизия 23.10.2026: «Ведёт уволенный» проверял ответственного сделки и ОСС, но не
    ответственного заказа (Responsible person, assignedById СП-172) — хозяина исполнения.
    Сделка 2: ответственный сделки и ОСС действуют, ответственный живого заказа 502 уволен."""
    п = Портал()
    п.orders[1]["assignedById"] = "22"          # 502 — живой
    ct = _посчитать(п, ЛЮДИ)
    r = _row(ct, "2")
    assert (r["managerGone"], r["ossGone"], r["respGone"]) == (False, False, True)
    o502 = next(o for o in r["orders"] if o["id"] == "502")
    assert o502["respGone"] is True and "resp" not in o502, "в строку — только признак, без подписи"
    g = ct["gone"]
    assert (g["deals"], g["byManager"], g["byOss"], g["byResp"], g["people"]) == (2, 1, 1, 1, 3)
    # «живых заказов — переназначить»: все живые заказы сделки с уволенным ответственным
    # и только заказ с уволенным ответственным в другой сделке — не все её заказы
    ждут = sum(1 for r in ct["rows"] if r["deal"] == "1" for o in r["orders"] if o["live"]) + 1
    assert g["orders"] == ждут, (g["orders"], ждут)
    gone = {q["deal"]: q for q in ct["life"]["queue"] if q["code"] == "gone"}
    assert set(gone) == {"1", "2"} and ct["life"]["counts"]["gone"] == 2
    assert gone["2"]["what"] == "ведёт уволенный: ответственный заказа"
    assert gone["1"]["what"] == "ведёт уволенный: ответственный сделки, ОСС"
    # хозяин пунктов заказа 502 — исполнитель шага (решение 26.10.2026, п. 1): ответственный
    # не из отдела ОРК (отделов нет) — менеджер ОРК «не назначен», даты размещения — ОСС
    q502 = {q["code"]: q for q in ct["life"]["queue"] if q["order"] == "502"}
    assert q502["s3Over"]["role"] == "orkMgr" and q502["s3Over"]["who"] == "не назначен"
    assert q502["noPlanDates"]["who"] == "Пётр Вьюгин (OSS)"
    # ответственный — из отдела ОРК: он и хозяин, уволен — с пометкой
    отделы = [{"ID": "50", "NAME": "Отдел реализации контрактов", "PARENT": "1", "UF_HEAD": "10"}]
    люди = {**ЛЮДИ, "22": {"name": "Жаров Олег", "active": False, "depts": ["50"]}}
    import os
    os.environ["KVANT_NOW"] = СЕЙЧАС
    try:
        ct2 = contracts_mod.compute(п, as_of=dt.date(2026, 10, 9), people=люди, service_ids={"542"},
                                    departments=отделы)
    finally:
        os.environ.pop("KVANT_NOW", None)
    s3 = next(q for q in ct2["life"]["queue"] if q["order"] == "502" and q["code"] == "s3Over")
    # в копии — руководитель ОРК: руководитель отдела ОРК по справочнику отделов (UF_HEAD)
    assert s3["who"] == "Жаров Олег (уволен)" and s3["whoGone"] and s3["escalateTo"] == "Вера Астахова (KAM)"


def test_уволенный_ответственный_закрытого_заказа_не_в_счёт():
    """Признак — только у живого заказа: исполненный (успех) и проигранный заказ никто
    не ведёт, переназначать нечего. Действующий ответственный — не уволенный."""
    п = Портал()
    п.orders[2]["assignedById"] = "22"          # 503 — своя стадия провала
    п.orders[3]["assignedById"] = "22"          # 504 — закрыт успехом
    п.orders[3]["stageId"] = "DT172_26:SUCCESS"
    п.orders[1]["assignedById"] = "10"          # 502 — живой, ответственный действует
    ct = _посчитать(п, ЛЮДИ)
    r = _row(ct, "2")
    assert r["respGone"] is False and not any(o["respGone"] for o in r["orders"] if o["live"])
    assert next(o for o in r["orders"] if o["id"] == "504")["respGone"] is True, "у заказа — как есть, как ossGone"
    assert ct["gone"]["deals"] == 1 and ct["gone"]["byResp"] == 0
    assert {q["deal"] for q in ct["life"]["queue"] if q["code"] == "gone"} == {"1"}


def test_доставлено_по_стадии_без_даты_не_просрочка():
    """Заказ в стадии успеха (семантика S) без «Customer delivery date, actual» при
    прошедшем сроке клиента: не просрочка — ни у заказа, ни у сделки, ни в очереди; пункт —
    «внести фактическую дату доставки» (noFact). Стадию «доставлено» с семантикой P по
    имени не угадываем: имена стадий у воронок свои."""
    п = Портал()
    п.orders.append({"id": 505, "title": "902/4. Компрессор", "companyId": "", "parentId2": 2,
                     "opportunity": 5000, "currencyId": "EUR", "stageId": "DT172_26:SUCCESS",
                     "createdTime": _t(80), "movedTime": _t(5), DL: _t(15)[:10], OSS: "11"})
    ct = _посчитать(п)
    r = _row(ct, "2")
    o505 = next(o for o in r["orders"] if o["id"] == "505")
    assert o505["live"] is False and o505["late"] is False and r["late"] is False
    life = ct["life"]
    assert life["counts"].get("late") == 1, "просрочка — только заказ 501 сделки 1"
    assert [q["code"] for q in life["queue"] if q["order"] == "505"] == ["noFact"]


def test_сумма_и_ндс_сделки_для_сверки_с_файлом():
    """Ревизия 25.10.2026 (дефекты 4 и 5): «Сумма» и НДС в ней — числом для сверки с
    финальной экономикой файла; нечитаемая — None, а не 0 («суммы нет» ≠ «не прочитали»).
    Итоги вкладки считают сумму как прежде: для читаемых чисел ничего не меняется."""
    assert contracts_mod._amount("1500.5") == 1500.5 and contracts_mod._amount(1500) == 1500.0
    assert contracts_mod._amount(None) == 0.0 and contracts_mod._amount("") == 0.0
    assert contracts_mod._amount("много") is None and contracts_mod._amount(float("nan")) is None
    assert contracts_mod._amount(float("inf")) is None
    п = Портал()
    до = _посчитать(Портал())
    п.deals[0]["TAX_VALUE"] = "18032.79"                  # придуманный НДС в «Сумме»
    ct = _посчитать(п)
    r1, r2 = _row(ct, "1"), _row(ct, "2")
    assert (r1["saleAmt"], r1["saleTax"]) == (100_000.0, 18_032.79) and (r2["saleAmt"], r2["saleTax"]) == (50_000.0, 0.0)
    assert [x["saleEur"] for x in ct["rows"]] == [x["saleEur"] for x in до["rows"]], "итоги — как были"


# ── отдельное направление (решение владельца 25.10.2026) ──────────────────────
# Корпус — fixture.ContractsStub: 6100 — сделка воронки направления с заказом, 6101 —
# сделка реализации той же компании, прочие — другие клиенты. Имена придуманы (правило 18).

def _стаб_со_счётом():
    """ContractsStub, который запоминает, какие обходы сделок у него спросили."""
    from tests import fixture
    stub = fixture.ContractsStub()
    stub.обходы = []
    orig = stub.list_deals_fast

    def list_deals_fast(*, filter=None, select=None, **kw):
        stub.обходы.append((dict(filter or {}), list(select or [])))
        return orig(filter=filter, select=select, **kw)
    stub.list_deals_fast = list_deals_fast
    return stub


def _реализация(stub=None, *, без_направлений=False, monkeypatch=None):
    import os

    from tests import fixture
    if без_направлений:
        monkeypatch.setattr(contracts_mod, "ОТДЕЛЬНЫЕ_ВОРОНКИ", ())
    os.environ["KVANT_NOW"] = fixture.CT_NOW
    try:
        return contracts_mod.compute(stub or fixture.ContractsStub(), as_of=fixture.CT_TODAY, people={})
    finally:
        os.environ.pop("KVANT_NOW", None)


@pytest.fixture(scope="module")
def сеп():
    return _реализация()


def test_направление_по_воронке_и_по_компании(сеп):
    from tests import fixture
    seg = {r["deal"]: r["seg"] for r in сеп["rows"]}
    assert seg["6100"] == fixture.CT_SEP_CAT, "сделка воронки направления"
    assert seg["6101"] == fixture.CT_SEP_CAT, "сделка реализации той же компании"
    другие = [r for r in сеп["rows"] if r["deal"] not in ("6100", "6101")]
    assert другие and all(r["seg"] is None for r in другие), "сделки других клиентов — в основном виде"
    assert "6102" not in seg, "сделка воронки без заказов во вселенную вкладки не входит"
    h = сеп["head"]["sep"]
    assert h["cat"] == fixture.CT_SEP_CAT and h["name"] == fixture.CT_SEP_NAME, "имя — из портала, не из кода"
    assert (h["deals"], h["live"], h["orders"], h["late"], h["lateDeals"], h["companies"]) == (2, 2, 3, 1, 1, 1)


def test_основной_вид_без_направления(сеп):
    сеп_ids = {"6100", "6101"}
    life, sl = сеп["life"], сеп["sep"]["life"]
    assert not {q["deal"] for q in life["queue"]} & сеп_ids, "очередь руководителя — без направления"
    assert {q["deal"] for q in sl["queue"]} == сеп_ids, "у направления — своя очередь"
    assert {(q["deal"], q["code"]) for q in sl["queue"]} >= {("6101", "late"), ("6100", "risk")}
    # плитки, итоги, воронка и сроки клиента — по сделкам основного вида
    main = [r for r in сеп["rows"] if not r["seg"]]
    live = [r for r in main if not r["closed"]]
    h = сеп["head"]
    assert (h["live"], h["total"]) == (len(live), len(main))
    assert h["lateDeals"] == sum(1 for r in live if r["late"]) == сеп["deadlines"]["lateDeals"]
    assert сеп["deadlines"]["liveOrders"] == sum(1 for r in live for o in r["orders"] if o["live"])
    assert sum(f["n"] for f in сеп["funnel"]) == sum(1 for r in live if r["inCat0"])
    assert sum(p["orders"] for p in sl["phases"]) == 3 and sum(p["orders"] for p in life["phases"]) > 0
    assert life["counts"].get("late") == sum(1 for q in life["queue"] if q["code"] == "late")
    assert сеп["sep"]["head"]["live"] == 2 and сеп["sep"]["deadlines"]["lateDeals"] == 1
    # нагрузка людей поимённо в открытые данные вкладки не идёт (решение 26.10.2026, п. 3)
    assert "people" not in life and "people" not in sl


def test_нормы_и_поставщики_по_всем_заказам(сеп, monkeypatch):
    """Норма звена, обычное опоздание и надёжность поставщика — мерило, а не портфель:
    в обоих видах те же, что без разделения."""
    без = _реализация(без_направлений=True, monkeypatch=monkeypatch)
    for k in ("lead", "norms", "suppliers", "supAll"):
        assert сеп["life"][k] == сеп["sep"]["life"][k] == без["life"][k], k
    assert сеп["speed"]["medCycle"] == без["speed"]["medCycle"]
    assert сеп["head"]["grossThr"] == без["head"]["grossThr"]
    assert [b["med"] for b in сеп["speed"]["orderBench"]] == [b["med"] for b in без["speed"]["orderBench"]]
    assert сеп["suppliers"]["rows"] == без["suppliers"]["rows"], "вкладка «Поставщики» не меняется"


def test_сумма_видов_не_теряет_ни_пункта(сеп, monkeypatch):
    """Сумма прячет потерю: пункты обеих очередей — ровно прежняя одна очередь (её же
    читает очередь КАМа), итоги видов складываются в прежние."""
    без = _реализация(без_направлений=True, monkeypatch=monkeypatch)
    assert без.get("sep") is None and all(r["seg"] is None for r in без["rows"])

    def ключ(q):
        return (q["code"], q["deal"], q["order"], q["days"], q["who"], q["phase"])
    вместе = contracts_mod.life_all(сеп)
    assert sorted(map(ключ, вместе["queue"])) == sorted(map(ключ, без["life"]["queue"]))
    assert [q["code"] for q in вместе["queue"]] == [q["code"] for q in без["life"]["queue"]]
    assert вместе["counts"] == без["life"]["counts"]
    for k in ("live", "total", "lateDeals", "slow", "contracts", "paidStage", "nNoBuy", "grossSusp"):
        assert сеп["head"][k] + сеп["sep"]["head"][k] == без["head"][k], k
    for k in ("lateOrders", "liveOrders", "noDl"):
        assert сеп["deadlines"][k] + сеп["sep"]["deadlines"][k] == без["deadlines"][k], k
    assert len(сеп["rows"]) == len(без["rows"]), "строки таблицы — все, с признаком вида"


def test_очередь_кама_не_меняется(сеп, monkeypatch):
    """Очередь КАМа (kamqueue.py) читает обе очереди: направление снято только с
    ежедневной очереди руководителя реализации."""
    import kamqueue
    from tests import fixture
    без = _реализация(без_направлений=True, monkeypatch=monkeypatch)
    ps = fixture.build_presale()
    a = kamqueue.build(ps, contracts_mod.life_all(сеп), сеп["rows"], fixture.CT_TODAY, people=fixture.CT_PEOPLE)
    b = kamqueue.build(ps, без["life"], без["rows"], fixture.CT_TODAY, people=fixture.CT_PEOPLE)
    assert a == b
    assert any(x["deal"] == "6101" for x in a["queue"]), "сделка направления — в очереди КАМа"


def test_обход_направления_один_запрос():
    """Бюджет портала (правило 24.09.2026): +1 обход на воронку направления — только ID,
    воронка и компания; имя — из справочника воронок (кэш клиента)."""
    stub = _стаб_со_счётом()
    _реализация(stub)
    assert [f for f, _ in stub.обходы] == [{"CATEGORY_ID": 0}] + [
        {"CATEGORY_ID": c} for c in contracts_mod.ОТДЕЛЬНЫЕ_ВОРОНКИ]
    assert stub.обходы[1][1] == ["ID", "CATEGORY_ID", "COMPANY_ID"]


def test_без_сделок_направления_вкладка_с_нулями():
    """Сделок направления во вселенной вкладки нет (закрылись, ушли) — вида нет, но сводка
    направления есть, с нулями: вкладка остаётся и говорит «Нет данных» (portal-ui §2),
    а не пропадает молча. Компания направления — по сделке его воронки без заказов."""
    from tests import fixture
    stub = fixture.ContractsStub()
    stub.deals = [d for d in stub.deals if d["ID"] != "6101"]
    stub.other = [x for x in stub.other if x["ID"] != "6100"]        # осталась 6102 без заказов
    stub.orders = [o for o in stub.orders if str(o["parentId2"]) not in ("6100", "6101")]
    data = _реализация(stub)
    assert data["sep"] is None and all(r["seg"] is None for r in data["rows"])
    h = data["head"]["sep"]
    assert (h["cat"], h["name"]) == (fixture.CT_SEP_CAT, fixture.CT_SEP_NAME), "имя — из портала и без сделок"
    assert (h["deals"], h["live"], h["orders"], h["late"], h["lateDeals"], h["queue"], h["companies"]) == \
        (0, 0, 0, 0, 0, 0, 1)
    assert h["sale"] == contracts_mod._money(0)


def test_без_направлений_сводки_нет(monkeypatch):
    """Направления не заданы (ОТДЕЛЬНЫЕ_ВОРОНКИ пуст) — вкладка как до 25.10.2026: ни вида,
    ни сводки, ни переключателя."""
    data = _реализация(без_направлений=True, monkeypatch=monkeypatch)
    assert data["sep"] is None and "sep" not in data["head"]
    assert all(r["seg"] is None for r in data["rows"])
