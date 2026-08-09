#!/usr/bin/env python3
"""Сборка gt/public/hot.html — отдельная страница по камерам сгорания и горелкам лота.

Данные: gt/data/hot_parts.json (глубокий ресерч по позициям) + таможенные прецеденты
из zip/customs/out. Тот же стиль работы, что /gt/rfq: кандидаты ранжированы,
отметки сорсеров в localStorage.
"""
import glob
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent


def customs_hits():
    """Таможенные записи по камерам/горелкам/жаровым — первичка для карточек."""
    pat = re.compile(r"камер\w* сгорани|жаров|горелк|combustion chamber|burner|liner", re.I)
    out = []
    for f in glob.glob(str(REPO / "zip/customs/out/customs_8411*.json")):
        for rec in json.load(open(f)).get("matched") or []:
            blob = (rec.get("desc") or "") + " " + (rec.get("brand") or "")
            if pat.search(blob):
                out.append({
                    "d": (rec.get("date") or "")[:10],
                    "ex": (rec.get("exporter") or "?")[:60],
                    "im": (rec.get("importer") or "?")[:40],
                    "ds": (rec.get("desc") or "")[:180],
                    "co": rec.get("dispatch") or "",
                })
    out.sort(key=lambda r: r["d"], reverse=True)
    return out


CN = re.compile(r"кита|china|chin[ae]se|шэньчжэн|shenzhen|чэнду|chengdu|ханчжоу|hangzhou|guangzhou|beijing|шанха|shangha", re.I)


def main():
    data = json.load(open(ROOT / "data/hot_parts.json"))
    # правило владельца 09.08.2026: по камерам и горелкам китайский реверс не рассматриваем —
    # только Европа или подтверждённый склад
    for pos in data["positions"]:
        keep, dropped = [], []
        for c in pos.get("candidates") or []:
            blob = (c.get("country") or "") + " " + (c.get("name") or "") + " " + (c.get("site") or "")
            if CN.search(blob):
                dropped.append(c.get("name"))
            else:
                keep.append(c)
        pos["candidates"] = keep
        if dropped:
            pos["excluded_cn"] = dropped
    tpl = (ROOT / "site/hot.template.html").read_text(encoding="utf-8")
    subs = {
        "__HOT_JSON__": json.dumps(data, ensure_ascii=False),
        "__CUSTOMS_JSON__": json.dumps(customs_hits(), ensure_ascii=False),
    }
    for k, v in subs.items():
        assert k in tpl, f"нет плейсхолдера {k}"
        tpl = tpl.replace(k, v)
    out = ROOT / "public/hot.html"
    out.write_text(tpl, encoding="utf-8")
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
