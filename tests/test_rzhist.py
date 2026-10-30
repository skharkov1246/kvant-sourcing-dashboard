"""История счётов «Реализации» — «±N за неделю» (решение владельца 30.10.2026, п. 1; rzhist.py,
scripts/realization_hist_kv.py).

  · точка — те же счёты, что рисует «Неделя», в своих единицах; ни имён, ни номеров, ни денег;
  · слияние: одна точка на день (тот же день перезаписывается), по дате, не больше 400 дней;
  · правило недели: сегодня − ближайшая точка не позже чем 7 дней назад (в окне недели); нет —
    дельты нет; «срок сорван» до набора истории — восстановлением Д-12 (своя точка первее);
  · ключ KV — свой (rzhist:v1), чтение и запись через настоящий клиент Cloudflare на подменённом
    HTTP (сети нет), сверка чтением; чужая версия или не JSON — ключ не перезаписывается;
  · журнал — только счёты; шаги deploy.yml мягкие, публикация — после деплоя.

Корпус придуман (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import sys
import types
from pathlib import Path
from urllib import error, parse

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import lifecycle  # noqa: E402
import realization_hist_kv as kv  # noqa: E402
import rzhist  # noqa: E402
from tests import fixture  # noqa: E402

TODAY = dt.date(2026, 10, 19)


def _pt(day: dt.date, **v) -> dict:
    m = dict.fromkeys(rzhist.FIELDS, 0)
    m.update(v)
    return {"d": day.isoformat(), "main": m}


def _hist(*pts) -> dict:
    h = rzhist.empty()
    for p in pts:
        h = rzhist.merge(h, p)
    return h


# ── слияние и форма ──────────────────────────────────────────────────────────────────────────
def test_слияние_один_день_одна_точка_и_по_дате():
    h = _hist(_pt(TODAY, lateOrders=5), _pt(TODAY - dt.timedelta(days=2), lateOrders=3))
    assert [p["d"] for p in h["pts"]] == ["2026-10-17", "2026-10-19"]
    h = rzhist.merge(h, _pt(TODAY, lateOrders=9))                       # тот же день — перезапись
    assert len(h["pts"]) == 2 and h["pts"][-1]["main"]["lateOrders"] == 9
    assert h["v"] == rzhist.VERSION and h["units"]["lateOrders"] == "order" and h["units"]["lateDeals"] == "deal"


def test_слияние_не_длиннее_400_дней():
    h = rzhist.empty()
    for k in range(450, -1, -1):
        h = rzhist.merge(h, _pt(TODAY - dt.timedelta(days=k), lateOrders=k))
    assert len(h["pts"]) == rzhist.CAP_DAYS == 400
    assert h["pts"][-1]["d"] == TODAY.isoformat()
    assert (TODAY - dt.date.fromisoformat(h["pts"][0]["d"])).days == 399
    # дыры в днях: старше 400 дней от последней точки — вон, даже если точек меньше 400
    h = _hist(_pt(TODAY - dt.timedelta(days=500)), _pt(TODAY - dt.timedelta(days=10)), _pt(TODAY))
    assert [p["d"] for p in h["pts"]] == [(TODAY - dt.timedelta(days=10)).isoformat(), TODAY.isoformat()]


def test_форма_только_счёты():
    raw = {"v": 1, "pts": [
        _pt(TODAY, lateOrders=2) | {"main": {**_pt(TODAY)["main"], "лишнее": "Компания 401"}, "кто": "Пётр"},
        {"d": "вчера", "main": {}},                                     # дата не ISO
        {"d": "2026-10-10", "main": {"lateOrders": -1}},                # отрицательный счёт
        {"d": "2026-10-11", "main": {"lateOrders": "5"}},               # строка вместо числа
        {"d": "2026-10-12", "main": {"lateOrders": True}},              # bool — не счёт
        {"d": "2026-10-13", "main": {"lateOrders": 3}, "sep": {"x": 1, "lateDeals": 1.5}},
        {"d": "2026-10-14", "main": {"lateOrders": None, "overOrders": 4}},
    ]}
    h, bad = rzhist.clean(raw)
    assert bad == 5 and [p["d"] for p in h["pts"]] == ["2026-10-14", "2026-10-19"]
    текст = json.dumps(h, ensure_ascii=False)
    assert "Компания" not in текст and "Пётр" not in текст and "лишнее" not in текст
    assert set(h["pts"][-1]) == {"d", "main"} and set(h["pts"][-1]["main"]) == set(rzhist.FIELDS)
    assert rzhist.clean({"v": 2, "pts": []}) == (None, 0)
    assert rzhist.clean([]) == (None, 0)


# ── правило недели ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("дни, было, ждём", [
    ((7,), {7: 10}, (2, 7)),               # ровно неделю назад
    ((9, 8), {9: 4, 8: 6}, (6, 8)),        # 7 дней назад точки нет — ближайшая не позже: 8 дней
    ((6, 3), {6: 1, 3: 1}, None),          # только точки моложе недели — дельты нет
    ((14,), {14: 1}, None),                # старше окна недели (сборка стояла 2 недели) — не «за неделю»
    ((13, 1), {13: 5, 1: 0}, (7, 13)),     # самая старая в окне недели
    ((), {}, None),                        # истории нет
])
def test_правило_недели(дни, было, ждём):
    h = _hist(*[_pt(TODAY - dt.timedelta(days=k), overOrders=было[k]) for k in дни], _pt(TODAY, overOrders=12))
    w = rzhist.delta(h, None, "main", "overOrders", TODAY)
    if ждём is None:
        assert w is None
    else:
        assert (w["d"], (TODAY - dt.date.fromisoformat(w["at"])).days) == ждём
        assert w["now"] == 12 and w["src"] == "kv"


def test_неделя_без_сегодняшней_точки_и_с_нет_данных():
    h = _hist(_pt(TODAY - dt.timedelta(days=7), overOrders=3), _pt(TODAY - dt.timedelta(days=1), overOrders=4))
    assert rzhist.delta(h, None, "main", "overOrders", TODAY) is None, "сегодня не собрано — дельты нет"
    h = _hist(_pt(TODAY - dt.timedelta(days=7), overOrders=None), _pt(TODAY, overOrders=4))
    assert rzhist.delta(h, None, "main", "overOrders", TODAY) is None, "«Нет данных» — не 0"


def test_ряд_12_недель_и_восстановление_срыва():
    fr = [(TODAY - dt.timedelta(days=3 + 7 * i)).isoformat() for i in range(11, -1, -1)]   # пятницы
    ct = {"ork": {"week": {"tiles": {"late": {"hist": list(range(12)), "fridays": fr}}}}}
    h = _hist(_pt(TODAY - dt.timedelta(days=7), lateOrders=50), _pt(TODAY, lateOrders=60))
    s = rzhist.series(h, ct, "main", "lateOrders", TODAY)
    assert len(s) == 12 and s[-1] == {"at": TODAY.isoformat(), "v": 60, "src": "kv"}
    assert s[-2]["src"] == "kv" and s[-2]["v"] == 50, "своя точка первее восстановления"
    assert s[-3]["src"] == "d12" and s[-3]["at"] == fr[-3] and s[-3]["v"] == 9, "неделя без истории — пятница Д-12"
    assert all(x["at"] != fr[-1] for x in s), "пятница моложе недели в «неделю назад» не идёт"
    w = rzhist.delta(h, ct, "main", "lateOrders", TODAY)
    assert w["d"] == 10 and w["src"] == "kv"
    # своей точки неделю назад нет — дельта по Д-12, с пометкой источника
    h = _hist(_pt(TODAY, lateOrders=60))
    w = rzhist.delta(h, ct, "main", "lateOrders", TODAY)
    assert w == {"d": 60 - 10, "now": 60, "was": 10, "at": fr[-2], "src": "d12"}
    # у прочих счётов Д-12 нет
    assert rzhist.delta(_hist(_pt(TODAY, overOrders=1)), ct, "main", "overOrders", TODAY) is None


def test_страница_ряд_только_от_двух_точек():
    h = _hist(_pt(TODAY, overOrders=3))
    pg = rzhist.page(h, None, TODAY)
    assert pg["since"] == TODAY.isoformat() and pg["n"] == 1 and pg["wk"]["main"] == {} and pg["spark"]["main"] == {}
    h = _hist(_pt(TODAY - dt.timedelta(days=200)), _pt(TODAY - dt.timedelta(days=7), overOrders=5), _pt(TODAY, overOrders=3))
    pg = rzhist.page(h, None, TODAY)
    assert pg["wk"]["main"]["overOrders"]["d"] == -2
    assert pg["spark"]["main"]["overOrders"][-2:] == [5, 3] and pg["spark"]["main"]["overOrders"][0] is None
    assert [p["d"] for p in pg["pts"]] == [(TODAY - dt.timedelta(days=7)).isoformat(), TODAY.isoformat()], \
        "в страницу — точки последних 12 недель"
    assert pg["since"] == (TODAY - dt.timedelta(days=200)).isoformat() and pg["n"] == 3
    assert "sep" not in pg["wk"]


# ── счёты из сборки: те же числа, что «Неделя» (два способа) ────────────────────────────────────
@pytest.fixture(scope="module")
def built():
    return fixture.build_realization(hist=False)


def test_счёты_сборки_те_же_что_на_вкладке(built):
    ct, dc = built["contracts"], built["dealctx"]
    pt = rzhist.point(ct, dc, TODAY)
    for d, v in (("main", ct), ("sep", ct["sep"])):
        m, W = pt[d], v["ork"]["week"]
        T = W["tiles"]
        assert m["lateOrders"] == T["W3"]["byOrders"]["late"] and m["lateDeals"] == T["W3"]["byDeals"]["late"]
        assert m["threatOrders"] == T["W3"]["byOrders"]["risk"] + T["W3"]["byOrders"]["forecast"]
        assert m["overOrders"] == T["W2"]["value"] and m["acceptDeals"] == T["W1"]["value"]
        assert m["unpaidDeals"] == T["unpaid"]["deals"]
        # второй способ: ключи списков за числами (drill) — столько же разных объектов
        D = W["drill"]
        assert len(set(D["W3p"]["late"]["k"])) == m["lateOrders"]
        assert m["noMgrOrders"] == sum(r["n"] for r in W["pulse"]["rows"] if r["kind"] == "none")
        # «Сделок без хозяина»: счёт = строки = ключи «d:» списка за числом (сделки, не заказы)
        assert m["noOwnerDeals"] == W["noOwner"]["n"] == len(W["noOwner"]["rows"]) == len(set(D["noOwner"]["k"]))
        assert m["closeDeals"] == len(set(D["step"]["13"]["now"]["k"]))
        # части очереди: без закрытой части — счёт life.bucketCounts; с ней — разные объекты пунктов
        bc = v["life"]["bucketCounts"]
        assert (m["execOrders"], m["dealDeals"], m["hygOrders"], m["hygDeals"]) == \
            (bc["exec"]["orders"], bc["deal"]["deals"], bc["hyg"]["orders"], bc["hyg"]["deals"])
        q = v["life"]["queue"] + [x for x in dc["queue"] if bool(x.get("seg")) == (d == "sep")]
        ub = [(x, *(lifecycle.unit_bucket(x["code"], order=x.get("order")) if not x.get("bucket") else (x["unit"], x["bucket"])))
              for x in q]
        assert m["execOrdersFull"] == len({str(x["order"]) for x, u, b in ub if b == "exec" and u == "order"})
        assert m["dealDealsFull"] == len({str(x["deal"]) for x, u, b in ub if b == "deal" and u == "deal"})
        assert m["hygOrdersFull"] == len({str(x["order"]) for x, u, b in ub if b == "hyg" and u == "order"})
        assert m["hygDealsFull"] == len({str(x["deal"]) for x, u, b in ub if b == "hyg" and u == "deal"})
    assert pt["main"]["dealDealsFull"] > pt["main"]["dealDeals"], "в корпусе есть пункты сделок закрытой части"
    # единицы: каждое имя — заказ или сделка, по суффиксу
    assert all(u == ("order" if "Orders" in f else "deal") for f, u in rzhist.UNITS.items())


def test_нет_данных_не_ноль(built):
    import copy
    ct = copy.deepcopy(built["contracts"])
    ct["ork"]["week"]["tiles"]["W2"]["state"] = "nodata"
    ct["ork"]["week"]["tiles"]["W3"]["state"] = "hidden"
    m = rzhist.point(ct, built["dealctx"], TODAY)["main"]
    assert m["overOrders"] is None and m["lateOrders"] is None and m["threatOrders"] is None
    assert rzhist.point({"rows": []}, None, TODAY) is None, "без «Реализации» точки нет"


# ── ключ KV: настоящий клиент на подменённом HTTP ──────────────────────────────────────────────
NS = "b" * 32


class Ответ:
    def __init__(self, raw: bytes):
        self.raw, self.status, self.headers = raw, 200, {"Content-Length": str(len(raw))}

    def read(self, n=-1):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class HTTP:
    """Cloudflare API без сети: привязка Pages → пространство KV, значения ключей."""

    def __init__(self, values=None):
        self.values, self.calls = dict(values or {}), []

    def open(self, req, timeout=None):
        u = parse.urlsplit(req.full_url)
        self.calls.append((req.get_method(), u.path))
        if "/pages/projects/" in u.path:
            body = {"success": True, "result": {"deployment_configs": {"production": {
                "kv_namespaces": {"ACL": {"namespace_id": NS}}}}}}
            return Ответ(json.dumps(body).encode())
        key = parse.unquote(u.path.split("/values/", 1)[1])
        if req.get_method() == "GET":
            if key not in self.values:
                raise error.HTTPError(req.full_url, 404, "нет", {}, io.BytesIO(b""))
            return Ответ(self.values[key])
        self.values[key] = req.data
        return Ответ(json.dumps({"success": True, "result": None}).encode())


def _store(http: HTTP):
    """Как realization_ctx_kv.kv_store, но с подменённым HTTP (opener)."""
    import realization_ctx_kv as rk
    spec = importlib.util.spec_from_file_location("ps_rzhist_test", ROOT / "scripts" / "publish_suppliers.py")
    ps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ps)

    class CF(ps.Cloudflare):
        КЛЮЧИ = (rzhist.KV_KEY,)

    cf = CF("a" * 32, "synthetic-token", opener=http, sleep=lambda s: None)
    return rk.KVStore(cf, cf.namespace(), (0,), rzhist.KV_KEY)


def test_ключ_публикация_слияние_и_чтение(built, tmp_path):
    http = HTTP()
    журнал: list[str] = []
    точка = tmp_path / "rzhist_today.json"
    # файла точки нет — ключ не тронут, клиент не создаётся
    вызван = []
    assert kv.publish(точка, lambda: вызван.append(1), печать=журнал.append) == 0 and not вызван
    pt = rzhist.point(built["contracts"], built["dealctx"], TODAY)
    rzhist.write(точка, pt)
    assert kv.publish(точка, lambda: _store(http), печать=журнал.append) == 0
    h = json.loads(http.values[rzhist.KV_KEY])
    assert [p["d"] for p in h["pts"]] == [TODAY.isoformat()] and h["pts"][0]["main"] == pt["main"]
    # следующий день и повтор того же дня: одна точка на день, перезапись
    pt2 = {**pt, "d": (TODAY + dt.timedelta(days=1)).isoformat(), "main": {**pt["main"], "lateOrders": 1}}
    rzhist.write(точка, pt2)
    kv.publish(точка, lambda: _store(http), печать=журнал.append)
    pt2["main"]["lateOrders"] = 2
    rzhist.write(точка, pt2)
    kv.publish(точка, lambda: _store(http), печать=журнал.append)
    h = json.loads(http.values[rzhist.KV_KEY])
    assert [p["main"]["lateOrders"] for p in h["pts"]] == [pt["main"]["lateOrders"], 2]
    # писали только свой ключ, со сверкой чтением
    assert {p.split("/values/")[1] for m, p in http.calls if "/values/" in p} == {parse.quote(rzhist.KV_KEY, safe="")}
    assert [m for m, p in http.calls if "/values/" in p][-2:] == ["PUT", "GET"]
    # чтение в файл сборки
    файл = tmp_path / "in" / "rzhist.json"
    assert kv.fetch(файл, lambda: _store(http), печать=журнал.append) == 0
    assert rzhist.load(файл)["pts"] == h["pts"]
    # ключа нет — файла нет, выход 0
    assert kv.fetch(tmp_path / "нет.json", lambda: _store(HTTP()), печать=журнал.append) == 0
    assert not (tmp_path / "нет.json").exists()
    # в журнале — только счёты и даты: ни номеров сделок, ни названий
    текст = "\n".join(журнал)
    имена = {str(r.get(k)) for r in built["contracts"]["rows"] for k in ("customer", "kam", "title") if r.get(k)}
    assert not any(n in текст for n in имена if len(n) > 3)
    assert not any(str(r["deal"]) in текст.split() for r in built["contracts"]["rows"])


def test_чужой_ключ_не_перезаписывается(built, tmp_path, capsys):
    точка = tmp_path / "t.json"
    rzhist.write(точка, rzhist.point(built["contracts"], built["dealctx"], TODAY))
    for старое in (b"{not json", json.dumps({"v": 2, "pts": []}).encode()):
        http = HTTP({rzhist.KV_KEY: старое})
        assert kv.main(["--publish", str(точка)], store=lambda: _store(http)) == 1
        assert http.values[rzhist.KV_KEY] == старое
    журнал = capsys.readouterr().out
    assert "::warning::" in журнал and "::error::" not in журнал and "HIST_BAD" in журнал
    # точка не по форме — ключ не тронут
    точка.write_text(json.dumps({"d": "вчера", "main": {}}), encoding="utf-8")
    http = HTTP()
    assert kv.main(["--publish", str(точка)], store=lambda: _store(http)) == 1 and not http.values
    # сбой сети — классом, без трассы и адреса
    class Обрыв(HTTP):
        def open(self, req, timeout=None):
            raise ConnectionResetError("https://example.invalid/секрет")
    assert kv.main(["--fetch", str(tmp_path / "x.json")], store=lambda: _store(Обрыв())) == 1
    журнал = capsys.readouterr().out
    assert "::warning::" in журнал and "example.invalid" not in журнал and "секрет" not in журнал


# ── сборка и прогон ─────────────────────────────────────────────────────────────────────────────
def test_сборка_кладёт_историю_и_пишет_точку_только_при_переменной(built, tmp_path, monkeypatch, capsys):
    import copy

    import main
    ct = copy.deepcopy(built["contracts"])
    monkeypatch.setenv("KVANT_NOW", "2026-10-19T12:00:00+03:00")
    monkeypatch.delenv("RZHIST_OUT", raising=False)
    monkeypatch.delenv("RZHIST_PATH", raising=False)
    main._rz_hist(ct, built["dealctx"])
    assert ct["rzHist"]["n"] == 1 and ct["rzHist"]["since"] == "2026-10-19"
    assert "lateOrders" in ct["rzHist"]["wk"]["main"], "«срок сорван» — по Д-12 и без истории"
    журнал = capsys.readouterr().out
    assert "История счётов" in журнал
    assert not any(n in журнал for r in ct["rows"] for n in (r.get("customer"), r.get("kam")) if n and len(n) > 3)
    # с историей из файла и с файлом точки
    hist = fixture.rz_hist_points(rzhist.point(ct, built["dealctx"], TODAY))
    rzhist.write(tmp_path / "h.json", hist)
    monkeypatch.setenv("RZHIST_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("RZHIST_OUT", str(tmp_path / "o" / "p.json"))
    main._rz_hist(ct, built["dealctx"])
    assert ct["rzHist"]["n"] == fixture.RZ_HIST_DAYS + 1
    assert ct["rzHist"]["wk"]["main"]["overOrders"]["d"] == -fixture.RZ_HIST_SHIFT["overOrders"]
    p = json.loads((tmp_path / "o" / "p.json").read_text(encoding="utf-8"))
    assert p["d"] == "2026-10-19" and rzhist.clean_point(p) == p
    # без «Реализации» — ничего; сбой — классом ошибки
    main._rz_hist(None, None)
    monkeypatch.setattr(rzhist, "page", lambda *a, **k: 1 / 0)
    main._rz_hist(copy.deepcopy(built["contracts"]), built["dealctx"])
    assert "ZeroDivisionError" in capsys.readouterr().out


def _wf(имя: str) -> dict:
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / имя).read_text(encoding="utf-8"))
    wf["on"] = wf.get("on", wf.get(True))
    return wf


def test_деплой_читает_до_сборки_и_пишет_после_выкладки():
    wf = _wf("deploy.yml")
    шаги = wf["jobs"]["build-and-deploy"]["steps"]
    имена = [s.get("name") for s in шаги]
    ген = next(i for i, s in enumerate(шаги) if s.get("name", "").startswith("Генерация"))
    чт = next(i for i, s in enumerate(шаги) if "realization_hist_kv.py --fetch" in (s.get("run") or ""))
    пуб = next(i for i, s in enumerate(шаги) if "realization_hist_kv.py --publish" in (s.get("run") or ""))
    assert чт < ген < имена.index("Деплой на Cloudflare Pages") < пуб
    env = шаги[ген]["env"]
    assert env["RZHIST_PATH"] == "${{ runner.temp }}/rzhist.json" and "$RUNNER_TEMP/rzhist.json" in шаги[чт]["run"]
    assert env["RZHIST_OUT"] == "${{ runner.temp }}/rzhist_today.json" and "$RUNNER_TEMP/rzhist_today.json" in шаги[пуб]["run"]
    for i in (чт, пуб):
        assert "|| echo \"::warning::" in шаги[i]["run"], "сбой ключа деплой не роняет"
        assert set(шаги[i]["env"]) == {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}
    assert "rzhist.py" in wf["on"]["push"]["paths"]
    сверка = (ROOT / ".github" / "workflows" / "live-check.yml").read_text(encoding="utf-8")
    assert "realization_hist_kv" not in сверка and "RZHIST" not in сверка, "у живой сверки — без ключа, одна точка"
    assert "rzhist.py" in сверка, "правка расчёта истории проходит живую сверку"
