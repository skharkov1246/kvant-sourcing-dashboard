"""«Сделки и деньги», карточка сделки и путь заказа: сделка и заказ поставщика — врозь (пакет T3,
спецификация «сделка и заказ» 30.10.2026, TP-5 и TP-6; владелец: «перепутаны сделки и заказы
поставщика… Пересмотри эту логику»).

Инварианты, которые держит вёрстка:
  * срок сделки — ближайший срок НЕдоставленного заказа, с номером заказа и счётом заказов
    («доставлено 2 из 3, к сроку 2»); срок доставленного заказа сроком сделки не ставится (E33);
  * шаг в строке сделки — с номером заказа, чей он; остальные живые заказы — «ещё N заказов» (E32);
  * плитки: главное число — сделки, заказы — в скобках; деньги — доля этих заказов с оговоркой
    «оценка по доле закупки», сумма сделок — мелко (E35, E20);
  * «Требует решения»: у каждого числа единица словом, сделки и заказы — двумя числами (E36);
  * «ещё N дел» в строке и «Что сделать — N дел» в карточке — одно правило: дело = единица + код +
    действие + хозяин (E39);
  * «Смотреть как»: в строке первым — дело выбранного человека (E40);
  * карточка: «По сделке» — только пункты сделки, «По заказам» — у каждого дела номер заказа;
    «ОСС сделки» и «ОСС заказа/заказов» — разными строками (E38, E41); оплата — номерами заказов (E34);
  * путь заказа: число «Что сделать» — только дела заказа, дела сделки — одной строкой со ссылкой
    на карточку; шаг 1 — «по сделке целиком», у позднего заказа — «назначение менеджера ОРК» (E42, E43);
  * поля договора (unit, ords, dlNextOpen, stepCell.lbl) читаются, когда сборка их отдаёт.
Корпус придуман (CLAUDE.md, правило 18): фикстура и дописанные здесь случаи.
"""
from __future__ import annotations

import copy
import html
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import lifecycle  # noqa: E402

PROBE = r"""<script>(function(){
var out={};
function txt(e){return e?(e.textContent||'').replace(/\s+/g,' ').trim():'';}
function rows(){return [].slice.call(document.querySelectorAll('#ctPanel-main #ctBody tr[data-ct]')).map(function(tr){
  return {deal:tr.dataset.ct, act:txt(tr.querySelector('td.c-do')), ord:txt(tr.querySelector('td.c-ord')),
    dl:txt(tr.querySelector('td.c-dl')), pay:txt(tr.querySelector('td.c-pay'))};});}
function secs(body){
  // подразделы «Что сделать»: h4 и всё до следующего h4 / h3
  return [].slice.call(body.querySelectorAll('.ctd-h4')).map(function(h){
    var L=[], n=h.nextElementSibling;
    while(n&&!(n.classList&&(n.classList.contains('ctd-h4')||n.classList.contains('ctd-h')))){
      [].slice.call(n.querySelectorAll?n.querySelectorAll('li'):[]).forEach(function(li){L.push(txt(li));});
      n=n.nextElementSibling;}
    return {h:txt(h), items:L};});}
try{
  var CT=[].slice.call(document.querySelectorAll('.tabs .tab')).filter(function(x){return x.dataset.tab==='contracts'})[0];
  CT.click();
  document.querySelector('#tab-contracts [role="tab"][data-sub="main"]').click();
  var X=window.__CONTRACTS__, by={};
  X.rows.forEach(function(r){by[String(r.deal)]=r;});
  var P=document.getElementById('ctPanel-main');
  out.rows=rows();
  out.say=txt(document.getElementById('ctdSay'));
  out.tiles=[].slice.call(P.querySelectorAll('.ctd-tile')).map(function(t){var b=t.querySelector('.ctd-tb');
    return {m:b.dataset.m, v:txt(t.querySelector('.ctd-tv')), sub:txt(t.querySelector('.ctd-ts')), aria:b.getAttribute('aria-label')};});
  out.hB=txt(document.getElementById('ctdB'));
  out.cr=[].slice.call(P.querySelectorAll('.ctd-b .ctd-cr')).map(function(b){var w=b.parentElement, o=w&&w.classList.contains('ctd-crw')?w.querySelector('[data-ctol]'):null;
    return {m:b.dataset.m||'', n:txt(b.querySelector('.ctd-crn')), t:txt(b), ord:o?txt(o):'', ol:o?o.dataset.ctol:''};});
  // список заказов за числом «Требует решения» открывается (_odOpen) с тем же числом строк
  out.olOpen={};
  out.cr.forEach(function(c){if(!c.ol) return; var calls=[], keep=window._odOpen;
    window._odOpen=function(X1,O,S){calls.push(S&&S.keys&&S.keys.k?S.keys.k.length:-1);};
    P.querySelector('[data-ctol="'+c.ol+'"]').click(); window._odOpen=keep; out.olOpen[c.m]=calls;});
  out.cards={};
  out.rows.forEach(function(r){
    openContract(by[r.deal]);
    var body=document.getElementById('drwBody');
    out.cards[r.deal]={h3:[].slice.call(body.querySelectorAll('.ctd-h')).map(txt), secs:secs(body),
      facts:[].slice.call(body.querySelectorAll('dl.ctd-facts > div')).map(function(d){return [txt(d.querySelector('dt')),txt(d.querySelector('dd'))];}),
      badges:[].slice.call(body.querySelectorAll('.ctd-badge')).map(txt)};
    document.getElementById('drwX').click();
  });
  // путь заказа: разметка уровня заказа без перехода
  out.path={};
  X.rows.forEach(function(r){(r.orders||[]).forEach(function(o){_OP.s=null; out.path[o.id]=_opHtml(String(o.id)); _OP.s=1; out.path['s1:'+o.id]=_opHtml(String(o.id)); _OP.s=null;});});
  // «Смотреть как»: человек с id 30 (исполнитель пунктов, не КАМ)
  _rzKam.v='30'; P._ctdRe(); out.rowsK=rows(); _rzKam.v=''; P._ctdRe();
}catch(e){out.err=String(e&&e.stack||e);}
var p=document.createElement('pre'); p.id='t3out'; p.textContent=JSON.stringify(out); document.body.appendChild(p);
})();</script>"""


