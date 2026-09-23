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


# ------------------------------------------------------------------ поисковый индекс
def _index():
    import json
    p = ROOT / "data" / "index.json"
    assert p.exists(), "нет data/index.json — соберите: python scripts/build_index.py"
    return json.loads(p.read_text(encoding="utf-8"))


def test_индекс_не_содержит_персональных_и_ценовых_полей():
    """Индекс лежит рядом с данными и не должен становиться отдельной выгрузкой."""
    idx = _index()
    banned = {"email", "phone", "inn", "price", "val", "custval", "margin", "revenue"}
    assert banned <= set(idx["excluded_fields"]), "список исключений сузился"
    # ни одно проиндексированное поле не должно быть из запрещённых
    assert not (set(idx["indexed_fields"]) & banned)


def test_в_индексе_нет_значений_похожих_на_адреса_и_телефоны():
    """Парт-номера бывают из 10–12 цифр, поэтому «похоже на телефон» проверяем
    только вне групп pn и hs и только при телефонном оформлении номера."""
    import re
    idx = _index()
    mail = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}")
    phone = re.compile(r"^(\+|8|7)[\d\s()-]{10,}$")
    bad = []
    for group, values in idx["index"].items():
        for key in values:
            if mail.search(key):
                bad.append(f"{group}: адрес {key}")
            elif group not in ("pn", "hs") and phone.match(key):
                bad.append(f"{group}: телефон {key}")
            if len(bad) > 5:
                break
    assert not bad, f"в индекс попали контакты: {bad[:5]}"


def test_каждая_ссылка_индекса_ведёт_в_существующий_набор():
    idx = _index()
    n = len(idx["files"])
    for group, values in idx["index"].items():
        for key, postings in list(values.items())[:200]:
            for fi, ri in postings:
                assert 0 <= fi < n, f"{group}:{key} ссылается на набор {fi}, а их {n}"
                assert ri >= 0


def test_поиск_по_индексу_находит_известный_парт_номер():
    """4380132 — свеча зажигания Cummins, встречается в базе PN и в ценах."""
    idx = _index()
    postings = idx["index"]["pn"].get("4380132")
    assert postings, "известный парт-номер пропал из индекса"
    paths = {idx["files"][fi]["path"] for fi, _ in postings}
    assert "gt/data/pn_db.json" in paths


def test_индекс_ссылается_только_на_источники_а_не_на_сборочные_копии():
    idx = _index()
    copies = [f["path"] for f in idx["files"] if "/public/" in f["path"]]
    assert not copies, f"в индексе сборочные копии: {copies[:3]}"


# ------------------------------------------------------------------ упаковка записей
def test_упаковка_повторяющихся_записей_сохраняет_содержимое():
    """Имена полей в JSON повторяются в каждой записи — на живых данных это сотни
    килобайт. Упаковка обязана быть обратимой без потерь."""
    import json

    import dashboard
    rows = [{"id": i, "subj": f"Запрос {i}", "st": "sent"} for i in range(25)]
    packed = dashboard._pack_records(rows)
    assert packed["_p"] == 1 and packed["f"] == ["id", "subj", "st"]
    back = [dict(zip(packed["f"], r)) for r in packed["r"]]
    assert back == rows
    assert len(json.dumps(packed, ensure_ascii=False)) < len(json.dumps(rows, ensure_ascii=False))


def test_короткий_список_и_разнородный_не_пакуются():
    import dashboard
    short = [{"a": 1}] * 5
    assert dashboard._pack_records(short) is short
    mixed = [{"a": 1}] * 30 + [7]
    assert dashboard._pack_records(mixed) is mixed


def test_упаковка_не_портит_исходные_метрики():
    """Тот же словарь метрик уходит в отчёт reports/ — мутировать его нельзя."""
    import dashboard
    src = {"sourcersA": [{"id": "1", "details": [{"a": i} for i in range(30)]}]}
    out = dashboard._pack_metrics(src)
    assert isinstance(src["sourcersA"][0]["details"], list)          # исходник цел
    assert out["sourcersA"][0]["details"]["_p"] == 1                 # копия упакована


# ── кто заводит запросы ──────────────────────────────────────────────────────
# Разбор нужен владельцу, чтобы видеть, кто грузит очередь запросов помимо
# отдела поиска поставщиков. Считается по автору карточки, а не по ответственному.

