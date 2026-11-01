"""Строки запроса поставщику (reqlines.py): источник строк, сквозная группа, кэш файлов и бюджет
закачек. Корпус придуман (CLAUDE.md, правило 18)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

import reqlines as R  # noqa: E402

F = R.REQ_FIELD


def карта(cid, deal="", file=None, letter="", title="Уплотнение", t="2026-10-01T10:00:00+03:00"):
    c = {"id": cid, "parentId2": deal, "title": title, "createdTime": t}
    if file:
        c[F] = {"id": file, "urlMachine": "u" + file}
    if letter:
        c[R.LETTER_FIELD] = letter
    return c


def test_позиции_файла_без_шапки():
    rows = [("Л", 1, ["№", "Наименование", "Кол-во"]), ("Л", 2, [1, "Фильтр", 2]), ("Л", 3, [None, ""]),
            ("Л", 4, [2, "Уплотнение", 4]), ("Л", 5, ["Итого позиций"])]
    assert R.rows_count(rows) == 2
    assert R.rows_count([("Л", 1, ["одна строка текста"]), ("Л", 2, ["ещё одна"])]) == 2   # pdf одной ячейкой
    assert R.rows_count([]) == 0


def test_таблица_письма_без_шапки_и_хэш():
    html = "<table><tr><th>№</th><th>Позиция</th></tr><tr><td>1</td><td>A</td></tr><tr><td>2</td><td>B</td></tr></table>"
    n, k = R.table_rows(html)
    assert n == 2 and len(k) == 12
    assert R.table_rows(html.replace("A", "a"))[1] == k        # регистр не меняет содержание
    assert R.table_rows("без таблицы") == (0, "")


def test_источники_и_сквозная_группа():
    cache = {"v": 1, "f": {"10": [120, "h1"], "11": [120, "h1"], "12": [-1, "h2"]}}
    rfqs = [карта("1", "D", file="10"), карта("2", "D", file="11"),      # тот же файл двум поставщикам
            карта("3", "D", file="12"),                                  # не разобран
            карта("4", "E", letter="поз 1\nпоз 2\nпоз 3"),
            карта("5", "E", title="Фильтр"), карта("6", "E", title="  фильтр "),   # одна позиция двум
            карта("7", "", title="Фильтр")]
    first = {"5": {"tr": 4, "tk": "tt"}}
    out, new, st = R.build(rfqs, first, cache)
    assert out["1"][1:] == (120, "f") and out["1"][0] == out["2"][0]
    assert out["3"][1:] == (None, "u")
    assert out["4"][1:] == (3, "x")
    assert out["5"][1:] == (4, "t")                              # таблица письма раньше названия
    assert out["6"][1:] == (1, "o")
    assert out["7"][0] != out["6"][0]                            # без сделки — своя группа
    assert new == {} and st["src"] == {"f": 2, "u": 1, "x": 1, "t": 1, "o": 2}


def test_кэш_и_бюджет_закачек():
    rfqs = [карта(str(i), "D", file=str(100 + i), t=f"2026-10-{i + 1:02d}T10:00:00+03:00") for i in range(5)]
    calls = []

    def fetch(url):
        calls.append(url)
        return (b"x" * 300, "ок", "a.xlsx")

    def parse(name, body):
        return [("Л", 1, ["№", "Позиция"]), ("Л", 2, [1, "A"]), ("Л", 3, [2, "B"])], "xlsx"
    out, new, st = R.build(rfqs, {}, {"v": 1, "f": {"104": [7, "h"]}}, fetch=fetch, parse=parse, budget=2)
    assert len(calls) == 2 and calls == ["u103", "u102"]          # свежие первыми, кэш не качается
    assert new == {"103": [2, R._h(b"x" * 300)], "102": [2, R._h(b"x" * 300)]}
    assert out["4"][1] == 7 and out["3"][1] == 2 and out["1"][1] is None and st["pending"] == 2
    assert out["3"][0] == out["2"][0]                             # одинаковое содержание в сделке — одна группа


def test_кэш_из_файла(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"v": 1, "f": {"1": [5, "h"], "2": "bad"}}', encoding="utf-8")
    assert R.load_cache(str(p)) == {"v": 1, "f": {"1": [5, "h"]}}
    assert R.load_cache(str(tmp_path / "нет.json")) == {"v": 1, "f": {}}


def test_номера_вложений_письма():
    assert R.attach_ids([{"id": 5, "url": "x"}, {"id": "0"}, {"url": "y"}, "z", {"id": 6}, {"id": 7}, {"id": 8}]) == ["5", "6", "7"]
    assert R.attach_ids(None) == [] and R.attach_ids("a:0:{}") == []


def test_строгий_счёт_вложения():
    assert R.rows_count([("Л", 1, ["Добрый день"]), ("Л", 2, ["просим КП"])], strict=True) == 0   # письмо, не таблица
    assert R.rows_count([("Л", 1, ["№", "Позиция"]), ("Л", 2, [1, "A"])], strict=True) == 1


def _таблица(n):
    return [("Л", 1, ["№", "Позиция"])] + [("Л", i + 2, [i + 1, f"P{i}"]) for i in range(n)]


def test_вложения_письма_источник_a():
    rfqs = [карта("1", "D", t="2026-10-05T10:00:00+03:00"), карта("2", "D", t="2026-10-04T10:00:00+03:00"),
            карта("3", "E", letter="поз 1\nпоз 2", t="2026-10-03T10:00:00+03:00"),
            карта("4", "E", file="40", t="2026-10-02T10:00:00+03:00")]
    first = {"1": {"fa": ["501", "502"]}, "2": {"fa": ["503"]}, "3": {"fa": ["504"], "tr": 5, "tk": "t"},
             "4": {"fa": ["505"]}}
    тела = {"501": b"A" * 300, "502": b"B" * 300, "503": b"A" * 300, "504": b"C" * 300}
    строк = {b"A" * 300: _таблица(30), b"B" * 300: _таблица(4), b"C" * 300: [("Л", 1, ["просим КП"])]}
    calls = []

    def fa(fid):
        calls.append(fid)
        return тела.get(fid), "spec.xlsx"
    out, new, st = R.build(rfqs, first, {"v": 1, "f": {"40": [9, "h40"]}}, parse=lambda n, b: (строк[b], "xlsx"),
                           fetch_attach=fa)
    assert calls == ["501", "502", "503", "504"]                  # у карточки с «Request file» вложение не читается
    assert out["1"][1:] == (30, "a") and out["1"][0] == out["2"][0]   # самое длинное вложение; тот же файл в сделке — одна группа
    assert out["3"][1:] == (2, "x")                                # вложение без таблицы — дальше по порядку
    assert out["4"][1:] == (9, "f")
    assert new["a504"][0] == -1 and new["a501"] == [30, R._h(b"A" * 300)]
    assert st["att"] == 4 and st["attFetched"] == 4 and st["attMiss"] == 0 and st["attPending"] == 0


def test_вложения_очередь_и_без_вебхука():
    rfqs = [карта(str(i), "D", title=f"Т{i}", t=f"2026-10-{i + 1:02d}T10:00:00+03:00") for i in range(3)]
    first = {str(i): {"fa": [f"9{i}"]} for i in range(3)}
    parse = lambda n, b: (_таблица(6), "xlsx")                     # noqa: E731
    out, new, st = R.build(rfqs, first, {"v": 1, "f": {}}, parse=parse, fetch_attach=lambda f: (b"x" * 300, ""), att_budget=1)
    assert list(new) == ["a92"] and out["2"][1:] == (6, "a")      # свежие первыми
    assert out["1"][1:] == (None, "u") and st["attPending"] == 2   # не прочитано — строки неизвестны, не «одна»
    out, _new, _st = R.build(rfqs, first, {"v": 1, "f": new}, parse=parse)   # без вебхука файлов
    assert out["2"][1:] == (6, "a") and out["1"][1:] == (1, "o")  # кэш в ходу, непрочитанное — как раньше


def test_вложения_обрыв_вебхука():
    rfqs = [карта(str(i), "D", title=f"Т{i}", t=f"2026-10-{i + 1:02d}T10:00:00+03:00") for i in range(15)]
    first = {str(i): {"fa": [f"8{i:02d}"]} for i in range(15)}
    calls = []

    def fa(fid):
        calls.append(fid)
        raise RuntimeError("ACCESS_DENIED")
    out, new, st = R.build(rfqs, first, {"v": 1, "f": {}}, parse=lambda n, b: ([], ""), fetch_attach=fa)
    assert len(calls) == R.ОБРЫВ and new == {} and st["attBroken"]
    assert all(v[2] == "o" for v in out.values())                  # не «u» навсегда — как без вебхука
