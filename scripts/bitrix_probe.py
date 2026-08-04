"""CI-зонд v5: где на самом деле лежит «Экономика проекта» на диске stepan@kvantpro.com.

A. Листинг корня диска (папки).
B. Поиск xlsx-файлов по всему диску (resources/files) — ищем 'hydac'/'экономика' в путях.
C. Если нашли реальный префикс — качаем один файл и печатаем структуру (листы, сетка, ключевые ячейки).
"""
from __future__ import annotations

import io
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DAPI = "https://cloud-api.yandex.net/v1/disk"


def hdr(t):
    print(f"\n{'='*12} {t} {'='*12}")


def main() -> int:
    tok = (os.getenv("YADISK_TOKEN") or "").strip()
    if not tok:
        print("YADISK_TOKEN не задан")
        return 0
    H = {"Authorization": f"OAuth {tok}"}

    hdr("A. Корень диска")
    r = requests.get(DAPI + "/resources", headers=H,
                     params={"path": "/", "limit": 200, "fields": "_embedded.items.name,_embedded.items.type,_embedded.items.path"},
                     timeout=30)
    print("status", r.status_code)
    items = (r.json().get("_embedded", {}) or {}).get("items", []) if r.ok else []
    for it in items:
        print(f"  [{it.get('type')}] {it.get('name')}")

    hdr("B. Поиск xlsx по диску (первые 1000 файлов)")
    found_pref = None
    offset = 0
    hits = []
    while offset < 1000:
        rf = requests.get(DAPI + "/resources/files", headers=H,
                          params={"limit": 100, "offset": offset,
                                  "fields": "items.name,items.path,items.size"},
                          timeout=30)
        if not rf.ok:
            print("files:", rf.status_code, rf.text[:150])
            break
        fitems = rf.json().get("items", [])
        if not fitems:
            break
        for it in fitems:
            p = str(it.get("path", ""))
            low = p.lower()
            if "эконом" in low or "hydac" in low or low.endswith(".xlsx"):
                hits.append(p)
        offset += 100
    print(f"кандидатов: {len(hits)}")
    for p in hits[:25]:
        print("  ", p)
    for p in hits:
        if "эконом" in p.lower():
            found_pref = p
            break

    hdr("C. Структура файла экономики")
    target = found_pref or (hits[0] if hits else None)
    if not target:
        print("файлы не найдены на этом диске — папка, вероятно, расшарена без монтирования.")
        print("Решение: в веб-интерфейсе Я.Диска у stepan@kvantpro.com открыть «Общий доступ» →")
        print("папку «Экономика проекта» → «Сохранить на свой Диск» (подключить), либо выдать токен")
        print("от аккаунта-владельца папки.")
        return 0
    path = target.replace("disk:", "")
    print("качаем:", path)
    dl = requests.get(DAPI + "/resources/download", headers=H, params={"path": path}, timeout=30)
    if not dl.ok:
        print("download:", dl.status_code, dl.text[:180])
        return 0
    content = requests.get(dl.json().get("href"), headers=H, timeout=60).content
    print(f"скачано {len(content)} байт")
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    print("листы:", wb.sheetnames)
    ws = wb[wb.sheetnames[0]]
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=26, max_col=10, values_only=True), 1):
        vals = [("" if v is None else str(v))[:15] for v in row]
        if any(vals):
            print(f"   r{i:02}: " + " | ".join(vals))
    kw = re.compile(r"(?i)закуп|итог|cost|себестоим|поставщик|маржа|margin|прибыл|доход|расход")
    hits2 = []
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=90, max_col=14, values_only=True), 1):
        for jx, v in enumerate(row, 1):
            if isinstance(v, str) and kw.search(v):
                hits2.append(f"r{i}c{jx}='{v[:36]}'")
    print("ключевые ячейки:", "; ".join(hits2[:20]) or "—")
    print("\n✓ зонд v5 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
