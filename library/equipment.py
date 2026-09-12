#!/usr/bin/env python3
"""Правила для звеньев «модель» и «узел»: как назвать машину и куда отнести деталь.

Отдельный модуль без зависимостей — ровно по той же причине, что и segments.py:
правило должно проверяться тестом на гейте, где нет ни psycopg2, ни сети.

ЧТО ЗДЕСЬ ЕСТЬ И ЧЕГО НЕТ. Здесь только правила. Сами машины и узлы лежат в
gt/data/models.json и gt/data/parts.json — это работа инженеров, дублировать её
в коде нельзя: разойдётся. Модуль умеет привести имя машины к ключу, разобрать
строку «Taurus 70, Taurus 70MD» на две машины и отнести деталь к узлу по тексту.

ПОЧЕМУ КЛЮЧ МАШИНЫ БЕЗ ПУНКТУАЦИИ. Одну машину пишут «SGT-400», «SGT 400»,
«sgt400», «Cyclone» (имя до продажи завода Siemens). Первые три — одно и то же,
и ключ их сводит. Четвёртое ключом не сводится никак, поэтому наследные имена
идут отдельным списком псевдонимов из models.json.

ПОЧЕМУ УЗЕЛ ОПРЕДЕЛЯЕТСЯ СЛОВАМИ, А НЕ МОДЕЛЬЮ. Узлы промышленной ГТУ одинаковы
у Solar и Siemens: горячий тракт, компрессор, ротор. Поэтому правило смотрит на
текст позиции, а не на машину.
"""
from __future__ import annotations

import re

RULE_VERSION = "equip-v1"

_PUNCT = re.compile(r"[^0-9a-zа-яё]+")
# Что в имени машины не про машину: пояснения в скобках («по документу», «сток»),
# слова «турбина», «ГТУ», «двигатель» и висящие уточнения после запятой.
_PAREN = re.compile(r"\([^)]*\)")
_NOISE = ("гту", "гтд", "турбина", "турбины", "двигатель", "агрегат", "унифицированнные",
          "унифицированные", "общая", "сток", "пакет", "по документу")
_SPLIT = re.compile(r"\s*[,;/]\s*|\s+и\s+")


def norm_model(name: str) -> str:
    """Ключ машины: без регистра, пунктуации и пробелов. SGT-400 → sgt400."""
    t = _PAREN.sub(" ", (name or "").lower().replace("ё", "е"))
    words = [w for w in _PUNCT.sub(" ", t).split() if w not in _NOISE]
    return "".join(words)


def split_machines(s: str) -> list[str]:
    """«Taurus 70, Taurus 70MD» → два имени. «GE Frame 6B» — одно.

    Разделители — запятая, точка с запятой, косая и союз «и». Пробел НЕ делит:
    почти каждое имя машины из двух слов («Centaur 50», «GE Frame 6B»)."""
    t = _PAREN.sub(" ", s or "")
    out, seen = [], set()
    for part in _SPLIT.split(t):
        p = " ".join(part.split())
        if len(p) < 2:
            continue
        k = norm_model(p)
        if k and k not in seen:
            seen.add(k)
            out.append(p)
    return out


