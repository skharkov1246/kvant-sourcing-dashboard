#!/usr/bin/env python3
"""Библиотека оборудования ОВЭ-75 в IFC4 — чтобы моделлер ЦИМ стартовал не с нуля.

Из ove/data/equipment.json (70 позиций базы оборудования: лот/участок/наименование/
материал/габариты/мощность/количество) генерируется библиотека типов:
  * на каждую позицию — IfcElementType подходящего класса (насос → IfcPumpType,
    дымосос → IfcFanType, ёмкость/бак/реактор → IfcTankType, кран/транспортёр →
    IfcTransportElementType, горелка → IfcBurnerType и т.д.; чего в схеме IFC4 нет —
    честный дефолт IfcBuildingElementProxyType);
  * габаритное тело-заглушка (Box) там, где из поля dims извлекается габарит:
    «A × B × C мм», Ø+L (лежачий шнек), Ø+Н (тарелка), «D = X м» (куб по диаметру),
    объём «V/объём X м³» (куб по кубокорню). Не извлёкся — тип без геометрии;
  * Pset_KVANT_LOI с атрибутами из базы (что есть): Позиция, Наименование, Лот,
    Мощность_кВт, Материал, Количество, Масса_т. Русские строки — utf-8; в файле
    IFC-SPF они по ISO-10303-21 кодируются \\X2\\-последовательностями и читаются
    обратно без потерь (самопроверка это сверяет).

GlobalId типов детерминированные (md5 от позиции базы) — пересборка не «трясёт» файлы,
одна и та же позиция имеет один GlobalId и в общей библиотеке, и в лотовом файле.

Выход: ove/public/docs/cim/ove75-equip-lib.ifc (вся база)
       ove/public/docs/cim/ove75-equip-lot{N}.ifc (по лотам; лот 0 — «вне БИ»)

Запуск:  python3 ove/tools/equip2ifc.py            # генерация + самопроверка
         python3 ove/tools/equip2ifc.py --out DIR  # сложить файлы в другую папку
Зависимость: pip install ifcopenshell — только локально; в сборке сайта при
отсутствии пакета build() мягко пропускается с предупреждением (та же схема,
что ветка pdf в doc_meta.py) — файлы в docs/cim остаются прежними.
Коды выхода: 0 — сгенерировано и проверено, 1 — самопроверка провалена, 2 — нет пакета.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

# внешняя зависимость: на CI её нет и не нужно — инструмент запускается локально
try:
    import ifcopenshell
    import ifcopenshell.guid as _guid
    import ifcopenshell.util.element as _uel
    HAS_IFC = True
except Exception:
    HAS_IFC = False

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "equipment.json"
OUT = ROOT / "public" / "docs" / "cim"
ORG = "ООО «КВАНТ»"
NUM = r"\d+(?:[.,]\d+)?"

# Класс IFC по имени позиции: первое совпавшее правило. Порядок важен: «Ёмкость …
# с насосным оборудованием» — ёмкость, а не насос; «фильтрующая центрифуга» — не фильтр.
# IfcFurnaceType в схемах IFC не существует (печей buildingSMART не завёл) — печь при
# генерации честно упадёт в IfcBuildingElementProxyType через фолбэк по схеме.
CLASS_RULES = (
    (r"горелк", "IfcBurnerType"),                           # раньше печи: «горелка (разогрев печи)»
    (r"печь|печи\b", "IfcFurnaceType"),
    (r"кот[её]л", "IfcBoilerType"),
    (r"дымосос|вентилятор|воздуходувк", "IfcFanType"),
    (r"испарител", "IfcEvaporatorType"),
    (r"центрифуг", "IfcBuildingElementProxyType"),          # у центрифуги класса нет
    (r"ошиновк", "IfcCableSegmentType"),                    # шинопровод (BUSBAR по смыслу)
    (r"ёмкост|емкост|\bбак\b|\bбака\b|баки\b|бункер|резервуар|репульпатор|реактор|"
     r"сгуститель|\bванн[аы]\b", "IfcTankType"),
    (r"насос|эжектор", "IfcPumpType"),                      # эжектор — струйный вакуум-насос
    (r"фильтр|циклон", "IfcFilterType"),                    # циклон — пылеотделитель
    (r"теплообменник|холодильник", "IfcHeatExchangerType"),
    (r"конденсатор", "IfcCondenserType"),
    (r"затвор", "IfcDamperType"),                           # дроссельный затвор газохода
    # «транспортировка\b» — именительный падеж (система транспортировки как позиция);
    # косвенный («подготовка к транспортировке» у оборудования маркировки) не считается
    (r"\bкран|транспорт[её]р|транспортировка\b|конвейер", "IfcTransportElementType"),
    (r"выпрямител|трансформатор", "IfcTransformerType"),
    (r"генератор", "IfcElectricGeneratorType"),
)
DEFAULT_CLASS = "IfcBuildingElementProxyType"


def _f(s: str) -> float:
    return float(str(s).replace(",", "."))


def _gid(seed: str) -> str:
    """Детерминированный GlobalId: md5 от смыслового ключа, сжатый в base64 IFC."""
    return _guid.compress(hashlib.md5(seed.encode("utf-8")).hexdigest())


def pick_class(name: str) -> str:
    low = name.lower()
    return next((c for pat, c in CLASS_RULES if re.search(pat, low)), DEFAULT_CLASS)


def parse_dims(s: str):
    """Габарит (dx, dy, dz) в мм из строки dims или None — тогда тип без тела.

    Правила консервативные: числа без явного габаритного контекста (мощности, НЗП
    в тоннах, площади м², расходы м³/ч) габаритом не считаются.
    """
    if not s:
        return None
    m = re.search(rf"({NUM})\s*[×x]\s*({NUM})\s*[×x]\s*({NUM})\s*(мм|м)\b", s)
    if m:  # прямые габариты «8485 × 1155 × 1650 мм»
        k = 1.0 if m.group(4) == "мм" else 1000.0
        return _f(m.group(1)) * k, _f(m.group(2)) * k, _f(m.group(3)) * k
    kw = {}
    for key, pat in (("dl", r"длин\w*"), ("sh", r"ширин\w*"), ("vy", r"высот\w*")):
        mk = re.search(rf"{pat}[^;,\d]*({NUM})\s*мм", s, re.I)
        if mk:
            kw[key] = _f(mk.group(1))
    if len(kw) == 3:  # «Ширина ленты 400 мм, средняя длина 3000 мм, высота слоя 100 мм»
        return kw["dl"], kw["sh"], kw["vy"]
    dias = [_f(x) for x in re.findall(rf"[Øø]\s*({NUM})", s)]
    lens = [_f(x) for x in re.findall(rf"\bL\s*({NUM})", s)]
    heis = [_f(x) for x in re.findall(rf"\b[НH]\s*({NUM})", s)]
    if dias and lens:  # лежачая труба/шнек: длина по X; вариантов несколько — оболочка по max
        return max(lens), max(dias), max(dias)
    if dias and heis:  # тарелка/диск: Ø в плане, Н по вертикали
        return max(dias), max(dias), max(heis)
    m = re.search(rf"\bD\s*=\s*({NUM})\s*м\b", s)
    if m:  # только диаметр вертикального аппарата — условный куб по диаметру
        d = _f(m.group(1)) * 1000.0
        return d, d, d
    m = re.search(rf"\bV\s*=\s*({NUM})\s*м³", s) or re.search(rf"[Оо]бъ[её]м[^;]*?({NUM})\s*м³(?!/)", s)
    if m:  # только объём — условный куб со стороной по кубокорню
        a = _f(m.group(1)) ** (1.0 / 3.0) * 1000.0
        return a, a, a
    return None


def parse_power(s: str):
    """Одиночное чистое значение «37 кВт» → число; диапазоны/киловольты — как текст."""
    if not s or s == "—":
        return None
    m = re.fullmatch(rf"\s*({NUM})\s*кВт\s*", s)
    return _f(m.group(1)) if m else s.strip()


def parse_qty(s):
    if s is None:
        return None
    s = str(s).strip()
    if not s or s == "—":
        return None
    return int(s) if re.fullmatch(r"\d+", s) else s


def parse_mass(it: dict):
    """Масса, т — в текущей базе поля нет; поддержано на будущее (атрибут «что есть»)."""
    for k in ("mass_t", "mass", "масса_т", "масса"):
        if it.get(k) not in (None, "", "—"):
            try:
                return _f(it[k])
            except (TypeError, ValueError):
                return str(it[k])
    return None


# ---------------------------------------------------------------- сборка модели IFC
def _sv(f, name: str, val):
    """IfcPropertySingleValue с типом по значению: int → IfcInteger, float → IfcReal,
    строка → IfcText (русские строки любой длины); ifc_type — принудительный тип."""
    if isinstance(val, bool) or val is None:
        raise ValueError(f"свойство {name}: неподдерживаемое значение {val!r}")
    if isinstance(val, int):
        v = f.create_entity("IfcInteger", val)
    elif isinstance(val, float):
        v = f.create_entity("IfcReal", val)
    else:
        v = f.create_entity("IfcText", str(val))
    return f.createIfcPropertySingleValue(name, None, v, None)


def _pset_props(f, it: dict) -> list:
    """Атрибуты Pset_KVANT_LOI из базы — только те, что в базе есть (без выдумок)."""
    props = []
    if it.get("pos"):
        props.append(f.createIfcPropertySingleValue(
            "Позиция", None, f.create_entity("IfcIdentifier", str(it["pos"])), None))
    props.append(_sv(f, "Наименование", it["name"]))
    props.append(_sv(f, "Лот", int(it["lot"])))
    for key, parsed in (("Мощность_кВт", parse_power(it.get("power"))),
                        ("Материал", None if it.get("material") in (None, "", "—") else it["material"]),
                        ("Количество", parse_qty(it.get("qty"))),
                        ("Масса_т", parse_mass(it))):
        if parsed is not None:
            props.append(_sv(f, key, parsed))
    return props


def _box_map(f, ctx, dims):
    """Габаритное тело-заглушка: Box (dx × dy) в плане с центром в начале координат,
    выдавленный вверх на dz, — как IfcRepresentationMap для типа."""
    dx, dy, dz = (float(v) for v in dims)
    prof = f.createIfcRectangleProfileDef(
        "AREA", None, f.createIfcAxis2Placement2D(f.createIfcCartesianPoint((0.0, 0.0)), None), dx, dy)
    solid = f.createIfcExtrudedAreaSolid(
        prof, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None),
        f.createIfcDirection((0.0, 0.0, 1.0)), dz)
    shape = f.createIfcShapeRepresentation(ctx, "Body", "SweptSolid", [solid])
    origin = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None)
    return f.createIfcRepresentationMap(origin, shape)


def make_library(rows, fname: str, title: str):
    """Файл-библиотека IFC4 из позиций rows = [(глобальный №, позиция), …].

    Возвращает (модель, статистика). GlobalId типа зависит только от позиции базы
    (не от файла) — в библиотеке и лотовом файле у позиции один идентификатор.
    """
    f = ifcopenshell.file(schema="IFC4")
    schema_names = {d.name() for d in ifcopenshell.ifcopenshell_wrapper.schema_by_name("IFC4").declarations()}
    units = f.createIfcUnitAssignment([
        f.createIfcSIUnit(None, "LENGTHUNIT", "MILLI", "METRE"),
        f.createIfcSIUnit(None, "AREAUNIT", None, "SQUARE_METRE"),
        f.createIfcSIUnit(None, "VOLUMEUNIT", None, "CUBIC_METRE"),
    ])
    origin = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None)
    ctx = f.createIfcGeometricRepresentationContext(None, "Model", 3, 1e-5, origin, None)
    prj = f.create_entity("IfcProject", GlobalId=_gid(f"ove75-equip-prj|{fname}"), Name=title,
                          Description="Библиотека типов оборудования по ove/data/equipment.json (стадия БИ)",
                          RepresentationContexts=[ctx], UnitsInContext=units)

    stat = {"classes": Counter(), "bodies": 0, "fallback": []}
    types = []
    for gidx, it in rows:
        cls = pick_class(it["name"])
        if cls not in schema_names:  # желаемого класса нет в IFC4 (например, IfcFurnaceType)
            stat["fallback"].append((it["name"], cls))
            cls = DEFAULT_CLASS
        pset = f.createIfcPropertySet(_gid(f"ove75-equip-pset|{gidx}|{it['lot']}|{it['name']}"),
                                      None, "Pset_KVANT_LOI", None, _pset_props(f, it))
        dims = parse_dims(it.get("dims", ""))
        model = (it.get("model") or "").strip()
        t = f.create_entity(
            cls,
            GlobalId=_gid(f"ove75-equip|{gidx}|{it['lot']}|{it['name']}"),
            Name=it["name"],
            Description=it.get("purpose") or None,
            HasPropertySets=[pset],
            RepresentationMaps=[_box_map(f, ctx, dims)] if dims else None,
            Tag=f"ОВЭ75-Л{it['lot']}-{gidx + 1:02d}",
            ElementType=model if model and model != "—" else it.get("area") or it["name"],
            PredefinedType="USERDEFINED",
        )
        types.append(t)
        stat["classes"][cls] += 1
        stat["bodies"] += bool(dims)
    f.create_entity("IfcRelDeclares", GlobalId=_gid(f"ove75-equip-decl|{fname}"),
                    RelatingContext=prj, RelatedDefinitions=types)
    h = f.header
    h.file_name.name = fname
    h.file_name.author = [ORG]
    h.file_name.organization = [ORG]
    h.file_name.originating_system = "ove/tools/equip2ifc.py (IfcOpenShell)"
    h.file_name.authorization = "проект ОВЭ-75, стадия БИ"
    h.file_description.description = ["ViewDefinition [ReferenceView]"]
    return f, stat


def build(out_dir=None) -> list:
    """Полная генерация: общая библиотека + по-лотовые файлы. Fail-soft для сборки:
    без ifcopenshell или без данных — предупреждение и пропуск, файлы остаются прежними."""
    if not HAS_IFC:
        print("ПРЕДУПРЕЖДЕНИЕ: ifcopenshell не установлен — IFC-библиотека оборудования "
              "не пересобрана (docs/cim остаётся прежним). Инструмент локальный: pip install ifcopenshell")
        return []
    try:
        data = json.loads(SRC.read_text(encoding="utf-8"))
        items = data["items"]
    except Exception as e:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {SRC} не прочитан ({e}) — IFC-библиотека пропущена")
        return []
    out = Path(out_dir) if out_dir else OUT
    out.mkdir(parents=True, exist_ok=True)
    rows = list(enumerate(items))
    written = []

    lib, stat = make_library(rows, "ove75-equip-lib.ifc", "ОВЭ-75 · Библиотека оборудования")
    p = out / "ove75-equip-lib.ifc"
    lib.write(str(p))
    written.append((p, rows))
    for nm, cls in stat["fallback"]:
        print(f"  замечание: для «{nm}» класса {cls} нет в схеме IFC4 — тип записан как {DEFAULT_CLASS}")
    print(f"IFC-библиотека: {len(rows)} типов, {stat['bodies']} с габаритным телом, "
          f"{len(rows) - stat['bodies']} без тела (в dims нет пригодных габаритов) → {p.name} "
          f"({p.stat().st_size // 1024} КБ)")
    print("  классы: " + ", ".join(f"{c.replace('Ifc', '').replace('Type', '')} {n}"
                                   for c, n in stat["classes"].most_common()))

    for lot in sorted({it["lot"] for it in items}):
        sub = [(i, it) for i, it in rows if it["lot"] == lot]
        fname = f"ove75-equip-lot{lot}.ifc"
        title = f"ОВЭ-75 · Оборудование лота №{lot}" if lot else "ОВЭ-75 · Оборудование вне БИ (лот 0)"
        m, st = make_library(sub, fname, title)
        p = out / fname
        m.write(str(p))
        written.append((p, sub))
        print(f"  лот {lot}: {len(sub)} типов, {st['bodies']} с телом → {p.name} ({p.stat().st_size // 1024} КБ)")
    return written


# ---------------------------------------------------------------- самопроверка
def _fmt_val(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


def _box_of(t):
    """Габарит типа из перечитанного файла (профиль × глубина выдавливания), мм."""
    if not t.RepresentationMaps:
        return "без тела"
    solid = t.RepresentationMaps[0].MappedRepresentation.Items[0]
    prof = solid.SweptArea
    return f"Box {prof.XDim:g} × {prof.YDim:g} × {solid.Depth:g} мм"


def verify(written) -> int:
    """Обратное чтение каждого файла: счётчики типов, Pset и русские строки без потерь;
    в конце — 5 примеров из общей библиотеки с их Pset_KVANT_LOI."""
    errors = []
    for path, rows in written:
        try:
            f = ifcopenshell.open(str(path))
        except Exception as e:
            errors.append(f"{path.name}: не читается ifcopenshell ({e})")
            continue
        types = f.by_type("IfcTypeProduct")
        if len(types) != len(rows):
            errors.append(f"{path.name}: типов {len(types)}, ожидалось {len(rows)}")
        by_tag = {f"ОВЭ75-Л{it['lot']}-{i + 1:02d}": it for i, it in rows}
        for t in types:
            it = by_tag.get(t.Tag)
            ps = _uel.get_psets(t).get("Pset_KVANT_LOI")
            if it is None:
                errors.append(f"{path.name}: лишний тип {t.Tag} «{t.Name}»")
            elif ps is None:
                errors.append(f"{path.name}: у «{t.Name}» нет Pset_KVANT_LOI")
            elif not (t.Name == it["name"] and ps.get("Наименование") == it["name"]
                      and ps.get("Лот") == it["lot"]):
                errors.append(f"{path.name}: «{it['name']}» — атрибуты/utf-8 не сошлись "
                              f"(Name={t.Name!r}, Pset={ps.get('Наименование')!r}, Лот={ps.get('Лот')!r})")
        dup = [g for g, n in Counter(t.GlobalId for t in types).items() if n > 1]
        if dup:
            errors.append(f"{path.name}: дубликаты GlobalId: {dup}")

    lib_path, lib_rows = written[0]
    f = ifcopenshell.open(str(lib_path))
    print(f"\nПроверка обратным чтением: файлов {len(written)}, "
          f"в библиотеке {len(f.by_type('IfcTypeProduct'))} типов — "
          + ("ОК" if not errors else f"ошибок {len(errors)}"))
    print("Примеры (5 позиций из ove75-equip-lib.ifc):")
    prefer = ("Печь кипящего слоя", "Котёл-утилизатор", "Дымосос", "Электролизная ванна",
              "Испаритель I ступени (выпарной аппарат)")
    types = f.by_type("IfcTypeProduct")
    picked, seen = [], set()
    for pref in prefer:
        t = next((x for x in types if (x.Name or "").startswith(pref) and x.Tag not in seen), None)
        if t:
            picked.append(t)
            seen.add(t.Tag)
    picked += [t for t in types if t.Tag not in seen][: 5 - len(picked)]
    for t in picked[:5]:
        print(f"  #{t.Tag} · {t.is_a()} · «{t.Name}» · {_box_of(t)}")
        ps = _uel.get_psets(t).get("Pset_KVANT_LOI") or {}
        for k, v in ps.items():
            if k != "id":
                print(f"      {k}: {_fmt_val(v)}")
    if errors:
        print("\nСАМОПРОВЕРКА ПРОВАЛЕНА:")
        for e in errors:
            print("  -", e)
        return 1
    print("\nСАМОПРОВЕРКА ПРОЙДЕНА: все файлы читаются, счётчики сходятся, "
          "Pset_KVANT_LOI и русские строки на месте.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="equipment.json → библиотека IFC4-типов оборудования ОВЭ-75 (+ по-лотовые файлы)")
    ap.add_argument("--out", help=f"папка результата (по умолчанию {OUT})")
    a = ap.parse_args(argv)
    if not HAS_IFC:
        print("ПРЕДУПРЕЖДЕНИЕ: ifcopenshell не установлен — генерация IFC пропущена. "
              "Инструмент локальный: pip install ifcopenshell")
        return 2
    written = build(a.out)
    if not written:
        return 2
    return verify(written)


if __name__ == "__main__":
    sys.exit(main())
