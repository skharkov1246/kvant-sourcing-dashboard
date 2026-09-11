#!/usr/bin/env python3
"""Сводит проверки наличия по заявке ЛУКОЙЛ в один датасет gt/data/ship_lukoil.json.

Источники (все закоммичены, файл воспроизводим из репозитория):
  gt/data/rfq_demand.json      — сама заявка, оба листа: что и сколько нужно
  gt/data/rfq_prices.json      — наши ценовые вилки и ранняя проверка 08.2026 (checks)
  gt/data/ship_energoseti.json — проверка 505 строк «Энергосетей» 09.2026
  gt/data/ship_sweep.json      — сплошная проверка остатка 863 строк 09.2026

Позднейшая проверка перекрывает раннюю. Строки заявки без проверки попадают в
датасет с вердиктом not_checked — так видно реальное покрытие, а не подогнанное.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEMAND = ROOT / "gt/data/rfq_demand.json"
PRICES = ROOT / "gt/data/rfq_prices.json"
SHIP = ROOT / "gt/data/ship_energoseti.json"
SWEEP = ROOT / "gt/data/ship_sweep.json"
DST = ROOT / "gt/data/ship_lukoil.json"

VERDICTS = ("in_stock", "available_lead", "pn_found_no_stock", "oem_only",
            "pn_not_found", "not_checked")

# ранняя проверка 08.2026 писала свой словарь вердиктов — приводим к общему
LEGACY_NOTE = {
    "confirmed": "ранняя проверка 08.2026: карточка подтверждена",
    "price_differs": "ранняя проверка 08.2026: карточка есть, цена расходится с основанием",
    "dead_link": "ранняя проверка 08.2026: ссылка-основание мертва",
    "not_a_seller": "ранняя проверка 08.2026: источник оказался не продавцом",
    "pn_not_found": "ранняя проверка 08.2026: артикул не найден",
}


def s(x) -> str:
    return str(x if x is not None else "").strip()


def num(x):
    """Цена может прийти строкой ('1019.23'), числом или пустотой."""
    if x in (None, "", "-"):
        return None
    try:
        return float(str(x).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def blank(pn: str) -> dict:
    return {
        "pn": pn, "verdict": "not_checked", "seller": "", "seller_url": "",
        "seller_country": "", "kind": "unknown", "in_stock": "unknown", "stock_qty": "",
        "lead_time": "", "price": None, "currency": "USD", "pack_qty": 1,
        "covers_qty": "unknown", "real_maker": "", "real_pn": "", "substitute": "",
        "note": "", "checked_by": "",
    }


def norm(raw: dict, source: str) -> dict:
    """Приводит запись проверки любого поколения к общей схеме."""
    r = blank(s(raw.get("pn")))
    v = s(raw.get("verdict"))
    if v in VERDICTS:
        r["verdict"] = v
    else:
        # словарь ранней проверки: вердикт выводим из подтверждённого наличия
        ins = s(raw.get("in_stock"))
        if ins == "yes":
            r["verdict"] = "in_stock"
        elif ins == "no":
            r["verdict"] = "pn_found_no_stock"
        elif v in ("confirmed", "price_differs"):
            r["verdict"] = "pn_found_no_stock"
        else:
            r["verdict"] = "pn_not_found"
    for k in ("seller", "seller_url", "seller_country", "stock_qty", "lead_time",
              "real_maker", "real_pn", "substitute", "note"):
        r[k] = s(raw.get(k))
    for k, allowed in (("kind", ("oem", "component_maker", "aftermarket", "unknown")),
                       ("in_stock", ("yes", "no", "conditional", "unknown")),
                       ("covers_qty", ("full", "partial", "no", "unknown"))):
        val = s(raw.get(k))
        r[k] = val if val in allowed else r[k]
    r["price"] = num(raw.get("price"))
    if r["price"] is None:  # ранняя проверка держала цену в real_lo/real_hi
        r["price"] = num(raw.get("real_lo"))
    r["currency"] = s(raw.get("currency")) or "USD"
    try:
        r["pack_qty"] = int(raw.get("pack_qty") or 1) or 1
    except (TypeError, ValueError):
        r["pack_qty"] = 1
    if v in LEGACY_NOTE:
        r["note"] = (LEGACY_NOTE[v] + ". " + r["note"]).strip()
    r["checked_by"] = source
    return r


def load_rows(path: Path, key: str) -> list:
    if not path.exists():
        return []
    doc = json.loads(path.read_text())
    return doc[key] if isinstance(doc, dict) else doc


def main() -> int:
    demand = json.loads(DEMAND.read_text())["rows"]
    prices_doc = json.loads(PRICES.read_text())
    price = {p["pn"]: p for p in prices_doc["prices"]}

    # заявка: агрегируем количество по артикулу, лист запоминаем
    items: dict[str, dict] = {}
    for row in demand:
        pn = s(row.get("pn"))
        if not pn:
            continue
        it = items.setdefault(pn, {
            "pn": pn, "sheet": row.get("sheet", ""), "man": s(row.get("man")),
            "model": s(row.get("model")), "name": s(row.get("name")),
            "cat": s(row.get("cat")), "qty": 0, "unit": s(row.get("unit")) or "шт",
        })
        try:
            it["qty"] += int(row.get("qty") or 0)
        except (TypeError, ValueError):
            pass

    # проверки от ранней к поздней — поздняя перекрывает
    checks: dict[str, dict] = {}
    for path, key, src in (
        (PRICES, "checks", "проверка 08.2026"),
        (SHIP, "rows", "проверка 505 строк 09.2026"),
        (SWEEP, "rows", "сплошная проверка остатка 09.2026"),
    ):
        rows = prices_doc["checks"] if path == PRICES else load_rows(path, key)
        for raw in rows:
            pn = s(raw.get("pn"))
            if pn in items:
                checks[pn] = norm(raw, src)

    out = []
    for pn, it in items.items():
        rec = dict(it)
        p = price.get(pn)
        rec["usd_lo"] = p["usd_lo"] if p else None
        rec["usd_hi"] = p["usd_hi"] if p else None
        rec["conf"] = p["conf"] if p else ""
        rec.update({k: v for k, v in (checks.get(pn) or blank(pn)).items() if k != "pn"})
        out.append(rec)

    out.sort(key=lambda r: (r["sheet"], r["cat"], r["pn"]))
    DST.write_text(json.dumps({
        "updated": date.today().isoformat(),
        "source": "Заявка ЛУКОЙЛ (листы «Энергосети» и «НВН»): наличие у продавцов по всей номенклатуре",
        "method": "gt/tools/ship_merge.py сводит gt/data/rfq_demand.json с тремя поколениями "
                  "проверок: rfq_prices.json:checks (08.2026), ship_energoseti.json (505 строк) "
                  "и ship_sweep.json (остаток 863). Поздняя проверка перекрывает раннюю; "
                  "строки без проверки помечены not_checked.",
        "rows": out,
    }, ensure_ascii=False, indent=1))

    checked = sum(1 for r in out if r["verdict"] != "not_checked")
    print(f"позиций {len(out)}, проверено {checked}, без проверки {len(out) - checked}")
    for sheet in sorted({r["sheet"] for r in out}):
        n = [r for r in out if r["sheet"] == sheet]
        st = sum(1 for r in n if r["verdict"] == "in_stock")
        print(f"  {sheet}: {len(n)} позиций, на складе {st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