# Правила «слово → узел». Порядок значим: первое совпадение выигрывает, поэтому
# частные правила идут раньше общих. Идентификаторы узлов — из gt/data/parts.json
# (система) и её компонентов (система.имя-по-английски).
UNIT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hot.combustion-liner", ("жаровая труба", "жаровые трубы", "combustion liner", "flame tube")),
    ("hot.main-burner", ("горелк", "burner", "dle")),
    ("hot.igniter", ("запальник", "свеча", "зажигани", "igniter", "spark plug", "розжиг")),
    ("hot.fuel-injectors", ("форсунк", "injector", "nozzle tip")),
    ("hot.transition-piece", ("переходной патрубок", "transition piece")),
    ("hot", ("камера сгорания", "горячий тракт", "combustor", "combustion chamber")),
    ("turbine.hp-turbine-blades", ("рабочая лопатка", "рабочие лопатки", "turbine blade")),
    ("turbine.nozzle-guide-vanes", ("сопловая лопатка", "сопловой аппарат", "направляющая лопатка",
                                    "nozzle guide", "ngv", "vane")),
    ("turbine.turbine-discs", ("диск турбины", "turbine disc")),
    ("turbine", ("лопатк", "турбина высокого", "силовая турбина", "blade")),
    ("compressor.inlet-guide-vanes", ("вна", "входной направляющий", "igv", "inlet guide")),
    ("compressor.bleed", ("антипомпажн", "сбросной клапан", "bleed valve", "blow-off")),
    ("compressor", ("компрессор", "compressor", "квоу")),
    ("rotor.journal-bearings", ("подшипник опорн", "вкладыш", "journal bearing", "баббит")),
    ("rotor.thrust-bearing", ("подшипник упорн", "thrust bearing")),
    ("rotor.dry-gas-seals", ("сухое газовое", "сухие газовые", "dry gas seal", "dgs")),
    ("rotor.couplings", ("муфта", "торсион", "coupling")),
    ("rotor.labyrinth-seals", ("лабиринтн", "labyrinth")),
    ("rotor", ("подшипник", "bearing", "ротор", "rotor")),
    ("fuel.fuel-metering", ("дозирован", "metering valve", "регулятор топлив")),
    ("fuel.shut-off-valves", ("стопорн", "отсечн", "shut-off", "shutoff")),
    ("fuel.fuel-manifolds", ("коллектор топлив", "fuel manifold", "рукав", "flex hose")),
    ("fuel.fuel-gas-filters", ("фильтр топлив", "fuel gas filter")),
    ("fuel", ("топлив", "fuel", "клапан", "valve")),
    ("controls.control-system", ("контроллер", "сау", "plc", "шкаф управления", "control system")),
    ("controls.exhaust-thermocouples", ("термопар", "thermocouple", "egt")),
    ("controls.vibration-probes", ("вибродатчик", "проксиметр", "вибрац", "vibration", "bently")),
    ("controls.solenoids", ("соленоид", "актуатор", "solenoid", "actuator", "конечник")),
    ("controls.flame-scanners", ("датчик пламени", "flame scanner", "уф-датчик")),
    ("controls", ("датчик", "sensor", "кип", "реле", "преобразователь", "switch", "электрик",
                  "кабель", "cable", "harness", "коммутатор", "модуль", "module",
                  "transducer", "контакт")),
    ("consumables.inlet-air-filters", ("фильтр квоу", "фильтр воздуш", "inlet air filter")),
    ("consumables.lube-oil-filters", ("маслофильтр", "фильтр масл", "сепаратор", "oil filter")),
    ("consumables.turbine-oil", ("турбинное масло", "turbine oil", "смазка", "lubricant")),
    ("seals", ("уплотнени", "прокладк", "сальник", "gasket", "o-ring", "seal")),
    ("consumables", ("фильтр", "filter", "расходник")),
    ("package.starter-system", ("стартер", "starter", "пусков")),
    ("package.lube-oil-pumps", ("маслонасос", "насос масл", "oil pump")),
    ("package.oil-coolers", ("аво", "теплообменник", "маслоохладител", "oil cooler")),
    ("package.gearbox", ("редуктор", "gearbox", "мультипликатор")),
    ("package.enclosure-ventilation", ("кожух", "вентиляц", "пожаротушен", "газоанализ", "enclosure",
                           "fire supp")),
    ("package", ("насос", "pump", "теплообмен", "вспомогательн", "bop")),
    ("fasteners", ("болт", "винт", "гайка", "шайба", "шпилька", "шплинт", "крепёж", "крепеж",
                   "стопорное кольцо", "screw", "bolt", "nut", "washer", "stud",
                   "retaining ring", "cotter")),
    ("tooling", ("оснастка", "инструмент", "приспособлени", "tooling", "fixture")),
    ("generator", ("генератор", "возбудител", "статор", "generator", "exciter", "stator")),
)

# Узлы, которых нет в номенклатуре инженеров, но без которых не разложить
# партномера: крепёж (825 позиций), прокладки и уплотнения (311), оснастка (72),
# выхлопной тракт. Генератор — отдельный случай: он не часть ГТУ, но ремонтируют
# его те же цеха и в той же остановке, и разведка по нему собрана вместе с ГТУ.
# Помечены своим источником, чтобы отличать нашу разметку от работы инженеров.
EXTRA_UNITS: tuple[tuple[str, str, str, str], ...] = (
    ("fasteners", "Крепёж", "fasteners / hardware", "C"),
    ("seals", "Уплотнения и прокладки", "seals / gaskets", "C"),
    ("tooling", "Оснастка и инструмент", "tooling / fixtures", "C"),
    ("exhaust", "Выхлопной тракт и диффузор", "exhaust duct / diffuser", "B"),
    ("generator", "Генератор и возбудитель", "generator / exciter", "A"),
)


# Короткое слово ищется целиком, длинное — вхождением. Правило оплачено разметкой
# 12 442 партномеров: «вна» подстрокой ловится в слове «топливная», «аво» — в
# «доставочной» и «заводской», «сау» — где угодно. Порог 4 знака: всё, что короче,
# по-русски слишком часто оказывается куском другого слова. Все опасные оказались
# ровно трёхбуквенными, поэтому порог 3, а не 4: четырёхбуквенное «seal» должно
# ловиться и во множественном числе.
_WHOLE = 3
_COMPILED = tuple(
    (unit, tuple((w, re.compile(rf"(?<![0-9a-zа-яё]){re.escape(w)}(?![0-9a-zа-яё])"))
                 for w in words))
    for unit, words in UNIT_RULES)


def unit_of(text: str) -> str | None:
    """Узел по тексту позиции. None — не опознан; это честнее, чем «прочее»."""
    t = (text or "").lower().replace("ё", "е")
    if len(t) < 3:
        return None
    for unit, words in _COMPILED:
        for w, rx in words:
            if (rx.search(t) if len(w) <= _WHOLE else w in t):
                return unit
    return None


