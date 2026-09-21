"""Разбор таможенной выгрузки: колонки у каждой базы свои, формат — какой прислали.

Корпуса придуманы (правило 18 CLAUDE.md): ни одной строки из живой базы.
"""
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "zip" / "customs"))

import analyze_export as ae  # noqa: E402

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _xlsx(path: Path, head: list[str], rows: list[list[str]]) -> None:
    """Минимальный XLSX: общие строки плюс лист. Читателю большего не нужно."""
    shared = []
    for cell in head + [c for r in rows for c in r]:
        if cell not in shared:
            shared.append(cell)
    si = "".join(f"<si><t>{v}</t></si>" for v in shared)
    body = ""
    for n, row in enumerate([head] + rows, start=1):
        cells = "".join(
            f'<c r="{chr(65 + i)}{n}" t="s"><v>{shared.index(v)}</v></c>'
            for i, v in enumerate(row))
        body += f'<row r="{n}">{cells}</row>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}">{si}</sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{NS}"><sheetData>{body}</sheetData></worksheet>')


def test_колонки_разных_баз_сводятся_к_одним_именам():
    rows = ae.normalize([{
        "Дата декларации": "2024-05-06", "Получатель": 'ООО "РОМАШКА"', "ИНН": "1234567890",
        "Отправитель": "SAMPLE MACHINERY CO", "Страна происхождения": "CN",
        "Код ТН ВЭД": "8413708100", "Вес нетто, кг": "1 250,5", "Фактурная стоимость": "12 000,00",
    }])
    assert rows[0]["recipient"] == 'ООО "РОМАШКА"'
    assert rows[0]["sender"] == "SAMPLE MACHINERY CO"
    assert rows[0]["hs"] == "8413708100"
    assert ae.num(rows[0]["net"]) == 1250.5


def test_xlsx_читается_без_внешних_библиотек(tmp_path):
    f = tmp_path / "выгрузка.xlsx"
    _xlsx(f, ["Дата", "Получатель", "ИНН", "Отправитель", "Код ТН ВЭД", "Вес нетто"],
          [["2025-02-03", 'ООО "РОМАШКА"', "1234567890", "SAMPLE MACHINERY CO", "8413708100", "900"],
           ["2025-07-11", 'ООО "ЛЮТИК"', "9876543210", "OTHER TRADING LTD", "8483409000", "40"]])
    rows = ae.normalize(ae.read_xlsx(f))
    assert len(rows) == 2
    assert rows[1]["sender"] == "OTHER TRADING LTD"


def test_фильтр_по_инн_отсекает_однофамильца(tmp_path):
    """Однофамильцы — обычное дело: поиска по ИНН в API базы нет, отсев наш."""
    f = tmp_path / "выгрузка.xlsx"
    _xlsx(f, ["Дата", "Получатель", "ИНН", "Отправитель", "Код ТН ВЭД", "Вес нетто"],
          [["2025-02-03", 'ООО "РОМАШКА"', "1234567890", "SAMPLE MACHINERY CO", "8413708100", "900"],
           ["2025-03-04", 'ООО "РОМАШКА"', "5555555555", "TOY FACTORY LTD", "9503004900", "70"]])
    out = subprocess.run(
        [sys.executable, str(ROOT / "zip" / "customs" / "analyze_export.py"), str(f), "--inn", "1234567890"],
        capture_output=True, text=True, check=True).stdout
    assert "строк после фильтра: 1" in out
    assert "SAMPLE MACHINERY CO" in out
    assert "TOY FACTORY LTD" not in out
    assert "готовые насосы 8413: 100.0 %" in out


# Заголовки, как их отдаёт выгрузка ГТД: код графы плюс расшифровка в скобках.
GTD_HEAD = [
    "ND (Номер декларации)", "G072 (Дата регистрации)", "G021 (ИНН отправителя)",
    "G022 (Наименование отправителя)", "G081 (ИНН получателя)", "G082 (Наименование получателя)",
    "G083 (Адрес получателя)", "G12 (Общая таможенная стоимость по ГТД)", "G16 (Страна происхождения)",
    "G33 (Код товара)", "G38 (Вес нетто)", "G42 (Фактурная стоимость)", "G31_11 (Изготовитель)",
]


def test_инн_берётся_у_получателя_а_не_у_отправителя():
    """В ГТД «ИНН отправителя» стоит раньше «ИНН получателя» — и забирал поле себе."""
    rec = ae.normalize([dict(zip(GTD_HEAD, [
        "10000000/010125/3000001", "01.02.2025", "7700000001", "SAMPLE MACHINERY CO",
        "1234567890", 'ООО "РОМАШКА"', "г. Москва", "9 000 000,00", "ГЕРМАНИЯ",
        "8413708100", "900", "12 000,00", "SAMPLE MACHINERY CO"]))])[0]
    assert rec["inn"] == "1234567890"
    assert rec["sender"] == "SAMPLE MACHINERY CO"
    assert rec["recipient"] == 'ООО "РОМАШКА"'
    assert rec["country"] == "ГЕРМАНИЯ"
    assert ae.num(rec["value"]) == 12000.0      # фактурная, а не общая таможенная


def test_границы_периода_считаются_по_дате_а_не_по_строке():
    """«20.07.2022» лексически больше «03.11.2022» — сортировка строк врёт на месяцах."""
    assert ae.as_date("03.11.2022") > ae.as_date("20.07.2022")
    assert ae.as_date("2022-11-03") == ("2022", "11", "03")
    assert ae.as_date("без даты") is None


def test_выгрузка_гтд_разбирается_целиком(tmp_path):
    f = tmp_path / "гтд.xlsx"
    _xlsx(f, GTD_HEAD, [
        ["10000000/010125/3000001", "05.05.2025", "7700000001", "SAMPLE MACHINERY CO",
         "1234567890", 'ООО "РОМАШКА"', "г. Москва", "9 000 000,00", "ГЕРМАНИЯ",
         "8413708100", "900", "12 000,00", "SAMPLE MACHINERY CO"],
        ["10000000/020125/3000002", "26.10.2025", "7700000002", "OTHER TRADING LTD",
         "1234567890", 'ООО "РОМАШКА"', "г. Москва", "1 000 000,00", "КИТАЙ",
         "8483409000", "40", "500,00", "OTHER TRADING LTD"],
    ])
    out = subprocess.run(
        [sys.executable, str(ROOT / "zip" / "customs" / "analyze_export.py"), str(f), "--inn", "1234567890"],
        capture_output=True, text=True, check=True).stdout
    assert "строк после фильтра: 2" in out
    assert "период: 05.05.2025 … 26.10.2025" in out
    assert "2025-Q2 — 1" in out and "2025-Q4 — 1" in out
    assert "ГЕРМАНИЯ — 1" in out and "КИТАЙ — 1" in out
    assert "готовые насосы 8413: 50.0 %" in out
