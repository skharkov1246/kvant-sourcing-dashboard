#!/usr/bin/env python3
"""Сборка gt/public/lot.html — хаб-меню лота ЛУКОЙЛ.

Считает живые цифры из данных и рисует навигацию: мелочь (план пачек), горячая часть,
склады, поставщики по направлениям, блоки лота.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    html = (ROOT / "public/rfq.html").read_text(encoding="utf-8")
    batches = json.loads(re.search(r"const BATCHES = (\[.*?\]);\nconst STOCK", html, re.S).group(1))
    stock = json.loads(re.search(r"const STOCK = (\[.*?\]);\nconst META", html, re.S).group(1))
    meta = json.loads(re.search(r"const META = (\{.*?\});", html, re.S).group(1))
    hot = json.load(open(ROOT / "data/hot_parts.json"))

    dirs = {}
    for b in batches:
        d = dirs.setdefault(b["dir"], {"batches": 0, "n": 0, "sups": set()})
        d["batches"] += 1
        d["n"] += b["n"]
        for s in b["sups"]:
            d["sups"].add(s["name"])
    dir_rows = "".join(
        f'<a class="dirrow" href="./rfq#dir={json.dumps(d)[1:-1]}">'
        f'<span>{d}</span><span class="mut">{v["n"]} поз. · {v["batches"]} пач. · {len(v["sups"])} адресатов</span></a>'
        for d, v in sorted(dirs.items(), key=lambda kv: -kv[1]["n"])
    )

    hot_stock = sum(len(v["finds"]) for v in (hot.get("stock") or {}).values())
    stock_sum = sum((x.get("hi") or 0) * (x.get("qty") or 0) for x in stock)
    uniq_sups = len({s["name"] for b in batches for s in b["sups"]})

    tpl = (ROOT / "site/lot.template.html").read_text(encoding="utf-8")
    subs = {
        "__TOTAL__": f"{meta['total']:,}".replace(",", " "),
        "__PRICED__": str(meta.get("priced", 0)),
        "__VERIFIED__": str(meta.get("verified", 0)),
        "__BATCHES__": str(len(batches)),
        "__SUPS__": str(uniq_sups),
        "__STOCK_N__": str(len(stock)),
        "__STOCK_SUM__": f"${stock_sum:,.0f}".replace(",", " "),
        "__HOT_N__": str(len(hot["positions"])),
        "__HOT_QTY__": str(sum(int(p["qty"] or 0) for p in hot["positions"])),
        "__HOT_STOCK__": str(hot_stock),
        "__DIR_ROWS__": dir_rows,
        "__UPDATED__": hot.get("updated", ""),
    }
    for k, v in subs.items():
        assert k in tpl, f"нет плейсхолдера {k}"
        tpl = tpl.replace(k, v)
    out = ROOT / "public/lot.html"
    out.write_text(tpl, encoding="utf-8")
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
