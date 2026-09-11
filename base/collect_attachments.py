#!/usr/bin/env python3
"""Список вложений сделок для fetch_files.py.

Ключевой момент доступа: файлы карточек НЕ отдаются по ссылкам вида
`/bitrix/tools/...` и `crm_show_file.php` — там нужна сессия портала, а вебхук
получает страницу входа с кодом 200. Рабочая ссылка — `urlMachine` из
crm.item.list (entityTypeId=2): REST-ссылка с одноразовым токеном, по ней файл
отдаётся как есть. Поэтому обход идёт через crm.item.list, а не crm.deal.list.

Ссылки живут недолго, так что список собирается непосредственно перед
скачиванием, а не хранится про запас.

    python base/collect_attachments.py --db base/kvant.db --out attachments.json
    python base/collect_attachments.py --ids 1,2,3 --out part.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import requests

from parse_archives import webhook


def file_fields(sess: requests.Session, base: str) -> dict[str, str]:
    """Файловые поля сделки: код → человеческое название."""
    r = sess.post(base + "crm.item.fields.json", json={"entityTypeId": 2}, timeout=120).json()
    fields = ((r or {}).get("result") or {}).get("fields") or {}
    return {k: (v.get("title") or k) for k, v in fields.items() if v.get("type") == "file"}


def collect(deal_ids: list[int], out_path: str, pause: float = 0.3) -> int:
    sess = requests.Session()
    base = webhook().rstrip("/") + "/"
    ffields = file_fields(sess, base)
    print(f"файловых полей у сделки: {len(ffields)}", flush=True)
    atts: list[dict] = []
    t0 = time.time()
    for i in range(0, len(deal_ids), 50):
        part = deal_ids[i:i + 50]
        r = None
        for attempt in range(6):
            try:
                r = sess.post(base + "crm.item.list.json", json={
                    "entityTypeId": 2, "filter": {"@id": part},
                    "select": ["id"] + list(ffields), "start": 0}, timeout=180).json()
            except Exception:
                time.sleep(2 * (attempt + 1))
                continue
            if "operation time limit" in (r.get("error_description") or ""):
                time.sleep(min(30 * (attempt + 1), 180))
                continue
            break
        for it in (((r or {}).get("result") or {}).get("items") or []):
            deal = int(it.get("id") or 0)
            for code, title in ffields.items():
                v = it.get(code)
                if not v:
                    continue
                for o in (v if isinstance(v, list) else [v]):
                    if isinstance(o, dict) and o.get("urlMachine"):
                        atts.append({"fid": str(o.get("id")), "deal": deal, "field": code,
                                     "field_name": title, "url": o["urlMachine"]})
        if (i // 50) % 20 == 0 and i:
            done = min(i + 50, len(deal_ids))
            speed = done / max(time.time() - t0, 1)
            print(f"  сделок {done}/{len(deal_ids)} · вложений {len(atts)} · "
                  f"осталось ~{(len(deal_ids)-done)/max(speed,0.01)/60:.0f} мин", flush=True)
        time.sleep(pause)
    Path(out_path).write_text(json.dumps(atts, ensure_ascii=False), encoding="utf-8")
    print(f"готово: вложений {len(atts)} по {len(deal_ids)} сделкам за "
          f"{(time.time()-t0)/60:.1f} мин → {out_path}", flush=True)
    return len(atts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--ids", help="список id сделок через запятую вместо всей базы")
    ap.add_argument("--new-only", action="store_true",
                    help="только сделки, у которых в базе ещё нет ни одного вложения")
    ap.add_argument("--out", default="attachments.json")
    a = ap.parse_args()
    if a.ids:
        ids = [int(x) for x in a.ids.split(",") if x.strip().isdigit()]
    else:
        con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=300)
        if a.new_only:
            ids = [r[0] for r in con.execute(
                "SELECT id FROM deals WHERE id NOT IN (SELECT DISTINCT deal_id FROM files) ORDER BY id")]
        else:
            ids = [r[0] for r in con.execute("SELECT id FROM deals ORDER BY id")]
        con.close()
    print(f"сделок к обходу: {len(ids)}", flush=True)
    collect(ids, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
