#!/usr/bin/env python3
"""Выгрузка файлов-вложений ГТУ-сделок Bitrix24 → gt/data/bitrix_attachments.json.

Что делает (нужен BITRIX_WEBHOOK_URL в окружении):
1) находит ГТУ-сделки по ключевым словам;
2) читает файловые UF-поля сделок (crm.deal.userfield.list, USER_TYPE_ID=file);
3) пытается скачать каждый файл (несколько стратегий: disk API, прямая
   downloadUrl); если скоупа не хватает — фиксирует это в отчёте;
4) из скачанных xlsx/csv/txt извлекает парт-номера (Solar/Siemens/GE паттерны);
5) пишет инвентарь + извлечённые PN в gt/data/bitrix_attachments.json.

Запуск: Actions → «GT bitrix attachments» → Run workflow.
Секреты в лог не выводятся.
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

OUT = ROOT / "gt/data/bitrix_attachments.json"

KEYWORDS = ["SGT", "Solar", "Taurus", "Centaur", "Titan", "Mars", "Saturn",
            "ГТУ", "газотурбин", "газовая турбина", "Typhoon", "Tornado",
            "турбин", "LM2500", "LM6000", "MS5001", "6FA", "9FA", "RB211"]

PN_RES = [
    r"\b\d{6,7}-\d{1,4}(?:-\d{1,4})?\b", r"\b\d{6}C\d\b", r"\b64/\d{8}/\d{1,4}\b",
    r"\b[MR][WTU]\d{4,5}[A-Z]?(?:/\d+)?\b", r"\bCT\d{3,5}[A-Z]?/\d+\b",
    r"\bSP0\d{5}\b", r"\bESP0\d{5}\b", r"\bE\d{6}-\d{3}\b",
    r"\b\d{3,4}[A-Z]{1,2}\d{4}[PG]\d{3,4}\b", r"\b\d{8}P\d{3}\b",
    r"\b\d{4}M\d{2}P\d{2}\b", r"\b\d{5,6}/\d{2,3}\b",
]
CYR = str.maketrans({"А": "A", "В": "B", "С": "C", "Е": "E", "К": "K", "М": "M",
                     "Н": "H", "О": "O", "Р": "P", "Т": "T", "Х": "X"})


def extract_pns(text: str) -> list[str]:
    t = text.upper().translate(CYR).replace(" ", "")
    out: set[str] = set()
    for rx in PN_RES:
        out.update(re.findall(rx, t))
    return sorted(out)


def main() -> None:
    wh = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not wh:
        print("нет BITRIX_WEBHOOK_URL", file=sys.stderr)
        sys.exit(1)
    bx = BitrixClient(wh)

    # 1. ГТУ-сделки
    deals: dict[str, dict] = {}
    for kw in KEYWORDS:
        start: int | None = 0
        while start is not None:
            d = bx.call("crm.deal.list", {
                "filter": {"%TITLE": kw},
                "select": ["ID", "TITLE", "CATEGORY_ID"],
                "order": {"ID": "ASC"}, "start": start})
            for x in d.get("result", []):
                deals[str(x["ID"])] = x
            start = d.get("next")
    print(f"ГТУ-сделок: {len(deals)}")

    # 2. файловые UF-поля
    uf = bx.call("crm.deal.userfield.list", {"order": {"FIELD_NAME": "ASC"}})
    file_fields = [u["FIELD_NAME"] for u in uf.get("result", [])
                   if u.get("USER_TYPE_ID") == "file"]
    print(f"файловых полей: {len(file_fields)}")

    inventory = []
    extracted: dict[str, list[str]] = {}
    n_dl = n_fail = 0

    ids = sorted(int(k) for k in deals)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        d = bx.call("crm.deal.list", {
            "filter": {"ID": chunk}, "select": ["ID", "TITLE"] + file_fields})
        for x in d.get("result", []):
            for f_name in file_fields:
                v = x.get(f_name)
                if not v:
                    continue
                files = v if isinstance(v, list) else [v]
                for fobj in files:
                    if not isinstance(fobj, dict) or "id" not in fobj:
                        continue
                    rec = {
                        "deal_id": x["ID"], "deal_title": x.get("TITLE", "")[:120],
                        "field": f_name, "file_id": fobj.get("id"),
                        "file_name": fobj.get("fileName", ""),
                    }
                    # 3. попытки скачивания
                    content = None
                    try:
                        df = bx.call("disk.file.get", {"id": fobj["id"]})
                        dl = (df.get("result") or {}).get("DOWNLOAD_URL")
                        if dl:
                            import requests
                            rr = requests.get(dl, timeout=60)
                            if rr.status_code == 200 and len(rr.content) > 100:
                                content = rr.content
                    except Exception:
                        pass
                    if content:
                        n_dl += 1
                        rec["size"] = len(content)
                        text = ""
                        fn = (rec["file_name"] or "").lower()
                        if fn.endswith((".txt", ".csv")):
                            text = content.decode("utf-8", errors="ignore")
                        elif fn.endswith((".xlsx", ".xls")):
                            try:
                                import io
                                import openpyxl
                                wbk = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
                                for wss in wbk.worksheets:
                                    for row_ in wss.iter_rows(values_only=True):
                                        text += " ".join(str(c) for c in row_ if c) + "\n"
                            except Exception:
                                pass
                        pns = extract_pns(text)
                        if pns:
                            rec["pns"] = pns[:200]
                            extracted[str(x["ID"])] = sorted(set(extracted.get(str(x["ID"]), []) + pns))
                    else:
                        n_fail += 1
                        rec["download"] = "недоступно (скоуп вебхука без disk или сессия)"
                    inventory.append(rec)

    out = {
        "updated": date.today().isoformat(),
        "source": "Bitrix24: файловые UF-поля ГТУ-сделок (live)",
        "deals_total": len(deals),
        "files_total": len(inventory),
        "downloaded": n_dl,
        "unavailable": n_fail,
        "inventory": inventory,
        "pns_by_deal": extracted,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"файлов: {len(inventory)} · скачано: {n_dl} · недоступно: {n_fail} · "
          f"сделок с PN из файлов: {len(extracted)} → {OUT}")
    if n_fail and not n_dl:
        print("ВНИМАНИЕ: ни один файл не скачан — добавьте вебхуку скоуп «Диск» "
              "(disk) в Bitrix (Разработчикам → Вебхуки → права).")


if __name__ == "__main__":
    main()
