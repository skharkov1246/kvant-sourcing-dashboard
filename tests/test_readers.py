"""Читатели форматов, у которых своей ветки разбора не было.

Замер 23.09.2026 по всей базе: из 31 869 файлов 9 527 (29,9 %) не дали ни одной
позиции. Два разряда потеряны целиком, потому что читателя у них нет вовсе —
«прочее» (410 файлов) и «архив» (169), — и ещё 375 файлов «старого office»
упали в xlrd, потому что это был не .xls.

Корпуса собраны здесь же, а не сняты с базы (CLAUDE.md, правило 18).
"""
from __future__ import annotations

import io
import zipfile

from library import readers


def test_csv_с_разделителем_внутри_кавычек_не_рвёт_позицию():
    """split разорвал бы «Насос ЦНС 38-176; с рамой» надвое — это две позиции
    вместо одной, и обе неверные."""
    b = ('Наименование;Кол-во;Цена\r\n'
         '"Насос ЦНС 38-176; с рамой";2;1500\r\n'
         'Задвижка 30с41нж;4;250\r\n').encode("cp1251")
    rows = readers.rows_from_text(b)
    assert rows[0] == ["Наименование", "Кол-во", "Цена"]
    assert rows[1] == ["Насос ЦНС 38-176; с рамой", "2", "1500"], rows[1]
    assert len(rows) == 3


def test_запятая_в_размере_не_становится_разделителем():
    """csv.Sniffer на русских файлах видит запятую в «1,5 мм» и ошибается.

    Разделитель берётся тот, что встречается в строках ОДИНАКОВОЕ число раз:
    у таблицы их поровну в каждой строке, у чисел с запятой — как придётся.
    """
    b = ('Наименование;Толщина;Кол-во\n'
         'Лист стальной;1,5 мм;10\n'
         'Прокладка;0,8 мм;4\n').encode()
    rows = readers.rows_from_text(b)
    assert rows[1] == ["Лист стальной", "1,5 мм", "10"], rows[1]


def test_битые_кавычки_дают_строки_а_не_ноль():
    """Одна колонка хуже пяти, но лучше нуля: файл не теряется целиком."""
    b = b'"\xd0\xbd\xd0\xb5\xd0\xb7\xd0\xb0\xd0\xba\xd1\x80\xd1\x8b\xd1\x82\xd0\xb0\xd1\x8f\n' * 3
    rows = readers.rows_from_text(b)
    assert rows, "файл с битыми кавычками потерян целиком"


def test_выгрузка_1с_читается_и_колонки_не_съезжают():
    """Пустая ячейка задана атрибутом ss:Index, а не самой ячейкой.

    Без восстановления пропуска колонки съезжают влево, и цена оказывается под
    заголовком количества — то есть в базу ложится неверное число.
    """
    b = '''<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
          xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="TDSheet"><Table>
  <Row><Cell><Data ss:Type="String">Наименование</Data></Cell>
       <Cell><Data ss:Type="String">Кол-во</Data></Cell>
       <Cell><Data ss:Type="String">Цена</Data></Cell></Row>
  <Row><Cell><Data ss:Type="String">Насос ЦНС-38</Data></Cell>
       <Cell ss:Index="3"><Data ss:Type="Number">1500</Data></Cell></Row>
 </Table></Worksheet></Workbook>'''.encode()
    rows = readers.rows_from_spreadsheetml(b)
    assert rows[0] == ["Наименование", "Кол-во", "Цена"]
    assert rows[1] == ["Насос ЦНС-38", "", "1500"], rows[1]


def test_rtf_разворачивает_кириллицу_и_снимает_разметку():
    b = (rb"{\rtf1\ansi\ansicpg1251\deff0{\fonttbl{\f0\fnil Arial;}}"
         rb"\f0\fs20 \'cd\'e0\'f1\'ee\'f1 \'d6\'cd\'d1-38\par "
         rb"\'c7\'e0\'e4\'e2\'e8\'e6\'ea\'e0\par}")
    текст = readers.text_from_rtf(b)
    assert "Насос ЦНС-38" in текст, текст
    assert "Задвижка" in текст, текст
    assert "fonttbl" not in текст and "\\f0" not in текст, "разметка просочилась"


