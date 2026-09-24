"""Разбор вложений карточек запросов: берём чужое, не берём своё.

Корпус придуман (правило 18). Портал не опрашивается: bx_all подменяется.
"""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for каталог in ("library", "scripts"):
    sys.path.insert(0, str(ROOT / каталог))

psycopg2 = pytest.importorskip("psycopg2", reason="индексатор импортирует psycopg2")
_spec = importlib.util.spec_from_file_location("indexer", ROOT / "library" / "indexer.py")
ix = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ix)

ОФФЕР = "ufCrm18_1731179998"          # Offer from supplier
КП = "ufCrm18_1700698211875"          # КП поставщика


def карточки(monkeypatch, items):
    """Подмена чтения портала. Карточки читаются обходом ПО КЛЮЧУ (bx_all_by_id):
    на смещении СП-166 отдавал 7 750 записей вместо 21 865 — молча."""
    monkeypatch.setattr(ix, "bx_all_by_id", lambda *a, **k: items)


def test_берутся_только_файлы_поставщика(monkeypatch):
    """Наш «Request file» лежит в той же карточке и попасть в разбор не должен:
    разобрать его как котировку значит объявить прокотированным свой же запрос."""
    карточки(monkeypatch, [{
        "id": 101,
        ОФФЕР: [{"id": 1, "urlMachine": "https://portal.example.test/f/1"}],
        ix.ПОЛЕ_ЗАПРОСА: [{"id": 2, "urlMachine": "https://portal.example.test/f/2"}],
    }])
    refs = ix.collect_refs_rfq(30)
    assert len(refs) == 1
    assert refs[0]["field"] == ОФФЕР
    assert all(r["field"] != ix.ПОЛЕ_ЗАПРОСА for r in refs)


def test_происхождение_отличает_запрос_от_сделки(monkeypatch):
    """lib_files.deal_id хранит и сделки, и карточки запросов — различает origin."""
    карточки(monkeypatch, [{"id": 77, КП: [{"id": 9, "urlMachine": "https://x.test/9"}]}])
    refs = ix.collect_refs_rfq(30)
    assert refs[0]["origin"] == "поле запроса"
    assert refs[0]["deal"] == "77"


def test_файл_без_ссылки_пропускается(monkeypatch):
    """Без urlMachine скачать нечего: такую запись брать в работу бессмысленно."""
    карточки(monkeypatch, [{"id": 5, ОФФЕР: [{"id": 3}, {"id": 4, "urlMachine": "https://x.test/4"}]}])
    refs = ix.collect_refs_rfq(30)
    assert [r["fo"]["id"] for r in refs] == [4]


def test_одиночное_поле_и_список_читаются_одинаково(monkeypatch):
    """Bitrix отдаёт файловое поле то словарём, то списком словарей."""
    карточки(monkeypatch, [{"id": 1, ОФФЕР: {"id": 8, "urlMachine": "https://x.test/8"}},
                           {"id": 2, ОФФЕР: [{"id": 9, "urlMachine": "https://x.test/9"}]}])
    assert len(ix.collect_refs_rfq(30)) == 2


def test_источник_по_умолчанию_прежний():
    """Прогон без SOURCE обязан вести себя как раньше: сделки, а не запросы."""
    assert ix.SOURCE == "deals"


def test_список_полей_общий_с_замерами():
    """Два списка разошлись бы молча: разбирали бы одно, а считали другое."""
    import quote_coverage as qc
    assert ix.ПОЛЯ_КП is qc.ПОЛЯ_КП and ix.ПОЛЕ_ЗАПРОСА == qc.ПОЛЕ_ЗАПРОСА


def test_компания_поставщика_едет_вместе_с_файлом(monkeypatch):
    """Цена без поставщика — просто число.

    Компания известна в момент чтения карточки; отдельный проход за ней позже
    стоил бы второго сплошного чтения портала.
    """
    карточки(monkeypatch, [{"id": 11, ix.ПОЛЕ_ПОСТАВЩИКА: 4242,
                            ОФФЕР: [{"id": 1, "urlMachine": "https://x.test/1"}]}])
    refs = ix.collect_refs_rfq(30)
    assert refs[0]["company"] == "4242"


def test_карточка_без_поставщика_не_придумывает_его(monkeypatch):
    карточки(monkeypatch, [{"id": 12, ОФФЕР: [{"id": 2, "urlMachine": "https://x.test/2"}]}])
    assert ix.collect_refs_rfq(30)[0]["company"] is None


def test_поле_поставщика_общее_с_отзывчивостью():
    """Разойдись эти два имени — цена и отзывчивость считались бы по разным
    компаниям, и никто бы не заметил."""
    import supplier_responsiveness as sr
    assert ix.ПОЛЕ_ПОСТАВЩИКА == sr.ПОЛЕ_ПОСТАВЩИКА


