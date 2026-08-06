#!/usr/bin/env python3
"""Сборка gt/public/index.html — библиотека поставщиков газовых турбин (Solar + Siemens SGT-100…400).

Данные: gt/data/*.json + таможенная статистика РФ HS 8411 из zip/customs/out/.
Плейсхолдеры в gt/site/index.template.html: __SUPPLIERS_JSON__, __MODELS_JSON__,
__PARTS_JSON__, __PN_JSON__, __CUSTOMS_JSON__, __BUILT_AT__.
"""
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent

CUSTOMS_FILES = sorted((REPO / "zip/customs/out").glob("customs_8411*.json"))
MODEL_RE = re.compile(
    r"(SOLAR|TAURUS|CENTAUR|MARS\b|TITAN|SATURN|SGT[- ]?\d{3,4}\w?|TYPHOON|TORNADO|TEMPEST|CYCLONE"
    r"|GT10|GT13|GT35|GTX100|TB\d{4}|MS\s?\d{4}|FRAME\s?\d|9F[AB]?\b|6F\.?03|6FA|7EA|\b6B\b"
    r"|V94\.?[23]|V64\.?3|ГТЭ[- ]?160)",
    re.I,
)


def norm_name(s):
    """Схлопывает дубли вида «АО МОТОР СИЧ» / АО "МОТОР СИЧ" / АО"МОТОР СИЧ"."""
    return re.sub(r"\s+", " ", (s or "").replace('"', " ").replace("«", " ").replace("»", " ")).strip()


def customs_summary():
    total = 0
    visible = 0
    by_year = Counter()
    kg_by_year = Counter()
    exporters = Counter()
    importers = Counter()
    countries = Counter()
    model_hits = defaultdict(Counter)  # модель -> экспортёры
    records = []  # компактные записи для drill-down по экспортёру на сайте
    for f in CUSTOMS_FILES:
        d = json.loads(f.read_text())
        total += int(d.get("total_records") or 0)
        for rec in d.get("matched") or []:
            visible += 1
            y = (rec.get("date") or "")[:4]
            if y:
                by_year[y] += 1
                try:
                    kg_by_year[y] += float(rec.get("net_kg") or 0)
                except ValueError:
                    pass
            ex_key = norm_name(rec.get("exporter"))
            if ex_key:
                exporters[ex_key] += 1
            if rec.get("importer"):
                importers[norm_name(rec["importer"])] += 1
            if rec.get("dispatch"):
                countries[rec["dispatch"]] += 1
            desc = f"{rec.get('desc') or ''} {rec.get('brand') or ''}"
            for m in set(x.upper().replace(" ", "-") for x in MODEL_RE.findall(desc)):
                model_hits[m][rec.get("exporter") or "?"] += 1
            records.append({
                "d": rec.get("date") or "",
                "ex": ex_key,
                "im": norm_name(rec.get("importer")),
                "ds": (rec.get("desc") or "")[:220],
                "kg": rec.get("net_kg") or "",
                "uk": rec.get("usd_kg") or "",
                "hs": rec.get("hs10") or "",
                "co": rec.get("dispatch") or "",
            })
    records.sort(key=lambda r: r["d"], reverse=True)
    return {
        "records": records,
        "hs": "8411 (турбины газовые и их части: 841181/841182/841199)",
        "period": "2023-01 … 2026-03",
        "total_records": total,
        "visible_records": visible,
        "by_year": [{"year": y, "n": by_year[y], "kg": round(kg_by_year[y])} for y in sorted(by_year)],
        "top_exporters": [{"name": n, "n": c} for n, c in exporters.most_common(15)],
        "top_importers": [{"name": n, "n": c} for n, c in importers.most_common(15)],
        "countries": [{"code": n, "n": c} for n, c in countries.most_common(10)],
        "models": [
            {"model": m, "n": sum(ex.values()), "exporters": [e for e, _ in ex.most_common(3)]}
            for m, ex in sorted(model_hits.items(), key=lambda kv: -sum(kv[1].values()))
        ],
    }


def main():
    tpl = (ROOT / "site/index.template.html").read_text(encoding="utf-8")
    subs = {
        "__SUPPLIERS_JSON__": (ROOT / "data/suppliers.json").read_text(encoding="utf-8"),
        "__MODELS_JSON__": (ROOT / "data/models.json").read_text(encoding="utf-8"),
        "__PARTS_JSON__": (ROOT / "data/parts.json").read_text(encoding="utf-8"),
        "__PN_JSON__": (ROOT / "data/pn_guide.json").read_text(encoding="utf-8"),
        "__BITRIX_JSON__": (ROOT / "data/bitrix_gt.json").read_text(encoding="utf-8"),
        "__CHECKLIST_JSON__": (ROOT / "data/sgt400_checklist.json").read_text(encoding="utf-8"),
        "__HEAVY_JSON__": (ROOT / "data/heavy_suppliers.json").read_text(encoding="utf-8"),
        "__TFSSUB_JSON__": (ROOT / "data/tfs_subsuppliers.json").read_text(encoding="utf-8"),
        "__BXSITES_JSON__": (ROOT / "data/bitrix_supplier_sites.json").read_text(encoding="utf-8"),
        "__CUSTOMS_JSON__": json.dumps(customs_summary(), ensure_ascii=False),
        "__BUILT_AT__": date.today().isoformat(),
    }
    for k, v in subs.items():
        assert k in tpl, f"нет плейсхолдера {k} в шаблоне"
        tpl = tpl.replace(k, v)
    out = ROOT / "public/index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(tpl, encoding="utf-8")
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
