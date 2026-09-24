"""Сборка снимка брендов (library/brands.py) на придуманном корпусе.

Проверяется поведение, ради которого снимок заводился (этап 8.1 и распоряжение
владельца 24.09.2026), а не «не упало»:
  · бренд сводится к ключу словаря dict/oem.json; написание без ключа не
    пропадает, а считается числом «имя без ключа»;
  · ячейка «SKF/FAG» каталога — два бренда, а не один «skffag»;
  · у узлов видна доля «не определено» числом;
  · у каждого поля карточки есть источник, и та же таблица — у счётчика;
  · имя поставщика берётся из Битрикса по номеру компании, иначе — пометка, а
    не выдуманное имя;
  · код лежит ровно в одной корзине, и указатель знает, в какой;
  · в снимке нет полей контактов и денег, которые воркер режет правом;
  · размер каждого ключа — с запасом до предела 8 МиБ на объёме синтетики
    живой базы.

Корпус придуман (CLAUDE.md, правило 18): имена брендов и компаний сочинены.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from library import brands, codes_sql

ROOT = Path(__file__).resolve().parents[1]
ПРЕДЕЛ = 8 * 1024 * 1024

СЛОВАРЬ = {"records": [
    {"oem_key": "skf", "name": "SKF", "spellings": [
        {"spelling": "SKF", "where": "выдумка"}, {"spelling": "Skf Gmbh", "where": "выдумка"},
        {"spelling": "Ab Skf", "where": "выдумка"}]},
    {"oem_key": "fag", "name": "FAG", "spellings": [{"spelling": "FAG", "where": "выдумка"}]},
    # Спорное написание: «Двойня» ведёт в два ключа — в карту не идёт.
    {"oem_key": "двойня1", "name": "Двойня Один", "spellings": [{"spelling": "Двойня", "where": "в"}]},
    {"oem_key": "двойня2", "name": "Двойня Два", "spellings": [{"spelling": "Двойня", "where": "в"}]},
]}
ЦЕПОЧКА = {"records": [
    {"from": "SKF", "to": "Выдуманный литейщик", "kind": "maker", "scope": ["кольца"],
     "proof": ["П0 — выдумка"], "n": 3},
    {"from": "Прочие", "to": "Кто-то", "kind": "maker", "from_is_bucket": True, "n": 9},
]}
АТЛАС = {"makers": [{"name": "SKF Gmbh", "country": "Швеция", "owner": "выдуманный владелец",
                     "pn_system": "шесть цифр", "former_names": "нет"}]}
КАНАЛЫ = {"brands": [{"brand": "FAG", "state": "только запрос", "channel": "выдуманный канал"}]}

КОДЫ = {
    "brands": [
        {"brand_key": "skf", "brand": "SKF", "spellings": 2, "codes_any": 3, "codes_plausible": 3,
         "codes_customer": 2, "codes_kp_file": 1, "codes_catalog": 1, "codes_our_docs": 0,
         "codes_side_unknown": 1, "codes_customer_kp_price": 1, "codes_customer_buy_price": 1,
         "rows_customer": 4, "rows_kp_price": 1, "deals_customer": 2, "portal_companies_naming": 1},
        {"brand_key": "акмеро", "brand": "Акмеро", "spellings": 1, "codes_any": 1,
         "codes_customer": 1},
    ],
    "suppliers": [
        {"supplier": "KV-S-000001-1", "name": "alfaclaw", "name_from": "реестр",
         "portal_keys": "101 102", "codes": 2, "price_rows": 3, "cards": 2, "files": 2,
         "currencies": "USD", "brands_file": 1, "domains": "alfa.example"},
        {"supplier": "bitrix:777", "name": "(имени в базе нет)", "name_from": "нет в реестре",
         "portal_keys": "777", "codes": 1, "price_rows": 1},
    ],
    "totals": [
        {"section": "проверка", "metric": "lower", "value": 1},
        {"section": "всё", "metric": brands.ПЛИТКИ[0][2], "value": 10},
        {"section": "цены КП", "metric": "кодов с ценой КП", "value": 3},
    ],
    "card_brands": [{"brand_id": "501", "codes": 2, "price_rows": 3, "suppliers": 1, "cards": 2}],
    "match": [
        {"код_ключ": "ab6205", "код_как_написан": "AB-6205", "наименование": "Подшипник",
         "бренд_из_строки": "SKF", "бренд_из_строки_ключ": "skf", "бренд_с_карточки": "501",
         "бренд_из_спроса": "FAG; SKF", "бренд_из_спроса_ключ": "fag skf",
         "поставщик_ключ": "KV-S-000001-1", "ключи_портала": "101 102", "валюта": "USD",
         "единица": "шт", "ед_изм_как_написано": "pcs", "цена_мин": 10, "цена_макс": 12,
         "цена_медиана": 11, "строк_цены": 2, "строк_отсеяно": 1, "спрошен_нами": True,
         "сделок_спроса": 2, "поставщиков_с_ценой_по_коду": 2, "количество_в_КП": 2,
         "дата_первая": "2026-09-01", "дата_последняя": "2026-09-05",
         "дата_откуда": "запись разбора", "карточек_запроса": 2, "файлов_кп": 2},
        {"код_ключ": "ab6205", "код_как_написан": "AB-6205", "поставщик_ключ": "bitrix:777",
         "поставщик": "ключ 777 (имени в базе нет)", "ключи_портала": "777",
         "валюта": "(не названа)", "единица": "шт", "цена_мин": 9, "цена_макс": 9,
         "цена_медиана": 9, "строк_цены": 1, "спрошен_нами": True},
        {"код_ключ": "kl7", "код_как_написан": "KL-7", "поставщик_ключ": "(не указан)",
         "поставщик": "(поставщик на карточке не указан)", "валюта": "RUB", "единица": "шт",
         "цена_мин": 1400, "цена_макс": 1600, "цена_медиана": 1500, "строк_цены": 2},
    ],
    "pairs": [
        {"row_kind": "пара", "brand_key": "skf", "brand": "SKF", "supplier": "KV-S-000001-1",
         "codes": 1, "codes_own_row": 1, "codes_by_customer": 1, "codes_priced": 1,
         "price_rows": 2, "codes_by_currency": "USD 1", "brand_codes_priced": 1,
         "brand_codes_asked": 2, "asked_closed": 1},
    ],
}

КАТАЛОГ = {
    "models": [("SKF", "м1", "Выдуманная машина", "семья", None, "насосная станция", 2)],
    # «SKF/FAG» — ячейка двух брендов: деталей по три у каждого.
    "parts": [("SKF/FAG", 3, 1, 2), ("Ab Skf", 2, 2, 1)],
    "units": [("машина", "SKF", "hot", "Горячая часть", "A", 4),
              ("машина", "SKF", None, None, None, 6),
              ("деталь", "SKF/FAG", None, None, None, 2)],
    "alts": [("SKF", "номер изготовителя", "Выдуманный подшипниковый", 3),
             ("SKF", "аналог", None, 1)],
    "registry": [("SKF", "Разведанная компания", "oem", "Швеция", "каталог ЗИП", 2, 1)],
}


def снимок(**кроме):
    аргументы = dict(словарь=СЛОВАРЬ, цепочка=ЦЕПОЧКА, атлас=АТЛАС, каналы=КАНАЛЫ,
                     имена_портала={"101": "Альфа-Коготь ООО"}, имена_брендов={"501": "SKF"},
                     собран="2026-09-24T01:00:00Z")
    аргументы.update(кроме)
    return brands.собрать(КОДЫ, КАТАЛОГ, **аргументы)


def бренд(s, k):
    return next(b for b in s[brands.КЛЮЧ]["brands"] if b["k"] == k)


def поставщик(s, k):
    return next(x for x in s[brands.КЛЮЧ]["suppliers"] if x["k"] == k)


def test_ключ_написания_совпадает_с_правилом_запроса():
    assert codes_sql.ключ_написания("SKF Gmbh") == "skf"
    assert codes_sql.ключ_написания("Ab Skf") == "skf"
    assert codes_sql.ключ_написания("Wärtsilä Oyj") == "wartsila"
    # Кириллическая «К» в латинском слове — двойник, ключ латинский.
    assert codes_sql.ключ_написания("SКF") == "skf"
    # Чисто русский ключ двойниками не трогается.
    assert codes_sql.ключ_написания("ООО Ромашка") == "ромашка"
    # «co» внутри слова — не правовая форма.
    assert codes_sql.ключ_написания("Cobalt") == "cobalt"


def test_карта_словаря_и_спорные_написания():
    карта, записи, спорных = brands.карта_словаря(СЛОВАРЬ)
    assert карта["skf"] == "skf" and карта["fag"] == "fag"
    assert "двойня" not in карта, "спорное написание не должно склеивать бренды"
    assert спорных == 1
    assert set(записи) == {"skf", "fag", "двойня1", "двойня2"}


def test_бренд_сведён_к_словарю_а_без_ключа_виден_числом():
    s = снимок()
    skf = бренд(s, "skf")
    assert skf["dict"] is True and skf["name"] == "SKF"
    assert "Skf Gmbh" in skf["spellings"]
    акмеро = бренд(s, "акмеро")
    assert акмеро["dict"] is False and акмеро["name"] == "Акмеро"
    н = s[brands.КЛЮЧ]["coverage"]["undefined"]
    assert н["brands_without_dict_key"] == sum(1 for b in s[brands.КЛЮЧ]["brands"] if not b["dict"])
    assert н["brands_without_dict_key"] >= 1


def test_ячейка_двух_брендов_делится():
    s = снимок()
    # «SKF/FAG» дал по три детали обоим, «Ab Skf» — ещё две SKF.
    assert бренд(s, "skf")["parts"]["n"] == 5
    assert бренд(s, "fag")["parts"]["n"] == 3
    assert "skffag" not in {b["k"] for b in s[brands.КЛЮЧ]["brands"]}


def test_узлы_с_долей_не_определено():
    s = снимок()
    м = бренд(s, "skf")["units"]["machine"]
    assert м["parts"] == 10 and м["undefined"] == 6 and м["units"] == 1
    assert м["list"][0]["id"] == "hot"
    д = бренд(s, "fag")["units"]["part"]
    assert д["undefined"] == 2 and д["units"] == 0


def test_каталожная_часть_и_файлы():
    s = снимок()
    skf = бренд(s, "skf")
    assert skf["models"][0]["use"] == "насосная станция" and skf["fleet"] == 2
    assert skf["alts"]["kinds"] == {"номер изготовителя": 3, "аналог": 1}
    assert skf["alts"]["makers"] == [["Выдуманный подшипниковый", 3]]
    assert skf["atlas"]["country"] == "Швеция"
    assert skf["chain"]["makers"] == 1 and skf["chain"]["proven"] == 1
    # Корзина «Прочие» цепочки бренда не заводит.
    assert "прочие" not in {b["k"] for b in s[brands.КЛЮЧ]["brands"]}
    assert бренд(s, "fag")["channel"]["state"] == "только запрос"
    assert skf["registry"]["list"][0]["src"] == ["каталог ЗИП"]
    # Бренд карточки запроса сведён по имени из Битрикса.
    assert skf["card"] == [{"id": "501", "codes": 2}]


def test_у_каждого_поля_источник_и_счётчик_по_тем_же_полям():
    s = снимок()
    поля = s[brands.КЛЮЧ]["fields"]
    assert [f["id"] for f in поля] == brands.ИМЕНА_ПОЛЕЙ
    assert all(f["src"] for f in поля)
    for вселенная in s[brands.КЛЮЧ]["coverage"]["universes"].values():
        assert [f["id"] for f in вселенная["fields"]] == brands.ИМЕНА_ПОЛЕЙ
        for f in вселенная["fields"]:
            assert f["status"] == brands.статус(f["pct"])
    каталог = s[brands.КЛЮЧ]["coverage"]["universes"]["catalog"]
    assert каталог["total"] == 2          # skf и fag — у них есть детали в каталоге
    машины = next(f for f in каталог["fields"] if f["id"] == "models")
    assert (машины["filled"], машины["pct"], машины["status"]) == (1, 50.0, "частично")


def test_пороги_статуса():
    assert brands.статус(80) == "закрыто"
    assert brands.статус(79.9) == "частично"
    assert brands.статус(40) == "частично"
    assert brands.статус(39.9) == "дыра"


def test_имя_поставщика_из_битрикса_иначе_пометка():
    s = снимок()
    а = поставщик(s, "KV-S-000001-1")
    assert а["name"] == "Альфа-Коготь ООО" and а["from"].startswith("Битрикс")
    assert а["reg"] == "alfaclaw"
    б = поставщик(s, "bitrix:777")
    assert б["name"] == "Компания портала 777"
    assert б["from"].startswith("имени нет")
    без = снимок(имена_портала={})
    assert поставщик(без, "KV-S-000001-1")["name"] == "alfaclaw"


def test_код_в_своей_корзине_и_указатель_знает_её():
    s = снимок()
    связи = s[brands.КЛЮЧ_СВЯЗЕЙ]
    коды = {c[0]: c for c in связи["codes"]}
    assert set(коды) == {"ab6205", "kl7"}
    for код, строка in коды.items():
        n = строка[2]
        assert n == brands.корзина(код)
        корзина = s[brands.КЛЮЧИ_КОРЗИН[n]]
        assert код in корзина["codes"]
        for i, ключ in enumerate(brands.КЛЮЧИ_КОРЗИН):
            if i != n:
                assert код not in s[ключ]["codes"]
    ab = коды["ab6205"]
    # Бренды и поставщики кода — номерами в списки того же ключа.
    assert ab[1] == "AB-6205" and {связи["brands"][i] for i in ab[3]} == {"skf", "fag"}
    assert {связи["suppliers"][i] for i in ab[4]} == {"KV-S-000001-1", "bitrix:777"}
    assert ab[5] == 1
    подробно = s[brands.КЛЮЧИ_КОРЗИН[ab[2]]]["codes"]["ab6205"]
    usd = next(o for o in подробно["offers"] if o["s"] == "KV-S-000001-1")
    assert (usd["min"], usd["med"], usd["max"], usd["cur"]) == (10, 11, 12, "USD")
    # Написание единицы отличается от сведённой — оно сохраняется рядом.
    assert usd["unit_w"] == "pcs" and usd["drop"] == 1
    # Валюта не названа — это значение, а не пропуск: цена без неё не печатается.
    без_валюты = next(o for o in подробно["offers"] if o["s"] == "bitrix:777")
    assert без_валюты["cur"] == "(не названа)"


def test_пары_и_число_поставщиков_у_бренда():
    s = снимок()
    пары = s[brands.КЛЮЧ_ПАР]
    assert len(пары["pairs"][0]) == len(пары["pair_fields"])
    б, п = пары["pairs"][0][:2]
    assert (пары["brands"][б], пары["suppliers"][п]) == ("skf", "KV-S-000001-1")
    skf = бренд(s, "skf")
    assert skf["sups"] == 1 and skf["priced"] == 1 and skf["asked"] == 2


def test_плитки_сводки_берутся_из_итогов():
    s = снимок()
    плитки = {p["id"]: p["value"] for p in s[brands.КЛЮЧ]["totals"]["tiles"]}
    assert плитки["all"] == 10 and плитки["kp"] == 3
    # Метрики, которой в итогах нет, — «нет данных», а не ноль.
    assert плитки["customer"] is None
    assert s[brands.КЛЮЧ]["totals"]["locale_ok"] is True


def test_метрики_плиток_есть_в_запросе_итогов():
    """Плитка ищет строку итогов по тексту метрики. Разойдись текст с запросом —
    плитка молча показала бы «нет данных»."""
    текст = codes_sql.ЗАПРОСЫ["totals"]
    for _, раздел, метрика, _ in brands.ПЛИТКИ:
        assert f"'{раздел}'" in текст, раздел
        assert метрика in текст, метрика


def test_одинаковый_вход_одинаковый_выход():
    assert json.dumps(снимок(), ensure_ascii=False) == json.dumps(снимок(), ensure_ascii=False)


# Поля, которые воркер режет правами suppliers_pii и suppliers_fin
# (public/_worker.js, SUPPLIERS_FIELDS). В снимке брендов их быть не должно:
# резка там — страховка, а не единственная защита.
def закрытые_поля():
    текст = (ROOT / "public" / "_worker.js").read_text(encoding="utf-8")
    блок = re.search(r"const SUPPLIERS_FIELDS = \[(.*?)\];", текст, re.S).group(1)
    return set(re.findall(r'"([a-z_]+)"', блок)) - {"suppliers_pii", "suppliers_fin"}


def ключи_вглубь(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from ключи_вглубь(x)
    elif isinstance(v, list):
        for x in v:
            yield from ключи_вглубь(x)


def test_в_снимке_нет_полей_контактов_и_денег():
    закрытые = закрытые_поля()
    assert {"emails", "terms", "spend"} <= закрытые
    for ключ, объект in снимок().items():
        лишние = set(ключи_вглубь(объект)) & закрытые
        assert not лишние, f"{ключ}: {лишние}"


def test_ключи_снимка_совпадают_с_закрытым_списком_воркера():
    текст = (ROOT / "public" / "_worker.js").read_text(encoding="utf-8")
    assert f'const BRANDS_KEY = "{brands.КЛЮЧ}"' in текст
    assert f'const BRANDS_LINKS_KEY = "{brands.КЛЮЧ_СВЯЗЕЙ}"' in текст
    assert f'const BRANDS_PAIRS_KEY = "{brands.КЛЮЧ_ПАР}"' in текст
    assert f"const BRANDS_PARTS = {brands.КОРЗИН};" in текст
    assert set(снимок()) == set(brands.ВСЕ_КЛЮЧИ)


# ── Размер ───────────────────────────────────────────────────────────────────
# ПРОПОРЦИИ — СИНТЕТИКИ ОБЪЁМА ЖИВОЙ БАЗЫ (2,34 млн строк спроса, 40 тыс. строк
# цены КП, 13,5 тыс. деталей каталога), на которой запросы проверялись
# 23.09.2026. Прогон сборщика по ней 24.09.2026 дал: брендов 2 999, поставщиков
# 841, пар 50 908, строк «код × поставщик × валюта × единица» 34 901, кодов
# 10 917. Корпус ниже тяжелее той синтетики (случайные числа вместо нулей), то
# есть проверка пессимистичная: на нём пары и указатель кодов одним ключом с
# именами заняли 81 % предела — поэтому они разнесены по двум ключам и ссылаются
# номерами. Живая база меньше: цен КП ~35 тыс.
БРЕНДОВ, ПОСТАВЩИКОВ, ПАР, СТРОК_КОДОВ, КОДОВ = 2_999, 841, 50_908, 34_901, 10_917
ЗАПАС = 0.80


def корпус_объёма(сид=7):
    r = random.Random(сид)
    слоги = ["ал", "бор", "вект", "гал", "дор", "ер", "жук", "зен", "ир", "кор", "лум", "мак"]
    имя = lambda: "".join(r.choice(слоги) for _ in range(r.randint(2, 4))).capitalize()  # noqa: E731
    бренды_ = [f"b{i:04d}{имя().lower()}" for i in range(БРЕНДОВ)]
    пост = [f"KV-S-{i:06d}-{i % 10}" for i in range(ПОСТАВЩИКОВ)]
    коды = [f"x{i:05d}{r.randint(100, 99999)}" for i in range(КОДОВ)]
    к = {"brands": [{"brand_key": b, "brand": b[5:].capitalize(), "spellings": r.randint(1, 4),
                     "codes_any": r.randint(1, 900), "codes_customer": r.randint(0, 400),
                     "codes_kp_file": r.randint(0, 50), "codes_catalog": r.randint(0, 300),
                     "codes_customer_kp_price": r.randint(0, 50), "rows_customer": r.randint(0, 2000),
                     "deals_customer": r.randint(0, 300)} for b in бренды_],
         "suppliers": [{"supplier": s, "name": имя().lower() + "industries", "name_from": "реестр",
                        "portal_keys": str(1000 + i), "codes": r.randint(1, 300),
                        "price_rows": r.randint(1, 900), "cards": r.randint(1, 80),
                        "files": r.randint(1, 80), "currencies": "USD EUR",
                        "brands_file": r.randint(0, 40), "domains": имя().lower() + ".example"}
                       for i, s in enumerate(пост)],
         "totals": [], "card_brands": [], "match": [], "pairs": []}
    for _ in range(ПАР):
        к["pairs"].append({"row_kind": "пара", "brand_key": r.choice(бренды_), "supplier": r.choice(пост),
                           "codes": r.randint(1, 60), "codes_own_row": r.randint(0, 20),
                           "codes_by_customer": r.randint(0, 30), "codes_by_catalog": r.randint(0, 10),
                           "codes_priced": r.randint(0, 60), "price_rows": r.randint(1, 90),
                           "codes_by_currency": "USD 12; EUR 3", "asked_closed": r.randint(0, 20),
                           "brand_codes_priced": 100, "brand_codes_asked": 200})
    for i in range(СТРОК_КОДОВ):
        код = коды[i % КОДОВ]
        б = r.sample(бренды_, 2)
        к["match"].append({
            "код_ключ": код, "код_как_написан": код.upper()[:4] + "-" + код[4:],
            "наименование": "Подшипник роликовый конический однорядный " + имя(),
            "бренд_из_строки": б[0][5:].capitalize(), "бренд_из_строки_ключ": б[0],
            "бренд_из_спроса": "; ".join(x[5:].capitalize() for x in б), "бренд_из_спроса_ключ": " ".join(б),
            "бренд_с_карточки": "1234 5678", "поставщик_ключ": r.choice(пост),
            "ключи_портала": "1234", "валюта": r.choice(["USD", "EUR", "CNY", "RUB"]), "единица": "шт",
            "цена_мин": round(r.uniform(1, 9000), 4), "цена_макс": round(r.uniform(1, 9000), 4),
            "цена_медиана": round(r.uniform(1, 9000), 4), "строк_цены": r.randint(1, 9),
            "строк_отсеяно": r.choice([0, 0, 1]), "количество_в_КП": r.randint(1, 50),
            "базис": r.choice(["EXW", "DDP", None]), "дата_первая": "2026-09-01",
            "дата_последняя": "2026-09-12", "дата_откуда": "запись разбора (переразбор ставит новую)",
            "карточек_запроса": r.randint(1, 5), "файлов_кп": r.randint(1, 5),
            "спрошен_нами": r.random() < 0.4, "сделок_спроса": r.randint(0, 12),
            "стороны_спроса": "заказчик", "поставщиков_с_ценой_по_коду": r.randint(1, 4)})
    return к


def test_каждый_ключ_с_запасом_до_предела():
    снимки = brands.собрать(корпус_объёма(), {}, словарь=None, собран="2026-09-24T01:00:00Z")
    for ключ, объект in снимки.items():
        n = len(json.dumps(объект, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        assert n <= ЗАПАС * ПРЕДЕЛ, f"{ключ}: {n / ПРЕДЕЛ:.0%} предела"
    assert len(снимки[brands.КЛЮЧ_ПАР]["pairs"]) == ПАР
    assert len(снимки[brands.КЛЮЧ_СВЯЗЕЙ]["codes"]) == КОДОВ
