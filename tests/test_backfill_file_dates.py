"""Дата файла у источника: суд о заголовке закачки, запись разбора и досчёт истории.

Что проверяется и почему:

1. СУД О ЗАГОЛОВКЕ (indexer.дата_заголовка). Что значит Last-Modified ответа
   закачки у портала, на живых данных не проверено, поэтому заголовок
   принимается только правдоподобным: не «сейчас» сервера, не раньше 2000 года,
   не позже ответа, не раньше создания карточки или сделки. Каждый отказ —
   своей причиной (константа кода — в журнал можно).
2. РАЗБОР БЕЗ ЛИШНИХ ЗАПРОСОВ. Письмо получает дату CREATED всегда: она пришла в
   списке писем. Поле карточки или сделки заголовок меряет всегда, а пишет —
   только со входом FILE_DATE_HEADER (правило 3). Из заголовков берутся только
   нужные суду — имя файла из Content-Disposition не должно попасть никуда.
3. ЗАГОЛОВКИ БЕЗ ТЕЛА (indexer.заголовки_адреса) — для досчёта: файл уже
   разобран, качать его целиком ради заголовка незачем; страница входа — отказ.
4. ДОСЧЁТ. Части не рвут владельца; запросов столько, сколько в плане; гейты
   отменяют запись части с противоречиями; холостой прогон НИЧЕГО не пишет (и
   не может: соединение только для чтения); запись — только в пустую дату, с
   ключом прогона; откат снимает ровно своё. В журнале — ни номеров, ни адресов.
5. КОД ПОЛЯ ПО КЛЮЧУ. В базе код лежит и как «UF_CRM_…» (crm.deal.*), а
   crm.item.list отдаёт только «ufCrm…»: сверка идёт ключом doc_folder, в select —
   написание портала из crm.item.fields (раз на сущность за прогон).
6. ГЕЙТ ПО ИСТОЧНИКУ. Письма в той же части не размывают долю противоречий у
   заголовка закачки; не прошедший источник снимается один.
7. ОЧЕРЕДЬ. plan и rollback портал не читают и в общую очередь портала не встают.

Портал и сеть подставные (правило 18); корпус придуман.
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "library"))

import backfill_file_dates as bf  # noqa: E402
import indexer as ix  # noqa: E402

DSN = os.environ.get("LIBRARY_SQL_TEST_DSN")
нужна_база = pytest.mark.skipif(not DSN, reason="одноразовая база PostgreSQL не настроена")
УТС = timezone.utc
МСК = timezone(timedelta(hours=3))
СЕЙЧАС = datetime(2026, 9, 27, 12, 0, tzinfo=УТС)


def http(м: datetime) -> str:
    return format_datetime(м.astimezone(УТС), usegmt=True)


def заголовки(lm: datetime | None, ответ: datetime = СЕЙЧАС, длина: int | None = None) -> dict:
    з = {"Date": http(ответ)}
    if lm is not None:
        з["Last-Modified"] = http(lm)
    if длина is not None:
        з["Content-Length"] = str(длина)
    return з


# ── 1. Суд о заголовке ───────────────────────────────────────────────────────

def test_момент_из_времени_портала_и_http():
    assert ix.момент("2026-09-20T10:15:00+03:00") == datetime(2026, 9, 20, 7, 15, tzinfo=УТС)
    assert ix.момент("2026-09-20T07:15:00Z") == datetime(2026, 9, 20, 7, 15, tzinfo=УТС)
    assert ix.момент("Sun, 20 Sep 2026 07:15:00 GMT") == datetime(2026, 9, 20, 7, 15, tzinfo=УТС)
    # Без пояса — не догадка: сдвиг на три часа переносит файл в другой день.
    for мусор in ("2026-09-20T10:15:00", "", None, "вчера", datetime(2026, 9, 20)):
        assert ix.момент(мусор) is None, мусор


@pytest.mark.parametrize("з, создан, почему", [
    ({"Date": http(СЕЙЧАС)}, None, ix.ЗАГ_НЕТ),
    ({"Date": http(СЕЙЧАС), "Last-Modified": "позавчера"}, None, ix.ЗАГ_НЕ_ЧИТАЕТСЯ),
    # Сервер ставит «сейчас» любому ответу — это не дата файла.
    (заголовки(СЕЙЧАС - timedelta(seconds=1)), None, ix.ЗАГ_ОТВЕТ),
    (заголовки(datetime(1999, 12, 31, tzinfo=УТС)), None, ix.ЗАГ_ДО_2000),
    (заголовки(СЕЙЧАС + timedelta(hours=1)), None, ix.ЗАГ_БУДУЩЕЕ),
    # Файл кладут в поле уже заведённой карточки: раньше неё — дата чужой копии.
    (заголовки(datetime(2026, 9, 1, tzinfo=УТС)), "2026-09-10T09:00:00+03:00", ix.ЗАГ_ДО_ВЛАДЕЛЬЦА),
    (заголовки(datetime(2026, 9, 10, 5, 30, tzinfo=УТС)), "2026-09-10T09:00:00+03:00", ix.ЗАГ_ПРИНЯТ),
    (заголовки(datetime(2026, 9, 20, tzinfo=УТС)), None, ix.ЗАГ_ПРИНЯТ),
])
def test_суд_о_заголовке(з, создан, почему):
    м, суд = ix.дата_заголовка(з, создан)
    assert суд == почему
    assert (м is not None) == (почему == ix.ЗАГ_ПРИНЯТ)


def test_имена_заголовков_без_учёта_регистра():
    м, суд = ix.дата_заголовка({"last-modified": http(datetime(2026, 9, 20, tzinfo=УТС)),
                               "DATE": http(СЕЙЧАС)})
    assert суд == ix.ЗАГ_ПРИНЯТ and м == datetime(2026, 9, 20, tzinfo=УТС)


# ── 2. Разбор: письмо, поле, вход ────────────────────────────────────────────

class Ответ:
    def __init__(self, код: int, тело: bytes = b"x" * 500, з: dict | None = None):
        self.status_code, self.content, self.headers = код, тело, з or {}
        self.закрыт = False
        self.прочитано = 0

    def iter_content(self, n):
        self.прочитано += n
        yield self.content[:n]

    def close(self):
        self.закрыт = True


@pytest.fixture(autouse=True)
def без_пауз(monkeypatch):
    import bitrix_client
    monkeypatch.setattr(ix.time, "sleep", lambda _с: None)
    monkeypatch.setattr(bitrix_client.time, "sleep", lambda _с: None)


def _ссылка(**ещё):
    return {"fo": {"id": "501", "urlMachine": "https://portal.test/rest/1/SEKRET/getFile?x=1"},
            "deal": "7770001", "origin": "поле запроса", "field": "ufCrm18_1700698211875",
            "field_title": "КП поставщика", "card_created": "2026-09-10T09:00:00+03:00", **ещё}


def test_из_заголовков_берутся_только_нужные_суду(monkeypatch):
    з = {**заголовки(datetime(2026, 9, 20, tzinfo=УТС), длина=500),
         "Content-Disposition": 'attachment; filename="Смета_Ромашка.xlsx"', "Set-Cookie": "s=1"}
    monkeypatch.setattr(ix.requests, "get", lambda *_a, **_k: Ответ(200, з=з))
    monkeypatch.setattr(ix, "is_login_page", lambda _b: False)
    rec: dict = {}
    assert ix.download(_ссылка()["fo"], rec)
    assert set(rec["заголовки_закачки"]) == {"Last-Modified", "Date", "Content-Length"}
    assert "Ромашка" not in str(rec)


@pytest.mark.parametrize("вход, пишется", [(False, False), (True, True)])
def test_поле_карточки_меряет_заголовок_всегда_а_пишет_со_входом(monkeypatch, вход, пишется):
    загружен = datetime(2026, 9, 20, 8, tzinfo=УТС)
    monkeypatch.setattr(ix, "ДАТА_ЗАГОЛОВКА", вход)
    monkeypatch.setattr(ix, "download",
                        lambda fo, rec=None: rec.update(заголовки_закачки=заголовки(загружен)))
    rec, _ = ix.handle(_ссылка())
    assert rec["дата_заголовка"] == ix.ЗАГ_ПРИНЯТ
    строка = ix.строка_файла(rec)
    assert (строка["source_created_at"], строка["source_date_src"]) == (
        (загружен, ix.ИСТ_ДАТЫ_ЗАКАЧКА) if пишется else (None, None))


def test_не_скачавшийся_файл_поля_без_даты_письмо_с_датой(monkeypatch):
    monkeypatch.setattr(ix, "ДАТА_ЗАГОЛОВКА", True)
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: None)
    rec, _ = ix.handle(_ссылка())
    assert (rec["source_created_at"], rec["дата_заголовка"]) == (None, ix.ЗАГ_НЕ_КАЧАЛИ)
    # Письмо пришло в свой день, скачали мы его или нет; заголовок не судится.
    rec, _ = ix.handle(_ссылка(origin="письмо поставщика", field="письмо 9001",
                               card_created="2026-09-16T09:30:00+03:00", file_id="mail:501"))
    assert (rec["source_created_at"], rec["source_date_src"], rec["дата_заголовка"]) == (
        datetime(2026, 9, 16, 6, 30, tzinfo=УТС), ix.ИСТ_ДАТЫ_ПИСЬМО, None)
    rec, _ = ix.handle(_ссылка(origin="письмо поставщика", field="письмо 9001", card_created="",
                               file_id="mail:502"))
    assert rec["source_created_at"] is None


def test_сделка_несёт_дату_создания_тем_же_запросом(monkeypatch):
    списки = []

    def bx(method, params):
        списки.append((method, params))
        return {"result": {"items": [{"id": 501, "mycompanyId": 0,
                                      "createdTime": "2026-09-01T10:00:00+03:00",
                                      "ufCrm_1": [{"id": 71, "urlMachine": "https://x.test/71"}]}]}}
    monkeypatch.setattr(ix, "bx", bx)
    refs = ix.ссылки_сделок([501], ["ufCrm_1"], {"ufCrm_1": "Offer from supplier(s)"})
    assert len(списки) == 1 and "createdTime" in списки[0][1]["select"]
    assert refs[0]["owner_created"] == "2026-09-01T10:00:00+03:00"


# ── 3. Заголовки без тела ────────────────────────────────────────────────────

def test_заголовки_без_тела(monkeypatch):
    ответ = Ответ(200, b"%PDF-1.4" + b"0" * 5000, заголовки(datetime(2026, 9, 20, tzinfo=УТС)))
    вызовы = []
    monkeypatch.setattr(ix.requests, "get", lambda *a, **k: вызовы.append(k) or ответ)
    з, почему = ix.заголовки_адреса("https://portal.test/f/1")
    assert почему == "" and "Last-Modified" in з
    assert вызовы[0].get("stream") is True and ответ.закрыт and ответ.прочитано <= 512


def test_заголовки_страница_входа_и_отказ(monkeypatch):
    ответ = Ответ(200, b"<!DOCTYPE html><html>login</html>", заголовки(СЕЙЧАС))
    monkeypatch.setattr(ix.requests, "get", lambda *a, **k: ответ)
    assert ix.заголовки_адреса("https://portal.test/f/1") == (None, "вместо файла страница входа")
    assert ответ.закрыт
    попытки = []
    monkeypatch.setattr(ix.requests, "get", lambda *a, **k: попытки.append(1) or Ответ(404, b""))
    assert ix.заголовки_адреса("https://portal.test/f/1") == (None, "код 404")
    assert len(попытки) == 1, "404 не лечится повтором"


# ── 4. Досчёт: части, план, суд части ────────────────────────────────────────

def _файл(fid, ист, владелец, **кв):
    return bf.Файл(fid, ист, владелец, кв.pop("поле", "ufCrm18_1700698211875"), "разобран",
                   кв.pop("размер", 1000), кв.pop("первая", СЕЙЧАС))


def test_части_не_рвут_владельца_и_план_запросов():
    файлы = ([_файл(f"п{i}", bf.ПИСЬМА, str(900 + i // 2)) for i in range(6)]
             + [_файл(f"к{i}", bf.КАРТОЧКИ, str(100 + i // 3)) for i in range(9)]
             + [_файл("с1", bf.СДЕЛКИ, "55")])
    for n in (1, 2, 3, 7, 50):
        куски = bf.части(файлы, n)
        assert all(куски) and len(куски) <= n
        assert sorted(f.file_id for к in куски for f in к) == sorted(f.file_id for f in файлы)
        владельцы = [{(f.источник, f.владелец) for f in к} for к in куски]
        assert sum(len(в) for в in владельцы) == len(set().union(*владельцы)), "владелец в двух частях"
    # Письма: по запросу на 50 писем; карточки и сделки — на 50 владельцев плюс по файлу.
    assert bf.запросов(файлы) == 1 + (1 + 9) + (1 + 1)
    assert bf.выборка(файлы, 5) == bf.выборка(list(reversed(файлы)), 5)
    assert len(bf.выборка(файлы, 5)) == 5 and bf.выборка(файлы, 0) == файлы


def test_монотонность_и_квантили():
    д = lambda d: datetime(2026, 9, d, tzinfo=УТС)                    # noqa: E731
    assert bf.монотонность([(10, д(1)), (11, д(2)), (12, д(3))]) == (2, 0)
    assert bf.монотонность([(10, д(5)), (11, д(2)), (12, д(3))]) == (2, 1)
    assert bf.монотонность([]) == (0, 0)
    assert "медиана 2.0" in bf.квантили([1.0, 2.0, 3.0])
    assert bf.квантили([122.0, 266.0]).endswith("10 % 122.0 · медиана 194.0 · 90 % 266.0")
    assert bf.квантили([]) == "нет"


def test_источники_входа():
    assert bf.источники_входа("") == bf.ИСТОЧНИКИ
    assert bf.источники_входа(" deals , mail") == ("mail", "deals")
    with pytest.raises(SystemExit):
        bf.источники_входа("mail,лиды")


class Портал:
    """Подставной портал: письма, карточки и сделки, заголовки по адресу.

    Как настоящий crm.item.*: поля описывает и отдаёт ТОЛЬКО в написании
    «ufCrm…» — поле, названное в select иначе, ответ не несёт под тем именем.
    Описание полей сущности (поля) по умолчанию — «uf»-ключи её записей."""

    def __init__(self, письма=None, владельцы=None, заголовки_по_адресу=None, поля=None):
        self.письма = письма or {}
        self.владельцы = владельцы or {}            # (сущность, номер) → запись
        self.заголовки = заголовки_по_адресу or {}
        self.поля = поля                            # сущность → коды полей
        self.вызовы: list = []
        self.select: list = []

    def коды(self, сущность) -> set[str]:
        if self.поля is not None:
            return set(self.поля.get(сущность) or ())
        return {к for (с, _н), з in self.владельцы.items() if с == сущность
                for к in з if к.startswith("uf")}

    def bx(self, method, params):
        self.вызовы.append(method)
        if method == "crm.activity.list":
            assert params["start"] == -1
            return {"result": [{"ID": str(n), "CREATED": self.письма[str(n)]}
                               for n in params["filter"]["ID"] if str(n) in self.письма]}
        if method == "crm.item.fields":
            return {"result": {"fields": {к: {"type": "file"} for к in self.коды(params["entityTypeId"])}}}
        assert method == "crm.item.list" and params["start"] == -1
        self.select.append(list(params["select"]))
        выбрано = set(params["select"])
        return {"result": {"items": [
            {к: v for к, v in self.владельцы[(params["entityTypeId"], str(n))].items()
             if к in ("id", "createdTime") or к in выбрано}
            for n in params["filter"]["@id"] if (params["entityTypeId"], str(n)) in self.владельцы]}}

    def заголовки_адреса(self, адрес):
        self.вызовы.append("файл")
        return self.заголовки.get(адрес, (None, "код 403"))


def карточка(номер, создана, **поля):
    return {"id": int(номер), "createdTime": создана,
            **{п: [{"id": int(fid), "urlMachine": f"https://portal.test/f/{fid}"} for fid in fids]
               for п, fids in поля.items()}}


def test_суд_части_и_гейты():
    поле = "ufCrm18_1700698211875"
    п = Портал(
        письма={"9001": "2026-09-16T09:30:00+03:00"},
        владельцы={(166, "7770001"): карточка("7770001", "2026-09-10T09:00:00+03:00",
                                              **{поле: ["501", "502", "503"]})},
        заголовки_по_адресу={
            "https://portal.test/f/501": (заголовки(datetime(2026, 9, 20, tzinfo=УТС), длина=1000), ""),
            "https://portal.test/f/502": (заголовки(СЕЙЧАС - timedelta(seconds=1)), ""),
            "https://portal.test/f/503": (заголовки(datetime(2026, 9, 1, tzinfo=УТС)), ""),
        })
    часть = [_файл("mail:1", bf.ПИСЬМА, "9001", поле="письмо 9001"),
             _файл("mail:2", bf.ПИСЬМА, "9002", поле="письмо 9002"),
             _файл("501", bf.КАРТОЧКИ, "7770001"), _файл("502", bf.КАРТОЧКИ, "7770001"),
             _файл("503", bf.КАРТОЧКИ, "7770001"), _файл("504", bf.КАРТОЧКИ, "7770001")]
    и = bf.разобрать_часть(часть, п.bx, п.заголовки_адреса)
    assert и.даты == {"mail:1": (datetime(2026, 9, 16, 6, 30, tzinfo=УТС), ix.ИСТ_ДАТЫ_ПИСЬМО),
                      "501": (datetime(2026, 9, 20, tzinfo=УТС), ix.ИСТ_ДАТЫ_ЗАКАЧКА)}
    assert и.причины == {("mail", bf.ВЛАДЕЛЬЦА_НЕТ): 1, ("rfq", "заголовок: " + ix.ЗАГ_ОТВЕТ): 1,
                         ("rfq", "заголовок: " + ix.ЗАГ_ДО_ВЛАДЕЛЬЦА): 1, ("rfq", bf.ФАЙЛА_НЕТ): 1}
    assert и.провал == [] and и.счёт["станет хуже"] == 0
    # Запросов ровно по плану: одно письмо на пачку, одна карточка, по файлу из
    # поля и одно описание полей сущности.
    assert п.вызовы.count("crm.activity.list") == 1 and п.вызовы.count("crm.item.list") == 1
    assert п.вызовы.count("crm.item.fields") == 1 == bf.описаний(часть)
    assert п.вызовы.count("файл") == 3 <= bf.запросов(часть)

    # Противоречие: размер не тот, дата позже первой обработки — гейт отменяет часть.
    п.заголовки["https://portal.test/f/502"] = (заголовки(datetime(2026, 9, 20, tzinfo=УТС), длина=7), "")
    поздний = _файл("503", bf.КАРТОЧКИ, "7770001", первая=datetime(2026, 9, 5, tzinfo=УТС))
    п.заголовки["https://portal.test/f/503"] = (заголовки(datetime(2026, 9, 20, tzinfo=УТС)), "")
    и = bf.разобрать_часть([часть[2], часть[3], поздний], п.bx, п.заголовки_адреса)
    assert и.причины[("rfq", bf.РАЗМЕР_НЕ_ТОТ)] == 1 and и.причины[("rfq", bf.ПОЗЖЕ_ОБРАБОТКИ)] == 1
    assert и.счёт["rfq: противоречий"] == 2 and и.провал and "противоречий 2 из 3" in и.провал[0]
    # Годная дата 501 той же части снята вместе с источником: запись части — всё или ничего.
    assert и.даты == {} and и.счёт["снято гейтом"] == 1 and и.провал[0].startswith("rfq: ")

    # Портал не отдал владельцев — сбой чтения, а не удалённые карточки.
    и = bf.разобрать_часть([_файл("601", bf.КАРТОЧКИ, "7770009")], п.bx, п.заголовки_адреса)
    assert и.провал and "портал отдал 0 владельцев из 1" in и.провал[0]


def test_код_поля_в_написании_crm_deal_находится_по_ключу():
    """В базе «UF_CRM_…», портал отдаёт «ufCrm…»: вложение находится, в select —
    написание портала, а не записанное; поля, которого на портале нет, —
    своя причина, а не «файла в поле больше нет»."""
    сделка, кп = "ufCrm_1577091983333", "ufCrm18_1700698211875"
    п = Портал(
        владельцы={
            (2, "6660001"): карточка("6660001", "2026-09-01T09:00:00+03:00", **{сделка: ["801"]}),
            (166, "7770001"): карточка("7770001", "2026-09-10T09:00:00+03:00", **{кп: ["802"]}),
        },
        заголовки_по_адресу={
            "https://portal.test/f/801": (заголовки(datetime(2026, 9, 18, tzinfo=УТС), длина=1000), ""),
            "https://portal.test/f/802": (заголовки(datetime(2026, 9, 19, tzinfo=УТС), длина=1000), ""),
        })
    описания: dict = {}
    сд = [_файл("801", bf.СДЕЛКИ, "6660001", поле="UF_CRM_1577091983333"),
          _файл("803", bf.СДЕЛКИ, "6660001", поле="UF_CRM_1999999999")]
    и = bf.разобрать_часть(сд, п.bx, п.заголовки_адреса, описания)
    assert и.даты == {"801": (datetime(2026, 9, 18, tzinfo=УТС), ix.ИСТ_ДАТЫ_ЗАКАЧКА)}
    assert и.причины == {("deals", bf.ПОЛЯ_НЕТ): 1} and и.провал == []
    assert п.select[-1] == ["id", "createdTime", сделка]
    # СП-166: «UF_CRM_18_…» ↔ «ufCrm18_…»; вторая часть описание полей не просит.
    и = bf.разобрать_часть([_файл("802", bf.КАРТОЧКИ, "7770001", поле="UF_CRM_18_1700698211875")],
                           п.bx, п.заголовки_адреса, описания)
    assert list(и.даты) == ["802"] and п.select[-1] == ["id", "createdTime", кп]
    bf.разобрать_часть(сд, п.bx, п.заголовки_адреса, описания)
    assert п.вызовы.count("crm.item.fields") == 2, "описание полей — раз на сущность за прогон"


def test_ответ_с_полем_в_другом_написании_сверяется_ключом():
    """Если портал отдаст поле не тем написанием, что в select, вложение не теряется."""
    def bx(method, params):
        if method == "crm.item.fields":
            return {"result": {"fields": {"ufCrm_1577091983333": {"type": "file"}}}}
        return {"result": {"items": [{"id": 6660001, "createdTime": "2026-09-01T09:00:00+03:00",
                                      "UF_CRM_1577091983333": [{"id": 801, "urlMachine": "https://portal.test/f/801"}]}]}}
    отдано, вложения = bf.прочитать_владельцев(bf.СДЕЛКИ, ["6660001"], ["ufCrm_1577091983333"], bx)
    assert отдано == {"6660001"} and set(вложения) == {"801"}


def test_портал_не_отдал_описание_полей_снимает_только_свой_источник():
    п = Портал(письма={"9001": "2026-09-16T09:30:00+03:00"},
               владельцы={(166, "7770001"): карточка("7770001", "2026-09-10T09:00:00+03:00",
                                                     ufCrm18_1700698211875=["501"])},
               поля={})
    часть = [_файл("mail:1", bf.ПИСЬМА, "9001", поле="письмо 9001"), _файл("501", bf.КАРТОЧКИ, "7770001")]
    и = bf.разобрать_часть(часть, п.bx, п.заголовки_адреса)
    assert list(и.даты) == ["mail:1"]
    assert и.причины == {("rfq", bf.ОПИСАНИЯ_НЕТ): 1}
    assert len(и.провал) == 1 and и.провал[0].startswith("rfq: " + bf.ОПИСАНИЯ_НЕТ)
    assert "crm.item.list" not in п.вызовы and "файл" not in п.вызовы


def _смешанная_часть(портал_отдаёт_карточки: bool = True, размер_не_тот: int = 5):
    """100 писем и 10 файлов карточек в ОДНОЙ части — как на стыке источников."""
    письма = {str(9000 + i): "2026-09-16T09:30:00+03:00" for i in range(100)}
    владельцы, заг = {}, {}
    for k in range(10):
        fid = str(700 + k)
        if портал_отдаёт_карточки:
            владельцы[(166, str(7770000 + k))] = карточка(str(7770000 + k), "2026-09-10T09:00:00+03:00",
                                                          ufCrm18_1700698211875=[fid])
        заг[f"https://portal.test/f/{fid}"] = (
            заголовки(datetime(2026, 9, 20, tzinfo=УТС), длина=7 if k < размер_не_тот else 1000), "")
    п = Портал(письма=письма, владельцы=владельцы, заголовки_по_адресу=заг,
               поля={166: ["ufCrm18_1700698211875"]})
    часть = ([_файл(f"mail:{i}", bf.ПИСЬМА, str(9000 + i), поле=f"письмо {9000 + i}") for i in range(100)]
             + [_файл(str(700 + k), bf.КАРТОЧКИ, str(7770000 + k)) for k in range(10)])
    return п, часть


def test_письма_не_размывают_гейт_заголовков_в_смешанной_части():
    """Счёт на всю часть давал 5 противоречий из 110 — порог пройден, и 5 дат из
    заголовка, у которого противоречит половина, шли в запись."""
    п, часть = _смешанная_часть()
    assert len(bf.части(часть, 1)) == 1
    и = bf.разобрать_часть(часть, п.bx, п.заголовки_адреса)
    assert и.счёт["rfq: противоречий"] == 5 and и.счёт["rfq: с датой-кандидатом"] == 10
    assert и.счёт["mail: противоречий"] == 0 and и.счёт["mail: с датой-кандидатом"] == 100
    assert not any(fid.isdigit() for fid in и.даты), "даты заголовков прошли мимо гейта"
    assert sum(1 for fid in и.даты if fid.startswith("mail:")) == 100, "письма сняты заодно"
    assert и.счёт["снято гейтом"] == 5 and и.счёт["дат к записи"] == 100
    assert len(и.провал) == 1 and и.провал[0].startswith("rfq: противоречий 5 из 10")
    # Без противоречий — обе стороны части пишутся.
    п, часть = _смешанная_часть(размер_не_тот=0)
    и = bf.разобрать_часть(часть, п.bx, п.заголовки_адреса)
    assert и.провал == [] and len(и.даты) == 110


def test_сбой_чтения_судится_по_источнику():
    """Портал отдал все письма и ни одной карточки: 100 из 110 владельцев на всю
    часть — «гейт пройден», по источнику — сбой чтения карточек."""
    п, часть = _смешанная_часть(портал_отдаёт_карточки=False, размер_не_тот=0)
    и = bf.разобрать_часть(часть, п.bx, п.заголовки_адреса)
    assert len(и.провал) == 1 and и.провал[0].startswith("rfq: портал отдал 0 владельцев из 10")
    assert len(и.даты) == 100


def test_откат_из_одних_пробелов_отказ_до_базы(monkeypatch):
    """Прогон с непустым rollback стоит вне очереди портала — читать портал он не должен."""
    import psycopg2
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example.test/db")
    monkeypatch.setenv("ROLLBACK", "  ")

    def нельзя(*_a, **_k):
        raise AssertionError("соединение с базой при пустом ключе отката")
    monkeypatch.setattr(psycopg2, "connect", нельзя)
    вывод = io.StringIO()
    with redirect_stdout(вывод):
        assert bf.main() == 2
    assert "ROLLBACK из одних пробелов" in вывод.getvalue()


# ── 5. Досчёт на PostgreSQL: холостой ничего не пишет, запись и откат ────────

СХЕМА = "backfill_file_dates_test"
КП = "ufCrm18_1700698211875"
ОФФЕР_СДЕЛКИ = "ufCrm_1577091983333"
ОФФЕР_СДЕЛКИ_ВЕРХНИЙ = "UF_CRM_1577091983333"      # то же поле в написании crm.deal.*

# Корпус: окно 60 дней от 27.09.2026 12:00 UTC.
#   КП-А (карточка 7770001) — заголовок правдоподобен → дата;
#   КП-Б (та же карточка)   — заголовок «сейчас» сервера → без даты;
#   КП-В (карточка 7770002) — заголовок позже первой строки спроса → противоречие,
#                             гейт части: дата не пишется;
#   письмо 9990001 — тело и вложение: CREATED → дата обоим;
#   СД-А (сделка 6660001, «Offer from supplier(s)») → дата; код поля записан
#                             в написании crm.deal.* («UF_CRM_…»), как у части
#                             живых строк, а портал отдаёт его как «ufCrm_…»;
#   СД-Б (та же сделка, спецификация заказчика) — не предложение;
#   КП-Г — обработан до окна; КП-Д — не скачался; КП-Е — дата уже есть: не трогаются.
# Строки до миграции дат — как на живой базе: first_seen_at у них пусто. Вставка
# после миграции ставит first_seen_at сама (и явный null — тоже), поэтому
# историю кладём ДО неё, а письма — после, как новые файлы.
КОРПУС_ДО = f"""
insert into lib_files (file_id, deal_id, origin, field, field_title, side, status, size_bytes,
                       processed_at) values
 ('КП-А', '7770001', 'поле запроса', '{КП}', 'КП поставщика', 'поставщик', 'разобран', 1000,
  '2026-09-22 10:00+00'),
 ('КП-Б', '7770001', 'поле запроса', '{КП}', 'КП поставщика', 'поставщик', 'разобран', 1000,
  '2026-09-22 10:00+00'),
 ('КП-В', '7770002', 'поле запроса', '{КП}', 'КП поставщика', 'поставщик', 'разобран', 1000,
  '2026-09-22 10:00+00'),
 ('СД-А', '6660001', 'поле сделки', '{ОФФЕР_СДЕЛКИ_ВЕРХНИЙ}', 'Offer from supplier(s)', 'поставщик', 'разобран', 2000,
  '2026-09-23 10:00+00'),
 ('СД-Б', '6660001', 'поле сделки', 'ufCrm_1633502831', 'Техническая спецификация', 'заказчик', 'разобран', 2000,
  '2026-09-23 10:00+00'),
 ('КП-Г', '7770003', 'поле запроса', '{КП}', 'КП поставщика', 'поставщик', 'разобран', 1000,
  '2026-06-01 10:00+00'),
 ('КП-Д', '7770004', 'поле запроса', '{КП}', 'КП поставщика', 'поставщик', 'не скачался', null,
  '2026-09-22 10:00+00'),
 ('КП-Е', '7770005', 'поле запроса', '{КП}', 'КП поставщика', 'поставщик', 'разобран', 1000,
  '2026-09-22 10:00+00');