def _render(tmp_path_factory, name, mutate=None):
    import validate_dashboard as vd
    chrome, _v = vd.find_chrome()
    if not chrome:
        pytest.skip("нет рабочего Chromium/Chrome — проверка страницы пропущена")
    import dashboard
    from tests import fixture
    r = json.loads(json.dumps(fixture.build_realization(), ensure_ascii=False, default=str))
    if mutate:
        mutate(r)
    d = tmp_path_factory.mktemp(name)
    p = dashboard.write(fixture.build_metrics(), {"source": "rules", "items": []}, d / "index.html", **r)
    p.write_text(p.read_text(encoding="utf-8").replace("</body>", PROBE + "</body>", 1), encoding="utf-8")
    dom, log = vd._chrome_run(chrome, p, [], 180)
    m = re.search(r'<pre id="t3out">(.*?)</pre>', dom, re.S)
    assert m, "страница не отдала результат пробы: " + log[-800:]
    out = json.loads(html.unescape(m.group(1)))
    assert "err" not in out, out.get("err")
    return r, out


# --- поля договора 4.3, как их отдаст расчёт (PY-1, PY-2, PY-4): придуманный второй неоплаченный заказ
# сделки 955 и свёртка неоплаты в один пункт сделки с ords
def _contract_fields(r):
    C = r["contracts"]
    rows = {x["deal"]: x for x in C["rows"]}
    q = C["life"]["queue"]
    u = next(x for x in q if x["code"] == "unpaid" and x["deal"] == "6005")
    o1 = next(o for o in rows["6005"]["orders"] if o["lbl"] == "955/1")
    # после свёртки (lifecycle._fold_deal_codes) пункт unpaid — сделки, его заказ — в ords
    oid = u.get("order") or u["ords"][0]["id"]
    o2 = next(o for o in rows["6005"]["orders"] if str(o["id"]) == str(oid))
    item = dict(u, unit="deal", bucket="deal", order="", ordLbl="", ordNo=None,
                ords=[{"id": o2["id"], "lbl": o2["lbl"], "due": "04.09.2026", "days": 45, "revEur": o2["revEur"]},
                      {"id": o1["id"], "lbl": o1["lbl"], "due": "20.09.2026", "days": 30, "revEur": o1["revEur"]}])
    q[q.index(u)] = item
    # «Клиент не оплатил» — одно значение на подвкладках: плитка «Недели» (расчёт) знает второй заказ
    C["ork"]["week"]["tiles"]["unpaid"]["orders"] += 1
    for x in q:
        x.setdefault("unit", "order" if x.get("order") else "deal")
    # строка сделки с полями расчёта: срок открытого заказа и шаг — с номером
    r973 = rows["6023"]
    r973["dlNextOpen"], r973["dlNextLbl"] = "06.12.2026", "973/1"
    r973["ordStat"] = {"n": 3, "delivered": 2, "ontime": 2, "late": 0}


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    return _render(tmp_path_factory, "t3_base")


