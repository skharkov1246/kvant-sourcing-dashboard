"""Битрикс не перегружать: общий бюджет, терпеливые повторы, ежедневный проход.

24.09.2026 холостой переразбор сделок упал в 44 частях из 50 с HTTP 429 на
crm.deal.list. По документации Битрикс24 (apidocs.bitrix24.ru/limits.html) 429 —
это OPERATION_TIME_LIMIT: время метода за 10 минут кончилось, и метод
заблокирован до выпадения старой минуты; частота — отдельный лимит, 503
QUERY_LIMIT_EXCEEDED. Клиент ждал не дольше минуты и падал раньше, чем метод
освобождался.

Портал здесь подставной, сна нет (правило 18): паузы записываются, а не ждутся.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "library"))

import bitrix_client as bc  # noqa: E402


class Ответ:
    def __init__(self, код: int, тело, заголовки: dict | None = None):
        self.status_code, self._тело, self.headers = код, тело, заголовки or {}

    def json(self):
        if isinstance(self._тело, (dict, list)):
            return self._тело
        raise ValueError("не JSON")

    @property
    def text(self) -> str:
        return str(self._тело)


class Сессия:
    def __init__(self, ответы):
        self.ответы, self.вызовов = list(ответы), 0

    def post(self, *_a, **_k):
        self.вызовов += 1
        return self.ответы.pop(0)


@pytest.fixture
def паузы(monkeypatch):
    """Паузы клиента: записываются, не ждутся."""
    сон: list[float] = []
    monkeypatch.setattr(bc.time, "sleep", lambda с: сон.append(с))
    monkeypatch.setattr(bc.random, "random", lambda: 0.5)      # без джиттера
    return сон


def клиент(ответы, **kw):
    c = bc.BitrixClient("https://пример.bitrix24.ru/rest/1/выдумка/", min_interval=0, **kw)
    c._session = Сессия(ответы)
    return c


# ─────────────────────────────────────────────────────────── бюджет частоты
def test_интервал_из_бюджета_портала(monkeypatch):
    """Пауза процесса = PARALLEL / RPS: части вместе держат бюджет портала."""
    monkeypatch.setenv("BITRIX_RPS", "1.5")
    monkeypatch.setenv("BITRIX_PARALLEL", "12")
    assert bc.интервал_портала() == pytest.approx(8.0)
    assert bc.BitrixClient("https://x/").min_interval == pytest.approx(8.0)
    monkeypatch.setenv("BITRIX_PARALLEL", "1")
    assert bc.интервал_портала() == pytest.approx(1 / 1.5)


def test_умолчание_ниже_утечки_ведра(monkeypatch):
    monkeypatch.delenv("BITRIX_RPS", raising=False)
    monkeypatch.delenv("BITRIX_PARALLEL", raising=False)
    rps, par = bc.бюджет_портала()
    assert par == 1 and rps < bc.ПОРТАЛ_УТЕЧКА_В_С


def test_бюджет_выше_лимита_урезается_вслух(monkeypatch, capsys):
    """Шаг прогона не подменяет вход молча — урезание видно в журнале."""
    monkeypatch.setenv("BITRIX_RPS", "5")
    rps, _ = bc.бюджет_портала()
    assert rps == bc.ПОРТАЛ_УТЕЧКА_В_С
    assert "урезано" in capsys.readouterr().err


def test_двенадцать_частей_по_старому_числу_превышали_портал():
    """Прежние 0,5 с на процесс: 12 частей — 24 запроса в секунду при утечке 2."""
    assert 12 / 0.5 > bc.ПОРТАЛ_УТЕЧКА_В_С
    assert 12 / bc.интервал_портала(1.5, 12) <= bc.ПОРТАЛ_УТЕЧКА_В_С


# ─────────────────────────────────────────────────────── терпеливые повторы
def test_частота_пережидается_с_растущей_паузой(паузы):
    """Восемь отказов 503 подряд — не повод падать: прежде хватало шести."""
    отказ = Ответ(503, {"error": "QUERY_LIMIT_EXCEEDED", "error_description": "Too many requests"})
    c = клиент([отказ] * 8 + [Ответ(200, {"result": [1]})])
    assert c.call("crm.deal.list") == [1]
    assert паузы == sorted(паузы), "пауза на лимите не растёт"
    assert паузы[0] == pytest.approx(bc.ЛИМИТ_ПАУЗА_С)
    assert max(паузы) <= bc.ЛИМИТ_ПАУЗА_МАКС_С
    assert sum(паузы) > 60, "ожидание не длиннее прежней минуты — блокировка метода его переживёт"


def test_блокировка_по_времени_метода_ждёт_до_сброса(паузы):
    """429 OPERATION_TIME_LIMIT несёт operating_reset_at — ждём до него."""
    сброс = time.time() + 300
    отказ = Ответ(429, {"error": "OPERATION_TIME_LIMIT",
                        "time": {"operating": 421.0, "operating_reset_at": сброс}})
    c = клиент([отказ, Ответ(200, {"result": 7})])
    assert c.call("crm.deal.list") == 7
    assert паузы[0] == pytest.approx(301, abs=2)


def test_retry_after_учитывается(паузы):
    c = клиент([Ответ(503, "<html>", {"Retry-After": "40"}), Ответ(200, {"result": 1})])
    assert c.call("m") == 1
    assert паузы[0] == pytest.approx(40)


def test_бюджет_ожидания_кончается_громко(паузы):
    """Портал держит лимит вечно — прогон падает с названной причиной, а не висит."""
    отказ = Ответ(429, {"error": "OPERATION_TIME_LIMIT"})
    c = клиент([отказ] * 100)
    c.limit_wait_budget = 100
    with pytest.raises(bc.BitrixLimitError, match="бюджета ожидания"):
        c.call("crm.deal.list")
    assert sum(паузы) <= 100


def test_прочие_сбои_по_прежнему_считаются_попытками(паузы):
    """Лимит меряется временем, а сеть и 500 — числом попыток, как раньше."""
    c = клиент([Ответ(500, "<html>")] * 3, retries=3)
    with pytest.raises(bc.BitrixError, match="за 3 попыток"):
        c.call("m")
    assert c._session.вызовов == 3


def test_тормоз_по_времени_метода_до_отказа(паузы):
    """time.operating близко к пределу — пауза сама, не дожидаясь 429.

    Время метода общее у всех наших процессов на вебхуке: каждый видит его в
    своём ответе и притормаживает без согласования с остальными."""
    ответ = Ответ(200, {"result": [], "time": {"operating": 380.0,
                                                "operating_reset_at": time.time() + 200}})
    c = клиент([ответ])
    c.call("crm.deal.list")
    assert паузы and паузы[0] == pytest.approx(201, abs=2)


def test_без_нагрузки_тормоза_нет(паузы):
    c = клиент([Ответ(200, {"result": [], "time": {"operating": 3.0,
                                                    "operating_reset_at": time.time() + 500}})])
    c.call("m")
    assert паузы == []


def test_отказ_замедляет_процесс_и_скорость_возвращается(паузы):
    c = клиент([Ответ(503, {"error": "QUERY_LIMIT_EXCEEDED"})] + [Ответ(200, {"result": 1})] * 40)
    c.call("m")
    assert c._замедление > 1
    for _ in range(39):
        c.call("m")
    assert c._замедление < 1.2


def test_сводка_нагрузки_только_числа():
    строка = bc.сводка_нагрузки()
    assert "запросов" in строка and "429" in строка and "503" in строка
    assert "/rest/" not in строка


# ─────────────────────────────────────────────────────── ежедневный проход
def test_окно_по_отметке_с_перекрытием():
    import increment
    сейчас = datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)
    начало = datetime(2026, 9, 24, 2, 40, tzinfo=timezone.utc)
    с, после = increment.окно({"начало": начало.timestamp(), "после_id": 900}, сейчас)
    assert с == начало - timedelta(hours=increment.ЗАПАС_Ч) and после == 900


def test_без_отметки_окно_первого_прохода_и_без_номера():
    """Номер 0 значил бы «все записи» — сплошной обход, от которого уходим."""
    import increment
    сейчас = datetime(2026, 9, 25, tzinfo=timezone.utc)
    с, после = increment.окно(None, сейчас)
    assert после == 0 and с == сейчас - timedelta(days=increment.ПЕРВЫЙ_ДНЕЙ)
    assert increment.окно({"мусор": 1}, сейчас) == (с, 0)


def test_объединение_без_повторов_по_номеру():
    import increment
    a = [{"id": 5}, {"id": 3}]
    b = [{"id": 3}, {"id": 9}]
    assert [x["id"] for x in increment.объединить(a, b)] == [3, 5, 9]


def test_фильтр_даты_в_часовом_поясе_портала():
    import increment
    момент = datetime(2026, 9, 24, 21, 30, tzinfo=timezone.utc)
    assert increment.для_фильтра(момент) == "2026-09-25T00:30:00+03:00"


def _подставной_портал(monkeypatch, ix, карточки):
    вызовы: list[dict] = []

    def bx(method, params):
        f = dict(params.get("filter") or {})
        вызовы.append({"method": method, "filter": f})
        if method == "crm.item.list":
            after = int(f.get(">id", 0))
            if ">=updatedTime" in f:
                items = [x for x in карточки if x["updatedTime"] >= "2026-09-24" and x["id"] > after]
            else:
                items = [x for x in карточки if x["id"] > after]
            return {"result": {"items": items[:50]}}
        return {"result": {}}

    monkeypatch.setattr(ix, "bx", bx)
    return вызовы


def test_ежедневный_проход_карточек_читает_только_новое(monkeypatch):
    """Из тысячи карточек проход берёт изменённые и новые — две страницы, а не двадцать."""
    import increment

    import indexer as ix
    поле = next(iter(ix.ПОЛЯ_КП))
    карточки = [{"id": i, "updatedTime": "2026-01-01", "createdTime": "2026-01-01"} for i in range(1, 1001)]
    карточки[99]["updatedTime"] = "2026-09-24T10:00"        # КП пришло в старую карточку
    карточки[99][поле] = [{"id": "f100", "urlMachine": "https://пример/f100"}]
    карточки.append({"id": 1001, "updatedTime": "2026-09-24T11:00", "createdTime": "2026-09-24",
                     поле: [{"id": "f1001", "urlMachine": "https://пример/f1001"}]})
    вызовы = _подставной_портал(monkeypatch, ix, карточки)
    monkeypatch.setattr(ix, "наша_компания", lambda _v: None)
    monkeypatch.setattr(increment, "окно_из_базы",
                        lambda source: (datetime(2026, 9, 24, tzinfo=timezone.utc), 1000))
    monkeypatch.setattr(ix, "ИНКРЕМЕНТ", True)
    refs = ix.collect_refs_rfq(7300)
    assert sorted(r["fo"]["id"] for r in refs) == ["f100", "f1001"]
    assert len(вызовы) <= 4, f"проход сделал {len(вызовы)} запросов — это уже обход"
    фильтры = [в["filter"] for в in вызовы]
    assert any(">=updatedTime" in f for f in фильтры)
    assert any(f.get(">id") == 1000 for f in фильтры), "страховки номером нет"


def test_ежедневный_проход_идёт_одной_частью(monkeypatch):
    import indexer as ix
    monkeypatch.setattr(ix, "ИНКРЕМЕНТ", True)
    monkeypatch.setattr(ix, "bx", lambda *_a: pytest.fail("вторая часть пошла в портал"))
    assert ix.collect_refs_rfq(30, 3, 12) == [] and ix.collect_refs(30, 3, 12) == []


def test_ежедневный_проход_сделок(monkeypatch):
    import increment

    import indexer as ix
    вызовы: list[tuple[str, dict]] = []

    def bx(method, params):
        f = dict(params.get("filter") or {})
        вызовы.append((method, f))
        if method == "crm.deal.list":
            if ">=DATE_MODIFY" in f:
                return {"result": [{"ID": "12"}, {"ID": "40"}] if f.get(">ID", 0) < 12 else []}
            return {"result": [{"ID": "41"}] if f.get(">ID", 0) < 41 else []}
        if method == "crm.item.fields":
            return {"result": {"fields": {"ufF": {"type": "file", "title": "Спецификация"}}}}
        if method == "crm.item.list":
            return {"result": {"items": [{"id": i, "ufF": [{"id": f"d{i}", "urlMachine": "u"}]}
                                         for i in f["@id"]]}}
        return {"result": {}}

    monkeypatch.setattr(ix, "bx", bx)
    monkeypatch.setattr(ix, "наша_компания", lambda _v: None)
    monkeypatch.setattr(increment, "окно_из_базы",
                        lambda source: (datetime(2026, 9, 24, tzinfo=timezone.utc), 40))
    monkeypatch.setattr(ix, "ИНКРЕМЕНТ", True)
    refs = ix.collect_refs(3650)
    assert sorted(r["deal"] for r in refs) == ["12", "40", "41"]
    assert all("<=ID" not in f for _m, f in вызовы), "ежедневный проход ушёл в диапазоны частей"


# ─────────────────────────────────────────────────────── прогоны
def test_параллельность_прогона_совпадает_с_матрицей():
    """BITRIX_PARALLEL считается из того же числа, что strategy.max-parallel."""
    import re
    текст = (ROOT / ".github/workflows/library-index.yml").read_text(encoding="utf-8")
    матрица = int(re.search(r"max-parallel:\s*(\d+)", текст).group(1))
    в_шаге = int(re.search(r"MAX_PARALLEL=(\d+)", текст).group(1))
    assert матрица == в_шаге
    assert "BITRIX_PARALLEL" in текст and "BITRIX_RPS" in текст


def test_ежедневный_прогон_ручной_и_отметка_последней():
    import yaml
    wf = yaml.safe_load((ROOT / ".github/workflows/library-daily.yml").read_text(encoding="utf-8"))
    triggers = wf.get("on") or wf.get(True)
    assert "schedule" not in triggers, "расписание — решение владельца"
    шаги = wf["jobs"]["daily"]["steps"]
    assert "increment.py отметка" in шаги[-1]["run"]
    assert "if" not in шаги[-1], "отметка упавшего прохода сдвинула бы окно"
    assert wf["jobs"]["daily"]["env"]["INCREMENT"] == "1"


def test_извлечение_дефектов_просит_свою_часть():
    код = (ROOT / "library/harvest_defects.py").read_text(encoding="utf-8")
    assert "collect_refs(DAYS, SHARD, SHARDS)" in код
    assert "% SHARDS == SHARD" not in код, "второе разбиение по хешу выбросит файлы"
