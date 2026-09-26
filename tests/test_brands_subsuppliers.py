"""Карточка бренда: «Кто делает узлы» — подтверждённые субпоставщики
(library/brands.субпоставщики, ключ KV brands:subs:v1).

Проверяется:
  · из разведки идёт только verified: true, из сверки verified.json — любая
    запись; без ссылки http(s) на источник — не идёт; повтор пары
    «компания · узел» — одной строкой, сверка сильнее разведки;
  · нет файла сверки — читается одна разведка;
  · ключ бренда разведки сводится к карточке общим правилом: свой ключ,
    написание словаря (codes_sql.свести), обратный brand_key справочника рядов;
  · ссылка на реестр KV-S — только при однозначном совпадении имени;
  · ключ brands:subs:v1 = поле subs карточек, в закрытом списке и у воркера;
    на настоящих файлах разведки — в пределе.

Корпус придуман (CLAUDE.md, правило 18): марки, компании и адреса сочинены.
"""
from __future__ import annotations

import json
from pathlib import Path

from library import brands

ROOT = Path(__file__).resolve().parents[1]
ИСТ = "https://example.invalid/spec"


def _разведка(папка: Path, oem_key: str, имя: str, записи: list[dict]):
    (папка / "data" / "brand_research").mkdir(parents=True, exist_ok=True)
    (папка / "data" / "brand_research" / f"{oem_key}.json").write_text(json.dumps(
        {"oem_key": oem_key, "name": имя, "sub_suppliers": записи}, ensure_ascii=False), encoding="utf-8")


def _сверка(папка: Path, записи: list[dict]):
    (папка / "data" / "subsuppliers").mkdir(parents=True, exist_ok=True)
    (папка / "data" / "subsuppliers" / "verified.json").write_text(json.dumps(
        {"schema": 1, "records": записи, "gaps": [{"oem_key": "velmora", "component": "муфта"}]},
        ensure_ascii=False), encoding="utf-8")


def _корпус(папка: Path, со_сверкой=True):
    _разведка(папка, "velmora", "Velmora Turbines", [
        {"company": "Искрон", "component": "свечи зажигания", "machine": "VT-10, VT-20",
         "evidence": "выдумка", "sources": [ИСТ], "verified": True},
        {"company": "Черновик-Литьё", "component": "лопатки", "machine": "VT-10",
         "sources": [ИСТ], "verified": False},
        {"company": "Без Пометки", "component": "корпус", "machine": "VT-10", "sources": [ИСТ]},
        {"company": "Без Источника", "component": "фильтр", "machine": "VT-10",
         "sources": ["каталог на бумаге"], "verified": True},
        {"company": "Форсунов", "component": "топливные форсунки", "machine": "VT-20",
         "sources": [ИСТ + "/old"], "verified": True},
    ])
    # oem_key разведки не ключ карточки: имя сводится словарём.
    _разведка(папка, "pranto-research", "Pranto Pumps", [
        {"company": "Уплотнитель (Sealex)", "component": "торцевые уплотнения", "machine": "PX",
         "sources": [ИСТ], "verified": True}])
    if со_сверкой:
        _сверка(папка, [
            {"oem_key": "velmora", "brand": "Velmora Turbines", "company": "ФОРСУНОВ",
             "component": "Топливные  форсунки", "machines": ["VT-20", "VT-30"], "evidence_kind": "а",
             "quote": "выдуманная цитата", "sources": [ИСТ + "/new", "ftp://example.invalid/x"],
             "checked": "2026-09-26"},
            {"oem_key": "velmora", "brand": "Velmora Turbines", "company": "Подшипникс / Bearix",
             "component": "подшипники ротора", "machines": [], "evidence_kind": "в", "quote": "…",
             "sources": [ИСТ], "checked": "2026-09-26"}])


СЛОВАРЬ = {"records": [
    {"oem_key": "velmora", "name": "Velmora Turbines", "spellings": [{"spelling": "Velmora", "where": "выдумка"}]},
    {"oem_key": "pranto", "name": "Pranto", "spellings": [{"spelling": "Pranto Pumps", "where": "выдумка"}]},
    {"oem_key": "kordex", "name": "Kordex", "spellings": [{"spelling": "Kordex", "where": "выдумка"}]},
]}


