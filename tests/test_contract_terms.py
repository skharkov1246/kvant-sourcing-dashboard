"""Пилот чтения договоров (scripts/contract_terms.py) — без сети.

Проверяется то, что защищает деньги, данные и журнал:
  · отбор: последние НЕ проигранные сделки реализации, карточки СП-1174 и СП-1192
    по parentId2 этих сделок, поле файла по приоритету, сторона по компании
    карточки (сверка), по связи с заказом и подписи списков (справочно);
  · строгая схема ответа выведена из contract_b2b.TERMS_SCHEMA;
  · значение без цитаты снимается и называется по имени, сторона из ответа модели
    читается; флаг «на отсутствии» по снятому полю не ставится;
  · версия метода выведена из подсказки и схемы ответа;
  · бюджет останавливает чтение ДО вызова, который в него не помещается;
    оборванный вызов списывается по оценке; срок прогона останавливает чтение;
  · файл больше предела чтения модели не отдаётся (limit), сделка — без флагов;
  · строки и флаги: сторона — по модели (решение владельца 22.10.2026); сделка со
    спорным документом (компания карточки против модели), с документом без
    стороны и недочитанная флагов не получают; связь и подпись «Чей шаблон»
    сторону не решают; гейт стороны красный, если спорных много или модель
    стороны почти не называет;
  · закачка файла поля CRM — только по urlMachine, без disk.file.get;
  · журнал холостого и живого прогона — только агрегаты: ни номеров, ни цитат,
    ни условий, ни подписей (CLAUDE.md, правило 17);
  · прогон Actions: только ручной, общая очередь портала, без артефактов и кэша.

Портал, модель и база подставные. Корпус придуман (правило 18): номера, тексты и
условия выдуманы.
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import contract_terms as ct  # noqa: E402

rl = ct.read_llm

# --------------------------------------------------------------------------- выдуманный портал
СДЕЛКА_НОВАЯ, СДЕЛКА_ВТОРАЯ, СДЕЛКА_ПРОИГРАНА, СДЕЛКА_ЧУЖАЯ, СДЕЛКА_СТАРАЯ = 9900005, 9900004, 9900003, 9900002, 9900001
КОМПАНИЯ_ЗАКАЗЧИКА, КОМПАНИЯ_ПОСТАВЩИКА = 9600001, 9600002
ЗАКАЗ = 9700001

СДЕЛКИ = [
    {"ID": str(СДЕЛКА_СТАРАЯ), "DATE_CREATE": "2026-09-01T10:00:00+03:00", "COMPANY_ID": "9600009",
     "STAGE_SEMANTIC_ID": "S", "CATEGORY_ID": "0"},
    {"ID": str(СДЕЛКА_НОВАЯ), "DATE_CREATE": "2026-10-15T10:00:00+03:00", "COMPANY_ID": str(КОМПАНИЯ_ЗАКАЗЧИКА),
     "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0"},
    # проигранная и чужой воронки — портал их не должен отдать, но отбор проверяет сам
    {"ID": str(СДЕЛКА_ПРОИГРАНА), "DATE_CREATE": "2026-10-14T10:00:00+03:00", "COMPANY_ID": "9600003",
     "STAGE_SEMANTIC_ID": "F", "CATEGORY_ID": "0"},
    {"ID": str(СДЕЛКА_ЧУЖАЯ), "DATE_CREATE": "2026-10-13T10:00:00+03:00", "COMPANY_ID": "9600004",
     "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "32"},
    {"ID": str(СДЕЛКА_ВТОРАЯ), "DATE_CREATE": "2026-10-10T10:00:00+03:00", "COMPANY_ID": "9600005",
     "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0"},
]

ФИН_1174, ТЕК_1174 = ct.ПОЛЯ_ФАЙЛА[ct.СП_РАЗОВЫЙ]
ФИН_1192, ТЕК_1192 = ct.ПОЛЯ_ФАЙЛА[ct.СП_РАМОЧНЫЙ]
ПОЛЯ = {
    ct.СП_РАЗОВЫЙ: {
        "id": {}, "parentId2": {}, "parentId172": {}, "companyId": {}, "createdTime": {},
        ФИН_1174: {"type": "file"}, ТЕК_1174: {"type": "file"},
        ct.ПОЛЕ_ШАБЛОН: {"type": "enumeration", "items": [{"ID": 11, "VALUE": "Шаблон заказчика ВЫДУМКА"},
                                                         {"ID": 12, "VALUE": "Шаблон поставщика ВЫДУМКА"},
                                                         {"ID": 13, "VALUE": "Наш шаблон"}]},
        ct.ПОЛЕ_ТИП: {"type": "enumeration", "items": [{"ID": 21, "VALUE": "Спецификация ВЫДУМКА"}]},
    },
    # у рамочных нет связи с заказом и компании — их и не спрашиваем
    ct.СП_РАМОЧНЫЙ: {"id": {}, "parentId2": {}, ФИН_1192: {"type": "file"}, ТЕК_1192: {"type": "file"}},
    # прежние смарт-процессы договоров (зонд v57): спецификация — под рамочным
    ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ: {"id": {}, "parentId2": {}, "parentId172": {}, "parentId175": {}, "parentId1192": {},
                            "companyId": {}, "createdTime": {},
                            **{к: {"type": "file"} for к in ct.ПОЛЯ_ФАЙЛА[ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ]}},
    ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ: {"id": {}, "parentId2": {}, "parentId172": {}, "companyId": {}, "createdTime": {},
                             **{к: {"type": "file"} for к in ct.ПОЛЯ_ФАЙЛА[ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ]}},
    ct.СП_ЗАКАЗ: {"id": {}, "parentId2": {}, "companyId": {}, "stageId": {},
                  **{к: {"type": "file"} for к in ct.ПОЛЯ_ФАЙЛА[ct.СП_ЗАКАЗ]}},
}
ПО_172, ОС_172 = ct.ПОЛЯ_ФАЙЛА[ct.СП_ЗАКАЗ]
СКАН_132, ТЕК_132, ДОГ_132 = ct.ПОЛЯ_ФАЙЛА[ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ]
СКАН_175, ФАЙЛ_175 = ct.ПОЛЯ_ФАЙЛА[ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ]
#: справочник стадий заказа СП-172: воронка 5 (выдумка)
СТАДИИ = {"DYNAMIC_172_STAGE_5": [{"STATUS_ID": "DT172_5:NEW", "SEMANTICS": None},
                                   {"STATUS_ID": "DT172_5:SUCCESS", "SEMANTICS": "S"},
                                   {"STATUS_ID": "DT172_5:UC_ОТКАЗ", "SEMANTICS": "F"}]}


def файл(fid: str) -> dict:
    return {"id": fid, "url": f"/выдумка/{fid}", "urlMachine": f"https://выдумка.invalid/{fid}"}


КАРТОЧКИ = {
    ct.СП_РАЗОВЫЙ: [
        # договор с заказчиком: финальная версия есть
        {"id": 9800001, "parentId2": СДЕЛКА_НОВАЯ, "parentId172": 0, "companyId": КОМПАНИЯ_ЗАКАЗЧИКА,
         ct.ПОЛЕ_ШАБЛОН: 11, ct.ПОЛЕ_ТИП: 21, ФИН_1174: файл("fid-заказчик"), ТЕК_1174: файл("fid-не-берётся")},
        # договор с поставщиком: финальной версии нет — берётся текущий файл
        {"id": 9800002, "parentId2": СДЕЛКА_НОВАЯ, "parentId172": ЗАКАЗ, "companyId": КОМПАНИЯ_ПОСТАВЩИКА,
         ct.ПОЛЕ_ШАБЛОН: 12, ФИН_1174: [], ТЕК_1174: [файл("fid-поставщик")]},
        # карточка без файла
        {"id": 9800003, "parentId2": СДЕЛКА_ВТОРАЯ, "parentId172": None, ct.ПОЛЕ_ШАБЛОН: 13},
        # карточка проигранной сделки — в отбор не идёт
        {"id": 9800004, "parentId2": СДЕЛКА_ПРОИГРАНА, "parentId172": 0, ФИН_1174: файл("fid-проиграна")},
    ],
    ct.СП_РАМОЧНЫЙ: [
        {"id": 9800005, "parentId2": СДЕЛКА_ВТОРАЯ, ФИН_1192: файл("fid-рамочный")},
    ],
    ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ: [],
    ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ: [],
    # заказы поставщикам СП-172: по ним видно, что компания карточки — поставщик сделки
    172: [
        {"id": ЗАКАЗ, "parentId2": СДЕЛКА_НОВАЯ, "companyId": КОМПАНИЯ_ПОСТАВЩИКА, "stageId": "DT172_5:NEW",
         ПО_172: [файл("fid-po-не-берётся")]},
    ],
}


class Портал:
    """Отвечает как портал на те методы, что зовёт отбор; фильтров не применяет
    (их применение проверяется по записанным параметрам), кроме @связи и >id.
    карточки и поля — свой корпус вместо КАРТОЧКИ и ПОЛЯ (новые источники отбора)."""

    def __init__(self, файлы: dict[str, bytes] | None = None, карточки: dict | None = None,
                 поля: dict | None = None):
        self.вызовы: list[tuple[str, dict]] = []
        self.скачано: list[str] = []
        self.файлы = файлы or {}
        self.карточки = карточки or КАРТОЧКИ
        self.поля = поля or ПОЛЯ

    def вызов(self, метод: str, п: dict) -> dict:
        self.вызовы.append((метод, json.loads(json.dumps(п, default=str))))
        if метод == "crm.deal.list":
            return {"result": СДЕЛКИ[п["start"]:п["start"] + 50]}
        if метод == "crm.item.fields":
            return {"result": {"fields": self.поля[п["entityTypeId"]]}}
        if метод == "crm.status.list":
            return {"result": СТАДИИ.get(п["filter"]["ENTITY_ID"], [])}
        if метод == "crm.item.list":
            ф = п["filter"]
            связь = next(к for к in ф if к.startswith("@"))
            записи = [{к: v for к, v in r.items() if к in п["select"]}
                      for r in self.карточки.get(п["entityTypeId"], [])
                      if int(r.get(связь[1:]) or 0) in ф[связь] and int(r["id"]) > ф[">id"]]
            return {"result": {"items": записи[:50]}}
        raise AssertionError(f"неожиданный метод {метод}")

    def скачать(self, fo: dict) -> tuple[bytes | None, str]:
        self.скачано.append(fo["id"])
        тело = self.файлы.get(fo["id"])
        return тело, ("" if тело else "urlMachine: код 404")


# --------------------------------------------------------------------------- выдуманная модель
class Поток:
    def __init__(self, ответ):
        self.ответ = ответ

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get_final_message(self):
        return self.ответ


def записи(у: dict) -> str:
    """Условия в удобном тестам виде (поле → значение, evidence — список {field,
    quote, page}) → ответ модели в формате схемы: {side, items: [{field, value,
    quote, page}]}. Значение без цитаты — запись с пустой цитатой; цитата к пустому
    полю — запись с пустым значением."""
    items = []
    цитаты: dict = {}
    for e in у.get("evidence") or []:
        цитаты.setdefault(e["field"], []).append(e)
    for к, v in у.items():
        if к in ("side", "evidence") or v is None or v == []:
            continue
        e = (цитаты.get(к) or [{"quote": "", "page": 0}])[0]
        значения = v if isinstance(v, list) else [v]
        for зн in значения:
            текст = ("true" if зн else "false") if isinstance(зн, bool) else str(зн)
            items.append({"field": к, "value": текст, "quote": e["quote"], "page": e.get("page") or 0})
    for к, ее in цитаты.items():
        if у.get(к) in (None, []):
            items += [{"field": к, "value": "", "quote": e["quote"], "page": e.get("page") or 0} for e in ее]
    return json.dumps({"side": у.get("side") or "unknown", "items": items}, ensure_ascii=False)


def ответ(данные, вход: int = 10_000, выход: int = 2_000, стоп: str = "end_turn"):
    текст = данные if isinstance(данные, str) else (
        записи(данные) if "evidence" in данные else json.dumps(данные, ensure_ascii=False))
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=текст)],
        stop_reason=стоп,
        usage=SimpleNamespace(input_tokens=вход, output_tokens=выход,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0))


def пустые_условия() -> dict:
    у = {к: None for к in ct.поля_условий()}
    у["documents"] = []
    у["evidence"] = []
    return у


ЦИТАТА_ЗАКАЗЧИКА = "ЦИТАТА-ВЫДУМКА: поставка в течение 120 календарных дней, пени 0,1 % в день"
ЦИТАТА_ПОСТАВЩИКА = "ЦИТАТА-ВЫДУМКА: lead time 110 days, EXW ВЫДУМГРАД"
ПУНКТ = "СКЛАД-ВЫДУМКА-ПЕРМЬ"


def условия_заказчика() -> dict:
    у = пустые_условия()
    у.update(side="customer", deliveryDays=120, incoterms="DDP", place=ПУНКТ, penaltyPctDay=0.1,
             acceptance="ПРИЁМКА-ВЫДУМКА при шефмонтаже", warrantyMonths=24)
    у["evidence"] = [{"field": к, "quote": ЦИТАТА_ЗАКАЗЧИКА, "page": 3}
                     for к in ("deliveryDays", "incoterms", "place", "penaltyPctDay", "acceptance", "warrantyMonths")]
    return у


def условия_поставщика() -> dict:
    у = пустые_условия()
    у.update(side="supplier", deliveryDays=110, incoterms="EXW", place="ВЫДУМГРАД", warrantyMonths=12,
             claimDays=30, penaltyPctDay=0.05)
    у["evidence"] = [{"field": к, "quote": ЦИТАТА_ПОСТАВЩИКА, "page": 1}
                     for к in ("deliveryDays", "incoterms", "place", "warrantyMonths", "claimDays", "penaltyPctDay")]
    return у


class Сообщения:
    """Отвечает по содержимому документа: текст с «ПОСТАВЩИК» — условия поставщика."""

    def __init__(self, по_тексту=None):
        self.запросы: list[dict] = []
        self.по_тексту = по_тексту

    def stream(self, **запрос):
        self.запросы.append(запрос)
        документ = next(бл for бл in запрос["messages"][0]["content"] if бл["type"] == "document")
        текст = документ["source"].get("data", "") if документ["source"]["type"] == "text" else ""
        if self.по_тексту:
            return Поток(self.по_тексту(текст))
        return Поток(ответ(условия_поставщика() if "ПОСТАВЩИК" in текст else условия_заказчика()))


class Клиент:
    def __init__(self, по_тексту=None):
        self.messages = Сообщения(по_тексту)


class База:
    def __init__(self, кэш: dict | None = None, читанные: set | None = None):
        self.условия: list[dict] = []
        self.флаги: list[dict] = []
        self.кэш = кэш or {}
        self.читанные = читанные or set()
        self.спрошено_читанных = 0

    def из_кэша(self, sha, модель):
        return self.кэш.get(sha)

    def прочитанные_файлы(self, модель):
        self.спрошено_читанных += 1
        return set(self.читанные)

    def записать_условия(self, строки):
        self.условия += строки

    def записать_итог(self, флаги, исходы, run_id):
        self.флаги += флаги
        self.исходы = dict(исходы)

    def итог(self, run_id):
        return len(self.условия), len(self.флаги), sum(r["usd"] for r in self.условия)

    def итог_сделок(self, run_id):
        return len(getattr(self, "исходы", {}))


def pdf_на(страниц: int) -> bytes:
    from pypdf import PdfWriter
    писатель = PdfWriter()
    for _ in range(страниц):
        писатель.add_blank_page(width=595, height=842)
    буфер = io.BytesIO()
    писатель.write(буфер)
    return буфер.getvalue()


ФАЙЛЫ = {
    "fid-заказчик": "ДОГОВОР ПОСТАВКИ С ЗАКАЗЧИКОМ (выдумка). Срок поставки 120 календарных дней.".encode(),
    "fid-поставщик": "ДОГОВОР С ПОСТАВЩИКОМ (выдумка). Lead time 110 days EXW.".encode(),
    "fid-рамочный": "РАМОЧНЫЙ ДОГОВОР С ЗАКАЗЧИКОМ (выдумка).".encode(),
}


@pytest.fixture(autouse=True)
def чистая_среда(monkeypatch):
    for имя in ("LLM_READ_MODEL", "LLM_READ_MAX_PAGES", "LLM_READ_MAX_MB", "LLM_READ_EFFORT",
                "LLM_READ_RETRIES", "LLM_READ_MAX_CHARS", "RUN_ID", "CONTRACT_TERMS_MODEL"):
        monkeypatch.delenv(имя, raising=False)
    monkeypatch.setattr(rl.time, "sleep", lambda _с: None)


# --------------------------------------------------------------------------- отбор и сторона
def test_отбор_сделок_карточек_и_стороны():
    портал = Портал()
    отбор = ct.отобрать(портал, 2)
    # последние по дате, без проигранной и чужой воронки, не больше двух
    assert [int(d["ID"]) for d in отбор.сделки] == [СДЕЛКА_НОВАЯ, СДЕЛКА_ВТОРАЯ]
    метод, п = портал.вызовы[0]
    assert метод == "crm.deal.list"
    assert п["filter"] == {"CATEGORY_ID": 0, "!STAGE_SEMANTIC_ID": "F"}
    assert п["order"]["DATE_CREATE"] == "DESC"
    списки = [п for м, п in портал.вызовы if м == "crm.item.list"]
    # договоры всех четырёх типов и заказы сделок (поставщики — для стороны по компании)
    по_сделкам = [п for п in списки if "@parentId2" in п["filter"]]
    assert {п["entityTypeId"] for п in по_сделкам} == {*ct.ТИПЫ_ДОГОВОРОВ, ct.СП_ЗАКАЗ}
    for п in списки:
        assert п["start"] == -1 and п["order"] == {"id": "ASC"}
    for п in по_сделкам:
        assert sorted(п["filter"]["@parentId2"]) == sorted([СДЕЛКА_НОВАЯ, СДЕЛКА_ВТОРАЯ])
    # карточки, связанные только с заказом, — по живым заказам сделок; у рамочного без
    # связи с заказом такой запрос не идёт
    по_заказам = [п for п in списки if "@parentId172" in п["filter"]]
    assert {п["entityTypeId"] for п in по_заказам} == {ct.СП_РАЗОВЫЙ, ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ, ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ}
    assert all(п["filter"]["@parentId172"] == [ЗАКАЗ] for п in по_заказам)
    рамочный = next(п for п in по_сделкам if п["entityTypeId"] == ct.СП_РАМОЧНЫЙ)
    assert "parentId172" not in рамочный["select"], "поле, которого у типа нет, спрашивать нельзя"
    assert not отбор.нет_полей
    # стадия заказа — по справочнику воронки, один запрос на воронку
    assert [п["filter"] for м, п in портал.вызовы if м == "crm.status.list"] == [{"ENTITY_ID": "DYNAMIC_172_STAGE_5"}]
    assert отбор.заказы[ЗАКАЗ]["live"] and отбор.заказы[ЗАКАЗ]["sem"] == "P"

    д = {x.card_id: x for x in отбор.все()}
    assert 9800004 not in д, "карточка проигранной сделки попала в отбор"
    заказчик, поставщик, пустая, рамочный_д = д[9800001], д[9800002], д[9800003], д[9800005]
    assert (заказчик.side_link, заказчик.link_source, заказчик.order_id) == ("customer", "deal_link", None)
    assert (поставщик.side_link, поставщик.link_source, поставщик.order_id) == ("supplier", "order_link", ЗАКАЗ)
    # приоритет поля файла: финальная версия, иначе текущий файл
    assert заказчик.file_field == ФИН_1174 and [f["id"] for f in заказчик.files] == ["fid-заказчик"]
    assert поставщик.file_field == ТЕК_1174 and [f["id"] for f in поставщик.files] == ["fid-поставщик"]
    assert пустая.files == [] and пустая.file_field is None
    assert рамочный_д.side_link == "customer" and рамочный_д.file_field == ФИН_1192
    # подписи списков и сверка стороны вторым способом
    assert заказчик.template == "Шаблон заказчика ВЫДУМКА" and заказчик.doc_type == "Спецификация ВЫДУМКА"
    assert (заказчик.side_template, поставщик.side_template, пустая.side_template) == ("customer", "supplier", None)
    assert (заказчик.side_company, поставщик.side_company, рамочный_д.side_company) == ("customer", "supplier", None)
    # в сделке разовый договор раньше рамочного
    assert [x.card_entity for x in отбор.документы[СДЕЛКА_ВТОРАЯ]] == [ct.СП_РАЗОВЫЙ, ct.СП_РАМОЧНЫЙ]


def test_сторона_двойная_связь_и_подписи():
    assert ct.сторона_по_связи({"parentId172": "77", "parentId2": "5"}) == ("supplier", "order_link")
    assert ct.сторона_по_связи({"parentId172": "0", "parentId2": "5"}) == ("customer", "deal_link")
    assert ct.сторона_по_подписи("Шаблон заказчика") == "customer"
    assert ct.сторона_по_подписи("Supplier template") == "supplier"
    assert ct.сторона_по_подписи("Наш шаблон") is None
    assert ct.сторона_по_подписи("Заказчика или поставщика") is None
    assert ct.подпись([11, 12], {"11": "А", "12": "Б"}) == "А / Б"
    assert ct.подпись(None, {"11": "А"}) is None


def test_нет_поля_у_типа_называется_а_не_молчит():
    портал = Портал()
    сохранено = dict(ПОЛЯ[ct.СП_РАМОЧНЫЙ])
    try:
        del ПОЛЯ[ct.СП_РАМОЧНЫЙ]["parentId2"]
        отбор = ct.отобрать(портал, 2)
    finally:
        ПОЛЯ[ct.СП_РАМОЧНЫЙ].clear()
        ПОЛЯ[ct.СП_РАМОЧНЫЙ].update(сохранено)
    assert f"СП-{ct.СП_РАМОЧНЫЙ} parentId2" in отбор.нет_полей
    assert not any(п["entityTypeId"] == ct.СП_РАМОЧНЫЙ for м, п in портал.вызовы if м == "crm.item.list")


# --------------------------------------------------------------------------- схема и ответ
def test_строгая_схема_без_необязательных_и_объединений():
    """Живой прогон 22.10: схема с двадцатью необязательными условиями собиралась 180 с
    и падала «Schema is too complex» на каждом файле. Теперь ни одного
    необязательного поля и ни одного объединения типов."""
    с = ct.СХЕМА_ОТВЕТА
    счёт = {"необязательных": 0, "объединений": 0}

    def обойти(узел, путь="$"):
        if isinstance(узел, dict):
            if isinstance(узел.get("type"), list) or "anyOf" in узел:
                счёт["объединений"] += 1
            for запрет in ("minimum", "maximum", "minLength", "maxLength", "multipleOf"):
                assert запрет not in узел, f"{путь}: {запрет} строгий режим не берёт"
            if узел.get("type") == "object":
                assert узел.get("additionalProperties") is False, путь
                assert set(узел.get("required") or []) <= set(узел.get("properties") or {}), путь
                счёт["необязательных"] += len(set(узел.get("properties") or {}) - set(узел.get("required") or []))
            for к, v in узел.items():
                обойти(v, f"{путь}.{к}")
        elif isinstance(узел, list):
            for i, v in enumerate(узел):
                обойти(v, f"{путь}[{i}]")
    обойти(с)
    assert счёт == {"необязательных": 0, "объединений": 0}, счёт
    поля = с["properties"]["items"]["items"]["properties"]["field"]["enum"]
    # перечень полей — из TERMS_SCHEMA: новое поле там появится и здесь, и в подсказке
    assert set(поля) == set(ct.поля_условий()) - {"side"}
    assert all(f"- {к} (" in ct.СИСТЕМА for к in поля)
    assert с["properties"]["side"]["enum"] == ["customer", "supplier", "unknown"]


def test_значения_записей_приводятся_к_типу_поля():
    items = [{"field": "deliveryDays", "value": "120", "quote": "ц1", "page": 2},
             {"field": "deliveryDays", "value": "90", "quote": "ц2", "page": 3},       # повтор — первая
             {"field": "penaltyPctDay", "value": "0,1 %", "quote": "ц3", "page": 0},
             {"field": "fxFixed", "value": "true", "quote": "ц4", "page": 1},
             {"field": "claimDays", "value": "тридцать", "quote": "ц5", "page": 1},   # не число — снято
             {"field": "penaltyCapPct", "value": "null", "quote": "ц6", "page": 1},   # «нет» словами
             {"field": "documents", "value": "паспорт", "quote": "ц7", "page": 4},
             {"field": "documents", "value": "сертификат ТР ТС", "quote": "ц8", "page": 4},
             {"field": "side", "value": "supplier", "quote": "ц9", "page": 1}]       # side — не запись
    у, док, снято = ct.разобрать_ответ(json.dumps({"side": "unknown", "items": items}, ensure_ascii=False))
    assert у["deliveryDays"] == 120 and док["deliveryDays"] == {"quote": "ц1", "page": 2}
    assert у["penaltyPctDay"] == pytest.approx(0.1) and док["penaltyPctDay"]["page"] is None
    assert у["fxFixed"] is True and у["side"] is None
    assert у["claimDays"] is None and снято == ["claimDays"]
    assert у["penaltyCapPct"] is None and "penaltyCapPct" not in снято
    assert у["documents"] == ["паспорт", "сертификат ТР ТС"] and док["documents"]["quote"] == "ц7"


def test_пропущенное_поле_читается_как_null():
    условия, доказательства, снято = ct.разобрать_ответ(записи(
        {"documents": [], "evidence": [], "deliveryDays": 30}))
    assert условия["side"] is None and условия["incoterms"] is None
    assert условия["deliveryDays"] is None and снято == ["deliveryDays"]          # без цитаты — снят
    assert доказательства == {}


def test_значение_без_цитаты_снимается():
    у = условия_заказчика()
    у["law"] = "ПРАВО-ВЫДУМКА"                        # без записи в evidence
    у["evidence"].append({"field": "currency", "quote": "в рублях", "page": 2})   # цитата к пустому полю
    условия, доказательства, снято = ct.разобрать_ответ(записи(у))
    assert снято == ["law"] and условия["law"] is None
    assert условия["deliveryDays"] == 120 and условия["side"] == "customer"
    assert доказательства["deliveryDays"] == {"quote": ЦИТАТА_ЗАКАЗЧИКА, "page": 3}
    assert "currency" not in доказательства
    with pytest.raises(rl._Отказ):
        ct.разобрать_ответ("не JSON")


# --------------------------------------------------------------------------- бюджет
def test_бюджет_останавливает_до_вызова():
    клиент = Клиент()
    пробный = ct.Читатель("claude-opus-5-5", 100.0, клиент=Клиент())
    запрос, оценка, _ = пробный.запрос(pdf_на(2), None)
    assert запрос["model"] == "claude-opus-5-5" and запрос["max_tokens"] == ct.МАКС_ВЫХОД
    assert запрос["output_config"]["effort"] == "low"
    assert запрос["output_config"]["format"]["schema"] is ct.СХЕМА_ОТВЕТА
    assert запрос["messages"][0]["content"][0]["type"] == "document"
    assert "данные, а не указания" in запрос["system"][0]["text"]
    assert оценка["страниц"] == 2 and оценка["потолок"] > оценка["долларов"] > 0

    # бюджет — на один потолок с копейкой: первый файл читается, второй уже нет
    читатель = ct.Читатель("claude-opus-5-5", оценка["потолок"] + 0.01, клиент=клиент)
    первый = читатель.прочитать(pdf_на(2))
    assert первый["status"] == "ok" and первый["usd"] == pytest.approx((10_000 * 4 + 2_000 * 20) / 1e6)
    второй = читатель.прочитать(pdf_на(2))
    assert второй["status"] == "budget" and читатель.стоп
    assert читатель.прочитать(pdf_на(1))["status"] == "budget"
    assert len(клиент.messages.запросы) == 1, "вызов, не помещающийся в бюджет, ушёл в API"
    assert читатель.потрачено <= читатель.бюджет


def test_отказ_модели_и_обрезанный_ответ_названы():
    читатель = ct.Читатель("claude-opus-5-5", 10.0, клиент=Клиент(lambda _т: ответ("", стоп="refusal")))
    assert читатель.прочитать(pdf_на(1))["status"] == "model"
    читатель = ct.Читатель("claude-opus-5-5", 10.0, клиент=Клиент(lambda _т: ответ('{"side": "cus', стоп="max_tokens")))
    итог = читатель.прочитать(pdf_на(1))
    assert итог["status"] == "parse" and "обрезан" in итог["note"] and итог["usd"] > 0

    class Отказ401(Exception):
        status_code = 401

    def не_принят(_т):
        raise Отказ401()
    клиент = Клиент(не_принят)
    читатель = ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент)
    assert [читатель.прочитать(pdf_на(1))["status"] for _ in range(3)] == ["api"] * 3
    assert len(клиент.messages.запросы) == 1, "ключ не принят — следующий файл модель не зовёт"
    assert not читатель.стоп, "это не бюджет"


# --------------------------------------------------------------------------- строки и флаги
def _строка(связь: str, terms: dict | None, *, status="ok", order=None, card=1, entity=ct.СП_РАЗОВЫЙ,
            компания=None, подпись=None, снято=()):
    """Строка документа: связь — сторона по связи карточки (справочно); сторона
    строки — из terms (модель)."""
    д = ct.Документ(deal_id=СДЕЛКА_НОВАЯ, card_entity=entity, card_id=card, order_id=order, side_link=связь,
                    link_source="order_link" if order else "deal_link", template=None, doc_type=None,
                    side_template=подпись, side_company=компания, file_field=ФИН_1174, files=[файл(f"fid-{card}")])
    итог = {"terms": terms, "evidence": {}, "usd": 0.1, "pages": 3, "dropped": list(снято)} if terms else {}
    return ct.строка("ct-проба", "claude-opus-5-5", д, д.files[0], status=status, sha256="0" * 64, итог=итог)


def test_строки_и_флаги_сделки():
    у_з, _, _ = ct.разобрать_ответ(записи(условия_заказчика()))
    у_п, _, _ = ct.разобрать_ответ(записи(условия_поставщика()))
    з = _строка("customer", у_з, card=1)
    п = _строка("supplier", у_п, card=2, order=ЗАКАЗ)
    assert з["status"] == "ok" and з["terms"]["deliveryDays"] == 120 and з["model"] == "claude-opus-5-5"
    assert (з["side"], з["side_source"], з["side_link"]) == ("customer", "model", "customer")
    assert set(ct.База.КОЛОНКИ) <= set(з)
    флаги, исход = ct.флаги_сделки([з, п])
    коды = {f["code"]: f["days"] for f in флаги}
    assert исход == "пара" and коды["lateByDesign"] == 9                 # 120 − (110 + 19)
    assert {"basisGap", "penaltyUncovered", "penaltyNoCap", "warrantyGap", "claimGap"} <= set(коды)
    assert all(f["order_id"] == ЗАКАЗ and f["deal_id"] == СДЕЛКА_НОВАЯ for f in флаги)

    # договор с заказчиком, привязанный к заказу: связь «поставщик», модель — заказчик;
    # связь сторону не решает (решение владельца 22.10.2026) — спора нет, пары тоже
    к_заказу = _строка("supplier", у_з, card=3, order=ЗАКАЗ)
    assert к_заказу["side"] == "customer" and not ct.сторона_спорная(к_заказу)
    assert ct.флаги_сделки([з, к_заказу]) == ([], "нет пары")
    assert ct.флаги_сделки([з, к_заказу, п])[1] == "пара"
    # компания карточки — поставщик, модель — заказчик: спорная, флагов нет
    спорная = _строка("supplier", у_з, card=6, order=ЗАКАЗ, компания="supplier")
    assert ct.сторона_спорная(спорная)
    assert ct.флаги_сделки([з, спорная, п]) == ([], "спорная")
    # недочитанный файл (бюджет) и нечитаемый (закачка) — флагов нет, а не «чисто»
    for статус, исход_ in (("budget", "недочитана"), ("download", "не читается")):
        assert ct.флаги_сделки([з, п, _строка("supplier", None, status=статус, card=4, order=ЗАКАЗ)]) \
            == ([], исход_)
    # непрочитанная строка не несёт ни условий, ни стороны; связь — справочно остаётся
    отказ = _строка("customer", None, status="format", card=5)
    assert отказ["terms"] is None and отказ["evidence"] is None and отказ["usd"] == 0.0
    assert (отказ["side"], отказ["side_source"], отказ["side_link"]) == (None, None, "customer")


def test_свод_условий_стороны():
    новый = {"terms": {**{к: None for к in ct.поля_условий()}, "deliveryDays": 90, "documents": ["паспорт"]}}
    рамочный = {"terms": {**{к: None for к in ct.поля_условий()}, "deliveryDays": 150, "warrantyMonths": 24,
                          "documents": ["паспорт", "ТР ТС"]}}
    с = ct.свести([новый, рамочный])
    assert с["deliveryDays"] == 90 and с["warrantyMonths"] == 24 and с["documents"] == ["паспорт", "ТР ТС"]


# --------------------------------------------------------------------------- журнал
ЗАПРЕТНОЕ = [str(n) for n in (СДЕЛКА_НОВАЯ, СДЕЛКА_ВТОРАЯ, СДЕЛКА_СТАРАЯ, КОМПАНИЯ_ЗАКАЗЧИКА, ЗАКАЗ,
                              9800001, 9800002, 9800003, 9800005)] + [
    "ЦИТАТА-ВЫДУМКА", ПУНКТ, "ВЫДУМГРАД", "ПРИЁМКА-ВЫДУМКА", "Шаблон заказчика", "Шаблон поставщика",
    "Спецификация ВЫДУМКА", "fid-", "выдумка.invalid", "ДОГОВОР", "шефмонтаж", "DDP", "EXW"]


def _журнал(capsys) -> str:
    вывод = capsys.readouterr()
    return вывод.out + вывод.err


def test_живой_прогон_пишет_в_базу_а_в_журнал_только_агрегаты(capsys):
    портал = Портал(ФАЙЛЫ)
    клиент = Клиент()
    база = База()
    код = ct.прогон("live", 3, 10.0, "claude-opus-5-5", портал=портал, run_id="ct-проба",
                    читатель=ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент), база=база)
    журнал = _журнал(capsys)
    assert код == 0
    # не пусто: строки в базе несут то, чего нет в журнале
    assert len(база.условия) == 4                     # 2 файла сделки + без файла + рамочный
    по_карточке = {r["card_id"]: r for r in база.условия}
    assert по_карточке[9800001]["terms"]["place"] == ПУНКТ
    assert по_карточке[9800001]["evidence"]["deliveryDays"]["quote"] == ЦИТАТА_ЗАКАЗЧИКА
    assert по_карточке[9800003]["status"] == "no_file"
    assert {f["code"] for f in база.флаги} >= {"lateByDesign", "basisGap"}
    assert all(r["run_id"] == "ct-проба" for r in база.условия)
    assert len(клиент.messages.запросы) == 3
    # журнал: только числа и собственные коды
    for слово in ЗАПРЕТНОЕ:
        assert слово not in журнал, f"в журнал попало «{слово}»"
    assert "флаги: " in журнал and "lateByDesign" in журнал
    assert "ok 3" in журнал and "no_file 1" in журнал


def test_повтор_файла_берётся_из_базы_даром(capsys):
    import hashlib
    sha = hashlib.sha256(ФАЙЛЫ["fid-заказчик"]).hexdigest()
    у, док, _ = ct.разобрать_ответ(записи(условия_заказчика()))
    база = База(кэш={sha: {"terms": у, "evidence": док, "pages": 3, "note": None}})
    клиент = Клиент()
    ct.прогон("live", 3, 10.0, "claude-opus-5-5", портал=Портал(ФАЙЛЫ), run_id="ct-проба",
              читатель=ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент), база=база)
    _журнал(capsys)
    r = next(r for r in база.условия if r["card_id"] == 9800001)
    assert r["status"] == "reused" and r["usd"] == 0.0 and r["terms"]["deliveryDays"] == 120
    assert len(клиент.messages.запросы) == 2


def test_спорные_стороны_снимают_флаги_прогона(capsys):
    # модель называет любой документ договором с заказчиком: поставщик «спорный»
    база = База()
    код = ct.прогон("live", 3, 10.0, "claude-opus-5-5", портал=Портал(ФАЙЛЫ), run_id="ct-проба",
                    читатель=ct.Читатель("claude-opus-5-5", 10.0,
                                         клиент=Клиент(lambda _т: ответ(условия_заказчика()))),
                    база=база)
    журнал = _журнал(capsys)
    assert код == 1 and база.флаги == [] and "::error::" in журнал
    assert len(база.условия) == 4, "оплаченные строки условий должны остаться"


def test_холостой_прогон_не_качает_и_не_зовёт_модель(capsys):
    портал = Портал(ФАЙЛЫ)
    код = ct.прогон("dry", 30, 10.0, "claude-opus-5-5", портал=портал, run_id="ct-проба")
    журнал = _журнал(capsys)
    assert код == 0 and портал.скачано == []
    assert {м for м, _п in портал.вызовы} == {"crm.deal.list", "crm.item.fields", "crm.item.list", "crm.status.list"}
    assert "claude-opus-5-5: файлов 3" in журнал and "claude-sonnet-5-5" in журнал
    assert "холостой прогон" in журнал
    for слово in ЗАПРЕТНОЕ:
        assert слово not in журнал, f"в журнал попало «{слово}»"


def test_оценка_денег_растёт_со_страницами():
    о = ct.оценка_денег(10, "claude-opus-5-5")
    assert 0 < о["мало"] < о["обычно"] < о["много"]
    assert ct.оценка_денег(10, "claude-sonnet-5-5")["обычно"] == pytest.approx(о["обычно"] / 2)


# --------------------------------------------------------------------------- прогон Actions
def test_прогон_ручной_и_недельный_в_очереди_портала_без_артефактов():
    import re
    import yaml
    путь = ROOT / ".github" / "workflows" / "contract-terms.yml"
    текст = путь.read_text(encoding="utf-8")
    wf = yaml.safe_load(текст)
    on = wf.get("on", wf.get(True))
    # ручной запуск и недельный от планировщика (решение владельца 23.10.2026); крона GitHub нет
    assert set(on) == {"workflow_dispatch", "repository_dispatch"}
    assert on["repository_dispatch"]["types"] == ["contract-terms"]
    входы = on["workflow_dispatch"]["inputs"]
    assert входы["deals"]["default"] == "30" and входы["budget_usd"]["default"] == "10"
    assert входы["mode"]["default"] == "dry" and входы["mode"]["options"] == ["dry", "live", "reflag"]
    # пересчёт без модели пишет только с apply (по умолчанию — холостой)
    assert входы["apply"]["default"] is False and "inputs.apply" in wf["jobs"]["terms"]["env"]["APPLY"]
    assert wf["concurrency"]["group"] == "bitrix-portal"
    for запрет in ("upload-artifact", "actions/cache", "cache:"):
        assert запрет not in текст, запрет
    job = wf["jobs"]["terms"]
    # недельный: live, все открытые (до предела скрипта), бюджет 10, срок 150 мин
    je = job["env"]
    assert "'live'" in je["MODE"] and "'400'" in je["DEALS"] and "'10'" in je["BUDGET_USD"]
    assert "'--open'" in je["OPEN"] and "'150'" in je["DEADLINE_MIN"] and "'40'" in je["DEADLINE_MIN"]
    # ручной срок чтения — входом (разовое дочитывание 25.10.2026: 150 мин), проверка до
    # установки: срок плюс установка, один файл после срока и запись — в timeout job
    assert входы["deadline_min"]["default"] == str(ct.СРОК_МИН_ПО_УМОЛЧАНИЮ)
    assert "inputs.deadline_min" in je["DEADLINE_MIN"]
    проверка = job["steps"][0]
    assert проверка["name"] == "Проверка входов" and f"d > {ct.СРОК_МИН_МАКС}" in проверка["run"]
    assert "d < 1" in проверка["run"], "0 у скрипта — «без срока»: в job его не пропускать"
    assert "${{" not in проверка["run"], "вход — через env, не подстановкой в текст скрипта"
    assert ct.СРОК_МИН_МАКС * 60 + 300 < job["timeout-minutes"] * 60
    assert int(re.search(r"'(\d+)'", je["DEALS"]).group(1)) <= ct.СДЕЛОК_МАКС
    шаг = next(ш for ш in job["steps"] if str(ш.get("run", "")).startswith("python scripts/contract_terms.py"))
    env = шаг["env"]
    for секрет in ("BITRIX_WEBHOOK_URL", "BITRIX_FILES_WEBHOOK_URL", "ANTHROPIC_API_KEY", "SUPABASE_DB_URL"):
        assert f"secrets.{секрет}" in env[секрет], секрет
    for секрет in ("ANTHROPIC_API_KEY", "SUPABASE_DB_URL"):
        assert "inputs.mode == 'live'" in env[секрет] and "repository_dispatch" in env[секрет], секрет
    assert float(env["BITRIX_RPS"]) <= 2 and env["BITRIX_PARALLEL"] == "1"
    assert "'ct'" in env["RUN_ID"] and "'ctr'" in env["RUN_ID"] and "github.run_id" in env["RUN_ID"]
    # пересчёту не нужны ни модель, ни портал
    assert "inputs.mode == 'reflag'" not in env["ANTHROPIC_API_KEY"]
    for секрет in ("BITRIX_WEBHOOK_URL", "BITRIX_FILES_WEBHOOK_URL"):
        assert "inputs.mode != 'reflag'" in env[секрет], секрет
    assert "$APPLY" in шаг["run"]
    assert '--deadline-min "$DEADLINE_MIN"' in шаг["run"] and "$OPEN" in шаг["run"]
    # обрезанный файл модели не отдаётся — предел страниц не меньше длинного договора
    assert int(env["LLM_READ_MAX_PAGES"]) >= 100
    # одна попытка вызова: оборванная могла быть оплачена, бюджет — перед вызовом
    assert env["LLM_READ_RETRIES"] == "1" and int(env["LLM_READ_TIMEOUT"]) <= 300
    # срок прогона (самый длинный — недельный) короче таймаута job с запасом на закачку, вызов и запись
    assert 150 * 60 + 2 * int(env["LLM_READ_TIMEOUT"]) < job["timeout-minutes"] * 60
    # срок считается от начала чтения: до него установка и проверки (5 минут), после последней
    # проверки срока — один файл (ожидание лимита портала, три закачки по 90 с, вызов модели)
    после_срока = int(env["BITRIX_WAIT_BUDGET"]) + 3 * 90 + int(env["LLM_READ_TIMEOUT"])
    assert ct.СРОК_МИН_МАКС * 60 + 5 * 60 + после_срока <= job["timeout-minutes"] * 60
    установка = next(ш["run"] for ш in job["steps"] if "pip install" in str(ш.get("run")))
    assert "pillow" in установка, "без Pillow сканы TIFF — статус format, сделка без флагов"
    # после чтения — свод в закрытый ключ, только в live
    свод = job["steps"][-1]
    assert свод["run"] == "python scripts/realization_ctx.py --publish" and "env.MODE == 'live'" in свод["if"]
    assert "!cancelled()" in свод["if"]


def test_схема_подключена_к_миграциям():
    миграции = (ROOT / ".github/workflows/zip-db.yml").read_text(encoding="utf-8")
    assert "apply library/supabase/contract_terms_schema.sql" in миграции
    assert (миграции.index("apply library/supabase/contract_terms_schema.sql")
            < миграции.index("apply zip/supabase/migrations_rls_stage1.sql"))


# --------------------------------------------------------------------------- правки по разбору (19.10.2026)
def _условия(данные: dict) -> tuple[dict, list[str]]:
    у, _док, снято = ct.разобрать_ответ(записи(данные))
    return у, снято


def test_обрезанный_файл_модели_не_отдаётся_и_сделка_без_флагов(monkeypatch, capsys):
    """Файл, который и по частям не прочесть целиком (страниц больше предела чтения по
    частям): условия первых страниц — не условия договора (потолок пеней, гарантия,
    документы — в конце). Строка limit, модель не зовётся, сделка «не читается» (нужен
    человек: разделить файл), флагов нет, в кэш ничего не кладётся. Обрезка read_llm (не
    PDF, который pypdf не открыл) — тоже limit."""
    monkeypatch.setenv("LLM_READ_MAX_PAGES", "20")
    monkeypatch.setattr(ct, "СТРАНИЦ_ПО_ЧАСТЯМ_МАКС", 24)
    файлы = dict(ФАЙЛЫ, **{"fid-поставщик": pdf_на(25)})
    клиент = Клиент()
    база = База()
    читатель = ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент)
    код = ct.прогон("live", 3, 10.0, "claude-opus-5-5", портал=Портал(файлы), run_id="ct-проба",
                    читатель=читатель, база=база)
    журнал = _журнал(capsys)
    r = next(r for r in база.условия if r["card_id"] == 9800002)
    assert r["status"] == "limit" and r["terms"] is None and r["usd"] == 0.0 and r["model"] is None
    assert "24" in r["note"] and "25" in r["note"]
    assert len(клиент.messages.запросы) == 2, "обрезанный файл ушёл в модель"
    assert база.флаги == [] and "не читается 1" in журнал and код == 0
    assert читатель.обрезано == 1 and "больше предела чтения: 1" in журнал
    # на границе предела файл целый и читается
    _, оценка, _ = читатель.запрос(pdf_на(20), None)
    assert not оценка["обрезан"] and оценка["всего_страниц"] == 20
    # обрезку read_llm (PDF, который pypdf не делит, — по подписи страниц) модель не видит
    monkeypatch.setattr(ct, "части_pdf", lambda _b: None)
    итог = ct.Читатель("claude-opus-5-5", 10.0, клиент=Клиент()).прочитать(pdf_на(25))
    assert итог["status"] == "limit" and итог["pages"] == 25 and "20" in итог["note"]


def test_модель_не_называет_сторону_гейт_красный(capsys):
    """Наше имя в договорах не то, что в подсказке: модель стороны не называет ни
    разу. «Спорных нет» тогда ничего не значит — флаги не пишутся, прогон красный."""
    def без_стороны(текст):
        у = условия_поставщика() if "ПОСТАВЩИК" in текст else условия_заказчика()
        у.pop("side")
        return ответ(у)
    база = База()
    код = ct.прогон("live", 3, 10.0, "claude-opus-5-5", портал=Портал(ФАЙЛЫ), run_id="ct-проба",
                    читатель=ct.Читатель("claude-opus-5-5", 10.0, клиент=Клиент(без_стороны)), база=база)
    журнал = _журнал(capsys)
    assert код == 1 and база.флаги == [] and "::error::модель назвала сторону у 0 из 3" in журнал
    assert len(база.условия) == 4, "оплаченные строки условий должны остаться"


def test_компания_против_модели_делает_документ_спорным_а_связь_и_подпись_нет():
    """Сторону называет модель (решение владельца 22.10.2026). Компания карточки
    сверяет: назвала другую — документ спорный, сделка флагов не получает вовсе.
    Связь с заказом и подпись «Чей шаблон» — справочно: договор с заказчиком
    привязывают к заказу, шаблон заказчика бывает у договора с поставщиком."""
    у_з, _ = _условия(условия_заказчика())
    у_п, _ = _условия(условия_поставщика())
    у_п_без_стороны = {**у_п, "side": None}               # модель промолчала
    з = _строка("customer", у_з, card=1, компания="customer", подпись="customer")
    п = _строка("supplier", у_п, card=2, order=ЗАКАЗ, компания="supplier")
    по_компании = _строка("customer", у_п, card=3, компания="customer")
    по_связи = _строка("supplier", у_з, card=4, order=ЗАКАЗ)
    по_подписи = _строка("customer", у_п, card=5, подпись="customer")
    без_стороны = _строка("customer", у_п_без_стороны, card=6)    # и компания о стороне молчит
    assert not ct.сторона_спорная(з) and not ct.сторона_спорная(п)
    assert ct.сторона_спорная(по_компании)
    assert not ct.сторона_спорная(по_связи) and not ct.сторона_спорная(по_подписи)
    assert без_стороны["side"] is None and not ct.сторона_спорная(без_стороны)
    assert ct.флаги_сделки([з, п])[1] == "пара"
    assert ct.флаги_сделки([з, п, по_связи])[1] == "пара", "договор с заказчиком у заказа — в своде заказчика"
    assert ct.флаги_сделки([з, п, по_компании]) == ([], "спорная")
    assert ct.флаги_сделки([з, п, без_стороны]) == ([], "без стороны")
    # гейт: знаменатель — документы со стороной модели, о которых сказала компания; документ
    # — файл карточки, а не строка: повтор того же документа (рамочный у двух сделок) счёт
    # не меняет
    гейт, журнал = ct.гейт_стороны([з, п, по_компании, по_компании, по_подписи, по_связи])
    assert not гейт and any("спорных документов" in s and "1 из 3" in s for s in журнал)
    assert any("сторона по модели ↔ связь (справочно" in s and "совпала 2 · разошлась 3" in s for s in журнал)
    assert ct.гейт_стороны([з, п])[0]
    # без стороны у меньшинства — гейт зелёный, у большинства — красный
    assert ct.гейт_стороны([з, п, без_стороны])[0]
    без_стороны_2 = _строка("customer", у_п_без_стороны, card=7)
    гейт, журнал = ct.гейт_стороны([з, без_стороны, без_стороны_2])
    assert not гейт and any("модель назвала сторону у 1 из 3" in s for s in журнал)


def test_договор_поставщика_без_заказа_снимает_флаги_сделки():
    """Модель назвала договор поставщиком, а связи с заказом СП-172 у карточки нет:
    к какому заказу и поставщику его условия — неизвестно. Общий свод таких
    договоров разных поставщиков спрятал бы риск одного за условиями другого, а
    рамочный без связи рядом со спецификацией по заказу дал бы ложный флаг."""
    у_з, _ = _условия(условия_заказчика())
    у_п, _ = _условия(условия_поставщика())
    з = _строка("customer", у_з, card=1)
    по_заказу = _строка("supplier", у_п, card=2, order=ЗАКАЗ)
    без_заказа_а = _строка("customer", у_п, card=3)
    без_заказа_б = _строка("customer", {**у_п, "penaltyPctDay": None, "documents": []}, card=4,
                           entity=ct.СП_РАМОЧНЫЙ)
    assert ct.флаги_сделки([з, по_заказу])[1] == "пара"
    assert ct.флаги_сделки([з, без_заказа_а, без_заказа_б]) == ([], "поставщик без заказа")
    assert ct.флаги_сделки([з, по_заказу, без_заказа_б]) == ([], "поставщик без заказа")
    флаги, _ = ct.флаги_сделки([з, по_заказу])
    assert флаги and all(f["order_id"] == ЗАКАЗ for f in флаги), "флаг без заказа не пишется"


def test_спорный_документ_снимает_все_флаги_сделки():
    """Сделка: спецификация заказчика, спорное ДС (компания — клиент сделки, модель
    назвала поставщика; сменило срок) и договор поставщика. Флаги по одной
    спецификации соврали бы — их нет совсем."""
    у_з, _ = _условия(условия_заказчика())
    у_п, _ = _условия(условия_поставщика())
    спецификация = _строка("customer", у_з, card=1)
    дс = _строка("customer", {**у_з, "side": "supplier", "deliveryDays": 200}, card=2, компания="customer")
    поставщик = _строка("supplier", у_п, card=3, order=ЗАКАЗ)
    assert ct.флаги_сделки([спецификация, поставщик])[0], "без спорного флаги есть"
    assert ct.флаги_сделки([спецификация, дс, поставщик]) == ([], "спорная")


def test_снятое_без_цитаты_не_даёт_флага_отсутствия():
    """Потолок пеней модель нашла, но без цитаты: значение снято. «Пени без
    потолка» по нему ставить нельзя — это сказало бы обратное договору."""
    з_данные = условия_заказчика()
    з_данные["penaltyCapPct"] = 10                         # без записи в evidence
    у_з, снято = _условия(з_данные)
    assert снято == ["penaltyCapPct"] and у_з["penaltyCapPct"] is None
    у_п, _ = _условия(условия_поставщика())
    п = _строка("supplier", у_п, card=2, order=ЗАКАЗ)

    снятые: Counter = Counter()
    флаги, исход = ct.флаги_сделки([_строка("customer", у_з, card=1, снято=снято), п], снятые)
    коды = {f["code"] for f in флаги}
    assert исход == "пара" and "penaltyNoCap" not in коды and снятые == Counter({"penaltyNoCap": 1})
    assert "lateByDesign" in коды, "флаги на присутствии условий остаются"
    # то же условие, которого в договоре просто нет, — флаг есть
    assert "penaltyNoCap" in {f["code"] for f in ct.флаги_сделки([_строка("customer", у_з, card=1), п])[0]}
    # снятое поле, которое другой документ стороны заполнил, — не «не доказано»
    с_потолком = {**у_з, "penaltyCapPct": 10}
    флаги, _ = ct.флаги_сделки([_строка("customer", у_з, card=1, снято=снято),
                                _строка("customer", с_потолком, card=3), п])
    assert "penaltyNoCap" not in {f["code"] for f in флаги}


def test_оборванный_вызов_списывается_в_бюджет_по_оценке(monkeypatch):
    class APIConnectionError(Exception):
        pass

    вызовов = {"n": 0}

    def обрыв_потом_ответ(_т):
        вызовов["n"] += 1
        if вызовов["n"] == 1:
            raise APIConnectionError()
        return ответ(условия_заказчика())

    monkeypatch.setenv("LLM_READ_RETRIES", "1")
    клиент = Клиент(обрыв_потом_ответ)
    читатель = ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент)
    _, оценка, _ = читатель.запрос(pdf_на(2), None)
    итог = читатель.прочитать(pdf_на(2))
    assert итог["status"] == "api" and "списано по оценке" in итог["note"]
    assert читатель.потрачено == pytest.approx(оценка["долларов"]) and читатель.оборвано == 1
    строка = _строка("customer", None, status="api")
    assert строка["usd"] == 0.0, "в строку — только оплата по usage"
    # бюджет на остаток после списания: следующий файл уже не помещается
    тесный = ct.Читатель("claude-opus-5-5", оценка["долларов"] + оценка["потолок"] - 0.01,
                         клиент=Клиент(lambda _т: (_ for _ in ()).throw(APIConnectionError())))
    assert тесный.прочитать(pdf_на(2))["status"] == "api"
    assert тесный.прочитать(pdf_на(2))["status"] == "budget" and тесный.стоп
    # повтор внутри вызова, кончившийся ответом, тоже списывается: первая попытка оборвана
    monkeypatch.setenv("LLM_READ_RETRIES", "2")
    вызовов["n"] = 0
    повторный = ct.Читатель("claude-opus-5-5", 10.0, клиент=Клиент(обрыв_потом_ответ))
    итог = повторный.прочитать(pdf_на(2))
    assert итог["status"] == "ok" and повторный.оборвано == 1
    assert повторный.потрачено == pytest.approx(оценка["долларов"] + итог["usd"])


def test_срок_прогона_останавливает_чтение_но_не_запись(capsys):
    """Срок вышел после первого файла: остальные — статус time, сделки
    «недочитаны», а строки, флаги и итог прогон дописывает сам, а не таймаут job."""
    отсчёты = iter([0.0, 0.0] + [10_000.0] * 50)
    база = База()
    клиент = Клиент()
    код = ct.прогон("live", 3, 10.0, "claude-opus-5-5", портал=Портал(ФАЙЛЫ), run_id="ct-проба",
                    читатель=ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент), база=база,
                    срок_мин=1, часы=lambda: next(отсчёты))
    журнал = _журнал(capsys)
    статусы = Counter(r["status"] for r in база.условия)
    assert статусы == Counter({"ok": 1, "time": 2, "no_file": 1})
    assert len(клиент.messages.запросы) == 1 and база.флаги == [] and код == 0
    assert "остановлено сроком" in журнал and "time 2" in журнал and "в базе по ключу прогона" in журнал


def test_версия_метода_выведена_из_подсказки_и_схемы():
    assert ct.METHOD_VER == ct.версия_метода() and ct.METHOD_VER.startswith(ct.ВЕРСИЯ_РУЧНАЯ + ":")
    assert ct.версия_метода(система=ct.СИСТЕМА + " ") != ct.METHOD_VER
    assert ct.версия_метода(просьба=ct.ПРОСЬБА + " ") != ct.METHOD_VER
    assert ct.версия_метода(усилие="medium") != ct.METHOD_VER
    схема = json.loads(json.dumps(ct.СХЕМА_ОТВЕТА))
    схема["properties"]["items"]["description"] = "ВЫДУМКА"
    assert ct.версия_метода(схема=схема) != ct.METHOD_VER
    # строка и ключ повтора несут полную версию
    assert _строка("customer", None, status="no_file")["method_ver"] == ct.METHOD_VER


def test_закачка_файла_поля_только_по_urlmachine():
    """id файла поля CRM — номер b_file, не объекта Диска: indexer.download не
    должен получить его и уйти в disk.file.get."""
    отдано: list[dict] = []

    class Индексатор:
        @staticmethod
        def download(fo, rec):
            отдано.append(dict(fo))
            rec["reason"] = "urlMachine: код 404"
            return None

        @staticmethod
        def bx(метод, параметры):
            raise AssertionError(f"неожиданный вызов {метод}")

    портал = ct.Портал.__new__(ct.Портал)
    портал._ix = Индексатор
    тело, почему = портал.скачать(файл("fid-проба"))
    assert тело is None and "404" in почему
    assert отдано == [{"urlMachine": "https://выдумка.invalid/fid-проба"}]
    тело, почему = портал.скачать({"id": "fid-без-ссылки", "url": "/выдумка"})
    assert тело is None and "urlMachine" in почему and len(отдано) == 1, "без urlMachine качать нечем"


def test_сторона_по_компании_поставщик_только_из_заказов_сделки():
    """Третье юрлицо (дочка клиента, логист) о стороне не говорит; поставщик —
    компания заказа этой сделки; без списка поставщиков — прежнее правило."""
    сделка = {"COMPANY_ID": str(КОМПАНИЯ_ЗАКАЗЧИКА)}
    карточка = lambda к: {"companyId": к}
    поставщики = {КОМПАНИЯ_ПОСТАВЩИКА}
    assert ct.сторона_по_компании(карточка(КОМПАНИЯ_ЗАКАЗЧИКА), сделка, поставщики) == "customer"
    assert ct.сторона_по_компании(карточка(КОМПАНИЯ_ПОСТАВЩИКА), сделка, поставщики) == "supplier"
    assert ct.сторона_по_компании(карточка(9699999), сделка, поставщики) is None
    assert ct.сторона_по_компании(карточка(9699999), сделка) == "supplier"
    assert ct.чья_компания(карточка(9699999), сделка, поставщики) == "other"
    assert ct.чья_компания(карточка(None), сделка, поставщики) == "none"


def test_холостой_прогон_разбирает_чью_компанию(capsys):
    отбор = ct.отобрать(Портал(), 2)
    журнал = "\n".join(ct.сверка_сторон(отбор.все()))
    assert "компания карточки (все карточки, и без файла): компания сделки 1 · поставщик заказов сделки 1" in журнал
    # сторону dry не знает: печатает, что скажет компания у документов с файлом
    assert ("сторона — по модели после чтения (решение владельца 22.10.2026); у документов с файлом компания "
            "карточки скажет: заказчик 1 · поставщик 1 · не говорит 1") in журнал
    assert "связь карточки с заказом ↔ компания (справочно, сторону не решает): совпала 2" in журнал



def test_откуда_компания_карточки_с_заказом():
    """Компания клиента в карточке, привязанной к заказу: скопирована из сделки или
    у самого заказа компания клиента — счёт различает; время заведения — рядом."""
    сделка = {"COMPANY_ID": str(КОМПАНИЯ_ЗАКАЗЧИКА)}
    заказы = {ЗАКАЗ: {"deal": СДЕЛКА_НОВАЯ, "company": КОМПАНИЯ_ПОСТАВЩИКА,
                      "created": "2026-03-02T10:00:00+03:00"},
              ЗАКАЗ + 1: {"deal": СДЕЛКА_НОВАЯ, "company": КОМПАНИЯ_ЗАКАЗЧИКА, "created": ""}}

    def карточка(к, заказ=ЗАКАЗ, когда="2026-03-01T10:00:00+03:00"):
        return {"companyId": к, "parentId172": заказ, "createdTime": когда}

    assert ct.откуда_компания(карточка(КОМПАНИЯ_ЗАКАЗЧИКА), сделка, заказы) == ("deal", True)
    assert ct.откуда_компания(карточка(КОМПАНИЯ_ПОСТАВЩИКА, когда="2026-03-03T10:00:00+03:00"),
                              сделка, заказы) == ("order", False)
    assert ct.откуда_компания(карточка(КОМПАНИЯ_ЗАКАЗЧИКА, ЗАКАЗ + 1), сделка, заказы) == ("order_is_client", None)
    assert ct.откуда_компания(карточка(9699999), сделка, заказы) == ("other", True)
    assert ct.откуда_компания(карточка(None), сделка, заказы) == ("none", True)
    assert ct.откуда_компания(карточка(КОМПАНИЯ_ЗАКАЗЧИКА, 9799999), сделка, заказы) == ("order_unknown", None)
    assert ct.откуда_компания({"companyId": КОМПАНИЯ_ЗАКАЗЧИКА, "parentId172": 0}, сделка, заказы) == ("", None)
    # время без пояса против времени с поясом не сравнивается
    assert ct.откуда_компания(карточка(КОМПАНИЯ_ЗАКАЗЧИКА, когда="2026-03-01T10:00:00"), сделка, заказы) == \
        ("deal", None)


def test_разбор_спорных_поставщик_заказчик():
    def док(сделка, заказ, сторона, по_компании, чья, происхождение, раньше, подпись=None):
        return ct.Документ(deal_id=сделка, card_entity=ct.СП_РАЗОВЫЙ, card_id=сделка * 10 + (заказ or 0),
                           order_id=заказ, side_link=сторона, link_source="", template=None, doc_type=None,
                           side_template=подпись, side_company=по_компании, file_field=None,
                           company_rel=чья, company_origin=происхождение, card_before_order=раньше)

    документы = [
        # сделка 1: у заказа 11 две карточки — с компанией клиента и с компанией поставщика
        док(1, 11, "supplier", "customer", "deal", "deal", True, "customer"),
        док(1, 11, "supplier", "supplier", "supplier", "order", False),
        # сделка 2: договора с заказчиком по связи нет, спорная карточка заведена позже заказа
        док(2, 21, "supplier", "customer", "deal", "deal", False),
        # сделка 3: договор с заказчиком по связи есть
        док(3, 31, "supplier", "customer", "deal", "deal", None, "supplier"),
        док(3, None, "customer", "customer", "deal", "", None),
    ]
    журнал = "\n".join(ct.разбор_спорных(документы))
    assert ("карточки со связью с заказом (4): компания как у своего заказа 1 · как у сделки 3 · "
            "у самого заказа компания клиента 0 · другая 0 · не указана 0 · заказ не из сделки 0") in журнал
    assert ("связь «поставщик», компания «заказчик» (3): у сделки нет документа с заказчиком по связи 2 · "
            "у того же заказа есть карточка с компанией поставщика 1 · карточка заведена раньше своего заказа: "
            "да 1, нет 1, не известно 1 · подпись говорит: заказчик 1, поставщик 1, молчит 1") in журнал
    assert ct.разбор_спорных([документы[-1]]) == []


def test_холостой_прогон_считает_заказы_сделок():
    журнал = "\n".join(ct.журнал_отбора(ct.отобрать(Портал(), 2)))
    assert "заказов СП-172 у сделок выборки: 1, из них с компанией клиента сделки 0" in журнал
    assert "карточки со связью с заказом (1): компания как у своего заказа 1" in журнал


def test_причины_отказов_агрегатом_без_номеров():
    строки = [{"status": "api", "note": "сбой сети (APITimeoutError), попыток 1; в бюджет прогона списано"},
              {"status": "api", "note": "сбой сети (APITimeoutError), попыток 1; в бюджет прогона списано"},
              {"status": "api", "note": "API отказал (400): файл 9812345 слишком большой"},
              {"status": "time", "note": "срок прогона вышел"}, {"status": "ok", "note": None},
              {"status": "format", "note": None}]
    п = ct.причины_отказов(строки)
    assert п["api: сбой сети (APITimeoutError), попыток 1"] == 2
    assert п["api: API отказал (400): файл … слишком большой"] == 1
    assert п["format: без заметки"] == 1 and sum(п.values()) == 4, "срок и прочитанное — не отказы"


def test_отказ_схемы_останавливает_чтение_после_первого_вызова():
    """Живой прогон 22.10: «Schema is too complex» на каждом из 14 файлов по 3 минуты.
    Отказ запросу целиком не лечится следующим файлом — модель больше не зовётся."""
    class Отказ400(Exception):
        status_code = 400
        body = {"error": {"message": "Schema is too complex for compilation."}}

    def отказ(_т):
        raise Отказ400()
    клиент = Клиент(отказ)
    читатель = ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент)
    итоги = [читатель.прочитать(pdf_на(1)) for _ in range(3)]
    assert [и["status"] for и in итоги] == ["api"] * 3 and "too complex" in итоги[-1]["note"]
    assert len(клиент.messages.запросы) == 1


def test_список_исходов_в_схеме_как_в_коде():
    """Исход разбора сделки (contract_deal_outcome, 23.10.2026): список в проверке базы —
    дословно contract_terms.ИСХОДЫ, а исходы флаги_сделки — из него."""
    import re
    import contract_terms as ct
    текст = (ROOT / "library" / "supabase" / "contract_terms_schema.sql").read_text(encoding="utf-8")
    блок = текст[текст.index("add constraint contract_deal_outcome_chk"):]
    блок = блок[:блок.index(";")]
    assert tuple(re.findall(r"'([^']+)'", блок)) == ct.ИСХОДЫ
    src = (ROOT / "scripts" / "contract_terms.py").read_text(encoding="utf-8")
    тело = src[src.index("def флаги_сделки"):src.index("# --------------------------------------------------------------------------- база")]
    for исход in re.findall(r'return \[\], "([^"]+)"', тело) + ["пара"]:
        assert исход in ct.ИСХОДЫ, исход


def test_открытые_сделки_выбираются_фильтром_портала():
    """--open (недельный прогон по расписанию): фильтр портала — открытые сделки
    реализации, и он проверяется ещё раз на ответе."""
    import contract_terms as ct
    запросы = []

    class Портал:
        def вызов(self, метод, параметры):
            запросы.append(параметры["filter"])
            return {"result": [
                {"ID": "11", "DATE_CREATE": "2026-10-01", "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0"},
                {"ID": "12", "DATE_CREATE": "2026-10-02", "STAGE_SEMANTIC_ID": "S", "CATEGORY_ID": "0"},
                {"ID": "13", "DATE_CREATE": "2026-10-03", "STAGE_SEMANTIC_ID": "F", "CATEGORY_ID": "0"}]}

    assert [d["ID"] for d in ct.выбрать_сделки(Портал(), 5, открытые=True)] == ["11"]
    assert запросы[-1] == {"CATEGORY_ID": ct.КАТЕГОРИЯ_РЕАЛИЗАЦИИ, "STAGE_SEMANTIC_ID": "P"}
    assert [d["ID"] for d in ct.выбрать_сделки(Портал(), 5)] == ["12", "11"]
    assert запросы[-1] == {"CATEGORY_ID": ct.КАТЕГОРИЯ_РЕАЛИЗАЦИИ, "!STAGE_SEMANTIC_ID": "F"}


# --------------------------------------------------------------------------- новые источники (зонд v57, 25.10.2026)
П1, П2, П3, П4, П5, П6 = 9600011, 9600012, 9600013, 9600014, 9600015, 9600016
З1, З2, З3, З4, З5, З6, З7 = 9700011, 9700012, 9700013, 9700014, 9700015, 9700016, 9700017
РАМ_175, РАМ_1192 = 9820001, 9830003
КОМПАНИЯ_ВТОРОЙ = 9600005                     # компания СДЕЛКА_ВТОРАЯ в СДЕЛКИ
ПОЛЯ_НОВЫЕ = {**ПОЛЯ, ct.СП_РАМОЧНЫЙ: {**ПОЛЯ[ct.СП_РАМОЧНЫЙ], "parentId172": {}, "companyId": {}},
              ct.СП_РАЗОВЫЙ: {**ПОЛЯ[ct.СП_РАЗОВЫЙ], "parentId1192": {}, "parentId175": {}}}
КАРТОЧКИ_НОВЫЕ = {
    ct.СП_РАЗОВЫЙ: [
        # договор с заказчиком (компания сделки), привязанный к заказу З5: З5 без документа поставщика
        {"id": 9800101, "parentId2": СДЕЛКА_ВТОРАЯ, "parentId172": З5, "companyId": КОМПАНИЯ_ВТОРОЙ,
         ФИН_1174: файл("fid-1174-заказчик")},
        # связь со сделкой не из выборки и с живым заказом З1 сделки выборки — документ той
        # сделки, а не сделки заказа: не берётся
        {"id": 9800102, "parentId2": 9900099, "parentId172": З1, "companyId": П1, ФИН_1174: файл("fid-1174-з1")},
        # только с заказом, проваленным по суффиксу стадии
        {"id": 9800103, "parentId2": 0, "parentId172": З7, ФИН_1174: файл("fid-1174-з7")},
        # спецификация под рамочным СП-1192, который сам связан со сделкой
        {"id": 9800104, "parentId2": СДЕЛКА_НОВАЯ, "parentId172": 0, ФИН_1174: файл("fid-1174-спец"),
         "parentId1192": РАМ_1192},
    ],
    ct.СП_РАМОЧНЫЙ: [
        {"id": 9830001, "parentId2": 0, "parentId172": З1, ФИН_1192: файл("fid-1192-з1")},     # только с заказом
        {"id": 9830002, "parentId2": 0, "parentId172": З3, ФИН_1192: файл("fid-1192-з3")},     # заказ провален
        {"id": РАМ_1192, "parentId2": СДЕЛКА_НОВАЯ, ФИН_1192: файл("fid-1192-рам")},
    ],
    ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ: [
        # прежняя спецификация с поставщиком З1, под прежним рамочным; файл — второе поле
        {"id": 9810001, "parentId2": СДЕЛКА_НОВАЯ, "parentId172": З1, "companyId": П1, "parentId175": РАМ_175,
         СКАН_132: [], ТЕК_132: файл("fid-132-з1")},
        # две спецификации второй сделки под тем же рамочным
        {"id": 9810002, "parentId2": СДЕЛКА_ВТОРАЯ, "companyId": КОМПАНИЯ_ВТОРОЙ, "parentId175": РАМ_175,
         СКАН_132: файл("fid-132-заказчик")},
        {"id": 9810003, "parentId2": СДЕЛКА_ВТОРАЯ, "parentId175": РАМ_175, ДОГ_132: файл("fid-132-второй")},
    ],
    ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ: [
        # рамочный поставщика заказа З4 — без связи со сделкой; под ним спецификации обеих сделок
        {"id": РАМ_175, "parentId2": 0, "companyId": П4, СКАН_175: файл("fid-175-рам"),
         ФАЙЛ_175: файл("fid-175-не-берётся")},
    ],
    ct.СП_ЗАКАЗ: [
        {"id": З1, "parentId2": СДЕЛКА_НОВАЯ, "companyId": П1, "stageId": "DT172_5:NEW", ПО_172: файл("fid-po-з1")},
        {"id": З2, "parentId2": СДЕЛКА_НОВАЯ, "companyId": П2, "stageId": "DT172_5:SUCCESS",
         ПО_172: файл("fid-po-з2"), ОС_172: файл("fid-oc-з2")},
        {"id": З3, "parentId2": СДЕЛКА_НОВАЯ, "companyId": П3, "stageId": "DT172_5:UC_ОТКАЗ", ПО_172: файл("fid-po-з3")},
        {"id": З4, "parentId2": СДЕЛКА_ВТОРАЯ, "companyId": П4, "stageId": "DT172_5:NEW", ПО_172: файл("fid-po-з4")},
        {"id": З5, "parentId2": СДЕЛКА_ВТОРАЯ, "companyId": П5, "stageId": "DT172_5:NEW", ОС_172: [файл("fid-oc-з5")]},
        {"id": З6, "parentId2": СДЕЛКА_ВТОРАЯ, "companyId": П6, "stageId": "DT172_5:NEW"},
        {"id": З7, "parentId2": СДЕЛКА_НОВАЯ, "companyId": П6, "stageId": "DT172_9:FAIL"},
    ],
}


def _отбор_новый() -> tuple[ct.Отбор, "Портал"]:
    портал = Портал(карточки=КАРТОЧКИ_НОВЫЕ, поля=ПОЛЯ_НОВЫЕ)
    return ct.отобрать(портал, 2), портал


def test_прежние_СП_и_карточки_только_с_живым_заказом():
    """СП-132/175 по parentId2 — главная дыра (121 из 131 сделки «нет договоров»);
    карточка, связанная только с живым заказом сделки (без связи со сделкой), — документ
    сделки заказа; с проваленным заказом (по справочнику стадий и по суффиксу «:FAIL»)
    — нет; связанная с другой сделкой — документ той сделки: у сделки заказа она меняла
    бы флаги и по-разному при разной выборке."""
    отбор, портал = _отбор_новый()
    д = {(x.card_entity, x.card_id): x for x in отбор.все()}
    прежняя = д[(ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ, 9810001)]
    assert (прежняя.deal_id, прежняя.link_source, прежняя.order_id) == (СДЕЛКА_НОВАЯ, "order_link", З1)
    assert прежняя.file_field == ТЕК_132 and [f["id"] for f in прежняя.files] == ["fid-132-з1"]
    assert прежняя.side_company == "supplier" and прежняя.frameworks == [(ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ, РАМ_175)]
    assert д[(ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ, 9810003)].file_field == ДОГ_132, "скан договора без спецификации"
    x = д[(ct.СП_РАМОЧНЫЙ, 9830001)]
    assert (x.deal_id, x.link_source, x.order_id) == (СДЕЛКА_НОВАЯ, "order_only", З1)
    assert (ct.СП_РАЗОВЫЙ, 9800102) not in д, "карточка другой сделки стала документом сделки заказа"
    assert (ct.СП_РАМОЧНЫЙ, 9830002) not in д, "карточка проваленного заказа взята"
    assert (ct.СП_РАЗОВЫЙ, 9800103) not in д, "провал по суффиксу «:FAIL» не узнан"
    assert отбор.заказы[З3]["sem"] == "F" and отбор.заказы[З7]["sem"] == "F" and отбор.заказы[З2]["sem"] == "S"
    # запрос по заказам — только живые заказы; справочник — по одному на воронку
    по_заказам = [п for м, п in портал.вызовы if м == "crm.item.list" and "@parentId172" in п["filter"]]
    assert по_заказам and all(З3 not in п["filter"]["@parentId172"] and З7 not in п["filter"]["@parentId172"]
                              for п in по_заказам)
    assert sorted(п["filter"]["ENTITY_ID"] for м, п in портал.вызовы if м == "crm.status.list") == \
        ["DYNAMIC_172_STAGE_5", "DYNAMIC_172_STAGE_9"]
    assert отбор.счёт["у заказа, но связана с другой сделкой — не взята"] == 1
    assert отбор.счёт["только с заказом, без сделки"] == 1
    # каждая строка несёт, откуда документ
    r = ct.строка("ct-проба", "м", x, status="no_file")
    assert r["link_source"] == "order_only" and r["link_source"] in ct.ИСТОЧНИКИ


def test_рамочный_один_раз_на_сделку():
    """Рамочный над спецификациями — документ каждой сделки один раз, даже под двумя
    её спецификациями; уже связанный со сделкой не задваивается. Заказ документа —
    общий заказ спецификаций, иначе нет."""
    отбор, портал = _отбор_новый()
    рамочные = [x for x in отбор.все() if x.link_source == "framework"]
    assert sorted((x.deal_id, x.card_entity, x.card_id) for x in рамочные) == sorted(
        [(СДЕЛКА_НОВАЯ, ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ, РАМ_175), (СДЕЛКА_ВТОРАЯ, ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ, РАМ_175)])
    у_новой = next(x for x in рамочные if x.deal_id == СДЕЛКА_НОВАЯ)
    у_второй = next(x for x in рамочные if x.deal_id == СДЕЛКА_ВТОРАЯ)
    assert у_новой.order_id == З1 and у_второй.order_id is None
    assert у_новой.file_field == СКАН_175 and [f["id"] for f in у_новой.files] == ["fid-175-рам"]
    # компания рамочного — поставщик второй сделки (её заказ З4), у первой — третье лицо
    assert (у_второй.side_company, у_новой.side_company, у_новой.company_rel) == ("supplier", None, "other")
    assert [(x.card_entity, x.card_id) for x in отбор.документы[СДЕЛКА_НОВАЯ]].count((ct.СП_РАМОЧНЫЙ, РАМ_1192)) == 1
    assert отбор.счёт["рамочных: уже документ сделки"] == 1
    # рамочный читается по номеру — компания и поля файла, без связей
    п = next(п for м, п in портал.вызовы if м == "crm.item.list" and "@id" in п["filter"]
             and п["entityTypeId"] == ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ)
    assert п["filter"]["@id"] == [РАМ_175]
    assert set(п["select"]) == {"id", "companyId", "createdTime", СКАН_175, ФАЙЛ_175}


def test_purchase_order_только_у_заказа_без_документа_поставщика():
    """Решение владельца 19.10.2026: гарантия — из договора, без договора — из Purchase
    order. Purchase order — только у живого заказа без документа поставщика: ни карточки
    с этим заказом (договор с заказчиком, привязанный к заказу, — не в счёт), ни
    карточки с компанией заказа; заказ без файла — не документ."""
    отбор, _ = _отбор_новый()
    по = {x.order_id: x for x in отбор.все() if x.link_source == "purchase_order"}
    assert set(по) == {З2, З5}, "З1 — есть договор поставщика, З3 — провален, З4 — рамочный поставщика, З6 — без файла"
    з2 = по[З2]
    assert (з2.card_entity, з2.card_id, з2.deal_id) == (ct.СП_ЗАКАЗ, З2, СДЕЛКА_НОВАЯ)
    assert з2.file_field == ПО_172 and [f["id"] for f in з2.files] == ["fid-po-з2"], "Purchase order раньше подтверждения"
    assert (з2.side_company, з2.company_id) == ("supplier", П2)
    assert по[З5].file_field == ОС_172 and по[З5].deal_id == СДЕЛКА_ВТОРАЯ
    assert отбор.счёт["заказов: документ поставщика по компании"] == 1
    assert отбор.счёт["из них без Purchase order и Order confirmation"] == 1


def test_порядок_документов_спецификации_purchase_order_рамочные():
    """Свод условий берёт первое заполненное: спецификации (1174, затем 132), Purchase
    order, рамочные (1192, затем 175); внутри вида — новая карточка раньше."""
    отбор, _ = _отбор_новый()
    assert [(x.card_entity, x.card_id) for x in отбор.документы[СДЕЛКА_НОВАЯ]] == [
        (ct.СП_РАЗОВЫЙ, 9800104), (ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ, 9810001), (ct.СП_ЗАКАЗ, З2),
        (ct.СП_РАМОЧНЫЙ, РАМ_1192), (ct.СП_РАМОЧНЫЙ, 9830001), (ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ, РАМ_175)]
    assert [(x.card_entity, x.card_id) for x in отбор.документы[СДЕЛКА_ВТОРАЯ]] == [
        (ct.СП_РАЗОВЫЙ, 9800101), (ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ, 9810003), (ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ, 9810002),
        (ct.СП_ЗАКАЗ, З5), (ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ, РАМ_175)]
    assert set(ct.ПОРЯДОК_ВИДОВ) == {*ct.ТИПЫ_ДОГОВОРОВ, ct.СП_ЗАКАЗ}


ФАЙЛЫ_НОВЫЕ = {f: f"ДОКУМЕНТ ВЫДУМКА {f}".encode() for f in (
    "fid-1174-заказчик", "fid-1174-спец", "fid-1192-з1", "fid-1192-рам", "fid-132-з1",
    "fid-132-заказчик", "fid-132-второй", "fid-175-рам", "fid-po-з2", "fid-oc-з5")}


def test_рамочный_качается_и_читается_один_раз_а_строки_у_обеих_сделок(capsys):
    портал = Портал(ФАЙЛЫ_НОВЫЕ, карточки=КАРТОЧКИ_НОВЫЕ, поля=ПОЛЯ_НОВЫЕ)
    клиент = Клиент()
    база = База()
    ct.прогон("live", 2, 10.0, "claude-opus-5-5", портал=портал, run_id="ct-проба",
              читатель=ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент), база=база)
    журнал = _журнал(capsys)
    assert портал.скачано.count("fid-175-рам") == 1, "рамочный второй сделки скачан ещё раз"
    assert len(клиент.messages.запросы) == len(ФАЙЛЫ_НОВЫЕ), "файл, встреченный дважды, прочитан дважды"
    рамочный = [r for r in база.условия if r["card_id"] == РАМ_175]
    assert sorted((r["deal_id"], r["status"]) for r in рамочный) == sorted(
        [(СДЕЛКА_НОВАЯ, "ok"), (СДЕЛКА_ВТОРАЯ, "reused")])
    assert {r["link_source"] for r in рамочный} == {"framework"}
    assert next(r for r in рамочный if r["status"] == "reused")["usd"] == 0.0
    assert next(r for r in рамочный if r["status"] == "ok")["usd"] > 0
    # строки сделки — в порядке свода (тот же ключ, что у выгрузки для вкладки)
    for сделка in (СДЕЛКА_НОВАЯ, СДЕЛКА_ВТОРАЯ):
        rs = [r for r in база.условия if r["deal_id"] == сделка]
        assert rs and rs == sorted(rs, key=ct.порядок_строки)
    for слово in ("fid-", "9810001", str(РАМ_175), str(З2), str(П4), "ДОКУМЕНТ ВЫДУМКА"):
        assert слово not in журнал, f"в журнал попало «{слово}»"


def test_рамочный_прочитанный_до_срока_берётся_и_после_него(capsys):
    """Срок вышел после первой сделки: рамочный второй сделки уже прочитан в прогоне —
    его строка «reused», а не «time», и не качается заново."""
    портал = Портал(ФАЙЛЫ_НОВЫЕ, карточки=КАРТОЧКИ_НОВЫЕ, поля=ПОЛЯ_НОВЫЕ)
    отсчёты = iter([0.0] + [0.0] * 6 + [10_000.0] * 50)       # начало + шесть файлов первой сделки
    база = База()
    ct.прогон("live", 2, 10.0, "claude-opus-5-5", портал=портал, run_id="ct-проба",
              читатель=ct.Читатель("claude-opus-5-5", 10.0, клиент=Клиент()), база=база,
              срок_мин=1, часы=lambda: next(отсчёты))
    _журнал(capsys)
    вторая = {(r["card_entity"], r["card_id"]): r["status"] for r in база.условия if r["deal_id"] == СДЕЛКА_ВТОРАЯ}
    assert вторая[(ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ, РАМ_175)] == "reused"
    assert вторая[(ct.СП_РАЗОВЫЙ, 9800101)] == "time"
    assert портал.скачано.count("fid-175-рам") == 1


def test_холостой_прогон_считает_источники_агрегатами(capsys):
    портал = Портал(карточки=КАРТОЧКИ_НОВЫЕ, поля=ПОЛЯ_НОВЫЕ)
    assert ct.прогон("dry", 2, 10.0, "claude-opus-5-5", портал=портал, run_id="ct-проба") == 0
    журнал = _журнал(capsys)
    assert портал.скачано == []
    assert ("документов по источнику: связь со сделкой 6 (с файлом 6, файлов 6) · только связь с заказом 1 "
            "(с файлом 1, файлов 1) · рамочный над спецификацией 2 (с файлом 2, файлов 2) · Purchase order "
            "заказа 2 (с файлом 2, файлов 2)") in журнал
    # рамочный двух сделок — один файл к чтению: 11 документов с файлом, 10 файлов
    assert ("файлов к чтению без повторов (рамочный под несколькими сделками — один раз; прочитанный раньше — "
            "даром по sha256, его dry не знает): связь со сделкой 6 · только связь с заказом 1 · рамочный над "
            "спецификацией 1 · Purchase order заказа 2") in журнал
    assert f"claude-opus-5-5: файлов {len(ФАЙЛЫ_НОВЫЕ)} " in журнал
    assert "заказов со стадией F 2" in журнал
    for слово in ("fid-", "9810001", str(РАМ_175), str(З1), str(З2), str(П4), "9900099", "UC_ОТКАЗ", "DT172"):
        assert слово not in журнал, f"в журнал попало «{слово}»"


def test_не_читается_главнее_недочитана():
    """«Не читается» (закачка, формат, предел) — нужен человек; «недочитана» (бюджет,
    срок, временный отказ) — дочитает следующий прогон. Есть оба — «не читается»."""
    у_з, _ = _условия(условия_заказчика())
    з = _строка("customer", у_з, card=1)
    for статус in ct.НЕ_ДОЧИТАН:
        assert ct.флаги_сделки([з, _строка("supplier", None, status=статус, card=2)]) == ([], "недочитана"), статус
    for статус in ct.НЕ_ЧИТАЕТСЯ:
        assert ct.флаги_сделки([з, _строка("supplier", None, status=статус, card=2)]) == ([], "не читается"), статус
        assert ct.флаги_сделки([_строка("supplier", None, status="budget", card=3), з,
                                _строка("supplier", None, status=статус, card=2)]) == ([], "не читается")
    assert set(ct.НЕ_ПРОЧИТАН) == set(ct.НЕ_ДОЧИТАН) | set(ct.НЕ_ЧИТАЕТСЯ)
    assert not set(ct.НЕ_ДОЧИТАН) & set(ct.НЕ_ЧИТАЕТСЯ)
    assert "не читается" in ct.ИСХОДЫ


def test_срок_чтения_не_длиннее_job(capsys, monkeypatch):
    assert ct.main(["--deadline-min", str(ct.СРОК_МИН_МАКС + 1)]) == 2
    assert f"--deadline-min вне [0; {ct.СРОК_МИН_МАКС}]" in _журнал(capsys)
    # 0 — «без срока»: в Actions не берётся (прогон без срока убил бы таймаут до записи флагов)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert ct.main(["--deadline-min", "0"]) == 2
    assert "без срока" in _журнал(capsys)
    # разовое дочитывание — в пределе
    assert ct.СРОК_МИН_МАКС == 150


def test_источники_и_типы_в_схеме_как_в_коде():
    import re
    текст = (ROOT / "library" / "supabase" / "contract_terms_schema.sql").read_text(encoding="utf-8")
    блок = текст[текст.index("add constraint contract_terms_link_chk"):]
    assert tuple(re.findall(r"'([^']+)'", блок[:блок.index(";")])) == ct.ИСТОЧНИКИ
    блок = текст[текст.index("add constraint contract_terms_entity_chk"):]
    assert {int(x) for x in re.findall(r"\d+", блок[:блок.index(";")])} == set(ct.ПОРЯДОК_ВИДОВ)
    assert "primary key (run_id, deal_id, card_entity, card_id, file_id, order_key)" in текст
    assert "link_source" in ct.База.КОЛОНКИ


# --------------------------------------------------------------------------- правки по разбору (25.10.2026, вторая проверка)
def _коды(строки: list[dict], снятые: Counter | None = None) -> set[str]:
    return {f["code"] for f in ct.флаги_сделки(строки, снятые)[0]}


def test_purchase_order_один_не_доказывает_отсутствия_пеней_и_документов():
    """Пеней и перечня документов в Purchase order обычно нет, а договор поставщика может
    лежать в карточке без связи (зонд v57: 77 из 775 живых заказов). У заказа, где у
    поставщика только Purchase order, «пени поставщика нет» и «документов нет» — не
    доказаны: флагов penaltyUncovered и docsGap нет, счёт — в снятые_флаги. Флаги на
    присутствии условий (срок) остаются; Purchase order с пенями ниже — не отсутствие."""
    з_данные = условия_заказчика()
    з_данные["documents"] = ["паспорт ВЫДУМКА"]
    з_данные["evidence"].append({"field": "documents", "quote": ЦИТАТА_ЗАКАЗЧИКА, "page": 3})
    у_з, _ = _условия(з_данные)
    по_данные = условия_поставщика()
    по_данные["penaltyPctDay"] = None
    по_данные["evidence"] = [e for e in по_данные["evidence"] if e["field"] != "penaltyPctDay"]
    у_по, _ = _условия(по_данные)
    з = _строка("customer", у_з, card=1)
    по = _строка("supplier", у_по, card=ЗАКАЗ, order=ЗАКАЗ, entity=ct.СП_ЗАКАЗ, компания="supplier")
    по["link_source"] = "purchase_order"
    снятые: Counter = Counter()
    коды = _коды([з, по], снятые)
    assert not коды & {"penaltyUncovered", "docsGap"}
    assert снятые == Counter({"penaltyUncovered": 1, "docsGap": 1})
    assert "lateByDesign" in коды and ct.флаги_сделки([з, по])[1] == "пара"
    # тот же документ — договор поставщика: его «нет» — флаг
    договор = {**по, "link_source": "order_link", "card_entity": ct.СП_РАЗОВЫЙ, "card_id": 3}
    assert {"penaltyUncovered", "docsGap"} <= _коды([з, договор])
    # Purchase order рядом с договором поставщика того же заказа — отсутствие доказывает договор
    assert {"penaltyUncovered", "docsGap"} <= _коды([з, по, договор])
    # пени в Purchase order ниже, чем у заказчика, — это не отсутствие
    assert "penaltyUncovered" in _коды([з, {**по, "terms": {**у_по, "penaltyPctDay": 0.01}}])
    assert ct.ПОЛЯ_ОТСУТСТВИЯ_ПОСТАВЩИКА == ("penaltyPctDay", "documents")


def test_гейт_считает_документы_а_не_строки():
    """Рамочный под 15 сделками или 12 Purchase order не топят спорные своими согласиями;
    спорный рамочный под 6 сделками — один спорный документ, а не шесть. Purchase order
    — вне доли спорных: его сторона — по виду документа, а не по компании."""
    у_з, _ = _условия(условия_заказчика())
    у_п, _ = _условия(условия_поставщика())

    def док(card, *, спор=False, сделка=СДЕЛКА_НОВАЯ):
        r = _строка("customer", у_з, card=card, компания="supplier" if спор else "customer")
        r["deal_id"] = сделка
        return r

    а = [док(i, спор=i <= 8) for i in range(1, 31)]                      # 8 из 30 — 27 %
    assert not ct.гейт_стороны(а)[0]
    рамочный_15 = [док(100, сделка=СДЕЛКА_НОВАЯ - k) for k in range(15)]
    assert not ct.гейт_стороны(а + рамочный_15)[0], "рамочный под 15 сделками утопил спорные"
    по_12 = []
    for k in range(12):
        r = _строка("supplier", у_п, card=ЗАКАЗ + k, order=ЗАКАЗ + k, entity=ct.СП_ЗАКАЗ, компания="supplier")
        r["link_source"] = "purchase_order"
        по_12.append(r)
    гейт, журнал = ct.гейт_стороны(а + по_12)
    assert not гейт and any("без Purchase order: 8 из 30" in s for s in журнал), журнал
    б = [док(i, спор=i <= 6) for i in range(1, 41)]                      # 6 из 40 — 15 %
    assert ct.гейт_стороны(б)[0]
    спорный_6 = [док(300, спор=True, сделка=СДЕЛКА_НОВАЯ - k) for k in range(6)]
    гейт, журнал = ct.гейт_стороны(б + спорный_6)
    assert гейт and any("7 из 41" in s for s in журнал), "один спорный рамочный встал за шесть"
    # названность — тоже по документам
    assert any("сторона по модели названа: 41 из 41" in s for s in журнал)


def _двухзаказный() -> dict:
    """Сделка: спецификация заказчика; две спецификации поставщика П1 по заказам З1 и З2
    под одним рамочным поставщика (компания П1); рамочный заказчика без компании над
    спецификацией заказчика, привязанной к заказу З3 (у З3 есть Purchase order)."""
    return {
        ct.СП_РАЗОВЫЙ: [
            {"id": 9800201, "parentId2": СДЕЛКА_НОВАЯ, "parentId172": З3, "companyId": КОМПАНИЯ_ЗАКАЗЧИКА,
             ФИН_1174: файл("fid-заказчик-спец"), "parentId1192": 9830201},
        ],
        ct.СП_РАМОЧНЫЙ: [{"id": 9830201, "parentId2": 0, ФИН_1192: файл("fid-рамочный-заказчика")}],
        ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ: [
            {"id": 9810201, "parentId2": СДЕЛКА_НОВАЯ, "parentId172": З1, "companyId": П1, "parentId175": РАМ_175,
             СКАН_132: файл("fid-поставщик-з1")},
            {"id": 9810202, "parentId2": СДЕЛКА_НОВАЯ, "parentId172": З2, "companyId": П1, "parentId175": РАМ_175,
             СКАН_132: файл("fid-поставщик-з2")},
        ],
        ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ: [{"id": РАМ_175, "parentId2": 0, "companyId": П1, СКАН_175: файл("fid-рамочный-поставщика")}],
        ct.СП_ЗАКАЗ: [
            {"id": З1, "parentId2": СДЕЛКА_НОВАЯ, "companyId": П1, "stageId": "DT172_5:NEW"},
            {"id": З2, "parentId2": СДЕЛКА_НОВАЯ, "companyId": П1, "stageId": "DT172_5:NEW"},
            {"id": З3, "parentId2": СДЕЛКА_НОВАЯ, "companyId": П3, "stageId": "DT172_5:NEW", ПО_172: файл("fid-po-з3")},
        ],
    }


ФАЙЛЫ_ДВУХЗАКАЗНЫЕ = {
    "fid-заказчик-спец": "СПЕЦИФИКАЦИЯ С ЗАКАЗЧИКОМ (выдумка)".encode(),
    "fid-рамочный-заказчика": "РАМОЧНЫЙ С ЗАКАЗЧИКОМ (выдумка)".encode(),
    "fid-поставщик-з1": "СПЕЦИФИКАЦИЯ ПОСТАВЩИК З1 (выдумка)".encode(),
    "fid-поставщик-з2": "СПЕЦИФИКАЦИЯ ПОСТАВЩИК З2 (выдумка)".encode(),
    "fid-рамочный-поставщика": "РАМОЧНЫЙ ПОСТАВЩИК (выдумка)".encode(),
    "fid-po-з3": "PURCHASE ORDER ПОСТАВЩИК З3 (выдумка)".encode(),
}


def test_рамочный_поставщика_двух_заказов_и_рамочный_заказчика_без_компании():
    """Рамочный поставщика над спецификациями двух заказов — документ каждого заказа, а
    не «поставщик без заказа» для всей сделки. Рамочный заказчика заказ от спецификации
    заказчика (её привязали к заказу) не наследует и Purchase order заказа не снимает."""
    отбор = ct.отобрать(Портал(карточки=_двухзаказный(), поля=ПОЛЯ_НОВЫЕ), 1)
    дд = отбор.документы[СДЕЛКА_НОВАЯ]
    рам_п = [x for x in дд if (x.card_entity, x.card_id) == (ct.СП_РАМОЧНЫЙ_ПРЕЖНИЙ, РАМ_175)]
    assert sorted(x.order_id for x in рам_п) == [З1, З2]
    assert {x.link_source for x in рам_п} == {"framework"} and {x.side_company for x in рам_п} == {"supplier"}
    рам_з = [x for x in дд if (x.card_entity, x.card_id) == (ct.СП_РАМОЧНЫЙ, 9830201)]
    assert [x.order_id for x in рам_з] == [None], "рамочный заказчика унаследовал заказ спецификации заказчика"
    по = [x for x in дд if x.link_source == "purchase_order"]
    assert [x.order_id for x in по] == [З3], "рамочный без компании снял Purchase order заказа"
    assert отбор.счёт["рамочных: над спецификациями нескольких заказов"] == 1


def test_рамочный_двух_заказов_читается_раз_и_даёт_флаги_обоих(capsys):
    портал = Портал(ФАЙЛЫ_ДВУХЗАКАЗНЫЕ, карточки=_двухзаказный(), поля=ПОЛЯ_НОВЫЕ)
    клиент = Клиент()
    база = База()
    assert ct.прогон("live", 1, 10.0, "claude-opus-5-5", портал=портал, run_id="ct-проба",
                     читатель=ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент), база=база) == 0
    _журнал(capsys)
    assert портал.скачано.count("fid-рамочный-поставщика") == 1
    assert len(клиент.messages.запросы) == len(ФАЙЛЫ_ДВУХЗАКАЗНЫЕ)
    рам = sorted((r["order_id"], r["status"]) for r in база.условия if r["card_id"] == РАМ_175)
    assert рам == [(З1, "ok"), (З2, "reused")]
    assert база.исходы == {СДЕЛКА_НОВАЯ: "пара"}
    по_заказам = {(f["order_id"], f["code"]) for f in база.флаги}
    assert {(З1, "lateByDesign"), (З2, "lateByDesign"), (З3, "lateByDesign")} <= по_заказам


def test_временный_отказ_закачки_недочитана_а_не_читается(capsys):
    """«Не читается» зовёт человека перезалить файл — только когда файла у портала нет
    (404 / 410), он пуст или у поля нет ссылки. Сеть, 5xx, лимит портала дольше ожидания,
    страница входа (протух токен), права вебхука — «недочитана»: дочитает следующий прогон.
    Рамочный двух сделок, не скачанный по сети, — «недочитана» у обеих."""
    for причина in ("urlMachine: код 404", "urlMachine: код 410", "urlMachine: тело 0 байт — меньше порога",
                    "у файла поля нет urlMachine", "ссылок на файл нет вовсе"):
        assert ct.статус_закачки(причина) == "download", причина
    for причина in ("urlMachine: сеть: ReadTimeout", "urlMachine: код 503 (лимит портала дольше бюджета ожидания)",
                    "urlMachine: код 429 (лимит портала дольше бюджета ожидания)", "urlMachine: код 502",
                    "urlMachine: вместо файла страница входа", "urlMachine: код 403", "urlMachine: код 401",
                    "urlMachine: код 4040", "не скачался", "", None):
        assert ct.статус_закачки(причина) == "fetch", причина
    assert "fetch" in ct.НЕ_ДОЧИТАН and "download" in ct.НЕ_ЧИТАЕТСЯ

    class Сбойный(Портал):
        def скачать(self, fo):
            self.скачано.append(fo["id"])
            if fo["id"] == "fid-175-рам":
                return None, "urlMachine: код 503 (лимит портала дольше бюджета ожидания)"
            return super().скачать(fo)
    портал = Сбойный(ФАЙЛЫ_НОВЫЕ, карточки=КАРТОЧКИ_НОВЫЕ, поля=ПОЛЯ_НОВЫЕ)
    база = База()
    ct.прогон("live", 2, 10.0, "claude-opus-5-5", портал=портал, run_id="ct-проба",
              читатель=ct.Читатель("claude-opus-5-5", 10.0, клиент=Клиент()), база=база)
    журнал = _журнал(capsys)
    рам = [r for r in база.условия if r["card_id"] == РАМ_175]
    assert {r["status"] for r in рам} == {"fetch"} and len(рам) == 2
    assert портал.скачано.count("fid-175-рам") == 1, "сбой закачки повторён у второй сделки — лишний запрос"
    # корпус спорный по сторонам (гейт прогона красный) — исход сделки по её строкам
    for сделка in (СДЕЛКА_НОВАЯ, СДЕЛКА_ВТОРАЯ):
        assert ct.флаги_сделки([r for r in база.условия if r["deal_id"] == сделка]) == ([], "недочитана"), сделка
    assert "сделки: недочитана 2" in журнал and "fetch 2" in журнал and "fetch: urlMachine: код 503" in журнал
    # прежний download — по заметке: временный — «недочитана», 404 — «не читается»
    assert ct.уточнить_статус("download", "urlMachine: сеть: ConnectionError") == "fetch"
    assert ct.уточнить_статус("download", "urlMachine: код 404") == "download"
    assert ct.уточнить_статус("format", "документ не подготовлен (ValueError)") == "api"
    assert ct.уточнить_статус("format", "pdf: ни картинкой, ни текстом модели отдать нечего") == "format"
    for статус in ct.СТАТУСЫ_ЖУРНАЛА:
        if статус not in ("download", "format"):
            assert ct.уточнить_статус(статус, "любая заметка") == статус


def test_непредвиденный_сбой_подготовки_файла_не_формат(monkeypatch):
    """Ошибка кода при подготовке файла — не «формат» (тот — «не читается» и «перезалить
    файл» ответственному): статус api, сделка «недочитана»."""
    def сбой(_b, _подвид):
        raise ValueError("ВЫДУМКА")
    monkeypatch.setattr(rl, "собрать_запрос", сбой)
    итог = ct.Читатель("claude-opus-5-5", 10.0, клиент=Клиент()).прочитать("ДОКУМЕНТ ВЫДУМКА".encode())
    assert итог["status"] == "api" and "ValueError" in итог["note"]


def test_прежняя_спецификация_и_скан_договора_читаются_оба():
    """СП-132: скан подписанной спецификации и её текущий файл — версии одной
    спецификации (берётся первое непустое), а скан подписанного договора — другой
    документ, где обычно пени, гарантия, право и документы: читаются оба."""
    к = {"id": 9810301, СКАН_132: файл("fid-12"), ТЕК_132: файл("fid-13-не-берётся"), ДОГ_132: [файл("fid-11")]}
    поле, файлы = ct.файлы_карточки(к, ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ)
    assert поле == СКАН_132
    assert [(f["id"], f[ct.ПОЛЕ_ФАЙЛА]) for f in файлы] == [("fid-12", СКАН_132), ("fid-11", ДОГ_132)]
    # спецификации нет — текущий файл, и договор
    поле, файлы = ct.файлы_карточки({**к, СКАН_132: []}, ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ)
    assert [(f["id"], f[ct.ПОЛЕ_ФАЙЛА]) for f in файлы] == [("fid-13-не-берётся", ТЕК_132), ("fid-11", ДОГ_132)]
    # у прочих типов поля — версии одного документа: первое непустое
    _, другие = ct.файлы_карточки({ФИН_1174: файл("fid-1"), ТЕК_1174: файл("fid-2")}, ct.СП_РАЗОВЫЙ)
    assert [f["id"] for f in другие] == ["fid-1"]
    # строка несёт поле своего файла
    д = ct.Документ(deal_id=СДЕЛКА_НОВАЯ, card_entity=ct.СП_РАЗОВЫЙ_ПРЕЖНИЙ, card_id=9810301, order_id=None,
                    side_link="customer", link_source="deal_link", template=None, doc_type=None,
                    side_template=None, side_company=None, file_field=поле, files=файлы)
    assert [ct.строка("ct-проба", "м", д, fo, status="time")["file_field"] for fo in д.files] == [ТЕК_132, ДОГ_132]


def test_purchase_order_заказа_без_компании_сверяется_видом_документа():
    """Purchase order — всегда документ поставщика (заказ поставщику): и у заказа без
    компании side_company «supplier», и модель, назвавшая его договором с заказчиком,
    делает сделку спорной, а его условия не идут в свод заказчика."""
    карточки = {**КАРТОЧКИ_НОВЫЕ, ct.СП_ЗАКАЗ: [
        {"id": З2, "parentId2": СДЕЛКА_НОВАЯ, "stageId": "DT172_5:NEW", ПО_172: файл("fid-po-з2")}]}
    отбор = ct.отобрать(Портал(карточки=карточки, поля=ПОЛЯ_НОВЫЕ), 2)
    по = next(x for x in отбор.все() if x.link_source == "purchase_order")
    assert (по.order_id, по.side_company, по.company_id) == (З2, "supplier", None)
    у_з, _ = _условия(условия_заказчика())
    з = _строка("customer", у_з, card=1)
    r = _строка("supplier", у_з, card=З2, order=З2, entity=ct.СП_ЗАКАЗ, компания=по.side_company)
    r["link_source"] = "purchase_order"
    assert ct.флаги_сделки([з, r]) == ([], "спорная")


def test_после_бюджета_качается_только_читанный_раньше(capsys):
    """Бюджет кончился: новый файл всё равно не прочесть — его закачка лишь запрос к
    порталу. Качается только файл, который та же модель и версия уже читали (его
    условия даром по sha256); список читанных — один запрос к базе."""
    import hashlib
    у, док, _ = ct.разобрать_ответ(записи(условия_заказчика()))
    sha = hashlib.sha256(ФАЙЛЫ["fid-рамочный"]).hexdigest()
    база = База(кэш={sha: {"terms": у, "evidence": док, "pages": 3, "note": None}},
                читанные={(ct.СП_РАМОЧНЫЙ, 9800005, "fid-рамочный")})
    портал = Портал(ФАЙЛЫ)
    клиент = Клиент()
    ct.прогон("live", 3, 0.0001, "claude-opus-5-5", портал=портал, run_id="ct-проба",
              читатель=ct.Читатель("claude-opus-5-5", 0.0001, клиент=клиент), база=база)
    журнал = _журнал(capsys)
    статусы = {r["card_id"]: (r["status"], r["sha256"] is not None) for r in база.условия}
    # первый файл (новый, СП-1174 поставщика) скачан и упёрся в бюджет; второй новый — не
    # качался; рамочный читан раньше — скачан и взят даром
    assert портал.скачано == ["fid-поставщик", "fid-рамочный"]
    assert статусы == {9800002: ("budget", True), 9800001: ("budget", False), 9800003: ("no_file", False),
                       9800005: ("reused", True)}
    assert клиент.messages.запросы == [] and база.спрошено_читанных == 1
    assert "после бюджета не качались (той же моделью и версией не читаны): 1 файлов" in журнал


def test_статусы_в_схеме_как_в_коде():
    """Список статусов в проверке базы — ровно статусы кода: статус, которого нет ни в
    НЕ_ДОЧИТАН, ни в НЕ_ЧИТАЕТСЯ, флаги_сделки приняли бы за отсутствие документа — и
    флаги «по отсутствию» врали бы."""
    import re
    текст = (ROOT / "library" / "supabase" / "contract_terms_schema.sql").read_text(encoding="utf-8")
    блок = текст[текст.index("add constraint contract_terms_status_chk"):]
    в_схеме = set(re.findall(r"'([^']+)'", блок[:блок.index(";")]))
    assert в_схеме == set(ct.ПРОЧИТАН) | {"no_file"} | set(ct.НЕ_ПРОЧИТАН)
    assert в_схеме == set(ct.СТАТУСЫ_ЖУРНАЛА) and len(ct.СТАТУСЫ_ЖУРНАЛА) == len(в_схеме)
    assert set(ct.СТАТУС_ОТКАЗА.values()) <= set(ct.НЕ_ПРОЧИТАН)
    assert {ct.статус_закачки("код 404"), ct.статус_закачки("сеть")} <= set(ct.НЕ_ПРОЧИТАН)


# --------------------------------------------------------------------------- сторона по карточке (25.10.2026)
def test_сторона_по_карточке_когда_модель_не_назвала():
    """Решение владельца 25.10.2026: модель стороны не назвала — сторона по компании
    карточки (компания сделки — заказчик, компания заказа сделки и Purchase order —
    поставщик), side_source card. Спорный документ — как прежде вне флагов; гейт стороны
    меряет модель, а не карточку."""
    у_з, _ = _условия(условия_заказчика())
    у_п, _ = _условия(условия_поставщика())
    молчит_з = {**у_з, "side": None}
    молчит_п = {**у_п, "side": None}
    з = _строка("customer", молчит_з, card=1, компания="customer")
    п = _строка("supplier", молчит_п, card=2, order=ЗАКАЗ, компания="supplier")
    assert (з["side"], з["side_source"]) == ("customer", "card")
    assert (п["side"], п["side_source"]) == ("supplier", "card")
    assert not ct.сторона_спорная(з) and not ct.сторона_спорная(п)
    по_карточке, исход = ct.флаги_сделки([з, п])
    по_модели, исход_м = ct.флаги_сделки([_строка("customer", у_з, card=1), _строка("supplier", у_п, card=2,
                                                                                     order=ЗАКАЗ)])
    assert исход == исход_м == "пара" and по_карточке == по_модели and по_карточке
    # модель назвала — главнее карточки: расхождение — спорный документ, а не подмена
    спор = _строка("customer", у_п, card=3, компания="customer")
    assert (спор["side"], спор["side_source"]) == ("supplier", "model") and ct.сторона_спорная(спор)
    assert ct.флаги_сделки([з, п, спор]) == ([], "спорная")
    # компания о стороне молчит — документ без стороны, как прежде
    молчат = _строка("customer", молчит_п, card=4)
    assert (молчат["side"], молчат["side_source"]) == (None, None)
    assert ct.флаги_сделки([з, п, молчат]) == ([], "без стороны")
    # непрочитанный документ стороны не получает и от карточки
    assert _строка("customer", None, status="budget", card=5, компания="customer")["side"] is None
    # гейт: по карточке — не «названо моделью»; спорных среди них не бывает
    гейт, журнал = ct.гейт_стороны([з, п, _строка("customer", у_з, card=6)])
    assert not гейт and any("модель назвала сторону у 1 из 3" in s for s in журнал)
    assert any(s.startswith("сторона по карточке") and "2 документов — заказчик 1 · поставщик 1" in s
               for s in журнал)
    assert ct.гейт_стороны([з, _строка("customer", у_з, card=6), _строка("supplier", у_п, card=7, order=ЗАКАЗ)])[0]


def test_сторона_по_карточке_в_живом_прогоне_и_purchase_order(capsys):
    """Живой прогон: модель стороны поставщика не назвала — его договор (компания заказа
    сделки) идёт в пару по карточке; журнал называет число таких документов, без номеров."""
    def поставщик_молчит(текст):
        у = условия_поставщика() if "ПОСТАВЩИК" in текст else условия_заказчика()
        if "ПОСТАВЩИК" in текст:
            у["side"] = None
        return ответ(у)
    база = База()
    код = ct.прогон("live", 3, 10.0, "claude-opus-5-5", портал=Портал(ФАЙЛЫ), run_id="ct-проба",
                    читатель=ct.Читатель("claude-opus-5-5", 10.0, клиент=Клиент(поставщик_молчит)), база=база)
    журнал = _журнал(capsys)
    r = next(r for r in база.условия if r["card_id"] == 9800002)
    assert (r["side"], r["side_source"]) == ("supplier", "card") and код == 0
    assert база.исходы[СДЕЛКА_НОВАЯ] == "пара" and {f["code"] for f in база.флаги} >= {"lateByDesign"}
    assert "сторона по карточке (модель не назвала; решение владельца 25.10.2026): 1 документов" in журнал
    for слово in ЗАПРЕТНОЕ:
        assert слово not in журнал, f"в журнал попало «{слово}»"


def test_источники_стороны_в_схеме_как_в_коде():
    import re
    текст = (ROOT / "library" / "supabase" / "contract_terms_schema.sql").read_text(encoding="utf-8")
    блок = текст[текст.index("add constraint contract_terms_side_chk"):]
    блок = блок[:блок.index(";")]
    источники = re.search(r"side_source in \(([^)]*)\)", блок).group(1)
    assert set(re.findall(r"'([^']+)'", источники)) == set(ct.ИСТОЧНИКИ_СТОРОНЫ)
    assert "side_source is distinct from 'card' or side = side_company" in блок


def test_версия_сверки_отдельна_от_версии_чтения():
    """Правка правил сверки не делает прочитанное чужим: ключ повтора по sha256 —
    METHOD_VER, исходы и флаги — ВЕРСИЯ_ИСХОДА (с отпечатком правил)."""
    assert ct.ВЕРСИЯ_ИСХОДА.startswith(ct.METHOD_VER + "+сверка:")
    assert ct.версия_сверки() == ct.ВЕРСИЯ_ИСХОДА.split("+", 1)[1]


# --------------------------------------------------------------------------- PDF по частям (25.10.2026)
def pdf_с_текстом(страниц: int, *, текст: str | None = "Page text of the contract", добавка: int = 0,
                  большая: int | None = None) -> bytes:
    """Выдуманный PDF: на странице текстовый слой (Helvetica) и комментарий-набивка
    добавка байт; у страницы большая — вдесятеро больше."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, StreamObject
    писатель = PdfWriter()
    for i in range(страниц):
        стр = писатель.add_blank_page(width=595, height=842)
        шрифт = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                  NameObject("/BaseFont"): NameObject("/Helvetica")})
        стр[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): писатель._add_object(шрифт)})})
        тело = (f"BT /F1 9 Tf 30 800 Td ({(текст + ' ') * 12}{i + 1}) Tj ET\n".encode("latin-1") if текст else b"")
        тело += b"%" + b"x" * (добавка * (10 if большая == i + 1 else 1)) + b"\n"
        поток = StreamObject()
        поток.set_data(тело)
        стр[NameObject("/Contents")] = писатель._add_object(поток)
    буфер = io.BytesIO()
    писатель.write(буфер)
    return буфер.getvalue()


