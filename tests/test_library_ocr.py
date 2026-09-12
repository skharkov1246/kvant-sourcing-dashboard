"""Распознавание сканов: статус и причина не должны врать.

История. Первая часть большого прогона дала 233 пустых файла из 400, и по этому
числу нельзя было понять ничего: любая неудача — таймаут, отсутствие tesseract,
битый файл — возвращала пустую строку, и файл получал тот же статус «пусто», что
настоящая фотография без надписей. Статус файла не должен врать (CLAUDE.md,
правило 15), поэтому причина считается отдельно и доезжает до lib_files.reason.

Картинки для теста рисуются здесь же, из базы ничего не берётся (правило 18).
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
есть_tesseract = shutil.which("tesseract") is not None
есть_pillow = importlib.util.find_spec("PIL") is not None


def load():
    sys.path.insert(0, str(ROOT / "library"))
    spec = importlib.util.spec_from_file_location("kvant_ocr", ROOT / "library" / "ocr.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_кандидаты_не_включают_форматы_которые_нечем_распознать():
    """Треть времени первой части ушла на xlsx, архивы и экзотику: они
    скачивались, доходили до tesseract и возвращали «формат не читаем».
    Отсекать их надо в выборке, а не в обработчике."""
    ocr = load()
    assert "in ('изображение', 'pdf', '')" in ocr.CANDIDATES
    assert "ocr_at is null" in ocr.CANDIDATES


def test_причина_пустого_pdf_называет_таймаут_а_не_пустоту():
    """У PDF страниц много, и таймаут одной важнее пустоты остальных: по
    «текста не найдено» стали бы чинить распознавание, а чинить надо время."""
    ocr = load()
    _, причина = ocr.почему_пусто(["текста не найдено", "таймаут распознавания"], 5)
    assert "таймаут" in причина
    _, причина = ocr.почему_пусто(["текста не найдено"], 3)
    assert причина == "текста не найдено на 3 страницах"


@pytest.mark.skipif(not есть_tesseract, reason="tesseract не установлен")
def test_битый_файл_отличается_от_файла_без_текста(tmp_path):
    ocr = load()
    битый = tmp_path / "битый.png"
    битый.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    текст, причина = ocr.ocr_image(str(битый))
    assert not текст.strip()
    assert причина and "текста не найдено" not in причина, причина


@pytest.mark.skipif(not (есть_tesseract and есть_pillow), reason="нужны tesseract и Pillow")
def test_на_нарисованном_листе_спецификации_текст_читается(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    ocr = load()
    шрифт = None
    try:
        шрифт = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
    except OSError:
        pass
    лист = Image.new("L", (1100, 400), 255)
    d = ImageDraw.Draw(лист)
    for i, t in enumerate(["СПЕЦИФИКАЦИЯ", "1 Подшипник 22315 SKF 4 шт",
                           "2 Болт М12х60 DIN 933 20 шт"]):
        d.text((40, 60 + i * 120), t, fill=0, font=шрифт)
    путь = tmp_path / "лист.png"
    лист.save(путь)
    текст, причина = ocr.ocr_image(str(путь))
    assert причина == ""
    assert "22315" in текст, текст[:200]


def test_tesseract_с_русским_языком_доступен():
    """Если языковые данные не поставлены, распознавание вернёт латиницу вместо
    кириллицы, и это видно только по мусору в базе."""
    if not есть_tesseract:
        pytest.skip("tesseract не установлен")
    r = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    assert "rus" in r.stdout, r.stdout[:200]
