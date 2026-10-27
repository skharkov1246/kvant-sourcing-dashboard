"""ork.py — шаги регламента ОРК, сегменты S1–S10, «сверх нормы», контрольные точки, гигиена
карточки, контракт показателя. Корпуса придуманы (правило 18): коды стадий и даты свои,
имён людей, компаний и сумм нет."""
from __future__ import annotations

import datetime as dt
import sys
import types
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import ork       # noqa: E402
import period as period_mod       # noqa: E402

D = dt.date
# коды стадий придуманы; имена — как у стадий СП-172 (воронка 26)
ИМЕНА = {
    "S:NEW": "Заказ создан", "S:SENT": "Заказ направлен", "S:PO": "Сформирован РО/Подготовка описаний",
    "S:SPEC": "Подготовка спецификации и инвойса", "S:AGR": "Согласование спецификации и оплаты",
    "S:PAY": "Оплата поставщику / агенту", "S:PROD": "Производство заказа", "S:READY": "Заказ готов",
    "S:PREP": "Подготовка к отгрузке", "S:WH": "Приемка на складе",
    "S:TRUCK": "Подготовка груза/Поиск транспорта", "S:GOT": "Заказ получен клиентом",
    "S:WAIT": "Ожидание оплаты", "S:WIN": "Закрытие заказа", "S:ODD": "Стадия, которой нет в регламенте",
}


def t(day: str, hm: str = "10:00") -> str:
    return f"2026-{day}T{hm}:00+03:00"


def test_таблица_шагов_полна():
    assert [s["no"] for s in ork.STEPS] == list(range(1, 14))
    assert ork.OWNER_RULE == "step"
    assert ork.NORM_WD == {1: 1, 4: 2, 5: 9, 8: 2, 12: 10}
    assert ork.EXECUTOR[1] == "orkHead" and ork.EXECUTOR[11] == "log" and ork.EXECUTOR[13] == "oss"
    assert {ork.EXECUTOR[n] for n in (4, 5, 6, 7, 8, 9, 12)} == {"orkMgr"}
    assert {ork.EXECUTOR[n] for n in (2, 3, 10)} == {"kam"}
    # все 18 стадий заказа покрыты, и ни одна не ведёт на шаг вне 1–13
    assert len(ork.STAGE_STEP["order"]) >= 18
    assert all(1 <= v <= 13 for m in ork.STAGE_STEP.values() for v in m.values())
    # поле обязано быть после шага; платёжка клиента (шаг 13) — у закрытого заказа
    assert all(a["need"] > a["step"] or a["cond"] == "closed" for a in ork.ARTIFACTS)


def test_стадия_в_шаг_пробелы_регистр_двойники():
    assert ork.stage_step("  заказ   ГОТОВ ") == 8
    assert ork.stage_step("Сформирован PO / Подготовка описаний") == 5      # латинские P и O
    assert ork.stage_step("Оплата получена|Закрытие сделки", "deal") == 13
    assert ork.stage_step("Бюджет сделки согласован | подпись поменяли", "deal") == 5
    assert ork.stage_step("") is None and ork.stage_step(None) is None


def test_неизвестная_стадия_None_и_счёт():
    счёт = Counter()
    assert ork.step_of("Стадия, которой нет в регламенте", {"delFact": D(2026, 9, 1)}, unknown=счёт) is None
    assert ork.step_of("Ещё одна", unknown=счёт) is None
    assert sum(счёт.values()) == 2


def test_факты_поднимают_шаг():
    assert ork.step_of("Заказ направлен") == 4
    assert ork.step_of("Заказ направлен", {"supPaidPct": 30.0}) == 6
    assert ork.step_of("Производство заказа", {"inbFact": D(2026, 9, 1)}) == 9
    assert ork.step_of("Заказ готов", {"delFact": D(2026, 9, 1)}) == 11
    assert ork.step_of("Ожидание оплаты", {"delFact": D(2026, 9, 1), "utd": True}) == 13
    # факт не опускает шаг
    assert ork.step_of("Заказ получен клиентом", {"prodConf": True}) == 11


