"""Последний рубеж чтения: файл, который не прочитал никто, читает модель.

Это единственный читатель, который стоит денег за каждый файл, поэтому здесь
проверяется прежде всего то, что защищает деньги: включение только явным
LLM_READ=1, кэш по sha256 (второй вызов не идёт в API), обрезка по пределу
страниц, пределы цены и бюджета, названная причина вместо исключения.

НАСТОЯЩИХ ВЫЗОВОВ API НЕТ: клиент подставной, он записывает запросы и отдаёт
заготовленные ответы. Корпуса придуманы (правило 18), а не сняты с базы.
"""
from __future__ import annotations

import base64
import io
import json
import sys
from types import SimpleNamespace

import pytest

from library import read_llm


# --------------------------------------------------------------------------- подстава
class Поток:
    def __init__(self, ответ):
        self.ответ = ответ

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get_final_message(self):
        return self.ответ


class Сообщения:
    """Сценарий: по шагу на вызов; последний шаг повторяется. Исключение в
    сценарии бросается, как бросил бы SDK."""

    def __init__(self, сценарий):
        self.сценарий = list(сценарий)
        self.запросы: list[dict] = []

    def stream(self, **запрос):
        self.запросы.append(запрос)
        шаг = self.сценарий.pop(0) if len(self.сценарий) > 1 else self.сценарий[0]
        if isinstance(шаг, BaseException):
            raise шаг
        return Поток(шаг)


class Клиент:
    def __init__(self, *сценарий):
        self.messages = Сообщения(сценарий)


def ответ(данные, стоп: str = "end_turn", вход: int = 30_000, выход: int = 8_000):
    текст = данные if isinstance(данные, str) else json.dumps(данные, ensure_ascii=False)
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=текст)],
        stop_reason=стоп,
        usage=SimpleNamespace(input_tokens=вход, output_tokens=выход,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0))


class APIConnectionError(Exception):
    """Имя как у ошибки SDK: разбор ошибок смотрит на имя класса."""


class Отказ400(Exception):
    """Как APIStatusError SDK: message — обёртка, суть — в body.error.message."""
    status_code = 400
    message = "Error code: 400 - {'type': 'error', ...}"
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "PDF could not be parsed"}}


ОТВЕТ_КП = {
    "tables": [
        {"header": ["№", "Наименование", "Кол-во", "Цена"],
         "rows": [["1", "Подшипник ВЫДУМ-101\nк насосу", "4", "1 200,00"],
                  ["2", "Уплотнение ВЫДУМ-202", None, "[неразборчиво]"],
                  ["", "", "", ""]]},
        {"header": ["Итого"], "rows": [["4 800,00"]]},
    ],
    "text": "КП № 7 от 01.02.2026\n[[ТАБЛИЦА 1]]\nУсловия: DAP [рукописно: Пермь]\n[печать: ООО Выдумка]",
}


def pdf_на(страниц: int) -> bytes:
    from pypdf import PdfWriter
    писатель = PdfWriter()
    for _ in range(страниц):
        писатель.add_blank_page(width=595, height=842)
    буфер = io.BytesIO()
    писатель.write(буфер)
    return буфер.getvalue()


def страниц_в(данные_b64: str) -> int:
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(base64.standard_b64decode(данные_b64))).pages)


@pytest.fixture(autouse=True)
def чистая_среда(monkeypatch, tmp_path):
    """Каждая проверка — с нуля: своё окружение, свой каталог кэша, пустая сводка,
    без пауз между повторами."""
    for имя in ("LLM_READ", "LLM_READ_MODEL", "LLM_READ_MAX_PAGES", "LLM_READ_MAX_MB", "LLM_READ_MAX_USD_FILE",
                "LLM_READ_BUDGET_USD", "LLM_READ_EFFORT", "LLM_READ_RETRIES", "LLM_READ_RETRY_FAILED",
                "LLM_READ_MAX_CHARS"):
        monkeypatch.delenv(имя, raising=False)
    monkeypatch.setenv("LLM_READ_CACHE", str(tmp_path / "кэш"))
    monkeypatch.setattr(read_llm.time, "sleep", lambda _с: None)
    read_llm.сбросить()
    yield
    read_llm.сбросить()


