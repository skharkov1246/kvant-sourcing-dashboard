"""«Сорсинг»: шрифтовая система и раскладка вкладки (30.10.2026) — разметка шаблона.

Принцип «Реализации» (portal-ui §5б) для #tab-sourcing: одна гарнитура IBM Plex Sans с кириллицей,
подписи без капса и разрядки, текст не мельче 13 px, заголовки не жирнее 600, столбцы и полосы без
градиентов. Правила — только под корнем вкладки и в её выдвижной панели (.drw.rz).
"""
import re
from pathlib import Path

TPL = (Path(__file__).resolve().parent.parent / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
STYLE = TPL[TPL.index("<style>"):TPL.index("</style>")]
TAB = TPL[TPL.index('<div id="tab-sourcing">'):TPL.index("</div><!-- /tab-sourcing -->")]


def _block():
    a = STYLE.index("/* ==== «Сорсинг»: шрифтовая система и раскладка вкладки")
    b = STYLE.index("/* ==== «Реализация»: шрифтовая система вкладки", a)
    return STYLE[a:b]


def _rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"@media[^{]*\{", "", css)
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def _split(sel):
    out, depth, cur = [], 0, ""
    for ch in sel:
        depth += ch in "(["
        depth -= ch in ")]"
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


def test_scale_and_family():
    blk = _block()
    for v, px in (("--so-xs", 13), ("--so-sm", 13), ("--so-md", 14), ("--so-lg", 16), ("--so-xl", 20), ("--so-2xl", 26)):
        assert f"{v}:{px}px" in blk, v
    assert "'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif" in blk
    assert "tabular-nums lining-nums" in blk


def test_block_is_scoped():
    for sel, _ in _rules(_block()):
        for part in _split(sel):
            part = part.strip()
            if part:
                assert part.startswith("#tab-sourcing") or part.startswith(".drw.rz"), part


def test_no_small_type_no_caps_no_gradient():
    blk = re.sub(r"/\*.*?\*/", "", _block(), flags=re.S)
    assert "Archivo" not in blk and "uppercase" not in blk and "gradient" not in blk
    decl = " ".join(body for _, body in _rules(blk))
    for px in re.findall(r"font-size:(\d+(?:\.\d+)?)px", decl):
        assert float(px) >= 13, px
    for fw in re.findall(r"font-weight:(\d+)", blk):
        assert int(fw) <= 600, fw


def test_tab_markup_plain_words():
    """Без «БЛОК A/B», капс-меток и встроенных градиентов; разделы — заголовком и строкой «зачем»."""
    assert "БЛОК A" not in TAB and 'class="tag a"' not in TAB
    assert "gradient" not in TAB
    assert TAB.count('class="secttl"') == TAB.count('class="sowhy"') - 2   # + «Порядок» и «Служебное»
    js = TPL[TPL.index("// ==== «Сорсинг»: «Сегодня», плитки с действием"):TPL.index("// ---- вкладка «Пульс компании»")]
    assert "gradient" not in js


def test_today_and_order_split():
    js = TPL[TPL.index("const SO_TODAY="):]
    today = re.search(r"const SO_TODAY=\[([^\]]*)\]", js).group(1).split(",")
    cards = re.search(r"const SO_CARDS=\[([^\]]*)\]", js).group(1).split(",")
    assert len(today) <= 6
    assert not set(today) & set(cards)
