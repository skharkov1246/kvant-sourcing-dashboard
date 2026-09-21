"""Замер котировок: корпус придуман (правило 18), имена полей настоящие."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location(
    "quote_coverage", ROOT / "scripts" / "quote_coverage.py")
qc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qc)


def test_наш_файл_запроса_не_считается_котировкой():
    """Считать свой запрос за полученное КП значит объявить прокотированным то,
    что мы сами отправили. Эта ошибка уже ловилась в джойне brand_suppliers."""
    assert qc.ПОЛЕ_ЗАПРОСА not in qc.ПОЛЯ_КП


def test_поля_котировки_закрытый_список():
    """Открытый список рано или поздно зачтёт наш файл за чужой."""
    assert isinstance(qc.ПОЛЯ_КП, dict) and qc.ПОЛЯ_КП
    assert all(поле.startswith("ufCrm18") for поле in qc.ПОЛЯ_КП)


def test_пустое_поле_файла_это_ноль_файлов():
    for пусто in (None, "", [], {}, 0):
        assert qc.есть_файл(пусто) is False
    assert qc.есть_файл([{"id": 1}]) is True
    assert qc.есть_файл({"id": 1}) is True


def test_сводка_не_печатает_идентификаторов(capsys):
    """Репозиторий публичный: только агрегаты (правило 17)."""
    qc.сводка([{"stageId": "DT166_24:SUCCESS", "opportunity": "5", "currencyId": "EUR",
                "id": 987654, "ufCrm18_1731179998": [{"id": 12345}]}])
    напечатано = capsys.readouterr().out
    assert "987654" not in напечатано and "12345" not in напечатано


def test_нечисловая_сумма_не_роняет_замер():
    """Bitrix кладёт в opportunity и пустое, и строку — падать на этом нельзя."""
    qc.сводка([{"stageId": "DT166_24:1", "opportunity": "не число", "currencyId": None},
               {"stageId": "DT166_24:NEW", "opportunity": None}])