def test_части_pdf_меньше_пределов_и_подряд(monkeypatch):
    monkeypatch.setenv("LLM_READ_MAX_MB", "0.01")          # 10 КБ: страница ~4 КБ
    b = pdf_с_текстом(5, добавка=4000)
    assert len(b) > 10_000
    части = ct.части_pdf(b)
    assert части and len(части) >= 3
    assert [(ч.первая, ч.последняя) for ч in части][0][0] == 1 and части[-1].последняя == 5
    for пред, след in zip(части, части[1:]):
        assert след.первая == пред.последняя + 1, "страницы частей подряд, без пропусков"
    from pypdf import PdfReader
    for ч in части:
        assert ч.вид == "pdf" and len(ч.тело) <= 10_000 * ct.ДОЛЯ_ПРЕДЕЛА_ЧАСТИ
        assert len(PdfReader(io.BytesIO(ч.тело)).pages) == ч.последняя - ч.первая + 1
    # файл в пределах и не PDF — обычное чтение
    monkeypatch.setenv("LLM_READ_MAX_MB", "20")
    assert ct.части_pdf(b) is None and ct.части_pdf(b"not a pdf") is None
    # длиннее предела страниц — части не длиннее предела
    monkeypatch.setenv("LLM_READ_MAX_PAGES", "2")
    части = ct.части_pdf(pdf_на(5))
    assert [(ч.первая, ч.последняя) for ч in части] == [(1, 2), (3, 4), (5, 5)]
    # оценка по страницам поодиночке промахнулась (общая разметка части тяжелее) — запись
    # части больше предела делится пополам, пока не станет меньше
    monkeypatch.delenv("LLM_READ_MAX_PAGES")
    monkeypatch.setenv("LLM_READ_MAX_MB", "0.01")
    b = pdf_с_текстом(4, добавка=3000)
    assert [(ч.первая, ч.последняя) for ч in ct.части_pdf(b)] == [(1, 2), (3, 4)]
    исходная = ct._pdf_страниц
    monkeypatch.setattr(ct, "_pdf_страниц", lambda стр: исходная(стр) + b"%" * (6000 if len(стр) > 1 else 0))
    части = ct.части_pdf(b)
    assert [(ч.первая, ч.последняя) for ч in части] == [(i, i) for i in range(1, 5)]
    assert all(len(ч.тело) <= 10_000 * ct.ДОЛЯ_ПРЕДЕЛА_ЧАСТИ for ч in части)


