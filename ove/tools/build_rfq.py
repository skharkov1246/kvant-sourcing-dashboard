#!/usr/bin/env python3
"""Пакет запросов бюджетных ТКП (RFQ) проекта ОВЭ-75 → ove/public/docs/rfq/.

По каждой из закупочных позиций ove/data/suppliers.json (positions) собирается
ove75-rfq-<NN>-<слаг>.docx: письмо-запрос бюджетного ТКП на русском, ниже —
английский перевод (один документ, две части), затем техническое приложение —
таблицы требований с параметрами соответствующих позиций ove/data/equipment.json.
Плюс ove75-rfq-index.docx — внутренний реестр запросов (позиция, классы
оборудования, кандидаты-поставщики из suppliers.json по позиции).

В письмах НЕТ цен и НЕТ имён поставщиков (внутренние комментарии позиции
переписаны/убраны через SPEC_OVERRIDE/ASK_OVERRIDE; после сборки все письма
автоматически проверяются на вхождение имён кандидатов). Поля адресата, дат и
контактов — плейсхолдеры [____], заполняются перед отправкой.

Собрано по образцу build_apply_docs.py, переиспользует OOXML-хелперы
build_docx.py; внешних зависимостей нет. Запуск: python3 ove/tools/build_rfq.py
"""
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_docx as bd  # noqa: E402 — OOXML-хелперы (run/p/cell/table + части пакета)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTDIR = ROOT / "public" / "docs" / "rfq"
TODAY = date.today().strftime("%d.%m.%Y")

PH = "[____]"                    # плейсхолдер полей адресата/дат/контактов
GRAY, DGRAY, RED = "888888", "444444", "B22222"
HEAD_SHADE = "EFEFEF"
W_PORT, W_LAND = 9638, 14570     # полезная ширина A4 портрет/альбом (поля 1134)
PAGE_BREAK = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'

LOT_NAME = {0: "вне лотов (стыки границ)", 1: "Лот №1 · Цех обжига",
            2: "Лот №2 · Участок купоросов", 3: "Лот №3 · Электроэкстракция",
            4: "Лот №4 · Склад готовой продукции", 9: "все лоты (АСУ/КИП)"}

# ------------------------------------------------------------------ данные

# Подбор позиций equipment.json к закупочной позиции suppliers.json.
# Ключ — "<cls>:<lot>" (пара уникальна); селектор: лот + regex по началу
# наименования либо целая технологическая area. Несовпадение селектора с
# данными валит сборку — чтобы приложение не оказалось молча пустым.
EQ_SELECT = {
    "roaster:1": [(1, r"^Печь кипящего слоя"), (1, r"^Шнековый забрасыватель"),
                  (1, r"^Воздуходувка"), (1, r"^Дроссельный затвор")],
    "whb:1": [(1, r"^Котёл-утилизатор")],
    "esp:1": [(1, r"^Сухой электрофильтр")],
    "cyclone:1": [(1, r"^Одиночный циклон")],
    "fan:1": [(1, r"^Дымосос$")],
    "baghouse:1": [(1, r"^Рукавный фильтр"), (1, r"^Дымосос\(ы\) аспирации")],
    "vacfilter:1": [(1, r"^Барабанные вакуум-фильтры"), (1, r"^Ёмкость приёма фильтрата")],
    "conveyor:1": [(1, ("area", "Шихтоподготовка"))],
    "burner:1": [(1, r"^Мазутная горелка")],
    "evaporator:2": [(2, r"^Испаритель")],
    "centrifuge:2": [(2, r"^Пульсирующая")],
    "hx:2": [(2, r"^Паровой эжектор"), (2, r"^Барометрический конденсатор"),
             (2, r"^Пароэжекторный блок")],
    "cell:3": [(3, r"^Электролизная ванна"), (3, r"^Аспирационные укрытия")],
    "anode:3": [(3, r"^Аноды")],
    "cathode:3": [(3, r"^Катодные матрицы")],
    "busbar:3": [(3, r"^Ошиновка")],
    "stripper:3": [(3, r"^Роботизированная катодосдирочная"),
                   (3, r"^Оборудование формирования пакетов"),
                   (3, r"^Автоматизированная транспортировка")],
    "crane:3": [(3, r"^Специальные автоматические краны")],
    "rectifier:3": [(3, r"^Выпрямители")],
    "starter:3": [(3, r"^Оборудование автоматической изоляции")],
    "scale:4": [(4, r"^Оборудование коммерческого учёта")],
    "filterpress:0": [(0, r"^Мембранный фильтр-пресс")],
    "thickener:0": [(0, r"^Сгуститель")],
    "agitator:0": [(0, r"реактор")],
    "hx:0": [(0, r"^Теплообменник"), (3, r"^Холодильник электролита")],
    "plc:9": [],    # перечни АСУ — по Приложению №5 ТЗ, в equipment.json позиций нет
    "instr:9": [],  # то же по КИПиА
}

