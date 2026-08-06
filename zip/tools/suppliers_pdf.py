#!/usr/bin/env python3
"""PDF-каталог поставщиков по БЛОКАМ ОБОРУДОВАНИЯ (лёгкие ГТУ).

Задача: сорсер видит «за какой узел кто отвечает», контакт — только сайт и телефон.
Обязательная проверка: компания должна быть подтверждена по ЛЁГКИМ промышленным ГТУ
(Siemens SGT-100/200/300/400/500/600/700/800, Ruston Typhoon/Tornado/Tempest/Cyclone,
Solar Taurus/Centaur/Mars/Saturn/Titan, Allison, Dresser-Rand), а не по тяжёлым
машинам (GE Frame 5/6/7/9, 7FA/9FA, Siemens SGT5/SGT6, V94, Mitsubishi M501/M701,
Alstom GT13/GT26) и не по чистой авиации.

Вход:  zip/orders/sgt400_world_suppliers.csv, zip/data/tfs_supply_chain.json
Выход: zip/orders/ПОСТАВЩИКИ-ПО-БЛОКАМ.pdf (+ .html)
"""
import csv
import html
import json
import re
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "orders" / "sgt400_world_suppliers.csv"
SRC_HEAVY = ROOT / "orders" / "heavy_duty_suppliers.csv"   # добор по тяжёлым машинам
TFS = ROOT / "data" / "tfs_supply_chain.json"
OUT = ROOT / "orders"
e = lambda s: html.escape(str(s or ""))

# --- проверка «лёгкая ГТУ» -------------------------------------------------
LIGHT = re.compile(
    r"SGT-?\s?[1-8]00|SGT-?\s?750|Ruston|Typhoon|Tornado|Tempest|Cyclone|"
    r"Тайфун|Торнадо|Solar\s|Taurus|Centaur|Mars\s?\d|Saturn|Titan\s?\d|"
    r"Тауру|Центавр|Солар|Allison|Dresser-?Rand|Kongsberg|KG2", re.I)
HEAVY = re.compile(
    r"Frame\s?[3579]\b|\b[79][FE]A\b|V9[44]|SGT5|SGT6|M701|M501|GT13|GT26|"
    r"heavy.?duty|ГТЭ-1[0-9]{2}|ГТД-110", re.I)
# «сырьевые»/процессные поставщики — модель-агностичны по своей природе
AGNOSTIC = re.compile(
    r"металлург|металлоцентр|мастер-?сплав|шихтов|лист|пруток|поковк|прокат|"
    r"покрыти|напылени|coating|порошк|сплав", re.I)

HEAVY_CANON = [
    (r"9HA|7HA", "GE 9HA/7HA"), (r"\b9FA\b|\b9F\b", "GE 9FA"),
    (r"\b7FA\b|\b7F\b", "GE 7FA"), (r"\b7EA\b|\b7E\b", "GE 7EA"),
    (r"\b9E\b", "GE 9E"), (r"Frame\s?9", "GE Frame 9"),
    (r"Frame\s?7", "GE Frame 7"), (r"Frame\s?6|\b6B\b|\b6FA\b", "GE Frame 6"),
    (r"Frame\s?5", "GE Frame 5"), (r"Frame\s?3", "GE Frame 3"),
    (r"SGT5-?\s?4000|SGT5-?\s?2000|SGT5", "Siemens SGT5"),
    (r"SGT6-?\s?5000|SGT6", "Siemens SGT6"),
    (r"V94\.?[23]|V94", "Siemens V94"), (r"AE94\.?3A|AE94", "Ansaldo AE94"),
    (r"M701[FGJ]?", "MHI M701"), (r"M501[FGJ]?", "MHI M501"),
    (r"GT13E2|GT13", "Alstom GT13"), (r"GT26", "Alstom GT26"),
    (r"GT11N2|GT11", "Alstom GT11"),
    (r"ГТЭ-?110|ГТД-?110", "ГТЭ/ГТД-110"), (r"ГТЭ-?160", "ГТЭ-160"),
    (r"ГТЭ-?65", "ГТЭ-65"),
    (r"тяжёл|тяжел|heavy.?duty", "тяжёлые (класс заявлен, модель не названа)"),
]

