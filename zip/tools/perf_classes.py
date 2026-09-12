# -*- coding: utf-8 -*-
"""Карта сорсинга по классам деталей гидроперфораторов Sandvik / Epiroc (Atlas Copco).

Ничего нового не ищет. Перерабатывает накопленное: позиции базы ЗИП, статусы
Битрикса, заводские КП, ODM-привязки, CRM прозвона, реестр поставщиков с
контактами, материаловедческую стратегию по узлам. Раскладывает 179 позиций
перфораторов по классам деталей и для каждого класса собирает: куда идти
(поставщики с контактами, ранжированные по доказанности), что уже есть
(КП, продажи), ценовой ориентир.

Выход: zip/data/perf_sourcing_map.json
"""
import json
import re
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data"
PN = ROOT.parent / "pnw" / "data"
FX = {"USD": 1, "CNY": 0.1386, "EUR": 1.164, "RUB": 0.01158}

# Классы деталей перфоратора. Порядок = приоритет при совпадении нескольких.
CLASSES = [
    ("хвостовик", "Хвостовик (shank adapter)", r"хвостовик|shank"),
    ("букса", "Буксы и втулки вращения, направляющие втулки", r"букса|втулка|bush|направляющая втулка|ступица"),
    ("поршень", "Поршень-боёк, направляющие поршня", r"поршень|боёк|boek|piston|направляющая поршня|направляющая\b"),
    ("драйвер", "Драйвер вращения (rotation chuck driver)", r"драйвер|driver|вращатель"),
    ("диафрагма", "Диафрагма аккумулятора", r"диафрагм|мембран"),
    ("комплект", "Комплекты уплотнений и ремкомплекты", r"комплект|набор|ремонт|seal kit|kit"),
    ("уплотнение", "Уплотнения, манжеты, кольца, пыльники (поштучно)", r"уплотн|манжет|кольц|пыльник|сальник|o-?ring|seal"),
    ("клапан", "Клапаны, ниппели, зарядные клапаны", r"клапан|ниппель|valve"),
    ("крепёж", "Шпильки, болты, стяжки", r"шпильк|болт|гайк|шайб|стопор|штифт"),
    ("гидромотор", "Гидромотор вращения, насосы", r"мотор|насос|привод|двигатель гидравл"),
    ("электрика", "Датчики, кабели, реле, генераторы", r"датчик|кабель|реле|генератор|соединитель|провод"),
    ("прочее", "Прочее (шток, муфта, экстрактор, охладитель)", r"."),
]
# соответствие класса узлу материаловедческой стратегии (part_class по префиксу)
NODE_OF = {"хвостовик": "Хвостовик", "букса": "Поворотные втулки", "поршень": "Боёк",
           "диафрагма": "Accumulator diaphragm", "уплотнение": "Уплотнения, манжеты",
           "комплект": "Уплотнения, манжеты", "драйвер": "Хвостовик"}


def load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def cls_of(name):
    n = (name or "").lower()
    for key, title, rx in CLASSES:
        if re.search(rx, n):
            return key
    return "прочее"


def machine_of(model):
    m = re.findall(r"COP\s?\d{4}|COP\s?MD\s?\d+|COP\s?RR\s?\d+|HL\d{3,4}|HLX\d|RD\d{3}", str(model or ""), re.I)
    return sorted({re.sub(r"\s+", " ", x.upper()) for x in m})


