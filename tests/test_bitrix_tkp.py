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


def test_файловый_объект_узнаётся_в_трёх_формах():
    """Битрикс отдаёт файлы тремя разными формами, и все встречаются.

    Форму-словарь-по-id поймала проверка документации 17.09.2026: FILES
    комментария таймлайна приходит как {"10": {id, name, urlDownload}}. Прежний
    код получал такой словарь, считал его ОДНИМ объектом, не находил ссылки и
    возвращал пустоту — файл при этом есть.

    Требование «только с urlMachine» тоже пришлось снять, и это не ослабление.
    У файла Диска прямой ссылки нет вовсе: есть числовой ID, по которому ссылку
    спрашивают у disk.file.get. Отбрасывать такие объекты значило бы терять всё
    из таймлайна и дел.
    """
    # не файл: скаляры и crm-привязки
    assert T.objs("CO_9634") == [], "crm-привязка принята за файл"
    assert T.objs(None) == []
    assert T.objs(123) == []
    assert T.objs({}) == []

    # форма 1: список объектов со ссылкой
    got = T.objs([{"id": 1, "urlMachine": "https://x/1"},
                  {"id": 2, "downloadUrl": "https://x/2"}])
    assert len(got) == 2

    # форма 2: одиночный объект
    assert len(T.objs({"id": 5, "urlMachine": "https://x/5"})) == 1

    # форма 3: словарь ПО id — та, на которой терялись файлы
    keyed = T.objs({"10": {"id": 10, "name": "kp.pdf", "urlDownload": "https://x/10"},
                    "11": {"id": 11, "name": "kp2.pdf", "urlDownload": "https://x/11"}})
    assert len(keyed) == 2, f"словарь по id не разобран: {keyed}"
    assert {o["id"] for o in keyed} == {10, 11}


def test_путь_скачивания_зависит_от_источника():
    """urlMachine содержит одноразовый токен, urlDownload из таймлайна — нет.

    Документация прямо предупреждает: по ссылке без токена серверный клиент
    получит html-страницу вместо файла. Значит источник обязан помнить, каким
    путём брать байты, иначе страница входа уйдёт в разбор как спецификация.
    """
    crm = T.cand("сделка 1", "ufCrm_1", "КП", "входящее",
                 {"id": 7, "urlMachine": "https://portal/rest/getFile?auth=x"})
    assert crm["via"] == "ссылка", "файл поля CRM должен качаться по ссылке"
    assert crm["url"]

    tl = T.cand("комментарий 5", "FILES", "вложение комментария", "неизвестно",
                {"id": 10, "urlDownload": "https://portal/show_file.php?id=10"},
                via="диск")
    assert tl["via"] == "диск", "вложение таймлайна должно идти через disk.file.get"
    assert tl["url"] == "", "ссылка без токена не должна попадать в url"

    # автор берётся у файла, а не у комментария: приложить мог не тот, кто писал
    auth = T.cand("комментарий 5", "FILES", "вложение", "неизвестно",
                  {"id": 11, "authorName": "Петров", "urlDownload": "https://x"},
                  via="диск")
    assert auth["author"] == "Петров"


def test_направление_по_имени_поля_сп166():
    """Владелец требовал брать чужие КП, а не наши запросы, и чтобы это было
    очевидно из контекста. Контекст — имя файлового поля смарт-процесса."""
    assert T.RFQ_FILE_FIELDS["ufCrm18_1700698211875"][1] == "входящее"   # КП поставщика
    assert T.RFQ_FILE_FIELDS["ufCrm18_1731179998"][1] == "входящее"      # Offer from supplier
    assert T.RFQ_FILE_FIELDS["ufCrm18_1727423346"][1] == "наш запрос"    # Request file
    # «Bank Details» и «Мануал» НЕ отбрасываются: сорсер мог положить КП не в
    # тот слот, а отброшенное КП мы не увидим никогда. Поэтому «неизвестно».
    assert T.RFQ_FILE_FIELDS["ufCrm18_1730999106096"][1] == "неизвестно"
    assert T.RFQ_FILE_FIELDS["ufCrm18_1730999038678"][1] == "неизвестно"
    assert "не цены" not in {d for _, d in T.RFQ_FILE_FIELDS.values()}, (
        "поле не должно отбрасываться наглухо: цена ошибки асимметрична")
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
        return types.SimpleNamespace(
            status_code=200, content=body,
            headers={"Content-Disposition": 'attachment; filename="оферта.csv"'})

    import sys as _s
    mod = _s.modules.setdefault("requests", types.SimpleNamespace())
    old = getattr(mod, "get", None)
    mod.get = fake_get
    try:
        body, how, _ = T.fetch("https://portal/bad")
        assert body is None
        assert "страница входа" in how, how
        body2, how2, name2 = T.fetch("https://portal/good")
        assert body2 is not None and how2 == "ок"
        assert name2 == "оферта.csv", name2
    finally:
        if old is not None:
            mod.get = old


