"""Чтение файлов «Экономика проекта» (scripts/econ_files.py) — без сети и без базы.

  · ссылка clck.ru → страница проверки ссылок → путь общего диска → путь API Диска;
  · очередь: «Сумма» менялась после сверенного чтения — первыми; затем ни разу не читанные
    от новых сделок, затем давние чтения от давних;
  · предел суток — из базы: каждая попытка, удачная или нет; пауза между файлами;
  · строка: ключ прогона, sha256 пути вместо пути, сверка с «Суммой» сделки без НДС;
  · несверенный файл чисел не несёт; сбой одной сделки прогон не роняет (ревизия 25.10);
  · гейты: «шаблон другой» у большинства — стоп; суммы далеко — прогон помечает себя;
    прошёл гейт — прогон записан в econ_file_runs (без этого его строк во вкладке нет);
  · «≈ с НДС» — отдельным счётом, не расхождение, и только при пустом налоге сделки;
    «Сумма» — «Наше КП» до переторжки — тоже отдельно;
  · база не выдаёт попытку суток — стоп без отметки гейта;
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

ПРОДАЖА_EUR = 2 * 1400 + 5 * 320 + 4500                # продажа придуманной книги ПОЗИЦИИ, € без НДС

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
        assert "TAX_VALUE" in select, "сверка с файлом — без НДС"
        return [dict(d) for d in self.сделки]


class База:
    """Подпорка закрытой базы с тем же предлом суток, что у econ_file_take_slot."""

    def __init__(self, взято: int = 0, прочитано: dict | None = None, сверка: dict | None = None,
                 последние: dict | None = None):
        self.взято = взято
        self._прочитано = прочитано or {}
        self._сверка = сверка or {}
        self._последние = последние or {}
        self.строки: list[dict] = []
        self.попытки: list[int] = []
        self.помечено: set[str] = set()
        self.прошли: list[str] = []

    def прочитано(self):
        return dict(self._прочитано)

    def сверка_на_чтение(self):
        return dict(self._сверка)

    def последние(self):
        return dict(self._последние)

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

    def отметить_прогон(self, run_id):
        assert run_id not in self.помечено, "помеченный прогон гейт не прошёл"
        self.прошли.append(run_id)
        свои = [r for r in self.строки if r["run_id"] == run_id]
        return len(свои), sum(1 for r in свои if r["status"] == et.СВЕРЕНО)

    def итог(self, run_id):
        return Counter(r["status"] for r in self.строки if r["run_id"] == run_id and run_id not in self.помечено)


def _сделка(i: int, *, ссылка: str | None = "", дней: int = 0, сумма="20000", валюта="EUR", стадия="C0:EXECUTING",
            налог="0"):
    return {"ID": str(8000 + i), "DATE_CREATE": (СЕЙЧАС - dt.timedelta(days=дней)).isoformat(),
            "STAGE_ID": стадия, "STAGE_SEMANTIC_ID": "P", "CATEGORY_ID": "0", "OPPORTUNITY": сумма,
            "TAX_VALUE": налог, "CURRENCY_ID": валюта,
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


def _сверка_чтения(сумма_файла: float, ratio, note=None, at=None) -> dict:
    """Сверенное чтение для очереди: файл в рублях, 100 ₽ за €."""
    return {"at": at or СЕЙЧАС - dt.timedelta(days=3), "sale": сумма_файла, "cur": "RUB",
            "saleEur": сумма_файла / 100, "fx": {"RUB": 1.0, "EUR": 100.0}, "ratio": ratio, "note": note}


def test_очередь_сумма_менялась_после_чтения_первой():
    """Вторая ревизия, 5: «Сумму» внесли или поменяли после чтения — сделка не ждёт
    ПОВТОР_ДНЕЙ, а перечитывается первой (сверка — по полям сделки, уже прочитанным)."""
    прочли = СЕЙЧАС - dt.timedelta(days=3)
    канд = [_сделка(i, дней=10 * i, сумма="30000", валюта="EUR") for i in range(5)]
    прочитано = {8001: прочли, 8002: прочли, 8003: прочли, 8004: прочли + dt.timedelta(days=1)}
    сверка = {
        8001: _сверка_чтения(3_000_000.0, 1.0),                    # та же «Сумма» — свежая
        8002: _сверка_чтения(3_000_000.0, None, "суммы нет"),      # суммы не было — внесли
        8003: _сверка_чтения(3_000_000.0, 0.5),                    # «Сумму» поменяли
        # поменяли, но после сверенного чтения уже была попытка (сбой) — в общий черёд
        8004: _сверка_чтения(3_000_000.0, 0.5, at=прочли),
    }
    счёт = Counter()
    q = ef.очередь(канд, прочитано, СЕЙЧАС, счёт, сверка)
    assert [d["ID"] for d in q] == ["8002", "8003", "8000"]
    assert (счёт["сумма менялась"], счёт["ни разу"], счёт["свежих"]) == (2, 1, 2)
    # налог в «Сумме» вычитается так же, как у сборки: та же нетто-сумма — не менялась
    канд[1].update(OPPORTUNITY="36600", TAX_VALUE="6600")
    сверка[8002] = _сверка_чтения(3_000_000.0, 1.0)
    assert [d["ID"] for d in ef.очередь(канд, прочитано, СЕЙЧАС, Counter(), сверка)] == ["8003", "8000"]
    # прогон берёт сверку из базы и пишет счёт в журнал
    сделки, диск = _мир(2)
    # «Сумма» 20 000 € против 890 000 ₽ по 100 ₽ за € — как на день чтения у 8000, не у 8001
    база = База(прочитано={8000: прочли, 8001: прочли},
                сверка={8000: _сверка_чтения(890_000.0, round(2_000_000 / 890_000, 6)),
                        8001: _сверка_чтения(890_000.0, 0.5)})
    код, журнал, _ = _прогон(сделки, диск, база)
    assert код == 0 and база.попытки == [8001]
    assert any("«Сумма» менялась после чтения" in s for s in журнал if s.startswith("очередь:"))
    _журнал_чист(журнал, сделки)


def test_кандидаты_открытые_со_ссылкой():
    сделки = [_сделка(0), _сделка(1, ссылка=None), _сделка(2, стадия="C0:PAID"), _сделка(3, стадия="C0:WON"),
              dict(_сделка(4), CATEGORY_ID="7"), _сделка(5, ссылка="не ссылка")]
    счёт = Counter()
    канд = ef.кандидаты(сделки, Портал([]).deal_stage_meta(), счёт)
    assert [d["ID"] for d in канд] == ["8000"]
    assert счёт["открытых"] == 3 and счёт["закрыта"] == 2 and счёт["без ссылки"] == 2
    assert счёт["ссылка clck.ru"] == 1


def test_вид_ссылки_без_домена():
    """Ревизия: ссылка на портал клиента в журнале — его имя; печатается вид, не домен."""
    assert [ef.вид_ссылки(x) for x in ("https://clck.ru/ВЫДУМКА", _ссылка_диска(ПУТЬ), "https://disk.yandex.com/d/x",
                                        "https://portal.client-vydumka.example/doc", "https://[битая", "")] == \
        ["clck.ru", "disk.yandex", "disk.yandex", "другой", "другой", "другой"]
    сделки, диск = _мир(2)
    сделки.append(_сделка(5, ссылка="https://portal.client-vydumka.example/doc/1.xlsx"))
    код, журнал, _ = _прогон(сделки, диск, База())
    assert код == 0 and any("вид ссылки: clck.ru 2 · другой 1" in s for s in журнал)
    assert "client-vydumka" not in "\n".join(журнал)
    _журнал_чист(журнал, сделки)


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
    # суммы нет — сверять не с чем, а не «расходится на 100 %»
    assert база.строки[1]["bitrix_ratio"] is None and база.строки[1]["bitrix_note"] == "суммы нет"
    текст = json.dumps(база.строки, ensure_ascii=False)
    assert "Сделка 0" not in текст and МЕТКА not in текст, "пути и метки диска в базе нет"
    # пауза между файлами — не меньше двух секунд, первый — без паузы
    assert len(сон) == 2 and all(с >= ef.ПАУЗА_С for с in сон)
    _журнал_чист(журнал, сделки)
    assert any(s.startswith("файлы: сверено 3") for s in журнал)
    assert any("суммы нет 1" in s for s in журнал if s.startswith("сверенные против"))
    # гейт пройден — решение в базе: строки прогона видит вкладка
    assert база.прошли == ["ef-test-1"] and any(s.startswith("гейт пройден") for s in журнал)
    assert any("вид ссылки: clck.ru 3" in s for s in журнал)


def _журнал_чист(журнал: list[str], сделки: list[dict]) -> None:
    текст = "\n".join(журнал)
    for d in сделки:
        assert d["ID"] not in текст, "номера сделок в журнал не идут"
    for слово in ("Сделка ", МЕТКА, "clck.ru/", "sba.yandex", "disk.yandex.ru/", "6000", "наценк", "OAuth"):
        assert слово not in текст, слово
    # вид ссылки («disk.yandex») — можно, а ссылку целиком на любом домене — нет
    assert "://" not in текст, "ссылок в журнале нет"
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


def test_несверенный_файл_чисел_не_несёт():
    """Ревизия, дефект 3: «скидка» 15 вместо 15 % — продажа отрицательна; в строке ни продажи,
    ни закупки, ни наценки (иначе ограничение базы роняло прогон и обходило гейт сумм)."""
    тело = книга([dict(p, AK=15) for p in ПОЗИЦИИ], переторжка=True)
    сделки, диск = _мир(3, тело=тело)
    база = База()
    код, журнал, _ = _прогон(сделки, диск, база)
    assert код == 0 and [r["status"] for r in база.строки] == [et.НЕПРАВДОПОДОБНО] * 3
    for r in база.строки:
        assert (r["sale_sub"], r["sale_eur"], r["purchase_eur"], r["markup"], r["fx"], r["bitrix_ratio"]) == \
            (None,) * 6
        assert r["why"] == "проверка 2" and r["totals_dev"] is not None, "чем проверено — остаётся (правило 16)"


class ОтказБазы(Exception):
    """Подпорка ошибки базы (CheckViolation и т. п.)."""


def test_сбой_одной_сделки_не_роняет_прогон_и_не_держит_очередь(monkeypatch):
    сделки, диск = _мир(4)

    class Капризная(База):
        def записать(self, r):
            if r["deal_id"] == 8001 and r["status"] != ef.СБОЙ:
                raise ОтказБазы("значения строки — в журнал не идут")
            super().записать(r)
    разобрать = et.разобрать
    вызовы = [0]

    def ломается_на_третьем(b):
        вызовы[0] += 1
        if вызовы[0] == 3:
            raise ZeroDivisionError("разбор")
        return разобрать(b)
    monkeypatch.setattr(ef.et, "разобрать", ломается_на_третьем)
    база = Капризная()
    код, журнал, _ = _прогон(сделки, диск, база)
    assert код == 0 and len(база.строки) == 4 and база.прошли == ["ef-test-1"]
    assert [r["status"] for r in база.строки] == [et.СВЕРЕНО, ef.СБОЙ, ef.СБОЙ, et.СВЕРЕНО]
    сбой = база.строки[1]
    assert сбой["file_key"] and сбой["sale_eur"] is None and сбой["bitrix_ratio"] is None
    предупреждение = next(s for s in журнал if s.startswith("::warning::сбои чтения"))
    assert "запись: ОтказБазы 1" in предупреждение and "чтение: ZeroDivisionError 1" in предупреждение
    assert "значения строки" not in "\n".join(журнал), "текст ошибки в журнал не идёт"
    _журнал_чист(журнал, сделки)
    # сбой — тоже чтение: в следующую ночь сделка не первая
    q = ef.очередь(ef.кандидаты(сделки, Портал([]).deal_stage_meta(), Counter()),
                   {r["deal_id"]: СЕЙЧАС for r in база.строки}, СЕЙЧАС + dt.timedelta(days=1))
    assert q == []


def test_база_не_принимает_и_сбой_стоп_без_отметки_гейта():
    сделки, диск = _мир(3)

    class Мёртвая(База):
        def записать(self, r):
            raise ОтказБазы("нет связи")
    база = Мёртвая()
    код, журнал, _ = _прогон(сделки, диск, база)
    assert код == 1 and база.прошли == [] and база.попытки == [8000]
    assert any(s.startswith("::error::база отказала") for s in журнал)


def test_база_не_выдаёт_попытку_стоп_без_отметки_гейта():
    """Ревизия sql, 1 (необязательное): отказ базы в econ_file_take_slot — не падение
    прогона с трассой, а стоп «база»: гейт не судит, прогона в econ_file_runs нет."""
    сделки, диск = _мир(3)

    class Обрыв(База):
        def взять(self, run_id, deal_id, предел):
            if deal_id == 8001:
                raise ОтказБазы("связь оборвалась")
            return super().взять(run_id, deal_id, предел)
    база = Обрыв()
    код, журнал, _ = _прогон(сделки, диск, база)
    assert код == 1 and база.прошли == [] and [r["deal_id"] for r in база.строки] == [8000]
    assert any(s.startswith("::error::база отказала") for s in журнал)
    assert any("попытка суток: ОтказБазы 1" in s for s in журнал if s.startswith("::warning::"))
    assert "связь оборвалась" not in "\n".join(журнал)
    _журнал_чист(журнал, сделки)


def test_сумма_без_ндс_и_счёт_похожих_на_ндс():
    """«Сумма» сделки — с НДС: TAX_VALUE вычитается; налога нет, а отношение ×1,22 или ×1,20
    — «≈ с НДС», отдельным счётом, не расхождение (ревизия, дефект 4)."""
    с_ндс = round(ПРОДАЖА_EUR * 1.22)
    сделки, диск = _мир(6, сумма=str(с_ндс))
    сделки[0]["TAX_VALUE"] = str(с_ндс - ПРОДАЖА_EUR)                 # НДС указан — сходится
    сделки[1]["OPPORTUNITY"] = str(round(ПРОДАЖА_EUR * 1.2))           # 20 %
    сделки[2]["OPPORTUNITY"] = str(round(ПРОДАЖА_EUR * 1.4))           # не НДС — расхождение
    база = База()
    код, журнал, _ = _прогон(сделки, диск, база)
    assert код == 0
    отн = [r["bitrix_ratio"] for r in база.строки]
    assert отн[0] == pytest.approx(1.0) and отн[1] == pytest.approx(1.2, abs=1e-4)
    assert отн[2] == pytest.approx(1.4, abs=1e-4) and отн[3] == pytest.approx(1.22, abs=1e-4)
    сверка = next(s for s in журнал if s.startswith("сверенные против"))
    assert f"{ef.НДС_СЧЁТ} 4" in сверка and "расходится больше 10% 1" in сверка and "сходится (±10%) 1" in сверка
    assert ef.сверка_с_суммой(база.строки)[ef.НДС_СЧЁТ] == 4
    assert "tax_empty" not in ef.База.КОЛОНКИ, "признак — для журнала, не колонка базы"
    _журнал_чист(журнал, сделки)


def test_налог_указан_тогда_120_расхождение_в_журнале():
    """Вторая ревизия, 2а: НДС указан и вычтен, а нетто на 20 % выше продажи файла — это
    расхождение, а не «≈ с НДС»."""
    сделки, диск = _мир(2, сумма=str(round(ПРОДАЖА_EUR * 1.44)))
    for d in сделки:
        d["TAX_VALUE"] = str(round(ПРОДАЖА_EUR * 0.24))                 # нетто ×1,20
    база = База()
    код, журнал, _ = _прогон(сделки, диск, база)
    assert код == 0 and [r["bitrix_ratio"] for r in база.строки] == [pytest.approx(1.2, abs=1e-4)] * 2
    сверка = next(s for s in журнал if s.startswith("сверенные против"))
    assert "расходится больше 10% 2" in сверка and ef.НДС_СЧЁТ not in сверка


def test_сумма_до_переторжки_отдельным_счётом():
    """Вторая ревизия, 2б: файл с переторжкой — в строке и продажа «Нашего КП»; «Сумма»,
    оставленная по ней, — «до переторжки», а не «≈ с НДС»."""
    переторжка = [dict(p, AK=0.17) for p in ПОЗИЦИИ]
    кп = et.разобрать(книга(ПОЗИЦИИ))
    сделки, диск = _мир(2, тело=книга(переторжка, переторжка=True), сумма=str(round(кп.sale_sub)), валюта="RUB")
    база = База()
    код, журнал, _ = _прогон(сделки, диск, база)
    r = база.строки[0]
    assert код == 0 and r["rebid"] is True and r["kp_sale_sub"] == кп.sale_sub and r["sale_sub"] < кп.sale_sub
    сверка = next(s for s in журнал if s.startswith("сверенные против"))
    assert f"{ef.ДО_ПЕРЕТОРЖКИ_СЧЁТ} 2" in сверка and ef.НДС_СЧЁТ not in сверка
    _журнал_чист(журнал, сделки)
    # без переторжки продажи КП в строке нет — она и есть продажа
    сделки, диск = _мир(1)
    база = База()
    _прогон(сделки, диск, база)
    assert база.строки[0]["kp_sale_sub"] is None and "kp_sale_sub" in ef.База.КОЛОНКИ


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
    assert база.прошли == [], "гейт не пройден — прогона в econ_file_runs нет"
    assert any(s.startswith("::error::гейт сумм") for s in журнал)
    assert any("сверено 0" in s for s in журнал if s.startswith("в базе по ключу прогона"))
    # сделки без суммы — не довод против разбора
    сделки, диск = _мир(6, сумма="0")
    база = База()
    код, _журнал, _ = _прогон(сделки, диск, база)
    assert код == 0 and not база.помечено and база.прошли == ["ef-test-1"]


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
    assert set(re.findall(r"'([^']+)'", вид)) == set(ef.НЕ_ЧТЕНИЕ) == {ef.НЕ_РАСКРЫЛАСЬ, ef.НЕ_СКАЧАЛСЯ, ef.СБОЙ}
    # вид — только строки прогонов, прошедших гейт; суммы — только у «сверено»
    тело_вида = sql[sql.index("create view econ_file_latest"):]
    assert "join econ_file_runs g on g.run_id = r.run_id" in тело_вида[:тело_вида.index(";")]
    assert "status = 'сверено' or num_nulls(sale_sub, sale_eur, purchase_eur, markup, kp_sale_sub) = 5" in sql
    assert "add column if not exists kp_sale_sub numeric" in sql, "create table if not exists колонку не добавит"
    assert "r.kp_sale_sub" in тело_вида[:тело_вида.index(";")]
    # очередь считает чтением только строки прошедших гейт прогонов — как и вид
    import inspect
    assert "join econ_file_runs g on g.run_id = r.run_id" in inspect.getsource(ef.База.прочитано)
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


# ── v4: перечтение после новой версии правил и вид значения в журнале ─────────

ПРЕЖНЯЯ = "econ_files:v3.econ_template:v3"          # версия первого живого прогона 25.10.2026


def test_очередь_не_сверенное_прежней_версией_снова_к_ни_разу_не_читанным():
    """Последнее чтение не «сверено» и сделано версией старше нынешней — сделка снова в черёд
    ни разу не читанных (от новых к старым), не ждёт ПОВТОР_ДНЕЙ; сверенное прежней — в обычный."""
    assert ef.прежняя_версия(ПРЕЖНЯЯ) and not ef.прежняя_версия(ef.METHOD_VER), "версия правил поднята"
    канд = [_сделка(i, дней=10 * i) for i in range(8)]
    вчера = СЕЙЧАС - dt.timedelta(days=1)
    прочитано = {8001: вчера, 8002: вчера, 8003: вчера, 8004: вчера, 8005: СЕЙЧАС - dt.timedelta(days=20),
                 8006: вчера, 8007: вчера}
    наша_файлов = ef._версия(ef.METHOD_VER)[0]
    последние = {8001: (et.НЕТ_ВАЛЮТЫ, ПРЕЖНЯЯ),             # не сверено прежней — снова к новым
                 8002: (et.СВЕРЕНО, ПРЕЖНЯЯ),                # сверено прежней — свежее
                 8003: (et.НЕТ_ВАЛЮТЫ, ef.METHOD_VER),       # нынешней — свежее
                 8004: (ef.СБОЙ, "econ_files:v2"),           # неразборчивая версия — прежняя
                 8005: (et.ШАБЛОН_ДРУГОЙ, ПРЕЖНЯЯ),          # давнее и прежнее — к новым, не к давним
                 # сбой доставки: поднята лишь версия шаблона — свежее, место суток не тратится;
                 # старше версия econ_files (доставка) — к новым
                 8006: (ef.НЕ_РАСКРЫЛАСЬ, ПРЕЖНЯЯ),
                 8007: (ef.НЕ_СКАЧАЛСЯ, f"econ_files:v{наша_файлов - 1}.{et.ВЕРСИЯ}")}
    assert ПРЕЖНЯЯ.startswith(f"econ_files:v{наша_файлов}."), "у ПРЕЖНЕЙ старше только версия шаблона"
    счёт = Counter()
    q = ef.очередь(канд, прочитано, СЕЙЧАС, счёт, None, последние)
    assert [d["ID"] for d in q] == ["8000", "8001", "8004", "8005", "8007"], "от новых сделок к старым"
    assert (счёт["ни разу"], счёт["прежняя версия"], счёт["свежих"], счёт["перечитать"]) == (5, 4, 3, 0)
    # версия новее нынешней (прогон с ветки) — не прежняя: в обычный черёд
    последние[8001] = (et.НЕТ_ВАЛЮТЫ, "econ_files:v3.econ_template:v99")
    assert [d["ID"] for d in ef.очередь(канд, прочитано, СЕЙЧАС, Counter(), None, последние)] == \
        ["8000", "8004", "8005", "8007"]
    # от шаблона зависят статусы разбора и сбой разбора; доставка — нет
    assert set(ef.ОТ_ШАБЛОНА) == set(et.СТАТУСЫ_ФАЙЛА) | {ef.СБОЙ}
    assert set(ef.СТАТУСЫ) - set(ef.ОТ_ШАБЛОНА) == {ef.НЕ_РАСКРЫЛАСЬ, ef.НЕ_ОБЩИЙ, ef.НЕ_XLSX, ef.НЕ_СКАЧАЛСЯ}
    for статус in set(ef.СТАТУСЫ) - set(ef.ОТ_ШАБЛОНА):
        assert not ef.прежняя_версия(ПРЕЖНЯЯ, статус) and ef.прежняя_версия("econ_files:v1.econ_template:v4", статус)
        assert ef.прежняя_версия("иная запись", статус), "неразборчивая — прежняя и у доставки"
    # прогон берёт последние чтения из базы и пишет счёт в журнал
    сделки, диск = _мир(2)
    база = База(прочитано={8000: вчера, 8001: вчера},
                последние={8000: (et.НЕТ_ВАЛЮТЫ, ПРЕЖНЯЯ), 8001: (et.СВЕРЕНО, ПРЕЖНЯЯ)})
    код, журнал, _ = _прогон(сделки, диск, база, режим="dry")
    assert код == 0 and any("ни разу не читанных 1 (из них не сверены прежней версией правил 1)" in s
                            and "свежих 1" in s for s in журнал)
    _журнал_чист(журнал, сделки)


ИМЯ_В_ЯЧЕЙКЕ = "ООО «Ромашка-Сервис» 7654321 info@romashka-servis.example"   # придумано (правило 18)


def test_журнал_вида_значения_без_текста_ячеек():
    """У несверенных файлов журнал считает (статус, где, вид значения); имя компании, номер и
    почта из ячейки в журнал не попадают ни в одном месте — ни в D4, ни в подписи, ни в AE."""
    тела = [книга(ПОЗИЦИИ, валюта_подачи=ИМЯ_В_ЯЧЕЙКЕ),
            книга(ПОЗИЦИИ, заголовки={"G": "Кол-во " + ИМЯ_В_ЯЧЕЙКЕ}),
            книга([ПОЗИЦИИ[0], dict(ПОЗИЦИИ[1], ячейки={"AE": ИМЯ_В_ЯЧЕЙКЕ}), ПОЗИЦИИ[2]]),
            книга(ПОЗИЦИИ, валюта_подачи=ИМЯ_В_ЯЧЕЙКЕ),
            книга(ПОЗИЦИИ)]
    сделки = [_сделка(i, дней=i) for i in range(len(тела) + 1)]
    ссылки = {d[contracts.ECON_LINK]: _sba(_ссылка_диска(f"Сделка {i}/Экономика проекта.xlsx"))
              for i, d in enumerate(сделки[:len(тела)])}     # у последней ссылка не раскрывается
    файлы = {f"vd:{МЕТКА}:disk:/Сделка {i}/Экономика проекта.xlsx": тело for i, тело in enumerate(тела)}
    база = База()
    код, журнал, _ = _прогон(сделки, Диск(файлы, ссылки), база)
    assert код == 0 and [r["status"] for r in база.строки] == [
        et.НЕТ_ВАЛЮТЫ, et.ШАБЛОН_ДРУГОЙ, et.НЕТ_ВАЛЮТЫ, et.НЕТ_ВАЛЮТЫ, et.СВЕРЕНО, ef.НЕ_РАСКРЫЛАСЬ]
    i = журнал.index(next(s for s in журнал if s.startswith("несверенные — вид значения")))
    assert журнал[i + 1:i + 5] == [
        f"  {et.НЕТ_ВАЛЮТЫ} · D4 · текст-иное (>30) · валюты рядом нет: 2",
        f"  {et.НЕТ_ВАЛЮТЫ} · AE14 · текст-иное (>30): 1",
        f"  {ef.НЕ_РАСКРЫЛАСЬ} · — · —: 1",
        f"  {et.ШАБЛОН_ДРУГОЙ} · G12 · текст-иное (>30) · слов словаря шаблона 2 + иных 4+ · "
        "слов ожидаемой подписи 2 из 2 · ожидаемой подписи рядом нет: 1"]
    текст = "\n".join(журнал).lower()
    for слово in ("ромашк", "romashk", "сервис", "servis", "7654321", "@", "info", "example", "ооо"):
        assert слово not in текст, слово
    _журнал_чист(журнал, сделки)
    # вид — журналу, не колонка базы; у сверенного его нет
    assert "shape" not in ef.База.КОЛОНКИ and база.строки[4]["shape"] is None


def test_журнал_вида_без_имён_из_слов_словаря():
    """Ревизия v4: имя клиента из обычных слов словаря шаблона («Новая логистика», «Базис»,
    «Код») в D4, подписи раздела и столбца, AE и R1 — в журнале прогона ни одного его слова:
    у текста печатается только счёт слов словаря."""
    from tests.test_econ_template import _УТЕЧКИ_СЛОВАРЯ, _с_ячейками
    тела = [книга(ПОЗИЦИИ, валюта_подачи="ООО «Новая Логистика»"),
            _с_ячейками(книга(ПОЗИЦИИ), {"I11": "ООО «Таможня и логистика»"}),
            книга(ПОЗИЦИИ, заголовки={"G": "ООО Базис"}),
            книга([ПОЗИЦИИ[0], dict(ПОЗИЦИИ[1], ячейки={"AE": "АО «Код»"}), ПОЗИЦИИ[2]]),
            книга(ПОЗИЦИИ, итоги={"R1": "ООО «Новый курс»"}),
            книга(ПОЗИЦИИ, валюта_подачи="Бренд-Доставка"),
            книга(ПОЗИЦИИ)]
    сделки, _диск = _мир(len(тела))
    файлы = {f"vd:{МЕТКА}:disk:/Сделка {i}/Экономика проекта.xlsx": тело for i, тело in enumerate(тела)}
    база = База()
    код, журнал, _ = _прогон(сделки, Диск(файлы, _диск.ссылки), база)
    assert код == 0 and [r["status"] for r in база.строки] == [
        et.НЕТ_ВАЛЮТЫ, et.ШАБЛОН_ДРУГОЙ, et.ШАБЛОН_ДРУГОЙ, et.НЕТ_ВАЛЮТЫ, et.НЕТ_КУРСА, et.НЕТ_ВАЛЮТЫ, et.СВЕРЕНО]
    i = журнал.index(next(s for s in журнал if s.startswith("несверенные — вид значения")))
    блок = журнал[i + 1:i + 7]
    assert len(блок) == 6 and all(s.startswith("  ") for s in блок), блок
    assert f"  {et.НЕТ_ВАЛЮТЫ} · D4 · текст-иное (11–30) · слов словаря шаблона 2 + иных 1 · валюты рядом нет: 1" \
        in блок
    виды = [s.split(" · ", 2)[2].lower() for s in блок]       # «статус · где · вид: файлов» — вид
    for вид in виды:
        assert "«" not in вид and not re.search(r"(?<![а-я-])код(?![а-я])", вид), вид
        for слово in _УТЕЧКИ_СЛОВАРЯ:
            assert слово not in вид, (слово, вид)
    текст = "\n".join(журнал).lower()
    for слово in ("логистик", "таможн", "базис", "новая", "бренд-доставк"):
        assert слово not in текст, слово
    _журнал_чист(журнал, сделки)
