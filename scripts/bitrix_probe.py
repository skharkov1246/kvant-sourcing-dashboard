"""Зонд v34: подстановка ключа в ссылку выдачи файла.

Проверка v31 могла быть ошибочной. Ссылка downloadUrl у Битрикса, как правило, уже
содержит параметр auth= с пустым значением. Я дописывал второй auth= в конец —
портал читает первый, пустой, и отдаёт страницу входа. То есть «страница входа
25 из 25» могла означать не «путь закрыт», а «ключ подставлен не туда».

Здесь ключ подставляется четырьмя способами, и печатается, чем ответил каждый:
  как есть · дописан в конец · подставлен в существующий пустой auth= ·
  собран заново из идентификатора файла.

ПЕЧАТАЮТСЯ ТОЛЬКО ПРИЗНАКИ ССЫЛКИ (есть ли в ней auth=, относительная ли она),
КОДЫ ОТВЕТОВ И ДОЛИ. Сами ссылки, имена файлов и содержимое не выводятся.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

BASE = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
m = re.match(r"(https://[^/]+)/rest/(\d+)/([^/]+)", BASE)
PORTAL, USER_ID, TOKEN = (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")
N = 20


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
    if not u:
        return "ссылки нет"
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
        s = b.lstrip()[:1]
        if s == b"<":
            return "страница входа (html)"
        return f"ФАЙЛ иное ({len(b)} б)"
    except Exception as e:
        return f"ошибка {type(e).__name__}"


def with_auth(u: str) -> str:
    """Подставляет ключ в существующий параметр auth=, а не дописывает второй."""
    p = urlparse(u)
    q = parse_qs(p.query, keep_blank_values=True)
    q["auth"] = [TOKEN]
    return urlunparse(p._replace(query=urlencode(q, doseq=True)))


def main() -> int:
    print("=== Зонд v34: как правильно подставить ключ в ссылку файла ===\n")
    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00+03:00")
    uf = bx("crm.deal.userfield.list", {"order": {"FIELD_NAME": "ASC"}}).get("result") or []
    ff = [str(u["FIELD_NAME"]) for u in uf if u.get("USER_TYPE_ID") == "file"]
    deals = bx_all("crm.deal.list", {"filter": {">=DATE_CREATE": since},
                                     "select": ["ID"], "order": {"ID": "DESC"}})
    ids = [str(d["ID"]) for d in deals[:200]]

    refs: list[dict] = []
    for i in range(0, len(ids), 50):
        j = bx("crm.deal.list", {"filter": {"ID": ids[i:i + 50]}, "select": ["ID"] + ff})
        for x in j.get("result") or []:
            for f in ff:
                v = x.get(f)
                if not v:
                    continue
                for fo in (v if isinstance(v, list) else [v]):
                    if isinstance(fo, dict) and fo.get("downloadUrl"):
                        refs.append(fo)
        if len(refs) >= N:
            break
    refs = refs[:N]
    print(f"вложений в выборке: {len(refs)}")
    if not refs:
        print("вложений не найдено")
        return 0

    # признаки ссылки — без самой ссылки
    sample_u = str(refs[0]["downloadUrl"])
    pr = urlparse(sample_u)
    q = parse_qs(pr.query, keep_blank_values=True)
    print(f"ссылка относительная: {'да' if not sample_u.startswith('http') else 'нет'}")
    print(f"путь ссылки: {pr.path}")
    print(f"имена параметров: {sorted(q.keys())}")
    print(f"параметр auth присутствует: {'да' if 'auth' in q else 'нет'}"
          f" · пустой: {'да' if q.get('auth') == [''] else 'нет'}\n")

    res: dict[str, Counter] = {}

    def note(way: str, out: str) -> None:
        res.setdefault(way, Counter())[out] += 1

    for fo in refs:
        for key in ("downloadUrl", "showUrl"):
            u = str(fo.get(key) or "")
            if not u:
                continue
            full = u if u.startswith("http") else PORTAL + u
            note(f"{key}: как есть", probe(full))
            sep = "&" if "?" in full else "?"
            note(f"{key}: ключ дописан в конец", probe(f"{full}{sep}auth={TOKEN}"))
            note(f"{key}: ключ подставлен в auth=", probe(with_auth(full)))
        fid = fo.get("id")
        if fid:
            note("собрана заново: /rest/.../download",
                 probe(f"{BASE}/download.json?fileId={fid}"))
            note("собрана заново: uf.php с ключом",
                 probe(with_auth(f"{PORTAL}/bitrix/tools/disk/uf.php?attachedId={fid}&action=download&auth=")))

    print("=== ЧТО ОТВЕТИЛ КАЖДЫЙ СПОСОБ ===")
    for way, c in res.items():
        good = sum(n for o, n in c.items() if o.startswith("ФАЙЛ"))
        mark = "  ✔ РАБОТАЕТ" if good else ""
        print(f"{way:44s} годных {good:>3d} из {sum(c.values()):>3d} · {dict(c.most_common(3))}{mark}")

    print("\n✓ зонд v34 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
