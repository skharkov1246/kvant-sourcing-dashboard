"""Публикатор брендов: свои ключи KV закрытым списком и журнал без имён.

ЗАЧЕМ. В одном пространстве KV лежат документ прав, реестр поставщиков,
номенклатура и счётчики, а токен Cloudflare один на всех. Отделяет публикатор
от чужого снимка только закрытый список ключей в коде. Второе — журнал:
репозиторий публичный, и журнал Actions видит любой (CLAUDE.md, правило 17). В
него идут числа, размеры и заполненность по полям — ни имени бренда с числами,
ни имени компании, ни кода.

Сеть и база здесь не нужны: чтение базы и Битрикса подменено корпусом.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import quote

import pytest

from library import brands
from tests import test_brands_snapshot as корпус

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
    return модуль("kvant_publish_suppliers_b", "scripts/publish_suppliers.py")


@pytest.fixture(scope="module")
def публикатор():
    return модуль("kvant_publish_brands_t", "scripts/publish_brands.py")


def test_публикатор_брендов_пишет_только_свои_ключи(поставщики):
    class CloudflareBrands(поставщики.Cloudflare):
        КЛЮЧИ = brands.ВСЕ_КЛЮЧИ

    cf = CloudflareBrands(СЧЁТ, ТОКЕН)
    for ключ in brands.ВСЕ_КЛЮЧИ:
        assert quote(ключ, safe="") in cf.value_path(ПРОСТРАНСТВО, ключ)
    for ключ in ["acl:v1", "library:v1", "suppliers:v1", "crossref:v1", "counters:v1",
                 "brands:codes:16", "brands:codes:3", "brands:v2", "", "../acl:v1"]:
        with pytest.raises(поставщики.PublishError) as отказ:
            cf.value_path(ПРОСТРАНСТВО, ключ)
        assert str(отказ.value) == "INVALID_KV_KEY"


def test_список_ключей_полный_и_без_чужих():
    assert brands.ВСЕ_КЛЮЧИ[:3] == ("brands:v1", "brands:links:v1", "brands:pairs:v1")
    assert len(brands.КЛЮЧИ_КОРЗИН) == brands.КОРЗИН == 16
    assert all(k.startswith("brands:") for k in brands.ВСЕ_КЛЮЧИ)
    assert len(set(brands.ВСЕ_КЛЮЧИ)) == len(brands.ВСЕ_КЛЮЧИ)


def test_наследник_объявлен_заменой_списка():
    текст = (ROOT / "scripts" / "publish_brands.py").read_text(encoding="utf-8")
    assert "КЛЮЧИ = brands.ВСЕ_КЛЮЧИ" in текст
    assert "КЛЮЧИ +" not in текст and "+ brands.ВСЕ_КЛЮЧИ" not in текст


def test_вхолостую_журнал_без_имён_а_ключи_в_файлах(публикатор, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(публикатор, "читать_базу",
                        lambda dsn, карта: (корпус.КОДЫ, корпус.КАТАЛОГ, {"brands": 0.1}))
    monkeypatch.setattr(публикатор, "имена_из_битрикса",
                        lambda коды, url: ({"101": "Альфа-Коготь ООО"}, {"501": "SKF"}))
    assert публикатор.main(["--out", str(tmp_path)]) == 0
    журнал = capsys.readouterr().out
    # Числа, размеры и заполненность — есть.
    assert "брендов:" in журнал and "размер brands:v1" in журнал
    assert "заполненность карточки" in журнал
    assert "в KV ничего не записано" in журнал
    # Имён компаний, брендов с числами и кодов — нет.
    for секрет in ["Альфа-Коготь", "alfaclaw", "Акмеро", "AB-6205", "ab6205", "KL-7",
                   "Выдуманная машина", "Разведанная компания", "KV-S-000001-1"]:
        assert секрет not in журнал, секрет
    файлы = {p.name for p in tmp_path.iterdir()}
    assert {k.replace(":", "_") + ".json" for k in brands.ВСЕ_КЛЮЧИ} == файлы


def test_переполнение_даёт_свой_код_возврата(публикатор, поставщики, monkeypatch):
    monkeypatch.setattr(публикатор, "читать_базу",
                        lambda dsn, карта: (корпус.КОДЫ, корпус.КАТАЛОГ, {}))
    monkeypatch.setattr(публикатор, "имена_из_битрикса", lambda коды, url: ({}, {}))
    ps = публикатор._публикатор()
    monkeypatch.setattr(ps, "MAX_BYTES", 100)
    monkeypatch.setattr(публикатор, "_публикатор", lambda: ps)
    assert публикатор.main([]) == публикатор.КОД_ПЕРЕПОЛНЕНИЯ