def _origin():
    from tests import fixture
    return fixture.build_metrics()["origin"]


def test_происхождение_запросов_разложено_без_потерь():
    o = _origin()
    s = o["summary"]
    assert s["sourcing"] + s["outside"] + s["auto"] == s["total"], (
        "сумма по источникам разошлась с общим числом запросов")
    assert sum(d["n"] for d in o["byDept"]) == s["total"], (
        "сумма по подразделениям разошлась с общим числом запросов")


def test_автор_вне_отдела_попадает_в_список_с_подразделением():
    o = _origin()
    out = o["outsideCreators"]
    assert out, "в синтетике есть авторы из смежных отделов, список не должен быть пуст"
    for c in out:
        assert not c["src"] and not c["auto"], "в список вне отдела попал сорсер или автоматика"
        assert c["dept"], f"у автора {c['name']} не указано подразделение"
        assert c["n"] > 0


def test_карточки_без_автора_считаются_автоматикой_а_не_человеком():
    o = _origin()
    auto = [c for c in o["byCreator"] if c["auto"]]
    assert auto, "карточки без автора должны выделяться отдельно"
    assert o["summary"]["auto"] == sum(c["n"] for c in auto)
    assert o["summary"]["people"] == len([c for c in o["byCreator"] if not c["auto"]])


def test_передача_сорсингу_считается_только_от_заведённых_вне_отдела():
    o = _origin()
    s = o["summary"]
    assert s["handoff"] <= s["outside"], (
        "передано сорсингу не может превышать число заведённых вне отдела")


def test_разбор_не_падает_без_карты_подразделений():
    """Карта подразделений необязательна: при её отсутствии разбор остаётся,
    а подразделение помечается как неуказанное."""
    import metrics as metrics_mod
    from tests import fixture
    d = fixture.make_dataset()
    m = metrics_mod.build(d["period"], d["rfqs"], d["deal_index"], d["period_deals"],
                          d["dept_a_ids"], d["names"], d["since"],
                          d["deal_stage_names"], d["category_names"])
    o = m["origin"]
    assert o["summary"]["total"] == m["kpi"]["total"]
    assert any(c["dept"] == "подразделение не указано" for c in o["byCreator"])


# ── служебные записи (воронка пресейла) ──────────────────────────────────────
# Карточки заводит робот от имени служебной учётной записи. Если считать
# исполнителем её, работа сорсера, запустившего кампанию, исчезает из его
# статистики. Исполнитель восстанавливается цепочкой: ответственный карточки →
# владелец родительской сделки → автор карточки.

def _build(service_ids):
    import metrics as metrics_mod
    from tests import fixture
    d = fixture.make_dataset()
    return metrics_mod.build(d["period"], d["rfqs"], d["deal_index"], d["period_deals"],
                             d["dept_a_ids"], d["names"], d["since"],
                             d["deal_stage_names"], d["category_names"], d["user_depts"],
                             service_ids)


def test_служебная_запись_не_числится_исполнителем():
    from tests import fixture
    m = _build(fixture.SERVICE_IDS)
    for s in m["sourcersA"]:
        assert s["id"] not in fixture.SERVICE_IDS, (
            "служебная запись попала в список сорсеров как исполнитель")


def test_карточки_робота_засчитаны_живому_сотруднику():
    from tests import fixture
    m = _build(fixture.SERVICE_IDS)
    s = m["origin"]["summary"]
    assert s["viaService"] > 0, "в синтетике есть карточки служебной записи"
    assert s["serviceResolved"] > 0, "ни одна карточка робота не отнесена к человеку"
    assert s["serviceResolved"] <= s["viaService"]
    to = m["origin"]["resolvedTo"]
    assert sum(c["n"] for c in to) == s["serviceResolved"]
    assert all(c["uid"] not in fixture.SERVICE_IDS for c in to)


def test_цепочка_восстановления_объяснена_и_сходится():
    from tests import fixture
    m = _build(fixture.SERVICE_IDS)
    o = m["origin"]
    how = {x["how"]: x["n"] for x in o["resolvedHow"]}
    assert sum(how.values()) == o["summary"]["viaService"], (
        "разбор «чем определён исполнитель» не сходится с числом карточек робота")
    assert how.get("владелец сделки"), (
        "у карточек робота исполнитель должен восстанавливаться по родительской сделке")
    assert "ответственный" not in how, (
        "ответственным у карточки робота записан он сам — этот путь невозможен")