def test_сегменты_по_переходам():
    tr = [("S:NEW", t("09-01")), ("S:SENT", t("09-02")), ("S:PO", t("09-07")),
          ("S:SPEC", t("09-08")), ("S:PAY", t("09-15")), ("S:PROD", t("09-16")),
          ("S:READY", t("10-14")), ("S:PREP", t("10-19"))]
    r = ork.segments(tr, ИМЕНА, t0=t("09-01", "09:00"))
    s = r["segs"]
    assert s["S1"]["wd"] == 1 and s["S1"]["inNorm"] is True             # вт → ср
    assert s["S2"]["wd"] == 3 and s["S2"]["inNorm"] is False            # ср 2 → пн 7
    assert s["S3"]["wd"] == 6 and s["S3"]["norm"] == 9 and s["S3"]["inNorm"] is True
    assert s["S5"]["norm"] is None and s["S5"]["inNorm"] is None and s["S5"]["cd"] == 28.0
    assert s["S6"]["wd"] == 3 and s["S6"]["inNorm"] is False
    assert r["open"] == {"seg": "S7", "start": t("10-19")}
    assert r["returns"] == 0 and not r["burst"] and not r["noCalendar"]
    assert not any(x["ret"] or x["burst"] or x["neg"] for x in s.values())


def test_пропущенная_стадия_не_теряет_сегмент():
    # «Заказ направлен» пропущен: S1 кончается входом сразу на шаг 5, S2 — нулевой и помечен
    # «шаг пропущен» (Рег. 6.13–6.14, Ан. S2): в своевременность он не идёт
    tr = [("S:NEW", t("09-01")), ("S:PO", t("09-03")), ("S:PAY", t("09-10"))]
    s = ork.segments(tr, ИМЕНА)["segs"]
    assert s["S1"]["wd"] == 2 and s["S2"]["wd"] == 0 and s["S3"]["wd"] == 5
    assert s["S2"]["skip"] is True and not s["S1"]["skip"] and not s["S3"]["skip"]
    import orkview
    m1 = orkview._timeliness([s["S2"]], "S2")
    assert m1["skipped"] == 1 and m1["k"] == 0 and m1["state"] == "nodata", "пропуск — не «в норме за 0 р.д.»"


def test_возврат_в_стадию():
    tr = [("S:NEW", t("09-01")), ("S:SENT", t("09-02")), ("S:PO", t("09-03")),
          ("S:SENT", t("09-04")), ("S:PO", t("09-08")), ("S:PAY", t("09-09"))]
    r = ork.segments(tr, ИМЕНА)
    assert r["returns"] == 1 and r["returnPairs"] == [(5, 4)]
    # граница — первый выход на шаг: S2 = 02 → 03, S3 = 03 → 09, в окне S3 был возврат
    assert r["segs"]["S2"]["wd"] == 1 and not r["segs"]["S2"]["ret"]
    assert r["segs"]["S3"]["ret"] is True


def test_пачка_переходов():
    tr = [("S:NEW", t("09-01", "10:00")), ("S:SENT", t("09-01", "10:01")),
          ("S:PO", t("09-01", "10:02")), ("S:PAY", t("09-01", "10:04")), ("S:PROD", t("09-21"))]
    assert ork.bursts([x for _, x in tr]) == {0, 1, 2, 3}
    r = ork.segments(tr, ИМЕНА)
    assert r["burst"] is True
    assert r["segs"]["S2"]["burst"] and r["segs"]["S3"]["burst"] and r["segs"]["S4"]["burst"]
    # три перехода за 6 минут — не пачка
    assert ork.bursts([t("09-01", "10:00"), t("09-01", "10:03"), t("09-01", "10:06")]) == set()