def _коды(ключи=("velmora", "pranto", "kordex")):
    return {"brands": [{"brand_key": k, "brand": k, "codes_any": 10 + i} for i, k in enumerate(ключи)]}


def test_только_подтверждённые_и_с_источником(tmp_path):
    _корпус(tmp_path)
    прочитано = brands.читать_субпоставщиков(tmp_path)
    пары = {(r["name"], r["by"]) for r in прочитано["records"]}
    assert пары == {("Искрон", "разведка"), ("ФОРСУНОВ", "сверка а"), ("Подшипникс / Bearix", "сверка в"),
                    ("Уплотнитель (Sealex)", "разведка")}
    с = прочитано["counts"]
    assert (с["research"], с["research_verified"], с["verified_file"]) == (6, 4, 2)
    assert (с["without_source"], с["duplicates"], с["confirmed"]) == (1, 1, 4)
    # Сверка сильнее разведки: у форсунок — её машины и её источник, ftp отброшен.
    ф = next(r for r in прочитано["records"] if r["name"] == "ФОРСУНОВ")
    assert ф["m"] == "VT-20, VT-30" and ф["src"] == [ИСТ + "/new"]


def test_без_файла_сверки_читается_разведка(tmp_path):
    _корпус(tmp_path, со_сверкой=False)
    прочитано = brands.читать_субпоставщиков(tmp_path)
    assert {r["name"] for r in прочитано["records"]} == {"Искрон", "Форсунов", "Уплотнитель (Sealex)"}
    assert "verified_file" not in прочитано["counts"]
    # Пустая папка — пустой итог, без ошибки.
    assert brands.читать_субпоставщиков(tmp_path / "нет")["records"] == []


def test_функция_по_ключу_бренда(tmp_path):
    _корпус(tmp_path)
    записи = brands.читать_субпоставщиков(tmp_path)["records"]
    строки = brands.субпоставщики("velmora", записи)
    assert [x["unit"] for x in строки] == ["Подшипники ротора", "Свечи зажигания", "Топливные форсунки"]
    assert строки[1] == {"name": "Искрон", "unit": "Свечи зажигания", "m": "VT-10, VT-20",
                         "src": [ИСТ], "by": "разведка"}
    # По имени бренда тем же ключом написания; чужой бренд — пусто.
    assert [x["name"] for x in brands.субпоставщики("Pranto Pumps", записи)] == ["Уплотнитель (Sealex)"]
    assert brands.субпоставщики("kordex", записи) == []


def test_карточка_снимка_и_ключ_kv(tmp_path):
    _корпус(tmp_path)
    записи = brands.читать_субпоставщиков(tmp_path)["records"]
    снимки = brands.собрать(_коды(), {}, словарь=СЛОВАРЬ, субпоставщики_записи=записи,
                            собран="2026-09-26T00:00:00Z")
    сводка, ключ = снимки[brands.КЛЮЧ], снимки[brands.КЛЮЧ_СУБПОСТАВЩИКОВ]
    по_ключу = {b["k"]: b for b in сводка["brands"]}
    # Строки — по узлу; ключ pranto-research сведён к карточке pranto словарём.
    assert [x["unit"] for x in по_ключу["velmora"]["subs"]] == [
        "Подшипники ротора", "Свечи зажигания", "Топливные форсунки"]
    assert по_ключу["pranto"]["subs"][0]["name"] == "Уплотнитель (Sealex)"
    assert "subs" not in по_ключу["kordex"]
    assert ключ == {"version": 1, "published_at": "2026-09-26T00:00:00Z",
                    "brands": {"pranto": по_ключу["pranto"]["subs"], "velmora": по_ключу["velmora"]["subs"]}}
    assert brands.итоги_субпоставщиков(сводка) == {"brands": 2, "rows": 4, "with_registry": 0, "from_file": 2}
    # Нет записей — ключ пустой, карточки без поля.
    пусто = brands.собрать(_коды(), {}, словарь=СЛОВАРЬ)
    assert пусто[brands.КЛЮЧ_СУБПОСТАВЩИКОВ] == {"version": 1, "brands": {}}


