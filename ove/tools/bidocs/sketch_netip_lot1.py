#!/usr/bin/env python3
"""ОВЭ75-БИ-Л1-ЭЧ — Эскизные чертежи нетипового оборудования Лота №1 (пункт D-20).

Лист 1: печь кипящего слоя — эскиз общего вида (продольный разрез-схема с
футеровкой, подиной с соплами, газовой коробкой, патрубками; узлы сопла и стенки;
схема подины; таблица патрубков и закладных). Геометрия — из базы решений
(ИД ч.1 табл. 23 п.214–217, разд. 2.3; высоты слоя — ИД табл. 23; отметки —
компоновка БИ). Стиль — как у существующих эскизов сайта (docs/sketches).

build(row) -> list[Path]: пути SVG (docframe кладёт их на лист A3 с основной
надписью). SVG сохраняется в ove/public/docs/sketches/ove75-lot1-pech-ks-eskiz.svg.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as c  # noqa: E402

OUT = c.PUBLIC / "docs" / "sketches" / "ove75-lot1-pech-ks-eskiz.svg"

# палитра существующих эскизов
BG, FRAME = "#fcfcfb", "rgba(11,11,11,.10)"
INK, TXT, MUTED = "#0b0b0b", "#52514e", "#898781"
BLUE, ORANGE, RED, PURPLE = "#2a78d6", "#e07f2e", "#d03b3b", "#a34e9e"


# ---------------------------------------------------------------- данные

def _num(s, default):
    v = c.num(s)
    return default if v is None else v


def furnace_data() -> dict:
    """Числа для листа — из базы решений; при отсутствии — значения ИД по умолчанию."""
    d = {"d_pod": 6000, "d_top": 8390, "h_total": 9200, "h_cone": 6450, "n_nozzle": 583,
         "d_duct": 2000, "bed_bulk": 1647, "bed_lo": 1918, "bed_hi": 2144, "t_bed": 930,
         "load_t": 139, "stay_h": 9, "blast": "11 342 / 19 146 нм³/ч", "o2": "52,0 / 30,8 % O₂",
         "t_blast": "25 / 455,3 °C", "p_blast": "0,09–0,12 МПа", "gas": "12 129 / 19 934 нм³/ч",
         "t_gas": "913 / 919 °C", "dust": "241,5 г/нм³", "so2": "17,83 / 10,87 %",
         "dp": "52,3 / 53,3 кПа", "otm_pod": "+4,500", "otm_svod": "≈ +13,700",
         "feed": "15,45 (до 17,8) т/ч", "zab": "Ø150 · L1500 · до 20 т/ч",
         "shamot": 350, "cooler": "D2424-6"}
    for s in c.bi_sections(1):
        for i in s.get("items") or []:
            lab, val = i.get("label", ""), str(i.get("value", ""))
            if lab.startswith("Печь КС: геометрия"):
                m = re.search(r"Ø\s*(\d+)\s*/\s*(\d+)", val)
                if m:
                    d["d_pod"], d["d_top"] = int(m.group(1)), int(m.group(2))
                m = re.search(r"H\s*=\s*(\d+)", val)
                if m:
                    d["h_total"] = int(m.group(1))
                m = re.search(r"(\d+)\s*сопл", val)
                if m:
                    d["n_nozzle"] = int(m.group(1))
            elif lab.startswith("Температура кипящего слоя"):
                m = re.search(r"(\d+)\s*°C\s*/\s*(\d+)\s*ч.*?(\d+)\s*т", val)
                if m:
                    d["t_bed"], d["stay_h"], d["load_t"] = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif lab.startswith("Отметка подины печи"):
                m = re.search(r"\+\s*(\d+,\d)", val)
                if m:
                    d["otm_pod"] = "+" + m.group(1).replace(",", ",") + "00"
        body = s.get("body") or ""
        m = re.search(r"слой\s+(\d+)\s*мм\s*\(насыпной\)\s*/\s*(\d+)[–-](\d+)\s*мм\s*\(кипящий\)", body)
        if m:
            d["bed_bulk"], d["bed_lo"], d["bed_hi"] = int(m.group(1)), int(m.group(2)), int(m.group(3))
    for b in c.load("calc").get("blocks", []):
        if b.get("id") == "L1-BED":
            for i in b.get("inputs", []):
                m = re.search(r"высота конуса\s*(\d[\d\s]*)", i.get("v", ""))
                if m:
                    d["h_cone"] = int(m.group(1).replace(" ", ""))
    return d


# ---------------------------------------------------------------- svg

def T(x, y, s, size=8.5, fill=TXT, anchor="start", weight=None, rot=None, halo=False):
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    w = f' font-weight="{weight}"' if weight else ""
    r = f' transform="rotate({rot} {x} {y})"' if rot is not None else ""
    h = f' stroke="{BG}" stroke-width="3" paint-order="stroke" stroke-linejoin="round"' if halo else ""
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}"{a}{w}{r}{h}>{s}</text>'


def L(x1, y1, x2, y2, stroke=TXT, w=1, dash=None, ms=None, me=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    a = (f' marker-start="url(#{ms})"' if ms else "") + (f' marker-end="url(#{me})"' if me else "")
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{w}"{d}{a}/>'


def PATH(d, stroke=TXT, w=1, fill="none", dash=None, me=None, op=None):
    ds = f' stroke-dasharray="{dash}"' if dash else ""
    a = f' marker-end="url(#{me})"' if me else ""
    o = f' opacity="{op}"' if op is not None else ""
    return f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{w}"{ds}{a}{o}/>'


def dim_h(x1, x2, y, text, size=9, off=-4):
    """Горизонтальный размер с засечками."""
    return (L(x1, y, x2, y, TXT, 1, ms="mkNd", me="mkNd") +
            T((x1 + x2) / 2, y + off, text, size, TXT, "middle", 700))


def dim_v(x, y1, y2, text, size=9, off=-4):
    return (L(x, y1, x, y2, TXT, 1, ms="mkNd", me="mkNd") +
            T(x + off, (y1 + y2) / 2, text, size, TXT, "middle", 700, rot=-90))


def ext(x1, y1, x2, y2):
    return L(x1, y1, x2, y2, TXT, .7)


def build(row: dict) -> list:
    d = furnace_data()
    W, H = 1190, 800
    S = 46.0 / 1000.0            # px на мм: 1 м = 46 px
    cx, y_p = 300, 612           # ось печи; уровень подины
    r1, r2 = d["d_pod"] / 2 * S, d["d_top"] / 2 * S
    y_c = y_p - d["h_cone"] * S
    y_top = y_p - d["h_total"] * S
    t = round(d["shamot"] * S)   # футеровка (шамот) в px
    rd = d["d_duct"] / 2 * S     # радиус газохода
    y_duct = 118
    o = []
    o.append(f'<svg id="ech1-svg" class="gsvg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'style="width:1190px;max-width:none;display:block;font-family:system-ui,sans-serif">')
    o.append('<defs>'
             '<marker id="mkN" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="9" refX="9" refY="4.5" orient="auto">'
             '<path d="M0 0 L10 4.5 L0 9 Z" fill="context-stroke"/></marker>'
             '<marker id="mkNd" markerWidth="10" markerHeight="10" refX="5" refY="5" orient="auto">'
             '<path d="M2 8 L8 2" stroke="context-stroke" stroke-width="1.6"/></marker>'
             f'<pattern id="ht" patternUnits="userSpaceOnUse" width="7" height="7" patternTransform="rotate(45)">'
             f'<line x1="0" y1="0" x2="0" y2="7" stroke="{TXT}" stroke-width=".7"/></pattern>'
             f'<pattern id="ht2" patternUnits="userSpaceOnUse" width="4" height="4" patternTransform="rotate(-45)">'
             f'<line x1="0" y1="0" x2="0" y2="4" stroke="{MUTED}" stroke-width=".8"/></pattern>'
             f'<pattern id="conc" patternUnits="userSpaceOnUse" width="8" height="8">'
             f'<circle cx="2" cy="2" r=".9" fill="{MUTED}"/><circle cx="6" cy="6" r=".9" fill="{MUTED}"/></pattern>'
             '</defs>')
    o.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="{BG}"/>')
    o.append(f'<rect x="6" y="6" width="{W - 12}" height="{H - 12}" fill="none" stroke="{FRAME}"/>')
    # заголовок
    o.append(T(1174, 24, "ЭСКИЗ ОБЩЕГО ВИДА · ЛОТ №1 — ПЕЧЬ КИПЯЩЕГО СЛОЯ (ПОЗ. НО-1)", 12, TXT, "end", 800))
    o.append(T(1174, 38, "продольный разрез — схема · размеры в мм, отметки в м · геометрия по ИД ч.1 табл. 23 п.214–217, разд. 2.3",
               8, MUTED, "end"))
    o.append(T(1174, 52, "синий — дутьё · оранжевый — газ и огарок · серый — шихта · штриховка — футеровка · пунктир — уточняется по КД",
               8.5, MUTED, "end"))
    o.append(T(20, 786, "ЭСКИЗ — не для строительства · эскиз общего вида печи кипящего слоя, Лот №1, поз. НО-1 · "
               "источники: ИД ч.1 табл. 23–25, разд. 2.3–2.4; компоновка БИ Лота №1; расчётная модель КВАНТ", 10, MUTED))

    # ------------------------------------------------ корпус и футеровка
    def half(sign):
        """Контуры левой (sign=-1) / правой (+1) половины: внутренняя грань футеровки."""
        return [(cx + sign * r1, y_p), (cx + sign * r2, y_c), (cx + sign * r2, y_top),
                (cx + sign * rd, y_top), (cx + sign * rd, y_duct)]

    def offset(pts, sign, dx, dy_roof):
        """Смещение контура наружу: по x на dx (стенки), по y на dy_roof (свод)."""
        (a, b, cc, dd, e) = pts
        return [(a[0] + sign * dx, a[1]), (b[0] + sign * dx, b[1]), (cc[0] + sign * dx, cc[1] - dy_roof),
                (dd[0] + sign * dx * 0.5, dd[1] - dy_roof), (e[0] + sign * dx * 0.5, e[1])]

    def poly(pts):
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    for sign in (-1, 1):
        inner = half(sign)
        ref = offset(inner, sign, t, t)
        asb = offset(inner, sign, t + 2, t + 2)
        shell = offset(inner, sign, t + 5, t + 5)
        # футеровка (замкнутая полоса)
        band = ref + list(reversed(inner))
        o.append(f'<polygon points="{poly(band)}" fill="url(#ht)" stroke="{TXT}" stroke-width=".6"/>')
        # асбест
        o.append(f'<polyline points="{poly(asb)}" fill="none" stroke="{MUTED}" stroke-width="1.6" stroke-dasharray="2 2"/>')
        # корпус
        o.append(f'<polyline points="{poly(shell)}" fill="none" stroke="{INK}" stroke-width="2"/>')

    # ------------------------------------------------ подина, сопла, коробка
    xl, xr = cx - r1 - t - 5, cx + r1 + t + 5
    o.append(f'<rect x="{xl:.1f}" y="{y_p}" width="{xr - xl:.1f}" height="13" fill="url(#conc)" stroke="{INK}" stroke-width="1.2"/>')
    o.append(L(xl, y_p, xr, y_p, INK, 3))
    for i in range(-6, 7):
        x = cx + i * 20
        o.append(f'<rect x="{x - 1.5}" y="{y_p - 8}" width="3" height="8" fill="{TXT}"/>')
        o.append(PATH(f"M{x - 5} {y_p - 8} Q{x} {y_p - 16} {x + 5} {y_p - 8} Z", TXT, .8, TXT))
    o.append(T(cx, y_p - 20, f"газораспределительная подина · {d['n_nozzle']} сопел ХН78Т · 4 отв. Ø4 (узел А)", 7.5, TXT, "middle"))
    # газовая коробка
    yb1, yb2 = y_p + 13, y_p + 58
    o.append(PATH(f"M{xl:.1f} {yb1} L{xr:.1f} {yb1} L{cx + 100} {yb2} L{cx - 100} {yb2} Z", INK, 1.6))
    o.append(T(cx, yb1 + 20, "газовая коробка (несекционированная)", 8.5, TXT, "middle", 700))
    o.append(T(cx, yb1 + 32, "расчётное давление — не ниже давления воздуходувки", 7.5, MUTED, "middle"))
    # опорный каркас
    for x in (cx - r1 - t - 25, cx + r1 + t + 25):
        o.append(L(x, yb1, x, y_p + 96, TXT, 1.2, dash="5 4"))
    o.append(T(cx, y_p + 112, "опорный каркас печи и зона обслуживания сопел под печью — по КД разработчика печи", 8, MUTED, "middle"))
    # дутьё
    o.append(PATH(f"M70 {y_p + 36} L{xl - 2:.0f} {y_p + 36}", BLUE, 2, me="mkN"))
    o.append(PATH(f"M{xl - 2:.0f} {y_p + 36} L{xl - 2:.0f} {yb1 + 12}", BLUE, 2))
    o.append(T(22, y_p + 20, "Б — ДУТЬЁ (воздух + O₂)", 8, BLUE, "start", 700))
    o.append(T(22, y_p + 31, d["blast"], 8, BLUE))
    o.append(T(22, y_p + 48, f"{d['p_blast']} изб. · {d['o2']}", 7.5, TXT))
    o.append(T(22, y_p + 59, f"{d['t_blast']} · осн. / вспом.", 7.5, TXT))
    o.append(T(22, y_p + 70, "воздуходувка — серийная, вне поставки печи", 7.2, MUTED))
    # давление коробки
    o.append(f'<circle cx="{cx - 118}" cy="{yb1 + 26}" r="6" fill="{BG}" stroke="{TXT}" stroke-width="1"/>')
    o.append(T(cx - 118, yb1 + 28.5, "И", 6.5, INK, "middle", 700))

    # ------------------------------------------------ слой
    def r_at(h_mm):
        return r1 + (r2 - r1) * min(h_mm, d["h_cone"]) / d["h_cone"]

    yb_hi = y_p - d["bed_hi"] * S
    yb_lo = y_p - d["bed_lo"] * S
    yb_bulk = y_p - d["bed_bulk"] * S
    rh = r_at(d["bed_hi"])
    o.append(f'<polygon points="{cx - r1:.1f},{y_p} {cx - rh:.1f},{yb_hi:.1f} {cx + rh:.1f},{yb_hi:.1f} {cx + r1:.1f},{y_p}" '
             f'fill="{ORANGE}" opacity=".14"/>')
    for yy, rr in ((yb_hi, r_at(d["bed_hi"])), (yb_lo, r_at(d["bed_lo"]))):
        o.append(L(cx - rr, yy, cx + rr, yy, ORANGE, 1, dash="5 3"))
    o.append(L(cx - r_at(d["bed_bulk"]), yb_bulk, cx + r_at(d["bed_bulk"]), yb_bulk, TXT, .8, dash="3 3"))
    o.append(T(cx, y_p - 46, f"КИПЯЩИЙ СЛОЙ · {d['t_bed']} °C", 9.5, INK, "middle", 700))
    o.append(T(cx, y_p - 34, f"{d['load_t']} т · {d['stay_h']} ч · Δp «коробка — слой» {d['dp']}", 7.5, TXT, "middle"))
    # подписи уровней слоя (слева, вне корпуса)
    xlab = cx - r_at(d["bed_hi"]) - t - 8
    o.append(L(cx - r_at(d["bed_hi"]) - t - 5, yb_hi, xlab - 2, yb_hi, ORANGE, .8))
    o.append(T(xlab - 4, yb_hi + 3, f"зеркало КС {d['bed_lo']}–{d['bed_hi']}", 7.5, ORANGE, "end"))
    o.append(L(cx - r_at(d["bed_bulk"]) - t - 5, yb_bulk, xlab - 2, yb_bulk, TXT, .8))
    o.append(T(xlab - 4, yb_bulk + 3, f"насыпной слой {d['bed_bulk']}", 7.5, TXT, "end"))
    # термопары слоя (Ж)
    for hmm in (600, 1300):
        yy = y_p - hmm * S
        xx = cx - r_at(hmm) - t - 5
        o.append(L(xx - 12, yy, xx + 6, yy, TXT, .8))
        o.append(f'<circle cx="{xx - 18}" cy="{yy}" r="6" fill="{BG}" stroke="{TXT}" stroke-width="1"/>')
        o.append(T(xx - 18, yy + 2.5, "Ж", 6.5, INK, "middle", 700))
    # давление слоя (И) справа
    hmm = 1000
    yy = y_p - hmm * S
    xx = cx + r_at(hmm) + t + 5
    o.append(L(xx - 6, yy, xx + 12, yy, TXT, .8))
    o.append(f'<circle cx="{xx + 18}" cy="{yy}" r="6" fill="{BG}" stroke="{TXT}" stroke-width="1"/>')
    o.append(T(xx + 18, yy + 2.5, "И", 6.5, INK, "middle", 700))

    # ------------------------------------------------ надслоевое пространство
    o.append(T(cx, 372, "надслоевое пространство (зона сепарации)", 8.5, TXT, "middle"))
    o.append(T(cx, 384, f"газ на выходе {d['t_gas']} · {d['gas']}", 7.5, TXT, "middle"))
    o.append(T(cx, 396, f"SO₂ {d['so2']} · пыль {d['dust']} · пылевынос 19 / 29 %", 7.5, TXT, "middle"))
    o.append(T(cx, 412, "давление под сводом +50…100 Па (ИД) — знак уточняется", 7.2, MUTED, "middle"))

    # ------------------------------------------------ загрузка (А), горелки (Е)
    h_feed = 3400
    yf = y_p - h_feed * S
    xf = cx - r_at(h_feed)
    o.append(f'<rect x="40" y="{yf - 6:.1f}" width="{xf - 40 + 4:.1f}" height="12" fill="{BG}" stroke="{TXT}" stroke-width="1.4"/>')
    for k in range(6):
        xk = 48 + k * 14
        o.append(L(xk, yf - 5, xk + 8, yf + 5, TXT, .8))
    o.append(PATH(f"M{xf + 4:.1f} {yf:.1f} L{xf + 30:.1f} {yf + 14:.1f}", TXT, 1.2, me="mkN"))
    o.append(PATH(f"M{xf + 4:.1f} {yf:.1f} Q{cx - 40} {yf + 20} {cx - 10} {yb_hi - 6:.1f}", TXT, .8, dash="2 3"))
    o.append(T(22, yf - 40, "А — шнековый забрасыватель", 8, INK, "start", 700))
    o.append(T(22, yf - 29, f"шихты · {d['zab']}", 7.5, TXT))
    o.append(T(22, yf - 18, f"шихта {d['feed']}", 7.5, TXT))
    o.append(T(22, yf + 22, "подача в надслоевое", 7.2, MUTED))
    o.append(T(22, yf + 32, "пространство над зеркалом слоя", 7.2, MUTED))
    # горелки
    for sign, hmm, lab in ((1, 3700, True), (-1, 2700, False)):
        yy = y_p - hmm * S
        xw = cx + sign * (r_at(hmm) + t + 5)
        x0, x1 = (xw, xw + 28) if sign > 0 else (xw - 28, xw)
        o.append(f'<rect x="{min(x0, x1):.1f}" y="{yy - 5:.1f}" width="28" height="10" fill="{BG}" stroke="{TXT}" stroke-width="1.2"/>')
        o.append(T(xw + sign * 14, yy + 2.5, "Е", 6.5, INK, "middle", 700))
        if lab:
            o.append(T(x1 + 6, yy - 6, "Е — горелки МГМГ-10, 3 шт.", 7.5, TXT))
            o.append(T(x1 + 6, yy + 5, "мазут до 0,975 т/ч каждая", 7.2, MUTED))
            o.append(T(x1 + 6, yy + 16, "розжиг дровами · размещение по КД", 7.2, MUTED))

    # ------------------------------------------------ выгрузка (Г, Д)
    h_sl = 2000
    ys = y_p - h_sl * S
    xw = cx + r_at(h_sl)
    xe = xw + t + 5 + 46
    o.append(f'<rect x="{xw - 2:.1f}" y="{ys - 9:.1f}" width="{xe - xw + 2:.1f}" height="18" fill="{BG}" stroke="{INK}" stroke-width="1.4"/>')
    o.append(f'<rect x="{xw + t + 5:.1f}" y="{ys - 6:.1f}" width="{xe - xw - t - 5:.1f}" height="12" fill="url(#ht)" stroke="none"/>')
    o.append(PATH(f"M{xe + 2:.1f} {ys:.1f} L{xe + 26:.1f} {ys:.1f}", ORANGE, 2, me="mkN"))
    o.append(T(xe + 30, ys - 8, "Г — сливной порог огарка", 8, INK, "start", 700))
    o.append(T(xe + 30, ys + 3, f"огарок {d['t_bed']} °C → дроссельный затвор", 7.5, ORANGE))
    o.append(T(xe + 30, ys + 14, f"→ холодильник {d['cooler']} под порогом", 7.5, ORANGE))
    o.append(T(xe + 30, ys + 25, "высота порога 1,9–2,1 м — предварительно", 7.2, MUTED))
    h_dn = 500
    yd = y_p - h_dn * S
    xw = cx + r_at(h_dn)
    xe2 = xw + t + 5 + 34
    o.append(f'<rect x="{xw - 2:.1f}" y="{yd - 7:.1f}" width="{xe2 - xw + 2:.1f}" height="14" fill="{BG}" stroke="{INK}" stroke-width="1.4"/>')
    o.append(L(xe2 - 12, yd - 12, xe2 - 12, yd + 12, RED, 2))
    o.append(PATH(f"M{xe2 + 2:.1f} {yd:.1f} L{xe2 + 22:.1f} {yd:.1f}", ORANGE, 1.6, me="mkN"))
    o.append(T(xe2 + 26, yd - 8, "Д — донный порог", 8, INK, "start", 700))
    o.append(T(xe2 + 26, yd + 3, "ножевая шиберная задвижка · крупные фракции", 7.2, TXT))
    o.append(T(xe2 + 26, yd + 14, "аварийный сброс слоя в кюбель на отм. 0,000", 7.2, TXT))

    # ------------------------------------------------ гильзы кладки (К), пирометры (Л)
    for hmm in (1500, 4500, 7500):
        yy = y_p - hmm * S
        xx = cx + (r_at(hmm) if hmm <= d["h_cone"] else r2) + t + 5
        o.append(L(xx - 4, yy - 4, xx + 4, yy + 4, TXT, 1.2))
        o.append(L(xx - 4, yy + 4, xx + 4, yy - 4, TXT, 1.2))
    yy = y_p - 4500 * S
    xk = cx + r_at(4500) + t + 14
    o.append(T(xk, yy - 3, "К — гильзы термопар кладки", 7.2, TXT))
    o.append(T(xk, yy + 8, "2–3 пояса, 12–18 шт.", 7.2, MUTED))
    yy = y_p - 5600 * S
    xx = cx - r_at(5600) - t - 5
    o.append(PATH(f"M{xx - 2:.1f} {yy - 5:.1f} L{xx - 12:.1f} {yy:.1f} L{xx - 2:.1f} {yy + 5:.1f} Z", TXT, 1))
    o.append(T(xx - 16, yy - 4, "Л — пирометры", 7.2, TXT, "end"))
    o.append(T(xx - 16, yy + 7, "корпуса, 4–6 шт.", 7.2, MUTED, "end"))

    # ------------------------------------------------ газоход (В)
    o.append(PATH(f"M{cx} {y_duct - 2} L{cx} 96", ORANGE, 2.2, me="mkN"))
    o.append(T(cx + rd + 12, 108, "В — отвод газов Ø2000 → котёл-утилизатор", 8.5, INK, "start", 700))
    o.append(T(cx + rd + 12, 120, f"газ {d['t_gas']} · {d['gas']} · пыль {d['dust']}", 7.5, ORANGE))
    o.append(T(cx + rd + 12, 132, "газоход 09Г2С с футеровкой ШЛ-0,4 · из центра свода", 7.5, TXT))
    o.append(dim_h(cx - rd, cx + rd, 150, f"Ø{d['d_duct']}", 8.5, -4))

    # ------------------------------------------------ размеры
    o.append(dim_h(cx - r2, cx + r2, y_c - 14, f"Ø{d['d_top']} (на уровне газохода)", 9, -4))
    o.append(ext(cx - r1, y_p + 13, cx - r1, y_p + 92))
    o.append(ext(cx + r1, y_p + 13, cx + r1, y_p + 92))
    o.append(dim_h(cx - r1, cx + r1, y_p + 84, f"Ø{d['d_pod']} (подина, 28,27 м²)", 9, -4))
    # высоты — справа: конус и общая; отметки — слева от размерных линий
    xv, xv2 = 664, 692
    o.append(ext(cx + r1 + t + 5, y_p, xv2 + 8, y_p))
    o.append(ext(cx + r2 + t + 5, y_c, xv + 8, y_c))
    o.append(ext(cx + r2 + t + 5, y_top, xv2 + 8, y_top))
    o.append(dim_v(xv, y_c, y_p, f"{d['h_cone']} (конус 21°)", 9, -4))
    o.append(dim_v(xv2, y_top, y_p, f"{d['h_total']} (подина — газоход)", 9, -4))
    o.append(T(xv - 8, y_p + 13, f"▽ {d['otm_pod']} (подина, предв.)", 8, TXT, "end", 700))
    o.append(T(xv - 8, y_top - 5, f"▽ {d['otm_svod']} (свод, предв.)", 8, TXT, "end", 700))

    # ================================================ узел А — сопло
    ax, ay = 730, 92
    o.append(T(ax, ay, "Узел А — сопло подины (схема)", 9.5, INK, "start", 700))
    o.append(T(ax, ay + 12, f"грибковое безпровальное · сталь ХН78Т · {d['n_nozzle']} шт.", 7.5, TXT))
    o.append(T(ax, ay + 23, "4 отв. Ø4 · шаг и Δp решётки — расчёт изготовителя", 7.2, MUTED))
    px, py = 810, 190   # верх плиты подины
    o.append(f'<rect x="{px - 60}" y="{py}" width="120" height="34" fill="url(#conc)" stroke="{INK}" stroke-width="1"/>')
    o.append(L(px - 60, py, px + 60, py, INK, 3))
    o.append(f'<rect x="{px - 5}" y="{py - 24}" width="10" height="60" fill="{BG}" stroke="{TXT}" stroke-width="1.2"/>')
    o.append(PATH(f"M{px - 20} {py - 24} Q{px} {py - 46} {px + 20} {py - 24} Z", TXT, 1.2, BG))
    for sx in (-1, 1):
        o.append(f'<circle cx="{px + sx * 11}" cy="{py - 30}" r="2.2" fill="{BG}" stroke="{TXT}" stroke-width="1"/>')
        o.append(PATH(f"M{px + sx * 14} {py - 30} L{px + sx * 30} {py - 30}", BLUE, 1.2, me="mkN"))
    o.append(PATH(f"M{px} {py + 44} L{px} {py + 8}", BLUE, 1.4, me="mkN"))
    o.append(T(px + 34, py - 27, "4 отв. Ø4", 7, BLUE))
    o.append(T(ax, py + 62, "1 — колпак сопла (ХН78Т) · 2 — стойка сопла", 7.2, TXT))
    o.append(T(ax, py + 73, "3 — плита подины (жаропроч. сталь) · 4 — бетон", 7.2, TXT))
    o.append(T(ax, py + 84, "5 — газовая коробка · уплотнение сопла — по КД", 7.2, TXT))
    o.append(T(px - 24, py - 40, "1", 7, INK, "end", 700))
    o.append(T(px + 9, py - 8, "2", 7, INK, "start", 700))
    o.append(T(px + 64, py + 6, "3", 7, INK, "start", 700))
    o.append(T(px + 64, py + 22, "4", 7, INK, "start", 700))
    o.append(T(px + 14, py + 44, "5", 7, INK, "start", 700))

    # ================================================ узел Б — стенка
    bx, by = 930, 92
    o.append(T(bx, by, "Узел Б — стенка корпуса (схема)", 9.5, INK, "start", 700))
    o.append(T(bx, by + 12, "снаружи → внутрь: корпус (углеродистая сталь),", 7.5, TXT))
    o.append(T(bx, by + 23, "листовой асбест, шамот Ш-22/23/5/44/45 на мертеле;", 7.5, TXT))
    o.append(T(bx, by + 34, f"рабочее пространство {d['t_bed']} °C", 7.5, TXT))
    wx, wy = 950, 140
    o.append(f'<rect x="{wx}" y="{wy}" width="9" height="56" fill="{TXT}"/>')
    o.append(f'<rect x="{wx + 9}" y="{wy}" width="6" height="56" fill="url(#ht2)" stroke="{MUTED}" stroke-width=".6"/>')
    o.append(f'<rect x="{wx + 15}" y="{wy}" width="70" height="56" fill="url(#ht)" stroke="{TXT}" stroke-width=".8"/>')
    for k in range(1, 4):
        o.append(L(wx + 15, wy + k * 14, wx + 85, wy + k * 14, TXT, .5))
    o.append(f'<rect x="{wx + 85}" y="{wy}" width="70" height="56" fill="{ORANGE}" opacity=".12"/>')
    o.append(T(wx + 4.5, wy - 5, "корпус", 7, TXT, "middle"))
    o.append(T(wx + 12, wy + 66, "асбест", 7, MUTED, "middle"))
    o.append(T(wx + 50, wy - 5, "шамот", 7, TXT, "middle"))
    o.append(T(wx + 120, wy + 30, f"{d['t_bed']} °C", 8, ORANGE, "middle", 700))
    o.append(dim_h(wx + 15, wx + 85, wy + 70, f"≈{d['shamot']} (оценка)", 7.5, -3))
    o.append(T(wx + 4.5, wy + 84, "по КД", 6.5, MUTED, "middle"))
    o.append(T(bx, wy + 96, "толщины корпуса и футеровки, температурные швы,", 7.2, MUTED))
    o.append(T(bx, wy + 107, "перевязка — по проекту футеровки; гильзы термопар", 7.2, MUTED))
    o.append(T(bx, wy + 118, "кладки (К) — до кладки, установка после невозможна", 7.2, MUTED))

    # ================================================ схема подины (вид сверху)
    sx0, sy0 = 815, 400
    o.append(T(730, 286, "Схема подины — вид сверху (условно)", 9.5, INK, "start", 700))
    o.append(T(730, 298, f"Ø{d['d_pod']} · Ø{d['d_top']} (уровень газохода) · Ø{d['d_duct']}", 7.5, TXT))
    R1, R2, RD = 58, 80, 14
    o.append(f'<circle cx="{sx0}" cy="{sy0}" r="{R2}" fill="none" stroke="{TXT}" stroke-width="1" stroke-dasharray="5 3"/>')
    o.append(f'<circle cx="{sx0}" cy="{sy0}" r="{R1}" fill="none" stroke="{INK}" stroke-width="1.4"/>')
    o.append(f'<circle cx="{sx0}" cy="{sy0}" r="{RD}" fill="none" stroke="{ORANGE}" stroke-width="1" stroke-dasharray="3 2"/>')
    import math
    step = 9
    for iy in range(-6, 7):
        for ix in range(-7, 8):
            xx = sx0 + ix * step + (step / 2 if iy % 2 else 0)
            yy = sy0 + iy * step * 0.87
            if math.hypot(xx - sx0, yy - sy0) < R1 - 4 and math.hypot(xx - sx0, yy - sy0) > RD - 2:
                o.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="1.1" fill="{TXT}"/>')
    # порог Г (справа), Д (снизу справа), А (слева), Е ×3
    o.append(f'<rect x="{sx0 + R1 - 3}" y="{sy0 - 7}" width="14" height="14" fill="{BG}" stroke="{INK}" stroke-width="1.2"/>')
    o.append(T(sx0 + R1 + 16, sy0 + 3, "Г", 7.5, INK, "start", 700))
    ang = math.radians(50)
    o.append(f'<rect x="{sx0 + R1 * math.cos(ang) - 5:.1f}" y="{sy0 + R1 * math.sin(ang) - 5:.1f}" width="10" height="10" fill="{BG}" stroke="{INK}" stroke-width="1.2"/>')
    o.append(T(sx0 + (R1 + 14) * math.cos(ang), sy0 + (R1 + 14) * math.sin(ang) + 3, "Д", 7.5, INK, "start", 700))
    o.append(f'<rect x="{sx0 - R1 - 11}" y="{sy0 - 4}" width="14" height="8" fill="{BG}" stroke="{TXT}" stroke-width="1.2"/>')
    o.append(T(sx0 - R1 - 14, sy0 + 3, "А", 7.5, INK, "end", 700))
    for k in range(3):
        a = math.radians(-90 + k * 120)
        xx, yy = sx0 + (R2 - 6) * math.cos(a), sy0 + (R2 - 6) * math.sin(a)
        o.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4" fill="{BG}" stroke="{TXT}" stroke-width="1.2"/>')
    o.append(T(sx0 + 8, sy0 - R2 + 9, "Е ×3", 7, TXT, "start", 700))
    o.append(T(730, 494, f"точки — сопла (условно; {d['n_nozzle']} шт. по КД)", 7, MUTED))
    o.append(T(730, 505, "Г — сливной порог · Д — донный порог", 7, MUTED))
    o.append(T(730, 516, "А — забрасыватель · Е — горелки (3, через 120°)", 7, MUTED))

    # ================================================ таблица патрубков
    tx, ty = 920, 300
    cols = [26, 150, 42, 30]
    rows = [
        ("Обозн.", "Патрубок / закладной элемент", "Размер", "Кол."),
        ("А", "Загрузка шихты — забрасыватель", "Ø150", "1"),
        ("Б", "Подвод дутья в газовую коробку", "по КД", "1"),
        ("В", "Отвод газов из центра свода", f"Ø{d['d_duct']}", "1"),
        ("Г", "Сливной порог, дроссельный затвор", "по КД", "1"),
        ("Д", "Донный порог, шиберная задвижка", "по КД", "1"),
        ("Е", "Горелки разогрева МГМГ-10", "по КД", "3"),
        ("Ж", "Гильзы термопар слоя, 2 уровня", "—", "8–10"),
        ("И", "Штуцеры давления: слой, коробка", "—", "2–4"),
        ("К", "Гильзы термопар кладки, 2–3 пояса", "—", "12–18"),
        ("Л", "Площадки под пирометры корпуса", "—", "4–6"),
        ("М", "Лючки розжига, люки обслуживания", "по КД", "по КД"),
    ]
    rh = 17
    tw = sum(cols)
    o.append(f'<rect x="{tx}" y="{ty}" width="{tw}" height="{rh}" fill="rgba(11,11,11,.06)"/>')
    for i, r in enumerate(rows):
        yy = ty + i * rh
        o.append(L(tx, yy, tx + tw, yy, TXT, .6))
        xx = tx
        for j, (cell, w) in enumerate(zip(r, cols)):
            anchor = "middle" if j in (0, 3) else "start"
            xt = xx + w / 2 if anchor == "middle" else xx + 3
            o.append(T(xt, yy + 12, cell, 7, INK if i == 0 else TXT, anchor, 700 if i == 0 else None))
            xx += w
    o.append(L(tx, ty + len(rows) * rh, tx + tw, ty + len(rows) * rh, TXT, .6))
    xx = tx
    for w in cols + [0]:
        o.append(L(xx, ty, xx, ty + len(rows) * rh, TXT, .6))
        xx += w
    o.append(T(tx, ty + len(rows) * rh + 12, "размещение патрубков и закладных элементов — по КД изготовителя", 7.2, MUTED))

    # ================================================ примечания
    nx, ny = 730, 548
    o.append(T(nx, ny, "Примечания", 9.5, INK, "start", 700))
    notes = [
        "1. Размеры в мм, отметки в м. Разрез — схема: форма свода, толщины корпуса и футеровки, конструкция",
        "    опорного каркаса, число и размещение патрубков — по конструкторской документации изготовителя.",
        f"2. Геометрия рабочего пространства (Ø{d['d_pod']}/{d['d_top']}, конус {d['h_cone']}, высота {d['h_total']}, {d['n_nozzle']} сопел) — ИД ч.1 табл. 23",
        "    п.214–217, разд. 2.3 (конструкция HATCH); высоты слоя — ИД табл. 23; отметки — компоновка БИ.",
        "3. Значения через дробь — основной / вспомогательный вариант дутья по ИД табл. 23.",
        f"4. Футеровка: листовой асбест, шамот (ориентир {d['shamot']} мм — оценка, около 135 т); подина — жаропрочная",
        "    сталь и бетон; сопла — ХН78Т; масса печи с футеровкой 200–230 т (оценка, уточняется по КД).",
        "5. Требования к проектированию, изготовлению, контролю и приёмке — документ ОВЭ75-БИ-Л1-НО, поз. НО-1.",
    ]
    for k, s in enumerate(notes):
        o.append(T(nx, ny + 13 + k * 11.5, s, 7.4, TXT))

    o.append("</svg>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(o) + "\n", encoding="utf-8")
    return [OUT]


if __name__ == "__main__":
    for r in c.df.register():
        if r.get("module") == "sketch_netip_lot1":
            print(build(r))