def test_нагрузка_сорсеров_растёт_после_учёта_служебной_записи():
    from tests import fixture
    before = _build(None)
    after = _build(fixture.SERVICE_IDS)
    a_before = sum(s["c"] for s in before["sourcersA"])
    a_after = sum(s["c"] for s in after["sourcersA"])
    assert a_after > a_before, (
        "после учёта служебной записи карточки робота должны вернуться сорсерам")
    assert before["kpi"]["total"] == after["kpi"]["total"], (
        "общее число запросов от разбора зависеть не должно")


def test_пустой_список_служебных_записей_ничего_не_меняет():
    """Сначала мерим, потом применяем: пока список пуст, поведение прежнее —
    исполнителем остаётся ответственный карточки."""
    import metrics as metrics_mod
    from tests import fixture
    d = fixture.make_dataset()
    m = metrics_mod.build(d["period"], d["rfqs"], d["deal_index"], d["period_deals"],
                          d["dept_a_ids"], d["names"], d["since"],
                          d["deal_stage_names"], d["category_names"], d["user_depts"], None)
    assert m["origin"]["summary"]["viaService"] == 0
    assert m["origin"]["summary"]["serviceConfigured"] == 0
    assert all(r["_owner"] == str(r["assignedById"]) for r in d["rfqs"]), (
        "при пустом списке исполнитель обязан совпадать с ответственным карточки")


def test_кандидаты_показываются_но_не_применяются():
    """Запись без подразделения с потоком карточек предлагается владельцу,
    но из статистики не выключается сама."""
    from tests import fixture
    m = _build(None)
    o = m["origin"]
    cand = {c["uid"] for c in o["serviceCandidates"]}
    assert fixture.SERVICE_BOT in cand, (
        "робот без подразделения с потоком карточек должен попасть в кандидаты")
    assert o["summary"]["candidates"] == len(o["serviceCandidates"])
    assert o["summary"]["candidateFloor"] >= 10
    # кандидат остаётся обычным автором: его карточки никуда не переехали
    assert o["summary"]["viaService"] == 0
    assert any(c["uid"] == fixture.SERVICE_BOT and not c["auto"]
               for c in o["byCreator"]), "кандидат должен остаться в разборе как автор"


def test_нераспознанная_карточка_робота_не_приписывается_никому():
    from tests import fixture
    m = _build(fixture.SERVICE_IDS)
    o = m["origin"]
    how = {x["how"]: x["n"] for x in o["resolvedHow"]}
    assert how.get("не определён", 0) >= 1, (
        "карточка робота без родительской сделки должна оставаться нераспознанной"
    )
    assert o["summary"]["serviceResolved"] + how.get("не определён", 0) == o["summary"]["viaService"]


def test_разрез_по_подразделению_исполнителя_сходится():
    """«Чья это работа» считается по исполнителю, а не по автору карточки,
    и обязан раскладывать все запросы периода без потерь."""
    from tests import fixture
    m = _build(fixture.SERVICE_IDS)
    o = m["origin"]
    assert sum(d["n"] for d in o["byOwnerDept"]) == o["summary"]["total"]
    assert all(d["dept"] != "служебная запись (робот пресейла)" for d in o["byOwnerDept"]), (
        "служебная запись не может быть подразделением-исполнителем")


def test_карточки_робота_переезжают_в_подразделение_исполнителя():
    from tests import fixture
    before = {d["dept"]: d["n"] for d in _build(None)["origin"]["byOwnerDept"]}
    after = {d["dept"]: d["n"] for d in _build(fixture.SERVICE_IDS)["origin"]["byOwnerDept"]}
    assert before.get("подразделение не указано", 0) > after.get("подразделение не указано", 0), (
        "до учёта служебной записи её карточки висели на записи без подразделения")
    assert after.get("Отдел поиска поставщиков", 0) > before.get("Отдел поиска поставщиков", 0), (
        "после учёта часть карточек робота должна вернуться отделу поиска поставщиков")


# ── полученные КП по неделям ─────────────────────────────────────────────────
# Источник — файл КП со стороны поставщика в карточке, а не входящее письмо:
# замер прогона 23.09.2026 показал ноль входящих писем на карточках СП-166.
# Неделя берётся по последнему движению карточки, другой даты в выдаче нет.

