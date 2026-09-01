#!/usr/bin/env python3
"""Выгрузка запросов по газопоршневым из Bitrix24 → gpu/data/bitrix_gpu.json.

Что делает (нужен BITRIX_WEBHOOK_URL в .env или окружении):
1) находит сделки по ключевым словам ГПУ (Cummins, QSV, QSK, Jenbacher, CAT G35xx,
   газопоршн, ГПЭС, свечи зажигания…);
2) тянет привязанные записи СП-166 «Запросы поставщикам» (parentId2 → сделка)
   + запросы, где ключевые слова прямо в названии;
3) резолвит поставщиков (companyId / ufCrm18Supplier), стадии и сорсеров;
4) размечает OEM и модели регэкспом;
5) отдельно проверяет, заведены ли у нас компании мирового пула по ГПУ
   (TECHIE, Weyeah, İggnita, PowerUP, MOTORTECH…) и слали ли мы им запросы;
6) пишет снапшот для вкладки «Наши запросы» сайта gpu/.

Запуск:  .venv/bin/python gpu/tools/bitrix_gpu.py
Без вебхука сайт живёт на закоммиченном сиде (собран из data/chat_snapshot.json).
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402
from config import SPA_ENTITY_TYPE_ID, Settings  # noqa: E402

OUT = ROOT / "gpu/data/bitrix_gpu.json"

# ключевые слова поиска сделок/запросов (%TITLE — подстрока, регистр не важен)
KEYWORDS = [
    "Cummins", "Камминз", "Камминс", "QSV", "QSK", "KTA", "HSK78",
    "Jenbacher", "Йенбахер", "Дженбахер", "Енбахер", "INNIO", "JGS",
    "Caterpillar", "Катерпиллер", "Катерпилар", "G3516", "G3520", "G3512", "G3606", "G3616",
    "CG132", "CG170", "CG260", "MWM", "Waukesha", "Guascor",
    "газопоршн", "ГПЭС", "ГПУ ", "когенерац", "мини-ТЭЦ",
    "свеча зажигания", "свечи зажигания", "газогенератор",
]

# модели/OEM: (регэксп, метка). Метки «… уточнить» — общие, ставятся, только если точных нет.
MODEL_RE = [
    (re.compile(r"QSV\s?91", re.I), "Cummins QSV91G"),
    (re.compile(r"QSV\s?81", re.I), "Cummins QSV81G"),
    (re.compile(r"QSK\s?60", re.I), "Cummins QSK60G"),
    (re.compile(r"QSK\s?50", re.I), "Cummins QSK50G"),
    (re.compile(r"QSK\s?45", re.I), "Cummins QSK45G"),
    (re.compile(r"QSK\s?38", re.I), "Cummins QSK38G"),
    (re.compile(r"QSK\s?19", re.I), "Cummins QSK19G"),
    (re.compile(r"HSK\s?78", re.I), "Cummins HSK78G"),
    (re.compile(r"KTA\s?50", re.I), "Cummins KTA50G"),
    (re.compile(r"KTA\s?38", re.I), "Cummins KTA38G"),
    (re.compile(r"J\s?208|JGS\s?208", re.I), "Jenbacher J208 (Type 2)"),
    (re.compile(r"J\s?312|JGS\s?312", re.I), "Jenbacher J312 (Type 3)"),
    (re.compile(r"J\s?316|JGS\s?316", re.I), "Jenbacher J316 (Type 3)"),
    (re.compile(r"J\s?320|JGS\s?320", re.I), "Jenbacher J320 (Type 3)"),
    (re.compile(r"J\s?412|JGS\s?412", re.I), "Jenbacher J412 (Type 4)"),
    (re.compile(r"J\s?416|JGS\s?416", re.I), "Jenbacher J416 (Type 4)"),
    (re.compile(r"J\s?420|JGS\s?420", re.I), "Jenbacher J420 (Type 4)"),
    (re.compile(r"J\s?612|JGS\s?612", re.I), "Jenbacher J612 (Type 6)"),
    (re.compile(r"J\s?616|JGS\s?616", re.I), "Jenbacher J616 (Type 6)"),
    (re.compile(r"J\s?620|JGS\s?620", re.I), "Jenbacher J620 (Type 6)"),
    (re.compile(r"J\s?624|JGS\s?624", re.I), "Jenbacher J624 (Type 6)"),
    (re.compile(r"J\s?920", re.I), "Jenbacher J920 (Type 9)"),
    (re.compile(r"G\s?3516", re.I), "CAT G3516"),
    (re.compile(r"G\s?3520", re.I), "CAT G3520"),
    (re.compile(r"G\s?3512", re.I), "CAT G3512"),
    (re.compile(r"G\s?3508", re.I), "CAT G3508"),
    (re.compile(r"G\s?360[68]|G\s?361[26]", re.I), "CAT G3600"),
    (re.compile(r"CG\s?132|TCG\s?2016", re.I), "CAT CG132 (MWM TCG 2016)"),
    (re.compile(r"CG\s?170|TCG\s?2020", re.I), "CAT CG170 (MWM TCG 2020)"),
    (re.compile(r"CG\s?260|TCG\s?2032", re.I), "CAT CG260 (MWM TCG 2032)"),
    (re.compile(r"Waukesha|Ваукеша", re.I), "Waukesha"),
    (re.compile(r"Guascor", re.I), "Guascor"),
    (re.compile(r"MWM", re.I), "MWM (модель уточнить)"),
    (re.compile(r"Jenbacher|[ЙД]?енбахер|INNIO|JGS", re.I), "Jenbacher (модель уточнить)"),
    (re.compile(r"Cummins|Камминз|Камминс", re.I), "Cummins (модель уточнить)"),
    (re.compile(r"Caterpillar|Катерпилл?[ае]р", re.I), "Caterpillar (модель уточнить)"),
    (re.compile(r"газопоршн|ГПЭС|когенерац|мини-ТЭЦ|газогенератор", re.I), "ГПУ (модель уточнить)"),
]

# мировой пул по ГПУ: проверяем, заведены ли компании и слали ли мы им запросы
CANDIDATES = [
    "MOTORTECH", "Altronic", "Hatraco", "ERS", "PowerUP", "ONERGYS", "PERI", "Peri-Parts",
    "Emission Partner", "RS Motor", "IMAGE Spareparts", "MurCal", "Clarke Energy",
    "Iggnita", "İggnita", "BMF Enerji", "Borusan", "HARTEX",
    "InstruBiz", "Radiant", "Techie", "Weyeah", "Zuhon", "SparkPlugs", "EB Machinery",
    "NIDS", "North India Diesel", "Ghaddar", "Modern Group",
    "IPD", "Interstate", "McBee", "PAI Industries", "Costex", "Atmus", "Fleetguard",
    "Donaldson", "Mann", "Elring", "Denso", "Champion", "Woodward", "IMPCO", "Heinzmann",
    "Сервис Юнит", "ЭнергоТехСервис", "ROLT", "Цезарь-Нева", "Детройт", "Reman",
    "Energy Assistance", "Энерджи Ассистанс", "GeneratorSparkPlug", "Walsh",
    "Gilkes", "Holset", "Accelleron", "KBB", "Bleistahl",
]

SELECT = ["id", "title", "stageId", "createdTime", "parentId2", "companyId",
          "assignedById", "ufCrm18Supplier", "ufCrm18SupplContact", "opportunity"]


def tag_models(text: str) -> list[str]:
    exact: list[str] = []
    generic: list[str] = []
    for rx, name in MODEL_RE:
        if rx.search(text or ""):
            bucket = generic if name.endswith("уточнить)") else exact
            if name not in bucket:
                bucket.append(name)
    return exact or generic[:1]


def crm_refs(v) -> list[str]:
    vals = v if isinstance(v, list) else ([v] if v else [])
    return [str(x).split("_", 1)[1] for x in vals if str(x).startswith("CO_")]


def main() -> int:
    if not (os.getenv("BITRIX_WEBHOOK_URL") or (ROOT / ".env").exists()):
        print("нет BITRIX_WEBHOOK_URL — оставляю закоммиченный сид", file=sys.stderr)
        return 0
    client = BitrixClient(Settings.load().bitrix_webhook_url)

    deals: dict[str, dict] = {}
    for kw in KEYWORDS:
        for d in client.list_paged("crm.deal.list", {
            "filter": {"%TITLE": kw},
            "select": ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CURRENCY_ID",
                       "ASSIGNED_BY_ID", "DATE_CREATE"],
        }):
            deals[str(d["ID"])] = d
    print(f"сделок по ключевым словам: {len(deals)}")

    rfqs: dict[str, dict] = {}
    ids = list(deals)
    for i in range(0, len(ids), 50):
        for r in client.list_items(SPA_ENTITY_TYPE_ID, filter={"parentId2": ids[i:i + 50]}, select=SELECT):
            rfqs[str(r["id"])] = r
    for kw in KEYWORDS:
        for r in client.list_items(SPA_ENTITY_TYPE_ID, filter={"%title": kw}, select=SELECT):
            rfqs[str(r["id"])] = r
    print(f"запросов СП-166: {len(rfqs)}")

    comp_ids, user_ids = set(), set()
    for r in rfqs.values():
        if r.get("companyId"):
            comp_ids.add(str(r["companyId"]))
        comp_ids.update(crm_refs(r.get("ufCrm18Supplier")))
        if r.get("assignedById"):
            user_ids.add(str(r["assignedById"]))
    comp_names: dict[str, str] = {}
    if comp_ids:
        for c in client.list_paged("crm.company.list",
                                   {"filter": {"@ID": list(comp_ids)}, "select": ["ID", "TITLE"]}):
            comp_names[str(c["ID"])] = c.get("TITLE") or c["ID"]
    users = {}
    for u in (client.list_paged("user.get", {"ID": list(user_ids)}) if user_ids else []):
        users[str(u["ID"])] = f'{u.get("NAME", "")} {u.get("LAST_NAME", "")}'.strip()
    stages: dict[str, str] = {}
    for cat in (24, 0):
        try:
            stages.update(client.spa_stages(SPA_ENTITY_TYPE_ID, cat))
        except Exception:
            pass

    out_deals, dropped = [], 0
    for did, d in sorted(deals.items(), key=lambda kv: -int(kv[0])):
        title = d.get("TITLE") or ""
        if not tag_models(title):
            dropped += 1
            continue
        dr = [r for r in rfqs.values() if str(r.get("parentId2") or "") == did]
        out_deals.append({
            "id": did, "title": title, "models": tag_models(title),
            "stage": d.get("STAGE_ID"), "created": (d.get("DATE_CREATE") or "")[:10],
            "sourcer": users.get(str(d.get("ASSIGNED_BY_ID")), ""),
            "rfqs": [{
                "id": str(r["id"]), "title": r.get("title"),
                "created": (r.get("createdTime") or "")[:10],
                "supplier": ", ".join(filter(None, (
                    [comp_names.get(str(r.get("companyId") or ""), "")] +
                    [comp_names.get(cid, cid) for cid in crm_refs(r.get("ufCrm18Supplier"))]))) or "—",
                "stage": stages.get(str(r.get("stageId")), r.get("stageId")),
                "sourcer": users.get(str(r.get("assignedById")), ""),
            } for r in sorted(dr, key=lambda x: int(x["id"]))],
        })

    # кого из мирового пула мы уже заводили и что им слали
    pool = []
    for cand in CANDIDATES:
        try:
            comps = client.list_paged("crm.company.list",
                                      {"filter": {"%TITLE": cand}, "select": ["ID", "TITLE", "WEB", "EMAIL"]})
        except Exception:
            continue
        for c in comps[:5]:
            cid = str(c["ID"])
            items = client.list_items(SPA_ENTITY_TYPE_ID, filter={"companyId": cid}, select=SELECT)
            items.sort(key=lambda x: str(x.get("createdTime")), reverse=True)
            pool.append({
                "query": cand, "id": cid, "name": c.get("TITLE"),
                "rfqs": len(items),
                "last": (items[0].get("createdTime") or "")[:10] if items else "",
                "items": [{"date": (r.get("createdTime") or "")[:10], "title": r.get("title"),
                           "stage": stages.get(str(r.get("stageId")), r.get("stageId")),
                           "sourcer": users.get(str(r.get("assignedById")), "")} for r in items[:5]],
            })

    # свод по поставщикам: сколько запросов, когда последний, какие стадии
    by_supplier: dict[str, dict] = {}
    for r in rfqs.values():
        names_ = list(filter(None, (
            [comp_names.get(str(r.get("companyId") or ""), "")] +
            [comp_names.get(cid, cid) for cid in crm_refs(r.get("ufCrm18Supplier"))])))
        for nm in names_ or []:
            a = by_supplier.setdefault(nm, {"name": nm, "n": 0, "last": "", "stages": {}})
            a["n"] += 1
            dt = (r.get("createdTime") or "")[:10]
            if dt > a["last"]:
                a["last"] = dt
            st = stages.get(str(r.get("stageId")), str(r.get("stageId")))
            a["stages"][st] = a["stages"].get(st, 0) + 1

    offers: dict[str, dict[str, int]] = {}
    for d in out_deals:
        for r in d["rfqs"]:
            if r["supplier"] == "—":
                continue
            for m in d["models"]:
                offers.setdefault(m, {})
                offers[m][r["supplier"]] = offers[m].get(r["supplier"], 0) + 1

    OUT.write_text(json.dumps({
        "updated": date.today().isoformat(),
        "source": "Bitrix24: сделки по ключевым словам ГПУ + СП-166 «Запросы поставщикам»",
        "live": True,
        "deals": out_deals,
        "pool": sorted(pool, key=lambda x: -x["rfqs"]),
        "by_supplier": sorted(by_supplier.values(), key=lambda x: -x["n"]),
        "offers": [{"model": m, "suppliers": [{"name": n, "n": c} for n, c in
                                              sorted(sup.items(), key=lambda kv: -kv[1])]}
                   for m, sup in sorted(offers.items())],
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"отброшено не-ГПУ сделок: {dropped}")
    print(f"OK → {OUT}\nдальше: python3 gpu/build.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
