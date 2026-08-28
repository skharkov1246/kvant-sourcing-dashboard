#!/usr/bin/env python3
"""«Красный карандаш» — локальная сверка ТКП поставщика с нашим запросом (ОВЭ-75).

Сорсер получает ТКП (PDF/DOCX/XLSX), запускает инструмент — тот ДЕТЕРМИНИРОВАННО,
без всякой генерации, сопоставляет текст ТКП с нашими требованиями и печатает акт:

  1) обязательные коммерческие поля — состав берётся из sourcing_rules
     ove/data/suppliers.json (материал контактных частей, срок изготовления,
     условия поставки/базис и весь перечень опросного листа) + цена с валютой
     и срок действия ТКП;
  2) технические параметры позиции — числа с единицами из ove/data/equipment.json
     (подбор позиций оборудования — та же карта EQ_SELECT, что в build_rfq.py);
     найденное рядом с той же единицей число сравнивается с нашим: совпало /
     укладывается в «до…» — ✅, отличается — «⚠️ расходится: у нас X, в ТКП Y»;
  3) язык и валюта ТКП — детект по тексту.

Каждая строка акта: ✅ найдено / ⚠️ расходится / ❌ отсутствует + цитата из ТКП
(30–60 знаков контекста). Отчёт — в консоль и в markdown рядом с файлом ТКП
(<имя>-check.md). ТКП без материала контактных частей помечается
«К ОЦЕНКЕ НЕ ПРИНИМАЕТСЯ» — прямое правило проекта из sourcing_rules.

Запуск:  python3 ove/tools/check_tkp.py <ткп.pdf|.docx|.xlsx> --pos <N>
         python3 ove/tools/check_tkp.py --list          # номера позиций
         python3 ove/tools/check_tkp.py --selftest      # самотест на синтетике
Ключи: --pos принимает номер (как в ove75-rfq-NN-*.docx), класс (fan) или
класс:лот (hx:2); --out — куда положить markdown-акт вместо «рядом с ТКП».

Извлечение текста: PDF — системный pdftotext; DOCX — zipfile + regex по
word/document.xml; XLSX — zipfile + sharedStrings. Внешних pip-зависимостей нет;
нет pdftotext — мягкое предупреждение и выход (в сборку сайта не входит).
Коды выхода: 0 — комплектно, 1 — есть ⚠️/❌, 2 — ошибка запуска.
"""
import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from math import isclose
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_docx as bd    # noqa: E402 — OOXML-хелперы для синтетики самотеста
import build_rfq as rfq    # noqa: E402 — карта позиция → оборудование (EQ_SELECT)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

OK, WARN, MISS, INFO = "✅", "⚠️", "❌", "ℹ️"
REL_TOL = 0.02   # допуск сравнения чисел (округления, пересчёт единиц у поставщика)

# ------------------------------------------------------------ извлечение текста

def _docx_text(p: Path) -> str:
    """DOCX = zip: word/document.xml → текст (схема наших тестов: zipfile + regex)."""
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:(?:tab|br)[^>]*/?>", "\t", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def _xlsx_text(p: Path) -> str:
    """XLSX = zip: sharedStrings + листы; ячейки строки — через таб, строки — \\n."""
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            sst = z.read("xl/sharedStrings.xml").decode("utf-8", errors="ignore")
            for si in re.findall(r"<si>(.*?)</si>", sst, re.S):
                shared.append(html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))))
        lines = []
        for n in sorted(x for x in names if re.match(r"xl/worksheets/sheet\d+\.xml$", x)):
            sheet = z.read(n).decode("utf-8", errors="ignore")
            for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
                cells = []
                for m in re.finditer(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
                    attrs, inner = m.group(1), m.group(2) or ""
                    v = re.search(r"<v[^>]*>(.*?)</v>", inner, re.S)
                    if 't="s"' in attrs and v:
                        i = int(v.group(1))
                        cells.append(shared[i] if i < len(shared) else "")
                    elif 't="inlineStr"' in attrs:
                        cells.append(html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", inner, re.S))))
                    elif v:
                        cells.append(html.unescape(v.group(1)))
                if any(c.strip() for c in cells):
                    lines.append("\t".join(cells))
    return "\n".join(lines)


def _pdf_text(p: Path) -> str:
    """PDF — системным pdftotext (-layout сохраняет таблицы построчно)."""
    try:
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(p), "-"],
                           capture_output=True, timeout=120)
    except FileNotFoundError:
        raise RuntimeError("pdftotext не найден в системе — поставь poppler-utils "
                           "или конвертируй ТКП в DOCX/XLSX")
    if r.returncode != 0:
        raise RuntimeError(f"pdftotext не разобрал файл: {r.stderr.decode('utf-8', 'ignore').strip()}")
    return r.stdout.decode("utf-8", errors="ignore")


