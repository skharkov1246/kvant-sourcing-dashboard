"""Зонд v32: остались ли пути к файлам, кроме закрытых.

v31 показал: ссылки из полей сделки ведут на страницу входа даже с ключом вебхука,
а disk.file.get и disk.attachedObject.get по идентификатору из поля ссылки не дают.
Файлы полей лежат в файловом хранилище портала, а не в Диске.

Проверяются оставшиеся возможности, прежде чем просить владельца менять настройки:
  1. crm.deal.get вместо crm.deal.list — иногда отдаёт объект файла иначе;
  2. права вебхука: какие скоупы вообще выданы;
  3. Диск: какие хранилища видны, есть ли среди них хранилище CRM;
  4. вложения таймлайна (письма и задачи) — там идентификаторы другого рода,
     и именно они могли дать «9 из 40» в ранней разведке.

ПЕЧАТАЮТСЯ ТОЛЬКО ИМЕНА КЛЮЧЕЙ, КОДЫ ОТВЕТОВ И НАЗВАНИЯ ХРАНИЛИЩ.
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

BASE = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")


def bx(method: str, params: dict) -> dict:
    for _ in range(3):
        try:
            r = requests.post(f"{BASE}/{method}.json", json=params, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return {}


def bx_all(method: str, params: dict) -> list:
    out, start = [], 0
    while True:
        j = bx(method, {**params, "start": start})
        res = j.get("result")
        items = res.get("items") if isinstance(res, dict) and "items" in res else res
        out += items or []
        if "next" not in j:
            return out
        start = j["next"]


def probe(u: str) -> str:
    try:
        r = requests.get(u, timeout=45)
        if r.status_code != 200:
            return f"http {r.status_code}"
        b = r.content
        if b[:2] == b"PK":
            return "ФАЙЛ xlsx/docx"
        if b[:4] == b"%PDF":
            return "ФАЙЛ pdf"
        if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            return "ФАЙЛ office"
        if b.lstrip()[:1] == b"<":
            return "страница входа (html)"
        return f"иное ({len(b)} б)"
    except Exception as e:
        return f"ошибка {type(e).__name__}"


def main() -> int:
    print("=== Зонд v32: оставшиеся пути к файлам ===\n")

    # 1. Права вебхука
    sc = sorted((bx("scope", {}).get("result") or []))
    print(f"ПРАВА ВЕБХУКА ({len(sc)}): {', '.join(sc) if sc else 'не отдаются'}")
    for need in ("crm", "disk", "task", "mailservice", "im"):
        print(f"    {need:12s} {'есть' if need in sc else 'НЕТ'}")
    print()

    # 2. Диск: какие хранилища видны
    st = bx("disk.storage.getlist", {}).get("result") or []
    print(f"ХРАНИЛИЩА ДИСКА: {len(st)}")
    for s in st[:15]:
        print(f"    id={s.get('ID')} тип={s.get('ENTITY_TYPE')} имя={str(s.get('NAME'))[:40]}")
    print()

    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00+03:00")
    uf = bx("crm.deal.userfield.list", {"order": {"FIELD_NAME": "ASC"}}).get("result") or []
    ff = [str(u["FIELD_NAME"]) for u in uf if u.get("USER_TYPE_ID") == "file"]
    deals = bx_all("crm.deal.list", {"filter": {">=DATE_CREATE": since},
                                     "select": ["ID"], "order": {"ID": "DESC"}})
    ids = [str(d["ID"]) for d in deals[:200]]

    # 3. crm.deal.get против crm.deal.list — форма объекта файла
    print("ФОРМА ОБЪЕКТА ФАЙЛА: crm.deal.get против crm.deal.list")
    shown = 0
    for did in ids:
        g = bx("crm.deal.get", {"id": did}).get("result") or {}
        for f in ff:
            v = g.get(f)
            if not v:
                continue
            for fo in (v if isinstance(v, list) else [v]):
                if isinstance(fo, dict) and shown < 3:
                    print(f"    ключи из crm.deal.get: {sorted(fo.keys())}")
                    for k in fo:
                        if "url" in k.lower():
                            u = str(fo[k])
                            if u.startswith("http"):
                                print(f"      {k}: {probe(u)}")
                    shown += 1
        if shown >= 3:
            break
    if not shown:
        print("    файловых значений не встретилось в первых сделках")
    print()

    # 4. Вложения таймлайна: письма и задачи
    print("ВЛОЖЕНИЯ ТАЙМЛАЙНА")
    res: Counter = Counter()
    keys: Counter = Counter()
    tried = 0
    for did in ids[:120]:
        acts = bx("crm.activity.list", {"filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": did},
                                        "select": ["ID", "PROVIDER_ID", "FILES"]}).get("result") or []
        for a in acts:
            fl = a.get("FILES")
            if not fl:
                continue
            items = fl.values() if isinstance(fl, dict) else fl
            for fo in items:
                if not isinstance(fo, dict):
                    continue
                keys.update(fo.keys())
                if tried >= 20:
                    continue
                tried += 1
                got = False
                for k in fo:
                    if "url" in k.lower() or "link" in k.lower():
                        u = str(fo[k])
                        if u.startswith("http"):
                            res[f"{k}: {probe(u)}"] += 1
                            got = True
                fid = fo.get("id") or fo.get("ID") or fo.get("fileId")
                if fid:
                    d = bx("disk.attachedObject.get", {"id": fid}).get("result") or {}
                    u = d.get("DOWNLOAD_URL")
                    res[f"attachedObject: {probe(u) if u else 'ссылки нет'}"] += 1
                    got = True
                if not got:
                    res["ссылок в объекте нет"] += 1
        if tried >= 20:
            break
    print(f"    ключи объекта вложения: {dict(keys.most_common())}")
    for k, n in res.most_common():
        print(f"    {k:52s} {n}")

    print("\n✓ зонд v32 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
