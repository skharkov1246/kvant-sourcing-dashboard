#!/usr/bin/env python3
"""Удаление российских компаний из всех списков поставщиков.

Распоряжение владельца (17.08.2026): «русских всех убрать вообще отовсюду, это конкуренты».
Российские компании конкурируют с КВАНТ как сорсеры/поставщики ЗИП, поэтому им не пишут
запросы и они не должны показываться сорсерам в подборе.

Что считается российской компанией (строгий критерий, чтобы не задеть соседей):
  · страна начинается с «Россия / РФ / Russia»;
  · русская орг-форма в начале названия (ООО, ОАО, ЗАО, ПАО, АО, РУП, ФГУП, НПО, УК);
  · домен сайта в зоне .ru / .рф.
Явно НЕ трогаем Казахстан, Узбекистан, Беларусь и прочих соседей — это не «русские»
в смысле распоряжения, а самостоятельные рынки со своими каналами.

Что НЕ чистится:
  · CRM-снапшот (bitrix_gt.json, bitrix_history.json) — это факт наших сделок и запросов,
    его переписывать нельзя; от появления карточек защищает фильтр в сборке (gt/build.py
    и templates), см. RU_GUARD;
  · таможенная первичка zip/customs — там российские компании идут импортёрами
    (покупателями), это данные о рынке, а не адресаты запросов;
  · тексты фактов в разборах моделей — «парк стоит на ТТЭЦ-1» это факт о рынке.

Удалённое складывается в gt/data/removed_ru.json — аудит-след, чтобы решение можно было
пересмотреть без раскопок в истории git.

Запуск: python3 gt/tools/drop_ru.py [--dry]
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RU_COUNTRY = re.compile(r"^\s*(Россия|РФ|Russia|Russian)", re.I)
RU_ORG = re.compile(r"(^|[\s(])(ООО|ОАО|ЗАО|ПАО|АО|РУП|ФГУП|НПО|УК)\s*[«\"„]?")
RU_DOM = re.compile(r"(^|[/.])([a-z0-9\-]+\.)?(ru|рф)(/|$|\b)", re.I)
NEIGHBOURS = re.compile(
    r"Казахстан|Узбекистан|Беларус|Belarus|Kazakh|Uzbek|Азербайджан|Армени|Киргиз|Туркмен|Грузи", re.I
)

LIST_FILES = [
    "data/suppliers.json", "data/heavy_suppliers.json", "data/tfs_subsuppliers.json",
    "data/research_suppliers.json", "data/sgt400_checklist.json", "data/rfq_suppliers.json",
    "data/solar.json", "data/lm6000.json", "data/v643a.json", "data/ms6001b.json",
    "data/sgt4000f.json",
]
STOCK_FILES = ["data/solar.json", "data/lm6000.json", "data/v643a.json",
               "data/ms6001b.json", "data/sgt4000f.json", "data/hot_parts.json"]


def domain(site):
    s = re.sub(r"^https?://", "", (site or "").strip().lower())
    return s.split("/")[0]


def is_ru(name="", country="", site="", extra=""):
    if NEIGHBOURS.search(f"{name} {country} {extra}"):
        return False
    if RU_COUNTRY.search(country or ""):
        return True
    # «ООО «Ромашка»» — да; «AO Smith» — нет (латиница в начале названия)
    if RU_ORG.search(name or "") and not re.match(r"^[A-Za-z]{3,}", (name or "").strip()):
        return True
    if RU_DOM.search(domain(site)):
        return True
    return False


def rows_key(d):
    if isinstance(d, list):
        return None
    for k in ("rows", "companies", "suppliers"):
        if isinstance(d.get(k), list):
            return k
    return None


def main():
    dry = "--dry" in sys.argv
    removed = {"updated": date.today().isoformat(),
               "reason": "распоряжение владельца 17.08.2026: российские компании — конкуренты КВАНТ, "
                         "из списков поставщиков убраны; файл хранится как аудит-след",
               "items": []}
    total = 0

    for rel in LIST_FILES:
        p = ROOT / rel
        d = json.loads(p.read_text(encoding="utf-8"))
        key = rows_key(d)
        rows = d if key is None else d[key]
        keep, drop = [], []
        for r in rows:
            if isinstance(r, dict) and is_ru(r.get("name", ""), r.get("country", ""), r.get("site", ""),
                                             json.dumps(r, ensure_ascii=False)[:300]):
                drop.append(r)
            else:
                keep.append(r)
        if drop:
            for r in drop:
                removed["items"].append({"file": rel, "name": r.get("name", ""),
                                         "country": r.get("country", ""), "site": r.get("site", "")})
            total += len(drop)
            if not dry:
                if key is None:
                    p.write_text(json.dumps(keep, ensure_ascii=False, indent=0), encoding="utf-8")
                else:
                    d[key] = keep
                    p.write_text(json.dumps(d, ensure_ascii=False, indent=0), encoding="utf-8")
            print(f"{rel}: −{len(drop)} (осталось {len(keep)})")

    # досье: поля site нет, зато есть note — ловим и по нему,
    # плюс подчищаем досье тех, кого уже убрали из списков выше
    gone = {re.sub(r"[^a-zа-яё0-9]", "", i["name"].lower()) for i in removed["items"] if i.get("name")}
    p = ROOT / "data/dossiers.json"
    D = json.loads(p.read_text(encoding="utf-8"))
    dd = D["dossiers"]

    def ru_dossier(n, v):
        blob = json.dumps(v, ensure_ascii=False)
        if NEIGHBOURS.search(n + " " + blob[:600]):
            return False
        if re.sub(r"[^a-zа-яё0-9]", "", n.lower()) in gone:
            return True
        if is_ru(n, "", "", blob[:400]):
            return True
        # кириллическое имя + явная привязка к РФ в досье.
        # Кириллицу ищем только в НАЧАЛЕ названия: «Meggitt (Parker Meggitt) — дистрибуция
        # клапанов через VBR» — британцы с русским пояснением, а не российская компания.
        head = n.strip()[:24]
        cyr = bool(re.search(r"[А-Яа-яЁё]{4}", head)) and not re.match(r"^[A-Za-z]{3,}", n.strip())
        return cyr and bool(re.search(r"Росси|Москв|Петербург|РФ\b|\.ru\b", blob[:800], re.I))

    drop = [n for n, v in dd.items() if ru_dossier(n, v)]
    for n in drop:
        removed["items"].append({"file": "data/dossiers.json", "name": n})
        if not dry:
            del dd[n]
    total += len(drop)
    print(f"data/dossiers.json: −{len(drop)} (осталось {len(dd)})")

    # профили сайтов и CRM-справочник сайтов
    p = ROOT / "data/site_profiles.json"
    P = json.loads(p.read_text(encoding="utf-8"))
    prof = P["profiles"]
    drop = [n for n, v in prof.items() if is_ru(n, "", (v or {}).get("site", ""),
                                                json.dumps(v, ensure_ascii=False)[:300])]
    for n in drop:
        removed["items"].append({"file": "data/site_profiles.json", "name": n})
        if not dry:
            del prof[n]
    total += len(drop)
    if not dry:
        p.write_text(json.dumps(P, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"data/site_profiles.json: −{len(drop)} (осталось {len(prof)})")

    p = ROOT / "data/bitrix_supplier_sites.json"
    B = json.loads(p.read_text(encoding="utf-8"))
    for sect in ("confirmed", "not_found"):
        v = B.get(sect)
        if isinstance(v, dict):
            drop = [n for n in v if is_ru(n, "", (v[n] or {}).get("site", "") if isinstance(v[n], dict) else "",
                                          json.dumps(v[n], ensure_ascii=False)[:300])]
            for n in drop:
                removed["items"].append({"file": f"data/bitrix_supplier_sites.json:{sect}", "name": n})
                if not dry:
                    del v[n]
        elif isinstance(v, list):
            keep, drop = [], []
            for x in v:
                n = x if isinstance(x, str) else x.get("name", "")
                (drop if is_ru(n) else keep).append(x)
            for x in drop:
                removed["items"].append({"file": f"data/bitrix_supplier_sites.json:{sect}",
                                         "name": x if isinstance(x, str) else x.get("name", "")})
            if not dry:
                B[sect] = keep
        else:
            continue
        total += len(drop)
        print(f"data/bitrix_supplier_sites.json:{sect}: −{len(drop)}")
    if not dry:
        p.write_text(json.dumps(B, ensure_ascii=False, indent=0), encoding="utf-8")

    # складские лоты у российских продавцов
    for rel in STOCK_FILES:
        p = ROOT / rel
        d = json.loads(p.read_text(encoding="utf-8"))
        st = d.get("stock")
        if isinstance(st, list):
            keep = []
            for s in st:
                if isinstance(s, dict) and is_ru(s.get("seller", ""), s.get("country", ""), s.get("url", "")):
                    removed["items"].append({"file": rel + ":stock", "name": s.get("seller", ""),
                                             "what": (s.get("what") or "")[:120]})
                    total += 1
                else:
                    keep.append(s)
            if len(keep) != len(st):
                if not dry:
                    d["stock"] = keep
                    p.write_text(json.dumps(d, ensure_ascii=False, indent=0), encoding="utf-8")
                print(f"{rel} (склад): −{len(st) - len(keep)}")
        elif isinstance(st, dict):  # hot_parts: {позиция: {finds: [...]}}
            n = 0
            for pos in st.values():
                finds = pos.get("finds") or []
                keep = []
                for s in finds:
                    if is_ru(s.get("seller", ""), s.get("country", ""), s.get("url", "")):
                        removed["items"].append({"file": rel + ":stock", "name": s.get("seller", ""),
                                                 "what": (s.get("what") or "")[:120]})
                        n += 1
                    else:
                        keep.append(s)
                pos["finds"] = keep
            if n:
                total += n
                if not dry:
                    p.write_text(json.dumps(d, ensure_ascii=False, indent=0), encoding="utf-8")
                print(f"{rel} (склад): −{n}")

    if not dry:
        (ROOT / "data/dossiers.json").write_text(json.dumps(D, ensure_ascii=False, indent=0), encoding="utf-8")
        out = ROOT / "data/removed_ru.json"
        prev = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"items": []}
        seen = {(i.get("file"), i.get("name")) for i in prev.get("items", [])}
        removed["items"] = prev.get("items", []) + [i for i in removed["items"]
                                                    if (i.get("file"), i.get("name")) not in seen]
        out.write_text(json.dumps(removed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nИТОГО удалено записей: {total}"
          f"{' (сухой прогон, файлы не тронуты)' if dry else ' → аудит в gt/data/removed_ru.json'}")


if __name__ == "__main__":
    main()
