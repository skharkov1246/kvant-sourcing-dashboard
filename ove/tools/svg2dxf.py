#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Конвертация эскизов ОВЭ-75 из SVG в DXF со слоями — чтобы листы открывались
в nanoCAD/AutoCAD как нормальный чертёж (слои, линии, русские тексты),
а не как картинка.

Вход:  ove/public/docs/sketches/*.svg — статические эскизы (генерирует
       gen_sketches.py). Это детерминированное подмножество SVG: rect / circle /
       ellipse / line / path (M,L,H,V,Z + одиночные Q и A — сглаживаются) /
       text / g c translate/rotate, абсолютные координаты, литеральные цвета.
Выход: ove/public/docs/dxf/<то же имя>.dxf (DXF R2010, UTF-8, единицы — мм:
       1 SVG-юнит = 1 мм, $INSUNITS=4).

Слои по семантике цвета (цвет слоя = цвет палитры сайта, объекты — ByLayer):
    GAS       #e07f2e  газовый тракт            LIQUID  #2a78d6  жидкостные потоки
    DUST      #a34e9e  пылевой тракт            ELECTRIC #fab219 электрика
    INTERLOCK #d03b3b  блокировки/СБ            OUTLINE  чёрный/ink — контуры
    TEXT_RU   все тексты (высота из font-size)  DIM      размерные/вспомогательные
Прочие цвета (зелёный «продукт» и т.п.) падают на OUTLINE с истинным цветом
объекта. Пунктиры → тип линии DASHED (4-компонентный штрихпунктир → DASHDOT).
Стрелки/засечки маркеров (marker-start/-end) отрисовываются явно.

Запуск:
    python3 ove/tools/svg2dxf.py        # все эскизы + самопроверка обратным чтением
Требует: pip install ezdxf (локально). Из сборки вызывается через build():
если ezdxf не установлен (CI) — шаг пропускается с предупреждением, как в
doc_meta.py (ветка pdf).
"""

import re
import sys
from collections import Counter
from math import atan2, cos, degrees, hypot, pi, radians, sin, sqrt
from pathlib import Path
from xml.etree import ElementTree as ET

OVE_DIR = Path(__file__).resolve().parents[1]                 # .../ove
SRC_DIR = OVE_DIR / "public" / "docs" / "sketches"            # исходные SVG
OUT_DIR = OVE_DIR / "public" / "docs" / "dxf"                 # результат

# ── палитра сайта → слои ─────────────────────────────────────────────────────
# имя слоя: (ACI-цвет, точный RGB слоя или None, толщина 1/100 мм, описание)
LAYERS = {
    "GAS":       (30, (224, 127, 46), 35, "Газовый тракт (оранжевый)"),
    "LIQUID":    (5, (42, 120, 214), 35, "Жидкостные потоки: кислота, растворы, вода (синий)"),
    "DUST":      (6, (163, 78, 158), 35, "Пылевой тракт: циклоны, фильтры (фиолетовый)"),
    "ELECTRIC":  (2, (250, 178, 25), 30, "Электрика / энергоснабжение (жёлтый)"),
    "INTERLOCK": (1, (208, 59, 59), 30, "Блокировки и защиты (красный)"),
    "OUTLINE":   (7, None, 25, "Контуры оборудования, строительная графика (ink)"),
    "TEXT_RU":   (7, None, 25, "Надписи (русские, UTF-8)"),
    "DIM":       (8, None, 13, "Размерные и вспомогательные линии, рамка, сетка"),
}
# базовый RGB из SVG → слой
SEMANTIC = {
    (224, 127, 46): "GAS",
    (42, 120, 214): "LIQUID",
    (163, 78, 158): "DUST",
    (250, 178, 25): "ELECTRIC",
    (208, 59, 59): "INTERLOCK",
    (11, 11, 11): "OUTLINE",      # основной ink
    (82, 81, 78): "OUTLINE",      # вторичный ink (#52514e)
    (137, 135, 129): "DIM",       # приглушённый серый выносок (#898781)
    (127, 127, 127): "DIM",       # серые подложки
}
# цвета, которые внутри слоя рисуем ByLayer (без переопределения)
CANON = {
    "GAS": {(224, 127, 46)},
    "LIQUID": {(42, 120, 214)},
    "DUST": {(163, 78, 158)},
    "ELECTRIC": {(250, 178, 25)},
    "INTERLOCK": {(208, 59, 59)},
    "OUTLINE": {(11, 11, 11), (82, 81, 78)},
    "TEXT_RU": {(11, 11, 11), (82, 81, 78), (137, 135, 129)},
    "DIM": {(137, 135, 129)},
}
PAPER = (252, 252, 251)           # фон листа #fcfcfb — не рисуем
MIN_FILL_ALPHA = 0.15             # совсем прозрачные заливки-подложки пропускаем
TEXT_H = 0.7                      # высота DXF-текста = 0.7 × font-size (кап-высота)

NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
SVG_NS = "{http://www.w3.org/2000/svg}"


def _tag(el):
    return el.tag.split("}")[-1]


def _f(el, name, default=0.0):
    v = el.get(name)
    return float(v) if v not in (None, "") else default


# ── аффинные преобразования SVG (a b c d e f): x'=ax+cy+e, y'=bx+dy+f ───────
IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mmul(m, n):
    """Композиция m∘n (сначала применяется n)."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def mapply(m, p):
    a, b, c, d, e, f = m
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def parse_transform(s):
    """Поддерживаем то, что реально встречается в эскизах: translate, rotate, scale."""
    m = IDENT
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = [float(t) for t in NUM_RE.findall(args)]
        if name == "translate":
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
            m = mmul(m, t)
        elif name == "rotate":
            a = radians(v[0])
            r = (cos(a), sin(a), -sin(a), cos(a), 0.0, 0.0)
            if len(v) >= 3:  # rotate(a cx cy) = T(c)·R·T(-c)
                cx, cy = v[1], v[2]
                r = mmul(mmul((1, 0, 0, 1, cx, cy), r), (1, 0, 0, 1, -cx, -cy))
            m = mmul(m, r)
        elif name == "scale":
            sx = v[0]
            sy = v[1] if len(v) > 1 else sx
            m = mmul(m, (sx, 0, 0, sy, 0, 0))
        else:
            raise ValueError(f"transform «{name}» не поддерживается")
    return m


# ── цвета ────────────────────────────────────────────────────────────────────
def parse_color(s):
    """'#rrggbb' | rgba(...) → (r, g, b, a); none → None."""
    s = (s or "").strip()
    if not s or s in ("none", "transparent"):
        return None
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.match(r"rgba?\(([^)]*)\)", s)
    if m:
        p = [t.strip() for t in m.group(1).split(",")]
        a = float(p[3]) if len(p) > 3 else 1.0
        return (int(float(p[0])), int(float(p[1])), int(float(p[2])), a)
    raise ValueError(f"неизвестный цвет «{s}»")


def blend(rgb, a):
    """Полупрозрачный цвет поверх белого листа → непрозрачный RGB для DXF."""
    return tuple(int(round(a * c + (1 - a) * 255)) for c in rgb)


def classify(rgb, a):
    """Базовый цвет SVG → имя слоя."""
    if rgb == (11, 11, 11) and a < 0.5:   # rgba(11,11,11,.10) — рамка и сетка листа
        return "DIM"
    return SEMANTIC.get(rgb, "OUTLINE")


def _paint(raw, opacity):
    """Атрибут цвета → (слой, отображаемый RGB, эфф. альфа) или None (не рисуем)."""
    col = parse_color(raw)
    if col is None:
        return None
    r, g, b, a = col
    if (r, g, b) == PAPER:                # белые заливки/обводки-«маски» не нужны
        return None
    a_eff = a * opacity
    return classify((r, g, b), a), blend((r, g, b), a_eff), a_eff


def _attr(layer, disp, ltype=None):
    """dxfattribs: слой + ByLayer, если цвет канонический для слоя, иначе true color."""
    from ezdxf.colors import rgb2int
    d = {"layer": layer}
    if disp is not None and tuple(disp) not in CANON.get(layer, ()):
        d["true_color"] = rgb2int(disp)
    else:
        d["color"] = 256              # ByLayer (add_hatch по умолчанию ставит 7)
    if ltype:
        d["linetype"] = ltype
    return d


def _linetype(el):
    dash = el.get("stroke-dasharray")
    if not dash:
        return None
    return "DASHDOT" if len(NUM_RE.findall(dash)) >= 4 else "DASHED"


# ── разбор path ──────────────────────────────────────────────────────────────
def _arc_pts(p0, rx, ry, phi_deg, laf, sf, p1, n=24):
    """Дуга A/a (SVG F.6.5) → ломаная из n сегментов (без стартовой точки)."""
    if rx == 0 or ry == 0:
        return [p1]
    rx, ry, phi = abs(rx), abs(ry), radians(phi_deg)
    dx2, dy2 = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1p = cos(phi) * dx2 + sin(phi) * dy2
    y1p = -sin(phi) * dx2 + cos(phi) * dy2
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        s = sqrt(lam)
        rx, ry = rx * s, ry * s
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = sqrt(max(0.0, (rx * rx * ry * ry - den) / den)) if den else 0.0
    if laf == sf:
        co = -co
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cos(phi) * cxp - sin(phi) * cyp + (p0[0] + p1[0]) / 2
    cy = sin(phi) * cxp + cos(phi) * cyp + (p0[1] + p1[1]) / 2
    th1 = atan2((y1p - cyp) / ry, (x1p - cxp) / rx)
    dth = atan2((-y1p - cyp) / ry, (-x1p - cxp) / rx) - th1
    if not sf and dth > 0:
        dth -= 2 * pi
    elif sf and dth < 0:
        dth += 2 * pi
    out = []
    for i in range(1, n + 1):
        t = th1 + dth * i / n
        out.append((cx + rx * cos(t) * cos(phi) - ry * sin(t) * sin(phi),
                    cy + rx * cos(t) * sin(phi) + ry * sin(t) * cos(phi)))
    return out


def _quad_pts(p0, p1, p2, n=12):
    """Квадратичная Безье Q → ломаная (без стартовой точки)."""
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def parse_path(d):
    """d → список подпутей [(closed, [точки в координатах SVG])]."""
    toks = re.findall(r"[MmLlHhVvZzQqAaCcSsTt]|" + NUM_RE.pattern, d)
    pos = 0

    def more_nums():
        return pos < len(toks) and toks[pos] not in "MmLlHhVvZzQqAaCcSsTt"

    def nf():
        nonlocal pos
        v = float(toks[pos])
        pos += 1
        return v

    subs, pts = [], []
    cur = start = (0.0, 0.0)

    def flush():
        nonlocal pts
        if len(pts) >= 2:
            subs.append((False, pts))
        pts = []

    while pos < len(toks):
        c = toks[pos]
        pos += 1
        rel = c.islower()
        if c in "Mm":
            flush()
            x, y = nf(), nf()
            cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
            start = cur
            pts = [cur]
            while more_nums():           # неявные lineto после moveto
                x, y = nf(), nf()
                cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
                pts.append(cur)
        elif c in "Ll":
            while more_nums():
                x, y = nf(), nf()
                cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
                pts.append(cur)
        elif c in "Hh":
            while more_nums():
                x = nf()
                cur = (cur[0] + x if rel else x, cur[1])
                pts.append(cur)
        elif c in "Vv":
            while more_nums():
                y = nf()
                cur = (cur[0], cur[1] + y if rel else y)
                pts.append(cur)
        elif c in "Zz":
            if len(pts) >= 2:
                subs.append((True, pts))
            cur = start
            pts = [cur]
        elif c in "Qq":
            while more_nums():
                x1, y1, x, y = nf(), nf(), nf(), nf()
                c1 = (cur[0] + x1, cur[1] + y1) if rel else (x1, y1)
                p2 = (cur[0] + x, cur[1] + y) if rel else (x, y)
                pts.extend(_quad_pts(cur, c1, p2))
                cur = p2
        elif c in "Aa":
            while more_nums():
                rx, ry, rot, laf, sf = nf(), nf(), nf(), int(nf()), int(nf())
                x, y = nf(), nf()
                p2 = (cur[0] + x, cur[1] + y) if rel else (x, y)
                pts.extend(_arc_pts(cur, rx, ry, rot, laf, sf, p2))
                cur = p2
        else:
            raise ValueError(f"команда пути «{c}» вне поддерживаемого подмножества")
    flush()
    return subs


# ── геометрические помощники ─────────────────────────────────────────────────
def bulge3(s, m, e):
    """Bulge дуги по началу, середине дуги и концу (в координатах DXF)."""
    cx, cy = (s[0] + e[0]) / 2, (s[1] + e[1]) / 2
    chord = hypot(e[0] - s[0], e[1] - s[1])
    sag = hypot(m[0] - cx, m[1] - cy)
    if chord < 1e-9 or sag < 1e-9:
        return 0.0
    cross = (e[0] - s[0]) * (m[1] - s[1]) - (e[1] - s[1]) * (m[0] - s[0])
    return (2 * sag / chord) * (1 if cross > 0 else -1)


def rect_outline(x, y, w, h, r, m, flip):
    """Прямоугольник (в т.ч. со скруглением r) → вершины (x, y, bulge) для DXF."""
    r = max(0.0, min(r, w / 2, h / 2))
    if r < 1e-9:
        vs = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        return [flip(mapply(m, p)) + (0.0,) for p in vs]
    k = r * 0.7071067811865476
    # (вершина, середина дуги до следующей вершины | None) — обход по часовой в SVG
    seq = [
        ((x + r, y), None), ((x + w - r, y), (x + w - r + k, y + r - k)),
        ((x + w, y + r), None), ((x + w, y + h - r), (x + w - r + k, y + h - r + k)),
        ((x + w - r, y + h), None), ((x + r, y + h), (x + r - k, y + h - r + k)),
        ((x, y + h - r), None), ((x, y + r), (x + r - k, y + r - k)),
    ]
    out = []
    for i, (v, mid) in enumerate(seq):
        p = flip(mapply(m, v))
        b = 0.0
        if mid is not None:
            nxt = flip(mapply(m, seq[(i + 1) % len(seq)][0]))
            b = bulge3(p, flip(mapply(m, mid)), nxt)
        out.append(p + (b,))
    return out


def _norm_text(s):
    """Схлопнуть пробелы (включая NBSP/узкие) — и для DXF, и для сверки."""
    return " ".join((s or "").split())


# ── обход SVG и генерация сущностей ─────────────────────────────────────────
def _collect_markers(defs_el, ctx):
    for mk in defs_el.iter(SVG_NS + "marker"):
        prims = []
        for p in mk.iter(SVG_NS + "path"):
            prims.append({
                "subs": parse_path(p.get("d", "")),
                "fill_ctx": p.get("fill") == "context-stroke",
                "stroke_ctx": p.get("stroke") == "context-stroke",
            })
        ctx["markers"][mk.get("id")] = {
            "refx": _f(mk, "refX"), "refy": _f(mk, "refY"),
            "user_units": mk.get("markerUnits") == "userSpaceOnUse",
            "prims": prims,
        }


def _emit_marker(ref, anchor, direction, layer, disp, sw, ctx):
    """Стрелка/засечка в точке anchor (координаты SVG до переворота)."""
    m = re.search(r"url\(#([^)]+)\)", ref or "")
    mk = ctx["markers"].get(m.group(1)) if m else None
    if not mk:
        ctx["warn"].append(f"маркер «{ref}» не найден")
        return
    dl = hypot(*direction)
    ux, uy = (direction[0] / dl, direction[1] / dl) if dl > 1e-9 else (1.0, 0.0)
    s = 1.0 if mk["user_units"] else sw
    flip = ctx["flip"]
    for prim in mk["prims"]:
        for closed, pts in prim["subs"]:
            world = []
            for px, py in pts:
                lx, ly = (px - mk["refx"]) * s, (py - mk["refy"]) * s
                world.append(flip((anchor[0] + ux * lx - uy * ly,
                                   anchor[1] + uy * lx + ux * ly)))
            a = _attr(layer, disp)
            if prim["fill_ctx"] and closed and len(world) in (3, 4):
                ctx["msp"].add_solid(world, dxfattribs=a)
                ctx["count"]["SOLID"] += 1
            elif len(world) == 2:
                ctx["msp"].add_line(world[0], world[1], dxfattribs=a)
                ctx["count"]["LINE"] += 1
            elif len(world) > 2:
                ctx["msp"].add_lwpolyline(world, format="xy", close=closed, dxfattribs=a)
                ctx["count"]["LWPOLYLINE"] += 1


def _stroke_fill(el, default_fill_black=True):
    """(слой+цвет обводки, слой+цвет заливки, тип линии, толщина обводки)."""
    op = _f(el, "opacity", 1.0)
    stroke = _paint(el.get("stroke"), op) if el.get("stroke") else None
    # в SVG отсутствие fill = чёрная заливка; у наших path это стрелки-«капли»
    raw_fill = el.get("fill") if el.get("fill") is not None else ("#0b0b0b" if default_fill_black else None)
    fill = _paint(raw_fill, op) if raw_fill is not None else None
    if fill is not None and fill[2] < MIN_FILL_ALPHA:   # едва заметные подложки
        fill = None
    # размерные линии: засечки/стрелки с обеих сторон
    if stroke and el.get("marker-start") and el.get("marker-end"):
        stroke = ("DIM", stroke[1], stroke[2])
    return stroke, fill, _linetype(el), _f(el, "stroke-width", 1.0)


def _emit_rect(el, m, ctx):
    x, y, w, h = _f(el, "x"), _f(el, "y"), _f(el, "width"), _f(el, "height")
    if w <= 0 or h <= 0:
        return
    stroke, fill, lt, _ = _stroke_fill(el, False)
    pts = rect_outline(x, y, w, h, _f(el, "rx"), m, ctx["flip"])
    if fill:
        hx = ctx["msp"].add_hatch(dxfattribs=_attr(fill[0], fill[1]))
        hx.paths.add_polyline_path(pts, is_closed=True)
        ctx["count"]["HATCH"] += 1
    if stroke:
        ctx["msp"].add_lwpolyline(pts, format="xyb", close=True,
                                  dxfattribs=_attr(stroke[0], stroke[1], lt))
        ctx["count"]["LWPOLYLINE"] += 1


def _emit_circle(el, m, ctx):
    stroke, fill, lt, _ = _stroke_fill(el, False)
    c = ctx["flip"](mapply(m, (_f(el, "cx"), _f(el, "cy"))))
    a, b, cc, d = m[0], m[1], m[2], m[3]
    r = _f(el, "r") * sqrt(abs(a * d - b * cc))   # равномерный масштаб
    if r <= 0:
        return
    if fill:
        hx = ctx["msp"].add_hatch(dxfattribs=_attr(fill[0], fill[1]))
        ep = hx.paths.add_edge_path()
        ep.add_arc(c, radius=r, start_angle=0, end_angle=360)
        ctx["count"]["HATCH"] += 1
    if stroke:
        ctx["msp"].add_circle(c, r, dxfattribs=_attr(stroke[0], stroke[1], lt))
        ctx["count"]["CIRCLE"] += 1


def _emit_ellipse(el, m, ctx):
    stroke, fill, lt, _ = _stroke_fill(el, False)
    cx, cy, rx, ry = _f(el, "cx"), _f(el, "cy"), _f(el, "rx"), _f(el, "ry")
    if rx <= 0 or ry <= 0:
        return
    c = ctx["flip"](mapply(m, (cx, cy)))
    # ломаная-аппроксимация (эллипс в эскизах один и без поворота; так — проще и надёжнее)
    pts = [ctx["flip"](mapply(m, (cx + rx * cos(2 * pi * i / 48),
                                  cy + ry * sin(2 * pi * i / 48)))) for i in range(48)]
    if fill:
        hx = ctx["msp"].add_hatch(dxfattribs=_attr(fill[0], fill[1]))
        hx.paths.add_polyline_path([p + (0.0,) for p in pts], is_closed=True)
        ctx["count"]["HATCH"] += 1
    if stroke:
        vx, vy = m[0] * rx, m[1] * rx          # большая полуось после трансформа
        wx, wy = m[2] * ry, m[3] * ry
        la, lb = hypot(vx, vy), hypot(wx, wy)
        major, ratio = ((vx, -vy), lb / la) if la >= lb else ((wx, -wy), la / lb)
        ctx["msp"].add_ellipse(c, major_axis=major, ratio=min(1.0, ratio),
                               dxfattribs=_attr(stroke[0], stroke[1], lt))
        ctx["count"]["ELLIPSE"] += 1


def _emit_line(el, m, ctx):
    stroke, _, lt, sw = _stroke_fill(el, False)
    if not stroke:
        return
    p1 = mapply(m, (_f(el, "x1"), _f(el, "y1")))
    p2 = mapply(m, (_f(el, "x2"), _f(el, "y2")))
    ctx["msp"].add_line(ctx["flip"](p1), ctx["flip"](p2),
                        dxfattribs=_attr(stroke[0], stroke[1], lt))
    ctx["count"]["LINE"] += 1
    d = (p2[0] - p1[0], p2[1] - p1[1])
    if el.get("marker-start"):
        _emit_marker(el.get("marker-start"), p1, d, stroke[0], stroke[1], sw, ctx)
    if el.get("marker-end"):
        _emit_marker(el.get("marker-end"), p2, d, stroke[0], stroke[1], sw, ctx)


def _emit_path(el, m, ctx):
    stroke, fill, lt, sw = _stroke_fill(el, True)
    subs = parse_path(el.get("d", ""))
    if not subs:
        return
    tsubs = [(closed, [mapply(m, p) for p in pts]) for closed, pts in subs]
    if fill:
        hx = None
        for closed, pts in tsubs:
            if not closed:      # заливаем только явно замкнутые контуры (Z)
                continue
            if hx is None:
                hx = ctx["msp"].add_hatch(dxfattribs=_attr(fill[0], fill[1]))
                ctx["count"]["HATCH"] += 1
            hx.paths.add_polyline_path(
                [ctx["flip"](p) + (0.0,) for p in pts], is_closed=True)
    if stroke:
        for closed, pts in tsubs:
            dp = [ctx["flip"](p) for p in pts]
            if len(dp) == 2 and not closed:
                ctx["msp"].add_line(dp[0], dp[1], dxfattribs=_attr(stroke[0], stroke[1], lt))
                ctx["count"]["LINE"] += 1
            else:
                ctx["msp"].add_lwpolyline(dp, format="xy", close=closed,
                                          dxfattribs=_attr(stroke[0], stroke[1], lt))
                ctx["count"]["LWPOLYLINE"] += 1
        first, last = tsubs[0][1], tsubs[-1][1]
        if el.get("marker-start") and len(first) >= 2:
            d = (first[1][0] - first[0][0], first[1][1] - first[0][1])
            _emit_marker(el.get("marker-start"), first[0], d, stroke[0], stroke[1], sw, ctx)
        if el.get("marker-end") and len(last) >= 2:
            d = (last[-1][0] - last[-2][0], last[-1][1] - last[-2][1])
            _emit_marker(el.get("marker-end"), last[-1], d, stroke[0], stroke[1], sw, ctx)


ALIGN_MAP = {"start": "LEFT", "middle": "CENTER", "end": "RIGHT"}


def _emit_text(el, m, ctx):
    from ezdxf.enums import TextEntityAlignment
    content = _norm_text("".join(el.itertext()))
    if not content:
        return
    fs = _f(el, "font-size", 10.0)
    col = parse_color(el.get("fill") or "#0b0b0b")
    disp = blend(col[:3], col[3])
    p = ctx["flip"](mapply(m, (_f(el, "x"), _f(el, "y"))))
    rot = round((-degrees(atan2(m[1], m[0]))) % 360.0, 2) % 360.0
    t = ctx["msp"].add_text(content, dxfattribs={
        "style": "TXTRU", "height": round(fs * TEXT_H, 3), "rotation": rot,
        **_attr("TEXT_RU", disp)})
    align = ALIGN_MAP.get(el.get("text-anchor", "start"), "LEFT")
    t.set_placement(p, align=getattr(TextEntityAlignment, align))
    ctx["count"]["TEXT"] += 1


def _walk(el, m, ctx):
    tag = _tag(el)
    if tag == "defs":
        _collect_markers(el, ctx)
        return
    if el.get("transform"):
        m = mmul(m, parse_transform(el.get("transform")))
    if tag in ("svg", "g"):
        for ch in el:
            _walk(ch, m, ctx)
    elif tag == "rect":
        _emit_rect(el, m, ctx)
    elif tag == "circle":
        _emit_circle(el, m, ctx)
    elif tag == "ellipse":
        _emit_ellipse(el, m, ctx)
    elif tag == "line":
        _emit_line(el, m, ctx)
    elif tag == "path":
        _emit_path(el, m, ctx)
    elif tag == "text":
        _emit_text(el, m, ctx)
    elif tag not in ("title", "desc", "style", "marker", "tspan"):
        ctx["warn"].append(f"тег <{tag}> пропущен")


# ── конвертация одного файла ────────────────────────────────────────────────
def convert_one(svg_path, dxf_path):
    """SVG → DXF. Возвращает (Counter сущностей, число слоёв, предупреждения)."""
    import ezdxf

    root = ET.parse(svg_path).getroot()
    vb = [float(t) for t in NUM_RE.findall(root.get("viewBox", "0 0 1000 700"))]
    minx, miny, w, h = vb

    def flip(p):    # SVG (y вниз) → DXF (y вверх); 1 юнит = 1 мм
        return (p[0] - minx, (miny + h) - p[1])

    doc = ezdxf.new("R2010")            # R2010 = UTF-8, русские тексты без потерь
    doc.header["$INSUNITS"] = 4         # миллиметры
    doc.header["$MEASUREMENT"] = 1      # метрическая система
    doc.header["$LTSCALE"] = 1.0
    doc.linetypes.add("DASHED", pattern=[9.0, 6.0, -3.0],
                      description="Штриховая — — — —")
    doc.linetypes.add("DASHDOT", pattern=[14.0, 8.0, -3.0, 0.0, -3.0],
                      description="Штрихпунктирная — · — · —")
    doc.styles.add("TXTRU", font="arial.ttf")   # TTF с полной кириллицей
    for name, (aci, rgb, lw, descr) in LAYERS.items():
        layer = doc.layers.add(name, color=aci)
        layer.dxf.lineweight = lw
        if rgb:
            layer.rgb = rgb
        layer.description = descr

    ctx = {"msp": doc.modelspace(), "flip": flip, "markers": {},
           "count": Counter(), "warn": []}
    _walk(root, IDENT, ctx)

    # габариты листа (мм): при экспорте ezdxf берёт $EXTMIN/$EXTMAX из modelspace;
    # нулевой вектор он считает «не задано», поэтому чуть смещаем начало
    msp = doc.modelspace()
    msp.dxf.extmin = (-0.01, -0.01, 0)
    msp.dxf.extmax = (w, h, 0)
    msp.dxf.limmin = (0, 0)
    msp.dxf.limmax = (w, h)

    auditor = doc.audit()
    if auditor.errors:
        ctx["warn"].append(f"audit: {len(auditor.errors)} ошибок")
    doc.saveas(dxf_path)
    return ctx["count"], len(doc.layers), ctx["warn"]


def verify_one(svg_path, dxf_path):
    """Обратное чтение DXF: валидность, слои, целостность русских текстов."""
    import ezdxf

    problems = []
    doc = ezdxf.readfile(dxf_path)          # упадёт исключением, если DXF битый
    if doc.audit().errors:
        problems.append(f"{dxf_path.name}: audit нашёл ошибки")
    msp = doc.modelspace()
    dxf_texts = {e.dxf.text for e in msp.query("TEXT")}
    svg_texts = [_norm_text("".join(t.itertext()))
                 for t in ET.parse(svg_path).getroot().iter(SVG_NS + "text")]
    svg_texts = [t for t in svg_texts if t]
    missing = [t for t in svg_texts if t not in dxf_texts]
    if missing:
        problems.append(f"{dxf_path.name}: потеряно текстов {len(missing)}, "
                        f"например «{missing[0][:60]}»")
    cyr = [t for t in dxf_texts if re.search(r"[А-Яа-яЁё]", t)]
    if svg_texts and not cyr:
        problems.append(f"{dxf_path.name}: в DXF не осталось русских текстов")
    return len(msp), len(doc.layers), len(dxf_texts), problems


# ── точка входа для сборки (fail-soft) и запуска руками ─────────────────────
def build():
    """Конвертирует все эскизы; без ezdxf — пропуск с предупреждением (для CI).

    Возвращает список проблем (пустой = всё хорошо)."""
    try:
        import ezdxf  # noqa: F401
    except Exception:
        print("svg2dxf: пакет ezdxf не установлен — пропускаю DXF-эскизы "
              "(локально: pip install ezdxf)")
        return []
    svgs = sorted(SRC_DIR.glob("*.svg"))
    if not svgs:
        print(f"svg2dxf: нет исходных SVG в {SRC_DIR}")
        return []
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    problems, total = [], Counter()
    for svg in svgs:
        dxf = OUT_DIR / (svg.stem + ".dxf")
        try:
            count, _, warns = convert_one(svg, dxf)
        except Exception as e:
            problems.append(f"{svg.name}: {e}")
            continue
        for wmsg in warns:
            problems.append(f"{svg.name}: {wmsg}")
        try:
            n_ent, n_lay, n_txt, vp = verify_one(svg, dxf)
        except Exception as e:
            problems.append(f"{dxf.name}: обратное чтение не удалось ({e})")
            continue
        problems.extend(vp)
        total.update(count)
        parts = ", ".join(f"{k} {v}" for k, v in sorted(count.items()))
        print(f"  {dxf.name:44s} {n_ent:5d} сущн. ({parts}); слоёв {n_lay}, текстов {n_txt}")

    print(f"svg2dxf: {len(svgs)} SVG → DXF в {OUT_DIR} "
          f"(всего {sum(total.values())} сущностей: "
          + ", ".join(f"{k} {v}" for k, v in sorted(total.items())) + ")")
    if problems:
        print("svg2dxf ПРОБЛЕМЫ:\n  " + "\n  ".join(problems), file=sys.stderr)
    return problems


if __name__ == "__main__":
    sys.exit(1 if build() else 0)
