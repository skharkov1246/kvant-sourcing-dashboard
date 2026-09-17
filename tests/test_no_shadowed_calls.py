"""Локальная переменная не должна затирать функцию, которую сама же и вызывает.

Ошибка, оплаченная прогоном 17.09.2026: в `bitrix_tkp.main()` список кандидатов
назывался `take`, и там же вызывалась функция модуля `take(bx, c)`. Python
считает имя локальным на весь кадр, поэтому вызов уходил в список и падал
`TypeError: 'list' object is not callable` на ПЕРВОМ же файле. Холостой прогон
до этого места не доходит и ошибку не показывает — она бы вскрылась только в
боевом прогоне с секретом, то есть в Actions, через двадцать минут ожидания.

Юнит-тесты её не ловят: они проверяют функции по отдельности, а не `main()`.
Поэтому проверка статическая и на весь репозиторий — класс ошибки один и тот же
везде.
"""
from __future__ import annotations

import ast
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv", "venv", "node_modules", "smoke", "__pycache__", ".git", "kb"}

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_own_scope(node):
    """Узлы тела функции, НЕ заходя во вложенные области видимости.

    Класс и вложенная функция — свои пространства имён: `status = 200` внутри
    `class Reply` не затирает функцию `status()` снаружи. Без этого проверка
    даёт ложную тревогу (проверено на tests/test_repair_archive_key.py).
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, SCOPES):
            continue
        yield child
        yield from _walk_own_scope(child)


def _assigned(fn) -> set[str]:
    names: set[str] = set()
    for n in _walk_own_scope(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(n.target, ast.Name):
                names.add(n.target.id)
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            if isinstance(n.target, ast.Name):
                names.add(n.target.id)
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for item in n.items:
                if isinstance(item.optional_vars, ast.Name):
                    names.add(item.optional_vars.id)
    return names


def _called(fn) -> set[str]:
    return {n.func.id for n in _walk_own_scope(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _declared_global(fn) -> set[str]:
    out: set[str] = set()
    for n in _walk_own_scope(fn):
        if isinstance(n, ast.Global):
            out.update(n.names)
    return out


def _files() -> list[Path]:
    return [p for p in sorted(ROOT.rglob("*.py"))
            if not (set(p.relative_to(ROOT).parts) & SKIP_DIRS)]


def _short(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name


def shadowed_calls(path: Path) -> list[str]:
    # ast.parse ругается на «\s» в обычной строке чужого файла — это его дело,
    # а не находка проверки; гасим, чтобы не шуметь в отчёте гейта
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            warnings.simplefilter("ignore", DeprecationWarning)
            tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return []
    top = {n.name for n in tree.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    bad: list[str] = []
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        clash = (_assigned(fn) & _called(fn) & top) - {fn.name} - _declared_global(fn)
        for name in sorted(clash):
            bad.append(f"{_short(path)}:{fn.name} — локальная {name!r} "
                       f"затирает функцию модуля {name}(), которая тут же вызывается")
    return bad


def test_repo_has_no_shadowed_calls():
    bad = [msg for p in _files() for msg in shadowed_calls(p)]
    assert not bad, "\n".join(bad)


def test_checker_catches_the_real_case(tmp_path):
    """Проверка обязана поймать ту самую ошибку, иначе она бесполезна."""
    src = tmp_path / "m.py"
    src.write_text(
        "def take(x):\n"
        "    return x\n"
        "\n"
        "def main():\n"
        "    take = [1, 2]\n"
        "    for i in take:\n"
        "        take(i)\n"
        "    return 0\n", encoding="utf-8")
    found = shadowed_calls(src)
    assert len(found) == 1 and "'take'" in found[0]


def test_checker_ignores_class_attribute(tmp_path):
    """Атрибут класса — своё пространство имён, тревоги быть не должно."""
    src = tmp_path / "m.py"
    src.write_text(
        "def status():\n"
        "    return 1\n"
        "\n"
        "def t():\n"
        "    class Reply:\n"
        "        status = 200\n"
        "    return status(), Reply.status\n", encoding="utf-8")
    assert shadowed_calls(src) == []


def test_checker_ignores_nested_function(tmp_path):
    """Вложенная функция — тоже свой кадр."""
    src = tmp_path / "m.py"
    src.write_text(
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def t():\n"
        "    def inner():\n"
        "        helper = 5\n"
        "        return helper\n"
        "    return helper() + inner()\n", encoding="utf-8")
    assert shadowed_calls(src) == []


def test_checker_allows_global_declaration(tmp_path):
    """`global` делает имя не локальным — это не затирание."""
    src = tmp_path / "m.py"
    src.write_text(
        "def cache():\n"
        "    return {}\n"
        "\n"
        "def t():\n"
        "    global cache\n"
        "    cache = cache()\n", encoding="utf-8")
    assert shadowed_calls(src) == []
