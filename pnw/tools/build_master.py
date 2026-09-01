# -*- coding: utf-8 -*-
"""Единый справочник номенклатуры КВАНТ.

Сводит номенклатуру всех разделов в одну таблицу, присваивает внутренний
номер KV и раскладывает ВСЕ известные номера детали в отдельную таблицу,
чтобы поиск работал по любому из них.

Выход:
  pnw/data/item_master.json — одна строка = одна деталь (KV — навсегда)
  pnw/data/numbers.json     — номер | тип | владелец | KV
  pnw/data/kv_registry.json — реестр выданных номеров (не переиспользуются)

Типы номеров:
  свой         — KV, внутренний
  бренд        — под чьим шильдиком продаётся (Atlas Copco, Sandvik)
  изготовитель — кто реально делает узел (SKF, Donaldson, Parker)
  поставщик    — номер в прайсе конкретного поставщика (П1/П2/П3)
  аналог       — кросс вторичного рынка
  заказчика    — номер в номенклатуре заказчика
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pnw" / "tools"))
from kv_number import make, load_registry, save_registry

OUT = ROOT / "pnw" / "data"


def norm(s):
    return re.sub(r"[^A-Z0-9А-Я]", "", str(s or "").upper())


def main():
    items, numbers = [], []
    by_norm = {}          # норм-номер → индекс детали (склейка одинаковых)
    reg = load_registry()
    seq = reg.get("next_seq", 1)
    assigned = reg.get("assigned", {})   # устойчивый ключ детали → выданный KV
    reused = 0

    def add_item(src, name, brand, maker, node, machine, extra, key):
        """key — устойчивый ключ детали. Номер выдаётся ОДИН РАЗ и навсегда:
        при пересборке справочника деталь получает тот же KV, что и раньше."""
        nonlocal seq, reused
        if key in assigned:
            kv = assigned[key]
            reused += 1
        else:
            kv = make(seq)
            assigned[key] = kv
            seq += 1
        items.append({
            "kv": kv, "name": name, "brand": brand, "maker": maker,
            "node": node, "machine": machine, "section": src, **extra,
        })
        return len(items) - 1, kv

    def add_number(idx, number, kind, owner):
        n = norm(number)
        if not n or len(n) < 3:
            return
        numbers.append({"kv": items[idx]["kv"], "number": str(number).strip(),
                        "number_norm": n, "kind": kind, "owner": owner or ""})

    # ── 1. ЗИП ГШО ───────────────────────────────────────────────────────
    Z = json.loads((ROOT / "zip" / "data" / "positions.json").read_text(encoding="utf-8"))
    zmap = {}
    for p in Z:
        idx, kv = add_item("ЗИП ГШО", p.get("name"), p.get("oem"), "",
                           p.get("category"), p.get("model"),
                           {"equipment": p.get("target_equipment"),
                            "hs": p.get("hs_code"), "src_id": p.get("id"),
                            "material": p.get("material_type"),
                            "note": (p.get("note") or "")[:120]},
                           key=f"zip:{p['id']}")
        zmap[p["id"]] = idx
        by_norm.setdefault(norm(p.get("catalog_no")), idx)
        add_number(idx, p.get("catalog_no"), "бренд", p.get("oem"))

    # кросс-номера ЗИП, разобранные из aliases
    cr = OUT / "crossrefs.json"
    if cr.exists():
        kind_map = {"oem": "бренд", "analog": "аналог", "unknown": "аналог"}
        for r in json.loads(cr.read_text(encoding="utf-8"))["rows"]:
            idx = zmap.get(r["position_id"])
            if idx is not None:
                add_number(idx, r["number"], kind_map.get(r["kind"], "аналог"), r.get("brand"))

    # ── 2. PN-база ГТУ ───────────────────────────────────────────────────
    G = json.loads((ROOT / "gt" / "data" / "pn_db.json").read_text(encoding="utf-8"))["rows"]
    for r in G:
        pn = r.get("pn")
        if not pn:
            continue
        n = norm(pn)
        if n in by_norm:                      # деталь уже есть — добавляем только номера
            idx = by_norm[n]
        else:
            idx, kv = add_item("ГТУ", r.get("desc"), r.get("oem"), r.get("mk"),
                               r.get("seg"), r.get("mach"),
                               {"client": r.get("cli"), "qty": r.get("qty"),
                                "note": (r.get("sn") or "")[:120]},
                               key=f"gt:{n}")
            by_norm[n] = idx
            add_number(idx, pn, "бренд", r.get("oem"))
        if r.get("mpn"):
            add_number(idx, r["mpn"], "изготовитель", r.get("mk"))

    reg["next_seq"] = seq
    reg["updated"] = "2026-09-01"
    reg["assigned"] = assigned
    reg["note"] = ("assigned: устойчивый ключ детали → выданный номер. "
                   "Номера НЕ переиздаются: при пересборке деталь получает тот же KV. "
                   "Освободившиеся номера не переиспользуются.")
    save_registry(reg)
    print(f"номеров переиспользовано из реестра: {reused}, выдано новых: {len(assigned)-reused}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "item_master.json").write_text(json.dumps(
        {"updated": "2026-09-01", "count": len(items), "items": items},
        ensure_ascii=False), encoding="utf-8")
    (OUT / "numbers.json").write_text(json.dumps(
        {"updated": "2026-09-01", "count": len(numbers), "rows": numbers},
        ensure_ascii=False), encoding="utf-8")

    from collections import Counter
    print(f"деталей в справочнике : {len(items)}")
    for k, v in Counter(i["section"] for i in items).most_common():
        print(f"   {k:10} {v}")
    print(f"номеров всего         : {len(numbers)}")
    for k, v in Counter(n["kind"] for n in numbers).most_common():
        print(f"   {k:14} {v}")
    print(f"деталей с изготовителем под шильдиком: "
          f"{sum(1 for i in items if str(i.get('maker') or '').strip())}")
    print(f"номер первый/последний: {items[0]['kv']} … {items[-1]['kv']}")


if __name__ == "__main__":
    main()