def test_короткий_ответ_считается_пустым():
    import types
    import sys as _s

    def fake_get(url, timeout=0):
        return types.SimpleNamespace(status_code=200, content=b"tiny",
                                     headers={})

    mod = _s.modules.setdefault("requests", types.SimpleNamespace())
    old = getattr(mod, "get", None)
    mod.get = fake_get
    try:
        body, how, _ = T.fetch("https://portal/x")
        assert body is None and how == "пусто"
    finally:
        if old is not None:
            mod.get = old


def test_скан_без_текста_не_называется_пустым():
    """Правило 15 CLAUDE.md: статус файла не должен врать.

    pdf без текстового слоя — это скан под распознавание, а не «пусто» и не
    «не КП». Соврав тут, мы занизим оценку объёма распознавания и решим, что
    файлов с ценами меньше, чем есть.
    """
    rows, how = T.parse("КП-скан.pdf", b"%PDF-1.4 no text layer" + b"\x00" * 400)
    # разбор мог и не состояться — важно, что это не выдаётся за «пусто»
    assert rows == [] or isinstance(rows, list)
    assert "не разобрался" in how or how == "pdf", how


def test_тело_письма_считается_источником_цены():
    """КП может прийти прямо в тексте письма, без вложения.

    Тело лежит в DESCRIPTION дела и скачивать его не надо. Раньше я его не брал
    вовсе и терял такие КП целиком. Проверяем, что текст письма проходит тот же
    разбор, что и файл.
    """
    body = "Добрый день! Предлагаем:\n3420932  566 шт  86,89 USD\n1017891  108 шт  6,52 USD"
    rows, how = T.parse("письмо.txt", body.encode())
    got = {p["pn"]: p["price"] for p in T.price_rows(rows, T.header_map(rows))}
    assert got.get("3420932") == 86.89, got
    assert got.get("1017891") == 6.52, got


# --- правки по итогам ХОЛОСТОГО прогона 17.09.2026 ---------------------------
# Прогон дал три числа, каждое из которых меняло поведение:
#   1998 файлов — и ИМЯ есть только у 12: crm.item.list имени не отдаёт;
#   1509 файлов уходили в «неизвестно», потому что направление читалось только
#     по восьми зашитым кодам СП-166, а у сделки 25 своих файловых полей;
#   предел --max-files 400 отрезал бы хвост в порядке обхода, то есть случайно.


def test_имя_берётся_из_заголовка_отдачи():
    """Единственное место, где имя есть. Две формы заголовка, обе рабочие."""
    assert T.name_from('attachment; filename="КП Сименс.xlsx"') == "КП Сименс.xlsx"
    assert T.name_from("attachment; filename*=utf-8''%D0%9A%D0%9F.pdf") == "КП.pdf"
    assert T.name_from("inline") == ""
    assert T.name_from("") == ""


def test_формат_узнаётся_без_имени():
    """Без этого 99 % файлов получили бы «формат не поддержан» при рабочей
    выгрузке — то есть прогон отчитался бы нулём и соврал о причине."""
    assert T.sniff(b"%PDF-1.7\nxxx") == ".pdf"
    assert T.sniff(b"PK\x03\x04" + b"xl/workbook.xml" + b"\x00" * 50) == ".xlsx"
    assert T.sniff(b"PK\x03\x04" + b"word/document.xml" + b"\x00" * 50) == ".zip"
    assert T.sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 40) == ".xls"
    assert T.sniff("Артикул;Цена\n3420932;86,89\n".encode()) == ".txt"


