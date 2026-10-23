"""Вкладка «Сорсинг»: число показывается, только если оно верно (ревизия 23.10.2026).

Правило владельца: сломанные данные хуже, чем никаких. Каждый тест закрепляет
одну правку ревизии — нарушения правил КП только с даты правил, «двинута без
письма в CRM» без доказанного контакта, «первые 60 из N» в пресейле, «—» вместо
нуля там, где знаменателя нет, карточки без исполнителя названы внутри блока B,
темп «за 4 недели» без незаконченного сегодняшнего дня, пометка неполной недели,
один предикат покрытия на оба места, тексты без скрытой доли ТКП, реакция в
чатах только по дням со знаменателем. Корпуса придуманы (правило 18).
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import metrics as metrics_mod   # noqa: E402
import period as period_mod     # noqa: E402
from tests import fixture       # noqa: E402

АКТИВЕН = {"1": {"active": True, "hired": dt.date(2024, 1, 1)}}


def _к(i: int, день: dt.date, стадия: str = "DT166_24:NEW", moved: dt.date | None = None, **kw) -> dict:
    return {"id": i, "assignedById": "1", "createdBy": "1", "movedBy": "1", "updatedBy": "1",
            "lastActivityBy": "1", "stageId": стадия, "categoryId": 24, "title": f"Запрос {i}",
            "createdTime": день.isoformat() + "T10:00:00+03:00",
            "movedTime": (moved or день).isoformat() + "T12:00:00+03:00", "parentId2": None, **kw}


def _сборка(карточки: list[dict], начало: str, конец: str, **kw) -> dict:
    p = fixture.make_period(начало, конец)
    staff = kw.pop("staff", АКТИВЕН)
    return metrics_mod.build(p, карточки, {}, kw.pop("period_deals", []), set(staff), {u: "Сорсер " + u for u in staff},
                             {}, {}, {}, staff=staff, **kw)


# ── правила отметки КП: до правил — только застывшее до 19.10.2026 ───────────

def test_до_правил_только_застывшее_остальное_нарушение():
    после = _к(1, dt.date(2026, 10, 1), "DT166_24:UC_H49RUE", moved=dt.date(2026, 10, 20))   # PaW без файла
    до = _к(2, dt.date(2026, 9, 1), "DT166_24:UC_H49RUE", moved=dt.date(2026, 9, 20))       # PaW без файла с 20.09
    чисто = _к(3, dt.date(2026, 10, 2), "DT166_24:SUCCESS", moved=dt.date(2026, 10, 21), _hasQuote=True)
    закрыта = _к(4, dt.date(2026, 9, 2), "DT166_24:SUCCESS", moved=dt.date(2026, 10, 1))     # Selected без файла
    файл = _к(5, dt.date(2026, 10, 5), "DT166_24:PREPARATION", moved=dt.date(2026, 10, 12), _hasQuote=True,
              updatedTime="2026-10-21T10:00:00+03:00")                                       # файл после правил
    m = _сборка([после, до, чисто, закрыта, файл], "2026-09-01", "2026-10-23")
    s = m["sourcersA"][0]
    assert (s["violN"], s["viol"], s["violPre"]) == (3, {"noFile": 2, "fileNoStage": 1}, 2), (
        "открытая Price at Work без файла и файл после 19.10 — нарушения; закрытая до правил — наследие")
    det = {d["id"]: d for d in s["details"]}
    assert det["1"]["fl"] == ["noFile"] and "flp" not in det["1"]
    assert det["2"]["fl"] == ["noFile"] and det["2"]["flp"] == ["pawStale"], (
        "под правилами прошло 4 дня из 14 — простой пока наследие, файл приложить можно и сейчас")
    assert det["4"]["flp"] == ["noFile"] and "fl" not in det["4"]
    assert det["5"]["fl"] == ["fileNoStage"]
    kr = m["kpRules"]
    assert (kr["cards"], kr["total"]) == (3, {"noFile": 2, "fileNoStage": 1})
    assert (kr["preCards"], kr["pre"]) == (2, {"noFile": 1, "pawStale": 1})
    assert m["kpi"]["violCards"] == 3


def test_price_at_work_до_правил_становится_нарушением_через_14_дней(monkeypatch):
    monkeypatch.setenv("KVANT_NOW", "2026-11-20T09:00:00+03:00")
    paw = _к(1, dt.date(2026, 10, 10), "DT166_24:UC_H49RUE", moved=dt.date(2026, 10, 15), _hasQuote=True)
    s = _сборка([paw], "2026-10-01", "2026-11-20")["sourcersA"][0]
    assert (s["violN"], s["viol"], s["violPre"]) == (1, {"pawStale": 1}, 0)


# ── «Ср. срок» без закрытых — нет срока, а не ноль ───────────────────────────

def test_ср_срок_без_закрытых_пустой_а_не_ноль():
    m = _сборка([_к(i, dt.date(2026, 6, 1) + dt.timedelta(days=i)) for i in range(5)], "2026-05-04", "2026-07-31")
    assert m["sourcersA"][0]["avgDays"] is None, "0 читался бы как «самый быстрый»"
    закрытая = _к(9, dt.date(2026, 6, 1), "DT166_24:SUCCESS", moved=dt.date(2026, 6, 3))
    m = _сборка([закрытая], "2026-05-04", "2026-07-31")
    assert m["sourcersA"][0]["avgDays"] == 2


# ── «двинута без письма в CRM» — без карточек с записанной реакцией поставщика ─

def _без_письма(стадии: dict, с_файлом=()) -> int:
    import main as main_mod
    rfqs = [{"id": i, "_owner": "76", "stageId": s, "_hasQuote": i in с_файлом} for i, s in стадии.items()]
    return main_mod._send_stats([], rfqs, [{"id": "76", "n": "Сорсер"}], {"76"})["rows"][0]["fake"]


def test_двинута_без_письма_не_считает_карточки_с_реакцией_поставщика():
    стадии = {1: "DT166_24:PREPARATION", 2: "DT166_24:SUCCESS", 3: "DT166_24:UC_H49RUE",
              4: "DT166_24:PREPARATION", 5: "DT166_24:2", 6: "DT166_24:3", 7: "DT166_24:NEW",
              8: "DT166_24:UC_61BSRU", 9: "DT166_24:FAIL"}
    # считаются 1 (отправлен) и 6 («Ответ не получен»): ни письма, ни файла, ни ответа поставщика
    assert _без_письма(стадии, с_файлом=(3, 4)) == 2
    assert _без_письма({6: "DT166_24:3"}) == 1, "«Ответ не получен» ответа не фиксирует — остаётся в счёте"
    assert _без_письма({8: "DT166_24:UC_61BSRU", 9: "DT166_24:UC_GFJ5A8"}) == 0, (
        "переписка без цены — поставщик ответил")


# ── покрытие: один предикат, технические воронки вне знаменателя ─────────────

def test_предикат_покрытия_один_и_без_технических_воронок():
    import main as main_mod
    живая = {"ID": "1", "TITLE": "Насос", "OPPORTUNITY": "1000", "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "8"}
    assert metrics_mod.req_expected(живая) is True
    assert metrics_mod.req_expected(dict(живая, CATEGORY_ID="22")) is False, "тестовая воронка"
    assert metrics_mod.req_expected(dict(живая, OPPORTUNITY="0")) is False
    assert metrics_mod.req_expected(dict(живая, STAGE_SEMANTIC_ID="F")) is False
    assert metrics_mod.req_expected(dict(живая, TITLE="RFQ насосы")) is False
    # правило 14: вторая копия предиката в main.py больше не пишется руками
    исходник = inspect.getsource(main_mod)
    assert "_exp = metrics_mod.req_expected" in исходник and "_exp = lambda" not in исходник
    m = _сборка([], "2026-05-04", "2026-07-31",
                period_deals=[живая, dict(живая, ID="2", CATEGORY_ID="22")])
    assert m["coverage"]["deals"] == 1, "сделка технической воронки запроса не ждёт"


def test_глубина_подписана_запросами_а_не_поставщиками():
    m = fixture.build_metrics()
    assert [h["l"] for h in m["coverage"]["hist"]] == ["1 запрос", "2–3", "4–5", "6+"]


# ── блок B: карточки без исполнителя названы числом ──────────────────────────

def test_исполнитель_не_определён_назван_внутри_вне_отдела():
    m = fixture.build_metrics()
    k = m["kpi"]
    assert k["deptA"] + k["outside"] == k["total"], "инвариант сверки не меняется"
    assert 0 < k["unresolved"] <= k["outside"]
    assert sum(k["unresolvedBy"].values()) == k["unresolved"]
    строка = next(x for x in m["origin"]["byOwnerDept"] if x["dept"] == "исполнитель не определён")
    assert строка["n"] == k["unresolved"], "то же число, что в разрезе «Чья это работа»"


# ── темп «сейчас» — по вчерашний день ────────────────────────────────────────

def test_темп_за_4_недели_не_делит_на_незаконченный_сегодняшний_день(monkeypatch):
    сегодня = dt.date(2026, 7, 31)                                   # пятница, сборка в 9 утра
    monkeypatch.setenv("KVANT_NOW", "2026-07-31T09:00:00+03:00")
    карточки = [_к(i, dt.date(2026, 5, 4) + dt.timedelta(days=i)) for i in range((сегодня - dt.date(2026, 5, 4)).days)]
    карточки += [_к(500 + i, сегодня) for i in range(6)]              # сегодня успели завести шесть
    s = _сборка(карточки, "2026-05-04", "2026-07-31")["sourcersA"][0]
    вчера = сегодня - dt.timedelta(days=1)
    от = вчера - dt.timedelta(days=27)
    wd4 = period_mod.workdays(от, вчера)
    assert wd4 == 20, "окно — те же 28 дней, только закрыто вчерашним днём"
    assert s["c4"] == 28, "сегодняшние карточки не идут в числитель, как и сам день — в знаменатель"
    assert s["perDay4"] == round(28 / wd4, 1)
    assert s["c"] == len(карточки), "итог Σ не урезается"


def test_роботные_карточки_темпа_названы_числом():
    m = fixture.build_metrics()
    assert all(0 <= s["robot4"] <= s["c4"] for s in m["sourcersA"])
    assert sum(s["robot4"] for s in m["sourcersA"]) > 0, "в синтетике робот заводит карточки сорсерам"


# ── неполная последняя неделя помечена ───────────────────────────────────────

def test_неполная_неделя_помечена(monkeypatch):
    monkeypatch.setenv("KVANT_NOW", "2026-07-29T09:00:00+03:00")      # среда
    m = _сборка([_к(1, dt.date(2026, 7, 27))], "2026-05-04", "2026-07-29")
    last = m["weekly"][-1]
    assert (last["part"], last.get("cur")) == (2, True), "прожиты пн и вт, среда идёт"
    assert all("part" not in w for w in m["weekly"][:-1])
    monkeypatch.delenv("KVANT_NOW")
    m = _сборка([_к(1, dt.date(2026, 7, 27))], "2026-05-04", "2026-07-31")   # прошлый период, по пятницу
    assert "part" not in m["weekly"][-1], "без выходных неделя полная: рабочие дни все"


def test_первая_неделя_периода_тоже_неполная():
    """01.05.2026 — пятница: в первой неделе периода один рабочий день."""
    m = _сборка([_к(1, dt.date(2026, 5, 1))], "2026-05-01", "2026-07-31")
    assert (m["weekly"][0]["part"], m["weekly"][0].get("cur")) == (1, None)
    assert all("part" not in w for w in m["weekly"][1:])


# ── тренд ▲▼: оба темпа по вчерашний день ───────────────────────────────────

def test_ровный_темп_без_ложной_стрелки(monkeypatch):
    """Сборка в пятницу 23.10 в 9 утра: сегодняшний день не идёт ни в «за 4
    недели», ни в средний темп окна — при ровной работе оба темпа равны, ▲ нет."""
    monkeypatch.setenv("KVANT_NOW", "2026-10-23T09:00:00+03:00")
    приём = dt.date(2026, 10, 12)

    def будни(a: dt.date, b: dt.date) -> list[dt.date]:
        return [a + dt.timedelta(days=i) for i in range((b - a).days + 1) if (a + dt.timedelta(days=i)).weekday() < 5]

    новичок = [dict(_к(100 + j * 10 + n, д), assignedById="2", createdBy="2")
               for j, д in enumerate(будни(приём, dt.date(2026, 10, 22))) for n in range(4)]
    старожил = [_к(1000 + j * 10 + n, д) for j, д in enumerate(будни(dt.date(2026, 9, 1), dt.date(2026, 10, 22)))
                for n in range(6)]
    staff = {"1": {"active": True, "hired": dt.date(2024, 1, 1)}, "2": {"active": True, "hired": приём}}
    m = _сборка(новичок + старожил, "2026-09-01", "2026-10-23", staff=staff)
    темп = {s["id"]: (s["perDay"], s["perDay4"]) for s in m["sourcersA"]}
    assert темп["2"] == (4.0, 4.0) and темп["1"] == (6.0, 6.0), темп


# ── пресейл: «первые 60 из N», без «Выиграно» ────────────────────────────────

def test_пресейл_сообщает_полное_число_до_обрезки(monkeypatch):
    import presale
    from tests import test_presale as tp
    полный = tp._ps()
    monkeypatch.setattr(presale, "ACT_SHOWN", 2)
    ps = tp._ps()
    assert len(ps["act"]) == 2 and ps["head"]["actN"] == len(полный["act"]) == 3


def test_шаблон_не_показывает_скрытое():
    t = (ROOT / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
    сорсинг = t[t.index("function renderPresale"):t.index("function openDrw")]
    for нет in ("Выиграно</th>", "Выиграно: <b>", "tkpList", "donutChain", "${c.tkpPct}%",
                "${r.tkpP}%", "фейк", "Ср. постав.", "≈ 98"):
        assert нет not in сорсинг, нет
    assert "actN" in сорсинг and "показаны первые" in сорсинг
    assert "до ТКП ${s.tkpP}%" not in t and "≈ 98%" not in t


# ── тексты по правилам не называют скрытую долю ТКП ──────────────────────────

def test_тексты_не_называют_долю_ткп():
    import insights
    m = fixture.build_metrics()
    out = insights.rule_based(m)
    for блок, текст in out.items():
        assert "ТКП" not in текст, блок
    assert "запрос" in out["coverage"] and "поставщика на сделку" not in out["coverage"]
    assert "до 19.10.2026 файл не был обязателен" in out["conversion"], "доля КП файлом — с оговоркой"
    assert "мало" not in out["conversion"], "величину недоучёта никто не мерил"


# ── реакция в чатах: среднее только по дням со знаменателем ──────────────────

def _день(m: int, h: int | None, s: int, w: int | None) -> dict:
    return {"mentions": m, "self": s, "coll": 0, "none": m - s, "medMin": 10 if s else None,
            "withinSla": w, "w30": w, "w120": w, "w1440": w, "answeredSelf": s, "handledPct": h,
            "waitN": 0, "waitGt1d": 0, "waitGt3d": 0, "waitMaxDays": 0, "activeChats": 1, "lastPostDays": 1.0}


def test_реакция_усредняется_только_по_дням_с_упоминаниями(tmp_path):
    import reps
    смешанный, без_упоминаний = "900111", "900222"
    дни = {}
    for i in range(7):
        есть = i >= 3                                            # три дня без упоминаний, четыре с ними
        дни[(dt.date(2031, 2, 1) + dt.timedelta(days=i)).isoformat()] = {
            смешанный: _день(1 if есть else 0, 100 if есть else 0, 1 if есть else 0, 100 if есть else 0),
            без_упоминаний: _день(0, 0, 0, 0)}
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({"days": дни}), encoding="utf-8")
    r = reps._load_react_snapshot([смешанный, без_упоминаний], private={}, path=str(p))
    assert r[смешанный]["handledPct"] == 100, "дни без упоминаний не тянут долю вниз (было 57)"
    assert r[смешанный]["withinSla"] == 100 and r[смешанный]["answeredSelf"] == 1
    assert r[без_упоминаний]["handledPct"] is None and r[без_упоминаний]["withinSla"] is None, (
        "нет знаменателя — нет доли: на странице «—», а не 0 %")
    assert r[без_упоминаний]["mentions"] == 0 and r[без_упоминаний]["days"] == 7


def test_доля_реакции_не_стоит_рядом_с_нулём(tmp_path):
    """Счётчики рядом с долей — по тем же дням: «100 % из 0» не бывает."""
    import reps
    редкий = "900333"
    дни = {(dt.date(2031, 3, 1) + dt.timedelta(days=i)).isoformat():
           {редкий: _день(1, 100, 1, 100) if i >= 5 else _день(0, 0, 0, 0)} for i in range(7)}
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({"days": дни}), encoding="utf-8")
    r = reps._load_react_snapshot([редкий], private={}, path=str(p))[редкий]
    assert r["handledPct"] == 100 and r["mentions"] >= 1 and r["self"] >= 1, r
    assert r["withinSla"] == 100 and r["answeredSelf"] >= 1, r


def test_нет_снимка_чатов_не_ноль_процентов():
    t = (ROOT / "templates" / "dashboard_core.html").read_text(encoding="utf-8")
    assert "нет данных скана" in t
    import reps
    исходник = inspect.getsource(reps.compute)
    assert '"handledPct": None' in исходник and '"withinSla": None' in исходник
