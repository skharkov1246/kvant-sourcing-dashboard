"""Ежедневный проход писем поставщиков (library/increment.py, mail_source.письма_прохода).

Что закреплено:
  • письмо с номером не выше отметки не берётся — кроме страницы перекрытия у
    самой границы, созданной после начала прошлого прохода; письмо выше верха
    прохода (пришло во время разбора) — тоже нет: его возьмёт следующий;
  • первый проход — не глубже N дней и не по отметке ручной пачки; наплыв
    сверх лимита переносится на завтра от границы без потерь;
  • отметка писем сдвигается только после разбора с записью, дошедшего до
    конца: холостой замер, падение разбора, отказ Диска — отметка стоит;
  • повтор того же окна не дублирует ни файлов, ни строк спроса, ни цен и не
    платит порталу за разобранное; письмо, разобранное ручной пачкой,
    отсеивается по lib_files; отметки пачки и прохода друг друга не трогают;
  • прогон: шаг писем после отметки сделок и карточек, своя отметка последней,
    умолчание режима — замер, расписание не тронуто.

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
    assert increment.main(["increment.py", "окно"]) == 2


@pytest.mark.parametrize("имя, значение", [("MAIL_FIRST_DAYS", "0"), ("MAIL_FIRST_DAYS", "неделя"),
                                           ("MAIL_DAILY_LIMIT", "-5")])
def test_входы_писем_проверяются(monkeypatch, имя, значение):
    monkeypatch.setenv("MAIL_DAILY", "dry")
    monkeypatch.setenv(имя, значение)
    assert increment.main(["increment.py", "начало"]) == 2


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


def test_начало_без_писем_в_группе_отказ(monkeypatch, tmp_path):
    def bx(метод, п):
        return {"result": [{"ID": "4100"}]} if метод == "crm.deal.list" else {"result": []}
    _подставной_индексатор(monkeypatch, bx)
    monkeypatch.setenv("MAIL_DAILY", "apply")
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


def test_умолчания_режима_одни_у_входа_и_у_dispatch():
    """У repository_dispatch входов нет: действует умолчание в env задания, и оно
    обязано совпадать с умолчанием входа — иначе ручной и ночной прогон разойдутся."""
    wf = прогон()
    входы = (wf.get("on") or wf[True])["workflow_dispatch"]["inputs"]
    env = wf["jobs"]["daily"]["env"]
    for вход, переменная, код in (("mail", "MAIL_DAILY", None),
                                  ("mail_days", "MAIL_FIRST_DAYS", increment.ПЕРВЫЙ_ДНЕЙ_ПИСЕМ),
                                  ("mail_limit", "MAIL_DAILY_LIMIT", increment.ЛИМИТ_ПИСЕМ)):
        м = re.fullmatch(r"\$\{\{\s*inputs\.(\w+)\s*\|\|\s*'([^']*)'\s*\}\}", env[переменная].strip())
        assert м and м.group(1) == вход, env[переменная]
        assert м.group(2) == str(входы[вход]["default"])
        if код is not None:
            assert int(м.group(2)) == код
    assert входы["mail"]["default"] == "dry", "запись включается после разбора замера"
    assert set(входы["mail"]["options"]) == set(increment.РЕЖИМЫ)


def test_расписание_и_бюджет_не_тронуты():
    wf = прогон()
    triggers = wf.get("on") or wf[True]
    assert "schedule" not in triggers
    assert triggers["repository_dispatch"]["types"] == ["library-daily"]
    assert wf["concurrency"]["group"] == "bitrix-portal"
    env = wf["jobs"]["daily"]["env"]
    assert env["BITRIX_RPS"] == "1.0" and env["BITRIX_PARALLEL"] == "1"
    assert "BITRIX_RPS" not in шаг_писем()["env"], "письма идут в бюджете прохода"
