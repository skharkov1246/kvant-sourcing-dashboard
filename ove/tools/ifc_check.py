#!/usr/bin/env python3
"""«Кнопка приёмки ЦИМ» — входной контроль IFC-моделей от САПРщиков за минуты.

Шесть проверок перед сдачей/приёмкой (контур Таблицы 3 ТЗ на ЦИМ ред.2 и Прил.1):
  1) имя файла по правилу ТЗ — блоки шифр_стадия_объект_марка_содержание_разработчик_ПО
     (шаблон, шифр и марки берутся из ove/data/bim_plan.json) + лимит 500 МБ;
  2) иерархия IfcProject → IfcSite → IfcBuilding → IfcBuildingStorey, единицы СИ (мм/м),
     допустимая версия схемы IFC;
  3) каждый элемент привязан к этажу и имеет Name по конвенции (непустой, не заглушка);
  4) обязательные LOI-атрибуты Прил.1 для стадии БИ — перечень групп атрибутов читается
     из bim_plan.json (models[].loi_src), основание в реестре поставки — deliverables.json;
  5) геометрия: нет вырожденных/пустых тел, координаты в разумном габарите площадки
     (не километры от начала координат — по ТЗ начало СК на пересечении первых осей);
  6) GlobalId: дубликаты и корректность формата.

Результат: акт приёмки в консоль + DOCX-акт (замечание / серьёзность / адрес =
GlobalId + имя элемента) через build_docx. Вердикты: ПРИНЯТО / ПРИНЯТО С ЗАМЕЧАНИЯМИ /
ВОЗВРАТ НА ДОРАБОТКУ (любое «критично» = возврат).

Запуск:  python3 ove/tools/ifc_check.py <файл.ifc | папка>     # проверка и акт
         python3 ove/tools/ifc_check.py --selftest [папка]     # самотест на синтетике
Зависимость: pip install ifcopenshell — только локально; в сборку сайта инструмент
не входит, при отсутствии пакета модуль импортируется без ошибок и мягко сообщает
об этом (та же схема, что ветка pdf в doc_meta.py).
Коды выхода: 0 — принято (в т.ч. с замечаниями), 1 — возврат на доработку, 2 — ошибка запуска.
"""
import argparse
import json
import re
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

# внешняя зависимость: на CI её нет и не нужно — инструмент запускается локально
try:
    import ifcopenshell
    import ifcopenshell.guid as _guid
    import ifcopenshell.util.element as _uel
    import ifcopenshell.util.unit as _uu
    HAS_IFC = True
except Exception:
    HAS_IFC = False
HAS_GEOM = False
if HAS_IFC:
    try:
        import ifcopenshell.geom as _geom  # геометрическое ядро; без него — запасной путь
        HAS_GEOM = True
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

CRIT, MAJ, INFO = "критично", "существенно", "инфо"
SEV_ORDER = (CRIT, MAJ, INFO)
SEV_SHORT = {CRIT: "КРИТ", MAJ: "СУЩ ", INFO: "ИНФО"}
SEV_COLOR = {CRIT: "C00000", MAJ: "B07C00", INFO: "666666"}
V_OK, V_REM, V_RET = "ПРИНЯТО", "ПРИНЯТО С ЗАМЕЧАНИЯМИ", "ВОЗВРАТ НА ДОРАБОТКУ"
V_COLOR = {V_OK: "0CA30C", V_REM: "B07C00", V_RET: "C00000"}

CHECKS = {
    1: "Наименование файла по правилу ТЗ (разд. 7.2) и лимит 500 МБ",
    2: "Иерархия IfcProject→Site→Building→Storey, единицы СИ, версия схемы",
    3: "Привязка элементов к этажам и имена по конвенции",
    4: "LOI: обязательные атрибуты Прил.1 (стадия БИ)",
    5: "Геометрия: вырожденные тела и координаты площадки",
    6: "GlobalId: дубликаты и формат",
}

# Порог «разумного габарита площадки»: по ТЗ начало относительной СК — пересечение
# первых разбивочных осей и уровня 0,000, т.е. модель лежит около нуля. Всё, что
# дальше 2 км, — ошибка привязки (модель «уехала» в геодезические координаты).
SITE_RADIUS_M = 2000.0
MIN_EXTENT_M = 1e-4          # 0,1 мм — меньший габарит по любой оси считаем вырожденным
MAX_IFC_MB = 500             # ТЗ: файл IFC не более 500 МБ, иначе делить на части

# Коды стадии: таблица стадий ред.2 даёт единственный код CONCP, пример имени в ТЗ —
# «PD», этап объявлен как БИ; код для БИ согласуется с Заказчиком (bim_plan.open, C-79).
STAGE_CODES = ("CONCP", "PD", "BI")
CONTENT_CODES = ("MM", "FM")  # дисциплинарная сборка / обобщённая ИМ; либо высотные отметки

# Сопоставление групп LOI из bim_plan.json (models[].loi_src) с именами свойств Pset.
# Ключ — начало названия группы, значение — regex по именам свойств (рус/англ).
LOI_KEYS = {
    "местоположен": r"местоположен|корпус|здани|отметк|уровен|location|building|elevation|level|storey",
    "наимен": r"наимен|назван|name",
    "геометри": r"длин|диаметр|радиус|габарит|размер|сечен|length|diamet|radius|size",
    "характеристик": r"сред|давлен|температур|мощност|расход|производит|напор|fluid|medium|pressur|temperat|power|flow|capacit",
    "габарит": r"габарит|сечен|размер|длин|ширин|высот|глубин|dimens|size|section|width|height|length",
    "нагруз": r"нагруз|load",
    "напряжен": r"напряжен|voltag",
    "потребляем": r"мощност|power",
    "тип": r"тип|систем|system",
    "масс": r"масс|вес|mass|weight",
}


