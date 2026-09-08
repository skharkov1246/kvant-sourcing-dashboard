"""Зонд v31: каким способом вообще скачиваются вложения сделок.

Пробный прогон индексатора: 50 файлов из 50 — «не скачался». Значит дело не в
хранилище и не в пуле соединений, а в способе получения. Разведка v29 давала
9 из 40 через disk.file.get — то есть какой-то путь работает, но не для всех.

Зонд не гадает, а перебирает пути на одной выборке и печатает, что ответил каждый:
  • ссылки, лежащие в самом объекте файла (какие там вообще есть ключи);
  • те же ссылки с добавленным ключом вебхука;
  • disk.file.get по идентификатору;
  • disk.attachedObject.get — для файлов, привязанных как объекты Диска.

ПЕЧАТАЮТСЯ ТОЛЬКО ИМЕНА КЛЮЧЕЙ И КОДЫ ОТВЕТОВ. Ни ссылок, ни имён файлов, ни
содержимого: репозиторий публичный, журналы сборок открыты.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

BASE = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
# из вебхука вида https://<портал>/rest/<id>/<токен>/ достаём части для ручных ссылок
m = re.match(r"(https://[^/]+)/rest/(\d+)/([^/]+)", BASE)
PORTAL, USER_ID, TOKEN = (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")
N = 25


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


def probe_url(u: str) -> str:
    try:
        r = requests.get(u, timeout=45, allow_redirects=True)
        if r.status_code != 200:
            return f"http {r.status_code}"
        b = r.content
        if len(b) < 200:
            return f"пусто ({len(b)} б)"
        head = b[:4]
        if head[:2] == b"PK":
            return "ФАЙЛ xlsx/docx"
        if head == b"%PDF":
            return "ФАЙЛ pdf"
        if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            return "ФАЙЛ office"
        if b.lstrip()[:1] in (b"<",):
            return "страница входа (html)"
        return f"иное ({len(b)} б)"
    except Exception as e:
        return f"ошибка {type(e).__name__}"


def main() -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00+03:00")
    print(f"=== Зонд v31: способы скачивания вложений (выборка {N}) ===")
    print(f"портал разобран из вебхука: {'да' if PORTAL else 'НЕТ — ручные ссылки не построить'}\n")

    uf = bx("crm.deal.userfield.list", {"order": {"FIELD_NAME": "ASC"}}).get("result") or []
    ff = [str(u["FIELD_NAME"]) for u in uf if u.get("USER_TYPE_ID") == "file"]
    deals = bx_all("crm.deal.list", {"filter": {">=DATE_CREATE": since},
                                     "select": ["ID"], "order": {"ID": "DESC"}})
    ids = [str(d["ID"]) for d in deals[:400]]

    refs: list[tuple[str, str, dict]] = []
    keyset: Counter = Counter()
    for i in range(0, len(ids), 50):
        j = bx("crm.deal.list", {"filter": {"ID": ids[i:i + 50]}, "select": ["ID"] + ff})
        for x in j.get("result") or []:
            for f in ff:
                v = x.get(f)
                if not v:
                    continue
                for fo in (v if isinstance(v, list) else [v]):
                    if isinstance(fo, dict):
                        keyset.update(fo.keys())
                        refs.append((str(x["ID"]), f, fo))
        if len(refs) >= N * 4:
            break

    print(f"объектов файлов собрано: {len(refs)}")
    print(f"КЛЮЧИ объекта файла: {dict(keyset.most_common())}\n")
    if not refs:
        print("вложений не найдено")
        return 0

    url_keys = [k for k in keyset if "url" in k.lower() or "link" in k.lower() or "download" in k.lower()]
    print(f"ключи, похожие на ссылку: {url_keys}\n")

    res: dict[str, Counter] = {}

    def note(way: str, outcome: str) -> None:
        res.setdefault(way, Counter())[outcome] += 1

    for _did, _f, fo in refs[:N]:
        fid = fo.get("id") or fo.get("ID")
        for k in url_keys:
            u = fo.get(k)
            if not u:
                note(f"ключ {k}", "нет значения")
                continue
            u = str(u)
            full = u if u.startswith("http") else (PORTAL + u if PORTAL else "")
            if not full:
                note(f"ключ {k}", "относительная ссылка, портал неизвестен")
                continue
            note(f"ключ {k}", probe_url(full))
            sep = "&" if "?" in full else "?"
            note(f"ключ {k} + auth", probe_url(f"{full}{sep}auth={TOKEN}"))
        if fid:
            d = bx("disk.file.get", {"id": fid})
            u = (d.get("result") or {}).get("DOWNLOAD_URL")
            note("disk.file.get", probe_url(u) if u else f"нет DOWNLOAD_URL ({str(d.get('error') or '')[:40]})")
            a = bx("disk.attachedObject.get", {"id": fid})
            u2 = (a.get("result") or {}).get("DOWNLOAD_URL")
            note("disk.attachedObject.get", probe_url(u2) if u2 else f"нет ссылки ({str(a.get('error') or '')[:40]})")

    print("=== ЧТО ОТВЕТИЛ КАЖДЫЙ СПОСОБ ===")
    for way, c in res.items():
        good = sum(n for o, n in c.items() if o.startswith("ФАЙЛ"))
        print(f"{way:34s} годных {good:>3d} из {sum(c.values()):>3d} · {dict(c.most_common(4))}")

    print("\n✓ зонд v31 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
