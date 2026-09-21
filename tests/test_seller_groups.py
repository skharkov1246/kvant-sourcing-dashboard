"""«Два независимых продавца» считается по списку групп, а не на глаз.

Оплачено шестью развалившимися выводами за ночь 17–18.09.2026: витрины с
разными доменами оказывались одним оператором. Улики каждый раз были прямые —
общий объект в коде страницы, общий складской номер, общая почта, посимвольно
совпадающее описание.

Здесь закреплены три вещи: группа записывается только с уликой; замер отделяет
строку, где мы САМИ сказали «это один оператор», от строки, где две витрины
выданы за рынок (иначе замер — тавтология); и поддомен www не считается второй
витриной.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ROOT / "gt/data/ship_seller_groups.json"
MEASURE = ROOT / "gt/data/ship_seller_independence.json"
sys.path.insert(0, str(ROOT / "gt/tools"))


def groups() -> list[dict]:
    if not GROUPS.exists():
        pytest.skip("списка групп нет")
    return json.loads(GROUPS.read_text(encoding="utf-8"))["groups"]


def test_у_каждой_группы_есть_улика_и_домены():
    for g in groups():
        assert len(g.get("domains") or []) >= 2, f'{g["group"]}: группа из одной витрины'
        ev = (g.get("evidence") or "").strip()
        assert len(ev) > 40, f'{g["group"]}: улика не названа'


def test_домен_принадлежит_ровно_одной_группе():
    seen = {}
    for g in groups():
        for d in g["domains"]:
            assert d not in seen, f"{d} записан в две группы: {seen[d]} и {g['group']}"
            seen[d] = g["group"]


def test_www_не_считается_второй_витриной():
    from seller_groups import domains_in
    got = domains_in({"price_source": "открыл www.irby.com и irby.com"})
    assert got == {"irby.com"}, f"поддомен www раздвоил витрину: {got}"


def test_замер_отделяет_оговорённое_от_неоговорённого():
    from seller_groups import marked
    yes = {"price_source": "Irby и OneSource — обе компании группы Sonepar, это НЕ два "
                           "независимых продавца"}
    no = {"price_source": "цену подтвердили codale.com и needco.com"}
    assert marked(yes) is True
    assert marked(no) is False


def test_набор_замера_согласован_сам_с_собой():
    if not MEASURE.exists():
        pytest.skip("замера нет")
    d = json.loads(MEASURE.read_text(encoding="utf-8"))
    t = d["totals"]
    assert t["rows_citing_one_group_twice"] == len(d["rows"])
    assert (t["rows_marked_as_one_operator"] + t["rows_not_marked"]
            == t["rows_citing_one_group_twice"])
    assert abs(sum(r["usd"] for r in d["rows"]) - t["usd_on_those_rows"]) < 1.0
    for r in d["rows"]:
        for g, doms in r["groups"].items():
            assert len(doms) > 1, f'{r["pn"]}: в замер попала одна витрина группы {g}'


def test_в_методе_сказано_почему_замер_не_тавтология():
    if not MEASURE.exists():
        pytest.skip("замера нет")
    d = json.loads(MEASURE.read_text(encoding="utf-8"))
    assert "тавтолог" in (d.get("method") or "").lower()


def test_пул_наличия_не_смешан_с_группой_владения():
    """Пул — разные фирмы с ОДНИМ складом, группа — одни люди под разными вывесками.

    Оплачено 18.09.2026: пул plc-mall ↔ automation-base сначала записали группой,
    и домен automation-base оказался сразу в двух группах — поиск группы по
    домену стал неоднозначным, а он решает, считать двух продавцов одним
    свидетелем или нет. Пулы живут отдельным разделом, и набор обязан объяснять
    разницу.
    """
    import json as _json
    d = _json.loads(GROUPS.read_text(encoding="utf-8"))
    pools = d.get("pools") or []
    if not pools:
        pytest.skip("пулов в наборе нет")
    assert (d.get("why_pools") or "").strip(), "набор обязан объяснять, чем пул отличается от группы"
    gdom = {x.lower() for g in d["groups"] for x in (g.get("domains") or [])}
    for pl in pools:
        assert pl.get("pool"), "у пула должно быть имя в поле pool, а не в поле group"
        assert (pl.get("evidence") or "").strip(), f"{pl.get('pool')}: пул без улики"
        assert len(pl.get("domains") or []) > 1, f"{pl.get('pool')}: пул из одного домена"
    # и свидетель по домену пула сводится к пулу, а не к группе владения
    import sys
    sys.path.insert(0, str(ROOT / "gt/tools"))
    import stocklist_cross as sc
    for pl in pools:
        for dom in pl["domains"]:
            w = sc.witness_of(dom)
            assert w, f"{dom}: свидетель не определён"
            if dom.lower() in gdom:
                # домен есть и в группе владения — свидетелем должен стать пул,
                # потому что общий склад сильнее общей вывески
                assert w == pl["pool"], (
                    f"{dom}: свидетелем назван «{w}», а должен быть пул «{pl['pool']}»: "
                    "общий остаток нельзя считать дважды")
