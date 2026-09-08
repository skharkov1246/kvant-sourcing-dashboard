"""Зонд v33: последние пути к файлам и оценка достижимой доли.

Установлено: у вебхука все 69 прав, Диск отвечает, но файлы полей сделок — это
файлы портала (тип «file»), а не объекты Диска, и их ссылки ведут на страницы,
требующие сессии пользователя.

Однако ранняя разведка v29 получила 9 файлов из 40 через disk.file.get — значит
часть вложений всё же лежит в Диске. Прежде чем объявлять путь закрытым, нужно:

  1. измерить, какая доля файлов достижима через disk.file.get на большой выборке;
  2. проверить служебный адрес выдачи вложений /bitrix/tools/disk/uf.php с ключом
     вебхука — он предназначен именно для отдачи привязанных файлов;
  3. перечислить типы всех хранилищ Диска: если есть хранилище CRM, файлы сделок
     достижимы обходом папок, а не по идентификатору из поля.

ПЕЧАТАЮТСЯ ТОЛЬКО ТИПЫ, КОДЫ ОТВЕТОВ И ДОЛИ.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

BASE = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
m = re.match(r"(https://[^/]+)/rest/(\d+)/([^/]+)", BASE)
PORTAL, USER_ID, TOKEN = (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")
SAMPLE = 200


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
        return f"ФАЙЛ иное ({len(b)} б)"
    except Exception as e:
        return f"ошибка {type(e).__name__}"


def main() -> int:
    print("=== Зонд v33: достижимая доля вложений ===\n")

    st = bx("disk.storage.getlist", {}).get("result") or []
    types = Counter(str(s.get("ENTITY_TYPE")) for s in st)
    print(f"ТИПЫ ХРАНИЛИЩ ДИСКА ({len(st)}): {dict(types.most_common())}")
    crm_st = [s for s in st if "crm" in str(s.get("ENTITY_TYPE", "")).lower()]
    print(f"  хранилищ CRM: {len(crm_st)}\n")

    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00+03:00")
    uf = bx("crm.deal.userfield.list", {"order": {"FIELD_NAME": "ASC"}}).get("result") or []
    ff = [str(u["FIELD_NAME"]) for u in uf if u.get("USER_TYPE_ID") == "file"]
    deals = bx_all("crm.deal.list", {"filter": {">=DATE_CREATE": since},
                                     "select": ["ID"], "order": {"ID": "DESC"}})
    ids = [str(d["ID"]) for d in deals]

    refs: list[dict] = []
    for i in range(0, len(ids), 50):
        j = bx("crm.deal.list", {"filter": {"ID": ids[i:i + 50]}, "select": ["ID"] + ff})
        for x in j.get("result") or []:
            for f in ff:
                v = x.get(f)
                if not v:
                    continue
                for fo in (v if isinstance(v, list) else [v]):
                    if isinstance(fo, dict) and fo.get("id"):
                        refs.append(fo)
        if len(refs) >= SAMPLE * 3:
            break
    step = max(1, len(refs) // SAMPLE)
    sample = refs[::step][:SAMPLE]
    print(f"выборка вложений: {len(sample)} из {len(refs)} собранных\n")

    # 1. Какая доля достижима через Диск
    disk_ok = 0
    disk_res: Counter = Counter()
    for fo in sample:
        d = bx("disk.file.get", {"id": fo["id"]})
        u = (d.get("result") or {}).get("DOWNLOAD_URL")
        if u:
            r = probe(u)
            disk_res[r] += 1
            if r.startswith("ФАЙЛ"):
                disk_ok += 1
        else:
            disk_res["объекта Диска нет"] += 1
    print("=== ПУТЬ 1: disk.file.get по идентификатору поля ===")
    print(f"  годных: {disk_ok} из {len(sample)} ({disk_ok / max(len(sample), 1) * 100:.1f}%)")
    print(f"  {dict(disk_res.most_common(5))}\n")

    # 2. Служебный адрес выдачи привязанных файлов
    print("=== ПУТЬ 2: /bitrix/tools/disk/uf.php с ключом вебхука ===")
    uf_res: Counter = Counter()
    for fo in sample[:30]:
        fid = fo["id"]
        for tmpl in (f"{PORTAL}/bitrix/tools/disk/uf.php?attachedId={fid}&auth={TOKEN}&action=download",
                     f"{PORTAL}/bitrix/tools/disk/uf.php?attachedId={fid}&action=download"):
            uf_res[probe(tmpl)] += 1
    print(f"  {dict(uf_res.most_common(5))}\n")

    print("✓ зонд v33 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
