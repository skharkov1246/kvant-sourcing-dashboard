"""Владелец — участник чатов реализации (scripts/chat_join.py) — без сети.

Портал подменён: проверяется отбор (только открытая реализация с чатом), проба
доступа, что dry ничего не пишет, что live без права imopenlines не начинает,
пробные добавления останавливают прогон, повторная проба после добавления и что
в журнал не уходят ни номера сделок и чатов, ни номер владельца. Корпус придуман
(правило 18).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import chat_join as cj  # noqa: E402
from tests import test_chat_context as tcc  # noqa: E402  (подмена портала сборки — та же, что у пилота чатов)

ВЛАДЕЛЕЦ = 9876543
АДРЕС = f"https://выдумка.invalid/rest/{ВЛАДЕЛЕЦ}/кодвыдумка/"


class BitrixLimitError(RuntimeError):
    """Как bitrix_client.BitrixLimitError — узнаётся по имени класса."""


class Владелец:
    """Вебхук владельца: scope, чтение чатов, добавление в чат сделки."""

    def __init__(self, права=("im", "imopenlines"), доступ=(), добавить="ok", лимит_на=None):
        self.права = list(права)
        self.доступ = set(доступ)
        self.добавить = добавить
        self.лимит_на = лимит_на
        self.вызовы: list[tuple[str, dict]] = []

    def call(self, method, params, **kw):
        self.вызовы.append((method, dict(params)))
        if method == "scope":
            return list(self.права)
        if method == "im.dialog.messages.get":
            чат = params["DIALOG_ID"].removeprefix("chat")
            if чат == self.лимит_на:
                raise BitrixLimitError("лимит")
            if чат not in self.доступ:
                raise RuntimeError("im.dialog.messages.get: ACCESS_ERROR You do not have access to this chat")
            return {"messages": [{"id": i} for i in range(min(params["LIMIT"], 3))]}
        if method == "imopenlines.crm.chat.user.add":
            assert params["CRM_ENTITY_TYPE"] == "deal" and params["USER_ID"] == ВЛАДЕЛЕЦ
            if self.добавить == "ok":
                self.доступ.add(str(params["CHAT_ID"]))
                return params["CHAT_ID"]
            raise RuntimeError(f"imopenlines.crm.chat.user.add: {self.добавить} описание")
        raise AssertionError(f"неожиданный вызов {method}")


def _журнал() -> tuple[list[str], callable]:
    строки: list[str] = []
    return строки, строки.append


def test_номер_владельца_из_адреса():
    assert cj.номер_владельца(АДРЕС) == ВЛАДЕЛЕЦ
    assert cj.номер_владельца("https://выдумка.invalid/rest/") is None
    assert cj.номер_владельца("") is None


def test_dry_считает_и_ничего_не_пишет():
    владелец = Владелец(права=("im",), доступ={tcc.ЧАТ_Р})
    журнал, печать = _журнал()
    код = cj.прогон("dry", 300, 10, сборка=tcc.Портал({}), владелец_клиент=владелец, владелец=ВЛАДЕЛЕЦ,
                    печать=печать)
    текст = "\n".join(журнал)
    assert код == 0
    assert not [м for м, _ in владелец.вызовы if м == "imopenlines.crm.chat.user.add"], "dry не пишет в портал"
    # отбор: две открытые сделки реализации с чатом (оплаченная и без чата — мимо), пресейл не берётся
    assert "открытых сделок с чатом 2 · без id чата 1 · закрытых и «Оплата получена» отсеяно 1" in текст
    assert {п["DIALOG_ID"] for м, п in владелец.вызовы if м == "im.dialog.messages.get"} == \
        {f"chat{tcc.ЧАТ_Р}", f"chat{tcc.ЧАТ_Р2}"}
    assert "итог: доступ есть 1 · нет доступа 1" in текст
    assert "добавление в чат сделки (imopenlines) — нет" in текст and "сначала право" in текст


def test_live_без_права_не_начинает():
    владелец = Владелец(права=("im",))
    журнал, печать = _журнал()
    код = cj.прогон("live", 300, 10, сборка=tcc.Портал({}), владелец_клиент=владелец, владелец=ВЛАДЕЛЕЦ,
                    печать=печать)
    assert код == 2 and any("нет права «Открытые линии" in s for s in журнал)
    assert [м for м, _ in владелец.вызовы] == ["scope"], "без права — ни пробы, ни добавления"


def test_live_добавляет_где_нет_доступа_и_проверяет_чтение():
    владелец = Владелец(доступ={tcc.ЧАТ_Р})
    журнал, печать = _журнал()
    код = cj.прогон("live", 300, 10, сборка=tcc.Портал({}), владелец_клиент=владелец, владелец=ВЛАДЕЛЕЦ,
                    печать=печать)
    текст = "\n".join(журнал)
    assert код == 0
    добавлено = [п for м, п in владелец.вызовы if м == "imopenlines.crm.chat.user.add"]
    assert [(п["CRM_ENTITY"], п["CHAT_ID"]) for п in добавлено] == [(int(tcc.СДЕЛКА_Р2), int(tcc.ЧАТ_Р2))]
    assert "добавлено 1 из 1 · после добавления читается 1 (история видна 1)" in текст
    # журнал — только агрегаты
    for запретное in (tcc.СДЕЛКА_Р, tcc.СДЕЛКА_Р2, tcc.ЧАТ_Р, tcc.ЧАТ_Р2, str(ВЛАДЕЛЕЦ), "выдумка.invalid"):
        assert запретное not in текст, f"в журнал попало «{запретное}»"


def test_пробные_добавления_не_удались_стоп():
    real = [{"ID": str(8800000 + i), "CATEGORY_ID": "0", "STAGE_ID": "C0:WORK", "STAGE_SEMANTIC_ID": "P",
             tcc.cc.chat_activity.CHAT_FIELD: str(7700000 + i)} for i in range(6)]
    владелец = Владелец(добавить="CHAT_NOT_IN_CRM")
    журнал, печать = _журнал()
    код = cj.прогон("live", 300, 10, сборка=tcc.Портал({}, real=real), владелец_клиент=владелец,
                    владелец=ВЛАДЕЛЕЦ, печать=печать)
    текст = "\n".join(журнал)
    assert код == 1 and "::error::остановлено: пробные добавления не удались" in текст
    assert len([м for м, _ in владелец.вызовы if м == "imopenlines.crm.chat.user.add"]) == cj.ПРОБА
    assert "отказы: чат не привязан к сделке в CRM 3" in текст and "не дошли 3" in текст


def test_лимит_портала_останавливает():
    владелец = Владелец(лимит_на=tcc.ЧАТ_Р)
    журнал, печать = _журнал()
    код = cj.прогон("dry", 300, 10, сборка=tcc.Портал({}), владелец_клиент=владелец, владелец=ВЛАДЕЛЕЦ,
                    печать=печать)
    assert код == 1 and any("остановлено: лимит портала" in s for s in журнал)


def test_без_номера_владельца_не_начинает():
    журнал, печать = _журнал()
    assert cj.прогон("dry", 300, 10, сборка=tcc.Портал({}), владелец_клиент=Владелец(), владелец=None,
                     печать=печать) == 2


def test_классы_отказа():
    assert cj.род_отказа(RuntimeError("x: insufficient_scope The request requires")) == \
        "нет права imopenlines у вебхука"
    assert cj.род_отказа(RuntimeError("x: WRONG_REQUEST You don't have access")) == "уже участник или нет доступа"
    assert cj.род_отказа(BitrixLimitError("лимит")) == "лимит портала"
    assert cj.род_отказа(RuntimeError("x: что-то иное")) == "сбой"