def test_части_pdf_страница_больше_предела(monkeypatch):
    """Страница больше предела сама: читается текстовым слоем; слоя нет (скан) — limit."""
    monkeypatch.setenv("LLM_READ_MAX_MB", "0.02")
    b = pdf_с_текстом(3, добавка=3000, большая=2)
    части = ct.части_pdf(b)
    виды = [(ч.первая, ч.последняя, ч.вид) for ч in части]
    assert (2, 2, "text") in виды and виды[0] == (1, 1, "pdf") and виды[-1] == (3, 3, "pdf")
    слой = next(ч for ч in части if ч.вид == "text").тело.decode("utf-8")
    assert слой.startswith("[страница 2]") and "Page text" in слой
    with pytest.raises(rl._Отказ) as о:
        ct.части_pdf(pdf_с_текстом(3, текст=None, добавка=3000, большая=2))
    assert о.value.класс == "предел" and "страница 2" in о.value.причина
    # частей больше предела — limit, как прежде
    monkeypatch.setenv("LLM_READ_MAX_PAGES", "1")
    monkeypatch.setattr(ct, "ЧАСТЕЙ_МАКС", 2)
    with pytest.raises(rl._Отказ):
        ct.части_pdf(pdf_на(3))


class ЧастиКлиент:
    """Модель, которая отвечает по пометке части: часть k — свои записи со страницей
    внутри части; сторону называет только первая часть."""

    def __init__(self, ответы_частей=None):
        self.запросы: list[dict] = []
        self.messages = self
        self.ответы_частей = ответы_частей or {}

    def stream(self, **запрос):
        self.запросы.append(запрос)
        import re
        тексты = [бл.get("text", "") for бл in запрос["messages"][0]["content"] if бл["type"] == "text"]
        k = int(re.search(r"Это часть (\d+) из", " ".join(тексты)).group(1))
        данные = self.ответы_частей.get(k) or {"side": "unknown", "items": []}
        return Поток(ответ(json.dumps(данные, ensure_ascii=False)))


