#!/usr/bin/env python3
"""Сборка gt/public/ansaldo.html — досье Ansaldo Energia (модели, субпоставщики, каналы)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    d = json.load(open(ROOT / "data/ansaldo.json", encoding="utf-8"))
    n_co = sum(len(g["rows"]) for g in d["subs"]) + sum(len(g["rows"]) for g in d["aftermarket"]) + len(d["network"])
    tpl = (ROOT / "site/ansaldo.template.html").read_text(encoding="utf-8")
    subs = {
        "__ANSALDO_JSON__": json.dumps(d, ensure_ascii=False),
        "__N_CO__": str(n_co),
        "__N_MODELS__": str(len(d["models"])),
        "__N_SRC__": str(len(d["sources"])),
        "__UPDATED__": d["updated"],
    }
    for k, v in subs.items():
        assert k in tpl, f"нет плейсхолдера {k}"
        tpl = tpl.replace(k, v)
    out = ROOT / "public/ansaldo.html"
    out.write_text(tpl, encoding="utf-8")
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
