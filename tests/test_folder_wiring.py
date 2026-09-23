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
5. КОЛОНКА our_company доезжает обеими записями (правило 14).

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
        assert method == "crm.item.list", method
        списки.append(params)
        выбрано = set(params["select"])
        return {"result": {"items": [{к: v for к, v in с.items() if к in выбрано}
                                     for с in сделки if с["id"] in params["filter"]["@id"]]}}

    def bx_all(method, params):
        assert method == "crm.deal.list", method
        return [{"ID": "501"}, {"ID": "502"}]

    monkeypatch.setattr(ix, "bx", bx)
    monkeypatch.setattr(ix, "bx_all", bx_all)
    refs = ix.collect_refs(30)
    assert len(списки) == 1, "наша компания сделки — тем же запросом, без лишних обращений"
    по_файлу = {r["fo"]["id"]: r for r in refs}
    assert по_файлу[71]["our_company"] == "ООО «КВАНТ»"
    assert по_файлу[72]["our_company"] is None
    assert "сделок с нашей компанией (mycompanyId): 1 из 2" in capsys.readouterr().out


# ── 5. колонка our_company обеими записями ───────────────────────────────────

def _код(путь: str) -> str:
    return (ROOT / путь).read_text(encoding="utf-8")


def test_вставка_называет_ровно_колонки_проверки():
    """Проверка колонок до обхода портала перечисляет ровно то, что пишет вставка."""
    m = re.search(r"insert into lib_files\s*\(([^)]*)\)", _код("library/indexer.py"))
    в_запросе = tuple(к.strip() for к in m.group(1).split(",") if к.strip())
    assert в_запросе == ix.КОЛОНКИ_ВСТАВКИ
    assert "our_company" in ix.КОЛОНКИ_ВСТАВКИ
    assert "our_company = excluded.our_company" in _код("library/indexer.py")


def test_кортеж_вставки_несёт_нашу_компанию():
    дерево = ast.parse(_код("library/indexer.py"))
    for у in ast.walk(дерево):
        if (isinstance(у, ast.Call) and isinstance(у.func, ast.Attribute)
                and у.func.attr == "append" and isinstance(у.func.value, ast.Name)
                and у.func.value.id == "buf_files"):
            последнее = ast.unparse(у.args[0].elts[-1])
            assert "our_company" in последнее, последнее
            return
    raise AssertionError("buf_files.append не найден")


def test_проверка_колонок_находит_недостающие():
    class Курсор:
        def execute(self, sql, params):
            self.params = params

        def fetchall(self):
            return [(к,) for к in self.params[0] if к != "our_company"]

    assert ix.нет_колонок(Курсор()) == ["our_company"]


def test_переразбор_пишет_нашу_компанию():
    import library.reparse as r
    assert "our_company" in r.КОЛОНКИ_ЗАПИСИ
    код = _код("library/reparse.py")
    i = код.index("update lib_files set")
    правка = код[i:код.index("where file_id = %s", i)]
    assert "our_company = %s" in правка
    assert 'rec.get("our_company")' in код[i:i + 2500]


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
