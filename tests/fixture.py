"""Синтетические данные Bitrix для тестов и smoke-рендера — без обращения к порталу.

Формы записей повторяют реальные ответы Bitrix (СП-166 и crm.deal.list), поэтому
метрики считаются настоящим модулем metrics.build, а не подделкой словаря.
"""
from __future__ import annotations

import datetime as dt
import random

import people as people_mod
import period as period_mod
import stages as stages_mod

DEPT_A = {"76", "77", "78", "79"}          # «Отдел поиска поставщиков»
DEPT_B = {"90", "91"}
NAMES = {"76": "Иванов И.", "77": "Петрова А.", "78": "Сидоров С.", "79": "Кузнецов К.",
         "90": "Орлов О.", "91": "Волкова В."}


def _stage_ids() -> list[str]:
    """Реальные stageId СП-166 из карты stages.py, чтобы бакеты раскладывались как в проде."""
    ids = [s for s in getattr(stages_mod, "_SPA_STAGE_BUCKET", {}) if isinstance(s, str)]
    return ids or ["DT166_24:NEW", "DT166_24:PREPARATION", "DT166_24:SUCCESS", "DT166_24:FAIL"]


def make_period(start: str = "2026-05-01", end: str = "2026-07-31") -> period_mod.Period:
    return period_mod.parse_period(f"{start}:{end}", as_of=dt.date.fromisoformat(end))


def make_dataset(n_rfq: int = 240, n_deals: int = 120, seed: int = 7) -> dict:
    """Полный набор входов для metrics.build (детерминированный при том же seed)."""
    rnd = random.Random(seed)
    p = make_period()
    stage_ids = _stage_ids()
    users = sorted(DEPT_A | DEPT_B)
    span = (p.end - p.start).days or 1

    rfqs = []
    for i in range(n_rfq):
        created = p.start + dt.timedelta(days=rnd.randint(0, span))
        moved = created + dt.timedelta(days=rnd.randint(0, 20))
        rfqs.append({
            "id": 1000 + i,
            "assignedById": int(rnd.choice(users)),
            "stageId": rnd.choice(stage_ids),
            "createdTime": created.isoformat() + "T10:00:00+03:00",
            "movedTime": moved.isoformat() + "T10:00:00+03:00",
            "parentId2": 500 + (i % n_deals),
            "categoryId": 24,
            "title": f"Запрос №{i}",
            "companyId": 300 + (i % 40),
            "ufCrm18Supplier": [f"CO_{300 + (i % 40)}"],
            "_supplier": f"ООО Поставщик-{i % 40}",
        })

    deal_stages = ["C24:NEW", "C24:PREPARATION", "C24:WON", "C24:LOSE", "C0:WON"]
    deal_index, period_deals = {}, []
    for j in range(n_deals):
        did = str(500 + j)
        created = p.start + dt.timedelta(days=rnd.randint(0, span))
        d = {
            "ID": did, "TITLE": f"Сделка {j}", "CATEGORY_ID": str(rnd.choice([0, 24, 7])),
            "STAGE_ID": rnd.choice(deal_stages),
            "STAGE_SEMANTIC_ID": rnd.choice(["P", "S", "F"]),
            "DATE_CREATE": created.isoformat() + "T09:00:00+03:00",
            "ASSIGNED_BY_ID": rnd.choice(users),
            "COMPANY_ID": str(300 + (j % 40)),
            "OPPORTUNITY": str(rnd.randint(10_000, 5_000_000)),
            "CURRENCY_ID": "RUB",
        }
        deal_index[did] = d
        period_deals.append(d)

    return {
        "period": p, "rfqs": rfqs, "deal_index": deal_index, "period_deals": period_deals,
        "dept_a_ids": set(DEPT_A), "names": dict(NAMES),
        "since": {u: "2025-01-01" for u in users},
        "deal_stage_names": {s: s.split(":")[-1].title() for s in deal_stages},
        "category_names": {"0": "Продажи", "24": "Сорсинг", "7": "Сервис"},
    }