def test_неизвестная_стадия_в_истории():
    tr = [("S:NEW", t("09-01")), ("S:ODD", t("09-02")), ("S:SENT", t("09-03")), ("X:GONE", t("09-04"))]
    r = ork.segments(tr, ИМЕНА)
    assert r["unknown"] == 2 and r["returns"] == 0
    assert r["segs"]["S1"]["wd"] == 2                 # неизвестная стадия границей не служит


def test_год_без_календаря_None():
    tr = [("S:NEW", "2028-03-01T10:00:00+03:00"), ("S:SENT", "2028-03-03T10:00:00+03:00")]
    r = ork.segments(tr, ИМЕНА)
    assert r["segs"]["S1"]["wd"] is None and r["segs"]["S1"]["inNorm"] is None
    assert r["segs"]["S1"]["cd"] == 2.0 and r["noCalendar"] is True
    o = {"stage": "Заказ направлен", "stepSince": "2027-12-20T10:00:00+03:00"}
    v = ork.overrun(o, D(2028, 1, 15))
    assert v["over"] is None and v["wd"] is None and "календар" in v["why"]


def test_упд_по_сырым_датам():
    tr = [("S:GOT", t("09-01")), ("S:WAIT", t("09-02"))]
    r = ork.segments(tr, ИМЕНА, facts={"delFact": D(2026, 9, 1), "utdRaw": D(2026, 9, 16)})
    assert r["segs"]["S9"]["wd"] == 11 and r["segs"]["S9"]["inNorm"] is False
    assert r["open"]["seg"] == "S10"
    # УПД «раньше доставки» — порядок нарушен, в норму не идёт
    r = ork.segments(tr, ИМЕНА, facts={"delFact": D(2026, 9, 10), "utdRaw": D(2026, 9, 1)})
    assert r["segs"]["S9"]["neg"] is True and r["segs"]["S9"]["inNorm"] is False


def test_вход_в_шаг_через_три_стадии():
    tr = [("S:SENT", t("09-01")), ("S:PO", t("09-02")), ("S:SPEC", t("09-10")), ("S:AGR", t("09-14"))]
    assert ork.step_since(tr, ИМЕНА) == (5, t("09-02"))
    assert ork.step_since([], ИМЕНА) == (None, "")


def test_сверх_нормы():
    сегодня = D(2026, 9, 18)          # пятница
    o = {"stage": "Согласование спецификации и оплаты", "stepSince": t("09-02"),
         "stageSince": t("09-14")}
    v = ork.overrun(o, сегодня)
    assert (v["step"], v["role"], v["norm"], v["wd"], v["over"]) == (5, "orkMgr", 9, 12, True)
    assert v["label"] == "без пауз" and v["by"] == 3
    # приёмка — от t0
    v = ork.overrun({"stage": "Заказ создан", "t0": D(2026, 9, 17), "stageSince": t("09-01")}, сегодня)
    assert v["wd"] == 1 and v["over"] is False and v["role"] == "orkHead"
    # производство не нормируется
    v = ork.overrun({"stage": "Производство заказа", "stageSince": t("06-01")}, сегодня)
    assert v["norm"] is None and v["over"] is None and v["why"] == "шаг не нормируется"
    # УПД — 10 р.д. от доставки, на 11-й — сверх нормы
    o = {"stage": "Ожидание оплаты", "delFact": D(2026, 9, 3)}
    assert ork.overrun(o, D(2026, 9, 17))["over"] is False
    assert ork.overrun(o, D(2026, 9, 18))["over"] is True
    assert ork.overrun({"stage": "Нет такой"}, сегодня)["step"] is None


