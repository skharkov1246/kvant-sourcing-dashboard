"""Папку ставит система, содержимое сверяет: проводка в library/indexer.py.

ЗАЧЕМ ЭТО ПРОВЕРЯТЬ. Замечание владельца 23.09.2026: «Мы отправляем запросы от
разных компаний, а в системе запросов поставщикам уже есть вся избыточная
информация». Правка держится на пяти обещаниях, и каждое нарушается молча:

1. ПАПКУ РЕШАЕТ СИСТЕМА (сущность и код поля), даже если файл не скачался.
2. СОДЕРЖИМОЕ ТОЛЬКО СВЕРЯЕТ: уверенный спор пишется в doc_kind_why словом
   «расхождение», папка остаётся системной; сверке не подсказывают поле.
3. НАШИ КОМПАНИИ — ИЗ БИТРИКСА, один запрос на процесс; сбой — пусто и одно
   предупреждение; в журнал — только число (правило 17).
4. SELECT КАРТОЧКИ НЕСЁТ «Request file» И mycompanyId: Битрикс отдаёт только
   запрошенное, и до правки счётчик нашего «Request file» был нулём всегда.
   Подделка портала здесь ЧЕСТНАЯ — отдаёт только выбранные поля; прежняя
   отдавала всё, и потому ошибку не ловила.
5. КОЛОНКА our_company доезжает обеими записями (правило 14) и пустым значением
   не затирает записанное: сбой чтения наших компаний даёт пусто у каждой
   карточки прогона.
6. НЕТ КОЛОНКИ-СВЕДЕНИЯ — РАЗБОР ИДЁТ БЕЗ НЕЁ. Миграцию применяют руками, а
   ночной разбор котировок идёт по расписанию: остановка на колонке our_company
   красила бы его каждую ночь до миграции. Останавливает только обязательная
   колонка. Проверяется поведением — подделкой базы и сгенерированным запросом,
   а не написанием кода.

Корпус придуман (правило 18): компании, изделия, номера вымышлены.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from library import indexer as ix

ROOT = Path(__file__).resolve().parents[1]
ОФФЕР = "ufCrm18_1731179998"               # Offer from supplier (СП-166)
ЗАПРОС = "ufCrm18_1727423346"              # наш Request file (СП-166)

НАШИ = {"1": "ООО «КВАНТ»", "7": "ООО «Кордален»"}

НАШЕ_ТКП = """ООО «Кордален»
ИНН 0000000000, г. Условный, тел. 0-000-000-00-00
Исх. № 15 от 21.09.2026
Генеральному директору АО «Условная горная компания»
Коммерческое предложение № 15
ООО «Кордален» предлагает поставку насоса ВЫДУМ НЦ-50-200:
Насос ВЫДУМ НЦ-50-200 2 шт 450 000,00 900 000,00
Итого: 900 000,00 руб., в т.ч. НДС 22 %
Срок поставки 12 недель. Оплата 50 % предоплата.
Предложение действительно 30 дней.
Генеральный директор ООО «Кордален» ________ Петров П.П."""

КП_ПОСТАВЩИКА = """ООО «Техснаб-Пример»
ИНН 0000000000, КПП 000000000, г. Условный, ул. Примерная, д. 1
Исх. № 113 от 22.09.2026
Кому: ООО «Кордален»
Коммерческое предложение № 113
На Ваш запрос № 77 предлагаем к поставке:
1 Насос центробежный ВЫДУМ НЦ-50-200 2 шт 450 000,00 900 000,00
Итого: 900 000,00 руб., в т.ч. НДС 22 %
Срок поставки: 8–10 недель. Оплата 50 % предоплата.
Срок действия предложения — 30 календарных дней.
Генеральный директор ООО «Техснаб-Пример» ____________ Сидоров С.С."""


@pytest.fixture
def наши(monkeypatch):
    """Список наших компаний без портала: как будто Битрикс его уже отдал."""
    monkeypatch.setattr(ix, "_НАШИ", dict(НАШИ))
    monkeypatch.setattr(ix, "_НАШИ_ИМЕНА", ix.doc_kind.с_транслитом(НАШИ.values()))


def _ссылка(поле, название, **ещё):
    return {"fo": {"id": "5"}, "deal": "11", "origin": "поле запроса", "field": поле,
            "field_title": название, **ещё}


# ── 1. папку решает система ──────────────────────────────────────────────────

def test_папка_системы_ставится_до_закачки(monkeypatch, наши):
    """Не скачался — папка всё равно есть: ей содержимое не нужно."""
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: None)
    rec, items = ix.handle(_ссылка(ЗАПРОС, "Request file", our_company="ООО «Кордален»"))
    assert rec["status"] == "не скачался" and items == []
    assert rec["doc_kind"] == ix.doc_folder.НАШ_ЗАПРОС
    assert rec["doc_kind_conf"] == 1.0 and rec["doc_kind_why"].startswith("поле: код поля")
    assert rec["our_company"] == "ООО «Кордален»"


# ── 2. содержимое только сверяет ─────────────────────────────────────────────

def test_расхождение_пишется_а_папка_остаётся_системной(monkeypatch, наши):
    """Наше ТКП второй нашей компании в поле «Offer from supplier»: система
    говорит «предложение поставщика», содержимое — «наше предложение»."""
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: b"x" * 100)
    monkeypatch.setattr(ix, "читать", lambda b, п, rec=None: ([], НАШЕ_ТКП, ""))
    rec, _ = ix.handle(_ссылка(ОФФЕР, "Offer from supplier"))
    assert rec["doc_kind"] == ix.doc_folder.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА
    assert rec["doc_kind_conf"] == 1.0
    assert rec["расхождение"] and rec["папка_содержимого"] == ix.doc_kind.НАШЕ_ПРЕДЛОЖЕНИЕ
    почему = rec["doc_kind_why"]
    assert re.match(r"расхождение: поле → предложение поставщика нам, "
                    r"содержимое → наше предложение заказчику [01]\.\d\d \|", почему), почему
    assert len(почему) <= 300
    # Обе оси целиком: запись режет на 300 знаках, а автор — в конце «почему».
    assert "автор мы" in почему and not почему.endswith("«пред"), почему
    # Только подписи признаков, ни слова из документа (правило 17).
    for кусок in ("Кордален", "ВЫДУМ", "Петров", "Условн", "900"):
        assert кусок not in почему, кусок


def test_согласие_не_расхождение(наши):
    rec = {"origin": "поле запроса", "field": ОФФЕР}
    ix.определить_папку(rec, КП_ПОСТАВЩИКА, [])
    assert rec["doc_kind"] == rec["папка_содержимого"] == ix.doc_folder.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА
    assert not rec["расхождение"] and "согласно" in rec["doc_kind_why"]


def test_содержимое_не_назначает_папку_там_где_система_молчит(наши):
    """Поле не опознано — папка «не определено», вердикт содержимого — подсказка."""
    rec = {"origin": "поле сделки", "field": "ufCrm_0000000001", "field_title": "Новое поле"}
    ix.определить_папку(rec, КП_ПОСТАВЩИКА, [])
    assert rec["doc_kind"] == ix.doc_folder.НЕ_ОПРЕДЕЛЕНО and rec["doc_kind_conf"] == 0.0
    assert not rec["расхождение"]
    assert "подсказка содержимого → предложение поставщика нам" in rec["doc_kind_why"]


def test_сверке_не_подсказывают_поле_но_дают_всех_наших(monkeypatch, наши):
    """Сверка, в которую подмешан ответ системы, свою независимость теряет
    (правило 1). А «мы» — это все наши компании, а не одна."""
    видели = {}

    def ловушка(т, с, сторона, **k):
        видели.update(сторона=сторона, **k)
        return ix.doc_kind.НЕ_ОПРЕДЕЛЕНО, 0.3, "ниже порога"

    monkeypatch.setattr(ix.doc_kind, "вид_документа", ловушка)
    ix.определить_папку({"origin": "поле запроса", "field": ОФФЕР, "side": "поставщик"},
                        "текст", [])
    assert видели["сторона"] is None
    assert {"квант", "kvant", "кордален", "kordalen"} <= set(видели["наши"])


def test_без_наших_компаний_сверка_не_выдумывает_расхождений(monkeypatch):
    """Список не прочитан — автор не определён, и спорить о «мы / не мы» нечем."""
    monkeypatch.setattr(ix, "_НАШИ", {})
    monkeypatch.setattr(ix, "_НАШИ_ИМЕНА", ())
    rec = {"origin": "поле запроса", "field": ОФФЕР}
    ix.определить_папку(rec, НАШЕ_ТКП, [])
    assert rec["doc_kind"] == ix.doc_folder.ПРЕДЛОЖЕНИЕ_ПОСТАВЩИКА
    assert not rec["расхождение"] and rec["папка_содержимого"] == ix.doc_kind.НЕ_ОПРЕДЕЛЕНО


# ── 3. наши компании из Битрикса ─────────────────────────────────────────────

@pytest.fixture
def без_кеша(monkeypatch):
    monkeypatch.setattr(ix, "_НАШИ", None)
    monkeypatch.setattr(ix, "_НАШИ_ИМЕНА", ())
    monkeypatch.setattr(ix, "BASE", "https://portal.example.test/rest/1/x")


def test_наши_компании_одним_запросом_и_только_число(monkeypatch, capsys, без_кеша):
    звали = []

    def портал(method, params):
        звали.append((method, params))
        return [{"ID": "1", "TITLE": "ООО «Кордален»"}, {"ID": 7, "TITLE": "Mirvelta Trading LLC"}]

    monkeypatch.setattr(ix, "bx_all", портал)
    assert ix.наши_компании() == {"1": "ООО «Кордален»", "7": "Mirvelta Trading LLC"}
    ix.наши_компании()
    ix.наши_имена()
    assert len(звали) == 1, "список наших компаний читается один раз на процесс"
    method, params = звали[0]
    assert method == "crm.company.list" and params["filter"] == {"IS_MY_COMPANY": "Y"}
    assert {"ID", "TITLE"} <= set(params["select"])
    out = capsys.readouterr().out
    assert "наших компаний (IS_MY_COMPANY=Y): 2" in out
    for имя in ("Кордален", "Mirvelta", "кордален"):
        assert имя not in out, "в журнал — только число (правило 17)"
    assert {"кордален", "kordalen", "mirvelta trading"} <= set(ix.наши_имена())


def test_сбой_списка_наших_пусто_и_одно_предупреждение(monkeypatch, capsys, без_кеша):
    звали = []

    def падает(method, params):
        звали.append(method)
        raise RuntimeError("секрет-из-ответа-портала")

    monkeypatch.setattr(ix, "bx_all", падает)
    assert ix.наши_компании() == {} and ix.наши_имена() == ()
    assert ix.наши_компании() == {}
    out = capsys.readouterr().out
    assert звали == ["crm.company.list"], "сбой не повторяется на каждом файле"
    assert out.count("::warning::") == 1 and "секрет-из-ответа-портала" not in out
    # Никакого «КВАНТ по умолчанию»: наша компания карточки не выдумывается.
    assert ix.наша_компания(1) is None


def test_без_вебхука_портал_не_спрашивается(monkeypatch, capsys, без_кеша):
    monkeypatch.setattr(ix, "BASE", "")
    monkeypatch.setattr(ix, "bx_all", lambda *a, **k: pytest.fail("портал спрошен без вебхука"))
    assert ix.наши_компании() == {}
    assert capsys.readouterr().out.count("::warning::") == 1


def test_наша_компания_по_mycompanyid(наши):
    assert ix.наша_компания(7) == "ООО «Кордален»"
    assert ix.наша_компания("1") == "ООО «КВАНТ»"
    assert ix.наша_компания(99) is None           # не из списка наших
    for пусто in (None, 0, "0", ""):
        assert ix.наша_компания(пусто) is None


# ── 4. select карточки и сделки ──────────────────────────────────────────────

def _честный_портал(карточки):
    """Отдаёт ТОЛЬКО выбранные поля — как настоящий Битрикс."""
    def портал(method, params, **прочее):
        выбрано = set(params["select"])
        return [{к: v for к, v in к_.items() if к in выбрано} for к_ in карточки]
    return портал


def test_карточка_несёт_наш_запрос_и_нашу_компанию(monkeypatch, capsys, наши):
    карточки = [
        {"id": 31, "mycompanyId": 7, ix.ПОЛЕ_ПОСТАВЩИКА: 4242,
         ЗАПРОС: [{"id": 90, "urlMachine": "https://x.test/90"}],
         ОФФЕР: [{"id": 91, "urlMachine": "https://x.test/91"}]},
        {"id": 32, "mycompanyId": 99,
         ОФФЕР: [{"id": 92, "urlMachine": "https://x.test/92"},
                 {"id": 90, "urlMachine": "https://x.test/90"}]},
    ]
    monkeypatch.setattr(ix, "bx_all_by_id", _честный_портал(карточки))
    refs = ix.collect_refs_rfq(0)
    out = capsys.readouterr().out
    # Счётчик нашего «Request file» больше не ноль: поле запрошено.
    assert "карточек с нашим «Request file» (не берём): 1" in out, out
    assert "карточек с нашей компанией (mycompanyId): 1 из 2" in out, out
    assert "mycompanyId вне списка наших: 1" in out, out
    # Наш запрос по-прежнему НЕ разбирается — только считается.
    assert ЗАПРОС not in {r["field"] for r in refs}
    по_файлу = {r["fo"]["id"]: r for r in refs}
    assert по_файлу[91]["our_company"] == "ООО «Кордален»"
    assert по_файлу[92]["our_company"] is None
    assert all(r["field"] == ОФФЕР for r in refs)


def test_сделка_несёт_нашу_компанию_тем_же_запросом(monkeypatch, capsys, наши):
    сделки = [
        {"id": 501, "mycompanyId": 1,
         "ufCrm_1585568303498": [{"id": 71, "urlMachine": "https://x.test/71"}]},
        {"id": 502, "mycompanyId": 0,
         "ufCrm_1633502831": {"id": 72, "urlMachine": "https://x.test/72"}},
    ]
    списки = []

    def bx(method, params):
        if method == "crm.item.fields":
            return {"result": {"fields": {
                "ufCrm_1585568303498": {"type": "file", "title": "Offer from us"},
                "ufCrm_1633502831": {"type": "file", "title": "Техническая спецификация"},
                "title": {"type": "string", "title": "Название"}}}}
        if method == "crm.deal.list":
            # список сделок читается по ключу (bx_all_by_id, ключ «ID»)
            после = int(params["filter"].get(">ID", 0))
            return {"result": [д for д in ({"ID": "501"}, {"ID": "502"}) if int(д["ID"]) > после]}
        assert method == "crm.item.list", method
        списки.append(params)
        выбрано = set(params["select"])
        return {"result": {"items": [{к: v for к, v in с.items() if к in выбрано}
                                     for с in сделки if с["id"] in params["filter"]["@id"]]}}

    monkeypatch.setattr(ix, "bx", bx)
    refs = ix.collect_refs(30)
    assert len(списки) == 1, "наша компания сделки — тем же запросом, без лишних обращений"
    по_файлу = {r["fo"]["id"]: r for r in refs}
    assert по_файлу[71]["our_company"] == "ООО «КВАНТ»"
    assert по_файлу[72]["our_company"] is None
    assert "сделок с нашей компанией (mycompanyId): 1 из 2" in capsys.readouterr().out


# ── 5. колонка our_company обеими записями ───────────────────────────────────

def _код(путь: str) -> str:
    return (ROOT / путь).read_text(encoding="utf-8")


@pytest.fixture
def запись(monkeypatch):
    """Запись файла такой, какой её отдаёт handle() (файл не скачался)."""
    monkeypatch.setattr(ix, "download", lambda fo, rec=None: None)
    rec, _ = ix.handle(_ссылка(ОФФЕР, "Offer from supplier", our_company="ООО «Кордален»"))
    return rec


def _колонки_вставки(запрос: str) -> tuple[str, ...]:
    m = re.search(r"insert into lib_files\s*\(([^)]*)\)", запрос)
    return tuple(к.strip() for к in m.group(1).split(",") if к.strip())


def _обновляемые(запрос: str) -> set[str]:
    return set(re.findall(r"(\w+) = (?:excluded\.|coalesce\((?:excluded|lib_files)\.)", запрос))


def _есть(запрос: str, колонка: str) -> bool:
    return re.search(rf"(?<!\w){колонка}(?!\w)", запрос) is not None


def test_значения_записи_ровно_по_колонкам_вставки(запись):
    """Значения берутся из одного места на все колонки: колонка без значения и
    значение без колонки невозможны по устройству."""
    assert tuple(ix.строка_файла(запись)) == ix.КОЛОНКИ_ВСТАВКИ
    assert ix.КОЛОНКИ_ВСТАВКИ[0] == "file_id", "буфер отсеивает повторы по r[0]"
    assert set(ix.КОЛОНКИ_ОБЯЗАТЕЛЬНЫЕ).isdisjoint(ix.КОЛОНКИ_СВЕДЕНИЙ)
    assert "our_company" in ix.КОЛОНКИ_СВЕДЕНИЙ


def test_вставка_по_всем_колонкам_несёт_нашу_компанию_и_не_затирает_её(запись, monkeypatch):
    monkeypatch.setattr(ix, "_НАШИ_СБОЙ", True)
    запрос, шаблон = ix.вставка_файлов(ix.КОЛОНКИ_ВСТАВКИ)
    assert _колонки_вставки(запрос) == ix.КОЛОНКИ_ВСТАВКИ
    строка = ix.кортеж_файла(запись, ix.КОЛОНКИ_ВСТАВКИ)
    assert шаблон.count("%s") == len(строка) == len(ix.КОЛОНКИ_ВСТАВКИ)
    assert строка[ix.КОЛОНКИ_ВСТАВКИ.index("our_company")] == "ООО «Кордален»"
    # Пустая наша компания при сбое чтения списка записанную не стирает…
    assert "our_company = coalesce(excluded.our_company, lib_files.our_company)" in запрос
    # …а без сбоя пустое значение — факт (компанию в карточке сняли), и оно пишется.
    monkeypatch.setattr(ix, "_НАШИ_СБОЙ", False)
    без_сбоя, _ = ix.вставка_файлов(ix.КОЛОНКИ_ВСТАВКИ)
    assert "our_company = excluded.our_company" in без_сбоя
    assert "coalesce(excluded." not in без_сбоя
    # Сведения о файле: записанное остаётся, пустое дополняется повторной закачкой.
    for к in ("kind", "size_bytes", "sha256"):
        assert f"{к} = coalesce(lib_files.{к}, excluded.{к})" in без_сбоя, к
    # Ключ и происхождение вложения при конфликте не переписываются, остальное — да.
    assert _обновляемые(запрос) == set(ix.КОЛОНКИ_ВСТАВКИ) - ix.НЕ_ОБНОВЛЯТЬ
    assert not {"file_id", "deal_id", "origin", "field"} & _обновляемые(запрос)
    assert запрос.rstrip().endswith("processed_at = now()")


@pytest.mark.parametrize("нет", [("our_company",), ("read_chain", "our_company"),
                                 ix.КОЛОНКИ_СВЕДЕНИЙ])
def test_без_колонок_сведений_вставка_их_не_называет(запись, нет):
    """База отстала от схемы: запрос строится только из того, что в ней есть, —
    и мест под значения ровно столько, сколько значений в кортеже."""
    колонки, нет_обяз, нет_свед = ix.колонки_записи(set(ix.КОЛОНКИ_ВСТАВКИ) - set(нет))
    assert нет_обяз == [] and нет_свед == [к for к in ix.КОЛОНКИ_СВЕДЕНИЙ if к in нет]
    запрос, шаблон = ix.вставка_файлов(колонки)
    for к in нет:
        assert not _есть(запрос, к), к
    assert _колонки_вставки(запрос) == колонки
    assert шаблон.count("%s") == len(ix.кортеж_файла(запись, колонки)) == len(колонки)
    assert len(колонки) == len(ix.КОЛОНКИ_ВСТАВКИ) - len(нет)


class _База:
    """Подделка базы: знает, какие колонки lib_files в ней есть."""

    def __init__(self, есть):
        self.есть, self.запросы = set(есть), []

    def execute(self, sql, params=None):
        self.запросы.append((sql, params))
        self._последний = (sql, params)

    def fetchall(self):
        sql, params = self._последний
        if "information_schema.columns" in sql:
            return [(к,) for к in params[0] if к in self.есть]
        if "from lib_files f" in sql:                   # кандидаты переразбора
            return list(self.кандидаты)
        return []

    кандидаты: tuple = ()

    def fetchone(self):
        return (1,)

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_нет_сведения_одно_предупреждение_и_разбор_идёт(capsys):
    база = _База(set(ix.КОЛОНКИ_ВСТАВКИ) - {"our_company", "read_chain"})
    колонки = ix.проверить_колонки(база)
    assert колонки == tuple(к for к in ix.КОЛОНКИ_ВСТАВКИ if к not in ("our_company", "read_chain"))
    out, err = capsys.readouterr()
    assert out.count("::warning::") == 1 and err == ""
    assert "our_company" in out and "read_chain" in out
    # Запрос колонок — один на прогон, по всему списку вставки.
    assert len(база.запросы) == 1 and set(база.запросы[0][1][0]) == set(ix.КОЛОНКИ_ВСТАВКИ)


def test_нет_обязательной_разбор_останавливается(capsys):
    assert ix.проверить_колонки(_База(set(ix.КОЛОНКИ_ВСТАВКИ) - {"status"})) is None
    out, err = capsys.readouterr()
    assert "status" in err and "::warning::" not in out


def test_все_колонки_на_месте_без_предупреждений(capsys):
    assert ix.проверить_колонки(_База(ix.КОЛОНКИ_ВСТАВКИ)) == ix.КОЛОНКИ_ВСТАВКИ
    assert capsys.readouterr() == ("", "")


def _прогон(monkeypatch, есть, запись_файла):
    """main() разбора целиком: подделки базы и портала, запись перехватывается."""
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal.example.test/rest/1/x")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example.test/db")
    monkeypatch.setattr(ix, "connect", lambda *a, **k: _База(есть))
    обход = []

    def ссылки(*a, **k):
        обход.append(a)
        return [_ссылка(ОФФЕР, "Offer from supplier")]

    monkeypatch.setattr(ix, "collect_refs", ссылки)
    monkeypatch.setattr(ix, "collect_refs_rfq", ссылки)
    monkeypatch.setattr(ix, "handle", lambda ref: (dict(запись_файла), []))
    вставки = []

    def execute_values(cur, sql, rows, template=None, page_size=100):
        if "lib_files" in sql:
            вставки.append((sql, list(rows), template))

    monkeypatch.setattr(ix.psycopg2.extras, "execute_values", execute_values)
    return ix.main(), обход, вставки


def test_ночной_разбор_без_колонки_our_company_пишет_остальное(monkeypatch, capsys, запись):
    """Прежде: «нет колонок: our_company» и код 2 каждую ночь до ручной миграции."""
    код, обход, вставки = _прогон(monkeypatch, set(ix.КОЛОНКИ_ВСТАВКИ) - {"our_company"},
                                  запись)
    assert код == 0 and обход, "разбор остановился"
    (sql, строки, шаблон), = вставки
    assert not _есть(sql, "our_company")
    assert all(len(r) == шаблон.count("%s") == len(_колонки_вставки(sql)) for r in строки)
    assert строки[0][0] == запись["file_id"]
    assert capsys.readouterr().out.count("::warning::") == 1


def test_без_обязательной_колонки_портал_не_обходится(monkeypatch, capsys, запись):
    код, обход, вставки = _прогон(monkeypatch, set(ix.КОЛОНКИ_ВСТАВКИ) - {"status"}, запись)
    assert код == 2 and обход == [] and вставки == []


def test_переразбор_пишет_нашу_компанию_не_затирая_пустым(запись, monkeypatch):
    import library.reparse as r
    monkeypatch.setattr(r.indexer, "_НАШИ_СБОЙ", True)
    колонки = r.КОЛОНКИ_ЗАПИСИ
    sql = r.правка_файла(колонки)
    значения = r.значения_правки(запись, колонки)
    assert "our_company = coalesce(%s, our_company)" in sql
    assert sql.count("%s") == len(значения) == len(колонки) + 1
    monkeypatch.setattr(r.indexer, "_НАШИ_СБОЙ", False)
    assert "coalesce" not in r.правка_файла(колонки)
    assert значения[колонки.index("our_company")] == "ООО «Кордален»"
    assert значения[-1] == запись["file_id"] and sql.rstrip().endswith("where file_id = %s")


def test_переразбор_без_колонки_сведения_её_не_называет(запись):
    import library.reparse as r
    колонки, нет_обяз, нет_свед = r.indexer.колонки_записи(
        set(r.КОЛОНКИ_ЗАПИСИ) - {"our_company"}, r.ОБЯЗАТЕЛЬНЫЕ_ЗАПИСИ, r.СВЕДЕНИЯ_ЗАПИСИ)
    assert (нет_обяз, нет_свед) == ([], ["our_company"])
    sql = r.правка_файла(колонки)
    assert not _есть(sql, "our_company")
    assert sql.count("%s") == len(r.значения_правки(запись, колонки)) == len(колонки) + 1


def _переразбор(monkeypatch, есть, запись_в_базу: bool, запись_файла=None):
    """main() переразбора целиком на подделке базы. С запись_файла в базе есть
    один кандидат, и переразбор доходит до UPDATE; все базы прогона — в списке."""
    import library.reparse as r
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://portal.example.test/rest/1/x")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://example.test/db")
    monkeypatch.setattr(r, "APPLY", запись_в_базу)
    базы = []

    def база(*a, **k):
        б = _База(есть)
        if запись_файла is not None:
            б.кандидаты = ((запись_файла["file_id"], "прочее", 0),)
        базы.append(б)
        return б

    monkeypatch.setattr(r.indexer, "connect", база)
    monkeypatch.setattr(r.indexer, "collect_refs", lambda *a, **k: [_ссылка(ОФФЕР, "x")])
    monkeypatch.setattr(r.indexer, "collect_refs_rfq", lambda *a, **k: [_ссылка(ОФФЕР, "x")])
    monkeypatch.setattr(r.indexer, "handle", lambda ref: (dict(запись_файла or {}), []))
    код = r.main()
    правки = [(sql, p) for б in базы for sql, p in б.запросы if "update lib_files" in sql]
    return код, правки


@pytest.mark.parametrize("запись_в_базу", [True, False])
def test_переразбор_без_сведения_не_останавливается(monkeypatch, capsys, запись_в_базу):
    """Нет our_company — запись идёт без неё (кандидатов нет — «нечего»), код 0."""
    import library.reparse as r
    код, _ = _переразбор(monkeypatch, set(r.КОЛОНКИ_ЗАПИСИ) - {"our_company"}, запись_в_базу)
    out = capsys.readouterr().out
    assert код == 0 and "нечего переразбирать" in out and out.count("::warning::") == 1


def test_переразбор_без_сведения_пишет_файл_без_неё(monkeypatch, capsys, запись):
    """С кандидатом: UPDATE доходит до базы и не называет недостающую колонку."""
    import library.reparse as r
    код, правки = _переразбор(monkeypatch, set(r.КОЛОНКИ_ЗАПИСИ) - {"our_company"}, True,
                              запись)
    assert код == 0
    (sql, значения), = правки
    assert not _есть(sql, "our_company") and _есть(sql, "doc_kind")
    assert sql.count("%s") == len(значения) and значения[-1] == запись["file_id"]
    assert "записано файлов: 1" in capsys.readouterr().out


def test_переразбор_без_обязательной_останавливает_только_запись(monkeypatch, capsys):
    """Холостой прогон не останавливается никогда: он ничего не пишет."""
    import library.reparse as r
    есть = set(r.КОЛОНКИ_ЗАПИСИ) - {"status"}
    assert _переразбор(monkeypatch, есть, True)[0] == 2
    assert "status" in capsys.readouterr().err
    assert _переразбор(monkeypatch, есть, False)[0] == 0


def test_схема_добавляет_колонку_нашей_компании():
    схема = re.sub(r"(?m)--.*$", "", _код("library/supabase/schema_junk.sql"))
    assert re.search(r"alter table lib_files add column if not exists our_company\s+text", схема)


def test_распознавание_не_трогает_папку_и_нашу_компанию():
    """Вставка распознавания не называет эти колонки — значит, при конфликте не
    затирает того, что записал разбор."""
    код = _код("library/ocr.py")
    i = код.index("insert into lib_files")
    вставка = код[i:код.index("\"\"\"", i)]
    assert "our_company" not in вставка and "doc_kind" not in вставка
