# -*- coding: utf-8 -*-
"""Реестр поставщиков: сведение контактов из всех разделов + привязка к деталям.

Собирает поставщиков из ЗИП (ODM, КП, CRM), ГТУ (исследование, тяжёлые машины,
Битрикс), базы подшипников и материалов. Дедуплицирует по имени, объединяет
контакты: чей email нашёлся в одном источнике, а телефон в другом — склеиваются
в одну карточку.

Привязка к детали по правилу рабочей таблицы:
  П1 — Китай, П2 — РФ и СНГ, П3 — прочие страны.

Выход:
  pnw/data/supplier_master.json — реестр поставщиков с контактами
  pnw/data/part_suppliers.json  — KV детали → П1/П2/П3
"""
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "pnw" / "data"

EMAIL = re.compile(r"[\w\.\-\+]+@[\w\-]+\.[\w\.\-]{2,}")
PHONE = re.compile(r"\+?\d[\d\s\-\(\)]{8,}\d")
CN = re.compile(r"кита|china|\bcn\b|китай", re.I)
RU = re.compile(r"росси|russia|\bru\b|беларус|казахстан|узбекистан|киргиз|армени", re.I)


def load(p):
    f = ROOT / p
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def nkey(name):
    """Ключ дедупликации: имя без юрформ и знаков."""
    s = re.split(r"[/(（]", str(name or ""))[0].lower()
    s = re.sub(r"\b(ооо|оао|зао|пао|ао|тоо|ltd|llc|inc|co|gmbh|corp\w*|s\.?r\.?l|"
               r"pte|sdn|bhd|a/s|b\.?v|s\.?a|group|company|limited)\b", " ", s)
    return re.sub(r"[^0-9a-zа-я一-鿿]", "", s)[:22]


CN_HINT = re.compile(r"\.cn\b|made-in-china|alibaba|shanghai|guangzhou|qingdao|jinan|"
                     r"ningbo|shenzhen|hangzhou|wuxi|xiamen|fujian|zhejiang|jiangsu|shandong|"
                     r"hebei|henan|hunan|sichuan|chengdu|chongqing|tianjin|dongguan|foshan|"
                     r"yantai|linyi|taizhou|wenzhou|xian|xi'an|beijing|nanjing|changsha|"
                     r"zhengzhou|luoyang|baoding|zibo|liaoning|anhui|jiangxi|guangdong|xingtai|nokcn|hovoo|yiwu|weifang|zhuzhou|changzhou|suzhou|kunshan|huludao|liaocheng|yanggu|quanzhou|putian|ganzhou|nantong|jiaxing|jiashan", re.I)
RU_HINT = re.compile(r"\.ru\b|\.by\b|\.kz\b|ооо|оао|зао|пао|\bао\b|тоо", re.I)


def geo(country, name="", site=""):
    """П1 Китай, П2 РФ и СНГ, П3 прочие. Если страна не заполнена — определяем
    по домену и названию: у части поставщиков страна в источнике отсутствует,
    но домен .cn или китайский город в имени однозначно её задают."""
    c = str(country or "")
    if CN.search(c):
        return "П1"
    if RU.search(c):
        return "П2"
    if not c.strip():
        blob = f"{name} {site}"
        if CN_HINT.search(blob):
            return "П1"
        if RU_HINT.search(blob):
            return "П2"
    return "П3"


def clean_phone(v):
    v = re.split(r"[;,\n]| / ", str(v or ""))[0].strip()
    v = re.sub(r"\s*\((?![^)]*\d{3})[^)]*\)", "", v).strip()
    return v[:34] if len(re.sub(r"\D", "", v)) >= 7 else ""


def clean_email(v):
    m = EMAIL.search(str(v or ""))
    return m.group(0)[:48] if m else ""


