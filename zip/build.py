#!/usr/bin/env python3
"""Сборка сайта базы ЗИП: шаблон + данные → zip/public/index.html.

Данные (positions/odm_suppliers/price_records) живут в zip/data/*.json — это
офлайн-копия (SEED). На бою сайт читает те же данные из Supabase; SEED —
фолбэк, когда БД недоступна, и источник для локального просмотра.

Запуск:  python zip/build.py
Результат: zip/public/index.html  (+ vendor/supabase.js рядом)
"""
import json
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SITE = ROOT / "site"
OUT = ROOT / "public"


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def build():
    positions = load("positions.json")
    odm = load("odm_suppliers.json")
    prices = load("price_records.json")
    try:
        samples = load("samples.json")
    except FileNotFoundError:
        samples = []

    # group odm/prices/samples by position id (pp — стабильный ключ строки в UI)
    by_pos_odm, by_pos_price, by_pos_samp = {}, {}, {}
    for o in odm:
        by_pos_odm.setdefault(o.get("position_id"), []).append(o)
    for r in prices:
        by_pos_price.setdefault(r.get("position_id"), []).append(r)
    for s in samples:
        by_pos_samp.setdefault(s.get("position_id"), []).append(s)

    seed = []
    for p in positions:
        pid = p["id"]
        seed.append({
            "pos": p,
            "odm": by_pos_odm.get(pid, []),
            "prices": by_pos_price.get(pid, []),
            "samples": by_pos_samp.get(pid, []),
        })

    # статистика для баннера
    n_pos = len(positions)
    n_with_odm = sum(1 for s in seed if s["odm"])
    n_odm = len(odm)
    conf = {"high": 0, "med": 0, "low": 0}
    for o in odm:
        c = (o.get("confidence") or "").lower()
        if c in conf:
            conf[c] += 1

    # справочные датасеты спроса/бюджета (не привязаны к позициям; из Excel-лимитов)
    try:
        demand = load("demand.json")
    except FileNotFoundError:
        demand = []
    try:
        competitor = load("competitor_prices.json")
    except FileNotFoundError:
        competitor = []
    try:
        mat_nodes = load("material_strategy.json")
    except FileNotFoundError:
        mat_nodes = []
    try:
        mat_sup = load("material_suppliers.json")
    except FileNotFoundError:
        mat_sup = []
    mat_text_path = ROOT / "СТРАТЕГИЯ-МАТЕРИАЛЫ.md"
    mat_text = mat_text_path.read_text(encoding="utf-8") if mat_text_path.exists() else ""

    tpl = (SITE / "index.template.html").read_text(encoding="utf-8")
    seed_json = json.dumps(seed, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("[/*__SEED__*/]", seed_json)
    html = html.replace("[/*__DEMAND__*/]", json.dumps(demand, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("[/*__COMPET__*/]", json.dumps(competitor, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("[/*__MATNODES__*/]", json.dumps(mat_nodes, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("[/*__MATSUP__*/]", json.dumps(mat_sup, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("[/*__MATTEXT__*/]", "[" + json.dumps(mat_text, ensure_ascii=False) + "]")
    repl = {
        "__N_POS__": str(n_pos),
        "__N_WITH_ODM__": str(n_with_odm),
        "__N_ODM__": str(n_odm),
        "__N_HI__": str(conf["high"]),
        "__N_ME__": str(conf["med"]),
        "__N_LO__": str(conf["low"]),
        "__UPDATED__": date.today().isoformat(),
    }
    for k, v in repl.items():
        html = html.replace(k, v)
    assert "__SEED__" not in html and "__N_POS__" not in html, "остались плейсхолдеры"

    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(html, encoding="utf-8")
    (OUT / "vendor").mkdir(exist_ok=True)
    shutil.copy2(SITE / "vendor" / "supabase.js", OUT / "vendor" / "supabase.js")

    size = (OUT / "index.html").stat().st_size
    print(f"zip/public/index.html: {size:,} байт | позиций {n_pos}, "
          f"с ODM {n_with_odm}, ODM-записей {n_odm}, цен {len(prices)}")


if __name__ == "__main__":
    build()
