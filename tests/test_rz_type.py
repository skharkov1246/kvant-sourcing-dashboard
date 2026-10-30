"""«Реализация»: шрифтовая система вкладки (R3, 29.10.2026) — разметка шаблона.

Archivo в шаблоне подключён только с латиницей: кириллица в нём падала в Times. Во вкладке
«Реализация» и в её выдвижной панели (класс rz) — одно семейство IBM Plex Sans, шкала --rz-*,
подписи без капса и разрядки. Панели других вкладок класс rz снимают — их вид не меняется.
"""
import re
from pathlib import Path

TPL = (Path(__file__).resolve().parent.parent / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
STYLE = TPL[TPL.index("<style>"):TPL.index("</style>")]


def _block():
    a = STYLE.index("/* ==== «Реализация»: шрифтовая система вкладки")
    b = STYLE.index("/* ---- perf/удобство", a)
    return STYLE[a:b]


def _rules(css):
    """Пары (селектор, тело) без вложенных @media — грубый разбор, его хватает для блока."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{", "", css)
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def test_scale_variables_and_family():
    blk = _block()
    # --rz-xs — 13 px: минимальный кегль вкладки (спец. R4, 0.3 и 2.3: «шрифты плохо читаемы»)
    for v, px in (("--rz-xs", 13), ("--rz-sm", 13), ("--rz-md", 14), ("--rz-lg", 16), ("--rz-xl", 20), ("--rz-2xl", 26)):
        assert f"{v}:{px}px" in blk, v
    assert "'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif" in blk
    assert "tabular-nums lining-nums" in blk
    # кириллица Plex Sans подключена (иначе вкладка снова упадёт в системный шрифт)
    assert re.search(r"font-family:'IBM Plex Sans'[^}]*ibm-plex-sans-var-cyr\.woff2", STYLE)


def _split(sel):
    """Селекторы списка — по запятым вне скобок (:where(a,b) — один селектор)."""
    out, depth, cur = [], 0, ""
    for ch in sel:
        depth += ch in "([" ; depth -= ch in ")]"
        if ch == "," and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    return out + [cur]


def test_block_is_scoped():
    for sel, _ in _rules(_block()):
        for part in _split(sel):
            part = part.strip()
            if not part or part in ("from", "to"):
                continue
            assert part.startswith("#tab-contracts") or part.startswith(".drw.rz"), part


def test_no_small_type_no_caps_no_archivo_in_block():
    blk = re.sub(r"/\*.*?\*/", "", _block(), flags=re.S)
    assert "Archivo" not in blk
    assert "uppercase" not in blk
    decl = " ".join(body for _, body in _rules(blk))   # тела правил, не [style*="font-size:…"]
    for px in re.findall(r"font-size:(\d+(?:\.\d+)?)px", decl):
        assert float(px) >= 12, px
    for fw in re.findall(r"font-weight:(\d+)", blk):
        assert int(fw) <= 600, fw
    for ls in re.findall(r"letter-spacing:(-?[\d.]+)em", blk):
        assert abs(float(ls)) <= .02, ls


def test_labels_reset_inside_scope():
    """Подписи, шапки таблиц, заголовки разделов, плитки — без капса внутри вкладки."""
    blk = _block()
    for cls in ("th", ".secttl", ".eyebrow", ".actile .t", ".kpi .lbl", ".h", ".tag"):
        assert re.search(r":where\([^)]*" + re.escape(cls) + r"[^)]*\)\{[^}]*text-transform:none", blk), cls


def test_rz_drawer_switch():
    assert "function _rzDrw(on){" in TPL
    # карточка сделки — первая строка openContract
    oc = TPL[TPL.index("function openContract(x,opt){"):]
    assert oc.split("\n")[1].strip() == "if(typeof _rzDrw==='function') _rzDrw(true);"
    # панели «Сорсинга» (запросы сорсера, сделки без запроса, список за числом) — та же шкала
    # (portal-ui §5б, 30.10.2026); панели прочих вкладок снимают класс
    for fn in ("function openDrw(", "function openUncovered(", "function _soOpen("):
        body = TPL[TPL.index(fn):]
        assert "_rzDrw(true)" in body[:body.index("\n}\n")], fn
    for fn in ("function openDeals(", "function openDir(", "function openPeopleDeals("):
        body = TPL[TPL.index(fn):]
        head = body[:body.index("\n", body.index("\n") + 1)]
        assert "_rzDrw(false)" in head, fn
    # _rzDrw объявлен до openContract (новый регион прямо перед ним)
    assert TPL.index("function _rzDrw(on){") < TPL.index("function openContract(x,opt){")
