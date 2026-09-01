# -*- coding: utf-8 -*-
"""Массовый сбор контактов поставщиков с их сайтов.

Обходит сайты из реестра, у которых нет ни email, ни телефона, читает главную
и типовые страницы контактов, вынимает адреса и номера. Ничего не выдумывает:
что не нашлось на странице — остаётся пустым.

Результат дописывается в pnw/data/contacts_found.json (слой обогащения),
исходный реестр не перезаписывается.

Запуск:  python3 pnw/tools/harvest_contacts.py [сколько_поставщиков]
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "pnw" / "data"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PAGES = ["", "contact", "contact-us", "pages/contact", "contactus", "about-us"]

EMAIL = re.compile(r"[\w.\-+]+@[\w\-]+\.[\w.\-]{2,}", re.I)
# Телефон берём ТОЛЬКО из tel:-ссылок или рядом с явным указателем.
# Иначе в номера попадают каталожные артикулы: 5580004128 и 3115078900 —
# это партномера Epiroc, а не телефоны, и они матчатся любым общим шаблоном.
TEL_LINK = re.compile(r"tel:\s*([+\d][\d\s\-()]{7,20})", re.I)
PHONE_NEAR = re.compile(
    r"(?:tel|phone|telephone|моб|тел|call|whatsapp|wechat|电话|手机|传真|fax)"
    r"[^\d+]{0,18}(\+?\d[\d\s\-()]{7,19}\d)", re.I)
PHONE_INTL = re.compile(r"(\+\d{1,3}[\s\-]?\d[\d\s\-()]{6,18}\d)")
BAD_MAIL = re.compile(r"\.(png|jpe?g|gif|webp|svg|css|js)$|sentry|example\.|"
                      r"@(?:sentry|wixpress|godaddy|cloudflare)", re.I)
TAG = re.compile(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>|<[^>]+>", re.S)


def text_of(html):
    t = TAG.sub(" ", html)
    mail = " ".join(re.findall(r"mailto:([^\"'\s>]+)", html, re.I))
    return t + " " + mail


def pick_email(cands, domain):
    """Предпочитаем адрес в домене сайта и служебные ящики продаж."""
    good = [c for c in cands if not BAD_MAIL.search(c)]
    if not good:
        return ""
    dom = re.sub(r"^www\.", "", domain).lower()
    same = [c for c in good if c.lower().endswith("@" + dom) or dom.split(".")[0] in c.lower()]
    pool = same or good
    for pref in ("sales@", "export", "info@", "sale@", "trade", "market"):
        for c in pool:
            if c.lower().startswith(pref) or pref in c.lower():
                return c
    return pool[0]


def phones_from(html, text):
    """Три источника по убыванию надёжности: tel:-ссылка, номер рядом со словом
    «телефон», номер с международным префиксом. Голые десятизначные числа без
    контекста НЕ берём — это чаще всего артикул."""
    out = []
    for rx, src in ((TEL_LINK, html), (PHONE_NEAR, text), (PHONE_INTL, text)):
        for c in rx.findall(src):
            d = re.sub(r"\D", "", c)
            if not (9 <= len(d) <= 15) or d.startswith(("000", "111")):
                continue
            v = re.sub(r"\s{2,}", " ", c.strip())
            if v not in out:
                out.append(v)
        if out:
            break
    return out


def pick_phone(cands):
    return cands[0][:30] if cands else ""


def fetch(url, timeout=12, tries=3):
    """Прокси иногда рвёт соединение на середине обмена — без повтора страница
    ошибочно считается пустой. Три попытки снимают почти все такие потери."""
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout,
                             allow_redirects=True)
            if r.status_code < 400 and r.text:
                return r.text
            if r.status_code in (401, 403, 404, 410):
                return ""            # осмысленный отказ — повторять незачем
        except Exception:
            pass
    return ""


def harvest(site):
    base = site if site.startswith("http") else "https://" + site
    base = base.rstrip("/")
    domain = re.sub(r"^https?://", "", base).split("/")[0]
    emails, phones, src = [], [], ""
    for suf in PAGES:
        html = fetch(base + ("/" + suf if suf else ""))
        if not html:
            continue
        t = text_of(html)
        e = [x.lower() for x in EMAIL.findall(t)]
        p = phones_from(html, t)
        if e:
            emails += e
            phones += p
            src = base + ("/" + suf if suf else "")
            break
        if p and not phones:
            phones = p
            src = src or base + ("/" + suf if suf else "")
    return pick_email(list(dict.fromkeys(emails)), domain), pick_phone(phones), src


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    sup = json.loads((DATA / "supplier_master.json").read_text(encoding="utf-8"))["suppliers"]
    parts = json.loads((DATA / "part_suppliers.json").read_text(encoding="utf-8"))["parts"]
    found_p = DATA / "contacts_found.json"
    found = json.loads(found_p.read_text(encoding="utf-8"))

    weight = Counter()
    for b in parts.values():
        for ks in b.values():
            for k in ks:
                weight[k] += 1

    todo = [(n, k) for k, n in weight.items()
            if k not in found["contacts"]
            and not (sup[k].get("email") or sup[k].get("phone"))
            and sup[k].get("site")]
    todo.sort(reverse=True)
    todo = todo[:limit]
    print(f"на обход: {len(todo)} поставщиков (закрывают {sum(t[0] for t in todo)} привязок)\n")

    ok = 0
    for n, k in todo:
        s = sup[k]
        em, ph, src = harvest(s["site"])
        mark = "  "
        if em or ph:
            found["contacts"][k] = {
                "name": s["name"], "city": s.get("city", ""), "email": em, "phone": ph,
                "whatsapp": "", "site": s["site"], "source": src,
                "note": (s.get("what") or "")[:160],
            }
            ok += 1
            mark = "OK"
        print(f"{mark} {n:4} дет. {s['name'][:38]:40} {em[:34]:36} {ph[:18]}")

    found["updated"] = "2026-09-01"
    found_p.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nнайдено контактов: {ok} из {len(todo)}; всего в слое: {len(found['contacts'])}")


if __name__ == "__main__":
    main()
