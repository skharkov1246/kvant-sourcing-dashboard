"""Проверка страницы деплоя на случаях живых данных, которых нет в основном корпусе (стресс-проверка 30.10.2026).

Днём 30.10.2026 деплой остановился на «число w:who/204/wait не открыло список заказов» — уволенного в
корпусе не было (tests/test_rz_gone_who.py). Стресс-проверка нашла ещё три случая, которые портал может
дать и на которых проверка деплоя (validate_dashboard.check_browser) падает:
  · тёзки: уволенный и действующий с одной подписью в Битриксе — число строки уволенного открывало
    список действующего (_odWhoKey брал первого по имени без пометки «уволен»);
  · спокойная неделя: шаги ОРК в норме, у плитки «Шаги ОРК дольше нормы» при нуле не было метки KPI 2;
  · в очереди только «Порядок в карточках» (часть hyg): проверка требовала групп «Что сделать сегодня»,
    хотя такие пункты по решению владельца 30.10.2026 — вне дневного счёта.
Каждый случай — своя страница из придуманного корпуса (CLAUDE.md, правило 18) и та же проверка, что у
деплоя; у тёзок — ещё разбор ключа в странице.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from tests import fixture  # noqa: E402

NOW = dt.datetime.fromisoformat(fixture.CT_NOW)
NAME = "Пётр (ОРК)"


def _chrome():
    from scripts import validate_dashboard as vd
    if not vd.find_chrome()[0]:
        pytest.skip("нет Chromium")
    return vd


def _realization(stub=None, people=None) -> dict:
    """Как fixture.build_realization, но со своим корпусом и справочником людей."""
    import contracts as contracts_mod
    import dealctx
    ctx = dealctx.clean(fixture.realctx_raw())
    kw = {"stub": stub} if stub is not None else {}
    if people is not None:
        kw["people"] = people
    ct = fixture.build_contracts(ctx_chats=ctx["chats"], ctx_econ=ctx["econ"], ctx_contracts=ctx["contracts"],
                                 keep_head=True, **kw)
    om, head_ = ct.pop("orkMonth", None), ct.pop("_orkHead", "")
    head, payload = dealctx.build(ct["rows"], contracts_mod.life_all(ct), ctx, NOW, today=fixture.CT_TODAY,
                                  ork_head=head_)
    sep = head.pop("sep", None)
    ct["head"]["ctx"] = head
    if sep is not None and ct.get("sep"):
        ct["sep"]["head"]["ctx"] = sep
    return {"contracts": ct, "dealctx": payload, "ork_month": om}


def _page(tmp_path, name: str, r: dict) -> Path:
    import dashboard
    f = tmp_path / f"{name}.html"
    dashboard.write(fixture.build_metrics(), {"source": "rules", "items": []}, str(f),
                    people=fixture.build_people(), **r)
    return f


def _errors(vd, page: Path) -> list[str]:
    errors: list[str] = []
    warns: list[str] = []
    vd.check_browser(page, errors, warns)
    return errors


class Тёзки(fixture.ContractsStub):
    """Уволенный (id 36) с той же подписью, что действующий менеджер ОРК (id 32): у уволенного — каждый
    второй заказ id 32. Так бывает при пересозданной учётной записи вернувшегося сотрудника или при имени,
    набранном в одно поле."""

    def __init__(self):
        super().__init__()
        k = 0
        for o in self.orders:
            if str(o.get("assignedById")) == "32":
                if k % 2:
                    o["assignedById"] = "36"
                k += 1


def _тёзки_people() -> dict:
    ppl = {u: {"name": p["name"], "active": p.get("active", True), "depts": p.get("depts", [])}
           for u, p in fixture.CT_ROSTER.items()}
    ppl["32"] = {"name": NAME, "active": True, "depts": ["50"]}
    ppl["36"] = {"name": NAME, "active": False, "depts": ["50"]}
    return ppl


def test_тёзки_уволенный_и_действующий(tmp_path):
    r = _realization(Тёзки(), _тёзки_people())
    who = r["contracts"]["ork"]["week"]["drill"]["who"]
    gone = NAME + " (уволен)"
    assert NAME in who and gone in who, "в корпусе должны быть оба: действующий и уволенный тёзка"
    vd = _chrome()
    page = _page(tmp_path, "namesake", r)
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:                       # noqa: BLE001 — своя сборка Chromium не скачана
            b = p.chromium.launch(executable_path=vd.find_chrome()[0])
        pg = b.new_page()
        pg.goto(page.resolve().as_uri())
        got = pg.evaluate("""([a,g])=>{const C=window.__CONTRACTS__, ka=_odWhoKey(a), kg=_odWhoKey(g);
            const r=(k,f)=>((_odResolve(C,C.ork,'w:who/'+k+'/'+f)||{}).k||[]).map(String).sort();
            return {ka, kg, a:r(ka,'over').concat(r(ka,'wait')), g:r(kg,'over').concat(r(kg,'wait'))};}""",
                          [NAME, gone])
        b.close()
    assert got["ka"] == "32" and got["kg"] == "36", got
    want = lambda w: sorted(map(str, (who[w].get("over") or []))) + sorted(map(str, (who[w].get("wait") or [])))
    assert got["a"] == want(NAME) and got["g"] == want(gone)
    assert not [e for e in _errors(vd, page) if "w:who/" in e]


class Спокойная(fixture.ContractsStub):
    """Все карточки сдвинуты недавно: заказы шагов 4 и 8 перешли на шаг 7 пять дней назад, шага 5 — вошли
    вчера. Шаги ОРК в норме: «Шаги ОРК дольше нормы» = 0 при заказах в расчёте."""

    def __init__(self):
        super().__init__()
        ts5, ts1 = (NOW - dt.timedelta(days=5)).isoformat(), (NOW - dt.timedelta(days=1)).isoformat()
        for o in self.orders:
            sc, oid = str(o.get("stageId", "")).split(":")[-1], str(o["id"])
            if sc in ("SENT", "READY"):
                o["stageId"], o["movedTime"] = "DT172_26:PREPARATION", ts5
                self.hist[172].append((oid, "DT172_26:PREPARATION", ts5))
            elif sc in ("PO", "SPEC", "AGREE"):
                self.hist[172] = [h for h in self.hist[172]
                                  if not (h[0] == oid and h[1].split(":")[-1] in ("PO", "SPEC", "AGREE"))]
                self.hist[172].append((oid, o["stageId"], ts1))
                o["movedTime"] = ts1


def test_спокойная_неделя_плитка_W2_ноль(tmp_path):
    r = _realization(Спокойная())
    w2 = r["contracts"]["ork"]["week"]["tiles"]["W2"]
    assert w2.get("value") == 0 and w2.get("state") == "show", w2
    vd = _chrome()
    page = _page(tmp_path, "calm", r)
    errs = _errors(vd, page)
    assert not errs, errs


class ТолькоПорядок(fixture.ContractsStub):
    """Одна сделка с двумя заказами в производстве без плановых дат (noPlanDates — «Порядок в карточках»),
    срок клиента через 200 дней: ни просрочки, ни дольше нормы."""

    def __init__(self):
        super().__init__()
        import contracts as ct
        self.deals = [x for x in self.deals if x["ID"] == "6033"]
        self.other = []
        keep = [o for o in self.orders if str(o["parentId2"]) == "6033" and o["title"][:6] in ("933/1.", "933/9.")]
        for o in keep:
            o[ct.DL_CUSTOMER] = (fixture.CT_TODAY + dt.timedelta(days=200)).isoformat()
        self.orders = keep


def test_очередь_только_порядок_в_карточках(tmp_path):
    ct = fixture.build_contracts(stub=ТолькоПорядок(), keep_head=False)
    om = ct.pop("orkMonth", None)
    q = ct["life"]["queue"]
    assert q and {x.get("bucket") for x in q} == {"hyg"}, [(x["code"], x.get("bucket")) for x in q]
    vd = _chrome()
    page = _page(tmp_path, "hygonly", {"contracts": ct, "dealctx": None, "ork_month": om})
    errs = _errors(vd, page)
    assert not errs, errs