insert into lib_demand (deal_id, item_name, source, source_file, created_at)
values ('7770002', 'позиция', 'КП', 'КП-В', '2026-08-01 10:00+00');
"""
КОРПУС_ПОСЛЕ = """
insert into lib_files (file_id, deal_id, origin, field, field_title, side, status, size_bytes,
                       processed_at, first_seen_at) values
 ('mail:880001', 'C5550001', 'письмо поставщика', 'письмо 9990001', 'входящее письмо', 'поставщик',
  'разобран', 800, '2026-09-21 10:00+00', '2026-09-21 10:00+00'),
 ('mail-body:9990001', 'C5550001', 'письмо поставщика', 'письмо 9990001', 'входящее письмо', 'поставщик',
  'текст без спецификации', 300, '2026-09-21 10:00+00', '2026-09-21 10:00+00');
update lib_files set source_created_at = '2026-09-19 10:00+00', source_date_src = 'закачка: Last-Modified'
 where file_id = 'КП-Е';
"""
УТЕЧКИ = ("КП-", "СД-", "mail:88", "mail-body:", "7770001", "7770002", "6660001", "9990001",
          "C5550001", "portal.test", "Offer from")


def _портал() -> Портал:
    return Портал(
        письма={"9990001": "2026-09-21T11:40:00+03:00"},
        владельцы={
            (166, "7770001"): {"id": 7770001, "createdTime": "2026-09-15T09:00:00+03:00",
                               КП: [{"id": "КП-А", "urlMachine": "https://portal.test/f/a"},
                                    {"id": "КП-Б", "urlMachine": "https://portal.test/f/b"}]},
            (166, "7770002"): {"id": 7770002, "createdTime": "2026-07-01T09:00:00+03:00",
                               КП: [{"id": "КП-В", "urlMachine": "https://portal.test/f/c"}]},
            (2, "6660001"): {"id": 6660001, "createdTime": "2026-09-01T09:00:00+03:00",
                             ОФФЕР_СДЕЛКИ: {"id": "СД-А", "urlMachine": "https://portal.test/f/d"}},
        },
        заголовки_по_адресу={
            "https://portal.test/f/a": (заголовки(datetime(2026, 9, 20, 8, tzinfo=УТС), длина=1000), ""),
            "https://portal.test/f/b": (заголовки(СЕЙЧАС), ""),
            "https://portal.test/f/c": (заголовки(datetime(2026, 9, 20, 8, tzinfo=УТС)), ""),
            "https://portal.test/f/d": (заголовки(datetime(2026, 9, 18, 8, tzinfo=УТС), длина=2000), ""),
        })


@pytest.fixture
def база(monkeypatch):
    import psycopg2

    from tests.test_library_schema_sql import операторы
    for к, v in {"DAYS": "60", "SHARDS": "10", "SOURCES": "", "LIMIT": "0", "PLAN": "",
                 "RUN_ID": "fd-тест"}.items():
        monkeypatch.setenv(к, v)
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    c = conn.cursor()
    c.execute(f"drop schema if exists {СХЕМА} cascade")
    c.execute(f"create schema {СХЕМА}")
    c.execute(f"set search_path to {СХЕМА}")
    for имя in ("schema.sql", "schema_junk.sql"):
        for оператор in операторы((ROOT / "library" / "supabase" / имя).read_text(encoding="utf-8")):
            c.execute(оператор)
    c.execute(КОРПУС_ДО)
    for оператор in операторы((ROOT / "library" / "supabase" / "file_dates_schema.sql")
                              .read_text(encoding="utf-8")):
        c.execute(оператор)
    c.execute(КОРПУС_ПОСЛЕ)
    try:
        yield c
    finally:
        c.execute(f"drop schema if exists {СХЕМА} cascade")
        conn.close()


def _прогон(писать: bool, портал: Портал) -> tuple[int, str]:
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(DSN, options=f"-c search_path={СХЕМА}")
    conn.set_session(readonly=not писать)            # как main(): холостой — только чтение
    вывод = io.StringIO()
    try:
        with redirect_stdout(вывод):
            код = bf.выполнить(conn, писать, psycopg2.extras.execute_values, bx=портал.bx,
                               заголовки_адреса=портал.заголовки_адреса, сейчас=СЕЙЧАС)
    finally:
        conn.close()
    return код, вывод.getvalue()


def _снимок(cur) -> list:
    cur.execute("select * from lib_files order by file_id")
    return cur.fetchall()


@нужна_база
def test_холостой_досчёт_ничего_не_пишет(база):
    до = _снимок(база)
    портал = _портал()
    код, текст = _прогон(False, портал)
    assert код == 1                                   # часть КП-В не прошла гейт — сказано кодом
    assert _снимок(база) == до, "холостой прогон изменил базу"
    assert "файл" in портал.вызовы, "холостой прогон обязан мерить, а не молчать"
    # Описание полей — раз на сущность за прогон, хотя частей с карточками две.
    assert портал.вызовы.count("crm.item.fields") == 2
    assert any(ОФФЕР_СДЕЛКИ in s for s in портал.select)
    assert not any(ОФФЕР_СДЕЛКИ_ВЕРХНИЙ in s for s in портал.select)
    assert "вхолостую: в базе ничего не изменено" in текст
    итог = next(с for с in текст.splitlines() if с.startswith("ИТОГ:"))
    assert "дат к записи 4" in итог and "станет хуже 0" in итог and "записано" not in итог
    assert "гейт не пройден" in текст and "противоречий 1 из 1" in текст
    assert f"{bf.НЕ_СКАЧИВАЛСЯ} 1" in текст and f"{bf.НЕ_ПРЕДЛОЖЕНИЕ} 1" in текст
    for кусок in УТЕЧКИ:
        assert кусок not in текст, f"в журнал попало: {кусок!r}"


@нужна_база
def test_план_не_читает_портал(база, monkeypatch):
    monkeypatch.setenv("PLAN", "1")
    портал = _портал()
    код, текст = _прогон(False, портал)
    assert код == 0 and портал.вызовы == []
    # Шесть файлов к досчёту: письмо (тело и вложение — один запрос на пачку писем),
    # две карточки в разных частях (по запросу на карточку и по файлу: 2 + 3),
    # сделка (1 + 1) и по описанию полей на карточки и сделки (2) — 10 запросов.
    assert "файлов без даты у источника: 6" in текст and "запросов к порталу около 10" in текст


@нужна_база
def test_запись_только_в_пустое_с_ключом_и_откат(база, monkeypatch):
    код, текст = _прогон(True, _портал())
    assert код == 1 and "ключ прогона fd-тест" in текст
    база.execute("select file_id, source_created_at, source_date_src, source_date_run from lib_files"
                 " where source_created_at is not null order by file_id")
    записано = {r[0]: r[1:] for r in база.fetchall()}
    assert записано == {
        "mail-body:9990001": (datetime(2026, 9, 21, 8, 40, tzinfo=УТС), ix.ИСТ_ДАТЫ_ПИСЬМО, "fd-тест"),
        "mail:880001": (datetime(2026, 9, 21, 8, 40, tzinfo=УТС), ix.ИСТ_ДАТЫ_ПИСЬМО, "fd-тест"),
        "КП-А": (datetime(2026, 9, 20, 8, tzinfo=УТС), ix.ИСТ_ДАТЫ_ЗАКАЧКА, "fd-тест"),
        "СД-А": (datetime(2026, 9, 18, 8, tzinfo=УТС), ix.ИСТ_ДАТЫ_ЗАКАЧКА, "fd-тест"),
        # Записанная раньше — не тронута.
        "КП-Е": (datetime(2026, 9, 19, 10, tzinfo=УТС), ix.ИСТ_ДАТЫ_ЗАКАЧКА, None),
    }
    # Повтор берёт только оставшееся пустым: ничего нового не пишет и не переписывает.
    до = _снимок(база)
    _прогон(True, _портал())
    assert _снимок(база) == до
    # Откат снимает ровно своё.
    monkeypatch.setenv("ROLLBACK", "fd-тест")
    monkeypatch.setenv("SUPABASE_DB_URL", DSN)
    import psycopg2
    настоящий = psycopg2.connect
    monkeypatch.setattr(psycopg2, "connect",
                        lambda dsn, **k: настоящий(dsn, **{**k, "options": k.get("options", "")
                                                           + f" -c search_path={СХЕМА}"}))
    вывод = io.StringIO()
    with redirect_stdout(вывод):
        assert bf.main() == 0
    assert "снята у файлов: 4" in вывод.getvalue()
    база.execute("select file_id from lib_files where source_created_at is not null")
    assert [r[0] for r in база.fetchall()] == ["КП-Е"]


def test_main_холостой_открывает_соединение_только_для_чтения(monkeypatch):
    """Свойство соединения, а не дисциплина кода: запись в холостом упадёт в базе."""
    import psycopg2

    class Соединение:
        def __init__(self):
            self.сессия = None

        def set_session(self, **k):
            self.сессия = k

        def close(self):
            pass

    с = Соединение()
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example.test/db")
    monkeypatch.delenv("ROLLBACK", raising=False)
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: с)
    monkeypatch.setattr(bf, "выполнить", lambda conn, писать, *_a, **_k: 0)
    for apply, только_чтение in (("", True), ("1", False)):
        monkeypatch.setenv("APPLY", apply)
        assert bf.main() == 0
        assert с.сессия == {"readonly": только_чтение}


def группа_очереди(wf: dict, plan: bool, rollback: str) -> str:
    """Вычислить выражение группы вида `(inputs.plan || inputs.rollback != '') && 'x' || 'y'`."""
    import re
    выр = wf["concurrency"]["group"]
    m = re.fullmatch(r"\$\{\{\s*\(\s*inputs\.plan\s*\|\|\s*inputs\.rollback\s*!=\s*''\s*\)"
                     r"\s*&&\s*'([^']+)'\s*\|\|\s*'([^']+)'\s*\}\}", выр.strip())
    assert m, f"группа очереди не распознана: {выр}"
    return m.group(1) if (plan or rollback != "") else m.group(2)


def test_прогон_ручной_очередь_портала_только_для_чтения_портала_и_входы_не_прибиты():
    import yaml
    wf = yaml.safe_load((ROOT / ".github/workflows/library-file-dates.yml").read_text(encoding="utf-8"))
    триггеры = wf.get("on") or wf.get(True)
    assert set(триггеры) == {"workflow_dispatch"}, "расписание — решение владельца"
    # Портал читают только замер и запись; plan и rollback — в своей группе:
    # в bitrix-portal ждёт ОДИН, и они сняли бы ожидающий прогон, читающий портал.
    for plan, rollback, группа in ((False, "", "bitrix-portal"), (True, "", "library-file-dates-db"),
                                   (False, "fd-1", "library-file-dates-db"),
                                   (True, "fd-1", "library-file-dates-db")):
        assert группа_очереди(wf, plan, rollback) == группа, (plan, rollback)
    assert wf["concurrency"]["cancel-in-progress"] is False
    входы = триггеры["workflow_dispatch"]["inputs"]
    assert входы["apply"]["default"] is False and входы["days"]["default"] == "60"
    assert "10" in входы["shards"]["options"] and "50" in входы["shards"]["options"]
    работа = wf["jobs"]["backfill"]
    assert работа["env"]["BITRIX_PARALLEL"] == "1"
    шаг = работа["steps"][-1]
    assert шаг["run"].strip() == "python library/backfill_file_dates.py"
    for имя, вход in (("DAYS", "days"), ("SHARDS", "shards"), ("SOURCES", "sources"),
                      ("LIMIT", "limit"), ("ROLLBACK", "rollback")):
        assert шаг["env"][имя] == "${{ inputs." + вход + " }}", имя
    миграции = (ROOT / ".github/workflows/zip-db.yml").read_text(encoding="utf-8")
    assert миграции.index("schema_junk.sql") < миграции.index("file_dates_schema.sql")