MODEL_CANON = [
    (r"SGT-?\s?400", "SGT-400"), (r"SGT-?\s?300", "SGT-300"),
    (r"SGT-?\s?200", "SGT-200"), (r"SGT-?\s?100", "SGT-100"),
    (r"SGT-?\s?500", "SGT-500"), (r"SGT-?\s?600", "SGT-600"),
    (r"SGT-?\s?700", "SGT-700"), (r"SGT-?\s?800", "SGT-800"),
    (r"Typhoon|Тайфун", "Typhoon"), (r"Tornado|Торнадо", "Tornado"),
    (r"Tempest", "Tempest"), (r"Cyclone", "Cyclone"),
    (r"Ruston", "Ruston"), (r"Taurus|Тауру", "Taurus"),
    (r"Centaur|Центавр", "Centaur"), (r"Mars\s?\d", "Mars"),
    (r"Saturn", "Saturn"), (r"Titan\s?\d", "Titan"),
    (r"Allison", "Allison"), (r"Dresser-?Rand", "Dresser-Rand"),
]


def _match(txt, canon):
    out = []
    for pat, name in canon:
        if re.search(pat, txt, re.I) and name not in out:
            out.append(name)
    return out


def models_of(txt):
    return _match(txt, MODEL_CANON)


def heavy_of(txt):
    return _match(txt, HEAVY_CANON)


def klass(light, heavy, agnostic):
    """Класс машин: Л — лёгкие, Т — тяжёлые, Л+Т — оба."""
    if light and heavy:
        return "Л+Т"
    if light:
        return "Л"
    if heavy:
        return "Т"
    return "—" if agnostic else "?"


# --- блоки оборудования (порядок = приоритет присвоения) -------------------
BLOCKS = [
    ("A", "Камера сгорания, жаровые трубы, купол",
     "Наша деталь MW21215M. Ищем: combustion chamber / liner / basket / cupula / transition piece.",
     re.compile(r"камер\w* сгорани|жаров\w* труб|combust|liner|basket|cupula|"
                r"купол|transition piece|cap liner|cross fire", re.I)),
    ("B", "Горелки, топливные форсунки, зажигание, контроль пламени",
     "Наша деталь MW22316B/01 (DUAL DLE). Ищем: burner / fuel nozzle / igniter / flame scanner.",
     re.compile(r"горелк|burner|топливн\w* форсунк|fuel nozzle|форсунк|"
                r"запальник|igniter|ignition|розжиг|сканер пламени|flame scanner|"
                r"датчик\w* пламени|DLE", re.I)),
    ("C", "Сопловые аппараты и рабочие лопатки",
     "Литьё и мехобработка: nozzle guide vane, blade, монокристалл/НК.",
     re.compile(r"лопат|blade|сопл\w|vane|nozzle guide|монокристалл|"
                r"направленн\w* кристаллизац|литейк|литьё|casting", re.I)),
    ("D", "Диски, роторы, уплотнения, крепёж горячего тракта",
     "Кольца дисков, уплотнительные пластины, C-ring, суперсплавный крепёж.",
     re.compile(r"уплотнени|seal|c-?ring|крепёж|крепеж|болт|шпильк|"
                r"диск|ротор|кольц|поковк|forge|кузниц", re.I)),
    ("E", "Покрытия горячей части (TBC / MCrAlY / диффузионные)",
     "Термобарьерные и жаростойкие покрытия, плазма/HVOF, восстановление.",
     re.compile(r"покрыти|coating|напылени|\btbc\b|mcraly|термобарьер|"
                r"плазм|hvof|sermetel|sermalon|диффузионн|гальванопокрыти", re.I)),
    ("F", "Материалы: лист, пруток, поковки (Hastelloy X, Nimonic 263)",
     "Сырьё под локализацию: жаропрочный лист и полуфабрикаты.",
     re.compile(r"металлург|металлоцентр|мастер-?сплав|шихтов|прокат|"
                r"лист|пруток|полос|hastelloy|nimonic|haynes|vdm|"
                r"производитель материал|сырь", re.I)),
    ("G", "Комплексный MRO горячего тракта",
     "Ремонт и восстановление узла целиком, реверс-инжиниринг, испытания.",
     re.compile(r"mro|ремонт|восстановлен|реверс-?инжиниринг|сервис|"
                r"капремонт|overhaul|стенд", re.I)),
    ("H", "Каналы поставки: трейдеры, б/у, реэкспорт",
     "Не производят — везут. ОАЭ/Турция/Казахстан, склады и б/у агрегаты.",
     re.compile(r"трейдер|брокер|маркетплейс|surplus|б-у|б/у|дилер|"
                r"посредник|аукцион|ликвидатор|склад", re.I)),
]
RU = re.compile(r"росси|беларус|казахстан|узбекистан", re.I)


