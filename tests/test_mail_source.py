"""Письма Битрикса как источник файлов библиотеки (library/mail_source.py).

Корпус придуман (CLAUDE.md, правило 18): номера писем, сделок и файлов Диска
выдуманы. Портал и база не опрашиваются — чтение подменяется функцией, которая
запоминает вызовы, база — курсором-заглушкой.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
for каталог in ("library", "scripts"):
    sys.path.insert(0, str(ROOT / каталог))

from library import doc_folder, doc_side, mail_source as ms  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "library-mail.yml"


def индексатор():
    pytest.importorskip("psycopg2", reason="индексатор импортирует psycopg2")
    spec = importlib.util.spec_from_file_location("indexer_mail_test", ROOT / "library" / "indexer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Портал:
    """Подмена indexer.bx: отдаёт письма по фильтру >ID / <=ID страницами по 50."""

    def __init__(self, письма: list[dict]):
        self.письма = sorted(письма, key=lambda x: int(x["ID"]))
        self.вызовы: list[tuple[str, dict]] = []

    def __call__(self, метод: str, параметры: dict) -> dict:
        self.вызовы.append((метод, параметры))
        ф = параметры["filter"]
        низ = int(ф.get(">ID", 0))
        верх = ф.get("<=ID")
        подходят = [x for x in self.письма if int(x["ID"]) > низ
                    and (верх is None or int(x["ID"]) <= int(верх))]
        return {"result": подходят[:ms.СТРАНИЦА]}


def письмо(n: int, файлы=(), тип=ms.СДЕЛКА, направление=ms.ВХОДЯЩЕЕ, владелец=7) -> dict:
    return {"ID": str(n), "OWNER_ID": str(владелец), "OWNER_TYPE_ID": str(тип),
            "DIRECTION": str(направление), "CREATED": "2026-09-01T10:00:00+03:00",
            "FILES": [{"id": str(f), "url": f"https://portal.example.test/show/{f}"} for f in файлы]}


# ─────────────────────────────────────────────── группы
def test_лиды_только_входящие_письма_лидов():
    ф = ms.фильтр_группы("mail-lead")
    assert ф == {"TYPE_ID": 4, "OWNER_TYPE_ID": 1, "DIRECTION": 1}


def test_поставщики_входящие_контактов_и_компаний():
    ф = ms.фильтр_группы("mail-supplier")
    assert ф["TYPE_ID"] == 4 and ф["DIRECTION"] == 1
    assert sorted(ф["OWNER_TYPE_ID"]) == [3, 4]


def test_сделки_оба_направления():
    ф = ms.фильтр_группы("mail-deal")
    assert ф == {"TYPE_ID": 4, "OWNER_TYPE_ID": 2}, "у писем сделки направление не отбирается"


def test_неизвестная_группа_отказ():
    with pytest.raises(ValueError):
        ms.фильтр_группы("mail-everything")


def test_происхождения_групп_различны_и_известны_папке():
    происхождения = [г["origin"] for г in ms.ГРУППЫ.values()]
    assert len(set(происхождения)) == len(происхождения)
    assert set(происхождения) == set(doc_folder.ПРОИСХОЖДЕНИЯ_ПИСЕМ)
    assert not set(происхождения) & set(doc_folder.СУЩНОСТЬ_ПО_ПРОИСХОЖДЕНИЮ)


# ─────────────────────────────────────────────── обход портала
def test_обход_по_ключу_без_подсчёта():
    портал = Портал([письмо(n, [1000 + n]) for n in range(101, 231)])
    refs, курсор = ms.collect_refs_mail("mail-deal", 100, 0, bx=портал)
    assert len(refs) == 130 and курсор == 230
    for метод, п in портал.вызовы:
        assert метод == "crm.activity.list"
        assert п["start"] == -1, "смещение считает total — это время метода (429)"
        assert п["order"] == {"ID": "ASC"}
        assert ">ID" in п["filter"] and п["filter"]["TYPE_ID"] == 4
    # Страницы идут от последнего прочитанного номера, а не от смещения.
    assert [п["filter"][">ID"] for _, п in портал.вызовы] == [100, 150, 200]


def test_лимит_писем_и_курсор_на_последнем_взятом():
    портал = Портал([письмо(n, [5000 + n]) for n in range(1, 121)])
    письма, курсор = ms.читать_письма("mail-deal", 0, 70, bx=портал)
    assert len(письма) == 70
    assert курсор == 70, "курсор за непрочитанными письмами пропустил бы их навсегда"
    assert len(портал.вызовы) == 2


def test_верхняя_граница_пачки():
    портал = Портал([письмо(n, [n]) for n in range(1, 60)])
    письма, курсор = ms.читать_письма("mail-deal", 10, 0, до_id=20, bx=портал)
    assert [int(x["ID"]) for x in письма] == list(range(11, 21))
    assert курсор == 20
    assert all(п["filter"]["<=ID"] == 20 for _, п in портал.вызовы)


def test_пустая_пачка_курсор_на_месте():
    письма, курсор = ms.читать_письма("mail-lead", 500, 100, bx=Портал([]))
    assert письма == [] and курсор == 500


def test_письмо_без_вложений_ссылок_не_даёт_но_курсор_двигает():
    портал = Портал([письмо(1), письмо(2, [77])])
    refs, курсор = ms.collect_refs_mail("mail-deal", 0, 0, bx=портал)
    assert [r["file_id"] for r in refs] == ["mail:77"] and курсор == 2


# ─────────────────────────────────────────────── форма ссылки
def test_ссылка_несёт_только_номер_диска():
    """url из FILES — страница портала: вебхуку она отдаёт страницу входа."""
    r = ms.ссылки_письма(письмо(9, [314]), "mail-deal")[0]
    assert r["fo"] == {"id": "314"}
    assert r["file_id"] == "mail:314"


def test_номер_владельца_с_буквой_сущности():
    лид = ms.ссылки_письма(письмо(1, [1], тип=ms.ЛИД, владелец=55), "mail-lead")[0]
    комп = ms.ссылки_письма(письмо(2, [2], тип=ms.КОМПАНИЯ, владелец=55), "mail-supplier")[0]
    конт = ms.ссылки_письма(письмо(3, [3], тип=ms.КОНТАКТ, владелец=55), "mail-supplier")[0]
    сд = ms.ссылки_письма(письмо(4, [4], тип=ms.СДЕЛКА, владелец=55), "mail-deal")[0]
    assert len({лид["deal"], комп["deal"], конт["deal"], сд["deal"]}) == 4
    assert сд["deal"] == "55", "у письма сделки deal_id — номер сделки"


def test_сторона_по_группе_и_направлению():
    вх = ms.ссылки_письма(письмо(1, [1]), "mail-deal")[0]
    исх = ms.ссылки_письма(письмо(2, [2], направление=ms.ИСХОДЯЩЕЕ), "mail-deal")[0]
    лид = ms.ссылки_письма(письмо(3, [3], тип=ms.ЛИД), "mail-lead")[0]
    пст = ms.ссылки_письма(письмо(4, [4], тип=ms.КОМПАНИЯ), "mail-supplier")[0]
    assert (вх["side"], исх["side"], лид["side"], пст["side"]) == (
        doc_side.ЗАКАЗЧИК, doc_side.МЫ, doc_side.ЗАКАЗЧИК, doc_side.ПОСТАВЩИК)


def test_формы_значения_files():
    assert ms.файлы_письма({"10": {"id": 10}, "11": {"id": 11}}) == ["10", "11"]
    assert ms.файлы_письма({"id": 12}) == ["12"]
    assert ms.файлы_письма([{"id": 13}, {"id": 13}, {"name": "без номера"}]) == ["13"]
    assert ms.файлы_письма(None) == []


def test_папка_письма():
    for (origin, заголовок), ждём in doc_folder.ПАПКА_ПИСЬМА.items():
        п, ув, _ = doc_folder.определить(origin, "письмо 1", заголовок)
        assert п == ждём
        assert ув == (0.0 if ждём == doc_folder.НЕ_ОПРЕДЕЛЕНО else doc_folder.УВ_ОБРАЗЕЦ)
    # Прежние источники — как были.
    assert doc_folder.определить("поле сделки", "ufCrm_1633502831", None)[0] == \
        doc_folder.ЗАПРОС_ЗАКАЗЧИКА


# ─────────────────────────────────────────────── ключ файла
def test_ключ_письма_не_совпадает_с_ключом_поля():
    ix = индексатор()
    поле = {"deal": "5", "fo": {"id": 314, "urlMachine": "https://x.test/314"}}
    почта = ms.ссылки_письма(письмо(1, [314]), "mail-deal")[0]
    assert ix.ключ_ссылки(поле) == "314"
    assert ix.ключ_ссылки(почта) == "mail:314"
    оставлено, дублей = ix.без_повторов([поле, почта])
    assert len(оставлено) == 2 and дублей == 0


def test_разбор_берёт_ключ_и_сторону_из_ссылки(monkeypatch):
    ix = индексатор()
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: None)
    ref = ms.ссылки_письма(письмо(8, [99], тип=ms.ЛИД), "mail-lead")[0]
    rec, items = ix.handle(ref)
    assert rec["file_id"] == "mail:99" and rec["side"] == doc_side.ЗАКАЗЧИК
    assert rec["origin"] == doc_folder.ПИСЬМО_ЛИДА
    assert rec["doc_kind"] == doc_folder.ЗАПРОС_ЗАКАЗЧИКА
    assert items == []


# ─────────────────────────────────────────────── отметка
class Курсор:
    """Заглушка курсора: одна строка отметки и журнал записей."""

    def __init__(self, после_id: int | None):
        self.строка = None if после_id is None else ({"после_id": после_id},)
        self.записи: list[tuple] = []
        self._последний = ""

    def execute(self, sql, params=None):
        self._последний = sql
        if sql.lstrip().startswith("insert"):
            self.записи.append(params)

    def fetchone(self):
        return self.строка


def test_отметка_сдвигается_с_места_плана():
    c = Курсор(100)
    assert ms.сдвинуть_отметку(c, "mail-lead", 100, 250, 40, "r1")
    assert len(c.записи) == 1
    metric, run_key, nums, _ = c.записи[0]
    assert metric == "почта:mail-lead" and json.loads(nums)["после_id"] == 250


def test_отметка_не_откатывает_чужой_прогресс():
    c = Курсор(300)                     # другой прогон ушёл дальше
    assert not ms.сдвинуть_отметку(c, "mail-lead", 100, 250, 40, "r1")
    assert c.записи == []


def test_пустая_пачка_отметку_не_трогает():
    c = Курсор(None)
    assert not ms.сдвинуть_отметку(c, "mail-deal", 0, 0, 0, "r1")
    assert c.записи == []


def test_отметка_без_плана_отказ(monkeypatch):
    monkeypatch.setenv("MAIL_GROUP", "mail-deal")
    monkeypatch.delenv("MAIL_TO", raising=False)
    monkeypatch.delenv("MAIL_FROM", raising=False)
    assert ms.main(["mail_source.py", "отметка"]) == 2


# ─────────────────────────────────────────────── деление пачки на части
def редкие_в_начале(от: int = 0) -> list[int]:
    """Придуманная пачка как у лидов 25.09.2026: в начале номера редки, дальше густо.

    Прогон 36079859054 резал такую пачку равными отрезками номеров, и части 1–4
    из 10 не получили ни одного письма. Номера выдуманы (правило 18).
    """
    редкие = [от + 1 + 900 * i for i in range(40)]          # 40 писем на 36 тыс. номеров
    густые = [от + 40_000 + 3 * i for i in range(1960)]      # 1 960 писем подряд
    return редкие + густые


def в_части(n: int, низ: int, верх: int) -> bool:
    return низ < n <= верх


@pytest.mark.parametrize("частей", [10, 12, 25, 50])
def test_границы_поровну_по_письмам_а_не_по_номерам(частей):
    от = 17
    номера = редкие_в_начале(от)
    до = номера[-1]
    б = ms.границы_по_письмам(номера, от, до, частей)
    assert len(б) == частей + 1
    # смежно: первая часть — от отметки, последняя — до конца пачки
    assert б[0] == от and б[-1] == до
    assert all(a <= c for a, c in zip(б, б[1:]))
    куски = [ms.границы_части(б, от, до, k, частей) for k in range(частей)]
    for (_, верх), (низ, _) in zip(куски, куски[1:]):
        assert верх == низ, "между частями не должно быть щели"
    # каждое письмо — ровно в одной части
    for n in номера:
        assert sum(в_части(n, низ, верх) for низ, верх in куски) == 1, (n, частей)
    # и любой номер пачки, даже письма, появившегося после плана, — тоже
    for n in range(от + 1, до + 1, 97):
        assert sum(в_части(n, низ, верх) for низ, верх in куски) == 1, (n, частей)
    # поровну: писем в частях — с точностью до одного, пустых частей нет
    доли = [sum(в_части(n, низ, верх) for n in номера) for низ, верх in куски]
    assert sum(доли) == len(номера)
    assert max(доли) - min(доли) <= 1, доли
    assert min(доли) > 0


def test_писем_меньше_чем_частей():
    номера = [120, 5_000, 90_000]
    б = ms.границы_по_письмам(номера, 100, 90_000, 25)
    куски = [ms.границы_части(б, 100, 90_000, k, 25) for k in range(25)]
    доли = [sum(в_части(n, низ, верх) for n in номера) for низ, верх in куски]
    assert sorted(доли) == [0] * 22 + [1] * 3
    assert all(низ == верх for (низ, верх), д in zip(куски, доли) if д == 0), \
        "пустая часть не должна читать портал"


def test_границы_пустой_пачки_и_одной_части():
    assert ms.границы_по_письмам([], 500, 500, 10) == [500] * 11
    assert ms.границы_по_письмам([3, 9], 0, 9, 1) == [0, 9]
    assert ms.границы_части(None, 0, 9, 0, 1) == (0, 9)
    with pytest.raises(ValueError):
        ms.границы_по_письмам([5, 50], 10, 50, 10)       # номер позади отметки


def test_часть_отказывает_на_чужих_границах():
    б = ms.границы_по_письмам(list(range(101, 201)), 100, 200, 10)
    with pytest.raises(ValueError):
        ms.границы_части(None, 100, 200, 0, 10)          # план границ не дал
    with pytest.raises(ValueError):
        ms.границы_части(б, 100, 200, 0, 12)             # деление на другое число
    with pytest.raises(ValueError):
        ms.границы_части(б, 100, 250, 0, 10)             # другая пачка
    with pytest.raises(ValueError):
        ms.границы_части([100, 150, 140, 200], 100, 200, 0, 3)
    with pytest.raises(ValueError):
        ms.границы_части(б, 100, 200, 10, 10)


def test_границы_из_вывода_плана():
    assert ms.разобрать_границы("[0,5,9]") == [0, 5, 9]
    for мусор in ("", "не json", "{}", "[]", '["1"]', "[1.5]", "[true, 2]"):
        with pytest.raises(ValueError):
            ms.разобрать_границы(мусор)


class База:
    """Заглушка indexer.connect: план читает отметку, больше ничего."""

    def cursor(self):
        import contextlib
        return contextlib.nullcontext(object())

    def close(self):
        pass


def план(monkeypatch, tmp_path, письма, частей: int, отметка: int = 0, лимит: int = 2000):
    """Прогнать «mail_source.py план» с подменой портала и базы. (выход, портал)."""
    import types
    портал = Портал(письма)
    monkeypatch.setitem(sys.modules, "indexer",
                        types.SimpleNamespace(connect=База, bx=портал))
    monkeypatch.setattr(ms, "прочитать_отметку", lambda cur, группа: отметка)
    выход = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(выход))
    monkeypatch.setenv("MAIL_GROUP", "mail-lead")
    monkeypatch.setenv("MAIL_LIMIT", str(лимит))
    monkeypatch.setenv("SHARDS", str(частей))
    assert ms.main(["mail_source.py", "план"]) == 0
    строки = выход.read_text(encoding="utf-8").splitlines()
    return dict(x.split("=", 1) for x in строки), портал


@pytest.mark.parametrize("частей", [10, 50])
def test_план_отдаёт_границы_и_части_делят_пачку_поровну(monkeypatch, tmp_path, частей):
    """Сквозь: план читает номера пачки, части по его границам читают письма."""
    письма = [письмо(n, [n], тип=ms.ЛИД) for n in редкие_в_начале()]
    # за пределом лимита — ещё письма: пачка должна кончиться на лимите
    письма += [письмо(n, [n], тип=ms.ЛИД) for n in range(100_000, 100_300)]
    выход, портал = план(monkeypatch, tmp_path, письма, частей)
    assert all(п["start"] == -1 and п["select"] == ["ID"] for _, п in портал.вызовы), \
        "план читает одни номера и без подсчёта total"
    assert len(портал.вызовы) == 2000 // ms.СТРАНИЦА, "лишних запросов на границы нет"
    от, до = int(выход["mail_from"]), int(выход["mail_to"])
    assert (от, до, int(выход["mail_count"])) == (0, редкие_в_начале()[-1], 2000)
    б = ms.разобрать_границы(выход["mail_bounds"])
    прочитано: list[int] = []
    доли = []
    for k in range(частей):
        низ, верх = ms.границы_части(б, от, до, k, частей)
        своих, _ = ms.читать_письма("mail-lead", низ, 0, до_id=верх, bx=Портал(письма)) \
            if верх > низ else ([], низ)
        доли.append(len(своих))
        прочитано += [int(x["ID"]) for x in своих]
    assert sorted(прочитано) == редкие_в_начале(), "каждое письмо пачки — ровно одной частью"
    assert max(доли) - min(доли) <= 1 and min(доли) > 0, доли


def test_выход_плана_совпадает_с_тем_что_ждёт_прогон(monkeypatch, tmp_path):
    """Имена выхода плана — ровно те, что прогон передаёт дальше."""
    import re
    выход, _ = план(monkeypatch, tmp_path, [письмо(n, тип=ms.ЛИД) for n in range(1, 40)], 10)
    wf = прогон()
    ждёт = {m.group(1) for v in wf["jobs"]["plan"]["outputs"].values()
            for m in [re.fullmatch(r"\$\{\{\s*steps\.plan\.outputs\.(\w+)\s*\}\}", v.strip())] if m}
    assert ждёт == set(выход), (ждёт, set(выход))
    assert "mail_bounds" in ждёт


def test_прогон_передаёт_границы_плана_частям():
    wf = прогон()
    шаг_плана = next(ш for ш in wf["jobs"]["plan"]["steps"] if ш.get("id") == "plan")
    assert шаг_плана["env"]["SHARDS"].replace(" ", "") == "${{inputs.shards}}", \
        "план обязан делить на то же число частей, что и матрица"
    env = шаг_разбора(wf)["env"]
    assert env["MAIL_BOUNDS"].replace(" ", "") == "${{needs.plan.outputs.mail_bounds}}"
    assert env["SHARDS"].replace(" ", "") == "${{inputs.shards}}"
    assert env["SHARD"].replace(" ", "") == "${{matrix.shard}}"
    assert "mail_source.py план" in шаг_плана["run"]


def test_часть_индексатора_берёт_свой_кусок_и_отказывает_без_границ(monkeypatch):
    ix = индексатор()
    monkeypatch.setattr(ix, "mail_source", ms, raising=False)
    monkeypatch.setattr(ix, "MAIL_GROUP", "mail-lead")
    вызовы = []
    monkeypatch.setattr(ms, "collect_refs_mail",
                        lambda группа, после, лимит, до_id=None, bx=None:
                        (вызовы.append((после, до_id)) or [], до_id))
    номера = редкие_в_начале(1000)
    от, до = 1000, номера[-1]
    б = ms.границы_по_письмам(номера, от, до, 10)
    monkeypatch.setenv("MAIL_FROM", str(от))
    monkeypatch.setenv("MAIL_TO", str(до))
    monkeypatch.setenv("MAIL_BOUNDS", json.dumps(б))
    monkeypatch.setattr(ix, "SHARDS", 10)
    for k in range(10):
        monkeypatch.setattr(ix, "SHARD", k)
        assert ix.collect_refs_mail_part() == []
    assert вызовы == list(zip(б, б[1:])), "часть читает ровно свой кусок из границ плана"
    # без границ, с чужими границами — отказ, а не равные отрезки номеров
    monkeypatch.delenv("MAIL_BOUNDS")
    assert ix.collect_refs_mail_part() is None
    monkeypatch.setenv("MAIL_BOUNDS", json.dumps(б[:-1]))
    assert ix.collect_refs_mail_part() is None


# ─────────────────────────────────────────────── прогон
def прогон() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def группа_очереди(wf: dict, группа: str) -> str:
    """Вычислить выражение группы очереди вида `a == 'x' && 'y' || 'z'`."""
    import re
    выр = wf["concurrency"]["group"]
    m = re.fullmatch(r"\$\{\{\s*inputs\.group\s*==\s*'([^']+)'\s*&&\s*'([^']+)'\s*\|\|\s*'([^']+)'\s*\}\}",
                     выр.strip())
    assert m, f"группа очереди не распознана: {выр}"
    return m.group(2) if группа == m.group(1) else m.group(3)


def test_лиды_вне_общей_очереди():
    wf = прогон()
    assert группа_очереди(wf, "mail-lead") == "bitrix-fifth"
    assert группа_очереди(wf, "mail-deal") == "bitrix-portal"
    assert группа_очереди(wf, "mail-supplier") == "bitrix-portal"
    assert wf["concurrency"]["cancel-in-progress"] is False


def шаг_разбора(wf: dict) -> dict:
    return next(ш for ш in wf["jobs"]["index"]["steps"] if "indexer.py" in (ш.get("run") or ""))


def бюджет_шага(группа: str, вход: str, частей: str = "25") -> dict:
    """Выполнить скрипт шага разбора bash-ем, подставив группу, без Python."""
    ш = шаг_разбора(прогон())
    скрипт = (ш["run"].replace("${{ inputs.group }}", группа)
              .replace("python library/indexer.py",
                       'echo "RPS=$BITRIX_RPS PAR=$BITRIX_PARALLEL"'))
    env = {"PATH": os.environ.get("PATH", ""), "INPUT_RPS": вход, "SHARDS": частей}
    out = subprocess.run(["bash", "-c", скрипт], env=env, capture_output=True, text=True,
                         check=True).stdout
    строка = [s for s in out.splitlines() if s.startswith("RPS=")][-1]
    return dict(x.split("=") for x in строка.split())


def test_лиды_всегда_пятая_часть():
    for вход in ("0.8", "1.2", "1.5"):
        assert бюджет_шага("mail-lead", вход)["RPS"] == "0.3"
    assert бюджет_шага("mail-deal", "1.5")["RPS"] == "1.5"
    assert бюджет_шага("mail-supplier", "1.2")["PAR"] == "10"
    assert бюджет_шага("mail-lead", "1.2")["PAR"] == "3"


def test_план_лидов_тоже_пятая_часть():
    wf = прогон()
    шаг = next(ш for ш in wf["jobs"]["plan"]["steps"] if ш.get("id") == "plan")
    assert "'0.3'" in шаг["env"]["BITRIX_RPS"] and "mail-lead" in шаг["env"]["BITRIX_RPS"]


def test_отметка_последней_и_только_после_всех_частей():
    wf = прогон()
    triggers = wf.get("on") or wf.get(True)
    assert "schedule" not in triggers, "расписание — решение владельца"
    отметка = wf["jobs"]["cursor"]
    assert set(отметка["needs"]) >= {"plan", "index"}
    условие = отметка.get("if", "")
    for обход in ("always()", "failure()", "cancelled()"):
        assert обход not in условие, "отметка упавшего прогона пропустила бы письма"
    assert "inputs.apply" in условие, "холостой прогон отметку не двигает"
    assert "mail_source.py отметка" in отметка["steps"][-1]["run"]


def test_параллельность_совпадает_с_матрицей():
    wf = прогон()
    import re
    # Параллельность зависит от группы: лидам — меньше (пятая часть портала,
    # лишние части лишь занимают раннеры). Шаг и матрица обязаны давать одно
    # и то же число для каждой группы.
    матрица = str(wf["jobs"]["index"]["strategy"]["max-parallel"])
    м = re.search(r"mail-lead' && (\d+) \|\| (\d+)", матрица)
    assert м, "max-parallel задаётся по группе"
    лиды, прочие = int(м.group(1)), int(м.group(2))
    шаг = re.search(r'= "mail-lead" \]; then MAX_PARALLEL=(\d+); else MAX_PARALLEL=(\d+)',
                    шаг_разбора(wf)["run"])
    assert шаг and (int(шаг.group(1)), int(шаг.group(2))) == (лиды, прочие)
    assert лиды < прочие <= 10, "раннеров 20 на весь репозиторий: оставить деплою и гейтам"
    triggers = wf.get("on") or wf.get(True)
    части = triggers["workflow_dispatch"]["inputs"]["shards"]["options"]
    assert min(int(x) for x in части) >= 10, "делить минимум на 10 (правило дробления)"


# ─────────────────────────────────────────────── тело письма
ТЕЛО_HTML = (
    "<div>Добрый день! Прошу коммерческое предложение:</div>"
    "<table><tr><td>№</td><td>Наименование</td><td>Кол-во</td><td>Ед.</td></tr>"
    "<tr><td>1</td><td>Подшипник выдуманный ВЫД-6205</td><td>10</td><td>шт</td></tr>"
    "<tr><td>2</td><td>Уплотнение выдуманное УВ-40х52</td><td>4</td><td>шт</td></tr></table>"
    "<div>С уважением, отдел снабжения</div>"
    "<blockquote><div>Наш прежний запрос:</div>"
    "<table><tr><td>1</td><td>Муфта выдуманная МВ-9</td><td>2</td><td>шт</td></tr></table>"
    "</blockquote>")


def письмо_с_телом(n: int, тело: str, тип_тела="3", файлы=(), **k) -> dict:
    п = письмо(n, файлы, **k)
    п.update({"DESCRIPTION": тело, "DESCRIPTION_TYPE": тип_тела})
    return п


def test_тело_без_цитаты_прежней_переписки():
    """Ниже ответа заказчика лежит наш же запрос — его в тело не берём."""
    б = ms.тело_письма(ТЕЛО_HTML, "3").decode("utf-8")
    assert "ВЫД-6205" in б and "УВ-40х52" in б
    assert "МВ-9" not in б and "blockquote" not in б
    assert б.startswith("<html><head><meta charset=\"utf-8\">")


def test_тело_текстом_режется_на_строке_цитаты():
    текст = ("Здравствуйте, нужен подшипник выдуманный ВЫД-6205, 10 штук, срочно.\n"
             "Спасибо.\n"
             "\n"
             "25.09.2026, 10:00, Отдел закупок пишет:\n"
             "> Муфта выдуманная МВ-9, 2 шт\n")
    б = ms.тело_письма(текст, "1").decode("utf-8")
    assert "ВЫД-6205" in б and "МВ-9" not in б and "пишет" not in б


def test_строки_цитаты_со_знаком_больше_пропускаются():
    текст = "Прошу счёт на уплотнение выдуманное УВ-40х52, 4 шт.\n> старое: МВ-9\nЗаранее спасибо за ответ."
    б = ms.тело_письма(текст, "1").decode("utf-8")
    assert "УВ-40х52" in б and "МВ-9" not in б


@pytest.mark.parametrize("тело", ["", "   ", "См. вложение.", "<div>Спасибо, получили.</div>",
                                   "<p>&nbsp;</p><blockquote>" + "длинная цитата " * 20 + "</blockquote>"])
def test_короткое_или_пустое_тело_ссылкой_не_становится(тело):
    assert ms.тело_письма(тело, "3") == b""


def test_тело_письма_только_со_входом(monkeypatch):
    """Без MAIL_BODIES — прежнее поведение: только вложения, тело не читается."""
    monkeypatch.delenv("MAIL_BODIES", raising=False)
    п = письмо_с_телом(5, ТЕЛО_HTML, файлы=[77])
    assert [r["file_id"] for r in ms.ссылки_письма(п, "mail-deal")] == ["mail:77"]
    портал = Портал([п])
    ms.collect_refs_mail("mail-deal", 0, 0, bx=портал)
    assert "DESCRIPTION" not in портал.вызовы[0][1]["select"]


def test_тело_письма_отдельной_ссылкой_со_своим_ключом(monkeypatch):
    monkeypatch.setenv("MAIL_BODIES", "1")
    п = письмо_с_телом(5, ТЕЛО_HTML, файлы=[77], тип=ms.ЛИД, владелец=31)
    refs = ms.ссылки_письма(п, "mail-lead")
    assert [r["file_id"] for r in refs] == ["mail:77", "mail-body:5"]
    тело = refs[1]
    # ключ тела не совпадает ни с номером Диска, ни с номером письма без приставки
    assert тело["fo"]["тело"].startswith(b"<html>") and "id" not in тело["fo"]
    assert (тело["deal"], тело["side"], тело["field"]) == ("L31", doc_side.ЗАКАЗЧИК, "письмо 5")
    портал = Портал([п, письмо_с_телом(6, "коротко")])
    refs, курсор = ms.collect_refs_mail("mail-lead", 0, 0, bx=портал)
    assert "DESCRIPTION" in портал.вызовы[0][1]["select"]
    assert [r["file_id"] for r in refs] == ["mail:77", "mail-body:5"] and курсор == 6


def test_тело_письма_разбирается_без_запроса_к_порталу(monkeypatch):
    """Тело уже пришло списком дел: download не зовёт портал и не качает."""
    ix = индексатор()
    # Справочник наших компаний индексатор читает один раз на прогон — это не
    # запрос за телом; всё прочее к порталу из разбора тела — ошибка.
    monkeypatch.setattr(ix, "bx", lambda m, _p: {"result": []} if m == "crm.company.list"
                        else pytest.fail(f"тело письма пошло в портал: {m}"))
    monkeypatch.setattr(ix, "bx_файлов", lambda *_a: pytest.fail("тело письма пошло в Диск"))
    monkeypatch.setattr(ix.requests, "get", lambda *_a, **_k: pytest.fail("тело письма качали"))
    monkeypatch.setenv("MAIL_BODIES", "1")
    ref = ms.ссылки_письма(письмо_с_телом(8, ТЕЛО_HTML, тип=ms.ЛИД), "mail-lead")[0]
    rec, items = ix.handle(ref)
    assert rec["file_id"] == "mail-body:8" and rec["status"] != "не скачался"
    имена = " ".join(it["item_name"] for it in items)
    assert "ВЫД-6205" in имена and "УВ-40х52" in имена and "МВ-9" not in имена


def test_без_вложений_только_тела_и_своя_отметка(monkeypatch):
    """Прогон одних тел не тратит disk.file.get на вложения и двигает СВОЮ
    отметку: общая осталась бы за письмами, чьи вложения не брали."""
    monkeypatch.setenv("MAIL_BODIES", "1")
    monkeypatch.setenv("MAIL_FILES", "0")
    п = письмо_с_телом(5, ТЕЛО_HTML, файлы=[77, 78])
    assert [r["file_id"] for r in ms.ссылки_письма(п, "mail-deal")] == ["mail-body:5"]
    assert ms.имя_отметки("mail-lead") == "почта:mail-lead:тела"
    к = Курсор(40)
    assert ms.сдвинуть_отметку(к, "mail-lead", 40, 90, 50, "прогон-1")
    assert к.записи[-1][0] == "почта:mail-lead:тела"
    monkeypatch.delenv("MAIL_FILES")
    assert [r["file_id"] for r in ms.ссылки_письма(п, "mail-deal")] == ["mail:77", "mail:78", "mail-body:5"]
    assert ms.имя_отметки("mail-lead") == "почта:mail-lead"


def test_план_и_отметка_знают_про_вложения():
    """План читает, а шаг отметки пишет ту же отметку, что имел в виду прогон."""
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in ("plan", "cursor"):
        envs = [st.get("env") or {} for st in wf["jobs"][job]["steps"]]
        assert any("MAIL_FILES" in e and "MAIL_GROUP" in e for e in envs), job


# ─────────────────────────────────────────────── позиции тела письма
ТЕЛО_ПРОЗОЙ = (
    "Добрый день, коллеги!\n"
    "Просим рассмотреть возможность поставки следующих позиций в кратчайшие сроки.\n"
    "Подшипник выдуманный ВЫД-6205-2RS — 10 шт.\n"
    "Насос выдуманный НВ 40-25-160 с двигателем, 2 шт\n"
    "Уплотнение торцевое ГОСТ 99999-01 — 4 шт.\n"
    "Заранее благодарим за оперативный ответ.\n"
    "С уважением, Иван Выдуманный, отдел снабжения\n"
    "Тел.: +7 (900) 000-00-00, моб. 8 900 000 00 01\n"
    "E-mail: snab@example.test, сайт www.example.test\n")


def test_тело_письма_даёт_только_строки_перечня(monkeypatch):
    """Холостой замер 25.09.2026: у письма в десять строк ворота файла говорят
    «мало строк», и позицией становилось всё — приветствие, подпись, телефон."""
    ix = индексатор()
    monkeypatch.setattr(ix, "bx", lambda m, _p: {"result": []} if m == "crm.company.list"
                        else pytest.fail(f"тело письма пошло в портал: {m}"))
    monkeypatch.setenv("MAIL_BODIES", "1")
    ref = ms.ссылки_письма(письмо_с_телом(9, ТЕЛО_ПРОЗОЙ, "1", тип=ms.ЛИД), "mail-lead")[0]
    rec, items = ix.handle(ref)
    имена = [it["item_name"] for it in items]
    assert len(имена) == 3, имена
    assert all(("ВЫД-6205" in и) or ("НВ 40-25-160" in и) or ("ГОСТ 99999" in и) for и in имена)
    assert rec["rows_found"] == 3 and rec["item_lines"] == 3


def test_тело_без_перечня_причина_названа(monkeypatch):
    ix = индексатор()
    monkeypatch.setattr(ix, "bx", lambda m, _p: {"result": []})
    monkeypatch.setenv("MAIL_BODIES", "1")
    текст = ("Добрый день! Направляем вам информацию о нашей компании и просим\n"
             "рассмотреть сотрудничество в следующем году, ждём вашего ответа.\n"
             "С уважением, отдел продаж. Тел.: +7 (900) 000-00-00\n")
    ref = ms.ссылки_письма(письмо_с_телом(10, текст, "1", тип=ms.ЛИД), "mail-lead")[0]
    rec, items = ix.handle(ref)
    assert items == [] and rec["status"] != "разобран"
    assert "нет строк с признаками позиции" in rec["reason"]


def test_контакты_обвиняются_даже_с_цифрами():
    """Телефон несёт цифры, похожие на типоразмер, — строка контактов не позиция."""
    ix = индексатор()
    строки = [{"item_name": "Тел. 8 (900) 000-00-00 доб. 123"}, {"item_name": "info@example.test"},
              {"item_name": "Фильтр масляный выдуманный ФМ-009, 6 шт"}]
    assert [it["item_name"] for it in ix.позиции_письма(строки)] == ["Фильтр масляный выдуманный ФМ-009, 6 шт"]


def test_отбор_по_строке_только_для_тела_письма(monkeypatch):
    """Обычное вложение идёт прежними воротами файла — отбор по строке его не трогает."""
    ix = индексатор()
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: ТЕЛО_ПРОЗОЙ.encode("utf-8") * 1)
    monkeypatch.setattr(ix, "bx", lambda m, _p: {"result": []})
    ref = ms.ссылки_письма(письмо(11, [501], тип=ms.ЛИД), "mail-lead")[0]
    rec, items = ix.handle(ref)
    assert rec["file_id"] == "mail:501" and len(items) > 3


@pytest.mark.parametrize("строка, ждём", [
    ("Подшипник выдуманный ВЫД-6205-2RS — 10 шт.", (10, "шт")),
    ("Насос выдуманный НВ 40-25-160 с двигателем, 2 шт", (2, "шт")),
    ("Уплотнение торцевое ГОСТ 99999-01 — 4 шт.", (4, "шт")),
    ("Болт выдуманный М12х60 ГОСТ 7798-70, 1 500 шт", (1500, "шт")),
    ("Фильтр выдуманный ФВ 6205 10 шт", (10, "шт")),
    ("Ремкомплект выдуманный РК-7 — 3 компл.", (3, "компл")),
    ("Filter element VYD-100, 12 pcs", (12, "шт")),
    ("Кабель выдуманный 3х2,5 — 100 м", (100, "м")),
    ("Масло выдуманное МВ-46: 200 л", (200, "л")),
    ("Труба выдуманная 57х3,5 мм", (None, None)),
    ("Подшипник выдуманный ВЫД-6205-2RS", (None, None)),
    ("Муфта выдуманная МВ-9 0 шт", (None, None)),
])
def test_количество_из_строки_письма(строка, ждём):
    """Счётная единица — где угодно, мера — только после тире или двоеточия:
    размер детали не количество."""
    ix = индексатор()
    assert ix.количество_в_строке(строка) == ждём


def test_позиции_тела_несут_количество(monkeypatch):
    ix = индексатор()
    monkeypatch.setattr(ix, "bx", lambda m, _p: {"result": []})
    monkeypatch.setenv("MAIL_BODIES", "1")
    ref = ms.ссылки_письма(письмо_с_телом(12, ТЕЛО_ПРОЗОЙ, "1", тип=ms.ЛИД), "mail-lead")[0]
    _rec, items = ix.handle(ref)
    assert sorted((it["qty"], it["unit"]) for it in items) == [(2, "шт"), (4, "шт"), (10, "шт")]