def test_ключ_бренда_через_brand_key_справочника():
    """Разведка ведёт справочным ключом рядов (velmora), карточка — ключом словаря
    velmoraenergy с brand_key velmora: сводится, как у владения."""
    запись = {"brand": "velmora", "brand_name": "Нечто", "name": "Искрон", "unit": "свечи",
              "m": None, "src": [ИСТ], "by": "разведка"}
    бренды = {"velmoraenergy": {"k": "velmoraenergy"}}
    assert brands.ключ_карточки_субпоставщика(запись, бренды, {}, {"velmoraenergy": "velmora"}) == "velmoraenergy"
    итог = brands.субпоставщики_брендов(бренды, [запись, {**запись, "brand": "nulvo"}], {},
                                         {"velmoraenergy": "velmora"})
    assert итог == {"without_card": 1} and бренды["velmoraenergy"]["subs"][0]["unit"] == "Свечи"


def test_ссылка_на_реестр_только_однозначная(tmp_path):
    _корпус(tmp_path)
    записи = brands.читать_субпоставщиков(tmp_path)["records"]
    реестр = [("KV-S-000101-1", "ООО Искрон"), ("KV-S-000102-2", "Bearix GmbH"),
              ("KV-S-000103-3", "Форсунов"), ("KV-S-000104-4", "ФОРСУНОВ АО"),   # два кандидата
              ("не номер", "Sealex")]
    снимки = brands.собрать(_коды(), {}, словарь=СЛОВАРЬ, субпоставщики_записи=записи,
                            реестр_поставщиков=реестр)
    по_имени = {x["name"]: x.get("e") for b in снимки[brands.КЛЮЧ]["brands"] for x in b.get("subs", [])}
    assert по_имени == {"Искрон": "KV-S-000101-1", "Подшипникс / Bearix": "KV-S-000102-2",
                        "ФОРСУНОВ": None, "Уплотнитель (Sealex)": None}
    # Поставщик KV-S этого же снимка — тоже реестр.
    коды = {**_коды(), "suppliers": [{"supplier": "KV-S-000105-5", "name": "Sealex Ltd", "codes": 1}]}
    с = brands.собрать(коды, {}, словарь=СЛОВАРЬ, субпоставщики_записи=записи)[brands.КЛЮЧ]
    pranto = next(b for b in с["brands"] if b["k"] == "pranto")
    assert pranto["subs"][0]["e"] == "KV-S-000105-5"


def test_ключ_в_закрытом_списке_и_у_воркера():
    assert brands.КЛЮЧ_СУБПОСТАВЩИКОВ == "brands:subs:v1" and brands.КЛЮЧ_СУБПОСТАВЩИКОВ in brands.ВСЕ_КЛЮЧИ
    текст = (ROOT / "public" / "_worker.js").read_text(encoding="utf-8")
    assert f'const BRANDS_SUBS_KEY = "{brands.КЛЮЧ_СУБПОСТАВЩИКОВ}"' in текст
    assert f"const BRANDS_SUBS_MAX_BYTES = {brands.ПРЕДЕЛ_СУБПОСТАВЩИКОВ // 1024} * 1024;" in текст


def test_на_настоящих_файлах_в_пределе_и_только_подтверждённые():
    """Верхняя граница: карточка на каждый бренд разведки — ключ в пределе, и
    ни одной строки из неподтверждённой записи разведки."""
    прочитано = brands.читать_субпоставщиков()
    записи = прочитано["records"]
    assert записи, "в файлах разведки нет ни одной подтверждённой записи"
    ключи = sorted({r["brand"] for r in записи})
    снимки = brands.собрать(
        {"brands": [{"brand_key": k, "brand": k, "codes_any": 1} for k in ключи]}, {},
        словарь={"records": [{"oem_key": k, "name": k, "spellings": [{"spelling": k, "where": "проверка"}]}
                             for k in ключи]},
        субпоставщики_записи=записи, собран="2026-09-26T00:00:00Z")
    сырой = json.dumps(снимки[brands.КЛЮЧ_СУБПОСТАВЩИКОВ], ensure_ascii=False, separators=(",", ":")).encode()
    assert len(сырой) <= brands.ПРЕДЕЛ_СУБПОСТАВЩИКОВ, len(сырой)
    for r in записи:
        assert r["src"] and all(u.startswith(("http://", "https://")) for u in r["src"])
        assert r["by"] in brands.ОСНОВАНИЯ_СУБПОСТАВЩИКА
