"""Поставщик письма контакта (indexer.MAIL_CONTACT_COMPANY, вход contact_company).

Что закреплено:
  • контакт с одной компанией → rfq_company строки цены = эта компания, а
    rfq_id остаётся «K<контакт>»;
  • с двумя компаниями и без компании → поставщик пуст (не угадываем), и так
    же — если число компаний проверялось вторым шагом (batch items.get);
  • один контакт спрашивается один раз, пятьдесят контактов — один запрос;
  • вход выключен — ни одного запроса и прежний результат; вход без цен или
    не у mail-supplier — ни одного запроса и «не действует» в журнале;
  • вход прогона объявлен выключенным и действует только вместе с prices.

Корпус придуман (CLAUDE.md, правило 18): номера контактов, компаний, писем и
файлов выдуманы. Портал подменяется заглушкой, которая запоминает вызовы.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
for каталог in ("library", "scripts"):
    sys.path.insert(0, str(ROOT / каталог))

from library import doc_side, mail_source as ms  # noqa: E402
from tests.test_mail_prices import (  # noqa: E402
    WORKFLOW, индексатор, письмо, по_имени, разобрать, шаг_разбора, xlsx_кп)


class ПорталКонтактов:
    """Заглушка клиента Битрикса: crm.contact.list по @ID и batch items.get.

    компании: контакт → список его компаний. со_списком — отдаёт ли список
    контактов COMPANY_IDS (иначе число компаний проверяется вторым шагом).
    """

    def __init__(self, компании: dict[int, list[int]], со_списком: bool = True):
        self.компании = компании
        self.со_списком = со_списком
        self.вызовы: list[tuple[str, dict]] = []

    def __call__(self, метод: str, параметры: dict) -> dict:
        if метод not in (ms.МЕТОД_КОНТАКТОВ, "batch"):
            # Прочее чтение разбора (наши компании и т. п.) — пусто и не в счёт:
            # считаются только запросы компании контакта.
            return {"result": []}
        self.вызовы.append((метод, параметры))
        if метод == ms.МЕТОД_КОНТАКТОВ:
            assert параметры["start"] == -1, "без подсчёта total"
            номера = параметры["filter"]["@ID"]
            assert len(номера) <= ms.ПАКЕТ_КОНТАКТОВ
            out = []
            for н in номера:
                if н not in self.компании:
                    continue
                к = self.компании[н]
                x = {"ID": str(н), "COMPANY_ID": str(к[0]) if к else "0"}
                if self.со_списком:
                    x["COMPANY_IDS"] = [str(c) for c in к]
                out.append(x)
            return {"result": out}
        if метод == "batch":
            ответы = {}
            for ключ, команда in параметры["cmd"].items():
                assert команда.startswith(ms.МЕТОД_КОМПАНИЙ_КОНТАКТА + "?id=")
                н = int(команда.split("=")[1])
                ответы[ключ] = [{"COMPANY_ID": c, "IS_PRIMARY": "N"}
                                for c in self.компании.get(н, [])]
            return {"result": {"result": ответы, "result_error": []}}


def индексатор_кк(monkeypatch, вход: bool, цены: bool = True, группа: str = "mail-supplier"):
    if вход:
        monkeypatch.setenv("MAIL_CONTACT_COMPANY", "1")
    else:
        monkeypatch.delenv("MAIL_CONTACT_COMPANY", raising=False)
    return индексатор(monkeypatch, цены=цены, группа=группа)


def цены_контакта(ix, monkeypatch, портал, контакт: int, n: int = 7101):
    """Ссылка письма контакта → дополнение компанией → разбор → строки цены."""
    monkeypatch.setattr(ix, "bx", портал)
    п = письмо(n, 9200 + n % 100, тип=ms.КОНТАКТ, владелец=контакт)
    refs = ms.ссылки_письма(п, "mail-supplier")
    журнал = ix.компании_контактов_части(refs)
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: xlsx_кп())
    rec, items = ix.handle(refs[0])
    return [по_имени(c) for c in ix.строки_цен(rec, items)], журнал, rec, items


# ─────────────────────────────────────────────── исход по числу компаний
@pytest.mark.parametrize("со_списком", [True, False], ids=["COMPANY_IDS", "batch"])
def test_одна_компания_становится_поставщиком(monkeypatch, со_списком):
    ix = индексатор_кк(monkeypatch, вход=True)
    портал = ПорталКонтактов({77: [555]}, со_списком=со_списком)
    строки, журнал, _rec, _items = цены_контакта(ix, monkeypatch, портал, 77)
    assert строки
    assert {с["rfq_company"] for с in строки} == {"555"}
    assert {с["rfq_id"] for с in строки} == {"K77"}, "привязка к письму не меняется"
    assert "с одной компанией 1" in журнал[0]
    assert len(портал.вызовы) == (1 if со_списком else 2)


@pytest.mark.parametrize("со_списком", [True, False], ids=["COMPANY_IDS", "batch"])
def test_две_компании_поставщик_пуст(monkeypatch, со_списком):
    ix = индексатор_кк(monkeypatch, вход=True)
    портал = ПорталКонтактов({78: [555, 556]}, со_списком=со_списком)
    строки, журнал, _rec, _items = цены_контакта(ix, monkeypatch, портал, 78)
    assert строки and {с["rfq_company"] for с in строки} == {None}
    assert "с несколькими 1" in журнал[0]


@pytest.mark.parametrize("со_списком", [True, False], ids=["COMPANY_IDS", "batch"])
def test_без_компании_поставщик_пуст(monkeypatch, со_списком):
    ix = индексатор_кк(monkeypatch, вход=True)
    портал = ПорталКонтактов({79: []}, со_списком=со_списком)
    строки, журнал, _rec, _items = цены_контакта(ix, monkeypatch, портал, 79)
    assert строки and {с["rfq_company"] for с in строки} == {None}
    assert "без компании 1" in журнал[0]
    # Без основной компании второго шага нет.
    assert [м for м, _ in портал.вызовы] == [ms.МЕТОД_КОНТАКТОВ]


def test_контакт_не_найден_поставщик_пуст(monkeypatch):
    ix = индексатор_кк(monkeypatch, вход=True)
    строки, журнал, _rec, _items = цены_контакта(ix, monkeypatch, ПорталКонтактов({}), 80)
    assert {с["rfq_company"] for с in строки} == {None}
    assert "не найдено 1" in журнал[0]


def test_ошибка_портала_не_роняет_часть(monkeypatch):
    def упал(метод, параметры):
        raise RuntimeError("выдуманный отказ")
    ix = индексатор_кк(monkeypatch, вход=True)
    строки, журнал, _rec, _items = цены_контакта(ix, monkeypatch, упал, 81)
    assert строки and {с["rfq_company"] for с in строки} == {None}
    assert "не проверено (ошибка портала) 1" in журнал[0]


# ─────────────────────────────────────────────── нагрузка
def test_один_контакт_один_раз_и_пятьдесят_на_запрос():
    """120 контактов, у каждого по два письма: три запроса списка, не 240."""
    портал = ПорталКонтактов({н: [10_000 + н] for н in range(1, 121)})
    refs = []
    for н in range(1, 121):
        for k in range(2):
            refs += ms.ссылки_письма(письмо(н * 10 + k, н * 10 + k, тип=ms.КОНТАКТ, владелец=н),
                                     "mail-supplier")
    журнал = ms.дополнить_компании_контактов(refs, портал)
    assert len(портал.вызовы) == 3
    assert sorted(len(п["filter"]["@ID"]) for _, п in портал.вызовы) == [20, 50, 50]
    assert all(r["company"] == str(10_000 + int(r["deal"][1:])) for r in refs)
    assert "контактов в части 120 · запросов к порталу 3" in журнал[0]
    assert "файлов писем контактов: 240 · получили поставщика 240" in журнал[1]


def test_письма_компаний_и_чужие_стороны_не_трогаются():
    портал = ПорталКонтактов({55: [1]})
    компании = ms.ссылки_письма(письмо(1, 1, тип=ms.КОМПАНИЯ, владелец=55), "mail-supplier")
    наше = ms.ссылки_письма(письмо(2, 2, тип=ms.КОНТАКТ, владелец=55,
                                   направление=ms.ИСХОДЯЩЕЕ), "mail-supplier")
    ms.дополнить_компании_контактов(компании + наше, портал)
    assert портал.вызовы == []
    assert компании[0]["company"] == "55" and наше[0]["company"] is None
    assert наше[0]["side"] != doc_side.ПОСТАВЩИК


# ─────────────────────────────────────────────── вход выключен / не действует
def test_вход_выключен_нет_запросов_и_прежний_результат(monkeypatch):
    итоги = []
    for вход in (False, True):
        ix = индексатор_кк(monkeypatch, вход=вход)
        портал = ПорталКонтактов({77: [555]})
        строки, журнал, rec, items = цены_контакта(ix, monkeypatch, портал, 77)
        if not вход:
            assert портал.вызовы == [] and журнал == []
            assert {с["rfq_company"] for с in строки} == {None}
        итоги.append((ix.кортеж_файла(rec, ix.КОЛОНКИ_ВСТАВКИ),
                      [(it["deal_id"], it["item_name"], it["source_file"]) for it in items],
                      [{к: в for к, в in с.items() if к != "rfq_company"} for с in строки]))
    # Вход меняет только поставщика строки цены: lib_files, спрос и прочие
    # колонки цены — те же.
    assert итоги[0] == итоги[1]


@pytest.mark.parametrize("цены, группа", [(False, "mail-supplier"), (True, "mail-deal"),
                                          (True, "mail-lead")])
def test_вход_без_цен_поставщиков_не_действует(monkeypatch, цены, группа):
    ix = индексатор_кк(monkeypatch, вход=True, цены=цены, группа=группа)
    портал = ПорталКонтактов({77: [555]})
    monkeypatch.setattr(ix, "bx", портал)
    refs = ms.ссылки_письма(письмо(7201, 9301, тип=ms.КОНТАКТ, владелец=77), "mail-supplier")
    журнал = ix.компании_контактов_части(refs)
    assert портал.вызовы == []
    assert refs[0]["company"] is None
    assert "не действует" in журнал[0]


def test_замер_до_и_после_только_числа(monkeypatch):
    ix = индексатор_кк(monkeypatch, вход=True)
    замер = ix.ЗамерЦен()
    портал = ПорталКонтактов({77: [555]})
    _с, _ж, rec, items = цены_контакта(ix, monkeypatch, портал, 77)
    замер.учесть(rec, items)
    rec2, items2, _ = разобрать(ix, monkeypatch, xlsx_кп(), письмо(7202, 9302), "mail-supplier")
    замер.учесть(rec2, items2)
    текст = "\n".join(замер.строки(False))
    assert "с поставщиком: до компании контакта 4 · после 8 (из компании контакта 4)" in текст
    assert "VYD" not in текст and "555" not in текст


# ─────────────────────────────────────────────── прогон
def выполнить(группа: str, цены: str, кк: str) -> tuple[str, str]:
    скрипт = (шаг_разбора()["run"].replace("${{ inputs.group }}", группа)
              .replace("python library/indexer.py",
                       'echo "MAIL_CONTACT_COMPANY=[${MAIL_CONTACT_COMPANY:-}]"'))
    env = {"PATH": os.environ.get("PATH", ""), "INPUT_RPS": "1.2", "SHARDS": "10",
           "INPUT_PRICES": цены, "INPUT_CONTACT_COMPANY": кк}
    out = subprocess.run(["bash", "-c", скрипт], env=env, capture_output=True, text=True,
                         check=True).stdout
    return [s for s in out.splitlines() if s.startswith("MAIL_CONTACT_COMPANY=")][-1], out


def test_вход_объявлен_выключенным():
    wf = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    вход = wf["on"]["workflow_dispatch"]["inputs"]["contact_company"]
    assert вход["type"] == "boolean" and вход["default"] == "false"
    assert "inputs.contact_company" in шаг_разбора()["env"]["INPUT_CONTACT_COMPANY"]


def test_вход_действует_только_с_ценами_поставщиков():
    assert выполнить("mail-supplier", "1", "1")[0] == "MAIL_CONTACT_COMPANY=[1]"
    assert выполнить("mail-supplier", "1", "")[0] == "MAIL_CONTACT_COMPANY=[]"
    for группа, цены in (("mail-supplier", ""), ("mail-deal", "1"), ("mail-lead", "")):
        значение, журнал = выполнить(группа, цены, "1")
        assert значение == "MAIL_CONTACT_COMPANY=[]"
        assert "вход contact_company не действует" in журнал
        значение, журнал = выполнить(группа, цены, "")
        assert "contact_company" not in журнал
