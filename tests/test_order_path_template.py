"""Путь заказа по шагам (_opOpen / _opHtml) — вёрстка без браузера (node:vm, как
test_ork_drill_template): у каждого заказа синтетики — 13 шагов, текущий помечен и совпадает с
шагом строки заказа, р.д. текущего захода — stepWd, ссылки в Битрикс (заказ — СП-172, сделка),
пункты очереди заказа — в «Что сделать», другие заказы сделки — кнопками; без «undefined»,
«NaN», «null» в тексте. Владелец 29.10.2026: «нельзя провалиться в процессы, где зависло».
Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from tests import fixture  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="нет node")

TPL = (ROOT / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
START, END = "// ---- «Реализация» по регламенту ОРК", "function _ctPanel("

HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const inp = JSON.parse(fs.readFileSync(0, 'utf8'));
const node = () => ({innerHTML: '', textContent: '', dataset: {}, classList: {toggle() {}, add() {}, remove() {}, contains() { return false; }},
  addEventListener() {}, setAttribute() {}, getAttribute() { return null; }, querySelector() { return null; }, contains() { return false; }});
const byId = {};
const document = {getElementById: id => (byId[id] ||= node()), querySelectorAll: () => [], querySelector: () => null};
const box = {document, window: {__CONTRACTS__: inp.ct, __DEALCTX__: null}, location: {hash: ''}, console, JSON, Math,
  Object, Array, String, Number, Set, Map, RegExp, Date, encodeURIComponent};
vm.createContext(box);
vm.runInContext(inp.esc + '\n' + inp.ctN + '\n' + inp.unpack + '\nfunction _dcData(){return null;}\n' + inp.code
  + '\nthis._opHtml=_opHtml; this._OP=_OP; this._opFind=_opFind;', box);
const out = {};
for (const d of inp.ct.rows) for (const o of (d.orders || [])) {
  box._OP.back = ''; box._OP.s = null;
  out[o.id] = box._opHtml(String(o.id));
  // уровень «Шаг» (У5): раскрыт текущий шаг
  if (o.live && o.step != null) { box._OP.s = o.step; out['s:' + o.id] = box._opHtml(String(o.id)); box._OP.s = null; }
}
box._OP.back = 'deal';
out.__deal = box._opHtml(String(inp.k0));
out.__none = box._opHtml('нет-такого');
process.stdout.write(JSON.stringify(out));
"""


def _line(prefix: str) -> str:
    return next(ln for ln in TPL.splitlines() if ln.startswith(prefix))


def _fn(name: str) -> str:
    a = TPL.index(f"function {name}(")
    b = min(i for i in (TPL.find("\nfunction ", a + 1), TPL.find("\n//", a + 1)) if i > 0)
    return TPL[a:b]


@pytest.fixture(scope="module")
def run():
    r = json.loads(json.dumps(fixture.build_realization(), ensure_ascii=False, default=str))
    ct = r["contracts"]
    k0 = next(o["id"] for d in ct["rows"] for o in d.get("orders") or [] if o.get("live"))
    inp = {"code": TPL[TPL.index(START):TPL.index(END)], "esc": _line("const _esc="), "ctN": _line("const _ctN="),
           "unpack": _fn("_unpack"), "ct": ct, "k0": k0}
    p = subprocess.run(["node", "-e", HARNESS], input=json.dumps(inp, ensure_ascii=False), capture_output=True,
                       text=True, timeout=180)
    assert p.returncode == 0, p.stderr[-2000:]
    return ct, json.loads(p.stdout)


def _text(h: str) -> str:
    # <abbr> — внутри слова и фразы: снимается без пробела
    return re.sub(r"<[^>]+>", " ", re.sub(r"</?abbr[^>]*>", "", h))


