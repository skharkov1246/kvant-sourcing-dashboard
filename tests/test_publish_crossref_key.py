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
    class CloudflareCross(поставщики.Cloudflare):
        КЛЮЧИ = (перекрёстный.KEY,)

    cf = CloudflareCross(СЧЁТ, ТОКЕН)
    assert quote(перекрёстный.KEY, safe="") in cf.value_path(ПРОСТРАНСТВО,
                                                              перекрёстный.KEY)
    for ключ in [*ЧУЖИЕ, поставщики.KEY]:
        with pytest.raises(поставщики.PublishError) as отказ:
            cf.value_path(ПРОСТРАНСТВО, ключ)
        assert str(отказ.value) == "INVALID_KV_KEY"


def test_списки_ключей_не_пересекаются(поставщики, перекрёстный):
    """Замена, а не расширение: пересечение означало бы общий доступ к снимкам."""
    свои = set(поставщики.Cloudflare.КЛЮЧИ)
    чужие = {перекрёстный.KEY}
    assert not (свои & чужие), "публикатор поставщиков дотягивается до crossref:v1"
    assert len(свои) == 1, "список ключей публикатора поставщиков перестал быть один"


def test_наследник_объявлен_заменой_списка(перекрёстный):
    """В самом скрипте список задан присваиванием КЛЮЧИ, а не добавлением.

    Тест выше проверяет класс, собранный здесь же; этот — что в скрипте написано
    то же самое. Без него скрипт мог бы расширять список, а тест — не заметить.
    """
    текст = (ROOT / "scripts" / "publish_crossref.py").read_text(encoding="utf-8")
    assert "КЛЮЧИ = (KEY,)" in текст
    assert "КЛЮЧИ +" not in текст and "КЛЮЧИ = ps.Cloudflare.КЛЮЧИ" not in текст
