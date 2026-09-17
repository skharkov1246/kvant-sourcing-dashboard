"""Валютная цена поставщика → рублёвая себестоимость с ввозом.

Мы импортируем запчасти сами, поэтому цена российского перепродавца нам не
себестоимость. Здесь одно правило на весь портал: цена × курс × коэффициент
ввоза. Ставки и курс лежат в data/import_cost.json, чтобы их правили в одном
месте, а не переписывали в каждой смете.

    >>> landed(863.61)["rub"]
    84391
"""
from __future__ import annotations

import json
import pathlib

PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "import_cost.json"


def config(path: pathlib.Path | None = None) -> dict:
    """Ставки ввоза и курс как они записаны в данных."""
    return json.loads((path or PATH).read_text(encoding="utf-8"))


def import_factor(cfg: dict | None = None) -> float:
    """Коэффициент к цене поставщика: доставка, пошлина, оформление."""
    cfg = cfg or config()
    return round(1 + sum(s["pct"] for s in cfg["stack"]), 4)


def landed(usd: float, cfg: dict | None = None, fx: float | None = None,
           duty: float | None = None) -> dict:
    """Себестоимость одной позиции с разложением, откуда взялся каждый рубль.

    usd  — цена поставщика в валюте котировки (доллары).
    fx   — курс, если он не тот, что в данных (например курс сделки).
    duty — ставка пошлины по конкретной ТН ВЭД, если брокер назвал свою.
    """
    cfg = cfg or config()
    fx = fx if fx is not None else cfg["fx"]["rub_per_usd"]
    parts, pcts = {}, 0.0
    base = usd * fx
    for s in cfg["stack"]:
        pct = duty if (duty is not None and s["key"] == "duty") else s["pct"]
        pcts += pct
        parts[s["key"]] = round(base * pct)   # для показа; итог считается неокруглённым
    factor = round(1 + pcts, 4)
    return {
        "usd": usd,
        "fx": fx,
        "base_rub": round(base),
        "parts": parts,
        "factor": factor,
        "rub": round(base * factor),
        "vat_note": cfg["vat"]["note"],
    }


def retail_gap(usd: float, retail_rub: float, cfg: dict | None = None) -> float:
    """Во сколько раз российская розница дороже нашей себестоимости."""
    own = landed(usd, cfg)["rub"]
    return round(retail_rub / own, 1) if own else 0.0