def _ответы_договора() -> dict:
    return {
        1: {"side": "customer", "items": [
            {"field": "deliveryDays", "value": "120", "quote": "ЦИТАТА-ВЫДУМКА срок 120", "page": 2}]},
        2: {"side": "unknown", "items": [
            {"field": "deliveryDays", "value": "90", "quote": "ЦИТАТА-ВЫДУМКА срок в приложении", "page": 1},
            {"field": "penaltyCapPct", "value": "10", "quote": "ЦИТАТА-ВЫДУМКА потолок", "page": 1},
            {"field": "documents", "value": "паспорт", "quote": "ЦИТАТА-ВЫДУМКА паспорт", "page": 1},
            {"field": "warrantyMonths", "value": "24", "quote": "", "page": 1}]},
        3: {"side": "customer", "items": [
            {"field": "documents", "value": "ТР ТС", "quote": "ЦИТАТА-ВЫДУМКА ТР ТС", "page": 1},
            {"field": "documents", "value": "паспорт", "quote": "ЦИТАТА-ВЫДУМКА паспорт", "page": 1}]},
    }


def test_чтение_по_частям_сводит_записи_со_страницами_исходника(monkeypatch):
    monkeypatch.setenv("LLM_READ_MAX_PAGES", "2")
    клиент = ЧастиКлиент(_ответы_договора())
    читатель = ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент)
    итог = читатель.прочитать(pdf_на(5))
    assert итог["status"] == "ok" and len(клиент.запросы) == 3 and читатель.частей == 3
    у, док = итог["terms"], итог["evidence"]
    # первое значение поля в порядке страниц; страницы — исходного файла
    assert у["deliveryDays"] == 120 and док["deliveryDays"]["page"] == 2
    assert у["penaltyCapPct"] == 10 and док["penaltyCapPct"]["page"] == 3      # часть 2: стр. 3–4
    assert у["documents"] == ["паспорт", "ТР ТС"] and у["side"] == "customer"
    assert итог["dropped"] == ["warrantyMonths"], "без цитаты — снято, как у чтения целиком"
    assert итог["pages"] == 5 and итог["usd"] > 0 and "по частям: 3" in итог["note"]
    assert читатель.по_частям == 1 and читатель.страниц == 5
    # части назвали разные стороны — сторона не названа
    ответы = _ответы_договора()
    ответы[3]["side"] = "supplier"
    итог = ct.Читатель("claude-opus-5-5", 10.0, клиент=ЧастиКлиент(ответы)).прочитать(pdf_на(5))
    assert итог["status"] == "ok" and итог["terms"]["side"] is None and "разные стороны" in итог["note"]


