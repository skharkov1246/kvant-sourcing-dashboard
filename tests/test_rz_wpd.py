"""«Реализация» R4, пакет WP-D «Заказ и шаг» (У4, У5): данные карточки заказа.

Д-1а — прогноз доставки по частям (lifecycle.forecast_parts): дата не сдвинулась ни у одного
заказа (сверка с прежней формулой на придуманном корпусе), части складываются в дату, в
очереди — fcWhy. Д-5 — в строке заказа (contracts ords_det) плановые даты, файлы шагов
(есть / нет / не требуется по ork.art_required), прогноз по частям и контрольные точки; дата
прогноза карточки = дата пункта очереди = дата строки «Недели».

Корпус придуман (CLAUDE.md, правило 18): поставщики «Поставщик А…», даты условные.
"""
from __future__ import annotations

import datetime as dt
import random
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import lifecycle as lc  # noqa: E402
import ork  # noqa: E402

СЕГОДНЯ = dt.date(2026, 10, 19)


def д(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def прежний_прогноз(o: dict, today: dt.date, med: dict, sup_slip: dict) -> dt.date | None:
    """lifecycle.forecast до Д-1а, дословно (эталон сверки: правка не двигает даты)."""
    if not o.get("live") or lc.delivered(o):
        return None
    tr, lm = med.get("transit"), med.get("lastMile")
    if o.get("inbFact"):
        base, rest = o["inbFact"], lm
    elif o.get("shipFact"):
        base, rest = o["shipFact"], (tr + lm if tr is not None and lm is not None else None)
    else:
        rest = tr + lm if tr is not None and lm is not None else None
        кандидаты = []
        if o.get("shipPlan"):
            кандидаты.append(o["shipPlan"])
        if o.get("prodEnd"):
            кандидаты.append(o["prodEnd"] + dt.timedelta(
                days=max(0.0, sup_slip.get(o.get("sup") or "", 0.0) or 0.0)))
        if not кандидаты and o.get("created") and med.get("toShip") is not None:
            кандидаты.append(o["created"] + dt.timedelta(days=med["toShip"]))
        if not кандидаты:
            return None
        base = max(кандидаты)
        base = max(base, today)
    if rest is None:
        return None
    return max(today, base + dt.timedelta(days=round(rest)))


def _случайный_заказ(rnd: random.Random, i: int) -> dict:
    def дата(p: float) -> dt.date | None:
        return СЕГОДНЯ + dt.timedelta(days=rnd.randint(-120, 120)) if rnd.random() < p else None
    return {"id": str(7000 + i), "live": rnd.random() < 0.9, "success": False,
            "sup": rnd.choice(["Поставщик А", "Поставщик Б", "Поставщик В", ""]),
            "created": дата(0.95), "shipPlan": дата(0.4), "prodEnd": дата(0.5), "shipFact": дата(0.15),
            "inbFact": дата(0.1), "delFact": дата(0.08)}


def test_дата_прогноза_не_сдвинулась_ни_у_одного_заказа():
    """Инвариант Д-1а: forecast() после правки = прежняя формула на 4 000 придуманных заказах
    при дробных и целых медианах, с опозданием поставщика и без."""
    rnd = random.Random(20261030)
    мерила = [({"transit": 12.5, "lastMile": 3.4, "toShip": 40.5}, {"Поставщик А": 6.5, "Поставщик Б": -3.0}),
              ({"transit": 20, "lastMile": 5, "toShip": 30}, {"Поставщик В": 11.0}),
              ({"transit": None, "lastMile": 4.0, "toShip": 25.0}, {}),
              ({"transit": 9.5, "lastMile": 2.5}, {"Поставщик А": 0.4})]
    n = сравнено = 0
    for i in range(1000):
        o = _случайный_заказ(rnd, i)
        for med, slip in мерила:
            n += 1
            old = прежний_прогноз(o, СЕГОДНЯ, med, slip)
            assert lc.forecast(o, СЕГОДНЯ, med, slip) == old, (o, med, slip)
            p = lc.forecast_parts(o, СЕГОДНЯ, med, slip)
            assert (p["date"] if p else None) == old
            сравнено += old is not None
    assert n == 4000 and сравнено > 1500      # корпус действительно покрывает прогнозы


def test_части_прогноза_складываются_в_дату():
    """Опора + транзит + миля = дата (без подъёма до сегодня); опоздание поставщика — к концу
    производства; опора в прошлом — «от сегодня» с пометкой."""
    med = {"transit": 12.4, "lastMile": 3.4, "toShip": 40}
    # бюджетный конец производства + обычное опоздание поставщика 6 дн. позже плановой отгрузки
    o = {"live": True, "sup": "Поставщик А", "prodEnd": д("2026-11-01"), "shipPlan": д("2026-11-03")}
    p = lc.forecast_parts(o, СЕГОДНЯ, med, {"Поставщик А": 6.0})
    assert p["baseKind"] == "prodEnd" and p["supSlip"] == 6 and p["baseRaw"] == д("2026-11-07")
    assert p["transit"] == 12 and p["lastMile"] == 4          # 12 + 4 = round(15,8) = 16
    assert p["date"] == p["base"] + dt.timedelta(days=p["transit"] + p["lastMile"]) == д("2026-11-23")
    assert p["baseKind"] in lc.FC_SOLID
    why = lc.fc_why(p)
    assert why.startswith("бюджетный конец производства 01.11.2026 + обычное опоздание поставщика 6 дн.")
    assert "транзит 12 дн. + последняя миля 4 дн." in why and "медиан" not in why
    # плановая отгрузка прошла, отгрузки нет — от сегодня, пометка «прошла»
    p = lc.forecast_parts({"live": True, "shipPlan": д("2026-10-01")}, СЕГОДНЯ, med, {})
    assert p["pushed"] and p["base"] == СЕГОДНЯ and p["baseRaw"] == д("2026-10-01")
    assert "плановая отгрузка поставщика 01.10.2026 — прошла, считаем от 19.10.2026" in lc.fc_why(p)
    # на складе: только последняя миля, транзита нет
    p = lc.forecast_parts({"live": True, "inbFact": д("2026-10-15")}, СЕГОДНЯ, med, {})
    assert p["transit"] is None and p["lastMile"] == 3 and p["date"] == д("2026-10-19")
    assert "транзит" not in lc.fc_why(p)
    # давно на складе: прогноз поднят до сегодня — так и сказано
    p = lc.forecast_parts({"live": True, "inbFact": д("2026-09-01")}, СЕГОДНЯ, med, {})
    assert p["clamped"] and p["date"] == СЕГОДНЯ and "ставим сегодня" in lc.fc_why(p)


def test_дата_из_одних_медиан_помечена():
    """Опора — дата создания + медиана до отгрузки: не опора заказа, пометка «из одних медиан»;
    у опор заказа (факт, план) пометки нет."""
    med = {"transit": 10, "lastMile": 3, "toShip": 30}
    p = lc.forecast_parts({"live": True, "created": д("2026-10-10")}, СЕГОДНЯ, med, {})
    assert p["baseKind"] == "created" and p["baseKind"] not in lc.FC_SOLID and p["toShip"] == 30
    assert lc.fc_why(p).endswith("— дата из одних медиан")
    assert "дата создания заказа 10.10.2026 + обычный срок до отгрузки 30 дн." in lc.fc_why(p)
    for kind, o in (("shipFact", {"shipFact": д("2026-10-10")}), ("shipPlan", {"shipPlan": д("2026-11-10")})):
        p = lc.forecast_parts({"live": True, **o}, СЕГОДНЯ, med, {})
        assert p["baseKind"] == kind and "медиан" not in lc.fc_why(p)
    assert lc.fc_why(None) == ""
    assert lc.forecast_parts({"live": True, "delFact": д("2026-10-01")}, СЕГОДНЯ, med, {}) is None


def test_пункт_очереди_несёт_fcwhy():
    """У пункта с прогнозом — fcWhy словами; без прогноза — пусто."""
    o = {"id": "1", "deal": "100", "seq": 900, "title": "900/1. Насос", "sup": "Поставщик А", "live": True,
         "success": False, "prodStart": д("2026-05-01"), "prodEnd": д("2026-11-20"), "delBudget": д("2026-12-01"),
         "dl": д("2026-11-25"), "created": д("2026-04-01"), "stage": "Производство заказа", "stepSince": "2026-06-01"}
    d = {"deal": "100", "seq": 900, "customer": "Заказчик 1", "closed": False, "done": False, "norders": 1}
    # мерило звеньев — три доставленных заказа (транзит 20 дн., миля 5 дн.)
    мерило = [{"id": str(10 + i), "live": False, "success": True, "created": д("2026-03-01"),
               "shipFact": д("2026-06-01"), "inbFact": д("2026-06-21"), "delFact": д("2026-06-26")}
              for i in range(3)]
    life = lc.build([o], [d], СЕГОДНЯ, measure=[o] + мерило)
    with_fc = [q for q in life["queue"] if q.get("fc")]
    assert with_fc, "корпус должен дать пункт с прогнозом (прогноз позже срока клиента)"
    for q in life["queue"]:
        assert bool(q.get("fcWhy")) == bool(q.get("fc"))


def test_контрольные_точки_недели_из_одной_функции():
    """checkpoints (неделя) — подмножество checkpoint_dates (все точки) по окну пн–вс."""
    o = {"prodStart": "2026-09-01", "prodEnd": "2026-11-30", "shipPlan": "2026-12-05", "stage": "Производство заказа"}
    все = ork.checkpoint_dates(o)
    assert [k for k, _ in все] == ["P50", "P14"]
    assert все[1][1] == д("2026-11-21")                        # плановая отгрузка − 14 дн.
    for week in ("2026-10-15", "2026-11-18", "2026-12-01"):
        w = д(week)
        mon = w - dt.timedelta(days=w.weekday())
        ждём = [{"kind": k, "date": x.isoformat()} for k, x in все if mon <= x <= mon + dt.timedelta(days=6)]
        assert ork.checkpoints(o, week) == ждём
    assert ork.checkpoint_dates({**o, "shipFact": "2026-10-01"}) == []


# --- сборка вкладки на синтетике (tests/fixture): Д-5 и сквозная сверка прогноза ---------------
def _сборка():
    from tests import fixture
    return fixture.build_contracts()


def test_карточка_заказа_и_очередь_говорят_одну_дату():
    """Инвариант Д-1а в сборке: прогноз в карточке (ords_det.fcp) = fc пункта очереди = fc строки
    «Недели» — у каждого заказа обоих видов; fcWhy пункта = why карточки."""
    ct = _сборка()
    card = {o["id"]: o for r in ct["rows"] for o in r["orders"]}
    views = [ct] + ([ct["sep"]] if ct.get("sep") and ct["sep"].get("life") else [])
    n_q = n_w = 0
    for V in views:
        for q in V["life"]["queue"]:
            if not q.get("order"):
                continue
            c = card[str(q["order"])]
            ждём = dt.datetime.strptime(q["fc"], "%d.%m.%Y").date().isoformat() if q.get("fc") else ""
            assert (c.get("fcp") or {}).get("date", "") == ждём, q["order"]
            assert bool(q.get("fcWhy")) == bool(ждём), q["order"]
            n_q += 1
        for r in V["ork"]["week"]["orders"]:
            c = card[r["k"]]
            assert (r.get("fc") or "") == (c.get("fcp") or {}).get("date", ""), r["k"]
            n_w += 1
    assert n_q > 20 and n_w > 20


def test_строка_заказа_несёт_даты_файлы_и_точки():
    """Д-5: плановые даты — как в заказе; файл «нет» — только обязательный по ork.art_required
    (сверка с ведомостью: столько же пар «заказ × файл», сколько у «Недели» в artPairs, если она
    их даёт); у каждого живого заказа art по всем ключам каталога artCat."""
    ct = _сборка()
    keys = [a["key"] for a in ct["artCat"]]
    assert keys == [a["key"] for a in ork.ARTIFACTS]
    live = [o for r in ct["rows"] for o in r["orders"] if o["live"]]
    assert live
    n_no = 0
    for o in live:
        assert len(o["art"]) == len(keys) and set(o["art"]) <= {"y", "n", "-"}
        n_no += o["art"].count("n")
        for k in ("shipPlan", "shipFact", "prodStart", "inbFact", "delPlan", "payPlan"):
            if k in o:
                dt.date.fromisoformat(o[k])
        for cp in o.get("cps", []):
            assert cp["kind"] in ("P50", "P14")
            dt.date.fromisoformat(cp["date"])
    assert n_no > 0                                              # корпус даёт заказы без файла
    closed = [o for r in ct["rows"] for o in r["orders"] if not o["live"]]
    assert all("art" not in o and "fcp" not in o for o in closed)
