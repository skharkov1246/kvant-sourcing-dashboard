#!/usr/bin/env python3
"""Сборка gpu/data/demand.json — реальный спрос по ГПУ из наших рабочих файлов.

Источник — файлы ГТУ-библиотеки, куда исторически попал непрофильный (не турбинный)
спрос по газопоршневым:
  gt/data/rfq_demand.json  строки заявки (лист «Энергосети»): бренд, PN, кол-во, категория
  gt/data/rfq_prices.json  оценка вилки цены по PN (usd_lo/usd_hi, уверенность, кроссы)
                           + checks — живые проверки конкретных продавцов

Скрипт НЕ придумывает данные: он только фильтрует по ГПУ-брендам и соединяет три
среза по PN. Позиции Siemens (турбинные) отбрасываются — они не про ГПУ.

Запуск:  python3 gpu/tools/build_demand.py
"""
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent

# бренды ГПУ, которые считаем своими (Siemens в этом листе — турбинные позиции)
GPU_BRANDS = {"Cummins", "Jenbacher/INNIO", "Caterpillar", "MWM", "Waukesha"}

# система по категории заявки + по тексту наименования
SYSTEM_BY_CAT = {
    "зажигание и свечи": "ignition",
    "фильтры": "filtration",
    "прокладки и уплотнения": "seals",
    "датчики и КИП": "sensors",
    "шланги и трубки": "hoses",
    "механика и приводы": "mech",
    "электрика и автоматика": "control",
    "клапаны и арматура": "gas",
    "насосы": "cooling",
    "крепёж": "hardware",
    "прочее": "other",
}
# уточнение системы по ключевым словам в наименовании (сильнее категории)
SYSTEM_BY_WORD = [
    (r"турбокомпрессор|турбина", "air"),
    (r"газов\w* фильтр|вставка газового", "gas"),
    (r"форкамер", "gas"),
    (r"катушк\w* зажигания|свеч|колпач\w* свеч|модуль управления зажиганием|наконечник контактный", "ignition"),
    (r"ГБЦ|головк\w* цилиндра|прокладок ГБЦ", "head"),
    (r"кольцо маслосъемное|поршн", "cpg"),
    (r"подогрев|ТЭН|охлажда\w* жидкост|насос охлажд", "cooling"),
    (r"интеркулер|охладител\w* смеси", "air"),
    (r"элемент питания|батаре", "control"),
]

SYSTEMS = {
    "ignition": "Зажигание",
    "cpg": "ЦПГ (поршень–гильза–кольца)",
    "head": "ГБЦ и клапаны",
    "gas": "Газовый тракт и смесеобразование",
    "air": "Воздух и наддув",
    "filtration": "Фильтрация",
    "seals": "Уплотнения и прокладки",
    "cooling": "Охлаждение",
    "sensors": "Датчики и КИП",
    "control": "САУ и электрика",
    "hoses": "Шланги и трубопроводы",
    "mech": "Механика и приводы",
    "hardware": "Крепёж",
    "other": "Прочее",
}


def system_of(row):
    name = (row.get("name") or "") + " " + (row.get("model") or "")
    for rx, sysname in SYSTEM_BY_WORD:
        if re.search(rx, name, re.I):
            return sysname
    return SYSTEM_BY_CAT.get(row.get("cat"), "other")


def main():
    demand = json.loads((REPO / "gt/data/rfq_demand.json").read_text(encoding="utf-8"))
    prices = json.loads((REPO / "gt/data/rfq_prices.json").read_text(encoding="utf-8"))

    by_pn = {str(p["pn"]): p for p in prices.get("prices", [])}
    checks_by_pn = {}
    for c in prices.get("checks", []):
        checks_by_pn.setdefault(str(c.get("pn")), []).append(c)

    rows = []
    for r in demand.get("rows", []):
        if r.get("man") not in GPU_BRANDS:
            continue
        pn = str(r.get("pn") or "").strip()
        p = by_pn.get(pn) or {}
        ch = checks_by_pn.get(pn) or []
        lo, hi = p.get("usd_lo"), p.get("usd_hi")
        qty = r.get("qty") or 0
        row = {
            "pn": pn,
            "name": r.get("name"),
            "man": r.get("man"),
            "qty": qty,
            "unit": r.get("unit") or "шт",
            "cat": r.get("cat"),
            "sys": system_of(r),
            "usd_lo": lo,
            "usd_hi": hi,
            "conf": p.get("conf"),
            "cross": bool(p.get("cross")),
            "gap": p.get("gap"),
            "basis": p.get("basis"),
            "url": p.get("url"),
            "note": p.get("note"),
            # деньги лота по нижней и верхней границе — то, ради чего торгуемся
            "lot_lo": round(lo * qty) if isinstance(lo, (int, float)) and qty else None,
            "lot_hi": round(hi * qty) if isinstance(hi, (int, float)) and qty else None,
            "checks": [{
                "verdict": c.get("verdict"),
                "seller": c.get("seller"),
                "url": c.get("seller_url"),
                "lo": c.get("real_lo"),
                "hi": c.get("real_hi"),
                "stock": c.get("in_stock"),
                "lead": c.get("lead_time"),
                "note": c.get("note"),
            } for c in ch],
        }
        rows.append(row)

    rows.sort(key=lambda x: -(x.get("lot_hi") or 0))

    by_sys = Counter(r["sys"] for r in rows)
    by_man = Counter(r["man"] for r in rows)
    total_lo = sum(r["lot_lo"] or 0 for r in rows)
    total_hi = sum(r["lot_hi"] or 0 for r in rows)
    priced = [r for r in rows if r.get("usd_lo") is not None]
    checked = [r for r in rows if r["checks"]]

    out = {
        "updated": date.today().isoformat(),
        "source": "Лист «Энергосети» рабочей заявки (gt/data/rfq_demand.json) + оценка вилок и живые "
                  "проверки продавцов (gt/data/rfq_prices.json). Сборка: gpu/tools/build_demand.py",
        "caveat": "Вилки цен — оценка по открытой рознице, а не наши закупочные. Нижняя граница почти "
                  "везде = азиатский аналог, верхняя = дилер OEM. Реальная цена лота определяется КП, "
                  "а не этой таблицей. Позиции с conf=C проверены слабо — их первыми и уточнять.",
        "systems": SYSTEMS,
        "stats": {
            "positions": len(rows),
            "priced": len(priced),
            "checked": len(checked),
            "total_qty": sum(r["qty"] for r in rows),
            "lot_usd_lo": total_lo,
            "lot_usd_hi": total_hi,
            "by_system": [{"sys": k, "n": v} for k, v in by_sys.most_common()],
            "by_man": [{"man": k, "n": v} for k, v in by_man.most_common()],
        },
        "rows": rows,
    }
    dst = ROOT / "data/demand.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"OK → {dst}: {len(rows)} позиций, {len(priced)} с ценой, {len(checked)} с живой проверкой; "
          f"лот ${total_lo:,} … ${total_hi:,}".replace(",", " "))


if __name__ == "__main__":
    main()
