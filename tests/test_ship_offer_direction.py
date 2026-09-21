"""Цена берётся по НАПРАВЛЕНИЮ файла, а не минимумом по всем подряд.

Оплачено готовым документом на 4,4 МБ, который нельзя было выдавать. Первая
версия ship_offer не смотрела на направление вовсе и брала минимум цены по
всем файлам. В графу «выставлено заказчику» попадала бы цена из КП поставщика —
она заведомо ниже, и запас на снижение выходил бы нарисованным или нулевым.
На защите такую цифру опровергает сам поставщик.

Корпуса придуманные (правило 18 CLAUDE.md).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))

import ship_offer as O  # noqa: E402

RATES = {"USD": 1.0, "EUR": 0.9}


def corpus(tmp_path: Path) -> Path:
    doc = {"files": [
        {"direction": "наша цена", "field_name": "Result, ТКП",
         "file_name": "ТКП.xlsx", "origin": "сделка 1",
         "prices": [{"pn": "AF25545", "price": 300, "currency": "USD", "is_price": True, "row": 4},
                    {"pn": "MW21215M", "price": 50000, "currency": "USD", "is_price": True, "row": 9}]},
        {"direction": "входящее", "field_name": "Offer from supplier",
         "file_name": "offer.xlsx", "origin": "СП-166 7",
         "prices": [{"pn": "AF25545", "price": 120, "currency": "USD", "is_price": True, "row": 2}]},
        {"direction": "наш запрос", "field_name": "Request file",
         "file_name": "req.xlsx", "origin": "СП-166 7",
         "prices": [{"pn": "AF25545", "price": 10, "currency": "USD", "is_price": True, "row": 1}]},
        {"direction": "заявка", "field_name": "Техническая спецификация",
         "file_name": "spec.xlsx", "origin": "сделка 1",
         "prices": [{"pn": "AF25545", "price": 5, "currency": "USD", "is_price": True, "row": 1}]},
        {"direction": "неизвестно", "field_name": "Documents, Bot",
         "file_name": "bot.xlsx", "origin": "сделка 1",
         "prices": [{"pn": "AF25545", "price": 7, "currency": "USD", "is_price": True, "row": 1}]},
    ]}
    p = tmp_path / "tkp.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def test_выставленная_цена_только_из_наших_файлов(tmp_path):
    ours, supp, unk = O.prices_by_direction(corpus(tmp_path), RATES)
    assert ours["AF25545"]["usd"] == 300, "в выставленную цену попало чужое"
    assert supp["AF25545"]["usd"] == 120
    assert unk["AF25545"]["usd"] == 7


def test_наш_запрос_и_заявка_в_цену_не_идут(tmp_path):
    """Иначе выставленная цена стала бы 5 USD вместо 300 — минимум по всем."""
    ours, supp, unk = O.prices_by_direction(corpus(tmp_path), RATES)
    для_всех = [v["usd"] for v in list(ours.values()) + list(supp.values())
                + list(unk.values())]
    assert 5 not in для_всех and 10 not in для_всех


def test_неопознанное_не_подмешивается_к_выставленному(tmp_path):
    """Сорсер мог положить КП не в тот слот, но догадка — не выставленная цена."""
    ours, _, unk = O.prices_by_direction(corpus(tmp_path), RATES)
    assert ours["AF25545"]["usd"] == 300
    assert "AF25545" in unk


def test_минимум_внутри_направления_а_не_первая_цена(tmp_path):
    """Один артикул в нескольких наших файлах: берём меньшую выставленную."""
    doc = {"files": [
        {"direction": "наша цена", "field_name": "Result, ТКП", "file_name": "a",
         "origin": "o", "prices": [{"pn": "X1", "price": 900, "currency": "USD", "is_price": True}]},
        {"direction": "наша цена", "field_name": "Economics of the project",
         "file_name": "b", "origin": "o",
         "prices": [{"pn": "X1", "price": 700, "currency": "USD", "is_price": True}]},
    ]}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    ours, _, _ = O.prices_by_direction(p, RATES)
    assert ours["X1"]["usd"] == 700


def test_запас_считается_к_кп_поставщика_а_не_к_витрине(tmp_path):
    """КП поставщика — письменное предложение по этой заявке, витрина — нет."""
    rows = [{"pn": "AF25545", "name": "фильтр", "qty": 10,
             "unit_price_usd": 250, "stock_grade": "твёрдый", "sellers": []}]
    ours, supp, _ = O.prices_by_direction(corpus(tmp_path), RATES)
    html = O.build(rows, ours, "17.09", supp)
    # запас на штуку = 300 (выставлено) − 120 (КП поставщика) = 180
    assert "180.00" in html, "запас посчитан не к КП поставщика"
    assert "КП поставщика" in html
    # витрина показана рядом, но в запас не пошла
    assert "250.00" in html


def test_без_кп_поставщика_запас_считается_к_витрине(tmp_path):
    rows = [{"pn": "MW21215M", "name": "камера", "qty": 2,
             "unit_price_usd": 40000, "stock_grade": "нет", "sellers": []}]
    ours, supp, _ = O.prices_by_direction(corpus(tmp_path), RATES)
    html = O.build(rows, ours, "17.09", supp)
    assert "витрина" in html
    assert "10 000" in html or "10000" in html   # 50000 − 40000 на 2 шт = 20 000


def test_левая_часть_берёт_и_неопознанное_но_с_пометкой(tmp_path):
    """Иначе документ выходит пустым при работающей выгрузке.

    Боевой прогон 17.09.2026: у полей «Result, ТКП» и «Economics of the
    project» шесть файлов и НИ ОДНОЙ цены (картинки и сканы), а 1 843 строки с
    ценой лежат в «Result file», которое по имени в «наша цена» не попадает.
    Взяли только «нашу цену» — получили ноль совпадений из 1 638.
    """
    ours, supp, unk = O.prices_by_direction(corpus(tmp_path), RATES)
    side = O.price_side(ours, unk)
    assert side["AF25545"]["usd"] == 300, "«наша цена» должна иметь приоритет"
    assert side["AF25545"]["direction"] == "наша цена"
    # а если своей цены по артикулу нет — берём неопознанную, не теряя артикул
    side2 = O.price_side({}, unk)
    assert side2["AF25545"]["usd"] == 7
    assert side2["AF25545"]["field"] == "Documents, Bot"


def test_в_документе_видно_поле_и_что_направление_не_установлено(tmp_path):
    rows = [{"pn": "AF25545", "name": "фильтр", "qty": 10,
             "unit_price_usd": 250, "stock_grade": "твёрдый", "sellers": []}]
    _, supp, unk = O.prices_by_direction(corpus(tmp_path), RATES)
    html = O.build(rows, O.price_side({}, unk), "17.09", supp)
    assert "Documents, Bot" in html, "имя поля не показано"
    assert "направление не установлено" in html, "догадка выдана за факт"


def test_своя_цена_не_помечается_неустановленной(tmp_path):
    rows = [{"pn": "AF25545", "name": "фильтр", "qty": 10,
             "unit_price_usd": 250, "stock_grade": "твёрдый", "sellers": []}]
    ours, supp, unk = O.prices_by_direction(corpus(tmp_path), RATES)
    html = O.build(rows, O.price_side(ours, unk), "17.09", supp)
    assert "Result, ТКП" in html
    assert "направление не установлено" not in html


def test_строка_с_кп_поставщика_без_нашей_цены_попадает_в_документ(tmp_path):
    """Иначе теряется самое ценное свидетельство.

    Прогон по «НВН» 18.09.2026 выдал ПУСТОЙ документ при 167 артикулах заявки,
    покрытых входящими КП поставщиков: build требовал именно НАШУ цену и
    выбрасывал строку, если её нет. Письменное предложение контрагента по этой
    самой заявке при этом просто исчезало.
    """
    doc = {"files": [
        {"direction": "входящее", "field_name": "Offer from supplier",
         "file_name": "offer.xlsx", "origin": "СП-166 9",
         "prices": [{"pn": "ZZ100", "price": 150, "currency": "USD", "is_price": True, "row": 3}]},
    ]}
    tk = tmp_path / "t.json"
    tk.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    ours, supp, unk = O.prices_by_direction(tk, RATES)
    assert ours == {} and "ZZ100" in supp
    rows = [{"pn": "ZZ100", "name": "деталь", "qty": 4,
             "unit_price_usd": None, "stock_grade": "нет", "sellers": []}]
    html = O.build(rows, O.price_side(ours, unk), "17.09", supp)
    assert "ZZ100" in html, "строка с КП поставщика выброшена"
    assert "Выставленной цены в файлах сделки НЕТ" in html
    assert "Есть КП поставщика, выставленной цены в файлах нет" in html
    # запас не выдуман: вычитать из пустоты нельзя
    assert "150.00" in html


def test_запас_не_считается_когда_нашей_цены_нет(tmp_path):
    doc = {"files": [
        {"direction": "входящее", "field_name": "Offer from supplier",
         "file_name": "o", "origin": "o",
         "prices": [{"pn": "ZZ200", "price": 10, "currency": "USD", "is_price": True}]},
    ]}
    tk = tmp_path / "t.json"
    tk.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    ours, supp, unk = O.prices_by_direction(tk, RATES)
    rows = [{"pn": "ZZ200", "name": "д", "qty": 2, "unit_price_usd": 999,
             "stock_grade": "нет", "sellers": []}]
    html = O.build(rows, O.price_side(ours, unk), "17.09", supp)
    # 999 - 10 = 989 быть не должно: нашей цены нет, запас неизвестен
    assert "989" not in html, "запас посчитан от пустой выставленной цены"


def test_двойной_номер_ищется_и_по_половинам(tmp_path):
    """В файлах сделки стоит один из двух номеров, а в заявке — составной.

    Замер 18.09.2026: пять строк Jenbacher записаны как «старый-новый» через
    дефис, вместе 589 633 USD. Составного номера не существует нигде, и
    сопоставление по нему целиком не находило ничего.
    """
    assert O.keys_of("334976-433894") == ["334976433894", "433894", "334976"]
    assert O.keys_of("AF25545") == ["AF25545"]
    assert O.keys_of("") == []
    # порядок значим: второй номер действующий, он пробуется раньше первого
    d = {"433894": {"usd": 300}, "334976": {"usd": 999}}
    got, key = O.lookup(d, "334976-433894")
    assert key == "433894" and got["usd"] == 300


def test_полный_ключ_имеет_приоритет_над_половиной():
    d = {"334976433894": {"usd": 111}, "433894": {"usd": 300}}
    got, key = O.lookup(d, "334976-433894")
    assert key == "334976433894" and got["usd"] == 111


def test_в_документе_видно_что_нашлось_по_половине(tmp_path):
    doc = {"files": [
        {"direction": "входящее", "field_name": "Offer from supplier",
         "file_name": "o.xlsx", "origin": "СП-166 3",
         "prices": [{"pn": "433894", "price": 300, "currency": "USD", "is_price": True, "row": 7}]},
    ]}
    tk = tmp_path / "t.json"
    tk.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    ours, supp, unk = O.prices_by_direction(tk, RATES)
    rows = [{"pn": "334976-433894", "name": "клапан", "qty": 662,
             "unit_price_usd": None, "stock_grade": "нет", "sellers": []}]
    html = O.build(rows, O.price_side(ours, unk), "17.09", supp)
    assert "334976-433894" in html
    assert "найдено по 433894" in html, "подмена ключа не показана"


def test_не_склеивает_разные_номера_одинаковой_формы():
    """Половина одного номера не должна подхватывать чужую строку."""
    assert O.keys_of("245488-631265")[1] == "631265"
    assert "631265" not in O.keys_of("265174-263174")


def test_догадка_последнее_число_строки_ценой_не_считается(tmp_path):
    """Значение без названного происхождения ценой не является.

    ОПЛАЧЕНО 18.09.2026 самым крупным исправлением за сутки. Из 6 619 значений
    выгрузки «Энергосети» по колонке «цена» взято 317, остальные — правилом
    «последнее число строки». Правило брало НОМЕРА ПОЗИЦИЙ (в Quotation
    p76057.pdf: 94, 101, 104, 105, 107 подряд, 187 пар из 474 с шагом ровно 1) и
    количество из файлов-заявок. Счётчики на этом основании дали 222 заниженные
    строки вместо 33, и я успел записать это число в отчёт владельцу.

    Корпус придуман, а не взят из выгрузки.
    """
    doc = {"files": [
        {"direction": "входящее", "field_name": "Offer from supplier",
         "file_name": "предложение.pdf", "origin": "сделка 9",
         "prices": [
             {"pn": "AA1000", "price": 99, "currency": "USD",
              "class_rule": "колонка «цена» по заголовку", "is_price": True},
             {"pn": "BB2000", "price": 107, "currency": "USD",
              "class_rule": "последнее число строки (заголовок не опознан)",
              "is_price": False},
         ]},
    ]}
    p = tmp_path / "tkp.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _, supp, _ = O.prices_by_direction(p, RATES)
    assert O.norm_key("AA1000") in supp, "цена по колонке обязана пройти"
    assert O.norm_key("BB2000") not in supp, "догадка не должна попасть в счёт денег"
    # отсев называется числом, а не молчит
    g = O.guess_stats(p)
    assert g == dict(g, values_total=2, prices_by_column=1, guesses_last_number_in_row=1), g


def test_выгрузка_без_градуса_читается_по_правилу_извлечения(tmp_path):
    """Выгрузки, снятые до разделения по градусу, поля is_price не несут.

    Тогда решает само правило: иначе старая выгрузка молча вернула бы прежние
    завышенные счётчики, и исправление не подействовало бы там, где оно нужнее
    всего — на уже снятых данных.
    """
    assert O.price_graded({"class_rule": "колонка «цена» по заголовку"})
    assert not O.price_graded({"class_rule": "последнее число строки (заголовок не опознан)"})
    # происхождение не названо вовсе — значит ценой не считается
    assert not O.price_graded({})


def test_пустая_валюта_новой_выгрузки_долларом_не_считается(tmp_path):
    """Выгрузка, умеющая сказать «валюта не установлена», не подставляет доллар.

    Замер 18.09.2026 по выгрузке «ЛУКОЙЛ»: 14 цен, взятых ИЗ КОЛОНКИ «цена»,
    несли знак ¥ и пустую валюту — и считались долларами. Это завышение в 6,7
    раза при юане и в 154 при иене. У прежних выгрузок поля нет, и для них
    прежний разбор сохранён намеренно.
    """
    import json

    import ship_offer as S
    new = {"files": [{"file_name": "kp.xlsx", "direction": "входящее", "prices": [
        {"pn": "ZZ-1", "price": 100, "currency": "", "currency_source": "не установлена",
         "class_rule": "колонка «цена» по заголовку", "is_price": True},
        {"pn": "ZZ-2", "price": 200, "currency": "USD", "currency_source": "файл",
         "class_rule": "колонка «цена» по заголовку", "is_price": True},
    ]}]}
    old = {"files": [{"file_name": "kp.xlsx", "direction": "входящее", "prices": [
        {"pn": "ZZ-1", "price": 100, "currency": "",
         "class_rule": "колонка «цена» по заголовку"},
    ]}]}
    pn = tmp_path / "new.json"
    po = tmp_path / "old.json"
    pn.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
    po.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    _, supp_new, _ = S.prices_by_direction(pn, {"USD": 1.0})
    assert "ZZ1" not in supp_new and "ZZ2" in supp_new
    _, supp_old, _ = S.prices_by_direction(po, {"USD": 1.0})
    assert "ZZ1" in supp_old