def build_metrics(**kw) -> dict:
    """Синтетика → настоящий metrics.build → метрики той же формы, что в проде."""
    import metrics as metrics_mod
    d = make_dataset(**kw)
    return metrics_mod.build(d["period"], d["rfqs"], d["deal_index"], d["period_deals"],
                             d["dept_a_ids"], d["names"], d["since"],
                             d["deal_stage_names"], d["category_names"])


# ------------------------------------------------------------------ коммерсанты
# Придуманный состав и портфель для вкладок «КАМы»/«Продукт-оунеры». Формы записей
# повторяют ответы Bitrix (user.get, department.get, crm.deal.list, СП-172), поэтому
# показатели считает настоящий people.compute, а не подделанный словарь.
PEOPLE_TODAY = dt.date(2026, 9, 17)
KAM_F, PROD_F = people_mod.KAM_F, people_mod.PROD_F
DL = people_mod.DL_CUSTOMER

USERS = [
    # действующие
    {"ID": 1, "LAST_NAME": "Астахова", "NAME": "Вера", "ACTIVE": "Y", "UF_DEPARTMENT": [10],
     "WORK_POSITION": "Key Account Manager, Mining"},
    {"ID": 2, "LAST_NAME": "Бортник", "NAME": "Илья", "ACTIVE": "Y", "UF_DEPARTMENT": [12],
     "WORK_POSITION": "Head of compressor department"},
    {"ID": 3, "LAST_NAME": "Вьюгин", "NAME": "Пётр", "ACTIVE": "Y", "UF_DEPARTMENT": [14],
     "WORK_POSITION": "Supplier Sourcing Manager"},
    {"ID": 4, "LAST_NAME": "Гущина", "NAME": "Лада", "ACTIVE": "Y", "UF_DEPARTMENT": [10],
     "WORK_POSITION": "Project Manager"},        # роль — по отделу (клиентская группа)
    {"ID": 6, "LAST_NAME": "Ежов", "NAME": "Тарас", "ACTIVE": "Y", "UF_DEPARTMENT": [12],
     "WORK_POSITION": "Project Manager"},        # продуктовый отдел, сделок нет
    {"ID": 7, "LAST_NAME": "Ветров", "NAME": "Олег", "ACTIVE": "Y", "UF_DEPARTMENT": [8],
     "WORK_POSITION": "Deputy Commercial Officer"},   # руководитель коммерческого блока
]
FIRED = [{"ID": 5, "LAST_NAME": "Донцов", "NAME": "Юрий", "ACTIVE": "N", "UF_DEPARTMENT": [10],
          "WORK_POSITION": "Key Account Manager, Oil and Gas"}]
# У отделов роли есть зонтичный родитель с руководителем (uid 7) — так правило
# «поднимись до вышестоящего отдела» проверяется на положительном пути; у отдела 12
# руководителя нет намеренно: это пробел, который вкладка обязана назвать.
DEPTS = [{"ID": 8, "NAME": "Коммерческий блок", "UF_HEAD": 7},
         {"ID": 10, "NAME": "Группа по работе с ПАО «Северный синтез»", "PARENT": 8, "UF_HEAD": 1},
         {"ID": 12, "NAME": "Группа по компрессорному оборудованию", "PARENT": 8},
         {"ID": 14, "NAME": "Отдел поиска поставщиков", "PARENT": 8}]
CATS = {"0": "Реализация", "4": "Запросы", "8": "Северный синтез", "22": "Тестовая воронка"}
STAGE_META = {"C4:NEW": {"name": "Новая заявка", "sem": "P", "sort": 10, "cat": "4"},
              "C8:UC_1": {"name": "ТКП выдано", "sem": "P", "sort": 20, "cat": "8"},
              "0:NEW": {"name": "Договор согласуется", "sem": "P", "sort": 10, "cat": "0"},
              "C22:NEW": {"name": "Новая", "sem": "P", "sort": 10, "cat": "22"}}


