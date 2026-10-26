"""Доход КАМов и продуктов (kaminc.py) — подменю «Доход» вкладки «Key-аккаунты и продукты».

Корпус придуман (CLAUDE.md, правило 18): номера сделок, люди, клиенты и суммы не взяты
из портала; форма строк — как у contracts.compute → rows, lifecycle.build → pay,
people.compute → deals и kamqueue.build → queue. Проверяется то, что решил владелец
25.10.2026: деньги — только у сделок, сверенных файлом экономики; «получено» — стадия
«Оплата получена» с датой входа в неё; ожидаемая дата оплаты без медиан; одно правило
атрибуции; двойная проверка денег (по сделкам = по людям, Σ строк разреза = итог) и
снятие денег при расхождении.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import contracts  # noqa: E402
import kaminc  # noqa: E402
import lifecycle  # noqa: E402
import premia  # noqa: E402

TODAY = dt.date(2031, 5, 20)          # II кв. 2031: с 01.04 по 30.06
CALIB = {"plug": 0.04, "calibN": 45}
ROSTER = {"501": {"name": "Ольха Тест", "active": True}, "502": {"name": "Ясень Тест", "active": True},
          "503": {"name": "Клён Уволен", "active": False}, "601": {"name": "Ива Продукт", "active": True},
          "701": {"name": "Бук Исполнитель", "active": True}}


def row(deal, sale, *, kam="501", src="file", buy=None, buy_src="file", paid=False, stage_days=10,
        closed=False, won="", lost=False, customer="АО «Тестовый холдинг»", **kw):
    """Строка «Реализации» (contracts.rows) — только поля, которые читает kaminc."""
    r = {"deal": str(deal), "seq": int(deal) % 1000, "title": f"{int(deal) % 1000}. Поставка {deal}",
         "customer": customer, "saleEur": sale, "saleKnown": True, "saleSrc": src,
         "buyEur": round(sale * 0.6) if buy is None else buy, "buySrc": buy_src, "buySusp": False,
         "fxMissing": [], "planBreak": None, "planSusp": False, "kamId": kam, "manager": "Бук Исполнитель",
         "ossDeal": "", "paidStage": paid, "closed": closed or paid, "lost": lost,
         "stageName": "Оплата получена | Закрытие сделки" if paid else "Производство",
         "stageDays": stage_days, "wonAt": won, "wonSrc": "tkp" if won else "", "seg": None}
    r.update(kw)
    return r


def contracts_data(rows, pay=None, queue=None):
    return {"rows": rows, "fx": {"asOf": "01.05.2031"},
            "life": {"pay": pay or {}, "queue": queue or []}}


def pdeal(did, *, state="presale", kam="", kam_src="", pl="", own="701", line=(), stage="ТКП выдано",
          idle=10, comp="900", cat="Клиентская воронка", cat_id="8"):
    return {"id": str(did), "t": f"Сделка {did}", "state": state, "kam": kam, "kamSrc": kam_src, "pl": pl,
            "own": own, "line": list(line), "stage": stage, "idle": idle, "comp": comp, "cat": cat, "catId": cat_id}


def corpus():
    rows = [
        row(1001, 100_000, paid=True, stage_days=12),                 # оплачено в квартале (08.05)
        row(1002, 50_000, paid=True, stage_days=80, kam="502"),       # оплачено раньше (01.03)
        row(1003, 200_000, won="10.05.2031"),                          # к получению, выиграна в квартале
        row(1004, 80_000, kam="502"),                                   # к получению, срок оплаты прошёл
        row(1005, 60_000, src="bitrix"),                                # «Сумма» не сверена — класс C
        row(1006, 40_000, kam="503"),                                   # КАМ уволен — «не закреплено»
        row(1007, 30_000, kam="", buy_src="none", buy=0),              # без КАМа, без закупки — A без R
        row(1008, 70_000, lost=True),                                   # проиграна — вне дохода
        row(1009, 90_000, closed=True, won="01.01.2030", stage_days=400),   # закрыта давно — вне дохода
        # закрыта в квартале: в «Оплата получена» вошла 20.04, в финальную стадию — 5 дн. назад
        row(1010, 20_000, closed=True, stage_days=5, stageName="Сделка успешна",
            timeline=[{"name": "Производство", "at": "2031-03-01 09:00"},
                      {"name": "Оплата получена | Закрытие сделки", "at": "2031-04-20 10:00"},
                      {"name": "Сделка успешна", "at": "2031-05-15 12:00"}]),
        row(1011, 50_000),                                              # платёжка во всех заказах, стадия не переведена
    ]
    pay = {"1001": {"eta": "", "src": "noLive", "orders": 2, "po": 2},
           "1003": {"eta": "2031-08-15", "src": "fcDays", "orders": 1, "po": 0, "delivered": False},
           "1004": {"eta": "2031-05-01", "src": "plan", "orders": 1, "po": 0, "delivered": True},
           "1005": {"eta": "", "src": "none", "orders": 1, "po": 0},
           "1006": {"eta": "2031-06-10", "src": "factGrace", "orders": 1, "po": 0, "delivered": True},
           "1007": {"eta": "2032-03-01", "src": "fcGrace", "orders": 1, "po": 0},
           "1011": {"eta": "", "src": "po", "orders": 2, "po": 2, "delivered": True}}
    queue = [{"deal": "1004", "code": "unpaid"}, {"deal": "1003", "code": "late"}, {"deal": "1003", "code": "gone"}]
    people = {"deals": [
        pdeal(1003, state="real", kam="501", kam_src="поле", pl="601", line=["7"]),
        pdeal(1006, state="real", kam="503", kam_src="поле", pl="601"),          # уволенный КАМ, живой PO
        pdeal(1007, state="real", kam="701", kam_src="владелец"),               # прокси-КАМ = исполнитель
        pdeal(2001, kam="501", kam_src="поле", pl="601", line=["7", "8"], stage="ТКП выдано клиенту"),
        pdeal(2002, kam="502", kam_src="владелец", stage="Переторжка", comp="901"),
        pdeal(2003, stage="Подготовка ТКП", idle=200),                 # тихая потеря
        pdeal(2004, stage="Экономика готова | Можно подавать", cat="Пресейл", cat_id="32"),
    ], "inSrc": [pdeal(2005, state="src", stage="Поиск поставщиков и сбор КП", cat="Пресейл", cat_id="32")],
        "names": {"701": ["Бук Исполнитель", True]}}
    presale = {"cid": "32", "openDeals": [{"id": "2004", "chat": {"status": "quiet"}}]}
    kamq = {"queue": [{"code": "unpaid", "sev": 6, "kamId": "502", "deal": "1004", "title": "Поставка 1004",
                       "what": "не оплачено", "act": "получить дату оплаты", "days": 19}]}
    return contracts_data(rows, pay, queue), people, presale, kamq


def run(**kw):
    ct, people, presale, kamq = corpus()
    args = dict(today=TODAY, presale=presale, kamq=kamq, calib=CALIB, cat_labels={"7": "Насосы", "8": "ЗИП"},
                companies={"900": "АО «Тестовый холдинг»", "901": "ООО «Другой клиент»"}, roster=ROSTER)
    args.update(kw)
    return kaminc.compute(ct, people, **args)


def R(sale, buy):
    """Результат по нормативу, как в положении: ПЗ = закупка × 1,10."""
    return premia.result_of(sale, buy * (1 + premia.PZ_OVER), buy, CALIB["plug"])


# ------------------------------------------------------------------ классы стадий
def test_регулярки_стадий_дословно_как_в_зонде_конверсии():
    spec = importlib.util.spec_from_file_location("kvant_probe_conversion_k", ROOT / "scripts" / "probe_conversion.py")
    pc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pc)
    for name in ("ПРЕДЛОЖЕНИЕ", "ЕЩЁ_НЕ_ВЫДАНО", "ПОСЛЕ_ПРЕДЛОЖЕНИЯ"):
        assert getattr(kaminc, name).pattern == getattr(pc, name).pattern, name


@pytest.mark.parametrize("name, presale, cls", [
    ("Подготовка ТКП", False, "pre"), ("ТКП выдано клиенту", False, "offer"), ("Переторжка", False, "tender"),
    ("Ожидаем решения заказчика", False, "wait"), ("Согласование финальной спецификации", False, "spec"),
    ("Поиск поставщиков и сбор КП", True, "pre"), ("Экономика готова | Можно подавать", True, "pre"),
    ("Техническая часть | Квалификация", True, "offer"),
    ("Тендерное предложение выдано | Работаем с клиентом по тендеру", True, "tender"),
    ("Сорсинг завершен / Ожидаем решение заказчика", True, "wait"),
    ("Согласование финальной спецификации", True, "spec"),
])
def test_класс_стадии(name, presale, cls):
    assert kaminc.stage_class(name, presale_funnel=presale) == cls


# ------------------------------------------------------------------ атрибуция
def test_одно_правило_атрибуции():
    act = lambda u: u != "503"
    c = kaminc.credit(kam_field="501", kam_proxy="502", pl="601", own="701", line=["Насосы", "ЗИП"], active=act)
    assert (c["kam"], c["kamSrc"], c["po"], c["ex"], c["line"], c["lineMore"]) == ("501", "поле", "601", "701", "Насосы", 1)
    c = kaminc.credit(kam_proxy="502", active=act)
    assert (c["kam"], c["kamSrc"], c["line"], c["lineSrc"]) == ("502", "прокси", kaminc.LINE_NONE, "")
    c = kaminc.credit(kam_field="503", pl="503", active=act)          # уволенный в поле — не закреплено
    assert (c["kam"], c["kamSrc"], c["po"]) == (kaminc.GONE_KEY, "уволен", kaminc.GONE_KEY)
    assert kaminc.credit(active=act)["kam"] == ""


# ------------------------------------------------------------------ класс доверия и R
def test_деньги_только_у_сверенных_файлом():
    assert kaminc.trust(row(1, 100_000, src="bitrix"), CALIB)[0::4] == ("C", "noFile")
    r = row(1, 100_000)
    del r["saleSrc"]                                     # до «файла экономики первым» поля нет
    assert kaminc.trust(r, CALIB)[0] == "C"
    cls, sale, res, coef, why = kaminc.trust(row(1, 100_000, buy=60_000), CALIB)
    assert (cls, sale, why) == ("A", 100_000, "") and res == pytest.approx(R(100_000, 60_000))
    assert coef == pytest.approx(100_000 / 66_000, abs=1e-3)
    assert kaminc.trust(row(1, 100_000, buy=0, buy_src="none"), CALIB)[2::2] == (None, "noPz")
    assert kaminc.trust(row(1, 100_000, buy=60_000, buySusp=True), CALIB)[4] == "noPz"
    # поправка откалибрована меньше чем по 30 сделкам — R нет нигде
    assert kaminc.trust(row(1, 100_000), {"plug": 0.04, "calibN": 12})[2::2] == (None, "calib")
    assert kaminc.trust(row(1, 100_000), None)[4] == "calib"


# ------------------------------------------------------------------ корзины KPI
def test_корзины_и_даты():
    H = run()["head"]
    T = H["tot"]
    D = {d["id"]: d for d in H["deals"]}
    assert "1008" not in D and "1009" not in D                 # проигранная и давно закрытая — вне дохода
    assert D["1001"]["b"] == "paidQ" and D["1001"]["paid"] == "08.05.2031" and D["1001"]["po1"] is True
    assert D["1002"]["b"] == "paidOld"
    assert (T["k1"], T["k1po"], T["k1old"], T["k1cl"], T["k1pf"]) == (2, 1, 1, 1, 1)
    assert T["due"] == 5 and T["dueB"] == {"overdue": 1, "q0": 1, "q1": 1, "later": 1, "none": 1}
    assert D["1003"]["db"] == "q1" and D["1004"]["db"] == "overdue" and D["1006"]["db"] == "q0"
    assert D["1007"]["db"] == "later" and D["1005"]["db"] == "none"
    assert (T["k3c"], T["k3r"]) == (1, 1) and D["1003"]["risk"] == ["late"]   # «gone» — не угроза денег
    assert T["k4"] == 1 and D["1003"]["wq"]
    # невыигранные: класс стадии, тихая потеря вне пайплайна, пресейл у сорсинга — в пайплайне
    assert T["pipe"] == 4 and T["quiet"] == 1
    assert T["pipeC"] == {"pre": 2, "offer": 1, "tender": 1, "wait": 0, "spec": 0}
    assert D["2002"]["kam"] == "502" and D["2002"]["kamS"] == "прокси" and D["2002"]["h"] == "ООО «Другой клиент»"
    assert D["2001"]["ln"] == "Насосы" and D["2001"]["po"] == "601"


def test_роли_и_строки_разрезов():
    H = run()["head"]
    kam = {r["key"]: r for r in H["cuts"]["kam"]}
    assert kam[kaminc.GONE_KEY]["name"] == kaminc.GONE and kam[kaminc.GONE_KEY]["due"] == 1
    assert kam[""]["name"] == kaminc.KAM_NONE and kam[""]["due"] == 0 and kam[""]["pipe"] == 2
    # 1007 — за прокси-КАМом: ответственный сделки по должности КАМ
    assert kam["701"]["due"] == 1 and {d["id"]: d for d in H["deals"]}["1007"]["kamS"] == "прокси"
    line = {r["key"]: r for r in H["cuts"]["line"]}
    assert line["Насосы"]["due"] == 1 and line[kaminc.LINE_NONE]["due"] == 4
    D = {d["id"]: d for d in H["deals"]}
    assert D["1003"]["po"] == "601" and D["1003"]["ln"] == "Насосы"   # сырьё «Коммерсантов» по сделке


def test_двойная_проверка_сходится_и_деньги_по_людям():
    out = run()
    H, M = out["head"], out["money"]
    assert H["money"]["ok"] and all(c["ok"] for c in H["checks"])
    # Σ по строкам каждого разреза = итог — по числу и по деньгам
    for cut, rows in H["cuts"].items():
        for f in ("k1", "due", "k4", "pipe", "quiet", "n", "nA"):
            assert sum(r[f] for r in rows) == H["tot"][f], (cut, f)
        assert sum(m["dueR"] for m in M["cuts"][cut]) == pytest.approx(M["tot"]["dueR"], abs=len(rows))
        assert {m["key"] for m in M["cuts"][cut]} == {r["key"] for r in rows}   # списком, ключ — в строке
    # R по сделкам — из положения; 1005 (не сверена) и 1007 (нет закупки) — без R
    with_r = {"1001": (100_000, 60_000), "1003": (200_000, 120_000), "1004": (80_000, 48_000),
              "1006": (40_000, 24_000), "1010": (20_000, 12_000)}
    for did, (s, b) in with_r.items():
        assert M["deals"][did]["r"] == round(R(s, b))
    assert "1005" not in M["deals"] and M["deals"]["1007"]["r"] is None
    assert M["tot"]["dueR"] == round(sum(R(*with_r[d]) for d in ("1003", "1004", "1006")))
    assert M["tot"]["k1R"] == round(R(100_000, 60_000) + R(20_000, 12_000))   # с закрытой в квартале
    # K7: Σ частей людей = Σ пулов сделок при К (вместе с «не закреплено»)
    pools = sum(premia.pool_of(R(s, b), s / (b * 1.1)) * premia.K_DEFAULT for s, b in with_r.values())
    k7 = sum(x["paidQ"] + x["due"] for x in M["k7"].values())
    assert k7 == pytest.approx(pools, abs=len(M["k7"]))
    assert "" in M["k7"]                                   # доля уволенного КАМа — «не закреплено»


def test_расхождение_проверки_снимает_деньги(monkeypatch):
    real = premia._parts
    monkeypatch.setattr(premia, "_parts", lambda d, neg: [p[:2] + [p[2] * 0.9] for p in real(d, neg)])
    out = run()
    assert out["money"] is None and not out["head"]["money"]["ok"]
    assert out["head"]["money"]["fail"][0] == "Σ R по сделкам = Σ по людям через доли"


def test_в_открытой_части_нет_евро():
    out = run()
    head = json.dumps(out["head"], ensure_ascii=False)
    for v in list(out["money"]["deals"].values()):
        for x in (v["s"], v["r"]):
            if x:
                assert f":{x}," not in head and f":{x}}}" not in head
    for d in out["head"]["deals"]:
        assert not {"s", "r", "sale", "_res", "_sale", "_r", "_pi"} & set(d)


def test_без_калибровки_r_нет_и_класс_a_остаётся():
    out = run(calib={"plug": 0.0, "calibN": 3})
    assert out["head"]["tot"]["nA"] == 6 and out["head"]["tot"]["nR"] == 0
    assert out["head"]["trust"]["calibOk"] is False
    assert all(v["r"] is None for v in out["money"]["deals"].values())


def test_действия_до_пятнадцати_и_с_результатом_первыми():
    H = run()["head"]
    A = H["actions"]
    assert A["502"]["items"][0]["code"] == "unpaid"        # очередь КАМа — у своего КАМа
    codes = {x["code"] for x in A[""]["items"]}
    assert {"noPo", "goneKam"} <= codes                      # пробелы без хозяина — руководителю
    assert any(x["code"] == "noBuy" for x in A["701"]["items"])   # 1007: нет закупки — её КАМу (прокси)
    assert any(x["code"] == "econ" for x in A["501"]["items"])  # 1005: «Сумма» не сверена
    assert all(len(v["items"]) <= kaminc.ACTIONS_MAX for v in A.values())
    ct, people, presale, kamq = corpus()
    kamq["queue"] = kamq["queue"] * 30
    big = kaminc.compute(ct, people, today=TODAY, kamq=kamq, calib=CALIB, roster=ROSTER)["head"]["actions"]["502"]
    # 30 пунктов очереди и «приложить платёжку» по 1002 (оплачено раньше, платёжки нет)
    assert big["n"] == 31 and len(big["items"]) == kaminc.ACTIONS_MAX


def test_нет_реализации_нет_дохода():
    assert kaminc.compute(None, {}, today=TODAY) is None
    assert kaminc.compute({"rows": None}, {}, today=TODAY) is None


# ------------------------------------------------------------------ дата оплаты
def _o(**kw):
    o = {"live": True, "success": False, "clientPaid": False, "payPlan": None, "delFact": None}
    o.update(kw)
    return o


def test_дата_оплаты_по_каскаду_без_медиан():
    t = TODAY
    d = dt.date
    assert lifecycle.pay_eta([_o(payPlan=d(2031, 7, 1))], 30, t, {}, {}) == (d(2031, 7, 1), "plan")
    assert lifecycle.pay_eta([_o(delFact=d(2031, 5, 1))], 45, t, {}, {}) == (d(2031, 6, 15), "factDays")
    assert lifecycle.pay_eta([_o(delFact=d(2031, 5, 1))], None, t, {}, {}) == (
        d(2031, 5, 1) + dt.timedelta(days=lifecycle.PAY_GRACE), "factGrace")
    # не отгружен, медиан звеньев нет — прогноза нет, и даты нет: медианой не достраивается
    assert lifecycle.pay_eta([_o(shipPlan=d(2031, 6, 1))], 30, t, {}, {}) == (None, "none")
    med = {"transit": 20, "lastMile": 10}
    eta, src = lifecycle.pay_eta([_o(shipFact=d(2031, 5, 10))], 30, t, med, {})
    assert src == "fcDays" and eta == d(2031, 6, 9) + dt.timedelta(days=30)
    # последний платёж — по самому позднему заказу; один без даты — у сделки даты нет
    two = [_o(payPlan=d(2031, 7, 1)), _o(payPlan=d(2031, 9, 1))]
    assert lifecycle.pay_eta(two, None, t, {}, {}) == (d(2031, 9, 1), "plan")
    assert lifecycle.pay_eta(two + [_o()], None, t, {}, {})[1] == "none"
    assert lifecycle.pay_eta([_o(clientPaid=True)], None, t, {}, {}) == (None, "po")
    assert lifecycle.pay_eta([_o(live=False, success=True)], None, t, {}, {}) == (None, "noLive")


def test_отсрочка_из_сделки():
    assert contracts._pay_days("45") == 45 and contracts._pay_days("30.0") == 30
    assert contracts._pay_days("") is None and contracts._pay_days("нет") is None
    assert contracts._pay_days("-5") is None and contracts._pay_days(None) is None


# ------------------------------------------------------------------ премии: оплаченные отдельно
def test_премии_оплаченные_не_в_пуле_реализации():
    deals = [{"id": "A", "t": "A", "cat": "Реализация", "stage": "Оплата получена", "raw": 10_000, "buyRaw": 5_000,
              "state": "real", "kam": "1", "prod": "", "own": "3", "big": False, "idle": 1},
             {"id": "B", "t": "B", "cat": "Реализация", "stage": "Производство", "raw": 20_000, "buyRaw": 9_000,
              "state": "real", "kam": "1", "prod": "", "own": "3", "big": False, "idle": 1}]
    out = premia.compute({"deals": deals, "roles": {}, "names": {}},
                         {"rows": [{"deal": "A", "paidStage": True}, {"deal": "B"}]}, eur_rub=100.0)
    st = {r["id"]: r["st"] for r in out["deals"]}
    assert st == {"A": "d", "B": "r"}
    assert out["totals"]["paid"] == 1 and out["totals"]["real"] == 1
    b = next(r for r in out["deals"] if r["id"] == "B")
    assert out["totals"]["poolReal"] == round(b["p1"] * premia.K_DEFAULT)


def test_результат_премий_и_дохода_одна_формула():
    # правило 14: одна вставка формулы — премии считают в ₽, доход в €, результат линеен
    assert premia.result_of(100.0, 60.0, 50.0, 0.1) * 100 == pytest.approx(premia.result_of(10_000, 6_000, 5_000, 0.1))
    assert premia.pool_of(-100.0, 2.0) == pytest.approx(premia.LPR_RATE * -100.0)
    assert premia.pool_of(100.0, 1.1) == 0.0


# ------------------------------------------------------------------ живая сверка
def test_живая_сверка_первый_прогон_с_заявкой_проходит():
    sys.path.insert(0, str(ROOT / "scripts"))
    import live_check as lc
    from tests.test_live_check import ПРОД, вкладки
    после = вкладки(kaminc=run())
    заявка = lc.ожидания("ожидается: меняется kaminc")
    итог = lc.сверить(ПРОД, copy.deepcopy(ПРОД), заявка, служебные={"234", "542"},
                      модули_до=вкладки(), модули_после=после, точно=True)
    assert [t for u, t in итог if u == lc.ПРОВАЛ] == []
    имена = [t for u, t in итог if u == lc.ГОДНО]
    assert any("инварианты правки сходятся" in t for t in имена)
    # без заявки новый раздел — провал (точный режим)
    итог = lc.сверить(ПРОД, copy.deepcopy(ПРОД), lc.ожидания("ожидается: без изменений"), служебные={"234", "542"},
                      модули_до=вкладки(), модули_после=после, точно=True)
    assert any("kaminc" in t for u, t in итог if u == lc.ПРОВАЛ)


def test_живая_сверка_ловит_разошедшийся_разрез():
    sys.path.insert(0, str(ROOT / "scripts"))
    import live_check as lc
    k = run()
    k["head"]["cuts"]["kam"][0]["due"] += 1
    inv = {имя: ok for имя, ok, _ in lc.инварианты({"kaminc": k})}
    assert inv["доход: Σ строк разреза «kam» = итог"] is False
    assert inv["доход: Σ строк разреза «po» = итог"] is True
    assert inv["доход: сверка денег по сделкам и по людям"] is True


# ------------------------------------------------------------------ правки по ревью 25.10.2026
def test_K1_нарастающим_закрытая_в_квартале_остаётся():
    # вошла в «Оплата получена» 20.04 и закрыта 15.05 — в K1 квартала по дате входа в оплату
    out = run()
    D = {d["id"]: d for d in out["head"]["deals"]}
    assert D["1010"]["b"] == "paidQ" and D["1010"]["cl"] is True and D["1010"]["paid"] == "20.04.2031"
    assert "1010" in out["money"]["deals"] and out["money"]["deals"]["1010"]["r"] == round(R(20_000, 12_000))
    # без пути стадий — дата входа в финальную стадию (день сборки − дни в стадии)
    r = row(1, 10_000, closed=True, stage_days=3)
    assert kaminc.paid_entry(r) is None
    ct = contracts_data([r])
    d = kaminc.compute(ct, {}, today=TODAY, calib=CALIB, roster=ROSTER)["head"]["deals"][0]
    assert (d["b"], d["paid"], d["cl"]) == ("paidQ", "17.05.2031", True)
    # закрытой сделке платёжку не просим: работа по ней кончена
    acts = run()["head"]["actions"]
    assert not any(x["code"] == "paidNoPo" and x["deal"] == "1010" for v in acts.values() for x in v["items"])


def test_оплачено_по_платёжке_не_к_получению():
    out = run()
    H, M = out["head"], out["money"]
    D = {d["id"]: d for d in H["deals"]}
    assert D["1011"]["b"] == "paidFile" and H["tot"]["k1pf"] == 1
    # ни в K1, ни в K2: ни счётом, ни деньгами, ни в K7
    assert H["tot"]["due"] == 5 and H["tot"]["k1"] == 2
    r1011 = M["deals"]["1011"]["r"]
    due_ids = [d for d in D.values() if d["b"] == "due" and d["id"] in M["deals"] and M["deals"][d["id"]]["r"] is not None]
    assert M["tot"]["dueR"] == sum(M["deals"][d["id"]]["r"] for d in due_ids) and r1011
    assert any(x["code"] == "moveStage" and x["deal"] == "1011" for x in H["actions"]["501"]["items"])
    # «Живых заказов нет» без платёжек во всех — не «оплачено», а «без даты»
    ct = contracts_data([row(1, 10_000)], {"1": {"eta": "", "src": "noLive", "orders": 0, "po": 0}})
    d = kaminc.compute(ct, {}, today=TODAY, calib=CALIB, roster=ROSTER)["head"]["deals"][0]
    assert (d["b"], d["db"]) == ("due", "none")


def test_K7_уволенный_КАМ_доля_PO_как_эксперта():
    # 1006: КАМ уволен, PO жив — PO получает 20 % части ЛПР (эксперт), а не 80 % (завёл сделку)
    out = run()
    s, b = 40_000, 24_000
    pool = premia.pool_of(R(s, b), s / (b * 1.1)) * premia.K_DEFAULT
    k7 = out["money"]["k7"]
    po_due = pool * premia.LPR_RATE / premia.RATE * premia.PO_EXPERT           # 0,13 пула
    po_1003 = premia.pool_of(R(200_000, 120_000), 200_000 / 132_000) * premia.K_DEFAULT * 0.13
    assert k7["601"]["due"] == pytest.approx(po_due + po_1003, abs=2)
    # «не закреплено»: 0,52 пула 1006 (часть уволенного КАМа)
    assert k7[""]["due"] >= round(pool * 0.52) - 1


def test_K7_знаменатель_и_счёт_по_сделкам():
    k7 = run()["money"]["k7"]
    # 701 — исполнитель 1003, 1006 и 1007 и прокси-КАМ 1007: сделок три, а не четыре
    assert (k7["701"]["n"], k7["701"]["nA"], k7["701"]["nR"]) == (3, 3, 2)
    assert set(k7["701"]["roles"]) == {"kam", "ex"}


def test_причины_складываются_в_n():
    T = run()["head"]["trust"]
    assert sum(T["reasons"].values()) == T["n"] and T["reasons"].get("ok", 0) == T["nR"]
    T = run(calib={"plug": 0.0, "calibN": 3})["head"]["trust"]
    assert sum(T["reasons"].values()) == T["n"] and T["reasons"]["calib"] == T["nA"] - T["reasons"].get("noPz", 0)


def test_проверки_разными_путями():
    H = run()["head"]
    names = {c["name"] for c in H["checks"]}
    assert {"K7: Σ частей людей = Σ пулов сделок", "итог «k1R» = Σ по сделкам корзины",
            "итог «dueR» = Σ по сделкам корзины", "счёт плиток = число сделок по корзинам"} <= names
    # K1 по платёжкам — пометка, а не гейт: подтверждено 1 из 2 (50 %) — не ниже порога
    assert H["trust"]["poLow"] is False and H["money"]["ok"]
    ct, people, presale, kamq = corpus()
    for r in ct["rows"]:
        if r["deal"] == "1001":
            r["paidStage"], r["closed"] = False, True       # закрыта, платёжки нет — 0 из 2
            r["timeline"] = [{"name": "Оплата получена | Закрытие сделки", "at": "2031-05-08 10:00"}]
    ct["life"]["pay"]["1001"]["po"] = 0
    out = kaminc.compute(ct, people, today=TODAY, presale=presale, kamq=kamq, calib=CALIB, roster=ROSTER)
    assert out["head"]["trust"]["poLow"] is True and out["money"] is not None


def test_в_денежном_массиве_нет_коэффициента():
    # продажа и коэффициент к ПЗ вместе дали бы закупку — коэффициента в массиве нет
    for v in run()["money"]["deals"].values():
        assert set(v) == {"s", "r"}


def test_дата_оплаты_не_из_одних_медиан():
    med = {"toShip": 40, "transit": 20, "lastMile": 10}
    # заказ без единой своей даты: прогноз был бы «создан + медиана» — даты нет
    assert lifecycle.pay_eta([_o(created=dt.date(2031, 5, 1))], 30, TODAY, med, {}) == (None, "none")
    # с плановой отгрузкой — прогноз от неё, и источник говорит про медианы звеньев
    eta, src = lifecycle.pay_eta([_o(created=dt.date(2031, 5, 1), shipPlan=dt.date(2031, 6, 1))], 30, TODAY, med, {})
    assert src == "fcDays" and eta == dt.date(2031, 6, 1) + dt.timedelta(days=30 + 30)
    assert "медиан" in lifecycle.PAY_SRC["fcDays"] and "медиан" in lifecycle.PAY_SRC["fcGrace"]


def test_отсрочка_и_раздел_pay_не_уходят_в_страницу():
    ct = contracts_data([row(1, 10_000, payDays=45)], {"1": {"eta": "", "src": "none"}})
    ct["sep"] = {"life": {"pay": {"2": {}}}, "rows": [row(2, 5_000, payDays=30)]}
    kaminc.strip_inputs(ct)
    assert "payDays" not in ct["rows"][0] and "payDays" not in ct["sep"]["rows"][0]
    assert "pay" not in ct["life"] and "pay" not in ct["sep"]["life"]
    kaminc.strip_inputs(None)                              # пустая «Реализация» — не падает


def test_страница_вкладки_без_денег_и_закрытый_массив_не_подтягивается():
    import dashboard
    from tests import fixture
    out = run()
    out["head"]["deals"][0]["t"] = "__KAMINC_JSON__ x y"     # «название сделки» с подстановкой и U+2028
    people = fixture.build_people()
    reps = fixture.build_reps()
    kam = {"label": "x", "rows": [{"group": "Г", "deals": 2, "open": 1, "contracts": 1, "sales": "€1K",
                                   "salesRaw": 1000, "buy": "€1K", "margin": "€0", "marginRaw": 0, "conv": 50}],
           "totals": {"deals": 2, "sales": "€1K", "pipeline": "€2K"}, "byRevenue": [{"group": "Г", "v": 1}],
           "kpis": [{"lbl": "Сделок", "val": "2", "meta": "создано"}, {"lbl": "Пайплайн", "val": "€2K", "meta": "Σ, €"}],
           "deals": [{"id": "1", "t": "Т", "amt": "€1K", "raw": 1000}]}
    html = dashboard.render({}, {}, people=people, reps=reps, kam=kam, prod=kam, kaminc=out)
    lines = {ln.split(" = ", 1)[0]: ln for ln in html.splitlines() if ln.startswith("window.__")}
    head = lines["window.__KAMINC_HEAD__"]
    assert "__KAMINC_JSON__ x\\u2028y" in head and " " not in html
    assert json.dumps(out["money"]["deals"], ensure_ascii=False, separators=(",", ":")) not in head
    for name in ("window.__PEOPLE__", "window.__REPS__", "window.__KAM__", "window.__PRODUCT__"):
        payload = json.loads(lines[name].split(" = ", 1)[1].rstrip(";"))

        def walk(o, key=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    assert k not in dashboard.TAB_MONEY_KEYS, (name, k)
                    walk(v, k)
            elif isinstance(o, list):
                for v in o:
                    walk(v, key)
            elif isinstance(o, str) and "€" in o:
                assert key in dashboard.TAB_TEXT_KEYS, (name, key, o)
        walk(payload)
    # счёт остаётся: деньги сняты, сделки и контракты — нет
    kp = json.loads(lines["window.__KAM__"].split(" = ", 1)[1].rstrip(";"))
    assert kp["rows"][0]["deals"] == 2 and [k["lbl"] for k in kp["kpis"] if "val" in k] == ["Сделок"]
    pp = json.loads(lines["window.__PEOPLE__"].split(" = ", 1)[1].rstrip(";"))
    assert pp["roles"]["kam"]["totals"]["open"] == people["roles"]["kam"]["totals"]["open"]
