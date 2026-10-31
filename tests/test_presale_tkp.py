"""КП → ТКП по истории стадий пресейла (зонд v62, 30.10.2026): сделка с КП файлом дошла до
«Экономика готова» и до «ТКП отправлено» — по первым входам из crm.stagehistory, а не по текущей
стадии. Корпус придуман (tests.fixture, CLAUDE.md, правило 18)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import bitrix_client  # noqa: E402
import presale  # noqa: E402
from tests import fixture  # noqa: E402


def test_стадии_экономики_и_ткп_по_подписи():
    econ, tkp = presale.tkp_stage_ids(fixture.ps_stage_meta(), "32")
    assert econ == {"C32:READY"}
    # «Сорсинг завершен / Ожидаем решение заказчика» и успех; «Тендерное предложение» — не ТКП регламента
    assert tkp == {"C32:WAIT", "C32:WON"}
    assert presale.tkp_stage_ids(fixture.ps_stage_meta(), "33") == (set(), set())


def test_кп_ткп_по_истории():
    P = fixture.build_presale(tkp=True)
    h = P["head"]
    assert (h["kp"], h["kpEcon"], h["kpTkp"]) == (6, 4, 2)
    assert h["wait7"] == 2                      # 8001 и 8002 сейчас на «Экономика готова»
    by = {a["id"]: a for a in P["bySourcer"]}
    assert (by["77"]["kp"], by["77"]["kpEcon"], by["77"]["kpTkp"]) == (2, 2, 2)
    assert (by["76"]["kp"], by["76"]["kpEcon"], by["76"]["kpTkp"]) == (3, 1, 0)
    assert (by["78"]["kp"], by["78"]["kpEcon"], by["78"]["kpTkp"]) == (1, 1, 0)
    kd = {d["id"]: d for d in P["kpDeals"]}
    assert set(kd) == {"8001", "8003", "8004", "8005", "8009", "8010"}
    assert kd["8004"]["econ"] and kd["8004"]["tkp"]
    assert kd["8005"]["tkp"] and not kd["8005"]["econ"]        # сразу «ТКП отправлено»
    assert kd["8009"]["econ"] and not kd["8009"]["tkp"]        # экономика, затем отказ
    assert kd["8001"]["econ"] == fixture.CT_TODAY.isoformat()  # сейчас на стадии, входа в выдаче нет
    assert not kd["8003"]["econ"] and not kd["8003"]["tkp"]
    assert not kd["8010"]["econ"] and not kd["8010"]["tkp"]
    # сверка вторым способом: сумма по сорсерам = итог воронки
    assert sum(a.get("kpTkp", 0) for a in P["bySourcer"]) == h["kpTkp"]
    assert sum(a.get("kp", 0) for a in P["bySourcer"]) == h["kp"] == len(P["kpDeals"])


def test_без_истории_полей_нет():
    P = fixture.build_presale()
    assert "kp" not in P["head"] and "kpDeals" not in P
    assert all("kp" not in a for a in P["bySourcer"])


class _Hist(bitrix_client.BitrixClient):
    """crm.stagehistory.list: одна страница, в выдаче — и чужая стадия (фильтр портала не сработал)."""

    def __init__(self):
        self.params = None

    def call_envelope(self, method, params=None, **kw):
        assert method == "crm.stagehistory.list"
        self.params = dict(params)
        return {"result": {"items": [
            {"OWNER_ID": "1", "STAGE_ID": "C32:READY", "CREATED_TIME": "2026-10-01T10:00:00+03:00"},
            {"OWNER_ID": "1", "STAGE_ID": "C32:READY", "CREATED_TIME": "2026-10-03T10:00:00+03:00"},
            {"OWNER_ID": "2", "STAGE_ID": "C7:READY", "CREATED_TIME": "2026-10-02T10:00:00+03:00"},
            {"OWNER_ID": "1", "STAGE_ID": "C32:WAIT", "CREATED_TIME": "2026-10-05T10:00:00+03:00"}]}}


def test_история_по_стадиям_фильтр_и_сверка():
    c = _Hist()
    h = c.stage_history(2, since="2026-05-01", stage_ids=["C32:WAIT", "C32:READY"])
    assert c.params["filter"]["STAGE_ID"] == ["C32:READY", "C32:WAIT"]
    assert "CATEGORY_ID" not in c.params["filter"]
    assert h == {"1": [("C32:READY", "2026-10-01T10:00:00+03:00"), ("C32:WAIT", "2026-10-05T10:00:00+03:00")]}


def test_шаг_регламента_по_подписи():
    assert presale.step_of("Проработка ТЗ и вопросы заказчику") == 3
    assert presale.step_of("Анализ КП и фиксация поставщиков") == 5
    assert presale.step_of("Сорсинг завершен / Ожидаем решение заказчика") == 9
    assert presale.step_of("Тендерное предложение выдано") is None


def test_норматив_кп_и_срок_заказчика():
    P = fixture.build_presale(tkp=True)
    od = {r["id"]: r for r in P["openDeals"]}
    a = od["8010"]                      # «Анализ КП», ЗИП: 1 КП из 3 — добрать; срок 4 дня назад = 2 р.д.
    assert (a["step"], a["zip"], a["kpNorm"], a["quotes"], a["kpShort"]) == (5, True, 3, 1, True)
    assert a["dtype"] == "Запрос от заказчика"
    assert (a["dueWd"], a["dueSoon"]) == (2, True)
    b = od["8007"]                      # «Поиск поставщиков»: норматив ещё не проверяется; срок через 2 р.д.
    assert (b["step"], b["kpShort"], b["dueWd"], b["dueSoon"]) == (4, False, -2, True)
    assert not od["8001"]["dueSoon"]    # срока нет
    h = P["head"]
    assert (h["kpShort"], h["dueSoon"], h["duePast"], h["dueFilled"]) == (1, 2, 1, 2)
    # сверка вторым способом: счёт головы = строки открытых сделок
    assert h["kpShort"] == sum(r["kpShort"] for r in P["openDeals"])
    assert h["dueSoon"] == sum(r["dueSoon"] for r in P["openDeals"])


def test_срок_на_шаге_7_и_дальше_не_дело_сорсера():
    deals = fixture.ps_deals()
    import datetime as dt
    for x in deals:
        x[presale.DUE_F] = (fixture.CT_TODAY - dt.timedelta(days=1)).isoformat()
    P = presale.compute(cid="32", cat_name="Пресейл", deals=deals, stage_meta=fixture.ps_stage_meta(), rfqs=[],
                        people={}, names={}, today=fixture.CT_TODAY)
    od = {r["id"]: r for r in P["openDeals"]}
    assert not od["8001"]["dueSoon"]    # «Экономика готова» (шаг 7) — ТКП за КАМом
    assert not od["8004"]["dueSoon"]    # «Сорсинг завершен» (шаг 9)
    assert od["8006"]["dueSoon"]        # «Поиск поставщиков» (шаг 4), срок вчера
