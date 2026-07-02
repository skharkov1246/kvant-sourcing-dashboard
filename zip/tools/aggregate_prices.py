#!/usr/bin/env python3
"""Слияние результатов добычи цен (Workflow) в zip/data/price_records.json.

Вход: JSON-массив результатов вида
  [{"pp": 3, "found": true, "records":[{source,unit_price,currency,...,url,confidence,note}], "note": "..."}]
(несколько таких файлов можно передать — сольются).

Маппинг pp → position_id берётся из zip/data/positions.json (поле id — реальный id в Supabase).
Позиции без находок получают отметку в opendb-логе (файл zip/data/mining_log.json) — честное покрытие.

Использование:
  python zip/tools/aggregate_prices.py <results1.json> [results2.json ...]
"""
import json
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

FIELDS = ("year", "source", "importer", "exporter", "country", "qty", "qty_unit",
          "unit_price", "total_price", "currency", "incoterms", "customs_decl",
          "hs_code", "url", "confidence", "note")


def main(argv):
    positions = json.loads((DATA / "positions.json").read_text(encoding="utf-8"))
    pp2id = {p.get("pp"): p.get("id") for p in positions}

    existing = json.loads((DATA / "price_records.json").read_text(encoding="utf-8"))
    # ключ дедупликации: (position_id, url, unit_price, year)
    seen = {(r.get("position_id"), r.get("url"), r.get("unit_price"), r.get("year"))
            for r in existing}
    out = list(existing)

    found_pps, empty_pps = set(), set()
    added = 0
    for path in argv:
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
        results = blob.get("results", blob) if isinstance(blob, dict) else blob
        for item in results:
            pp = item.get("pp")
            pid = pp2id.get(pp)
            if pid is None:
                continue
            recs = item.get("records") or []
            if recs:
                found_pps.add(pp)
            else:
                empty_pps.add(pp)
            for r in recs:
                rec = {"position_id": pid, "created_by": "workflow"}
                for f in FIELDS:
                    if r.get(f) not in (None, ""):
                        rec[f] = r[f]
                rec.setdefault("source", "прочее")
                rec.setdefault("currency", "USD")
                rec.setdefault("confidence", "low")
                key = (pid, rec.get("url"), rec.get("unit_price"), rec.get("year"))
                if key in seen:
                    continue
                seen.add(key)
                out.append(rec)
                added += 1

    (DATA / "price_records.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # лог покрытия
    empty_only = empty_pps - found_pps
    log = {
        "updated": date.today().isoformat(),
        "positions_with_prices": sorted(found_pps),
        "positions_no_open_price": sorted(empty_only),
        "total_records": len(out),
    }
    (DATA / "mining_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"price_records.json: +{added} новых, всего {len(out)}")
    print(f"позиций с ценой: {len(found_pps)}, без открытой цены: {len(empty_only)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1:])
