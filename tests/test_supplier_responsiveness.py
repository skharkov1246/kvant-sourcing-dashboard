"""Отзывчивость поставщика: корпус придуман (правило 18), стадии настоящие."""
import collections
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location(
    "supplier_responsiveness", ROOT / "scripts" / "supplier_responsiveness.py")
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def карточка(stage, supplier):
    return {"stageId": stage, sr.ПОЛЕ_ПОСТАВЩИКА: supplier}


def test_идентификатор_компании_читается_в_обоих_написаниях():
    # Bitrix отдаёт поле то числом, то строкой «CO_123» — оба должны сойтись
    # в одну компанию, иначе её запросы расщепятся надвое.
    assert sr.crm_id("CO_123") == sr.crm_id(123) == sr.crm_id("123") == 123
    assert sr.crm_id("") == sr.crm_id(None) == sr.crm_id("нечисло") == 0


def test_карточка_без_компании_не_приписывается_никому():
    по = sr.разложить([карточка("DT166_24:3", ""), карточка("DT166_24:3", "CO_5")])
    assert sum(по[0].values()) == 1
    assert sum(по[5].values()) == 1


def test_неотправленная_карточка_не_считается_запросом():
    """Стадия «Новый» — запрос ещё не ушёл. Считать её значит завысить знаменатель."""
    по = sr.разложить([карточка("DT166_24:NEW", "CO_7")])
    c = по[7]
    отправлено = sum(v for b, v in c.items() if b not in sr.НЕ_ОТПРАВЛЕН)
    assert отправлено == 0


def test_ответ_и_кп_считаются_отдельно():
    """Переписка без цены отклик показывает, а закупку не двигает."""
    по = sr.разложить([
        карточка("DT166_24:UC_61BSRU", "CO_9"),   # переписка — ответ, но не КП
        карточка("DT166_24:SUCCESS", "CO_9"),     # КП получено — и ответ, и КП
        карточка("DT166_24:2", "CO_9"),           # отказ в КП — ответ, не КП
        карточка("DT166_24:3", "CO_9"),           # нет ответа в срок
    ])
    c = по[9]
    assert sum(v for b, v in c.items() if b in sr.ОТВЕТИЛ) == 3
    assert sum(v for b, v in c.items() if b in sr.ДАЛ_КП) == 1
    assert sum(v for b, v in c.items() if b in sr.МОЛЧАЛ) == 1


def test_доля_печатается_со_знаменателем():
    """Ноль процентов по одному запросу и по сорока — разные утверждения."""
    assert sr.доля(0, 1) == "0/1 (0 %)"
    assert sr.доля(0, 40) == "0/40 (0 %)"
    assert sr.доля(3, 4) == "3/4 (75 %)"
    assert sr.доля(0, 0) == "0/0", "деления на ноль быть не должно"


def test_сводка_не_печатает_ни_одного_идентификатора(capsys):
    """Репозиторий публичный: в журнал уходят только агрегаты (правило 17)."""
    по = {0: collections.Counter({"no_answer": 2}),
          987654: collections.Counter({"selected": 3, "no_answer": 1})}
    sr.сводка(по)
    напечатано = capsys.readouterr().out
    assert "987654" not in напечатано