def test_чтение_по_частям_бюджет_и_срок(monkeypatch):
    """Потолок цены всех частей — до первого вызова; срок — перед каждой частью после
    первой: срок вышел — файл не прочитан (time), оплаченное за первую часть — в usd."""
    monkeypatch.setenv("LLM_READ_MAX_PAGES", "2")
    клиент = ЧастиКлиент(_ответы_договора())
    _, оценка, _ = ct.Читатель("claude-opus-5-5", 10.0).запрос(pdf_на(2), None)
    читатель = ct.Читатель("claude-opus-5-5", оценка["потолок"] * 2.5, клиент=клиент)
    итог = читатель.прочитать(pdf_на(5))
    assert итог["status"] == "budget" and клиент.запросы == [] and читатель.стоп
    читатель = ct.Читатель("claude-opus-5-5", 10.0, клиент=клиент)
    итог = читатель.прочитать(pdf_на(5), успеть=lambda: False)
    assert итог["status"] == "time" and len(клиент.запросы) == 1 and итог["usd"] > 0 and "terms" not in итог
    д = ct.Документ(deal_id=СДЕЛКА_НОВАЯ, card_entity=ct.СП_РАЗОВЫЙ, card_id=1, order_id=None, side_link="customer",
                    link_source="deal_link", template=None, doc_type=None, side_template=None,
                    side_company="customer", file_field=ФИН_1174, files=[файл("fid-1")])
    r = ct.строка("ct-проба", "claude-opus-5-5", д, д.files[0], status=итог["status"], итог=итог)
    assert r["terms"] is None and r["side"] is None and r["usd"] > 0 and r["model"] == "claude-opus-5-5"
    assert ct.флаги_сделки([r]) == ([], "недочитана")


