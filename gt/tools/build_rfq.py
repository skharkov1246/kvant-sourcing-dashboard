#!/usr/bin/env python3
"""Сборка gt/public/rfq.html — план покрытия запросами заявки ЛУКОЙЛ (критичный импортный ЗИП).

Читает gt/data/rfq_demand.json (позиции из XLSX), подбирает поставщиков из базы
(dossiers + research + checklist + история Bitrix), режет на пачки «направление × категория»,
считает вероятность ответа по прозрачным правилам и пишет самодостаточную страницу
с отметками сорсеров (localStorage).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = lambda f: json.load(open(ROOT / "data" / f))


def nn(s):
    return re.sub(r"[^a-zа-яё0-9]", "", (s or "").lower())


# ── пул поставщиков ──────────────────────────────────────────────────────────
def build_pool():
    dossiers = D("dossiers.json")["dossiers"]
    hist = {}
    for h in D("bitrix_history.json")["history"]:
        hist[nn(h["name"])] = h["items"]
    sites, extra = {}, {}
    for src, key in [("research_suppliers.json", "rows"), ("sgt400_checklist.json", "rows"),
                     ("heavy_suppliers.json", "rows"), ("tfs_subsuppliers.json", "rows")]:
        for x in D(src)[key]:
            k = nn(x.get("name", ""))
            if not k:
                continue
            sites.setdefault(k, x.get("site") or "")
            e = extra.setdefault(k, {"what": "", "country": "", "email": ""})
            e["what"] = e["what"] or (x.get("what") or "") + " " + (x.get("link") or "")
            e["country"] = e["country"] or (x.get("country") or "")
            e["email"] = e["email"] or (x.get("email") or "")
    for x in json.load(open(ROOT / "data" / "suppliers.json")):
        k = nn(x.get("name", ""))
        sites.setdefault(k, x.get("site") or "")
        e = extra.setdefault(k, {"what": "", "country": "", "email": ""})
        e["what"] = e["what"] or (x.get("what") or "")
        e["country"] = e["country"] or (x.get("country") or "")
    bx = D("bitrix_supplier_sites.json")
    for n, v in bx.get("confirmed", {}).items():
        k = nn(n)
        if isinstance(v, dict):
            sites.setdefault(k, v.get("site") or "")
            e = extra.setdefault(k, {"what": "", "country": "", "email": ""})
            e["email"] = e["email"] or (v.get("email") or "")
            e["what"] = e["what"] or (v.get("ev") or "")

    pool = {}
    for name, d in dossiers.items():
        k = nn(name)
        it = hist.get(k, [])
        stages = " ".join(str(i.get("stage") or "") for i in it).lower()
        answered = bool(re.search(r"кп|предложени|получено|счет|счёт|договор|поставк", stages))
        models = set()
        for i in it:
            for m in i.get("models") or []:
                models.add(m.upper())
        p = d.get("supplied_proof", "none")
        prank = 1 if (it or p == "shipment") else 2 if p == "named_client" else 3 if p == "catalog" else 4
        pool[k] = {
            "name": name, "country": extra.get(k, {}).get("country", ""),
            "site": sites.get(k, ""),
            "email": d.get("contact_email") or extra.get(k, {}).get("email", ""),
            "phone": d.get("contact_phone", ""), "person": d.get("contact_person", ""),
            "hook": d.get("hook", ""), "note": d.get("note", ""),
            "what": (extra.get(k, {}).get("what", "") + " " + d.get("note", "")).strip(),
            "prank": prank, "bx": len(it), "bx_answered": answered,
            "bx_models": sorted(models),
        }
    return pool


# ── ключевые слова профиля под категорию ─────────────────────────────────────
CAT_KEYS = {
    "прокладки и уплотнения": r"уплотнен|прокладк|seal|gasket|o-ring|кольц|манжет|graphite|ptfe|elastomer|packing",
    "клапаны и арматура": r"клапан|valve|арматур|actuator|регулятор|дозирован",
    "фильтры": r"фильтр|filter|сепаратор|filtration",
    "датчики и КИП": r"датчик|sensor|кип|instrument|термопар|transmitter|давлени|мониторинг|vibration|bently",
    "зажигание и свечи": r"свеч|зажиган|ignit|катушк|exciter",
    "электрика и автоматика": r"электро|модул|плат|контроллер|автоматик|control|electric|s7|simatic|panel|switchgear|шкаф|obsolete|электрик",
    "подшипники": r"подшипник|bearing",
    "насосы": r"насос|pump",
    "горячая часть и камера сгорания": r"лопатк|blade|nozzle|сопл|камер|combust|жаров|hot gas|литьё|литье|casting|покрыти|coating",
    "крепёж": r"крепеж|крепёж|болт|fastener|шпильк|stud|nimonic|inconel bolt",
    "шланги и трубки": r"шланг|hose|рукав|tubing|трубк",
    "масла и химия": r"масло|смазк|lubric|chemical|химия",
    "механика и приводы": r"привод|actuator|редуктор|gear|двигател|motor|муфт|coupling|цилиндр|гидравлик",
    "ремкомплекты и наборы": r"ремкомплект|overhaul kit|запчаст|spare|parts",
    "соединения и фитинги": r"фитинг|fitting|соединени|фланец|flange|адаптер",
    "инструмент и расходка": r"инструмент|tool",
    "канаты и такелаж": r"канат|трос|rope|wire",
    "прочее": r"запчаст|spare|parts|ЗИП",
}

# ── направления (что реально стоит в заявке) ────────────────────────────────
def dir_of(row):
    man, model = row["man"], (row["model"] or "").upper()
    if man == "Siemens":
        if "SGT-400" in model or "SGT400" in model:
            return "SGT-400"
        if "ШКАФ" in model or "PMS" in model or "БЛОК" in model or row["cat"] == "электрика и автоматика":
            return "Siemens: шкафы и электрика"
        return "SGT-400"
    if man == "Solar":
        return "Solar Taurus 60S/70/70MD"
    if man in ("Cummins", "Fleetguard"):
        return "Cummins/Fleetguard (ГПУ)"
    if man == "Jenbacher/INNIO":
        return "Jenbacher INNIO (ГПУ)"
    mu = man.upper()
    if "ABB" in mu:
        return "НВН: ABB (электрика и приводы)"
    if "DRILLMEC" in mu or "OLEOBI" in mu:
        return "НВН: Drillmec/Oleobi (буровые установки)"
    if "OILWELL" in mu or "NOV" == mu or "TESCO" in mu or "SHAFFER" in mu:
        return "НВН: NOV/Tesco/Shaffer (буровое)"
    if "SCHLUMBERGER" in mu or "CAMERON" in mu or "SWACO" in mu or "FMC" in mu:
        return "НВН: Schlumberger/Cameron/MI Swaco/FMC"
    if "LIEBHERR" in mu:
        return "НВН: Liebherr (краны)"
    if "DRILLTECH" in mu:
        return "НВН: Drilltech Cangzhou (буровое, реверс)"
    if "BORNEMANN" in mu or "MARFLEX" in mu:
        return "НВН: насосы (Bornemann/MarFlex)"
    return "НВН: прочие производители"


def model_match(sup, direction):
    m = " ".join(sup["bx_models"]) + " " + sup["what"].upper() + " " + sup["note"].upper()
    if direction == "SGT-400":
        return bool(re.search(r"SGT|CYCLONE|ЛИНКОЛЬН|LINCOLN|SIEMENS", m, re.I))
    if direction.startswith("Solar"):
        return bool(re.search(r"TAURUS|SOLAR|CENTAUR|MARS|TITAN|SATURN|СОЛАР", m, re.I))
    if direction.startswith("Siemens: шкафы"):
        return bool(re.search(r"SIMATIC|S7|ЭЛЕКТР|CONTROL|АВТОМАТИК|SIEMENS|OBSOLETE", m, re.I))
    if "Cummins" in direction:
        return bool(re.search(r"CUMMINS|FLEETGUARD|ГПУ|GAS ENGINE|ДИЗЕЛ", m, re.I))
    if "Jenbacher" in direction:
        return bool(re.search(r"JENBACHER|INNIO|ГПУ|GAS ENGINE", m, re.I))
    man = direction.split("(")[-1].rstrip(")").upper()
    return man[:6] in m


def probability(sup, direction, cat):
    """Прозрачная оценка вероятности содержательного ответа + причина."""
    why = []
    if sup["bx_answered"] and model_match(sup, direction):
        p = 70; why.append("уже отвечал нам по этой теме в Bitrix")
    elif sup["bx"] > 0 and model_match(sup, direction):
        p = 50; why.append(f"наши запросы в Bitrix ×{sup['bx']}, исход не зафиксирован")
    elif sup["bx"] > 0:
        p = 40; why.append("знаком по Bitrix, но по другой технике")
    elif sup["prank"] <= 2 and model_match(sup, direction):
        p = 35; why.append("П%d + профильная модель" % sup["prank"])
    elif sup["prank"] <= 2:
        p = 25; why.append("П%d, модель придётся объяснять" % sup["prank"])
    elif sup["prank"] == 3:
        p = 18; why.append("каталожный профиль, холодный контакт")
    else:
        p = 8; why.append("не доказан, холодный контакт")
    if re.search(CAT_KEYS.get(cat, "$^"), sup["what"], re.I):
        p += 10; why.append("категория в его профиле")
    if sup["email"]:
        p += 5; why.append("есть прямой email")
    else:
        p -= 5; why.append("только форма/телефон")
    if sup["person"]:
        p += 3; why.append("есть контактное лицо")
    return min(85, max(3, p)), "; ".join(why)


def main():
    demand = D("rfq_demand.json")
    pool = build_pool()

    # пачки: направление × категория; мелочь внутри направления сливаем в «смешанную» пачку
    batches = {}
    for r in demand["rows"]:
        key = (dir_of(r), r["cat"])
        batches.setdefault(key, []).append(r)
    merged = {}
    for (direction, cat), items in batches.items():
        if len(items) < 4:
            merged.setdefault((direction, "смешанная пачка (мелкие категории)"), []).extend(items)
        else:
            merged[(direction, cat)] = items
    batches = merged

    # кандидаты на пачку
    out_batches = []
    for (direction, cat), items in sorted(batches.items(), key=lambda kv: -len(kv[1])):
        cands = []
        for sup in pool.values():
            if not (model_match(sup, direction) or re.search(CAT_KEYS.get(cat, "$^"), sup["what"], re.I)):
                continue
            p, why = probability(sup, direction, cat)
            cands.append({**{k: sup[k] for k in ("name", "country", "site", "email", "phone", "person", "hook")},
                          "p": p, "why": why, "bx": sup["bx"], "prank": sup["prank"]})
        cands.sort(key=lambda c: -c["p"])
        cands = cands[:14]
        qty = sum(1 for _ in items)
        out_batches.append({
            "dir": direction, "cat": cat, "n": qty,
            "qty_total": sum(i["qty"] if isinstance(i["qty"], (int, float)) else 0 for i in items),
            "items": [{"name": i["name"][:160], "pn": i["pn"], "qty": i["qty"], "unit": i["unit"], "model": i["model"][:60]} for i in items],
            "sups": cands,
            "covered": len(cands),
        })

    tpl = (ROOT / "site" / "rfq.template.html").read_text(encoding="utf-8")
    tpl = tpl.replace("__BATCHES_JSON__", json.dumps(out_batches, ensure_ascii=False))
    tpl = tpl.replace("__META_JSON__", json.dumps({
        "updated": demand["updated"], "source": demand["source"],
        "total": len(demand["rows"]),
        "dirs": sorted({b["dir"] for b in out_batches}),
    }, ensure_ascii=False))
    out = ROOT / "public" / "rfq.html"
    out.write_text(tpl, encoding="utf-8")
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ), пачек: {len(out_batches)}")


if __name__ == "__main__":
    main()
