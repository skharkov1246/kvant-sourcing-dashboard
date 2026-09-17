"""Разбор вложенных ТКП: пары «артикул — цена» из таблиц и текста.

Корпуса придуманы, а не скопированы из базы (правило 18 CLAUDE.md): в публичном
репозитории не должно лежать ни строки настоящей клиентской спецификации.

Что именно стережём:
  • цена и количество не путаются местами — без этого 566 шт по 10 USD
    превращаются в 10 шт по 566;
  • отказ выносится по файлу, а не по строке (правило 13): шапка, пустые
    строки и подписи не должны отменять разбор остальных позиций;
  • числа в человеческой записи («1 234,56») читаются;
  • сохраняется исходная строка и способ, которым получено значение
    (правило 16), иначе следующая ошибка будет неизмеримой.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gt/tools"))

import bitrix_tkp as T  # noqa: E402


def rows(table):
    """Придуманная таблица → форма, которую отдают парсеры файлов."""
    return [("Лист1", i, r) for i, r in enumerate(table, 1)]


def test_число_в_человеческой_записи():
    assert T.to_num("1 234,56") == 1234.56
    assert T.to_num("1234.56") == 1234.56
    assert T.to_num("86,89") == 86.89
    assert T.to_num("1 500") == 1500
    assert T.to_num("") is None
    assert T.to_num("0") is None, "ноль — заглушка витрины, не цена"
    assert T.to_num("н/д") is None


def test_артикул_узнаётся_и_кириллица_переводится():
    assert "1794-ACNR15" in T.find_pns("модуль 1794-ACNR15 адаптер")
    assert "6ES7321-1BL00-0AA0" in T.find_pns("Siemens 6ES7321-1BL00-0AA0")
    # кириллические С и А в номере — типовая беда учётных систем
    assert "304649-100" in T.find_pns("прокладка 304649-100")
    assert T.norm_pn("СТ91035/239") == "CT91035/239"


def test_колонки_опознаются_по_заголовку():
    t = [
        ["Спецификация к ТКП", None, None, None],
        ["№", "Артикул", "Наименование", "Кол-во, шт", "Цена, USD"],
        [1, "1794-ACNR15", "Адаптер ControlNet", 86, 3119.32],
        [2, "6ES7321-1BL00-0AA0", "Модуль ввода", 11, 560.00],
    ]
    hdr = T.header_map(rows(t))
    assert hdr.get("pn") == 1
    assert hdr.get("qty") == 3
    assert hdr.get("price") == 4


def test_цена_не_путается_с_количеством():
    """Главная защита. Количество 566 больше цены 86,89 — без колонки количества
    правило «наибольшее число строки» взяло бы 566 и объявило это ценой."""
    t = [
        ["Артикул", "Кол-во", "Цена, USD"],
        ["3420932", 566, 86.89],
    ]
    r = rows(t)
    got = T.price_rows(r, T.header_map(r))
    assert len(got) == 1
    assert got[0]["pn"] == "3420932"
    assert got[0]["price"] == 86.89, "взято количество вместо цены"
    assert "заголовк" in got[0]["class_rule"]


def test_без_заголовка_количество_всё_равно_исключается():
    """Заголовок не опознался, но колонка количества найдена — её не берём."""
    t = [["3420932", 566, 86.89]]
    got = T.price_rows(rows(t), {"qty": 1})
    assert got and got[0]["price"] == 86.89


def test_шапка_и_подвал_не_отменяют_разбор():
    """Отказ по файлу, а не по строке: мусорные строки просто не дают пар."""
    t = [
        ["ООО «Пример»", None, None],
        ["Технико-коммерческое предложение № 1 от 01.01.2026", None, None],
        [None, None, None],
        ["Артикул", "Кол-во", "Цена, EUR"],
        ["1017891", 108, 6.52],
        ["964587C1", 109, 2.93],
        ["Итого:", None, 1024.00],
        ["Менеджер Иванов И.И.", None, None],
    ]
    r = rows(t)
    got = T.price_rows(r, T.header_map(r))
    pns = {g["pn"] for g in got}
    assert "1017891" in pns and "964587C1" in pns
    assert len(got) >= 2


def test_валюта_снимается_из_строки():
    t = [["Артикул", "Цена"], ["1794-IE8", "3 299,51 USD"]]
    r = rows(t)
    got = T.price_rows(r, T.header_map(r))
    assert got and got[0]["price"] == 3299.51
    assert got[0]["currency"].upper() == "USD"


def test_исходная_строка_и_правило_сохраняются():
    """Без происхождения значения следующая ошибка неизмерима (правило 16)."""
    t = [["Артикул", "Цена"], ["1756-A7", 1597.41]]
    r = rows(t)
    got = T.price_rows(r, T.header_map(r))
    assert got[0]["raw"], "исходная строка потеряна"
    assert got[0]["class_rule"], "не записано, как получено значение"
    assert got[0]["sheet"] == "Лист1" and got[0]["row"] == 2


def test_неподдержанный_формат_отказывает_по_файлу():
    got, how = T.parse("чертёж.dwg", b"\x00\x01binary")
    assert got == []
    assert "не поддержан" in how


def test_битый_xlsx_не_роняет_прогон():
    got, how = T.parse("спец.xlsx", "это не zip".encode())
    assert got == []
    assert "не разобрался" in how


def test_строка_из_pdf_приходит_токенами():
    """В pdf колонок нет, строка приходит текстом.

    Сквозная проверка на настоящем pdf показала, чем это кончается, если отдать
    строку одной ячейкой: из «964587C1 109 2,93» ценой становился сам артикул —
    964 587. Поэтому строка режется на токены, а цена берётся последним числом:
    в печатной таблице она стоит в правой колонке.
    """
    line = "964587C1 109 2,93"
    cells = line.split()
    got = T.price_rows([("стр.1", 1, cells)], {})
    assert got, "из строки pdf не снято ничего"
    assert got[0]["pn"] == "964587C1"
    assert got[0]["price"] == 2.93, f"ценой оказалось {got[0]['price']}"


def test_цена_берётся_последним_числом_а_не_наибольшим():
    """Количество бывает и больше цены, и меньше. «Наибольшее число» ошибается
    в первом случае, «последнее» соответствует порядку колонок в таблице."""
    got = T.price_rows([("стр.1", 1, ["3420932", "566", "86,89"])], {})
    assert got and got[0]["price"] == 86.89
    got2 = T.price_rows([("стр.1", 1, ["1794-ACNR15", "86", "3119,32"])], {})
    assert got2 and got2[0]["price"] == 3119.32


# --- адресный обход: направление файла и отсев не-файлов -------------------
# Написано по разбору 17.09.2026, который вскрыл, что первая версия не получила
# бы ни одного файла и разобрала бы страницу входа как спецификацию.


def test_файловый_объект_требует_машинной_ссылки():
    """id без urlMachine — не файл.

    Этой проверки не хватало в первой версии, а все четыре сборщика вложений в
    репозитории её делают: urlMachine и есть единственная рабочая для вебхука
    ссылка, а id без неё — мёртвая или не файловая запись.
    """
    assert T.objs({"id": 7}) == [], "словарь с id, но без ссылки принят за файл"
    assert T.objs("CO_9634") == [], "crm-привязка принята за файл"
    assert T.objs(None) == []
    assert T.objs(123) == []
    got = T.objs([{"id": 1, "urlMachine": "https://x/1"},
                  {"id": 2, "downloadUrl": "https://x/2"},
                  {"id": 3}])
    assert len(got) == 2, "взяты не только объекты со ссылкой"


def test_направление_по_имени_поля_сп166():
    """Владелец требовал брать чужие КП, а не наши запросы, и чтобы это было
    очевидно из контекста. Контекст — имя файлового поля смарт-процесса."""
    assert T.RFQ_FILE_FIELDS["ufCrm18_1700698211875"][1] == "входящее"   # КП поставщика
    assert T.RFQ_FILE_FIELDS["ufCrm18_1731179998"][1] == "входящее"      # Offer from supplier
    assert T.RFQ_FILE_FIELDS["ufCrm18_1727423346"][1] == "наш запрос"    # Request file
    assert T.RFQ_FILE_FIELDS["ufCrm18_1730999106096"][1] == "не цены"    # Bank Details
    incoming = [k for k, (_, d) in T.RFQ_FILE_FIELDS.items() if d == "входящее"]
    assert len(incoming) >= 2, "входящих полей должно быть несколько"


def test_направление_по_тексту_сомнение_даёт_неизвестно():
    """Асимметрия цены ошибки: отброшенное КП мы не увидим никогда, а наш же
    запрос виден сразу по отсутствию цен. Значит при сомнении — «неизвестно»."""
    assert T.direction_from_text("Получили КП от поставщика, во вложении цены") == "входящее"
    assert T.direction_from_text("Отправили запрос и ТЗ поставщику") == "наш запрос"
    # и то и другое в одной фразе — сомнение, а не выбор
    assert T.direction_from_text("отправили запрос, получили предложение") == "неизвестно"
    assert T.direction_from_text("") == "неизвестно"
    assert T.direction_from_text("файл") == "неизвестно"


def test_страница_входа_не_принимается_за_файл():
    """Вебхук без прав на ссылку получает страницу входа с кодом 200.

    Измерено в base/collect_attachments.py. Без этой проверки инструмент
    разобрал бы html страницы входа как спецификацию и отчитался бы «текст без
    цен» — то есть соврал бы о причине.
    """
    import types

    page = ("<html><head><title>Bitrix24</title></head><body>"
            "<form action='/auth/login'>вход</form></body></html>" + "x" * 300).encode()
    real = ("Артикул;Цена\n3420932;86,89\n" + "y" * 300).encode()

    def fake_get(url, timeout=0):
        body = page if "bad" in url else real
        return types.SimpleNamespace(status_code=200, content=body)

    import sys as _s
    mod = _s.modules.setdefault("requests", types.SimpleNamespace())
    old = getattr(mod, "get", None)
    mod.get = fake_get
    try:
        body, how = T.fetch("https://portal/bad")
        assert body is None
        assert "страница входа" in how, how
        body2, how2 = T.fetch("https://portal/good")
        assert body2 is not None and how2 == "ок"
    finally:
        if old is not None:
            mod.get = old


def test_короткий_ответ_считается_пустым():
    import types
    import sys as _s

    def fake_get(url, timeout=0):
        return types.SimpleNamespace(status_code=200, content=b"tiny")

    mod = _s.modules.setdefault("requests", types.SimpleNamespace())
    old = getattr(mod, "get", None)
    mod.get = fake_get
    try:
        body, how = T.fetch("https://portal/x")
        assert body is None and how == "пусто"
    finally:
        if old is not None:
            mod.get = old
