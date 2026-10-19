"""Пилот чтения договоров (scripts/contract_terms.py) — без сети.

Проверяется то, что защищает деньги, данные и журнал:
  · отбор: последние НЕ проигранные сделки реализации, карточки СП-1174 и СП-1192
    по parentId2 этих сделок, поле файла по приоритету, сторона по связи с заказом
    поставщику, подписи списков и сверка стороны вторым способом;
  · строгая схема ответа выведена из contract_b2b.TERMS_SCHEMA;
  · значение без цитаты снимается, сторона из ответа модели читается;
  · бюджет останавливает чтение ДО вызова, который в него не помещается;
  · строки и флаги: спорная сторона во флаги не идёт, недочитанная сделка флагов
    не получает;
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
}


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
}


class Портал:
    """Отвечает как портал на те методы, что зовёт отбор; фильтров не применяет
    (их применение проверяется по записанным параметрам), кроме parentId2 и >id."""

    def __init__(self, файлы: dict[str, bytes] | None = None):
        self.вызовы: list[tuple[str, dict]] = []
        self.скачано: list[str] = []
        self.файлы = файлы or {}

    def вызов(self, метод: str, п: dict) -> dict:
        self.вызовы.append((метод, json.loads(json.dumps(п, default=str))))
        if метод == "crm.deal.list":
            return {"result": СДЕЛКИ[п["start"]:п["start"] + 50]}
        if метод == "crm.item.fields":
            return {"result": {"fields": ПОЛЯ[п["entityTypeId"]]}}
        if метод == "crm.item.list":
            ф = п["filter"]
            записи = [{к: v for к, v in r.items() if к in п["select"]} for r in КАРТОЧКИ[п["entityTypeId"]]
                      if int(r["parentId2"]) in ф["@parentId2"] and int(r["id"]) > ф[">id"]]
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


def ответ(данные, вход: int = 10_000, выход: int = 2_000, стоп: str = "end_turn"):
    текст = данные if isinstance(данные, str) else json.dumps(данные, ensure_ascii=False)
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
    def __init__(self, кэш: dict | None = None):
        self.условия: list[dict] = []
        self.флаги: list[dict] = []
        self.кэш = кэш or {}

    def из_кэша(self, sha, модель):
        return self.кэш.get(sha)

    def записать_условия(self, строки):
        self.условия += строки

    def записать_флаги(self, флаги, run_id):
        self.флаги += флаги

    def итог(self, run_id):
        return len(self.условия), len(self.флаги), sum(r["usd"] for r in self.условия)


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
    assert {п["entityTypeId"] for п in списки} == {ct.СП_РАЗОВЫЙ, ct.СП_РАМОЧНЫЙ}
    for п in списки:
        assert п["start"] == -1 and п["order"] == {"id": "ASC"}
        assert sorted(п["filter"]["@parentId2"]) == sorted([СДЕЛКА_НОВАЯ, СДЕЛКА_ВТОРАЯ])
    рамочный = next(п for п in списки if п["entityTypeId"] == ct.СП_РАМОЧНЫЙ)
    assert "parentId172" not in рамочный["select"], "поле, которого у типа нет, спрашивать нельзя"
    assert not отбор.нет_полей

    д = {x.card_id: x for x in отбор.все()}
    assert 9800004 not in д, "карточка проигранной сделки попала в отбор"
    заказчик, поставщик, пустая, рамочный_д = д[9800001], д[9800002], д[9800003], д[9800005]
    assert (заказчик.side, заказчик.side_source, заказчик.order_id) == ("customer", "deal_link", None)
    assert (поставщик.side, поставщик.side_source, поставщик.order_id) == ("supplier", "order_link", ЗАКАЗ)
    # приоритет поля файла: финальная версия, иначе текущий файл
    assert заказчик.file_field == ФИН_1174 and [f["id"] for f in заказчик.files] == ["fid-заказчик"]
    assert поставщик.file_field == ТЕК_1174 and [f["id"] for f in поставщик.files] == ["fid-поставщик"]
    assert пустая.files == [] and пустая.file_field is None
    assert рамочный_д.side == "customer" and рамочный_д.file_field == ФИН_1192
    # подписи списков и сверка стороны вторым способом
    assert заказчик.template == "Шаблон заказчика ВЫДУМКА" and заказчик.doc_type == "Спецификация ВЫДУМКА"
    assert (заказчик.side_template, поставщик.side_template, пустая.side_template) == ("customer", "supplier", None)
    assert (заказчик.side_company, поставщик.side_company, рамочный_д.side_company) == ("customer", "supplier", None)
    # в сделке разовый договор раньше рамочного
    assert [x.card_entity for x in отбор.документы[СДЕЛКА_ВТОРАЯ]] == [ct.СП_РАЗОВЫЙ, ct.СП_РАМОЧНЫЙ]


def test_сторона_двойная_связь_и_подписи():
    assert ct.сторона({"parentId172": "77", "parentId2": "5"}) == ("supplier", "order_link")
    assert ct.сторона({"parentId172": "0", "parentId2": "5"}) == ("customer", "deal_link")
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
def test_строгая_схема_из_terms_schema_в_пределах_строгого_режима():
    с = ct.СХЕМА_ОТВЕТА
    assert set(с["properties"]) == set(ct.contract_b2b.TERMS_SCHEMA["properties"])
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
    # пределы считаются по всем строгим схемам запроса — у нас она одна
    assert счёт["необязательных"] <= ct.ПРЕДЕЛ_НЕОБЯЗАТЕЛЬНЫХ, счёт
    assert счёт["объединений"] <= ct.ПРЕДЕЛ_ОБЪЕДИНЕНИЙ, счёт
    assert {"documents", "evidence"} <= set(с["required"]) and "side" not in с["required"]
    поля = с["properties"]["evidence"]["items"]["properties"]["field"]["enum"]
    assert "side" not in поля and set(поля) == set(ct.поля_условий()) - {"side"}
    assert с["properties"]["side"]["enum"] == ["customer", "supplier"]


def test_пропущенное_поле_читается_как_null():
    условия, доказательства, снято = ct.разобрать_ответ(json.dumps(
        {"documents": [], "evidence": [], "deliveryDays": 30}, ensure_ascii=False))
    assert условия["side"] is None and условия["incoterms"] is None
    assert условия["deliveryDays"] is None and снято == 1          # без цитаты — снят
    assert доказательства == {}


def test_значение_без_цитаты_снимается():
    у = условия_заказчика()
    у["law"] = "ПРАВО-ВЫДУМКА"                        # без записи в evidence
    у["evidence"].append({"field": "currency", "quote": "в рублях", "page": 2})   # цитата к пустому полю
    условия, доказательства, снято = ct.разобрать_ответ(json.dumps(у, ensure_ascii=False))
    assert снято == 1 and условия["law"] is None
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
def _строка(д_side: str, terms: dict | None, *, status="ok", order=None, card=1, entity=ct.СП_РАЗОВЫЙ):
    д = ct.Документ(deal_id=СДЕЛКА_НОВАЯ, card_entity=entity, card_id=card, order_id=order, side=д_side,
                    side_source="order_link" if order else "deal_link", template=None, doc_type=None,
                    side_template=None, side_company=None, file_field=ФИН_1174, files=[файл(f"fid-{card}")])
    итог = {"terms": terms, "evidence": {}, "usd": 0.1, "pages": 3} if terms else {}
    return ct.строка("ct-проба", "claude-opus-5-5", д, д.files[0], status=status, sha256="0" * 64, итог=итог)


def test_строки_и_флаги_сделки():
    у_з, _, _ = ct.разобрать_ответ(json.dumps(условия_заказчика(), ensure_ascii=False))
    у_п, _, _ = ct.разобрать_ответ(json.dumps(условия_поставщика(), ensure_ascii=False))
    з = _строка("customer", у_з, card=1)
    п = _строка("supplier", у_п, card=2, order=ЗАКАЗ)
    assert з["status"] == "ok" and з["terms"]["deliveryDays"] == 120 and з["model"] == "claude-opus-5-5"
    assert set(ct.База.КОЛОНКИ) <= set(з)
    флаги, исход = ct.флаги_сделки([з, п])
    коды = {f["code"]: f["days"] for f in флаги}
    assert исход == "пара" and коды["lateByDesign"] == 9                 # 120 − (110 + 19)
    assert {"basisGap", "penaltyUncovered", "penaltyNoCap", "warrantyGap", "claimGap"} <= set(коды)
    assert all(f["order_id"] == ЗАКАЗ and f["deal_id"] == СДЕЛКА_НОВАЯ for f in флаги)

    # модель прочла «поставщика» как договор с заказчиком — сторона спорная, пары нет
    спорная = _строка("supplier", у_з, card=3, order=ЗАКАЗ)
    assert ct.сторона_спорная(спорная)
    assert ct.флаги_сделки([з, спорная]) == ([], "нет пары")
    # недочитанный файл (бюджет, закачка) — флагов нет, а не «чисто»
    for статус in ("budget", "download"):
        assert ct.флаги_сделки([з, п, _строка("supplier", None, status=статус, card=4, order=ЗАКАЗ)]) \
            == ([], "недочитана")
    # непрочитанная строка не несёт условий
    отказ = _строка("customer", None, status="format", card=5)
    assert отказ["terms"] is None and отказ["evidence"] is None and отказ["usd"] == 0.0


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
    у, док, _ = ct.разобрать_ответ(json.dumps(условия_заказчика(), ensure_ascii=False))
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
    assert {м for м, _п in портал.вызовы} == {"crm.deal.list", "crm.item.fields", "crm.item.list"}
    assert "claude-opus-5-5: файлов 3" in журнал and "claude-sonnet-5-5" in журнал
    assert "холостой прогон" in журнал
    for слово in ЗАПРЕТНОЕ:
        assert слово not in журнал, f"в журнал попало «{слово}»"


def test_оценка_денег_растёт_со_страницами():
    о = ct.оценка_денег(10, "claude-opus-5-5")
    assert 0 < о["мало"] < о["обычно"] < о["предел"]
    assert ct.оценка_денег(10, "claude-sonnet-5-5")["обычно"] == pytest.approx(о["обычно"] / 2)


# --------------------------------------------------------------------------- прогон Actions
def test_прогон_только_ручной_в_очереди_портала_без_артефактов():
    import yaml
    путь = ROOT / ".github" / "workflows" / "contract-terms.yml"
    текст = путь.read_text(encoding="utf-8")
    wf = yaml.safe_load(текст)
    on = wf.get("on", wf.get(True))
    assert set(on) == {"workflow_dispatch"}, "только ручной запуск (решение владельца 19.10.2026)"
    входы = on["workflow_dispatch"]["inputs"]
    assert входы["deals"]["default"] == "30" and входы["budget_usd"]["default"] == "10"
    assert входы["mode"]["default"] == "dry" and входы["mode"]["options"] == ["dry", "live"]
    assert wf["concurrency"]["group"] == "bitrix-portal"
    for запрет in ("upload-artifact", "actions/cache", "cache:"):
        assert запрет not in текст, запрет
    шаг = wf["jobs"]["terms"]["steps"][-1]
    assert шаг["run"].startswith("python scripts/contract_terms.py")
    env = шаг["env"]
    for секрет in ("BITRIX_WEBHOOK_URL", "BITRIX_FILES_WEBHOOK_URL", "ANTHROPIC_API_KEY", "SUPABASE_DB_URL"):
        assert f"secrets.{секрет}" in env[секрет], секрет
    assert "inputs.mode == 'live'" in env["ANTHROPIC_API_KEY"] and "inputs.mode == 'live'" in env["SUPABASE_DB_URL"]
    assert float(env["BITRIX_RPS"]) <= 2 and env["BITRIX_PARALLEL"] == "1"
    assert env["RUN_ID"].startswith("ct-")


def test_схема_подключена_к_миграциям():
    миграции = (ROOT / ".github/workflows/zip-db.yml").read_text(encoding="utf-8")
    assert "apply library/supabase/contract_terms_schema.sql" in миграции
    assert (миграции.index("apply library/supabase/contract_terms_schema.sql")
            < миграции.index("apply zip/supabase/migrations_rls_stage1.sql"))
