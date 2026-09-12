#!/usr/bin/env python3
"""Сводит проверки наличия по заявке ЛУКОЙЛ в один датасет gt/data/ship_lukoil.json.

Источники (все закоммичены, файл воспроизводим из репозитория):
  gt/data/rfq_demand.json      — сама заявка, оба листа: что и сколько нужно
  gt/data/rfq_prices.json      — наши ценовые вилки и ранняя проверка 08.2026 (checks)
  gt/data/ship_energoseti.json — проверка 505 строк «Энергосетей» 09.2026
  gt/data/ship_sweep.json      — сплошная проверка остатка 863 строк 09.2026

Позднейшая проверка перекрывает раннюю. Строки заявки без проверки попадают в
датасет с вердиктом not_checked — так видно реальное покрытие, а не подогнанное.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEMAND = ROOT / "gt/data/rfq_demand.json"
PRICES = ROOT / "gt/data/rfq_prices.json"
SHIP = ROOT / "gt/data/ship_energoseti.json"
SWEEP = ROOT / "gt/data/ship_sweep.json"
SELLERS = ROOT / "gt/data/ship_sellers.json"
DST = ROOT / "gt/data/ship_lukoil.json"

# наши базы контактов: путь -> ключ коллекции (None = файл сам массив)
CONTACT_BASES = [
    ("gt/data/dossiers.json", "dossiers"),
    ("gt/data/suppliers.json", None),
    ("gt/data/rfq_suppliers.json", "rows"),
    ("gt/data/research_suppliers.json", "rows"),
    ("gt/data/heavy_suppliers.json", "rows"),
    ("gt/data/tfs_subsuppliers.json", "rows"),
]
ORG_TAIL = re.compile(
    r"[,.]?\s*(LLC|L\.L\.C|Ltd\.?|Inc\.?|GmbH|B\.?V\.?|S\.?r\.?l\.?|S\.?p\.?A\.?|S\.?A\.?S\.?|"
    r"Co\.?|Corp\.?|AG|Limited|Company|Pvt\.?|ООО|АО|ЗАО)\b\.?", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
PHONE = re.compile(r"\+\d[\d\-\s()]{7,}\d")


def clean_name(name: str) -> str:
    """Имя компании без приписок агента.

    Агенты писали продавца свободной формой: «WTE PowerBolt s.r.o. (CZ) — серийно
    делает крепёж под турбины», «Shenzhen Blaze Turbine Co., Ltd: https://...».
    Без отсечения хвоста одна компания даёт четыре разные записи, и карта закупки
    врёт по числу адресатов.
    """
    n = re.sub(r"\s+", " ", s(name))
    if re.match(r"^https?://", n):  # агент вписал вместо имени голый адрес — берём домен
        return re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", n)
    n = re.split(r"\s[—–-]\s|:\s|,?\s*https?://|\s\(?(?:сайт|site)\b", n)[0]
    n = re.sub(r"\s*\([^)]*\)", " ", n)       # (CZ), (Чехия), (Дубай) — не часть имени
    return re.sub(r"\s+", " ", n).strip(" .,;·—–-")


def key(name: str) -> str:
    """Имя компании без орг-формы, регистра и пунктуации — для сопоставления."""
    n = ORG_TAIL.sub("", clean_name(name))
    return re.sub(r"[^0-9a-zа-я ]", "", n.lower()).strip()


def contact_index() -> dict:
    """Справочник контактов: нормализованное имя -> e-mail, телефон, сайт.

    Приоритет у gt/data/ship_sellers.json — он собран прицельно под эту заявку.
    Наши турбинные базы дают добор по тем продавцам, что в них уже были.
    """
    idx: dict[str, dict] = {}

    def put(name, emails, phones, site, country="", covers="", note=""):
        k = key(name)
        if not k or len(k) < 3:
            return
        e = idx.setdefault(k, {"name": s(name), "emails": [], "phones": [],
                               "site": "", "country": "", "covers": "", "note": ""})
        for v in emails:
            if v and v not in e["emails"]:
                e["emails"].append(v)
        for v in phones:
            if v and v not in e["phones"]:
                e["phones"].append(v)
        e["site"] = e["site"] or s(site)
        e["country"] = e["country"] or s(country)
        e["covers"] = e["covers"] or s(covers)
        e["note"] = e["note"] or s(note)

    for rel, coll in CONTACT_BASES:
        path = ROOT / rel
        if not path.exists():
            continue
        doc = json.loads(path.read_text())
        rows = doc if coll is None else doc.get(coll, [])
        pairs = rows.items() if isinstance(rows, dict) else (
            (r.get("name") or r.get("company") or "", r) for r in rows if isinstance(r, dict))
        for name, rec in pairs:
            blob = json.dumps(rec, ensure_ascii=False)
            put(name, EMAIL.findall(blob), PHONE.findall(blob),
                rec.get("site") or rec.get("url") or "", rec.get("country") or "",
                rec.get("what") or "", "из нашей базы поставщиков")

    if SELLERS.exists():  # прицельный справочник перекрывает общие базы
        for r in json.loads(SELLERS.read_text()).get("rows", []):
            put(r.get("seller"), r.get("emails") or [], r.get("phones") or [],
                r.get("site"), r.get("country"), r.get("covers"), r.get("note"))
            # сшиваем и по исходному ключу: часть имён была голыми адресами сайтов,
            # после нормализации они схлопнулись в домен и по имени уже не найдутся
            raw = s(r.get("seller_key"))
            if raw and raw not in idx:
                idx[raw] = idx.get(key(r.get("seller")), {})
    return idx

VERDICTS = ("in_stock", "available_lead", "pn_found_no_stock", "oem_only",
            "pn_not_found", "not_checked")

# ранняя проверка 08.2026 писала свой словарь вердиктов — приводим к общему
LEGACY_NOTE = {
    "confirmed": "ранняя проверка 08.2026: карточка подтверждена",
    "price_differs": "ранняя проверка 08.2026: карточка есть, цена расходится с основанием",
    "dead_link": "ранняя проверка 08.2026: ссылка-основание мертва",
    "not_a_seller": "ранняя проверка 08.2026: источник оказался не продавцом",
    "pn_not_found": "ранняя проверка 08.2026: артикул не найден",
}


def s(x) -> str:
    return str(x if x is not None else "").strip()


def num(x):
    """Цена может прийти строкой ('1019.23'), числом или пустотой."""
    if x in (None, "", "-"):
        return None
    try:
        return float(str(x).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def blank(pn: str) -> dict:
    return {
        "pn": pn, "verdict": "not_checked", "seller": "", "seller_url": "",
        "seller_country": "", "kind": "unknown", "in_stock": "unknown", "stock_qty": "",
        "lead_time": "", "price": None, "currency": "USD", "pack_qty": 1,
        "covers_qty": "unknown", "real_maker": "", "real_pn": "", "substitute": "",
        "note": "", "checked_by": "", "sellers": [],
    }


def sellers_list(raw: dict) -> list:
    """Все адресаты по строке: основной продавец плюс альтернативы, без дублей.

    Альтернативы агенты складывали в alt_sellers с разной формой ключей —
    нормализуем к одной, чтобы отчёт печатал 3-4 адресата на позицию.
    """
    out, seen = [], set()

    def push(name, url, country, price, lead, note=""):
        name = s(name)
        if not name:
            return
        k = key(name) or name.lower()
        if k in seen:
            return
        seen.add(k)
        out.append({"seller": clean_name(name) or name, "seller_key": k, "url": s(url),
                    "country": s(country), "price": num(price), "lead_time": s(lead),
                    "note": s(note)})

    push(raw.get("seller"), raw.get("seller_url"), raw.get("seller_country"),
         raw.get("price"), raw.get("lead_time"))
    for a in raw.get("alt_sellers") or []:
        if isinstance(a, dict):
            push(a.get("seller") or a.get("name"), a.get("url") or a.get("seller_url"),
                 a.get("country") or a.get("seller_country"), a.get("price"),
                 a.get("lead_time") or a.get("lead"), a.get("note"))
        elif isinstance(a, str):
            push(a, "", "", None, "")
    return out


def norm(raw: dict, source: str) -> dict:
    """Приводит запись проверки любого поколения к общей схеме."""
    r = blank(s(raw.get("pn")))
    v = s(raw.get("verdict"))
    if v in VERDICTS:
        r["verdict"] = v
    else:
        # словарь ранней проверки: вердикт выводим из подтверждённого наличия
        ins = s(raw.get("in_stock"))
        if ins == "yes":
            r["verdict"] = "in_stock"
        elif ins == "no":
            r["verdict"] = "pn_found_no_stock"
        elif v in ("confirmed", "price_differs"):
            r["verdict"] = "pn_found_no_stock"
        else:
            r["verdict"] = "pn_not_found"
    for k in ("seller", "seller_url", "seller_country", "stock_qty", "lead_time",
              "real_maker", "real_pn", "substitute", "note"):
        r[k] = s(raw.get(k))
    for k, allowed in (("kind", ("oem", "component_maker", "aftermarket", "unknown")),
                       ("in_stock", ("yes", "no", "conditional", "unknown")),
                       ("covers_qty", ("full", "partial", "no", "unknown"))):
        val = s(raw.get(k))
        r[k] = val if val in allowed else r[k]
    r["price"] = num(raw.get("price"))
    if r["price"] is None:  # ранняя проверка держала цену в real_lo/real_hi
        r["price"] = num(raw.get("real_lo"))
    r["currency"] = s(raw.get("currency")) or "USD"
    try:
        r["pack_qty"] = int(raw.get("pack_qty") or 1) or 1
    except (TypeError, ValueError):
        r["pack_qty"] = 1
    if v in LEGACY_NOTE:
        r["note"] = (LEGACY_NOTE[v] + ". " + r["note"]).strip()
    r["sellers"] = sellers_list(raw)
    r["checked_by"] = source
    return r


def attach_clusters(rows: list) -> None:
    """Кластер строки и адресаты кластера — чтобы «кому писать» было по каждой строке.

    По самой позиции продавец находится не всегда: на неопознанный чертёжный номер
    карточки в вебе нет в принципе. Но у соседних строк того же бренда и класса
    адресаты есть, и писать по ним надо тому же кругу компаний. Кластер —
    (лист, бренд заявки, категория), запасной — (лист, категория).
    """
    def wide(r):
        return f'{r["sheet"]} · {r["cat"]}'

    def tight(r):
        return f'{r["sheet"]} · {r["man"]} · {r["cat"]}' if r.get("man") else wide(r)

    pools: dict[str, dict] = {}
    for r in rows:
        for scope in (tight(r), wide(r)):
            pool = pools.setdefault(scope, {})
            for sl in r.get("sellers") or []:
                if not (sl.get("emails") or sl.get("phones")):
                    continue  # в подсказку идут только те, кому есть куда написать
                c = pool.setdefault(sl["seller_key"], dict(sl, lines=0))
                c["lines"] += 1

    for r in rows:
        r["cluster"] = tight(r)
        own = {sl["seller_key"] for sl in r.get("sellers") or []}
        picked, seen = [], set(own)
        for scope in (tight(r), wide(r)):
            for c in sorted(pools.get(scope, {}).values(), key=lambda x: -x["lines"]):
                if c["seller_key"] in seen:
                    continue
                seen.add(c["seller_key"])
                picked.append({k: v for k, v in c.items() if k != "lines"})
                if len(picked) >= 4:
                    break
            if len(picked) >= 4:
                break
        r["cluster_sellers"] = picked


def load_rows(path: Path, key: str) -> list:
    if not path.exists():
        return []
    doc = json.loads(path.read_text())
    return doc[key] if isinstance(doc, dict) else doc


def main() -> int:
    demand = json.loads(DEMAND.read_text())["rows"]
    prices_doc = json.loads(PRICES.read_text())
    price = {p["pn"]: p for p in prices_doc["prices"]}

    # заявка: агрегируем количество по артикулу, лист запоминаем
    items: dict[str, dict] = {}
    for row in demand:
        pn = s(row.get("pn"))
        if not pn:
            continue
        it = items.setdefault(pn, {
            "pn": pn, "sheet": row.get("sheet", ""), "man": s(row.get("man")),
            "model": s(row.get("model")), "name": s(row.get("name")),
            "cat": s(row.get("cat")), "qty": 0, "unit": s(row.get("unit")) or "шт",
        })
        try:
            it["qty"] += int(row.get("qty") or 0)
        except (TypeError, ValueError):
            pass

    # проверки от ранней к поздней — поздняя перекрывает
    checks: dict[str, dict] = {}
    for path, coll, src in (
        (PRICES, "checks", "проверка 08.2026"),
        (SHIP, "rows", "проверка 505 строк 09.2026"),
        (SWEEP, "rows", "сплошная проверка остатка 09.2026"),
    ):
        rows = prices_doc["checks"] if path == PRICES else load_rows(path, coll)
        for raw in rows:
            pn = s(raw.get("pn"))
            if pn in items:
                checks[pn] = norm(raw, src)

    out = []
    for pn, it in items.items():
        rec = dict(it)
        p = price.get(pn)
        rec["usd_lo"] = p["usd_lo"] if p else None
        rec["usd_hi"] = p["usd_hi"] if p else None
        rec["conf"] = p["conf"] if p else ""
        rec.update({k: v for k, v in (checks.get(pn) or blank(pn)).items() if k != "pn"})
        out.append(rec)

    # контакты — на продавца, а не на позицию: один справочник на всю выкладку
    contacts = contact_index()
    for rec in out:
        for sl in rec.get("sellers") or []:
            c = contacts.get(sl["seller_key"])
            if c:
                sl["emails"] = c["emails"][:3]
                sl["phones"] = c["phones"][:2]
                sl["site"] = sl["url"] or c["site"]
                sl["country"] = sl["country"] or c["country"]
            else:
                sl["emails"], sl["phones"] = [], []
                sl["site"] = sl["url"]

    attach_clusters(out)

    out.sort(key=lambda r: (r["sheet"], r["cat"], r["pn"]))
    DST.write_text(json.dumps({
        "updated": date.today().isoformat(),
        "source": "Заявка ЛУКОЙЛ (листы «Энергосети» и «НВН»): наличие у продавцов по всей номенклатуре",
        "method": "gt/tools/ship_merge.py сводит gt/data/rfq_demand.json с тремя поколениями "
                  "проверок: rfq_prices.json:checks (08.2026), ship_energoseti.json (505 строк) "
                  "и ship_sweep.json (остаток 863). Поздняя проверка перекрывает раннюю; "
                  "строки без проверки помечены not_checked.",
        "rows": out,
    }, ensure_ascii=False, indent=1))

    def contacted(r):
        return [sl for sl in (r.get("sellers") or []) + (r.get("cluster_sellers") or [])
                if sl.get("emails") or sl.get("phones")]

    checked = sum(1 for r in out if r["verdict"] != "not_checked")
    own = sum(1 for r in out if any(sl.get("emails") or sl.get("phones")
                                    for sl in r.get("sellers") or []))
    any_c = sum(1 for r in out if contacted(r))
    three = sum(1 for r in out if len(contacted(r)) >= 3)
    print(f"позиций {len(out)}, проверено {checked}, без проверки {len(out) - checked}")
    print(f"  контакт по самой строке: {own}")
    print(f"  контакт по строке или её кластеру: {any_c} ({round(100 * any_c / len(out))}%)")
    print(f"  три и больше адресатов с контактом: {three}")
    print(f"  БЕЗ единого адресата: {len(out) - any_c}")
    for sheet in sorted({r["sheet"] for r in out}):
        n = [r for r in out if r["sheet"] == sheet]
        st = sum(1 for r in n if r["verdict"] == "in_stock")
        print(f"  {sheet}: {len(n)} позиций, на складе {st}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