def _fnd(check: int, sev: str, addr: str, text: str) -> dict:
    return {"check": check, "sev": sev, "addr": addr, "text": text}


def _addr(o) -> str:
    return f"{getattr(o, 'GlobalId', '?')} · {getattr(o, 'Name', None) or o.is_a()}"


def _worst(fnds) -> str:
    sevs = {f["sev"] for f in fnds}
    return next((s for s in SEV_ORDER if s in sevs), INFO)


def verdict(fnds) -> str:
    w = _worst(fnds) if fnds else None
    return V_RET if w == CRIT else (V_REM if w == MAJ else V_OK)


def _split_commas(s: str) -> list:
    """Разбивка по запятым верхнего уровня (запятые внутри скобок не считаются)."""
    out, buf, depth = [], "", 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


# ---------------------------------------------------------------- правила из данных
def load_rules() -> dict:
    """Правила приёмки из ove/data/bim_plan.json (+ живые позиции ЦИМ из deliverables).

    Из bim_plan берём: шифр проекта (пример в правиле наименования), таблицу марок
    (АК=AK, СС=SS, ТХ=TX, ЭС=ES) и перечни групп LOI по дисциплинам (models[].loi_src).
    Если файла нет — работаем на запасных значениях, но честно об этом говорим.
    """
    rules = {
        "cipher": "OVE-75",
        "marks": {"AK", "SS", "TX", "ES"},
        "mark2disc": {"TX": "ТХ", "AK": "ЭОМ/СС/АК", "SS": "ЭОМ/СС/АК", "ES": "ЭОМ/СС/АК"},
        "loi": {},
        "alive": [],
        "src": "запасные значения (bim_plan.json не прочитан)",
    }
    try:
        bp = json.loads((DATA / "bim_plan.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {DATA / 'bim_plan.json'} не прочитан ({e}) — правила по умолчанию")
        return rules
    naming = (bp.get("cde") or {}).get("naming", "")
    m = re.search(r"пример\s+([A-Za-z0-9-]+)", naming)
    if m:
        rules["cipher"] = m.group(1)
    pairs = re.findall(r"([А-ЯЁ]{2,4})=([A-Z]{2})", (bp.get("models_note") or "") + naming)
    if pairs:
        rules["marks"] = {lat for _, lat in pairs}
        rules["mark2disc"] = {lat: ("ТХ" if cyr == "ТХ" else "ЭОМ/СС/АК") for cyr, lat in pairs}
    for mod in bp.get("models") or []:
        disc, src = mod.get("discipline", ""), mod.get("loi_src", "")
        if "»: " not in src:  # у лотов 2–4 перечень не расписан — берём полный из лота 1
            continue
        for grp in _split_commas(src.split("»: ")[-1]):
            rules["loi"].setdefault(disc, [])
            if grp not in rules["loi"][disc]:
                rules["loi"][disc].append(grp)
    rules["src"] = "ove/data/bim_plan.json (cde.naming, models[].loi_src)"
    # живые позиции реестра поставки про ЦИМ (v41.s != ok — не снятые ред.2) — в шапку акта
    try:
        dl = json.loads((DATA / "deliverables.json").read_text(encoding="utf-8"))
        alive = [str(it.get("id")) for it in dl.get("items", [])
                 if "ЦИМ" in str(it.get("group", "")) and (it.get("v41") or {}).get("s") != "ok"]
        rules["alive"] = sorted(alive, key=lambda s: (s.split("-")[0] != "D", int(re.sub(r"\D", "", s) or 0)))
    except Exception:
        pass
    return rules


# ---------------------------------------------------------------- проверка 1: имя файла
def check_filename(path: Path, rules: dict) -> list:
    res = []
    name = path.name
    if path.suffix.lower() != ".ifc":
        res.append(_fnd(1, CRIT, name, f"расширение «{path.suffix}» — ожидается .ifc"))
    if " " in name:
        res.append(_fnd(1, CRIT, name, "пробелы в имени — по ТЗ имя из блоков через «_», без пробелов"))
    try:
        mb = path.stat().st_size / (1024 * 1024)
        if mb > MAX_IFC_MB:
            res.append(_fnd(1, CRIT, name, f"размер {mb:.0f} МБ > {MAX_IFC_MB} МБ — по ТЗ файл делится "
                                           "на логичные части с сохранением общих координат"))
    except OSError:
        pass
    blocks = path.stem.split("_")
    if len(blocks) != 7:
        res.append(_fnd(1, CRIT, name, "ожидается 7 блоков через «_» (шифр_стадия_объект_марка_"
                                       f"содержание_разработчик_ПО), получено {len(blocks)}"))
        return res
    cipher, stage, obj, mark, content, dev, sw = blocks
    if not all(blocks):
        res.append(_fnd(1, CRIT, name, "пустые блоки в имени (два «_» подряд)"))
    if cipher != rules["cipher"]:
        res.append(_fnd(1, CRIT, name, f"шифр проекта «{cipher}» — по ТЗ «{rules['cipher']}»"))
    if stage not in STAGE_CODES:
        res.append(_fnd(1, MAJ, name, f"код стадии «{stage}» не из ожидаемых ({'/'.join(STAGE_CODES)}); "
                                      "код стадии БИ согласуется с Заказчиком (реестр C-79)"))
    if mark not in rules["marks"]:
        res.append(_fnd(1, CRIT, name, f"марка «{mark}» не из таблицы ТЗ ({', '.join(sorted(rules['marks']))})"))
    if content not in CONTENT_CODES and not re.fullmatch(r"\d{3}([.,–-]\d{3})?", content):
        res.append(_fnd(1, INFO, name, f"содержание «{content}» — не MM/FM и не похоже на высотные отметки"))
    return res


# ---------------------------------------------------------------- проверка 2: структура
def _parent(o):
    dec = getattr(o, "Decomposes", None) or []
    return dec[0].RelatingObject if dec else None


def _units_findings(f) -> list:
    res = []
    prjs = f.by_type("IfcProject")
    ua = prjs[0].UnitsInContext if prjs and prjs[0].UnitsInContext else None
    if ua is None:
        uas = f.by_type("IfcUnitAssignment")
        ua = uas[0] if uas else None
    if ua is None:
        res.append(_fnd(2, CRIT, "файл", "нет IfcUnitAssignment — единицы модели не заданы"))
        return res

    def unit_of(t):
        return next((u for u in ua.Units if getattr(u, "UnitType", None) == t), None)

    lu = unit_of("LENGTHUNIT")
    if lu is None:
        res.append(_fnd(2, CRIT, "файл", "не задана единица длины"))
    elif not (lu.is_a("IfcSIUnit") and lu.Name == "METRE" and lu.Prefix in (None, "MILLI")):
        desc = " ".join(str(x) for x in (lu.is_a(), getattr(lu, "Prefix", None), getattr(lu, "Name", None)) if x)
        res.append(_fnd(2, CRIT, "файл", f"единица длины не СИ мм/м: {desc} — по ТЗ метрическая система, масштаб 1:1"))
    for t, nm, ru in (("AREAUNIT", "SQUARE_METRE", "площади (м²)"), ("VOLUMEUNIT", "CUBIC_METRE", "объёма (м³)")):
        u = unit_of(t)
        if u is None:
            res.append(_fnd(2, INFO, "файл", f"не задана единица {ru}"))
        elif not (u.is_a("IfcSIUnit") and u.Name == nm and u.Prefix is None):
            res.append(_fnd(2, MAJ, "файл", f"единица {ru} не СИ"))
    return res


def check_structure(f) -> list:
    res = []
    if not str(f.schema).upper().startswith(("IFC2X3", "IFC4")):
        res.append(_fnd(2, CRIT, "файл", f"схема {f.schema} вне допустимых по ТЗ (IFC 2.3.0.0 / 4.0.2.1 / 4.3+)"))
    prjs = f.by_type("IfcProject")
    if len(prjs) != 1:
        res.append(_fnd(2, CRIT, "файл", f"IfcProject: найдено {len(prjs)}, должен быть ровно один"))
    sites, blds, sts = f.by_type("IfcSite"), f.by_type("IfcBuilding"), f.by_type("IfcBuildingStorey")
    if not sites:
        res.append(_fnd(2, CRIT, "файл", "нет IfcSite — нарушена иерархия IfcProject → IfcSite → "
                                         "IfcBuilding → IfcBuildingStorey"))
    for s in sites:
        p = _parent(s)
        if p is None or not p.is_a("IfcProject"):
            res.append(_fnd(2, MAJ, _addr(s), "IfcSite не агрегирован в IfcProject"))
    if not blds:
        res.append(_fnd(2, CRIT, "файл", "нет IfcBuilding"))
    for b in blds:
        p = _parent(b)
        if p is None or not p.is_a("IfcSite"):
            res.append(_fnd(2, CRIT, _addr(b), f"IfcBuilding агрегирован в {p.is_a() if p else 'ничто'}, "
                                               "а не в IfcSite"))
    if not sts:
        res.append(_fnd(2, CRIT, "файл", "нет IfcBuildingStorey — модель без этажей/уровней"))
    for st in sts:
        p = _parent(st)
        if p is None or not p.is_a("IfcBuilding"):
            res.append(_fnd(2, MAJ, _addr(st), "IfcBuildingStorey не агрегирован в IfcBuilding"))
    res += _units_findings(f)
    return res


# --------------------------------------------------- проверка 3: этажи и имена элементов
def model_elements(f) -> list:
    """Физические элементы модели: IfcElement без отверстий/виртуальных."""
    return [e for e in f.by_type("IfcElement")
            if not e.is_a("IfcFeatureElement") and not e.is_a("IfcVirtualElement")]


def _container(e):
    """Пространственный контейнер элемента: прямой или через родительский агрегат."""
    cur, guard = e, 0
    while cur is not None and guard < 32:
        rels = getattr(cur, "ContainedInStructure", None) or []
        if rels:
            return rels[0].RelatingStructure
        dec = getattr(cur, "Decomposes", None) or []
        cur = dec[0].RelatingObject if dec else None
        guard += 1
    return None


def check_containment_names(els) -> list:
    res = []
    for e in els:
        c = _container(e)
        if c is None:
            res.append(_fnd(3, MAJ, _addr(e), "не привязан к этажу (нет IfcRelContainedInSpatialStructure)"))
        elif not c.is_a("IfcBuildingStorey"):
            res.append(_fnd(3, MAJ, _addr(e), f"привязан к {c.is_a()}, а не к IfcBuildingStorey"))
        name = (getattr(e, "Name", None) or "").strip()
        if not name:
            res.append(_fnd(3, MAJ, _addr(e), "пустой Name — по конвенции каждый элемент именуется "
                                              "(обозначение позиции + наименование)"))
        elif len(name) < 3 or re.match(r"(?i)^(ifc|default|без имени|new\b|element$|элемент$)", name):
            res.append(_fnd(3, INFO, _addr(e), f"имя «{name}» похоже на заглушку"))
    return res


# ------------------------------------------------------------ проверка 4: LOI по Прил.1
def check_loi(els, disc: str, rules: dict) -> list:
    res = []
    groups = rules["loi"].get(disc) or []
    if not groups:
        res.append(_fnd(4, INFO, "файл", f"в bim_plan.json нет перечня LOI для дисциплины «{disc}» — "
                                         "проверка пропущена"))
        return res
    warned_unknown = set()
    for e in els:
        props = set()
        try:
            for pset in _uel.get_psets(e).values():
                props |= {str(k).lower() for k in pset if k != "id"}
        except Exception:
            pass
        missing = []
        for grp in groups:
            key = grp.split()[0].lower()
            # местоположение закрывается привязкой к этажу, наименование — атрибутом Name
            if key.startswith("местоположен"):
                c = _container(e)
                if c is not None and c.is_a("IfcBuildingStorey"):
                    continue
            if key.startswith("наимен") and (getattr(e, "Name", None) or "").strip():
                continue
            pat = next((v for k, v in LOI_KEYS.items() if key.startswith(k)), None)
            if pat is None:
                if key not in warned_unknown:
                    warned_unknown.add(key)
                    res.append(_fnd(4, INFO, "файл", f"группа LOI «{grp}» без правила сопоставления — пропущена"))
                continue
            if any(re.search(pat, p) for p in props):
                continue
            missing.append(grp)
        if missing:
            res.append(_fnd(4, MAJ, _addr(e), "нет обязательных LOI-атрибутов Прил.1 (стадия БИ, "
                                              f"{disc}): " + "; ".join(missing)))
    return res


# ---------------------------------------------------------------- проверка 5: геометрия
def _abs_point(e, scale: float):
    """Абсолютная точка вставки по цепочке IfcLocalPlacement (повороты не учитываем —
    для вопроса «не в километрах ли от нуля» этого достаточно)."""
    x = y = z = 0.0
    pl, guard = getattr(e, "ObjectPlacement", None), 0
    while pl is not None and pl.is_a("IfcLocalPlacement") and guard < 64:
        loc = getattr(pl.RelativePlacement, "Location", None)
        if loc is not None and getattr(loc, "Coordinates", None):
            c = list(loc.Coordinates) + [0.0, 0.0]
            x, y, z = x + c[0], y + c[1], z + c[2]
        pl, guard = pl.PlacementRelTo, guard + 1
    return x * scale, y * scale, z * scale


def _rep_items(e):
    rep = getattr(e, "Representation", None)
    for r in (getattr(rep, "Representations", None) or []):
        for it in (getattr(r, "Items", None) or []):
            yield it


def check_geometry(f, els) -> list:
    """Габариты — по локальной геометрии ядра (детерминированно, уже в метрах);
    положение — точка вставки по цепочке размещений + локальный bbox. Мировые
    координаты ядра (use-world-coords) намеренно не используем: в 0.8.x они
    нестабильны (мусорные вершины от прогона к прогону)."""
    res = []
    try:
        scale = _uu.calculate_unit_scale(f)  # коэффициент к метрам (мм -> 0.001)
    except Exception:
        scale = 1.0
    settings = None
    if HAS_GEOM:
        try:
            settings = _geom.settings()
        except Exception:
            settings = None
    for e in els:
        a = _addr(e)
        if getattr(e, "Representation", None) is None:
            res.append(_fnd(5, MAJ, a, "нет геометрического представления (Body)"))
            continue
        x, y, z = _abs_point(e, scale)
        local = None  # (minx, maxx, miny, maxy, minz, maxz) в метрах, вокруг точки вставки
        if settings is not None:
            try:
                # ссылку на shape держим до конца чтения вершин: у временного объекта
                # C++-буфер освобождается раньше, чем прочитается .verts (мусор в данных)
                shape = _geom.create_shape(settings, e)
                v = shape.geometry.verts
                if v:
                    xs, ys, zs = v[0::3], v[1::3], v[2::3]
                    local = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
                else:
                    res.append(_fnd(5, CRIT, a, "пустая геометрия (0 вершин) — вырожденное тело"))
                    continue
            except Exception:
                res.append(_fnd(5, CRIT, a, "геометрия не строится (вырожденное или битое тело)"))
                continue
        if local is not None:
            ext = (local[1] - local[0], local[3] - local[2], local[5] - local[4])
            if min(ext) < MIN_EXTENT_M:
                res.append(_fnd(5, CRIT, a, "вырожденный габарит: %.3g × %.3g × %.3g м" % ext))
            bounds = (x + local[0], x + local[1], y + local[2], y + local[3], z + local[4], z + local[5])
        else:
            # запасной путь без геометрического ядра: точка вставки + параметры экструзии
            bounds = (x, x, y, y, z, z)
            for it in _rep_items(e):
                if it.is_a("IfcExtrudedAreaSolid") and float(it.Depth) * scale < MIN_EXTENT_M:
                    res.append(_fnd(5, CRIT, a, f"вырожденная экструзия (глубина {it.Depth})"))
                    break
        dmax = max(abs(b) for b in bounds)
        if dmax > SITE_RADIUS_M:
            res.append(_fnd(5, CRIT, a, f"вне габарита площадки: {dmax / 1000:.1f} км от начала координат "
                                        f"(порог {SITE_RADIUS_M / 1000:.0f} км); по ТЗ начало СК — "
                                        "пересечение первых разбивочных осей"))
    return res


# ---------------------------------------------------------------- проверка 6: GlobalId
def check_guids(f) -> list:
    res = []
    roots = f.by_type("IfcRoot")
    cnt = Counter(r.GlobalId for r in roots)
    for gid, n in sorted(cnt.items()):
        if n > 1:
            names = ", ".join((r.Name or r.is_a()) for r in roots if r.GlobalId == gid)
            res.append(_fnd(6, CRIT, str(gid), f"GlobalId повторяется {n} раз(а): {names} — "
                                               "идентификаторы обязаны быть уникальны"))
    bad = [r for r in roots if not re.fullmatch(r"[0-9A-Za-z_$]{22}", r.GlobalId or "")]
    for r in bad[:20]:
        res.append(_fnd(6, MAJ, _addr(r), "GlobalId не в формате IFC (22 символа base64)"))
    return res


# ---------------------------------------------------------------- прогон одного файла
def run_file(path: Path, rules: dict) -> dict:
    rep = {"path": path, "findings": [], "meta": {}}
    rep["findings"] += check_filename(path, rules)
    # дисциплина LOI — по марке из имени файла; не распознали — профиль ТХ по умолчанию
    disc, mark_known = "ТХ", False
    blocks = path.stem.split("_")
    if len(blocks) == 7 and blocks[3] in rules["mark2disc"]:
        disc, mark_known = rules["mark2disc"][blocks[3]], True
    try:
        f = ifcopenshell.open(str(path))
    except Exception as e:
        rep["findings"].append(_fnd(2, CRIT, path.name, f"файл IFC не читается: {e}"))
        rep["meta"] = {"схема": "—", "размер": f"{path.stat().st_size // 1024} КБ"}
        return rep
    els = model_elements(f)
    rep["findings"] += check_structure(f)
    rep["findings"] += check_containment_names(els)
    rep["findings"] += check_loi(els, disc, rules)
    rep["findings"] += check_geometry(f, els)
    rep["findings"] += check_guids(f)
    rep["meta"] = {
        "схема": str(f.schema),
        "размер": f"{path.stat().st_size // 1024} КБ",
        "элементов": len(els),
        "этажей": len(f.by_type("IfcBuildingStorey")),
        "LOI-профиль": disc + ("" if mark_known else " (марка в имени не распознана — по умолчанию)"),
    }
    return rep


# ---------------------------------------------------------------- акт: консоль
def print_header(rules: dict) -> None:
    print("=" * 78)
    print(f"ВХОДНОЙ КОНТРОЛЬ ЦИМ (IFC) · ОВЭ-75 · {date.today().isoformat()}")
    print("Основание: ТЗ на ЦИМ ред.2 (Прил.4 к ТЗ на БИ v4.1), Таблица 3; Прил.1 LOI стадии БИ")
    line = f"Правила: {rules['src']}"
    if rules["alive"]:
        line += f" · живые позиции ЦИМ реестра: {', '.join(rules['alive'])}"
    print(line)
    print("=" * 78)


def print_report(rep: dict) -> None:
    F = rep["findings"]
    print(f"\n--- {rep['path'].name}")
    if rep["meta"]:
        print("    " + " · ".join(f"{k}: {v}" for k, v in rep["meta"].items()))
    for c in range(1, 7):
        fs = [x for x in F if x["check"] == c]
        tag = "[OK]  " if not fs else f"[{SEV_SHORT[_worst(fs)].strip()}]"
        note = "" if not fs else f" — замечаний: {len(fs)}"
        print(f"    {tag:6} {c}. {CHECKS[c]}{note}")
    if F:
        print("    Замечания:")
        for x in F[:60]:
            print(f"      [{SEV_SHORT[x['sev']]}] (пров.{x['check']}) {x['addr']}: {x['text']}")
        if len(F) > 60:
            print(f"      ... и ещё {len(F) - 60} (полный перечень — в DOCX-акте)")
    print(f"    ВЕРДИКТ: {verdict(F)}")


# ---------------------------------------------------------------- акт: DOCX
def write_act(reports: list, out: Path, rules: dict):
    """DOCX-акт приёмки через build_docx (общие стили/хелперы OOXML проекта)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build_docx as bd
    except Exception as e:  # как ветка pdf в doc_meta.py: без модуля — пропуск с предупреждением
        print(f"ПРЕДУПРЕЖДЕНИЕ: DOCX-акт пропущен — build_docx недоступен ({e})")
        return None
    today = date.today().strftime("%d.%m.%Y")
    n_by = Counter(verdict(r["findings"]) for r in reports)
    intro = ("Автоматический входной контроль IFC-моделей инструментом ove/tools/ifc_check.py. "
             "Основание: ТЗ на ЦИМ ред.2 (Приложение №4 к ТЗ на БИ v4.1 от 25.08.2026), "
             "Таблица 3 «Проверки»; Прил.1 «Требования к составу ЦИМ, LOD G и LOI» (стадия БИ).")
    if rules["alive"]:
        intro += f" Живые позиции реестра поставки по ЦИМ: {', '.join(rules['alive'])}."
    body = [
        bd.p([bd.run("ОВЭ-75 · Акт входного контроля ЦИМ (IFC)", bold=True, size="40")]),
        bd.p([bd.run(f"Составлен {today}. {intro}", color="666666", size="20")]),
        bd.p([bd.run("Сводка: ", bold=True),
              bd.run(f"файлов {len(reports)} — принято {n_by.get(V_OK, 0)}, "
                     f"с замечаниями {n_by.get(V_REM, 0)}, возврат {n_by.get(V_RET, 0)}.")]),
    ]
    wc = (600, 6438, 2600)
    wf = (1400, 700, 3200, 4338)
    for rep in reports:
        F = rep["findings"]
        v = verdict(F)
        body.append(bd.p([bd.run(rep["path"].name, bold=True), bd.run("  — " + v, bold=True, color=V_COLOR[v])],
                         style="Heading1"))
        if rep["meta"]:
            body.append(bd.p([bd.run(" · ".join(f"{k}: {val}" for k, val in rep["meta"].items()),
                                     color="666666", size="20")]))
        rows = [[bd.cell("№", wc[0], shade="F2F2F2", bold_first=True),
                 bd.cell("Проверка", wc[1], shade="F2F2F2", bold_first=True),
                 bd.cell("Результат", wc[2], shade="F2F2F2", bold_first=True)]]
        for c in range(1, 7):
            fs = [x for x in F if x["check"] == c]
            if not fs:
                cell_res = bd.cell([bd.p([bd.run("ОК", bold=True, color="0CA30C")])], wc[2])
            else:
                w = _worst(fs)
                cell_res = bd.cell([bd.p([bd.run(f"замечаний: {len(fs)} ({w})", bold=True,
                                                 color=SEV_COLOR[w])])], wc[2])
            rows.append([bd.cell(str(c), wc[0]), bd.cell(CHECKS[c], wc[1]), cell_res])
        body.append(bd.table(rows, list(wc)))
        if F:
            body.append(bd.p([bd.run(f"Замечания ({len(F)}) — адрес замечания: GlobalId + имя элемента:",
                                     bold=True)]))
            rows2 = [[bd.cell("Серьёзность", wf[0], shade="F2F2F2", bold_first=True),
                      bd.cell("Пров.", wf[1], shade="F2F2F2", bold_first=True),
                      bd.cell("Адрес (GlobalId · имя)", wf[2], shade="F2F2F2", bold_first=True),
                      bd.cell("Замечание", wf[3], shade="F2F2F2", bold_first=True)]]
            for x in F[:500]:
                rows2.append([bd.cell([bd.p([bd.run(x["sev"], bold=True, color=SEV_COLOR[x["sev"]])])], wf[0]),
                              bd.cell(str(x["check"]), wf[1]),
                              bd.cell(x["addr"], wf[2]),
                              bd.cell(x["text"], wf[3])])
            if len(F) > 500:
                rows2.append([bd.cell(f"... и ещё {len(F) - 500} замечаний", sum(wf))])
            body.append(bd.table(rows2, list(wf)))
    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
            ' w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{"".join(body)}{sect}</w:body></w:document>')
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", bd.CONTENT_TYPES)
        z.writestr("_rels/.rels", bd.RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", bd.DOC_RELS)
        z.writestr("word/styles.xml", bd.STYLES)
    return out


# ---------------------------------------------------------------- самотест на синтетике
def _new_model(with_site: bool = True):
    """Каркас синтетической модели: единицы мм/м²/м³ + иерархия до этажа."""
    f = ifcopenshell.file(schema="IFC4")
    units = f.createIfcUnitAssignment([
        f.createIfcSIUnit(None, "LENGTHUNIT", "MILLI", "METRE"),
        f.createIfcSIUnit(None, "AREAUNIT", None, "SQUARE_METRE"),
        f.createIfcSIUnit(None, "VOLUMEUNIT", None, "CUBIC_METRE"),
    ])
    origin = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None)
    ctx = f.createIfcGeometricRepresentationContext(None, "Model", 3, 1e-5, origin, None)
    prj = f.create_entity("IfcProject", GlobalId=_guid.new(), Name="ОВЭ-75",
                          RepresentationContexts=[ctx], UnitsInContext=units)

    def lp(rel, xyz=(0.0, 0.0, 0.0)):
        pt = f.createIfcCartesianPoint(tuple(float(v) for v in xyz))
        return f.createIfcLocalPlacement(rel, f.createIfcAxis2Placement3D(pt, None, None))

    top, site_pl = prj, None
    if with_site:
        site_pl = lp(None)
        site = f.create_entity("IfcSite", GlobalId=_guid.new(), Name="Площадка КГМК, Мончегорск",
                               ObjectPlacement=site_pl, CompositionType="ELEMENT")
        f.createIfcRelAggregates(_guid.new(), None, None, None, prj, [site])
        top = site
    b_pl = lp(site_pl)
    bld = f.create_entity("IfcBuilding", GlobalId=_guid.new(), Name="Объект 2.8.3 — Цех обжига",
                          ObjectPlacement=b_pl, CompositionType="ELEMENT")
    f.createIfcRelAggregates(_guid.new(), None, None, None, top, [bld])  # дефект Д2: bld виснет на prj
    s_pl = lp(b_pl)
    storey = f.create_entity("IfcBuildingStorey", GlobalId=_guid.new(), Name="Отм. +0,000",
                             ObjectPlacement=s_pl, CompositionType="ELEMENT", Elevation=0.0)
    f.createIfcRelAggregates(_guid.new(), None, None, None, bld, [storey])
    return f, ctx, storey, s_pl, lp


def _loi_props_tx(f):
    """Pset стадии БИ по перечню листа «Стадия БИ_ТХ» Прил.1 (см. bim_plan models[].loi_src)."""
    def sv(k, v):
        val = f.create_entity("IfcLabel", v) if isinstance(v, str) else f.create_entity("IfcReal", float(v))
        return f.createIfcPropertySingleValue(k, None, val, None)
    return [sv("Корпус/здание", "2.8.3"), sv("Отметка", "+0,000"), sv("Длина, мм", 6000.0),
            sv("Диаметр DN, мм", 200.0), sv("Среда", "обжиговый газ"), sv("Давление, МПа", 0.101),
            sv("Температура, °C", 350.0), sv("Мощность, кВт", 15.0), sv("Расход, м³/ч", 1200.0)]


def _add_elem(f, ctx, storey, s_pl, lp, cls, name, xyz, dims, *, gid=None, pset=True, kind="rect"):
    pos2 = f.createIfcAxis2Placement2D(f.createIfcCartesianPoint((0.0, 0.0)), None)
    if kind == "circle":
        prof = f.createIfcCircleProfileDef("AREA", None, pos2, float(dims[0]) / 2.0)
    else:
        prof = f.createIfcRectangleProfileDef("AREA", None, pos2, float(dims[0]), float(dims[1]))
    solid = f.createIfcExtrudedAreaSolid(
        prof, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None),
        f.createIfcDirection((0.0, 0.0, 1.0)), float(dims[2]))
    rep = f.createIfcShapeRepresentation(ctx, "Body", "SweptSolid", [solid])
    e = f.create_entity(cls, GlobalId=gid or _guid.new(), Name=name, ObjectPlacement=lp(s_pl, xyz),
                        Representation=f.createIfcProductDefinitionShape(None, None, [rep]))
    f.createIfcRelContainedInSpatialStructure(_guid.new(), None, None, None, [e], storey)
    if pset:
        ps = f.createIfcPropertySet(_guid.new(), None, "ОВЭ75_LOI_БИ_ТХ", None, _loi_props_tx(f))
        f.createIfcRelDefinesByProperties(_guid.new(), None, None, None, [e], ps)
    return e


def gen_valid(dst: Path, rules: dict) -> Path:
    """Эталон: имя по ТЗ, полная иерархия, СИ, этажи, LOI, чистая геометрия, GlobalId ок."""
    f, ctx, st, s_pl, lp = _new_model(with_site=True)
    _add_elem(f, ctx, st, s_pl, lp, "IfcDuctSegment", "ТХ-ГХ-001 Газоход Ду1200",
              (2000, 1500, 3000), (1200, 0, 8000), kind="circle")
    _add_elem(f, ctx, st, s_pl, lp, "IfcPipeSegment", "ТХ-ТР-014 Трубопровод Ду250",
              (4000, 2500, 1200), (273, 0, 6000), kind="circle")
    _add_elem(f, ctx, st, s_pl, lp, "IfcBuildingElementProxy", "ТХ-ОБ-201 Печь обжига КС",
              (12000, 9000, 0), (6000, 6000, 9200))
    _add_elem(f, ctx, st, s_pl, lp, "IfcBuildingElementProxy", "ТХ-ОБ-305 Насос питательный",
              (9000, 3000, 0), (1200, 800, 900))
    out = dst / f"{rules['cipher']}_CONCP_2.8.3_TX_MM_KVANT_MSCS2024.ifc"
    f.write(str(out))
    return out


def gen_defect(dst: Path) -> Path:
    """Пять намеренных дефектов: Д1 имя файла не по ТЗ; Д2 нет IfcSite; Д3 насос без
    LOI-атрибутов; Д4 бак в 25 км от начала координат; Д5 дубликат GlobalId у двух труб.
    Проверка 3 (этажи/имена) намеренно остаётся чистой — контроль ложных срабатываний."""
    f, ctx, st, s_pl, lp = _new_model(with_site=False)                      # Д2
    _add_elem(f, ctx, st, s_pl, lp, "IfcPipeSegment", "ТХ-ТР-020 Трубопровод Ду150",
              (3000, 2000, 800), (159, 0, 4000), kind="circle")             # чистый элемент
    _add_elem(f, ctx, st, s_pl, lp, "IfcBuildingElementProxy", "ТХ-ОБ-305 Насос питательный",
              (6000, 2000, 0), (1200, 800, 900), pset=False)                # Д3
    _add_elem(f, ctx, st, s_pl, lp, "IfcBuildingElementProxy", "ТХ-ЕМ-401 Бак оборотной воды",
              (25_000_000, 4000, 0), (3000, 3000, 4000))                    # Д4: 25 км
    dup = _guid.new()
    _add_elem(f, ctx, st, s_pl, lp, "IfcPipeSegment", "ТХ-ТР-777А Трубопровод Ду100",
              (8000, 1000, 500), (114, 0, 3000), gid=dup, kind="circle")    # Д5
    _add_elem(f, ctx, st, s_pl, lp, "IfcPipeSegment", "ТХ-ТР-777Б Трубопровод Ду100",
              (8000, 3000, 500), (114, 0, 3000), gid=dup, kind="circle")    # Д5 (тот же GlobalId)
    out = dst / "ЦИМ обжиг (ТХ) финал v2.ifc"                               # Д1
    f.write(str(out))
    return out


def selftest(dir_arg: str) -> int:
    dst = Path(dir_arg) if dir_arg else Path(tempfile.mkdtemp(prefix="ifc_check_selftest_"))
    dst.mkdir(parents=True, exist_ok=True)
    rules = load_rules()
    p_ok, p_bad = gen_valid(dst, rules), gen_defect(dst)
    print(f"Синтетика ifcopenshell {ifcopenshell.version} → {dst}")
    print(f"  эталон:    {p_ok.name} ({p_ok.stat().st_size // 1024} КБ)")
    print(f"  с дефектами: {p_bad.name} ({p_bad.stat().st_size // 1024} КБ)")
    print_header(rules)
    r_ok, r_bad = run_file(p_ok, rules), run_file(p_bad, rules)
    print_report(r_ok)
    print_report(r_bad)
    act = write_act([r_ok, r_bad], dst / "akt-priemki-cim-selftest.docx", rules)

    errors = []
    if r_ok["findings"]:
        errors.append(f"валидный файл получил {len(r_ok['findings'])} замечаний: {r_ok['findings'][:3]}")
    if verdict(r_ok["findings"]) != V_OK:
        errors.append(f"вердикт валидного файла: {verdict(r_ok['findings'])} (ожидалось {V_OK})")
    expected = {1: "Д1 имя файла не по правилу ТЗ", 2: "Д2 отсутствует IfcSite в иерархии",
                4: "Д3 насос без LOI-атрибутов Прил.1", 5: "Д4 бак в 25 км от начала координат",
                6: "Д5 дубликат GlobalId у двух труб"}
    got = {c: [x for x in r_bad["findings"] if x["check"] == c] for c in range(1, 7)}
    print("\nСАМОТЕСТ — ловля намеренных дефектов:")
    for c, label in expected.items():
        ok = bool(got[c])
        print(f"  {'ПОЙМАН  ' if ok else 'ПРОПУЩЕН'} {label} -> проверка {c} ({len(got[c])} замеч.)")
        if not ok:
            errors.append(f"дефект не пойман: {label}")
    if got[3]:
        errors.append(f"ложные срабатывания проверки 3: {got[3]}")
    else:
        print("  ЧИСТО    проверка 3 (этажи/имена): дефект не закладывался, замечаний нет")
    if verdict(r_bad["findings"]) != V_RET:
        errors.append(f"вердикт дефектного файла: {verdict(r_bad['findings'])} (ожидалось {V_RET})")
    if act is not None and act.exists() and act.stat().st_size > 2000:
        print(f"  DOCX-акт: {act} ({act.stat().st_size // 1024} КБ)")
    else:
        errors.append("DOCX-акт не собрался")
    if errors:
        print("\nСАМОТЕСТ ПРОВАЛЕН:")
        for e in errors:
            print("  -", e)
        return 1
    print("\nСАМОТЕСТ ПРОЙДЕН: эталон принят без замечаний, дефектный файл ловит все 5 дефектов.")
    return 0


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Кнопка приёмки ЦИМ: 6 проверок IFC-моделей, акт в консоль и DOCX")
    ap.add_argument("path", nargs="?", help="файл .ifc или папка с моделями")
    ap.add_argument("--selftest", nargs="?", const="", metavar="ПАПКА",
                    help="самотест: два синтетических файла (эталон + 5 дефектов)")
    ap.add_argument("--out", help="путь DOCX-акта (по умолчанию рядом с моделями)")
    ap.add_argument("--no-docx", action="store_true", help="без DOCX-акта, только консоль")
    a = ap.parse_args(argv)
    if not HAS_IFC:
        print("ПРЕДУПРЕЖДЕНИЕ: ifcopenshell не установлен — проверка ЦИМ пропущена. "
              "Инструмент локальный: pip install ifcopenshell")
        return 2
    if a.selftest is not None:
        return selftest(a.selftest)
    if not a.path:
        ap.print_help()
        return 2
    src = Path(a.path)
    if src.is_dir():
        paths = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() == ".ifc")
    elif src.is_file():
        paths = [src]
    else:
        print(f"нет такого файла или папки: {src}")
        return 2
    if not paths:
        print(f"в {src} нет файлов .ifc")
        return 2
    rules = load_rules()
    print_header(rules)
    reports = [run_file(p, rules) for p in paths]
    for r in reports:
        print_report(r)
    n_by = Counter(verdict(r["findings"]) for r in reports)
    print(f"\nИТОГ: файлов {len(reports)} — принято {n_by.get(V_OK, 0)}, "
          f"с замечаниями {n_by.get(V_REM, 0)}, возврат {n_by.get(V_RET, 0)}")
    if not a.no_docx:
        base = src if src.is_dir() else src.parent
        out = Path(a.out) if a.out else base / f"akt-priemki-cim-{date.today().isoformat()}.docx"
        act = write_act(reports, out, rules)
        if act:
            print(f"DOCX-акт → {act} ({act.stat().st_size // 1024} КБ)")
    return 1 if n_by.get(V_RET, 0) else 0


if __name__ == "__main__":
    sys.exit(main())