def test_кп_считаются_по_файлу_поставщика_а_не_по_письму():
    from tests import fixture
    d = fixture.make_dataset()
    m = fixture.build_metrics()
    ждём = sum(1 for r in d["rfqs"]
               if r.get("_hasQuote")
               and d["period"].week_index(period_mod.parse_dt(r.get("movedTime") or "")) is not None)
    assert ждём > 0, "в синтетике есть карточки с файлом КП"
    assert sum(w["kp"] for w in m["weekly"]) == ждём


def test_кп_принято_в_работу_считается_по_стадии_и_неделе_перевода():
    """Другой факт, чем наличие файла: карточку перевели в «КП получено»."""
    import stages as st
    from tests import fixture
    d = fixture.make_dataset()
    m = fixture.build_metrics()
    ждём = sum(1 for r in d["rfqs"]
               if st.classify_stage(r.get("stageId", "")) == "selected"
               and d["period"].week_index(period_mod.parse_dt(r.get("movedTime") or "")) is not None)
    assert ждём > 0, "в синтетике есть карточки в стадии «КП получено»"
    assert sum(w["kpa"] for w in m["weekly"]) == ждём


def test_письма_остаются_контрольным_числом_а_не_источником_кп():
    """Входящие письма считаются отдельной величиной и в столбец КП не идут:
    ноль писем не должен обнулять график, у которого источник другой."""
    import metrics as metrics_mod
    from tests import fixture
    d = fixture.make_dataset()
    m = metrics_mod.build(d["period"], d["rfqs"], d["deal_index"], d["period_deals"],
                          d["dept_a_ids"], d["names"], d["since"],
                          d["deal_stage_names"], d["category_names"], d["user_depts"],
                          d.get("service_ids"), None)
    assert all(w["inb"] == 0 for w in m["weekly"])
    assert sum(w["kp"] for w in m["weekly"]) > 0, (
        "без писем столбец полученных КП обязан остаться: он считается по файлам"
    )
    с_письмами = fixture.build_metrics()["weekly"]
    assert sum(w["inb"] for w in с_письмами) > 0
    assert [w["kp"] for w in с_письмами] == [w["kp"] for w in m["weekly"]], (
        "письма на счёт полученных КП влиять не должны")


def test_горизонт_недельного_ряда_покрывает_семь_недель():
    """График показывает последние семь недель — значит ряд обязан быть не короче."""
    from tests import fixture
    assert len(fixture.build_metrics()["weekly"]) >= 7


def test_файл_кп_отличается_от_нашего_исходящего_запроса():
    """«Request file» — наш запрос, и за полученное КП он не считается."""
    import config as config_mod
    import main as main_mod
    наш = "ufCrm18_1727423346"
    assert наш not in config_mod.RFQ_QUOTE_FIELDS
    поле_кп = "ufCrm18_1700698211875"
    assert main_mod._has_quote_file({поле_кп: [{"id": 1}]}) is True
    assert main_mod._has_quote_file({поле_кп: {"id": 1}}) is True
    assert main_mod._has_quote_file({наш: [{"id": 1}]}) is False
    for пусто in ([], "", None, [None]):
        assert main_mod._has_quote_file({поле_кп: пусто}) is False, пусто


# ── разбор писем карточек: исходящие и входящие из одной выгрузки ────────────
# Выгрузка сведена в один проход crm.activity.list без фильтра DIRECTION.
# Разбор на нашей стороне, поэтому он и проверяется: ошибка здесь тихо обнулила
# бы и таблицу «отправлено vs создано», и счётчик полученных КП.

def _acts():
    """Придуманная выгрузка писем в том виде, в каком её отдаёт _mail_activities."""
    def m(cid, d, dt_, file=False):
        return {"cid": cid, "dir": d, "subj": "тема", "to": "s@example.org",
                "body": "текст", "file": file, "dt": dt_[:16].replace("T", " "), "dtx": dt_}
    return [
        m("1000", "2", "2026-05-04T10:00:00+03:00"),          # отправлен запрос
        m("1000", "2", "2026-05-06T10:00:00+03:00"),          # напоминание
        m("1000", "1", "2026-05-07T09:00:00+03:00", True),    # КП с вложением
        m("1001", "1", "2026-05-07T11:00:00+03:00"),          # ответ без вложения
        m("1002", "2", "2026-05-08T10:00:00+03:00"),
    ]


