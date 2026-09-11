"""Правки инженеров в библиотеке ГТУ не должны теряться.

История, ради которой написаны эти проверки. Заметка инженера привязывалась к
нормализованному имени компании и жила только в localStorage браузера. В августе
2026 схлопывание дублей (#142, 1625 -> 1285 компаний) и склейка по базовому имени
(#148) выбросили карточки из выдачи вместе с их ключами — заметки остались в
хранилище, но показывать их стало негде. Инженеры увидели «комментарии пропали».

Каждый тест ниже закрывает одну из дыр, через которые правка может исчезнуть снова.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "gt" / "site"
PUBLIC = ROOT / "gt" / "public"
NOTES = SITE / "notes.js"

STORE_RE = re.compile(r"localStorage\.[gs]etItem\(['\"]([^'\"]+)['\"]")


def pages_with_store() -> dict[str, set[str]]:
    """Страницы библиотеки, где инженер что-то отмечает, и их ключи хранения."""
    out: dict[str, set[str]] = {}
    for f in sorted(SITE.glob("*.template.html")):
        keys = {k for k in STORE_RE.findall(f.read_text(encoding="utf-8"))
                if not k.startswith("gt_notes_")       # служебные ключи самого модуля
                and k != "gt_edits_before_import"}     # копия перед импортом, не хранилище
        if keys:
            out[f.name] = keys
    return out


def test_модуль_правок_на_месте():
    assert NOTES.exists(), "gt/site/notes.js — общий модуль правок; без него правки только в браузере"
    text = NOTES.read_text(encoding="utf-8")
    for fn in ("syncStore", "seedStore", "sweepLegacy", "migrate", "orphans", "signature"):
        assert fn in text, f"в модуле правок нет {fn}"


def test_модуль_знает_все_старые_ключи():
    """Ключ, о котором модуль не знает, — это правки, которые не переедут в общую базу."""
    known = NOTES.read_text(encoding="utf-8")
    for page, keys in pages_with_store().items():
        for k in keys:
            assert k in known, (
                f"ключ {k} со страницы {page} не перечислен в gt/site/notes.js: "
                "старые заметки инженеров из этого хранилища не будут найдены")


def test_страницы_подключают_модуль():
    """И шаблон, и готовая страница: для одиннадцати страниц ГТУ исходник деплоя — public."""
    for page in pages_with_store():
        tpl = (SITE / page).read_text(encoding="utf-8")
        assert "notes.js" in tpl, f"{page} не подключает общий модуль правок"
        built = PUBLIC / page.replace(".template", "")
        if built.exists():
            assert "notes.js" in built.read_text(encoding="utf-8"), (
                f"{built.name} собрана без модуля правок — пересоберите страницу")


def test_правки_уходят_в_общую_базу():
    for page in pages_with_store():
        tpl = (SITE / page).read_text(encoding="utf-8")
        assert "KVN.save" in tpl or "KVN.syncStore" in tpl, (
            f"{page}: правка никуда не отправляется, останется в одном браузере")


def test_перенос_заметок_при_склейке_дублей():
    """Ровно та строка, из-за которой всё и потерялось: карточку удаляют из выдачи."""
    tpl = (SITE / "index.template.html").read_text(encoding="utf-8")
    assert "function adoptEdits" in tpl, "нет переноса правок на выжившую карточку"
    for block in re.findall(r"map\.delete\(k\);", tpl):
        pass
    merges = re.findall(r"(adoptEdits\(k, keep\.key[^\n]*\n\s*map\.delete\(k\);)", tpl)
    assert len(merges) >= 2, (
        "в обеих функциях склейки (mergeByDomain, mergeByNameBase) перед map.delete "
        "должен стоять adoptEdits — иначе заметка снова осиротеет")


def test_потеря_видна_глазами():
    tpl = (SITE / "index.template.html").read_text(encoding="utf-8")
    assert "orphanNotes" in tpl and "pkOrphanBadge" in tpl, (
        "нет счётчика заметок без карточки: следующая дедупликация снова пройдёт бесшумно")


def test_сохранение_не_молчит_об_ошибке():
    tpl = (SITE / "index.template.html").read_text(encoding="utf-8")
    save = re.search(r"function saveEdits\(\)\s*\{(.*?)\n\}", tpl, re.S)
    assert save, "функция saveEdits не найдена"
    body = save.group(1)
    assert "catch(e){}" not in body.replace(" ", ""), (
        "пустой catch: переполнение квоты localStorage снова потеряет правку молча")


def test_импорт_правок_не_затирает_свои():
    tpl = (SITE / "index.template.html").read_text(encoding="utf-8")
    imp = re.search(r"function pkImportEdits\(inp\)\s*\{(.*?)\n\}", tpl, re.S)
    assert imp, "функция импорта правок не найдена"
    assert "Object.assign(EDITS" not in imp.group(1), (
        "импорт чужого файла перезаписывает свои заметки без отката")


def test_автора_проставляет_воркер():
    """Подпись, которую можно прислать из браузера, — не подпись."""
    w = (ROOT / "zip" / "site" / "_worker.js").read_text(encoding="utf-8")
    assert "stampAuthor" in w, "воркер не проставляет автора правки"
    assert "gt_notes" in w, "воркер не знает таблицу правок"
    assert '"/api/me"' in w, "нет ответа о вошедшем: странице нечего показать в подписи"
    assert "who.email" in w, "автор берётся не из подписи Cloudflare Access"


def test_правки_не_удаляются_физически():
    w = (ROOT / "zip" / "site" / "_worker.js").read_text(encoding="utf-8")
    assert 'request.method === "DELETE"' in w, (
        "воркер должен отбивать DELETE по правкам: снятая правка помечается, а не стирается")
    sql = (ROOT / "gt" / "supabase" / "migrations.sql").read_text(encoding="utf-8")
    assert "removed" in sql and "revoke delete" in sql.lower(), (
        "в схеме нет флага снятия и запрета на удаление")


def test_схема_общей_базы_полна():
    sql = (ROOT / "gt" / "supabase" / "migrations.sql").read_text(encoding="utf-8")
    for col in ("scope", "key", "kind", "text", "author", "at", "removed"):
        assert re.search(rf"^\s+{col}\b", sql, re.M), f"в таблице gt_notes нет колонки {col}"


def test_сборка_публикует_модуль():
    gt_build = (ROOT / "gt" / "build.py").read_text(encoding="utf-8")
    assert "notes.js" in gt_build, "gt/build.py не кладёт модуль правок рядом со страницами"
    zip_build = (ROOT / "zip" / "build.py").read_text(encoding="utf-8")
    assert '"*.js"' in zip_build or "'*.js'" in zip_build, (
        "zip/build.py публикует только *.html — модуль правок не доедет до сайта")


def test_ключи_хранения_не_переименованы():
    """Переименование ключа = мгновенная потеря всех правок во всех браузерах."""
    expected = {
        "index.template.html": "gt_edits_v2", "rfq.template.html": "gt_rfq_v1",
        "hot.template.html": "gt_hot_v1", "solar.template.html": "gt_solar_v1",
        "cummins.template.html": "gt_cummins_v1", "lm6000.template.html": "gt_lm6000_v1",
        "f4000.template.html": "gt_4000f_v1", "v643a.template.html": "gt_v643a_v1",
        "ms6001b.template.html": "gt_6b_v1",
    }
    have = pages_with_store()
    for page, key in expected.items():
        assert page in have, f"страница {page} потеряла хранилище правок"
        assert key in have[page], (
            f"{page}: ключ хранения изменился ({sorted(have[page])}), ожидался {key}. "
            "Переименование ключа обнуляет заметки инженеров в их браузерах.")


def test_модуль_синтаксически_цел():
    """Грубая проверка без node: баланс скобок и наличие экспорта."""
    t = NOTES.read_text(encoding="utf-8")
    assert t.count("{") == t.count("}"), "несбалансированные скобки в notes.js"
    assert t.count("(") == t.count(")"), "несбалансированные скобки в notes.js"
    assert "global.KVN" in t, "модуль ничего не экспортирует"
    json.dumps(t[:10])            # текст читается как обычная строка, без сюрпризов кодировки


def test_миграция_правок_применяется_первой():
    """Прогон №4 (11.09.2026): тяжёлая схема библиотеки упала на statement timeout,
    и из-за set -e до gt_notes очередь не дошла — общей базы правок не появилось.
    Мелкая схема правок должна применяться раньше тяжёлой и не зависеть от неё."""
    wf = (ROOT / ".github" / "workflows" / "zip-db.yml").read_text(encoding="utf-8")
    gt = wf.index("apply gt/supabase/migrations.sql")
    lib = wf.index("apply library/supabase/schema.sql")
    assert gt < lib, (
        "gt/supabase/migrations.sql применяется после library/supabase/schema.sql: "
        "падение тяжёлой схемы снова оставит правки инженеров без общей базы")
    assert "rc=1" in wf, (
        "неудача одного файла миграций отменяет остальные — нужен сбор кода возврата")
    sql = (ROOT / "library" / "supabase" / "schema.sql").read_text(encoding="utf-8")
    assert "statement_timeout" in sql, (
        "в схеме библиотеки не снят лимит времени запроса: перестройка колонок fts "
        "не укладывается в двухминутный дефолт Supabase")