def test_каждый_живой_заказ_путь_из_13_шагов(run):
    ct, out = run
    live = 0
    for d in ct["rows"]:
        for o in d.get("orders") or []:
            h = out[str(o["id"])]
            assert h and 'id="opRoot"' in h, o["id"]
            t = _text(h)
            for bad in ("undefined", "NaN", "null", "[object"):
                assert bad not in t, (o["id"], bad)
            # ссылки в Битрикс: заказ (СП-172) и сделка — в новой вкладке
            assert f'href="https://kvantpro.bitrix24.ru/crm/type/172/details/{o["id"]}/" target="_blank" rel="noopener"' in h
            assert f'href="https://kvantpro.bitrix24.ru/crm/deal/details/{d["deal"]}/" target="_blank" rel="noopener"' in h
            assert "Заказ в Битриксе ↗" in h and "Сделка в Битриксе ↗" in h
            if not o.get("live"):
                assert "Заказ закрыт" in h and 'class="op-steps"' not in h
                continue
            live += 1
            # шаг 0 «Подтверждение передачи в ОРК» (решение владельца 31.10.2026) — в пути, только если
            # заказ на нём сейчас или был (путь с записью шага 0): тогда 14 шагов, иначе прежние 13
            has0 = o["step"] == 0 or any(st == 0 for st, _day, _wd in o["path"])
            assert h.count('<li class="op-s ') == (14 if has0 else 13), o["id"]
            cur = re.findall(r'<li class="op-s ([^"]*)" aria-current="step" data-ops="\d+"><button [^>]*><span class="op-dot" aria-hidden="true">(\d+)</span>', h)
            if o["step"] is None:
                assert not cur and "неизвестна регламенту" in h and "сверить стадию заказа в Битриксе" in h
                continue
            assert len(cur) == 1 and int(cur[0][1]) == o["step"] and "is-cur" in cur[0][0], o["id"]
            if o["step"] == 0:
                assert "Шаг 0 · до передачи в ОРК" in h and "Шаг 0 из 13" not in h, o["id"]
            else:
                assert f"Шаг {o['step']} из 13" in h
            if o["stepWd"] is not None:
                seg = h[h.index('aria-current="step"'):]
                seg = seg[:seg.index("</li>")]
                assert f"<b>{o['stepWd']}</b> <abbr" in seg, o["id"]
                assert f'<b class="op-num">{o["stepWd"]}</b> <abbr' in h
            if o["stepOver"]:
                assert "дольше нормы на" in h and "op-stuck is-over" in h and "Где застрял" in h
            # шаг 0 сверх 5 раб. дн. — подсвечен (stepPreOver), словами «передача не подтверждена», не «дольше нормы»
            if o["step"] == 0 and o.get("stepPreOver"):
                assert "передача в ОРК не подтверждена дольше" in t and "op-stuck is-over" in h, o["id"]
                assert "op-bad\">дольше нормы на" not in h, o["id"]
            # заказ, не переданный в ОРК (whoOut, решение владельца 31.10.2026, заказ 968/2): делает ответственный
            # заказа, «не из ОРК — заказ не передан в ОРК», менеджера ОРК назначает рук. ОРК
            if o.get("stepWhoOut"):
                assert "(не из ОРК — заказ не передан в ОРК)" in t and "рук. ОРК: принять заказ и назначить менеджера ОРК" in t, o["id"]
                assert not re.search(r"(?i)менеджер ОРК\s+" + re.escape(o["stepWho"].split(" (")[0]), t), o["id"]
            # пройденные шаги пути — с датами входа (строка шага — «дд.мм»)
            for st, day, _wd in o["path"][:-1]:
                y, m, dd = day.split("-")
                assert f"{dd}.{m}" in h, (o["id"], st, day)
            # слова раунда R4: «раб. дн.», без «р.д.», «хозяин», «в копии»; роль не повторяется
            for bad in ("р.д.", "хозяин", "в копии"):
                assert bad not in t, (o["id"], bad)
            assert not re.search(r"\((КАМ|ОРК|рук\. ОРК)\)\s*\(", t), o["id"]
    assert live > 10


def test_что_сделать_пункты_очереди_заказа(run):
    ct, out = run
    allq = ct["life"]["queue"] + ((ct.get("sep") or {}).get("life") or {}).get("queue", [])
    q = [x for x in allq if x.get("order")]
    assert q
    unq = lambda s: s.replace("&quot;", '"').replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")  # noqa: E731
    for x in q:
        h = out[str(x["order"])]
        todo = unq(_text(h[h.index('id="opTodoH"'):h.index('id="opPathH"') if 'id="opPathH"' in h else len(h)]))
        # «Рег. 9.2.5» — словами (шаблон, _rzRef): «п. 9.2.5 регламента ОРК»
        act = re.sub(r"Ан\. §([\d.]*\d)", r"разд. \1 аналитики ОРК", re.sub(r"Рег\. ([\d.]*\d)", r"п. \1 регламента ОРК", x["act"]))
        act = re.sub(r"(^|[^А-Яа-яЁё])р\.\s?д\.", r"\1раб. дн.", act)
        assert re.sub(r"\s+", " ", act[:1].upper() + act[1:]) in re.sub(r"\s+", " ", todo), (x["order"], x["code"])
    # без пунктов заказа и сделки — действие шага по регламенту
    busy = {str(x["order"]) for x in q}
    busy_d = {str(x["deal"]) for x in allq if not x.get("order")}
    # производство дольше P80 (week.longP80) — свой текст «запросить статус», не «отклонений нет»;
    # с 30.10.2026 пункт сделки из заказов (unpaid и т. п.) — без заказа, его сделка в busy_d
    p80 = {str(x["order"]) for v in (ct, ct.get("sep") or {})
           for x in ((((v.get("ork") or {}).get("week") or {}).get("longP80") or {}).get("items") or [])}
    some = [o for d in ct["rows"] if str(d["deal"]) not in busy_d for o in d.get("orders") or []
            if o.get("live") and o["step"] and str(o["id"]) not in busy and str(o["id"]) not in p80]
    assert some
    for o in some[:5]:
        assert "Отклонений нет. По регламенту на шаге" in out[str(o["id"])]


