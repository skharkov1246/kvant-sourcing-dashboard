"""Замер разбора котировок по базе. Корпус придуман (правило 18)."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "quote_parse_coverage", ROOT / "scripts" / "quote_parse_coverage.py")
qpc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qpc)

ОФФЕР = "ufCrm18_1731179998"
ЗАПРОС = "ufCrm18_1727423346"


def test_наш_запрос_в_итог_котировок_не_попадает(capsys):
    """Семьсот позиций из нашего же запроса не должны раздуть счёт котировок."""
    qpc.свод([(ОФФЕР, "разобран", 2, 40, 900),
              (ЗАПРОС, "разобран", 1, 700, 20000)])
    текст = capsys.readouterr().out
    итог = текст.split("ПО ПОЛЯМ")[0]
    assert "позиций получено:     40" in итог
    assert "700" not in итог, "позиции нашего запроса попали в котировки"
    assert "Request file" in текст, "и при этом наш файл должен быть показан отдельно"


def test_неразобранные_файлы_видны_а_не_молчат():
    """Файл, который не прочитался, — это измеримая потеря, а не пустое место."""
    строки = [(ОФФЕР, "разобран", 8, 100, 5000),
              (ОФФЕР, "формат не читаем", 2, 0, 0)]
    qpc.свод(строки)  # не падает и печатает оба статуса


def test_доля_печатается_со_знаменателем():
    assert qpc.доля(8, 10) == "8/10 (80 %)"
    assert qpc.доля(0, 0) == "0/0", "деления на ноль быть не должно"


def test_поля_берутся_из_одного_места_с_замером_портала():
    """Два списка полей разошлись бы молча: считали бы разное и звали одинаково."""
    import quote_coverage as qc
    assert qpc.ПОЛЯ_КП is qc.ПОЛЯ_КП
    assert qpc.ПОЛЕ_ЗАПРОСА == qc.ПОЛЕ_ЗАПРОСА