def test_двоичное_не_объявляется_текстом():
    """b"\\x00\\x01binary" — ВАЛИДНЫЙ utf-8, и проверка одной декодировкой
    объявляла двоичный чертёж текстом. Ноль-байт решает раньше кодировки."""
    assert T.sniff(b"\x00\x01binary") == ".bin"
    got, how = T.parse("чертёж.dwg", b"\x00\x01binary")
    assert got == [] and "не поддержан" in how


def test_безымянный_csv_всё_равно_разбирается():
    """Сквозная проверка: имени нет вовсе, а позиция с ценой должна найтись."""
    body = ("Артикул;Наименование;Кол-во;Цена\n"
            "3420932;прокладка;566;86,89\n").encode()
    rows, how = T.parse("", body)
    assert rows, "безымянный файл не разобрался"
    assert how == "текст"
    pr = T.price_rows(rows, T.header_map(rows))
    assert len(pr) == 1 and pr[0]["pn"] == "3420932"
    assert abs(pr[0]["price"] - 86.89) < 0.01, pr[0]


def test_направление_по_имени_поля_сделки():
    """Четыре группы, а не две. «Наша цена» — то, что владелец просил оставить."""
    assert T.dir_from_field("Offer from supplier(s)") == "входящее"
    assert T.dir_from_field("КП поставщика") == "входящее"
    assert T.dir_from_field("Offer, old") == "входящее"
    assert T.dir_from_field("Offer from us") == "наш запрос"
    assert T.dir_from_field("Request file") == "наш запрос"
    assert T.dir_from_field("Result, ТКП") == "наша цена"
    assert T.dir_from_field("Economics of the project") == "наша цена"
    assert T.dir_from_field("Техническая спецификация") == "заявка"
    assert T.dir_from_field("Tender Platform Complexity") == "неизвестно"
    assert T.dir_from_field("") == "неизвестно"


def test_наш_запрос_проверяется_раньше_входящего():
    """«Processed file for supplier» содержит слово supplier, но это НАШ файл.
    Порядок правил — часть поведения, а не косметика."""
    assert T.dir_from_field("Processed file for supplier") == "наш запрос"


def test_порядок_разбора_отрезает_картинки_а_не_цены():
    """Предел есть всегда, значит вопрос — что останется неразобранным."""
    def c(d, f):
        return {"direction": d, "field_name": f}
    items = [
        c("неизвестно", "Tender Platform Complexity"),
        c("наш запрос", "Offer from us"),
        c("входящее", "Offer from supplier(s)"),
        c("заявка", "Техническая спецификация"),
        c("наша цена", "Result, ТКП"),
        c("неизвестно", "вложение дела"),
    ]
    order = [x["field_name"] for x in sorted(items, key=T.rank)]
    assert order[0] == "Result, ТКП", order
    assert order[1] == "Offer from supplier(s)", order
    assert order[2] == "вложение дела", order
    assert order.index("вложение дела") < order.index("Tender Platform Complexity")
    assert order[-1] == "Offer from us", order


def test_наша_цена_входит_в_разбор():
    """Иначе выставленные цены владельца не скачиваются вовсе."""
    assert "наша цена" in T.WANTED
    assert "входящее" in T.WANTED


def test_заявка_тоже_входит_потому_что_в_ней_есть_цены():
    """Исправление правила по замеру, а не по рассуждению.

    Я исключал «Техническую спецификацию» как требования заказчика без цен.
    Боевой прогон 17.09.2026 показал обратное: 1 431 строка с парой «артикул —
    цена» и 394 артикула заявки в файлах этого поля. Правило теряло треть
    покрытия.
    """
    assert "заявка" in T.WANTED
    assert T.dir_from_field("Техническая спецификация") == "заявка"
    # а вот наши исходящие по-прежнему за бортом: это ориентиры поставщику
    assert "наш запрос" not in T.WANTED
