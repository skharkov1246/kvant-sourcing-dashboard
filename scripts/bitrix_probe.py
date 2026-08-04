"""CI-зонд v4: Яндекс.Диск — доступ токеном к «экономикам проектов» и структура файла.

Запускается ТОЛЬКО вручную (workflow_dispatch, .github/workflows/probe.yml).
Секреты (BITRIX_WEBHOOK_URL, YADISK_TOKEN) в лог не выводятся.

Шаги:
  A. YADISK_TOKEN задан? Живой ли (GET /v1/disk/)?
  B. Берём из Bitrix 3 сделки кат.0 со ссылкой на экономику (UF_CRM_1740133235324),
     разворачиваем clck.ru → путь на Диске.
  C. Качаем файл через Disk API, печатаем структуру: листы + сетка + ячейки с «закуп/итого/cost».
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from bitrix_client import BitrixClient

ECON_LINK = "UF_CRM_1740133235324"
DAPI = "https://cloud-api.yandex.net/v1/disk"


def hdr(t):
    print(f"\n{'='*12} {t} {'='*12}")


def main() -> int:
    tok = (os.getenv("YADISK_TOKEN") or "").strip()
    hdr("A. Токен Яндекс.Диска")
    if not tok:
        print("YADISK_TOKEN НЕ задан в секретах — добавьте секрет и перезапустите зонд.")
        return 0
    H = {"Authorization": f"OAuth {tok}"}
    r = requests.get(DAPI + "/", headers=H, timeout=20)
    print(f"GET /v1/disk/ → {r.status_code}")
    if r.status_code != 200:
        print(r.text[:300])
        return 0
    info = r.json()
    print(f"диск доступен: total={round(info.get('total_space',0)/2**30)}GB "
          f"used={round(info.get('used_space',0)/2**30)}GB user={info.get('user',{}).get('display_name','')}")

    hdr("B. Ссылки на экономики из сделок кат.0")
    s = config.Settings.load()
    c = BitrixClient(s.bitrix_webhook_url)
    deals = c.list_deals_fast(filter={"CATEGORY_ID": 0}, select=["ID", "TITLE", ECON_LINK])
    linked = [d for d in deals if str(d.get(ECON_LINK) or "").startswith("http")]
    print(f"сделок кат.0: {len(deals)} | со ссылкой на экономику: {len(linked)}")
    samples = linked[-3:]

    def disk_path_from_link(url: str) -> str | None:
        try:
            rr = requests.head(url, allow_redirects=False, timeout=15)
            loc = rr.headers.get("Location", "")
            # clck → sba.yandex.ru/redirect?url=<enc>; либо сразу disk.yandex.ru/edit/disk/...
            if "url=" in loc:
                loc = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("url", [""])[0]
            m = re.search(r"/edit/disk/([^?]+)", loc)
            if m:
                # слэши внутри пути закодированы (%2F) — сначала раскодируем целиком
                dec = urllib.parse.unquote(m.group(1))
                if "/disk/" in dec:
                    return "/" + dec.split("/disk/", 1)[1]
            m = re.search(r"disk\.yandex\.ru/(?:client/)?disk/([^?]+)", loc)
            if m:
                return "/" + urllib.parse.unquote(m.group(1))
            print(f"  не распознан Location: {loc[:140]}")
        except Exception as e:
            print(f"  ошибка резолва: {type(e).__name__}: {e}")
        return None

    hdr("C. Скачивание и структура")
    try:
        import openpyxl
    except ImportError:
        print("нет openpyxl")
        return 0
    for d in samples:
        did, title = d["ID"], str(d.get("TITLE"))[:46]
        url = str(d.get(ECON_LINK))
        print(f"\n--- сделка {did} «{title}» → {url}")
        path = disk_path_from_link(url)
        print(f"  путь на Диске: {path}")
        if not path:
            continue
        meta = requests.get(DAPI + "/resources", headers=H,
                            params={"path": path, "fields": "name,size,mime_type,file"}, timeout=20)
        print(f"  meta → {meta.status_code}: {json.dumps(meta.json(), ensure_ascii=False)[:220] if meta.ok else meta.text[:220]}")
        if not meta.ok:
            continue
        dl = requests.get(DAPI + "/resources/download", headers=H, params={"path": path}, timeout=20)
        if not dl.ok:
            print(f"  download href → {dl.status_code}: {dl.text[:180]}")
            continue
        href = dl.json().get("href")
        content = requests.get(href, headers=H, timeout=60).content
        print(f"  скачано {len(content)} байт")
        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
        except Exception as e:
            print(f"  не xlsx? {type(e).__name__}: {e}")
            continue
        print(f"  листы: {wb.sheetnames}")
        ws = wb[wb.sheetnames[0]]
        # сетка: первые 24 строки × 10 колонок
        for i, row in enumerate(ws.iter_rows(min_row=1, max_row=24, max_col=10, values_only=True), 1):
            vals = [("" if v is None else str(v))[:16] for v in row]
            if any(vals):
                print(f"   r{i:02}: " + " | ".join(vals))
        # ячейки-ключи
        kw = re.compile(r"(?i)закуп|итог|cost|себестоим|поставщик|маржа|margin|прибыл")
        hits = []
        for i, row in enumerate(ws.iter_rows(min_row=1, max_row=80, max_col=14, values_only=True), 1):
            for jx, v in enumerate(row, 1):
                if isinstance(v, str) and kw.search(v):
                    hits.append(f"r{i}c{jx}='{v[:38]}'")
        print("  ключевые ячейки:", "; ".join(hits[:18]) or "—")

    print("\n✓ зонд v4 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
