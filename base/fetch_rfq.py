#!/usr/bin/env python3
"""Выгрузка смарт-процесса 166 «Запросы поставщикам» — стороны поставщика.

Зачем. В сделке видно, что мы запрашивали и чем ответили заказчику, но не видно
второй половины работы: кого из поставщиков спросили, кто ответил, что прислал и
по какой цене. Всё это лежит в отдельном смарт-процессе — 21 193 записи с
восемью файловыми полями, среди них «КП поставщика» и «Offer from supplier».
Без него не построить проверку предложения оригинальным изготовителем.

Что делает: забирает карточки запросов (постранично по возрастанию id — по
смещению портал отдаёт лишь первые тысячи), кладёт их в таблицу rfq и собирает
список вложений в том же виде, что attachments.json для fetch_files.py: файлы
качаются и разбираются той же машинкой, что и вложения сделок.

Связь с остальной базой — через parentId2 (родительская сделка) и
ufCrm18Supplier (компания-поставщик).

    python base/fetch_rfq.py --db base/kvant.db --out rfq_attachments.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import requests

from parse_archives import webhook

FILE_FIELDS = {
    "ufCrm18_1700698211875": "КП поставщика",
    "ufCrm18_1703711961310": "Offer, old",
    "ufCrm18_1703712059311": "Processed offer",
    "ufCrm18_1703712074559": "Processed offer with descriptions / archive",
    "ufCrm18_1727423346": "Request file",
    "ufCrm18_1730999038678": "Мануал, чертеж, шильд",
    "ufCrm18_1730999106096": "Bank Details",
    "ufCrm18_1731179998": "Offer from supplier",
}
SELECT = ["id", "title", "stageId", "previousStageId", "createdTime", "updatedTime", "movedTime",
          "begindate", "closedate", "assignedById", "companyId", "currencyId", "opportunity",
          "parentId2", "ufCrm18Supplier", "ufCrm18SupplContact", "ufCrm18Brands",
          "ufCrm18_1713171552714", "ufCrm18_1731560557301", "ufCrm18_1739090522015",
          *FILE_FIELDS]

DDL = """
CREATE TABLE IF NOT EXISTS rfq (
  id INTEGER PRIMARY KEY, deal_id INTEGER, title TEXT,
  stage_id TEXT, stage TEXT, prev_stage_id TEXT,
  supplier_id INTEGER, supplier TEXT, contact_id INTEGER,
  company_id INTEGER, currency TEXT, amount REAL,
  created TEXT, updated TEXT, moved TEXT, closed TEXT,
  assigned_id INTEGER, sender_email TEXT, comment TEXT, chosen INTEGER,
  files INTEGER
);
CREATE INDEX IF NOT EXISTS ix_rfq_deal ON rfq(deal_id);
CREATE INDEX IF NOT EXISTS ix_rfq_supplier ON rfq(supplier_id);
CREATE TABLE IF NOT EXISTS rfq_files (fid TEXT PRIMARY KEY, rfq_id INTEGER, deal_id INTEGER);
"""


def crm_id(value) -> int:
    """Идентификатор из значения crm-поля.

    Битрикс отдаёт связь то числом, то строкой с префиксом сущности: «5288»,
    но и «CO_9634» (компания), «C_10810» (контакт). Список берём первым
    элементом — у поля «Brands» их бывает несколько."""
    if isinstance(value, list):
        value = value[0] if value else None
    if value in (None, "", 0, "0"):
        return 0
    s = str(value)
    if "_" in s:
        s = s.rsplit("_", 1)[1]
    return int(s) if s.isdigit() else 0


def text_of(value, limit: int = 1000) -> str:
    """Строка из значения поля: часть пользовательских полей приходит списком."""
    if isinstance(value, list):
        value = "; ".join(str(x) for x in value if x)
    return str(value or "")[:limit]


def call(sess: requests.Session, base: str, method: str, payload: dict) -> dict:
    """Запрос к порталу с отступом на временную блокировку метода."""
    for attempt in range(8):
        try:
            r = sess.post(base + method + ".json", json=payload, timeout=180).json()
        except Exception:
            time.sleep(3 * (attempt + 1))
            continue
        if "operation time limit" in (r.get("error_description") or ""):
            time.sleep(min(30 * (attempt + 1), 180))
            continue
        return r
    return {}


def stage_names(sess: requests.Session, base: str) -> dict[str, str]:
    r = call(sess, base, "crm.status.list", {"filter": {"ENTITY_ID": "DYNAMIC_166_STAGE_16"}})
    out = {i["STATUS_ID"]: i["NAME"] for i in (r.get("result") or [])}
    if out:
        return out
    # у смарт-процесса воронок может быть несколько: забираем все справочники стадий
    r = call(sess, base, "crm.status.entity.types", {})
    for e in (r.get("result") or []):
        eid = e.get("ID") or ""
        if eid.startswith("DYNAMIC_166_STAGE"):
            rr = call(sess, base, "crm.status.list", {"filter": {"ENTITY_ID": eid}})
            out.update({i["STATUS_ID"]: i["NAME"] for i in (rr.get("result") or [])})
    return out


def company_names(con: sqlite3.Connection, sess: requests.Session, base: str,
                  ids: list[int]) -> dict[int, str]:
    known = {r[0]: r[1] for r in con.execute("SELECT id, title FROM companies")}
    need = sorted({i for i in ids if i and i not in known})
    for i in range(0, len(need), 50):
        part = need[i:i + 50]
        r = call(sess, base, "crm.company.list",
                 {"filter": {"@ID": part}, "select": ["ID", "TITLE"], "start": 0})
        for c in (r.get("result") or []):
            known[int(c["ID"])] = c.get("TITLE") or ""
        time.sleep(0.2)
    return known


def run(db_path: str, out_path: str, from_raw: bool = False) -> dict:
    con = sqlite3.connect(db_path, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    con.executescript(DDL)
    sess = requests.Session()
    base = webhook().rstrip("/") + "/"
    stages = stage_names(sess, base)
    print(f"стадий в справочнике: {len(stages)}", flush=True)

    items: list[dict] = []
    raw = Path(out_path).with_suffix(".raw.json")
    if from_raw and raw.exists():
        items = json.loads(raw.read_text(encoding="utf-8"))
        print(f"взято из сохранённой выгрузки: {len(items)} карточек", flush=True)
    last = 0
    t0 = time.time()
    while not items:
        r = call(sess, base, "crm.item.list", {
            "entityTypeId": 166, "select": SELECT,
            "filter": {">id": last}, "order": {"id": "asc"}, "start": -1})
        batch = ((r or {}).get("result") or {}).get("items") or []
        if not batch:
            break
        items += batch
        last = int(batch[-1]["id"])
        if len(items) % 2000 < len(batch):
            print(f"  {len(items)} запросов · {len(items)/max(time.time()-t0,1):.0f}/с", flush=True)
        time.sleep(0.25)
    print(f"выгружено запросов: {len(items)}", flush=True)
    if not from_raw:
        raw.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        print(f"сырые карточки сохранены → {raw}", flush=True)

    comp = company_names(con, sess, base, [crm_id(x.get("ufCrm18Supplier")) for x in items])
    atts: list[dict] = []
    rows = []
    for it in items:
        rid = int(it["id"])
        deal = crm_id(it.get("parentId2"))
        sup = crm_id(it.get("ufCrm18Supplier"))
        nfiles = 0
        for code, title in FILE_FIELDS.items():
            v = it.get(code)
            if not v:
                continue
            for o in (v if isinstance(v, list) else [v]):
                if isinstance(o, dict) and o.get("urlMachine"):
                    atts.append({"fid": str(o.get("id")), "deal": deal, "rfq": rid,
                                 "field": code, "field_name": title, "url": o["urlMachine"]})
                    nfiles += 1
        rows.append((rid, deal, text_of(it.get("title"), 300), text_of(it.get("stageId"), 60),
                     text_of(stages.get(it.get("stageId"), it.get("stageId")), 120), text_of(it.get("previousStageId"), 60),
                     sup, comp.get(sup, ""), crm_id(it.get("ufCrm18SupplContact")),
                     crm_id(it.get("companyId")), text_of(it.get("currencyId"), 10),
                     float(it.get("opportunity") or 0), it.get("createdTime"),
                     it.get("updatedTime"), it.get("movedTime"), it.get("closedate"),
                     crm_id(it.get("assignedById")), text_of(it.get("ufCrm18_1713171552714"), 200),
                     text_of(it.get("ufCrm18_1731560557301")),
                     1 if it.get("ufCrm18_1739090522015") in (True, "Y", 1, "1") else 0, nfiles))
    con.execute("DELETE FROM rfq")
    con.executemany("INSERT OR REPLACE INTO rfq VALUES (" + ",".join("?" * 21) + ")", rows)
    con.executemany("INSERT OR REPLACE INTO rfq_files VALUES (?,?,?)",
                    [(a["fid"], a["rfq"], a["deal"]) for a in atts])
    con.commit()
    Path(out_path).write_text(json.dumps(atts, ensure_ascii=False), encoding="utf-8")
    con.close()
    print(f"вложений к скачиванию: {len(atts)} → {out_path}", flush=True)
    return {"rfq": len(rows), "files": len(atts)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--out", default="rfq_attachments.json")
    ap.add_argument("--from-raw", action="store_true",
                    help="разобрать сохранённую выгрузку, не обращаясь к порталу")
    a = ap.parse_args()
    run(a.db, a.out, a.from_raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
