"""Ежедневный проход писем поставщиков (library/increment.py, mail_source.письма_прохода).

Что закреплено:
  • письмо с номером не выше отметки не берётся — кроме страницы перекрытия у
    самой границы, созданной после начала прошлого прохода; письмо выше верха
    прохода (пришло во время разбора) — тоже нет: его возьмёт следующий;
  • первый проход — не глубже N дней и не по отметке ручной пачки; наплыв
    сверх лимита переносится на завтра от границы без потерь;
  • отметка писем сдвигается только после разбора с записью, дошедшего до
    конца: холостой замер, падение разбора, отказ Диска — отметка стоит;
  • разбор с записью кладёт строку шага ДО первой записи и её конец при любом
    выходе — по ней откатывается и упавший проход; без строки шага запись не
    начинается; откат — только последнего прохода;
  • письма не держат сделки и карточки и в общем шаге «начало»: сбой верха
    писем и неверный вход писем — предупреждение, отказывает шаг писем;
  • повтор того же окна не дублирует ни файлов, ни строк спроса, ни цен и не
    платит порталу за разобранное; письмо, разобранное ручной пачкой,
    отсеивается по lib_files; отметки пачки и прохода друг друга не трогают;
  • прогон: шаг писем после отметки сделок и карточек, своя отметка последней,
    ручное умолчание режима — замер, ночное — off (ночной замер повторял бы
    первый проход каждую ночь), расписание не тронуто.

Корпус придуман (CLAUDE.md, правило 18): номера писем, компаний, контактов,
файлов и позиции выдуманы. Портал и база подменены: портал отдаёт письма по
фильтру ключа, база держит lib_files, lib_demand, lib_prices и lib_metric_runs
в памяти.
"""
from __future__ import annotations

import functools
import importlib.util
import io
import json
import pathlib
import re
import subprocess
import sys
import types
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
for каталог in ("library", "scripts"):
    sys.path.insert(0, str(ROOT / каталог))

# Плоско, как их берёт прогон: индексатор и increment импортируют mail_source
# плоско, и подмены должны касаться того же модуля.
import increment  # noqa: E402
import mail_source as ms  # noqa: E402
import price_store  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "library-daily.yml"
МСК = timezone(timedelta(hours=3))
СЕЙЧАС = datetime.now(timezone.utc).replace(microsecond=0)
ПОТОК = price_store.КОЛОНКИ.index("feed")
ФАЙЛ = price_store.КОЛОНКИ.index("source_url")


# ─────────────────────────────────────────────── корпус
def письмо(n: int, создано: datetime | None, файлы=(), тип: int = ms.КОМПАНИЯ,
           направление: int = ms.ВХОДЯЩЕЕ, владелец: int = 55, тело: str | None = None) -> dict:
    x = {"ID": str(n), "OWNER_ID": str(владелец), "OWNER_TYPE_ID": str(тип),
         "DIRECTION": str(направление),
         "CREATED": создано.astimezone(МСК).isoformat() if создано else "",
         "FILES": [{"id": str(f)} for f in файлы]}
    if тело is not None:
        x["DESCRIPTION"], x["DESCRIPTION_TYPE"] = тело, "1"
    return x


def назад(**к) -> datetime:
    return СЕЙЧАС - timedelta(**к)


class Портал:
    """Подмена indexer.bx: письма по фильтру группы и ключа, контакты по @ID."""

    def __init__(self, письма: list[dict], контакты: dict[str, list[str]] | None = None):
        self.письма = sorted(письма, key=lambda x: int(x["ID"]))
        self.контакты = контакты or {}
        self.вызовы: list[tuple[str, dict]] = []

    def __call__(self, метод: str, параметры: dict) -> dict:
        self.вызовы.append((метод, параметры))
        if метод == ms.МЕТОД:
            assert параметры.get("start") == -1, "смещение считает total — время метода (429)"
            ф = параметры["filter"]
            assert ф["TYPE_ID"] == ms.ТИП_ПИСЬМО
            типы = ф["OWNER_TYPE_ID"] if isinstance(ф["OWNER_TYPE_ID"], list) else [ф["OWNER_TYPE_ID"]]

            def подходит(x: dict) -> bool:
                н = int(x["ID"])
                return (int(x["OWNER_TYPE_ID"]) in типы
                        and ("DIRECTION" not in ф or int(x["DIRECTION"]) == ф["DIRECTION"])
                        and (">ID" not in ф or н > int(ф[">ID"]))
                        and ("<ID" not in ф or н < int(ф["<ID"]))
                        and ("<=ID" not in ф or н <= int(ф["<=ID"])))
            подходят = [x for x in self.письма if подходит(x)]
            if (параметры.get("order") or {}).get("ID") == "DESC":
                подходят.reverse()
            поля = параметры.get("select") or []
            return {"result": [{к: v for к, v in x.items() if к in поля}
                               for x in подходят[:ms.СТРАНИЦА]]}
        if метод == ms.МЕТОД_КОНТАКТОВ:
            номера = [str(к) for к in параметры["filter"]["@ID"]]
            return {"result": [{"ID": к, "COMPANY_ID": (self.контакты[к] or ["0"])[0],
                                "COMPANY_IDS": self.контакты[к]}
                               for к in номера if к in self.контакты]}
        if метод == "crm.company.list":            # справочник наших компаний
            return {"result": []}
        raise AssertionError(f"неожиданный метод портала: {метод}")

    def списков(self) -> int:
        return sum(1 for м, _ in self.вызовы if м == ms.МЕТОД)


# ─────────────────────────────────────────────── письма_прохода: окно
def поставщики(номера, создано) -> list[dict]:
    return [письмо(n, создано(n), [90_000 + n]) for n in номера]


def test_письмо_старше_отметки_не_берётся():
    """Отметка 300: берутся письма выше неё до верха прохода и из перекрытия —
    только созданные после начала прошлого прохода (минус запас)."""
    с = назад(hours=3)
    письма = поставщики(range(1, 301), lambda n: назад(days=2))
    письма[298] = письмо(299, назад(hours=1), [90_299])     # у границы, лёг поздно
    письма += поставщики(range(301, 451), lambda n: назад(minutes=30))
    письма += поставщики(range(451, 471), lambda n: СЕЙЧАС)  # пришли во время прохода
    # Чужие письма вперемешку: сделка и наш исходящий — не группа поставщиков.
    письма += [письмо(10_001, СЕЙЧАС, [1], тип=ms.СДЕЛКА),
               письмо(10_002, СЕЙЧАС, [2], направление=ms.ИСХОДЯЩЕЕ)]
    портал = Портал(письма)
    взяты, граница, замер = ms.письма_прохода("mail-supplier", 300, с, 450, 1000, bx=портал)
    номера = [int(x["ID"]) for x in взяты]
    assert номера == [299] + list(range(301, 451))
    assert граница == 450 and замер["перекрытие"] == 1 and замер["новых"] == 150
    assert not any(n <= 300 and n != 299 for n in номера), "письмо старше отметки взято"
    assert замер["запросов"] == портал.списков() <= 150 // 50 + 2