def deal(did, cat, stage, amt, owner, *, kam=None, prod=None, moved="2026-09-10",
         company=100, created="2026-03-01", sem="P", close=""):
    d = {"ID": did, "TITLE": f"Сделка {did}", "CATEGORY_ID": cat, "STAGE_ID": stage,
         "STAGE_SEMANTIC_ID": sem, "OPPORTUNITY": amt, "CURRENCY_ID": "EUR",
         "DATE_CREATE": created + "T10:00:00+03:00", "MOVED_TIME": moved + "T10:00:00+03:00",
         "LAST_ACTIVITY_TIME": moved + "T10:00:00+03:00", "ASSIGNED_BY_ID": owner,
         "COMPANY_ID": company, "CLOSEDATE": (close + "T10:00:00+03:00") if close else ""}
    if kam:
        d[KAM_F] = str(kam)
    if prod:
        d[PROD_F] = str(prod)
    return d


OPEN_DEALS = [
    # КАМ стоит полем, а ответственный — сорсер: сделка должна уйти КАМу (1), не сорсеру (3)
    deal(101, "8", "C8:UC_1", 200_000, owner=3, kam=1, close="2026-12-01"),
    # реализация: воронка 0, есть заказ с просроченным дедлайном клиенту
    deal(102, "0", "0:NEW", 500_000, owner=1, kam=1, close="2026-11-15"),
    # продуктовая сделка полем Product leader
    deal(103, "4", "C4:NEW", 80_000, owner=3, prod=2, close="2026-02-01"),
    # ничья: поля пусты, владелец — сорсер (не коммерсант)
    deal(104, "4", "C4:NEW", 40_000, owner=3),
    # владелец-КАМ без поля: атрибуция по владельцу
    deal(105, "8", "C8:UC_1", 70_000, owner=4),
    # застоявшаяся сделка уволенного: бесхозная
    deal(106, "8", "C8:UC_1", 90_000, owner=5, moved="2025-01-10"),
    # техническая воронка — не в счёт
    deal(107, "22", "C22:NEW", 1_000, owner=1, kam=1),
    # без суммы и без клиента — косяк гигиены
    deal(108, "4", "C4:NEW", 0, owner=1, kam=1, company=0),
]
# Победа в этом портале — перевод в воронку реализации, а не семантика стадии:
# 201 помечена «успех», но в реализацию не переводилась и заказов не имеет — не победа;
# 203 названа номером реализации («912. …») — победа без заказа и без семантики.
CREATED = OPEN_DEALS + [
    deal(201, "8", "C8:UC_1", 300_000, owner=1, kam=1, sem="S", created="2026-02-01"),
    deal(202, "8", "C8:UC_1", 150_000, owner=1, kam=1, sem="F", created="2026-02-10"),
    dict(deal(203, "8", "C8:UC_1", 220_000, owner=1, kam=1, created="2026-02-20"),
         TITLE="912. Поставка узла"),
]
ORDERS = [
    {"id": 900, "stageId": "DT172_26:UC_1", "opportunity": 400_000, "currencyId": "EUR",
     "parentId2": 102, "assignedById": 3, DL: "2026-08-01"},          # просрочен на 47 дней
    {"id": 901, "stageId": "DT172_26:SUCCESS", "opportunity": 50_000, "currencyId": "EUR",
     "parentId2": 103, "assignedById": 3, DL: "2026-12-01"},
    {"id": 902, "stageId": "DT172_26:FAIL", "opportunity": 999_000, "currencyId": "EUR",
     "parentId2": 104, "assignedById": 3, DL: "2026-01-01"},          # проигранный не считается
]


class PeopleStub:
    """Минимальный Bitrix: отдаёт придуманный корпус в форме реальных ответов."""

    def list_paged(self, method, params=None):
        params = params or {}
        if method == "user.get":
            only_fired = (params.get("FILTER") or {}).get("ACTIVE") == "N"
            return FIRED if only_fired else USERS
        if method == "department.get":
            return DEPTS
        return []

    def categories(self):
        return dict(CATS)

    def deal_stage_meta(self):
        return dict(STAGE_META)

    def users(self):
        return {str(u["ID"]): f'{u["LAST_NAME"]} {u["NAME"]}' for u in USERS + FIRED}

    def call(self, method, params=None):
        if method == "crm.currency.list":
            return [{"CURRENCY": "EUR", "AMOUNT": 1, "AMOUNT_CNT": 1}]
        if method == "crm.status.list":
            return [{"STATUS_ID": k, "NAME": v["name"]} for k, v in STAGE_META.items()]
        return []

    def list_deals_fast(self, **kw):
        return []

    def list_items(self, *a, **kw):
        return []




