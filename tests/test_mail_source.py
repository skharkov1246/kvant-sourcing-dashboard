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


def test_границы_частей_смежны_и_покрывают_пачку():
    индексатор()
    от, до, частей = 1000, 1737, 12
    куски = [ms.границы_части(от, до, k, частей) for k in range(частей)]
    assert куски[0][0] == от and куски[-1][1] == до
    for (a, b), (c, d) in zip(куски, куски[1:]):
        assert b == c and a < b
    assert ms.границы_части(от, до, 0, 1) == (от, до)


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
    assert бюджет_шага("mail-supplier", "1.2")["PAR"] == "12"


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
    assert int(re.search(r"MAX_PARALLEL=(\d+)", шаг_разбора(wf)["run"]).group(1)) == \
        wf["jobs"]["index"]["strategy"]["max-parallel"]
    triggers = wf.get("on") or wf.get(True)
    части = triggers["workflow_dispatch"]["inputs"]["shards"]["options"]
    assert min(int(x) for x in части) >= 10, "делить минимум на 10 (правило дробления)"
