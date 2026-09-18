"""«Изготовитель» должен нести своё основание, и слабое основание нельзя считать сильным.

Оплачено двумя опровержениями 18.09.2026: масляному фильтру Fleetguard были
приписаны четыре фильтровых дома «по типу» с основанием «сегмент фильтров», а
наконечнику свечи Jenbacher — авиационный поставщик зажигания «типично» с
основанием «OE-поставщик зажигания Solar». Оба раза изготовитель выведен из
КЛАССА изделия, а не из документа, и оба раза открытый каталог это опроверг.

Здесь закреплено: класс основания определяется от сильного к слабому (документ
побеждает догадку), догадка по классу опознаётся своими формулировками, а сумма
по классам не расходится с общим счётом.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_maker_basis.json"
sys.path.insert(0, str(ROOT / "gt/tools"))


def doc() -> dict:
    if not SRC.exists():
        pytest.skip("замера нет")
    return json.loads(SRC.read_text(encoding="utf-8"))


def test_документ_побеждает_догадку():
    """Строка с декларацией И со словом «сегмент» — это документ, а не догадка."""
    from maker_basis import basis_of
    assert basis_of({"ev": "П1 — таможенная декларация КВАНТ", "mk": "Pall (по типу)"}) == "документ"
    assert basis_of({"ev": "П4: сегмент фильтров", "mk": "Pall/Donaldson (по типу)"}) \
        == "догадка по классу"


def test_догадка_опознаётся_своими_формулировками():
    from maker_basis import basis_of
    for ev in ("П4: сегмент фильтров", "П3: OE-поставщик зажигания Solar",
               "П4: типовой субпоставщик уплотнений", "П4: электрика"):
        assert basis_of({"ev": ev, "mk": ""}) == "догадка по классу", ev


def test_пустое_основание_не_выдаётся_за_документ():
    from maker_basis import basis_of
    assert basis_of({"ev": "", "mk": "ABB"}) == "основание не записано"


def test_счёт_по_классам_не_расходится_с_итогом():
    d = doc()
    t, cl = d["totals"], d["classes"]
    assert sum(c["rows_in_db"] for c in cl) == t["maker_named"], (
        "строки с названным изготовителем потерялись между классами")
    weak = [c for c in cl if c["basis"] == "догадка по классу"]
    assert len(weak) == 1
    assert weak[0]["rows_in_request"] == t["weak_rows_in_request"]
    assert abs(weak[0]["usd_in_request"] - t["weak_usd_in_request"]) < 1.0


def test_два_числа_догадки_считаются_раздельно():
    """Догадка в разметке и ОСТАТОК работы после зачёта перепроверки — разные числа.

    Оплачено моей же ошибкой 18.09.2026: в отчёт ушло первое (2 124 537 USD,
    23 % экспозиции), а открытой работы к тому часу оставалось впятеро меньше —
    перепроверка уже закрыла каталогом 19 из этих строк. Подменять одно число
    другим нельзя, поэтому считаются оба, и «из них» относится только к
    пересечению.
    """
    t = doc()["totals"]
    for k in ("weak_by_db_basis_rows", "weak_by_db_basis_usd",
              "weak_closed_by_reverify_rows", "weak_closed_by_reverify_usd",
              "closed_by_reverify_rows", "closed_by_reverify_usd"):
        assert k in t, f"нет ключа {k}: два числа снова слились в одно"
    assert abs(t["weak_by_db_basis_usd"] - t["weak_closed_by_reverify_usd"]
               - t["weak_usd_in_request"]) < 1.0, (
        "остаток работы должен быть ровно разницей: догадка в разметке минус закрытое "
        "перепроверкой")
    assert t["weak_closed_by_reverify_rows"] <= t["closed_by_reverify_rows"], (
        "пересечение не может быть больше всего закрытого перепроверкой")


def test_у_каждого_класса_сказано_как_его_читать():
    for c in doc()["classes"]:
        assert len((c.get("why") or "").strip()) > 20, f'{c["basis"]}: не сказано, как читать'


def test_замер_называет_повод_а_не_только_цифры():
    d = doc()
    why = (d.get("why") or "").lower()
    assert "класс" in why and "каталог" in why, (
        "замер должен говорить, почему он сделан: иначе через месяц это просто счётчик")


NEGATION = ("не вскрыт", "не раскрыт", "не установлен", "не подтвержд", "не найден",
            "нигде не", "вероятно", "гипотез")
GUESS = ("(по типу)", "(тип.)", "по типу", "тип.")


def test_короткое_имя_изготовителя_не_содержит_отрицаний_и_догадок():
    """maker_short — это ФАКТ с внешнего каталога, а не пересказ поисков.

    Оплачено разбором прозы: замер определял закрытые строки по тексту поля
    real_maker, и это не работало в обе стороны. Строка «Не найден, и по типу
    изделия его, скорее всего, нет» проходила как положительная, а строка
    «Cummins Inc. — владелец номера … Физический изготовитель катушки не
    вскрыт» отбраковывалась из-за слов в середине текста. Из-за этого замер
    насчитал 55 закрытых строк на 5,3 млн USD, и число было завышено.
    """
    src = ROOT / "gt/data/ship_reverify.json"
    if not src.exists():
        pytest.skip("набора перепроверки нет")
    rows = json.loads(src.read_text(encoding="utf-8"))["rows"]
    bad = []
    for r in rows:
        mk = str(r.get("maker_short") or "").strip()
        if not mk:
            continue
        low = mk.lower()
        if any(w in low for w in NEGATION):
            bad.append(f'{r["pn"]}: отрицание в «{mk}»')
        if any(w in low for w in GUESS):
            bad.append(f'{r["pn"]}: догадка по типу в «{mk}»')
        if len(mk) > 90:
            bad.append(f'{r["pn"]}: имя длиной {len(mk)} — это проза, а не имя')
        if low.count("/") > 2:
            bad.append(f'{r["pn"]}: перечень через слэш в «{mk}» — это не один изготовитель')
    assert not bad, f"поле короткого имени изготовителя испорчено: {bad}"


def test_замер_считает_ровно_строки_с_коротким_именем():
    """Иначе счёт закрытого снова начнёт зависеть от разбора прозы."""
    import sys
    sys.path.insert(0, str(ROOT / "gt/tools"))
    from maker_basis import checked_by_reverify

    src = ROOT / "gt/data/ship_reverify.json"
    if not src.exists():
        pytest.skip("набора перепроверки нет")
    rows = json.loads(src.read_text(encoding="utf-8"))["rows"]
    want = sum(1 for r in rows if str(r.get("maker_short") or "").strip())
    assert len(checked_by_reverify()) == want
