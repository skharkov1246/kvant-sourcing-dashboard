"""Загрузчик индексов цен (МВФ) и досчёт дат квотаций — без сети и без базы.

Ответ источника придуман (правило 18), но устроен как настоящий ответ SDMX 2.1
structure-specific от api.imf.org: атрибуты ряда и точки, период «ГГГГ-MММ».
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import load_cpi as L  # noqa: E402
import quote_dates_backfill as b  # noqa: E402

ОТВЕТ = """<?xml version='1.0' encoding='UTF-8'?>
<message:StructureSpecificData xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message">
 <message:DataSet>
  <Series COUNTRY="RUS" INDEX_TYPE="CPI" COICOP_1999="_T" TYPE_OF_TRANSFORMATION="IX"
          FREQUENCY="M" COMMON_REFERENCE_PERIOD="2010A">
   <Obs TIME_PERIOD="2025-M01" OBS_VALUE="250.5" REFERENCE_PERIOD="2010A"/>
   <Obs TIME_PERIOD="2025-M02" OBS_VALUE=""/>
   <Obs TIME_PERIOD="2025-M03" OBS_VALUE="0"/>
   <Obs TIME_PERIOD="кривой" OBS_VALUE="1"/>
  </Series>
  <Series COUNTRY="ITA" INDEX_TYPE="CPI" COICOP_1999="_T" TYPE_OF_TRANSFORMATION="IX"
          FREQUENCY="M" COMMON_REFERENCE_PERIOD="2025A">
   <Obs TIME_PERIOD="2025-M12" OBS_VALUE="101.2"/>
  </Series>
  <Series COUNTRY="G163" INDEX_TYPE="CPI"><Obs TIME_PERIOD="2025-M01" OBS_VALUE="5"/></Series>
 </message:DataSet>
</message:StructureSpecificData>"""


def test_разбор_ответа():
    точки = L.разобрать(ОТВЕТ)
    assert [(т["country"], т["month"], т["index_value"], т["base"]) for т in точки] == [
        ("RUS", date(2025, 1, 1), 250.5, "2010A"),
        ("ITA", date(2025, 12, 1), 101.2, "2025A"),     # база — из ряда, если у точки нет
    ]
    assert точки[0]["series"] == "RUS.CPI._T.IX.M"
    assert (точки[0]["currency"], точки[1]["currency"]) == ("RUB", "EUR")


def test_месяц():
    assert L.месяц("2025-M06") == date(2025, 6, 1)
    assert L.месяц("2025-06") == date(2025, 6, 1)
    assert L.месяц("2025-M13") is None
    assert L.месяц("") is None


def test_раскладка_и_гейты():
    точки = L.разобрать(ОТВЕТ)
    было = {("RUS", date(2025, 1, 1)): (250.5, "2010A"),
            ("RUS", date(2024, 12, 1)): (249.0, "2010A"),
            ("ITA", date(2025, 12, 1)): (100.0, "2015A")}
    сч = L.сравнить(точки, было)
    assert сч["без изменений"] == 1 and сч["пересмотрено"] == 1
    assert сч["в базе, нет в ответе (не трогаются)"] == 1
    провал = L.гейты(точки, сч, len(было), все_страны=True)
    assert any("меньше" in п for п in провал)
    assert any("CHN" in п and "DEU" in п for п in провал)
    assert any("смену методики" in п for п in провал)


def test_адрес_без_ключа_и_помесячно():
    assert L.АДРЕС.startswith("https://api.imf.org/external/sdmx/2.1/data/CPI/")
    assert L.АДРЕС.endswith(".CPI._T.IX.M")
    assert "key" not in L.АДРЕС.lower()


# ───────────────────────────────────────── досчёт дат
def test_части_смежные_без_пустых_и_без_потерь():
    номера = list(range(1, 24)) + [5, 7]
    куски = b.части(номера, 10)
    assert all(куски)
    assert sum(куски, []) == sorted(set(номера))
    assert b.части([], 10) == []
    assert b.части([3], 10) == [[3]]


def test_чтение_карточек_пачками_без_подсчёта_total():
    вызовы = []

    def bx(method, params):
        вызовы.append((method, params))
        return {"result": {"items": [{"id": н, "createdTime": "2025-01-10T10:00:00+03:00"}
                                     for н in params["filter"]["@id"] if н % 2]}}
    карточки = b.прочитать_карточки(list(range(1, 121)), bx)
    assert len(вызовы) == 3
    assert all(p["start"] == -1 and len(p["filter"]["@id"]) <= 50 for _m, p in вызовы)
    assert all(p["select"] == ["id", "createdTime"] for _m, p in вызовы)
    assert len(карточки) == 60


def test_разбор_части_и_гейт_чтения():
    строк = {1: 3, 2: 1, 3: 5}
    карточки = {1: "2025-01-10T10:00:00+03:00", 2: "2031-01-01T00:00:00+03:00", 3: None}
    д, сч, провал = b.разобрать_часть([1, 2, 3], строк, карточки, date(2026, 9, 24))
    assert д == {1: date(2025, 1, 10)}, "дата из будущего и пустая не пишутся"
    assert сч["строк получат дату"] == 3 and сч["строк останутся без даты"] == 6
    assert сч["строк станет хуже"] == 0
    assert not провал
    _д, _сч, провал = b.разобрать_часть([1, 2, 3], строк, {1: "2025-01-10"}, date(2026, 9, 24))
    assert провал, "портал отдал треть карточек — это сбой чтения, писать нельзя"


def test_досчёт_не_читает_портал_смещением():
    код = (ROOT / "library/quote_dates_backfill.py").read_text(encoding="utf-8")
    assert '"start": -1' in код
    assert "min_interval" not in код and "sleep(" not in код, "частота — только из бюджета"