@pytest.fixture(scope="module")
def page_fields(tmp_path_factory):
    return _render(tmp_path_factory, "t3_fields", _contract_fields)


def _dk(s):
    return s[6:10] + s[3:5] + s[:2] if s else ""


def _cases(items):
    return {(("order" if i.get("order") else "deal") if not i.get("unit") else i["unit"], i["code"], i["act"], i["who"])
            for i in items}


def _q(r, deal):
    C = r["contracts"]
    D = r.get("dealctx") or {}
    return [i for i in C["life"]["queue"] + (D.get("queue") or []) if str(i["deal"]) == str(deal)]


def test_срок_сделки_только_открытых_заказов(page):
    r, out = page
    rows = {x["deal"]: x for x in r["contracts"]["rows"]}
    seen = 0
    for row in out["rows"]:
        x = rows[row["deal"]]
        if x["closed"] or x["lateN"]:
            continue
        opn = sorted((o for o in x["orders"] if o["live"] and not o["delivered"] and o["dl"]), key=lambda o: _dk(o["dl"]))
        if not opn:
            continue
        seen += 1
        assert row["dl"].startswith(opn[0]["dl"][:5]) and opn[0]["lbl"] in row["dl"], (row, opn[0]["dl"])
        assert "ближайший срок заказа" in row["dl"]
        # срок доставленного заказа — не срок сделки (E33)
        dlv = [o["dl"][:5] for o in x["orders"] if o["delivered"] and o["dl"] and o["dl"] != opn[0]["dl"]]
        assert not any(row["dl"].startswith(d) for d in dlv), row
    assert seen > 3
    r973 = next(z for z in out["rows"] if z["deal"] == "6023")
    assert r973["dl"].startswith("06.12 · 973/1"), r973["dl"]
    assert "доставлено 2, к сроку 2" in r973["dl"] and "из 3 заказов" in r973["dl"], r973["dl"]
    f = dict(out["cards"]["6023"]["facts"])
    assert "06.12.2026" in f["Срок клиента"] and "973/1" in f["Срок клиента"] and "26.09" not in f["Срок клиента"], f


def test_сорванный_срок_с_номером_заказа(page):
    r, out = page
    rows = {x["deal"]: x for x in r["contracts"]["rows"]}
    for row in out["rows"]:
        x = rows[row["deal"]]
        if x["closed"] or not x["lateN"]:
            continue
        worst = max((o for o in x["orders"] if o["live"] and o["late"]), key=lambda o: o["dlDays"] or 0)
        assert worst["lbl"] in row["dl"] and f"просрочен на {x['lateDays']} дн." in row["dl"], row
        assert re.search(rf"после срока {x['lateN']} заказ", row["dl"]), row
        b = " ".join(out["cards"][row["deal"]]["badges"])
        assert worst["lbl"] in b or re.search(r"сорван у \d+ заказ", b), b


def test_шаг_строки_с_номером_заказа(page):
    r, out = page
    rows = {x["deal"]: x for x in r["contracts"]["rows"]}
    n = 0
    for row in out["rows"]:
        x = rows[row["deal"]]
        c = x.get("stepCell")
        if not c or x["closed"] or c.get("label") == "закрыть сделку" or not x["orders"]:
            continue
        on = [o for o in x["orders"] if o["live"] and o["step"] == c["step"]]
        m = re.search(rf"Шаг {c['step']}( «[^»]*»)? · (\d+/\d+)", row["ord"])
        assert m and m.group(2) in {o["lbl"] for o in on}, (row["ord"], c["step"])
        others = [o for o in x["orders"] if o["live"] and o["step"] is not None and o["lbl"] != m.group(2)]
        if others:
            assert re.search(rf"ещё {len(others)} заказ", row["ord"]), row["ord"]
        n += 1
    assert n > 5


