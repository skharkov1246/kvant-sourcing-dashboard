"""Кириллическая буква-двойник внутри латинского обозначения не должна прятать номер.

Оплачено разбором заявки 18.09.2026. В обозначении «1794-IВ10XOB6XT» буква «В»
кириллическая: на вид неотличима от латинской B, но это другой символ. Ключ
поиска строился как «оставить только A–Z и 0–9», поэтому кириллическая буква
просто ВЫБРАСЫВАЛАСЬ, и номер получал ключ «179410XOB6XT». Тот же номер в
верном написании давал «1794IB10XOB6XT» — другой ключ. Номер лежал в индексе,
а найти его по верному написанию было нельзя. Таких номеров в заявке девять.

Здесь закреплено: оба написания дают ОДИН ключ, и делают это одинаково в
сборщике индекса и в поиске по нему — иначе они разойдутся снова.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_оба_написания_дают_один_ключ_в_индексе():
    bi = load("build_index")
    cyr = "1794-IВ10XOB6XT"        # «В» кириллическая
    lat = "1794-IB10XOB6XT"        # «B» латинская
    assert cyr != lat, "проверка бессмысленна: строки совпали посимвольно"
    assert bi.normalize(cyr, "pn") == bi.normalize(lat, "pn") == "1794IB10XOB6XT"


def test_поиск_нормализует_ровно_так_же_как_индекс():
    bi, lu = load("build_index"), load("lookup")
    for s in ("1794-IВ10XOB6XT", "PEDROLLO HF 50В", "У808750D9J", "RI01170В00-66"):
        assert lu.keys_for(s)[0] == bi.normalize(s, "pn"), (
            f"ключ поиска разошёлся с ключом индекса на «{s}»")


def test_прочая_кириллица_не_ломается():
    """Сводятся только двойники. Русское слово в поле артикула остаётся русским."""
    bi = load("build_index")
    # «Ж», «Ш», «Д» латинских двойников не имеют и заменяться не должны: они
    # отсеиваются, как отсеивались до правки
    assert bi.normalize("ЖШД-1234", "pn") == "1234", "не-двойники стали заменяться"
    assert bi.normalize("Диск", "org") == "диск"


def test_замер_называет_и_номер_и_его_латинское_написание():
    src = ROOT / "gt/data/ship_homoglyphs.json"
    if not src.exists():
        pytest.skip("замера нет")
    d = json.loads(src.read_text(encoding="utf-8"))
    assert d["pns_with_homoglyphs"] == len(d["items"])
    import homoglyphs as hg
    for x in d["items"]:
        assert x["pn"] != x["pn_latin"], f'{x["pn"]}: двойников в номере нет, строка лишняя'
        assert hg.fold(x["pn"]) == x["pn_latin"]
    assert "НЕ правится" in d["what_we_did"] or "не правится" in d["what_we_did"], (
        "набор обязан сказать, что поле артикула в данных не меняется: там то, что "
        "прислал заказчик")