def test_перекрытие_не_глубже_страницы():
    """Всё окно перекрытия свежее (наплыв у границы) — читается одна страница."""
    с = назад(hours=3)
    письма = поставщики(range(1, 501), lambda n: назад(minutes=10))
    портал = Портал(письма)
    взяты, граница, замер = ms.письма_прохода("mail-supplier", 400, с, 500, 1000, bx=портал)
    assert замер["перекрытие"] == ms.ПЕРЕКРЫТИЕ and замер["новых"] == 100
    assert граница == 500
    назад_читали = [п for м, п in портал.вызовы if (п.get("order") or {}).get("ID") == "DESC"]
    assert len(назад_читали) == 1


def test_первый_проход_не_глубже_окна():
    """Отметки нет: письма, созданные за последние дни окна, и ни одного старше."""
    с = назад(days=7)
    письма = поставщики(range(1, 201), lambda n: назад(days=30 - n // 10))   # 30…11 дней
    письма += поставщики(range(201, 261), lambda n: назад(days=6, minutes=-n))  # в окне
    портал = Портал(письма)
    взяты, граница, замер = ms.письма_прохода("mail-supplier", 0, с, 260, 1000, bx=портал)
    assert [int(x["ID"]) for x in взяты] == list(range(201, 261))
    assert граница == 260 and замер["первого_прохода"] == 60 and not замер["обрезано"]
    # 60 писем окна + страница, на которой встретилось старое: не весь портал.
    assert портал.списков() == 2
    for _, п in портал.вызовы:
        assert п["order"] == {"ID": "DESC"} and "<ID" in п["filter"]


def test_письмо_без_даты_в_окне():
    """Недоказанное «старое» дешевле потерянного ответа поставщика."""
    с = назад(days=7)
    письма = [письмо(1, назад(days=20), [1]), письмо(2, None, [2]), письмо(3, назад(hours=1), [3])]
    взяты, _, _ = ms.письма_прохода("mail-supplier", 0, с, 3, 1000, bx=Портал(письма))
    assert [int(x["ID"]) for x in взяты] == [2, 3]


def test_лимит_переносит_остаток_на_завтра_без_потерь():
    """Наплыв 130 писем при лимите 50: три прохода подряд берут все, ни одного дважды."""
    письма = поставщики(range(1, 101), lambda n: назад(days=3))
    письма += поставщики(range(101, 231), lambda n: назад(hours=5))
    портал = Портал(письма)
    отметка, взятые, новых = 100, [], []
    for _ in range(3):
        с = назад(hours=6)          # начало прошлого прохода минус запас
        прежняя = отметка
        письма_дня, отметка, замер = ms.письма_прохода("mail-supplier", отметка, с, 230, 50,
                                                       bx=портал)
        номера = [int(x["ID"]) for x in письма_дня]
        взятые += номера
        новых += [n for n in номера if n > прежняя]
        assert замер["новых"] <= 50
    assert sorted(set(взятые)) == list(range(101, 231)), "письмо наплыва потерялось"
    assert sorted(новых) == list(range(101, 231)), "новое письмо взято дважды"
    assert отметка == 230
    # Дважды — только страница перекрытия у границы: её отсеет lib_files.
    assert len(взятые) - len(set(взятые)) <= 2 * ms.ПЕРЕКРЫТИЕ


def test_первый_проход_с_наплывом_берёт_свежие():
    с = назад(days=7)
    письма = поставщики(range(1, 1301), lambda n: назад(hours=2))
    взяты, граница, замер = ms.письма_прохода("mail-supplier", 0, с, 1300, 1000,
                                               bx=Портал(письма))
    номера = [int(x["ID"]) for x in взяты]
    assert номера == list(range(301, 1301)) and граница == 1300 and замер["обрезано"]


def test_наибольший_номер_одним_запросом():
    портал = Портал([письмо(n, СЕЙЧАС, [n]) for n in (5, 9, 12)]
                    + [письмо(40, СЕЙЧАС, [40], тип=ms.СДЕЛКА)])
    assert ms.наибольший_номер("mail-supplier", портал) == 12
    (м, п), = портал.вызовы
    assert п["order"] == {"ID": "DESC"} and п["select"] == ["ID"] and п["start"] == -1
    assert ms.наибольший_номер("mail-supplier", Портал([])) == 0


def test_ключи_прохода_и_пачки_одни():
    """Отсев по lib_files между пачкой и проходом держится на одинаковых file_id."""
    письма = [письмо(1, СЕЙЧАС, [7, 8], тело="Прошу КП: подшипник выдуманный ВЫД-6205 — 10 шт."
                                         " Уплотнение выдуманное УВ-40 — 4 шт."),
              письмо(2, СЕЙЧАС, [8], тип=ms.КОНТАКТ, владелец=77)]
    пачка, _ = ms.collect_refs_mail("mail-supplier", 0, 0, bx=Портал(письма))
    проход, счёт = ms.ссылки_писем(письма, "mail-supplier")
    assert [r["file_id"] for r in пачка] == [r["file_id"] for r in проход]
    assert счёт["повторов"] == 1, "файл Диска в двух письмах — одна ссылка"


# ─────────────────────────────────────────────── база в памяти
class База:
    """Подмена indexer.connect: lib_files, lib_demand, lib_prices, lib_metric_runs."""

    def __init__(self):
        self.файлы: dict[str, str] = {}
        self.спрос: list[tuple] = []
        self.цены: list[tuple] = []
        self.замеры: dict[tuple[str, str], tuple[dict, int]] = {}
        self.такт = 0
        # Порядок записей: строка шага обязана лечь раньше первой записи разбора,
        # а её конец — позже последней.
        self.журнал: list[str] = []

    def cursor(self):
        return Курсор(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def отметка(self, metric: str) -> dict | None:
        свои = [(т, nums) for (m, _k), (nums, т) in self.замеры.items() if m == metric]
        return max(свои, key=lambda x: x[0])[1] if свои else None


class Курсор:
    def __init__(self, база: База):
        self.база = база
        self.connection = база
        self._строки: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        б = self.база
        if "information_schema.columns" in s:
            self._строки = [(к,) for к in params[0]] if "lib_files" in s else []
        elif s.startswith("select file_id from lib_files where status <> 'не скачался'"):
            self._строки = [(к,) for к, ст in б.файлы.items() if ст != "не скачался"]
        elif s.startswith("select file_id from lib_files where status = 'не скачался'"):
            self._строки = [(к,) for к, ст in б.файлы.items() if ст == "не скачался"]
        elif s.startswith("select nums from lib_metric_runs where metric = %s"):
            nums = б.отметка(params[0])
            self._строки = [(json.dumps(nums),)] if nums is not None else []
        elif s.startswith("insert into lib_metric_runs"):
            metric, run_key, nums, _note = params
            б.такт += 1
            б.замеры[(metric, run_key)] = (json.loads(nums), б.такт)
            б.журнал.append(f"замер {metric}")
        elif s.startswith("update lib_metric_runs set nums = nums ||"):
            metric, run_key = params
            assert "'конец'" in s and (metric, run_key) in б.замеры, "конец шага без строки шага"
            б.замеры[(metric, run_key)][0]["конец"] = datetime.now(timezone.utc).timestamp()
            б.журнал.append(f"конец {metric}")
        elif s.startswith("delete from lib_prices"):
            поток, _источник, файлы = params
            б.цены = [r for r in б.цены if not (r[ПОТОК] == поток and r[ФАЙЛ] in файлы)]
        else:
            raise AssertionError(f"неожиданный запрос: {s[:90]}")

    def fetchall(self):
        return list(self._строки)

    def fetchone(self):
        return self._строки[0] if self._строки else None


def вставка(cur, sql, rows, template=None, page_size=100):
    s = " ".join(sql.split())
    б = cur.база
    if "insert into lib_segments" in s:
        return
    if "insert into lib_files" in s:
        колонки = re.search(r"insert into lib_files \(([^)]*)\)", s).group(1).split(", ")
        for r in rows:
            б.файлы[r[0]] = r[колонки.index("status")]
        б.журнал.append("файлы")
    elif "insert into lib_demand" in s:
        б.спрос.extend(rows)
    elif "insert into lib_prices" in s:
        б.цены.extend(rows)
    else:
        raise AssertionError(f"неожиданная вставка: {s[:90]}")


# ─────────────────────────────────────────────── прогон разбора целиком
@functools.lru_cache(maxsize=1)
def кп_xlsx() -> bytes:
    """Выдуманное КП поставщика: шапка с валютой, четыре позиции."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Коммерческое предложение № 77 от 20.09.2026"])
    ws.append([])
    for r in (["No", "Description", "Part No", "Qty", "Unit", "Unit Price, USD", "Amount, USD"],
              ["1", "Roller bearing VYD", "VYD-22315", "4", "pcs", "312,50", "1 250,00"],
              ["2", "Mechanical seal VYD", "VYD-4471/2", "10", "pcs", "85,00", "850,00"],
              ["3", "Spacer ring VYD", "VYD-6205", "2", "pcs", "47,25", "94,50"],
              ["4", "Rotor shaft VYD", "VYD-125/07", "1", "pcs", "12 400,00", "12 400,00"]):
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class Закачка:
    """Подмена indexer.download: тело письма — как есть, вложение — КП или отказ."""

    def __init__(self, отказ: bool = False, падение: str | None = None):
        self.отказ = отказ
        self.падение = падение
        self.диск: list[str] = []

    def __call__(self, fo: dict, rec: dict | None = None):
        if "тело" in fo:
            return fo["тело"] or None
        self.диск.append(str(fo["id"]))
        if self.падение == str(fo["id"]):
            raise RuntimeError("сеть оборвалась посреди прохода")
        if self.отказ:
            if rec is not None:
                rec["reason"] = "disk.file.get: ACCESS_DENIED"
            return None
        return кп_xlsx()


def индексатор(monkeypatch, tmp_path, база: База, портал: Портал, закачка: Закачка, *,
               запись: bool, верх: int):
    """Свежий модуль разбора с окружением шага писем library-daily.yml."""
    pytest.importorskip("psycopg2", reason="индексатор импортирует psycopg2")
    import psycopg2.extras
    for к, v in {"SOURCE": "mail", "MAIL_GROUP": "mail-supplier", "INCREMENT": "1",
                 "MAIL_PRICES": "1", "MAIL_CONTACT_COMPANY": "1", "MAIL_BODIES": "1",
                 "MAIL_FILES": "1", "RETRY_FAILED": "1", "RETRY_FAILED_LIMIT": "150",
                 "WORKERS": "1", "SHARDS": "1", "SHARD": "0", "LIMIT": "0",
                 "MAIL_DAILY": "apply" if запись else "dry",
                 "INCREMENT_MAX_MAIL": str(верх),
                 # Ключ строки шага — как у прогона Actions: прогон и попытка.
                 "GITHUB_RUN_ID": "5001", "GITHUB_RUN_ATTEMPT": "1",
                 "INCREMENT_START": str(int(СЕЙЧАС.timestamp())),
                 "BITRIX_WEBHOOK_URL": "https://portal.example.test/rest/1/x",
                 "SUPABASE_DB_URL": "postgresql://example.test/db"}.items():
        monkeypatch.setenv(к, v)
    for к in ("MAIL_FIRST_DAYS", "MAIL_DAILY_LIMIT", "BITRIX_FILES_WEBHOOK_URL"):
        monkeypatch.delenv(к, raising=False)
    if запись:
        monkeypatch.setenv("APPLY", "1")
    else:
        monkeypatch.delenv("APPLY", raising=False)
    # Свой GITHUB_ENV у каждого шага: граница прошлого прогона не должна сойти
    # за границу этого.
    окружение = tmp_path / "github_env"
    окружение.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_ENV", str(окружение))
    spec = importlib.util.spec_from_file_location(
        f"indexer_mail_daily_{int(запись)}", ROOT / "library" / "indexer.py")
    ix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ix)
    # increment берёт `import indexer` — он обязан получить этот же модуль.
    monkeypatch.setitem(sys.modules, "indexer", ix)
    monkeypatch.setattr(ix, "connect", lambda *a, **k: база)
    monkeypatch.setattr(ix, "bx", портал)
    monkeypatch.setattr(ix, "download", закачка)
    monkeypatch.setattr(ix, "наша_компания", lambda _v: None)
    monkeypatch.setattr(psycopg2.extras, "execute_values", вставка)
    return ix


def env_шага(tmp_path) -> dict[str, str]:
    строки = (tmp_path / "github_env").read_text(encoding="utf-8").splitlines()
    return dict(с.split("=", 1) for с in строки if "=" in с)


ТЕЛО = ("Добрый день! Направляем предложение:\n"
        "Подшипник выдуманный ВЫД-6205 — 10 шт.\n"
        "Уплотнение выдуманное УВ-40х52 — 4 шт.\n"
        "Муфта выдуманная МВ-9 — 2 шт.\n")


def диск(n: int) -> int:
    """Номер файла Диска у письма n — свой ряд чисел, чтобы ключи не путались."""
    return 100_000 + n


def корпус() -> list[dict]:
    """Старые письма поставщиков (20 дней), свежие (до трёх дней) и чужие вперемешку.

    Свежие — 1030, 1032, … 1040: чётные компании, нечётные по порядку — контакт 77
    (у него одна компания, 501). У 1034 — тело с перечнем; 1035 несёт тот же файл
    Диска, что 1034 (пересылка): ссылка одна.
    """
    письма = [письмо(n, назад(days=20), [диск(n)]) for n in range(1000, 1020)]
    письма += [письмо(n, назад(hours=2), [диск(n)], тип=ms.СДЕЛКА) for n in range(1020, 1025)]
    письма += [письмо(n, назад(hours=2), [диск(n)], направление=ms.ИСХОДЯЩЕЕ)
               for n in range(1025, 1030)]
    for i, n in enumerate(СВЕЖИЕ):
        создано = назад(minutes=30) if n == 1040 else назад(days=3, hours=-i)
        if i % 2:
            письма.append(письмо(n, создано, [диск(n)], тип=ms.КОНТАКТ, владелец=77))
        else:
            письма.append(письмо(n, создано, [диск(n)], владелец=55 + i,
                                 тело=ТЕЛО if n == 1034 else None))
    письма.append(письмо(1035, назад(days=3), [диск(1034)]))
    return письма


СВЕЖИЕ = list(range(1030, 1041, 2))
СВЕЖИЕ_ФАЙЛЫ = {f"mail:{диск(n)}" for n in СВЕЖИЕ}
КОНТАКТЫ = {"77": ["501"]}


def test_проход_с_записью_отдаёт_границу_и_пишет(monkeypatch, tmp_path):
    база, портал, закачка = База(), Портал(корпус(), КОНТАКТЫ), Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, портал, закачка, запись=True, верх=1040)
    assert ix.main() == 0
    assert set(база.файлы) == СВЕЖИЕ_ФАЙЛЫ | {"mail-body:1034"}
    assert all(ст == "разобран" for ст in база.файлы.values()), база.файлы
    assert база.спрос and база.цены
    assert {r[ПОТОК] for r in база.цены} == {price_store.FEED_ПИСЬМА}
    # Поставщик письма контакта — его единственная компания (MAIL_CONTACT_COMPANY),
    # письма компании — она сама.
    компания = price_store.КОЛОНКИ.index("rfq_company")
    assert {r[компания] for r in база.цены if r[ФАЙЛ] == f"mail:{диск(1032)}"} == {"501"}
    assert {r[компания] for r in база.цены if r[ФАЙЛ] == f"mail:{диск(1030)}"} == {"55"}
    assert sorted(закачка.диск) == sorted(str(диск(n)) for n in СВЕЖИЕ)
    assert env_шага(tmp_path)["INCREMENT_MAIL_TO"] == "1040"
    # Список писем — по ключу, без подсчёта (Портал это проверяет), и немного.
    assert портал.списков() <= 3


def test_повтор_окна_не_дублирует_и_не_платит_порталу(monkeypatch, tmp_path):
    """Отметку не записали (упал шаг отметки) — завтра то же окно: ни одной новой
    записи и ни одного запроса к Диску."""
    база = База()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), Закачка(),
                    запись=True, верх=1040)
    assert ix.main() == 0
    снимок = (dict(база.файлы), list(база.спрос), list(база.цены))
    повтор = Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), повтор,
                    запись=True, верх=1040)
    assert ix.main() == 0
    assert (база.файлы, база.спрос, база.цены) == снимок
    assert повтор.диск == [], "за разобранный файл портал не платит"
    assert env_шага(tmp_path)["INCREMENT_MAIL_TO"] == "1040", "граница двигается и без новых файлов"


def test_следующий_день_берёт_новое_и_письмо_у_границы(monkeypatch, tmp_path):
    """После отметки: новые письма выше границы и запоздавшее у самой границы;
    письмо ниже границы, созданное до перекрытия, не берётся, как и пришедшее
    после верха прохода."""
    база = База()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), Закачка(),
                    запись=True, верх=1040)
    monkeypatch.setenv("INCREMENT_START", str(int(СЕЙЧАС.timestamp())))
    assert ix.main() == 0
    monkeypatch.setenv("INCREMENT_MAIL_TO", env_шага(tmp_path)["INCREMENT_MAIL_TO"])
    monkeypatch.setenv("GITHUB_RUN_ID", "9001")
    assert increment.main(["increment.py", "отметка", "письма"]) == 0
    assert база.отметка(increment.ЗАМЕР["mail-supplier"])["после_id"] == 1040

    письма = корпус() + [письмо(1039, назад(hours=1), [диск(1039)]),   # лёг поздно
                         письмо(1037, назад(hours=5), [диск(1037)])]   # старше перекрытия
    письма += [письмо(n, СЕЙЧАС, [диск(n)]) for n in range(1041, 1046)]
    завтра = Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(письма, КОНТАКТЫ), завтра,
                    запись=True, верх=1044)
    assert ix.main() == 0
    assert sorted(завтра.диск) == [str(диск(n)) for n in (1039, 1041, 1042, 1043, 1044)]
    assert f"mail:{диск(1037)}" not in база.файлы, "письмо старше отметки взято"
    assert f"mail:{диск(1045)}" not in база.файлы, "письмо после верха прохода взято"
    assert env_шага(tmp_path)["INCREMENT_MAIL_TO"] == "1044"


def test_лимит_прохода_ставит_границу_на_последнем_взятом(monkeypatch, tmp_path):
    """Лимит 3 письма: граница — третье взятое, а не верх прохода; следующий
    проход продолжает с неё, и файл, уже разобранный (пересылка 1035), не качает."""
    база = База()
    отметка = {"начало": int((СЕЙЧАС - timedelta(days=5)).timestamp()), "после_id": 1029}
    база.замеры[(increment.ЗАМЕР["mail-supplier"], "вчера")] = (отметка, 1)
    закачка = Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), закачка,
                    запись=True, верх=1040)
    monkeypatch.setenv("MAIL_DAILY_LIMIT", "3")
    assert ix.main() == 0
    assert sorted(закачка.диск) == [str(диск(n)) for n in (1030, 1032, 1034)]
    assert env_шага(tmp_path)["INCREMENT_MAIL_TO"] == "1034"
    база.замеры[(increment.ЗАМЕР["mail-supplier"], "сегодня")] = (
        {"начало": int(СЕЙЧАС.timestamp()), "после_id": 1034}, 2)
    дальше = Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), дальше,
                    запись=True, верх=1040)
    monkeypatch.setenv("MAIL_DAILY_LIMIT", "3")
    assert ix.main() == 0
    assert sorted(дальше.диск) == [str(диск(n)) for n in (1036, 1038)]
    assert env_шага(tmp_path)["INCREMENT_MAIL_TO"] == "1038"


def test_холостой_замер_не_пишет_и_границы_не_отдаёт(monkeypatch, tmp_path):
    база, закачка = База(), Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), закачка,
                    запись=False, верх=1040)
    assert ix.main() == 0
    assert закачка.диск, "замер обязан разобрать файлы — иначе мерить нечего"
    assert база.файлы == {} and база.спрос == [] and база.цены == [] and база.замеры == {}
    assert "INCREMENT_MAIL_TO" not in env_шага(tmp_path)


def test_упавший_разбор_границы_не_отдаёт(monkeypatch, tmp_path):
    база = База()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ),
                    Закачка(падение=str(диск(1034))), запись=True, верх=1040)
    with pytest.raises(RuntimeError):
        ix.main()
    assert "INCREMENT_MAIL_TO" not in env_шага(tmp_path)


def test_отказ_диска_роняет_шаг_и_повтор_дочитывает(monkeypatch, tmp_path):
    """Ни одно из десятка вложений не скачалось — отметка стоит; завтра, когда
    Диск снова отдаёт файлы, повтор «не скачался» их дочитывает без дублей."""
    письма = [письмо(n, назад(hours=3), [диск(n)]) for n in range(2001, 2013)]
    база = База()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(письма), Закачка(отказ=True),
                    запись=True, верх=2012)
    assert ix.main() == 3
    assert "INCREMENT_MAIL_TO" not in env_шага(tmp_path)
    assert set(база.файлы.values()) == {"не скачался"} and база.спрос == []
    повтор = Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(письма), повтор, запись=True, верх=2012)
    assert ix.main() == 0
    assert len(повтор.диск) == 12 and set(база.файлы.values()) == {"разобран"}
    по_файлу = Counter(r[-2] for r in база.спрос)
    assert len(по_файлу) == 12 and len(set(по_файлу.values())) == 1, по_файлу
    assert env_шага(tmp_path)["INCREMENT_MAIL_TO"] == "2012"


ШАГ = (increment.ШАГ_ПИСЕМ, "5001.1")


def test_строка_шага_раньше_первой_записи_и_конец_после_последней(monkeypatch, tmp_path):
    """По строке шага откат находит запись и упавшего прохода: она обязана лечь
    ДО первой записи разбора, а конец — ПОСЛЕ последней."""
    база = База()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), Закачка(),
                    запись=True, верх=1040)
    assert ix.main() == 0
    assert база.журнал[0] == f"замер {increment.ШАГ_ПИСЕМ}", база.журнал
    assert база.журнал[-1] == f"конец {increment.ШАГ_ПИСЕМ}", база.журнал
    assert "файлы" in база.журнал
    nums = база.замеры[ШАГ][0]
    assert nums["граница"] == 1040 and nums["конец"] > 0
    assert all(isinstance(v, (int, float)) for v in nums.values()), "в nums — только числа"
    # Отметку прохода пишет не разбор, а свой шаг прогона.
    assert база.отметка(increment.ЗАМЕР["mail-supplier"]) is None


@pytest.mark.parametrize("как", ["исключение", "отказ Диска"])
def test_упавший_проход_пишет_конец_шага(monkeypatch, tmp_path, как):
    """Упавший разбор отметки не пишет, но конец строки шага — пишет: иначе его
    запись (flush пишет пакетами по ходу) было бы нечем откатить."""
    база = База()
    if как == "исключение":
        ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ),
                        Закачка(падение=str(диск(1034))), запись=True, верх=1040)
        with pytest.raises(RuntimeError):
            ix.main()
    else:
        письма = [письмо(n, назад(hours=3), [диск(n)]) for n in range(2001, 2013)]
        ix = индексатор(monkeypatch, tmp_path, база, Портал(письма), Закачка(отказ=True),
                        запись=True, верх=2012)
        assert ix.main() == 3
        assert база.файлы, "гейт срабатывает после записи — её и снимает откат"
    assert "конец" in база.замеры[ШАГ][0]
    assert "INCREMENT_MAIL_TO" not in env_шага(tmp_path)


def test_без_строки_шага_запись_не_начинается(monkeypatch, tmp_path):
    """Строка шага не легла (нет ключа прогона, база отказала) — разбор не
    начинается: запись, которую нечем откатить, хуже пропущенного дня."""
    база, закачка = База(), Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), закачка,
                    запись=True, верх=1040)
    monkeypatch.delenv("GITHUB_RUN_ID")
    monkeypatch.delenv("INCREMENT_START")
    assert ix.main() == 2
    assert закачка.диск == [] and база.файлы == {} and база.спрос == [] and база.цены == []
    assert база.замеры == {}


def test_ключ_шага_прогон_и_попытка(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "36300000001")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert increment.ключ_шага() == "36300000001.2"
    monkeypatch.delenv("GITHUB_RUN_ID")
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT")
    monkeypatch.setenv("INCREMENT_START", "1790000000")
    assert increment.ключ_шага() == "1790000000.1", "как у отметки: прогон — начало прохода"


# ─────────────────────────────────────────────── окна отката (без базы)
Т0 = datetime(2026, 9, 27, 2, 10, tzinfo=timezone.utc)


def мин(n: float) -> datetime:
    return Т0 + timedelta(minutes=n)


def отметка_(с: float, по: float) -> tuple:
    return ({"начало": мин(с).timestamp(), "после_id": 300}, мин(по))


def шаг_(ключ: str, с: float, по: float | None) -> tuple:
    return (ключ, мин(с), {"граница": 300, **({"конец": мин(по).timestamp()} if по is not None else {})})


def test_окна_отметка_и_попытки_без_промежутка():
    """Попытка 1 упала (конец есть), попытка 2 дошла до отметки: окна — по
    попыткам, промежуток между ними (там могла пройти ручная пачка) не берётся."""
    окна = increment.окна_прохода(отметка_(300, 340), [шаг_("77.1", 5, 30), шаг_("77.2", 305, 335)])
    пары = sorted((с, по) for с, по, _ in окна)
    assert пары == [(мин(5), мин(30)), (мин(300), мин(340)), (мин(305), мин(335))]
    assert not any(с < мин(200) < по for с, по in пары), "промежуток между попытками в окне"


def test_окно_упавшего_прохода_по_строке_шага():
    (с, по, откуда), = increment.окна_прохода(None, [шаг_("78.1", 0, 42)])
    assert (с, по) == (мин(0), мин(42)) and "78.1" in откуда


def test_шаг_без_конца_требует_ROLLBACK_TO():
    with pytest.raises(increment.ОтказОтката, match="ROLLBACK_TO"):
        increment.окна_прохода(None, [шаг_("79.1", 0, None)])
    (с, по, _), = increment.окна_прохода(None, [шаг_("79.1", 0, None)], мин(170))
    assert (с, по) == (мин(0), мин(170))
    for до in (мин(-1), мин(increment.ЗАДАНИЕ_МИН + 1)):
        with pytest.raises(increment.ОтказОтката):
            increment.окна_прохода(None, [шаг_("79.1", 0, None)], до)
    with pytest.raises(increment.ОтказОтката, match="попытки"):
        increment.окна_прохода(None, [шаг_("79.1", 0, None), шаг_("79.2", 60, None)], мин(90))


def test_шаг_без_конца_внутри_отметки_покрыт_ей():
    """Конец не записался (база моргнула), но отметка прогона есть — её окно
    покрывает шаг, ROLLBACK_TO не нужен."""
    окна = increment.окна_прохода(отметка_(0, 50), [шаг_("80.1", 3, None)])
    assert [(с, по) for с, по, _ in окна] == [(мин(0), мин(50))]
    with pytest.raises(increment.ОтказОтката, match="сними ROLLBACK_TO"):
        increment.окна_прохода(отметка_(0, 50), [шаг_("80.1", 3, 45)], мин(60))


def test_окно_без_начала_или_длиннее_задания_отказ():
    with pytest.raises(increment.ОтказОтката, match="1970"):
        increment.окна_прохода(({"после_id": 300}, мин(10)), [])
    with pytest.raises(increment.ОтказОтката, match="длиннее"):
        increment.окна_прохода(отметка_(0, increment.ЗАДАНИЕ_МИН + 5), [])


def test_ROLLBACK_TO_эпоха_или_ISO_с_поясом():
    assert increment.момент("1790000000") == datetime.fromtimestamp(1790000000, tz=timezone.utc)
    assert increment.момент("2026-09-27T05:40:00+03:00") == datetime(2026, 9, 27, 2, 40, tzinfo=timezone.utc)
    assert increment.момент("2026-09-27T02:40:00Z") == datetime(2026, 9, 27, 2, 40, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        increment.момент("2026-09-27T02:40:00")


def test_срезанные_LIMIT_новые_файлы_держат_границу(monkeypatch, tmp_path):
    """LIMIT срезал новые файлы окна — граница не отдаётся; без среза проход
    дочитывает остаток и отдаёт её."""
    база = База()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), Закачка(),
                    запись=True, верх=1040)
    monkeypatch.setattr(ix, "LIMIT", 2)
    assert ix.main() == 0
    assert len(база.файлы) == 2 and "INCREMENT_MAIL_TO" not in env_шага(tmp_path)
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), Закачка(),
                    запись=True, верх=1040)
    assert ix.main() == 0
    assert set(база.файлы) == СВЕЖИЕ_ФАЙЛЫ | {"mail-body:1034"}
    assert env_шага(tmp_path)["INCREMENT_MAIL_TO"] == "1040"


def test_отложенные_повторы_границу_не_держат(monkeypatch, tmp_path):
    """Повторы «не скачался» сверх предела остаются в lib_files (их повторит ручная
    пачка): держать ради них отметку — расширять окно без конца, если файл не
    скачается никогда."""
    письма = [письмо(n, назад(hours=3), [диск(n)]) for n in range(2001, 2013)]
    база = База()
    for п in письма:
        база.файлы[f"mail:{диск(int(п['ID']))}"] = "не скачался"
    закачка = Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(письма), закачка, запись=True, верх=2012)
    monkeypatch.setattr(ix, "RETRY_FAILED_LIMIT", 3)
    assert ix.main() == 0
    assert len(закачка.диск) == 3
    assert list(база.файлы.values()).count("не скачался") == 9
    assert env_шага(tmp_path)["INCREMENT_MAIL_TO"] == "2012"


def test_гейт_закачки_только_на_десятке_и_без_единой_удачи():
    pytest.importorskip("psycopg2", reason="индексатор импортирует psycopg2")
    spec = importlib.util.spec_from_file_location("indexer_gate", ROOT / "library" / "indexer.py")
    ix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ix)
    assert ix.закачка_провалена(ix.ГЕЙТ_ВЛОЖЕНИЙ, 0)
    assert not ix.закачка_провалена(ix.ГЕЙТ_ВЛОЖЕНИЙ - 1, 0), "пара осечек — не отказ Диска"
    assert not ix.закачка_провалена(100, 1)


def test_разобранное_ручной_пачкой_отсеивается(monkeypatch, tmp_path):
    """Пачка уже разобрала два свежих письма (их файлы и тело — в lib_files):
    проход их не качает и не пишет, остальные берёт."""
    база = База()
    monkeypatch.setenv("MAIL_BODIES", "1")         # пачка разбирала и тела
    for п in корпус():
        if п["ID"] in ("1030", "1034"):
            for r in ms.ссылки_письма(п, "mail-supplier"):
                база.файлы[r["file_id"]] = "разобран"
    закачка = Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), закачка,
                    запись=True, верх=1040)
    assert ix.main() == 0
    assert str(диск(1030)) not in закачка.диск and str(диск(1034)) not in закачка.диск
    assert sorted(закачка.диск) == sorted(str(диск(n)) for n in (1032, 1036, 1038, 1040))
    assert not any(r[-2] in (f"mail:{диск(1030)}", f"mail:{диск(1034)}", "mail-body:1034")
                   for r in база.спрос)
    assert env_шага(tmp_path)["INCREMENT_MAIL_TO"] == "1040"


def test_первый_проход_не_берёт_отметку_пачки(monkeypatch, tmp_path):
    """Отметка ручной пачки выше части свежих писем — проход её не читает (окно по
    дате), а записи отметок друг друга не трогают."""
    база = База()
    база.замеры[(ms.ЗАМЕР + "mail-supplier", "пачка")] = ({"от": 0, "после_id": 1036}, 1)
    закачка = Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), закачка,
                    запись=True, верх=1040)
    assert ix.main() == 0
    assert str(диск(1030)) in закачка.диск, "письмо ниже отметки пачки, но в окне дней, не взято"
    assert str(диск(1000)) not in закачка.диск, "письмо старше окна первого прохода взято"
    monkeypatch.setenv("INCREMENT_START", str(int(СЕЙЧАС.timestamp())))
    monkeypatch.setenv("INCREMENT_MAIL_TO", "1040")
    assert increment.main(["increment.py", "отметка", "письма"]) == 0
    assert база.отметка(ms.ЗАМЕР + "mail-supplier") == {"от": 0, "после_id": 1036}
    assert база.отметка(increment.ЗАМЕР["mail-supplier"])["после_id"] == 1040
    with база.cursor() as cur:
        assert ms.прочитать_отметку(cur, "mail-supplier") == 1036
        assert increment.прочитать(cur, "mail-supplier")["после_id"] == 1040


# ─────────────────────────────────────────────── increment: режим и отметка
def test_режим_писем(monkeypatch):
    monkeypatch.delenv("MAIL_DAILY", raising=False)
    assert increment.режим_писем() == "off", "не задан — прежнее поведение"
    for р in ("apply", "dry", "off"):
        monkeypatch.setenv("MAIL_DAILY", р)
        assert increment.режим_писем() == р
    monkeypatch.setenv("MAIL_DAILY", "yes")
    with pytest.raises(ValueError):
        increment.режим_писем()
    # Там, где работают письма, опечатка — отказ; в общем шаге — «письма не читаются».
    assert increment.main(["increment.py", "отметка", "письма"]) == 2
    assert increment.режим_писем_в_общем_шаге() == "off"


def test_неверный_режим_писем_не_держит_сделки(monkeypatch, tmp_path):
    """Опечатка в режиме писем: «начало» и отметка сделок идут, верх писем не
    читается и не пишется — шаг писем откажет сам."""
    портал = Портал([письмо(n, СЕЙЧАС, [n]) for n in (3, 8)])

    def bx(метод, п):
        return {"result": [{"ID": "4100"}]} if метод == "crm.deal.list" else портал(метод, п)
    база = База()
    _подставной_индексатор(monkeypatch, bx, база)
    monkeypatch.setenv("MAIL_DAILY", "yes")
    файл = tmp_path / "env"
    файл.touch()
    monkeypatch.setenv("GITHUB_ENV", str(файл))
    assert increment.main(["increment.py", "начало"]) == 0
    записано = dict(с.split("=", 1) for с in файл.read_text(encoding="utf-8").splitlines())
    assert записано["INCREMENT_MAX_DEALS"] == "4100" and "INCREMENT_MAX_MAIL" not in записано
    assert портал.вызовы == []
    for к, v in записано.items():
        monkeypatch.setenv(к, v)
    assert increment.main(["increment.py", "отметка"]) == 0
    assert база.отметка(increment.ЗАМЕР["deals"])["после_id"] == 4100


@pytest.mark.parametrize("имя, значение", [("MAIL_FIRST_DAYS", "0"), ("MAIL_FIRST_DAYS", "неделя"),
                                           ("MAIL_DAILY_LIMIT", "-5"), ("MAIL_DAILY", "yes")])
def test_входы_писем_проверяет_шаг_писем_а_не_начало(monkeypatch, tmp_path, имя, значение):
    """Неверный вход писем роняет только шаг писем — до портала и Диска; общие
    шаги «начало» и «окно» идут, и сделки с карточками разбираются."""
    портал = Портал([письмо(n, СЕЙЧАС, [n]) for n in (3, 8, 13)])

    def bx(метод, п):
        return {"result": [{"ID": "4100"}]} if метод == "crm.deal.list" else портал(метод, п)
    _подставной_индексатор(monkeypatch, bx)
    monkeypatch.setenv("MAIL_DAILY", "dry")
    monkeypatch.setenv(имя, значение)
    файл = tmp_path / "env"
    файл.touch()
    monkeypatch.setenv("GITHUB_ENV", str(файл))
    assert increment.main(["increment.py", "начало"]) == 0
    assert "INCREMENT_MAX_DEALS=4100" in файл.read_text(encoding="utf-8")
    assert increment.main(["increment.py", "окно"]) == 0

    база, закачка = База(), Закачка()
    портал_шага = Портал(корпус(), КОНТАКТЫ)
    ix = индексатор(monkeypatch, tmp_path, база, портал_шага, закачка, запись=True, верх=1040)
    monkeypatch.setenv(имя, значение)
    assert ix.main() == 2
    assert портал_шага.вызовы == [] and закачка.диск == [] and база.файлы == {}
    assert база.замеры == {}, "строка шага без разбора"


def test_первое_окно_писем_своё(monkeypatch):
    monkeypatch.delenv("MAIL_FIRST_DAYS", raising=False)
    assert increment.первый_дней_для("mail-supplier") == increment.ПЕРВЫЙ_ДНЕЙ_ПИСЕМ == 7
    assert increment.первый_дней_для("deals") == increment.ПЕРВЫЙ_ДНЕЙ
    monkeypatch.setenv("MAIL_FIRST_DAYS", "3")
    с, после = increment.окно(None, СЕЙЧАС, первый_дней=increment.первый_дней_для("mail-supplier"))
    assert после == 0 and с == СЕЙЧАС - timedelta(days=3)


def _подставной_индексатор(monkeypatch, портал, база=None):
    ns = types.SimpleNamespace(
        bx=портал, bx_max_id=lambda метод, п: 700, SPA_RFQ=166,
        connect=lambda *a, **k: база or База())
    monkeypatch.setitem(sys.modules, "indexer", ns)
    return ns


@pytest.mark.parametrize("режим", ["off", "dry", "apply"])
def test_начало_читает_верх_писем_только_с_письмами(monkeypatch, tmp_path, режим):
    портал = Портал([письмо(n, СЕЙЧАС, [n]) for n in (3, 8, 13)])

    def bx(метод, п):
        if метод == "crm.deal.list":
            return {"result": [{"ID": "4100"}]}
        return портал(метод, п)
    _подставной_индексатор(monkeypatch, bx)
    monkeypatch.setenv("MAIL_DAILY", режим)
    файл = tmp_path / "env"
    файл.touch()
    monkeypatch.setenv("GITHUB_ENV", str(файл))
    assert increment.main(["increment.py", "начало"]) == 0
    записано = dict(с.split("=", 1) for с in файл.read_text(encoding="utf-8").splitlines())
    if режим == "off":
        assert "INCREMENT_MAX_MAIL" not in записано and портал.вызовы == []
    else:
        assert записано["INCREMENT_MAX_MAIL"] == "13" and len(портал.вызовы) == 1
    assert записано["INCREMENT_MAX_DEALS"] == "4100"


@pytest.mark.parametrize("сбой", ["пусто", "портал"])
def test_сбой_верха_писем_не_держит_сделки(monkeypatch, tmp_path, сбой):
    """Шаг «начало» общий: пустая группа или отказ портала на письмах (429/503
    после бюджета ожидания, сеть) — предупреждение, а не отказ. Верх писем не
    пишется, и шаг писем сам выходит с отказом «нет верха»."""
    def bx(метод, п):
        if метод == "crm.deal.list":
            return {"result": [{"ID": "4100"}]}
        if сбой == "портал":
            raise RuntimeError("503 после бюджета ожидания")
        return {"result": []}
    _подставной_индексатор(monkeypatch, bx)
    monkeypatch.setenv("MAIL_DAILY", "apply")
    файл = tmp_path / "env"
    файл.touch()
    monkeypatch.setenv("GITHUB_ENV", str(файл))
    assert increment.main(["increment.py", "начало"]) == 0
    записано = dict(с.split("=", 1) for с in файл.read_text(encoding="utf-8").splitlines())
    assert записано["INCREMENT_MAX_DEALS"] == "4100" and записано["INCREMENT_MAX_RFQ"] == "700"
    assert "INCREMENT_MAX_MAIL" not in записано, "ноль значит «все письма»"

    база, закачка = База(), Закачка()
    ix = индексатор(monkeypatch, tmp_path, база, Портал(корпус(), КОНТАКТЫ), закачка,
                    запись=True, верх=1040)
    monkeypatch.delenv("INCREMENT_MAX_MAIL")
    assert ix.main() == 2
    assert закачка.диск == [] and база.файлы == {} and база.замеры == {}


def test_начало_без_сделок_отказ(monkeypatch, tmp_path):
    """Нулевой верх СДЕЛОК по-прежнему отказ: ноль значит «все записи»."""
    _подставной_индексатор(monkeypatch, lambda м, п: {"result": []})
    monkeypatch.setenv("MAIL_DAILY", "off")
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    assert increment.main(["increment.py", "начало"]) == 2


@pytest.mark.parametrize("режим, граница, код, пишет", [
    ("dry", "900", 0, False),        # замер отметку не двигает никогда
    ("off", "900", 0, False),
    ("apply", None, 0, False),       # разбор не дошёл до конца с записью
    ("apply", "0", 2, False),        # ноль значил бы «все письма»
    ("apply", "900", 0, True),
])
def test_отметка_писем_только_после_разбора_с_записью(monkeypatch, режим, граница, код, пишет):
    база = База()
    _подставной_индексатор(monkeypatch, None, база)
    monkeypatch.setenv("MAIL_DAILY", режим)
    monkeypatch.setenv("INCREMENT_START", "1790000000")
    monkeypatch.setenv("GITHUB_RUN_ID", "4242")
    if граница is None:
        monkeypatch.delenv("INCREMENT_MAIL_TO", raising=False)
    else:
        monkeypatch.setenv("INCREMENT_MAIL_TO", граница)
    assert increment.main(["increment.py", "отметка", "письма"]) == код
    метка = база.отметка(increment.ЗАМЕР["mail-supplier"])
    assert (метка is not None) == пишет
    if пишет:
        assert метка == {"начало": 1790000000, "после_id": 900}
    # Отметку сделок и карточек этот шаг не пишет никогда.
    assert база.отметка(increment.ЗАМЕР["deals"]) is None


def test_отметка_сделок_не_пишет_писем(monkeypatch):
    база = База()
    _подставной_индексатор(monkeypatch, None, база)
    monkeypatch.setenv("MAIL_DAILY", "apply")
    for к, v in {"INCREMENT_START": "1790000000", "INCREMENT_MAX_DEALS": "10",
                 "INCREMENT_MAX_RFQ": "20", "INCREMENT_MAIL_TO": "900"}.items():
        monkeypatch.setenv(к, v)
    assert increment.main(["increment.py", "отметка"]) == 0
    assert база.отметка(increment.ЗАМЕР["deals"])["после_id"] == 10
    assert база.отметка(increment.ЗАМЕР["mail-supplier"]) is None


def test_имена_отметок_прохода_и_пачки_различны():
    for группа in ms.ГРУППЫ:
        assert increment.ЗАМЕР["mail-supplier"] != ms.ЗАМЕР + группа
    assert len(set(increment.ЗАМЕР.values())) == len(increment.ЗАМЕР)


# ─────────────────────────────────────────────── прогон
def прогон() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def шаги() -> list[dict]:
    return прогон()["jobs"]["daily"]["steps"]


def номер(условие) -> int:
    найдено = [i for i, ш in enumerate(шаги()) if условие(ш)]
    assert len(найдено) == 1, найдено
    return найдено[0]


def шаг_писем() -> dict:
    return шаги()[номер(lambda ш: (ш.get("env") or {}).get("SOURCE") == "mail")]


def test_шаг_писем_после_отметки_сделок_и_своя_отметка_последней():
    ш = шаги()
    сделки = номер(lambda x: (x.get("env") or {}).get("SOURCE") == "deals"
                   and "indexer.py" in x.get("run", ""))
    распознавание = [i for i, x in enumerate(ш) if "ocr.py" in x.get("run", "")]
    отметка = номер(lambda x: x.get("run", "").strip() == "python library/increment.py отметка")
    письма = номер(lambda x: (x.get("env") or {}).get("SOURCE") == "mail")
    своя = номер(lambda x: "increment.py отметка письма" in x.get("run", ""))
    assert сделки < отметка and max(распознавание) < отметка
    assert отметка < письма < своя == len(ш) - 1
    for i in (отметка, своя):
        assert "if" not in ш[i], "отметка упавшего прохода сдвинула бы окно"
    assert "off" in ш[письма]["if"] and "MAIL_DAILY" in ш[письма]["if"]


def test_шаг_писем_как_ручная_пачка_с_ценами_и_компанией():
    env = шаг_писем()["env"]
    assert env["MAIL_GROUP"] == "mail-supplier"
    for к in ("MAIL_PRICES", "MAIL_CONTACT_COMPANY", "MAIL_BODIES", "MAIL_FILES"):
        assert env[к] == "1", к
    assert env["BITRIX_FILES_WEBHOOK_URL"].replace(" ", "") == "${{secrets.BITRIX_FILES_WEBHOOK_URL}}"
    assert "APPLY" not in env, "запись решает режим, а не прибитый вход"


@pytest.mark.parametrize("режим, запись", [("apply", "1"), ("dry", ""), ("off", "")])
def test_запись_только_в_режиме_apply(режим, запись):
    скрипт = шаг_писем()["run"].replace("python library/indexer.py", 'echo "APPLY=[$APPLY]"')
    out = subprocess.run(["bash", "-c", скрипт], capture_output=True, text=True, check=True,
                         env={"PATH": "/usr/bin:/bin", "MAIL_DAILY": режим,
                              "BITRIX_FILES_WEBHOOK_URL": "x"}).stdout
    assert f"APPLY=[{запись}]" in out


def test_ночью_письма_выключены_руками_замер():
    """У repository_dispatch входов нет: ночью действует умолчание в env задания.

    Ночью — off, а не dry: замер отметку не пишет, и каждая ночь была бы первым
    проходом заново — до ЛИМИТ_ПИСЕМ писем за ПЕРВЫЙ_ДНЕЙ_ПИСЕМ дней с закачкой
    всех вложений, тысячи запросов к порталу за ночь (ревизия 27.09.2026). Руками
    по умолчанию — замер. Окно и лимит — одни у входа и у ночи."""
    wf = прогон()
    входы = (wf.get("on") or wf[True])["workflow_dispatch"]["inputs"]
    env = wf["jobs"]["daily"]["env"]
    ночь = {}
    for вход, переменная in (("mail", "MAIL_DAILY"), ("mail_days", "MAIL_FIRST_DAYS"),
                             ("mail_limit", "MAIL_DAILY_LIMIT")):
        м = re.fullmatch(r"\$\{\{\s*inputs\.(\w+)\s*\|\|\s*'([^']*)'\s*\}\}", env[переменная].strip())
        assert м and м.group(1) == вход, env[переменная]
        ночь[вход] = м.group(2)
    assert ночь["mail"] in ("off", "apply"), "ночной dry повторял бы первый проход каждую ночь"
    assert ночь["mail"] == "off", "запись ночью включается после первой отметки, не в этой правке"
    assert входы["mail"]["default"] == "dry", "ручной запуск по умолчанию — замер"
    assert set(входы["mail"]["options"]) == set(increment.РЕЖИМЫ)
    for вход, код in (("mail_days", increment.ПЕРВЫЙ_ДНЕЙ_ПИСЕМ), ("mail_limit", increment.ЛИМИТ_ПИСЕМ)):
        assert ночь[вход] == str(входы[вход]["default"]) == str(код), вход


def test_предел_окна_отката_не_меньше_задания():
    """Окно прохода длиннее задания — не окно одного задания (increment.ЗАДАНИЕ_МИН)."""
    assert прогон()["jobs"]["daily"]["timeout-minutes"] == increment.ЗАДАНИЕ_МИН


def test_расписание_и_бюджет_не_тронуты():
    wf = прогон()
    triggers = wf.get("on") or wf[True]
    assert "schedule" not in triggers
    assert triggers["repository_dispatch"]["types"] == ["library-daily"]
    assert wf["concurrency"]["group"] == "bitrix-portal"
    env = wf["jobs"]["daily"]["env"]
    assert env["BITRIX_RPS"] == "1.0" and env["BITRIX_PARALLEL"] == "1"
    assert "BITRIX_RPS" not in шаг_писем()["env"], "письма идут в бюджете прохода"