# Капитал и канал поставщика — по данным нашей базы (ODM-реестр, вердикты по сайтам).
# Компании с капиталом OEM (США/ЕС/Швеция) и дилеры оригинала для размещения не опция:
# исключаются из ранжирования, остаются только как ценовой ориентир.
CAPITAL = [
    (r"sanshan|zuanshan|shandong rock drilling", "oem",
     "Капитал OEM: по странице компании — с августа 2016 дочернее предприятие Atlas Copco "
     "(в ODM-реестре базы отмечено как бывшее СП с Atlas Copco/Epiroc 2013–2020). "
     "Для размещения не опция; котировки — только ценовой ориентир"),
    (r"lingong|lgmrt", "oem", "СП Sandvik Group и Lingong — капитал OEM, для размещения не опция"),
    (r"answk|machfans|aircompressorstrade", "dealer",
     "Дилер оригинальных запчастей Atlas Copco/Epiroc (по ODM-реестру: distributor of genuine, not ODM) — "
     "канал OEM, для размещения не опция"),
    (r"\bwms\b|atmaca", "reseller",
     "Реселлер уровня OEM (Турция) — цены в 8 раз выше китайских заводов; только ценовой ориентир"),
]


# Экспортёры из price_records → имя поставщика в реестре/CRM
QUOTER_RX = {"KAT": r"\bKAT\b", "YLF": r"yonglianfeng|\bYLF\b", "Fuxuan": r"fuxuan",
             "Mindrill": r"mindrill", "Sanshan": r"sanshan|zuanshan|shandong rock drilling",
             "WMS": r"\bwms\b|atmaca"}


def quoter_match(exp, name):
    rx = QUOTER_RX.get(exp)
    return bool(re.search(rx, name, re.I)) if rx else exp.lower()[:5] in name.lower()


METAL = {"хвостовик", "букса", "поршень", "драйвер", "крепёж", "прочее"}
SEAL = {"уплотнение", "комплект", "диафрагма"}


def off_profile(key, direction):
    """1, если направление поставщика в CRM явно из другой группы классов."""
    d = direction or ""
    if key in METAL and re.search(r"seal|рти|уплотн|фильтр|filter|мотор|motor|сенсор|sensor", d, re.I) \
            and not re.search(r"металл|piston|bushing|shank|хвостов|внутренност|drifter|дрифтер", d, re.I):
        return 1
    if key in SEAL and re.search(r"shank|хвостов|мотор|motor|фильтр|filter|сенсор|sensor", d, re.I) \
            and not re.search(r"seal|рти|уплотн|kit|внутренност", d, re.I):
        return 1
    return 0


def capital_of(name):
    """→ (код, причина-исключения или '', исключён?)."""
    for rx, code, why in CAPITAL:
        if re.search(rx, name, re.I):
            return code, why, True
    return "cn", "", False