# Английское наименование предмета запроса для английской части письма.
EN_NAME = {
    "roaster:1": "Fluidized-bed roasting furnace, 10–16 t/h, oxygen-enriched blast",
    "whb:1": "Waste-heat boiler, up to 30,000 Nm³/h, steam 1.9 MPa / 375 °C",
    "esp:1": "Dry electrostatic precipitator, up to 57,600 m³/h (2 off)",
    "cyclone:1": "Single cyclone, up to 32,000 m³/h",
    "fan:1": "Flue-gas exhaust fans, up to 78,000 m³/h, 400 kW / 6 kV (2 off)",
    "baghouse:1": "Bag filters for dust aspiration with stack fans",
    "vacfilter:1": "Drum vacuum filters for copper concentrate slurry",
    "conveyor:1": "Charge preparation equipment: feeders, belt dosers, screw mixer, belt conveyors",
    "burner:1": "Fuel-oil burners (blast preheating and furnace heat-up)",
    "evaporator:2": "Evaporators, stages I and II (4 off), 904L",
    "centrifuge:2": "Pusher-type filtering centrifuges (4 off), 904L",
    "hx:2": "Barometric condensers, steam ejectors and steam-jet vacuum unit",
    "cell:3": "Polymer-concrete electrowinning cells, 180 off",
    "anode:3": "Lead-alloy anodes, approx. 15,300 off",
    "cathode:3": "Stainless-steel permanent cathode blanks, approx. 15,120 off",
    "busbar:3": "Cell busbar system with double contact",
    "stripper:3": "Robotic cathode stripping machine, 120 cathodes/h",
    "crane:3": "Special automatic tankhouse cranes (2 off)",
    "rectifier:3": "Rectifiers 250 V / 47 kA (3 off)",
    "starter:3": "Cathode blank preparation line",
    "scale:4": "Certified weighing equipment for commercial metering",
    "filterpress:0": "Membrane filter presses for thickener sands",
    "thickener:0": "Thickener D = 15 m",
    "agitator:0": "Leaching cascade reactors with agitators, Hastelloy G-35 / 904L",
    "hx:0": "Plate heat exchangers (electrolyte heating and cooling)",
    "plc:9": "PLC and SCADA system (all process areas)",
    "instr:9": "Field instrumentation: pressure, flow, level, temperature, density, gas analysis",
}