def test_поле_поставщика_запрашивается_у_портала(monkeypatch):
    """Не попросишь в select — Bitrix его не отдаст, и связь потеряется молча."""
    видели = {}

    # **прочее: обход принимает границы диапазона (с_id, до_id), которыми
    # части делят обход портала между собой — см. indexer.диапазон_части.
    def подмена(method, params, **прочее):
        видели["select"] = params.get("select")
        return []

    monkeypatch.setattr(ix, "bx_all_by_id", подмена)
    ix.collect_refs_rfq(30)
    assert ix.ПОЛЕ_ПОСТАВЩИКА in видели["select"]


def test_карточки_читаются_обходом_по_ключу(monkeypatch):
    """Обход по смещению обрывался без ошибки: 7 750 из 21 865."""
    import inspect

    assert "bx_all_by_id" in inspect.getsource(ix.collect_refs_rfq)


def test_обход_по_ключу_идёт_за_последний_id(monkeypatch):
    """Ключ следующей страницы — id последней записи предыдущей."""
    страницы = [
        [{"id": i} for i in range(1, ix.СТРАНИЦА + 1)],
        [{"id": ix.СТРАНИЦА + 1}],
    ]
    спрошено = []

    # **прочее: обход принимает границы диапазона (с_id, до_id), которыми
    # части делят обход портала между собой — см. indexer.диапазон_части.
    def подмена(method, params, **прочее):
        спрошено.append(params["filter"][">id"])
        return {"result": {"items": страницы[len(спрошено) - 1]}}

    monkeypatch.setattr(ix, "bx", подмена)
    из_портала = ix.bx_all_by_id("crm.item.list", {"entityTypeId": 166})
    assert спрошено == [0, ix.СТРАНИЦА]
    assert len(из_портала) == ix.СТРАНИЦА + 1


def test_обрыв_на_смещении_не_молчит(monkeypatch, capsys):
    """Полная последняя страница без продолжения — признак обрыва, а не конца."""
    monkeypatch.setattr(ix, "bx", lambda m, p: {"result": [{"id": i} for i in range(ix.СТРАНИЦА)]})
    ix.bx_all("crm.deal.list", {})
    assert "оборвалось" in capsys.readouterr().out


def test_короткая_последняя_страница_молчит(monkeypatch, capsys):
    monkeypatch.setattr(ix, "bx", lambda m, p: {"result": [{"id": 1}]})
    ix.bx_all("crm.deal.list", {})
    assert "оборвалось" not in capsys.readouterr().out


def test_фильтр_даты_не_уходит_на_портал(monkeypatch):
    """Серверный «>=createdTime» съедал две трети карточек.

    Замер 21.09.2026: с фильтром за 7 300 дней приходило 7 150 карточек, а
    замеры котировок, читающие ту же сущность без фильтра, видят 21 865.
    Двадцатилетнее окно отсечь ничего не может — отсекал фильтр.
    """
    видели = {}

    # **прочее: обход принимает границы диапазона (с_id, до_id), которыми
    # части делят обход портала между собой — см. indexer.диапазон_части.
    def подмена(method, params, **прочее):
        видели["params"] = params
        return []

    monkeypatch.setattr(ix, "bx_all_by_id", подмена)
    ix.collect_refs_rfq(7300)
    assert "filter" not in видели["params"], "фильтр по дате снова ушёл на портал"
    assert "createdTime" in видели["params"]["select"]