def main():
    P = load(D / "positions.json")
    perf = [p for p in P if str(p.get("category", "")).startswith(("01", "02"))]
    master = load(PN / "item_master.json")["items"]
    kv_of = {it.get("src_id"): it["kv"] for it in master if it["section"] == "ЗИП ГШО"}
    S = load(PN / "supplier_master.json")["suppliers"]
    PS = load(PN / "part_suppliers.json")["parts"]
    crm = {re.sub(r"[^a-zа-я0-9]", "", (s["name"] or "").lower())[:14]: s
           for s in (load(D / "supplier_crm.json") or {}).get("suppliers", [])}
    nodes = load(D / "material_strategy.json") or []
    prices = load(D / "price_records.json") or []

    quotes = defaultdict(list)          # position_id → [(завод, USD)]
    for r in prices:
        if "КП поставщика" in str(r.get("source")) and r.get("unit_price"):
            usd = r["unit_price"] * FX.get((r.get("currency") or "USD").upper(), 1)
            quotes[r["position_id"]].append((r.get("exporter") or "?", round(usd, 1)))

    GENERIC = {"shandong", "china", "guangzhou", "ningbo", "jining", "xiamen", "jinan", "qingdao",
               "shanghai", "jiangxi", "zhejiang", "jiangsu", "hebei", "fujian", "hunan", "rock",
               "drilling", "tools", "machinery", "hydraulic", "mining", "seals", "co", "ltd"}

    def tokens(name):
        return [t for t in re.findall(r"[a-zа-я]{4,}", (name or "").lower()) if t not in GENERIC]

    def crm_stage(name):
        """Совпадение по отличительному слову имени (sanshan, hovoo, woserld…), а не по
        префиксу: иначе «Shandong Jufengyuan» ловил карточку «Shandong KAT» по слову Shandong."""
        mine = set(tokens(name))
        for c in crm.values():
            if mine & set(tokens(c.get("name"))):
                return c.get("stage") or "", c.get("direction") or ""
        return "", ""

    groups = defaultdict(list)
    for p in perf:
        groups[cls_of(p["name"])].append(p)

    # Хвостовики: как позиции в базе ЗИП не заведены, но есть в BOM COP 3060MUX
    # (KV30 0001, KV30 0002) и как направление в CRM (7 поставщиков). Класс нужен —
    # это первый кандидат на тестовую закупку по материаловедческой стратегии.
    bom = load(D / "bom.json") or {"machines": []}
    shank = [x for m in bom["machines"] for x in m.get("parts", [])
             if re.search(r"ХВОСТОВИК|ШТАНГ", x.get("desc", ""))]
    if shank and not groups.get("хвостовик"):
        groups["хвостовик"] = [{"id": None, "catalog_no": x["epiroc_pn"], "name": f"{x['desc'].capitalize()} {x['epiroc_pn']} Epiroc",
                                "model": bom["machines"][0]["name"], "bitrix_status": "ожидает",
                                "_kv30": x.get("kv_pn"), "_izmer": x.get("st_izmer"), "_rkd": x.get("st_rkd")}
                               for x in shank]

    out = []
    for key, title, _ in CLASSES:
        ps = groups.get(key, [])
        if not ps:
            continue
        # поставщики класса: собираем по всем позициям, считаем вес и контакт
        sup = Counter()
        for p in ps:
            for g, ks in PS.get(kv_of.get(p["id"]), {}).items():
                for k in ks:
                    sup[k] += 1
        sup_rows = []
        quoted_by = Counter(e for p in ps for e, _ in quotes.get(p["id"], []))
        seen_tok = set()
        for k, n in sup.most_common():
            s = S.get(k)
            if not s:
                continue
            # дедуп: Sanshan под тремя именами — один поставщик
            tk = tuple(sorted(tokens(s["name"])))[:1]
            if tk and tk in seen_tok:
                continue
            stage, direction = crm_stage(s["name"])
            nq = sum(v for e, v in quoted_by.items() if quoter_match(e, s["name"]))
            # ранг: WMS — реселлер по ценам уровня OEM (медиана x8 к лучшему китайцу),
            # в тестовую закупку не идёт, остаётся ориентиром цены и последним резервом.
            # Дальше: есть КП > ответил в CRM > есть контакт > просто привязка.
            is_wms = "wms" in s["name"].lower() or "atmaca" in s["name"].lower()
            # релевантность: случайная одиночная ODM-привязка трейдера в класс не идёт —
            # оставляем, если есть КП, или CRM ведёт этот класс, или связок заметно
            if not nq and not stage and n < max(2, round(0.2 * len(ps))):
                continue
            if tk:
                seen_tok.add(tk)
            rank = (1 if is_wms else 0, 0 if nq else 1, off_profile(key, direction),
                    0 if ("Ответил" in stage or "К/П" in stage) else 1,
                    0 if (s.get("email") or s.get("phone")) else 1, -n)
            flag = ""
            if re.search(r"sanshan|zuanshan", s["name"], re.I):
                flag = ("С августа 2016 — полностью дочернее предприятие Atlas Copco (по их странице /company/). "
                        "Для тестовой закупки плюс: оригинальное качество по китайской цене. "
                        "Для замещения OEM — риск: канал контролирует сам OEM и может его закрыть")
            elif re.search(r"lingong|lgmrt", s["name"], re.I):
                flag = "СП Sandvik Group и Lingong — конфликт интересов с OEM"
            cap, why, excl = capital_of(s["name"])
            sup_rows.append({
                "flag": flag, "capital": cap, "excluded": why,
                "name": s["name"], "country": s.get("country", ""), "email": s.get("email", ""),
                "phone": s.get("phone", ""),
                "whatsapp": s.get("whatsapp", "") if re.search(r"\d{7,}", str(s.get("whatsapp", ""))) else "",
                "site": s.get("site", ""), "positions": n, "quotes": nq,
                "crm_stage": stage, "crm_direction": direction, "_rank": rank,
            })
        def row_from_crm(c, nq=0):
            nm = c.get("name") or ""
            ct = str(c.get("contacts") or "")
            em = re.search(r"[\w.\-+]+@[\w\-]+\.[\w.\-]+", ct)
            ph = re.search(r"\+?\d[\d\s\-()]{7,}\d", ct)
            wa = re.search(r"(?:WA|WhatsApp|WeChat)[^\n\d]{0,12}(\+?\d[\d\s\-]{7,}\d)", ct, re.I)
            stage = c.get("stage") or ""
            cap, why, excl = capital_of(nm)
            return {
                "capital": cap, "excluded": why,
                "name": nm, "country": c.get("geo", ""), "email": em.group(0) if em else "",
                "phone": ph.group(0).strip() if ph else "", "whatsapp": ("WA/WeChat " + wa.group(1).strip()) if wa else "",
                "site": "", "positions": 0, "quotes": nq, "crm_stage": stage,
                "crm_direction": c.get("direction") or "",
                "_rank": (0, 0 if nq else 1, off_profile(key, c.get("direction") or ""),
                          0 if ("Ответил" in stage or "К/П" in stage) else 1,
                          0 if (em or ph) else 1, 0),
            }

        # Заводы, давшие КП по позициям класса, но не привязанные к ним в реестре
        # (KAT, YLF, Fuxuan, Mindrill) — берём из CRM прозвона с контактами
        for exp, v in quoted_by.items():
            if any(quoter_match(exp, r["name"]) for r in sup_rows):
                continue
            c = next((c for c in crm.values() if quoter_match(exp, c.get("name") or "")), None)
            if c:
                sup_rows.append(row_from_crm(c, v))

        # CRM прозвона: поставщики привязаны к НАПРАВЛЕНИЮ, не к позиции — подмешиваем по классу
        DIR_RX = {"хвостовик": r"хвостовик|shank", "букса": r"bushing|втулк|guide|металл(?!.*мотор)",
                  "поршень": r"(?<![-\w])piston|внутренност", "драйвер": r"driver|внутренност",
                  "комплект": r"seal kit|overhaul|уплотн|рти", "уплотнение": r"рти|seal",
                  "диафрагма": r"рти|seal|диафрагм", "клапан": r"клапан|valve",
                  "гидромотор": r"мотор|motor", "прочее": r"комплектн|drifter|дрифтер"}
        rx = DIR_RX.get(key)
        have = {t for r in sup_rows for t in tokens(r["name"])} | {t for r in sup_rows for t in tokens(r["name"])}
        if rx:
            for c in crm.values():
                if not re.search(rx, c.get("direction") or "", re.I):
                    continue
                nm = c.get("name") or ""
                if set(tokens(nm)) & have:
                    continue
                sup_rows.append(row_from_crm(c))
        # отказ по направлению — выводим, но в конец
        for r in sup_rows:
            if "Отказ" in (r.get("crm_stage") or ""):
                r["_rank"] = (2,) + tuple(r["_rank"][1:])
        sup_rows.sort(key=lambda r: r["_rank"])
        for r in sup_rows:
            r.pop("_rank")
        allowed = [r for r in sup_rows if not r["excluded"]]
        excluded = [r for r in sup_rows if r["excluded"]]

        # ценовой ориентир по позициям класса: лучший независимый китайский КП,
        # отдельно — Sanshan (капитал OEM, справочно) и WMS (реселлер)
        best, ref_oem, wms = [], [], []
        for p in ps:
            q = dict(quotes.get(p["id"], []))
            cn = [v for e, v in q.items() if not capital_of(e)[2] and v > 0]
            oemcn = [v for e, v in q.items() if capital_of(e)[0] == "oem" and v > 0]
            if cn:
                best.append(min(cn))
            if oemcn:
                ref_oem.append(min(oemcn))
            if q.get("WMS"):
                wms.append(q["WMS"])
        node = next((m for m in nodes if m["part_class"].startswith(NODE_OF.get(key, "\x00"))), None)
        machines = Counter(mm for p in ps for mm in machine_of(p.get("model")))
        out.append({
            "class": key, "title": title,
            "positions": len(ps),
            "with_quote": sum(1 for p in ps if quotes.get(p["id"])),
            "sold": sum(1 for p in ps if p.get("bitrix_status") == "продавали"),
            "quoted_bitrix": sum(1 for p in ps if p.get("bitrix_status") == "квотировали"),
            "machines": [m for m, _ in machines.most_common(8)],
            "examples": [{"kv": kv_of.get(p["id"]) or p.get("_kv30"), "pn": p["catalog_no"], "name": p["name"],
                          "model": p.get("model"), "bitrix": p.get("bitrix_status"),
                          "quotes": quotes.get(p["id"], []),
                          **({"izmer": p.get("_izmer") or "—", "rkd": p.get("_rkd") or "—"} if "_kv30" in p else {})}
                         for p in ps[:60]],
            "price_best_cn_usd": round(st.median(best), 1) if best else None,
            "price_ref_oem_cn_usd": round(st.median(ref_oem), 1) if ref_oem else None,
            "price_wms_usd": round(st.median(wms), 1) if wms else None,
            "material": {"oem": node.get("oem_material"), "alt": node.get("alt_material"),
                         "alt_treatment": node.get("alt_treatment"), "gain": node.get("expected_gain")} if node else None,
            "suppliers": allowed[:8],
            "excluded": [{"name": r["name"], "capital": r["capital"], "reason": r["excluded"], "quotes": r["quotes"],
                          "crm_stage": r["crm_stage"]} for r in excluded],
        })

    (D / "perf_sourcing_map.json").write_text(json.dumps(
        {"updated": "2026-09-02", "source": "positions + price_records + supplier_master + supplier_crm + material_strategy",
         "classes": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    packets = []
    for c in out:
        packets.append({
            "class": c["class"], "title": c["title"], "positions": c["positions"],
            "with_quote": c["with_quote"], "sold": c["sold"], "machines": c["machines"],
            "examples": [{k: v for k, v in e.items() if k in ("kv", "pn", "name", "model", "bitrix", "quotes", "izmer", "rkd")}
                         for e in c["examples"][:6]],
            "price_best_cn_usd": c["price_best_cn_usd"], "price_ref_oem_cn_usd": c["price_ref_oem_cn_usd"],
            "price_wms_usd": c["price_wms_usd"],
            "material": c["material"],
            "suppliers": [{k: v for k, v in r.items() if k in ("name", "country", "email", "phone", "whatsapp", "quotes", "crm_stage", "crm_direction")}
                          for r in c["suppliers"][:5]],
            "excluded": c["excluded"],
        })
    (D / "perf_class_packets.json").write_text(json.dumps(packets, ensure_ascii=False), encoding="utf-8")
    print(f"позиций перфораторов: {len(perf)} → классов: {len(out)}; пакетов для агентов: {len(packets)} "
          f"({(D / 'perf_class_packets.json').stat().st_size:,} байт)".replace(",", " "))
    for c in out:
        top = c["suppliers"][0]["name"][:28] if c["suppliers"] else "—"
        print(f"  {c['positions']:3} поз. | КП {c['with_quote']:2} | прод {c['sold']:2} | "
              f"CN ${str(c['price_best_cn_usd']):>6} / OEM-CN ${str(c['price_ref_oem_cn_usd']):>6} / WMS ${str(c['price_wms_usd']):>6} | "
              f"{c['title'][:34]:36} | {top} | искл. {len(c['excluded'])}")


if __name__ == "__main__":
    main()