# Санитизация текстов позиции для ИСХОДЯЩИХ писем: внутренние комментарии и
# упоминания поставщиков/брендов кандидатов переписаны нейтрально; None —
# строку в письмо не включать. В реестре (внутреннем) остаются исходные тексты.
SPEC_OVERRIDE = {
    "plc:9": "Средний уровень — ПЛК по утверждённому перечню Приложения №5 к ТЗ; верхний "
             "уровень — SCADA на ОС Linux с горячим резервированием; запас 30 % по каналам "
             "ввода-вывода; SNMP-мониторинг",
}
ASK_OVERRIDE = {
    "cell:3": "Подтвердить конструкцию и материал ванны (монолитный винилэфирный композит), "
              "интеграцию бортового отсоса и легкосъёмного колпака",
    "busbar:3": "Подтвердить равномерность распределения тока по ваннам, падение напряжения "
                "на контактах двойной контактной системы и совместимость с изоляторами ванн",
    "evaporator:2": "Обязательно указать материал контактных частей (904L) и напряжённость "
                    "по соковому пару",
    "starter:3": "Указать, входят ли правка и обрамление катодных основ в комплект поставки "
                 "катодосдирочной машины или предлагаются отдельной линией",
    "agitator:0": "По требованию исходных данных материал 316L не допускается — только "
                  "Hastelloy G-35 или аналог; допустимо 904L",
    "filterpress:0": None,   # исходный комментарий — внутренний, с именем кандидата
    "rectifier:3": None,     # исходный комментарий — внутренняя заметка о границе БИ
}

# Поля отдельных позиций equipment.json, переписанные для исходящих писем
# (в исходных данных — бренд кандидата и внутренние ссылки на проект-аналог).
EQ_FIELD_OVERRIDE = {
    ("Электролизная ванна", "model"): "Полимербетонные ванны из монолитного винилэфирного "
                                      "композита; соответствие конструкции подтверждается в ТКП",
}

# Обобщённые слова из названий компаний — не идентифицируют поставщика,
# исключаются из автопроверки «в письмах нет имён кандидатов».
STOP_TOKENS = {
    # английские родовые
    "china", "engineering", "corp", "corporation", "group", "company", "limited",
    "international", "industries", "industrial", "industry", "machinery", "machine",
    "heavy", "technology", "technologies", "equipment", "environmental", "protection",
    "science", "institute", "research", "design", "metallurgy", "metallurgical",
    "nonferrous", "foreign", "construction", "energy", "power", "clean", "general",
    "crane", "cranes", "boiler", "filtration", "filter", "anodes", "copper", "mining",
    "metals", "metal", "refined", "separation", "steam", "saving", "trade",
    "composites", "convertors", "sensing", "graphite", "fluid", "manufacturing",
    "pharmaceutical", "factory", "mine", "intelligent", "electricals", "electric",
    "solutions", "systems", "system", "pump", "pumps", "valve", "valves", "steel",
    "automation", "instrument", "instruments", "electronics", "measurement",
    "measuring", "technic", "technics", "scales", "weighing", "exchange",
    "works", "plant", "heat", "plate", "exchanger", "special", "flow", "scada",
    "oxygen", "blower",
    # русские родовые
    "системы", "система", "группа", "промышленная", "инжиниринг", "автоматизация",
    "метрология", "завод", "заводы", "научно", "производственное", "предприятие",
    "компания", "техника", "приборы", "приборостроение", "машиностроение",
    "машиностроительный", "электро", "рынок",
}


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def key_of(pos) -> str:
    return f"{pos['cls']}:{pos['lot']}"


def select_equipment(eq_items, key):
    """Позиции equipment.json по селекторам EQ_SELECT[key]; порядок стабильный."""
    picked, seen = [], set()
    for lot, sel in EQ_SELECT[key]:
        if isinstance(sel, tuple):  # ("area", <имя технологической области>)
            hits = [i for i in eq_items if i.get("lot") == lot and i.get("area") == sel[1]]
        else:
            rx = re.compile(sel, re.I)
            hits = [i for i in eq_items if i.get("lot") == lot and rx.search(i.get("name", ""))]
        if not hits:
            raise RuntimeError(f"EQ_SELECT[{key}]: селектор {sel!r} (лот {lot}) "
                               f"не нашёл позиций в equipment.json")
        for it in hits:
            mark = id(it)
            if mark not in seen:
                seen.add(mark)
                picked.append(it)
    return picked


# ------------------------------------------------------------- OOXML-обвязка

