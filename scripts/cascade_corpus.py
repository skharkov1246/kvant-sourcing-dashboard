#!/usr/bin/env python3
"""Каскад чтения против прежней таблицы стратегий — на одном корпусе форматов.

ЗАЧЕМ. 23.09.2026 такое сравнение на двенадцати форматах одного придуманного КП
нашло три дефекта, которых не видели 2 400 тестов, — у каждого читателя свои
тесты были зелёными:

  • раскладка pdftotext ломает сборку таблицы PDF: шапка слиплась, цена встала из
    колонки «№», количество склеилось в семизначное число — а ворота шапки такую
    таблицу пропустили;
  • RTF от LibreOffice читался «Н?а?с?о?с?»: запасной знак после знака Юникода
    не пропускался, а переводы строк исходника рассыпали таблицу по ячейке на строку;
  • починка кодировки «исправляла» такой текст в псевдографику и считала это
    улучшением.

Свои тесты читателя проверяют то, что автор читателя знал. Сравнение с прежним
путём на общем корпусе проверяет то, чего не знал никто.

КОРПУС ПРИДУМАН (CLAUDE.md, правило 18): одно КП в HTML, разложенное LibreOffice
по форматам, плюс письмо, zip и 7z поверх. Нужны soffice и (для 7z) 7z; без них
соответствующие форматы пропускаются с названной причиной.

Что считается провалом (код выхода 1):
  • у каскада цен меньше, чем у прежнего пути, на любом формате;
  • у каскада нет цен там, где их ждут (все форматы, кроме RTF-текстом у прежнего);
  • цена или количество неправдоподобны: цена меньше 10 при «Цена, руб.» от 350,
    количество больше тысячи при количествах 2–10 в корпусе.

Запуск:  python scripts/cascade_corpus.py [--keep каталог]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(КОРЕНЬ / "library"))

КП = """<html><head><meta charset="utf-8"></head><body>
<p>ООО «Техснаб-Пример». Кому: ООО «КВАНТ». Коммерческое предложение № 45</p>
<table border="1">
<tr><td>№</td><td>Наименование</td><td>Кол-во</td><td>Ед.</td><td>Цена, руб.</td><td>Сумма, руб.</td></tr>
<tr><td>1</td><td>Насос ЦНС 38-176 с рамой</td><td>2</td><td>шт</td><td>150000</td><td>300000</td></tr>
<tr><td>2</td><td>Задвижка 30с41нж Ду100</td><td>4</td><td>шт</td><td>25000</td><td>100000</td></tr>
<tr><td>3</td><td>Подшипник 6208-2RS</td><td>10</td><td>шт</td><td>350</td><td>3500</td></tr>
<tr><td>4</td><td>Уплотнение торцевое</td><td>3</td><td>шт</td><td>12000</td><td>36000</td></tr>
</table>
<p>Условия поставки: DAP Москва. Оплата 30/70. Срок поставки 30 дней.</p>
<p>Директор ООО «Техснаб-Пример»</p></body></html>
"""
ЦЕНЫ = {150000.0, 25000.0, 350.0, 12000.0}
КОЛИЧЕСТВА = {2.0, 4.0, 10.0, 3.0}


def soffice(каталог: Path, вход: str, цель: str, фильтр: str = "") -> None:
    cmd = ["soffice", "--headless", f"-env:UserInstallation=file://{каталог}/.lo"]
    if фильтр:
        cmd.append(f"--infilter={фильтр}")
    cmd += ["--convert-to", цель, "--outdir", str(каталог), str(каталог / вход)]
    subprocess.run(cmd, capture_output=True, timeout=180, check=False)


def собрать(каталог: Path) -> list[str]:
    """Корпус в каталог. Возвращает пропущенные форматы с причиной."""
    (каталог / "kp.html").write_text(КП, encoding="utf-8")
    пропущено: list[str] = []
    if not shutil.which("soffice"):
        return ["все офисные форматы: нет soffice"]
    soffice(каталог, "kp.html", "odt")
    soffice(каталог, "kp.html", "pdf")
    for цель in ("xlsx", "ods", "xls"):
        soffice(каталог, "kp.html", цель, "HTML (StarCalc)")
    soffice(каталог, "kp.odt", "docx:MS Word 2007 XML")
    soffice(каталог, "kp.odt", "doc:MS Word 97")
    soffice(каталог, "kp.odt", "rtf:Rich Text Format")
    if (каталог / "kp.xlsx").exists():
        m = MIMEMultipart()
        m["From"], m["To"], m["Subject"] = "snab@primer.test", "z@primer-kvant.test", "КП"
        m.attach(MIMEText("Предложение во вложении. Условия поставки: DAP Москва.", "plain", "utf-8"))
        a = MIMEApplication((каталог / "kp.xlsx").read_bytes(), Name="kp.xlsx")
        a["Content-Disposition"] = 'attachment; filename="kp.xlsx"'
        m.attach(a)
        (каталог / "kp.eml").write_bytes(m.as_bytes())
    if (каталог / "kp.docx").exists():
        with zipfile.ZipFile(каталог / "kp.zip", "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("pismo.txt", "Здравствуйте, КП во вложении")
            z.write(каталог / "kp.docx", "kp.docx")
    if shutil.which("7z") and (каталог / "kp.pdf").exists():
        subprocess.run(["7z", "a", "-bd", str(каталог / "kp.7z"), str(каталог / "kp.pdf")],
                       capture_output=True, timeout=60, check=False)
    else:
        пропущено.append("7z: нет архиватора 7z")
    return пропущено


def прочитать(indexer, b: bytes, каскад: bool) -> dict:
    indexer.КАСКАД = каскад
    rec: dict = {"file_id": "корпус", "side": None}
    строки, текст, отказ = indexer.читать(b, indexer.подвид(b), rec)
    позиции = indexer.items_from_rows(строки) if строки else []
    цены = [float(п["_цена"]["price"]) for п in позиции if п.get("_цена")]
    кол = [float(п["qty"]) for п in позиции if п.get("qty") is not None]
    return {"строк": len(строки), "текст": len(текст), "цены": цены, "кол": кол,
            "отказ": отказ, "путь": rec.get("read_chain") or ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", help="оставить корпус в этом каталоге")
    арг = ap.parse_args()
    import indexer
    каталог = Path(арг.keep) if арг.keep else Path(tempfile.mkdtemp(prefix="korpus-"))
    каталог.mkdir(parents=True, exist_ok=True)
    try:
        пропущено = собрать(каталог)
        провалы: list[str] = []
        print(f"{'формат':6s} | {'прежний: строк/цен':18s} | {'каскад: строк/цен':17s} | путь каскада")
        for файл in sorted(каталог.glob("kp.*")):
            b = файл.read_bytes()
            стар, нов = прочитать(indexer, b, False), прочитать(indexer, b, True)
            ф = файл.suffix[1:]
            print(f"{ф:6s} | {стар['строк']:5d} / {len(стар['цены']):2d}        | "
                  f"{нов['строк']:5d} / {len(нов['цены']):2d}       | {нов['путь'][:60]}")
            if len(нов["цены"]) < len(стар["цены"]):
                провалы.append(f"{ф}: у каскада цен меньше ({len(нов['цены'])} против {len(стар['цены'])})")
            if not нов["цены"] and not нов["текст"]:
                провалы.append(f"{ф}: каскад не дал ни цен, ни текста ({нов['отказ'][:60]})")
            чужие = [ц for ц in нов["цены"] if ц not in ЦЕНЫ]
            if чужие:
                провалы.append(f"{ф}: неправдоподобные цены {чужие[:4]}")
            if [к for к in нов["кол"] if к not in КОЛИЧЕСТВА]:
                провалы.append(f"{ф}: неправдоподобные количества {нов['кол'][:4]}")
        for п in пропущено:
            print(f"  пропущено — {п}")
        if провалы:
            print("\nПРОВАЛЫ:")
            for п in провалы:
                print(f"  {п}")
            return 1
        print("\nкаскад не хуже прежнего пути ни на одном формате, цены и количества правдоподобны")
        return 0
    finally:
        if not арг.keep:
            shutil.rmtree(каталог, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("CASCADE", "")
    sys.exit(main())
