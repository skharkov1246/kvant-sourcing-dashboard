#!/usr/bin/env python3
"""Сборка gt/public/rfq.html — план покрытия запросами заявки ЛУКОЙЛ (критичный импортный ЗИП).

Читает gt/data/rfq_demand.json (позиции из XLSX), подбирает поставщиков из базы
(dossiers + research + checklist + история Bitrix), режет на пачки «направление × категория»,
считает вероятность ответа по прозрачным правилам и пишет самодостаточную страницу
с отметками сорсеров (localStorage).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = lambda f: json.load(open(ROOT / "data" / f))


def nn(s):
    return re.sub(r"[^a-zа-яё0-9]", "", (s or "").lower())


# ── пул поставщиков ──────────────────────────────────────────────────────────
def build_pool():
    dossiers = D("dossiers.json")["dossiers"]
    hist = {}
    for h in D("bitrix_history.json")["history"]:
        hist[nn(h["name"])] = h["items"]
    sites, extra = {}, {}
    for src, key in [("research_suppliers.json", "rows"), ("sgt400_checklist.json", "rows"),
                     ("heavy_suppliers.json", "rows"), ("tfs_subsuppliers.json", "rows")]:
        for x in D(src)[key]:
            k = nn(x.get("name", ""))
            if not k:
                continue
            sites.setdefault(k, x.get("site") or "")
            e = extra.setdefault(k, {"what": "", "country": "", "email": ""})
            e["what"] = e["what"] or (x.get("what") or "") + " " + (x.get("link") or "")
            e["country"] = e["country"] or (x.get("country") or "")
            e["email"] = e["email"] or (x.get("email") or "")
    for x in json.load(open(ROOT / "data" / "suppliers.json")):
        k = nn(x.get("name", ""))
        sites.setdefault(k, x.get("site") or "")
        e = extra.setdefault(k, {"what": "", "country": "", "email": ""})
        e["what"] = e["what"] or (x.get("what") or "")
        e["country"] = e["country"] or (x.get("country") or "")
    bx = D("bitrix_supplier_sites.json")
    for n, v in bx.get("confirmed", {}).items():
        k = nn(n)
        if isinstance(v, dict):
            sites.setdefault(k, v.get("site") or "")
            e = extra.setdefault(k, {"what": "", "country": "", "email": ""})
            e["email"] = e["email"] or (v.get("email") or "")
            e["what"] = e["what"] or (v.get("ev") or "")

    pool = {}
    # непрофильные поставщики — ОТДЕЛЬНЫЙ файл, в ГТУ-базу не попадают
    for x in D("rfq_suppliers.json")["rows"]:
        k = nn(x.get("name", ""))
        if not k:
            continue
        pool[k] = {
            "name": x["name"], "country": x.get("country", ""), "site": x.get("site", ""),
            "email": x.get("email", ""), "phone": "", "person": "",
            "hook": "", "note": x.get("resp_note", ""),
            "what": (x.get("what", "") + " " + (x.get("relation") or "")).strip(),
            "prank": 4, "bx": 0, "bx_answered": False, "bx_models": [],
            "domain": "nonprofile",
        }
    # компании из research без досье (профильный улов)
    for x in D("research_suppliers.json")["rows"]:
        k = nn(x.get("name", ""))
        if not k:
            continue
        pool[k] = {
            "name": x["name"], "country": x.get("country", ""), "site": x.get("site", ""),
            "email": x.get("email", ""), "phone": "", "person": "",
            "hook": "", "note": x.get("link", ""),
            "what": (x.get("what", "") + " " + (x.get("link") or "")).strip(),
            "prank": 4, "bx": 0, "bx_answered": False, "bx_models": [],
            "domain": "turbine",
        }
    for name, d in dossiers.items():
        k = nn(name)
        it = hist.get(k, [])
        stages = " ".join(str(i.get("stage") or "") for i in it).lower()
        answered = bool(re.search(r"кп|предложени|получено|счет|счёт|договор|поставк", stages))
        models = set()
        for i in it:
            for m in i.get("models") or []:
                models.add(m.upper())
        p = d.get("supplied_proof", "none")
        prank = 1 if (it or p == "shipment") else 2 if p == "named_client" else 3 if p == "catalog" else 4
        pool[k] = {
            "name": name, "country": extra.get(k, {}).get("country", ""),
            "site": sites.get(k, ""),
            "email": d.get("contact_email") or extra.get(k, {}).get("email", ""),
            "phone": d.get("contact_phone", ""), "person": d.get("contact_person", ""),
            "hook": d.get("hook", ""), "note": d.get("note", ""),
            "what": (extra.get(k, {}).get("what", "") + " " + d.get("note", "")).strip(),
            "prank": prank, "bx": len(it), "bx_answered": answered,
            "bx_models": sorted(models), "domain": "turbine",
        }
    return pool


# ── ключевые слова профиля под категорию ─────────────────────────────────────
CAT_KEYS = {
    "прокладки и уплотнения": r"уплотнен|прокладк|seal|gasket|o-ring|кольц|манжет|graphite|ptfe|elastomer|packing",
    "клапаны и арматура": r"клапан|valve|арматур|actuator|регулятор|дозирован",
    "фильтры": r"фильтр|filter|сепаратор|filtration",
    "датчики и КИП": r"датчик|sensor|кип|instrument|термопар|transmitter|давлени|мониторинг|vibration|bently",
    "зажигание и свечи": r"свеч|зажиган|ignit|катушк|exciter",
    "электрика и автоматика": r"электро|модул|плат|контроллер|автоматик|control|electric|s7|simatic|panel|switchgear|шкаф|obsolete|электрик",
    "подшипники": r"подшипник|bearing",
    "насосы": r"насос|pump",
    "горячая часть и камера сгорания": r"лопатк|blade|nozzle|сопл|камер|combust|жаров|hot gas|литьё|литье|casting|покрыти|coating",
    "крепёж": r"крепеж|крепёж|болт|fastener|шпильк|stud|nimonic|inconel bolt",
    "шланги и трубки": r"шланг|hose|рукав|tubing|трубк",
    "масла и химия": r"масло|смазк|lubric|chemical|химия",
    "механика и приводы": r"привод|actuator|редуктор|gear|двигател|motor|муфт|coupling|цилиндр|гидравлик",
    "ремкомплекты и наборы": r"ремкомплект|overhaul kit|запчаст|spare|parts",
    "соединения и фитинги": r"фитинг|fitting|соединени|фланец|flange|адаптер",
    "инструмент и расходка": r"инструмент|tool",
    "канаты и такелаж": r"канат|трос|rope|wire",
    "прочее": r"запчаст|spare|parts|ЗИП",
}

# ── направления (что реально стоит в заявке) ────────────────────────────────
def dir_of(row):
    man, model = row["man"], (row["model"] or "").upper()
    if man == "Siemens":
        if "SGT-400" in model or "SGT400" in model:
            return "SGT-400"
        if "ШКАФ" in model or "PMS" in model or "БЛОК" in model or row["cat"] == "электрика и автоматика":
            return "Siemens: шкафы и электрика"
        return "SGT-400"
    if man == "Solar":
        return "Solar Taurus 60S/70/70MD"
    if man in ("Cummins", "Fleetguard"):
        return "Cummins/Fleetguard (ГПУ)"
    if man == "Jenbacher/INNIO":
        return "Jenbacher INNIO (ГПУ)"
    mu = man.upper()
    if "ABB" in mu:
        return "НВН: ABB (электрика и приводы)"
    if "DRILLMEC" in mu or "OLEOBI" in mu:
        return "НВН: Drillmec/Oleobi (буровые установки)"
    if "OILWELL" in mu or "NOV" == mu or "TESCO" in mu or "SHAFFER" in mu:
        return "НВН: NOV/Tesco/Shaffer (буровое)"
    if "SCHLUMBERGER" in mu or "CAMERON" in mu or "SWACO" in mu or "FMC" in mu:
        return "НВН: Schlumberger/Cameron/MI Swaco/FMC"
    if "LIEBHERR" in mu:
        return "НВН: Liebherr (краны)"
    if "DRILLTECH" in mu:
        return "НВН: Drilltech Cangzhou (буровое, реверс)"
    if "BORNEMANN" in mu or "MARFLEX" in mu:
        return "НВН: насосы (Bornemann/MarFlex)"
    return "НВН: прочие производители"


# Направление → (домен пула, регексп доказательства профиля). Совпадение по направлению
# ОБЯЗАТЕЛЬНО: категория сама по себе адресата не оправдывает — иначе трейдер турбинных
# лопаток попадает в рассылку по реле Finder только за слово «control» в описании.
DIR_RULES = {
    # ВАЖНО: только конкретика брендов/моделей. Родовое «турбины» сюда нельзя —
    # иначе «Силовые машины» (ГТЭ-170) становятся поставщиком прокладок на Taurus.
    "SGT-400": ("turbine", r"SGT|CYCLONE|ЛИНКОЛЬН|LINCOLN|SIEMENS"),
    "Solar Taurus 60S/70/70MD": ("turbine", r"TAURUS|SOLAR TURBINES|CENTAUR|MARS \d|TITAN \d|SATURN \d|СОЛАР|\bSOLAR\b"),
    "Siemens: шкафы и электрика": ("any", r"SIMATIC|\bS7\b|SITRANS|SIEMENS|OBSOLETE|промэлектрон|industrial electronic|автоматизаци|switchgear|шкаф"),
    "Cummins/Fleetguard (ГПУ)": ("any", r"CUMMINS|FLEETGUARD|ГПУ|GAS ENGINE|газопоршн|DONALDSON|BALDWIN|фильтр"),
    "Jenbacher INNIO (ГПУ)": ("any", r"JENBACHER|INNIO|ГПУ|GAS ENGINE|газопоршн|MWM|свеч"),
    "НВН: ABB (электрика и приводы)": ("any", r"\bABB\b|привод|drive|электродвигател|промэлектрон|industrial electronic"),
    "НВН: Drillmec/Oleobi (буровые установки)": ("any", r"DRILLMEC|OLEOBI|бурово|drilling|rig\b|гидравлик"),
    "НВН: NOV/Tesco/Shaffer (буровое)": ("any", r"\bNOV\b|OILWELL|TESCO|SHAFFER|бурово|drilling|BOP|превентор|top drive"),
    "НВН: Schlumberger/Cameron/MI Swaco/FMC": ("any", r"SCHLUMBERGER|CAMERON|SWACO|\bFMC\b|устьев|wellhead|бурово|drilling|шламов"),
    "НВН: Liebherr (краны)": ("any", r"LIEBHERR|кран\b|crane|канат|такелаж|гидроцилиндр"),
    "НВН: Drilltech Cangzhou (буровое, реверс)": ("any", r"DRILLTECH|бурово|drilling|CANGZHOU|реверс"),
    "НВН: насосы (Bornemann/MarFlex)": ("any", r"BORNEMANN|MARFLEX|насос|pump"),
    "НВН: прочие производители": ("any", r"."),
}

# «Прочее» — не категория, а мешок. Разбираем его по типу самой детали: из наименований
# позиций достаём терминов-триггеры и требуем, чтобы профиль поставщика им отвечал.
PART_TERMS = [
    (r"сканер факела|детектор пламени|контрол\w* пламени|flame scan", r"сканер|flame|пламен|горелоч|burner|combustion|детектор"),
    (r"газов\w* регистр|горелк", r"горелк|burner|регистр|register|combustion"),
    (r"нагреват|подогреват|обогрев", r"нагреват|heater|подогрев|обогрев|heating element|thermal"),
    (r"извещател|пожарн|обнаружени\w* пожара|газоанализ", r"пожар|fire|извещател|flame detect|gas detect|газоанализ|detector"),
    (r"указател\w* уровня|уровнемер|гидрозатвор|смотров\w* стекл", r"уровн|level gauge|sight glass|смотров|indicator|gauge"),
    (r"коммутатор|scalance|switch|брандмауэр|firewall|simatic ipc", r"коммутатор|switch|scalance|network|ethernet|промышленн\w* сет|simatic|automation|плк|plc"),
    (r"наконечник|разъ[ёе]м|обжимн|контакт\b|стяжк", r"наконечник|разъ[ёе]м|connector|контакт|терминал|обжим|crimp|cable|кабель"),
    (r"мультистанц|монитор|hmi|панел\w* оператора|дисплей", r"hmi|панел|дисплей|monitor|операторск|scada|визуализ"),
    (r"экран|фильтр\w* воздухозабор|воздухозаборн", r"воздухозабор|air inlet|фильтр|filter|сетк|screen|mesh"),
    (r"сигнализатор|барьер|искробезопасн", r"барьер|искробезопасн|intrinsic|сигнализатор|signal condition"),
    (r"расцепител|выключател|3ah|3ak|3ae", r"выключател|switchgear|breaker|расцепител|коммутационн|вакуумн"),
    (r"сервоклапан|servo|moog", r"сервоклапан|servo|гидравлик|hydraulic|пропорциональн"),
    (r"канат|трос", r"канат|трос|rope|wire rope|такелаж"),
    (r"джойстик|энкодер", r"джойстик|энкодер|encoder|joystick|датчик положени"),
]


def batch_profile(items):
    """Регексп профиля поставщика, собранный из типов деталей конкретной пачки."""
    blob = " ".join((i.get("name") or "") + " " + (i.get("model") or "") for i in items).lower()
    pats = [sup_pat for trig, sup_pat in PART_TERMS if re.search(trig, blob, re.I)]
    return "|".join(pats) if pats else None


def model_match(sup, direction):
    """Профиль поставщика реально относится к этому направлению?"""
    m = " ".join(sup["bx_models"]) + " " + sup["what"].upper() + " " + sup["note"].upper() + " " + sup["name"].upper()
    _, pat = DIR_RULES.get(direction, ("any", r"$^"))
    return bool(re.search(pat, m, re.I))


def domain_ok(sup, direction):
    """Газотурбинную компанию не зовём на буровое и наоборот."""
    need, _ = DIR_RULES.get(direction, ("any", ""))
    if need == "any":
        return True
    return sup.get("domain") == need


# ── роль поставщика: узкий специалист / трейдер / общий сервис ───────────────
TRADER_RE = re.compile(
    r"трейдер|трейдинг|trading|trade\b|дистрибьютор|distributor|дилер|dealer|реселлер|reseller|"
    r"сток\b|stock\w*ist|склад готов|поставщик зип|supply of spare|parts supply|"
    r"маркетплейс|аукцион|auction|б/у|second.?hand|surplus|посредник", re.I)
SERVICE_RE = re.compile(
    r"\bmro\b|оверхол|overhaul|капремонт|ремонт турбин|сервис турбин|field service|"
    r"полевой сервис|техобслуживание|инспекц|балансировк|ltsa|o&m\b|станц", re.I)
MAKER_RE = re.compile(
    r"производ|изготовл|завод|manufact|литьё|литье|casting|forging|поковк|ковк|"
    r"механообработ|machining|обработк|штампов|токарн|фрезер|цех\b|plant\b|foundry|"
    r"выпускает|reverse.?engineer|реверс-инжинир", re.I)


# Кто понимает запрос ПО КОДУ (PN), а кому код чужого OEM ничего не говорит.
# У нас чертежей нет — значит субпоставщику, работающему по чертежам заказчика,
# запрос по PN отправлять бессмысленно: с ним разговор о номенклатуре и образце.
PN_OK_RE = re.compile(
    r"трейдер|trading|trade\b|дистрибьютор|distributor|дилер|dealer|реселлер|reseller|сток|stock|"
    r"склад|каталог|catalog|запчаст|spare part|aftermarket|реверс|reverse.?engineer|"
    r"кросс|cross.?refer|эквивалент|equivalent|interchangeable|взаимозаменя|"
    r"собственн\w* чертеж|по своим чертежам|own drawing|own design|"
    r"oem|авторизован|authorized|официальн\w* (дилер|партн)", re.I)


def ask_mode(sup):
    """'pn' — понимает запрос по каталожному номеру; 'nomen' — только по номенклатуре."""
    blob = sup["what"] + " " + sup["note"] + " " + sup["name"]
    return "pn" if PN_OK_RE.search(blob) else "nomen"


def sup_cats(sup):
    """Категории ЗИП, которые реально видны в профиле поставщика."""
    blob = sup["what"] + " " + sup["note"] + " " + sup["name"]
    return {c for c, pat in CAT_KEYS.items()
            if c != "прочее" and re.search(pat, blob, re.I)}


def role_of(sup):
    """specialist — делает конкретные детали; trader — торгует; service — общий сервис."""
    blob = sup["what"] + " " + sup["note"] + " " + sup["name"]
    cats = sup_cats(sup)
    maker = bool(MAKER_RE.search(blob))
    trader = bool(TRADER_RE.search(blob))
    service = bool(SERVICE_RE.search(blob))
    # узкий специалист: производит И профиль сфокусирован (1–4 категории)
    if maker and cats and len(cats) <= 4 and not (trader and not maker):
        return "specialist"
    if trader:
        return "trader"
    if service:
        return "service"
    # производитель с размытым профилем или ничего не понятно — к общим
    return "specialist" if (maker and cats) else "service"


def probability(sup, direction, cat):
    """Прозрачная оценка вероятности содержательного ответа + причина."""
    why = []
    if sup["bx_answered"] and model_match(sup, direction):
        p = 70; why.append("уже отвечал нам по этой теме в Bitrix")
    elif sup["bx"] > 0 and model_match(sup, direction):
        p = 50; why.append(f"наши запросы в Bitrix ×{sup['bx']}, исход не зафиксирован")
    elif sup["bx"] > 0:
        p = 22; why.append(f"знаком по Bitrix (×{sup['bx']}), но по ДРУГОЙ технике — спрашивать как о непрофильном")
    elif sup["prank"] <= 2 and model_match(sup, direction):
        p = 35; why.append("П%d + профильная модель" % sup["prank"])
    elif sup["prank"] <= 2:
        p = 25; why.append("П%d, модель придётся объяснять" % sup["prank"])
    elif sup["prank"] == 3:
        p = 18; why.append("каталожный профиль, холодный контакт")
    else:
        p = 8; why.append("не доказан, холодный контакт")
    if cat in sup_cats(sup):
        p += 12; why.append(f"делает именно эту номенклатуру ({cat})")
    if sup["email"]:
        p += 5; why.append("есть прямой email")
    else:
        p -= 5; why.append("только форма/телефон")
    if sup["person"]:
        p += 3; why.append("есть контактное лицо")
    return min(85, max(3, p)), "; ".join(why)


def load_prices():
    """Цены с пометкой проверки: verified — источник открыт и подтверждён скептиком."""
    try:
        raw = D("rfq_prices.json")
    except FileNotFoundError:
        return {}
    checks = {c["pn"].strip().upper(): c for c in raw.get("checks", []) if c.get("pn")}
    out = {}
    for p in raw.get("prices", []):
        pn = (p.get("pn") or "").strip().upper()
        if not pn:
            continue
        c = checks.get(pn)
        e = dict(p)
        if c:
            e["verdict"] = c["verdict"]
            e["in_stock"] = c.get("in_stock", "unknown")
            e["seller"] = c.get("seller", "")
            e["seller_url"] = c.get("seller_url") or p.get("url", "")
            e["stock_qty"] = c.get("stock_qty", "")
            e["lead_time"] = c.get("lead_time", "")
            e["check_note"] = c.get("note", "")
            if c["verdict"] == "confirmed":
                e["trust"] = "проверено"
                if c.get("real_lo"):
                    e["usd_lo"], e["usd_hi"] = c["real_lo"], c.get("real_hi") or c["real_lo"]
            elif c["verdict"] == "price_differs" and c.get("real_lo"):
                e["trust"] = "цена уточнена при проверке"
                e["usd_lo"], e["usd_hi"] = c["real_lo"], c.get("real_hi") or c["real_lo"]
            else:
                e["trust"] = "источник не подтвердился"
                e["conf"] = "C"
        else:
            e["verdict"] = ""
            e["trust"] = "не проверено" if p.get("conf") in ("A", "B") else "экспертная вилка"
            e["in_stock"] = "unknown"
            e["seller_url"] = p.get("url", "")
        out[pn] = e
    return out


def load_overrides():
    """Вердикты проверки актуальности (глазами сорсера): drop / doubt / send."""
    try:
        return D("rfq_overrides.json")["overrides"]
    except FileNotFoundError:
        return {}


def main():
    demand = D("rfq_demand.json")
    pool = build_pool()
    prices = load_prices()
    overrides = load_overrides()
    # применяем вердикты к пулу ДО раскладки
    dropped = 0
    for k in list(pool.keys()):
        sup = pool[k]
        ov = overrides.get(sup["name"])
        if not ov:
            continue
        if ov["verdict"] == "drop":
            del pool[k]
            dropped += 1
            continue
        if ov.get("new_site"):
            sup["site"] = ov["new_site"]
        sup["ov_verdict"] = ov["verdict"]
        sup["ov_note"] = ov.get("note", "")
        sup["ov_flags"] = ov.get("red_flags", "")
    print(f"проверка актуальности: вычеркнуто {dropped}, помечено doubt "
          f"{sum(1 for s in pool.values() if s.get('ov_verdict') == 'doubt')}")

    # пачки: направление × категория; мелочь внутри направления сливаем в «смешанную» пачку
    batches = {}
    for r in demand["rows"]:
        key = (dir_of(r), r["cat"])
        batches.setdefault(key, []).append(r)
    merged = {}
    for (direction, cat), items in batches.items():
        if len(items) < 4:
            merged.setdefault((direction, "смешанная пачка (мелкие категории)"), []).extend(items)
        else:
            merged[(direction, cat)] = items
    batches = merged

    # кандидаты на пачку
    def card(sup, direction, cat, role):
        p, why = probability(sup, direction, cat)
        ov = sup.get("ov_verdict", "")
        if ov == "doubt":
            p = max(3, p - 10)
            why += "; ⚠ проверка: " + (sup.get("ov_note") or "профиль под вопросом")[:160]
        elif ov == "send":
            why += "; ✓ актуальность подтверждена проверкой"
        # комплаенс-оценки — зона сорсеров (распоряжение 09.08), в карточки не выносим
        return {**{k: sup[k] for k in ("name", "country", "site", "email", "phone", "person", "hook")},
                "p": p, "why": why, "bx": sup["bx"], "prank": sup["prank"], "role": role,
                "cats": sorted(sup_cats(sup))[:4], "ask": ask_mode(sup), "ov": ov}

    # адресаты направления, разложенные по ролям (считаем один раз на направление)
    by_dir = {}
    for direction in {d for d, _ in batches}:
        spec, general = [], []
        for sup in pool.values():
            if not domain_ok(sup, direction):
                continue
            if not model_match(sup, direction):
                continue
            r = role_of(sup)
            (spec if r == "specialist" else general).append((sup, r))
        by_dir[direction] = (spec, general)

    out_batches = []
    for (direction, cat), items in sorted(batches.items(), key=lambda kv: -len(kv[1])):
        spec, _ = by_dir[direction]
        # в предметную пачку — ТОЛЬКО специалисты, у кого эта номенклатура в профиле
        cands = [card(s, direction, cat, r) for s, r in spec if cat in sup_cats(s)]
        fallback = False
        # «прочее»/«смешанная» — подбираем по типам деталей самой пачки, а не по категории
        if cat in ("прочее", "смешанная пачка (мелкие категории)"):
            prof = batch_profile(items)
            if prof:
                byparts = [card(s, direction, cat, r) for s, r in spec
                           if re.search(prof, s["what"] + " " + s["note"] + " " + s["name"], re.I)]
                if byparts:
                    seen_n = {c["name"] for c in cands}
                    cands += [c for c in byparts if c["name"] not in seen_n]
        if not cands:
            # профильного специалиста по этой номенклатуре в базе нет — показываем
            # специалистов направления с честной пометкой, чтобы пачка не висела пустой
            cands = [dict(c, why=c["why"] + "; профиль по этой номенклатуре НЕ подтверждён — уточнить до отправки")
                     for c in (card(s, direction, cat, r) for s, r in spec)]
            fallback = bool(cands)
        cands.sort(key=lambda c: -c["p"])
        cands = cands[:14]
        qty = sum(1 for _ in items)
        out_batches.append({
            "dir": direction, "cat": cat, "n": qty,
            "qty_total": sum(i["qty"] if isinstance(i["qty"], (int, float)) else 0 for i in items),
            "items": [dict({"name": i["name"][:160], "pn": i["pn"], "qty": i["qty"],
                            "unit": i["unit"], "model": i["model"][:60]},
                           **({"pr": prices[(i["pn"] or "").strip().upper()]}
                              if (i["pn"] or "").strip().upper() in prices else {}))
                      for i in items],
            "sups": cands,
            "covered": len(cands),
            "kind": "spec",
            "fallback": fallback,
        })

    # отдельная пачка на направление: трейдеры и общесервисные — им уходит весь
    # список направления одним письмом, а не предметная номенклатура
    for direction, (spec, general) in by_dir.items():
        if not general:
            continue
        dir_items = [r for r in demand["rows"] if dir_of(r) == direction]
        cands = [card(s, direction, "прочее", r) for s, r in general]
        cands.sort(key=lambda c: -c["p"])
        out_batches.append({
            "dir": direction, "cat": "трейдеры и общий сервис (весь список направления)",
            "n": len(dir_items),
            "qty_total": sum(i["qty"] if isinstance(i["qty"], (int, float)) else 0 for i in dir_items),
            "items": [dict({"name": i["name"][:160], "pn": i["pn"], "qty": i["qty"],
                            "unit": i["unit"], "model": i["model"][:60]},
                           **({"pr": prices[(i["pn"] or "").strip().upper()]}
                              if (i["pn"] or "").strip().upper() in prices else {}))
                      for i in dir_items[:400]],
            "sups": cands[:20],
            "covered": len(cands),
            "kind": "trade",
        })

    # склад: позиции, по которым проверкой подтверждён живой продавец с наличием
    stock = []
    for r in demand["rows"]:
        pn = (r["pn"] or "").strip().upper()
        p = prices.get(pn)
        if not p or p.get("verdict") not in ("confirmed", "price_differs"):
            continue
        if p.get("in_stock") != "yes":
            continue
        qty = r["qty"] if isinstance(r["qty"], (int, float)) else 0
        stock.append({
            "name": r["name"][:140], "pn": r["pn"], "qty": qty, "unit": r["unit"],
            "man": r["man"], "model": r["model"][:40], "dir": dir_of(r), "cat": r["cat"],
            "lo": p.get("usd_lo"), "hi": p.get("usd_hi"),
            "seller": p.get("seller", ""), "url": p.get("seller_url", ""),
            "stock_qty": p.get("stock_qty", ""), "lead": p.get("lead_time", ""),
            "note": p.get("check_note", "")[:160],
        })
    stock.sort(key=lambda s: -((s["hi"] or 0) * (s["qty"] or 0)))

    priced = sum(1 for r in demand["rows"] if (r["pn"] or "").strip().upper() in prices)
    verified = sum(1 for p in prices.values() if p.get("verdict") == "confirmed")

    tpl = (ROOT / "site" / "rfq.template.html").read_text(encoding="utf-8")
    tpl = tpl.replace("__BATCHES_JSON__", json.dumps(out_batches, ensure_ascii=False))
    tpl = tpl.replace("__STOCK_JSON__", json.dumps(stock, ensure_ascii=False))
    tpl = tpl.replace("__META_JSON__", json.dumps({
        "updated": demand["updated"], "source": demand["source"],
        "total": len(demand["rows"]),
        "priced": priced, "verified": verified, "stock": len(stock),
        "dirs": sorted({b["dir"] for b in out_batches}),
    }, ensure_ascii=False))
    out = ROOT / "public" / "rfq.html"
    out.write_text(tpl, encoding="utf-8")
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ), пачек: {len(out_batches)}")


if __name__ == "__main__":
    main()