def build_people(**kw) -> dict:
    """Синтетика → настоящий people.compute → данные вкладок той же формы, что в проде."""
    return people_mod.compute(PeopleStub(), as_of=PEOPLE_TODAY, open_deals=OPEN_DEALS,
                              created=CREATED, orders=ORDERS, **kw)


def build_reps(**kw) -> dict:
    """Синтетика → настоящий reps.compute → данные вкладки «Коммерсанты»."""
    import reps as reps_mod
    return reps_mod.compute(PeopleStub(), as_of=PEOPLE_TODAY, created=CREATED, orders_src=ORDERS, **kw)


# ---------------------------------------------------------------- «Советы знатока»
# Отдельный маленький корпус: вкладка советника считается не по составу людей, а по
# когорте сделок и сроку «создана → вошла в реализацию». Держим его здесь, чтобы
# smoke-рендер собирал вкладку с данными: до 17.09.2026 она уходила в проверку пустой,
# и вырожденный помесячный прогноз (98 % пайплайна мимо графика) никто не ловил.
ADV_TODAY = PEOPLE_TODAY


def adv_deal(did, cat, stage, amt, created, *, close="", sem="P", title=""):
    return {"ID": str(did), "TITLE": title or f"Сделка {did}", "CATEGORY_ID": cat,
            "STAGE_ID": stage, "STAGE_SEMANTIC_ID": sem, "OPPORTUNITY": amt,
            "CURRENCY_ID": "EUR", "DATE_CREATE": created + "T10:00:00+03:00",
            "CLOSEDATE": (close + "T10:00:00+03:00") if close else "",
            "ASSIGNED_BY_ID": "1", "COMPANY_ID": "100"}


# пять побед в воронке «2» — хватает на собственную медиану срока; в воронке «4» победа одна
ADV_DEALS = [adv_deal(10 + i, "2", "C2:WON", 100_000, "2026-01-10",
                      title=f"{900 + i}. Поставка насоса") for i in range(5)]
ADV_DEALS += [adv_deal(20, "4", "C4:WON", 50_000, "2026-02-01", title="910. Поставка фильтра")]
# открытые: плановая дата в будущем (план), в прошлом (автопростановка) и её отсутствие
ADV_DEALS += [adv_deal(301, "2", "C2:EXECUTING", 300_000, "2026-08-01", close="2026-11-20"),
              adv_deal(302, "2", "C2:EXECUTING", 200_000, "2026-08-01", close="2025-03-01"),
              adv_deal(303, "4", "C4:NEW", 100_000, "2026-09-01"),
              adv_deal(304, "2", "C2:NEW", 40_000, "2026-06-01", sem="F")]
ADV_REALIZE = {str(10 + i): "2026-03-11" for i in range(5)} | {"20": "2026-07-31"}
ADV_STAGES = {"C2:EXECUTING": "Тендерное предложение выдано", "C4:NEW": "Новая заявка",
              "C2:NEW": "Новый тендер", "C2:WON": "Реализация", "C4:WON": "Реализация"}
ADV_CATS = {"2": "Тендеры", "4": "Запросы"}


class AdvisorStub(PeopleStub):
    """Тот же клиент плюс прошлогодняя когорта и названия компаний."""

    def list_deals_fast(self, **kw):
        return [adv_deal(900 + i, "2", "C2:NEW", 10_000, "2025-05-01") for i in range(4)]

    def companies_by_ids(self, ids):
        return {str(c): f"Клиент {c}" for c in ids}


def build_advisor(**kw) -> dict:
    """Синтетика → настоящий advisor.compute → данные вкладки «Советы знатока»."""
    import advisor as advisor_mod
    return advisor_mod.compute(AdvisorStub(), as_of=ADV_TODAY, deals=ADV_DEALS, orders=[],
                               realize_date=ADV_REALIZE, deal_stage_names=ADV_STAGES,
                               category_names=ADV_CATS, **kw)
