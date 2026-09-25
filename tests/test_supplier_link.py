"""Связь реестра разведки с реестром компаний — правило без базы.

Что держится (library/supplier_link.py, шаг 4 портала):
  · сводят только сильные ключи: ИНН с верной суммой, домен сайта и почты;
  · общий домен (почта, площадка, соцсеть) не сводит никогда — закрытым
    списком, и norm_domain сведения реестров от этого не меняется;
  · имя — только кандидат, и не кандидат, если против говорят правовая форма,
    домен или ИНН;
  · спор виден числом: разведка → две сущности, домен против ИНН, домен,
    который носят слишком много имён; сущность ← много поставщиков разведки;
  · связь ведёт в корень цепочки слияний;
  · гейты: ноль связей, доля споров, «у скольких стало хуже».

Корпус придуман (CLAUDE.md, правило 18): домены — в зонах .example и .test,
ИНН — синтетические с верной контрольной суммой.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import supplier_registry_overlap as ov  # noqa: E402

from library import supplier_link as sl  # noqa: E402

ИНН_А = "0000000018"
ИНН_Б = "1111111117"


def р(id, name, **kw):
    """Строка lib_suppliers как словарь колонок."""
    return sl.разведка_из({"id": id, "name_key": f"k{id}", "name": name, **kw})


def реестр(сущности, признаки, имена=()):
    return sl.собрать_реестр(сущности, признаки, имена)


E = [("KV-S-000001-8", None, None, "Альфа Насосы"),
     ("KV-S-000002-6", None, "Нигдения", "Бета Уплотнения"),
     ("KV-S-000003-4", None, None, "gammaold"),
     ("KV-S-000004-2", "KV-S-000005-9", None, "deltaold"),    # слита в 5
     ("KV-S-000005-9", None, None, "Дельта Групп"),
     ("KV-S-000006-7", None, None, "ООО Ромашка")]
I = [("KV-S-000001-8", "domain", "alpha-pumps.example"),
     ("KV-S-000001-8", "inn", ИНН_А),
     ("KV-S-000002-6", "alias", "бетауплотнения"),
     ("KV-S-000003-4", "domain", "gamma.example"),
     ("KV-S-000003-4", "domain", "gmail.com"),            # чужой адрес почты в реестре
     ("KV-S-000004-2", "domain", "delta.example"),
     ("KV-S-000006-7", "legal", "ооо"),
     ("KV-S-000006-7", "alias", "ромашка")]


# ── ключи ───────────────────────────────────────────────────────────────────

def test_общий_домен_закрытым_списком():
    for d in ("gmail.com", "mail.ru", "yandex.ru", "ya.ru", "icloud.com", "ebay.co.uk", "ebay.com",
              "seller.en.made-in-china.com", "made-in-china.com", "t.me", "sites.google.com",
              "x.wixsite.com", "linkedin.com", "163.com"):
        assert ov.общий_домен(d), d
    for d in ("alpha-pumps.example", "amazon-pumps.example", "mailbox-industries.example",
              "acme.me", "", None):
        assert not ov.общий_домен(d), d


def test_разбор_адреса_сведения_не_изменился():
    """norm_domain — правило сведения реестров; хост() вынесен из него без
    перемены поведения."""
    assert ov.norm_domain("https://www.Alpha-Pumps.example/ru/каталог") == "alpha-pumps.example"
    assert ov.norm_domain("sales@gmail.com") == ""
    assert ov.хост("sales@gmail.com") == "gmail.com"
    assert ov.norm_domain("") == "" and ov.хост(None) == ""


def test_домены_сайта_и_почты_без_общих():
    assert sl.домены_сайта("https://www.alpha-pumps.example/ru, ebay.com/str/x; 2.5") == (
        {"alpha-pumps.example"}, 1)
    assert sl.домены_почты("sales@alpha-pumps.example, x@gmail.com; y@mail.ru") == (
        {"alpha-pumps.example"}, 2)
    assert sl.домены_сайта("sales@alpha-pumps.example") == (set(), 0)


def test_инн_из_текста_только_с_верной_суммой():
    assert sl.инн_из_текста("ООО Выдумка, ИНН " + ИНН_А) == {ИНН_А}
    assert sl.инн_из_текста("инн:" + ИНН_Б, None) == {ИНН_Б}
    assert sl.инн_из_текста("ИНН 0000000019") == set()          # сумма не сходится
    assert sl.инн_из_текста("Иннокентий " + ИНН_А) == set()      # не слово ИНН
    assert sl.инн("00 0000 0018") == ИНН_А and sl.инн("123") == ""


# ── сведение ────────────────────────────────────────────────────────────────

def test_инн_сводит():
    x = р(1, "Какое-то Другое Имя", strengths="реквизиты: ИНН " + ИНН_А)
    и = sl.сопоставить([x], реестр(E, I))
    assert и.связь == {1: "KV-S-000001-8"} and и.правила[1] == {"инн"}
    [строка] = и.строки.values()
    assert строка[2:6] == ("KV-S-000001-8", "инн", "link", 1.0)


def test_домен_сайта_и_почты_сводят_одной_связью():
    x = р(1, "Alpha Pumps", site="https://alpha-pumps.example", contact_email="info@alpha-pumps.example")
    y = р(2, "Альфа склад", contact_email="sales@alpha-pumps.example")
    и = sl.сопоставить([x, y], реестр(E, I))
    assert и.связь == {1: "KV-S-000001-8", 2: "KV-S-000001-8"}
    # Почта с тем же доменом, что и сайт, — не второе совпадение, а то же.
    assert и.правила[1] == {"домен сайта"} and и.правила[2] == {"домен почты"}
    # Одна сущность ← два поставщика разведки: видно числом.
    assert sl.на_сущность(и) == {"KV-S-000001-8": 2}


def test_общий_почтовый_домен_не_сводит():
    x = р(1, "Гамма Трейд", contact_email="gamma.trade@gmail.com")
    и = sl.сопоставить([x], реестр(E, I))
    assert x.почты == frozenset() and x.общих == 1
    assert и.связь == {} and not и.строки


def test_имя_только_кандидат():
    x = р(1, "ООО «Бета Уплотнения»")
    и = sl.сопоставить([x], реестр(E, I))
    assert и.связь == {}
    assert и.кандидаты == {1: {"KV-S-000002-6": "имя"}}
    [строка] = и.строки.values()
    assert (строка[3], строка[4]) == ("имя", "candidate")
    # Совпала страна — правило сильнее, но всё равно кандидат.
    y = р(2, "Бета Уплотнения", country="нигдения")
    assert sl.сопоставить([y], реестр(E, I)).кандидаты == {2: {"KV-S-000002-6": "имя+страна"}}


def test_имя_против_формы_домена_и_короткое_не_кандидат():
    форма = р(1, "АО Ромашка Плюс")            # другое имя — не кандидат вовсе
    ао = р(2, "АО Ромашка")                   # то же имя, другая форма
    домен = р(3, "Бета Уплотнения", site="beta-other.example")
    р_ = реестр(E, I + [("KV-S-000002-6", "domain", "beta.example")])
    и = sl.сопоставить([форма, ао, домен], р_, мин_длина=6)
    assert и.кандидаты == {}
    assert и.отвергнуто == {"правовые формы разные": 1, "домены разные": 1}
    # Ключ короче порога — не кандидат, даже если совпал.
    assert sl.сопоставить([р(4, "ООО Ромашка")], реестр(E, I), мин_длина=8).кандидаты == {}
    assert sl.сопоставить([р(4, "ООО Ромашка")], реестр(E, I), мин_длина=6).кандидаты == {
        4: {"KV-S-000006-7": "имя"}}


def test_спор_две_сущности_виден_и_не_сводит():
    x = р(1, "Смесь", site="gamma.example", contact_email="a@alpha-pumps.example")
    и = sl.сопоставить([x], реестр(E, I))
    assert и.связь == {} and и.спор_две == {1}
    assert {s[4] for s in и.строки.values()} == {"conflict"}
    # Кандидатом по имени спорный тоже не становится.
    assert и.кандидаты == {}


def test_домен_против_инн_не_сводит():
    x = р(1, "Альфа дочка", site="alpha-pumps.example", strengths="ИНН " + ИНН_Б)
    и = sl.сопоставить([x], реестр(E, I))
    assert и.связь == {} and и.спор_налог == {1}
    [строка] = и.строки.values()
    assert (строка[4], строка[7]) == ("conflict", "налоговые номера разные")


def test_связь_ведёт_в_корень_цепочки_слияний():
    x = р(1, "Дельта", site="delta.example")
    и = sl.сопоставить([x], реестр(E, I))
    assert и.связь == {1: "KV-S-000005-9"}
    # Пишется сущность, несущая признак: корень считает вид на чтении.
    [строка] = и.строки.values()
    assert строка[2] == "KV-S-000004-2"
    assert sl.корни({"a": "b", "b": "a"}) == {"a": "a", "b": "b"}      # круг — сама собой


def test_домен_многих_имён_придержан():
    разведка = [р(i, f"Витрина {i}", site="gamma.example") for i in range(1, 5)]
    и = sl.сопоставить(разведка, реестр(E, I), макс_имён=3)
    assert и.связь == {} and и.придержано == {1, 2, 3, 4}
    assert и.доменов_придержано == {"gamma.example"}
    и = sl.сопоставить(разведка, реестр(E, I), макс_имён=None)
    assert set(и.связь) == {1, 2, 3, 4}


def test_согласие_имени_со_связью():
    совпало = р(1, "Alpha Pumps", site="alpha-pumps.example")
    мимо = р(2, "Бета Уплотнения", site="gamma.example")
    р_ = реестр(E, I + [("KV-S-000001-8", "alias", "alphapumps")])
    и = sl.сопоставить([совпало, мимо], р_)
    assert и.согласие == {"совпало": 1, "мимо": 1}


def test_сетки_считаются():
    разведка = [р(1, "Бета Уплотнения"), р(2, "Alpha Pumps", site="alpha-pumps.example")]
    р_ = реестр(E, I)
    и = sl.сопоставить(разведка, р_)
    сетка = sl.сетка_имени(разведка, р_, и)
    assert [s[0] for s in сетка] == list(sl.СЕТКА_ДЛИНЫ)
    кандидатов = {s[0]: s[1] for s in сетка}
    assert кандидатов[6] == 1 and кандидатов[12] == 1      # «бетауплотнения» — 14 знаков
    дом = sl.сетка_домена(разведка, р_, {2: (3, 1)})
    assert [s[0] for s in дом] == list(sl.СЕТКА_ДОМЕНА)
    assert all(s[1] == 1 and s[2] == 3 for s in дом)


def test_гейты():
    р_ = реестр(E, I)
    пусто = sl.сопоставить([р(1, "Никто")], р_)
    assert any("связей 0" in p for p in sl.гейты(пусто, [], None))
    хорошо = sl.сопоставить([р(1, "Alpha", site="alpha-pumps.example")], р_)
    assert sl.гейты(хорошо, [], None) == []
    # У скольких стало хуже: прежде связей было двенадцать, теперь одна.
    прежние = {i: "KV-S-000001-8" for i in range(1, 13)}
    assert any("хуже стало у 11" in p for p in sl.гейты(хорошо, [], прежние))
    # Споров больше пяти процентов.
    спорный = sl.сопоставить([р(1, "a", site="gamma.example", contact_email="a@alpha-pumps.example"),
                              р(2, "b", site="alpha-pumps.example")], р_)
    assert any("споров" in p for p in sl.гейты(спорный, [], None))


# ── проводка: прогон миграций, прогон связи, воркер, страница ───────────────

def _без_комментариев(текст: str, знак: str) -> str:
    return "\n".join(строка.split(знак)[0] for строка in текст.splitlines())


def test_схема_связи_в_прогоне_миграций_по_порядку():
    yml = (ROOT / ".github" / "workflows" / "zip-db.yml").read_text(encoding="utf-8")
    файлы = [s.strip().split()[1] for s in _без_комментариев(yml, "#").splitlines()
             if s.strip().startswith("apply ")]
    где = файлы.index("library/supabase/supplier_link_schema.sql")
    for опора in ("schema.sql", "suppliers_schema.sql"):
        assert файлы.index("library/supabase/" + опора) < где, опора
    for читатель in ("portal_schema.sql", "portal_entity_schema.sql"):
        assert где < файлы.index("library/supabase/" + читатель), читатель
    assert файлы[-1] == "zip/supabase/migrations_rls_stage1.sql"


def test_прогон_связи_ручной_без_битрикса_схема_перед_записью():
    yml = (ROOT / ".github" / "workflows" / "supplier-link.yml").read_text(encoding="utf-8")
    код = _без_комментариев(yml, "#")
    assert "workflow_dispatch" in код and "schedule" not in код and "cron" not in код
    assert "apply:" in код and "rollback:" in код
    assert "BITRIX" not in код, "связь реестров Битрикс не читает"
    assert "RUN_ID: link-${{ github.run_id }}" in код
    assert "ROLLBACK_RUN_ID: ${{ inputs.rollback }}" in код
    # Схема — в рабочую базу (секрет), и шаг стоит ДО записи.
    схема = код.index('psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f library/supabase/supplier_link_schema.sql')
    assert схема < код.index("- name: Замер и связь")
    # Проверки записи на чистой базе в локали UTF-8 — тоже до записи.
    assert код.index("--locale=C.UTF-8") < схема
    assert "tests/test_supplier_link_sql.py" in код


def test_воркер_пропускает_только_сильную_связь():
    js = re.sub(r"(?m)^\s*//.*$", "", (ROOT / "public" / "_worker.js").read_text(encoding="utf-8"))
    m = re.search(r"const ПЕ_СВЕДЕНО = ПС\.k\(/\^\(([^)]*)\)\$/", js)
    assert m and set(m.group(1).split("|")) == set(sl.СИЛЬНЫЕ) == {"инн", "vat", "домен сайта", "домен почты"}
    assert "company: ПЕ_КОМПАНИЯ, link: ПЕ_СВЕДЕНО" in js
    assert "research: ПС.a(ПС.o({" in js and "research_n: ПС.n" in js
    стр = (ROOT / "public" / "portal_entity.js").read_text(encoding="utf-8")
    assert "m.company && m.company.id" in стр and "v.research_n" in стр
