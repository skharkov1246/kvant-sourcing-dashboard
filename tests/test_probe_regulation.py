"""Зонд v60 «Реализация по регламенту ОРК»: что дало бы неверный вывод или утечку.

1. Журнал публичный: ни номеров сделок, заказов, компаний, задач, ни сумм, ни имён, ни
   подписей с юрлицом, ни текста отказа портала. Первая строка — «ожидается: …»,
   последняя — сводка_нагрузки().
2. Части: 10/25/50, меньше 10 — 10; разбиение одно — по ID сделки, все заказы сделки в
   одной части; упавшая часть — «частей прочитано k из n», её когорта уходит из выводов.
3. Смета больше предела — прогон не начинается; часть, которой не хватает сметы, не
   читается вслух.
4. Граница шага у зонда та же, что у ork.segments (две реализации сверяются).
5. Отказ tasks.* — одна строка с кодом, без описания.
6. «Дата оплаты счета»: план / факт / неясно — по таблице порогов.
7. probe.yml: зонд в закрытом списке `script`, части — вход прогона.

Корпус придуман (правило 18): номера и суммы нарочно особые.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kvant_probe_regulation", ROOT / "scripts" / "probe_regulation.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
З, Д = m.З, m.Д

СЕГОДНЯ = dt.date(2026, 10, 26)
СУММЫ = ("9876543", "9 876 543", "9876543.21", "2718281")
НОМЕРА = ("7710001", "7710002", "7710003", "7710009", "7730001", "7730002", "7730003", "7730004", "7730005",
          "7730006", "7720001", "7720002", "7740001", "7750001", "7750002")
СЛОВА = ("Альфа", "Омега", "Ромашка", "Бета", "Иванов", "Учебн", "Доступ запрещён", "http")


def t(s: str) -> str:
    return s if "T" in s else s + "T12:00:00+03:00"


# ── придуманный портал ──────────────────────────────────────────────────────

ДЕАЛ_СТАДИИ = {
    "NEW": "ТКП согласован (конкурс выигран) | Согласование договора/спецификации с заказчиком",
    "PREPARATION": "Договор с заказчиком подписан | Согласование спецификаций/инвойсов с агентами/поставщиками",
    "UC_PAY": "Оплата товара произведена | Начато производство",
    "UC_SHIP": "Товар отгружен | Доставка клиенту",
    "UC_DEL": "Доставка клиенту произведена | Ожидание оплаты",
    "UC_PAID": "Оплата получена | Закрытие сделки",
}
_ЗС = (("NEW", "Заказ создан", "P"), ("PREPARATION", "Заказ направлен", "P"),
       ("CLIENT", "Сформирован PO/Подготовка описаний", "P"), ("UC_SPEC", "Согласование спецификации и оплаты", "P"),
       ("UC_PAY", "Оплата поставщику / агенту", "P"), ("UC_PROD", "Производство заказа", "P"),
       ("UC_READY", "Заказ готов", "P"), ("UC_PREP", "Подготовка к отгрузке", "P"), ("UC_CUST", "Таможня", "P"),
       ("UC_TRANS", "Подготовка груза/Поиск транспорта", "P"), ("UC_SHIPPED", "Заказ отгружен", "P"),
       ("UC_RECV", "Заказ получен клиентом", "P"), ("UC_WAIT", "Ожидание оплаты", "P"),
       ("SUCCESS", "Закрытие заказа", "S"), ("FAIL", "Провал", "F"))
ЗАКАЗ_МЕТА = {f"DT172_26:{k}": {"name": n, "sem": s} for k, n, s in _ЗС}
СП_МЕТА = {
    137: {"DT137_10:NEW": {"name": "Новый", "sem": "P"}, "DT137_10:SUCCESS": {"name": "Оплачен", "sem": "S"}},
    1174: {"DT1174_108:NEW": {"name": "Новый документ", "sem": "P"},
           "DT1174_108:UC_A": {"name": "Согласование с ООО «Ромашка»", "sem": "P"},
           "DT1174_108:SUCCESS": {"name": "Подписание и архивация", "sem": "S"}},
    1192: {"DT1192_116:NEW": {"name": "Новый документ", "sem": "P"},
           "DT1192_116:SUCCESS": {"name": "Подписание и архивация", "sem": "S"}},
    1066: {"DT1066_30:NEW": {"name": "Проверка", "sem": "P"}, "DT1066_30:SUCCESS": {"name": "Надёжен", "sem": "S"}},
}
КАТ = {137: 10, 1174: 108, 1192: 116, 1066: 30}


def сделка(i, stage, sem="P", cat="0", **к):
    d = {"ID": i, "TITLE": "Учебная поставка Альфа", "CATEGORY_ID": cat, "STAGE_ID": stage, "STAGE_SEMANTIC_ID": sem,
         "DATE_CREATE": t("2025-01-10"), "ASSIGNED_BY_ID": "77", "OPPORTUNITY": "9876543.21"}
    d.update(к)
    return d


СДЕЛКИ = [
    сделка("7710001", "UC_PAY", **{Д["orkField"]: "31", Д["endPoint"]: "11", Д["supContract"]: t("2026-09-04"),
                                    Д["moved"]: t("2026-08-30")}),
    сделка("7710002", "UC_DEL", **{Д["endPoint"]: "11"}),
    сделка("7710003", "NEW", **{Д["endPoint"]: "12"}),
    сделка("7710004", "LOSE", sem="F"),
]
СДЕЛКИ_ВНЕ = {"7710009": сделка("7710009", "C2:NEW", cat="2", **{Д["orkField"]: "31"})}


def заказ(i, deal, stage, created, **к):
    o = {"id": int(i), "title": "Заказ Омега", "parentId2": deal, "stageId": f"DT172_26:{stage}",
         "createdTime": t(created), "movedTime": t(created), "assignedById": 31, "companyId": 7720001,
         "opportunity": 9876543.21, З["dir"]: "1"}
    o.update(к)
    return o


ЗАКАЗЫ = [
    заказ("7730001", 7710001, "UC_PROD", "2026-09-01T09:00:00+03:00",
          **{З["supPaid"]: "50", З["prodStart"]: t("2026-09-20"), З["prodEnd"]: t("2026-11-20"),
             З["dl"]: t("2026-12-31"), З["respDate"]: t("2026-09-02"), З["taskAssign"]: "7740001",
             З["firstPay"]: "100"}),
    заказ("7730002", 7710001, "SUCCESS", "2025-03-10",
          **{З["prodEnd"]: t("2025-05-20"), З["shipPlan"]: t("2025-05-25"), З["shipFact"]: t("2025-05-25"),
             З["delFact"]: t("2025-06-20"), З["utdDate"]: t("2025-07-01"), З["dir"]: "2"}),
    заказ("7730003", 7710002, "NEW", "2025-01-15", assignedById=32),
    заказ("7730004", 7710002, "UC_WAIT", "2026-05-05",
          **{З["delFact"]: t("2026-09-01"), З["utdFile"]: [{"id": 1}], З["dl"]: t("2026-08-01")}),
    заказ("7730005", 7710003, "UC_PREP", "2026-08-25", companyId=7720002),
    заказ("7730006", 7710009, "PREPARATION", "2026-10-20", assignedById=41),
    заказ("7730007", 7710002, "FAIL", "2026-02-01"),
    заказ("7730008", 0, "NEW", "2026-03-01"),
]


def _ход(oid, шаги):
    return [{"ID": f"{oid}{i}", "OWNER_ID": oid, "STAGE_ID": s, "CREATED_TIME": t(c)} for i, (s, c) in enumerate(шаги)]


ИСТОРИЯ = {172: (
    _ход("7730001", [("DT172_26:NEW", "2026-09-01T10:00:00+03:00"), ("DT172_26:PREPARATION", "2026-09-02"),
                     ("DT172_26:CLIENT", "2026-09-04"), ("DT172_26:UC_SPEC", "2026-09-10"),
                     ("DT172_26:UC_PAY", "2026-09-15"), ("DT172_26:UC_PROD", "2026-09-20")])
    + _ход("7730002", [("DT172_26:NEW", "2025-03-10"), ("DT172_26:PREPARATION", "2025-03-11"),
                       ("DT172_26:CLIENT", "2025-03-12"), ("DT172_26:UC_PAY", "2025-03-25"),
                       ("DT172_26:UC_PROD", "2025-03-27"), ("DT172_26:UC_READY", "2025-05-20"),
                       ("DT172_26:UC_PREP", "2025-05-22"), ("DT172_26:UC_CUST", "2025-05-30"),
                       ("DT172_26:UC_TRANS", "2025-06-10"), ("DT172_26:UC_SHIPPED", "2025-06-12"),
                       ("DT172_26:UC_RECV", "2025-06-20"), ("DT172_26:UC_WAIT", "2025-06-21"),
                       ("DT172_26:SUCCESS", "2025-07-10")])
    + _ход("7730003", [("DT172_26:NEW", "2025-01-15")])
    + _ход("7730004", [("DT172_26:NEW", "2026-05-05"), ("DT172_26:PREPARATION", "2026-05-06"),
                       ("DT172_26:UC_PAY", "2026-05-20"), ("DT172_26:UC_PROD", "2026-05-21"),
                       ("DT172_26:UC_READY", "2026-07-20"), ("DT172_26:UC_PREP", "2026-07-21"),
                       ("DT172_26:UC_TRANS", "2026-08-20"), ("DT172_26:UC_RECV", "2026-09-01"),
                       ("DT172_26:UC_WAIT", "2026-09-02")])
    + _ход("7730005", [("DT172_26:NEW", "2026-08-25"), ("DT172_26:PREPARATION", "2026-08-26"),
                       ("DT172_26:CLIENT", "2026-08-27"), ("DT172_26:UC_PAY", "2026-09-01"),
                       ("DT172_26:UC_PROD", "2026-09-02"), ("DT172_26:UC_READY", "2026-10-01T10:00:00+03:00"),
                       ("DT172_26:UC_PROD", "2026-10-01T10:01:00+03:00"),
                       ("DT172_26:UC_READY", "2026-10-01T10:02:00+03:00"), ("DT172_26:UC_PREP", "2026-10-05")])
    + _ход("7730006", [("DT172_26:NEW", "2026-10-20"), ("DT172_26:PREPARATION", "2026-10-21")])
), 2: (
    _ход("7710001", [("NEW", "2026-08-30"), ("PREPARATION", "2026-09-03"), ("UC_PAY", "2026-09-16")])
    + _ход("7710002", [("NEW", "2025-01-12"), ("UC_DEL", "2026-09-02")])
    + _ход("7710003", [("NEW", "2026-08-24"), ("UC_PAY", "2026-09-05"), ("NEW", "2026-09-06")])
), 137: _ход("7750001", [("DT137_10:NEW", "2026-09-01"), ("DT137_10:SUCCESS", "2026-09-07")])}

СП = {
    137: [{"id": 7750001, "stageId": "DT137_10:SUCCESS", "createdTime": t("2026-09-01"), "movedTime": t("2026-09-07"),
           m.INV_TYPE: "21", m.INV_PAY_DATE: t("2026-09-07"), "parentId2": 7710001, m.INV_ORDERS: [7730001]},
          {"id": 7750002, "stageId": "DT137_10:NEW", "createdTime": t("2026-10-01"), "movedTime": t("2026-10-01"),
           m.INV_TYPE: "22", m.INV_PAY_DATE: t("2026-11-10")}],
    1174: [{"id": 1, "stageId": "DT1174_108:UC_A", "parentId2": 7710001, "parentId172": 7730001, "companyId": 7720001},
           {"id": 2, "stageId": "DT1174_108:SUCCESS", "parentId2": 0, "companyId": 0}],
    1192: [{"id": 3, "stageId": "DT1192_116:SUCCESS", "companyId": 7720001}],
    1066: [{"id": 4, "stageId": "DT1066_30:SUCCESS", "companyId": 0, m.CHECK_COMPANY: "CO_7720001"}],
}


class Фейк:
    """Придуманный портал: ровно те методы, что зовёт зонд."""

    def __init__(self, падать_на_владельце=None, задачи_отказ=False, всего_заказов=None):
        self.падать_на_владельце = падать_на_владельце
        self.задачи_отказ = задачи_отказ
        self.всего_заказов = всего_заказов
        self.истории = []

    def _сделки(self, f):
        out = [d for d in СДЕЛКИ if str(d["CATEGORY_ID"]) == str(f.get("CATEGORY_ID", d["CATEGORY_ID"]))]
        if f.get("!STAGE_SEMANTIC_ID"):
            out = [d for d in out if d["STAGE_SEMANTIC_ID"] != f["!STAGE_SEMANTIC_ID"]]
        return out

    def call_envelope(self, method, params=None):
        params = params or {}
        f = params.get("filter") or {}
        if method == "crm.deal.list":
            r = self._сделки(f)
            return {"result": r, "total": len(r)}
        if method == "crm.item.list":
            return {"result": {"items": []}, "total": self.всего_заказов or len(ЗАКАЗЫ)}
        if method == "crm.stagehistory.list":
            e = params["entityTypeId"]
            свои = {str(x) for x in f["OWNER_ID"]}
            if self.падать_на_владельце and self.падать_на_владельце in свои:
                raise ValueError("заказ 7730001 на 9876543.21 — текст не должен попасть в журнал")
            self.истории.append((e, sorted(свои)))
            r = [x for x in ИСТОРИЯ.get(e, ()) if x["OWNER_ID"] in свои
                 and ("CATEGORY_ID" not in f or m.pc.воронка_стадии(x["STAGE_ID"]) == str(f["CATEGORY_ID"]))]
            return {"result": {"items": r}, "total": len(r)}
        if method == "tasks.task.list":
            if self.задачи_отказ == "лимит":
                raise m.bc.BitrixLimitError("tasks.task.list: портал держит лимит дольше бюджета ожидания")
            if self.задачи_отказ:
                raise m.bc.BitrixError("tasks.task.list: ACCESS_DENIED Доступ запрещён Иванову")
            ids = {str(x) for x in f["ID"]}
            r = [{"id": "7740001", "closedDate": t("2026-09-02"), "createdDate": t("2026-09-01"), "status": "5"}]
            return {"result": {"tasks": [x for x in r if x["id"] in ids]}, "total": 1}
        raise AssertionError(f"неожиданный метод {method}")

    def call(self, method, params=None):
        params = params or {}
        if method == "crm.item.fields":
            e = params["entityTypeId"]
            if e == 172:
                return {"fields": {З["dir"]: {"items": [{"ID": "1", "VALUE": "Импорт"}, {"ID": "2", "VALUE": "РФ"}]}}}
            if e == 137:
                return {"fields": {m.INV_TYPE: {"items": [{"ID": "21", "VALUE": "Предоплата"},
                                                          {"ID": "22", "VALUE": "Оплата Бета LLC"}]}}}
            return {"fields": {}}
        if method == "crm.deal.fields":
            return {Д["endPoint"]: {"items": [{"ID": "11", "VALUE": "Склад"},
                                             {"ID": "12", "VALUE": "ООО «Омега» напрямую"}]}}
        if method == "crm.category.list":
            return {"categories": [{"id": КАТ[params["entityTypeId"]]}]}
        raise AssertionError(f"неожиданный метод {method}")

    def deal_stages_cat(self, cat=0):
        return dict(ДЕАЛ_СТАДИИ)

    def spa_stage_meta(self, entity, cid):
        return dict(ЗАКАЗ_МЕТА if entity == 172 else СП_МЕТА[entity])

    def list_items(self, entity, *, filter=None, select=None):
        return [dict(x) for x in (ЗАКАЗЫ if entity == 172 else СП[entity])]

    def list_deals_fast(self, *, filter=None, select=None, max_items=None):
        return self._сделки(filter or {})

    def deals_by_ids(self, ids, *, select=None):
        return {i: СДЕЛКИ_ВНЕ[i] for i in ids if i in СДЕЛКИ_ВНЕ}

    def departments(self):
        return [{"ID": 50, "NAME": "Отдел реализации контрактов", "UF_HEAD": 30},
                {"ID": 51, "NAME": "Группа по работе с ПАО «Альфа»", "UF_HEAD": 1}]

    def dept_member_ids(self, dept_id, include_children=True):
        return {"30", "31", "32"} if int(dept_id) == 50 else set()


@pytest.fixture(autouse=True)
def _части(monkeypatch):
    monkeypatch.delenv("PROBE_PARTS", raising=False)


def без_утечек(out: str) -> None:
    for x in СУММЫ + НОМЕРА + СЛОВА:
        assert x not in out, x
    assert "LLC" not in out and "ООО" not in out


# ── журнал целиком ──────────────────────────────────────────────────────────

def test_журнал_только_агрегаты(capsys):
    код = m.main(Фейк(), СЕГОДНЯ)
    out = capsys.readouterr().out
    без_утечек(out)
    строки = out.splitlines()
    assert строки[0].startswith("ожидается: сделок кат. 0 (не проигранных) 3, заказов СП-172 8, частей 10")
    assert строки[-1].startswith("портал: запросов")
    assert код == 0
    assert "частей прочитано 4 из 4" in out
    assert "проигранных 1" in out and "без сделки (только счёт) 1" in out
    # календарь покрывает историю
    assert "не покрыто ничего; заказов с сегментом без календаря 0" in out
    # подписи: из белого списка — как есть, с юрлицом — номером
    assert "Направление поставки (заказ): Импорт 5 · РФ 1" in out
    assert "Supply end point (сделка):" in out and "вариант 2" in out and "Склад" in out
    assert "«Тип оплаты»: Предоплата 1 · вариант 2 1" in out
    assert "(имя скрыто)" in out                              # стадия СП-1174 с юрлицом
    # порядок: возврат и «пачка» у одного заказа, стадия сделки позади её заказа
    assert "возвраты на шаг раньше 1 из 6" in out and "шаг 8 → 7 (1)" in out
    assert "«пачка» (≥ 3 переходов за 5 мин) 1 из 6" in out
    assert "стадия сделки впереди всех заказов 0, позади 1" in out
    # оплата поставщику раньше договора с заказчиком: у трёх из четырёх оплаченных заказов
    # сделка вошла в «Договор подписан» (или дальше) позже выхода заказа на шаг 6
    assert "«Договор с заказчиком подписан» 3 из 4" in out
    # «Таможня» по направлению
    assert "РФ: прошли 1 из 1" in out
    # менеджер ОРК: один живой заказ у человека не из отдела, поле сделки не совпало
    assert "все живые: 5; ответственный не из отдела ОРК 1 из 5 (20 %)" in out
    assert "совпадает с ответственным заказа 1 из 2" in out
    # УПД: файл без даты не идёт в noUtd
    assert "UTD_DATE пусто, а файл УПД есть: 1 заказов" in out
    assert "по регламенту > 10 р.д. — 0" in out
    # «план = факт»
    assert "PROD_END = день входа в «Заказ готов»: 1 из 1" in out and "SHIP_PLAN = SHIP_FACT: 1 из 1" in out
    # СП-137: оба способа сходятся, но поле заполнено у неоплаченного (в будущем) — план
    assert "≤ 1 р.д. 100 %, поле позже movedTime 0" in out and "вывод: похоже на план" in out
    # договоры и проверка контрагента: связь через поле «CO_…»
    assert "СП-1066: карточек 1" in out and "компания (поле) 1 из 1" in out
    # задачи читаются
    assert "tasks.*: вебхук читает задачи" in out and "закрыта 1 из 1" in out
    # вердикты: эскалаций нет в портале — НЕТ ДАННЫХ; малые n — PRELIM, не SHOW
    assert "W4 открытые эскалации и паузы: НЕТ ДАННЫХ" in out
    assert "S5 сегмент" in out and "ПРЕДВАРИТЕЛЬНО" not in out
    assert "PRELIM" in out and "пороги — предложение, ждут решения владельца" in out
    assert "НАГРУЗКА ПО БЛОКАМ" in out


def test_упавшая_часть_и_когорта(capsys):
    код = m.main(Фейк(падать_на_владельце="7730005"), СЕГОДНЯ)
    out = capsys.readouterr().out
    без_утечек(out)
    assert "сбой ValueError" in out and "probe_regulation.py:" in out
    assert "частей прочитано 3 из 4" in out
    assert "когорты, задетые упавшими частями, вне выводов: 2026-П2" in out
    assert "по полугодиям создания: 2025-П1 2 · 2026-П1 1\n" in out + "\n"   # 2026-П2 ушло целиком
    assert out.splitlines()[-1].startswith("портал: запросов")
    assert код == 1


def test_смета_больше_предела_прогон_не_начат(capsys):
    код = m.main(Фейк(всего_заказов=9000), СЕГОДНЯ)
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("ожидается:") and "прогон не начат" in out
    assert код == 1


def test_часть_вне_сметы_не_читается(monkeypatch, capsys):
    monkeypatch.setattr(m.Учёт, "сейчас", lambda self: m.БЮДЖЕТ["предел"] - 5)
    ф = Фейк()
    код = m.main(ф, СЕГОДНЯ)
    out = capsys.readouterr().out
    assert "не читается — смета" in out and "частей прочитано 0 из 4" in out
    assert not ф.истории and код == 1


def test_отказ_задач_одной_строкой(capsys):
    m.main(Фейк(задачи_отказ=True), СЕГОДНЯ)
    out = capsys.readouterr().out
    без_утечек(out)
    assert out.count("tasks.*: отказ портала (ACCESS_DENIED)") == 1
    assert "отказ портала (ACCESS_DENIED): задачи не читаются" in out


def test_лимит_на_задачах_не_отказ_в_праве(capsys):
    код = m.main(Фейк(задачи_отказ="лимит"), СЕГОДНЯ)
    out = capsys.readouterr().out
    assert "отказ портала" not in out and "сбой BitrixLimitError" in out
    assert "частей прочитано 3 из 4" in out and код == 1


# ── части ───────────────────────────────────────────────────────────────────

def test_части_входом(monkeypatch):
    for сырое, ждём in (("3", 10), ("10", 10), ("25", 25), ("50", 50), ("12", 25), ("90", 50), ("x", 10)):
        monkeypatch.setenv("PROBE_PARTS", сырое)
        n, замечание = m.частей()
        assert n == ждём, сырое
        assert bool(замечание) == (сырое not in ("10", "25", "50"))


def test_разбиение_по_сделке_смежными_срезами():
    части = m.разбить(["10", "3", "2", "11", "3"], 2)
    assert части == [["2", "3"], ["10", "11"]]
    assert m.разбить([], 10) == []
    assert len(m.разбить([str(i) for i in range(1, 8)], 10)) == 7


def test_все_заказы_сделки_в_одной_части(capsys):
    ф = Фейк()
    m.main(ф, СЕГОДНЯ)
    capsys.readouterr()
    по_части = [set(ids) for e, ids in ф.истории if e == 172]
    for o in ЗАКАЗЫ:
        if o["parentId2"] == 7710001:
            assert sum(1 for ids in по_части if str(o["id"]) in ids) == 1
    assert any({"7730001", "7730002"} <= ids for ids in по_части)


# ── чистые функции ──────────────────────────────────────────────────────────

def test_граница_шага_как_у_ork_segments():
    names = {k: v["name"] for k, v in ЗАКАЗ_МЕТА.items()}
    tr = [("DT172_26:NEW", t("2026-03-02")), ("DT172_26:PREPARATION", t("2026-03-03")),
          ("DT172_26:UC_PAY", t("2026-03-10")),               # шаг 5 пропущен
          ("DT172_26:UC_PROD", t("2026-03-11")), ("DT172_26:UC_READY", t("2026-04-20")),
          ("DT172_26:UC_PROD", t("2026-04-21")), ("DT172_26:UC_READY", t("2026-04-22")),
          ("DT172_26:UC_TRANS", t("2026-04-30"))]
    reach, idx = m.достижения(tr, names)
    seg = m.ork.segments(tr, names)["segs"]
    for код, _имя, a, b, _р in m.ork.SEGMENTS:
        if isinstance(a, int) and isinstance(b, int) and код in seg:
            assert m._вр(seg[код]["start"]) == reach[a] and m._вр(seg[код]["end"]) == reach[b], код
    assert reach[5] == reach[6] and idx[5] == idx[6]          # пропуск — одна отметка у двух шагов
    assert reach[8] == m._вр(t("2026-04-20"))                  # первый выход, а не последний


def test_диапазоны_стадий_сделки():
    д = m.диапазоны_сделки()
    assert д[2] == (1, 3) and д[9] == (9, 10) and д[13] == (13, 13)


def test_метка_и_имя_стадии():
    подп = {"1": "Импорт", "2": "ООО «Ромашка»", "10": "РФ"}
    assert m.метка(З["dir"], "1", подп) == "Импорт"
    assert m.метка(З["dir"], "2", подп) == "вариант 2"
    assert m.метка("ufCrm20_1724941500", "1", подп) == "вариант 1"     # «Схема поставки» не в белом списке
    assert m.метка(З["dir"], "", подп) == "пусто" and m.метка(З["dir"], "77", подп) == "нет в справочнике"
    assert m.имя_стадии("Экспертиза") == "Экспертиза"
    assert m.имя_стадии("Ждём Acme Ltd") == "(имя скрыто)"


def test_код_ошибки_без_описания():
    e = m.bc.BitrixError("tasks.task.list: ACCESS_DENIED Доступ запрещён Иванову И.")
    assert m.код_ошибки(e) == "ACCESS_DENIED"
    assert m.код_ошибки(ValueError("Иванов")) == "ValueError"


def test_p80_и_медиана():
    assert m.p80([1, 2, 3, 4, 5]) == 4 and m.p80([7]) == 7 and m.p80([]) is None
    assert m.мед([1, None, 3]) == 2


def test_сверка_опор_в_рабочих_днях():
    пт, пн = m._вр(t("2026-10-23")), m._вр(t("2026-10-26"))
    assert m.сверить(пт, пн) == 1                              # выходные не считаются
    assert m.сверить(пн, пт) == 1 and m.сверить(None, пн) is None
    assert m.сверить(m._вр(t("2031-01-10")), m._вр(t("2031-01-12"))) is None   # года нет — не Пн–Пт


def test_значение_даты_оплаты():
    assert m.значение_даты_оплаты(0.95, 0.92, 0.05).startswith("похоже на факт")
    assert m.значение_даты_оплаты(0.95, 0.92, 0.40).startswith("похоже на план")
    assert m.значение_даты_оплаты(0.95, None, 0.0).startswith("неясно")
    assert m.значение_даты_оплаты(0.60, 0.95, 0.0).startswith("неясно")


def test_смета_в_пределах_ориентира():
    assert m.смета(1500, 700, 10) <= m.БЮДЖЕТ["предел"]
    assert abs(m.смета(1500, 700, 10) - m.БЮДЖЕТ["ожидается"]) < 150


def test_история_по_сущности():
    class Портал:
        def __init__(self):
            self.e = None

        def call_envelope(self, method, params):
            self.e = params["entityTypeId"]
            return {"result": {"items": [{"OWNER_ID": "5", "STAGE_ID": "DT172_26:NEW", "CREATED_TIME": t("2026-01-01")}]},
                    "total": 1}
    п = Портал()
    ист, ош = m.pc.читать_историю(п, ["5"], entity=172)
    assert ош is None and п.e == 172 and ист == {"5": [("DT172_26:NEW", t("2026-01-01"))]}
    m.pc.читать_историю(п, ["5"])
    assert п.e == 2                                            # по умолчанию — сделки, как прежде


def test_зонд_в_probe_yml():
    wf = yaml.safe_load((ROOT / ".github/workflows/probe.yml").read_text(encoding="utf-8"))
    вход = (wf.get("on") or wf[True])["workflow_dispatch"]["inputs"]
    assert "probe_regulation.py" in вход["script"]["options"]
    assert set(вход["parts"]["options"]) == {"10", "25", "50"}
    шаг = next(s for s in wf["jobs"]["probe"]["steps"] if s.get("name") == "Зонд")
    assert шаг["env"]["PROBE_PARTS"] and шаг["env"]["BITRIX_RPS"] == "1.0" and шаг["env"]["BITRIX_PARALLEL"] == "1"
    assert шаг["env"]["BITRIX_WAIT_BUDGET"] == "900"
    assert wf["concurrency"]["group"] == "bitrix-portal"