def test_живой_прогон_читает_большой_pdf_по_частям(monkeypatch, capsys):
    monkeypatch.setenv("LLM_READ_MAX_MB", "0.01")
    файлы = dict(ФАЙЛЫ, **{"fid-поставщик": pdf_с_текстом(5, добавка=4000)})
    клиент = ЧастиКлиент({1: {"side": "supplier", "items": [
        {"field": "deliveryDays", "value": "110", "quote": "ЦИТАТА-ВЫДУМКА lead time", "page": 1}]}})

    class Смешанный:
        """Части — ЧастиКлиент, остальные файлы — обычный выдуманный клиент."""
        def __init__(self):
            self.обычный = Клиент()
            self.messages = self

        def stream(self, **запрос):
            тексты = " ".join(бл.get("text", "") for бл in запрос["messages"][0]["content"] if бл["type"] == "text")
            return клиент.stream(**запрос) if "Это часть" in тексты else self.обычный.messages.stream(**запрос)
    база = База()
    читатель = ct.Читатель("claude-opus-5-5", 10.0, клиент=Смешанный())
    код = ct.прогон("live", 3, 10.0, "claude-opus-5-5", портал=Портал(файлы), run_id="ct-проба",
                    читатель=читатель, база=база)
    журнал = _журнал(capsys)
    r = next(r for r in база.условия if r["card_id"] == 9800002)
    assert r["status"] == "ok" and r["terms"]["deliveryDays"] == 110 and r["pages"] == 5 and код == 0
    assert len(клиент.запросы) >= 3 and "прочитано по частям" in журнал and "файлов 1" in журнал
    for слово in ЗАПРЕТНОЕ:
        assert слово not in журнал, f"в журнал попало «{слово}»"


