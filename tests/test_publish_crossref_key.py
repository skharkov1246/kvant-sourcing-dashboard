"""Каждый публикатор пишет ровно свой ключ KV и ничей больше.

ЗАЧЕМ. В одном пространстве KV лежат документ прав (acl:v1), библиотека
(library:v1), реестр поставщиков (suppliers:v1) и перекрёстная система
(crossref:v1). Токен Cloudflare один на всех, поэтому единственное, что
отделяет публикатор от чужого снимка, — закрытый список ключей в коде.

Список стал свойством класса, чтобы наследник задал СВОЙ ключ. Ошибка, ради
которой написан тест: наследник расширяет список вместо замены, и тогда каждый
публикатор может переписать чужой снимок. Из кода это выглядит безобидно.

Сеть здесь не нужна: проверяется построение пути, до запроса дело не доходит.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]
СЧЁТ = "0" * 32
ТОКЕН = "проверочный-токен"
ПРОСТРАНСТВО = "a" * 32


def модуль(имя, путь):
    spec = importlib.util.spec_from_file_location(имя, ROOT / путь)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def поставщики():
    return модуль("kvant_publish_suppliers_t", "scripts/publish_suppliers.py")


@pytest.fixture(scope="module")
def перекрёстный():
    return модуль("kvant_publish_crossref_t", "scripts/publish_crossref.py")


ЧУЖИЕ = ["acl:v1", "library:v1", "visits:v1", "", "../acl:v1", "crossref:v2"]


def test_публикатор_поставщиков_пишет_только_свой_ключ(поставщики):
    cf = поставщики.Cloudflare(СЧЁТ, ТОКЕН)
    # Двоеточие в пути экранировано — сверяем с экранированным написанием.
    assert quote(поставщики.KEY, safe="") in cf.value_path(ПРОСТРАНСТВО, поставщики.KEY)
    for ключ in [*ЧУЖИЕ, "crossref:v1"]:
        with pytest.raises(поставщики.PublishError) as отказ:
            cf.value_path(ПРОСТРАНСТВО, ключ)
        assert str(отказ.value) == "INVALID_KV_KEY"


def test_публикатор_перекрёстной_системы_пишет_только_свой_ключ(поставщики,
                                                                перекрёстный):
    from library import crossref

    class CloudflareCross(поставщики.Cloudflare):
        КЛЮЧИ = crossref.ВСЕ_КЛЮЧИ

    cf = CloudflareCross(СЧЁТ, ТОКЕН)
    for свой in crossref.ВСЕ_КЛЮЧИ:
        assert quote(свой, safe="") in cf.value_path(ПРОСТРАНСТВО, свой)
    # Номер части за пределами закрытого списка — чужой ключ, а не новая часть.
    за_краем = [f"crossref:list:{crossref.СПИСОК_ЧАСТЕЙ:02d}",
                f"crossref:offers:{crossref.КОРЗИН:02d}", "crossref:offers:3",
                "crossref:list:", "brands:codes:00"]
    for ключ in [*ЧУЖИЕ, поставщики.KEY, *за_краем]:
        with pytest.raises(поставщики.PublishError) as отказ:
            cf.value_path(ПРОСТРАНСТВО, ключ)
        assert str(отказ.value) == "INVALID_KV_KEY"


def test_списки_ключей_не_пересекаются(поставщики, перекрёстный):
    """Замена, а не расширение: пересечение означало бы общий доступ к снимкам."""
    from library import crossref
    свои = set(поставщики.Cloudflare.КЛЮЧИ)
    чужие = set(crossref.ВСЕ_КЛЮЧИ)
    assert not (свои & чужие), "публикатор поставщиков дотягивается до crossref:v1"
    assert len(свои) == 1, "список ключей публикатора поставщиков перестал быть один"


def test_наследник_объявлен_заменой_списка(перекрёстный):
    """В самом скрипте список задан присваиванием КЛЮЧИ, а не добавлением.

    Тест выше проверяет класс, собранный здесь же; этот — что в скрипте написано
    то же самое. Без него скрипт мог бы расширять список, а тест — не заметить.
    """
    текст = (ROOT / "scripts" / "publish_crossref.py").read_text(encoding="utf-8")
    assert "КЛЮЧИ = crossref.ВСЕ_КЛЮЧИ" in текст
    assert "КЛЮЧИ +" not in текст and "КЛЮЧИ = ps.Cloudflare.КЛЮЧИ" not in текст
    assert перекрёстный.KEY == "crossref:v1", "имя заголовка сменилось — страницы его не найдут"


def test_закрытый_список_тот_же_что_в_воркере():
    """Числа частей и корзин в воркере и в сборщике — одни.

    Разойдись они — страница попросит корзину, которую воркер не знает (400),
    или воркер разрешит ключ, который никто не пишет.
    """
    import re
    from library import crossref
    воркер = (ROOT / "public" / "_worker.js").read_text(encoding="utf-8")
    код = "\n".join(ln for ln in воркер.splitlines() if not ln.lstrip().startswith("//"))
    lists = re.search(r"const CROSSREF_LISTS = (\d+);", код)
    parts = re.search(r"const CROSSREF_PARTS = (\d+);", код)
    assert lists and int(lists.group(1)) == crossref.СПИСОК_ЧАСТЕЙ
    assert parts and int(parts.group(1)) == crossref.КОРЗИН
    assert '"crossref:list:"' in код and '"crossref:offers:"' in код


def test_вхолостую_раскладывает_все_ключи(перекрёстный, tmp_path, monkeypatch, capsys):
    """Прогон без записи сохраняет все ключи файлами и печатает только агрегаты."""
    import datetime as dt
    строка = ("6205", "6-205", "Учебный подшипник", "101", "KV-S-000001-1", "Учебный завод",
              None, "SKF", 10, "EUR", 1, "шт", None, None, None, None, None, None,
              None, None, None, None, "RFQ-1", "med", dt.date(2026, 9, 1), None)
    monkeypatch.setattr(перекрёстный, "читать_базу",
                        lambda dsn: ([строка], [], [], [], [], []))
    assert перекрёстный.main(["--out", str(tmp_path)]) == 0
    from library import crossref
    имена = {p.name for p in tmp_path.iterdir()}
    assert имена == {k.replace(":", "_") + ".json" for k in crossref.ВСЕ_КЛЮЧИ}
    журнал = capsys.readouterr().out
    assert "заголовок crossref:v1" in журнал and "корзина, самая тяжёлая" in журнал
    # Правило 17: в журнале ни наименований, ни имён компаний.
    assert "Учебный подшипник" not in журнал and "Учебный завод" not in журнал
