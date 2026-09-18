"""Приёмник выдачи разведки обязан отсекать то, что уже испортило набор.

gt/tools/rv_merge.py — единственный вход данных в перепроверку, и каждая его
проверка написана по конкретной случившейся ошибке: проза в числовом поле цены,
вердикт по цене без цены, разбор про номер, которого в заявке нет, повторная
выдача по уже разобранному номеру, поля количества и вилки внутри строки.
Непроверенный привратник опаснее отсутствующего: на него полагаются.

Корпус здесь придуман, а не взят из базы, как велят правила репозитория.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gt/tools"))


def good() -> dict:
    return {
        "pn": "AB-1", "what_it_is": "Прокладка", "real_maker": "", "real_pn": "",
        "lifecycle": "", "channel": "склад", "contacts": "", "price_kind": "розница",
        "price_authorized": "", "price_source": "https://example.com/item — карточка",
        "stock": "", "lead_time": "", "volume_note": "", "recommended": "",
        "blocker": "", "band_verdict": "ВЕРНА: цена внутри вилки", "maker_short": "",
        "note": "чем подтверждено: карточка продавца", "price_low": 10.0,
        "price_high": 10.0, "price_note": "", "skeptics": [],
    }


def merge(tmp_path, monkeypatch, rows, ask=("AB-1",), have=()):
    import rv_merge as rm

    a = tmp_path / "ask.json"
    a.write_text(json.dumps({"rows": [{"pn": p} for p in ask]}, ensure_ascii=False),
                 encoding="utf-8")
    o = tmp_path / "out.json"
    o.write_text(json.dumps({"rows": [dict(good(), pn=p) for p in have]}, ensure_ascii=False),
                 encoding="utf-8")
    src = tmp_path / "src.json"
    src.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rm, "ASK", a)
    monkeypatch.setattr(rm, "OUT", o)
    return rm.merge([src], dry=True)


def test_хорошая_строка_проходит(tmp_path, monkeypatch):
    r = merge(tmp_path, monkeypatch, [good()])
    assert len(r["took"]) == 1 and not r["left"]


def test_проза_в_числовом_поле_цены_отсекается(tmp_path, monkeypatch):
    bad = dict(good(), price_low="319,31 GBP за штуку (£1 277,25 за коробку из 4)")
    r = merge(tmp_path, monkeypatch, [bad])
    assert not r["took"] and "не положительное число" in r["left"][0][2]


def test_вердикт_по_цене_без_цены_отсекается(tmp_path, monkeypatch):
    bad = dict(good(), price_low=None, price_high=None)
    r = merge(tmp_path, monkeypatch, [bad])
    assert not r["took"] and "без числовой цены" in r["left"][0][2]


def test_вердикт_по_цене_без_названной_страницы_отсекается(tmp_path, monkeypatch):
    bad = dict(good(), price_source="нашли у продавца, ссылку не сохранил")
    r = merge(tmp_path, monkeypatch, [bad])
    assert not r["took"] and "без названной страницы" in r["left"][0][2]
    # явная пометка об утере ссылки — хуже ссылки, но лучше умолчания
    ok = dict(good(), price_source="ССЫЛКА НЕ СОХРАНЕНА, цена снята 18.09.2026")
    assert len(merge(tmp_path, monkeypatch, [ok])["took"]) == 1


def test_номера_нет_в_заявке(tmp_path, monkeypatch):
    r = merge(tmp_path, monkeypatch, [dict(good(), pn="ZZ-9")])
    assert not r["took"] and "нет в сводке заявки" in r["left"][0][2]


def test_повторная_выдача_по_разобранному_номеру(tmp_path, monkeypatch):
    r = merge(tmp_path, monkeypatch, [good()], have=("AB-1",))
    assert not r["took"] and "уже разобран" in r["left"][0][2]
    # пояснение в скобках не делает номер новым
    r2 = merge(tmp_path, monkeypatch, [dict(good(), pn="AB-1 (он же XYZ)")], have=("AB-1",))
    assert not r2["took"]


def test_поля_количества_и_вилки_в_строке_отсекаются(tmp_path, monkeypatch):
    for field in ("qty", "lo", "hi", "expo", "band_lo", "band_hi", "exposure"):
        r = merge(tmp_path, monkeypatch, [dict(good(), **{field: 5})])
        assert not r["took"], f"поле {field} прошло в набор"
        assert field in r["left"][0][2]


def test_пустое_чем_подтверждено_отсекается(tmp_path, monkeypatch):
    r = merge(tmp_path, monkeypatch, [dict(good(), note="   ")])
    assert not r["took"] and "чем подтверждено" in r["left"][0][2]


def test_вердикт_не_из_закрытого_списка_отсекается(tmp_path, monkeypatch):
    r = merge(tmp_path, monkeypatch, [dict(good(), band_verdict="похоже, дороговато")])
    assert not r["took"] and "вердикт не опознан" in r["left"][0][2]


def test_пустой_список_скептиков_заполняется_честной_пометкой(tmp_path, monkeypatch):
    r = merge(tmp_path, monkeypatch, [good()])
    sk = r["took"][0]["skeptics"]
    assert isinstance(sk, list) and len(sk) == 1
    assert sk[0]["holds"] is None, "отсутствие скептика не должно читаться как «проверено»"
    assert "не было" in sk[0]["lens"]


def test_пол_выше_потолка_отсекается(tmp_path, monkeypatch):
    r = merge(tmp_path, monkeypatch, [dict(good(), price_low=99.0, price_high=1.0)])
    assert not r["took"] and "пол выше" in r["left"][0][2]


def test_наш_собственный_репозиторий_ценой_не_является(tmp_path, monkeypatch):
    """Круговой источник: подтверждение самим собой.

    Репозиторий публичный, и наши перепроверки проиндексированы: 18.09.2026
    поиск по «15508.2 реле Siemens» первой строкой возвращал наш собственный
    PR #305. Следующий проход принял бы вывод прошлого прохода за независимое
    подтверждение, и ошибка стала бы неопровержимой.
    """
    bad = dict(good(), band_verdict="ВЕРНА — цена совпала",
               price_source="взято из github.com/skharkov1246/kvant-sourcing-dashboard")
    r = merge(tmp_path, monkeypatch, [bad])
    assert not r["took"]
    assert "круговой" in r["left"][0][2]


def test_выдача_поисковой_машины_ценой_не_является(tmp_path, monkeypatch):
    """Сводка поиска выдумала цену «7 709 руб., 18 шт» по номеру, которого у
    названного магазина нет вовсе: его собственный интерфейс вернул ноль при
    рабочем положительном контроле по соседнему номеру."""
    bad = dict(good(), band_verdict="ЗАНИЖЕНА — нашлась цена дороже",
               price_source="https://www.google.com/search?q=3420932 — цена 7709 руб")
    r = merge(tmp_path, monkeypatch, [bad])
    assert not r["took"]
    assert "поисковой машины" in r["left"][0][2]


def test_англицизм_чистится_на_входе(tmp_path, monkeypatch):
    """Замена стоит на входе, а не разовой правкой набора: слово возвращается
    с каждой новой пачкой разведки."""
    row = dict(good(), note="Взято из заводского прайс-листа, цитата: «price list 2026»")
    r = merge(tmp_path, monkeypatch, [row])
    assert r["took"], r["left"]
    got = r["took"][0]["note"]
    assert "прейскуранта" in got
    assert "«price list 2026»" in got


def test_нечитаемый_файл_роняет_код_возврата_а_не_строку_в_списке(tmp_path, monkeypatch):
    """Отказ на уровне ФАЙЛА виден плохо в общем списке отказов.

    18.09.2026 одна выдача разведки читалась в момент записи. Приёмник честно
    записал отказ — но одной строкой среди сотни, и двенадцать разобранных
    строк уцелели только потому, что я сверил суммы вручную. Теперь такие
    отказы идут отдельным набором и роняют код возврата: пропустить их нельзя.
    """
    import rv_merge as rm

    a = tmp_path / "ask.json"
    a.write_text(json.dumps({"rows": [{"pn": "AB-1"}]}, ensure_ascii=False), encoding="utf-8")
    o = tmp_path / "out.json"
    o.write_text(json.dumps({"rows": []}, ensure_ascii=False), encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text('{"rows": [{"pn": "AB-1",', encoding="utf-8")   # обрыв записи
    monkeypatch.setattr(rm, "ASK", a)
    monkeypatch.setattr(rm, "OUT", o)
    r = rm.merge([broken], dry=True)
    assert r["bad_files"], "нечитаемый файл обязан попасть в отдельный набор"
    assert not r["left"], "он не должен маскироваться под отказ по строке"
    assert r["bad_files"][0][0] == "broken.json"
