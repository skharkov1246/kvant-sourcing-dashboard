#!/usr/bin/env python3
"""Агрегация таможни glbs.io (customs_*.json) → price_records + customs_market.json.

Вход:  zip/customs/out/customs_<hs>.json  (компактные ГТД-совпадения из glbs_pull.mjs)
Выход:
  1) добавления в zip/data/price_records.json — по strong-совпадениям (наш PN в
     описании ГТД) с привязкой к position_id (source=«таможня ГТД (glbs.io)»);
  2) zip/data/customs_market.json — рыночная сводка: перфораторный импорт в РФ
     (годы, страны, топ импортёров/экспортёров, бенчмарк USD/кг), плюс прямой
     импорт от OEM (Sandvik/Epiroc/Atlas Copco/Sandvik-Tamrock).

Цены: в режиме search инвойс-стоимость (G42) маскируется «*», но USD/кг (USDKG)
часто открыт — его и используем как ценовой бенчмарк. Реальную €/ед. даст
последующий method=save.
"""
import json
import re
import statistics
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "customs" / "out"

norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def norm_org(s):
    """Привести юр.форму к короткой и снять кавычки — иначе один завод
    двоится как «Общество с ограниченной ответственностью X» и «ООО X»."""
    s = re.sub(r"\s+", " ", str(s or "").strip())
    repl = [
        (r"ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО", "ПАО"),
        (r"ЗАКРЫТОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО", "ЗАО"),
        (r"ОТКРЫТОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО", "ОАО"),
        (r"АКЦИОНЕРНОЕ ОБЩЕСТВО", "АО"),
        (r"ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ", "ООО"),
        (r"ИНДИВИДУАЛЬНЫЙ ПРЕДПРИНИМАТЕЛЬ", "ИП"),
    ]
    up = s.upper()
    for pat, short in repl:
        up = re.sub(pat, short, up)
    up = up.replace("«", '"').replace("»", '"').replace("'", '"')
    up = up.split("(")[0]  # снять дублирующий хвост «ООО "X" (ООО "X"…)»
    up = re.sub(r'\s*"\s*', '"', up).strip().strip('"')
    return up
# перфоратор-специфика (узкий фильтр — отсечь генерик «буровая лебёдка» и пр.)
PERF_RE = re.compile(
    r"перфоратор|гидроперфоратор|гидроударник|drifter|rock ?drill|"
    r"COP\s?\d|HLX|HL\d{3}|RD\d{3}|коронк|хвостовик|shank|butt.?bit|"
    r"буриль\w*\s+молот|ударно-?вращ|штанг\w*\s+буров", re.I)
OEM_RE = re.compile(r"sandvik|epiroc|atlas ?copco|tamrock|montabert|furukawa", re.I)


def fnum(v):
    """'13.27'→13.27; маскированное '5*6.47'/'' → None."""
    s = str(v or "").strip()
    if not s or "*" in s:
        return None
    try:
        f = float(s.replace(",", "."))
        return f if f > 0 else None
    except ValueError:
        return None


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 2)


def load_positions_tokens():
    pos = json.loads((DATA / "positions.json").read_text(encoding="utf-8"))
    tok2pid = {}
    for p in pos:
        for raw in (p.get("catalog_no"), p.get("aliases")):
            for t in re.split(r"[\s,;/()]+", str(raw or "")):
                n = norm(t)
                if re.search(r"\d", n) and len(n) >= 6:
                    tok2pid.setdefault(n, set()).add(p["id"])
    return pos, tok2pid


