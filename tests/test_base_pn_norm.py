"""Один артикул — один ключ, во всех модулях base/ и на странице справочника.

История. Правило ключа было выписано в base/ четырьмя копиями (kb_catalog.py,
quote.py и дважды в JS страницы kb_page.py), и ни одна не сводила кириллические
буквы-двойники к латинице. Ошибка здесь обратная той, что была в инструментах
заявки: там класс символов кириллицу ВЫБРАСЫВАЛ, здесь — СОХРАНЯЕТ. Итог тот же,
один номер под двумя ключами:

    «917427С1» с кириллической С  →  917427С1
    «917427C1» с латинской  C     →  917427C1

Каталог считал их разными изделиями, а поиск по одному написанию не находил
запись, сохранённую под другим. Сведение давно применяют scripts/build_index.py,
scripts/lookup.py и gt/tools/pnkey.py — base/ остался в стороне.

Номера придуманы здесь же (правило 18 CLAUDE.md), кроме тех четырёх, что уже
опубликованы в шапке gt/tools/pnkey.py как разобранный случай.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "base"))
sys.path.insert(0, str(ROOT / "gt/tools"))


def модуль(имя: str, путь: Path):
    spec = importlib.util.spec_from_file_location(имя, путь)
    m = importlib.util.module_from_spec(spec)
    sys.modules[имя] = m
    spec.loader.exec_module(m)
    return m


pnkey = модуль("base_pn_norm", ROOT / "base" / "pn_norm.py")


# Пары «кириллическое написание, латинское написание». Первые четыре — разобранный
# случай из шапки gt/tools/pnkey.py, остальные придуманы.
ПАРЫ = [
    ("917427С1", "917427C1"),
    ("180В4158", "180B4158"),
    ("У808750D9J", "Y808750D9J"),
    ("1794-IВ10ХОВ6ХТ", "1794-IB10XOB6XT"),
    ("НМ-2О5К", "HM-2O5K"),
    ("рс-14е", "pc-14e"),
]


def test_двойники_сводятся_к_одному_ключу():
    for кир, лат in ПАРЫ:
        assert pnkey.key_of(кир) == pnkey.key_of(лат), (
            f"«{кир}» и «{лат}» дают разные ключи: "
            f"{pnkey.key_of(кир)} против {pnkey.key_of(лат)}")


def test_ключ_состоит_из_латиницы_и_цифр_если_двойники_сведены():
    for кир, _ in ПАРЫ:
        ключ = pnkey.key_of(кир)
        assert ключ.isascii(), f"в ключе «{ключ}» осталась кириллица: сведение не сработало"


def test_прочая_кириллица_не_трогается():
    """У российских изделий обозначение бывает кириллическим целиком."""
    assert pnkey.key_of("БП-123") == "БП123"
    assert pnkey.key_of("ЖГ-5Д") == "ЖГ5Д"
    # смешанный случай: двойники сведены, прочее осталось
    assert pnkey.key_of("РВД-100") == "PBД100"


def test_разделители_и_регистр():
    assert pnkey.key_of("nu 2216-e") == "NU2216E"
    assert pnkey.key_of("  3222 1881 52  ") == "3222188152"
    assert pnkey.key_of("") == ""
    assert pnkey.key_of(None) == ""


def test_таблица_двойников_совпадает_с_канонической():
    """Две таблицы обязаны быть одинаковы, иначе номер снова получит два ключа.

    gt/tools/homoglyphs.py — источник; base/pn_norm.py его копия. Расхождение
    означало бы, что подсистемы снова считают один номер по-разному.
    """
    gt = модуль("gt_homoglyphs", ROOT / "gt/tools/homoglyphs.py")
    assert pnkey.HOMOGLYPHS == gt.HOMOGLYPHS, (
        "таблицы двойников base/pn_norm.py и gt/tools/homoglyphs.py разошлись")


def test_модули_base_используют_общее_правило():
    """kb_catalog, quote и kb_page берут ключ из pn_norm, а не свою копию."""
    for имя in ("kb_catalog.py", "quote.py", "kb_page.py"):
        текст = (ROOT / "base" / имя).read_text(encoding="utf-8")
        assert "from pn_norm import" in текст, f"{имя} не подключил общее правило"
        assert 'NORM = re.compile(r"[^0-9A-ZА-Я]")' not in текст, (
            f"{имя} вернул собственную копию правила ключа")


def test_ключ_страницы_совпадает_с_ключом_базы():
    """JS страницы считает тот же ключ: иначе поиск не найдёт свою же запись.

    Алгоритм сверяется по составу, а не прогоном движка: таблица двойников в JS
    строится из той же HOMOGLYPHS, и класс отсева символов совпадает дословно.
    """
    js = pnkey.JS_FOLD
    for кир, лат in pnkey.HOMOGLYPHS.items():
        assert f"'{кир}':'{лат}'" in js, f"в JS нет пары {кир}→{лат}"
    assert "replace(/[^0-9A-ZА-Я]/g,'')" in js, "класс отсева в JS разошёлся с питоном"
    assert ".toUpperCase()" in js
    # сведение идёт ДО перевода регистра, иначе строчная «с» станет заглавной «С»
    # и останется кириллической
    assert js.index("PN_HOMOGLYPHS[c]") < js.index(".toUpperCase()"), (
        "в JS сведение двойников идёт после перевода регистра — строчные не сведутся")


def test_kb_catalog_отсеивает_короткие_ключи():
    """Обёртка key_of в kb_catalog добавляет отсев, и он не потерялся."""
    kb = модуль("kb_catalog", ROOT / "base" / "kb_catalog.py")
    assert kb.key_of("АБ") is None, "короткий ключ должен отсеиваться"
    assert kb.key_of("12345") is None, "числовой ключ короче шести знаков отсеивается"
    assert kb.key_of("917427С1") == "917427C1", "сведение в обёртке не работает"
