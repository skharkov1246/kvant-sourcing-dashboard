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


def test_статистика_хранит_числитель_и_знаменатель_а_не_долю():
    """Доля не пишется в базу числом: «50 %» из двух и из сорока лягут одинаково."""
    c = collections.Counter({"sent": 4, "dialog": 2, "selected": 1, "no_answer": 1, "new": 3})
    st = sr.статистика(c)
    assert st == {"sent": 8, "answered": 3, "quoted": 1, "silent": 1,
                  "no_outcome": 4, "cards": 11}
    assert not any(isinstance(v, float) for v in st.values()), "долей в факте быть не должно"


def test_исход_не_зафиксирован_считается_отдельно():
    """Карточка стоит в «Отправлен»: ни ответа, ни отказа, ни «не ответил в срок»."""
    st = sr.статистика(collections.Counter({"sent": 5}))
    assert st["sent"] == 5
    assert st["answered"] == st["silent"] == 0
    assert st["no_outcome"] == 5


def test_слагаемые_сходятся_с_отправленным():
    for c in (collections.Counter({"sent": 3, "selected": 2, "no_answer": 1}),
              collections.Counter({"dialog": 7, "refused": 2}),
              collections.Counter({"new": 4})):
        st = sr.статистика(c)
        assert st["answered"] + st["silent"] + st["no_outcome"] == st["sent"]


# ── ВОЗРАСТ КАРТОЧЕК БЕЗ ИСХОДА ─────────────────────────────────────────────
# 40 % запросов стоят без ответа и без отказа. Свежие — это ожидание, старые —
# брошенные карточки, и чинят это разные люди. Разбор отвечает, чего сколько.
import datetime as _dt  # noqa: E402


def _создана(дней_назад):
    t = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=дней_назад)
    return t.isoformat().replace("+00:00", "Z")


def test_возраст_считается_от_даты_создания():
    # Даты фиксированные: со «сейчас» из now() карточка возрастом ровно десять
    # дней округляется вниз до девяти, и тест краснеет на верном коде.
    сейчас = _dt.datetime(2026, 9, 21, tzinfo=_dt.timezone.utc).timestamp()
    десять = _dt.datetime(2026, 9, 11, tzinfo=_dt.timezone.utc).isoformat()
    сегодня = _dt.datetime(2026, 9, 21, tzinfo=_dt.timezone.utc).isoformat()
    assert sr.возраст_дней(десять.replace("+00:00", "Z"), сейчас) == 10
    assert sr.возраст_дней(сегодня.replace("+00:00", "Z"), сейчас) == 0
    # Будущая дата — не отрицательный возраст, а ноль: часы портала и наши
    # расходятся, и уходить в минус из-за этого нельзя.
    вперёд = _dt.datetime(2026, 9, 22, tzinfo=_dt.timezone.utc).isoformat()
    assert sr.возраст_дней(вперёд.replace("+00:00", "Z"), сейчас) == 0


def test_пустая_или_битая_дата_не_роняет_замер():
    сейчас = _dt.datetime.now(_dt.timezone.utc).timestamp()
    for плохо in ("", None, "вчера", "2026-13-45"):
        assert sr.возраст_дней(плохо, сейчас) is None


def test_в_разбор_идут_только_карточки_без_исхода():
    """Ответ, отказ и «не ответил в срок» — это исходы, их сюда пускать нельзя."""
    карточки = [
        карточка("DT166_24:PREPARATION", "CO_1") | {"createdTime": _создана(3)},
        карточка("DT166_24:FAIL", "CO_1") | {"createdTime": _создана(200)},
        карточка("DT166_24:SUCCESS", "CO_1") | {"createdTime": _создана(3)},
        карточка("DT166_24:3", "CO_1") | {"createdTime": _создана(3)},
        карточка("DT166_24:NEW", "CO_1") | {"createdTime": _создана(3)},
    ]
    из_них = sr.без_исхода_по_возрасту(карточки)
    assert sum(из_них.values()) == 2, "только «Отправлен» и «Прочее»"
    assert из_них["меньше недели"] == 1
    assert из_них["3–12 месяцев"] == 1


def test_карточка_без_даты_названа_отдельно_а_не_отброшена():
    из_них = sr.без_исхода_по_возрасту(
        [карточка("DT166_24:PREPARATION", "CO_1") | {"createdTime": ""}])
    assert из_них["дата создания пуста"] == 1
