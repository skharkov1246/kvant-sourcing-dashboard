"""Модельные ряды: справочник dict/model_series.json, распознавание и замер.

Строки придуманы (CLAUDE.md, правило 18): бренды «Kelton» и «Brisko» и их ряды
выдуманы, настоящие ряды проверяются только на собственных примерах
справочника, а не на строках из базы.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

from library import model_series as ms

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import brand_models as bm  # noqa: E402

ВЫДУМАННЫЙ = {"brands": [
    {"brand_key": "kelton", "name": "Kelton", "origin": "западный", "country": "Нигдения",
     "aliases": ["Kelton", "Келтон"], "context_aliases": ["KLT"],
     "series": [
         {"id": "kelton-kx", "series": "KX", "type": "ГПУ",
          "patterns": [{"rx": r"KX\s?\d{3}[A-Z]?", "context": "сам"}],
          "examples": ["KX500"], "sources": ["https://example.invalid/kx"]},
         {"id": "kelton-num", "series": "голые номера", "type": "дизель",
          "patterns": [{"rx": r"77\d{2}", "context": "бренд"}],
          "examples": ["7712"], "sources": ["https://example.invalid/77"]},
         {"id": "kelton-kx-long", "series": "KX с исполнением", "type": "ГПУ",
          "patterns": [{"rx": r"KX\s?\d{3}[A-Z]?\s?V\s?\d{2}", "context": "сам"}],
          "examples": ["KX500 V16"], "sources": ["https://example.invalid/kxv"]},
     ]},
    {"brand_key": "brisko", "name": "Brisko", "origin": "китайский", "country": "Нигдения",
     "aliases": ["Brisko"],
     "series": [
         {"id": "brisko-bt", "series": "BT", "type": "насос",
          "patterns": [{"rx": r"BT-?\d{2}", "context": "сам"}],
          "examples": ["BT-40"], "sources": ["https://example.invalid/bt"]},
     ]},
]}


def спр():
    return ms.из_данных(ВЫДУМАННЫЙ)


# ─── распознавание ───────────────────────────────────────────────────────────

def test_модель_сама_по_себе():
    assert ms.модель_в_тексте("Прокладка головки KX500 комплект", спр=спр()) == [
        ("kelton", "kelton-kx", "KX500")]


def test_границы_слова():
    с = спр()
    assert ms.модель_в_тексте("артикул AKX500", спр=с) == []
    assert ms.модель_в_тексте("артикул KX5001", спр=с) == []
    assert ms.модель_в_тексте("KX500/KX600", спр=с) == [
        ("kelton", "kelton-kx", "KX500"), ("kelton", "kelton-kx", "KX600")]
    # кириллица вплотную — тоже граница буквы
    assert ms.модель_в_тексте("ДляKX500", спр=с) == []


def test_контекст_бренда_обязателен_для_голых_номеров():
    с = спр()
    assert ms.модель_в_тексте("Кольцо 7712 резиновое", спр=с) == []
    assert ms.модель_в_тексте("Кольцо 7712 для Kelton", спр=с) == [("kelton", "kelton-num", "7712")]
    assert ms.модель_в_тексте("Кольцо 7712 KLT", спр=с) == [("kelton", "kelton-num", "7712")]
    # бренд из поля изготовителя разрешает так же, как имя в строке
    assert ms.модель_в_тексте("Кольцо 7712", бренды={"kelton"}, спр=с) == [
        ("kelton", "kelton-num", "7712")]
    # чужой бренд рядом не разрешает
    assert ms.модель_в_тексте("Кольцо 7712 Brisko", спр=с) == []


def test_длинное_совпадение_побеждает():
    assert ms.модель_в_тексте("Двигатель KX500 V16 комплект", спр=спр()) == [
        ("kelton", "kelton-kx-long", "KX500V16")]


def test_кириллические_двойники_в_обозначении():
    # «КХ500» набрано кириллицей (К и Х) — узнаётся; обычный текст не портится
    assert ms.модель_в_тексте("ремкомплект КХ500", спр=спр()) == [("kelton", "kelton-kx", "KX500")]
    assert ms.нормализовать("Корпус ХОМУТА") == "Корпус ХОМУТА"


def test_канон():
    assert ms.канон("SGT-400") == "SGT400"
    assert ms.канон("CG170-12") == "CG170-12"
    assert ms.канон("TCG 2020 V12") == "TCG2020V12"
    assert ms.канон("Таурус 60") == "TAURUS60"


def test_бренды_в_тексте_только_по_именам():
    с = спр()
    assert ms.бренды_в_тексте("насос Келтон и Brisko", с) == {"kelton", "brisko"}
    # context_aliases брендом строку не делают — только разрешают номера
    assert ms.бренды_в_тексте("KLT 7712", с) == set()


# ─── справочник ──────────────────────────────────────────────────────────────

def _файл():
    return json.loads((ROOT / "dict" / "model_series.json").read_text(encoding="utf-8"))


def test_справочник_устроен_и_с_источниками():
    d = _файл()
    типы = set(d["types"])
    ключи = [b["brand_key"] for b in d["brands"]]
    assert len(ключи) == len(set(ключи))
    ряды = [s["id"] for b in d["brands"] for s in b["series"]]
    assert len(ряды) == len(set(ряды))
    for b in d["brands"]:
        assert b["origin"] in ("западный", "китайский", "прочий"), b["brand_key"]
        assert re.fullmatch(r"[a-z0-9]+", b["brand_key"]), b["brand_key"]
        assert b["aliases"], b["brand_key"]
        for поле in ("context_aliases", "field_aliases"):
            assert all(isinstance(a, str) and a.strip() for a in b.get(поле, [])), (b["brand_key"], поле)
        for s in b["series"]:
            assert s["type"] in типы, s["id"]
            assert s["sources"] and all(u.startswith(("http://", "https://")) for u in s["sources"]), s["id"]
            assert s["examples"], s["id"]
            for p in s["patterns"]:
                assert p["context"] in (ms.САМ, ms.БРЕНД), s["id"]
                re.compile(p["rx"])


def test_ключи_брендов_совпадают_со_словарём_где_бренд_есть():
    """Бренд, уже заведённый в dict/oem.json, обязан идти под тем же ключом."""
    oem = json.loads((ROOT / "dict" / "oem.json").read_text(encoding="utf-8"))
    ключи_словаря = {r["oem_key"] for r in oem["records"]}
    for b in _файл()["brands"]:
        if b["brand_key"] in ключи_словаря:
            continue
        # нового ключа нет в словаре — тогда его имя не должно совпадать с именем
        # существующей записи словаря (иначе это дубль под другим ключом)
        имена = {r["name"].lower() for r in oem["records"]}
        assert b["name"].lower() not in имена, b["brand_key"]


def test_каждый_пример_узнаётся_своим_рядом():
    с = ms.справочник()
    for b in _файл()["brands"]:
        for s in b["series"]:
            for пример in s["examples"]:
                got = ms.модель_в_тексте(пример, бренды={b["brand_key"]}, спр=с)
                assert [(x[0], x[1]) for x in got] == [(b["brand_key"], s["id"])], (пример, got)


def test_пример_не_уходит_к_чужому_бренду():
    """Без бренда в строке пример не должен узнаваться чужим рядом."""
    с = ms.справочник()
    for b in _файл()["brands"]:
        for s in b["series"]:
            for пример in s["examples"]:
                чужие = [x for x in ms.модель_в_тексте(пример, спр=с) if x[0] != b["brand_key"]]
                assert not чужие, (пример, чужие)


def test_обычный_русский_текст_без_ложных_моделей():
    с = ms.справочник()
    for строка in ["Болт М12х40 ГОСТ 7798-70", "Кольцо уплотнительное 045-050-30",
                   "Подшипник 6205-2RS", "Сальник 35х52х7", "Шайба 12 оцинкованная",
                   "Труба 57х3,5 ст.20", "ТН ВЭД 8431 49 800 9", "Кабель ВВГнг 3х2,5",
                   "Работы по договору № 12/2026 от 15.03.2026",
                   # приводы, КИП, уплотнения и редукторы — там, где заведены ряды
                   # с короткими кодами (SK, CT, PV, 3051, 644, 3500/42)
                   "Двигатель АИР132М4 У3", "Сталь СТ 20 лист", "Кран шаровой 11с67п Ду50",
                   "Датчик давления Метран-150", "Редуктор Ч-100", "Насос ЦНС 180-1900",
                   "Труба А175-М", "Преобразователь частоты 7,5 кВт", "Шланг РВД 3/4 3500/42",
                   "Трансформатор ТМ 400/10", "Уплотнение торцевое 2Т-50", "Фильтр ФМ-009",
                   "Клапан КТ 120", "Датчик 3051 мм", "Лента ПВ 046"]:
        assert ms.модель_в_тексте(строка, спр=с) == [], строка


def test_разведка_узнаётся_своим_брендом():
    """Машины разведки брендов (data/brand_research) узнаются справочником рядов.

    Не все: «серия», «не указана» и машины чужих рядов (MWM в разведке Deutz)
    законно уходят мимо. Порог — чтобы справочник не отстал от разведки молча.
    """
    с = ms.справочник()
    всего = узнано = 0
    for f in sorted((ROOT / "data" / "brand_research").glob("*.json")):
        if f.name == "queue.json":
            continue
        b = json.loads(f.read_text(encoding="utf-8"))
        for m in b.get("machines", []):
            всего += 1
            if any(x[0] == b["oem_key"] for x in ms.модель_в_тексте(m["model"], {b["oem_key"]}, с)):
                узнано += 1
    assert всего and узнано / всего >= 0.85, (узнано, всего)


def test_короткие_коды_только_рядом_с_брендом():
    """Коды приводов, КИП и уплотнений без имени бренда не узнаются, с ним — узнаются."""
    с = ms.справочник()
    for строка, бренд in [("SK 9032.1", "nord"), ("R87", "seweurodrive"), ("3051", "rosemount"),
                          ("CT 120", "hoerbiger"), ("PV046", "parkerhannifin"), ("3500/42M", "bentlynevada"),
                          ("VF 49", "bonfiglioli"), ("W22", "weg"), ("P8", "wilden"), ("MSD", "sulzer")]:
        assert ms.модель_в_тексте(строка, спр=с) == [], строка
        assert [x[0] for x in ms.модель_в_тексте(строка, {бренд}, с)] == [бренд], строка


def test_имя_бренда_в_строке_разрешает_код():
    с = ms.справочник()
    assert ms.модель_в_тексте("Мотор-редуктор NORD SK 9032.1", спр=с) == [("nord", "nord-sk", "SK9032.1")]
    assert ms.модель_в_тексте("Клапан Hoerbiger CT 120", спр=с) == [("hoerbiger", "hoerbiger-valves", "CT120")]
    assert ms.модель_в_тексте("Датчик Rosemount 3051S", спр=с) == [("rosemount", "rosemount-pressure", "3051S")]
    # имя в поле изготовителя — только через field_aliases замера, в тексте NORD — контекст
    assert ms.бренды_в_тексте("NORD SK 9032.1", с) == set()


# ─── замер ───────────────────────────────────────────────────────────────────

def test_свести_дубли_написаниями():
    данные = json.loads(json.dumps(ВЫДУМАННЫЙ))
    данные["brands"][0]["field_aliases"] = ["KLTN"]
    данные["brands"][0]["aliases"].append("Kelton Werke")
    с = ms.из_данных(данные)
    карта = {"kltn": "kltn", "kltnwerk": "kltn", "keltonwerke": "keltonwerke",
             "keltonwerkeгермания": "keltonwerkeгермания", "klt": "klt", "brisko": "brisko",
             "прочее": "прочее"}
    итог = bm.свести_дубли(карта, с)
    # ключ реестра, равный написанию бренда, и все написания, ведущие к нему, — тот же бренд
    assert итог["kltn"] == итог["kltnwerk"] == "kelton"
    assert итог["keltonwerke"] == итог["keltonwerkeгермания"] == "kelton"
    # context_aliases не сводят, чужое не трогается
    assert итог["klt"] == "klt" and итог["brisko"] == "brisko" and итог["прочее"] == "прочее"
    # написание, которое дают два бренда, не сводит ни к одному
    данные["brands"][1]["aliases"].append("Kelton Werke")
    итог = bm.свести_дубли({"keltonwerke": "keltonwerke"}, ms.из_данных(данные))
    assert итог["keltonwerke"] == "keltonwerke"


def test_дубли_замера_25_09_сведены():
    """Ключи реестра из замера brand-models 25.09.2026 сводятся к брендам справочника."""
    карта = {k: k for k in ("cat", "warman", "dresserrand", "getriebebaunordгермания", "parker")}
    итог = {k: v for k, v in bm.свести_дубли(карта, ms.справочник()).items() if k in карта}
    assert итог == {"cat": "caterpillar", "warman": "weir",
                    "dresserrand": "siemensdemagdelavalturbomachinery",
                    "getriebebaunordгермания": "nord", "parker": "parkerhannifin"}


def test_учесть_пути_бренда_и_сделки():
    с = спр()
    карта = {"kelton": "kelton", "брискогмбх": "brisko"}
    итог = bm.Итог()
    bm.учесть(итог, сделка="d1", изготовитель="Kelton", текст="Кольцо 7712", карта=карта, спр=с)
    bm.учесть(итог, сделка="d2", изготовитель="", текст="Фильтр KX500", карта=карта, спр=с)
    bm.учесть(итог, сделка="d2", изготовитель="Неизвестно Кто", текст="Шайба 12", карта=карта, спр=с)
    bm.учесть(итог, сделка="d3", изготовитель=None, текст="насос Brisko BT-40", карта=карта,
              с_ценой=True, спр=с)
    assert итог.строк == 4 and итог.с_брендом == 3 and итог.с_моделью == 3
    assert итог.неразрешённых_написаний == 1
    assert итог.бренд_путь[("kelton", "поле")] == 1
    assert итог.бренд_путь[("kelton", "модель")] == 2
    assert итог.бренд_путь[("brisko", "имя")] == 1
    assert итог.бренд_сделки["kelton"] == {"d1", "d2"}
    assert итог.бренд_с_ценой["brisko"] == 1
    assert итог.модель_строк[("kelton", "kelton-num", "7712")] == 1


def test_свод_прячет_редкие_модели():
    с = спр()
    итог = bm.Итог()
    for i in range(3):
        bm.учесть(итог, сделка=f"d{i}", изготовитель="", текст="KX500", карта={}, спр=с)
    bm.учесть(итог, сделка="d9", изготовитель="", текст="KX777", карта={}, спр=с)
    св = bm.свод(итог, с, {})
    kelton = next(b for b in св["бренды"] if b["бренд"] == "kelton")
    assert kelton["моделей"] == 2
    assert [м["модель"] for м in kelton["модели"]] == ["KX500"]
    # в своде нет ни одного поля с номерами сделок
    assert "d1" not in json.dumps(св, ensure_ascii=False)


def test_словарь_реестра_точные_имена_с_цифрой():
    rx, имена = bm.словарь_реестра([("KX-500", ["Kelton KX 500"]), ("Турбина", None)])
    assert rx is not None and set(имена.values()) == {"KX-500"}
    assert rx.search("узел KX-500 в сборе")
    assert not rx.search("узел KX-5000")


def test_режим_в_прогоне_без_расписания():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "library-stats.yml").read_text(encoding="utf-8"))
    вход = wf[True]["workflow_dispatch"]["inputs"]["job"]
    assert "brand-models" in вход["options"]
    assert "schedule" not in wf[True]
    шаги = wf["jobs"]["run"]["steps"]
    шаг = next(s for s in шаги if s.get("if") == "inputs.job == 'brand-models'" and "run" in s)
    assert "scripts/brand_models.py" in шаг["run"]
