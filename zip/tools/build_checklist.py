#!/usr/bin/env python3
"""Чеклист сорсера по ЗИП SGT-400: мировой список поставщиков → 7 сегментов.

Вход:  zip/orders/sgt400_world_suppliers.csv  (135 компаний, веб-верификация)
       + 6 поставщиков, подтверждённых как контрагенты TFS (Веракрус) по компонентам.
Выход: zip/orders/sourcer_checklist_sgt400.csv  (одна компания = один сегмент,
       пустые колонки Статус/Дата/Ответ — их заполняет сорсер по ходу прозвона).

Сегментация — по функции (что компания реально делает для НАШИХ деталей:
камера сгорания MW21215M + горелка DUAL DLE MW22316B/01). Приоритет ★ —
сильный лид, физически достижимый из РФ (Китай/ОАЭ/Индия/Турция/Азия/ЛатАм);
• — релевантен, но западная юрисдикция (санкционный риск, заход через посредника).
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "orders" / "sgt400_world_suppliers.csv"
OUT = ROOT / "orders" / "sourcer_checklist_sgt400.csv"

SEGMENTS = {
    1: "СЕГМЕНТ 1 — Камеры сгорания и DLE-горелки (ПРЯМАЯ ЦЕЛЬ: MW21215M + MW22316B/01)",
    2: "СЕГМЕНТ 2 — Лопатки и сопловые аппараты (литьё суперсплавов)",
    3: "СЕГМЕНТ 3 — Покрытия горячей части (TBC / MCrAlY / диффузионные)",
    4: "СЕГМЕНТ 4 — Материалы (лист/пруток/поковки Hastelloy X, Nimonic 263)",
    5: "СЕГМЕНТ 5 — Независимые OEM-альтернативы и MRO горячего тракта",
    6: "СЕГМЕНТ 6 — Трейдеры, брокеры и каналы завоза (ОАЭ/Турция/Казахстан, б/у)",
    7: "СЕГМЕНТ 7 — РФ/СНГ: сервис, покрытия, реверс-инжиниринг",
}

# Юрисдикции, откуда завоз в РФ реально проходит (не под прямым скринингом)
REACHABLE = {"китай", "оаэ", "индия", "турция", "казахстан", "узбекистан",
             "малайзия", "индонезия", "иран", "сингапур", "южная корея",
             "таиланд", "аргентина", "бразилия", "мексика", "эквадор",
             "колумбия", "венесуэла", "юар", "саудовская аравия", "египет"}
WEST = {"сша", "великобритания", "германия", "италия", "франция", "канада",
        "швейцария", "нидерланды", "испания", "чехия", "румыния", "япония"}
CIS = {"россия", "беларусь"}

# Поставщики TFS (Веракрус) — идут в сегмент 1 с меткой TFS независимо от эвристики.
# «★ ТАМОЖНЯ» = связка подтверждена записями отгрузок (Panjiva/ImportKey/Volza,
# публичные страницы, см. zip/data/tfs_supply_chain.json);
# «★ ЛИД» = сильная гипотеза (компания подтверждена, связка с TFS — нет);
# «★ TFS» = профильный контрагент по открытым источникам, отгрузки не пробиты.
# Finework (Хунань) убран: проверка показала ветроэнергетику, не ГТУ — ложный след.
TFS = [
    dict(seg=1, star="★ ТАМОЖНЯ", comp="Liburdi Turbine Services", country="Канада (Dundas)",
         does="Сегменты сопловых аппаратов и лопатки, HS 8411999900, авиа Мехико в обе стороны — маршрут ремонта горячей части",
         site="liburdi.com", email="info@liburdi.com", phone="+1 905 689 0734"),
    dict(seg=1, star="★ ТАМОЖНЯ", comp="WBS Trading & Manufacturing", country="Сингапур (отгрузка из КНР)",
         does="КУПОЛ КАМЕРЫ СГОРАНИЯ (cupula), кольца дисков 1/2 ступени, уплотнительные пластины — 5 отгрузок",
         site="wbs.com.sg", email="sales@wbs.com.sg", phone="+65 6863 1978"),
    dict(seg=1, star="★ ТАМОЖНЯ", comp="Turbine Engineering Services Ltd", country="Великобритания (Першор)",
         does="Детали ГТУ, 5 отгрузок, происхождение UK/Нидерланды/Швейцария. Малая фирма 2-10 чел., сайт не отвечает",
         site="turbineengineeringservices.co.uk (не отвечает)", email="не найдено",
         phone="01386 555630 (справочники, не подтверждён)"),
    dict(seg=1, star="★ ТАМОЖНЯ", comp="Heinzmann UK Limited", country="Великобритания",
         does="Системы зажигания и свечи розжига камеры сгорания — 1 отгрузка",
         site="heinzmann-re.com", email="sales@heinzmann-re.com", phone="+44 1206 799556"),
    dict(seg=3, star="★ ТАМОЖНЯ", comp="Sifco Applied Surface Concepts", country="США (Огайо)",
         does="Селективное гальванопокрытие (brush plating) — восстановление корпусов и деталей горячей части",
         site="sifcoasc.com", email="не найдено", phone="не найдено"),
    dict(seg=1, star="★ ЛИД", comp="Gas Turbine Services A/S", country="Дания (Эсбьерг) + Линкольн UK",
         does="Крупнейший независимый сервис по линейке Siemens (Lincoln) SGT / Ruston. Дания — одно из 3 направлений экспорта TFS",
         site="gasturbineservices.com", email="gts@gasturbineservices.com", phone="+45 7546 8030 / +44 1522 842700"),
    dict(seg=1, star="★ ЛИД", comp="ITS — Industrial Turbine Services GmbH", country="Австрия (Лаакирхен)",
         does="Сканеры и датчики пламени ГТУ, детали ГТУ, АСУ (TMOS, FLAME SCANNER). Связь с TFS не подтверждена",
         site="turbineservices.at", email="contact@turbineservices.de", phone="+49 201 43728-0"),
    dict(seg=1, star="★ TFS", comp="DURAG Group / HEGWEIN", country="Германия",
         does="Запальники и системы розжига камер сгорания ГТУ (HEGWEIN M22D), патент ignition generator",
         site="durag.com", email="info@durag.com", phone="+49 40 554218-0"),
    dict(seg=1, star="★ TFS", comp="Sonkit (Shanghai)", country="Китай",
         does="Металлические уплотнения (C-ring) камеры сгорания / горячей части",
         site="sonkit.com", email="sales@sonkit.com", phone="+86 21 5439 9299"),
]

# --- ключевые слова функций (порядок = приоритет присвоения сегмента) ---
KW_COMBUST = re.compile(r"камер[аы]? сгорани|combust|горелк|burner|топливн\w* форсунк|"
                        r"fuel nozzle|жаров\w* труб|запальник|igniter|ignition|свеч\w* розжиг|liner")
KW_COAT = re.compile(r"покрыти|coating|напылени|\btbc\b|mcraly|термобарьер|плазм|"
                     r"sermetel|sermalon|hvof|диффузионн|термическ\w* напылени|surface")
KW_MAT = re.compile(r"металлург|металлоцентр|мастер-?сплав|шихтов|деформируем|прокат|"
                    r"полос[аы]|кузниц|ковк|поковк|forge|лист\w* спецсплав|сервисн\w* металлоцентр|"
                    r"пруток|производитель материал|haynes|special metals|vdm")
KW_CAST = re.compile(r"литейк|литьё|casting|монокристалл|направленн\w* кристаллизац|"
                     r"лопат|blade|сопл\w|vane|nozzle guide|прецизионн\w* лит")
KW_TRADER = re.compile(r"маркетплейс|surplus|брокер|аукцион|ликвидатор|объяв\w* площадк|"
                       r"онлайн-площадк|дилер|б-у|б/у|перепрода")
KW_MAKER = re.compile(r"завод|литейк|литьё|изготов|производ|мехобработ|мехобраб|"
                      r"кузниц|ковк|casting|manufactur|собственн\w* производ")
# «делает саму камеру/горелку» — глагол изготовления рядом с камерой сгорания
# (в описании релевантности), чтобы вытащить профильных изготовителей из общего MRO
KW_MAKES_COMBUST = re.compile(
    r"(производ\w*|изготов\w*|изготовитель|выпуск\w*|манифактур)[^.]{0,60}"
    r"(камер\w* сгорани|жаров\w* труб|горелк|combust|burner)"
    r"|(камер\w* сгорани|жаров\w* труб|горелк)[^.]{0,40}(производ|изготов|выпуск)"
    r"|производитель камер сгорани|изготовитель камер сгорани")


def classify(country, typ, rel):
    """Сегмент определяем по ТИПу компании (что она есть), не по «Релевантности»
    (там у всех упомянута камера сгорания SGT-400 — это описание нашей нужды).
    Слово «камера сгорания» в сегмент 1 засчитываем только если оно в ТИПе."""
    c = country.strip().lower().split("/")[0].split("(")[0].strip()
    t = typ.lower()               # первичный признак — тип
    # 1) РФ/СНГ — отдельный сегмент (домашний контур)
    if c in CIS or c == "узбекистан":
        return 7
    # 2) чистый трейдер/брокер/б-у без собственного производства → канал
    if KW_TRADER.search(t) and not KW_MAKER.search(t):
        return 6
    if ("трейдер" in t or "посредник" in t or "брокер" in t) and not KW_MAKER.search(t):
        return 6
    # 3) камера сгорания / горелка / форсунки — прямая цель (по ТИПу)
    if KW_COMBUST.search(t):
        return 1
    # 4) покрытия (по типу, если это профиль, а не литейка)
    if KW_COAT.search(t) and not KW_CAST.search(t):
        return 3
    # 5) материалы: металлургия / металлоцентр / кузница / лист-пруток
    if KW_MAT.search(t) and not KW_CAST.search(t):
        return 4
    # 6) литьё лопаток/сопел
    if KW_CAST.search(t):
        return 2
    # 7) прочее (независимый MRO / non-OEM / завод горячего тракта) →
    #    если тип прямо про камеру/горелку в релевантности И это профильный
    #    изготовитель — в 1, иначе общий сегмент 5
    return 5


def prio(country, seg, rel, high):
    c = country.strip().lower().split("/")[0].split("(")[0].strip()
    reachable = c in REACHABLE
    strong = high or seg in (1,)
    if reachable and strong:
        return "★"
    if reachable:
        return "•"
    # западная юрисдикция: звезда только материалам-первоисточникам
    if seg == 4 and high:
        return "•"
    return ""


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig"), delimiter=";"))
    buckets = {i: [] for i in SEGMENTS}
    for t in TFS:
        buckets[t["seg"]].append((t["star"], t["comp"], t["country"], t["does"],
                                  t["site"], t["email"], t["phone"]))
    for r in rows:
        comp = r["Компания"].strip()
        country = r["Страна"].strip()
        typ = r["Тип"].strip()
        rel = r["Релевантность SGT-400"].strip()
        high = r.get("Дов.", "").strip().lower() == "high"
        seg = classify(country, typ, rel)
        # промоушен в сегмент 1: остаточный MRO, но релевантность прямо говорит,
        # что компания ИЗГОТАВЛИВАЕТ саму камеру сгорания / горелку
        if seg == 5 and KW_MAKES_COMBUST.search(rel.lower()):
            seg = 1
        p = prio(country, seg, rel, high)
        does = rel[:140]
        buckets[seg].append((p, comp, country, does, r["Сайт"].strip(),
                             r["Email"].strip(), r["Телефон"].strip()))
    # сортировка внутри сегмента: ★ → • → пусто, затем по стране
    order = {"★ ТАМОЖНЯ": 0, "★ ЛИД": 1, "★ TFS": 2, "★": 3, "•": 4, "": 5}

    # дедуп по нормализованному имени (латиница/кириллица/CJK сохраняем),
    # оставляем лучшую строку: приоритетнее ★ и с контактом
    def dkey(comp):
        c = re.split(r"[/(]", comp.lower())[0]
        c = re.sub(r"\b(ltd|llc|inc|gmbh|srl|s\.?r\.?l|s\.?a|co|pvt|group|"
                   r"corporation|corp|ag|b\.?v|pte|sdn|bhd|s\.?p\.?a)\b", "", c)
        c = re.sub(r"[^0-9a-zа-я一-鿿]", "", c)
        return c[:16] if c else comp.lower()
    for i in SEGMENTS:
        seen, uniq = {}, []
        for row in sorted(buckets[i], key=lambda x: (order.get(x[0], 3), x[2])):
            k = dkey(row[1])
            if k in seen:
                continue  # уже взяли лучшую (сортировка выше ставит её первой)
            seen[k] = True
            uniq.append(row)
        buckets[i] = uniq
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Сегмент", "Приор", "Компания", "Страна", "Что делает",
                    "Сайт", "Email", "Телефон", "Статус", "Дата", "Ответ/КП"])
        for i in SEGMENTS:
            rs = sorted(buckets[i], key=lambda x: (order.get(x[0], 3), x[2]))
            for p, comp, country, does, site, email, phone in rs:
                w.writerow([SEGMENTS[i], p, comp, country, does, site, email, phone, "", "", ""])
    # сводка
    total = 0
    for i in SEGMENTS:
        rs = buckets[i]
        star = sum(1 for x in rs if x[0].startswith("★"))
        cont = sum(1 for x in rs if x[5] or x[6])
        total += len(rs)
        print(f"  С{i}: {len(rs):3} | ★{star:2} | контакт {cont:2} | {SEGMENTS[i][:44]}")
    print(f"ИТОГО: {total} компаний")


if __name__ == "__main__":
    main()