def test_плитки_сделки_заказы_и_доля_денег(page):
    r, out = page
    C = r["contracts"]
    live = [x for x in C["rows"] if not x["closed"] and not x.get("seg")]
    q = C["life"]["queue"]
    unp = {x["deal"] for x in live if any(i["code"] == "unpaid" and i["deal"] == x["deal"] for i in q)}
    unp_o = {o for i in q if i["code"] == "unpaid" and i["deal"] in unp for o in lifecycle.item_orders(i)}
    T = {t["m"]: t for t in out["tiles"]}
    assert T["unpaid"]["v"].startswith(f"{len(unp)} сдел"), T["unpaid"]
    assert f"({len(unp_o)} заказ" in T["unpaid"]["v"], T["unpaid"]
    assert "оценка по доле закупки" in T["unpaid"]["sub"] and "сумма сделок, где есть такой заказ" in T["unpaid"]["sub"]
    late = [x for x in live if x["late"]]
    assert T["late"]["v"].startswith(f"{len(late)} сдел") and f"({sum(x['lateN'] for x in late)} заказ" in T["late"]["v"], T["late"]
    rev = sum(x["lateRev"] for x in late if x.get("saleKnown") is not False)
    assert f"€{round(rev / 1e3)} тыс." in T["late"]["sub"].replace(" ", " ").replace(" ", "") or \
        f"€{round(rev / 1e3)}" in T["late"]["sub"].replace(" ", ""), (rev, T["late"]["sub"])
    assert "доля опоздавших заказов" in T["late"]["sub"]
    # одна подпись — одно значение: «Клиент не оплатил» = отбор таблицы (сделки)
    assert re.search(rf"по {len(unp)} сделк", out["say"]), out["say"]


def test_требует_решения_двумя_единицами(page):
    r, out = page
    C = r["contracts"]
    live = [x for x in C["rows"] if not x["closed"] and not x.get("seg")]
    q = C["life"]["queue"]
    assert out["cr"]
    for c in out["cr"]:
        assert re.search(r"\d+ (сдел|заказ)", c["n"]), c          # единица — словом у каждого числа
    nd = {x["deal"] for x in live if any(i["code"] == "noPlanDates" and i["deal"] == x["deal"] for i in q)}
    no = {i["order"] for i in q if i["code"] == "noPlanDates" and i["deal"] in nd}
    c = next(z for z in out["cr"] if z["m"] == "nodates")
    assert c["n"].startswith(f"{len(nd)} сдел") and c["ord"].startswith(f"{len(no)} заказ"), c
    assert out["olOpen"]["nodates"] == [len(no)], out["olOpen"]
    assert re.search(r"\d+ сдел", out["hB"]), out["hB"]


def test_ещё_дел_как_в_карточке(page):
    r, out = page
    rows = {x["deal"]: x for x in r["contracts"]["rows"]}
    D = (r.get("dealctx") or {}).get("deals") or {}
    n = 0
    for row in out["rows"]:
        x = rows[row["deal"]]
        if x["closed"]:
            continue
        items = _q(r, row["deal"])
        td = (D.get(str(row["deal"])) or {}).get("todo")
        k = len(_cases(items)) + (1 if td and not any(i["code"] == td["code"] and i["act"] == td["act"] for i in items) else 0)
        if not k:
            continue
        m = re.search(r"ещё (\d+) дел", row["act"])
        assert (int(m.group(1)) if m else 0) == k - 1, (row["deal"], row["act"], k)
        h = out["cards"][row["deal"]]["h3"][0]
        assert re.fullmatch(rf"Что сделать — {k} дел[оа]?", h), (row["deal"], h, k)
        n += 1
    assert n > 5


def test_карточка_по_сделке_и_по_заказам(page):
    r, out = page
    n = 0
    for deal, c in out["cards"].items():
        items = _q(r, deal)
        if not items:
            continue
        S = {s["h"].split(" — ")[0]: s for s in c["secs"]}
        assert set(S) == {"По сделке", "По заказам"}, c["secs"]
        no = len(_cases([i for i in items if i.get("order")]))
        nd = len(_cases([i for i in items if not i.get("order")]))
        assert (len(S["По заказам"]["items"]) == no) and (len(S["По сделке"]["items"]) == nd) or \
            (deal in ((r.get("dealctx") or {}).get("deals") or {})), (deal, c["secs"])
        for li in S["По заказам"]["items"]:
            assert re.search(r"\d+/\d+", li), li                 # у дела заказа — номер заказа
        n += 1
        dts = [f[0] for f in c["facts"]]
        assert "ОСС сделки" in dts and "ОСС" not in dts, dts
    assert n > 5
    # сделка 973: ОСС заказов — свои у каждого заказа, отдельной строкой
    f = dict(out["cards"]["6023"]["facts"])
    assert "ОСС заказов" in f and "973/1" in f["ОСС заказов"], f