@pytest.fixture
def включено(monkeypatch):
    monkeypatch.setenv("LLM_READ", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ключ-для-проверки")


def подставить(monkeypatch, клиент) -> Клиент:
    monkeypatch.setattr(read_llm, "_создать_клиента", lambda: клиент)
    return клиент


# --------------------------------------------------------------------------- включение
@pytest.mark.parametrize("значение", [None, "", "0", "true", "yes", "да"])
def test_без_явной_единицы_в_api_не_ходит(monkeypatch, tmp_path, значение):
    """Расход открывает только LLM_READ=1: опечатка в прогоне не должна платить."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ключ-для-проверки")
    if значение is not None:
        monkeypatch.setenv("LLM_READ", значение)
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    строки, текст, причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")
    assert (строки, текст) == ([], "")
    assert "LLM_READ=1" in причина
    assert клиент.messages.запросы == []
    assert not (tmp_path / "кэш").exists()


def test_единица_включает(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    строки, _, причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")
    assert строки and причина == ""
    assert len(клиент.messages.запросы) == 1


# --------------------------------------------------------------------------- ключ и пакет
def test_без_ключа_названная_причина(monkeypatch):
    monkeypatch.setenv("LLM_READ", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    вызван = []
    monkeypatch.setattr(read_llm, "_создать_клиента", lambda: вызван.append(1))
    строки, текст, причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")
    assert (строки, текст) == ([], "")
    assert "ANTHROPIC_API_KEY" in причина
    assert not вызван
    assert read_llm.итог()["отказов"] == {"нет ключа": 1}


def test_без_пакета_названная_причина(monkeypatch, включено):
    """Настоящая фабрика клиента, но пакета нет: причина, а не ImportError."""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    строки, _, причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")
    assert строки == []
    assert "нет пакета anthropic" in причина


# --------------------------------------------------------------------------- разбор ответа
def test_json_ответа_раскладывается_в_строки(monkeypatch, включено):
    подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    строки, текст, причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")
    assert причина == ""
    assert строки == [
        ["№", "Наименование", "Кол-во", "Цена"],
        ["1", "Подшипник ВЫДУМ-101 к насосу", "4", "1 200,00"],   # перевод строки в ячейке склеен
        ["2", "Уплотнение ВЫДУМ-202", "", "[неразборчиво]"],     # None — пустая ячейка
        ["Итого"],                                                # пустая строка таблицы выброшена
        ["4 800,00"],
    ]
    # Полный текст: таблица 1 встала на место метки, таблица 2 без метки — в хвост.
    assert текст.splitlines() == [
        "КП № 7 от 01.02.2026",
        "№\tНаименование\tКол-во\tЦена",
        "1\tПодшипник ВЫДУМ-101 к насосу\t4\t1 200,00",
        "2\tУплотнение ВЫДУМ-202\t\t[неразборчиво]",
        "Условия: DAP [рукописно: Пермь]",
        "[печать: ООО Выдумка]",
        "Итого",
        "4 800,00",
    ]
    assert read_llm.итог()["неразборчиво"] == 1                # одно место, а не два: таблица есть и в тексте


@pytest.mark.parametrize("сырой", [
    "```json\n" + json.dumps(ОТВЕТ_КП, ensure_ascii=False) + "\n```",
    "Вот данные: " + json.dumps(ОТВЕТ_КП, ensure_ascii=False) + " Готово.",
])
def test_json_в_ограде_и_в_прозе_тоже_разбирается(сырой):
    строки, _, причина = read_llm.разобрать_ответ(сырой)
    assert причина == "" and строки[1][1] == "Подшипник ВЫДУМ-101 к насосу"


def test_не_json_это_причина_а_не_исключение():
    assert read_llm.разобрать_ответ("не могу прочитать") == ([], "", "ответ модели не разобран как JSON")


def test_запрос_строгий_json_документ_перед_просьбой(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    read_llm.прочитать_моделью(pdf_на(1), "pdf")
    запрос = клиент.messages.запросы[0]
    assert запрос["model"] == "claude-sonnet-5"
    assert запрос["output_config"]["format"] == {"type": "json_schema", "schema": read_llm.СХЕМА}
    assert запрос["output_config"]["effort"] == "low"
    содержимое = запрос["messages"][0]["content"]
    assert содержимое[0]["type"] == "document"
    assert содержимое[0]["source"]["media_type"] == "application/pdf"
    assert содержимое[-1]["type"] == "text"


def test_haiku_без_effort(monkeypatch, включено):
    """Haiku 4.5 на effort отвечает 400 — параметр ему не шлётся."""
    monkeypatch.setenv("LLM_READ_MODEL", "claude-haiku-4-5-20251001")
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    read_llm.прочитать_моделью(pdf_на(1), "pdf")
    assert "effort" not in клиент.messages.запросы[0]["output_config"]


def test_картинка_идёт_изображением(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    read_llm.прочитать_моделью(png, "неизвестно")          # подпись важнее переданного подвида
    блок = клиент.messages.запросы[0]["messages"][0]["content"][0]
    assert блок["type"] == "image" and блок["source"]["media_type"] == "image/png"


def test_pdf_с_чужим_подвидом_идёт_документом(monkeypatch, включено):
    """Подпись %PDF главнее переданного подвида: PDF, опознанный как «неизвестно»,
    иначе ушёл бы модели текстом из байтов — дорого и бесполезно."""
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    read_llm.прочитать_моделью(pdf_на(1), "неизвестно")
    блок = клиент.messages.запросы[0]["messages"][0]["content"][0]
    assert блок["type"] == "document" and блок["source"]["media_type"] == "application/pdf"


def test_прочее_идёт_текстом(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    read_llm.прочитать_моделью("Позиция;Кол-во\nШайба ВЫДУМ;12\n".encode("cp1251"), "csv")
    блок = клиент.messages.запросы[0]["messages"][0]["content"][0]
    assert блок["source"]["type"] == "text"
    assert "Шайба ВЫДУМ" in блок["source"]["data"]


def test_двоичный_мусор_модели_не_отдаётся(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    строки, _, причина = read_llm.прочитать_моделью(bytes(range(256)) * 20, "неизвестно")
    assert строки == [] and "отдать нечего" in причина
    assert клиент.messages.запросы == []


# --------------------------------------------------------------------------- кэш
def test_второй_вызов_берётся_из_кэша(monkeypatch, включено, tmp_path):
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    pdf = pdf_на(1)
    первый = read_llm.прочитать_моделью(pdf, "pdf")
    второй = read_llm.прочитать_моделью(pdf, "pdf")
    assert первый == второй and первый[0]
    assert len(клиент.messages.запросы) == 1
    import hashlib
    ключ = hashlib.sha256(pdf).hexdigest()
    assert (tmp_path / "кэш" / ключ[:2] / f"{ключ}.json").exists()
    и = read_llm.итог()
    assert (и["вызовов"], и["из_кэша"], и["прочитано"]) == (1, 1, 2)


def test_кэш_работает_и_без_ключа(monkeypatch, включено):
    """Оплаченный ответ достаётся даром и тогда, когда ключа в прогоне уже нет."""
    подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    pdf = pdf_на(1)
    read_llm.прочитать_моделью(pdf, "pdf")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    строки, _, причина = read_llm.прочитать_моделью(pdf, "pdf")
    assert строки and причина == ""


def test_оплаченный_отказ_кэшируется_повтор_по_просьбе(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(ответ("", стоп="refusal")))
    pdf = pdf_на(1)
    assert "refusal" in read_llm.прочитать_моделью(pdf, "pdf")[2]
    assert "из кэша" in read_llm.прочитать_моделью(pdf, "pdf")[2]
    assert len(клиент.messages.запросы) == 1
    monkeypatch.setenv("LLM_READ_RETRY_FAILED", "1")
    read_llm.прочитать_моделью(pdf, "pdf")
    assert len(клиент.messages.запросы) == 2


def test_обрезанный_ответ_называется(monkeypatch, включено):
    подставить(monkeypatch, Клиент(ответ('{"tables": [{"header": ["№"', стоп="max_tokens")))
    причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")[2]
    assert "обрезан пределом" in причина


# --------------------------------------------------------------------------- пределы
def test_pdf_обрезается_по_пределу_страниц(monkeypatch, включено):
    monkeypatch.setenv("LLM_READ_MAX_PAGES", "2")
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    строки, _, причина = read_llm.прочитать_моделью(pdf_на(5), "pdf")
    assert строки
    assert "2 стр. из 5" in причина                          # неполнота названа, статус не врёт
    запрос = клиент.messages.запросы[0]
    assert страниц_в(запрос["messages"][0]["content"][0]["source"]["data"]) == 2
    assert запрос["max_tokens"] == 4_000 + 2 * 3_000
    assert read_llm.итог()["страниц"] == 2


def test_pdf_в_пределе_уходит_как_есть(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    pdf = pdf_на(3)
    read_llm.прочитать_моделью(pdf, "pdf")
    assert base64.standard_b64decode(клиент.messages.запросы[0]["messages"][0]["content"][0]["source"]["data"]) == pdf


def test_предел_цены_файла(monkeypatch, включено):
    monkeypatch.setenv("LLM_READ_MAX_USD_FILE", "0.01")
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    причина = read_llm.прочитать_моделью(pdf_на(3), "pdf")[2]
    assert "LLM_READ_MAX_USD_FILE" in причина and клиент.messages.запросы == []


def test_бюджет_прогона(monkeypatch, включено):
    """Первый файл укладывается, второй уже нет: бюджет считает потраченное."""
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП, вход=40_000, выход=15_000)))
    monkeypatch.setenv("LLM_READ_BUDGET_USD", "0.30")
    assert read_llm.прочитать_моделью(pdf_на(1), "pdf")[0]
    причина = read_llm.прочитать_моделью(pdf_на(2), "pdf")[2]
    assert "LLM_READ_BUDGET_USD" in причина
    assert len(клиент.messages.запросы) == 1


def test_предел_размера(monkeypatch, включено):
    monkeypatch.setenv("LLM_READ_MAX_MB", "0.0001")
    клиент = подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")[2]
    assert "LLM_READ_MAX_MB" in причина and клиент.messages.запросы == []


def test_цена_десяти_страниц_как_в_комментарии():
    """Оценка в коде и цифры в шапке модуля — одно и то же: верх ≈ $0,31."""
    _, оценка, _ = read_llm.собрать_запрос(pdf_на(10), "pdf")
    assert оценка["страниц"] == 10
    assert оценка["вход"] == 10 * 7_784 + 1_200
    assert оценка["долларов"] == pytest.approx(0.308, abs=0.005)
    assert оценка["потолок"] < 1.0                           # помещается в предел файла по умолчанию


# --------------------------------------------------------------------------- сеть
def test_сбой_сети_лечится_повтором(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(APIConnectionError(), APIConnectionError(), ответ(ОТВЕТ_КП)))
    строки, _, причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")
    assert строки and причина == ""
    assert len(клиент.messages.запросы) == 3
    assert read_llm.итог()["повторов"] == 2


def test_сбой_сети_до_конца_причина_и_не_в_кэш(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(APIConnectionError()))
    pdf = pdf_на(1)
    строки, текст, причина = read_llm.прочитать_моделью(pdf, "pdf")
    assert (строки, текст) == ([], "")
    assert "сбой сети" in причина and "попыток 3" in причина
    read_llm.прочитать_моделью(pdf, "pdf")                    # сетевой сбой не оплачен — пробуем снова
    assert len(клиент.messages.запросы) == 6


def test_отказ_api_не_повторяется(monkeypatch, включено):
    клиент = подставить(monkeypatch, Клиент(Отказ400()))
    причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")[2]
    assert причина == "модель: API отказал (400): PDF could not be parsed"
    assert len(клиент.messages.запросы) == 1


# --------------------------------------------------------------------------- без исключений
@pytest.mark.parametrize("b, подвид", [(b"", "pdf"), (None, "pdf"), (b"%PDF-1.4 broken", None),
                                       (b"PK\x03\x04" + b"\x00" * 30, "zip"), (b"Rar!\x1a\x07", "rar")])
def test_никогда_не_бросает(monkeypatch, включено, b, подвид):
    подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))
    строки, текст, причина = read_llm.прочитать_моделью(b, подвид)
    assert isinstance(строки, list) and isinstance(текст, str) and isinstance(причина, str)


def test_странный_ответ_не_роняет(monkeypatch, включено):
    подставить(monkeypatch, Клиент(SimpleNamespace(content=None, stop_reason=None, usage=None)))
    строки, _, причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")
    assert строки == [] and причина.startswith("модель:")


def test_неожиданная_ошибка_внутри_это_причина(monkeypatch, включено):
    """Дефект в самом читателе не роняет разбор файла: он становится причиной."""
    подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП)))

    def сломан(*_a, **_k):
        raise KeyError("дефект")
    monkeypatch.setattr(read_llm, "собрать_запрос", сломан)
    строки, текст, причина = read_llm.прочитать_моделью(pdf_на(1), "pdf")
    assert (строки, текст) == ([], "")
    assert "внутренний сбой (KeyError)" in причина


def test_сбой_фабрики_клиента_это_причина(monkeypatch, включено):
    def сломана():
        raise RuntimeError("не собрался")
    monkeypatch.setattr(read_llm, "_создать_клиента", сломана)
    assert "клиент не создан" in read_llm.прочитать_моделью(pdf_на(1), "pdf")[2]


# --------------------------------------------------------------------------- журнал
def test_сводка_только_агрегаты(monkeypatch, включено):
    """Журнал публичный (правило 17): ни строки документа, только числа."""
    подставить(monkeypatch, Клиент(ответ(ОТВЕТ_КП, вход=30_000, выход=8_000)))
    read_llm.прочитать_моделью(pdf_на(1), "pdf")
    строка = read_llm.строка_итога()
    for кусок in ("ВЫДУМ", "Подшипник", "Выдумка", "Пермь", "DAP"):
        assert кусок not in строка
    assert "$0.14" in строка                                 # 30 000 × $2 + 8 000 × $10 за млн
    assert "вход 30000 / выход 8000" in строка
