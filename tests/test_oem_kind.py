"""Вид записи словаря брендов (library/oem_kind.py) и его читатели.

Проверяется то, ради чего вид заводился (жалоба владельца 25.09.2026 на
«странные бренды»): запись-указание, запись с несколькими брендами, описание и
номер детали не выдают себя за марку ни на странице /brands, ни в реестре
брендов, ни в ревизии, — и при этом ни одно написание настоящего бренда не
теряет свою марку (CLAUDE.md, правило 0: «у скольких стало хуже»).

Корпус придуман (правило 18): Kelton, Vortexa, Brisko, Grifon — сочинены.
Последние тесты гоняют правило по настоящему dict/oem.json.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

from library import brand_registry as br
from library import brands, codes_sql, oem_kind

ROOT = Path(__file__).resolve().parents[1]


def _модуль(имя, путь):
    spec = importlib.util.spec_from_file_location(имя, ROOT / путь)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _запись(ключ, имя, *написания):
    return {"oem_key": ключ, "name": имя,
            "spellings": [{"spelling": н, "where": "выдумка:a"} for н in (имя, *написания)]}


# Записи в том виде, в каком их собирает build_dict.build_oem до вида.
СЫРЬЁ = [
    _запись("kelton", "Kelton GmbH", "Kelton", "KELTON"),
    _запись("vortexa", "Vortexa"),
    _запись("brisko", "Brisko"),
    _запись("келтон", "Келтон"),                        # транслитерация — отдельное русское имя
    _запись("востокмаш", "Восток-Маш"),                  # «сток» внутри слова — не указание
    _запись("энергоремонт", "Энергоремонт"),             # «ремонт» внутри слова — не указание
    _запись("keltonvortexa", "Kelton, Vortexa"),         # несколько: оба — бренды словаря
    _запись("keltongrifonholdings", "Kelton / Grifon Holdings"),   # «/» без доказательства
    _запись("keltonпопрофилю", "Kelton (по профилю)"),   # описание: голова — бренд
    _запись("keltonгермания", "Kelton, ГЕРМАНИЯ"),        # описание: страна
    _запись("vortexagrifonтип", "Vortexa / Grifon (тип.)"),   # несколько с пояснением
    _запись("заказпоспецификациикольцопогабариту", "Заказ по спецификации: кольцо по габариту"),
    _запись("keltonпотипу", "Kelton (по типу)"),         # указание: марка — пример
    _запись("типовыеkeltonvortexaпоформату", "Типовые: Kelton/Vortexa — по формату"),
    _запись("445566778899sensor", "445566/778899 (sensor)"),   # номер
    _запись("keltonмодульkx445566", "Kelton (модуль KX445566)"),   # описание, а не номер
    _запись("grifonsealsuk", "Grifon Seals (UK)"),        # страна, голова не бренд — защищена
]

ОЖИДАНИЕ = {
    "kelton": ("бренд", None), "vortexa": ("бренд", None), "brisko": ("бренд", None),
    "келтон": ("бренд", None), "востокмаш": ("бренд", None), "энергоремонт": ("бренд", None),
    "keltonvortexa": ("несколько", ["kelton", "vortexa"]),
    "keltongrifonholdings": ("бренд", None),
    "keltonпопрофилю": ("описание", ["kelton"]),
    "keltonгермания": ("описание", ["kelton"]),
    "vortexagrifonтип": ("несколько", ["vortexa"]),
    "заказпоспецификациикольцопогабариту": ("указание", None),
    "keltonпотипу": ("указание", ["kelton"]),
    "типовыеkeltonvortexaпоформату": ("указание", ["kelton", "vortexa"]),
    "445566778899sensor": ("номер", None),
    "keltonмодульkx445566": ("описание", ["kelton"]),
    "grifonsealsuk": ("бренд", None),
}


def словарь() -> dict:
    """Словарь, собранный правилом сборщика: вид — у каждой записи."""
    записи = copy.deepcopy(СЫРЬЁ)
    чистые = oem_kind.карта_чистых(записи)
    out = [{**r, **oem_kind.вид_записи(r["name"], r["oem_key"], чистые)} for r in записи]
    return {"count": len(out), "records": out}


def test_вид_каждой_записи_придуманного_корпуса():
    по_ключу = {r["oem_key"]: r for r in словарь()["records"]}
    for k, (вид, бренды) in ОЖИДАНИЕ.items():
        assert по_ключу[k]["kind"] == вид, (k, по_ключу[k])
        assert по_ключу[k].get("brands") == бренды, (k, по_ключу[k])
        if вид != "бренд":
            assert по_ключу[k]["kind_why"], k
    # Разложение с неизвестной частью: известное — в brands, прочее — в unresolved.
    assert по_ключу["vortexagrifonтип"]["unresolved"] == ["Grifon"]
    # Защищённая запись несёт причину защиты, а не молчит.
    assert "защищена" in по_ключу["keltongrifonholdings"]["kind_why"]
    assert "kind_why" not in по_ключу["kelton"]


def test_обвиняет_только_закрытый_список():
    for имя in ("Восток-Маш", "Энергоремонт", "Исток", "Kelton GmbH", "Bently Nevada, Llc"):
        assert oem_kind.указание(имя) is None, имя
    for имя, фраза in (("Заказ по спецификации", "по спецификац"), ("Kelton (по типу)", "по типу"),
                       ("Любой дистрибьютор стандарта", "Любой"), ("Типовые: A/B", "Типовые"),
                       ("Solar; сток: Выдумка", "сток")):
        assert фраза in (oem_kind.указание(имя) or ""), имя
    assert oem_kind.номер("445566/778899 (sensor)", "445566778899sensor")
    assert not oem_kind.номер("Kelton (модуль KX445566)", "keltonмодульkx445566")
    # «, Inc», «Co., Ltd.» и запятая в скобках — не второй бренд.
    for имя in ("Kelton, Inc", "Shanghai Kelton Co., Ltd.", "Kelton (Vortexa, Brisko)"):
        assert not oem_kind.разделитель_брендов(имя), имя
    # Транслитерация не пояснение: латиницы в имени нет.
    assert not oem_kind.кириллица_при_латинице("келтон", "Келтон")
    assert oem_kind.кириллица_при_латинице("keltonпопрофилю", "Kelton (по профилю)")


def test_ревизия_и_сборщик_судят_одним_правилом():
    """Ревизия берёт признаки из того же модуля, и на словаре сборщика ни одна
    запись вида «бренд» не несёт обвиняющего признака, кроме защищённых."""
    pa = _модуль("kvant_portal_audit_kind", "scripts/portal_audit.py")
    assert pa.похоже_на_описание is oem_kind.похоже_на_описание
    assert pa.без_формы is oem_kind.без_формы
    assert pa.НЕ_КОМПАНИЯ is oem_kind.не_компания()
    т = pa.ревизия_словаря(словарь(), корень=ROOT)
    for код in ("d.kind", "d.kind_ref", "d.instruction", "d.key_digits", "d.key_glue"):
        assert т.счета[код][1] == 0, код
    # Защищённые видны числом: «Kelton / Grifon Holdings» — «/» без доказательства.
    assert т.счета["d.multi"][1] == 1
    # Тот же корпус без вида (прежняя сборка) — дефекты на месте.
    прежний = {"count": len(СЫРЬЁ), "records": copy.deepcopy(СЫРЬЁ)}
    т0 = pa.ревизия_словаря(прежний, корень=ROOT)
    assert т0.счета["d.kind"][1] == len(СЫРЬЁ)
    assert т0.счета["d.instruction"][1] == 3 and т0.счета["d.multi"][1] >= 5


def test_страница_брендов_не_берёт_не_бренд():
    с = словарь()
    карта, записи, спорных = brands.карта_словаря(с)
    assert set(записи) == {k for k, (вид, _) in ОЖИДАНИЕ.items() if вид == "бренд"}
    ключ = codes_sql.ключ_написания
    assert карта[ключ("Kelton (по профилю)")] == "kelton"          # описание → бренд
    assert карта[ключ("Kelton, ГЕРМАНИЯ")] == "kelton"
    assert ключ("Kelton, Vortexa") not in карта                   # несколько — спорно
    assert ключ("Vortexa / Grifon (тип.)") not in карта           # один бренд из двух — не выбор
    assert ключ("Заказ по спецификации: кольцо по габариту") not in карта
    assert спорных >= 2
    разл = brands.разложение_словаря(с)
    assert разл[ключ("Kelton, Vortexa")] == ["kelton", "vortexa"]
    assert разл[ключ("Заказ по спецификации: кольцо по габариту")] == []
    # Часть ячейки, совпавшая с указанием или номером, бренда не даёт.
    assert brands.ключи_ячейки("Заказ по спецификации: кольцо по габариту", карта, разл) == []
    assert brands.ключи_ячейки("Kelton; 445566-778899 sensor", карта, разл) == ["kelton"]
    assert brands.ключи_ячейки("Kelton; 445566-778899 sensor", карта) == ["kelton", "445566778899sensor"]
    assert brands.ключи_ячейки("Kelton (по профилю)", карта, разл) == ["kelton", "попрофилю"]
    assert brands.ключи_не_брендов(с) == {k for k, (вид, _) in ОЖИДАНИЕ.items() if вид != "бренд"}
    # Реестр, засеянный до пометок, не выдаёт запись-не-бренд брендом.
    реестр = {"словарь": brands.словарь_из_реестра(
        [("kelton", "Kelton"), ("keltonпотипу", "Kelton (по типу)")],
        [("kelton", "Kelton", "dict/oem.json", "x"), ("keltonпотипу", "Kelton (по типу)", "dict/oem.json", "y")]),
        "карточка": {"501": "kelton", "502": "keltonпотипу"}, "имена": {}}
    чистый = brands.реестр_без_не_брендов(реестр, brands.ключи_не_брендов(с))
    assert [r["oem_key"] for r in чистый["словарь"]["records"]] == ["kelton"]
    assert чистый["карточка"] == {"501": "kelton"}
    assert brands.реестр_без_не_брендов(None, {"x"}) is None


def test_засев_реестра_по_виду_записи():
    п = br.план_файлов(словарь(), {}, {})
    assert set(п.бренды) == {k for k, (вид, _) in ОЖИДАНИЕ.items() if вид == "бренд"}
    строки = {(н["spelling"]): (н["status"], н.get("brand_key"), sorted(н.get("candidates") or []))
              for н in п.написания if н["source"] == "dict/oem.json"}
    assert строки["Kelton"] == ("разрешено", "kelton", [])
    assert строки["Kelton (по профилю)"] == ("разрешено", "kelton", [])
    assert строки["Kelton, Vortexa"] == ("спорно", None, ["kelton", "vortexa"])
    assert строки["Vortexa / Grifon (тип.)"] == ("спорно", None, ["vortexa"])
    assert строки["Заказ по спецификации: кольцо по габариту"] == ("не бренд", None, [])
    assert строки["445566/778899 (sensor)"] == ("не бренд", None, [])
    assert строки["Kelton (по типу)"] == ("не бренд", None, [])
    for н in п.написания:
        assert (н["status"] in ("разрешено", "проверено")) == (н.get("brand_key") is not None)
        assert н.get("brand_key") is None or н["brand_key"] in п.бренды
    # Справочник портала не заводит бренд из указания, а компания получает суждение.
    бренды_сп, написания_сп = br.план_справочника(п, [(1, "Заказ по спецификации: кольцо по габариту"),
                                                      (2, "Kelton (по профилю)"), (3, "Grifon Pumps")])
    по_номеру = {н["sp176_id"]: н for н in написания_сп}
    assert по_номеру[1]["status"] == "не бренд"
    assert по_номеру[2]["status"] == "разрешено" and по_номеру[2]["brand_key"] == "kelton"
    assert по_номеру[3]["status"] == "разрешено" and по_номеру[3]["brand_key"] in бренды_сп
    assert not {"заказпоспецификациикольцопогабариту", "keltonпопрофилю"} & set(бренды_сп)
    компании = br.написания_компаний([(10, "Kelton, Vortexa"), (11, "445566/778899 (sensor)")],
                                     п.карта, п.не_бренды)
    assert [(н["status"], н.get("candidates")) for н in компании] == [
        ("спорно", ["kelton", "vortexa"]), ("не бренд", None)]


# ── Настоящий словарь ────────────────────────────────────────────────────────

def _настоящий():
    return json.loads((ROOT / "dict" / "oem.json").read_text(encoding="utf-8"))


def _без_вида(словарь_):
    """Тот же словарь в прежней форме: полей вида нет, каждая запись — бренд."""
    out = copy.deepcopy(словарь_)
    for r in out["records"]:
        for поле in ("kind", "kind_why", "brands", "unresolved"):
            r.pop(поле, None)
    return out


def test_у_каждой_записи_вид_и_он_от_сборщика():
    с = _настоящий()
    записи = с["records"]
    чистые = oem_kind.карта_чистых(записи)
    виды = {r["oem_key"]: r["kind"] for r in записи}
    for r in записи:
        assert r["kind"] in oem_kind.ВИДЫ, r["oem_key"]
        ожидаемо = oem_kind.вид_записи(r["name"], r["oem_key"], чистые)
        assert {k: r.get(k) for k in ожидаемо} == ожидаемо, r["oem_key"]
        for b in r.get("brands") or []:
            assert виды.get(b) == "бренд", (r["oem_key"], b)
    assert с["by_kind"] == {v: sum(1 for r in записи if r["kind"] == v) for v in oem_kind.ВИДЫ}


def test_ни_одно_написание_настоящего_бренда_не_потеряло_марку():
    """Правило 0: пометка видов не имеет права отнять марку у написания бренда.
    Сравнение с тем же словарём без видов — «до» на тех же байтах."""
    с, до = _настоящий(), _без_вида(_настоящий())
    бренды_ = {r["oem_key"] for r in с["records"] if r["kind"] == "бренд"}
    карта_до, _, _ = brands.карта_словаря(до)
    карта_после, _, _ = brands.карта_словаря(с)
    for k, b in карта_до.items():
        if b in бренды_:
            assert карта_после.get(k) == b, (k, b)
    план_до, план_после = br.план_файлов(до, {}, {}), br.план_файлов(с, {}, {})
    после = {(н["spelling"], н["seen_at"]): (н["status"], н.get("brand_key")) for н in план_после.написания}
    for н in план_до.написания:
        if н["status"] == "разрешено" and н["brand_key"] in бренды_:
            assert после[(н["spelling"], н["seen_at"])] == ("разрешено", н["brand_key"]), н["spelling"]
    assert бренды_ <= set(план_после.бренды)


def test_ключ_обрезкой_не_склеивает_бренды():
    """nkey режет ключ до 40 знаков (ключ бренда в реестре базы). Под одним
    обрезанным ключом записи вида «бренд» не бывает двух разных написаний."""
    bd = _модуль("kvant_build_dict_kind", "scripts/build_dict.py")
    assert bd.nkey("A" * 50) == bd.nkey_full("A" * 50)[:40] == "a" * 40
    # Написания поля изготовителя из справочника рядов («SEW», «CAT») лежат под
    # brand_key справочника, а не под своим nkey — это их назначение.
    ключи_справочника = {б["brand_key"] for б in json.loads(
        (ROOT / "dict" / "model_series.json").read_text(encoding="utf-8"))["brands"]}
    for r in _настоящий()["records"]:
        из_баз = [x for x in r["spellings"] if not x["where"].startswith("dict/")]
        assert all(bd.nkey(x["spelling"]) == r["oem_key"] for x in из_баз)
        assert all(r["oem_key"] in ключи_справочника
                   for x in r["spellings"] if x["where"].startswith("dict/"))
        полные = {bd.nkey_full(x["spelling"]) for x in из_баз}
        if r["kind"] == "бренд":
            assert len(полные) == 1 or len(r["oem_key"]) < oem_kind.ДЛИНА_КЛЮЧА, r["oem_key"]
