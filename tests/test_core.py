"""Быстрые тесты ядра дашборда: без сети, без Bitrix, без ключей.

Покрывают то, что ломалось на практике: раскладка стадий, разбор периода,
форма метрик, ретраи клиента Bitrix и гейт достаточности данных.
"""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# config.py тянет python-dotenv; в тестах он не нужен
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import period as period_mod       # noqa: E402
import stages as stages_mod       # noqa: E402
from tests import fixture         # noqa: E402


# ------------------------------------------------------------------ стадии
def test_каждая_стадия_попадает_в_известный_бакет():
    for stage_id in stages_mod._SPA_STAGE_BUCKET:
        assert stages_mod.classify_stage(stage_id) in stages_mod.BUCKETS


def test_неизвестная_стадия_не_считается_закрытой_молча():
    bucket = stages_mod.classify_stage("DT166_24:НЕТ_ТАКОЙ_СТАДИИ")
    assert bucket in stages_mod.BUCKETS
    # неизвестное не должно попадать в «отказ» или «молчание» — это искажает конверсию
    assert bucket not in ("refused", "no_answer")


def test_открытые_и_закрытые_бакеты_не_пересекаются():
    assert not (stages_mod.OPEN & stages_mod.CLOSED)
    assert stages_mod.OPEN | stages_mod.CLOSED == set(stages_mod.BUCKETS)


def test_классификатор_ткп_различает_выигрыш_и_отказ():
    assert stages_mod.deal_reached_tkp("C24:WON", "S", "Сделка успешна") is True
    assert stages_mod.deal_reached_tkp("C24:LOSE", "F", "Проигрыш") is False


# ------------------------------------------------------------------ период
def test_период_месяца_разбирается_и_бьётся_на_недели():
    p = period_mod.parse_period("2026-05", as_of=dt.date(2026, 5, 31))
    assert p.start == dt.date(2026, 5, 1) and p.end == dt.date(2026, 5, 31)
    assert len(p.weeks) >= 4
    assert p.start_iso.endswith("T00:00:00") and p.end_iso.endswith("T23:59:59")


def test_период_диапазона_разбирается():
    p = period_mod.parse_period("2026-04-01:2026-05-15", as_of=dt.date(2026, 5, 15))
    assert (p.start, p.end) == (dt.date(2026, 4, 1), dt.date(2026, 5, 15))


def test_недели_идут_подряд_без_дыр():
    p = period_mod.parse_period("2026-05", as_of=dt.date(2026, 5, 31))
    for a, b in zip(p.weeks, p.weeks[1:]):
        assert (b.start - a.end).days == 1


# ------------------------------------------------------------------ метрики
@pytest.fixture(scope="module")
def metrics():
    return fixture.build_metrics()


def test_метрики_содержат_все_блоки_нужные_шаблону(metrics):
    # эти ключи читает templates/dashboard_core.html на верхнем уровне
    for key in ("kpi", "sourcersA", "weekly", "coverage", "chain", "catList", "period"):
        assert key in metrics, f"в метриках нет блока {key}"


def test_сумма_по_блокам_сходится_с_общим_числом(metrics):
    k = metrics["kpi"]
    assert k["total"] == k["deptA"] + k["outside"]


def test_доли_в_разумных_границах(metrics):
    k, cov = metrics["kpi"], metrics["coverage"]
    for name, val in (("inWorkPct", k["inWorkPct"]), ("covPct", cov["covPct"])):
        assert 0 <= val <= 100, f"{name} = {val} вне диапазона 0..100"


def test_нагрузка_сорсеров_отсортирована_по_убыванию(metrics):
    counts = [r["c"] for r in metrics["sourcersA"]]
    assert counts == sorted(counts, reverse=True)


def test_пустой_период_не_роняет_расчёт():
    import metrics as metrics_mod
    p = fixture.make_period()
    m = metrics_mod.build(p, [], {}, [], set(), {}, {}, {}, {})
    assert m["kpi"]["total"] == 0 and m["sourcersA"] == []


