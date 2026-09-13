#!/usr/bin/env python3
"""Что мы по Caterpillar уже продавали, квотировали и завозили — из Битрикса.

Распоряжение владельца 12.09.2026: «в Битриксе огромное количество информации по
тому, что мы уже продавали, завозили и какие кого-то получали — учти в проработке».
Без этого досье машины врёт в самом дорогом месте: выходя на торги, надо знать,
какие позиции мы уже отдавали в контракт, где проиграли по цене и кому.

Два режима, оба пишут один файл zip/data/r1700_bitrix.json:

  живой   — если задан BITRIX_WEBHOOK_URL (.env или окружение). Спрашивает портал:
            смарт-процесс 166 «Запросы поставщикам», сделки и товары по словам
            «R1700», «Caterpillar», «CAT» — id, стадия, семантика (успех/проигрыш),
            суммы, клиент. Записывает только машинные поля: без тел переписки,
            без ФИО, без личных контактов (правило 17 CLAUDE.md).
  офлайн  — если вебхука нет. Поднимает те же факты из уже лежащих в репозитории
            снимков: zip/data/positions.json (поля bitrix_status, bitrix_sold,
            bitrix_price_history), zip/data/price_records.json, supplier_crm.json,
            data/chat_snapshot.json (только наименования сделок).

Запуск:
    python zip/tools/r1700_bitrix.py             # авто: живой, если есть вебхук
    python zip/tools/r1700_bitrix.py --offline    # принудительно по снимкам
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DATA = ROOT / "data"
OUT = DATA / "r1700_bitrix.json"

WORDS = ["R1700", "Caterpillar", "CAT"]
PAT = re.compile(r"R1700|CATERPILLAR", re.I)
# «CAT» отдельным словом — иначе в сеть попадают «каталог», «category», «cat-6».
PAT_CAT = re.compile(r"(?<![A-Za-zА-Яа-я])CAT(?![A-Za-zА-Яа-я])")


def hit(text: str) -> bool:
    t = str(text or "")
    return bool(PAT.search(t) or PAT_CAT.search(t))


def load(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


# ────────────────────────────────────────────────────────────── офлайн-режим
def from_positions() -> tuple[list, list]:
    """Позиции базы ЗИП с признаком Caterpillar: что продавали, что квотировали."""
    pos = load(DATA / "positions.json", []) or []
    sold, quoted = [], []
    for p in pos:
        blob = f"{p.get('name', '')} {p.get('oem', '')} {p.get('model', '')} {p.get('applications', '')}"
        if not hit(blob):
            continue
        st = (p.get("bitrix_status") or "").strip()
        if st not in ("продавали", "квотировали"):
            continue
        row = {
            "position_id": p.get("id"), "pp": p.get("pp"), "pn": p.get("catalog_no"),
            "name": p.get("name"), "machine": p.get("model"), "node": p.get("category"),
            "status": st, "r1700": "R1700" in str(p.get("model", "")).upper()
            or "R1700" in str(p.get("applications", "")).upper(),
            "price_min": p.get("price_min"), "price_max": p.get("price_max"),
            "price_cur": p.get("price_cur"), "price_src": p.get("price_src"),
            "qty_quarter": p.get("qty_quarter"),
            "deals": [{"date": h.get("date"), "deal": h.get("deal"), "client": h.get("client"),
                       "status": h.get("status")} for h in (p.get("bitrix_price_history") or [])],
        }
        (sold if st == "продавали" else quoted).append(row)
    return sold, quoted


def from_prices() -> list:
    """Ценовые факты по позициям Caterpillar: КП, маркетплейсы, таможня."""
    pos = {p["id"]: p for p in (load(DATA / "positions.json", []) or [])}
    out = []
    for r in load(DATA / "price_records.json", []) or []:
        p = pos.get(r.get("position_id"))
        if not p:
            continue
        if not hit(f"{p.get('name', '')} {p.get('oem', '')} {p.get('model', '')}"):
            continue
        out.append({
            "position_id": r.get("position_id"), "pn": p.get("catalog_no"), "name": p.get("name"),
            "machine": p.get("model"), "year": r.get("year"), "source": r.get("source"),
            "seller": r.get("exporter"), "country": r.get("country"),
            "unit_price": r.get("unit_price"), "currency": r.get("currency"),
            "url": r.get("url"), "confidence": r.get("confidence"), "note": r.get("note"),
        })
    return out


def from_crm() -> list:
    """Поставщики из CRM базы ЗИП, у которых в профиле Caterpillar."""
    crm = load(DATA / "supplier_crm.json", {}) or {}
    out = []
    for s in crm.get("suppliers") or []:
        if not hit(json.dumps(s, ensure_ascii=False)):
            continue
        out.append({k: s.get(k) for k in ("name", "country", "kind", "status", "site", "email", "note")
                    if s.get(k)})
    return out


def from_chats() -> list:
    """Живые сделки с упоминанием Caterpillar — только наименование сделки и её номер.

    Из снимка чатов берём исключительно корпоративные поля: номер сделки и её
    название. Ни сотрудников, ни текста переписки — в публичном репозитории им не место.
    """
    d = load(REPO / "data" / "chat_snapshot.json", {}) or {}
    seen, out = set(), []
    for _day, emps in (d.get("days") or {}).items():
        if not isinstance(emps, dict):
            continue
        for _emp, v in emps.items():
            if not isinstance(v, dict):
                continue
            for w in v.get("waiting") or []:
                t = str(w.get("t") or "")
                if not hit(t):
                    continue
                key = (w.get("deal"), t)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"deal": w.get("deal"), "title": t})
    return out


# ────────────────────────────────────────────────────────────── живой режим
def live() -> dict | None:
    """Спросить портал. Возвращает None, если вебхука нет или клиент не поднялся."""
    webhook = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not webhook:
        env = REPO / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("BITRIX_WEBHOOK_URL="):
                    webhook = line.split("=", 1)[1].strip()
    if not webhook or "rest/" not in webhook:
        return None
    sys.path.insert(0, str(REPO))
    try:
        from bitrix_client import BitrixClient  # noqa: PLC0415
    except Exception as ex:
        print(f"живой режим недоступен: {ex}")
        return None

    c = BitrixClient(webhook.rstrip("/") + "/")
    res = {"spa": [], "deals": [], "products": []}

    # смарт-процесс 166 «Запросы поставщикам» — предмет запроса лежит в title
    for w in WORDS:
        try:
            items = c.list_items(166, filter={"%title": w},
                                 select=["id", "title", "stageId", "createdTime", "opportunity", "currencyId"])
        except Exception as ex:
            print(f"  СП-166 «{w}»: {ex}")
            continue
        for it in items:
            if not hit(it.get("title")):
                continue
            res["spa"].append({"id": it.get("id"), "title": it.get("title"), "stage": it.get("stageId"),
                               "created": str(it.get("createdTime") or "")[:10],
                               "sum": it.get("opportunity"), "cur": it.get("currencyId"), "word": w})

    stages = {}
    try:
        stages = c.deal_stages()
    except Exception:
        pass
    for w in WORDS:
        try:
            deals = c.list_paged("crm.deal.list", {
                "filter": {"%TITLE": w}, "order": {"ID": "ASC"},
                "select": ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CURRENCY_ID", "DATE_CREATE", "CLOSED"]})
        except Exception as ex:
            print(f"  сделки «{w}»: {ex}")
            continue
        for d in deals:
            if not hit(d.get("TITLE")):
                continue
            st = stages.get(d.get("STAGE_ID")) or {}
            res["deals"].append({"id": d.get("ID"), "title": d.get("TITLE"),
                                 "stage": st.get("name") or d.get("STAGE_ID"), "sem": st.get("sem"),
                                 "sum": d.get("OPPORTUNITY"), "cur": d.get("CURRENCY_ID"),
                                 "created": str(d.get("DATE_CREATE") or "")[:10], "word": w})
    for w in WORDS:
        try:
            prods = c.list_paged("crm.product.list", {
                "filter": {"%NAME": w}, "order": {"ID": "ASC"},
                "select": ["ID", "NAME", "CODE", "PRICE", "CURRENCY_ID", "ACTIVE"]})
        except Exception as ex:
            print(f"  товары «{w}»: {ex}")
            continue
        for p in prods:
            if not hit(p.get("NAME")):
                continue
            res["products"].append({"id": p.get("ID"), "name": p.get("NAME"), "code": p.get("CODE"),
                                    "price": p.get("PRICE"), "cur": p.get("CURRENCY_ID"), "word": w})
    # дубли между словами-запросами
    for k in res:
        seen, uniq = set(), []
        for r in res[k]:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            uniq.append(r)
        res[k] = uniq
    return res


def build(force_offline: bool = False) -> dict:
    sold, quoted = from_positions()
    prices = from_prices()
    crm = from_crm()
    chats = from_chats()
    lv = None if force_offline else live()

    out = {
        "updated": date.today().isoformat(),
        "schema": "kvant.r1700-bitrix/1",
        "built_by": "zip/tools/r1700_bitrix.py",
        "live": bool(lv),
        "note": "Факты сделок по Caterpillar: что продавали, что квотировали, по каким ценам. "
                "Только машинные поля — без переписки, ФИО и личных контактов.",
        "sold": sold,
        "quoted": quoted,
        "prices": prices,
        "crm": crm,
        "deal_titles": chats,
        "portal": lv or {},
        "stats": {
            "sold": len(sold), "quoted": len(quoted),
            "sold_r1700": sum(1 for r in sold if r["r1700"]),
            "quoted_r1700": sum(1 for r in quoted if r["r1700"]),
            "prices": len(prices),
            "price_sources": dict(Counter(r["source"] for r in prices)),
            "clients": dict(Counter(d["client"] for r in sold + quoted for d in r["deals"] if d.get("client"))),
            "deal_outcomes": dict(Counter(d["status"] for r in sold + quoted for d in r["deals"] if d.get("status"))),
            "crm": len(crm), "deal_titles": len(chats),
            "portal_spa": len((lv or {}).get("spa") or []),
            "portal_deals": len((lv or {}).get("deals") or []),
            "portal_products": len((lv or {}).get("products") or []),
        },
    }
    return out


def main() -> int:
    d = build(force_offline="--offline" in sys.argv)
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    s = d["stats"]
    mode = "живой (портал отвечает)" if d["live"] else "офлайн (по снимкам репозитория)"
    print(f"{OUT.relative_to(REPO)}: режим {mode}")
    print(f"  продавали {s['sold']} (из них по R1700 — {s['sold_r1700']}), "
          f"квотировали {s['quoted']} (R1700 — {s['quoted_r1700']})")
    print(f"  ценовых фактов {s['prices']} {s['price_sources']}")
    print(f"  заказчики: {s['clients']}")
    print(f"  исходы сделок: {s['deal_outcomes']}")
    if d["live"]:
        print(f"  портал: СП-166 {s['portal_spa']}, сделок {s['portal_deals']}, товаров {s['portal_products']}")
    else:
        print("  живой пас не сделан: нет BITRIX_WEBHOOK_URL. Запустить там, где вебхук есть — "
              "данные лягут в тот же файл.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