# --------------------------------------------------------------------------- пересчёт без модели (25.10.2026)
def _строка_базы(deal: int, run: str, card: int, *, terms=None, status="ok", side=None, side_company=None,
                 order=None, entity=ct.СП_РАЗОВЫЙ, link="deal_link") -> dict:
    """Строка contract_terms, как её читает База.разборы (все колонки записи)."""
    прочитан = status in ct.ПРОЧИТАН
    return {"run_id": run, "method_ver": ct.METHOD_VER, "deal_id": deal, "card_entity": entity, "card_id": card,
            "order_id": order, "side": side if прочитан else None,
            "side_source": "model" if прочитан and side else None, "side_link": None, "side_template": None,
            "side_company": side_company, "link_source": link, "template": None, "doc_type": None,
            "file_field": "f", "file_id": f"fid-{card}", "sha256": "a" * 64, "pages": 3, "note": None,
            "status": status, "model": "claude-opus-5-5", "terms": terms if прочитан else None,
            "evidence": {} if прочитан else None, "dropped": [] if прочитан else None, "usd": 0.5}


class БазаПересчёта:
    def __init__(self, выбор, строки, флаги, сверка_ломается=False):
        self.выбор, self.строки, self.флаги_пар = выбор, строки, флаги
        self.записано: dict | None = None
        self.сверка_ломается = сверка_ломается

    def разборы(self):
        return self.выбор, self.строки, self.флаги_пар

    def записать_пересчёт(self, строки, флаги, исходы, run_id, сверить):
        из_базы: dict = {}
        for r in строки:
            из_базы.setdefault((r["deal_id"], r["run_id"]), []).append(r)
        флаги_базы = [{"deal_id": f["deal_id"], "order_id": f["order_id"], "code": f["code"]} for f in флаги]
        if self.сверка_ломается:
            флаги_базы = флаги_базы[1:]
        расхождения = сверить(из_базы, флаги_базы, dict(исходы))
        if расхождения:
            return False, расхождения
        self.записано = {"строки": строки, "флаги": флаги, "исходы": dict(исходы), "run_id": run_id}
        return True, []

    def итог(self, run_id):
        return len(self.записано["строки"]), len(self.записано["флаги"]), 0.0

    def итог_сделок(self, run_id):
        return len(self.записано["исходы"])


