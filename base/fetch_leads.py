#!/usr/bin/env python3
"""Выгрузка лидов Битрикса — входящего потока закупок.

Зачем отдельно от сделок. В июне 2026 в портале включился бот тендерных
площадок, и входящий поток вырос с ~1 000 до ~30 000 заявок в месяц. Почти всё
это закрывается со стадией «Тендер: некачественный лид», в сделку не попадает и
потому в базе сделок не видно вовсе. Плюс именно в лиде живёт номер закупки
(UF_CRM_ETP_NUMBER) и разбор письма роботом — завод, оборудование, марка, балл;
при конверсии лида в сделку эти поля не переносятся.

Две особенности портала, из-за которых наивная выгрузка не работает:
  * постранично по смещению отдаётся лишь первые ~11 тысяч записей, дальше
    `next` обрывается — идём по возрастанию ID с фильтром «>ID последнего»;
  * при интенсивных запросах метод отвечает «Method is blocked due to operation
    time limit» — это временная блокировка портала, лечится паузой, а не
    повтором; поле COMMENTS из выборки убрано намеренно, с ним запрос
    выполняется заметно дольше и упирается в лимит быстрее.

Пишем построчно в JSONL, поэтому прогон можно прервать и продолжить: последний
выгруженный ID берётся из уже записанного файла.

    python base/fetch_leads.py --from 2026-01-01 --out leads2026.jsonl
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

from parse_archives import webhook

SELECT = ["ID", "TITLE", "STATUS_ID", "SOURCE_ID", "DATE_CREATE", "MOVED_BY_ID",
          "UF_CRM_ETP_NUMBER", "UF_CRM_LC_SCORE", "UF_CRM_LC_PLANT", "UF_CRM_LC_EQUIP",
          "UF_CRM_LC_BRAND"]
BLOCK_WAIT = 300          # столько ждём снятия блокировки метода
MAX_IDLE = 40             # и не дольше, чем столько раз подряд


def resume_from(out: Path) -> tuple[int, int]:
    """(последний выгруженный ID, сколько уже есть) — чтобы продолжить прогон."""
    last = have = 0
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            have += 1
            try:
                last = max(last, int(json.loads(line)["ID"]))
            except Exception:
                continue
    return last, have


def run(since: str, out_path: str, pause: float = 1.0) -> int:
    base = webhook().rstrip("/") + "/"
    out = Path(out_path)
    last, have = resume_from(out)
    if have:
        print(f"продолжаю: уже выгружено {have}, последний ID {last}", flush=True)
    sess = requests.Session()
    t0, blocked, idle = time.time(), 0, 0
    with out.open("a", encoding="utf-8") as fh:
        while True:
            r = None
            for attempt in range(8):
                try:
                    r = sess.post(base + "crm.lead.list.json", json={
                        "select": SELECT, "filter": {">=DATE_CREATE": since, ">ID": last},
                        "order": {"ID": "ASC"}, "start": -1}, timeout=180).json()
                except Exception:
                    time.sleep(3 * (attempt + 1))
                    continue
                if "operation time limit" in (r.get("error_description") or ""):
                    blocked += 1
                    time.sleep(min(30 * (attempt + 1), 180))
                    continue
                break
            if "operation time limit" in ((r or {}).get("error_description") or ""):
                idle += 1
                if idle > MAX_IDLE:
                    print("портал держит блокировку слишком долго, останавливаюсь", flush=True)
                    break
                time.sleep(BLOCK_WAIT)
                continue
            items = (r or {}).get("result") or []
            if not items:
                break
            idle = 0
            for it in items:
                fh.write(json.dumps(it, ensure_ascii=False) + "\n")
            fh.flush()
            have += len(items)
            last = int(items[-1]["ID"])
            if have % 5000 < len(items):
                print(f"  {have} лидов · {have/max(time.time()-t0,1):.1f} лид/с · "
                      f"блокировок {blocked}", flush=True)
            time.sleep(pause)
    print(f"готово: {have} лидов, блокировок {blocked}, за {(time.time()-t0)/60:.1f} мин "
          f"→ {out}", flush=True)
    return have


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="since", default="2026-01-01",
                    help="дата создания лида, от которой выгружать (ГГГГ-ММ-ДД)")
    ap.add_argument("--out", default="leads.jsonl")
    ap.add_argument("--pause", type=float, default=1.0)
    a = ap.parse_args()
    since = a.since if "T" in a.since else f"{a.since}T00:00:00+03:00"
    run(since, a.out, a.pause)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