def main():
    S = {}          # ключ → карточка поставщика

    def put(name, country="", city="", what="", site="", email="", phone="", src="", conf=""):
        if not str(name or "").strip():
            return None
        k = nkey(name)
        if not k:
            return None
        r = S.setdefault(k, {"name": str(name).strip()[:70], "country": "", "city": "",
                             "what": "", "site": "", "email": "", "phone": "",
                             "sources": [], "conf": ""})
        # берём первое непустое значение, но лучшее имя — самое длинное
        if len(str(name).strip()) > len(r["name"]):
            r["name"] = str(name).strip()[:70]
        for f, v in (("country", country), ("city", city), ("what", what), ("site", site)):
            if not r[f] and str(v or "").strip() and not str(v).lower().startswith("не найд"):
                r[f] = str(v).strip()[:90]
        if not r["email"]:
            r["email"] = clean_email(email)
        if not r["phone"]:
            r["phone"] = clean_phone(phone)
        if src and src not in r["sources"]:
            r["sources"].append(src)
        if conf and not r["conf"]:
            r["conf"] = conf
        return k

    # ── источники с контактами ──────────────────────────────────────────
    for path, key, src in (("gt/data/heavy_suppliers.json", "rows", "ГТУ тяжёлые"),
                           ("gt/data/research_suppliers.json", "rows", "ГТУ исследование")):
        d = load(path)
        for r in (d or {}).get(key, []):
            put(r.get("name"), r.get("country"), r.get("city"), r.get("what") or r.get("role"),
                r.get("site"), r.get("email"), r.get("phone"), src, r.get("conf"))

    for path, src in (("zip/data/bearing_sites_ru.json", "подшипники РФ/мир"),
                      ("zip/data/bearing_sites_cn.json", "подшипники КНР")):
        d = load(path) or {}
        for r in d.get("sites", []):
            put(r.get("company"), r.get("country"), r.get("city"),
                r.get("tech") or r.get("profile"), r.get("site"),
                r.get("email"), r.get("phone"), src, r.get("confidence"))

    d = load("zip/data/supplier_crm.json") or {}
    for r in d.get("suppliers", []):
        c = str(r.get("contacts") or "")
        put(r.get("name"), r.get("geo"), "", r.get("direction"),
            (re.search(r"([\w\-]+\.(?:com|cn|ru|net|org|ae|tr)[\w/\.\-]*)", c) or [None, ""])[0]
            if re.search(r"[\w\-]+\.(com|cn|ru|net|org|ae|tr)", c) else "",
            c, c, "CRM прозвона", "")

    for r in (load("zip/data/material_suppliers.json") or []):
        put(r.get("name"), r.get("country"), "", r.get("products"), r.get("url"),
            r.get("contact"), r.get("contact"), "материалы", r.get("confidence"))

    d = load("gt/data/bitrix_supplier_sites.json") or {}
    for name, r in (d.get("confirmed") or {}).items():
        put(name, "", "", r.get("ev"), r.get("site"), r.get("email"), "", "Битрикс", "")

    # ODM ЗИП — привязка к деталям (контактов почти нет, но связь нужна)
    odm = load("zip/data/odm_suppliers.json") or []
    link = defaultdict(set)      # position_id → ключи поставщиков
    for o in odm:
        k = put(o.get("name"), o.get("country"), "", o.get("makes"),
                o.get("catalog_url") or o.get("source_url"), "", "", "ODM ЗИП", o.get("confidence"))
        if k and o.get("position_id"):
            link[o["position_id"]].add(k)
    # КП: экспортёры с ценами
    for r in (load("zip/data/price_records.json") or []):
        if "КП поставщика" not in str(r.get("source")):
            continue
        k = put(r.get("exporter"), r.get("country"), "", "давал КП", r.get("url"), "", "", "КП", "high")
        if k and r.get("position_id"):
            link[r["position_id"]].add(k)

    # ── привязка П1/П2/П3 к деталям справочника ─────────────────────────
    master = json.loads((OUT / "item_master.json").read_text(encoding="utf-8"))["items"]
    parts = {}
    for it in master:
        sid = it.get("src_id")
        if it["section"] != "ЗИП ГШО" or sid not in link:
            continue
        buckets = {"П1": [], "П2": [], "П3": []}
        for k in link[sid]:
            s = S.get(k)
            if not s:
                continue
            buckets[geo(s["country"], s["name"], s.get("site", ""))].append(k)
        # внутри группы вперёд тех, у кого есть контакт
        for b in buckets:
            buckets[b].sort(key=lambda k: (not (S[k]["email"] or S[k]["phone"]), S[k]["name"]))
        if any(buckets.values()):
            parts[it["kv"]] = {b: v[:3] for b, v in buckets.items() if v}

    sup = {k: v for k, v in S.items() if v["name"]}
    (OUT / "supplier_master.json").write_text(json.dumps(
        {"updated": "2026-09-01", "count": len(sup), "suppliers": sup},
        ensure_ascii=False), encoding="utf-8")
    (OUT / "part_suppliers.json").write_text(json.dumps(
        {"updated": "2026-09-01",
         "note": "П1 — Китай, П2 — РФ и СНГ, П3 — прочие страны (правило рабочей таблицы)",
         "parts": parts}, ensure_ascii=False), encoding="utf-8")

    withmail = sum(1 for v in sup.values() if v["email"])
    withph = sum(1 for v in sup.values() if v["phone"])
    withany = sum(1 for v in sup.values() if v["email"] or v["phone"])
    print(f"поставщиков в реестре : {len(sup)}")
    print(f"  с email             : {withmail}")
    print(f"  с телефоном         : {withph}")
    print(f"  хотя бы один контакт: {withany}")
    print(f"деталей с привязкой   : {len(parts)}")
    from collections import Counter
    c = Counter(b for p in parts.values() for b in p)
    print(f"  по группам          : {dict(c)}")


if __name__ == "__main__":
    main()
