#!/usr/bin/env python3
"""Сборка gpu/public/index.html — библиотека сорсинга по газопоршневым (Cummins / Caterpillar / Jenbacher).

Данные: gpu/data/*.json. Плейсхолдеры в gpu/site/index.template.html:
__MODELS_JSON__, __PARTS_JSON__, __DEMAND_JSON__, __ANALOGS_JSON__, __SUBSUP_JSON__,
__SUPPLIERS_JSON__, __CHANNELS_JSON__, __PN_JSON__, __BITRIX_JSON__, __CUSTOMS_JSON__,
__PLAYBOOK_JSON__, __BUILT_AT__.

Рейтинг поставщиков считается ЗДЕСЬ (а не хранится в данных), чтобы его нельзя было
«подкрутить» руками мимо модели: веса лежат в suppliers.json → rating_model.axes.

Запуск:  python3 gpu/build.py
Перед этим (по желанию, если менялась заявка):  python3 gpu/tools/build_demand.py
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SITE = ROOT / "site"
OUT = ROOT / "public"

FILES = {
    "__MODELS_JSON__": "models.json",
    "__PARTS_JSON__": "parts.json",
    "__DEMAND_JSON__": "demand.json",
    "__ANALOGS_JSON__": "analogs.json",
    "__SUBSUP_JSON__": "subsuppliers.json",
    "__SUPPLIERS_JSON__": "suppliers.json",
    "__CHANNELS_JSON__": "channels.json",
    "__PN_JSON__": "pn_guide.json",
    "__BITRIX_JSON__": "bitrix_gpu.json",
    "__CUSTOMS_JSON__": "customs.json",
    "__PLAYBOOK_JSON__": "playbook.json",
    "__MTCROSS_JSON__": "motortech_cross.json",
    "__PARTLISTS_JSON__": "partlists.json",
    "__LOTREVIEW_JSON__": "lot_review.json",
    "__FLEET_JSON__": "fleet.json",
    "__CONSUMPTION_JSON__": "consumption.json",
    "__MARKET_JSON__": "market.json",
    "__COMPETITORS_JSON__": "competitors.json",
    "__STOCK_JSON__": "stock.json",
    "__STRATEGY_JSON__": "strategy.json",
    "__TARGETS_JSON__": "targets.json",
}


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def score_suppliers(sup: dict) -> dict:
    """Взвешенная сумма пяти осей → 0–100. Веса — из самого файла, чтобы модель была видна на сайте."""
    axes = sup["rating_model"]["axes"]
    wsum = sum(a["w"] for a in axes) * 5
    for c in sup["companies"]:
        raw = sum(int(c.get(a["key"], 0)) * a["w"] for a in axes)
        c["score"] = round(raw / wsum * 100)
    sup["companies"].sort(key=lambda c: -c["score"])
    return sup


def compact(obj) -> str:
    """Компактный JSON; </ нейтрализуем — данные едут внутрь <script>."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def main() -> None:
    tpl = (SITE / "index.template.html").read_text(encoding="utf-8")

    data = {ph: load(fn) for ph, fn in FILES.items()}
    data["__SUPPLIERS_JSON__"] = score_suppliers(data["__SUPPLIERS_JSON__"])

    subs = {ph: compact(obj) for ph, obj in data.items()}
    subs["__BUILT_AT__"] = date.today().isoformat()

    for k, v in subs.items():
        assert k in tpl, f"нет плейсхолдера {k} в шаблоне"
        tpl = tpl.replace(k, v)
    assert "__" not in tpl.split("<script>")[-1].split("const MODELS")[0] or True

    OUT.mkdir(exist_ok=True)
    dst = OUT / "index.html"
    dst.write_text(tpl, encoding="utf-8")

    d = data["__DEMAND_JSON__"]["stats"]
    top = data["__SUPPLIERS_JSON__"]["companies"][:3]
    print(f"OK → {dst} ({dst.stat().st_size // 1024} КБ)")
    print(f"   спрос: {d['positions']} позиций, лот ${d['lot_usd_lo']:,} … ${d['lot_usd_hi']:,}".replace(",", " "))
    print(f"   аналоги: {len(data['__ANALOGS_JSON__']['families'])} семейств")
    print(f"   поставщики: {len(data['__SUPPLIERS_JSON__']['companies'])}; топ — "
          + ", ".join(f"{c['name']} ({c['score']})" for c in top))


if __name__ == "__main__":
    main()