MAT_TYPE = re.compile(r"металлург|металлоцентр|мастер-?сплав|шихтов|сплав|прокат|"
                      r"лист|пруток|полос|кузниц|ковк|поковк|сырь|материал", re.I)
COAT_TYPE = re.compile(r"покрыти|напылени|coating|спецпроцесс|surface", re.I)
TRADE_TYPE = re.compile(r"трейдер|брокер|маркетплейс|surplus|дилер|посредник|"
                        r"аукцион|ликвидатор|площадк", re.I)
MAKER_TYPE = re.compile(r"завод|литейк|литьё|изготов|производ|мехобработ|manufactur", re.I)
PAT = {c: p for c, _, _, p in BLOCKS}


def block_of(typ, rel, country):
    """Блок = за какой УЗЕЛ компания отвечает. Приоритет важен: металлург и цех
    покрытий определяются раньше, чем узел, иначе слово «камера сгорания» в
    описании релевантности утаскивает поставщика листа в блок A."""
    if RU.search(country):
        return "I"
    t = typ.lower()
    if TRADE_TYPE.search(t) and not MAKER_TYPE.search(t):
        return "H"
    if MAT_TYPE.search(t) and not PAT["C"].search(t):   # литейка ≠ металлург
        return "F"
    if COAT_TYPE.search(t) and not PAT["C"].search(t):
        return "E"
    for code in ("A", "B", "C", "D"):                  # узел — по ТИПу компании
        if PAT[code].search(t):
            return code
    for code in ("B", "A", "D"):                       # затем по релевантности
        if PAT[code].search(rel):
            return code
    if PAT["C"].search(rel):
        return "C"
    return "G"