def test_окно_считается_у_себя(monkeypatch):
    """Свежая карточка проходит, древняя — нет."""
    from datetime import datetime, timedelta, timezone

    сейчас = datetime.now(timezone.utc)
    свежая = (сейчас - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00+03:00")
    древняя = (сейчас - timedelta(days=900)).strftime("%Y-%m-%dT00:00:00+03:00")
    карточки(monkeypatch, [
        {"id": 1, "createdTime": свежая, ОФФЕР: [{"id": 1, "urlMachine": "https://x.test/1"}]},
        {"id": 2, "createdTime": древняя, ОФФЕР: [{"id": 2, "urlMachine": "https://x.test/2"}]},
    ])
    assert [r["deal"] for r in ix.collect_refs_rfq(30)] == ["1"]


def test_карточка_без_даты_не_теряется(monkeypatch):
    """Недоказанное «старая» дешевле потерянной цены."""
    карточки(monkeypatch, [
        {"id": 3, ОФФЕР: [{"id": 3, "urlMachine": "https://x.test/3"}]},
        {"id": 4, "createdTime": "", ОФФЕР: [{"id": 4, "urlMachine": "https://x.test/4"}]},
    ])
    assert [r["deal"] for r in ix.collect_refs_rfq(30)] == ["3", "4"]


def test_окно_ноль_берёт_всё(monkeypatch):
    карточки(monkeypatch, [
        {"id": 5, "createdTime": "2009-01-01T00:00:00+03:00",
         ОФФЕР: [{"id": 5, "urlMachine": "https://x.test/5"}]},
    ])
    assert len(ix.collect_refs_rfq(0)) == 1


# ── чтение портала не имеет права молчать ────────────────────────────────────
def test_чтение_портала_идёт_общим_клиентом():
    """Прежний bx() делал четыре МГНОВЕННЫХ повтора и возвращал пустой словарь.

    Обход принимал пустоту за конец данных и завершался как успешный: чтение
    СП-166 обрывалось то на 7 150, то на 7 750, то на 8 250 записях из 21 865 —
    каждый раз в другом месте, всегда «успешно». Четыре повтора подряд без паузы
    против ограничения частоты бесполезны.
    """
    import inspect

    исходник = inspect.getsource(ix.bx)
    assert "call_envelope" in исходник, "bx должен звать общий клиент"
    assert "requests.post" not in исходник, "своя реализация запроса вернулась"


def test_исчерпанные_повторы_роняют_прогон(monkeypatch):
    """Неполное чтение, выданное за полное, дороже упавшего прогона."""
    from bitrix_client import BitrixError

    class Падающий:
        def call_envelope(self, method, params):
            raise BitrixError(f"{method}: не удалось выполнить за 6 попыток")

    monkeypatch.setattr(ix, "_КЛИЕНТ", Падающий())
    with pytest.raises(BitrixError):
        ix.bx_all_by_id("crm.item.list", {"entityTypeId": 166})


def test_пустая_страница_кончает_чтение_только_без_ошибки(monkeypatch):
    """Пустой ответ — законный конец ТОЛЬКО когда он пришёл без ошибки."""
    страницы = [{"result": {"items": [{"id": 1}]}}, {"result": {"items": []}}]
    monkeypatch.setattr(ix, "bx", lambda m, p: страницы.pop(0))
    assert len(ix.bx_all_by_id("crm.item.list", {})) == 1


def test_частота_запросов_под_лимитом_портала(monkeypatch):
    """Пауза клиента разбора берётся из ОБЩЕГО бюджета портала, а не своим числом.

    Прежние 0,5 с были на процесс: двенадцать частей давали 24 запроса в секунду
    при утечке ведра портала 2 в секунду (прогон 21.09.2026 10:43 упал всеми
    двенадцатью частями). Бюджет 1,5 в секунду на двенадцать частей — по 8 с
    между запросами каждой, вместе те же полтора.
    """
    monkeypatch.setenv("BITRIX_RPS", "1.5")
    monkeypatch.setenv("BITRIX_PARALLEL", "12")
    monkeypatch.setattr(ix, "_КЛИЕНТ", None)
    к = ix.клиент()
    assert abs(к.min_interval - 8.0) < 1e-9
    assert 12 / к.min_interval <= 2.0, "части вместе превышают утечку ведра портала"
    monkeypatch.setattr(ix, "_КЛИЕНТ", None)


def test_ночной_разбор_котировок_идёт_одной_частью():
    """Каждая часть вычитывает ВЕСЬ список карточек, чтобы выбрать свою долю
    файлов: 21 865 карточек — 438 запросов на обход, двенадцать частей — 5 256."""
    import pathlib

    import yaml

    корень = pathlib.Path(__file__).resolve().parents[1]
    прогон = yaml.safe_load(
        (корень / ".github" / "workflows" / "suppliers-quotes.yml").read_text(encoding="utf-8"))
    работа = прогон["jobs"]["quotes"]
    assert работа["strategy"]["matrix"]["shard"] == [0]
    assert работа["steps"][-1]["env"]["SHARDS"] == "1"


# ── бренд: разрез «чей это» ──────────────────────────────────────────────────
def test_бренды_запрашиваются_у_портала(monkeypatch):
    """Не попросишь в select — Битрикс не отдаст, и разреза по бренду не будет."""
    видели = {}
    monkeypatch.setattr(ix, "bx_all_by_id",
                        lambda m, p, **прочее: (видели.update(p), [])[1])
    ix.collect_refs_rfq(30)
    assert ix.ПОЛЕ_БРЕНДОВ in видели["select"]


@pytest.mark.parametrize("значение,ждём", [
    ([1, 2, 3], "1,2,3"),
    (["CO_7", "CO_9"], "7,9"),
    ("CO_5", "5"),
    ([5, 5, 7], "5,7"),          # повтор не удваивается
    ([], ""),
    (None, ""),
    (["", 0, "0"], ""),
])
def test_ключи_брендов_берут_все_а_не_первый(значение, ждём):
    """Первый бренд в поле — не «главный», а просто первый."""
    assert ix.ключи_брендов(значение) == ждём


def test_бренды_доезжают_до_ссылки(monkeypatch):
    карточки(monkeypatch, [{"id": 21, ix.ПОЛЕ_БРЕНДОВ: ["CO_11", "CO_12"],
                            ОФФЕР: [{"id": 1, "urlMachine": "https://x.test/1"}]}])
    assert ix.collect_refs_rfq(30)[0]["brands"] == "11,12"


def test_карточка_без_брендов_не_выдумывает_их(monkeypatch):
    карточки(monkeypatch, [{"id": 22, ОФФЕР: [{"id": 2, "urlMachine": "https://x.test/2"}]}])
    assert ix.collect_refs_rfq(30)[0]["brands"] is None
