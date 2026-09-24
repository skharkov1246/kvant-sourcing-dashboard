"""Ревизия вкладок портала (scripts/portal_audit.py) на выдуманных снимках.

Корпус придуман (CLAUDE.md, правило 18): компании «ООО Ромашка …», коды вида
«NU 316» и «RX-7731», ИНН с посчитанной контрольной цифрой, домены *.example.
Снимки собираются теми же функциями, что у публикаторов (publish_suppliers.собрать,
crossref.разложить, brands.заполненность, publish_counters.собрать, Builder
библиотеки), — чтобы ревизия проверялась на той форме, которую видит страница.

Три утверждения:
  · чистый корпус не даёт НИ ОДНОГО дефекта — проверки не ругаются на нормальные
    значения (NU 316, «ООО Ромашка», SKF), и почти каждая проверка на нём
    применяется (список неприменимых закрыт и объяснён);
  · каждая проверка ловит свой дефект: по мутации на каждую;
  · в журнал не уходит ни одно имя, код, ИНН или домен корпуса (правило 17).
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _модуль(имя, файл):
    spec = importlib.util.spec_from_file_location(имя, ROOT / "scripts" / файл)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pa = _модуль("portal_audit_under_test", "portal_audit.py")
ps = _модуль("publish_suppliers_for_audit", "publish_suppliers.py")
pc = _модуль("publish_counters_for_audit", "publish_counters.py")
lib2 = _модуль("publish_library_v2_for_audit", "publish_library_v2.py")

from kv_number import luhn  # noqa: E402
from library import brands, crossref  # noqa: E402

СЕЙЧАС = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
СОБРАН = "2026-09-24T01:07:00Z"
ДАТА = "2026-09-01"

ПЕРВЫЕ = ("Ромашка", "Лютик", "Василёк", "Колокольчик", "Одуванчик", "Незабудка")
ВТОРЫЕ = ("Трейд", "Сервис", "Снаб", "Пром", "Техно", "Инжиниринг", "Комплект", "Маш", "Лаб", "Групп")
ИМЕНА = [f"ООО {a} {b}" for a in ПЕРВЫЕ for b in ВТОРЫЕ]
ИМЕНА[0] = "ООО Ромашка"
КОДЫ = [("NU 316", "Подшипник роликовый цилиндрический"),
        ("6205-2RS", "Подшипник шариковый радиальный"),
        ("KV-4417-B", "Уплотнение торцевое"),
        ("RX-7731", "Втулка направляющая"),
        ("PL-2280-S", "Кольцо уплотнительное"),
        ("TM-5512", "Муфта зубчатая"),
        ("VK-3310", "Клапан обратный"),
        ("GS-8841-A", "Шестерня ведомая"),
        ("BN-1207", "Болт фундаментный"),
        ("ZX-6604", "Фильтр масляный"),
        ("QR-9015", "Датчик температуры"),
        ("HL-4478", "Рукав высокого давления")]
ОТКУДА = ("bitrix:title", "bitrix:requisite", "написание", "реестр")
ПРИЧИНЫ = ("один источник, сливать не с чем", "домен совпал у двух и более источников",
           "имя совпало, но домен есть не у всех — проверить нечем",
           "имя совпало, домена нет ни у одного источника",
           "схлопнуты 2 карточки портала под одним именем — задвоение Bitrix, подтвердить человеком")
МЕТКИ = (("bitrix",), ("bitrix", "реквизиты Битрикса"), ("реквизиты портала",),
         ("сведение реестров",), ("bitrix", "сведение реестров"), ("реквизиты портала", "реквизиты Битрикса"))


def номер(seq: int) -> str:
    тело = f"{seq:06d}"
    return f"KV-S-{тело}-{luhn(тело)}"


def инн10(база: str) -> str:
    веса = (2, 4, 10, 3, 5, 9, 4, 6, 8)
    return база + str(sum(w * int(c) for w, c in zip(веса, база)) % 11 % 10)


def ключ(n: str) -> str:
    return pa.ключ_кода(n)


# ── Корпус ───────────────────────────────────────────────────────────────────

def _поставщики():
    строки, признаки, отзывчивость = [], [], []
    for i, имя in enumerate(ИМЕНА):
        sid = номер(i + 1)
        строки.append((sid, имя, "RU", ПРИЧИНЫ[i % 5], "active", "candidate", True, ОТКУДА[i % 4]))
        метки = МЕТКИ[i % 6]
        признаки.append((sid, "inn", инн10(f"77{i + 1:07d}"), метки[0]))
        признаки.append((sid, "domain", f"firm{i}.example", метки[-1]))
        if i < 10:
            отзывчивость.append((sid, {"sent": 4, "answered": 2, "quoted": 1, "silent": 1,
                                       "no_outcome": 1, "cards": 4}))
    for j, имя in enumerate(("ООО Тюльпан Снаб", "ООО Тюльпан Маш")):
        sid = f"KV-S-9{j:05d}-0"
        строки.append((sid, имя, None, ПРИЧИНЫ[0], "active", "candidate", False, "bitrix:title"))
        признаки.append((sid, "domain", f"tulip{j}.example", "bitrix"))
    очередь = [{"reason": "несколько правовых форм", "names": ["ООО Тюльпан Групп", "АО Тюльпан Групп"],
                "keys": ["bitrix:7001", "bitrix:7002"]}]
    sup = ps.собрать(строки, признаки, 3, отзывчивость, очередь)
    sup["published_at"] = СОБРАН
    sup["entities"][0].update(legal_form="ООО", parts=4)
    return sup


def _компании_позиции(i: int) -> list[int]:
    """Какие компании (индексы) котируют позицию i: две у чётных, одна у нечётных.
    Компания 10 — вторая карточка портала той же сущности, что и компания 1."""
    out = [i % 10] + ([(i + 1) % 10] if i % 2 == 0 else [])
    if i == 7:
        out.append(10)
    return out


def _номенклатура():
    компании = []
    for ci in range(11):
        сущ = ci if ci < 10 else 1
        компании.append({"co": str(1001 + ci), "ent": номер(сущ + 1), "name": ИМЕНА[сущ],
                         "rows": 0, "parts": 0, "brands": ["SKF"], "oem": ["SKF"]})
    позиции = []
    for i, (n, имя) in enumerate(КОДЫ):
        k = ключ(n)
        каталог = i < 6
        предл, рёбра = [], []
        for ci in _компании_позиции(i):
            c = компании[ci]
            цена = 10.0 + i
            предл.append({"c": c["co"], "e": c["ent"], "p": цена, "u": "USD", "q": 10.0, "n": "шт",
                          "t": цена * 10, "d": ДАТА, "s": "DAP", "l": 30, "y": "30 % аванс, остальное по факту",
                          "a": 30.0, "r": "ссс?", "f": str(5001 + i), "v": 0.9, "b": ["SKF"], "m": "SKF"})
            рёбра.append([ci, 1, цена, "USD", ДАТА])
            c["rows"] += 1
            c["parts"] += 1
        позиции.append({
            "k": k, "n": n, "name": имя, "co": len(рёбра), "offers": len(предл), "shown": len(предл),
            "cmp": len(предл) >= 2, "oem_file": ["FAG"] if not каталог else [],
            "oem_cat": "SKF" if каталог else None, "brands": ["SKF"] if каталог else ["FAG"],
            "makers": ([{"name": "SKF", "role": "OEM", "country": "SE", "makes": "подшипники",
                         "verdict": "подтверждено", "conf": "высокая"}] if каталог else []),
            "alts": ([{"pn": n + " ECP", "kind": "аналог", "maker": "FAG", "conf": 0.8}] if каталог else []),
            "models": (["Насос НМ-1250"] if каталог else []),
            "demand": {"deals": 3, "rows": 5, "unit_name": "шт", "qty": 20.0, "units": 1},
            "cat": каталог, "cat_name": имя if каталог else None,
            "category": "Подшипники" if каталог else None, "unit": "Насос" if каталог else None,
            "kv": None, "list": предл, "e": sorted(рёбра)})
    снимок = {"version": 1, "published_at": СОБРАН, "positions": позиции, "companies": компании,
              "totals": {"positions": len(позиции),
                         "with_choice": sum(1 for p in позиции if p["co"] >= 2),
                         "comparable": sum(1 for p in позиции if p["cmp"]),
                         "in_catalog": sum(1 for p in позиции if p["cat"]),
                         "companies": len(компании), "companies_resolved": len(компании),
                         "no_demand": 0, "offers": sum(p["offers"] for p in позиции)}}
    return crossref.разложить(снимок)


def _бренды(ents_по_ci):
    коды = [(ключ(n), n, имя, i) for i, (n, имя) in enumerate(КОДЫ[:6])]
    поставщики_кода = {k: sorted({ents_по_ci[ci] for ci in _компании_позиции(i)}) for k, _, _, i in коды}
    все_поставщики = sorted({s for v in поставщики_кода.values() for s in v})
    поставщики = []
    for s in sorted(set(ents_по_ci.values())):
        i = int(s[5:11]) - 1
        ключи_портала = [str(1001 + ci) for ci, e in ents_по_ci.items() if e == s]
        поставщики.append({"k": s, "name": ИМЕНА[i], "from": "Битрикс: карточка компании " + ключи_портала[0],
                           "keys": ключи_портала, "codes": 2, "rows": 3, "cards": 2, "files": 1,
                           "cur": ["USD"], "domains": [f"firm{i}.example"]})
    поставщики[-1].update({"from": "реестр", "reg": поставщики[-1]["name"]})
    пары = [["skf", все_поставщики[0]], ["skf", все_поставщики[1]], ["fag", все_поставщики[2]]]
    бренды_ = [
        {"k": "skf", "name": "SKF", "dict": True, "spellings": ["SKF", "Skf"], "spellings_n": 2,
         "codes": {"any": 6, "plausible": 6, "customer": 4, "kp_file": 3, "catalog": 2, "customer_kp": 2,
                   "customer_buy": 3, "rows_customer": 8, "rows_kp": 5, "deals": 4, "companies_naming": 2},
         "spelled": 2, "sups": 2, "priced": 3, "asked": 4,
         "parts": {"n": 10, "unit": 9, "categories": 2},
         "models": [{"id": "m1", "name": "Насос НМ-1250", "family": "НМ", "use": "перекачка"}],
         "models_n": 1, "fleet": 0,
         "units": {"part": {"list": [{"id": "u1", "name": "Опора ротора", "crit": "A", "parts": 9}],
                            "units": 1, "parts": 10, "undefined": 1}},
         "alts": {"kinds": {"аналог": 2}, "makers": [["FAG", 2]], "makers_n": 1},
         "chain": {"n": 2, "makers": 1, "notes": 1, "proven": 1,
                   "list": [{"to": "FAG", "kind": "maker", "proof": ["каталог"]},
                            {"to": "дистрибьютор", "kind": "routing_note"}]},
         "channel": {"state": "есть", "channel": "дистрибьютор", "checked": "2026-08-01", "action": "запросить"},
         "registry": {"n": 1, "list": [{"name": "ООО Ромашка", "role": "дистрибьютор", "country": "RU",
                                        "src": ["разведка"], "parts": 3, "checked": 1}]},
         "card": [{"id": "1138", "codes": 2}],
         "atlas": {"name": "SKF", "country": "Швеция", "owner": "Группа SKF"}},
        {"k": "fag", "name": "FAG", "dict": True, "spellings": ["FAG"], "spellings_n": 1,
         "codes": {"any": 3, "plausible": 3, "customer": 2, "customer_kp": 1, "rows_customer": 3, "deals": 2},
         "spelled": 1, "sups": 1, "priced": 2, "asked": 2,
         "parts": {"n": 5, "unit": 5, "categories": 1},
         "models": [{"id": "m2", "name": "Турбина ТВ-8", "family": "ТВ"}], "models_n": 1, "fleet": 0},
    ]
    kp = len(коды)
    плитки = {"all": 100, "customer": 60, "kp": kp, "buy": 20, "brand": 50, "supplier": 5,
              "customer_kp": 4, "customer_buy": 10}
    строки = [{"section": "проверка", "metric": "локаль", "value": 1}]
    for ид, раздел, метрика, _ in brands.ПЛИТКИ:
        строки.append({"section": раздел, "metric": метрика, "value": плитки[ид]})
    строки += [{"section": "цены КП", "metric": "строк цены «разбор КП»", "value": 100},
               {"section": "цены КП", "metric": "строк цены, поставщик: поставщик не указан", "value": 5}]
    сводка = {"version": 1, "published_at": СОБРАН,
              "fields": [{"id": i, "label": л, "src": и} for i, л, и in brands.ПОЛЯ],
              "totals": brands._итоги(строки), "brands": бренды_, "suppliers": поставщики,
              "card_brands": [{"id": "1138", "name": "SKF", "k": "skf", "codes": 2, "rows": 3, "sups": 1,
                               "cards": 1}],
              "dict": {"records": 2, "spellings_mapped": 3, "ambiguous": 0, "from": "dict/oem.json",
                       "card_keys": 1},
              "parts": brands.КОРЗИН}
    сводка["coverage"] = brands.заполненность(сводка)
    список_б = ["skf"]
    список_с = все_поставщики
    связи = {"version": 1, "published_at": СОБРАН, "brands": список_б, "suppliers": список_с,
             "code_fields": ["code", "written", "part", "brands", "suppliers", "asked"],
             "codes": [[k, n if n != k else None, brands.корзина(k), [0],
                        [список_с.index(s) for s in поставщики_кода[k]], 1] for k, n, _, _ in sorted(коды)]}
    пб = sorted({p[0] for p in пары})
    пс = sorted({p[1] for p in пары})
    пары_снимок = {"version": 1, "published_at": СОБРАН, "brands": пб, "suppliers": пс,
                   "pair_fields": ["brand", "supplier", "codes", "own_row", "by_customer", "by_other_kp",
                                   "by_catalog", "priced", "price_rows", "by_currency", "asked_closed"],
                   "pairs": [[пб.index(b), пс.index(s), 2, 1, 1, 0, 1, 2, 3, "USD 2", 1] for b, s in пары]}
    корзины = [{"version": 1, "published_at": СОБРАН, "part": i, "codes": {}} for i in range(brands.КОРЗИН)]
    for k, n, имя, i in коды:
        offers = [{"s": s, "cur": "USD", "unit": "шт", "min": 10.0, "med": 11.0, "max": 12.0, "rows": 2,
                   "qty": 5.0, "basis": "DAP", "d1": "2026-08-01", "d2": ДАТА, "dsrc": "дата цены",
                   "cards": 1, "files": 1, "br": "SKF", "brk": ["skf"], "card": ["1138"], "tot_ok": 2}
                  for s in поставщики_кода[k]]
        корзины[brands.корзина(k)]["codes"][k] = {
            "n": n, "name": имя, "bs": "SKF", "bsk": ["skf"], "bc": "SKF", "bck": ["skf"], "asked": True,
            "deals": 2, "sides": "заказчик", "sups": len(поставщики_кода[k]), "offers": offers}
    out = {brands.КЛЮЧ: сводка, brands.КЛЮЧ_СВЯЗЕЙ: связи, brands.КЛЮЧ_ПАР: пары_снимок}
    for i, k in enumerate(brands.КЛЮЧИ_КОРЗИН):
        out[k] = корзины[i]
    return out


def _счётчики():
    def числа(asked, with_kp):
        other, rows_asked, rows_with = 100, 5000, 2000
        return {"asked": asked, "with_kp": with_kp, "other_feed": other,
                "no_price": asked - with_kp - other, "rows_asked": rows_asked, "rows_customer": rows_asked,
                "rows_with": rows_with, "rows_without": 2800, "price_codes": 800, "price_asked": with_kp,
                "price_not_asked": 800 - with_kp, "price_rows": 2400, "price_codes_any": 1200,
                "price_supplier_added": 100, "price_in_catalog": 150, "catalog": 12000,
                "catalog_priced": 150, "catalog_asked": 400, "plausible": asked - 100, "no_digit": 60,
                "shorter_than_four": 40, "longer_than_25": 10}

    строки = [
        ("коды_и_цены", "36000000001", "2026-09-23T01:30:00Z", числа(980, 290), None),
        ("коды_и_цены", "36000000002", "2026-09-24T01:30:00Z", числа(1000, 300), None),
        ("инкремент_сделки", "36000000003", "2026-09-23T02:00:00Z", {"начало": 1790000000, "после_id": 4900}, None),
        ("инкремент_сделки", "36000000004", "2026-09-24T02:00:00Z", {"начало": 1790086400, "после_id": 5000}, None),
        ("почта:сделки", "36000000005", "2026-09-24T03:00:00Z", {"после_id": 777, "писем": 12}, None),
        ("реестр_брендов", "36000000006", "2026-09-23T04:00:00Z",
         {"кп.1.всего": 100, "кп.1.с_текстом": 80, "кп.1.разрешено": 60}, None),
        ("реестр_брендов", "36000000007", "2026-09-24T04:00:00Z",
         {"кп.1.всего": 110, "кп.1.с_текстом": 90, "кп.1.разрешено": 70}, None),
    ]
    return pc.собрать(строки)


СЕГМЕНТЫ = [{"id": "bearings", "name": "Подшипники", "note": "Подшипники качения"},
            {"id": "pumps", "name": "Насосы", "note": "Центробежные насосы"}]


def _статья(ид, seg, вид, title, поля=None, **ещё):
    sources = {"kind": вид, "importer_id": ид, "publication_approved": True,
               "references": [{"url": "https://example.test/source", "title": "Каталог изготовителя"}]}
    if поля:
        sources[{"component": "component_fields", "supplier": "supplier_fields",
                 "price": "price_fields"}[вид]] = поля
    sources.update(ещё.pop("sources", {}))
    return {"id": ид, "segment_id": seg, "title": title, "topic": "Каталог", "confidence": "verified",
            "body": f"{title}. Сведения из каталога изготовителя.", "sources": sources,
            "updated_at": "2026-09-01T00:00:00Z", **ещё}


def _библиотека():
    строки, связи = [], {}
    for seg in ("bearings", "pumps"):
        comp = f"lib:{seg}:c1"
        строки.append(_статья(comp, seg, "component", "Подшипник роликовый цилиндрический",
                              {"part_number": "NU 316", "oem": "SKF", "name": "Подшипник роликовый цилиндрический",
                               "purpose": "опора ротора", "quantity": 2, "unit": "шт", "priced": True,
                               "family": "cylindrical_roller", "aliases": ["NU316-E"]}))
        строки.append(_статья(f"lib:{seg}:c2", seg, "component", "Кольцо уплотнительное",
                              {"part_number": "PL-2280-S", "oem": "FAG", "name": "Кольцо уплотнительное",
                               "quantity": 4, "unit": "шт", "priced": False}))
        for j, имя in enumerate(("ООО Ромашка", "ООО Лютик Трейд")):
            строки.append(_статья(f"lib:{seg}:s{j}", seg, "supplier", имя,
                                  {"name": имя, "role": "trader", "oem_brands": ["SKF"]}))
        for j in range(2):
            строки.append(_статья(f"lib:{seg}:p{j}", seg, "price", "Цена NU 316",
                                  {"amount": f"12{j}.50", "currency": "USD", "price_date": "2026-08-01",
                                   "price_type": "Предложение поставщика", "direction": "input_estimate",
                                   "supplier": "ООО Ромашка", "part_number": "NU 316", "unit": "шт"}))
        строки.append(_статья(f"lib:{seg}:k1", seg, "knowledge", "Опыт замены подшипника"))
        связи[comp] = [{"article_id": f"lib:{seg}:s{j}", "relation_type": "historical_supplier_candidate",
                        "position_id": j + 1, "json_pointer": f"/items/{j}", "part_number": "NU 316",
                        "source_url": "https://example.test/list"} for j in range(2)]
    return строки, связи


def _словарь():
    return {"note": "выдуманный словарь", "count": 2, "records": [
        {"oem_key": "skf", "name": "SKF", "spellings": [{"spelling": "SKF", "where": "dict/oem.json:records"},
                                                        {"spelling": "Skf", "where": "dict/oem.json:records"}],
         "n_spellings": 2},
        {"oem_key": "fag", "name": "FAG", "spellings": [{"spelling": "FAG", "where": "dict/oem.json:records"}],
         "n_spellings": 1}]}


def корпус() -> dict:
    """Объекты всех ключей плюс сырьё библиотеки и словарь (ключи на «_»)."""
    о = {"suppliers:v1": _поставщики()}
    о.update(_номенклатура())
    ents = {ci: c["ent"] for ci, c in enumerate(о[crossref.КЛЮЧ]["companies"])}
    о.update(_бренды(ents))
    о["counters:v1"] = _счётчики()
    строки, связи = _библиотека()
    о["_library_rows"], о["_relations"], о["_segments"] = строки, связи, copy.deepcopy(СЕГМЕНТЫ)
    о["_dict"] = _словарь()
    return о


class _ХранилищеБлобов:
    def __init__(self):
        self.values = {}

    def preserve(self, _ns, key, raw):
        self.values[key] = raw

    def get(self, _ns, key):
        return self.values.get(key)


def закодировать(о: dict) -> dict[str, bytes]:
    out = {k: json.dumps(v, ensure_ascii=False).encode("utf-8") for k, v in о.items() if not k.startswith("_")}
    cf = _ХранилищеБлобов()
    store = lib2.Store(cf, "0" * 32)
    builder = lib2.Builder(store, о["_segments"], о["_relations"])
    for row in sorted(о["_library_rows"], key=lambda r: r["id"]):
        builder.add(row)
    manifest = builder.finish("rev-1", о.get("_library_published", СОБРАН))
    out.update(cf.values)
    out[pa.КЛЮЧ_БИБЛИОТЕКИ] = lib2.encode({"version": 2, "revision": "rev-1", "published_at": СОБРАН,
                                          "manifest": store.put(manifest)})
    out.update(cf.values)
    return out


class Память:
    вид = "memory"

    def __init__(self, значения):
        self.значения = значения

    def get(self, ключ):
        return self.значения.get(ключ)


def прогон(правка=None, правка_байтов=None, прошлые=None) -> dict[str, "pa.Вкладка"]:
    о = корпус()
    if правка:
        правка(о)
    сырьё = закодировать(о)
    if правка_байтов:
        правка_байтов(сырьё)
    с = pa.Снимки(Память(сырьё))
    пр = pa.Снимки(Память(прошлые)) if прошлые is not None else None
    return {т.ид: т for т in pa.ревизия(с, СЕЙЧАС, о["_dict"], пр, lib2=lib2)}


@pytest.fixture(scope="module")
def чистый():
    return прогон()


# ── Чистый корпус ────────────────────────────────────────────────────────────

# Проверки, которым на чистом корпусе НЕЧЕГО проверять, — закрытым списком и с
# причиной. Новая проверка, не попавшая ни сюда, ни в применение, — сигнал, что
# корпус её не кормит и «ноль дефектов» по ней ничего не значит.
НЕ_ПРИМЕНИМЫ = {
    ("suppliers", "s.caveat"): "склеенных записей в корпусе нет, оговорки тоже",
    ("nomenclature", "n.o_t_noq"): "суммы без количества в корпусе нет",
    ("nomenclature", "n.worse_lost"): "нужен прошлый снимок (--prev-dir)",
    ("nomenclature", "n.worse_brand"): "нужен прошлый снимок (--prev-dir)",
    ("nomenclature", "n.worse_price"): "нужен прошлый снимок (--prev-dir)",
    ("nomenclature", "n.worse_co"): "нужен прошлый снимок (--prev-dir)",
    ("brands", "b.unmerged"): "все бренды корпуса словарные",
    ("counters", "c.note"): "оговорок в корпусе нет",
    ("library", "l.blob"): "считается только при сбое чтения блоба",
    ("library", "l.body_table"): "таблиц в текстах корпуса нет",
    ("library", "l.body_links"): "ссылок в текстах корпуса нет",
    ("library", "l.ref_locator"): "указателей места в корпусе нет",
    ("library", "l.crm_url"): "ссылок CRM в корпусе нет",
    ("library", "l.open_q"): "открытых вопросов у проверенных статей нет",
}


def test_чистый_корпус_без_дефектов(чистый):
    дефекты = [(т.ид, к, д) for т in чистый.values() for к, (п, д) in т.счета.items() if д]
    assert not дефекты


def test_почти_каждая_проверка_применена(чистый):
    пусто = {(т.ид, к) for т in чистый.values() for к, (п, _) in т.счета.items() if not п}
    assert пусто == set(НЕ_ПРИМЕНИМЫ), (
        f"не применены, но не объяснены: {sorted(пусто - set(НЕ_ПРИМЕНИМЫ))}; "
        f"объяснены, но применены: {sorted(set(НЕ_ПРИМЕНИМЫ) - пусто)}")


def test_нормальные_значения_не_обвиняются(чистый):
    """NU 316 — подшипник, а не марка 316; «ООО Ромашка» — имя; SKF — бренд."""
    assert pa.класс_не_кода("NU 316") is None
    assert pa.код_правдоподобен("NU 316")
    assert not pa.мусор_бренда("SKF") and not pa.служебное("SKF")
    assert not pa.company_names.как_ключ("ООО Ромашка")
    for т, к in (("nomenclature", "n.k_class"), ("nomenclature", "n.n_class"),
                 ("brands", "b.c_class"), ("suppliers", "s.name_key"), ("library", "l.oem_queue")):
        п, д = чистый[т].счета[к]
        assert п > 0 and д == 0, (т, к)


def test_польза_считается(чистый):
    for т in ("suppliers", "nomenclature", "brands", "counters", "library", "dict"):
        assert чистый[т].доли, т
        assert чистый[т].средняя_польза() is not None, т
    assert чистый["suppliers"].доли["u.real_name"] == (60, 60)
    assert чистый["suppliers"].доли["u.pos_brand_col"][0] == 0


# ── Каждая проверка ловит свой дефект ────────────────────────────────────────

def сущ(о, i=0):
    return о["suppliers:v1"]["entities"][i]


def поз(о, i=0):
    """Строка списка номенклатуры с позицией КОДЫ[i]."""
    k = ключ(КОДЫ[i][0])
    for часть in crossref.КЛЮЧИ_СПИСКА:
        for p in о[часть]["positions"]:
            if p["k"] == k:
                return p
    raise KeyError(k)


def подр(о, i=0):
    k = ключ(КОДЫ[i][0])
    return о[crossref.КЛЮЧИ_КОРЗИН[crossref.корзина(k)]]["positions"][k]


def код_бр(о, i=0):
    k = ключ(КОДЫ[i][0])
    return о[brands.КЛЮЧИ_КОРЗИН[brands.корзина(k)]]["codes"][k]


def указ(о, i=0):
    k = ключ(КОДЫ[i][0])
    return next(c for c in о[brands.КЛЮЧ_СВЯЗЕЙ]["codes"] if c[0] == k)


def бр(о, k="skf"):
    return next(b for b in о[brands.КЛЮЧ]["brands"] if b["k"] == k)


def пост(о, i=0):
    return о[brands.КЛЮЧ]["suppliers"][i]


def ком(о, ci=0):
    return о[crossref.КЛЮЧ]["companies"][ci]


def точка(о, замер="коды_и_цены", i=-1):
    return о["counters:v1"]["metrics"][замер][i]


def ст(о, суффикс):
    return next(r for r in о["_library_rows"] if r["id"].endswith(суффикс))


def поля(о, суффикс):
    r = ст(о, суффикс)
    вид = r["sources"]["kind"]
    return r["sources"][{"component": "component_fields", "supplier": "supplier_fields",
                         "price": "price_fields"}[вид]]


def _удалить(ключ_):
    return lambda о: о.pop(ключ_)


def _сдвинуть_дату(о, ключ_, дата):
    о[ключ_]["published_at"] = дата


МУТАЦИИ = [
    # ── /suppliers
    ("suppliers", "s.present", _удалить("suppliers:v1")),
    ("suppliers", "s.version", lambda о: о["suppliers:v1"].update(version=2)),
    ("suppliers", "s.published", lambda о: о["suppliers:v1"].update(published_at="24.09.2026")),
    ("suppliers", "s.age", lambda о: о["suppliers:v1"].update(published_at="2026-09-01T00:00:00Z")),
    ("suppliers", "s.cross_age", lambda о: о["suppliers:v1"].update(published_at="2026-09-21T00:00:00Z")),
    ("suppliers", "s.totals", lambda о: о["suppliers:v1"]["totals"].update(with_inn=1)),
    ("suppliers", "s.review", lambda о: о["suppliers:v1"]["totals"].update(review_open=0)),
    ("suppliers", "s.review_tail", lambda о: (о["suppliers:v1"].pop("inn_queue"),
                                              [о["suppliers:v1"]["totals"].pop(x) for x in ("inn_entities", "inn_cards")])),
    ("suppliers", "s.caveat", lambda о: сущ(о, 3).update(domain="firm3.example, firm3b.example")),
    ("suppliers", "s.number_fmt", lambda о: сущ(о, 2).update(number=сущ(о, 2)["number"][:-1] + "0"
                                                            if сущ(о, 2)["number"][-1] != "0" else сущ(о, 2)["number"][:-1] + "1")),
    ("suppliers", "s.number_dup", lambda о: сущ(о, 2).update(number=сущ(о, 3)["number"])),
    ("suppliers", "s.number_wait", lambda о: сущ(о, 2).update(wait_inn=True)),
    ("suppliers", "s.number_group", lambda о: сущ(о, 2).update(number="KV-G-000003-" + str(luhn("000003")))),
    ("suppliers", "s.name_empty", lambda о: сущ(о, 2).update(name="")),
    ("suppliers", "s.name_key", lambda о: сущ(о, 2).update(name="bitrix:2002")),
    ("suppliers", "s.name_latin", lambda о: сущ(о, 2).update(name="supremevalvesltd")),
    ("suppliers", "s.name_domain", lambda о: сущ(о, 2).update(name="firm2.example")),
    ("suppliers", "s.name_from_contra", lambda о: сущ(о, 2).update(name="romashka", name_from="реестр")),
    ("suppliers", "s.name_junk", lambda о: сущ(о, 2).update(name="ООО")),
    ("suppliers", "s.name_dup", lambda о: сущ(о, 2).update(name=ИМЕНА[3])),
    ("suppliers", "s.name_from_vocab", lambda о: сущ(о, 2).update(name_from="sup_entity.display_name")),
    ("suppliers", "s.name_from_absent", lambda о: [e.pop("name_from", None) for e in о["suppliers:v1"]["entities"]]),
    ("suppliers", "s.inn_sum", lambda о: сущ(о, 2).update(inn=сущ(о, 2)["inn"][:-1] + str((int(сущ(о, 2)["inn"][-1]) + 1) % 10))),
    ("suppliers", "s.inn_stub", lambda о: сущ(о, 2).update(inn="0000000000")),
    ("suppliers", "s.inn_letters", lambda о: сущ(о, 2).update(inn="DE123456789")),
    ("suppliers", "s.inn_multi", lambda о: сущ(о, 2).update(inn=сущ(о, 2)["inn"] + ", " + сущ(о, 3)["inn"])),
    ("suppliers", "s.inn_dup", lambda о: сущ(о, 2).update(inn=сущ(о, 3)["inn"])),
    ("suppliers", "s.inn_many", lambda о: [сущ(о, i).update(inn=сущ(о, 1)["inn"]) for i in range(2, 6)]),
    ("suppliers", "s.domain_host", lambda о: сущ(о, 2).update(domain="https://Firm2.example/about")),
    ("suppliers", "s.domain_mail", lambda о: сущ(о, 2).update(domain="gmail.com")),
    ("suppliers", "s.domain_dup", lambda о: сущ(о, 2).update(domain="firm3.example")),
    ("suppliers", "s.rfq_type", lambda о: сущ(о, 2)["rfq"].update(sent=-1)),
    ("suppliers", "s.rfq_sum", lambda о: сущ(о, 2)["rfq"].update(silent=5)),
    ("suppliers", "s.rfq_order", lambda о: сущ(о, 2)["rfq"].update(quoted=3)),
    ("suppliers", "s.rfq_cards", lambda о: сущ(о, 2)["rfq"].update(cards=1)),
    ("suppliers", "s.rfq_zero", lambda о: сущ(о, 2)["rfq"].update(sent=0, answered=0, quoted=0, silent=0,
                                                                  no_outcome=0, cards=0)),
    ("suppliers", "s.sources_vocab", lambda о: сущ(о, 2).update(sources=["список 3"])),
    ("suppliers", "s.sources_empty", lambda о: сущ(о, 2).update(sources=[])),
    ("suppliers", "s.sources_flat", lambda о: [e.update(sources=["bitrix"]) for e in о["suppliers:v1"]["entities"]]),
    ("suppliers", "s.merged_vocab", lambda о: сущ(о, 2).update(merged_by="слито вручную")),
    ("suppliers", "s.merged_empty", lambda о: сущ(о, 2).update(merged_by=None)),
    ("suppliers", "s.country_dead", lambda о: [e.update(country=None) for e in о["suppliers:v1"]["entities"]]),
    ("suppliers", "s.country_iso", lambda о: сущ(о, 2).update(country="Россия")),
    ("suppliers", "s.country_ru", lambda о: сущ(о, 2).update(country="DE")),
    ("suppliers", "s.dead_labels", lambda о: сущ(о, 0).pop("parts")),
    ("suppliers", "s.status_hidden", lambda о: сущ(о, 2).update(status="blocked")),
    ("suppliers", "q.names_key", lambda о: о["suppliers:v1"]["inn_queue"][0]["names"].append("tulipgroup")),
    ("suppliers", "q.reason", lambda о: о["suppliers:v1"]["inn_queue"][0].update(reason="прочее")),
    ("suppliers", "q.card_fmt", lambda о: о["suppliers:v1"]["inn_queue"][0]["cards"].append("bitrix:7003")),
    ("suppliers", "q.card_dup", lambda о: о["suppliers:v1"]["inn_queue"].append(
        {"reason": "несколько правовых форм", "names": ["ООО Пион"], "cards": ["7001"]})),
    ("suppliers", "q.names_empty", lambda о: о["suppliers:v1"]["inn_queue"][0].update(names=[])),
    ("suppliers", "q.stale", lambda о: о["suppliers:v1"]["inn_queue"][0]["names"].append(ИМЕНА[5])),
    ("suppliers", "x.present", _удалить(crossref.КЛЮЧИ_СПИСКА[3])),
    ("suppliers", "x.ent_missing", lambda о: ком(о, 4).update(ent="KV-S-999999-" + str(luhn("999999")))),
    ("suppliers", "x.ent_null", lambda о: ком(о, 4).update(ent=None)),
    ("suppliers", "x.brand_digits", lambda о: ком(о, 4).update(brands=["1138"])),
    ("suppliers", "x.brand_junk", lambda о: ком(о, 4).update(oem=["аналог SKF"])),
    ("suppliers", "x.link_n", lambda о: ком(о, 4).update(oem=["Timken"])),
    ("suppliers", "x.link_s", lambda о: о[brands.КЛЮЧ]["suppliers"].pop(0)),
    ("suppliers", "x.pn_class", lambda о: поз(о, 3).update(n="SS316")),
    ("suppliers", "x.pk_short", lambda о: поз(о, 3).update(k="31")),
    ("suppliers", "x.pname", lambda о: поз(о, 3).update(name="(19mm) SS316 8 3200.0 25600.0")),
    ("suppliers", "x.edge_price", lambda о: поз(о, 3)["e"][0].__setitem__(2, -5.0)),
    ("suppliers", "x.edge_cur", lambda о: поз(о, 3)["e"][0].__setitem__(3, "usd")),
    ("suppliers", "x.edge_date", lambda о: поз(о, 3)["e"][0].__setitem__(4, "2030-01-01")),
    ("suppliers", "x.false_alarm", lambda о: ком(о, 10).update(parts=5)),
    ("suppliers", "x.part_date", lambda о: _сдвинуть_дату(о, crossref.КЛЮЧИ_СПИСКА[2], "2026-09-23T01:07:00Z")),
    ("suppliers", "x.edge_index", lambda о: поз(о, 3)["e"][0].__setitem__(0, 99)),
    ("suppliers", "x.edge_rows", lambda о: ком(о, 4).update(rows=40)),
    # ── /nomenclature
    ("nomenclature", "n.keys", _удалить(crossref.КЛЮЧИ_КОРЗИН[5])),
    ("nomenclature", "n.published", lambda о: о[crossref.КЛЮЧ].update(published_at="вчера")),
    ("nomenclature", "n.age", lambda о: [о[k].update(published_at="2026-09-20T01:07:00Z") for k in crossref.ВСЕ_КЛЮЧИ]),
    ("nomenclature", "n.part_date", lambda о: _сдвинуть_дату(о, crossref.КЛЮЧИ_КОРЗИН[0], "2026-09-23T01:07:00Z")),
    ("nomenclature", "n.version", lambda о: о[crossref.КЛЮЧ].update(lists=9)),
    ("nomenclature", "n.totals", lambda о: о[crossref.КЛЮЧ]["totals"].update(offers=1)),
    ("nomenclature", "n.empty", lambda о: [о[k].update(positions=[]) for k in crossref.КЛЮЧИ_СПИСКА]),
    ("nomenclature", "n.co_fmt", lambda о: ком(о, 4).update(co="bitrix:1005")),
    ("nomenclature", "n.co_dup", lambda о: ком(о, 4).update(co="1004")),
    ("nomenclature", "n.co_orphan", lambda о: о[crossref.КЛЮЧ]["companies"].append(
        {"co": "1099", "ent": номер(20), "name": ИМЕНА[19], "rows": 0, "parts": 0})),
    ("nomenclature", "n.co_name_key", lambda о: ком(о, 4).update(name="supremevalves")),
    ("nomenclature", "n.co_name_latin", lambda о: ком(о, 4).update(name="alfapumpindustries")),
    ("nomenclature", "n.co_name_noent", lambda о: ком(о, 4).update(ent=None)),
    ("nomenclature", "n.co_name_domain", lambda о: ком(о, 4).update(name="firm4.ru")),
    ("nomenclature", "n.co_ent_noname", lambda о: ком(о, 4).update(name=None)),
    ("nomenclature", "n.co_ent_missing", lambda о: ком(о, 4).update(ent=номер(500))),
    ("nomenclature", "n.co_name_dup", lambda о: ком(о, 4).update(name=ИМЕНА[5])),
    ("nomenclature", "n.co_counts", lambda о: ком(о, 4).update(parts=9)),
    ("nomenclature", "n.co_brand_digits", lambda о: ком(о, 4).update(brands=["340"])),
    ("nomenclature", "n.co_brand_case", lambda о: ком(о, 4).update(brands=["SKF", "Skf"])),
    ("nomenclature", "n.co_oem_len", lambda о: ком(о, 4).update(oem=[f"Марка{i}" for i in range(11)])),
    ("nomenclature", "n.k_fmt", lambda о: поз(о, 3).update(k="RX-7731")),
    ("nomenclature", "n.k_class", lambda о: поз(о, 3).update(k="ss316")),
    ("nomenclature", "n.k_desc", lambda о: поз(о, 3).update(k="втулканаправляющаядлянасосатипаабв1")),
    ("nomenclature", "n.k_nodigit", lambda о: поз(о, 3).update(k="komplekt")),
    ("nomenclature", "n.k_short_num", lambda о: поз(о, 3).update(k="12")),
    ("nomenclature", "n.k_dup", lambda о: поз(о, 3).update(k=ключ(КОДЫ[4][0]))),
    ("nomenclature", "n.k_bucket", lambda о: поз(о, 3).update(b=(поз(о, 3)["b"] + 1) % 32)),
    ("nomenclature", "n.n_empty", lambda о: поз(о, 3).pop("n")),
    ("nomenclature", "n.n_class", lambda о: поз(о, 3).update(n="12.09.2026")),
    ("nomenclature", "n.n_words", lambda о: поз(о, 3).update(n="втулка направляющая для насоса")),
    ("nomenclature", "n.n_key", lambda о: поз(о, 3).update(n="RX-7732")),
    ("nomenclature", "n.name_empty", lambda о: поз(о, 3).pop("name")),
    ("nomenclature", "n.name_glued", lambda о: поз(о, 3).update(name="Втулка 8 3200.0 шт 25600.0")),
    ("nomenclature", "n.name_is_code", lambda о: поз(о, 3).update(name="RX 7731")),
    ("nomenclature", "n.name_prose", lambda о: поз(о, 3).update(name="Неустойка за просрочку поставки начисляется")),
    ("nomenclature", "n.name_long", lambda о: поз(о, 3).update(name="Втулка " * 40)),
    ("nomenclature", "n.dem_qty", lambda о: поз(о, 3)["demand"].update(qty=3163518182.316)),
    ("nomenclature", "n.dem_units", lambda о: поз(о, 3)["demand"].update(units=2)),
    ("nomenclature", "n.dem_rows", lambda о: поз(о, 3)["demand"].update(rows=1)),
    ("nomenclature", "n.dem_glued", lambda о: поз(о, 3)["demand"].update(deals=73, rows=90) or поз(о, 3).update(k="ss316")),
    ("nomenclature", "n.dem_unit", lambda о: поз(о, 3)["demand"].update(unit_name="12")),
    ("nomenclature", "n.off", lambda о: поз(о, 3).update(co=3)),
    ("nomenclature", "n.e_index", lambda о: поз(о, 3)["e"].append(list(поз(о, 3)["e"][0]))),
    ("nomenclature", "n.e_cnt", lambda о: поз(о, 3)["e"][0].__setitem__(1, 0)),
    ("nomenclature", "n.e_cur", lambda о: поз(о, 3)["e"][0].__setitem__(3, None)),
    ("nomenclature", "n.e_date", lambda о: поз(о, 3)["e"][0].__setitem__(4, "01.09.2026")),
    ("nomenclature", "n.co_zero", lambda о: (поз(о, 3).update(co=0), поз(о, 3).pop("e"))),
    ("nomenclature", "n.cmp_co", lambda о: поз(о, 3).update(cmp=True)),
    ("nomenclature", "n.cmp_basket", lambda о: подр(о, 0)["list"][1].update(u="EUR")),
    ("nomenclature", "n.cat_empty", lambda о: (поз(о, 0).pop("oem_cat"), поз(о, 0).pop("alts"), поз(о, 0).pop("models"),
                                              подр(о, 0).pop("cat_name"))),
    ("nomenclature", "n.cat_contra", lambda о: поз(о, 7).update(models=["Насос НМ-1250"])),
    ("nomenclature", "n.oem_cat", lambda о: поз(о, 0).update(oem_cat="Китай")),
    ("nomenclature", "n.oem_file", lambda о: поз(о, 3).update(oem_file=["не указан"])),
    ("nomenclature", "n.oem_file_legal", lambda о: поз(о, 3).update(oem_file=["ООО Ромашка"])),
    ("nomenclature", "n.oem_file_case", lambda о: поз(о, 3).update(oem_file=["FAG", "Fag"])),
    ("nomenclature", "n.brands_digits", lambda о: поз(о, 3).update(brands=["1138"])),
    ("nomenclature", "n.brands_case", lambda о: поз(о, 3).update(brands=["FAG", "fag"])),
    ("nomenclature", "n.brands_unknown", lambda о: поз(о, 3).update(brands=["Timken"])),
    ("nomenclature", "n.alt_kind", lambda о: поз(о, 0)["alts"][0].update(kind="похожий")),
    ("nomenclature", "n.alt_self", lambda о: поз(о, 0)["alts"][0].update(pn="NU-316")),
    ("nomenclature", "n.alt_dup", lambda о: поз(о, 0)["alts"].append(dict(поз(о, 0)["alts"][0]))),
    ("nomenclature", "n.alt_maker", lambda о: поз(о, 0)["alts"][0].update(maker="4471")),
    ("nomenclature", "n.alt_class", lambda о: поз(о, 0)["alts"][0].update(pn="AISI 304")),
    ("nomenclature", "n.models_id", lambda о: поз(о, 0).update(models=["gtu_16p"])),
    ("nomenclature", "n.models_dup", lambda о: поз(о, 0).update(models=["Насос НМ-1250", "насос нм-1250"])),
    ("nomenclature", "n.models_many", lambda о: поз(о, 0).update(models=[f"Насос НМ-{i}" for i in range(21)])),
    ("nomenclature", "n.b_shown", lambda о: подр(о, 0).update(shown=5)),
    ("nomenclature", "n.o_c", lambda о: подр(о, 0)["list"][0].update(c="9999")),
    ("nomenclature", "n.o_e", lambda о: подр(о, 0)["list"][0].update(e=номер(40))),
    ("nomenclature", "n.o_price", lambda о: подр(о, 0)["list"][0].update(p=5e8, t=5e9)),
    ("nomenclature", "n.o_cur", lambda о: подр(о, 0)["list"][0].update(u="РУБ")),
    ("nomenclature", "n.o_pu", lambda о: подр(о, 1)["list"][0].pop("u")),
    ("nomenclature", "n.o_qty", lambda о: подр(о, 0)["list"][0].update(q=2e6, t=None)),
    ("nomenclature", "n.o_triple", lambda о: подр(о, 0)["list"][0].update(t=1.0)),
    ("nomenclature", "n.o_unit", lambda о: подр(о, 0)["list"][0].update(n="10")),
    ("nomenclature", "n.o_frac", lambda о: подр(о, 0)["list"][0].update(q=2.5, t=подр(о, 0)["list"][0]["p"] * 2.5)),
    ("nomenclature", "n.o_total", lambda о: подр(о, 0)["list"][0].update(q=None, t=1.0)),
    ("nomenclature", "n.o_t_noq", lambda о: подр(о, 0)["list"][0].update(q=None, t=подр(о, 0)["list"][0]["p"] * 2.5)),
    ("nomenclature", "n.o_r", lambda о: подр(о, 0)["list"][0].update(r="нсс?")),
    ("nomenclature", "n.o_basis", lambda о: подр(о, 0)["list"][0].update(s="самовывоз")),
    ("nomenclature", "n.o_days", lambda о: подр(о, 0)["list"][0].update(l=900)),
    ("nomenclature", "n.o_adv", lambda о: подр(о, 0)["list"][0].update(a=130.0)),
    ("nomenclature", "n.o_pay_long", lambda о: подр(о, 0)["list"][0].update(y="оплата " * 40)),
    ("nomenclature", "n.o_b_digits", lambda о: подр(о, 0)["list"][0].update(b=["1138"])),
    ("nomenclature", "n.o_m", lambda о: подр(о, 0)["list"][0].update(m="ООО Лютик")),
    ("nomenclature", "n.o_date", lambda о: подр(о, 0)["list"][0].update(d="2012-01-01")),
    ("nomenclature", "n.o_dup", lambda о: подр(о, 0)["list"].append(dict(подр(о, 0)["list"][0]))),
    ("nomenclature", "n.o_repeat", lambda о: подр(о, 0)["list"].extend(
        dict(подр(о, 0)["list"][0], d=f"2026-08-0{i}") for i in range(1, 4))),
    ("nomenclature", "n.o_f", lambda о: подр(о, 0)["list"][0].update(f="bitrix:5001")),
    ("nomenclature", "n.mk", lambda о: подр(о, 0)["makers"][0].update(role="хозяин")),
    ("nomenclature", "n.mk_nocat", lambda о: подр(о, 7).update(makers=[{"name": "SKF", "role": "OEM"}])),
    ("nomenclature", "n.link_k", lambda о: указ(о, 0).__setitem__(0, "zz9999")),
    ("nomenclature", "n.brands_age", lambda о: о[brands.КЛЮЧ].update(published_at="2026-09-21T00:00:00Z")),
    # ── /brands
    ("brands", "b.keys", _удалить(brands.КЛЮЧИ_КОРЗИН[3])),
    ("brands", "b.published", lambda о: о[brands.КЛЮЧ].update(published_at=None)),
    ("brands", "b.age", lambda о: [о[k].update(published_at="2026-09-20T01:07:00Z") for k in brands.ВСЕ_КЛЮЧИ]),
    ("brands", "b.part_date", lambda о: _сдвинуть_дату(о, brands.КЛЮЧ_ПАР, "2026-09-23T01:07:00Z")),
    ("brands", "b.tiles", lambda о: о[brands.КЛЮЧ]["totals"]["tiles"][0].update(value=None)),
    ("brands", "b.tile_order", lambda о: next(t for t in о[brands.КЛЮЧ]["totals"]["tiles"]
                                              if t["id"] == "brand").update(value=500)),
    ("brands", "b.tile_zero", lambda о: next(t for t in о[brands.КЛЮЧ]["totals"]["tiles"]
                                             if t["id"] == "all").update(value=0)),
    ("brands", "b.tile_kp_links", lambda о: next(t for t in о[brands.КЛЮЧ]["totals"]["tiles"]
                                                 if t["id"] == "kp").update(value=60)),
    ("brands", "b.locale", lambda о: о[brands.КЛЮЧ]["totals"].update(locale_ok=False)),
    ("brands", "b.rows_kp", lambda о: next(r for r in о[brands.КЛЮЧ]["totals"]["rows"]
                                           if r[1] == "кодов с ценой КП").__setitem__(2, 99)),
    ("brands", "b.rows_noname", lambda о: next(r for r in о[brands.КЛЮЧ]["totals"]["rows"]
                                               if "поставщик не указан" in r[1]).__setitem__(2, 40)),
    ("brands", "b.cov_recount", lambda о: о[brands.КЛЮЧ]["coverage"]["undefined"].update(brands=7)),
    ("brands", "b.cov_card_all", lambda о: (о[brands.КЛЮЧ]["card_brands"][0].pop("name"),
                                            о[brands.КЛЮЧ]["coverage"]["undefined"].update(card_brands_without_name=1))),
    ("brands", "b.cov_nokey", lambda о: ([b.update(dict=False) for b in о[brands.КЛЮЧ]["brands"]],
                                         о[brands.КЛЮЧ]["coverage"]["undefined"].update(brands_without_dict_key=2))),
    ("brands", "b.cov_noname", lambda о: ([s.update({"from": "имени нет ни в базе, ни в Битриксе"})
                                           for s in о[brands.КЛЮЧ]["suppliers"]],
                                          о[brands.КЛЮЧ]["coverage"]["undefined"].update(
                                              suppliers_without_name=len(о[brands.КЛЮЧ]["suppliers"])))),
    ("brands", "b.name_key", lambda о: бр(о, "fag").update(k="schaefflergroup", name="schaefflergroup")),
    ("brands", "b.k_desc", lambda о: бр(о, "fag").update(name="Любой изготовитель подшипников по типу и размеру")),
    ("brands", "b.name_digits", lambda о: бр(о, "fag").update(name="1138, 340")),
    ("brands", "b.name_has_digit", lambda о: бр(о, "fag").update(name="FAG 6205")),
    ("brands", "b.name_cell", lambda о: бр(о, "fag").update(name="SKF/FAG")),
    ("brands", "b.name_junk", lambda о: бр(о, "fag").update(name="Прочие")),
    ("brands", "b.name_empty", lambda о: бр(о, "fag").update(name="")),
    ("brands", "b.name_dup", lambda о: бр(о, "fag").update(name="skf")),
    ("brands", "b.unmerged", lambda о: (бр(о, "fag")["spellings"].append("Schaeffler"),
                                        о[brands.КЛЮЧ]["brands"].append({"k": "schaeffler", "name": "Schaeffler",
                                                                          "dict": False}))),
    ("brands", "b.k_dup", lambda о: о[brands.КЛЮЧ]["brands"].append(dict(бр(о, "fag")))),
    ("brands", "b.codes", lambda о: бр(о, "skf")["codes"].update(customer_kp=9)),
    ("brands", "b.plausible_low", lambda о: бр(о, "skf")["codes"].update(plausible=1)),
    ("brands", "b.sups_pairs", lambda о: бр(о, "skf").update(sups=5)),
    ("brands", "b.asked", lambda о: бр(о, "skf").update(priced=60)),
    ("brands", "b.spell", lambda о: бр(о, "skf").update(spellings=["SKF", "не указан"], spellings_n=2)),
    ("brands", "b.spell_shared", lambda о: бр(о, "fag").update(spellings=["FAG", "Skf"], spellings_n=2)),
    ("brands", "b.atlas", lambda о: бр(о, "skf")["atlas"].update(name="Timken")),
    ("brands", "b.models", lambda о: бр(о, "skf").update(models_n=0)),
    ("brands", "b.models_shared", lambda о: бр(о, "fag")["models"][0].update(id="m1")),
    ("brands", "b.units", lambda о: бр(о, "skf")["units"]["part"]["list"][0].update(crit="Z")),
    ("brands", "b.units_undef", lambda о: бр(о, "skf")["units"]["part"].update(undefined=5)),
    ("brands", "b.parts_unit", lambda о: бр(о, "skf")["parts"].update(unit=11)),
    ("brands", "b.alts_trunc", lambda о: бр(о, "skf")["alts"].update(makers_n=30)),
    ("brands", "b.alts_maker", lambda о: бр(о, "skf")["alts"]["makers"].append(["4471", 1])),
    ("brands", "b.chain", lambda о: бр(о, "skf")["chain"].update(n=3)),
    ("brands", "b.chain_proven", lambda о: бр(о, "skf")["chain"].update(proven=0)),
    ("brands", "b.channel", lambda о: бр(о, "skf")["channel"].update(checked="2025-01-01")),
    ("brands", "b.registry_trunc", lambda о: бр(о, "skf")["registry"].update(n=26)),
    ("brands", "b.registry", lambda о: бр(о, "skf")["registry"]["list"][0].update(checked=9)),
    ("brands", "b.card", lambda о: бр(о, "skf")["card"].append({"id": "9999", "codes": 1})),
    ("brands", "b.s_name_key", lambda о: пост(о, 0).update(name="bitrix:1001")),
    ("brands", "b.s_name_json", lambda о: пост(о, 0).update(name='["ООО Ромашка", "Ромашка"]')),
    ("brands", "b.s_name_number", lambda о: пост(о, 0).update(name="Компания портала 1001")),
    ("brands", "b.s_k_fmt", lambda о: пост(о, 0).update(k="romashka")),
    ("brands", "b.s_dup", lambda о: пост(о, 0).update(name="АО " + пост(о, 1)["name"][4:])),
    ("brands", "b.s_reg", lambda о: пост(о, 0).update({"from": "реестр", "reg": "romashka"})),
    ("brands", "b.s_keys", lambda о: пост(о, 0).update(keys=["bitrix:1001"])),
    ("brands", "b.s_counts", lambda о: пост(о, 0).update(codes=9)),
    ("brands", "b.s_cur_case", lambda о: пост(о, 0).update(cur=["usd", "USD"])),
    ("brands", "b.s_cur_iso", lambda о: пост(о, 0).update(cur=["руб"])),
    ("brands", "b.s_domain", lambda о: пост(о, 0).update(domains=["info@firm0.example"])),
    ("brands", "b.s_codes_zero", lambda о: пост(о, 0).update(codes=0)),
    ("brands", "b.cb_all_noname", lambda о: о[brands.КЛЮЧ]["card_brands"][0].pop("name")),
    ("brands", "b.cb", lambda о: о[brands.КЛЮЧ]["card_brands"][0].update(name="1138")),
    ("brands", "b.cb_link", lambda о: о[brands.КЛЮЧ]["card_brands"][0].update(k="timken")),
    ("brands", "b.cov_table", lambda о: о[brands.КЛЮЧ]["coverage"]["universes"]["all"]["fields"][0].update(pct=99.0)),
    ("brands", "b.fields_src", lambda о: о[brands.КЛЮЧ]["fields"].pop(0)),
    ("brands", "b.dict", lambda о: о[brands.КЛЮЧ]["dict"].update(records=0)),
    ("brands", "b.c_class", lambda о: указ(о, 0).__setitem__(1, "SS316")),
    ("brands", "b.c_rubbish", lambda о: указ(о, 0).__setitem__(1, "12.09.2026")),
    ("brands", "b.c_short", lambda о: указ(о, 0).__setitem__(0, "nu3")),
    ("brands", "b.c_fmt", lambda о: указ(о, 0).__setitem__(0, "part_4471")),
    ("brands", "b.c_words", lambda о: указ(о, 0).__setitem__(1, "подшипник NU 316 роликовый")),
    ("brands", "b.c_brandkey", lambda о: указ(о, 0).__setitem__(0, "skf")),
    ("brands", "b.c_long", lambda о: указ(о, 0).__setitem__(0, "a1" * 14)),
    ("brands", "b.c_dup", lambda о: о[brands.КЛЮЧ_СВЯЗЕЙ]["codes"].append(list(указ(о, 0)))),
    ("brands", "b.c_written", lambda о: указ(о, 0).__setitem__(1, "NU 317")),
    ("brands", "b.c_part", lambda о: указ(о, 0).__setitem__(2, (указ(о, 0)[2] + 1) % 16)),
    ("brands", "b.c_orphan", lambda о: о[brands.КЛЮЧИ_КОРЗИН[0]]["codes"].update({"zz0001": {"n": "ZZ-0001"}})),
    ("brands", "b.c_brand_idx", lambda о: указ(о, 0).__setitem__(3, [0, 0])),
    ("brands", "b.c_sup_idx", lambda о: указ(о, 0).__setitem__(4, [99])),
    ("brands", "b.c_sups", lambda о: код_бр(о, 0).update(sups=7)),
    ("brands", "b.c_asked", lambda о: указ(о, 0).__setitem__(5, 0)),
    ("brands", "b.l_lists", lambda о: о[brands.КЛЮЧ_СВЯЗЕЙ]["brands"].append("fag")),
    ("brands", "b.p_fields", lambda о: о[brands.КЛЮЧ_ПАР]["pair_fields"].reverse()),
    ("brands", "b.p_idx", lambda о: о[brands.КЛЮЧ_ПАР]["pairs"][0].__setitem__(1, 99)),
    ("brands", "b.p_dup", lambda о: о[brands.КЛЮЧ_ПАР]["pairs"].append(list(о[brands.КЛЮЧ_ПАР]["pairs"][0]))),
    ("brands", "b.p_counts", lambda о: о[brands.КЛЮЧ_ПАР]["pairs"][0].__setitem__(3, 5)),
    ("brands", "b.p_asked", lambda о: о[brands.КЛЮЧ_ПАР]["pairs"][0].__setitem__(10, 9)),
    ("brands", "b.p_cur", lambda о: о[brands.КЛЮЧ_ПАР]["pairs"][0].__setitem__(9, "usd два")),
    ("brands", "b.p_nosup", lambda о: (о[brands.КЛЮЧ]["suppliers"].append({"k": "(не указан)", "name": "Поставщик на карточке не указан",
                                                                         "from": "на карточке не указан"}),
                                       о[brands.КЛЮЧ_ПАР]["suppliers"].append("(не указан)"),
                                       о[brands.КЛЮЧ_ПАР]["pairs"].append(
                                           [0, len(о[brands.КЛЮЧ_ПАР]["suppliers"]) - 1, 1, 0, 0, 0, 0, 1, 1, "USD 1", 0]))),
    ("brands", "b.k_name", lambda о: код_бр(о, 0).update(name="SS316")),
    ("brands", "b.k_n", lambda о: код_бр(о, 0).update(n="NU-316")),
    ("brands", "b.k_asked", lambda о: код_бр(о, 0).update(deals=0)),
    ("brands", "b.k_bs", lambda о: код_бр(о, 0).update(bsk=["skf", "fag"])),
    ("brands", "b.o_s", lambda о: код_бр(о, 0)["offers"][0].update(s="bitrix:77")),
    ("brands", "b.o_single_noname", lambda о: (код_бр(о, 1).update(offers=[dict(код_бр(о, 1)["offers"][0], s="(не указан)")],
                                                                   sups=0))),
    ("brands", "b.o_cur", lambda о: код_бр(о, 0)["offers"][0].update(cur="руб")),
    ("brands", "b.o_unit", lambda о: код_бр(о, 0)["offers"][0].update(unit="(не указана)")),
    ("brands", "b.o_minmax", lambda о: код_бр(о, 0)["offers"][0].update(min=20.0)),
    ("brands", "b.o_spread", lambda о: код_бр(о, 0)["offers"][0].update(max=5000.0)),
    ("brands", "b.o_suspect", lambda о: код_бр(о, 0)["offers"][0].update(min=2024.0, med=2024.0, max=2024.0, rows=1)),
    ("brands", "b.o_null", lambda о: [o.update(min=None, med=None, max=None) for o in код_бр(о, 1)["offers"]]),
    ("brands", "b.o_tot_bad", lambda о: код_бр(о, 0)["offers"][0].update(tot_bad=1, tot_ok=1)),
    ("brands", "b.o_rows", lambda о: код_бр(о, 0)["offers"][0].update(low=5)),
    ("brands", "b.o_marks", lambda о: [o.update(from_total=1) for c in range(6) for o in код_бр(о, c)["offers"]]),
    ("brands", "b.o_qty", lambda о: код_бр(о, 0)["offers"][0].update(qty=0.5)),
    ("brands", "b.o_basis", lambda о: код_бр(о, 0)["offers"][0].update(basis="доставка до склада покупателя")),
    ("brands", "b.o_dates", lambda о: код_бр(о, 0)["offers"][0].update(d1="2026-09-10", d2="2026-09-01")),
    ("brands", "b.o_dsrc", lambda о: код_бр(о, 0)["offers"][0].update(dsrc="запись разбора (переразбор ставит новую)")),
    ("brands", "b.o_br", lambda о: код_бр(о, 0)["offers"][0].update(brk=["skf", "fag"])),
    ("brands", "b.o_card", lambda о: код_бр(о, 0)["offers"][0].update(card=["9999"])),
    ("brands", "b.o_card_num", lambda о: о[brands.КЛЮЧ]["card_brands"][0].pop("name")),
    ("brands", "b.x_e", lambda о: пост(о, 0).update(k="KV-S-888888-" + str(luhn("888888")))),
    ("brands", "b.x_k", lambda о: указ(о, 0).__setitem__(0, "zz9999")),
    # ── /counters
    ("counters", "c.present", _удалить("counters:v1")),
    ("counters", "c.version", lambda о: о["counters:v1"].update(version=2)),
    ("counters", "c.dropped", lambda о: о["counters:v1"].update(dropped=3)),
    ("counters", "c.size", lambda о: точка(о).update(note="x" * 150_000)),
    ("counters", "c.metric", lambda о: о["counters:v1"]["metrics"].pop("коды_и_цены")),
    ("counters", "c.cut", lambda о: о["counters:v1"]["metrics"].update(прочее=[
        {"run": str(36000000100 + i), "at": f"2025-01-01T00:{i // 60:02d}:{i % 60:02d}Z", "nums": {}}
        for i in range(400)])),
    ("counters", "c.dup", lambda о: точка(о).update(run=точка(о, i=0)["run"])),
    ("counters", "c.order", lambda о: о["counters:v1"]["metrics"]["коды_и_цены"].reverse()),
    ("counters", "c.at", lambda о: точка(о).update(at="2026-09-24 01:30")),
    ("counters", "c.fresh", lambda о: точка(о).update(at="2026-09-23T02:30:00Z")),
    ("counters", "c.same_day", lambda о: точка(о, i=0).update(at="2026-09-24T00:30:00Z")),
    ("counters", "c.run", lambda о: точка(о).update(run="руками")),
    ("counters", "c.note", lambda о: точка(о).update(note="см. сделку № 23008")),
    ("counters", "c.note_needed", lambda о: точка(о)["nums"].update(asked=2000, no_price=1600)),
    ("counters", "c.with_kp_drop", lambda о: точка(о)["nums"].update(with_kp=280, price_asked=280, no_price=620,
                                                                   price_not_asked=520)),
    ("counters", "c.fields", lambda о: точка(о)["nums"].pop("catalog_asked")),
    ("counters", "c.int", lambda о: точка(о)["nums"].update(no_digit=6.5)),
    ("counters", "c.inv_asked", lambda о: точка(о)["nums"].update(no_price=700)),
    ("counters", "c.asked_zero", lambda о: точка(о)["nums"].update(asked=0, with_kp=0, price_asked=0, other_feed=0,
                                                                  no_price=0)),
    ("counters", "c.rows", lambda о: точка(о)["nums"].update(rows_customer=4999)),
    ("counters", "c.with_kp", lambda о: точка(о)["nums"].update(price_asked=301)),
    ("counters", "c.no_price", lambda о: точка(о)["nums"].update(rows_without=500)),
    ("counters", "c.other_feed", lambda о: точка(о)["nums"].update(price_codes_any=300)),
    ("counters", "c.price", lambda о: точка(о)["nums"].update(price_rows=10)),
    ("counters", "c.catalog", lambda о: точка(о)["nums"].update(catalog_priced=151)),
    ("counters", "c.catalog_scale", lambda о: точка(о)["nums"].update(catalog=0, catalog_priced=0, catalog_asked=0)),
    ("counters", "c.plausible", lambda о: точка(о)["nums"].update(plausible=1001)),
    ("counters", "c.inc_fresh", lambda о: точка(о, "инкремент_сделки").update(at="2026-09-22T02:00:00Z")),
    ("counters", "c.inc_start", lambda о: точка(о, "инкремент_сделки")["nums"].update(начало=1990000000)),
    ("counters", "c.after_id", lambda о: точка(о, "инкремент_сделки")["nums"].update(после_id=10)),
    ("counters", "c.mail", lambda о: точка(о, "почта:сделки")["nums"].update(писем=-1)),
    ("counters", "c.brand_reg", lambda о: точка(о, "реестр_брендов")["nums"].update({"кп.1.разрешено": 95})),
    ("counters", "c.brand_reg_drop", lambda о: точка(о, "реестр_брендов")["nums"].update({"кп.1.разрешено": 50})),
    # ── /library
    ("library", "l.present", lambda о: о.update(_drop_library=True)),
    ("library", "l.seg_name", lambda о: о["_segments"][0].update(name="bearings")),
    ("library", "l.seg_dup", lambda о: о["_segments"][1].update(name="Подшипники")),
    ("library", "l.seg_deadend", lambda о: о.update(_library_rows=[r for r in о["_library_rows"]
                                                                  if not (r["segment_id"] == "pumps"
                                                                          and r["sources"]["kind"] in ("supplier", "price"))],
                                                    _relations={k: v for k, v in о["_relations"].items()
                                                                if "pumps" not in k})),
    ("library", "l.seg_empty", lambda о: о["_segments"].append({"id": "valves", "name": "Арматура", "note": "Краны"})),
    ("library", "l.conf_keys", lambda о: ст(о, "bearings:k1").update(confidence="почти")),
    ("library", "l.conf_hidden", lambda о: ст(о, "bearings:k1").update(confidence="hypothesis")),
    ("library", "l.updated", lambda о: ст(о, "bearings:k1").update(updated_at="2026-09-30T00:00:00Z")),
    ("library", "l.title", lambda о: ст(о, "bearings:k1").update(title="bitrix:2002")),
    ("library", "l.title_pn", lambda о: ст(о, "bearings:c1").update(title="SKF NU 316")),
    ("library", "l.desc", lambda о: (поля(о, "bearings:c2").pop("name"), ст(о, "bearings:c2").update(title="PL-2280-S"))),
    ("library", "l.desc_hint", lambda о: (поля(о, "bearings:c2").pop("name"), ст(о, "bearings:c2").update(title="PL-2280-S"))),
    ("library", "l.topic", lambda о: ст(о, "bearings:k1").update(topic="bearing_replacement_history")),
    ("library", "l.body", lambda о: ст(о, "bearings:k1").update(body="Опыт замены подшипника")),
    ("library", "l.body_junk", lambda о: ст(о, "bearings:k1").update(body="Срок: undefined")),
    ("library", "l.body_table", lambda о: ст(о, "bearings:k1").update(body="| a | b |\n|---|---|\n| 1 |\n")),
    ("library", "l.body_links", lambda о: ст(о, "bearings:k1").update(body="См. [файл](file:///c:/docs/a.pdf)")),
    ("library", "l.conf", lambda о: ст(о, "bearings:k1").update(confidence="проверено")),
    ("library", "l.conf_norefs", lambda о: ст(о, "bearings:k1")["sources"].update(references=[])),
    ("library", "l.pn_class", lambda о: поля(о, "bearings:c2").update(part_number="AISI 304")),
    ("library", "l.pn_bad", lambda о: поля(о, "bearings:c2").update(part_number="1.2.3")),
    ("library", "l.pn_oem", lambda о: поля(о, "bearings:c2").update(oem="PL-2280-S")),
    ("library", "l.pn_words", lambda о: поля(о, "bearings:c2").update(part_number="Кольцо уплотнительное резиновое 22")),
    ("library", "l.pn_dup", lambda о: поля(о, "bearings:c2").update(part_number="NU 316", oem="SKF")),
    ("library", "l.oem_digits", lambda о: поля(о, "bearings:c2").update(oem="1138, 340")),
    ("library", "l.oem_key", lambda о: поля(о, "bearings:c2").update(oem="schaefflergroup")),
    ("library", "l.oem_unknown", lambda о: поля(о, "bearings:c2").update(oem="не указан")),
    ("library", "l.oem_multi", lambda о: поля(о, "bearings:c2").update(oem="SKF/FAG")),
    ("library", "l.oem_queue", lambda о: поля(о, "bearings:c2").update(oem="Timken")),
    ("library", "l.oem_instruction", lambda о: (о["_dict"]["records"].append(
        {"oem_key": "заказпоспецификации", "name": "Заказ по спецификации",
         "spellings": [{"spelling": "Заказ по спецификации", "where": "dict/oem.json:records"}]}),
        поля(о, "bearings:c2").update(oem="Заказ по спецификации"))),
    ("library", "l.family", lambda о: поля(о, "bearings:c2").update(family="rubber_o_ring")),
    ("library", "l.qty", lambda о: поля(о, "bearings:c2").update(quantity=2.5)),
    ("library", "l.priced", lambda о: поля(о, "bearings:c2").update(priced=True)),
    ("library", "l.aliases", lambda о: поля(о, "bearings:c1").update(aliases=["NU-316"])),
    ("library", "l.s_name", lambda о: поля(о, "bearings:s1").update(name="SKF")),
    ("library", "l.s_dup", lambda о: поля(о, "bearings:s1").update(name="ООО Ромашка")),
    ("library", "l.s_role", lambda о: поля(о, "bearings:s1").update(role="partner")),
    ("library", "l.s_brands", lambda о: поля(о, "bearings:s1").update(oem_brands=["340"])),
    ("library", "l.p_amount", lambda о: поля(о, "bearings:p0").update(amount="12 000,50")),
    ("library", "l.p_cur", lambda о: поля(о, "bearings:p0").update(currency="руб")),
    ("library", "l.p_date", lambda о: поля(о, "bearings:p0").update(price_date="2027-01-01")),
    ("library", "l.p_type", lambda о: поля(о, "bearings:p0").update(price_type="Прайс")),
    ("library", "l.p_supplier", lambda о: поля(о, "bearings:p0").update(supplier="Не указан")),
    ("library", "l.p_pn", lambda о: поля(о, "bearings:p0").update(part_number="ZX-6604")),
    ("library", "l.p_unit", lambda о: поля(о, "bearings:p0").update(unit="компл")),
    ("library", "l.r_dangling", lambda о: ст(о, "bearings:c2")["sources"].update(library_relations={
        "version": 1, "producer": "publisher-v2", "candidate_suppliers": [
            {"article_id": "lib:bearings:k1", "relation_type": "historical_supplier_candidate", "position_id": 1,
             "json_pointer": "/items/0", "part_number": "PL-2280-S"}]})),
    ("library", "l.r_meta", lambda о: ст(о, "bearings:c2")["sources"].update(library_relations={
        "version": 2, "producer": "publisher-v2", "candidate_suppliers": []})),
    ("library", "l.managed", lambda о: ст(о, "bearings:k1")["sources"].update(publication_approved=False)),
    ("library", "l.published", lambda о: о.update(_library_published="2027-01-01T00:00:00Z")),
    ("library", "l.r_fields", lambda о: о["_relations"]["lib:bearings:c1"][0].update(json_pointer="items/0")),
    ("library", "l.r_url", lambda о: о["_relations"]["lib:bearings:c1"][0].update(source_url="https://user:pw@example.test/x")),
    ("library", "l.r_dup", lambda о: о["_relations"]["lib:bearings:c1"].append(dict(о["_relations"]["lib:bearings:c1"][0]))),
    ("library", "l.ref_label", lambda о: ст(о, "bearings:k1")["sources"]["references"].append({"page": 3})),
    ("library", "l.ref_url", lambda о: ст(о, "bearings:k1")["sources"]["references"][0].update(url="ftp://example.test/a")),
    ("library", "l.ref_dup", lambda о: ст(о, "bearings:k1")["sources"]["references"].append(
        dict(ст(о, "bearings:k1")["sources"]["references"][0]))),
    ("library", "l.ref_locator", lambda о: ст(о, "bearings:k1")["sources"]["references"][0].update(
        locator={"page": 3, "extra": {"a": 1}})),
    ("library", "l.crm_url", lambda о: ст(о, "bearings:k1")["sources"].update(crm_links=[{"url": "javascript:alert(1)"}])),
    ("library", "l.open_q", lambda о: ст(о, "bearings:k1")["sources"].update(open_questions=["Совместимость?"])),
    # ── dict/oem.json
    ("dict", "d.count", lambda о: о["_dict"].update(count=3)),
    ("dict", "d.key", lambda о: о["_dict"]["records"][1].update(oem_key="skf")),
    ("dict", "d.key_long", lambda о: о["_dict"]["records"][1].update(oem_key="fagподшипникикачениявсехтиповиразмеров")),
    ("dict", "d.key_40", lambda о: о["_dict"]["records"][1].update(oem_key="f" * 40)),
    ("dict", "d.key_cyr", lambda о: о["_dict"]["records"][1].update(oem_key="fagпотипу")),
    ("dict", "d.instruction", lambda о: о["_dict"]["records"][1].update(oem_key="любойдистрибьютор")),
    ("dict", "d.multi", lambda о: о["_dict"]["records"][1].update(name="FAG/INA")),
    ("dict", "d.key_digits", lambda о: о["_dict"]["records"][1].update(oem_key="330180330105")),
    ("dict", "d.spell_quotes", lambda о: о["_dict"]["records"][1]["spellings"].append(
        {"spelling": "«FAG»", "where": "dict/oem.json:records"})),
    ("dict", "d.spell_shared", lambda о: о["_dict"]["records"][1]["spellings"].append(
        {"spelling": "SKF", "where": "dict/oem.json:records"})),
    ("dict", "d.where", lambda о: о["_dict"]["records"][1]["spellings"][0].update(where="нет/такого.json:x")),
]

def _манифест(правка):
    """Правка манифеста библиотеки с перезаписью адреса: блобы адресуются sha256."""
    def применить(с):
        указатель = json.loads(с[pa.КЛЮЧ_БИБЛИОТЕКИ])
        манифест = json.loads(с[pa.ПРЕФИКС_БЛОБА + указатель["manifest"]["sha256"]])
        правка(манифест)
        raw = lib2.encode(манифест)
        sha = hashlib.sha256(raw).hexdigest()
        с[pa.ПРЕФИКС_БЛОБА + sha] = raw
        указатель["manifest"] = {"sha256": sha, "bytes": len(raw)}
        с[pa.КЛЮЧ_БИБЛИОТЕКИ] = lib2.encode(указатель)
    return применить


def _сегмент(м, ид):
    return next(x for x in м["segments"] if x["id"] == ид)


# Проверки, которые ловятся только правкой сырых байтов: указатель, манифест и
# деревья библиотеки строит Builder, и нарушить их инварианты можно лишь после.
МУТАЦИИ_БАЙТОВ = [
    ("library", "l.pointer", lambda с: с.update({pa.КЛЮЧ_БИБЛИОТЕКИ: json.dumps(
        {**json.loads(с[pa.КЛЮЧ_БИБЛИОТЕКИ]), "version": 3}).encode()})),
    ("library", "l.manifest", lambda с: [с.pop(k) for k in list(с) if k.startswith(pa.ПРЕФИКС_БЛОБА)
                                         and b'"segments"' in с[k]]),
    ("library", "l.blob", lambda с: [с.pop(k) for k in list(с) if k.startswith(pa.ПРЕФИКС_БЛОБА)
                                     and b'"category":"catalog"' in с[k]][:1]),
    ("library", "l.seg_count", _манифест(lambda м: м["segments"].extend(
        {"id": f"s{i}", "name": f"Сегмент {i}", "article_count": 0,
         "counts_by_kind": {k: 0 for k in lib2.KINDS}} for i in range(101)))),
    ("library", "l.seg_sum", _манифест(lambda м: м.update(article_count=м["article_count"] + 1))),
    ("library", "l.index_count", _манифест(lambda м: _сегмент(м, "pumps")["counts_by_kind"].update(price=5))),
    ("library", "l.seg_kind", _манифест(lambda м: _сегмент(м, "pumps")["indexes"].update(
        component=_сегмент(м, "bearings")["indexes"]["component"]))),
    ("library", "l.id_dup", _манифест(lambda м: _сегмент(м, "pumps")["indexes"].update(
        component=_сегмент(м, "bearings")["indexes"]["component"]))),
    ("library", "l.dir_match", _манифест(lambda м: _сегмент(м, "pumps")["indexes"]["knowledge"].update(
        catalog=None))),
]


def test_каждая_проверка_ловит_свой_дефект():
    """Все коды проверок покрыты мутацией — список закрыт."""
    покрыто = {(т, к) for т, к, _ in МУТАЦИИ + МУТАЦИИ_БАЙТОВ} | {
        ("nomenclature", "n.worse_lost"), ("nomenclature", "n.worse_brand"),
        ("nomenclature", "n.worse_price"), ("nomenclature", "n.worse_co"),
    }                                   # четыре последние — test_прошлый_снимок_считает_ухудшения
    все = set()
    for ид, словарь_ in (("suppliers", pa.ПРОВЕРКИ_ПОСТАВЩИКОВ), ("nomenclature", pa.ПРОВЕРКИ_НОМЕНКЛАТУРЫ),
                         ("brands", pa.ПРОВЕРКИ_БРЕНДОВ), ("counters", pa.ПРОВЕРКИ_СЧЁТЧИКОВ),
                         ("library", pa.ПРОВЕРКИ_БИБЛИОТЕКИ), ("dict", pa.ПРОВЕРКИ_СЛОВАРЯ)):
        все |= {(ид, к) for к in словарь_}
    assert все - покрыто == set()


def _без_библиотеки(о, сырьё):
    if о.get("_drop_library"):
        сырьё.pop(pa.КЛЮЧ_БИБЛИОТЕКИ)


@pytest.mark.parametrize("вкладка,код,правка", МУТАЦИИ, ids=[f"{т}:{к}" for т, к, _ in МУТАЦИИ])
def test_мутация(вкладка, код, правка):
    о = корпус()
    правка(о)
    сырьё = закодировать(о)
    _без_библиотеки(о, сырьё)
    с = pa.Снимки(Память(сырьё))
    т = {x.ид: x for x in pa.ревизия(с, СЕЙЧАС, о["_dict"], lib2=lib2)}[вкладка]
    п, д = т.счета[код]
    assert д > 0, f"{код}: проверено {п}, дефект не пойман"


@pytest.mark.parametrize("вкладка,код,правка", МУТАЦИИ_БАЙТОВ, ids=[f"{т}:{к}" for т, к, _ in МУТАЦИИ_БАЙТОВ])
def test_мутация_байтов(вкладка, код, правка):
    т = прогон(правка_байтов=правка)[вкладка]
    assert т.счета[код][1] > 0, код


def test_прошлый_снимок_считает_ухудшения():
    прошлое = закодировать(корпус())

    def хуже(о):
        поз(о, 0).update(brands=[], oem_file=[])
        поз(о, 0).pop("oem_cat")
        поз(о, 2)["e"] = [[r[0], r[1]] for r in поз(о, 2)["e"]]
        поз(о, 4).update(co=1)
        for часть in crossref.КЛЮЧИ_СПИСКА:
            о[часть]["positions"] = [p for p in о[часть]["positions"] if p["k"] != ключ(КОДЫ[11][0])]

    т = прогон(правка=хуже, прошлые=прошлое)["nomenclature"]
    for код in ("n.worse_lost", "n.worse_brand", "n.worse_price", "n.worse_co"):
        п, д = т.счета[код]
        assert п > 0 and д >= 1, код
    assert т.счета["n.worse_lost"][1] == 1


# ── Журнал, файлы, KV ────────────────────────────────────────────────────────

ЗАПРЕТНОЕ = ([имя for имя in ИМЕНА] + [n for n, _ in КОДЫ] + [ключ(n) for n, _ in КОДЫ]
             + [инн10(f"77{i + 1:07d}") for i in range(len(ИМЕНА))] + [f"firm{i}.example" for i in range(60)]
             + ["Ромашка", "Тюльпан", "Подшипник", "SKF", "FAG"])


def _папка(tmp_path, сырьё):
    for k, raw in сырьё.items():
        (tmp_path / pa.имя_файла(k)).write_bytes(raw)
    return tmp_path


def test_журнал_только_агрегаты(tmp_path, capsys, monkeypatch):
    о = корпус()
    поз(о, 3).update(n="SS316")                  # дефект с образцом
    сущ(о, 2).update(inn="7700000000")           # и ещё один
    папка = _папка(tmp_path, закодировать(о))
    monkeypatch.setattr(pa, "читать_словарь", lambda: о["_dict"])
    monkeypatch.delenv("AUDIT_APPLY", raising=False)
    итог = tmp_path / "итог.json"
    assert pa.main(["--from-dir", str(папка), "--out", str(итог)]) == 0
    вывод = capsys.readouterr().out
    assert "x.pn_class" in вывод and "aa999" in вывод       # образец печатается формой
    for слово in ЗАПРЕТНОЕ:
        assert слово not in вывод, "в журнал попало значение корпуса"
    данные = итог.read_text(encoding="utf-8")
    for слово in ЗАПРЕТНОЕ:
        assert слово not in данные, "в итог попало значение корпуса"
    сводка = json.loads(данные)
    assert сводка["version"] == 1 and {t["id"] for t in сводка["tabs"]} == {
        "suppliers", "nomenclature", "brands", "counters", "library", "dict"}


def test_запись_из_папки_запрещена(tmp_path, capsys, monkeypatch):
    папка = _папка(tmp_path, закодировать(корпус()))
    monkeypatch.setenv("AUDIT_APPLY", "1")
    assert pa.main(["--from-dir", str(папка), "--only", "dict"]) == 1
    assert "не пишется" in capsys.readouterr().out


# Транспорт Cloudflare: проект Pages и значения KV из памяти.
СЧЁТ = "a" * 32
NS = "b" * 32


class _Ответ:
    def __init__(self, raw):
        self.raw, self.status = raw, 200
        self.headers = {"Content-Length": str(len(raw))}

    def read(self, n=-1):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Транспорт:
    def __init__(self, значения):
        self.значения = значения
        self.вызовы = []

    def open(self, req, timeout):
        u = parse.urlsplit(req.full_url)
        self.вызовы.append((req.get_method(), u.path))
        if u.path.endswith("/pages/projects/" + ps.PAGES_PROJECT):
            return _Ответ(json.dumps({"success": True, "result": {"deployment_configs": {"production": {
                "kv_namespaces": {"VISITS": {"namespace_id": NS}}}}}}).encode())
        ключ_ = parse.unquote(u.path.split("/values/", 1)[1])
        if req.get_method() == "PUT":
            self.значения[ключ_] = req.data
            return _Ответ(b'{"success": true, "result": null}')
        if ключ_ not in self.значения:
            raise error.HTTPError(req.full_url, 404, "нет", {}, io.BytesIO(b""))
        return _Ответ(self.значения[ключ_])


def test_чтение_kv_закрытым_списком_и_без_записи(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", СЧЁТ)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic-token")
    сырьё = закодировать(корпус())
    транспорт = _Транспорт(dict(сырьё))
    источник, модуль = pa.источник_kv(opener=транспорт, sleep=lambda s: None)
    assert источник.get("suppliers:v1") == сырьё["suppliers:v1"]
    assert источник.get(pa.КЛЮЧ_БИБЛИОТЕКИ) == сырьё[pa.КЛЮЧ_БИБЛИОТЕКИ]
    with pytest.raises(модуль.PublishError, match="INVALID_KV_KEY"):
        источник.get("acl:v1")
    with pytest.raises(модуль.PublishError, match="AUDIT_READ_ONLY"):
        источник.снимки.put(NS, "suppliers:v1", b"{}")
    with pytest.raises(Exception, match="AUDIT_READ_ONLY"):
        источник.библиотека.put(NS, pa.КЛЮЧ_БИБЛИОТЕКИ, b"{}")
    вкладки = pa.ревизия(pa.Снимки(источник), СЕЙЧАС, _словарь(), lib2=lib2)
    assert not [(т.ид, к) for т in вкладки for к, (_, д) in т.счета.items() if д]
    assert all(метод == "GET" for метод, _ in транспорт.вызовы)


def test_запись_ревизии_только_своим_ключом(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", СЧЁТ)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic-token")
    транспорт = _Транспорт({})
    pa.записать_ревизию(b'{"version":1}', ps, opener=транспорт)
    assert транспорт.значения == {pa.КЛЮЧ_РЕВИЗИИ: b'{"version":1}'}
    with pytest.raises(ps.PublishError, match="AUDIT_TOO_LARGE"):
        pa.записать_ревизию(b"x" * (pa.ПРЕДЕЛ_РЕВИЗИИ + 1), ps, opener=транспорт)


def test_словарь_репозитория_меряется():
    """На настоящем dict/oem.json ревизия проходит и что-то находит — это замер,
    а не требование нуля: словарь засорён известно как (опись 24.09.2026)."""
    т = pa.ревизия_словаря(pa.читать_словарь())
    assert т.счета["d.count"] == [1, 0]
    assert т.счета["d.where"][1] == 0
    assert т.счета["d.key_long"][1] > 0


def test_образец_не_называет_значение():
    assert pa.образец("SS316") == "aa999"
    assert pa.образец("Ду150") == "яя999"
    assert hashlib.sha256(pa.образец("NU 316").encode()).hexdigest() != hashlib.sha256(b"NU 316").hexdigest()


def test_упавшая_вкладка_не_роняет_остальные(tmp_path, capsys, monkeypatch):
    папка = _папка(tmp_path, закодировать(корпус()))
    monkeypatch.delenv("AUDIT_APPLY", raising=False)

    def падает(*_a, **_k):
        raise TypeError("синтетический сбой")

    monkeypatch.setattr(pa, "ревизия_счётчиков", падает)
    итог = tmp_path / "итог.json"
    assert pa.main(["--from-dir", str(папка), "--out", str(итог)]) == 1
    вывод = capsys.readouterr().out
    assert "ревизия упала на вкладках: counters" in вывод
    сводка = json.loads(итог.read_text(encoding="utf-8"))
    вкладки = {t["id"]: t for t in сводка["tabs"]}
    assert вкладки["counters"]["failed"] == "TypeError"
    assert вкладки["brands"]["checks"] and "failed" not in вкладки["brands"]
