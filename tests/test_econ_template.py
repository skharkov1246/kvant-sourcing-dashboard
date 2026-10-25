"""Шаблон «Экономика проекта» (scripts/econ_template.py) на придуманных книгах.

  · «Наше КП» и переторжка: продажа, закупка, наценка над поставщиком;
  · заголовок не тот — «шаблон другой», числа нет;
  · «нет курса» и ошибка в позиции — без числа;
  · сохранённых значений нет (формулы без кэша) — «нет сохранённых значений»;
  · разные валюты поставщиков: R2 не читается, закупка — в евро;
  · проверка 1 (итоги ↔ строки) и проверка 2 (правдоподобие);
  · курсы файла для сверки с суммой сделки.

Корпус придуман (CLAUDE.md, правило 18): ни одной позиции, суммы и подписи из
настоящих файлов, кроме подписей шаблона, замеренных зондом v56.
"""
from __future__ import annotations

import io
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import econ_template as et  # noqa: E402

openpyxl = pytest.importorskip("openpyxl")


def книга(позиции: list[dict], *, валюта_подачи="RUB", курс_подачи=100.0, переторжка=False,
          итоги: dict | None = None, заголовки: dict | None = None, курсы=None, без_кэша=False) -> bytes:
    """Придуманная книга по шаблону. Позиция: G (кол-во), P (валюта оффера), Q (цена в
    валюте поставщика), курс (единиц валюты за 1 €), Z (коэф.); для переторжки — AK
    (скидка) или AL (новый коэф). Значения пишутся числами — как кэш Excel; без_кэша —
    итоги формулами без сохранённого значения (файл сохранён без пересчёта)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Расчёт"
    ws["D4"] = валюта_подачи
    for i, (код, курс) in enumerate((курсы or {"EUR": 1.0, "RUB": курс_подачи, "USD": 0.9}).items()):
        ws[f"K{5 + i}"], ws[f"L{5 + i}"] = код, курс
        ws[f"Z{5 + i}"], ws[f"AA{5 + i}"] = код, курс
    for кол, текст in et.РАЗДЕЛЫ.items():
        ws[f"{кол}11"] = текст
    for кол, текст in {**et.ЗАГОЛОВКИ, **(заголовки or {})}.items():
        ws[f"{кол}12"] = текст
    sum_t = sum_ad = sum_ag = sum_r = sum_an = 0.0
    for i, p in enumerate(позиции):
        r = 13 + i
        g, q, курс, z = p["G"], p["Q"], p.get("курс", 1.0), p.get("Z", 1.5)
        s = round(q / курс, 4)
        t = g * s
        ac = math.ceil(s * z)
        ad = ac * g
        af = round(ac * курс_подачи, 2)
        ag = af * g
        ws[f"G{r}"], ws[f"P{r}"], ws[f"Q{r}"] = g, p.get("P", "EUR"), q
        ws[f"R{r}"], ws[f"S{r}"], ws[f"T{r}"], ws[f"W{r}"] = q * g, s, t, "FCA"
        ws[f"Z{r}"], ws[f"AC{r}"], ws[f"AD{r}"] = z, ac, ad
        ws[f"AE{r}"], ws[f"AF{r}"], ws[f"AG{r}"] = валюта_подачи, af, ag
        sum_t, sum_ad, sum_ag, sum_r = sum_t + t, sum_ad + ad, sum_ag + ag, sum_r + q * g
        if переторжка and ("AK" in p or "AL" in p):
            ws[f"AK{r}"], ws[f"AL{r}"] = p.get("AK"), p.get("AL")
            am = math.ceil(ac - ac * p["AK"]) if p.get("AK") is not None else math.ceil(p["AL"] * s)
        else:
            am = math.ceil(0 * s)           # AK и AL пусты: AM = ОКРВВЕРХ(AL·S) = 0
        ws[f"AM{r}"], ws[f"AN{r}"] = am, am * g
        ws[f"AP{r}"] = round(am * курс_подачи, 2)
        sum_an += am * g
        for кол, v in (p.get("ячейки") or {}).items():
            ws[f"{кол}{r}"] = v
    if без_кэша:
        ws["R1"], ws["AG1"], ws["AG2"] = "=SUM(T13:T1007)", "=SUM(AD13:AD1007)", "=SUM(AG13:AG1007)"
    else:
        ws["R1"], ws["AG1"], ws["AG2"] = sum_t, sum_ad, sum_ag
    ws["R2"] = sum_r                       # смесь валют поставщиков — читать нельзя
    for адрес, v in (итоги or {}).items():
        ws[адрес] = v
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ПОЗИЦИИ = [{"G": 2, "P": "EUR", "Q": 1000.0, "Z": 1.4}, {"G": 5, "P": "EUR", "Q": 200.0, "Z": 1.6},
           {"G": 1, "P": "EUR", "Q": 3000.0, "Z": 1.5}]


def test_наше_кп_продажа_закупка_и_наценка_над_поставщиком():
    и = et.разобрать(книга(ПОЗИЦИИ))
    assert и.status == et.СВЕРЕНО and и.сверено and и.rebid is False
    assert и.purchase_eur == 6000.0                     # 2·1000 + 5·200 + 1·3000
    assert и.sale_eur == 2 * 1400 + 5 * 320 + 4500      # ОКРВВЕРХ(S·Z)·G
    assert и.sale_sub == pytest.approx(и.sale_eur * 100)
    assert и.markup == pytest.approx(и.sale_eur / 6000 - 1)
    assert и.items == 3 and и.sub_cur == "RUB" and и.coef_median == 1.5
    assert и.totals_dev <= et.ДОПУСК_ИТОГА
    # курсы файла: евро — по итогам самого файла, доллар — по таблице подачи
    assert и.fx["RUB"] == 1.0 and и.fx["EUR"] == pytest.approx(100.0)
    assert и.fx["USD"] == pytest.approx(100 / 0.9, rel=1e-6)


def test_переторжка_берёт_продажу_переторжки():
    поз = [dict(ПОЗИЦИИ[0], AK=0.1), dict(ПОЗИЦИИ[1], AL=1.3), dict(ПОЗИЦИИ[2], AK=0.05)]
    кп = et.разобрать(книга(ПОЗИЦИИ))
    и = et.разобрать(книга(поз, переторжка=True))
    assert и.сверено and и.rebid is True
    am = [math.ceil(1400 - 140), math.ceil(1.3 * 200), math.ceil(4500 - 225)]
    assert и.sale_eur == 2 * am[0] + 5 * am[1] + am[2]
    assert и.sale_eur < кп.sale_eur, "переторжка — скидка к «Нашему КП»"
    assert и.sale_sub == pytest.approx(sum(g * a * 100 for g, a in zip((2, 5, 1), am)))
    assert и.purchase_eur == кп.purchase_eur
    assert и.markup == pytest.approx(и.sale_eur / и.purchase_eur - 1)


def test_переторжка_не_у_всех_позиций_без_числа():
    поз = [dict(ПОЗИЦИИ[0], AK=0.1), ПОЗИЦИИ[1], ПОЗИЦИИ[2]]
    и = et.разобрать(книга(поз, переторжка=True))
    assert и.status == et.ПЕРЕТОРЖКА_НЕПОЛНАЯ and и.sale_eur is None and и.markup is None


@pytest.mark.parametrize("ячейка,текст", [("G12", "Количество"), ("AN12", "Итого"), ("Z11", "КП"),
                                          ("AK11", "Наше КП")])
def test_заголовок_не_тот_шаблон_другой(ячейка, текст):
    кол = ячейка.rstrip("0123456789")
    и = (et.разобрать(книга(ПОЗИЦИИ, заголовки={кол: текст})) if ячейка.endswith("12")
         else _с_разделом(кол, текст))
    assert и.status == et.ШАБЛОН_ДРУГОЙ and и.why == ячейка
    assert и.sale_eur is None and и.purchase_eur is None and и.markup is None and и.fx == {}


def _с_разделом(кол: str, текст: str) -> et.Итог:
    wb = openpyxl.load_workbook(io.BytesIO(книга(ПОЗИЦИИ)))
    wb.active[f"{кол}11"] = текст
    buf = io.BytesIO()
    wb.save(buf)
    return et.разобрать(buf.getvalue())


def test_подписи_без_регистра_и_лишних_пробелов():
    и = et.разобрать(книга(ПОЗИЦИИ, заголовки={"G": "  кол-во ", "Z": "Коэф."}))
    assert и.сверено


@pytest.mark.parametrize("значение", ["нет курса", "#VALUE!", "#ДЕЛ/0!"])
def test_нет_курса_или_ошибка_в_позиции(значение):
    поз = [ПОЗИЦИИ[0], dict(ПОЗИЦИИ[1], ячейки={"S": значение, "T": значение}), ПОЗИЦИИ[2]]
    и = et.разобрать(книга(поз))
    assert и.status == et.НЕТ_КУРСА and и.bad_lines == 1
    assert и.sale_eur is None and и.markup is None


def test_ошибка_в_итоге_файла():
    и = et.разобрать(книга(ПОЗИЦИИ, итоги={"AG1": "#REF!"}))
    assert и.status == et.НЕТ_КУРСА and и.why == "AG1"


def test_нет_сохранённых_значений():
    и = et.разобрать(книга(ПОЗИЦИИ, без_кэша=True))
    assert и.status == et.НЕТ_ЗНАЧЕНИЙ and и.why == "R1" and и.sale_eur is None
    # итоги есть, а у позиции формула без кэша
    wb = openpyxl.load_workbook(io.BytesIO(книга(ПОЗИЦИИ)))
    wb.active["AD14"] = "=AC14*G14"
    buf = io.BytesIO()
    wb.save(buf)
    assert et.разобрать(buf.getvalue()).status == et.НЕТ_ЗНАЧЕНИЙ


def test_разные_валюты_поставщиков_R2_не_читается():
    поз = [{"G": 2, "P": "USD", "Q": 900.0, "курс": 0.9, "Z": 1.5},       # 1000 €
           {"G": 1, "P": "CNY", "Q": 7800.0, "курс": 7.8, "Z": 1.5},      # 1000 €
           {"G": 3, "P": "EUR", "Q": 500.0, "Z": 1.5}]
    b = книга(поз, курсы={"EUR": 1.0, "RUB": 100.0, "USD": 0.9, "CNY": 7.8})
    и = et.разобрать(b)
    assert и.сверено and и.purchase_eur == pytest.approx(2 * 1000 + 1000 + 1500)
    r2 = openpyxl.load_workbook(io.BytesIO(b), data_only=True).active["R2"].value
    assert r2 == 2 * 900 + 7800 + 3 * 500 and r2 != и.purchase_eur, "R2 складывает доллары с юанями"
    assert и.fx["CNY"] == pytest.approx(100 / 7.8, rel=1e-6)


def test_проверка_1_итог_не_сходится_со_строками():
    и = et.разобрать(книга(ПОЗИЦИИ, итоги={"R1": 6000.0 * 1.01}))
    assert и.status == et.ИТОГИ_НЕ_СХОДЯТСЯ and и.totals_dev == pytest.approx(0.01 / 1.01, rel=1e-3)
    assert et.разобрать(книга(ПОЗИЦИИ, итоги={"R1": 6000.0 * 1.004})).сверено, "в пределах 0,5 %"
    # итог строки T вписан руками мимо G·S: сумма T сходится с R1, сомножители — нет
    поз = [dict(ПОЗИЦИИ[0], ячейки={"T": 2600.0}), ПОЗИЦИИ[1], ПОЗИЦИИ[2]]
    и = et.разобрать(книга(поз, итоги={"R1": 6600.0}))
    assert и.status == et.ИТОГИ_НЕ_СХОДЯТСЯ, "второй способ — по сомножителям"
    # позиция вне строк с количеством, но в диапазоне итога
    wb = openpyxl.load_workbook(io.BytesIO(книга(ПОЗИЦИИ)))
    wb.active["T40"] = 500.0
    wb.active["R1"] = 6500.0
    buf = io.BytesIO()
    wb.save(buf)
    assert et.разобрать(buf.getvalue()).status == et.ИТОГИ_НЕ_СХОДЯТСЯ


def test_проверка_2_продажа_ниже_закупки_и_коэффициент():
    assert et.разобрать(книга([dict(p, Z=0.8) for p in ПОЗИЦИИ])).status == et.НЕПРАВДОПОДОБНО
    assert et.разобрать(книга([dict(p, Z=6.0) for p in ПОЗИЦИИ])).status == et.НЕПРАВДОПОДОБНО
    # медиана 1.5 при одной позиции с 0.9 — правдоподобно, но продажа всё равно ≥ закупки
    и = et.разобрать(книга([dict(ПОЗИЦИИ[0], Z=0.9), ПОЗИЦИИ[1], ПОЗИЦИИ[2]]))
    assert и.сверено and и.coef_median == 1.5


def test_нет_позиций_и_валюты():
    assert et.разобрать(книга([])).status == et.НЕТ_СТРОК
    и = et.разобрать(книга(ПОЗИЦИИ, валюта_подачи="тугрики"))
    assert и.status == et.НЕТ_ВАЛЮТЫ and и.why == "D4"
    assert et.разобрать(книга(ПОЗИЦИИ, валюта_подачи="евро")).sub_cur == "EUR"


def test_не_xlsx_не_открылся():
    assert et.разобрать(b"not a workbook").status == et.НЕ_ОТКРЫЛСЯ


def test_подача_в_евро_курсы_по_строкам_оффера():
    поз = [{"G": 1, "P": "USD", "Q": 900.0, "курс": 0.9, "Z": 1.5}, {"G": 1, "P": "EUR", "Q": 100.0, "Z": 1.5}]
    и = et.разобрать(книга(поз, валюта_подачи="EUR", курс_подачи=1.0))
    assert и.сверено and и.fx["EUR"] == 1.0
    assert и.fx["USD"] == pytest.approx(1 / 0.9, rel=1e-4), "таблица в евро стороны не скажет — курс строк"


def test_таблица_курсов_в_обратную_сторону_и_чужая():
    # таблица «€ за единицу»: RUB 0.01 — сторона узнаётся по строке валюты подачи
    fx = et.курсы_в_подаче("RUB", 100.0, {"RUB": 0.01, "USD": 1.1}, {})
    assert fx["USD"] == pytest.approx(110.0)
    # таблица не сходится с итогами файла ни так ни так — не берётся
    fx = et.курсы_в_подаче("RUB", 100.0, {"RUB": 80.0, "USD": 1.1}, {})
    assert "USD" not in fx and fx == {"RUB": 1.0, "EUR": 100.0}


def test_валюты():
    assert [et.валюта(x) for x in ("RUB", "руб.", "Евро", " usd ", "CNY", "€", "тугрики", None, 5)] == \
        ["RUB", "RUB", "EUR", "USD", "CNY", "EUR", None, None, None]


def test_статусы_закрытым_списком():
    assert len(set(et.СТАТУСЫ_ФАЙЛА)) == len(et.СТАТУСЫ_ФАЙЛА) and et.СВЕРЕНО in et.СТАТУСЫ_ФАЙЛА