# ------------------------------------------------------------------ клиент Bitrix
class _Resp:
    def __init__(self, code, body, is_json=True):
        self.status_code, self._b, self._j, self.text = code, body, is_json, str(body)

    def json(self):
        if not self._j:
            raise ValueError("не JSON")
        return self._b


class _Session:
    def __init__(self, seq):
        self.seq, self.calls = list(seq), 0

    def post(self, *a, **k):
        self.calls += 1
        x = self.seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


def _client(seq, **kw):
    import bitrix_client as bc
    c = bc.BitrixClient("https://x.bitrix24.ru/rest/1/t/", min_interval=0, **kw)
    c.backoff_base, c.backoff_max = 0.001, 0.002
    c._session = _Session(seq)
    return c


def test_транзиентные_сбои_ретраятся_и_запрос_доходит():
    c = _client([_Resp(500, "<html>", False),
                 _Resp(200, "<html>", False),
                 _Resp(200, {"error": "INTERNAL_SERVER_ERROR", "error_description": ""}),
                 _Resp(200, {"result": [1, 2, 3]})])
    assert c.call("crm.deal.list") == [1, 2, 3]
    assert c._session.calls == 4 and c.retry_count == 3


def test_ошибка_прав_не_ретраится():
    import bitrix_client as bc
    c = _client([_Resp(200, {"error": "ACCESS_DENIED", "error_description": "нет прав"})])
    with pytest.raises(bc.BitrixError):
        c.call("crm.deal.list")
    assert c._session.calls == 1


def test_исчерпание_попыток_даёт_понятную_ошибку():
    import bitrix_client as bc
    import requests
    c = _client([requests.RequestException("сеть")] * 3, retries=3)
    with pytest.raises(bc.BitrixError, match="не удалось выполнить"):
        c.call("m")
    assert c._session.calls == 3


def test_envelope_сохраняет_поле_next_для_пагинации():
    c = _client([_Resp(200, {"result": {"items": [{"OWNER_ID": 7}]}, "next": 50})])
    assert c.call_envelope("crm.stagehistory.list")["next"] == 50


# ------------------------------------------------------------------ гейт данных
def test_гейт_останавливает_прогон_на_пустой_выгрузке():
    import main as main_mod
    p = types.SimpleNamespace(days=120, label="тест")
    with pytest.raises(main_mod.DataGateError):
        main_mod._sanity_gates(p, [], [], set(), [])


def test_гейт_пропускает_нормальную_выгрузку():
    import main as main_mod
    p = types.SimpleNamespace(days=120, label="тест")
    main_mod._sanity_gates(p, [1] * 300, [1] * 200, {"76"}, [1, 2, 3, 4])


def test_гейт_масштабирует_пороги_под_короткий_период():
    import main as main_mod
    p = types.SimpleNamespace(days=3, label="короткий")
    main_mod._sanity_gates(p, [1, 2], [1, 2], {"76"}, [1])


# ------------------------------------------------------------------ рендер
def test_шаблон_рендерится_и_проходит_валидатор(tmp_path, metrics):
    import dashboard
    out = tmp_path / "index.html"
    dashboard.write(metrics, {"source": "rules", "items": []}, out)
    html = out.read_text(encoding="utf-8")
    assert "__DATA_JSON__" not in html and "__TITLE__" not in html
    sys.argv = ["v", str(out), "--no-browser"]
    import importlib.util
    spec = importlib.util.spec_from_file_location("v", ROOT / "scripts" / "validate_dashboard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main() == 0


def test_неполные_данные_не_гасят_страницу(tmp_path, metrics):
    """Раньше отсутствие weekly роняло весь скрипт и все 9 вкладок."""
    import copy
    import dashboard
    bad = copy.deepcopy(metrics)
    bad.pop("weekly"), bad.pop("chain")
    bad["coverage"] = {}
    out = tmp_path / "degraded.html"
    dashboard.write(bad, {"source": "rules", "items": []}, out)
    html = out.read_text(encoding="utf-8")
    assert "window.__RENDER_OK__=1" in html          # базовый слой на месте
    assert "if(M[_k]==null) M[_k]=_d" in html        # нормализация данных на месте