def test_путь_заказа_только_дела_заказа(page):
    r, out = page
    C = r["contracts"]
    q = C["life"]["queue"] + (((C.get("sep") or {}).get("life") or {}).get("queue") or [])
    # пункты закрытой части с заказом (сверка договоров с f.order — пункт заказа, PY-3) — дела заказа
    q = q + [i for i in ((r.get("dealctx") or {}).get("queue") or []) if i.get("order")]
    for x in C["rows"]:
        dq = [i for i in q + ((r.get("dealctx") or {}).get("queue") or []) if i["deal"] == x["deal"] and not i.get("order")]
        for o in x["orders"]:
            if not o["live"]:
                continue
            h = out["path"][str(o["id"])]
            oq = [i for i in q if str(i.get("order")) == str(o["id"])]
            m = re.search(r'id="opTodoH">Что сделать по заказу(?: — (\d+) дел)?', h)
            assert m and int(m.group(1) or 0) == len(_cases(oq)), (o["lbl"], m and m.group(0), len(oq))
            if dq:
                assert re.search(rf"По сделке №<span class=\"op-no\">{x['seq']}</span>: {len(_cases(dq))} дел", h), o["lbl"]
                assert 'data-opgo="card">в карточке сделки' in h
            # шаг 1 — по сделке целиком
            assert "по сделке целиком" in h
    # заказ 933/3 добавлен в давно принятую сделку: шаг 1 — назначение менеджера ОРК, не приёмка
    o3 = next(o for x in C["rows"] for o in x["orders"] if o["lbl"] == "933/3")
    h = out["path"][str(o3["id"])]
    # сделку приняли с заказом, ушедшим дальше шага 1 (933/5 — самый ранний из них); признак — из расчёта
    # (строка «Недели» asg, lifecycle._deal_accept), а не «первый созданный заказ»
    assert "сделка принята с заказом 933/5" in h and "назначение менеджера" in h, h[:400]
    assert "у этого заказа — назначение менеджера" in out["path"]["s1:" + str(o3["id"])]
    # 933/2 создан раньше всех, но сделку уже приняли с другими заказами: у него — тоже назначение
    o2 = next(o for x in C["rows"] for o in x["orders"] if o["lbl"] == "933/2")
    assert "у этого заказа — назначение менеджера" in out["path"]["s1:" + str(o2["id"])]


def test_смотреть_как_дело_человека(page):
    r, out = page
    rowsK = {z["deal"]: z for z in out["rowsK"]}
    n = 0
    for deal, z in rowsK.items():
        items = sorted(_q(r, deal), key=lambda i: i.get("sev") if i.get("sev") is not None else 99)
        mine = [i for i in items if "30" in (str(i.get("whoId") or ""), str(i.get("kamId") or ""), str(i.get("escalateToId") or ""))]
        if not mine or items[0] in mine:
            continue
        act = mine[0]["act"][:18].lower()
        assert act in z["act"].lower(), (deal, z["act"], mine[0]["act"])
        n += 1
    assert n > 0, "в фикстуре есть сделка, где первое дело — не человека с id 30"


def test_поля_договора_ords_и_срок(page_fields):
    r, out = page_fields
    T = {t["m"]: t for t in out["tiles"]}
    C = r["contracts"]
    live = {x["deal"] for x in C["rows"] if not x["closed"] and not x.get("seg")}
    unp = {i["deal"] for i in C["life"]["queue"] if i["code"] == "unpaid" and i["deal"] in live}
    orders = set()
    for i in C["life"]["queue"]:
        if i["code"] == "unpaid" and i["deal"] in live:
            orders |= {str(o["id"]) for o in i.get("ords") or []} or {str(i["order"])}
    # сделок столько же, заказов на один больше: свёртка не теряет заказ и не добавляет сделку
    assert T["unpaid"]["v"].startswith(f"{len(unp)} сдел") and f"({len(orders)} заказ" in T["unpaid"]["v"], T["unpaid"]
    row = next(z for z in out["rows"] if z["deal"] == "6005")
    assert "не оплачено: 2 заказа с 04.09" in row["pay"] and "955/2 с 04.09, 955/1 с 20.09" in row["pay"], row["pay"]
    assert "по сделке" in row["act"] or "955/" in row["act"]
    c = out["cards"]["6005"]
    S = {s["h"].split(" — ")[0]: s for s in c["secs"]}
    assert any("955/2" in li and "955/1" in li for li in S["По сделке"]["items"]), S["По сделке"]
    f = dict(c["facts"])
    assert "не оплачено: 2 заказа" in f["Оплата клиента"], f
    r973 = next(z for z in out["rows"] if z["deal"] == "6023")
    assert r973["dl"].startswith("06.12 · 973/1") and "доставлено 2, к сроку 2" in r973["dl"], r973