def test_html_снимает_скрипты_вместе_с_содержимым():
    """Иначе тело скрипта станет «позициями номенклатуры»."""
    b = (b"<html><head><style>.a{color:red}</style>"
         b"<script>var x = 'kupit nasos';</script></head>"
         b"<body><table><tr><td>\xd0\x9d\xd0\xb0\xd1\x81\xd0\xbe\xd1\x81</td>"
         b"<td>2</td></tr></table></body></html>")
    текст = readers.text_from_html(b)
    assert "Насос" in текст
    assert "kupit nasos" not in текст and "color:red" not in текст, текст


def test_архив_отдаёт_участников_и_не_лезет_глубже():
    """Архив в архиве не берётся: настоящие случаи закрываются одним уровнем."""
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w") as z:
        z.writestr("spec.csv", "Наименование;Кол-во\nНасос;2\n")
        z.writestr("pismo.txt", "Здравствуйте")
        z.writestr("foto.jpg", "\x89PNG")          # не в списке участников
        z.writestr("vlozhenie.zip", "PK\x03\x04")  # архив в архиве — мимо
    участники = readers.участники_архива(буфер.getvalue())
    имена = [и for и, _ in участники]
    assert "spec.csv" in имена and "pismo.txt" in имена
    assert "foto.jpg" not in имена and "vlozhenie.zip" not in имена, имена


def test_не_архив_не_роняет_читателя():
    assert readers.участники_архива(b"PK\x03\x04 \xff\xfe not a zip") == []
    assert readers.участники_архива(b"") == []


def test_старый_doc_называет_причину_а_не_молчит(monkeypatch):
    """Читатель внешний, и его может не быть в системе.

    Молчаливое «пусто» на 375 файлах — ровно то, из-за чего .doc не читались.
    Потеря обязана быть названа: по «нет antiword» видно, что ставить.
    """
    monkeypatch.setattr(readers.shutil, "which", lambda имя: None)
    текст, причина = readers.text_from_doc(b"\xd0\xcf\x11\xe0 doc")
    assert текст == "" and причина == "нет ни antiword, ни catdoc", причина


def test_старый_doc_читается_когда_читатель_есть(monkeypatch):
    class Готово:
        returncode = 0
        stdout = "Насос ЦНС-38    2 шт.\n".encode()

    monkeypatch.setattr(readers.shutil, "which", lambda имя: "/usr/bin/" + имя)
    monkeypatch.setattr(readers.subprocess, "run", lambda *a, **k: Готово())
    текст, причина = readers.text_from_doc(b"\xd0\xcf\x11\xe0 doc")
    assert "Насос ЦНС-38" in текст and причина == ""


def test_внешние_читатели_ставятся_прогоном():
    """Читатель .doc внешний, и без пакета он честно говорит «нет antiword» —
    то есть файл всё равно теряется. Замер: 375 таких файлов.

    Проверка держит связь «код зовёт внешнюю программу — прогон её ставит».
    Без неё правка выглядит сделанной, а в прогоне не работает ни на одном файле.
    """
    import re
    from pathlib import Path
    сырой = (Path(__file__).resolve().parent.parent
             / ".github/workflows/library-index.yml").read_text(encoding="utf-8")
    # РАЗБОР ЧИТАЕТ КОД, А НЕ КОММЕНТАРИИ (CLAUDE.md, стиль работы). Первая
    # редакция этой проверки искала «antiword» по всему файлу и ПЕРЕЖИЛА мутацию
    # «убрать antiword из установки»: слово нашлось в пояснении над шагом.
    yml = re.sub(r"(?m)^\s*#[^\n]*$", "", сырой)
    шаг = yml[yml.index("Установка читателей форматов"):]
    шаг = шаг[:шаг.index("- name:", 10)]
    ставятся = re.search(r"apt-get install[^\n]*", шаг)
    assert ставятся, "в шаге нет установки пакетов"
    for имя, _ключи in readers.ДОК_ЧИТАТЕЛИ:
        assert имя in ставятся.group(0), \
            f"{имя} зовётся из кода, но не стоит в команде установки"
    assert re.search(r"if:\s*\$\{\{\s*!inputs\.ocr\s*\}\}", шаг), \
        "шаг стоит под условием распознавания — для разбора читателей не будет"