# Разметка инженеров в поле seg — их собственные ярлыки узлов, и гадать по ним
# текстовыми правилами незачем: ярлыков полсотни, они перечислимы. Пустая правая
# часть значит «ярлык есть, но узла не называет» («Прочее / требует разметки»).
SEG_MAP: dict[str, str | None] = {
    "крепёж": "fasteners", "крепеж": "fasteners", "ротор / крепёж": "fasteners",
    "уплотнения и прокладки": "seals", "уплотнения": "seals", "прокладки": "seals",
    "компрессор / уплотнения": "seals",
    "сау, кип, электрика": "controls", "сау": "controls.control-system", "кип": "controls",
    "кип/вибрация": "controls.vibration-probes", "кип / вибрация": "controls.vibration-probes",
    "кип/управление": "controls.control-system", "управление/топливо": "controls.control-system",
    "кип / защиты": "controls", "защиты": "controls", "электрика пакета": "controls",
    "топливная система, клапаны": "fuel", "топливо": "fuel",
    "топливо / горелки": "hot.main-burner", "топливо / горелка": "hot.main-burner",
    "фильтры и расходники": "consumables", "химия и расходка": "consumables",
    "маслосистема/фильтрация": "consumables.lube-oil-filters",
    "оснастка и инструмент": "tooling",
    "пакет / навесное (bop)": "package", "трубопроводы": "package",
    "корпус/инспекция": "package", "пожарная система": "package.enclosure-ventilation",
    "привод агрегатов": "package.gearbox", "пусковая система": "package.starter-system",
    "маслосистема": "package.lube-oil-pumps",
    "горячий тракт: камеры, горелки, зажигание": "hot", "горячий тракт": "hot",
    "горячий тракт / камера сгорания": "hot", "горячий тракт / корпус": "hot",
    "горячий тракт / са": "turbine.nozzle-guide-vanes", "камера сгорания": "hot",
    "камера сгорания / крепёж": "hot", "зажигание": "hot.igniter",
    "ротор, подшипники, уплотнения": "rotor", "ротор": "rotor", "подшипники": "rotor",
    "лопатки / ротор": "rotor",
    "лопатки": "turbine", "лопатки и сопловые": "turbine",
    "лопатки / са": "turbine.nozzle-guide-vanes", "силовая турбина": "turbine",
    "компрессор": "compressor", "компрессор / вна": "compressor.inlet-guide-vanes",
    "компрессор / механизация вна": "compressor.inlet-guide-vanes",
    "компрессор / механизация": "compressor",
    "воздушная система": "compressor", "воздушный тракт": "compressor",
    "выхлоп": "exhaust", "выхлоп / диффузор": "exhaust",
    "прочее / требует разметки": None,
}


def unit_of_seg(seg: str) -> str | None:
    """Узел по ярлыку инженеров. Неизвестный ярлык — не догадка, а None."""
    return SEG_MAP.get(" ".join((seg or "").lower().replace("ё", "е").split()))


def slug_en(en: str) -> str:
    """Идентификатор компонента из английского имени: стабилен при переупорядочении.

    «combustion liner / can» → combustion-liner. Берём часть до косой черты:
    после неё в номенклатуре идёт синоним, а не уточнение."""
    head = (en or "").split("/")[0].strip().lower()
    head = re.sub(r"\(.*?\)", " ", head)
    words = [w for w in re.split(r"[^a-z0-9]+", head) if w][:3]
    return "-".join(words)


# Имена изготовителей, которые в поле «машина» стоят вместо машины: «Solar (сток)»
# значит «что-то из Solar», а не модель. Такие в справочник машин не заводим.
OEM_NAMES = frozenset((
    "solar", "solarturbines", "ge", "geenergy", "general", "generalelectric",
    "siemens", "siemensenergy", "ansaldo", "ansaldoenergia", "alstom", "abb",
    "rollsroyce", "rr", "caterpillar", "cat", "mitsubishi", "mhi", "man", "kawasaki",
))


def looks_like_machine(name: str, key: str | None = None) -> bool:
    """Годится ли строка из поля «машина» в справочник машин.

    Правило нужно потому, что поле заполняют вручную и туда попадает что угодно:
    имя изготовителя вместо модели («Solar (сток)») и вовсе не турбина («Шкаф
    управления PMS МЛСК Ф-1»). Четыре условия, каждое оплачено таким мусором:
    цифра И буква в имени (у машины есть и серия, и номер; голое «1534» — это
    обломок от «Avon 1533/1534»), не длиннее 24 знаков ключа, не имя
    изготовителя, и не длиннее трёх слов (пятисловное — это описание)."""
    key = key if key is not None else norm_model(name)
    if not key or len(key) > 24 or key in OEM_NAMES:
        return False
    if not (any(c.isdigit() for c in key) and any(c.isalpha() for c in key)):
        return False
    return len(_PAREN.sub(" ", name or "").split()) <= 3