def main():
    files = sorted(OUT.glob("customs_*.json"))
    if not files:
        print("нет customs_*.json — сначала прогон glbs_pull.mjs")
        return
    pos, tok2pid = load_positions_tokens()
    strong_all, weak_all = [], []
    hs_list = []
    for f in files:
        c = json.loads(f.read_text(encoding="utf-8"))
        hs_list.append(c.get("hs"))
        for x in c.get("matched", []):
            (strong_all if x.get("tag") == "strong" else weak_all).append(x)

    # --- 1) price_records из strong (привязка к позиции) ---
    CUSTOMS_SRC = "таможня ГТД (glbs.io)"
    prices = json.loads((DATA / "price_records.json").read_text(encoding="utf-8"))
    # чистка опечаток в источниках цен (латиница-гомоглифы/«маркетплайс» → «маркетплейс»)
    homo = str.maketrans("aeopcyxktmhb", "аеорсухктмнв")
    for r in prices:
        s = (r.get("source") or "").strip()
        canon = re.sub(r"[^а-я]", "", s.lower().translate(homo))
        if canon in ("маркетплейс", "маркетплайс"):
            r["source"] = "маркетплейс"
    # идемпотентно: убираем прежние таможенные записи и пересобираем начисто
    prices = [r for r in prices if (r.get("source") or "") != CUSTOMS_SRC]
    existing_keys = set()
    max_id = max([r.get("id", 0) for r in prices] + [0])
    added = 0
    for x in strong_all:
        pids = tok2pid.get(x.get("pn"))
        if not pids:
            continue
        year = int((x.get("date") or "0")[:4] or 0) or None
        usd_kg = fnum(x.get("usd_kg"))
        note = f"ГТД: {x.get('desc','')[:90]}"
        if usd_kg:
            note = f"USD/кг≈{usd_kg}; нетто {x.get('net_kg','?')}кг · " + note
        for pid in pids:
            key = (pid, year, x.get("importer", "")[:60])
            if key in existing_keys:
                continue
            existing_keys.add(key)
            max_id += 1
            prices.append({
                "id": max_id, "position_id": pid, "year": year,
                "source": CUSTOMS_SRC,
                "importer": x.get("importer", "")[:120],
                "exporter": x.get("exporter", "")[:120],
                "country": x.get("origin", ""),
                "qty": None, "unit_price": None, "currency": x.get("cur", ""),
                "incoterms": x.get("incoterms", ""), "url": "",
                "note": note[:200], "confidence": "high",
            })
            added += 1
    (DATA / "price_records.json").write_text(
        json.dumps(prices, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- 2) customs_market.json (рыночная сводка) ---
    perf = [x for x in (strong_all + weak_all) if PERF_RE.search(x.get("desc", ""))]

    def agg_by(items, keyf, topn=15, orgnorm=False):
        d = {}
        for x in items:
            k = (keyf(x) or "").strip()
            if orgnorm:
                k = norm_org(k)
            if not k:
                continue
            e = d.setdefault(k, {"cnt": 0, "kg": []})
            e["cnt"] += 1
            u = fnum(x.get("usd_kg"))
            if u:
                e["kg"].append(u)
        rows = [{"name": k[:70], "cnt": v["cnt"],
                 "usd_kg_med": pct(v["kg"], 0.5), "n_price": len(v["kg"])}
                for k, v in d.items()]
        rows.sort(key=lambda r: -r["cnt"])
        return rows[:topn]

    def by_year(items):
        d = {}
        for x in items:
            y = (x.get("date") or "")[:4]
            if re.fullmatch(r"20\d\d", y):
                d[y] = d.get(y, 0) + 1
        return dict(sorted(d.items()))

    all_kg = [fnum(x.get("usd_kg")) for x in perf]

    # бенчмарк USD/кг по 4-значному HS (для контекста в карточке любой позиции)
    hs_kg = {}
    for x in perf:
        h = str(x.get("hs10") or "")[:4]
        if re.fullmatch(r"\d{4}", h):
            hs_kg.setdefault(h, []).append(fnum(x.get("usd_kg")))
    by_hs4 = {h: {"declarations": len(v),
                  "usd_kg_p25": pct(v, 0.25), "usd_kg_med": pct(v, 0.5),
                  "usd_kg_p75": pct(v, 0.75), "n_price": len([k for k in v if k])}
              for h, v in hs_kg.items() if len(v) >= 5}

    # отслеживаемые конкуренты-локализаторы (показываем всегда, вне топа) —
    # по всем совпадениям, не только перфоратор-подмножеству
    WATCH = {"Машиностроительный холдинг": r"машиностроительн\w* холдинг|\bМХ\b",
             "Пумори / Уралмаш-инструмент": r"пумори"}
    watched = []
    allm = strong_all + weak_all
    for label, pat in WATCH.items():
        rx = re.compile(pat, re.I)
        hits = [x for x in allm if rx.search(x.get("importer", "") or "")]
        if not hits:
            continue
        kgv = [fnum(x.get("usd_kg")) for x in hits]
        yrs = {}
        for x in hits:
            y = (x.get("date") or "")[:4]
            if re.fullmatch(r"20\d\d", y):
                yrs[y] = yrs.get(y, 0) + 1
        origins = {}
        for x in hits:
            o = x.get("origin", "")
            if o:
                origins[o] = origins.get(o, 0) + 1
        watched.append({
            "name": label, "declarations": len(hits),
            "usd_kg_med": pct(kgv, 0.5), "n_price": len([k for k in kgv if k]),
            "by_year": dict(sorted(yrs.items())),
            "top_origins": sorted(origins.items(), key=lambda a: -a[1])[:5],
        })
    oem_direct = [{
        "year": int((x.get("date") or "0")[:4] or 0) or None,
        "importer": x.get("importer", "")[:60], "exporter": x.get("exporter", "")[:60],
        "origin": x.get("origin", ""), "usd_kg": fnum(x.get("usd_kg")),
        "desc": x.get("desc", "")[:100],
    } for x in perf if OEM_RE.search(x.get("exporter", "") or "")]
    oem_direct = [o for o in oem_direct if o["year"]][:120]

    market = {
        "updated": date.today().isoformat(),
        "source": "Таможенная статистика РФ (импорт ИМ), glbs.io / Глобус-ВЭД",
        "hs": [h for h in hs_list if h], "note": "USD/кг — открытый бенчмарк режима search; "
        "маскированные значения пропущены; реальную инвойс-стоимость даёт method=save.",
        "perforator": {
            "declarations": len(perf),
            "by_year": by_year(perf),
            "origins": agg_by(perf, lambda x: x.get("origin"), 12),
            "top_importers": agg_by(perf, lambda x: x.get("importer"), 20, orgnorm=True),
            "top_exporters": agg_by(perf, lambda x: x.get("exporter"), 20),
            "usd_kg": {"p25": pct(all_kg, 0.25), "median": pct(all_kg, 0.5),
                       "p75": pct(all_kg, 0.75), "n_price": len([k for k in all_kg if k])},
        },
        "oem_direct": oem_direct,
        "watched": watched,
        "by_hs4": by_hs4,
        "strong_linked": added,
    }
    (DATA / "customs_market.json").write_text(
        json.dumps(market, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- 3) полный эксплорер деклараций (drill-down, «терминал») ---
    # перфораторный срез + ВСЕ strong (наш PN в описании) — даже вне перфоратор-фильтра
    explorer = perf + [x for x in strong_all if not PERF_RE.search(x.get("desc", ""))]
    # массив массивов ради компактности: [importer, exporter, origin, year, usd_kg, hs10, desc, pn]
    cols = ["importer", "exporter", "origin", "year", "usd_kg", "hs", "desc", "pn"]
    decl = []
    for x in explorer:
        yr = (x.get("date") or "")[:4]
        decl.append([
            norm_org(x.get("importer", ""))[:60],
            (x.get("exporter", "") or "")[:52],
            x.get("origin", ""),
            yr if re.fullmatch(r"20\d\d", yr) else "",
            fnum(x.get("usd_kg")) or "",
            str(x.get("hs10") or "")[:10],
            (x.get("desc", "") or "")[:90],
            (x.get("pn") or "") if x.get("tag") == "strong" else "",
        ])
    decl.sort(key=lambda r: (r[3] or "", r[0]))
    (DATA / "customs_declarations.json").write_text(
        json.dumps({"updated": date.today().isoformat(), "cols": cols, "rows": decl},
                   ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"деклараций в эксплорер: {len(decl)}")

    print(f"strong→price_records: +{added} (всего {len(prices)})")
    print(f"perforator-декларации: {len(perf)} | OEM-прямой импорт: {len(oem_direct)}")
    print(f"USD/кг перфоратор: {market['perforator']['usd_kg']}")
    print(f"годы: {market['perforator']['by_year']}")
    print(f"топ-страны: {[(r['name'], r['cnt']) for r in market['perforator']['origins'][:6]]}")


if __name__ == "__main__":
    main()
