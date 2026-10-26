"""«Реализация» в евро по ЦБ РФ на дату КП и без НДС по правилу (решения владельца 25 и
26.10.2026).

Что проверяется — то, на чём курс и НДС ломаются:
  · разбор рядов ЦБ: номинал 10 и 100 — через VunitRate, без него — Value / Nominal;
    выходной и праздник — курс последнего рабочего дня; дата раньше ряда — курса нет;
  · валюты нет у ЦБ — сумма неизвестна (fxMissing), а не 1:1 и не курс Битрикса;
  · ЦБ и зеркало недоступны — курсы Битрикса, плашка и счёт сделок, не молча;
  · дата КП: «ТКП согласован» → КП до выигрыша (если поле в день создания) → вход в кат. 0 →
    создание; заказ — по курсу на дату заказа, бюджет — на дату книги;
  · НДС: покупатель из РФ и без реквизитов — ÷ 1,20 (ТКП до 2026) или ÷ 1,22, иностранный —
    как есть; продажа сверенного файла не трогается;
  · итоги: Σ продажи множества маржи − Σ закупки = Σ маржи — ровно;
  · в сеть тесты не ходят: загрузчику подставлен придуманный cbr.ru (fixture.ct_cbr_fetch).
Корпус придуман (CLAUDE.md, правило 18): сделки и ряды ContractsStub.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

import contracts as ct
from base import fx
from tests import fixture

D = dt.date


def _xml_series(recs: list[tuple[str, str, str, str | None]]) -> bytes:
    """Ответ XML_dynamic: [(дата дд.мм.гггг, номинал, Value, VunitRate | None)]."""
    body = "".join(f'<Record Date="{d}" Id="R0"><Nominal>{n}</Nominal><Value>{v}</Value>'
                   + (f"<VunitRate>{u}</VunitRate>" if u is not None else "") + "</Record>"
                   for d, n, v, u in recs)
    return f'<?xml version="1.0"?><ValCurs ID="R0">{body}</ValCurs>'.encode()


def _compute(stub=None, **kw) -> dict:
    old = os.environ.get("KVANT_NOW")
    os.environ["KVANT_NOW"] = fixture.CT_NOW
    kw.setdefault("fx_loader", fixture.ct_fx_loader)
    try:
        return ct.compute(stub or fixture.ContractsStub(), as_of=fixture.CT_TODAY, people={}, **kw)
    finally:
        if old is None:
            os.environ.pop("KVANT_NOW", None)
        else:
            os.environ["KVANT_NOW"] = old


def _row(data: dict, did: str) -> dict:
    return next(r for r in data["rows"] if r["deal"] == did)


def _day(s: str) -> D:
    return dt.datetime.strptime(s, "%d.%m.%Y").date()


def _eur_per(code: str, day: D) -> float:
    """€ за единицу валюты по придуманному ряду на дату (последний рабочий день не позже)."""
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    rub = 1.0 if code == "RUB" else fixture.ct_cbr_value(code, day)
    return 1.0 if code == "EUR" else rub / fixture.ct_cbr_value("EUR", day)


# ── ряды ЦБ ───────────────────────────────────────────────────────────────────

def test_номинал_10_и_100_через_vunitrate():
    rows = fx.parse_cbr_series(_xml_series([
        ("09.01.2025", "10", "135,1234", "13,51234"),       # юань: номинал 10
        ("10.01.2025", "100", "120,5", None),               # иена без VunitRate: Value / Nominal
        ("13.01.2025", "100", "105,0", "1,07"),             # рупия, номинал 100: VunitRate первым
    ]))
    assert rows == [(D(2025, 1, 9), pytest.approx(13.51234)), (D(2025, 1, 10), pytest.approx(1.205)),
                    (D(2025, 1, 13), pytest.approx(1.07))]


def test_выходной_и_праздник_берут_последний_рабочий_день():
    k = fx.CbrRates({"EUR": [(D(2025, 12, 30), 95.0), (D(2026, 1, 10), 97.0)], "USD": [(D(2025, 12, 30), 80.0)]})
    assert k.value("EUR", D(2026, 1, 3)) == 95.0          # праздники: курс 30.12
    assert k.value("EUR", D(2026, 1, 10)) == 97.0
    assert k.cross("USD", "EUR", D(2026, 1, 5)) == pytest.approx(95.0 / 80.0)


def test_дата_раньше_ряда_курса_нет():
    k = fx.CbrRates({"EUR": [(D(2025, 1, 9), 95.0)]})
    assert k.value("EUR", D(2025, 1, 8)) is None and k.to_eur(100, "RUB", D(2025, 1, 8)) is None
    курсы = ct.Курсы({"EUR": 1.0, "RUB": 0.01}, k)
    assert курсы.at(D(2025, 1, 8)) == {}, "не курс Битрикса и не ближайший курс ЦБ"
    assert ct._to_eur(1000, "RUB", курсы.at(D(2025, 1, 8))) is None
    assert ct._to_eur(9500, "RUB", курсы.at(D(2025, 1, 9))) == pytest.approx(100.0)


def test_загрузчик_один_запрос_на_валюту_и_кеш_прогона():
    calls = []

    def fetch(url):
        calls.append(url)
        return fixture.ct_cbr_fetch(url)

    k, info = fx.load_cbr({"USD", "CNY", "RUB"}, [D(2025, 3, 3), D(2026, 2, 2)], today=D(2026, 10, 19), fetch=fetch)
    assert info["src"] == "cbr" and info["requests"] == 4 and len(calls) == 4   # справочник + EUR, USD, CNY
    assert info["lo"] == D(2025, 3, 3) - dt.timedelta(days=fx.BUILD_LEAD_DAYS)
    # юань: номинал 10 в ряду, курс за ОДНУ единицу
    assert k.value("CNY", D(2025, 3, 3)) == pytest.approx(fixture.ct_cbr_value("CNY", D(2025, 3, 3)))
    k2, info2 = fx.load_cbr({"USD", "CNY"}, [D(2025, 3, 3), D(2026, 2, 2)], today=D(2026, 10, 19), fetch=fetch)
    assert info2["requests"] == 0 and len(calls) == 4, "повтор в прогоне в сеть не ходит"
    assert k2.value("USD", D(2026, 2, 2)) == k.value("USD", D(2026, 2, 2))


def test_валюты_нет_у_цб_сумма_неизвестна():
    stub = fixture.ContractsStub()
    stub.deals[3]["CURRENCY_ID"] = "XAU"                 # у придуманного ЦБ ряда нет
    data = _compute(stub)
    r = _row(data, stub.deals[3]["ID"])
    assert r["saleKnown"] is False and r["saleEur"] == 0 and r["fxMissing"] == ["XAU"]
    assert r["vatRule"] == "none" and r["rateSrc"] == "cbr"
    assert "XAU" in data["fx"]["noCode"] and data["fx"]["noRate"] >= 1


def test_дата_кп_раньше_ряда_сумма_неизвестна():
    stub = fixture.ContractsStub()
    stub.deals[1][ct.WON_F] = "2023-06-01"               # раньше придуманного ряда
    data = _compute(stub, fx_loader=lambda c, d, t: fx.load_cbr(
        c, [x for x in d if x >= fixture.CT_CBR_FROM + dt.timedelta(days=60)], today=t, fetch=fixture.ct_cbr_fetch))
    r = _row(data, stub.deals[1]["ID"])
    assert r["kpDate"] == "01.06.2023" and r["kpSrc"] == "tkp"
    assert r["saleKnown"] is False and r["fxMissing"] == ["EUR"], "курса на дату нет — не ближайший"


# ── курс ЦБ на дату КП и на дату заказа ───────────────────────────────────────

def test_рубли_по_цб_на_дату_кп_и_заказы_на_дату_заказа():
    stub = fixture.ContractsStub()
    d = stub.deals[5]                                    # i=5: «ТКП согласован» в 2025 году
    d["CURRENCY_ID"], d["OPPORTUNITY"] = "RUB", "12000000"
    did = d["ID"]
    for o in stub.orders:
        if str(o["parentId2"]) == did:
            o["currencyId"], o["opportunity"] = "USD", 10_000
    data = _compute(stub)
    r = _row(data, did)
    kp = _day(r["kpDate"])
    assert r["kpSrc"] == "tkp" and kp.year == 2025 and r["rateSrc"] == "cbr"
    assert r["vatRule"] == "gross" and r["vatK"] == 1.20, "ТКП до 2026 — 20 %"
    assert r["saleEur"] == round(12_000_000 * _eur_per("RUB", kp) / 1.20)
    want = sum(10_000 * _eur_per("USD", _day_of_order(o))
               for o in stub.orders if str(o["parentId2"]) == did)
    assert r["buyOrdersEur"] == round(want), "заказ — по курсу на дату заказа, а не на дату КП"


def _day_of_order(o: dict) -> D:
    return dt.datetime.fromisoformat(o["createdTime"]).astimezone(dt.timezone(dt.timedelta(hours=3))).date()


def test_ткп_в_день_создания_уступает_кп_из_истории():
    data = _compute()
    stub = fixture.ContractsStub()
    for i in (2, 6, 10):                                 # i % 4 == 2: поле = день создания, КП раньше
        r = _row(data, stub.deals[i]["ID"])
        created = dt.datetime.fromisoformat(stub.deals[i]["DATE_CREATE"]).date()
        assert r["kpSrc"] == "offer" and _day(r["kpDate"]) == created - dt.timedelta(days=12)
    assert data["fx"]["nOffers"] == 6 and not data["fx"]["kpErr"]
    # без поля и без КП — вход в кат. 0; сделка вне кат. 0 без истории — создание
    assert _row(data, stub.deals[0]["ID"])["kpSrc"] == "cat0"
    assert _row(data, "6100")["kpSrc"] == "created"


def test_дата_кп_правило_по_порядку():
    z = ct._зонд()
    w = {ct.WON_F: "2025-05-20", "DATE_CREATE": "2025-03-01T10:00:00+03:00"}
    assert ct._kp_date(w, [D(2025, 4, 1)], [D(2025, 5, 25)], z=z) == (D(2025, 5, 20), "tkp")
    у_создания = {ct.WON_F: "2025-03-01", "DATE_CREATE": "2025-03-01T10:00:00+03:00"}
    assert ct._kp_date(у_создания, [D(2025, 2, 10), D(2025, 2, 20)], [D(2025, 3, 1)], z=z) == (D(2025, 2, 20), "offer")
    assert ct._kp_date(у_создания, [], [D(2025, 3, 1)], z=z) == (D(2025, 3, 1), "tkp"), "КП нет — поле остаётся"
    без = {"DATE_CREATE": "2025-03-01T10:00:00+03:00"}
    assert ct._kp_date(без, [], [D(2025, 4, 2), D(2025, 6, 1)], z=z) == (D(2025, 4, 2), "cat0")
    assert ct._kp_date(без, [], [], z=z) == (D(2025, 3, 1), "created")
    assert ct._kp_date({}, [], [], z=z) == (None, "today")


def test_история_кп_не_прочитана_даты_запасные():
    stub = fixture.ContractsStub()

    def сбой(*a, **k):
        raise RuntimeError("QUERY_LIMIT_EXCEEDED")
    stub.call_envelope = сбой
    data = _compute(stub)
    assert data["fx"]["kpErr"] == "RuntimeError" and "offer" not in data["fx"]["kpSrc"]
    assert _row(data, stub.deals[2]["ID"])["kpSrc"] == "tkp"


# ── НДС по правилу ────────────────────────────────────────────────────────────

def test_ндс_рф_без_реквизитов_и_иностранный():
    stub = fixture.ContractsStub()
    data = _compute(stub)
    by_co = {}
    for d in stub.deals:
        r = _row(data, d["ID"])
        by_co.setdefault(d["COMPANY_ID"], set()).add((r["vatRule"], r["vatBuyer"]))
        kp = _day(r["kpDate"])
        k = 1.0 if d["COMPANY_ID"] == fixture.CT_FOREIGN_CO else (1.22 if kp >= D(2026, 1, 1) else 1.20)
        assert r["saleEur"] == round(float(d["OPPORTUNITY"]) * _eur_per(d["CURRENCY_ID"], kp) / k)
    assert by_co[fixture.CT_FOREIGN_CO] == {("foreign", "foreign")}
    assert by_co["300"] == {("gross", "ru")} and by_co["302"] == {("gross", "noreq")}
    assert data["fx"]["vat"]["foreign"] == 3 and data["fx"]["nCountry"] == 3


def test_ндс_20_и_22_по_дате_итогового_ткп():
    z = ct._зонд()
    assert ct._vat("РФ", D(2025, 12, 31), z=z) == ("gross", 1.20)
    assert ct._vat("РФ", D(2026, 1, 1), z=z) == ("gross", 1.22)
    assert ct._vat(None, D(2026, 3, 1), z=z) == ("gross", 1.22), "без реквизитов — как РФ"
    assert ct._vat("не РФ", D(2026, 3, 1), z=z) == ("foreign", 1.0)


def test_реквизиты_не_прочитаны_ндс_снят_у_всех_и_сказано():
    stub = fixture.ContractsStub()
    orig = stub.call

    def call(method, params=None, **kw):
        if method == "crm.requisite.list":
            raise RuntimeError("ACCESS_DENIED")
        return orig(method, params, **kw)
    stub.call = call
    data = _compute(stub)
    assert data["fx"]["reqErr"] == "RuntimeError" and data["fx"]["vat"].get("foreign", 0) == 0


def test_продажа_файла_не_трогается():
    stub = fixture.ContractsStub()
    d = stub.deals[1]
    amt = float(d["OPPORTUNITY"])
    e = {"at": dt.datetime(2026, 10, 18, 9, tzinfo=dt.timezone.utc), "sale": amt, "cur": "EUR",
         "saleEur": 26_000.0, "buyEur": 15_000.0, "markup": 0.73, "rebid": False, "items": 4, "fx": {},
         "ratio": 1.0}
    data = _compute(stub, ctx_econ={d["ID"]: e})
    r = _row(data, d["ID"])
    assert r["saleSrc"] == "file" and r["saleEur"] == 26_000 and r["vatRule"] == "net"
    # «Сумма» Битрикса рядом — тоже без НДС по правилу: сравнение нетто с нетто
    assert r["vatBx"] == "gross" and r["saleBitrixEur"] == round(amt / r["vatK"])


# ── итоги и запасной путь ─────────────────────────────────────────────────────

def test_итоги_сходятся_ровно():
    stub = fixture.ContractsStub()
    for i, d in enumerate(stub.deals):                   # смесь валют: курс на дату у каждой своя
        d["CURRENCY_ID"] = ("RUB", "USD", "EUR")[i % 3]
        if d["CURRENCY_ID"] == "RUB":
            d["OPPORTUNITY"] = str(float(d["OPPORTUNITY"]) * 100)
    for j, o in enumerate(stub.orders):
        o["currencyId"] = ("USD", "CNY", "EUR")[j % 3]
    data = _compute(stub)
    for h in [data["head"]] + ([data["sep"]["head"]] if data.get("sep") else []):
        assert h["saleMNum"] - h["buyNum"] == h["marginNum"]
    live = [r for r in data["rows"] if not r["closed"] and not r["seg"]]
    assert ct._money(sum(r["saleEur"] for r in live if r["saleKnown"])) == data["head"]["sale"]
    assert data["fx"]["src"] == "cbr" and data["fx"]["fallback"] is None


def test_цб_и_зеркало_недоступны_курсы_битрикса_с_плашкой():
    def нет(url):
        raise ConnectionError("нет связи")

    def зеркало_нет(url):
        return 503, b""

    loader = (lambda c, d, t: fx.load_cbr(c, d, today=t, fetch=нет, mirror_fetch=зеркало_нет, mirror_pause=0))
    import base.fx as fxm
    old = fxm.MIRROR_RETRY_WAIT
    fxm.MIRROR_RETRY_WAIT = ()
    try:
        data = _compute(fx_loader=loader)
    finally:
        fxm.MIRROR_RETRY_WAIT = old
    f = data["fx"]
    assert f["src"] == "bitrix" and f["fallback"]["why"] == "ЦБ недоступен"
    assert f["fallback"]["n"] == len(data["rows"]) and f["lbl"].startswith("курсы Битрикса (ЦБ недоступен)")
    assert {r["rateSrc"] for r in data["rows"]} == {"bitrix"}
    # по Битриксу — тот же расчёт, что до 25.10.2026 (НДС по правилу снят и здесь)
    r = _row(data, "6000")
    assert r["saleEur"] == round(20_000 / r["vatK"])
    assert f["usdRub"] == 90.0 and f["asOf"] == "01.10.2026"


def test_зеркало_когда_cbr_ru_не_ответил():
    def нет(url):
        raise ConnectionError("нет связи")

    def зеркало(url):
        import json
        if "archive" not in url:
            return 200, json.dumps({"Date": "2026-10-19T11:30:00+03:00", "Valute": {"EUR": {
                "CharCode": "EUR", "Nominal": 1, "Value": 100.0}}}).encode()
        y, m, dd = (int(x) for x in url.split("/archive/")[1].split("/")[:3])
        day = D(y, m, dd)
        if day.weekday() >= 5:
            return 404, b""
        return 200, json.dumps({"Date": day.isoformat() + "T11:30:00+03:00", "Valute": {
            "EUR": {"CharCode": "EUR", "Nominal": 1, "Value": 100.0},
            "USD": {"CharCode": "USD", "Nominal": 1, "Value": 90.0}}}).encode()

    k, info = fx.load_cbr({"USD"}, [D(2026, 10, 17), D(2026, 10, 14)], today=D(2026, 10, 19), fetch=нет,
                          mirror_fetch=зеркало, mirror_pause=0)
    assert info["src"] == "mirror" and info["why"] == "ConnectionError"
    assert k.value("EUR", D(2026, 10, 17)) == 100.0, "суббота — курс пятницы"
    assert k.cross("USD", "EUR", D(2026, 10, 14)) == pytest.approx(100.0 / 90.0)


def test_без_загрузчика_плашка_цб_не_запрашивался():
    data = _compute(fx_loader=None)
    assert data["fx"]["src"] == "bitrix" and data["fx"]["fallback"]["why"] == "ЦБ не запрашивался"


def test_бюджет_по_цб_на_дату_книги(monkeypatch):
    plan = {"revenue": 1_000_000.0, "margin_income": 100_000.0, "modified": "2025-06-04T10:00:00Z"}
    monkeypatch.setattr(ct, "_load_budget_plans", lambda: ({950: plan}, {}, "2026-10-18T00:00:00Z"))
    r = _row(_compute(), "6000")
    assert r["planFxDay"] == "04.06.2025"
    assert r["planRevEur"] == round(1_000_000 * _eur_per("RUB", D(2025, 6, 4)))


def test_сборка_в_сеть_не_ходит(monkeypatch):
    import requests

    def запрет(*a, **k):
        raise AssertionError("тест пошёл в сеть")
    monkeypatch.setattr(requests, "get", запрет)
    data = fixture.build_contracts()
    assert data["fx"]["src"] == "cbr"


def test_премии_читают_курс_битрикса_шапка_цб_отдельно():
    """premia.compute берёт € → ₽ из fx.eurUsd × fx.usdRub: перевод премий на ЦБ — отдельная
    правка, эта их не сдвигает; курс ЦБ на сегодня для шапки — cbrUsdRub."""
    f = _compute()["fx"]
    assert f["src"] == "cbr" and f["usdRub"] == 90.0, "справочник Битрикса, как до 25.10.2026"
    assert f["cbrUsdRub"] == pytest.approx(fixture.ct_cbr_value("USD", D(2026, 10, 19)), abs=0.01)
    assert _compute(fx_loader=None)["fx"]["cbrUsdRub"] is None


def test_справочник_стадий_сборки_не_перечитывается():
    stub = fixture.ContractsStub()
    meta = stub.deal_stage_meta()

    def второй_раз():
        raise AssertionError("справочник стадий уже прочитан сборкой")
    stub.deal_stage_meta = второй_раз
    data = _compute(stub, stage_meta=meta)
    assert data["fx"]["nOffers"] == 6 and not data["fx"]["kpErr"]


def test_зонд_не_ставит_scripts_в_начало_пути():
    import sys
    ct._зонд()
    assert not sys.path[0].endswith("scripts"), "scripts/ затенил бы модули корня в сборке"


def test_поставщик_без_курса_на_дату_заказа_счётом():
    stub = fixture.ContractsStub()
    o = stub.orders[0]
    o["currencyId"], o["opportunity"] = "XAU", 500
    data = _compute(stub)
    sup = data["suppliers"]["rows"]
    mine = next(r for r in sup if r["id"] == str(o["companyId"]))
    assert mine["noRate"] == 1
    assert all("noRate" not in r for r in sup if r["id"] != str(o["companyId"])), \
        "нулевой счёт не пишется: живая сверка судит его как пустое новое поле"


def test_журнал_курсов_плашка_запасного_пути(monkeypatch, capsys):
    """main._print_fx: запасной путь — предупреждением Actions со счётом сделок; в журнале
    только источник, коды валют и счётчики (правило 17)."""
    import main
    monkeypatch.setenv("KVANT_FX", "bitrix")
    assert main._cbr_loader() is None, "KVANT_FX=bitrix — без сети"
    monkeypatch.delenv("KVANT_FX")
    assert callable(main._cbr_loader())
    data = _compute(fx_loader=None)
    main._print_fx(data["fx"])
    out = capsys.readouterr().out
    assert f"::warning::курсы Битрикса (ЦБ не запрашивался): сделок по ручным курсам {len(data['rows'])}" in out
    assert "€" not in out and not any(r["deal"] in out for r in data["rows"])
