#!/usr/bin/env python3
"""Досье машины Caterpillar R1700G (ПДМ) → zip/data/r1700.json.

Цепочка портала требует по машине всё звеньями: машина → узел → запчасть →
изготовитель → исполнитель. По R1700G у нас было только 87 наименований из
раздела «ЗИП ГШО» (pnw/data/item_master.json) без парт-номеров отдельным полем,
без документации, без аналогов и без каналов закупки. Этот сборщик соединяет:

  1. разведку — zip/data/r1700_recon/*.json (14 направлений, каждое проверено
     вторым проходом: в строках стоит verdict и уровень доверия);
  2. свои позиции — pnw/data/item_master.json, раздел «ЗИП ГШО», machine=R1700G
     (из наименований вынимаются парт-номера Caterpillar и связываются с разведкой);
  3. таможню — zip/customs/out/customs_*.json и zip/data/customs_declarations.json:
     кто реально ввозит запчасти Caterpillar, чем и по каким маршрутам;
  4. справочник ODM — zip/data/odm_suppliers.json: китайские заводы с упоминанием
     Caterpillar как кандидаты на неоригинал.

Ничего не придумывает: строка без источника в разведку не попадает, а снятые
проверяющим строки сохраняются с verdict=«снят» — след ошибки дороже её стирания.

Выход: zip/data/r1700.json  (схема kvant.r1700/1)
Запуск: python zip/tools/r1700_dossier.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DATA = ROOT / "data"
RECON = DATA / "r1700_recon"
OUT = DATA / "r1700.json"

MACHINE_KEY = "R1700G"

# Парт-номер Caterpillar: 423-8524, 1R-1808, 4W-7301, 8T-4121, 9X-8256.
PN_RE = re.compile(r"\b(\d{3}-\d{4}|\d[A-ZА-Я]-\d{3,4})\b")

# Узлы досье. Порядок — как в машине: от двигателя к рабочему оборудованию.
NODES = [
    "01 Двигатель", "02 Топливная система", "03 Охлаждение", "04 Воздух/выпуск",
    "05 Гидравлика", "06 Фильтры и жидкости", "07 Трансмиссия/гидротрансформатор",
    "08 Мосты/карданы/тормоза", "09 Рулевое управление", "10 Электрика/датчики/освещение",
    "11 Кабина/органы управления/безопасность", "12 Рама/шарнир/стрела/ковш",
    "13 Колёса и шины", "14 Пожаротушение/смазка/прочее",
]

PART_SLICES = ["filters", "engine", "drive", "hyd", "get", "elec", "wheels"]
ORG_SLICES = ["dealers", "aftermarket", "traders"]

# Куда сваливаются узлы наших 87 позиций (справочник ЗИП ГШО) — в узлы досье.
OWN_NODE_MAP = {
    "06 Фильтры": "06 Фильтры и жидкости",
    "05 Гидравлика": "05 Гидравлика",
    "10 Электрика/датчики": "10 Электрика/датчики/освещение",
    "08 Трансмиссия/мосты/тормоза": "08 Мосты/карданы/тормоза",
    "07 Двигатель": "01 Двигатель",
    "03 РТИ/уплотнения (общие)": "05 Гидравлика",
    "12 Прочее": "14 Пожаротушение/смазка/прочее",
}


def norm_pn(pn: str) -> str:
    """Нормализация парт-номера для сверки: 1R-1808, 1R1808 и 1r 1808 — одно и то же."""
    return re.sub(r"[^0-9A-Z]", "", str(pn or "").upper())


def pretty_pn(pn: str) -> str:
    """Каталожное написание номера Caterpillar: 4238524 → 423-8524, 1R1808 → 1R-1808.

    В нашем справочнике номер местами лежит без дефиса (catalog_no = «5772006»),
    а в каталоге Caterpillar, в прайсах дилеров и в заявках он всегда с дефисом.
    Номер, не похожий на схему Caterpillar (ALN0681, MK-CAT-1528), не трогаем.
    """
    s = str(pn or "").strip().upper()
    if re.fullmatch(r"\d{7}", s):
        return f"{s[:3]}-{s[3:]}"
    m = re.fullmatch(r"(\d[A-Z])(\d{4})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return str(pn or "").strip()


def load(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        print(f"  ! {path.name}: не JSON ({ex}) — пропущен")
        return default


def recon_slices() -> dict:
    """Разведка по направлениям. Отсутствующий файл — не ошибка, а пустое звено."""
    out = {}
    if not RECON.is_dir():
        print(f"  ! нет каталога {RECON.relative_to(REPO)} — досье собирается только на своих данных")
        return out
    for p in sorted(RECON.glob("*.json")):
        d = load(p)
        if isinstance(d, dict):
            out[p.stem] = d
            rows = len(d.get("rows") or [])
            extra = " + ".join(f"{k} {len(v)}" for k, v in d.items()
                               if isinstance(v, list) and k != "rows" and v)
            print(f"  · {p.stem}: строк {rows}{' (' + extra + ')' if extra else ''}")
    return out


def merge_parts(slices: dict) -> tuple[list, list]:
    """Детали из всех узловых направлений в один перечень без дублей по парт-номеру.

    Одна и та же деталь приходит из двух направлений (фильтр гидравлики — и в
    «фильтрах», и в «гидравлике»). Сводим по нормализованному PN: копим источники
    и аналоги, доверие берём лучшее из встреченных, узел — от первой записи с
    непустым узлом.
    """
    order = {"high": 3, "med": 2, "low": 1, "": 0, None: 0}
    by_pn: dict[str, dict] = {}
    alts: list[dict] = []
    for key in PART_SLICES:
        d = slices.get(key) or {}
        for r in d.get("rows") or []:
            pn = pretty_pn(r.get("pn"))
            k = norm_pn(pn)
            if not k:
                continue
            cur = by_pn.get(k)
            if cur is None:
                cur = {
                    "pn": pn, "pn_norm": k,
                    "name_ru": str(r.get("name_ru") or "").strip(),
                    "name_en": str(r.get("name_en") or "").strip(),
                    "node": str(r.get("node") or "").strip(),
                    "applic": str(r.get("applic") or "").strip(),
                    "qty": str(r.get("qty") or "").strip(),
                    "interval": str(r.get("interval") or "").strip(),
                    "price_usd": str(r.get("price_usd") or "").strip(),
                    "confidence": (r.get("confidence") or "").strip() or "low",
                    "verdict": (r.get("verdict") or "").strip(),
                    "slices": [key],
                    "sources": [],
                    "alts": [],
                    "kv": [],
                    "note": str(r.get("note") or "").strip(),
                }
                by_pn[k] = cur
            else:
                if key not in cur["slices"]:
                    cur["slices"].append(key)
                for f in ("name_ru", "name_en", "node", "applic", "qty", "interval", "price_usd", "note"):
                    if not cur.get(f) and r.get(f):
                        cur[f] = str(r[f]).strip()
                if order.get(r.get("confidence")) > order.get(cur["confidence"]):
                    cur["confidence"] = r["confidence"]
                # вердикт «снят» не должен затирать «подтверждён» из другого прохода
                if r.get("verdict") and (not cur["verdict"] or cur["verdict"] == "снят"):
                    cur["verdict"] = r["verdict"]
            src = str(r.get("source") or "").strip()
            if src and src not in cur["sources"]:
                cur["sources"].append(src)
            for a in r.get("alts") or []:
                brand = str(a.get("brand") or "").strip()
                apn = pretty_pn(a.get("pn"))
                if not brand or not apn:
                    continue
                pair = {"brand": brand, "pn": apn, "kind": str(a.get("kind") or "аналог").strip(),
                        "note": str(a.get("note") or "").strip()}
                if pair not in cur["alts"]:
                    cur["alts"].append(pair)
                    alts.append({"pn": pn, "pn_norm": k, "brand": brand, "alt_pn": apn,
                                 "alt_pn_norm": norm_pn(apn), "kind": pair["kind"], "note": pair["note"]})
    parts = sorted(by_pn.values(), key=lambda x: (NODES.index(x["node"]) if x["node"] in NODES else 99, x["pn"]))
    return parts, alts


def own_positions() -> list:
    """Наши 87 позиций по R1700G — из трёх своих наборов, сведённые по id позиции.

    zip/data/positions.json — парт-номер (catalog_no), узел, ценовая вилка, статус
    Битрикса и применимость; pnw/data/item_master.json — наш внутренний номер KV
    (ключ связи src_id == positions.id); pnw/data/crossrefs.json — разобранные из
    поля aliases кроссы на вторичный рынок (167 строк по 52 позициям).
    """
    pos = load(DATA / "positions.json", []) or []
    im = load(REPO / "pnw" / "data" / "item_master.json", {}) or {}
    cr = load(REPO / "pnw" / "data" / "crossrefs.json", {}) or {}

    kv_by_src = {i.get("src_id"): i.get("kv") for i in (im.get("items") or []) if i.get("src_id")}
    xr = defaultdict(list)
    for r in cr.get("rows") or []:
        xr[r.get("position_id")].append(r)

    rows = []
    for p in pos:
        blob = f"{p.get('model', '')} {p.get('applications', '')}"
        if "R1700" not in blob.upper():
            continue
        pn = pretty_pn(p.get("catalog_no"))
        pns = [pn] if pn else []
        pns += [pretty_pn(m.group(1)) for m in PN_RE.finditer(f"{p.get('name', '')} {p.get('note', '')}")]
        rows.append({
            "position_id": p.get("id"), "pp": p.get("pp"), "kv": kv_by_src.get(p.get("id")),
            "pn": pn, "pns": sorted({x for x in pns if x}),
            "name": p.get("name"), "node": p.get("category"),
            "node_dossier": OWN_NODE_MAP.get(p.get("category") or "", "14 Пожаротушение/смазка/прочее"),
            "machine": p.get("model"), "applications": p.get("applications"),
            "material": p.get("material_type"), "hs": p.get("hs_code"), "note": p.get("note"),
            "qty_quarter": p.get("qty_quarter"),
            "price_min": p.get("price_min"), "price_max": p.get("price_max"),
            "price_cur": p.get("price_cur"), "price_src": p.get("price_src"),
            "bitrix_status": p.get("bitrix_status"),
            "opendb_signal": p.get("opendb_signal"),
            "crossrefs": [{"number": x.get("number"), "brand": x.get("brand"), "kind": x.get("kind")}
                          for x in xr.get(p.get("id"), [])],
        })
    return rows


def own_customs() -> dict:
    """Кто реально ввозит запчасти Caterpillar: строки, импортёры, отправители, маршруты.

    Два источника: платная выгрузка zip/customs/out (поля дружелюбные, суммы
    частично замаскированы источником) и агрегат zip/data/customs_declarations.json.
    В досье уходят только юридические лица, маршрут и род груза — правило 17
    CLAUDE.md про публичный репозиторий.
    """
    pat = re.compile(r"CATERPILLAR|\bCAT\b|R1700|ПОГРУЗОЧНО-ДОСТАВОЧН", re.I)
    rows = []
    for p in sorted((ROOT / "customs" / "out").glob("customs_*.json")):
        d = load(p, {})
        for r in d.get("matched") or []:
            if not isinstance(r, dict):
                continue
            blob = " ".join(str(r.get(k, "")) for k in ("brand", "exporter", "desc", "pn"))
            if not pat.search(blob):
                continue
            # ИНН и вес из выгрузки не переносим: для закупки они ничего не добавляют
            # (организация опознаётся по названию), а досье лежит в публичном репозитории.
            rows.append({
                "date": r.get("date"), "importer": r.get("importer"),
                "exporter": r.get("exporter"), "origin": r.get("origin"), "dispatch": r.get("dispatch"),
                "incoterms": r.get("incoterms"), "place": r.get("place"), "hs10": r.get("hs10"),
                "pn": r.get("pn"), "desc": (r.get("desc") or "")[:300], "usd_kg": r.get("usd_kg"),
                "src": "выгрузка ГлобусВЭД (zip/customs/out)",
            })
    decl = load(DATA / "customs_declarations.json", {})
    cols = decl.get("cols") or []
    for raw in decl.get("rows") or []:
        r = dict(zip(cols, raw))
        blob = f"{r.get('exporter', '')} {r.get('desc', '')} {r.get('pn', '')}"
        if not pat.search(blob):
            continue
        rows.append({
            "date": r.get("year"), "importer": r.get("importer"),
            "exporter": r.get("exporter"), "origin": r.get("origin"), "dispatch": "",
            "incoterms": "", "place": "", "hs10": r.get("hs"), "pn": r.get("pn"),
            "desc": (r.get("desc") or "")[:300], "usd_kg": r.get("usd_kg"),
            "src": "агрегат zip/data/customs_declarations.json",
        })
    # Названия организаций в двух источниках написаны по-разному: «ООО "СК-КОМПЛЕКТ"»
    # и «ООО"СК-КОМПЛЕКТ». Без сведения один и тот же импортёр попадает в список дважды
    # и завышает счёт каналов — считаем по ключу без кавычек и лишних пробелов.
    def org_key(v: str) -> str:
        return re.sub(r"[\"'«»\s.]+", " ", str(v or "").upper()).strip()

    def fold(vals) -> Counter:
        best: dict[str, str] = {}
        cnt: Counter = Counter()
        for v in vals:
            k = org_key(v)
            if not k:
                continue
            cnt[k] += 1
            # предпочитаем написание с кавычками — оно читается как в выписке
            if k not in best or len(str(v)) > len(best[k]):
                best[k] = str(v).strip()
        return Counter({best[k]: n for k, n in cnt.items()})

    imp = fold(r.get("importer") for r in rows)
    exp = fold(r.get("exporter") for r in rows)
    lanes = Counter((r.get("origin") or "?", r.get("incoterms") or "?", r.get("place") or "?")
                    for r in rows)
    r17 = [r for r in rows if "R1700" in f"{r.get('pn', '')} {r.get('desc', '')}".upper()]
    return {
        "rows": rows,
        "importers": [{"org": k, "shipments": v} for k, v in imp.most_common()],
        "exporters": [{"org": k, "shipments": v} for k, v in exp.most_common()],
        "lanes": [{"origin": a, "incoterms": b, "place": c, "shipments": n} for (a, b, c), n in lanes.most_common()],
        "r1700_rows": r17,
        "note": "Только юридические лица, маршрут и род груза. Суммы в источнике частично замаскированы.",
    }


def own_odm() -> list:
    """Китайские заводы из нашего справочника ODM, у которых в профиле есть Caterpillar."""
    odm = load(DATA / "odm_suppliers.json", []) or []
    pat = re.compile(r"caterpillar|\bcat\b", re.I)
    out = []
    for o in odm:
        blob = f"{o.get('name', '')} {o.get('makes', '')} {o.get('comment', '')}"
        if not pat.search(blob):
            continue
        out.append({
            "org": o.get("name"), "region": o.get("region"), "country": o.get("country"),
            "makes": o.get("makes"), "site": o.get("source_url") or o.get("catalog_url"),
            "catalog_url": o.get("catalog_url"), "terms": o.get("terms"),
            "confidence": o.get("confidence"), "status": o.get("status"),
            "src": "zip/data/odm_suppliers.json",
        })
    out.sort(key=lambda x: {"high": 0, "med": 1, "low": 2}.get(x.get("confidence"), 3))
    return out


def link_own_to_parts(parts: list, alts: list, own: list) -> tuple[int, int]:
    """Слить свои позиции с деталями разведки.

    Наши 87 позиций — не «дополнение» к разведке, а её проверка: парт-номер пришёл
    из перечня заказчика, то есть применимость к R1700G подтверждена не веб-страницей,
    а заявкой. Поэтому позиция, которой в разведке нет, добавляется в перечень
    самостоятельной строкой, а не теряется. Возвращает (связок, добавлено строк).
    """
    idx = {p["pn_norm"]: p for p in parts}
    linked = added = 0
    for o in own:
        target = None
        for pn in o["pns"]:
            target = idx.get(norm_pn(pn))
            if target is not None:
                break
        if target is None:
            if not o["pn"]:
                continue
            target = {
                "pn": o["pn"], "pn_norm": norm_pn(o["pn"]),
                "name_ru": o["name"], "name_en": "", "node": o["node_dossier"],
                "applic": o.get("applications") or o.get("machine") or MACHINE_KEY,
                "qty": "", "interval": "",
                "price_usd": "", "confidence": "high", "verdict": "наша база",
                "slices": ["own"], "sources": ["zip/data/positions.json — перечень ЗИП ГШО"],
                "alts": [], "kv": [], "note": o.get("note") or "",
            }
            parts.append(target)
            idx[target["pn_norm"]] = target
            added += 1
        # наши атрибуты сильнее выведенных из сети
        target["position_id"] = o["position_id"]
        target["pp"] = o["pp"]
        target["own_node"] = o["node"]
        target["price_eur_min"] = o["price_min"]
        target["price_eur_max"] = o["price_max"]
        target["price_src"] = o["price_src"]
        target["qty_quarter"] = o["qty_quarter"]
        target["bitrix_status"] = o["bitrix_status"]
        target["opendb_signal"] = o["opendb_signal"]
        if o.get("applications") and not target.get("applic"):
            target["applic"] = o["applications"]
        if o["kv"] and o["kv"] not in target["kv"]:
            target["kv"].append(o["kv"])
            linked += 1
        # кроссы из нашего разбора aliases — такие же аналоги, только проверенные нами
        for x in o["crossrefs"]:
            brand = (x.get("brand") or "").strip() or "не определён"
            apn = pretty_pn(x.get("number"))
            if not apn:
                continue
            pair = {"brand": brand, "pn": apn,
                    "kind": {"analog": "аналог", "oem": "оригинал", "unknown": "номер без бренда"}
                    .get(x.get("kind"), "аналог"),
                    "note": "из нашего разбора aliases (pnw/data/crossrefs.json)"}
            if pair not in target["alts"]:
                target["alts"].append(pair)
                alts.append({"pn": target["pn"], "pn_norm": target["pn_norm"], "brand": brand,
                             "alt_pn": apn, "alt_pn_norm": norm_pn(apn), "kind": pair["kind"],
                             "note": pair["note"]})
    parts.sort(key=lambda x: (NODES.index(x["node"]) if x["node"] in NODES else 99, x["pn"]))
    return linked, added


def bitrix_facts(parts: list) -> dict:
    """Факты сделок из zip/data/r1700_bitrix.json и привязка их к деталям по номеру."""
    b = load(DATA / "r1700_bitrix.json", {}) or {}
    idx = {p["pn_norm"]: p for p in parts}
    tied = 0
    for r in (b.get("sold") or []) + (b.get("quoted") or []):
        p = idx.get(norm_pn(r.get("pn")))
        if p is None:
            continue
        p["bitrix_status"] = r.get("status")
        p["bitrix_deals"] = r.get("deals") or []
        tied += 1
    for r in b.get("prices") or []:
        p = idx.get(norm_pn(r.get("pn")))
        if p is None or not r.get("unit_price"):
            continue
        p.setdefault("price_facts", []).append({
            "price": r["unit_price"], "cur": r.get("currency"), "seller": r.get("seller"),
            "source": r.get("source"), "url": r.get("url"), "year": r.get("year"),
        })
    b["tied_to_parts"] = tied
    return b


def orgs(slices: dict) -> list:
    out = []
    for key in ORG_SLICES:
        for r in (slices.get(key) or {}).get("rows") or []:
            if not str(r.get("org") or "").strip():
                continue
            out.append({
                "org": str(r.get("org")).strip(), "kind": r.get("kind") or "", "country": r.get("country") or "",
                "city": r.get("city") or "", "role": r.get("role") or "", "brands": r.get("brands") or "",
                "site": r.get("site") or "", "email": r.get("email") or "", "phone": r.get("phone") or "",
                "stock": r.get("stock") or "", "note": r.get("note") or "", "source": r.get("source") or "",
                "confidence": r.get("confidence") or "low", "verdict": r.get("verdict") or "", "slice": key,
            })
    return out


def playbook(parts: list, docs: list, org_rows: list, customs: dict, bitrix: dict, prices: list) -> list:
    """Прикладные шаги, посчитанные из данных досье, а не выдуманные.

    Владельцу нужен не отчёт, а порядок действий. Каждый шаг — от конкретной цифры:
    сколько позиций без аналога, у кого канал уже работает, что мы уже продавали.
    """
    out = []
    no_alt = [p for p in parts if not p["alts"]]
    no_price = [p for p in parts if not p["price_usd"] and not p.get("price_eur_min")]
    dropped = [p for p in parts if p["verdict"] == "снят"]
    sold = [p for p in parts if p.get("bitrix_status") == "продавали"]
    quoted = [p for p in parts if p.get("bitrix_status") == "квотировали"]
    cat_forms = [d for d in docs if "каталог" in (d.get("kind") or "").lower()]
    dealers = [o for o in org_rows if o["slice"] == "dealers"]
    traders = [o for o in org_rows if o["slice"] == "traders"]
    makers = [o for o in org_rows if o["slice"] == "aftermarket"]
    imps = customs.get("importers") or []
    bs = (bitrix.get("stats") or {})

    if cat_forms:
        forms = ", ".join(d["form"] for d in cat_forms[:4])
        out.append({
            "step": f"Взять каталог запчастей по серийному префиксу машины ({forms})",
            "why": f"Ведомость на машину собирается только из каталога: у нас {len(parts)} деталей, "
                   f"а в машине их тысячи. Пока каталога нет, любая заявка закрывается частично.",
            "how": "Спросить у заказчика серийный номер машины, по нему выбрать ревизию каталога — "
                   "префиксы у R1700G разные (8XZ, SBR и др.), номера узлов не совпадают.",
        })
    if sold or quoted:
        pns = ", ".join(p["pn"] for p in (sold + quoted)[:6])
        clients = ", ".join((bs.get("clients") or {}).keys()) or "заказчик из сделок"
        out.append({
            "step": f"Идти первыми позициями, которые уже проходили сделку ({pns})",
            "why": f"По {len(sold)} позициям был контракт, по {len(quoted)} — КП. Заказчик ({clients}) "
                   f"уже принимал нашу цену и документы, цикл согласования короче.",
            "how": "Поднять в Битриксе прошлые спецификации и цены, пересчитать на текущий курс и канал.",
        })
    if no_alt:
        out.append({
            "step": f"Закрыть аналогами {len(no_alt)} позиций из {len(parts)}",
            "why": "Позиция без кросса на торгах играется только оригиналом, а это самая высокая цена "
                   "и самый долгий срок. Кросс — это и снижение цены, и доказательство применимости.",
            "how": "Запросить кросс у производителей фильтров и РТИ по номеру Cat, а по литью и "
                   "механике — чертёж или образец на замер.",
        })
    if no_price:
        out.append({
            "step": f"Поставить ценовой ориентир на {len(no_price)} позиций",
            "why": f"Ценовых ориентиров собрано {len(prices)}. Без вилки «оригинал / аналог / эконом» "
                   f"нельзя ни назвать цену, ни понять, чем берёт конкурент.",
            "how": "Запрос по трём каналам одновременно: дилер СНГ, независимый склад, завод-изготовитель.",
        })
    if imps:
        top = ", ".join(x["org"] for x in imps[:5])
        out.append({
            "step": f"Проверить действующие каналы ввоза: {top}",
            "why": f"По таможне {len(customs.get('rows') or [])} отгрузок Caterpillar, импортёров — "
                   f"{len(imps)}. У этих компаний канал уже работает: это и конкуренты, и возможные партнёры.",
            "how": "Сверить их ИНН и профиль, запросить условия перепродажи, сравнить цену их канала "
                   "со своей ставкой по маршруту.",
        })
    if dealers:
        out.append({
            "step": f"Задать дилерам ({len(dealers)}) один вопрос до цены — отгружаете ли в РФ",
            "why": "Прайс без готовности отгружать не стоит ничего, а переписка о ценах занимает недели.",
            "how": "Письмо на английском одним абзацем: модель, серийный номер, перечень, вопрос об отгрузке.",
        })
    if makers:
        out.append({
            "step": f"Запустить пробники у {len(makers)} заводов неоригинала",
            "why": "Без образца и протокола испытаний неоригинал на торгах не проходит техническую часть.",
            "how": "По одной позиции на завод, с приёмкой по нашей карте замеров — как сделано по перфораторам.",
        })
    if traders:
        out.append({
            "step": f"Разослать РФ-поставщикам ({len(traders)}) запрос наличия по ходовым позициям",
            "why": "Наличие на складе в РФ снимает срок и таможню — это единственный способ выиграть "
                   "закупку с коротким сроком поставки.",
            "how": "Смарт-процесс «Запросы поставщикам» Битрикса, письмо с перечнем и сроком ответа 3 дня.",
        })
    if dropped:
        out.append({
            "step": f"Не использовать {len(dropped)} номеров со вердиктом «снят»",
            "why": "Их нашла разведка, но проверка не подтвердила источником. В заявке такой номер — "
                   "это отклонение по технической части и удар по репутации.",
            "how": "Оставлены в перечне со следом ошибки. Перед торгами фильтруй перечень по вердикту.",
        })
    return out


def build() -> dict:
    print("Досье R1700G:")
    sl = recon_slices()
    parts, alts = merge_parts(sl)
    own = own_positions()
    linked, added = link_own_to_parts(parts, alts, own)
    bitrix = bitrix_facts(parts)
    customs = own_customs()
    odm = own_odm()
    org_rows = orgs(sl)

    spec = sl.get("spec") or {}
    docs = [r for r in (sl.get("docs") or {}).get("rows") or [] if str(r.get("form") or "").strip()]
    prices = (sl.get("prices") or {}).get("rows") or []
    tnd = sl.get("tenders") or {}

    # Покрытие: без него непонятно, чем ещё нельзя торговать.
    by_node = Counter(p["node"] or "— не определён" for p in parts)
    with_alt = sum(1 for p in parts if p["alts"])
    with_price = sum(1 for p in parts if p["price_usd"] or p.get("price_eur_min") or p.get("price_facts"))
    with_kv = sum(1 for p in parts if p["kv"])
    verdicts = Counter(p["verdict"] or "не проверялся" for p in parts)
    conf = Counter(p["confidence"] for p in parts)

    dossier = {
        "updated": date.today().isoformat(),
        "schema": "kvant.r1700/1",
        "built_by": "zip/tools/r1700_dossier.py",
        "machine": {
            "key": MACHINE_KEY,
            "name": "Caterpillar R1700G",
            "brand": "Caterpillar",
            "kind": "погрузочно-доставочная машина (ПДМ / LHD) для подземных работ",
            "family": "R1700G · R1700 (2019+) · R1700 XE (аккумуляторная) · R1600G/H · R1300G",
            "why": "Машина из нашего реестра ГШО: 87 позиций в справочнике «ЗИП ГШО», "
                   "подтверждённый ввоз в РФ по таможне, эксплуатанты в РФ есть. "
                   "Досье собрано, чтобы выходить на торги по комплектующим с номерами, "
                   "аналогами, каналами и ценами, а не с одним наименованием.",
        },
        "nodes": NODES,
        "specs": spec.get("rows") or [],
        "variants": spec.get("variants") or [],
        "node_tree": spec.get("nodes") or [],
        "docs": docs,
        "parts": parts,
        "alts": alts,
        "orgs": org_rows,
        "prices": prices,
        "tenders": {
            "platforms": tnd.get("platforms") or [],
            "owners": tnd.get("owners") or [],
            "checklist": tnd.get("checklist") or [],
            "rows": tnd.get("rows") or [],
        },
        "own": {
            "known_parts": own,
            "customs": customs,
            "odm": odm,
            "bitrix": bitrix,
        },
        "playbook": playbook(parts, docs, org_rows, customs, bitrix, prices),
        "gaps": {k: (v.get("gaps") or "") for k, v in sl.items() if v.get("gaps")},
        "stats": {
            "recon_slices": len(sl),
            "parts": len(parts),
            "parts_by_node": dict(by_node),
            "parts_with_alt": with_alt,
            "parts_with_price": with_price,
            "parts_linked_to_kv": with_kv,
            "kv_links": linked,
            "parts_from_own_only": added,
            "parts_with_bitrix": sum(1 for p in parts if p.get("bitrix_status") in ("продавали", "квотировали")),
            "bitrix_live": bool(bitrix.get("live")),
            "bitrix_sold": (bitrix.get("stats") or {}).get("sold", 0),
            "bitrix_quoted": (bitrix.get("stats") or {}).get("quoted", 0),
            "bitrix_price_facts": (bitrix.get("stats") or {}).get("prices", 0),
            "alts": len(alts),
            "alt_brands": len({a["brand"] for a in alts}),
            "docs": len(docs),
            "specs": len(spec.get("rows") or []),
            "variants": len(spec.get("variants") or []),
            "orgs": len(org_rows),
            "orgs_by_kind": dict(Counter(o["kind"] for o in org_rows)),
            "orgs_by_country": dict(Counter(o["country"] for o in org_rows)),
            "prices": len(prices),
            "own_positions": len(own),
            "customs_rows": len(customs["rows"]),
            "customs_importers": len(customs["importers"]),
            "customs_exporters": len(customs["exporters"]),
            "odm_candidates": len(odm),
            "confidence": dict(conf),
            "verdicts": dict(verdicts),
            "sources": len({s for p in parts for s in p["sources"]}
                           | {r.get("source") for r in docs if r.get("source")}
                           | {o["source"] for o in org_rows if o["source"]}),
        },
    }
    return dossier


def main() -> int:
    d = build()
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    s = d["stats"]
    print(f"\n{OUT.relative_to(REPO)}: {OUT.stat().st_size:,} байт")
    print(f"  деталей {s['parts']} (с аналогом {s['parts_with_alt']}, с ценой {s['parts_with_price']}, "
          f"связано с нашими KV {s['parts_linked_to_kv']})")
    print(f"  аналогов {s['alts']} по {s['alt_brands']} брендам · документов {s['docs']} · "
          f"организаций {s['orgs']} · цен {s['prices']}")
    print(f"  таможня: строк {s['customs_rows']}, импортёров {s['customs_importers']}, "
          f"отправителей {s['customs_exporters']} · ODM-кандидатов {s['odm_candidates']}")
    print(f"  источников всего {s['sources']} · вердикты: {s['verdicts']}")
    if not d["parts"]:
        print("  ! перечень деталей пуст: нет zip/data/r1700_recon/*.json — заливай разведку")
    return 0


if __name__ == "__main__":
    sys.exit(main())