def test_заказы_сделки_и_возврат(run):
    ct, out = run
    d = next(d for d in ct["rows"] if len(d.get("orders") or []) >= 2)
    o = d["orders"][0]
    h = out[str(o["id"])]
    chips = re.findall(r'data-opk="([^"]+)"', h)
    assert set(chips) >= {str(x["id"]) for x in d["orders"]}
    assert h.count('aria-current="true"') == 1
    # крошки «… › Сделка №… › Заказ» — у панели (WP-C _odCrumbs) на всех уровнях; путь заказа свои не
    # рисует, когда они есть (один путь назад, спец. 0.3); переход к сделке — кнопкой «Сделка №… →»
    assert 'data-opgo="deal">Сделка №' in out["__deal"] or 'data-opgo="card">Сделка №' in out["__deal"]
    assert 'data-opgo="card">Сделка №' in h
    assert out["__none"] is None


def _dd(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}.{m}.{y}"


def test_что_сказать_клиенту_та_же_дата_и_опора(run):
    """«Что сказать клиенту» (Д-1а): дата — fcp.date карточки (= fc пункта очереди и строки «Недели»,
    tests/test_rz_wpd.py); опора заказа — «Реальная дата ≈ …» и «Из чего»; дата из одних медиан,
    прошедший план или давняя опора — «Дату не называть клиенту»."""
    ct, out = run
    n_solid = n_not = 0
    for d in ct["rows"]:
        for o in d.get("orders") or []:
            if not o.get("live") or o.get("delivered"):
                continue
            t = re.sub(r"\s+", " ", _text(out[str(o["id"])]))
            assert "Что сказать клиенту" in t
            fp = o.get("fcp")
            if not fp:
                assert "Дату не называть клиенту" in t
                continue
            assert _dd(fp["date"]) in t, o["id"]
            assert "Из чего" in t and "последняя миля" in t
            if fp["solid"] and not fp["pushed"] and not fp["clamped"]:
                assert f"Реальная дата ≈ {_dd(fp['date'])}" in t, o["id"]
                assert "не называть" not in t
                n_solid += 1
            else:
                assert "Дату не называть клиенту" in t, o["id"]
                if fp["baseKind"] == "created":
                    assert "дата из одних медиан" in t
                n_not += 1
    assert n_solid and n_not


def test_шаг_раскрыт_файлы_и_чем_кончается(run):
    """Уровень «Шаг» (У5): вход и выход, «Кончается, когда», кто делает; файлы шага — по art
    карточки и каталогу artCat («есть» / «нет» / «нужен к выходу из шага» / «не требуется: …»)."""
    ct, out = run
    cat = ct["artCat"]
    n = 0
    for d in ct["rows"]:
        for o in d.get("orders") or []:
            h = out.get("s:" + str(o["id"]))
            if not h:
                continue
            sx = h[h.index(f'id="opStep{o["step"]}"'):]
            sx = re.sub(r"\s+", " ", _text(sx[:sx.index("</dl></div>")]))
            assert "Кончается, когда" in sx and "Делает" in sx, o["id"]
            for i, a in enumerate(cat):
                if a["step"] != o["step"]:
                    continue
                st = o["art"][i]
                # «-» на текущем (не пройденном) шаге — не «не требуется», а «нужен к выходу из шага»
                # (ревью раунда 3: «PO — не требуется» на шаге 5 читалось как «PO не нужен»)
                word = {"y": "есть", "n": "нет", "-": "нужен к выходу из шага" if o.get("live") else "не требуется"}[st]
                nm = a["name"][:1].upper() + a["name"][1:]
                assert re.search(re.escape(nm) + r" — " + word, sx), (o["id"], a["key"], st)
                n += 1
    assert n > 5
