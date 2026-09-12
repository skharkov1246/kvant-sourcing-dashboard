"""Звенья «модель» и «узел»: правила именования машины и отнесения детали к узлу.

История. Справочника машин не было вовсе, и машина жила строкой в описании
детали. Первая же попытка разложить 12 442 партномера по узлам показала, чем
это оплачивается: подстрочный поиск ловил «ВНА» в слове «топливная», «АВО» —
в «доставочной» и «заводской», а в справочник машин из поля «машина» просились
«Solar (сток)» и «Шкаф управления PMS МЛСК Ф-1». Тесты ниже держат обе границы:
короткое слово ищется целиком, и не всякая строка — машина.

Примеры придуманы здесь и не взяты из базы: репозиторий публичный (правило 5).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"kvant_{name}", ROOT / "library" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eq = load("equipment")


# ─── имя машины ──────────────────────────────────────────────────────────────
def test_одна_машина_под_разными_написаниями_даёт_один_ключ():
    ключи = {eq.norm_model(s) for s in ("SGT-400", "SGT 400", "sgt400", "SGT–400")}
    assert ключи == {"sgt400"}


def test_разные_машины_ключом_не_сводятся():
    assert eq.norm_model("SGT-400") != eq.norm_model("SGT-300")
    assert eq.norm_model("Taurus 60") != eq.norm_model("Taurus 70")


def test_пояснение_в_скобках_из_ключа_выбрасывается():
    assert eq.norm_model("Centaur 50 (по документу)") == eq.norm_model("Centaur 50")


def test_перечисление_машин_делится_а_составное_имя_нет():
    assert eq.split_machines("Taurus 70, Taurus 70MD") == ["Taurus 70", "Taurus 70MD"]
    assert eq.split_machines("GE Frame 6B") == ["GE Frame 6B"]
    assert eq.split_machines("SGT-100 и SGT-400") == ["SGT-100", "SGT-400"]


def test_в_справочник_машин_не_идут_изготовитель_обломок_и_описание():
    # Каждый случай взят из разбора поля «машина» и стоил ложной машины.
    assert not eq.looks_like_machine("Solar (сток)")          # изготовитель, не модель
    assert not eq.looks_like_machine("1534")                  # обломок «Avon 1533/1534»
    assert not eq.looks_like_machine("Шкаф управления PMS МЛСК Ф-1")   # не турбина
    assert not eq.looks_like_machine("турбина")               # ни серии, ни номера


def test_настоящие_машины_в_справочник_идут():
    for имя in ("LM2500", "GE Frame 6B", "Taurus 60S", "SGT-400", "Avon 1533"):
        assert eq.looks_like_machine(имя), имя


# ─── узел по тексту ──────────────────────────────────────────────────────────
def test_короткое_слово_ищется_целиком_а_не_куском_другого():
    # «вна» внутри «топливная», «аво» внутри «доставочной» и «заводской».
    assert eq.unit_of("Клапан топливный дозирующий") != "compressor.inlet-guide-vanes"
    assert eq.unit_of("Запчасти погрузочно-доставочной машины") != "package.oil-coolers"
    assert eq.unit_of("Шильд заводской, алюминий") != "package.oil-coolers"
    # и при этом само слово целиком ловится
    assert eq.unit_of("Механизм ВНА компрессора") == "compressor.inlet-guide-vanes"
    assert eq.unit_of("АВО масла, секция") == "package.oil-coolers"


def test_узлы_горячего_тракта_и_ротора_различаются():
    assert eq.unit_of("Жаровая труба камеры сгорания") == "hot.combustion-liner"
    assert eq.unit_of("Горелка основная DLE, комплект") == "hot.main-burner"
    assert eq.unit_of("Подшипник упорный сегментный") == "rotor.thrust-bearing"
    assert eq.unit_of("Сухое газовое уплотнение ЦБК") == "rotor.dry-gas-seals"


def test_ничего_не_говорящая_строка_остаётся_без_узла():
    for s in ("Изделие", "Комплект поставки", "", "—"):
        assert eq.unit_of(s) is None


# ─── ярлык инженеров ─────────────────────────────────────────────────────────
def test_ярлык_инженеров_разбирается_явно_а_не_догадкой():
    assert eq.unit_of_seg("САУ, КИП, электрика") == "controls"
    assert eq.unit_of_seg("лопатки / СА") == "turbine.nozzle-guide-vanes"
    assert eq.unit_of_seg("Крепёж") == eq.unit_of_seg("крепеж") == "fasteners"
    assert eq.unit_of_seg("Прочее / требует разметки") is None
    assert eq.unit_of_seg("такого ярлыка нет") is None


# ─── связность правил и данных ───────────────────────────────────────────────
def узлы() -> set[str]:
    """Идентификаторы узлов, какими их строит загрузчик из данных инженеров."""
    d = json.loads((ROOT / "gt" / "data" / "parts.json").read_text(encoding="utf-8"))
    ids = set()
    for s in d["systems"]:
        ids.add(s["id"])
        for c in s.get("components", []):
            ids.add(f"{s['id']}.{eq.slug_en(c.get('en', ''))}")
    return ids | {u[0] for u in eq.EXTRA_UNITS}


def test_каждое_правило_ведёт_в_существующий_узел():
    """Если инженеры переименуют систему в parts.json, правила обязаны упасть здесь,
    а не молча разметить 12 тысяч позиций в несуществующий узел."""
    есть = узлы()
    нет = {u for u, _ in eq.UNIT_RULES if u not in есть}
    нет |= {u for u in eq.SEG_MAP.values() if u and u not in есть}
    assert not нет, f"правила ссылаются на несуществующие узлы: {sorted(нет)}"


def test_идентификатор_компонента_устойчив_и_читаем():
    assert eq.slug_en("combustion liner / can") == "combustion-liner"
    assert eq.slug_en("nozzle guide vanes (NGV)") == "nozzle-guide-vanes"
    assert eq.slug_en("") == ""


# ─── поиск машины в тексте статьи ────────────────────────────────────────────
def test_машина_в_тексте_ищется_целым_словом_и_длинная_раньше_короткой():
    """«ST14» подстрокой находится внутри «ST1400», «Mars» — внутри «Marshall».
    А «SGT-400» не должно съедаться более коротким «SGT»."""
    lk = load("link_knowledge")
    шаблоны = lk.машинный_шаблон([("sgt400", ["SGT-400"]), ("sgt", ["SGT"]),
                                  ("st14", ["ST14"]), ("mars90", ["Mars 90"])])

    def найти(текст):
        for rx, key in шаблоны:
            if rx.search(текст):
                return key
        return None

    assert найти("Ремонт камеры SGT-400 по регламенту") == "sgt400"
    assert найти("Буровая машина ST14, замена коронок") == "st14"
    assert найти("Партия ST1400 на складе") is None
    assert найти("Marshall Islands, судовая поставка") is None
    assert найти("Ничего про машины") is None


def test_слишком_короткое_написание_в_поиск_не_идёт():
    lk = load("link_knowledge")
    assert lk.машинный_шаблон([("st8", ["ST8"])]) == []
