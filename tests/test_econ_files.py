"""Чтение файлов «Экономика проекта» (scripts/econ_files.py) — без сети и без базы.

  · ссылка clck.ru → страница проверки ссылок → путь общего диска → путь API Диска;
  · очередь: ни разу не читанные от новых сделок, затем давние чтения от давних;
  · предел суток — из базы: каждая попытка, удачная или нет; пауза между файлами;
  · строка: ключ прогона, sha256 пути вместо пути, сверка с «Суммой» сделки;
  · гейты: «шаблон другой» у большинства — стоп; суммы далеко — прогон помечает себя;
  · холостой прогон не трогает ни Диск, ни базу;
  · в журнале только счётчики: ни номеров сделок, ни путей, ни сумм, ни ключа;
  · прогон Actions и планировщик.

Корпус придуман (CLAUDE.md, правило 18): сделки, метки диска, пути и суммы.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import contracts  # noqa: E402
import econ_files as ef  # noqa: E402
import econ_template as et  # noqa: E402
from tests.test_econ_template import ПОЗИЦИИ, книга  # noqa: E402

СЕЙЧАС = dt.datetime(2026, 10, 25, 19, 7, tzinfo=dt.timezone.utc)
МЕТКА = "a1b2c3d4e5f6"                                 # придуманная метка общего диска
ПУТЬ = "Проекты ВЫДУМКА/Сделка 1/Экономика проекта.xlsx"


def _ссылка_диска(путь: str, метка: str = МЕТКА) -> str:
    return "https://disk.yandex.ru/edit/disk/" + urllib.parse.quote(f"vd/{метка}/disk/{путь}", safe="")


def _sba(цель: str) -> str:
    return "https://sba.yandex.ru/redirect?url=" + urllib.parse.quote(цель, safe="") + "&client=clck"


class Диск:
    """Подпорка Диска: короткая ссылка → страница проверки → файл."""

    def __init__(self, файлы: dict[str, bytes | None] | None = None, ссылки: dict[str, str] | None = None):
        self.файлы = файлы or {}
        self.ссылки = ссылки or {}
        self.запросы: list[str] = []
        self.коды: Counter = Counter()

    def раскрыть(self, url: str) -> str:
        self.запросы.append("раскрыть")
        return self.ссылки.get(url, "")

    def получить(self, url: str) -> bytes | None:
        self.запросы.append("получить")
        if url.startswith(ef.API_СКАЧАТЬ):
            путь = urllib.parse.unquote(url[len(ef.API_СКАЧАТЬ):])
            if путь not in self.файлы:
                self.коды["Диск: 404"] += 1
                return None
            self.коды["Диск: 200"] += 1
            return json.dumps({"href": "https://downloader.disk.yandex.ru/x/" + hashlib.md5(путь.encode()).hexdigest()}).encode()
        for путь, тело in self.файлы.items():
            if url.endswith(hashlib.md5(путь.encode()).hexdigest()):
                return тело
        return None


class Портал:
    def __init__(self, сделки: list[dict]):
        self.сделки = сделки
        self.вызовы = 0

    def deal_stage_meta(self):
        self.вызовы += 1
        return {"C0:EXECUTING": {"name": "Производство", "sem": "P"},
                "C0:PAID": {"name": "Оплата получена | Закрытие сделки", "sem": "P"},
                "C0:WON": {"name": "Сделка успешна", "sem": "S"}}

    def list_deals_fast(self, *, filter=None, select=None, **kw):
        self.вызовы += 1
        assert contracts.ECON_LINK in select and "OPPORTUNITY" in select and "CURRENCY_ID" in select
        return [dict(d) for d in self.сделки]


class База:
    """Подпорка закрытой базы с тем же предлом суток, что у econ_file_take_slot."""

    def __init__(self, взято: int = 0, прочитано: dict | None = None):
        self.взято = взято
        self._прочитано = прочитано or {}
        self.строки: list[dict] = []
        self.попытки: list[int] = []
        self.помечено: set[str] = set()

    def прочитано(self):
        return dict(self._прочитано)

    def взято_сегодня(self):
        return self.взято

    def взять(self, run_id, deal_id, предел):
        if self.взято >= min(предел, ef.ПРЕДЕЛ_СУТОК):
            return False
        self.взято += 1
        self.попытки.append(deal_id)
        return True

    def записать(self, r):
        assert r["status"] in ef.СТАТУСЫ
        if not any(x["run_id"] == r["run_id"] and x["deal_id"] == r["deal_id"] for x in self.строки):
            self.строки.append(dict(r))

    def пометить_прогон(self, run_id):
        self.помечено.add(run_id)
        return sum(1 for r in self.строки if r["run_id"] == run_id)

    def итог(self, run_id):
        return Counter(r["status"] for r in self.строки if r["run_id"] == run_id and run_id not in self.помечено)


def _сделка(i: int, *, ссылка: str | None = "", дней: int = 0, сумма="20000", валюта="EUR", стадия="C0:EXECUTING"):
    return {"ID": str(8000 + i), "DATE_CREATE": (СЕЙЧАС - dt.timedelta(days=дней)).isoformat(),
            "STAGE_ID": стадия, "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0", "OPPORTUNITY": сумма,
            "CURRENCY_ID": валюта,
            contracts.ECON_LINK: f"https://clck.ru/ВЫДУМКА{i}" if ссылка == "" else ссылка}


def _мир(n: int, тело: bytes | None = None, **kw):
    """n сделок, у каждой свой файл на общем диске; файл — придуманная книга."""
    тело = книга(ПОЗИЦИИ) if тело is None else тело
    сделки = [_сделка(i, дней=i, **kw) for i in range(n)]
    ссылки = {d[contracts.ECON_LINK]: _sba(_ссылка_диска(f"Сделка {i}/Экономика проекта.xlsx"))
              for i, d in enumerate(сделки)}
    файлы = {f"vd:{МЕТКА}:disk:/Сделка {i}/Экономика проекта.xlsx": тело for i in range(n)}
    return сделки, Диск(файлы, ссылки)


def _прогон(сделки, диск, база, режим="live", лимит=30, частей=10, **kw):
    журнал: list[str] = []
    сон: list[float] = []
    t = [0.0]

    def часы():
        return t[0]

    def спать(с):
        сон.append(с)
        t[0] += с
    код = ef.прогон(режим, лимит, частей, портал=Портал(сделки), run_id="ef-test-1", база=база, диск=диск,
                    печать=журнал.append, сейчас=СЕЙЧАС, часы=часы, спать=спать, **kw)
    return код, журнал, сон


# ── ссылка → файл ─────────────────────────────────────────────────────────────

def test_ссылка_раскрывается_в_путь_общего_диска():
    короткая = "https://clck.ru/ВЫДУМКА"
    диск = Диск(ссылки={короткая: _sba(_ссылка_диска(ПУТЬ))})
    статус, путь_api, внутри = ef.найти_файл(короткая, диск)
    assert статус is None and внутри == ПУТЬ
    assert путь_api == f"vd:{МЕТКА}:disk:/{ПУТЬ}"
    # прямая ссылка на Диск и вариант пути без «disk/»
    assert ef.найти_файл(_ссылка_диска(ПУТЬ), Диск())[1] == путь_api
    вариант = "https://disk.yandex.ru/edit/disk/" + urllib.parse.quote(f"vd/{МЕТКА}/{ПУТЬ}", safe="")
    assert ef.найти_файл(вариант, Диск())[1] == путь_api


def test_ссылка_не_туда():
    assert ef.найти_файл("https://clck.ru/x", Диск())[0] == ef.НЕ_РАСКРЫЛАСЬ
    assert ef.найти_файл("https://example.org/a.xlsx", Диск())[0] == ef.НЕ_РАСКРЫЛАСЬ
    личный = "https://disk.yandex.ru/edit/disk/" + urllib.parse.quote("disk/Папка/Экономика.xlsx", safe="")
    assert ef.найти_файл(личный, Диск())[0] == ef.НЕ_ОБЩИЙ
    assert ef.найти_файл("https://disk.yandex.ru/d/abc", Диск())[0] == ef.НЕ_ОБЩИЙ
    статус, _п, внутри = ef.найти_файл(_ссылка_диска("Сделка/Экономика.pdf"), Диск())
    assert статус == ef.НЕ_XLSX and внутри == "Сделка/Экономика.pdf"


def test_ключ_диска_не_уходит_на_чужой_хост():
    class Чужой(Диск):
        def получить(self, url):
            self.запросы.append(url)
            return json.dumps({"href": "https://downloader.example.org/file"}).encode() if url.startswith(
                ef.API_СКАЧАТЬ) else b"PK"
    диск = Чужой()
    assert ef.скачать(f"vd:{МЕТКА}:disk:/{ПУТЬ}", диск) is None
    assert len(диск.запросы) == 1, "по ссылке не на Яндекс с ключом не ходим"


def test_общий_диск_как_у_зонда():
    import probe_econ_files as probe
    for путь in (f"vd/{МЕТКА}/disk/{ПУТЬ}", f"vd/{МЕТКА}/{ПУТЬ}", f"vd:{МЕТКА}:disk:/{ПУТЬ}", "disk/x.xlsx"):
        assert ef.общий_диск(путь) == probe.общий_диск(путь), путь


# ── очередь ───────────────────────────────────────────────────────────────────

def test_очередь_новые_от_новых_затем_давние_от_давних():
    канд = [_сделка(i, дней=10 * i) for i in range(6)]
    прочитано = {8001: СЕЙЧАС - dt.timedelta(days=3),            # свежее — не берётся
                 8002: СЕЙЧАС - dt.timedelta(days=15),
                 8004: СЕЙЧАС - dt.timedelta(days=40)}
    счёт = Counter()
    q = ef.очередь(канд, прочитано, СЕЙЧАС, счёт)
    assert [d["ID"] for d in q] == ["8000", "8003", "8005", "8004", "8002"]
    assert (счёт["ни разу"], счёт["перечитать"], счёт["свежих"]) == (3, 2, 1)


def test_кандидаты_открытые_со_ссылкой():
    сделки = [_сделка(0), _сделка(1, ссылка=None), _сделка(2, стадия="C0:PAID"), _сделка(3, стадия="C0:WON"),
              dict(_сделка(4), CATEGORY_ID="7"), _сделка(5, ссылка="не ссылка")]
    счёт = Counter()
    канд = ef.кандидаты(сделки, Портал([]).deal_stage_meta(), счёт)
    assert [d["ID"] for d in канд] == ["8000"]
    assert счёт["открытых"] == 3 and счёт["закрыта"] == 2 and счёт["без ссылки"] == 2


def test_нарезка_на_части_смежными_отрезками():
    части = ef.нарезать(list(range(30)), 10)
    assert len(части) == 10 and [x for ч in части for x in ч] == list(range(30))
    assert {len(ч) for ч in части} == {3}
    assert len(ef.нарезать(list(range(5)), 10)) == 10


# ── прогон ────────────────────────────────────────────────────────────────────

def test_живой_прогон_пишет_строки_с_ключом_и_без_пути():
    сделки, диск = _мир(3)
    сделки[1]["OPPORTUNITY"] = "0"                      # суммы в сделке нет
    база = База()
    код, журнал, сон = _прогон(сделки, диск, база)
    assert код == 0 and len(база.строки) == 3 and база.попытки == [8000, 8001, 8002]
    r = база.строки[0]
    assert r["run_id"] == "ef-test-1" and r["method_ver"] == ef.METHOD_VER and r["status"] == et.СВЕРЕНО
    assert r["file_key"] == hashlib.sha256("Сделка 0/Экономика проекта.xlsx".encode()).hexdigest()
    assert r["sale_sub"] > 0 and r["purchase_eur"] == 6000.0 and r["rebid"] is False and r["sub_cur"] == "RUB"
    # «Сумма» 20 000 € против продажи файла в рублях — по курсу самого файла
    assert r["deal_cur"] == "EUR" and r["bitrix_ratio"] == pytest.approx(20_000 / r["sale_eur"], rel=1e-5)
    assert база.строки[1]["bitrix_ratio"] == 0
    текст = json.dumps(база.строки, ensure_ascii=False)
    assert "Сделка 0" not in текст and МЕТКА not in текст, "пути и метки диска в базе нет"
    # пауза между файлами — не меньше двух секунд, первый — без паузы
    assert len(сон) == 2 and all(с >= ef.ПАУЗА_С for с in сон)
    _журнал_чист(журнал, сделки)
    assert any(s.startswith("файлы: сверено 3") for s in журнал)
    assert any("в сделке суммы нет 1" in s for s in журнал)


def _журнал_чист(журнал: list[str], сделки: list[dict]) -> None:
    текст = "\n".join(журнал)
    for d in сделки:
        assert d["ID"] not in текст, "номера сделок в журнал не идут"
    for слово in ("Сделка ", МЕТКА, "clck.ru/", "sba.yandex", "disk.yandex", "6000", "наценк", "OAuth"):
        assert слово not in текст, слово
    assert not re.search(r"\d{4,}\.\d", текст), "сумм в журнале нет"


def test_пауза_от_конца_прошлого_файла():
    """Закачка шла 5 с — пауза перед следующим файлом всё равно не меньше двух секунд."""
    сделки, диск = _мир(3)
    t = [0.0]
    сон: list[float] = []
    получить = диск.получить

    def долго(url):
        t[0] += 5.0
        return получить(url)
    диск.получить = долго

    def спать(с):
        сон.append(с)
        t[0] += с
    ef.прогон("live", 30, 10, портал=Портал(сделки), run_id="ef-test-2", база=База(), диск=диск,
              печать=lambda s: None, сейчас=СЕЙЧАС, часы=lambda: t[0], спать=спать)
    assert сон == [ef.ПАУЗА_С, ef.ПАУЗА_С]


def test_предел_суток_из_базы_и_неудачная_попытка_считается():
    сделки, диск = _мир(5)
    диск.файлы.pop(f"vd:{МЕТКА}:disk:/Сделка 0/Экономика проекта.xlsx")        # первый не скачается
    база = База(взято=27)
    код, журнал, _сон = _прогон(сделки, диск, база)
    assert код == 0 and база.взято == 30 and len(база.строки) == 3
    assert [r["status"] for r in база.строки] == [ef.НЕ_СКАЧАЛСЯ, et.СВЕРЕНО, et.СВЕРЕНО]
    assert any("взято 27 из 30" in s and "берётся 3" in s for s in журнал)
    # второй прогон в те же сутки — ни одного запроса к Диску
    диск2 = Диск(диск.файлы, диск.ссылки)
    код, журнал, _сон = _прогон(сделки, диск2, база)
    assert код == 0 and диск2.запросы == [] and len(база.строки) == 3


def test_предел_держит_база_даже_если_очередь_длиннее():
    """Соседний прогон взял попытки между чтением счёта и чтением файла: база не даёт больше."""
    сделки, диск = _мир(4)

    class Соседи(База):
        def взять(self, run_id, deal_id, предел):
            self.взято += 1                       # сосед успел взять попытку раньше
            return super().взять(run_id, deal_id, предел)
    база = Соседи(взято=26)
    код, журнал, _ = _прогон(сделки, диск, база)
    assert len(база.строки) == 2 and any("предел суток исчерпан" in s for s in журнал)


def test_статусы_файла_в_строке():
    сделки, диск = _мир(1, тело=книга(ПОЗИЦИИ, заголовки={"G": "Количество"}))
    база = База()
    _прогон(сделки, диск, база)
    r = база.строки[0]
    assert r["status"] == et.ШАБЛОН_ДРУГОЙ and r["sale_eur"] is None and r["markup"] is None
    assert r["bitrix_ratio"] is None and r["fx"] is None and r["file_key"]


def test_гейт_шаблона_останавливает_чтение():
    сделки, диск = _мир(8, тело=книга(ПОЗИЦИИ, заголовки={"AN": "Итого"}))
    база = База()
    код, журнал, _ = _прогон(сделки, диск, база)
    assert код == 1 and len(база.строки) == ef.ГЕЙТ_ОТ, "предел суток не тратится на сменившийся шаблон"
    assert any(s.startswith("::error::«шаблон другой» у 5 из 5") for s in журнал)
    assert "где не сошлось: AN12 5" in журнал, "в журнале — какая подпись шаблона сменилась"
    assert {r["why"] for r in база.строки} == {"AN12"}


def test_гейт_сумм_помечает_прогон():
    # «Сумма» сделок в сто раз больше продажи файла: разбор берёт не ту валюту
    сделки, диск = _мир(6, сумма="200000000")
    база = База()
    код, журнал, _ = _прогон(сделки, диск, база)
    assert код == 1 and "ef-test-1" in база.помечено
    assert any(s.startswith("::error::гейт сумм") for s in журнал)
    assert any("сверено 0" in s for s in журнал if s.startswith("в базе по ключу прогона"))
    # сделки без суммы — не довод против разбора
    сделки, диск = _мир(6, сумма="0")
    база = База()
    код, _журнал, _ = _прогон(сделки, диск, база)
    assert код == 0 and not база.помечено


def test_холостой_прогон_не_трогает_диск_и_базу():
    сделки, диск = _мир(4)
    база = База(взято=3, прочитано={8000: СЕЙЧАС - dt.timedelta(days=2)})
    код, журнал, сон = _прогон(сделки, диск, база, режим="dry")
    assert код == 0 and диск.запросы == [] and база.строки == [] and база.попытки == [] and сон == []
    assert any("ни разу не читанных 3" in s and "свежих 1" in s and "взято 3 из 30" in s for s in журнал)
    assert журнал[-1].startswith("холостой прогон")
    # без базы — очередь целиком, предел не известен
    код, журнал, _ = _прогон(сделки, Диск(), None, режим="dry", лимит=2)
    assert код == 0 and any("берётся 2" in s and "не знаем" in s for s in журнал)


def test_входы_и_секреты(monkeypatch):
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal.example/rest/1/x/")
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("YADISK_TOKEN", raising=False)
    печать = []
    assert ef.main(["--limit", "31"]) == 2
    assert ef.main(["--limit", "0"]) == 2
    assert ef.main(["--parts", "5"]) == 2
    assert ef.main(["--mode", "live"]) == 2, "live без ключа Диска"
    monkeypatch.setenv("YADISK_TOKEN", "ключ-ВЫДУМКА")
    assert ef.main(["--mode", "live"]) == 2, "live без базы"
    # dry по умолчанию: ни Диска, ни записи
    сделки, диск = _мир(2)
    assert ef.main([], портал=Портал(сделки), диск=диск, печать=печать.append, run_id="ef-t") == 0
    assert диск.запросы == [] and any(s.startswith("холостой прогон") for s in печать)
    assert "ключ-ВЫДУМКА" not in "\n".join(печать)


def test_статусы_совпадают_с_базой():
    sql = (ROOT / "library" / "supabase" / "econ_files_schema.sql").read_text(encoding="utf-8")
    блок = re.search(r"econ_file_reads_status_chk\s+check \(status in \((.*?)\)\);", sql, re.S).group(1)
    assert tuple(re.findall(r"'([^']+)'", блок)) == ef.СТАТУСЫ
    вид = re.search(r"status not in \((.*?)\)", sql).group(1)
    assert set(re.findall(r"'([^']+)'", вид)) == {ef.НЕ_РАСКРЫЛАСЬ, ef.НЕ_СКАЧАЛСЯ}
    assert re.search(r"предел constant integer := (\d+);", sql).group(1) == str(ef.ПРЕДЕЛ_СУТОК)
    assert "valuta" not in sql and "'валюта сделки другая'" in sql
    import dealctx
    assert dealctx.ECON_OTHER_CUR == "валюта сделки другая"


# ── прогон Actions и планировщик (решение владельца 23.10.2026) ───────────────

def _wf() -> dict:
    import yaml
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "econ-files.yml").read_text(encoding="utf-8"))
    wf["on"] = wf.get("on", wf.get(True))
    return wf


def test_прогон_actions():
    wf = _wf()
    assert wf["on"]["repository_dispatch"]["types"] == ["econ-files"]
    assert "schedule" not in wf["on"], "время держит планировщик Cloudflare"
    входы = wf["on"]["workflow_dispatch"]["inputs"]
    assert входы["mode"]["default"] == "dry" and входы["mode"]["options"] == ["dry", "live"]
    assert int(входы["limit"]["default"]) <= ef.ПРЕДЕЛ_СУТОК
    assert [int(x) for x in входы["parts"]["options"]] == list(ef.ЧАСТИ)
    assert wf["concurrency"] == {"group": "bitrix-portal", "cancel-in-progress": False}
    job = wf["jobs"]["econ"]
    assert "'live'" in job["env"]["MODE"] and "'30'" in job["env"]["LIMIT"] and "'10'" in job["env"]["PARTS"]
    шаги = {ш.get("name"): ш for ш in job["steps"]}
    чтение = шаги["Чтение файлов"]
    assert "python scripts/econ_files.py" in чтение["run"]
    env = чтение["env"]
    assert "secrets.YADISK_TOKEN" in env["YADISK_TOKEN"] and "live" in env["YADISK_TOKEN"], \
        "ключ Диска — только в live"
    assert env["RUN_ID"].startswith("ef-${{ github.run_id }}")
    assert env["BITRIX_PARALLEL"] == "1"
    assert "econ_files_schema.sql" in шаги["Схема в рабочей базе"]["run"]
    assert "tests/test_econ_files_sql.py" in шаги["Проверки на чистой базе"]["run"]
    свод = job["steps"][-1]
    assert свод["run"] == "python scripts/realization_ctx.py --publish"
    assert "env.MODE == 'live'" in свод["if"] and "!cancelled()" in свод["if"]
    assert job["timeout-minutes"] >= ef.СРОК_МИН + 5


def test_планировщик_зовёт_каждую_ночь_в_свободный_час():
    src = (ROOT / "scheduler" / "worker.js").read_text(encoding="utf-8")
    assert 'if (h === 22) дела.push("econ-files");' in src
    тело = src[src.index("function делаЧаса"):src.index("export default")]
    занятые = re.findall(r"if \(h === (\d+)(?: && [^)]*)?\) дела\.push", тело)
    assert занятые.count("22") == 1, "в 22 UTC больше ничего не идёт"


def test_схема_подключена_к_миграциям():
    миграции = (ROOT / ".github/workflows/zip-db.yml").read_text(encoding="utf-8")
    assert "apply library/supabase/econ_files_schema.sql" in миграции
    assert (миграции.index("apply library/supabase/econ_files_schema.sql")
            < миграции.index("apply zip/supabase/migrations_rls_stage1.sql"))