def _корпус_пересчёта():
    у_з, _ = _условия(условия_заказчика())
    у_п, _ = _условия(условия_поставщика())
    молчит_п = {**у_п, "side": None}
    # A: поставщик без стороны модели, компания — поставщик заказа: было «без стороны»
    a = [_строка_базы(101, "ct-1", 1, terms=у_з, side="customer", side_company="customer"),
         _строка_базы(101, "ct-1", 2, terms=молчит_п, side=None, side_company="supplier", order=77,
                      link="order_link")]
    # B: пара по модели — пересчёт ничего не меняет
    b = [_строка_базы(102, "ct-1", 3, terms=у_з, side="customer"),
         _строка_базы(102, "ct-1", 4, terms=у_п, side="supplier", order=78, link="order_link")]
    флаги_b = [{"deal_id": 102, "order_id": 78, "code": f["code"], "days": f["days"]}
               for f in ct.флаги_сделки(b)[0]]
    # C: записано «пара», а строки — недочитана: прежнее правило не повторяет записанного
    c = [_строка_базы(103, "ct-1", 5, terms=у_з, side="customer"),
         _строка_базы(103, "ct-1", 6, status="time")]
    выбор = [{"deal": 101, "run": "ct-1", "outcome": "без стороны", "ver": ct.ВЕРСИЯ_ИСХОДА},
             {"deal": 102, "run": "ct-1", "outcome": "пара", "ver": ct.ВЕРСИЯ_ИСХОДА},
             {"deal": 103, "run": "ct-1", "outcome": "пара", "ver": ct.ВЕРСИЯ_ИСХОДА}]
    return выбор, {(101, "ct-1"): a, (102, "ct-1"): b, (103, "ct-1"): c}, {(102, "ct-1"): флаги_b}


def test_пересчёт_холостой_по_умолчанию_и_только_счёт():
    выбор, строки, флаги = _корпус_пересчёта()
    база = БазаПересчёта(выбор, строки, флаги)
    журнал: list[str] = []
    assert ct.пересчёт(база, "ctr-проба", применить=False, печать=журнал.append) == 0
    assert база.записано is None
    текст = "\n".join(журнал)
    assert "сделок, у которых пересчёт меняет исход или флаги: 1 — без стороны → пара 1" in текст
    assert "не повторил записанный исход: 1" in текст
    assert "сторона по карточке (модель не назвала; решение владельца 25.10.2026): 1 документов" in текст
    for слово in ("101", "102", "103", "ct-1", "fid-", "ЦИТАТА-ВЫДУМКА", ПУНКТ, "ВЫДУМГРАД"):
        assert слово not in текст, f"в журнал попало «{слово}»"


def test_пересчёт_пишет_копии_с_новым_ключом_и_второй_проверкой():
    выбор, строки, флаги = _корпус_пересчёта()
    база = БазаПересчёта(выбор, строки, флаги)
    журнал: list[str] = []
    assert ct.пересчёт(база, "ctr-проба", применить=True, печать=журнал.append) == 0
    з = база.записано
    assert з["исходы"] == {101: "пара"} and з["run_id"] == "ctr-проба"
    assert {r["deal_id"] for r in з["строки"]} == {101} and len(з["строки"]) == 2
    for r in з["строки"]:
        assert r["run_id"] == "ctr-проба" and r["usd"] == 0.0 and r["status"] == "reused"
        assert r["note"].startswith("пересчёт без модели из ct-1") and r["method_ver"] == ct.METHOD_VER
    поставщик = next(r for r in з["строки"] if r["card_id"] == 2)
    assert (поставщик["side"], поставщик["side_source"]) == ("supplier", "card")
    assert {f["code"] for f in з["флаги"]} >= {"lateByDesign"} and all(f["order_id"] == 77 for f in з["флаги"])
    # исходные строки не тронуты (пометка, а не правка)
    assert строки[(101, "ct-1")][1]["side"] is None and строки[(101, "ct-1")][1]["run_id"] == "ct-1"
    assert any("вторая проверка (пересчёт из базы) сошлась" in s for s in журнал)
    # повтор пересчёта по его же копиям — писать нечего, заметка не растёт
    копии = {(101, "ctr-проба"): з["строки"]}
    повтор = БазаПересчёта([{"deal": 101, "run": "ctr-проба", "outcome": "пара", "ver": ct.ВЕРСИЯ_ИСХОДА}], копии,
                           {(101, "ctr-проба"): [{**f} for f in з["флаги"]]})
    журнал = []
    assert ct.пересчёт(повтор, "ctr-2", применить=True, печать=журнал.append) == 0 and повтор.записано is None
    assert ct.копия_строки(з["строки"][0], "ctr-2")["note"].count("пересчёт без модели") == 1


def test_пересчёт_откатывается_при_расхождении_и_красном_гейте():
    выбор, строки, флаги = _корпус_пересчёта()
    база = БазаПересчёта(выбор, строки, флаги, сверка_ломается=True)
    журнал: list[str] = []
    assert ct.пересчёт(база, "ctr-проба", применить=True, печать=журнал.append) == 1
    assert база.записано is None and any("запись откатана целиком" in s for s in журнал)
    # модель почти не называет сторону — гейт красный, ничего не пишется
    у_з, _ = _условия(условия_заказчика())
    молчат = {(201, "ct-1"): [_строка_базы(201, "ct-1", 1, terms={**у_з, "side": None}, side_company="customer"),
                              _строка_базы(201, "ct-1", 2, terms={**у_з, "side": None}, side_company="supplier",
                                           order=7)]}
    база = БазаПересчёта([{"deal": 201, "run": "ct-1", "outcome": "без стороны", "ver": "v"}], молчат, {})
    журнал = []
    assert ct.пересчёт(база, "ctr-проба", применить=True, печать=журнал.append) == 1 and база.записано is None
    assert any("гейт стороны не пройден" in s for s in журнал)


def test_журнал_пар_считает_поля_флагов_срока_приёмки_валюты():
    у_з, _ = _условия(условия_заказчика())
    у_п, _ = _условия(условия_поставщика())
    пары: list = []
    ct.флаги_сделки([_строка("customer", {**у_з, "currency": "RUB"}, card=1),
                     _строка("supplier", {**у_п, "deliveryDays": None, "deliveryDate": "2026-12-01",
                                          "currency": "EUR"}, card=2, order=ЗАКАЗ, снято=("claimDays",))],
                    пары=пары)
    строки = ct.журнал_пар(пары)
    текст = "\n".join(строки)
    assert "deliveryDays: заказчик 1, поставщик 0" in текст and "deliveryDate: заказчик 0, поставщик 1" in текст
    assert "дни против даты (без даты отсчёта не сравнить) 1" in текст
    assert "приёмка у заказчика поздняя (монтаж, пусконаладка, ввод): 1" in текст
    assert "валюта заказчика: рубли 1" in текст and "поставщика: рубли 0, валюта 1" in текст
    assert ct.журнал_пар([]) == []
    for слово in ("ПРИЁМКА-ВЫДУМКА", "2026-12-01", ПУНКТ):
        assert слово not in текст


def test_главный_вход_пересчёта_без_портала_и_модели(monkeypatch, capsys):
    """--mode reflag не требует ни вебхука портала, ни ключа модели; без строки базы — стоп."""
    for имя in ("BITRIX_WEBHOOK_URL", "ANTHROPIC_API_KEY", "SUPABASE_DB_URL", "APPLY"):
        monkeypatch.delenv(имя, raising=False)
    assert ct.main(["--mode", "reflag"]) == 2
    assert "режим reflag без SUPABASE_DB_URL" in _журнал(capsys)
    вызовы = []

    class Б:
        def __init__(self, dsn):
            вызовы.append(dsn)

        def нет_таблиц(self):
            return []

        def закрыть(self):
            вызовы.append("закрыта")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://выдумка.invalid/db")
    monkeypatch.setattr(ct, "База", Б)
    monkeypatch.setattr(ct, "пересчёт", lambda база, run_id, *, применить, печать=print: 7 if применить else 5)
    assert ct.main(["--mode", "reflag"]) == 5 and ct.main(["--mode", "reflag", "--apply"]) == 7
    assert вызовы.count("закрыта") == 2