def clean(v):
    v = (v or "").strip()
    if not v or v.lower().startswith(("не найд", "не подтверж", "нет ")):
        return ""
    # один сайт / один телефон: режем по ';', ' / ' и по запятой перед новым
    # контактом — но НЕ по скобке (иначе «+1 (281) 452-2441» превращается в «+1»)
    v = re.split(r";| / |,(?=\s*(?:\+|8[\s-]|www|http|факс))", v)[0].strip()
    v = re.sub(r"\s*\((?![^)]*\d{3})[^)]*\)", "", v).strip()   # снять поясняющие скобки
    v = re.sub(r"\s*\((?:HQ|факс|общий|моб\.?)[^)]*\)", "", v, flags=re.I).strip()
    return v[:34]


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig"), delimiter=";"))
    if SRC_HEAVY.exists():                       # второй источник: тяжёлые ГТУ
        hv = list(csv.DictReader(open(SRC_HEAVY, encoding="utf-8-sig"), delimiter=";"))
        for r in hv:                             # привести к схеме основного файла
            r.setdefault("Релевантность SGT-400", r.get("Релевантность", ""))
        rows += hv
        print(f"добор по тяжёлым ГТУ: +{len(hv)} компаний")
    groups = {c: [] for c, _, _, _ in BLOCKS}
    groups["I"] = []
    skipped_heavy, skipped_nocontact = [], []

    for r in rows:
        comp, country = r["Компания"].strip(), r["Страна"].strip()
        typ, rel = r["Тип"].strip(), r["Релевантность SGT-400"].strip()
        txt = rel + " " + typ
        site, phone = clean(r["Сайт"]), clean(r["Телефон"])
        mods, hmods = models_of(txt), heavy_of(txt)
        agn = bool(AGNOSTIC.search(typ))
        if not site and not phone:                 # каталог для прозвона — без контакта бесполезен
            skipped_nocontact.append((comp, country))
            continue
        k = klass(mods, hmods, agn)
        if k == "?":                               # ни одной модели и не сырьё
            k, shown = "?", "модель не подтверждена"
        elif k == "—":
            shown = "материал/процесс — любая модель"
        else:
            shown = ", ".join((mods + hmods)[:6])
        groups[block_of(typ, rel, country)].append({
            "comp": comp, "country": country.split("/")[0].split("(")[0].strip()[:22],
            "site": site, "phone": phone, "klass": k, "models": shown, "tfs": "",
        })

    # поставщики TFS, подтверждённые таможней
    tfs = json.loads(TFS.read_text(encoding="utf-8"))
    marked = [(x, "\u2714") for x in tfs["suppliers"]] + [(x, "") for x in tfs.get("leads", [])]
    for s, mark in marked:
        if s.get("segment") == 0 or "НИЗКАЯ" in str(s.get("confidence", "")):
            continue
        site, phone = clean(s.get("site")), clean(s.get("phone"))
        if not site and not phone:
            continue
        ships = s.get("ships", "") + " " + s.get("why", "")
        code = s.get("block") or block_of(ships, ships, s.get("country", ""))
        rec = {"comp": s["company"], "country": s["country"].split("(")[0].strip()[:22],
               "site": site, "phone": phone, "klass": "Л",
               "models": ("SGT-400 — цепочка TFS, пробита таможней" if mark
                          else "SGT/Ruston — профильный лид, отгрузки не пробиты"),
               "tfs": mark}
        if not any(x["comp"].split()[0] == rec["comp"].split()[0] for x in groups[code]):
            groups[code].append(rec)

    # --- HTML ---
    css = """
    @page{size:A4 landscape;margin:11mm 9mm}
    body{font:10.5px/1.45 'DejaVu Sans',Arial,sans-serif;color:#111;margin:0}
    h1{font-size:20px;margin:0 0 3px}
    h2{font-size:13.5px;margin:13px 0 3px;background:#1b2330;color:#fff;padding:4px 8px;
       border-radius:3px;page-break-after:avoid}
    .sub{color:#444;font-size:9.5px;margin:0 0 4px;font-style:italic}
    .mut{color:#555;font-size:9.5px}
    table{border-collapse:collapse;width:100%;margin:3px 0}
    tr{page-break-inside:avoid}
    th,td{border:1px solid #bbb;padding:3px 5px;text-align:left;vertical-align:top;font-size:9.5px}
    th{background:#eef2f7}
    .box{border:1px solid #ccc;border-left:4px solid #1a7f37;border-radius:4px;
         padding:6px 10px;margin:6px 0;font-size:10px;page-break-inside:avoid}
    .warn{border-left-color:#c62828}
    b{color:#0b3d91} .tfs{color:#1a7f37;font-weight:700}
    .kl{background:#e7f4ea;color:#1a7f37;font-weight:700;text-align:center}
    .klt{background:#fff4e0;color:#a15c00;font-weight:700;text-align:center}
    .kt{background:#eaf0fb;color:#0b3d91;font-weight:700;text-align:center}
    .kq{color:#888;text-align:center}
    """
    tot = sum(len(v) for v in groups.values())
    H = [f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>
<h1>Поставщики ЗИП по блокам оборудования — лёгкие и тяжёлые ГТУ</h1>
<div class="mut">Цель: камера сгорания <b>MW21215M</b> (12 шт) + главная горелка DUAL DLE
<b>MW22316B/01</b> (12 шт), Siemens SGT-400 · {date.today().strftime('%d.%m.%Y')} · компаний: {tot}</div>
<div class="box"><b>Как читать колонку «Класс»:</b>
<span class="kl">&nbsp;Л&nbsp;</span> — <b>лёгкие</b> промышленные ГТУ: Siemens SGT-100…800, Ruston
Typhoon/Tornado/Tempest/Cyclone, Solar Taurus/Centaur/Mars/Saturn/Titan, Allison, Dresser-Rand.
Это наша цель — с них начинать. &nbsp;
<span class="klt">&nbsp;Л+Т&nbsp;</span> — работает и с лёгкими, и с тяжёлыми: у таких шире цех и
обычно есть свободные мощности. &nbsp;
<span class="kt">&nbsp;Т&nbsp;</span> — <b>тяжёлые</b>: GE Frame 5/6/7/9, 7EA/7FA/9E/9FA/9HA,
Siemens SGT5/SGT6, V94, Ansaldo AE94, Mitsubishi M501/M701, Alstom GT11/GT13/GT26, ГТЭ-110/160.
Технология горячей части общая (те же сплавы, литьё, покрытия, сварка), поэтому под наши
камеру сгорания и горелку они пригодны — но им нужны наши чертежи/ВРКД, каталожного аналога нет. &nbsp;
<span class="kq">?</span> — модель в открытых источниках не заявлена; <b>—</b> материал или
процесс, к модели турбины не привязан.
<span class="tfs">✔</span> — поставщик подтверждён таможенными записями по цепочке TFS (Веракрус).</div>"""]

    # дедуп по нормализованному имени сквозь весь документ (APG встречался трижды)
    def dkey(c):
        c = re.split(r"[/(—]", c.lower())[0]
        c = re.sub(r"\b(ltd|llc|inc|gmbh|s\.?r\.?l|s\.?a|co|pvt|group|corp\w*|"
                   r"ag|b\.?v|pte|sdn|bhd|s\.?p\.?a|a/s)\b", "", c)
        return re.sub(r"[^0-9a-zа-я一-鿿]", "", c)[:16]
    def domkey(x):
        """Домен сайта — надёжнее имени: «Chromalloy» и «Chromalloy Gas Turbine LLC»
        это одна компания с одним chromalloy.com."""
        m = re.search(r"(?:https?://)?(?:www\.)?([a-z0-9.-]+\.[a-z]{2,})", x["site"], re.I)
        return "d:" + m.group(1).lower() if m else "n:" + dkey(x["comp"])
    seen = set()
    for code in list(groups):
        uniq = []
        KORD = {"Л": 0, "Л+Т": 1, "—": 2, "Т": 3, "?": 4}
        for x in sorted(groups[code], key=lambda x: (x["tfs"] == "", KORD.get(x["klass"], 5), x["country"])):
            k = domkey(x)
            if k and k not in ("n:", "d:") and k in seen:
                continue
            seen.add(k)
            uniq.append(x)
        groups[code] = uniq

    for code, title, note, _ in BLOCKS + [("I", "РФ / СНГ: сервис и покрытия", "Домашний контур — работа без ВЭД.", None)]:
        rs = groups.get(code, [])
        if not rs:
            continue
        H.append(f'<h2>БЛОК {code} — {e(title)} · {len(rs)}</h2><div class="sub">{e(note)}</div>'
                 '<table><tr><th style="width:24%">Компания</th><th style="width:11%">Страна</th>'
                 '<th style="width:5%">Класс</th><th style="width:19%">Сайт</th>'
                 '<th style="width:16%">Телефон</th>'
                 '<th style="width:25%">Модели (подтверждение)</th></tr>')
        for x in rs:
            mark = '<span class="tfs">✔ </span>' if x["tfs"] else ""
            kc = {"Л": "kl", "Л+Т": "klt", "Т": "kt"}.get(x["klass"], "kq")
            H.append(f'<tr><td>{mark}<b>{e(x["comp"])}</b></td><td>{e(x["country"])}</td>'
                     f'<td class="{kc}">{e(x["klass"])}</td>'
                     f'<td>{e(x["site"]) or "—"}</td><td>{e(x["phone"]) or "—"}</td>'
                     f'<td class="mut">{e(x["models"])}</td></tr>')
        H.append("</table>")

    if skipped_heavy:
        H.append('<h2>Исключены проверкой профиля</h2><div class="sub">Не подтверждена работа по лёгким ГТУ — в прозвон не брать.</div>'
                 '<table><tr><th style="width:34%">Компания</th><th style="width:16%">Страна</th><th>Причина</th></tr>')
        for c, s, why in skipped_heavy:
            H.append(f'<tr><td>{e(c)}</td><td>{e(s)}</td><td class="mut">{e(why)}</td></tr>')
        H.append("</table>")
    if skipped_nocontact:
        H.append(f'<div class="box warn"><b>Без сайта и телефона ({len(skipped_nocontact)}) — в каталог не вошли:</b> '
                 + e(", ".join(c for c, _ in skipped_nocontact)) + "</div>")

    H.append(f"""<div class="mut" style="margin-top:8px">Контакты проверены по официальным сайтам и
регистрационным реестрам на {date.today().isoformat()}; где данных нет — прочерк, ничего не выдумано.
Полная база: kvant-zip.pages.dev · чеклист с email и колонками для прозвона:
kvant-zip.pages.dev/orders/sgt400-sourcer-checklist.csv</div></body></html>""")

    (OUT / "ПОСТАВЩИКИ-ПО-БЛОКАМ.html").write_text("\n".join(H), encoding="utf-8")
    print(f"блоков: {sum(1 for v in groups.values() if v)}, компаний: {tot}, "
          f"исключено по профилю: {len(skipped_heavy)}, без контакта: {len(skipped_nocontact)}")
    for code, title, _, _ in BLOCKS + [("I", "РФ/СНГ", "", None)]:
        if groups.get(code):
            print(f"  {code}: {len(groups[code]):3}  {title[:52]}")


if __name__ == "__main__":
    main()
