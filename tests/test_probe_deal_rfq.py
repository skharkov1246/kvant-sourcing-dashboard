"""Зонд v61 «Запросы поставщикам одной сделки»: что дало бы неверный вывод или утечку.

1. Вход — только хеши токенов названия: токены и хеш считаются одним правилом с шапкой
   зонда; вход не того вида — выход 2 без эха значения; в Actions вход читается из файла
   события, а не из env шага (env шага печатается в публичном журнале), хеши маскируются.
2. Сделка совпала, только если ВСЕ хеши запроса есть в её названии; несколько совпавших —
   выбор по карточкам с брендом компрессора, затем по числу карточек, затем новейшая.
3. Статус карточки — один, по старшинству; молчание при ответе или КП не бывает никогда.
4. Ответ засчитан, только если он от поставщика карточки и после её якоря (первое письмо,
   иначе вход в «отправлен» / «нет ответа»); у неотправленной карточки якоря нет — письмо
   поставщика не делает её отвеченной и отправленной; бесплатная почта, наши домены и
   домен, общий у разных поставщиков, не сверяются.
5. Бренд: «seat» — не бренд; карточка → поставщик (образец бренда) → единственный бренд
   сделки; образец производителя ставит только вид поставщика; широкие слова (cooperation,
   Siemens, Mitsubishi Electric, Shenyang …) бренда не дают.
6. Журнал публичный: ни названий сделки, карточек и компаний, ни номеров, адресов, доменов,
   ни людей, ни хешей запроса; упавшая часть или непрочитанные строки и входящие сделки —
   вердикт «предварительный», последняя строка — сводка нагрузки.
7. probe.yml: зонд в закрытом списке `script`, вход `query` есть, а в env шага его нет.

Корпус придуман (правило 18): клиент, поставщики, адреса и номера нарочно особые.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kvant_probe_deal_rfq", ROOT / "scripts" / "probe_deal_rfq.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

СЕГОДНЯ = dt.date(2026, 10, 29)
OFFER = m.config.RFQ_OFFER_FIELD
АРХИВ = next(f for f in m.config.RFQ_QUOTE_FIELDS if f != OFFER)


def t(s: str) -> str:
    return s if "T" in s else s + "T12:00:00+03:00"


def h(*слова) -> str:
    return ",".join(hashlib.sha256(w.encode("utf-8")).hexdigest() for w in слова)


# ── 1–2. запрос, токены, совпадение ─────────────────────────────────────────────────────────

def test_токены_и_хеш_как_в_шапке():
    assert m.токены("ТЕСТ-2030 ЗИП") == ["тест", "2030", "зип"]
    assert m.токены("Ёлка_ёж/№5 x-1") == ["елка", "еж", "5", "x", "1"]
    assert m.токены("") == [] and m.токены(None) == []
    assert m.хеш("зип") == hashlib.sha256("зип".encode("utf-8")).hexdigest()
    # однострочник владельца из шапки даёт те же хеши
    однострочник = ",".join(hashlib.sha256(x.encode()).hexdigest()
                            for x in re.split("[^0-9a-zа-я]+", "ТЕСТ-2030 ЗИП".lower().replace("ё", "е")) if x)
    assert однострочник == ",".join(m.хеш(x) for x in m.токены("ТЕСТ-2030 ЗИП"))
    assert "ТЕСТ-2030 ЗИП" in m.__doc__


def test_разбор_запроса():
    a, b = "a" * 64, "0123456789abcdef" * 4
    assert m.разобрать_запрос(f" {a} , {b} ,{a}") == [a, b]               # пробелы по краям, повтор
    for плохо in ("", None, " , ", "a" * 63, "g" * 64, ",".join([a] * 9), f"{a},зип", f"{a};{b}",
                  b.upper(), f"{a},,{b}", f"{a},", "ТЕСТ-2030 ЗИП"):
        assert m.разобрать_запрос(плохо) is None, плохо
    assert len(m.разобрать_запрос(",".join(f"{i:064x}" for i in range(8)))) == 8
    # 9 элементов с повтором — тоже больше 8: счёт до снятия повторов
    assert m.разобрать_запрос(",".join([f"{i:064x}" for i in range(8)] + [f"{0:064x}"])) is None


def test_вход_из_файла_события_а_не_из_env(tmp_path):
    q = h("тест", "2030", "зип")
    событие = tmp_path / "event.json"
    событие.write_text('{"inputs": {"script": "probe_deal_rfq.py", "query": "%s"}}' % q, encoding="utf-8")
    assert m.сырой_запрос({"GITHUB_EVENT_PATH": str(событие)}) == q
    assert m.сырой_запрос({"PROBE_QUERY": "x", "GITHUB_EVENT_PATH": str(событие)}) == "x"   # тесты, локально
    assert m.сырой_запрос({"PROBE_QUERY": "", "GITHUB_EVENT_PATH": str(событие)}) == q
    assert m.сырой_запрос({}) is None
    assert m.сырой_запрос({"GITHUB_EVENT_PATH": str(tmp_path / "нет.json")}) is None
    битый = tmp_path / "bad.json"
    битый.write_text("{не json", encoding="utf-8")
    assert m.сырой_запрос({"GITHUB_EVENT_PATH": str(битый)}) is None
    без_входа = tmp_path / "pr.json"
    без_входа.write_text('{"pull_request": {"number": 1}}', encoding="utf-8")
    assert m.сырой_запрос({"GITHUB_EVENT_PATH": str(без_входа)}) is None
    assert m.маска(["ab", "cd"]) == ["::add-mask::ab", "::add-mask::cd"]


def test_совпадение_все_токены():
    q = m.разобрать_запрос(h("тест", "2030", "зип"))
    assert m.совпадает("ТЕСТ-2030 ЗИП компрессоры", q)
    assert m.совпадает("зип / Тест (2030)", q)                    # порядок и знаки не важны
    assert not m.совпадает("ТЕСТ-2030 ремонт", q)                 # нет одного токена
    assert not m.совпадает("ТЕСТОВЫЙ-2030 ЗИП", q)                # токен целиком, не подстрока
    assert not m.совпадает("что угодно", [])
    assert m.по_токенам(["ТЕСТ-2030 ЗИП", "ТЕСТ-2029", "прочее"], q) == [2, 1, 1]


def test_холдинг_по_kam_и_по_образцу():
    assert m.из_холдинга("Альфа Учебная", холдинг="Альфа Учебная", образец=re.compile("^$x"))
    assert m.из_холдинга("ALFATEST Ltd", холдинг="—", образец=re.compile(r"alfatest", re.I))
    assert not m.из_холдинга("Бета Учебная", холдинг="Альфа Учебная", образец=re.compile("^$x"))


# ── 5. бренд и вид поставщика ───────────────────────────────────────────────────────────────

def test_бренд_текста():
    assert m.бренд_текста("Клапан Howden Thomassen") == "Howden"            # раньше стоит
    assert m.бренд_текста("Ротор Нуово Пиньоне") == "Nuovo Pignone"
    assert m.бренд_текста("новопиньон ротор") == "Nuovo Pignone"
    assert m.бренд_текста("кольца СИАД") == "SIAD"
    assert m.бренд_текста("Хёрбигер клапан") == "Hoerbiger"
    assert m.бренд_текста("MAN Energy ротор") == "MAN Energy Solutions"
    assert m.бренд_текста("manual для German Ariel") == "Ariel"
    # «seat» — седло клапана, не бренд; «siad» внутри слова — не бренд
    assert m.бренд_текста("valve seat седло клапана") is None
    assert m.бренд_текста("Seat") is None
    assert m.бренд_текста("siadx") is None
    assert all(not re.search(r"\bseat\b", б.pattern) for _, б, _ in m.БРЕНДЫ)
    # падежи кириллицы
    assert m.бренд_текста("Запрос СИАДу на кольца") == "SIAD"
    assert m.бренд_текста("письмо СИАДа") == "SIAD"
    assert m.бренд_текста("клапан Борсиг") == "Borsig" and m.бренд_текста("Борзига ротор") == "Borsig"
    assert m.бренд_текста("ротор Эллиотта") == "Elliott"
    assert m.бренд_текста("клапан Ариэля") == "Ariel"
    # широкие слова — не бренд (придуманные названия)
    for мимо in ("Global Cooperation Trading", "Купер Трейд", "подшипник Cooper", "Siemens Healthineers Test",
                 "Сименс Учебный", "Shenyang Valve Test Co", "Mitsubishi Electric Test", "Мицубиси Электрик Учебный"):
        assert m.бренд_текста(мимо) is None, мимо
        assert m.бренд_текста(мимо, по=2) is None, мимо
    assert m.бренд_текста("Cooper-Bessemer поршень") == "Cooper / Cameron"
    assert m.бренд_текста("Siemens Energy ротор") == "Dresser-Rand / Siemens Energy"
    assert m.бренд_текста("Shenyang Blower ротор") == "Shenyang Blower"
    assert m.бренд_текста("Mitsubishi Heavy ротор") == "Mitsubishi Heavy"


def test_brand_of_порядок_источников():
    assert m.brand_of("Запрос Howden", "SIAD Учебная", ["Borsig"]) == ("Howden", "карточка")
    assert m.brand_of("Запрос", "SIAD Учебная", ["Borsig"]) == ("SIAD", "поставщик")
    # материнская компания из образца производителя бренда не ставит: продаёт и другое
    assert m.brand_of("Запрос", "BHGE Учебный филиал", []) == (m.НЕТ_БРЕНДА, "")
    assert m.brand_of("Запрос", "Caterpillar Учебный дилер", []) == (m.НЕТ_БРЕНДА, "")
    assert m.brand_of("Запрос", "Ebara Учебные насосы", ["Borsig"]) == ("Borsig", "сделка")
    assert m.brand_of("Запрос", "ООО Ромашка", ["Borsig", "Borsig", m.НЕТ_БРЕНДА]) == ("Borsig", "сделка")
    assert m.brand_of("Запрос", "ООО Ромашка", ["Borsig", "SIAD"]) == (m.НЕТ_БРЕНДА, "")
    assert m.brand_of("клапан seat", "", []) == (m.НЕТ_БРЕНДА, "")
    assert m.brand_of("Запрос клапанов", "Global Cooperation Trading", []) == (m.НЕТ_БРЕНДА, "")


def test_вид_поставщика():
    assert m.supplier_type("Howden", "Howden Учебный завод", True) == "производитель"
    assert m.supplier_type("Nuovo Pignone", "Бейкер Хьюз Учебная", True) == "производитель"
    assert m.supplier_type("Howden", "Chart Industries Учебная", True) == "производитель"
    assert m.supplier_type("Howden", "ООО Ромашка-Компрессор", True) == "дилер/трейдер"
    assert m.supplier_type(m.НЕТ_БРЕНДА, "Howden Учебный завод", True) == "дилер/трейдер"
    assert m.supplier_type("Howden", "", False) == "поставщик не указан"
    # без границы слова «cooper» и голое «siemens» делали трейдера производителем
    assert m.supplier_type("Cooper / Cameron", "Global Cooperation Trading", True) == "дилер/трейдер"
    assert m.supplier_type("Cooper / Cameron", "Купер Трейд", True) == "дилер/трейдер"
    assert m.supplier_type("Dresser-Rand / Siemens Energy", "Siemens Healthineers Test", True) == "дилер/трейдер"
    assert m.supplier_type("Mitsubishi Heavy", "Mitsubishi Electric Test", True) == "дилер/трейдер"
    assert m.supplier_type("Neuman & Esser", "Нойман Учебный", True) == "дилер/трейдер"


def test_поставщик_карточки_без_компании_клиента():
    к = {"ufCrm18Supplier": ["CO_11", "C_21"], "ufCrm18SupplContact": "CT_22", "companyId": 5}
    п = m.поставщик_карточки(к, {"22": "12"}, {"5"})
    assert п["companies"] == ["11", "12"] and п["contacts"] == ["21", "22"] and п["key"] == "CO11"
    assert п["client_company"]
    п = m.поставщик_карточки({"companyId": "5"}, {}, {"5"})
    assert п["companies"] == [] and п["key"] == "" and п["client_company"]
    assert m.поставщик_карточки({"ufCrm18SupplContact": ["C_7"]}, {}, set())["key"] == "CT7"


def test_исполнитель_не_служебный():
    assert m.исполнитель({"assignedById": "234", "movedBy": "77"}, {"234"}) == ("77", "двигал стадию")
    assert m.исполнитель({"assignedById": "78"}, {"234"}) == ("78", "ответственный")
    assert m.исполнитель({"assignedById": "234"}, {"234"}) == ("", "не определён")


# ── 3. статус карточки ──────────────────────────────────────────────────────────────────────

def карточка(**к):
    база = {"id": "1", "bucket": "sent", "letters": 0, "first_out": None, "last_out": None, "hist": {},
            "reply": None, "in_card": [], "offer": False, "quote_any": False,
            "created": m._вр(t("2026-09-01"))}
    база.update(к)
    return база


@pytest.mark.parametrize("молчание_по", [
    {"letters": 1, "last_out": m._вр(t("2026-10-01")), "first_out": m._вр(t("2026-10-01"))},
    {"bucket": "sent"}, {"bucket": "no_answer"}, {"bucket": "new", "hist": {"sent": m._вр(t("2026-10-01"))}},
])
@pytest.mark.parametrize("ответ", [
    {"offer": True}, {"quote_any": True}, {"bucket": "paw"}, {"bucket": "selected"}, {"bucket": "notsel"},
    {"hist": {"paw": m._вр(t("2026-10-05"))}}, {"bucket": "refused"}, {"bucket": "dialog"},
    {"hist": {"dialog": m._вр(t("2026-10-05"))}}, {"hist": {"refused": m._вр(t("2026-10-05"))}},
    {"reply": m._вр(t("2026-10-06"))}, {"in_card": [m._вр(t("2026-10-06"))]},
])
def test_молчания_при_ответе_или_кп_не_бывает(молчание_по, ответ):
    к = карточка(**молчание_по)
    assert m.card_status(к, СЕГОДНЯ)["status"] == "молчание"
    к = карточка(**{**молчание_по, **ответ, "hist": {**молчание_по.get("hist", {}), **ответ.get("hist", {})}})
    assert m.card_status(к, СЕГОДНЯ)["status"] in m.С_ОТВЕТОМ


def test_старшинство_статусов():
    st = lambda **к: m.card_status(карточка(**к), СЕГОДНЯ)  # noqa: E731
    assert st(bucket="refused", offer=True) == {"status": "КП", "sub": "без итога"}        # КП > отказ
    assert st(bucket="selected")["sub"] == "выбран" and st(bucket="notsel")["sub"] == "не выбран"
    assert st(bucket="refused", reply=m._вр(t("2026-10-06")))["status"] == "отказ"         # отказ > ответ
    assert st(bucket="dialog", reply=m._вр(t("2026-10-06")))["sub"] == "по стадии и письму"
    assert st(bucket="sent", reply=m._вр(t("2026-10-06")))["sub"] == "по письму"
    assert st(bucket="no_answer", hist={"dialog": m._вр(t("2026-10-02"))})["sub"] == "по стадии"
    assert st(bucket="new") == {"status": "не отправлена", "sub": ""}
    assert st(bucket="other") == {"status": "двинута без письма", "sub": ""}
    assert st(bucket="") == {"status": "прочее", "sub": ""}
    тихо = st(bucket="no_answer", hist={"sent": m._вр(t("2026-10-01"))})
    assert тихо["closed"] and тихо["no_letter"] and тихо["wd"] == 20 and тихо["sub"] == "11–20 р.д."
    # отсчёт — от ПОСЛЕДНЕГО письма
    по_письму = st(letters=2, first_out=m._вр(t("2026-08-03")), last_out=m._вр(t("2026-10-27")))
    assert по_письму["wd"] == 2 and по_письму["sub"] == "до 5 р.д." and not по_письму["no_letter"]


def test_корзины_молчания():
    assert [m.silence_bucket(x) for x in (0, 5, 6, 10, 11, 20, 21, None)] == [
        "до 5 р.д.", "до 5 р.д.", "6–10 р.д.", "6–10 р.д.", "11–20 р.д.", "11–20 р.д.", "больше 20 р.д.",
        m.ВНЕ_КАЛЕНДАРЯ]


# ── 4. письма и ответы ──────────────────────────────────────────────────────────────────────

def письмо(i, тип, владелец, направление, когда, тема="", от="", кому="", комм=()):
    return {"ID": str(i), "OWNER_TYPE_ID": str(тип), "OWNER_ID": str(владелец), "DIRECTION": str(направление),
            "CREATED": t(когда), "SUBJECT": тема, "PROVIDER_ID": "CRM_EMAIL",
            "SETTINGS": {"EMAIL_META": {"from": от, "to": кому}} if (от or кому) else [],
            "COMMUNICATIONS": list(комм)}


def test_разбор_письма_только_домены():
    p = m.разбор_письма(письмо(1, 166, 9, 2, "2026-10-01", "Re: Запрос КП", "Мы <s@our-test.example>",
                               "a@eu.vendor-test.example, b@x.co.uk",
                               [{"VALUE": "c@third-test.example", "ENTITY_TYPE_ID": "3", "ENTITY_ID": "44"}]))
    assert p["from"] == {"our-test.example"}
    assert p["to"] == {"vendor-test.example", "x.co.uk", "third-test.example"}
    assert p["ents"] == {(3, "44")} and p["subj"] == "запрос кп" and p["dir"] == "2"
    вх = m.разбор_письма(письмо(2, 3, 44, 1, "2026-10-02", от="Имя <c@mail.third-test.example>"))
    assert вх["from"] == {"third-test.example"} and вх["type"] == 3


def test_сводка_писем_и_чужие():
    письма = [m.разбор_письма(письмо(1, 166, 9, 2, "2026-10-05", "Запрос 1", кому="a@v.example")),
              m.разбор_письма(письмо(2, 166, 9, 2, "2026-10-01", "Запрос 1", кому="a@v.example")),
              m.разбор_письма(письмо(3, 166, 9, 1, "2026-10-07")),
              m.разбор_письма(письмо(4, 166, 77, 2, "2026-10-01")),
              m.разбор_письма(письмо(5, 3, 9, 2, "2026-10-01"))]
    с, чужие = m.сводка_писем(письма, {"9"})
    assert чужие == 2 and len(с["9"]["out"]) == 2 and с["9"]["out"][0] < с["9"]["out"][1]
    assert len(с["9"]["in"]) == 1 and с["9"]["to"] == {"v.example"}


def test_ответ_только_от_поставщика_и_после_письма():
    к1 = карточка(id="1", supplier="CO11", companies=["11"], contacts=["21"], contacts_all={"21"},
                  first_out=m._вр(t("2026-10-01")), domains={"vendor-a.example", "gmail.com", "our.example",
                                                             "shared.example"},
                  subj={"запрос кп ротор 001"}, ents=set())
    к2 = карточка(id="2", supplier="CO12", companies=["12"], contacts=[], first_out=m._вр(t("2026-10-01")),
                  domains={"vendor-b.example", "shared.example"}, subj=set(), ents=set())
    вх = [m.разбор_письма(x) for x in (
        письмо(1, 4, 11, 1, "2026-09-30", от="x@vendor-a.example"),            # до письма — не ответ
        письмо(2, 4, 12, 1, "2026-10-02", от="x@vendor-b.example"),            # от другого поставщика
        письмо(3, 2, 900, 1, "2026-10-03", от="x@gmail.com"),                  # бесплатная почта
        письмо(4, 2, 900, 1, "2026-10-03", от="x@shared.example"),             # общий домен
        письмо(5, 2, 900, 1, "2026-10-03", от="x@our.example"),                # наш домен
        письмо(6, 3, 21, 1, "2026-10-09", "RE: Запрос КП ротор 001"),          # контакт, в ветке
        письмо(7, 2, 900, 1, "2026-10-04", от="y@vendor-a.example"),           # домен адресата
    )]
    r = m.сопоставить_ответы([к1, к2], вх, {"our.example"})
    assert r["1"]["t"] == m._вр(t("2026-10-04")) and r["1"]["src"] == "домен" and r["1"]["n"] == 2
    assert r["1"]["thread"] is True
    assert r["2"]["src"] == "компания" and r["2"]["t"] == m._вр(t("2026-10-02"))
    assert к1["domains"] == {"vendor-a.example"}                               # общий, наш, бесплатный — сняты


def test_письмо_в_ветке_одной_карточки_идёт_только_ей():
    общие = dict(supplier="CO11", companies=["11"], contacts=[], domains=set(), ents=set(),
                 first_out=m._вр(t("2026-10-01")))
    к1 = карточка(id="1", subj={"запрос кп клапаны 001"}, **общие)
    к2 = карточка(id="2", subj={"запрос кп кольца 002"}, **общие)
    вх = [m.разбор_письма(письмо(1, 4, 11, 1, "2026-10-03", "RE: Запрос КП клапаны 001")),
          m.разбор_письма(письмо(2, 4, 11, 1, "2026-10-05", "Прайс-лист"))]
    r = m.сопоставить_ответы([к1, к2], вх, set())
    assert r["1"]["t"] == m._вр(t("2026-10-03")) and r["1"]["thread"] and r["1"]["n"] == 2
    assert r["2"]["t"] == m._вр(t("2026-10-05")) and not r["2"]["thread"] and r["2"]["n"] == 1


def test_домен_клиента_не_поставщик():
    к = карточка(id="1", supplier="CO11", companies=["11"], contacts=[], first_out=m._вр(t("2026-10-01")),
                 domains={"client-test.example", "vendor-test.example"}, subj=set(), ents=set())
    вх = [m.разбор_письма(письмо(1, 2, 900, 1, "2026-10-02", от="boss@client-test.example"))]
    assert m.сопоставить_ответы([к], вх, {"client-test.example"}) == {}
    assert к["domains"] == {"vendor-test.example"}


def test_фильтр_владельца_дел():
    f = {"OWNER_TYPE_ID": 166, "OWNER_ID": [1, 2]}
    свой = {"OWNER_TYPE_ID": "166", "OWNER_ID": "2"}
    чужой = {"OWNER_TYPE_ID": "166", "OWNER_ID": "7"}
    assert m.владельцы_свои(f, [свой, {"OWNER_TYPE_ID": "3", "OWNER_ID": "9"}])
    assert m.владельцы_свои({"OWNER_TYPE_ID": 2, "OWNER_ID": 5}, [{"OWNER_TYPE_ID": "2", "OWNER_ID": "5"}])
    # письмо с привязкой к нескольким владельцам одного типа: немного чужих — фильтр сработал
    assert m.владельцы_свои(f, [чужой])
    assert m.владельцы_свои(f, [свой] * 10 + [чужой] * 3)
    # отброшенный фильтр: чужая вся страница
    assert not m.владельцы_свои(f, [чужой] * 50)
    assert not m.владельцы_свои(f, [свой] + [чужой] * 3)
    # другой тип владельца в счёт не идёт
    assert m.владельцы_свои(f, [{"OWNER_TYPE_ID": "4", "OWNER_ID": "9"}] * 50)


def test_фильтр_компаний_и_контактов():
    по_компании = {"@COMPANY_ID": [11, 12]}
    assert m.строки_свои(по_компании, [{"ID": "1", "COMPANY_ID": "11"}, {"ID": "2", "COMPANY_ID": "99"}])
    assert not m.строки_свои(по_компании, [{"ID": str(i), "COMPANY_ID": str(i)} for i in range(1, 51)])
    assert not m.строки_свои({"@ID": [5]}, [{"ID": str(i)} for i in range(1, 51)])
    assert m.строки_свои({"@ID": [5, 6]}, [{"ID": "5"}, {"ID": "6"}])
    assert m.строки_свои({"%TITLE": "x"}, [{"ID": str(i)} for i in range(1, 51)])      # не фильтр по номерам


def test_ответ_без_письма_от_входа_в_отправлен():
    к = карточка(id="1", supplier="CO11", companies=["11"], contacts=[], first_out=None,
                 hist={"sent": m._вр(t("2026-10-01"))}, domains=set(), subj=set(), ents=set())
    вх = [m.разбор_письма(письмо(1, 4, 11, 1, "2026-09-20")), m.разбор_письма(письмо(2, 4, 11, 1, "2026-10-02"))]
    assert m.сопоставить_ответы([к], вх, set())["1"]["t"] == m._вр(t("2026-10-02"))


def test_якорь_без_создания_карточки():
    д = m._вр
    assert m.якорь({"first_out": д(t("2026-10-03")), "hist": {"sent": д(t("2026-10-01"))}}) == д(t("2026-10-03"))
    assert m.якорь({"hist": {"sent": д(t("2026-10-05")), "no_answer": д(t("2026-10-02"))}}) == д(t("2026-10-02"))
    assert m.якорь({"hist": {"no_answer": д(t("2026-10-02"))}}) == д(t("2026-10-02"))
    assert m.якорь({"hist": {"new": д(t("2026-09-01")), "dialog": д(t("2026-09-05"))},
                    "created": д(t("2026-09-01"))}) is None
    assert m.якорь({"created": д(t("2026-09-01"))}) is None


def test_неотправленная_карточка_ответа_письмом_не_получает():
    """Блокирующее замечание: письмо поставщика по другой сделке делало карточку «Новый» без
    письма отвеченной и отправленной."""
    д = m._вр
    новая = карточка(id="1", bucket="new", letters=0, hist={"new": д(t("2026-09-01"))}, supplier="CO11",
                     companies=["11"], contacts=[], domains=set(), subj=set(), ents=set())
    вх = [m.разбор_письма(письмо(1, 4, 11, 1, "2026-09-15", "Прайс на другое"))]
    assert m.сопоставить_ответы([новая], вх, set()) == {}
    st = m.card_status(новая, СЕГОДНЯ)
    assert st == {"status": "не отправлена", "sub": ""}
    с = m.aggregate([новая], [], СЕГОДНЯ)
    assert с["отправлено"] == 0 and с["статусы"] == {"не отправлена": 1}

    # две карточки одного поставщика: отправленная и «Новый»; письмо не по теме — только первой
    отправленная = карточка(id="2", bucket="sent", letters=1, first_out=д(t("2026-10-01")),
                            last_out=д(t("2026-10-01")), hist={"sent": д(t("2026-10-01"))}, supplier="CO11",
                            companies=["11"], contacts=[], domains=set(), subj={"запрос кп клапаны 002"}, ents=set())
    новая = карточка(id="3", bucket="new", letters=0, hist={"new": д(t("2026-09-20"))}, supplier="CO11",
                     companies=["11"], contacts=[], domains=set(), subj=set(), ents=set())
    вх = [m.разбор_письма(письмо(2, 4, 11, 1, "2026-10-05", "Прайс-лист"))]
    ответы = m.сопоставить_ответы([отправленная, новая], вх, set())
    assert set(ответы) == {"2"}
    for к in (отправленная, новая):
        к["reply"] = (ответы.get(к["id"]) or {}).get("t")
    с = m.aggregate([отправленная, новая], [], СЕГОДНЯ)
    assert с["отправлено"] == 1 and с["статусы"] == {"ответ без КП": 1, "не отправлена": 1}


def test_общий_домен_карточка_без_поставщика_не_делит():
    д = m._вр
    с_поставщиком = карточка(id="1", supplier="CO11", companies=["11"], contacts=[], first_out=д(t("2026-10-01")),
                             domains={"vendor-test.example"}, subj=set(), ents=set())
    без = карточка(id="2", supplier="", companies=[], contacts=[], first_out=д(t("2026-10-01")),
                   domains={"vendor-test.example"}, subj=set(), ents=set())
    assert m.общие([с_поставщиком, без], "domains") == set()
    вх = [m.разбор_письма(письмо(1, 2, 900, 1, "2026-10-03", от="x@vendor-test.example"))]
    assert set(m.сопоставить_ответы([с_поставщиком, без], вх, set())) == {"1", "2"}
    другой = карточка(id="3", supplier="CO12", domains={"vendor-test.example"})
    assert m.общие([с_поставщиком, без, другой], "domains") == {"vendor-test.example"}


def test_получатель_из_запасного_поля():
    a = письмо(1, 166, 9, 2, "2026-10-01", "Запрос")
    a["SETTINGS"] = {"EMAIL_META": {"from": "s@our-test.example", "__to": "q@vendor-test.example"}}
    assert m.разбор_письма(a)["to"] == {"vendor-test.example"}


# ── свод ────────────────────────────────────────────────────────────────────────────────────

def test_свод_по_брендам_видам_и_сорсерам():
    д = m._вр
    карточки = [
        карточка(id="1", brand="Howden", stype="производитель", supplier="CO1", owner="u1", offer=True,
                 bucket="paw", letters=1, first_out=д(t("2026-10-01")), created=д(t("2026-09-30")),
                 hist={"paw": д(t("2026-10-08"))}),
        карточка(id="2", brand="Howden", stype="дилер/трейдер", supplier="CO2", owner="u1", bucket="sent",
                 letters=1, first_out=д(t("2026-09-01")), last_out=д(t("2026-09-01")), created=д(t("2026-09-01"))),
        карточка(id="3", brand="SIAD", stype="дилер/трейдер", supplier="CO2", owner="u2", bucket="sent",
                 letters=2, first_out=д(t("2026-10-20")), last_out=д(t("2026-10-26")), created=д(t("2026-10-19"))),
        карточка(id="4", brand=m.НЕТ_БРЕНДА, stype="поставщик не указан", supplier="", owner="", bucket="new"),
    ]
    с = m.aggregate(карточки, ["Howden", "Borsig", m.НЕТ_БРЕНДА], СЕГОДНЯ, д(t("2026-08-28")))
    assert с["статусы"] == {"КП": 1, "молчание": 2, "не отправлена": 1}
    assert с["отправлено"] == 3 and с["письмом"] == 3 and с["молчат_с_дожимом"] == 1 and с["без_дожима_долго"] == 1
    hw = с["по_бренду"]["Howden"]
    assert (hw["строк"], hw["карточек"], len(hw["поставщики"]), len(hw["производители"]), hw["ответ"],
            hw["КП Offer"], hw["молчат"], hw["молчат > 10 р.д."]) == (1, 2, 2, 1, 1, 1, 1, 1)
    assert с["покрытие"] == {"строки без карточек": ["Borsig"], "нет запроса производителю": ["SIAD"],
                             "нет ни одного ответа": ["SIAD"]}
    assert с["дубли"] == (1, 2)
    assert с["по_виду"]["дилер/трейдер"]["карточек"] == 2
    assert с["сроки"]["сделка создана → первая карточка"][:2] == (2, 1)
    assert с["сроки"]["первое письмо → КП"][:2] == (5, 1)
    assert с["сорсеры"][0] == ("сорсер#1", {"карточек": 2, "отправлено": 2, "молчат": 1, "КП": 1})
    assert с["сорсеры"][-1][0] == "не определён" and с["сорсеров"] == 2


def test_части_и_разбиение(monkeypatch):
    monkeypatch.setenv("PROBE_PARTS", "3")
    assert m.частей()[0] == 10
    monkeypatch.setenv("PROBE_PARTS", "25")
    assert m.частей() == (25, "")
    assert m.разбить(["5", "1", "3"], 10) == [["1"], ["3"], ["5"]]
    # ровно min(n, карточек) почти равных частей: 23 → 10 частей, 11 → 10, 41 → 10
    assert [len(x) for x in m.разбить([str(i) for i in range(1, 24)], 10)] == [3] * 3 + [2] * 7
    for n_карточек in (10, 11, 19, 41, 100):
        части = m.разбить([str(i) for i in range(1, n_карточек + 1)], 10)
        assert len(части) == 10 and max(map(len, части)) - min(map(len, части)) <= 1
        assert [x for ч in части for x in ч] == [str(i) for i in range(1, n_карточек + 1)]
    assert len(m.разбить([str(i) for i in range(1, 60)], 50)) == 50
    assert m.разбить([], 10) == []


def test_запасной_шаг_в_пределе():
    assert m.запасной_помещается(4_000, 10_000, 10, 300) == ""
    assert "больше предела запасного шага" in m.запасной_помещается(12_000, 10_000, 10, 300)
    # 10 тыс. сделок — 200 страниц: с запасом на разбор в 300 не помещается после 50 запросов
    assert "не помещаются" in m.запасной_помещается(10_000, 10_000, 50, 300)
    assert "не помещаются" in m.запасной_помещается(20_000, 50_000, 0, 300)   # PROBE_LIMIT не обходит предел


def test_выбор_бренд_раньше_числа_карточек():
    старая = {"ID": "8810001", "DATE_CREATE": t("2026-01-01")}
    новая = {"ID": "8810002", "DATE_CREATE": t("2026-06-01")}
    с_брендом = [{"title": "Запрос Howden"}, {"title": "Запрос", "ufCrm18Supplier": ["CO_8820013"]}]
    без_бренда = [{"title": f"Запрос {i}"} for i in range(12)]
    названия = {"8820013": "BHGE Учебный филиал"}                   # производитель — тоже компрессор
    ключ = lambda д, кк: m.ключ_выбора(д, кк, названия, set())  # noqa: E731
    assert ключ(старая, с_брендом)[0] == 2 and ключ(новая, без_бренда)[0] == 0
    assert max([старая, новая], key=lambda д: ключ(д, с_брендом if д is старая else без_бренда)) is старая
    # поровну — новейшая
    assert max([старая, новая], key=lambda д: ключ(д, без_бренда)) is новая


# ── 6. придуманный портал ───────────────────────────────────────────────────────────────────

КЛИЕНТ = "8820001"
КОМПАНИИ = {
    КЛИЕНТ: {"TITLE": "АЛЬФАТЕСТ-Учебный округ", "EMAIL": [{"VALUE": "office@alfatest-client.example"}]},
    "8820002": {"TITLE": "АльфаТест Сервис", "EMAIL": []},
    "8820003": {"TITLE": "Альфатестовый магазин", "EMAIL": []},                 # не холдинг (\b)
    "8820011": {"TITLE": "Howden Учебный завод", "EMAIL": [{"VALUE": "q@howden-test.example"}]},
    "8820012": {"TITLE": "ООО Ромашка-Компрессор", "EMAIL": [{"VALUE": "sales@romashka-test.example"}]},
    "8820013": {"TITLE": "BHGE Учебный филиал", "EMAIL": []},
    "8820014": {"TITLE": "Василёк Трейд", "EMAIL": [{"VALUE": "info@vasilek-test.example"}]},
}
КОНТАКТЫ = {"8830001": {"COMPANY_ID": "8820013", "EMAIL": [{"VALUE": "np@bhge-test.example"}]},
            "8830002": {"COMPANY_ID": "8820012", "EMAIL": [{"VALUE": "mgr@romashka-test.example"}]}}
СДЕЛКИ = [
    {"ID": "8810001", "TITLE": "ТЕСТ-2030 ЗИП компрессоры", "COMPANY_ID": КЛИЕНТ, "DATE_CREATE": t("2026-03-02"),
     "CATEGORY_ID": "4", "STAGE_ID": "C4:NEW", "STAGE_SEMANTIC_ID": "P", "ASSIGNED_BY_ID": "99001"},
    {"ID": "8810002", "TITLE": "ТЕСТ-2029 ремонт", "COMPANY_ID": "8820002", "DATE_CREATE": t("2025-05-01"),
     "CATEGORY_ID": "4", "STAGE_ID": "C4:NEW", "STAGE_SEMANTIC_ID": "P", "ASSIGNED_BY_ID": "99001"},
    {"ID": "8810003", "TITLE": "Старая ТЕСТ-2030 ЗИП", "COMPANY_ID": КЛИЕНТ, "DATE_CREATE": t("2024-12-01"),
     "CATEGORY_ID": "4", "STAGE_ID": "C4:LOSE", "STAGE_SEMANTIC_ID": "F", "ASSIGNED_BY_ID": "99001"},
    {"ID": "8810004", "TITLE": "Ромашка прочее", "COMPANY_ID": "8820012", "DATE_CREATE": t("2026-01-01"),
     "CATEGORY_ID": "2", "STAGE_ID": "C2:NEW", "STAGE_SEMANTIC_ID": "P", "ASSIGNED_BY_ID": "99001"},
]


def _к(i, title, stage, **к):
    d = {"id": int(i), "title": title, "stageId": f"DT166_24:{stage}", "categoryId": 24, "parentId2": 8810001,
         "createdTime": t("2026-09-01"), "assignedById": 99002, "companyId": 0}
    d.update(к)
    return d


КАРТОЧКИ = [
    _к(8840001, "Запрос Howden Ромашка", "UC_H49RUE", ufCrm18Supplier=["CO_8820011"], **{OFFER: [{"id": 1}]}),
    _к(8840002, "Запрос SIAD", "PREPARATION", ufCrm18Supplier=["CO_8820012"]),
    _к(8840003, "Запрос Nuovo Pignone", "PREPARATION", ufCrm18SupplContact=["C_8830001"], assignedById=99003),
    _к(8840004, "Запрос клапаны seat", "2", ufCrm18Supplier=["CO_8820012"]),
    _к(8840005, "Запрос Borsig", "NEW"),
    _к(8840006, "Запрос Howden 2", "3", companyId=int(КЛИЕНТ), assignedById=234, movedBy=99003),
    _к(8840007, "Запрос Atlas Copco Василёк", "UC_61BSRU", ufCrm18Supplier=["CO_8820014"]),
    _к(8840008, "Запрос Howden 3", "SUCCESS", ufCrm18Supplier=["CO_8820011"], **{АРХИВ: [{"id": 2}]}),
]
ИСТОРИЯ = {
    "8840001": [("NEW", "2026-09-09"), ("PREPARATION", "2026-09-10"), ("UC_H49RUE", "2026-09-25")],
    "8840002": [("NEW", "2026-08-19"), ("PREPARATION", "2026-08-20")],
    "8840003": [("NEW", "2026-10-19"), ("PREPARATION", "2026-10-20")],
    "8840004": [("NEW", "2026-09-30"), ("PREPARATION", "2026-10-01"), ("2", "2026-10-10")],
    "8840005": [("NEW", "2026-10-27")],
    "8840006": [("NEW", "2026-09-30"), ("PREPARATION", "2026-10-01"), ("3", "2026-10-20")],
    "8840007": [("NEW", "2026-10-04"), ("PREPARATION", "2026-10-05"), ("UC_61BSRU", "2026-10-08")],
    "8840008": [("NEW", "2026-09-01"), ("PREPARATION", "2026-09-02"), ("UC_H49RUE", "2026-09-15"),
                ("SUCCESS", "2026-09-20")],
}
НАШ = "Сорсер Учебный <s@kvant-test.example>"
ПИСЬМА = [
    письмо(8850001, 166, 8840002, 2, "2026-08-20", "Запрос КП Ромашка-SIAD 002", НАШ, "sales@romashka-test.example"),
    письмо(8850002, 166, 8840002, 2, "2026-09-01", "Запрос КП Ромашка-SIAD 002", НАШ, "sales@romashka-test.example"),
    письмо(8850003, 166, 8840003, 2, "2026-10-20", "Запрос КП ротор 003", НАШ, "np@bhge-test.example",
           [{"VALUE": "np@bhge-test.example", "ENTITY_TYPE_ID": "3", "ENTITY_ID": "8830001"}]),
    письмо(8850004, 166, 8840007, 2, "2026-10-05", "Запрос КП Василёк 007", НАШ, "info@vasilek-test.example"),
    письмо(8850005, 166, 8840004, 2, "2026-10-01", "Запрос КП 004", НАШ, "sales@romashka-test.example"),
    письмо(8850006, 166, 8840001, 2, "2026-09-10", "Запрос КП Howden 001", НАШ, "q@howden-test.example"),
    письмо(8860001, 3, 8830001, 1, "2026-10-22", "RE: Запрос КП ротор 003", "np@bhge-test.example"),
    письмо(8860002, 4, 8820012, 1, "2026-08-10", "Прайс Ромашка", "sales@romashka-test.example"),
    письмо(8860003, 2, 8810001, 1, "2026-10-07", "Ответ Василёк", "info@vasilek-test.example"),
    письмо(8860004, 2, 8810001, 1, "2026-10-07", "Вопрос заказчика", "x@gmail.com"),
]
СТРОКИ = [{"PRODUCT_NAME": n} for n in ("Клапан Howden 9876543", "Кольцо SIAD", "Ротор Нуово Пиньоне",
                                        "Поршень Borsig", "Прокладка Ariel")]
ЗАПРЕТЫ = ("Ромашка", "Василёк", "Учебн", "АЛЬФАТЕСТ", "АльфаТест", "Альфатест", "ТЕСТ", "ЗИП", "2030", "BHGE",
           "@", "example", "gmail", "9876543", "9900", "seat")


def _ок(rec: dict, f: dict) -> bool:
    for k, v in f.items():
        op, поле = re.match(r"^(>=|<=|>|<|@|%|!)?(.+)$", k).groups()
        x = rec.get(поле)
        if op == "%":
            if str(v).lower() not in str(x or "").lower():
                return False
        elif op == "@":
            if str(x) not in {str(i) for i in v}:
                return False
        elif op == ">":
            if int(x or 0) <= int(v):
                return False
        elif op == ">=":
            if str(x or "") < str(v):
                return False
        elif isinstance(v, list):
            if str(x) not in {str(i) for i in v}:
                return False
        elif str(x) != str(v):
            return False
    return True


class Портал(m.bc.BitrixClient):
    """Придуманный портал: ровно те методы, что зовёт зонд; list_* — настоящие методы клиента."""

    def __init__(self, сделки=None, карточки=None, падать_на=(), лимит_на=None):
        super().__init__("https://portal.invalid/rest/1/x/", min_interval=0)
        self.сделки = СДЕЛКИ if сделки is None else сделки
        self.карточки = КАРТОЧКИ if карточки is None else карточки
        self.падать_на = {str(x) for x in падать_на}
        self.вызовы: list[tuple[str, dict]] = []

    def call_envelope(self, method, params=None, *, retries=None):
        params = params or {}
        f = dict(params.get("filter") or {})
        self.вызовы.append((method, f))
        m.bc._учесть("запросов")

        def страница(rows, ключ="ID"):
            rows = sorted((r for r in rows if _ок(r, f)), key=lambda r: int(r[ключ]))
            if params.get("start") == -1:
                return {"result": rows[:50]}
            s = int(params.get("start") or 0)
            env = {"result": rows[s:s + 50], "total": len(rows)}
            if s + 50 < len(rows):
                env["next"] = s + 50
            return env

        if method == "crm.company.list":
            return страница([{"ID": i, **v} for i, v in КОМПАНИИ.items()])
        if method == "crm.contact.list":
            return страница([{"ID": i, **v} for i, v in КОНТАКТЫ.items()])
        if method == "crm.deal.list":
            return страница([dict(d) for d in self.сделки])
        if method == "crm.item.list":
            rows = [dict(к, parentId2=к["parentId2"]) for к in self.карточки]
            rows = sorted((r for r in rows if _ок(r, f)), key=lambda r: r["id"])
            return {"result": {"items": rows[:50]}}
        if method == "crm.deal.productrows.get":
            return {"result": [dict(r) for r in СТРОКИ] if str(params.get("id")) == "8810001" else []}
        if method == "crm.activity.list":
            return страница(ПИСЬМА)
        if method == "crm.stagehistory.list":
            свои = {str(x) for x in f["OWNER_ID"]}
            if свои & self.падать_на:
                raise ValueError("карточка 8840004 Ромашка 9876543 — текст не должен попасть в журнал")
            items = [{"ID": str(i), "OWNER_ID": o, "STAGE_ID": f"DT166_24:{s}", "CREATED_TIME": t(когда)}
                     for o, rows in ИСТОРИЯ.items() if o in свои for i, (s, когда) in enumerate(rows, 1)]
            return {"result": {"items": items}, "total": len(items)}
        raise AssertionError(f"неожиданный метод {method}")


@pytest.fixture
def учебный(monkeypatch):
    monkeypatch.setattr(m, "ХОЛДИНГ_НАПИСАНИЯ", ("АЛЬФАТЕСТ",))
    monkeypatch.setattr(m, "ХОЛДИНГ_RX", re.compile(r"альфатест\b", re.I))
    monkeypatch.setenv("PROBE_QUERY", h("тест", "2030", "зип"))
    monkeypatch.setenv("PROBE_PARTS", "10")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)


def _журнал_чист(out: str) -> None:
    # строки ::add-mask:: раннер не печатает (и в них — только хеши запроса); остальное — журнал
    маски = [x for x in out.splitlines() if x.startswith("::add-mask::")]
    assert all(re.fullmatch(r"::add-mask::[0-9a-f]{64}", x) for x in маски)
    журнал = "\n".join(x for x in out.splitlines() if not x.startswith("::add-mask::"))
    for запрет in ЗАПРЕТЫ:
        assert запрет not in журнал, запрет
    assert not re.search(r"88\d{5}", журнал), "номер записи в журнале"
    assert not re.search(r"[0-9a-f]{64}", журнал), "хеш запроса в журнале"


def test_прогон_одной_сделки(учебный, capsys):
    п = Портал()
    assert m.main(п, СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    строки = out.strip().splitlines()
    assert строки[-1].startswith("портал: запросов")
    assert "компаний по 1 написаниям прочитано 3, из них холдинга 2" in out
    assert "сделок холдинга с 2025-01-01 прочитано 2, совпало 1" in out
    assert "разбирается ровно одна сделка" in out
    assert "СДЕЛКА: создана 2026-03 · воронка 4 · семантика P · карточек СП-166 8 · товарных строк 5" in out
    assert "холдинг" not in out.split("СДЕЛКА:")[1].split("\n")[0] and "дочк" not in out
    assert out.splitlines()[0].startswith("::add-mask::") and out.count("::add-mask::") == 3
    assert "частей прочитано 8 из 8, не прочитано 0" in out
    assert "СТАТУСЫ (8 карточек): КП 2 · отказ 1 · ответ без КП 2 · молчание 2 · не отправлена 1" in out
    assert "КП: выбран 1 · без итога 1; основание: файл Offer from supplier 1 · файл только в архивном поле 1" in out
    assert "закрыто «Ответ не получен» 1; без письма в CRM 1" in out
    assert "  Howden | 1 | 3 | 1 | 1 | 3 | 2 | 2 | 1 | 2 | 0 | 1 | 1" in out
    assert "первое письмо → первый ответ: 4 · n 4" in out and "первое письмо → КП: 11 · n 1" in out
    assert "  Ariel | 1 | 0 |" in out
    assert "строки без карточек: Ariel, Borsig, Nuovo Pignone, SIAD" not in out          # у них есть карточки
    assert "  строки без карточек: Ariel" in out
    assert "нет запроса производителю: Atlas Copco, Borsig, SIAD" in out
    assert "поставщиков с 2 и более карточками в сделке: 2 (карточек у них 4)" in out
    assert "companyId карточки — компания клиента у 1 карточек" in out
    assert "засчитанных ответов письмом 2 (по источнику: контакт 1 · домен 1), из них в ветке нашего письма 1" in out
    assert "ВЕРДИКТ: запросов 8; отправлено 7 (письмом в CRM 5); с ответом 5 — КП 2" in out
    assert "ПРЕДВАРИТЕЛЬНЫЙ" not in out
    # фильтры: каждый список — по номерам или по названию, полного обхода нет
    for method, f in п.вызовы:
        if method in ("crm.activity.list", "crm.contact.list", "crm.stagehistory.list"):
            assert any(k in f for k in ("OWNER_ID", "@ID", "@COMPANY_ID")), (method, f)
        if method == "crm.company.list":
            assert "%TITLE" in f or "@ID" in f
        if method == "crm.deal.list":
            assert "@COMPANY_ID" in f
    входящие = [f for meth, f in п.вызовы if meth == "crm.activity.list" and f.get("DIRECTION") == 1]
    assert входящие and all(f[">=CREATED"] and f["PROVIDER_ID"] == "CRM_EMAIL" for f in входящие)
    assert {f["OWNER_TYPE_ID"] for f in входящие} == {m.КОНТАКТ, m.КОМПАНИЯ, m.СДЕЛКА}


def test_новая_карточка_активного_поставщика_не_отправлена(учебный, capsys):
    """Блокирующее замечание на придуманном портале: у поставщика 8820014 есть отправленная
    карточка с ответом; его же карточка «Новый» без письма и без истории «отправлен» остаётся
    «не отправлена», а «отправлено» не растёт; её часть входящих не читает."""
    новая = _к(8840009, "Запрос Atlas Copco 2", "NEW", ufCrm18Supplier=["CO_8820014"])
    п = Портал(карточки=КАРТОЧКИ + [новая])
    assert m.main(п, СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    assert "СТАТУСЫ (9 карточек): КП 2 · отказ 1 · ответ без КП 2 · молчание 2 · не отправлена 2" in out
    assert "ВЕРДИКТ: запросов 9; отправлено 7 (письмом в CRM 5); с ответом 5" in out
    входящие = [f for meth, f in п.вызовы if meth == "crm.activity.list" and f.get("DIRECTION") == 1
                and f.get("OWNER_TYPE_ID") == m.КОМПАНИЯ]
    # 9 карточек — 9 частей: входящие поставщика читает только часть отправленной карточки
    # (якорь 05.10 минус день), часть новой — нет (прежде — от создания карточки)
    его = [f for f in входящие if 8820014 in f["OWNER_ID"]]
    assert len(его) == 1 and его[0][">=CREATED"] == "2026-10-04T00:00:00"


def test_упавшая_часть_предварительно(учебный, capsys):
    assert m.main(Портал(падать_на=["8840004"]), СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    assert "часть 4 из 8: сбой ValueError" in out
    assert "частей прочитано 7 из 8, не прочитано 1; карточек в выводах 7, вне выводов 1" in out
    assert "ВЕРДИКТ (ПРЕДВАРИТЕЛЬНЫЙ: частей не прочитано 1 из 8, карточек вне выводов 1)" in out


def test_фильтр_дел_не_сработал_часть_падает(учебный, capsys):
    class Глухой(Портал):
        def call_envelope(self, method, params=None, *, retries=None):
            if method == "crm.activity.list" and (params or {}).get("filter", {}).get("OWNER_TYPE_ID") == 166:
                m.bc._учесть("запросов")
                f = (params or {}).get("filter") or {}
                self.вызовы.append((method, dict(f)))
                return {"result": [{"ID": str(8851000 + i + int(f.get(">ID") or 0)), "OWNER_TYPE_ID": "166",
                                    "OWNER_ID": "8849999", "DIRECTION": "2", "CREATED": t("2026-10-01")}
                                   for i in range(50)]}
            return super().call_envelope(method, params, retries=retries)
    п = Глухой()
    assert m.main(п, СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    assert out.count("фильтр OWNER_ID массивом не сработал") == 8
    assert "частей прочитано 0 из 8, не прочитано 8" in out and "ПРЕДВАРИТЕЛЬНЫЙ" in out
    # с первой же страницы: по одному запросу писем на часть
    assert sum(1 for meth, f in п.вызовы if meth == "crm.activity.list" and f.get("OWNER_TYPE_ID") == 166) == 8


def test_фильтр_контактов_не_сработал_с_первой_страницы(учебный, capsys):
    """Портал отбросил @COMPANY_ID: прежде часть сжигала 40 страниц, теперь — одну."""
    class Глухой(Портал):
        def call_envelope(self, method, params=None, *, retries=None):
            f = (params or {}).get("filter") or {}
            if method == "crm.contact.list" and "@COMPANY_ID" in f:
                self.вызовы.append((method, dict(f)))
                m.bc._учесть("запросов")
                return {"result": [{"ID": str(8839000 + i + int(f.get(">ID") or 0)), "COMPANY_ID": "8829999",
                                    "EMAIL": []} for i in range(50)]}
            return super().call_envelope(method, params, retries=retries)
    п = Глухой()
    assert m.main(п, СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    assert "crm.contact.list: фильтр по номерам не сработал" in out and "ПРЕДВАРИТЕЛЬНЫЙ" in out
    по_компании = [f for meth, f in п.вызовы if meth == "crm.contact.list" and "@COMPANY_ID" in f]
    упало = out.count("crm.contact.list: фильтр по номерам не сработал")
    assert упало >= 1 and len(по_компании) == упало


def test_вход_не_того_вида(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("PROBE_QUERY", "зип,2030")
    assert m.main(Портал(), СЕГОДНЯ) == 2
    out = capsys.readouterr().out
    assert "зип" not in out and "2030" not in out and "PROBE_QUERY" in out and "add-mask" not in out


def test_вход_из_события_прогона(учебный, monkeypatch, tmp_path, capsys):
    """В Actions env PROBE_QUERY нет: вход — из файла события, хеши маскируются."""
    monkeypatch.delenv("PROBE_QUERY")
    q = h("тест", "2030", "зип")
    событие = tmp_path / "event.json"
    событие.write_text('{"inputs": {"query": "%s"}}' % q, encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(событие))
    assert m.main(Портал(), СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    assert [x for x in out.splitlines() if x.startswith("::add-mask::")] == [f"::add-mask::{x}" for x in q.split(",")]
    assert "разбирается ровно одна сделка" in out
    # текст названия, введённый по ошибке вместо хешей: выход 2 без эха
    событие.write_text('{"inputs": {"query": "ТЕСТ-2030 ЗИП"}}', encoding="utf-8")
    assert m.main(Портал(), СЕГОДНЯ) == 2
    out = capsys.readouterr().out
    assert "ТЕСТ" not in out and "ЗИП" not in out and "2030" not in out


def test_строки_не_прочитаны_вердикт_предварительный(учебный, capsys):
    class БезСтрок(Портал):
        def call_envelope(self, method, params=None, *, retries=None):
            if method == "crm.deal.productrows.get":
                raise m.bc.BitrixError("crm.deal.productrows.get: ACCESS_DENIED Учебный текст")
            return super().call_envelope(method, params, retries=retries)
    assert m.main(БезСтрок(), СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    assert "товарных строк не прочитаны" in out
    assert "ВЕРДИКТ (ПРЕДВАРИТЕЛЬНЫЙ: товарные строки не прочитаны" in out


def test_входящие_сделки_не_прочитаны_вердикт_предварительный(учебный, capsys):
    class Сбой(Портал):
        def call_envelope(self, method, params=None, *, retries=None):
            if method == "crm.activity.list" and (params or {}).get("filter", {}).get("OWNER_TYPE_ID") == m.СДЕЛКА:
                raise m.bc.BitrixError("crm.activity.list: QUERY_LIMIT_EXCEEDED")
            return super().call_envelope(method, params, retries=retries)
    assert m.main(Сбой(), СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    assert "ВЕРДИКТ (ПРЕДВАРИТЕЛЬНЫЙ: входящие письма сделки не прочитаны" in out


def test_выбрать_без_кандидатов_не_читает_карточки(учебный, capsys):
    п = Портал()
    прогон = m.Прогон(п, СЕГОДНЯ, m.разобрать_запрос(h("тест")))
    assert прогон.выбрать([], 2) == 1
    assert not any(meth == "crm.item.list" for meth, _ in п.вызовы)
    assert "разбирать нечего" in capsys.readouterr().out


def test_не_совпало_ничего_запасной_шаг(учебный, monkeypatch, capsys):
    monkeypatch.setenv("PROBE_QUERY", h("тест", "2031"))
    п = Портал()
    assert m.main(п, СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    assert "ШАГ 1, по токенам: токен 1 — сделок 2 · токен 2 — сделок 0" in out
    assert "ШАГ 2: сделок с 2025-01-01 прочитано 3 из 3, совпало 0" in out
    assert "ВЕРДИКТ: сделка не найдена" in out
    assert any(meth == "crm.deal.list" and set(f) <= {">=DATE_CREATE", ">ID"} for meth, f in п.вызовы)


def test_запасной_шаг_находит_сделку_вне_холдинга(учебный, monkeypatch, capsys):
    monkeypatch.setenv("PROBE_QUERY", h("ромашка", "прочее"))
    assert m.main(Портал(), СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    assert "ШАГ 2: сделок с 2025-01-01 прочитано 3 из 3, совпало 1" in out
    assert "семантика P · карточек СП-166 0 ·" in out


def test_запасной_шаг_больше_предела(учебный, monkeypatch, capsys):
    monkeypatch.setenv("PROBE_QUERY", h("нет-такого"))
    monkeypatch.setenv("PROBE_LIMIT", "2")
    assert m.main(Портал(), СЕГОДНЯ) == 0
    assert "больше предела запасного шага 2; не читается" in capsys.readouterr().out


def test_выбор_из_нескольких_по_бренду(учебный, monkeypatch, capsys):
    сделки = СДЕЛКИ + [{**СДЕЛКИ[0], "ID": "8810009", "DATE_CREATE": t("2026-06-01")}]
    карточки = КАРТОЧКИ + [_к(8840101, "Запрос Ромашка без бренда", "NEW", parentId2=8810009),
                           _к(8840102, "Запрос прочее", "NEW", parentId2=8810009)]
    assert m.main(Портал(сделки=сделки, карточки=карточки), СЕГОДНЯ) == 0
    out = capsys.readouterr().out
    _журнал_чист(out)
    assert "ВЫБОР: совпало сделок 2 (шаг 1), для выбора прочитаны карточки 2 новейших" in out
    assert "карточек СП-166 8 ·" in out                    # у новейшей карточек меньше и без бренда


def test_первый_шаг_упал(учебный, capsys):
    class Сломан(Портал):
        def call_envelope(self, method, params=None, *, retries=None):
            raise m.bc.BitrixError("crm.company.list: ACCESS_DENIED Доступ запрещён Учебному")
    assert m.main(Сломан(), СЕГОДНЯ) == 1
    out = capsys.readouterr().out
    assert "ACCESS_DENIED" in out and "Учебн" not in out


# ── 7. probe.yml ────────────────────────────────────────────────────────────────────────────

def test_зонд_в_probe_yml():
    wf = yaml.safe_load((ROOT / ".github/workflows/probe.yml").read_text(encoding="utf-8"))
    вход = (wf.get("on") or wf[True])["workflow_dispatch"]["inputs"]
    assert "probe_deal_rfq.py" in вход["script"]["options"]
    assert вход["query"]["default"] == "" and "sha256" in вход["query"]["description"]
    шаг = next(s for s in wf["jobs"]["probe"]["steps"] if s.get("name") == "Зонд")
    # env шага GitHub печатает в заголовке «Run …» публичного журнала: входа query там быть не должно
    assert "PROBE_QUERY" not in шаг["env"]
    assert not any("inputs.query" in str(v) for v in шаг["env"].values())
    assert "inputs.query" not in str(шаг.get("run") or "")
    assert шаг["env"]["PROBE_PARTS"] and шаг["env"]["BITRIX_RPS"] == "1.0"
    assert wf["concurrency"]["group"] == "bitrix-portal"
