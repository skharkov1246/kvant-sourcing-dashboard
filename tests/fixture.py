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
SERVICE_BOT = "900"                        # служебная запись воронки пресейла
SOURCER_FIELD = "UF_CRM_1779187335"         # поле сделки «Сорсер» — как в портале
HEAD_FIELD = "UF_CRM_1776169420"            # поле сделки «Head of sourcing departement»
DEAL_SOURCER_FIELDS = ((SOURCER_FIELD, "сорсер сделки"),
                       (HEAD_FIELD, "сорсер в сделке не указан", False))
SERVICE_IDS = {SERVICE_BOT}
NAMES = {"76": "Иванов И.", "77": "Петрова А.", "78": "Сидоров С.", "79": "Кузнецов К.",
         "90": "Орлов О.", "91": "Волкова В.", SERVICE_BOT: "Робот пресейла"}
# подразделения нужны разбору «кто заводит запросы»: одного признака
# «в отделе поиска поставщиков или нет» мало, важно какое именно подразделение
# служебная запись намеренно не числится ни в одном подразделении — как в портале
USER_DEPTS = {"76": "Отдел поиска поставщиков", "77": "Отдел поиска поставщиков",
              "78": "Отдел поиска поставщиков", "79": "Отдел поиска поставщиков",
              "90": "Инжиниринг", "91": "Коммерческий отдел"}


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
        # заводит запрос не всегда ответственный: часть создают смежные
        # подразделения, часть — автоматика портала (createdBy пустой)
        # часть карточек заводит и записывает на себя робот пресейла: живого
        # исполнителя у них видно только по владельцу родительской сделки
        # робот и двигал карточку сам — человеческого следа нет вовсе
        mover = updater = last = SERVICE_BOT
        if i % 11 == 3:
            creator = assignee = SERVICE_BOT
            # часть роботных карточек потом трогает живой сорсер: у них след есть.
            # Карточка i == 3 намеренно не в их числе: у неё нет ни родительской
            # сделки, ни человеческого следа — на ней проверяется, что такая
            # карточка остаётся нераспознанной, а не приписывается кому-то
            if i == 3:
                pass
            elif i % 33 == 3:
                mover = rnd.choice(sorted(DEPT_A))
            elif i % 33 == 14:
                updater = rnd.choice(sorted(DEPT_A))
            elif i % 33 == 25:
                last = rnd.choice(sorted(DEPT_A))
        elif i % 13 == 5:
            # робот стоит ТОЛЬКО ответственным: карточку завёл человек, а ведёт
            # её служебная запись. Разбор по автору такую карточку не видит
            creator, assignee = rnd.choice(sorted(DEPT_A)), SERVICE_BOT
        elif i % 9 == 0:
            creator, assignee = "", rnd.choice(users)
        elif i % 5 == 0:
            creator, assignee = rnd.choice(sorted(DEPT_B)), rnd.choice(users)
        else:
            creator, assignee = rnd.choice(sorted(DEPT_A)), rnd.choice(users)
        if assignee != SERVICE_BOT:
            mover = updater = last = assignee      # обычную карточку ведёт её ответственный
        rfqs.append({
            "id": 1000 + i,
            "assignedById": int(assignee),
            "createdBy": creator,
            # следы живого человека на карточке робота
            "movedBy": mover,
            "updatedBy": updater,
            "lastActivityBy": last,
            "stageId": rnd.choice(stage_ids),
            "createdTime": created.isoformat() + "T10:00:00+03:00",
            "movedTime": moved.isoformat() + "T10:00:00+03:00",
            # у первой роботной карточки сделки нет — исполнителя восстановить не из чего
            "parentId2": None if i == 3 else 500 + (i % n_deals),
            "categoryId": 24,
            "title": f"Запрос №{i}",
            "companyId": 300 + (i % 40),
            "ufCrm18Supplier": [f"CO_{300 + (i % 40)}"],
            "_supplier": f"ООО Поставщик-{i % 40}",
            # файл КП со стороны поставщика: по нему считается «получено КП».
            # Признак ставит выгрузка (main._has_quote_file), поэтому здесь он
            # задаётся готовым — как приходит в metrics.build
            "_hasQuote": i % 3 != 2,
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
            # сорсер, записанный в сделке: по нему карточка робота уходит
            # инициатору. Заполнен у половины сделок — вторая половина
            # проверяет, что цепочка идёт дальше, а не останавливается
            SOURCER_FIELD: sorted(DEPT_A)[j % len(DEPT_A)] if j % 2 == 0 else "",
        }
        deal_index[did] = d
        period_deals.append(d)

    # входящие письма поставщиков: из них считается счётчик полученных КП.
    # Часть писем без вложения (цена в теле) и часть приходит парами на одну
    # карточку — чтобы в тестах расходились «писем с вложением» и «карточек».
    inbound = []
    for i in range(n_rfq // 2):
        got = p.start + dt.timedelta(days=rnd.randint(0, span))
        cid = str(1000 + (i % n_rfq))
        inbound.append({"cid": cid, "dt": got.isoformat() + "T12:00:00+03:00",
                        "file": i % 4 != 0})
        if i % 9 == 0:
            inbound.append({"cid": cid, "dt": got.isoformat() + "T15:00:00+03:00",
                            "file": True})

    return {
        "period": p, "rfqs": rfqs, "deal_index": deal_index, "period_deals": period_deals,
        "inbound_mail": inbound,
        "dept_a_ids": set(DEPT_A), "names": dict(NAMES),
        "since": {u: "2025-01-01" for u in users},
        # состав отдела во времени: «79» ушёл в середине июля, но робот по полю
        # «Сорсер» сделки продолжает класть на него карточки — так в портале
        # выглядит уволенный, которого не сняли со сделок
        "staff": {**{u: {"active": True, "hired": dt.date(2025, 1, 1), "left": None}
                     for u in sorted(DEPT_A)},
                  "79": {"active": False, "hired": dt.date(2025, 1, 1), "left": dt.date(2026, 7, 10)}},
        "user_depts": dict(USER_DEPTS),
        "service_ids": set(SERVICE_IDS),
        "deal_stage_names": {s: s.split(":")[-1].title() for s in deal_stages},
        "category_names": {"0": "Продажи", "24": "Сорсинг", "7": "Сервис"},
    }


def build_metrics(**kw) -> dict:
    """Синтетика → настоящий metrics.build → метрики той же формы, что в проде."""
    import metrics as metrics_mod
    d = make_dataset(**kw)
    return metrics_mod.build(d["period"], d["rfqs"], d["deal_index"], d["period_deals"],
                             d["dept_a_ids"], d["names"], d["since"],
                             d["deal_stage_names"], d["category_names"], d["user_depts"],
                             d.get("service_ids"), d.get("inbound_mail"),
                             DEAL_SOURCER_FIELDS, staff=d.get("staff"), econ=ECON)


# Сделки с «Экономика готова» = «Да» и их запросы (правило 19.10.2026, п. 10): одна
# по правилу, две — с нарушением. Придуманы (CLAUDE.md, правило 18).
ECON = (
    [{"ID": 7001, "TITLE": "Насос дозирующий — спецификация", "ASSIGNED_BY_ID": "101", "STAGE_SEMANTIC_ID": "P"},
     {"ID": 7002, "TITLE": "Уплотнения компрессора", "ASSIGNED_BY_ID": "102", "STAGE_SEMANTIC_ID": "P"},
     {"ID": 7003, "TITLE": "Фильтроэлементы", "ASSIGNED_BY_ID": "101", "STAGE_SEMANTIC_ID": "F"}],
    [{"parentId2": 7001, "stageId": "DT166_24:SUCCESS"}, {"parentId2": 7001, "stageId": "DT166_24:FAIL"},
     {"parentId2": 7002, "stageId": "DT166_24:UC_H49RUE"}, {"parentId2": 7002, "stageId": "DT166_24:SUCCESS"},
     {"parentId2": 7003, "stageId": "DT166_24:UC_H49RUE"}],
)


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


def build_premia(**kw) -> dict:
    """Синтетика «Коммерсантов» → настоящий premia.compute → данные вкладки «Премии».
    Курс придуман: 1 € = 100 ₽ (рубли в синтетике — только масштаб)."""
    import premia as premia_mod
    return premia_mod.compute(build_people(), None, eur_rub=100.0, **kw)


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


# ── «Реализация»: придуманный портал для contracts.compute (сделки воронки 0 + заказы
# СП-172 во всех фазах жизненного цикла). Даты — от CT_NOW, суммы и названия придуманы.
CT_NOW = "2026-10-19T12:00:00+03:00"
CT_TODAY = dt.date(2026, 10, 19)


class ContractsStub:
    """Ровно те методы клиента, которые зовёт contracts.compute."""

    def __init__(self):
        import contracts as ct
        rnd = random.Random(17)
        now = dt.datetime.fromisoformat(CT_NOW)

        def t(days_ago: float) -> str:
            return (now - dt.timedelta(days=days_ago)).isoformat()

        def d(days_ago: int) -> str:
            return (CT_TODAY - dt.timedelta(days=days_ago)).isoformat()

        self.deals, self.orders, self.hist = [], [], {2: [], 172: []}
        stages = ["C0:NEW", "C0:PREPARATION", "C0:EXECUTING", "C0:FINAL_INVOICE"]
        oid = 7000
        for i in range(24):
            did = str(6000 + i)
            st = stages[i % len(stages)]
            self.deals.append({"ID": did, "TITLE": f"{950 + i}. Поставка узла {i}", "OPPORTUNITY": str(20_000 + 3_000 * i),
                               "CURRENCY_ID": "EUR", "COMPANY_ID": str(300 + i % 7), "STAGE_ID": st,
                               "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0", "DATE_CREATE": t(200 - i * 5),
                               "ASSIGNED_BY_ID": str(10 + i % 3), ct.OSS_DEAL: [str(20 + i % 4)],
                               ct.KAM_F: str(10 + (i + 1) % 3), "UF_CRM_1734380921": str(9000 + i)})
            self.hist[2].append((did, st, t(30 + i)))
            for j in range(1 + i % 3):
                oid += 1
                age = 40 + rnd.randint(0, 200)
                o = {"id": oid, "title": f"{950 + i}/{j + 1}. Узел {i}", "companyId": str(400 + (i + j) % 9),
                     "parentId2": int(did), "opportunity": 5_000 + rnd.randint(0, 20_000), "currencyId": "EUR",
                     "stageId": "DT172_26:PREPARATION", "createdTime": t(age), "movedTime": t(rnd.randint(1, 30)),
                     ct.OSS_F: str(20 + (i + j) % 4), ct.DL_CUSTOMER: d(rnd.randint(-60, 40))}
                kind = (i * 3 + j) % 6
                if kind >= 1:
                    o[ct.SUP_PAID] = 100 if kind >= 2 else 30
                if kind >= 2:
                    o[ct.SHIP_PLAN] = d(age - 30)
                    if kind >= 3 or rnd.random() < 0.5:
                        o[ct.SHIP_FACT] = d(age - 30 - rnd.randint(-5, 15))
                if kind >= 3:
                    o[ct.INB_FACT] = d(max(0, age - 60))
                if kind >= 4:
                    o[ct.DEL_FACT] = d(max(0, age - 75))
                    o[ct.PAY_PLAN] = d(max(0, age - 105))
                if kind == 5:
                    o[ct.UTD_DATE] = d(max(0, age - 77))
                    o[ct.CLIENT_PO] = [{"id": 1}]
                    o["stageId"] = "DT172_26:SUCCESS"
                self.orders.append(o)
                self.hist[172].append((str(oid), "DT172_26:NEW", t(age)))
                self.hist[172].append((str(oid), o["stageId"], t(rnd.randint(1, 30))))

    def call(self, method, params=None, **kw):
        if method == "crm.item.fields":
            return {"fields": {}}
        if method == "im.dialog.messages.get":
            # чётные чаты — человек писал вчера, нечётные — месяц назад, кратные 5 — нет доступа
            n = int(str(params["DIALOG_ID"]).removeprefix("chat")) - 9000
            if n % 5 == 0:
                raise RuntimeError("ACCESS_DENIED")
            days = 1 if n % 2 == 0 else 30
            return {"messages": [{"author_id": 20, "date": (CT_TODAY - dt.timedelta(days=days)).isoformat()}]}
        return [{"CURRENCY": "EUR", "AMOUNT": "1", "AMOUNT_CNT": "1", "BASE": "Y", "DATE_UPDATE": "2026-10-01"},
                {"CURRENCY": "RUB", "AMOUNT": "0.01", "AMOUNT_CNT": "1", "DATE_UPDATE": "2026-10-01"},
                {"CURRENCY": "USD", "AMOUNT": "0.9", "AMOUNT_CNT": "1", "DATE_UPDATE": "2026-10-01"}]

    def list_items(self, entity, *, filter=None, select=None, max_items=None):
        return [dict(o) for o in self.orders]

    def companies_by_ids(self, ids):
        return {str(c): f"Компания {c}" for c in ids}

    def list_deals_fast(self, *, filter=None, select=None, **kw):
        return [dict(x) for x in self.deals]

    def deals_by_ids(self, ids, *, select=None):
        return {}

    def deal_stages_cat(self, cid=0):
        return {"C0:NEW": "Договор на подписании", "C0:PREPARATION": "Оплата поставщикам",
                "C0:EXECUTING": "Производство", "C0:FINAL_INVOICE": "Доставка клиенту", "C0:WON": "Сделка успешна"}

    def deal_stages_process(self, cid=0):
        return {k: v for k, v in self.deal_stages_cat().items() if k != "C0:WON"}

    def spa_stage_meta(self, entity, cid):
        return {f"DT172_{cid}:NEW": {"name": "Новый заказ", "sem": "P"},
                f"DT172_{cid}:PREPARATION": {"name": "Производство заказа", "sem": "P"},
                f"DT172_{cid}:SUCCESS": {"name": "Заказ закрыт", "sem": "S"}}

    def stage_history(self, entity, *, category_id=None, since=None, last=None):
        out: dict = {}
        for oid, st, ts in self.hist[entity]:
            if last is not None:
                last[oid] = (st, ts)
            if st not in {s for s, _ in out.get(oid, [])}:
                out.setdefault(oid, []).append((st, ts))
        return out

    def user_name(self, uid):
        return {"10": "Анна (КАМ)", "11": "Борис (КАМ)", "12": "Вера (КАМ)", "20": "Глеб (ОСС)",
                "21": "Дина (ОСС)", "22": "Егор (ОСС)", "23": "Жанна (ОСС)"}.get(str(uid), f"user#{uid}")


def build_contracts(**kw) -> dict:
    """Синтетика → настоящий contracts.compute → данные вкладки «Реализация»."""
    import os

    import contracts as contracts_mod
    old = os.environ.get("KVANT_NOW")
    os.environ["KVANT_NOW"] = CT_NOW
    try:
        return contracts_mod.compute(ContractsStub(), as_of=CT_TODAY, people={}, **kw)
    finally:
        if old is None:
            os.environ.pop("KVANT_NOW", None)
        else:
            os.environ["KVANT_NOW"] = old
