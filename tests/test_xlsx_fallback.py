"""Книга, которую придирчивый читатель не открыл, ещё не потеряна.

Замер 18.09.2026 по описи вложений сделок: продажная сторона заявки ЛУКОЙЛ —
восемнадцать файлов примерно по 5 КБ в поле «Result, ТКП». Они скачались без
единой ошибки и НЕ ОТКРЫЛИСЬ обычным путём, из-за чего отчёт год отвечал
«продажной стороны у нас нет». Пока они не читаются, ни сопоставления продажи с
закупкой, ни маржи по заявке не существует.

Обычный читатель книг придирчив к устройству файла: ему нужны описание книги,
связи листов и общий словарь строк на своих местах. Выгрузка учётной системы
кладёт их иначе. Запасной путь берёт данные прямо из разметки.

Корпус здесь придуман и собирается кодом теста — из базы ничего не копируется.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def book(rows, shared=("Наименование", "Цена, USD", "Прокладка")) -> bytes:
    """Собирает архив с разметкой книги, но БЕЗ описания книги и связей:
    ровно то устройство, на котором спотыкается обычный читатель."""
    sheet = [f'<worksheet xmlns="{NS}"><sheetData>']
    for i, cells in enumerate(rows, 1):
        sheet.append(f'<row r="{i}">')
        for c in cells:
            if isinstance(c, int):
                sheet.append(f'<c t="s"><v>{c}</v></c>')
            elif c is None:
                sheet.append("<c/>")
            else:
                sheet.append(f"<c><v>{c}</v></c>")
        sheet.append("</row>")
    sheet.append("</sheetData></worksheet>")

    ss = [f'<sst xmlns="{NS}">']
    for t in shared:
        ss.append(f"<si><t>{t}</t></si>")
    ss.append("</sst>")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", "".join(sheet))
        z.writestr("xl/sharedStrings.xml", "".join(ss))
    return buf.getvalue()


def test_прямое_чтение_достаёт_строки_и_словарь():
    import bitrix_tkp as bt

    data = book([[0, 1], [2, "12.50"]])
    rows = bt.rows_from_xlsx_raw(data)
    assert [r[1] for r in rows] == [1, 2]
    assert rows[0][2] == ["Наименование", "Цена, USD"]
    assert rows[1][2] == ["Прокладка", "12.50"]


def test_пустые_строки_не_попадают():
    import bitrix_tkp as bt

    rows = bt.rows_from_xlsx_raw(book([[0], [None], [2]]))
    assert [r[2] for r in rows] == [["Наименование"], ["Прокладка"]]


def test_разбор_переключается_на_запасной_путь_сам():
    """Главное: отказ обычного читателя больше не означает потерю файла."""
    import bitrix_tkp as bt

    rows, how = bt.parse("result.xlsx", book([[0, 1], [2, "12.50"]]))
    assert rows, "файл снова потерян — запасной путь не сработал"
    assert "прямым чтением" in how, how


def test_битый_архив_честно_отказывает():
    import bitrix_tkp as bt

    rows, how = bt.parse("result.xlsx", b"PK\x03\x04" + "мусор".encode("utf-8"))
    assert not rows
    assert "не разобрался" in how or "не открыл" in how


def test_книга_без_листов_не_объявляется_разобранной():
    """Статус не должен врать: пустые листы — это не «разобрано»."""
    import bitrix_tkp as bt

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{NS}"></sst>')
    rows, how = bt.parse("result.xlsx", buf.getvalue())
    assert not rows
    assert "пуст" in how or "не разобрался" in how, how
