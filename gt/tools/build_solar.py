#!/usr/bin/env python3
"""Сборка gt/public/solar.html — субпоставщики, MRO и склады машин Solar Turbines."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    d = json.load(open(ROOT / "data/solar.json"))
    t = Counter(c["tier"] for c in d["companies"])
    tpl = (ROOT / "site/solar.template.html").read_text(encoding="utf-8")
    subs = {
        "__SOLAR_JSON__": json.dumps(d, ensure_ascii=False),
        "__N_CO__": str(len(d["companies"])),
        "__N_FACTS__": str(len(d["facts"])),
        "__N_STAR__": str(t.get("indep_proven_maker", 0)),
        "__N_PROVEN__": str(t.get("indep_proven", 0) + t.get("indep_service", 0)),
        "__N_MAKER__": str(t.get("maker", 0)),
        "__N_TRADER__": str(t.get("trader", 0)),
    }
    for k, v in subs.items():
        assert k in tpl, f"нет плейсхолдера {k}"
        tpl = tpl.replace(k, v)
    out = ROOT / "public/solar.html"
    out.write_text(tpl, encoding="utf-8")
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
