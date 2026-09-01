#!/usr/bin/env python3
"""Сборка gt/public/cummins.html — свечи и фильтры Cummins QSK60G, мировой пул поставщиков.

Данные: gt/data/cummins_qsk60.json (ресерч + сверка с Bitrix через scripts/bitrix_probe.py).
Тот же стиль, что /gt/hot и /gt/rfq: кандидаты ранжированы, у каждого доказательство,
отметки сорсеров живут в localStorage.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    data = json.load(open(ROOT / "data/cummins_qsk60.json", encoding="utf-8"))
    tpl = (ROOT / "site/cummins.template.html").read_text(encoding="utf-8")
    assert "__CUMMINS_JSON__" in tpl, "нет плейсхолдера __CUMMINS_JSON__"
    html = tpl.replace("__CUMMINS_JSON__", json.dumps(data, ensure_ascii=False))
    out = ROOT / "public/cummins.html"
    out.write_text(html, encoding="utf-8")
    n = sum(len(p["candidates"]) for p in data["positions"])
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ) | позиций {len(data['positions'])}, кандидатов {n}")


if __name__ == "__main__":
    main()
