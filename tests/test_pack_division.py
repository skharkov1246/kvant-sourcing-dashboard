"""Цену за штуку нельзя делить на фасовку дважды.

Найдено 18.09.2026 на строках 70-30093-1 и 1075721. На странице продавца стоит
95,08 USD за коробку из шести бухт. В наш набор попало уже поделённое значение
15,85 — цена ОДНОЙ бухты, — а сборщик сводки, видя фасовку 6, поделил его ещё
раз и получил 2,64 USD. Единица оказалась занижена ровно в шесть раз, и обе
строки при этом выглядели совершенно обычно.

Признак ошибки виден в самом пояснении: там дословно написано «95,08 USD за
коробку 6 бухт, то есть 15,85 USD за бухту», и это число 15,85 лежало в поле
цены рядом с фасовкой 6. Значит проверка возможна: если пояснение называет цену
ЗА ЕДИНИЦУ и та же цифра стоит в поле цены при фасовке больше единицы — деление
произойдёт второй раз.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "gt/data/ship_energoseti.json"
MERGED = ROOT / "gt/data/ship_lukoil.json"

# «то есть 15,85 USD за бухту», «= 12.50 USD за штуку», «по 3,40 USD за рулон»
PER_UNIT = re.compile(
    r"(\d[\d\s]*(?:[.,]\d+)?)\s*(?:USD|EUR|GBP|руб\.?)\s*за\s+"
    r"(?:бухту|штуку|рулон|шт\b|единицу|полоску)", re.I)


def num(s: str) -> float:
    return float(s.replace(" ", "").replace(" ", "").replace(",", "."))


def rows(p: Path) -> list:
    if not p.exists():
        pytest.skip("набора нет")
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["rows"] if isinstance(d, dict) else d


def test_цена_за_единицу_не_лежит_в_поле_цены_при_фасовке():
    bad = []
    for r in rows(SRC):
        pack = float(r.get("pack_qty") or 1)
        price = r.get("price")
        if pack <= 1 or not isinstance(price, (int, float)):
            continue
        for m in PER_UNIT.finditer(str(r.get("note") or "")):
            if abs(num(m.group(1)) - float(price)) < 0.01:
                bad.append((r.get("pn"), price, pack, m.group(0)))
    assert not bad, (
        "в поле цены лежит цена ЗА ЕДИНИЦУ при фасовке больше единицы — "
        f"сборщик сводки поделит её второй раз: {bad}")


def test_исправленные_строки_дают_верную_единицу():
    """Пин на две строки, на которых ошибка и нашлась."""
    got = {str(r.get("pn")): r for r in rows(MERGED)
           if str(r.get("pn")) in ("70-30093-1", "1075721")}
    if not got:
        pytest.skip("строк нет в сводке")
    for pn, r in got.items():
        assert abs(float(r["unit_price_usd"]) - 15.8467) < 0.01, (pn, r["unit_price_usd"])


def test_единица_и_фасовка_сходятся_с_ценой():
    """Арифметика сводки: цена за штуку, умноженная на фасовку, даёт цену."""
    for r in rows(MERGED):
        u, pack, pu = r.get("unit_price_usd"), r.get("pack_qty"), r.get("price_usd")
        if not all(isinstance(x, (int, float)) for x in (u, pack, pu)) or not pu:
            continue
        assert abs(u * float(pack) - pu) < max(0.02, 0.01 * pu), r.get("pn")