def extract_text(p: Path) -> str:
    ext = p.suffix.lower()
    if ext == ".pdf":
        text = _pdf_text(p)
    elif ext == ".docx":
        text = _docx_text(p)
    elif ext == ".xlsx":
        text = _xlsx_text(p)
    else:
        raise RuntimeError(f"формат {ext or '(без расширения)'} не поддерживается — жду .pdf/.docx/.xlsx")
    if len(text.strip()) < 40:
        raise RuntimeError("из файла извлечено меньше 40 знаков текста — похоже на скан; "
                           "прогони OCR или запроси у поставщика текстовую версию")
    return text


# ------------------------------------------------------- числа, единицы, цитаты

# число: «12 500 000», «12,500,000» (англ. тысячи), «1,9», «375»
NUM = r"(?:\d{1,3}(?:[   ]\d{3})+|\d{1,3}(?:,\d{3})+|\d+)(?:[.,]\d+)?"

# единицы: канон → regex (лат./кир. написания, м3 ≡ м³); порядок — от составных к простым,
# сканируются независимо, поэтому у простых стоят lookahead-исключения составных
UNITS = [
    ("м³/(м²·ч)", r"(?:м|m)\s*[³3]\s*/\s*\(\s*(?:м|m)\s*[²2]\s*[·×x*]\s*(?:ч|h)\s*\)"),
    ("кг/(м²·ч)", r"(?:кг|kg)\s*/\s*\(\s*(?:м|m)\s*[²2]\s*[·×x*]\s*(?:ч|h)\s*\)"),
    ("нм³/ч", r"(?:нм|nm)\s*[³3]\s*/\s*(?:час|ч|h)"),
    ("м³/ч", r"(?<![нn])(?:м|m)\s*[³3]\s*/\s*(?:час|ч|h)"),
    ("т/ч", r"(?:т|t)\s*/\s*(?:час|ч|h)"),
    ("кг/ч", r"(?:кг|kg)\s*/\s*(?:час|ч|h)(?![а-яa-z])"),
    ("МПа", r"(?:мпа|mpa)\b"),
    ("кПа", r"(?:кпа|kpa)\b"),
    ("атм", r"(?:атм|atm)\b"),
    ("°С", r"°\s*[сc]\b"),
    ("кВт", r"(?:квт|kw)(?!\s*[·×x*]?\s*[чh])"),        # не кВт·ч
    ("МВт", r"(?:мвт|mw)(?!\s*[·×x*]?\s*[чh])"),
    ("кВ", r"(?:кв|kv)(?![тt.а-яa-z])"),
    ("кА", r"(?:ка|ka)(?![а-яa-z])"),
    ("А/м²", r"(?:а|a)\s*/\s*(?:м|m)\s*[²2]"),
    ("В", r"(?:в|v)(?![а-яa-zё])"),                     # + защита кодом: значение ≥ 100
    ("мм", r"(?:мм|mm)(?![а-яa-z])"),
    ("м³", r"(?<![нn])(?:м|m)\s*[³3](?!\s*/)"),
    ("м²", r"(?:м|m)\s*[²2](?!\s*[·×x*]\s*[чh])(?!\s*/)"),
    ("м", r"(?:м|m)(?![а-яa-zё²³23/.*×])"),
    ("т", r"(?:т|t)(?![а-яa-zё/.])"),
    ("кг", r"(?:кг|kg)(?![а-яa-z/])"),
    ("шт", r"(?:шт|pcs|off)\b"),
    ("%", r"%"),
]
RX_UNIT = {u: re.compile(rf"({NUM})\s*(?:{rx})", re.I) for u, rx in UNITS}
RX_RANGE = {u: re.compile(rf"({NUM})\s*[–—-]\s*({NUM})\s*(?:{rx})", re.I) for u, rx in UNITS}
RX_DIMS3 = re.compile(rf"({NUM})\s*[×xх]\s*({NUM})\s*[×xх]\s*({NUM})\s*(?:мм|mm)\b", re.I)

# материалы контактных частей: канон → детект (включая латинские варианты и аналоги)
MATERIALS = [
    ("12Х18Н10Т", r"12\s*[хx]\s*18\s*[нh]\s*10\s*[тt]|aisi\s*321"),
    ("316L", r"(?:aisi\s*)?316\s*l\b"),
    ("904L", r"(?:aisi\s*)?904\s*l\b"),
    ("Hastelloy G-35", r"hastelloy|хастелло[йи]"),
    ("углеродистая сталь", r"углеродист\w*\s+стал|carbon\s+steel|\bст\.?\s*3\b|09г2с"),
    ("жаропрочная сталь", r"жаропрочн"),
    ("полимербетон", r"полимербетон|polymer\s*concrete"),
    ("винилэфирный композит", r"винилэфир|vinyl\s*ester"),
    ("Pb-Ca", r"pb\s*[-–]\s*ca\b"),
    ("Pb-Ag", r"pb\s*[-–]\s*ag\b"),
    ("нержавеющая сталь", r"нержавеющ|stainless"),
    ("титан", r"\bтитан|titanium"),
    ("резина", r"\bрезин|rubber"),
    ("шамот", r"шамот"),
    ("асбест", r"асбест"),
]
RX_MAT = [(name, re.compile(rx, re.I)) for name, rx in MATERIALS]

