"""CI-зонд v6: доступен ли Общий диск Яндекс 360 (vd/86djqFRmaSXN6g) через API токеном.

Пробуем все известные варианты адресации общих дисков:
REST resources с разными префиксами пути + WebDAV PROPFIND.
"""
from __future__ import annotations

import os
import sys

import requests

DAPI = "https://cloud-api.yandex.net/v1/disk"
VD = "86djqFRmaSXN6g"
FILE = "Экономика проекта/850_1_hydac_лц_кфс393_нн-805.xlsx"


def main() -> int:
    tok = (os.getenv("YADISK_TOKEN") or "").strip()
    if not tok:
        print("нет токена")
        return 0
    H = {"Authorization": f"OAuth {tok}"}

    print("=== REST /resources: варианты путей к общему диску ===")
    variants = [
        f"vd:/{VD}",
        f"vd:{VD}",
        f"/vd/{VD}",
        f"vd:/{VD}/Экономика проекта",
        f"vd:/{VD}/{FILE}",
        "shared:/",
        "common:/",
        f"disk:/vd/{VD}",
    ]
    for p in variants:
        try:
            r = requests.get(DAPI + "/resources", headers=H, params={"path": p, "limit": 5,
                             "fields": "name,type,_embedded.items.name"}, timeout=20)
            body = r.text[:160].replace("\n", " ")
            print(f"  path={p!r} → {r.status_code} {body}")
        except Exception as e:
            print(f"  path={p!r} → EXC {type(e).__name__}")

    print("\n=== список общих дисков? (недокументированные ручки) ===")
    for url in [DAPI + "/vd", DAPI + "/resources/vd",
                "https://cloud-api.yandex.net/v1/disk?fields=*"]:
        try:
            r = requests.get(url, headers=H, timeout=20)
            print(f"  GET {url} → {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"  GET {url} → EXC {type(e).__name__}")

    print("\n=== WebDAV PROPFIND / (Depth 1) ===")
    try:
        r = requests.request("PROPFIND", "https://webdav.yandex.ru/", headers={**H, "Depth": "1"}, timeout=25)
        print("status", r.status_code)
        import re
        names = re.findall(r"<d:href>([^<]+)</d:href>", r.text)[:30]
        for n in names:
            print("  ", requests.utils.unquote(n))
    except Exception as e:
        print("EXC", type(e).__name__, e)

    print("\n✓ зонд v6 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