def test_контрольные_точки_недели():
    o = {"stage": "Производство заказа", "prodStart": D(2026, 9, 1), "prodEnd": D(2026, 11, 30),
         "shipPlan": D(2026, 12, 7)}
    # P50 = 01.09 + 45 дн. = 16.10 (пт); P14 = 07.12 − 14 = 23.11 (пн)
    assert ork.checkpoints(o, D(2026, 10, 14)) == [{"kind": "P50", "date": "2026-10-16"}]
    assert ork.checkpoints(o, D(2026, 11, 29)) == [{"kind": "P14", "date": "2026-11-23"}]
    assert ork.checkpoints(o, D(2026, 10, 19)) == []
    # производство ≤ 30 дн. — точки «за 14 дней» нет; P14 от конца производства без shipPlan
    short = {"prodStart": D(2026, 10, 1), "prodEnd": D(2026, 10, 21)}
    assert ork.checkpoints(short, D(2026, 10, 7)) == [{"kind": "P50", "date": "2026-10-11"}]
    assert ork.checkpoints({**o, "shipPlan": None}, D(2026, 11, 16)) == [{"kind": "P14", "date": "2026-11-16"}]
    # готов или отгружен — контроль производства пройден
    assert ork.checkpoints({**o, "stage": "Заказ готов"}, D(2026, 10, 14)) == []
    assert ork.checkpoints({**o, "shipFact": D(2026, 10, 1)}, D(2026, 10, 14)) == []
    assert ork.checkpoints({"prodStart": D(2026, 10, 1)}, D(2026, 10, 7)) == []


def test_гигиена_стадия_позади_фактов_и_брошенная():
    сегодня = D(2026, 10, 26)
    h = ork.hygiene({"stage": "Заказ направлен", "shipFact": D(2026, 9, 1)}, сегодня)
    assert h["behind"] and h["exclude"] and not h["abandoned"]
    h = ork.hygiene({"stage": "Заказ создан", "t0": D(2025, 10, 1)}, сегодня)
    assert h["abandoned"] and h["exclude"] and not h["behind"]
    h = ork.hygiene({"stage": "Заказ создан", "t0": D(2026, 10, 20)}, сегодня)
    assert not h["exclude"]
    # производство без нормы — сколько бы ни стоял, не «брошенная»
    assert not ork.hygiene({"stage": "Производство заказа", "stageSince": t("01-10")}, сегодня)["exclude"]
    h = ork.hygiene({"stage": "Нечто"}, сегодня)
    assert h["unknown"] and not h["exclude"]


def test_контракт_показателя():
    g = ork.GATES
    assert ork.metric_state(None, 40, 0.9, src="история стадий", need="поле «Пауза»") == {
        "state": "nodata", "value": None, "n": 40, "cover": 0.9, "why": "нет данных",
        "src": "история стадий", "need": "поле «Пауза»"}
    assert ork.metric_state(0.5, 0, 0.9)["state"] == "nodata"          # ноль за неизвестное не выдаём
    assert ork.metric_state(0.0, 40, 0.9)["state"] == "show"           # настоящий ноль — показываем
    assert ork.metric_state(0.9, 40, 0.85)["state"] == "show"
    v = ork.metric_state(0.9, 40, 0.6)
    assert v["state"] == "prelim" and v["value"] == 0.9 and "покрытие 60 %" in v["why"]
    assert ork.metric_state(0.9, 12, 0.9)["state"] == "prelim"
    assert ork.metric_state(0.9, 12, 0.9, n_key="n_manager_month")["state"] == "show"
    for kw in ({"cover": 0.4}, {"agree": 0.8}, {"burst": 0.2}, {"plan_eq_fact": 0.35}):
        v = ork.metric_state(0.9, 40, kw.pop("cover", 0.9), **kw)
        assert v["state"] == "hidden" and v["value"] is None and v["why"].startswith("не показываем")
    assert g["n_supplier_quarter"] == 5 and g["n_cell"] == 8 and ork.GATES_NOTE


