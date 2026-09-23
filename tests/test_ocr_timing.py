"""Замер времени распознавания и деление потоков внутри tesseract.

ЗАЧЕМ. 23.09.2026 я объявил причиной 69 % таймаутов число внешних потоков и
ошибся: шаг прогона прибивал WORKERS=4, и вход `workers` до распознавания не
доходил. Замер на этой же машине показал другое: ОДИН tesseract читает выдуманный
шумный лист 3024×4032 за 1,2 с, ЧЕТЫРЕ таких же процесса разом не укладываются и в
400 с, а те же четыре с OMP_THREAD_LIMIT=1 — за 1,2 с на всех. В /proc у каждого
процесса четыре потока: tesseract собран с OpenMP и сам берёт все ядра, поэтому
четыре процесса дают шестнадцать потоков на четырёх ядрах. Размер картинки ни при
чём — тот же лист в 3,0 и 5,4 Мпкс читается за те же 1,3–1,4 с.

Отсюда две вещи, которые здесь и закрепляются:
1. Потоки делятся: снаружи WORKERS процессов, внутри каждого — ядер / WORKERS.
2. Прогон обязан сам отвечать «почему таймаут» — таблицей «размер × время», а не
   моим рассуждением. Корпус для проверки ВЫДУМАН (CLAUDE.md, правило 18):
   заголовки PNG и JPEG собраны здесь побайтно, ни одного файла из базы.
"""
from __future__ import annotations

import importlib.util
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def модуль(**окружение: str):
    for p in (ROOT, ROOT / "library", ROOT / "scripts"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    прежнее = {k: os.environ.get(k) for k in окружение}
    os.environ.update(окружение)
    try:
        spec = importlib.util.spec_from_file_location(
            "kvant_ocr_timing_" + "_".join(окружение.values()), ROOT / "library" / "ocr.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        for k, v in прежнее.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def png(w: int, h: int) -> bytes:
    """Заголовок PNG нужной ширины и высоты. Пиксели не нужны — их никто не читает."""
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
            + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00" + b"\x00" * 4)


def jpeg(w: int, h: int) -> bytes:
    """JPEG с сегментом APP0 перед SOF0: размер лежит НЕ первым, его надо искать."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w) + b"\x00" * 10
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def test_размер_png_читается(tmp_path):
    m = модуль(WORKERS="1")
    f = tmp_path / "a.png"
    f.write_bytes(png(3024, 4032))
    assert round(m.пикселей(str(f)), 1) == 12.2


def test_размер_jpeg_читается_не_первым_сегментом(tmp_path):
    """Наивный разбор взял бы длину APP0 за размер картинки."""
    m = модуль(WORKERS="1")
    f = tmp_path / "a.jpg"
    f.write_bytes(jpeg(1653, 2339))
    assert round(m.пикселей(str(f)), 1) == 3.9


def test_нечитаемый_файл_даёт_ноль_а_не_выдумку(tmp_path):
    """Разряд «размер не прочитан» честнее правдоподобного числа."""
    m = модуль(WORKERS="1")
    f = tmp_path / "a.bin"
    f.write_bytes(b"\xff\xd8" + b"\x00" * 40)
    assert m.пикселей(str(f)) == 0.0
    assert m.пикселей(str(tmp_path / "нет-такого")) == 0.0


def test_потоки_делятся_между_процессами():
    """Произведение «процессов × потоков» держится около числа ядер."""
    ядер = os.cpu_count() or 2
    m = модуль(WORKERS=str(ядер))
    assert int(m.OMP) == 1, f"при {ядер} процессах внутри должен остаться 1 поток, а не {m.OMP}"
    m1 = модуль(WORKERS="1")
    assert int(m1.OMP) == ядер, "один процесс вправе занять все ядра"


def test_доля_ядер_не_обнуляется():
    """Процессов больше, чем ядер, — внутри всё равно хотя бы один поток."""
    m = модуль(WORKERS=str((os.cpu_count() or 2) * 8))
    assert int(m.OMP) >= 1


def test_среда_вызова_несёт_предел_потоков():
    """Предел обязан доехать ДО tesseract, а не остаться константой модуля."""
    m = модуль(WORKERS="4")
    assert m.СРЕДА.get("OMP_THREAD_LIMIT") == m.OMP
    код = (ROOT / "library" / "ocr.py").read_text(encoding="utf-8")
    без_пояснений = "\n".join(s.split("#")[0] for s in код.splitlines())
    assert "env=СРЕДА" in без_пояснений, "среда не передана в subprocess.run"


def test_таблица_времени_считает_таймауты_и_медиану():
    m = модуль(WORKERS="1")
    m.ЗАМЕРЫ.clear()
    m.ЗАМЕРЫ.extend([(14.0, 120.0, True), (14.0, 120.0, True), (14.0, 7.0, False),
                     (3.0, 2.0, False), (0.0, 9.0, False)])
    строки = m.таблица_времени()
    сплошь = "\n".join(строки)
    assert "12–24" in сплошь and "2–6" in сплошь and "размер не прочитан" in сплошь
    разряд = next(s for s in строки if "12–24" in s)
    assert разряд.split()[-3:] == ["3", "2", "120.0"], разряд


def test_пустой_замер_не_печатает_пустую_таблицу():
    """Заголовок без строк читается как «замера нет», а не «нулей нет»."""
    m = модуль(WORKERS="1")
    m.ЗАМЕРЫ.clear()
    assert m.таблица_времени() == []