def document_xml(body: list, *, landscape=False) -> str:
    """Собранный word/document.xml — письма сначала проверяются, потом пишутся."""
    if landscape:
        sect = ('<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
                ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    else:
        sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
                ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>' + "".join(body) + sect + '</w:body></w:document>')


def save_docx(name: str, doc_xml: str) -> Path:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", bd.CONTENT_TYPES)
        z.writestr("_rels/.rels", bd.RELS)
        z.writestr("word/_rels/document.xml.rels", bd.DOC_RELS)
        z.writestr("word/styles.xml", bd.STYLES)
        z.writestr("word/document.xml", doc_xml)
    return out


def h1(t):  return bd.p([bd.run(t, bold=True, size=30)])
def h2(t):  return bd.p([bd.run(t, bold=True, size=24)], style="Heading2")
def pp(t, size=20, color=None, bold=False):
    return bd.p([bd.run(t, size=size, color=color, bold=bold)])


def kv_table(rows, w_label=3038, w_value=6600):
    """Двухколоночная таблица «Параметр | Требование» с шапкой-заливкой слева."""
    xml_rows = []
    for label, value in rows:
        xml_rows.append([
            bd.cell([bd.p([bd.run(label, bold=True, size=18)])], w_label, shade=HEAD_SHADE),
            bd.cell([bd.p([bd.run(str(value), size=18)])], w_value),
        ])
    return bd.table(xml_rows, [w_label, w_value])


# --------------------------------------------------------------- тексты писем

RU_BULLETS = [
    "бюджетную цену — отдельно на условиях EXW (склад изготовителя) и DDP г. Мончегорск, "
    "Российская Федерация (Incoterms 2020), с указанием валюты и срока действия предложения;",
    "срок изготовления и ориентировочный срок поставки (в неделях от даты заказа / авансового платежа);",
    "массогабаритные характеристики: габариты и массу единицы оборудования и крупнейших "
    "поставочных блоков (для проработки логистики);",
    "референсы аналогичных поставок: продукт, рабочая среда и параметры, год поставки, страна;",
    "материал частей, контактирующих с рабочей средой, и комплектность поставки (КИП, ЗИП, документация);",
    "потребность в энергоресурсах и требования к монтажу (при наличии данных).",
]
EN_BULLETS = [
    "budget price quoted separately on EXW (manufacturer's works) and DDP Monchegorsk, "
    "Russian Federation terms (Incoterms 2020), stating the currency and the validity period of the offer;",
    "manufacturing lead time and estimated delivery time (weeks from order / advance payment);",
    "overall dimensions and weights of the equipment and of the largest shipping blocks;",
    "reference list of similar supplies: product, process duty and parameters, year of supply, country;",
    "materials of parts in contact with the process medium, and the scope of supply "
    "(instrumentation, spare parts, documentation);",
    "utility consumption and installation requirements (if available).",
]

RU_CONF = ("Конфиденциальность. Настоящий запрос и приложенные к нему технические материалы "
           "предоставляются исключительно для подготовки ТКП, являются конфиденциальной "
           "информацией и не подлежат раскрытию третьим лицам, копированию или публикации "
           "без предварительного письменного согласия ООО «КВАНТ». Направление ТКП не создаёт "
           "для сторон юридических обязательств.")
EN_CONF = ("Confidentiality. This request and the attached technical materials are provided "
           "solely for the preparation of the quotation, are confidential, and shall not be "
           "disclosed to third parties, copied or published without the prior written consent "
           "of KVANT LLC. Submission of a quotation creates no legal obligations for either party.")


def letter_ru(no: str, pos, spec_txt) -> list:
    name = pos["name"]
    body = [
        pp("НА БЛАНКЕ ООО «КВАНТ» · черновик рассылки: заполнить поля [____] перед отправкой · "
           "ЭТУ СТРОКУ УДАЛИТЬ", 18, GRAY),
        h1(f"Запрос бюджетного технико-коммерческого предложения № {no}"),
        pp(f"Исх. № {PH} от {PH}"),
        pp(f"Кому: {PH} (наименование организации, адрес)"),
        pp(f"Вниманию: {PH} (контактное лицо)"),
        pp(f"Предмет запроса: {name}.", bold=True),
        pp("ООО «КВАНТ» (Российская Федерация) в рамках Базового инжиниринга проекта "
           "строительства медного завода — комплекса «обжиг – выщелачивание – электроэкстракция» "
           "производительностью 75 000 т катодной меди в год — просит представить бюджетное "
           "технико-коммерческое предложение (ТКП) по указанной выше позиции. Площадка "
           "строительства — г. Мончегорск, Мурманская область, Российская Федерация."),
    ]
    if spec_txt:
        body.append(pp(f"Краткая характеристика позиции: {spec_txt}. Технические требования — "
                       f"в Приложении 1 к настоящему запросу."))
    else:
        body.append(pp("Технические требования — в Приложении 1 к настоящему запросу."))
    body.append(pp("Просим включить в ТКП:", bold=True))
    body += [pp(f"{i}. {t}") for i, t in enumerate(RU_BULLETS, 1)]
    body += [
        pp("Запрос носит бюджетный характер и относится к стадии Базового инжиниринга (FEED): "
           "он не является офертой и не влечёт обязательств для сторон; уточнённые опросные "
           "листы будут направлены на следующей стадии проекта."),
        pp(f"Срок ответа: просим направить ТКП в течение 3 (трёх) недель с даты получения "
           f"настоящего запроса на адрес {PH}. Просим подтвердить получение запроса.", bold=True),
        pp(RU_CONF, 19),
        pp(f"Контактное лицо: {PH} (Ф.И.О., должность, телефон, e-mail)."),
        pp("Приложение 1: Технические требования по позиции запроса."),
        pp(""),
        pp(f"{PH} (должность)    {PH} (Ф.И.О.)    {PH} (подпись)"),
    ]
    return body


def letter_en(no_en: str, key: str) -> list:
    name_en = EN_NAME[key]
    body = [
        h1(f"Request for Budgetary Technical and Commercial Proposal No. {no_en}"),
        pp("English translation of the letter above / Английский перевод письма выше", 18, GRAY),
        pp(f"Ref. No. {PH} dated {PH}"),
        pp(f"To: {PH} (company name, address)"),
        pp(f"Attn: {PH} (contact person)"),
        pp(f"Subject: {name_en}.", bold=True),
        pp("KVANT LLC (Russian Federation), within the Basic Engineering (FEED) stage of the "
           "construction project of a copper production plant — a roasting–leaching–electrowinning "
           "complex with a capacity of 75,000 tonnes of copper cathodes per year — kindly requests "
           "your budgetary technical and commercial proposal (budgetary quotation) for the item "
           "above. Project site: Monchegorsk, Murmansk Region, Russian Federation."),
        pp("Technical requirements are given in Annex 1 to this request (in Russian; "
           "clarifications in English are available upon request)."),
        pp("Please include in your quotation:", bold=True),
    ]
    body += [pp(f"{i}. {t}") for i, t in enumerate(EN_BULLETS, 1)]
    body += [
        pp("This is a budgetary enquiry at the Basic Engineering (FEED) stage: it does not "
           "constitute an offer and creates no obligations for either party; detailed technical "
           "data sheets will be issued at the next project stage."),
        pp(f"Reply deadline: we kindly ask you to submit your quotation within 3 (three) weeks "
           f"from receipt of this request to {PH}. Please confirm receipt of this request.", bold=True),
        pp(EN_CONF, 19),
        pp(f"Contact person: {PH} (name, position, phone, e-mail)."),
        pp("Annex 1: Technical requirements."),
        pp(""),
        pp(f"{PH} (position)    {PH} (name)    {PH} (signature)"),
    ]
    return body


EQ_ROWS = [("Тип / модель — Type / model", "model"),
           ("Назначение — Duty", "purpose"),
           ("Основные параметры — Key parameters", "param"),
           ("Количество — Quantity", "qty"),
           ("Материальное исполнение — Materials", "material"),
           ("Габариты, НЗП — Dimensions, hold-up", "dims"),
           ("Электропотребление — Power", "power"),
           ("Дополнительные требования — Additional requirements", "note")]


def annex(no: str, no_en: str, pos, key: str, eqs, spec_txt, ask_txt) -> list:
    body = [
        h1("Приложение 1 / Annex 1. Технические требования — Technical requirements"),
        pp(f"К запросу № {no} / To RFQ No. {no_en} · параметры приведены по исходным данным и "
           f"ТЗ Заказчика; стадия Базового инжиниринга (FEED), возможные уточнения на следующей "
           f"стадии / Values follow the Customer's input data (FEED stage).", 18, GRAY),
    ]
    intro = [("Позиция запроса — RFQ item", pos["name"])]
    if spec_txt:
        intro.append(("Краткая характеристика — Summary", spec_txt))
    if ask_txt:
        intro.append(("Особые требования — Specific requirements", ask_txt))
    body.append(kv_table(intro))

    if eqs:
        body.append(pp("Состав позиции по перечню оборудования — Equipment covered by this "
                       "request:", bold=True))
        for i, it in enumerate(eqs, 1):
            meta = it.get("area", "")
            if it.get("pos"):
                meta += f" · поз. {it['pos']}"
            body.append(bd.p([bd.run(f"{i}. {it.get('name', '')}", bold=True, size=21),
                              bd.run(f"   {meta}", size=16, color=GRAY)]))
            rows = []
            for label, field in EQ_ROWS:
                v = EQ_FIELD_OVERRIDE.get((it.get("name", ""), field), it.get(field))
                if v is not None and str(v).strip() not in ("", "—", "-"):
                    rows.append((label, v))
            body.append(kv_table(rows))
    else:
        body.append(pp("Подробные перечни (контуры регулирования, точки измерения, утверждённый "
                       "перечень средств) — по Приложению №5 к ТЗ; предоставляются на следующей "
                       "стадии по запросу. / Detailed I/O and instrument lists per ToR Annex "
                       "No. 5 will be provided at the next stage upon request."))
    body.append(pp("В ТКП подлежат обязательному подтверждению: рабочая среда, температура, "
                   "давление, материал контактных частей, производительность, габариты и масса, "
                   "энергопотребление, срок изготовления, условия поставки. / The quotation must "
                   "confirm: process medium, temperature, pressure, materials of wetted parts, "
                   "capacity, dimensions and weight, power consumption, manufacturing lead time, "
                   "delivery terms.", bold=True))
    return body


def rfq_doc(n: int, pos, eqs) -> tuple:
    """(имя файла, готовый document.xml) — без записи на диск."""
    key = key_of(pos)
    no, no_en = f"ОВЭ75-RFQ-{n:02d}", f"OVE75-RFQ-{n:02d}"
    spec_txt = SPEC_OVERRIDE.get(key, pos.get("spec") or "")
    ask_txt = ASK_OVERRIDE.get(key, pos.get("ask") or "") if key in ASK_OVERRIDE \
        else (pos.get("ask") or "")
    body = (letter_ru(no, pos, spec_txt) + [PAGE_BREAK] +
            letter_en(no_en, key) + [PAGE_BREAK] +
            annex(no, no_en, pos, key, eqs, spec_txt, ask_txt))
    return f"ove75-rfq-{n:02d}-{pos['cls']}.docx", document_xml(body)


# ------------------------------------------------------------------- реестр

def cand_paras(pos):
    paras = []
    for label, field in (("Мир", "world"), ("Китай", "cn"), ("РФ", "ru")):
        names = pos.get(field) or []
        if names:
            paras.append(bd.p([bd.run(f"{label}: ", bold=True, size=16),
                               bd.run("; ".join(names), size=16)]))
    exc = pos.get("excluded_sanctioned") or []
    if exc:
        paras.append(bd.p([bd.run("Исключены (санкционный признак): " + "; ".join(exc),
                                  size=15, color=GRAY)]))
    if not paras:
        paras.append(bd.p([bd.run("кандидаты не заведены — искать с нуля", size=16, color=RED)]))
    return paras


def index_doc(sup, files_eqs) -> tuple:
    positions = sup["positions"]
    legend = sup.get("legend") or {}
    n_cand = sum(len(p.get(f) or []) for p in positions for f in ("world", "cn", "ru"))
    body = [
        pp("ВНУТРЕННИЙ ДОКУМЕНТ — содержит имена кандидатов; поставщикам НЕ направлять",
           19, RED, bold=True),
        h1("ОВЭ-75. Реестр запросов бюджетных ТКП (RFQ)"),
        # Абзац даты — последний абзац шапки: каркас выпуска (docframe, drop_head) снимает
        # всё до него, дальше идёт первый содержательный заголовок.
        pp("Проект: медный завод — комплекс «обжиг – выщелачивание – электроэкстракция» "
           "производительностью 75 000 т катодной меди в год, г. Мончегорск · шифр КГМК.ОВЭ-75 · "
           f"составлено {TODAY}", 19, DGRAY),
        h2("Общие сведения"),
        pp(f"Реестр охватывает {len(positions)} запросов бюджетных технико-коммерческих предложений "
           f"по оборудованию комплекса. По каждой позиции выпускается запрос на русском и "
           f"английском языках с техническим приложением, составленным по перечню оборудования "
           f"Базового инжиниринга. Запросы не содержат цен и имён других участников рынка; "
           f"кандидаты ({n_cand} записей по позициям) приведены только в настоящем реестре. "
           f"Срок ответа по всем запросам — 3 недели с даты получения."),
        pp("Классы рынка: " + " · ".join(f"{k} — {v}" for k, v in legend.items()), 17, GRAY),
        h2("Перечень запросов"),
    ]
    widths = [600, 3300, 600, 3400, 6670]
    head = ["№", "Позиция запроса · обозначение", "Лот", "Классы оборудования (по перечню БИ)",
            "Кандидаты-поставщики"]
    rows = [[bd.cell([bd.p([bd.run(h, bold=True, size=17)])], w, shade=HEAD_SHADE)
             for h, w in zip(head, widths)]]
    for n, (pos, fname, eqs) in enumerate(files_eqs, 1):
        tier = pos.get("tier", "")
        name_paras = [bd.p([bd.run(pos["name"], bold=True, size=17)]),
                      bd.p([bd.run(f"ОВЭ75-RFQ-{n:02d} · класс рынка {tier}", size=15, color=GRAY)])]
        if eqs:
            cls_paras = [bd.p([bd.run(f"{pos['cls']} · {len(eqs)} поз.: ", bold=True, size=16),
                               bd.run("; ".join(it.get("name", "") for it in eqs), size=16)])]
        else:
            cls_paras = [bd.p([bd.run(f"{pos['cls']} · перечни по Приложению №5 ТЗ",
                                      size=16, color=DGRAY)])]
        rows.append([
            bd.cell([bd.p([bd.run(f"{n:02d}", size=17)])], widths[0]),
            bd.cell(name_paras, widths[1]),
            bd.cell([bd.p([bd.run(str(pos.get("lot", "")), size=17)])], widths[2]),
            bd.cell(cls_paras, widths[3]),
            bd.cell(cand_paras(pos), widths[4]),
        ])
    body.append(bd.table(rows, widths))
    body.append(pp("Обозначения лотов: " +
                   "; ".join(f"{k} — {v}" for k, v in sorted(LOT_NAME.items())) + ".", 15, GRAY))
    body.append(h2("Открытые позиции"))
    n_c = sum(1 for pos in positions if pos.get("tier") == "C")
    n_none = sum(1 for pos in positions
                 if not any(pos.get(f) for f in ("world", "cn", "ru")))
    for t in ([f"По {n_c} позициям класса рынка C подтверждённых кандидатов под заданные параметры "
               "нет — круг изготовителей определяется на стадии БИ."] if n_c else []) + \
             ([f"По {n_none} позициям кандидаты не заведены: перечень формируется на стадии БИ."]
              if n_none else []) + \
             ["Стоимостные показатели в настоящий реестр не входят: цены принимаются по "
              "технико-коммерческим предложениям изготовителей и сводятся отдельным документом.",
              "Окончательный состав получателей запросов согласуется с Заказчиком; по позициям, "
              "закрываемым перечнями Приложения №5 технического задания, применяется "
              "утверждённый Заказчиком перечень изготовителей.",
              "Сроки изготовления и условия поставки уточняются по технико-коммерческим "
              "предложениям и учитываются в графике длинноциклового оборудования."]:
        body.append(pp("— " + t, 17))
    return "ove75-rfq-index.docx", document_xml(body, landscape=True)


# --------------------------------------------- проверка «в письмах нет имён»

def candidate_tokens(sup) -> set:
    names = set()
    for pos in sup["positions"]:
        for f in ("world", "cn", "ru", "excluded_sanctioned"):
            for nm in pos.get(f) or []:
                # обобщённые записи вида «Рынок РФ …» / «См. Приложение №5 …» — не имена
                if not nm.lower().startswith(("рынок", "см.")):
                    names.add(nm)
    for prof in sup.get("profiles") or []:
        if prof.get("name"):
            names.add(prof["name"])
    tokens = set()
    for nm in names:
        for w in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", nm):
            w = w.lower()
            if w not in STOP_TOKENS:
                tokens.add(w)
    return tokens


def check_letters(letters, tokens) -> None:
    """letters — [(имя файла, document.xml)]; валит сборку ДО записи файлов."""
    price_markers = ("руб.", "usd", "eur", "cny", "$", "€", "¥")
    problems = []
    for fname, doc in letters:
        xml = doc.lower()
        for tok in tokens:
            if re.search(rf"(?<![a-zа-яё]){re.escape(tok)}(?![a-zа-яё])", xml):
                problems.append(f"{fname}: имя кандидата «{tok}»")
        for mark in price_markers:
            if mark in xml:
                problems.append(f"{fname}: похоже на цену/валюту «{mark}»")
    if problems:
        raise RuntimeError("Проверка писем провалена, файлы не записаны:\n  " +
                           "\n  ".join(problems))


# ----------------------------------------------------------------------- main

def build() -> None:
    sup = load("suppliers.json")
    eq = load("equipment.json")
    positions = sup["positions"]

    missing = [key_of(p) for p in positions if key_of(p) not in EQ_SELECT]
    if missing:
        raise RuntimeError(f"Нет правил EQ_SELECT для позиций: {missing} — дополни карту")

    files_eqs, letters = [], []
    for n, pos in enumerate(positions, 1):
        eqs = select_equipment(eq["items"], key_of(pos))
        fname, doc = rfq_doc(n, pos, eqs)
        letters.append((fname, doc))
        files_eqs.append((pos, fname, eqs))

    # сначала проверка писем (цены/имена кандидатов), запись файлов — только после
    check_letters(letters, candidate_tokens(sup))
    made = [save_docx(fname, doc) for fname, doc in letters]
    made.append(save_docx(*index_doc(sup, files_eqs)))

    total_kb = sum(p.stat().st_size for p in made) // 1024
    n_eq = sum(len(e) for _, _, e in files_eqs)
    print(f"DOCX RFQ → {OUTDIR}: писем {len(letters)} (позиций оборудования в приложениях {n_eq}) "
          f"+ {made[-1].name}; всего {total_kb} КБ. Проверка «нет цен и имён поставщиков» пройдена.")


if __name__ == "__main__":
    build()