def test_входящие_отбираются_по_направлению_а_не_по_вложению():
    import main as main_mod
    inb = main_mod._inbound_mail(_acts())
    assert len(inb) == 2, "входящих в выгрузке ровно два"
    assert {a["cid"] for a in inb} == {"1000", "1001"}
    assert sum(1 for a in inb if a["file"]) == 1
    assert all(set(a) == {"cid", "dt", "file"} for a in inb)


def test_отправлено_считается_только_по_исходящим():
    import main as main_mod
    rfqs = [{"id": 1000, "_owner": "76", "stageId": "DT166_24:PREPARATION"},
            {"id": 1001, "_owner": "76", "stageId": "DT166_24:PREPARATION"},
            {"id": 1003, "_owner": "76", "stageId": "DT166_24:NEW"}]
    res = main_mod._send_stats(_acts(), rfqs, [{"id": "76", "n": "Иванов И."}], {"76"})
    row = res["rows"][0]
    assert row["total"] == 3
    assert row["sent"] == 1, "исходящие есть только у карточки 1000"
    assert row["nosend"] == 2
    assert row["fake"] == 1, "карточка 1001 вышла из «Новый» без единого письма"
    assert row["fuPct"] == 100, "по карточке 1000 было повторное письмо"
    # входящие письма в переписку сорсера не попадают
    assert all(e["dtx"] < "2026-05-07" for e in res["byCard"]["1000"])
    assert "1001" not in res["byCard"], (
        "карточка с одним входящим письмом исходящей переписки не имеет")


def test_служебная_запись_с_подразделением_помечается():
    """Живого сотрудника, попавшего в список по ошибке, надо увидеть, а не
    молча вычесть из его же статистики."""
    import metrics as metrics_mod
    from tests import fixture
    d = fixture.make_dataset()
    depts = dict(d["user_depts"])
    depts[fixture.SERVICE_BOT] = "Инжиниринг"        # как будто запись — человек
    m = metrics_mod.build(d["period"], d["rfqs"], d["deal_index"], d["period_deals"],
                          d["dept_a_ids"], d["names"], d["since"],
                          d["deal_stage_names"], d["category_names"], depts,
                          fixture.SERVICE_IDS, d["inbound_mail"])
    sv = m["origin"]["service"]
    assert sv and sv[0]["looksHuman"] is True and sv[0]["dept"] == "Инжиниринг"
    # без подразделения пометки быть не должно
    clean = fixture.build_metrics()["origin"]["service"]
    assert clean and clean[0]["looksHuman"] is False


def test_список_служебных_записей_переопределяется_окружением():
    """Состав меняется: значение в коде — только умолчание, решает окружение."""
    import importlib
    import os
    import config as config_mod
    prev = os.environ.get("SERVICE_ACCOUNT_IDS")
    try:
        os.environ["SERVICE_ACCOUNT_IDS"] = "7, 8 ,7"
        assert importlib.reload(config_mod).SERVICE_ACCOUNT_IDS == {"7", "8"}
        os.environ["SERVICE_ACCOUNT_IDS"] = "off"    # разбор отключается словом
        assert importlib.reload(config_mod).SERVICE_ACCOUNT_IDS == set()
        # Actions подставляет пустую строку для незаданной переменной — это
        # «переменной нет», а не «выключено»: умолчание кода должно устоять
        os.environ["SERVICE_ACCOUNT_IDS"] = ""
        cfg = importlib.reload(config_mod)
        assert cfg.SERVICE_ACCOUNT_IDS == {cfg.SERVICE_ACCOUNT_DEFAULT}
        del os.environ["SERVICE_ACCOUNT_IDS"]        # без переменной — то же самое
        cfg = importlib.reload(config_mod)
        assert cfg.SERVICE_ACCOUNT_IDS == {cfg.SERVICE_ACCOUNT_DEFAULT}
    finally:
        if prev is None:
            os.environ.pop("SERVICE_ACCOUNT_IDS", None)
        else:
            os.environ["SERVICE_ACCOUNT_IDS"] = prev
        importlib.reload(config_mod)
