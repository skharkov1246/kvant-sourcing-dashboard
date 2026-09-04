#!/usr/bin/env python3
"""Выгрузка ГТУ-запросов из Bitrix24 → gt/data/bitrix_gt.json.

Что делает (нужен BITRIX_WEBHOOK_URL в .env или окружении):
1) находит сделки по ключевым словам ГТУ (SGT, Solar, Taurus, Titan, газотурбин…);
2) тянет привязанные записи СП-166 «Запросы поставщикам» (parentId2 → сделка)
   + запросы, где ключевые слова прямо в названии;
3) резолвит поставщиков (companyId / ufCrm18Supplier), стадии и сорсеров;
4) размечает модели регэкспом и пишет снапшот для вкладки «Наши запросы»
   сайта gt/ (пересборка: python3 gt/build.py).

Запуск:  .venv/bin/python gt/tools/bitrix_gt.py
Без вебхука сайт живёт на закоммиченном снапшоте (сид — из снимка чатов сделок).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402
from config import SPA_ENTITY_TYPE_ID, Settings  # noqa: E402

OUT = ROOT / "gt/data/bitrix_gt.json"

# ключевые слова поиска сделок/запросов (регистр Bitrix не важен, %TITLE — подстрока)
KEYWORDS = ["SGT", "Solar", "Taurus", "Centaur", "Titan", "Mars", "Saturn",
            "ГТУ", "газотурбин", "газовая турбина", "Typhoon", "Tornado"]

MODEL_RE = [
    (re.compile(r"SGT[- ]?100|Typhoon", re.I), "SGT-100"),
    (re.compile(r"SGT[- ]?200|Tornado", re.I), "SGT-200"),
    (re.compile(r"SGT[- ]?300|Tempest", re.I), "SGT-300"),
    (re.compile(r"SGT[- ]?400|Cyclone", re.I), "SGT-400"),
    (re.compile(r"SGT[- ]?500|GT35", re.I), "SGT-500"),
    (re.compile(r"SGT[- ]?600|GT10B?\b", re.I), "SGT-600"),
    (re.compile(r"SGT[- ]?700|GT10C", re.I), "SGT-700"),
    (re.compile(r"SGT[- ]?750", re.I), "SGT-750"),
    (re.compile(r"SGT[- ]?800|GTX100", re.I), "SGT-800"),
    (re.compile(r"SGT5[- ]?2000E|V94\.?2|ГТЭ[- ]?160", re.I), "SGT5-2000E (V94.2/ГТЭ-160)"),
    (re.compile(r"SGT5[- ]?4000F|V94\.?3", re.I), "SGT5-4000F (V94.3A)"),
    (re.compile(r"V64\.?3|AE64", re.I), "V64.3A (AE64.3A)"),
    (re.compile(r"GT13", re.I), "GT13E2"),
    (re.compile(r"LM[- ]?2500|PGT[- ]?25", re.I), "LM2500 (PGT25)"),
    (re.compile(r"LM[- ]?6000", re.I), "LM6000"),
    (re.compile(r"RB[- ]?211|SGT-A35", re.I), "RB211 (SGT-A35)"),
    (re.compile(r"9F[AB]?\b|MS9001", re.I), "GE 9F/9FA"),
    (re.compile(r"6F\.?03|6FA", re.I), "GE 6F.03 (6FA)"),
    (re.compile(r"MS7001|7EA|Frame\s?7", re.I), "GE Frame 7 (MS7001)"),
    (re.compile(r"MS6001|Frame\s?6|\b6B\b", re.I), "GE Frame 6B (MS6001)"),
    (re.compile(r"MS5002|Frame\s?5", re.I), "GE MS5002"),
    (re.compile(r"MS5001", re.I), "GE MS5001"),
    (re.compile(r"Taurus\w*[- ]?60", re.I), "Taurus 60"),
    (re.compile(r"Taurus\w*[- ]?70", re.I), "Taurus 70"),
    (re.compile(r"Centaur\w*[- ]?40", re.I), "Centaur 40"),
    (re.compile(r"Centaur\w*[- ]?50", re.I), "Centaur 50"),
    (re.compile(r"Mars[- ]?90", re.I), "Mars 90"),
    (re.compile(r"Mars[- ]?100", re.I), "Mars 100"),
    (re.compile(r"Titan[- ]?130", re.I), "Titan 130"),
    (re.compile(r"Titan[- ]?250", re.I), "Titan 250"),
    (re.compile(r"Saturn", re.I), "Saturn 20"),
    (re.compile(r"Solar", re.I), "Solar (модель уточнить)"),
    (re.compile(r"Siemens|SGT", re.I), "Siemens (модель уточнить)"),
    (re.compile(r"ГТУ|газотурбин|газовая турбина|турбина газовая", re.I), "ГТУ (модель уточнить)"),
]


def tag_models(text: str) -> list[str]:
    """Точные модели из текста; если ни одной — первый общий тэг («… уточнить»)."""
    exact: list[str] = []
    generic: list[str] = []
    for rx, name in MODEL_RE:
        if rx.search(text or ""):
            bucket = generic if name.endswith("уточнить)") else exact
            if name not in bucket:
                bucket.append(name)
    return exact or generic[:1]


def crm_refs(v) -> list[str]:
    """['CO_372','C_45'] → ['372'] (только компании)."""
    vals = v if isinstance(v, list) else ([v] if v else [])
    return [str(x).split("_", 1)[1] for x in vals if str(x).startswith("CO_")]


def main() -> int:
    settings = Settings.load()
    client = BitrixClient(settings.bitrix_webhook_url)

    # 1. сделки по ключевым словам
    deals: dict[str, dict] = {}
    for kw in KEYWORDS:
        for d in client.list_paged(
            "crm.deal.list",
            {"filter": {"%TITLE": kw}, "select": ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY",
                                                  "CURRENCY_ID", "ASSIGNED_BY_ID", "DATE_CREATE"]},
        ):
            deals[str(d["ID"])] = d
    print(f"сделок по ключевым словам: {len(deals)}")

    # 2. запросы СП-166: привязанные к сделкам + по названию
    rfqs: dict[str, dict] = {}
    select = ["id", "title", "stageId", "createdTime", "parentId2", "companyId",
              "assignedById", "ufCrm18Supplier", "ufCrm18SupplContact", "opportunity"]
    ids = list(deals)
    for i in range(0, len(ids), 50):
        for r in client.list_items(SPA_ENTITY_TYPE_ID, filter={"parentId2": ids[i:i + 50]}, select=select):
            rfqs[str(r["id"])] = r
    for kw in KEYWORDS:
        for r in client.list_items(SPA_ENTITY_TYPE_ID, filter={"%title": kw}, select=select):
            rfqs[str(r["id"])] = r
    print(f"запросов СП-166: {len(rfqs)}")

    # 3. справочники: компании, пользователи, стадии
    comp_ids = set()
    user_ids = set()
    for r in rfqs.values():
        if r.get("companyId"):
            comp_ids.add(str(r["companyId"]))
        comp_ids.update(crm_refs(r.get("ufCrm18Supplier")))
        if r.get("assignedById"):
            user_ids.add(str(r["assignedById"]))
    comp_names: dict[str, str] = {}
    if comp_ids:
        for c in client.list_paged("crm.company.list", {"filter": {"@ID": list(comp_ids)}, "select": ["ID", "TITLE"]}):
            comp_names[str(c["ID"])] = c.get("TITLE") or c["ID"]
    users = {}
    for u in client.list_paged("user.get", {"ID": list(user_ids)}) if user_ids else []:
        users[str(u["ID"])] = f'{u.get("NAME", "")} {u.get("LAST_NAME", "")}'.strip()
    stages: dict[str, str] = {}
    for cat in (24, 0):
        try:
            stages.update(client.spa_stages(SPA_ENTITY_TYPE_ID, cat))
        except Exception:
            pass

    # 4. сборка снапшота
    out_deals = []
    dropped = 0
    for did, d in sorted(deals.items(), key=lambda kv: -int(kv[0])):
        title = d.get("TITLE") or ""
        if not tag_models(title):  # ключевое слово сработало, но это не ГТУ (напр. «TITANIUM valve»)
            dropped += 1
            continue
        dr = [r for r in rfqs.values() if str(r.get("parentId2") or "") == did]
        out_deals.append({
            "id": did, "title": title, "models": tag_models(title),
            "stage": d.get("STAGE_ID"), "created": (d.get("DATE_CREATE") or "")[:10],
            "rfqs": [{
                "id": str(r["id"]), "title": r.get("title"),
                "created": (r.get("createdTime") or "")[:10],
                "supplier": ", ".join(filter(None, (
                    [comp_names.get(str(r.get("companyId") or ""), "")] +
                    [comp_names.get(cid, cid) for cid in crm_refs(r.get("ufCrm18Supplier"))]))) or "—",
                "stage": stages.get(str(r.get("stageId")), r.get("stageId")),
                "sourcer": users.get(str(r.get("assignedById")), ""),
                "models": tag_models(f'{title} {r.get("title") or ""}'),
            } for r in sorted(dr, key=lambda x: int(x["id"]))],
        })

    # свод «модель → кто предлагает»
    offers: dict[str, dict[str, int]] = {}
    for d in out_deals:
        for r in d["rfqs"]:
            for m in r["models"]:
                if r["supplier"] != "—":
                    offers.setdefault(m, {})
                    offers[m][r["supplier"]] = offers[m].get(r["supplier"], 0) + 1

    OUT.write_text(json.dumps({
        "updated": date.today().isoformat(),
        "source": "Bitrix24: сделки по ключевым словам ГТУ + СП-166 «Запросы поставщикам»",
        "live": True,
        "deals": out_deals,
        "offers": [{"model": m, "suppliers": [{"name": n, "n": c} for n, c in
                                              sorted(sup.items(), key=lambda kv: -kv[1])]}
                   for m, sup in sorted(offers.items())],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"отброшено не-ГТУ сделок: {dropped}")
    print(f"OK → {OUT}")
    print("дальше: python3 gt/build.py  (пересборка сайта)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