def test_хозяин_по_шагу():
    люди = {"orkMgr": "", "dealOrk": "сотрудник#2", "orkHead": "", "kam": "сотрудник#3",
            "ossOrder": "", "ossDeal": "сотрудник#4"}
    assert ork.owner(1, люди) == {"role": "orkHead", "who": "руководитель ОРК", "copy": "сотрудник#3",
                                  "copyRole": "kam"}
    assert ork.owner(5, люди)["who"] == "сотрудник#2"            # не из ОРК — поле сделки
    assert ork.owner(5, {**люди, "dealOrk": ""})["who"] == "не назначен"
    assert ork.owner(11, люди)["who"] == "логистика"
    assert ork.owner(13, люди)["who"] == "сотрудник#4"
    долг = ork.owner(13, люди, debt=True)
    assert (долг["role"], долг["who"], долг["copyRole"], долг["copy"]) == ("kam", "сотрудник#3", "oss", "сотрудник#4")
    assert ork.owner(12, люди)["copyRole"] == "kam"
    assert ork.owner(7, люди, esc=None)["copy"] == ""


def test_календарь_покрывает_историю():
    # история стадий — с 2024-06-01 (contracts.HIST_SINCE) по следующий год
    years = period_mod.workcal_years()
    assert all(y in years for y in range(2024, 2027 + 1))


def test_ожидание_оплаты_с_упд_шаг_13_не_гигиена():
    """«Ожидание оплаты» — шаги 12 и 13 (STAGE_SPAN): с подписанным УПД живой заказ на шаге
    13, и это не «стадия позади фактов» — закрывать заказ до оплаты клиента не надо."""
    сегодня = D(2026, 10, 26)
    o = {"stage": "Ожидание оплаты", "delFact": D(2026, 10, 1), "utd": True}
    assert ork.step_of(o["stage"], o) == 13
    assert not ork.hygiene(o, сегодня)["behind"] and not ork.hygiene(o, сегодня)["exclude"]
    позади = {"stage": "Заказ получен клиентом", "delFact": D(2026, 10, 1), "utd": True}
    assert ork.hygiene(позади, сегодня)["behind"], "до «Ожидания оплаты» с УПД — стадия отстала"


def test_пункты_чатов_и_договоров_по_таблице():
    ppl = {"orkMgr": "Илья (ОРК)", "kam": "Анна (КАМ)", "orkHead": "Роза (рук. ОРК)", "ossDeal": "Ольга (ОСС)"}
    assert ork.item_owner("chatDue", 7, ppl, key="chatDue:оплата")["who"] == "Анна (КАМ)"
    assert ork.item_owner("chatDue", 7, ppl, key="chatDue:отгрузка")["who"] == "Илья (ОРК)"
    other = ork.item_owner("chatDue", 7, ppl, key="chatDue:встреча")
    assert (other["role"], other["step"], other["copy"]) == ("orkMgr", 7, "Анна (КАМ)")
    assert ork.item_owner("contractRisk", 5, ppl)["who"] == "Роза (рук. ОРК)"
    both = ork.item_owner("contractMissing", 5, ppl, key="contractMissing:both")
    assert (both["role"], both["copyRole"], both["step"]) == ("kam", "orkMgr", 2)
    assert all("oss" not in (r, c) for k, (_s, r, c) in ork.ITEM.items() if k.startswith("chat"))


