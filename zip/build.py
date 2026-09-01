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
import subprocess
import sys
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

    try:
        sheet_meta = load("sheet_meta.json")
    except FileNotFoundError:
        sheet_meta = {}

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
        sm = sheet_meta.get(str(pid)) or sheet_meta.get(pid)
        if sm:
            p = dict(p, sheet_meta=sm)  # прикрепляем к позиции (идентичность в офлайне — pp)
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
    try:
        mat_proc = load("material_process.json")
    except FileNotFoundError:
        mat_proc = []
    try:
        customs = load("customs_market.json")
    except FileNotFoundError:
        customs = None
    try:
        supplier_crm = load("supplier_crm.json")
    except FileNotFoundError:
        supplier_crm = None
    try:
        bom = load("bom.json")
    except FileNotFoundError:
        bom = None
    try:
        customs_decl = load("customs_declarations.json")
    except FileNotFoundError:
        customs_decl = None
    mat_text_path = ROOT / "СТРАТЕГИЯ-МАТЕРИАЛЫ.md"
    mat_text = mat_text_path.read_text(encoding="utf-8") if mat_text_path.exists() else ""

    tpl = (SITE / "index.template.html").read_text(encoding="utf-8")
    seed_json = json.dumps(seed, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("[/*__SEED__*/]", seed_json)
    html = html.replace("[/*__DEMAND__*/]", json.dumps(demand, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("[/*__COMPET__*/]", json.dumps(competitor, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("[/*__MATNODES__*/]", json.dumps(mat_nodes, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("[/*__MATSUP__*/]", json.dumps(mat_sup, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("[/*__MATPROC__*/]", json.dumps(mat_proc, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("[/*__CUSTOMS__*/]", "[" + json.dumps(customs, ensure_ascii=False, separators=(",", ":")) + "]")
    html = html.replace("[/*__SUPCRM__*/]", "[" + json.dumps(supplier_crm, ensure_ascii=False, separators=(",", ":")) + "]")
    html = html.replace("[/*__BOM__*/]", "[" + json.dumps(bom, ensure_ascii=False, separators=(",", ":")) + "]")
    html = html.replace("[/*__CDECL__*/]", "[" + json.dumps(customs_decl, ensure_ascii=False, separators=(",", ":")) + "]")
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

    # документы заказов → на сайт (/orders/), с ASCII-именами для чистых URL
    orders = ROOT / "orders"
    if orders.exists():
        (OUT / "orders").mkdir(exist_ok=True)
        alias = {"ПИЛОТ-ЗАКАЗ-1.pdf": "pilot-order-1.pdf",
                 "ПИЛОТ-ЗАКАЗ-1.md": "pilot-order-1.md",
                 "ПОСТАВЩИКИ-SGT400.pdf": "sgt400-suppliers.pdf",
                 "БАЗЫ-ДАННЫХ-ЗАКУПКА.pdf": "trade-databases.pdf",
                 "БАЗЫ-ДАННЫХ-ЗАКУПКА.md": "trade-databases.md",
                 "sgt400_world_suppliers.csv": "sgt400-world-suppliers.csv",
                 "sourcer_checklist_sgt400.csv": "sgt400-sourcer-checklist.csv",
                 "ПОСТАВЩИКИ-ПО-БЛОКАМ.pdf": "suppliers-by-block.pdf",
                 "ПОСТАВЩИКИ-ПО-БЛОКАМ.html": "suppliers-by-block.html",
                 "ПОДШИПНИКИ-ОТЧЁТ.pdf": "bearings-report.pdf",
                 "ПОДШИПНИКИ-ОТЧЁТ.html": "bearings-report.html",
                 "КТО-УЖЕ-ПОСТАВЛЯЕТ.pdf": "who-supplies.pdf",
                 "КТО-УЖЕ-ПОСТАВЛЯЕТ.html": "who-supplies.html"}
        for f in orders.iterdir():
            if f.suffix.lower() in (".pdf", ".csv", ".md", ".html"):
                shutil.copy2(f, OUT / "orders" / alias.get(f.name, f.name))

    # ГТУ-библиотека (gt/) → публикуется на этом же проекте Pages по пути /gt/
    try:
        subprocess.run([sys.executable, str(ROOT.parent / "gt" / "build.py")], check=True)
        (OUT / "gt").mkdir(exist_ok=True)
        for page in (ROOT.parent / "gt" / "public").glob("*.html"):
            shutil.copy2(page, OUT / "gt" / page.name)
            print(f"gt: {page.name} → zip/public/gt/{page.name}")
        # PN-wizard: отдельный сайт базы PN по пути /gt/wizard/ (подпапка с data.js/guide.html)
        wiz = ROOT.parent / "gt" / "public" / "wizard"
        if wiz.is_dir():
            dst = OUT / "gt" / "wizard"
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(wiz, dst)
            print(f"gt: wizard/ → zip/public/gt/wizard/ ({len(list(dst.iterdir()))} файлов)")
    except Exception as e:  # ГТУ-сайт не должен ронять деплой базы ЗИП
        print(f"gt: пропущен ({e})")

    size = (OUT / "index.html").stat().st_size
    print(f"zip/public/index.html: {size:,} байт | позиций {n_pos}, "
          f"с ODM {n_with_odm}, ODM-записей {n_odm}, цен {len(prices)}")


if __name__ == "__main__":
    build()