CURRENCIES = [
    ("RUB", r"(?<![а-яa-zё])руб|₽|\brub\b"),
    ("USD", r"\busd\b|\$|доллар"),
    ("EUR", r"\beur\b|€|(?<![а-яa-zё])евро(?![а-яё])"),
    ("CNY", r"\bcny\b|\brmb\b|юан|¥"),
    ("INR", r"\binr\b|₹|рупи"),
    ("TRY", r"₺|турецк\w+\s+лир|\bлир[аы]\b"),
]


def to_num(s: str) -> float:
    s = re.sub(r"[   ]", "", s)
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):
        s = s.replace(",", "")        # английский разделитель тысяч: 72,000
    else:
        s = s.replace(",", ".")       # русская десятичная запятая: 1,9
    return float(s)


def quote(raw: str, s: int, e: int, pad: int = 30) -> str:
    """Цитата из ТКП: совпадение + ~30 знаков контекста с каждой стороны."""
    mid = re.sub(r"\s+", " ", raw[s:e]).strip()
    left = re.sub(r"\s+", " ", raw[max(0, s - pad):s]).lstrip()
    right = re.sub(r"\s+", " ", raw[e:e + pad]).rstrip()
    if len(mid) > 42:
        mid = mid[:40].rstrip() + "…"
    room = max(6, 60 - len(mid))
    left, right = left[-(room // 2):], right[:room // 2]
    pre = "…" if s - pad > 0 or len(left) < s else ""
    post = "…" if e + pad < len(raw) else ""
    return f"{pre}{left}{mid}{right}{post}".strip()


def unit_hits(low: str, unit: str):
    """Все числа с данной единицей в тексте ТКП: [(значение, (s, e), текст числа)]."""
    hits = []
    for m in RX_UNIT[unit].finditer(low):
        try:
            v = to_num(m.group(1))
        except ValueError:
            continue
        if unit == "В" and v < 100:   # одиночная «В» без контекста ловит предлог «в»
            continue
        hits.append((v, m.span(), m.group(1).strip()))
    return hits


# --------------------------------------- чек-лист 1: коммерческие поля (правила)

# канонические поля: id → (метка, [regex-детекты по тексту ТКП])
FIELDS = {
    "price":       ("Цена и валюта", [r"(?:цен[аы]|стоимост|итого|сумм[аы]|price|total|amount)"]),
    "validity":    ("Срок действия ТКП", [r"срок\s+действ", r"действительн", r"предложение\s+действует",
                                          r"valid(?:ity|\s+until|\s+till|\s+for)"]),
    "medium":      ("Рабочая среда", [r"рабоч\w+\s+сред", r"перемещаем\w+\s+сред", r"перекачиваем",
                                      r"\bсред[аы]\b\s*[:–—-]?", r"состав\s+газа",
                                      r"process\s+(?:fluid|medium|gas)", r"handled\s+medium"]),
    "temperature": ("Температура", [r"температур", r"temperature", r"°\s*[cс]"]),
    "pressure":    ("Давление", [r"давлен", r"разрежен", r"pressure", r"\bмпа\b|\bкпа\b|\bатм\b|\bbar\b",
                                 r"\d\s*па\b"]),
    "material":    ("Материал контактных частей",
                    [r"материал", r"material", r"мат\.?\s*исполнен"] + [rx for _, rx in MATERIALS]),
    "capacity":    ("Производительность", [r"производительн", r"\bрасход\b", r"capacity", r"throughput",
                                           r"flow\s*rate", r"\bq\s*="]),
    "dims":        ("Габариты", [r"габарит", r"размер", r"dimension", r"\bдлина\b|\bширина\b|\bвысота\b",
                                 r"\d{2,}\s*[×xх]\s*\d{2,}"]),   # ≥2 цифр — не ловить коды моделей
    "mass":        ("Масса", [r"\bмасс[аы]?\b", r"\bвес\b", r"weight", r"\d\s*(?:кг|kg)\b"]),
    "power":       ("Энергопотребление / мощность", [r"мощност", r"энергопотреблен", r"электропитан",
                                                     r"напряжен", r"\bквт\b|\bkw\b", r"\bpower\b"]),
    "leadtime":    ("Срок изготовления", [r"срок\s+изготовлен", r"срок\s+поставки", r"срок\s+производства",
                                          r"готовность\s+к\s+отгрузке", r"производственный\s+цикл",
                                          r"delivery\s+(?:time|period)", r"lead\s*time"]),
    "delivery":    ("Условия поставки / базис (Инкотермс)",
                    [r"базис", r"услови[яй]\s+поставки", r"инкотермс", r"incoterms",
                     r"\b(?:dap|ddp|fca|exw|cip|cif|fob|cpt|cfr|dpu)\b", r"terms\s+of\s+delivery"]),
}
# сопоставление пунктов перечня из sourcing_rules каноническим полям (порядок важен:
# сначала более специфичные основы)
RULE_STEMS = [("материал", "material"), ("срок изготовлен", "leadtime"), ("услови", "delivery"),
              ("производительн", "capacity"), ("энергопотреблен", "power"), ("температур", "temperature"),
              ("давлен", "pressure"), ("габарит", "dims"), ("масс", "mass"), ("сред", "medium")]


def checklist_from_rules(rules):
    """Состав обязательных полей — из перечня «должен содержать: …» sourcing_rules.

    Возвращает [(fid, метка, [rx]), …] и словарь оснований fid → текст правила.
    Цена+валюта и срок действия ТКП — базовые поля любого ТКП, добавляются всегда.
    """
    basis, fields, seen = {}, [], set()

    def add(fid, label, pats, why):
        if fid not in seen:
            seen.add(fid)
            fields.append((fid, label, pats))
            basis[fid] = why

    add("price", *FIELDS["price"], "без цены и валюты ТКП не сравнить — базовое поле запроса")
    for i, rule in enumerate(rules, 1):
        m = re.search(r"долж\w*\s+содержать\s*:(.+)$", rule, re.I | re.S)
        if not m:
            continue
        why = f"правило проекта №{i}: «{rule[:90].rstrip()}…»"
        for item in m.group(1).strip().rstrip(".").split(","):
            item = item.strip()
            if not item:
                continue
            fid = next((f for stem, f in RULE_STEMS if stem in item.lower()), None)
            if fid:
                add(fid, *FIELDS[fid], why)
            else:  # новый пункт в правилах — ищем по основам слов, чек-лист не молчит
                pats = [rf"\b{re.escape(w[:6])}" for w in re.findall(r"[а-яёa-z]{5,}", item.lower())]
                if pats:
                    add(f"x:{item}", item.capitalize(), pats, why)
    add("validity", *FIELDS["validity"], "без срока действия цену нельзя удержать до сравнения ТКП")

    # ужесточения из отдельных правил: материал — «к оценке не принимается»
    for rule in rules:
        if "не принимается" in rule and "материал" in rule.lower():
            basis["material"] = f"правило проекта: «{rule.strip()}»"
        if "письменно" in rule and "срок" in rule.lower():
            basis["leadtime"] = basis["delivery"] = f"правило проекта: «{rule.strip()}»"
    return fields, basis


def check_fields(raw, low, fields, basis, currencies):
    """Раздел 1 акта: наличие обязательных полей, каждая находка — с цитатой."""
    out = []
    for fid, label, pats in fields:
        m = next((mm for rx in pats for mm in [re.search(rx, low, re.I)] if mm), None)
        if fid == "price":
            out.append(_check_price(raw, low, label, m, basis[fid], currencies))
            continue
        if m:
            out.append({"st": OK, "fid": fid, "label": label, "msg": "найдено",
                        "q": quote(raw, *m.span())})
        else:
            out.append({"st": MISS, "fid": fid, "label": label,
                        "msg": f"в ТКП отсутствует · {basis[fid]}", "q": ""})
    return out


def _check_price(raw, low, label, kw, why, currencies):
    """Цена: ключевое слово + число рядом + валюта где-либо в документе."""
    if not kw:
        return {"st": MISS, "fid": "price", "label": label,
                "msg": f"в ТКП отсутствует · {why}", "q": ""}
    win = low[kw.end():kw.end() + 90]
    num = re.search(NUM, win)
    if not num:
        return {"st": WARN, "fid": "price", "label": label,
                "msg": "поле цены есть, но числа рядом нет («договорная»?)",
                "q": quote(raw, *kw.span())}
    s, e = kw.start(), kw.end() + num.end()
    if not currencies:
        return {"st": WARN, "fid": "price", "label": label,
                "msg": "цена указана, но валюта в ТКП не обнаружена", "q": quote(raw, s, e)}
    return {"st": OK, "fid": "price", "label": label,
            "msg": f"найдено, валюта {'/'.join(c for c, _ in currencies)}", "q": quote(raw, s, e)}


# ------------------------------------ чек-лист 2: технические параметры позиции

EQ_FIELDS = [("param", "параметры"), ("power", "мощность/питание"),
             ("dims", "габариты/объёмы"), ("qty", "количество")]


def facts_of(item):
    """Числовые требования из полей позиции equipment.json: (метка, kind, данные)."""
    facts, seen = [], set()
    for f, flab in EQ_FIELDS:
        src = str(item.get(f) or "")
        if not src or src == "—":
            continue
        if f == "qty":   # количество — только когда это чистое число, а не «уточняется»
            m = re.match(r"\s*(\d+)\b", src)
            if m and "уточн" not in src.lower():
                facts.append({"lab": flab, "kind": "qty", "unit": "шт",
                              "v": float(m.group(1)), "txt": m.group(1)})
            continue
        low = src.lower()
        for m in RX_DIMS3.finditer(low):
            facts.append({"lab": flab, "kind": "dims3", "unit": "мм",
                          "v3": [to_num(m.group(i)) for i in (1, 2, 3)],
                          "txt": re.sub(r"\s+", " ", m.group(0))})
        taken = [m.span() for m in RX_DIMS3.finditer(low)]
        for unit, _ in UNITS:
            for m in RX_RANGE[unit].finditer(low):
                taken.append(m.span())
                key = (unit, "range", m.group(1), m.group(2))
                if key not in seen:
                    seen.add(key)
                    facts.append({"lab": flab, "kind": "range", "unit": unit,
                                  "a": to_num(m.group(1)), "b": to_num(m.group(2)),
                                  "txt": f"{m.group(1)}–{m.group(2)}"})
            for v, (s, e), txt in unit_hits(low, unit):
                if any(a <= s < b for a, b in taken):
                    continue   # число уже учтено диапазоном или тройкой габаритов
                pre = low[max(0, s - 16):s]
                kind = ("max" if re.search(r"до\s*$|не более|≤|<|up to", pre) else
                        "min" if re.search(r"\bот\s*$|не менее|≥|>|не ниже", pre) else "val")
                key = (unit, kind, txt)
                if key not in seen:
                    seen.add(key)
                    facts.append({"lab": flab, "kind": kind, "unit": unit, "v": v, "txt": txt})
    return facts


def _mat_checks(item, raw, low):
    """Материал позиции: наши марки найдены / вместо них другая марка / не указан."""
    req = str(item.get("material") or "")
    if not req or req == "—":
        return []
    req_names = [name for name, rx in RX_MAT if rx.search(req)]
    out = []
    others = [(name, rx.search(low)) for name, rx in RX_MAT
              if name not in req_names and rx.search(low)]
    for name in req_names:
        rx = dict(RX_MAT)[name]
        m = rx.search(low)
        if m:
            out.append({"st": OK, "label": f"материал: {name}", "msg": "совпадает",
                        "q": quote(raw, *m.span())})
        elif others:
            oname, om = others[0]
            out.append({"st": WARN, "label": f"материал: {name}",
                        "msg": f"расходится: у нас {name}, в ТКП {oname}",
                        "q": quote(raw, *om.span())})
        else:
            out.append({"st": MISS, "label": f"материал: {name}",
                        "msg": f"в ТКП не найден (у нас: {req})", "q": ""})
    return out


def _cmp_fact(fact, raw, low):
    """Одно числовое требование против всех чисел ТКП с той же единицей."""
    u = fact["unit"]
    hits = unit_hits(low, u)
    lab = f"{fact['lab']}: "
    if fact["kind"] == "dims3":
        lab += f"{fact['txt']}"
        best = None
        for m in RX_DIMS3.finditer(low):
            t = [to_num(m.group(i)) for i in (1, 2, 3)]
            best = (t, m.span(), m.group(0))
            if all(isclose(a, b, rel_tol=REL_TOL) for a, b in zip(fact["v3"], t)):
                return {"st": OK, "label": lab, "msg": "совпадает", "q": quote(raw, *m.span())}
        if best:
            found = re.sub(r"\s+", " ", best[2])
            return {"st": WARN, "label": lab,
                    "msg": f"расходится: у нас {fact['txt']}, в ТКП {found}",
                    "q": quote(raw, *best[1])}
        return {"st": MISS, "label": lab, "msg": "габариты Д×Ш×В в ТКП не найдены", "q": ""}

    if not hits:
        ours = {"range": f"{fact.get('txt', '')} {u}", "max": f"до {fact.get('txt', '')} {u}",
                "min": f"от {fact.get('txt', '')} {u}"}.get(fact["kind"], f"{fact.get('txt', '')} {u}")
        return {"st": MISS, "label": lab + ours.strip(),
                "msg": f"число с единицей «{u}» в ТКП не найдено (у нас {ours})", "q": ""}

    if fact["kind"] == "qty":
        lab += f"{fact['txt']} {u}"
        eq = [h for h in hits if h[0] == fact["v"]]
        if eq:
            return {"st": OK, "label": lab, "msg": "совпадает", "q": quote(raw, *eq[0][1])}
        v, sp, txt = min(hits, key=lambda h: abs(h[0] - fact["v"]))
        return {"st": WARN, "label": lab,
                "msg": f"расходится: у нас {fact['txt']} {u}, в ТКП {txt} {u}", "q": quote(raw, *sp)}

    if fact["kind"] == "range":
        lab += f"{fact['txt']} {u}"
        ok = [h for h in hits if fact["a"] * (1 - REL_TOL) <= h[0] <= fact["b"] * (1 + REL_TOL)]
        if ok:
            return {"st": OK, "label": lab, "msg": "в нашем диапазоне", "q": quote(raw, *ok[0][1])}
        v, sp, txt = min(hits, key=lambda h: min(abs(h[0] - fact["a"]), abs(h[0] - fact["b"])))
        return {"st": WARN, "label": lab,
                "msg": f"расходится: у нас {fact['txt']} {u}, в ТКП {txt} {u}", "q": quote(raw, *sp)}

    if fact["kind"] in ("max", "min"):
        sign = "до" if fact["kind"] == "max" else "от"
        lab += f"{sign} {fact['txt']} {u}"
        ok = [h for h in hits if (h[0] <= fact["v"] * (1 + REL_TOL)) == (fact["kind"] == "max")
              or isclose(h[0], fact["v"], rel_tol=REL_TOL)]
        if ok:
            v, sp, txt = min(ok, key=lambda h: abs(h[0] - fact["v"]))
            return {"st": OK, "label": lab, "msg": f"укладывается: в ТКП {txt} {u}",
                    "q": quote(raw, *sp)}
        v, sp, txt = min(hits, key=lambda h: abs(h[0] - fact["v"]))
        return {"st": WARN, "label": lab,
                "msg": f"расходится: у нас {sign} {fact['txt']} {u}, в ТКП {txt} {u}",
                "q": quote(raw, *sp)}

    # точное значение
    lab += f"{fact['txt']} {u}"
    eq = [h for h in hits if isclose(h[0], fact["v"], rel_tol=REL_TOL)]
    if eq:
        return {"st": OK, "label": lab, "msg": "совпадает", "q": quote(raw, *eq[0][1])}
    v, sp, txt = min(hits, key=lambda h: abs(h[0] - fact["v"]))
    return {"st": WARN, "label": lab,
            "msg": f"расходится: у нас {fact['txt']} {u}, в ТКП {txt} {u}", "q": quote(raw, *sp)}


def check_equipment(items, raw, low):
    """Раздел 2 акта: по каждой позиции оборудования — материал и числа с единицами."""
    groups = []
    for it in items:
        checks = _mat_checks(it, raw, low)
        checks += [_cmp_fact(f, raw, low) for f in facts_of(it)]
        head = it["name"] + (f" — {it['model']}" if it.get("model") not in (None, "", "—") else "")
        if not checks:
            checks = [{"st": INFO, "label": "числовые требования",
                       "msg": "в equipment.json не формализованы — сверить вручную по ТЗ", "q": ""}]
        groups.append((head, checks))
    return groups


# --------------------------------------------- чек-лист 3: язык и валюта детект

def detect_lang_currency(raw, low):
    cyr = len(re.findall(r"[а-яё]", low))
    lat = len(re.findall(r"[a-z]", low))
    cjk = len(re.findall(r"[一-鿿]", raw))
    tot = max(1, cyr + lat + cjk)
    parts = [(n, c) for n, c in (("русский", cyr), ("английский/латиница", lat),
                                 ("китайский", cjk)) if c / tot >= 0.05]
    lang = ", ".join(f"{n} {round(100 * c / tot)} %" for n, c in parts) or "не определён"
    curs = []
    for code, rx in CURRENCIES:
        m = re.search(rx, low, re.I)
        if m:
            curs.append((code, quote(raw, *m.span())))
    return lang, curs


# ------------------------------------------------------------------ отчёт (акт)

def line(c):
    q = f" — «{c['q']}»" if c.get("q") else ""
    return f"- {c['st']} **{c['label']}** — {c['msg']}{q}"


def build_report(tkp: Path, n, pos, field_checks, eq_groups, lang, curs, basis):
    flat = field_checks + [c for _, cs in eq_groups for c in cs]
    cnt = {s: sum(1 for c in flat if c["st"] == s) for s in (OK, WARN, MISS)}
    mat_missing = any(c["fid"] == "material" and c["st"] == MISS for c in field_checks)
    if mat_missing:
        verdict = (f"{MISS} К ОЦЕНКЕ НЕ ПРИНИМАЕТСЯ — не указан материал контактных частей. "
                   f"{basis.get('material', '')}")
    elif cnt[MISS] or cnt[WARN]:
        verdict = (f"{WARN} ДОРАБОТАТЬ С ПОСТАВЩИКОМ — запросить недостающее ({MISS} {cnt[MISS]}) "
                   f"и подтвердить расхождения ({WARN} {cnt[WARN]})")
    else:
        verdict = f"{OK} КОМПЛЕКТНО — можно в сравнительную таблицу ТКП"

    md = [f"# Сверка ТКП — «красный карандаш» · позиция {n:02d}",
          "",
          f"**ТКП:** `{tkp.name}` · **позиция {n:02d}:** {pos['name']} "
          f"(лот {pos['lot']}, класс {pos['cls']}, tier {pos.get('tier', '—')}) · {date.today():%d.%m.%Y}",
          "",
          f"**Итог: {verdict}**  \n{OK} {cnt[OK]} · {WARN} {cnt[WARN]} · {MISS} {cnt[MISS]}",
          "",
          "## 1. Обязательные поля ТКП (sourcing_rules)", ""]
    md += [line(c) for c in field_checks]
    md += ["", "## 2. Технические параметры позиции (equipment.json)", ""]
    if not eq_groups:
        md += [f"- {INFO} позиций в equipment.json для этого класса нет "
               "(перечни АСУ/КИП — по Приложению №5 ТЗ) — сверка вручную", ""]
    for head, checks in eq_groups:
        md.append(f"**{head}**")
        md += [line(c) for c in checks]
        md.append("")
    md += ["## 3. Язык и валюта", "", f"- {INFO} язык ТКП: {lang}"]
    if curs:
        for code, q in curs:
            md.append(f"- {INFO} валюта: {code} — «{q}»")
        if len(curs) > 1:
            md.append(f"- {WARN} в ТКП несколько валют — проверь, в какой указана цена")
    else:
        md.append(f"- {WARN} валюта в ТКП не обнаружена")
    md += ["", "---", "Сформировано ove/tools/check_tkp.py — детерминированная сверка "
                      "с ove/data/suppliers.json (sourcing_rules) и ove/data/equipment.json; "
                      "инструмент ничего не выдумывает, только сопоставляет текст."]
    rc = 0 if not (cnt[WARN] or cnt[MISS]) else 1
    return "\n".join(md), rc


# ------------------------------------------------------------------------- main

def resolve_pos(sup, arg: str):
    """--pos: номер (как в ove75-rfq-NN-*.docx), класс (fan) или класс:лот (hx:2)."""
    poss = sup["positions"]
    if arg.isdigit():
        n = int(arg)
        if not 1 <= n <= len(poss):
            raise RuntimeError(f"позиция {n} вне диапазона 1–{len(poss)} (см. --list)")
        return n, poss[n - 1]
    hits = [(i, p) for i, p in enumerate(poss, 1)
            if rfq.key_of(p) == arg or (":" not in arg and p["cls"] == arg)]
    if len(hits) != 1:
        raise RuntimeError(f"--pos {arg!r}: найдено {len(hits)} позиций — укажи номер или "
                           f"класс:лот (см. --list)")
    return hits[0]


def run_check(tkp: Path, pos_arg: str, out_md):
    sup = json.loads((DATA / "suppliers.json").read_text(encoding="utf-8"))
    eq = json.loads((DATA / "equipment.json").read_text(encoding="utf-8"))
    n, pos = resolve_pos(sup, pos_arg)
    try:
        items = rfq.select_equipment(eq["items"], rfq.key_of(pos))
    except KeyError:
        raise RuntimeError(f"для позиции {rfq.key_of(pos)} нет карты EQ_SELECT в build_rfq.py")

    raw = extract_text(tkp)
    low = raw.lower().replace("ё", "е")   # длины совпадают — офсеты цитат остаются верными

    lang, curs = detect_lang_currency(raw, low)
    fields, basis = checklist_from_rules(sup.get("sourcing_rules", []))
    field_checks = check_fields(raw, low, fields, basis, curs)
    eq_groups = check_equipment(items, raw, low)

    report, rc = build_report(tkp, n, pos, field_checks, eq_groups, lang, curs, basis)
    out = Path(out_md) if out_md else tkp.with_name(tkp.stem + "-check.md")
    out.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\nАкт сверки → {out}")
    return rc, field_checks, eq_groups, report, out


def list_positions():
    sup = json.loads((DATA / "suppliers.json").read_text(encoding="utf-8"))
    for i, p in enumerate(sup["positions"], 1):
        print(f"{i:02d} · лот {p['lot']} · tier {p.get('tier', '—')} · {p['name']}  [{rfq.key_of(p)}]")


# ---------------------------------------------------------------------- самотест

def _selftest_docx(path: Path):
    """Синтетический мини-ТКП на дымосос (позиция fan:1) хелперами build_docx:
    3 полных коммерческих поля, 1 техническое расхождение (450 кВт против наших 400),
    2 пропуска (материал контактных частей и срок действия ТКП)."""
    w1, w2 = 3200, 6438
    rows = [[bd.cell("Цена за комплект (2 шт.), без НДС", w1, bold_first=True),
             bd.cell("12 500 000 руб.", w2)],
            [bd.cell("Базис поставки", w1, bold_first=True),
             bd.cell("DAP г. Мончегорск, Инкотермс 2020", w2)],
            [bd.cell("Срок изготовления", w1, bold_first=True),
             bd.cell("6 месяцев с даты авансового платежа", w2)]]
    body = [bd.p([bd.run("ТЕХНИКО-КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ № 218-ФВ от 20.08.2026", bold=True)]),
            bd.p("Предмет поставки: дымосос ДН-15БНЖ для газового тракта цеха обжига (2 шт.)."),
            bd.table(rows, [w1, w2]),
            bd.p("Производительность по газу — 72 000 м³/ч при температуре перемещаемой "
                 "среды до 400 °С; разрежение на всасе 0,004 МПа."),
            bd.p("Установленная мощность электродвигателя 450 кВт, напряжение питания 6000 В."),
            bd.p("Габаритные размеры агрегата 3200 × 2900 × 3100 мм, масса не более 9,8 тонн."),
            bd.p("Гарантия 24 месяца с даты ввода. Оплата: аванс 30 %, остаток по готовности.")]
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{"".join(body)}</w:body></w:document>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", bd.CONTENT_TYPES)
        z.writestr("_rels/.rels", bd.RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", bd.DOC_RELS)
        z.writestr("word/styles.xml", bd.STYLES)


def _selftest_xlsx(path: Path):
    """Микро-XLSX для дымоопробования извлечения sharedStrings (не полный Excel-пакет)."""
    strings = ["Позиция", "Дымосос ДН-15БНЖ", "Цена, руб. без НДС", "Срок изготовления, мес."]
    sst = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst>'
           + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet><sheetData>'
             '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
             '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>12500000</v></c></row>'
             '<row r="3"><c r="A3" t="s"><v>3</v></c><c r="B3"><v>6</v></c></row>'
             '</sheetData></worksheet>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


def selftest() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="tkp-selftest-"))
    tkp = tmp / "tkp-dymosos-postavshchik.docx"
    _selftest_docx(tkp)
    print(f"Синтетический мини-ТКП: {tkp}\n")
    rc, field_checks, eq_groups, report, out = run_check(tkp, "fan:1", None)

    flat_eq = [c for _, cs in eq_groups for c in cs]

    none = {"st": None, "msg": ""}   # отсутствие проверки — FAIL сценария, не падение

    def fld(sub):
        return next((c for c in field_checks if sub in c["label"]), none)

    def eqc(sub):
        return next((c for c in flat_eq if sub in c["label"]), none)

    checks = [
        ("поле «Цена и валюта» найдено (✅)", fld("Цена").get("st") == OK),
        ("поле «Условия поставки / базис» найдено (✅)", fld("базис").get("st") == OK),
        ("поле «Срок изготовления» найдено (✅)", fld("Срок изготовления").get("st") == OK),
        ("мощность 400 кВт → в ТКП 450 кВт помечена «⚠️ расходится»",
         eqc("400 кВт")["st"] == WARN and "у нас 400" in eqc("400 кВт")["msg"]
         and "450" in eqc("400 кВт")["msg"]),
        ("пропуск «Материал контактных частей» пойман (❌)", fld("Материал").get("st") == MISS),
        ("пропуск «Срок действия ТКП» пойман (❌)", fld("Срок действия").get("st") == MISS),
        ("производительность 72 000 ≤ «до 78 000 м³/ч» — укладывается (✅)",
         eqc("78 000 м³/ч")["st"] == OK),
        ("вердикт: без материала — «к оценке не принимается»", "НЕ ПРИНИМАЕТСЯ" in report),
        ("markdown-акт записан рядом с ТКП", out.exists() and "красный карандаш" in out.read_text(encoding="utf-8")),
    ]
    xlsx = tmp / "mini.xlsx"
    _selftest_xlsx(xlsx)
    xt = _xlsx_text(xlsx)
    checks.append(("извлечение XLSX (sharedStrings + ячейки)",
                   "Цена" in xt and "12500000" in xt and "Дымосос" in xt))

    print("\n" + "=" * 72)
    bad = 0
    for name, ok_ in checks:
        print(f"  {'OK ' if ok_ else 'FAIL'} {name}")
        bad += 0 if ok_ else 1
    print(f"САМОТЕСТ {'ПРОЙДЕН' if not bad else 'ПРОВАЛЕН'}: {len(checks) - bad}/{len(checks)} "
          f"сценариев · синтетика и акты в {tmp}")
    return 0 if not bad else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Сверка ТКП поставщика с нашим запросом (ОВЭ-75)")
    ap.add_argument("tkp", nargs="?", help="файл ТКП: .pdf / .docx / .xlsx")
    ap.add_argument("--pos", help="позиция из suppliers.json: номер, класс или класс:лот")
    ap.add_argument("--out", help="куда положить markdown-акт (по умолчанию — рядом с ТКП)")
    ap.add_argument("--list", action="store_true", help="показать номера позиций")
    ap.add_argument("--selftest", action="store_true", help="самотест на синтетическом ТКП")
    a = ap.parse_args()
    try:
        if a.selftest:
            return selftest()
        if a.list:
            list_positions()
            return 0
        if not a.tkp or not a.pos:
            ap.print_help()
            return 2
        p = Path(a.tkp)
        if not p.is_file():
            raise RuntimeError(f"файл не найден: {p}")
        rc, *_ = run_check(p, a.pos, a.out)
        return rc
    except RuntimeError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