# ── одно правило шага 12 и «сверх нормы» (ревизия 2) ───────────────────────────────────────
def test_доставлено_без_упд_шаг_12_при_любой_стадии():
    """«Заказ получен клиентом», «Заказ отгружен» и «Ожидание оплаты» с фактом доставки и без
    УПД — один шаг 12, один исполнитель (менеджер ОРК) и один срок от доставки: 20 р.д. против
    10 — сверх нормы; УПД есть — шаг 13."""
    сегодня = D(2026, 10, 19)
    for стадия in ("Заказ получен клиентом", "Заказ отгружен", "Ожидание оплаты", "Стадия, которой нет"):
        o = {"stage": стадия, "delFact": D(2026, 9, 21), "utd": False, "stageSince": t("10-01")}
        assert ork.step_now(o) == 12, стадия
        v = ork.judged(o, сегодня)
        assert (v["step"], v["role"], v["norm"], v["wd"], v["over"], v["counted"]) == (
            12, "orkMgr", 10, 20, True, True), стадия
        assert ork.owner(v["step"], {"orkMgr": "Илья (ОРК)", "kam": "Анна (КАМ)"})["who"] == "Илья (ОРК)"
    assert ork.step_now({"stage": "Заказ получен клиентом", "delFact": D(2026, 9, 21), "utd": True}) == 13
    assert ork.step_now({"stage": "Заказ получен клиентом"}) == 11, "без доставки — шаг стадии"
    assert ork.step_now({"delFact": D(2026, 9, 21)}) == 12, "стадии нет — по фактам"
    # стадия не из таблицы: доставлено без УПД — шаг 12, имя в «не из таблицы» не идёт;
    # без доставки — None и имя в счёте (week.unknownStages ≤ week.unknown)
    unk = Counter()
    assert ork.step_now({"stage": "Своя стадия", "delFact": D(2026, 9, 21)}, unknown=unk) == 12 and not unk
    assert ork.step_now({"stage": "Своя стадия"}, unknown=unk) is None and sum(unk.values()) == 1


def test_старая_доставка_без_упд_гигиена_а_не_в_норме():
    """Доставлено больше CLOSE_DAYS дней назад, УПД нет: не «в норме» и не «сверх нормы», а
    гигиена «старая доставка без УПД» — вне счёта (одно правило с окном очереди)."""
    сегодня = D(2026, 10, 26)
    o = {"stage": "Ожидание оплаты", "delFact": сегодня - dt.timedelta(days=400), "utd": False}
    h = ork.hygiene(o, сегодня)
    assert h["old"] and h["exclude"] and "старая доставка без УПД" in h["why"]
    v = ork.judged(o, сегодня)
    assert v["rawOver"] is True and v["over"] is None and v["hyg"] and not v["counted"]
    свежий = {**o, "delFact": сегодня - dt.timedelta(days=30)}
    assert ork.judged(свежий, сегодня)["over"] is True and not ork.hygiene(свежий, сегодня)["old"]
    # стадия позади фактов срок УПД не портит: он от факта доставки
    позади = {"stage": "Заказ создан", "delFact": сегодня - dt.timedelta(days=30), "utd": False}
    assert ork.hygiene(позади, сегодня)["behind"] and ork.judged(позади, сегодня)["over"] is True


def test_приёмка_вне_реализации_не_в_счёте():
    v = ork.judged({"stage": "Заказ создан", "t0": D(2026, 9, 1)}, D(2026, 10, 1), in_cat0=False)
    assert v["rawOver"] is True and v["over"] is None and not v["counted"] and "Реализация" in v["why"]
    assert ork.judged({"stage": "Заказ создан", "t0": D(2026, 9, 28)}, D(2026, 10, 1))["over"] is True


def test_норма_шага_6_нет_данных_а_не_не_нормируется():
    """Рег. 8.2.6 и Ан. S4: шаг 6 — 1–2 р.д. после OC и оплаты; дат в заказе нет — «нет
    данных». «Не нормируется» — только шаги 2–3, 7, 9–11, 13."""
    assert ork.NORM_DOC[6][:2] == ("1–2 р.д.", "nodata") and "Order confirmation" in ork.NORM_DOC[6][2]
    assert {n for n, v in ork.NORM_DOC.items() if v[1] == "none"} == {2, 3, 7, 9, 10, 11, 13}
    assert {n for n, v in ork.NORM_DOC.items() if v[1] == "norm"} == set(ork.NORM_WD)
    v = ork.overrun({"stage": "Оплата поставщику / агенту", "stageSince": t("09-01")}, D(2026, 9, 18))
    assert v["step"] == 6 and v["over"] is None and v["normState"] == "nodata" and "нет данных" in v["why"]
    assert ork.overrun({"stage": "Производство заказа"}, D(2026, 9, 18))["why"] == "шаг не нормируется"


