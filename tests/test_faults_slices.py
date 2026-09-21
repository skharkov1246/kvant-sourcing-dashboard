"""Срезы «признак → дефект» по направлениям: честность вердикта и сверка номеров.

Звено «признак → дефект» цепочки портала закрыто тремя срезами: ГШО — внутри
досье машины (zip/data/r1700.json), ГТУ и ГПУ — файлами направления. Здесь
проверяется то, что легче всего испортить и дороже всего не заметить:
выдать общую инженерную практику за документ и сослаться на номер, которого
у машин направления нет.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SLICES = {
    "gtu": (ROOT / "gt" / "data" / "faults.json", [ROOT / "gt" / "data" / "pn_db.json",
                                                   ROOT / "gt" / "data" / "pn_catalog.json"]),
    "gpu": (ROOT / "gpu" / "data" / "faults.json", sorted(
        p for p in (ROOT / "gpu" / "data").glob("*.json") if p.name != "faults.json")),
}
VERDICTS = {"подтверждён", "не проверялся", "сомнителен", "снят"}


def _slice(name):
    path = SLICES[name][0]
    if not path.exists():
        pytest.skip(f"срез {name} ещё не собран")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(SLICES))
def test_вердикт_обязателен_и_из_закрытого_списка(name):
    """Строка без вердикта неотличима от подтверждённой — и завышает счётчик."""
    d = _slice(name)
    rows = d.get("rows") or []
    assert rows, "пустой срез направления"
    for x in rows:
        assert x.get("symptom", "").strip(), "строка без признака"
        assert x.get("node", "").strip(), f"строка без узла: {x.get('symptom')}"
        assert x.get("verdict") in VERDICTS, f"чужой вердикт: {x.get('verdict')}"
        if x["verdict"] == "подтверждён":
            assert (x.get("url") or "").strip(), (
                f"«подтверждён» без источника: {x['symptom'][:50]}")
            assert (x.get("note") or "").strip(), (
                f"«подтверждён» без цитаты источника: {x['symptom'][:50]}")


@pytest.mark.parametrize("name", sorted(SLICES))
def test_подтверждено_не_всё(name):
    """Срез, где подтверждено всё, — признак того, что границу не проводили.

    Общая инженерная практика по классу машин существует и полезна, но она не
    документ. Если в файле нет ни одной такой строки, вердикт проставлен
    механически.
    """
    rows = _slice(name).get("rows") or []
    ok = sum(1 for x in rows if x["verdict"] == "подтверждён")
    assert 0 < ok < len(rows), f"{name}: подтверждено {ok} из {len(rows)}"


@pytest.mark.parametrize("name", sorted(SLICES))
def test_номера_только_из_наших_данных(name):
    """Строка не может ссылаться на номер, которого у машин направления нет."""
    _path, refs = SLICES[name]
    rows = _slice(name).get("rows") or []
    blob = "".join(p.read_text(encoding="utf-8") for p in refs if p.exists())
    missing = sorted({p for x in rows for p in (x.get("parts") or []) if p not in blob})
    assert not missing, f"{name}: номеров нет в наших данных: {missing[:5]}"


@pytest.mark.parametrize("name", sorted(SLICES))
def test_в_срезе_нет_персональных_и_клиентских_данных(name):
    """Репозиторий публичный: ни почт, ни названий заказчиков."""
    import re
    raw = json.dumps(_slice(name), ensure_ascii=False)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]{2,}", raw), "в срезе есть почта"
    assert not re.search(r"\b(ООО|ЗАО|ПАО)\s*«", raw), "в срезе есть название компании в кавычках"


@pytest.mark.parametrize("name", sorted(SLICES))
def test_чего_нет_записано(name):
    """Пробел — такой же результат, как найденное, и должен быть назван."""
    d = _slice(name)
    gaps = d.get("gaps")
    text = " ".join(gaps) if isinstance(gaps, list) else str(gaps or "")
    assert len(text) > 200, f"{name}: пробелы среза не описаны"


def test_в_звено_цепочки_идёт_только_подтверждённое():
    """Счётчик заполняемости не должен считать общую практику знанием."""
    cov = ROOT / "data" / "chain_coverage.json"
    if not cov.exists():
        pytest.skip("счётчик не собран")
    data = json.loads(cov.read_text(encoding="utf-8"))
    by_seg = {s["segment"]: {c["link"]: c["n"] for c in s["cells"]} for s in data["segments"]}
    for name in sorted(SLICES):
        if not SLICES[name][0].exists():
            continue
        rows = _slice(name).get("rows") or []
        ok = {x["symptom"] for x in rows if x["verdict"] == "подтверждён"}
        assert by_seg[name]["symptom"] == len(ok), (
            f"{name}: в клетку «признак» попало не то, что подтверждено")
        assert by_seg[name]["symptom"] < len(rows), f"{name}: подтверждено не может быть всё"


def test_исполнитель_не_считается_по_свободному_тексту():
    """Корпус придуман. «Исполнитель» — утверждение о компании, не защита строки.

    Прежнее правило искало «ремонт|сервис» ещё и в примечании и дало по ГШО
    51 вместо 21: в исполнители попадали изготовитель уплотнений, завод РВД,
    поставщики фильтров и портал сервисной ИНФОРМАЦИИ Cat SIS — у всех слово
    стояло в описании, а не в занятии. Правило должно обвинять закрытым списком.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bcc", ROOT / "scripts" / "build_chain_coverage.py")
    src = (ROOT / "scripts" / "build_chain_coverage.py").read_text(encoding="utf-8")
    assert "CONTRACTOR_DOC" in src, "исключение для продавцов ремонтных руководств снято"
    assert "org.get('note'" not in src.split("def is_contractor")[1].split("def ")[0], \
        "исполнитель снова считается по свободному тексту примечания"

    import re
    narrow = re.compile(r"ремонт|ребилд|восстановлен|восстановительн|капремонт"
                        r"|overhaul|refurbish|\bMRO\b", re.I)
    doc = re.compile(r"ремонтн\w*\s+(руководств|каталог|документац|литератур)", re.I)

    def is_contractor(o):
        if (o.get("kind") or "").strip() == "ремонт":
            return True
        role = f"{o.get('role', '')} {o.get('what', '')}".strip()
        return bool(narrow.search(role)) and not doc.search(role)

    yes = [
        {"org": "Цех ребилда", "kind": "ремонт", "role": ""},
        {"org": "Независимый MRO", "kind": "торговец", "role": "независимый сервис MRO, лопатки"},
        {"org": "Моторный завод", "kind": "торговец", "role": "капитальный ремонт двигателей"},
    ]
    no = [
        {"org": "Изготовитель уплотнений", "kind": "производитель-неоригинал",
         "role": "10 000 стандартных уплотнений", "note": "годится в сервисный комплект и ремонт"},
        {"org": "Продавец каталогов", "kind": "маркетплейс",
         "role": "продажа электронных каталогов запчастей и ремонтных руководств"},
        {"org": "Портал документации", "kind": "маркетплейс",
         "role": "официальная система сервисной информации"},
        {"org": "Торговец фильтрами", "kind": "торговец", "role": "шесть брендов фильтров",
         "note": "закрывает весь сервисный контур"},
    ]
    assert all(is_contractor(o) for o in yes), [o["org"] for o in yes if not is_contractor(o)]
    assert not any(is_contractor(o) for o in no), [o["org"] for o in no if is_contractor(o)]