def test_брошенная_до_календаря():
    """Вход в шаг раньше первого года календаря — нижняя граница р.д. от 1 января: «брошенная»,
    а не «срок неизвестен»."""
    y0 = min(__import__("period").workcal_years())
    o = {"stage": "Заказ направлен", "stepSince": f"{y0 - 1}-06-01T10:00:00+03:00"}
    h = ork.hygiene(o, D(y0, 12, 1))
    assert h["abandoned"] and h["exclude"]


# ── артефакт требуется только у заказов, прошедших шаг после даты создания поля ─────────────
def test_артефакт_только_после_даты_создания_поля():
    a = {x["key"]: x for x in ork.ARTIFACTS}
    pro, oc, po = a["proforma"], a["oc"], a["po"]            # поля проформы и OC — с 24.08.2026
    base = {"stage": "Оплата поставщику / агенту", "prepayShare": 30, "created": D(2026, 6, 1)}
    # шаг 6 пройден до создания поля — не требуется; после — требуется
    assert not ork.art_required(base, pro, {6: "2026-08-20T10:00:00+03:00"})
    assert ork.art_required(base, pro, {6: "2026-09-02T10:00:00+03:00"})
    # перехода на шаг в истории нет: заказ старше поля — неизвестно, не требуем; моложе — требуем
    assert not ork.art_required(base, pro, {})
    assert ork.art_required({**base, "created": D(2026, 9, 1)}, pro, {})
    assert not ork.art_required({**base, "created": None}, pro, {})
    # шаг ещё не пройден — не требуется при любой дате
    assert not ork.art_required({**base, "stage": "Согласование спецификации и оплаты"}, pro,
                                {6: "2026-09-02T10:00:00+03:00"})
    # OC — только у импорта: «импорт/РФ» не сверен — не требуется; импорт — требуется
    assert not ork.art_required(base, oc, {6: "2026-09-02T10:00:00+03:00"})
    assert not ork.art_required({**base, "import": False}, oc, {6: "2026-09-02T10:00:00+03:00"})
    assert ork.art_required({**base, "import": True}, oc, {6: "2026-09-02T10:00:00+03:00"})
    # старое поле (Purchase order, 2024) — у заказа, прошедшего шаг в 2025-м, требуется
    assert ork.art_required(base, po, {6: "2025-03-01T10:00:00+03:00"})
    assert not ork.art_required(base, po, {6: "2024-07-01T10:00:00+03:00"})


def test_ведомость_нет_артефактов_по_дате_поля():
    """Ведомость считает «Нет артефактов» тем же правилом: заказ, прошедший шаг 6 до создания
    поля проформы, проформу не обязан."""
    import lifecycle
    import orkview
    T = D(2026, 10, 26)
    def o(i, at):
        return {"id": str(i), "deal": "D1", "seq": 1, "live": True, "stage": "Производство заказа",
                "stepSince": at, "stageSince": at, "t0": at, "created": D(2026, 6, 1), "supPaidPct": 100,
                "prepayShare": 50, "utd": False, "art": {}, "_reach": {6: at, 7: at}, "_segs": {},
                "orkMgr": "М", "respOrk": True}
    orders = [o(1, "2026-08-10T10:00:00+03:00"), o(2, "2026-09-10T10:00:00+03:00")]
    deals = [{"deal": "D1", "seq": 1, "customer": "К", "closed": False, "lost": False, "inCat0": True,
              "norders": 2, "cat0At": "2026-06-01T10:00:00+03:00"}]
    life = lifecycle.build(orders, deals, T)
    v, _ = orkview.build(orders, deals, life["queue"], T)
    led5 = next(r for r in v["week"]["ledger"] if r["no"] == 5)
    # шаг 5: PO и спецификации — у обоих (поля 2024 г.), проформа — только у второго, OC — ни у кого
    assert (led5["artReq"], led5["artMiss"]) == (5, 5)
