"""Заведение дилеров разведки брендов в реестр компаний — без базы.

Что держится (library/load_research_dealers.py):
  · заводится только запись с сильным ключом, которую замер dealer_link не свёл;
    сведённые, спорные, без ключа — отсеиваются с названной причиной;
  · записи одного ключа (дилер двух брендов) — одна сущность; ключ с разными
    именами, домен самого бренда, домен почты или площадки — не заводятся;
  · гейты: число к заведению, доля споров, площадки, доказательство — каждый
    ловит свою мутацию;
  · проверка в памяти: после заведения замер сводит каждую заведённую запись в
    её сущность и не прибавляет споров; испорченное назначение ловится;
  · печать — только агрегаты.

Корпус придуман (CLAUDE.md, правило 18): домены — в зоне .example, ИНН —
синтетические с верной контрольной суммой.
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import dealer_link as dl  # noqa: E402
from library import load_research_dealers as ld  # noqa: E402
from library import supplier_link as sl  # noqa: E402

ИНН_А = "0000000018"
ИНН_Б = "1111111117"
ИНН_В = "2222222223"

E = [("KV-S-000001-8", None, None, "Альфа Насосы"),
     ("KV-S-000003-4", None, None, "Спорная Выдумка")]
I = [("KV-S-000001-8", "domain", "alpha-pumps.example"),
     ("KV-S-000003-4", "inn", ИНН_Б)]

РАЗВЕДКА = [{"oem_key": "vydumka", "dealers": [
    # 0 — сведена доменом: компания уже есть
    {"company": "Альфа Насосы", "country": "Нигдения", "role": "официальный дилер",
     "domain": "alpha-pumps.example", "domain_source": "https://alpha-pumps.example/dealers"},
    # 1 — к заведению, официальный дилер
    {"company": "Бета Уплотнения (дистрибьютор)", "country": "Нигдения, склад в столице",
     "role": "официальный дилер", "domain": "beta-seal.example",
     "domain_source": "https://beta-seal.example/about", "sources": ["https://beta-seal.example/about"]},
    # 2 — без сильного ключа
    {"company": "Гамма", "country": "Нигдения", "role": "дилер", "sources": ["https://x.example/"]},
    # 3 — ИНН, к заведению, своя площадка; доказательство — первая из sources
    {"company": f"ООО Дельта Выдуманная (ИНН {ИНН_А})", "country": "Россия",
     "role": "дочерняя компания изготовителя", "sources": ["https://delta.example/rekvizity"]},
    # 4, 5 — один домен, разные имена: не заводятся
    {"company": "Эпсилон Юг", "country": "Нигдения", "role": "дилер", "domain": "eps.example",
     "domain_source": "https://eps.example/"},
    {"company": "Зета Север", "country": "Нигдения", "role": "дилер", "domain": "eps.example",
     "domain_source": "https://eps.example/"},
    # 6 — домен самого бренда
    {"company": "Выдумка Регион", "country": "Нигдения", "role": "дочерняя компания изготовителя",
     "domain": "vydumka.example", "domain_source": "https://vydumka.example/contacts"},
    # 7 — домен в одну сущность, ИНН в другую: спор
    {"company": f"Тета (ИНН {ИНН_Б})", "country": "Нигдения", "role": "дилер",
     "domain": "alpha-pumps.example", "domain_source": "https://alpha-pumps.example/"},
]}, {"oem_key": "drugaya", "dealers": [
    # 8 — тот же дилер у второго бренда: одна сущность с записью 1
    {"company": "Бета Уплотнения", "country": "Нигдения", "role": "независимый продавец",
     "domain": "beta-seal.example", "domain_source": "https://beta-seal.example/drugaya"},
    # 9 — сведена доменом, ИНН в реестре нет
    {"company": f"Альфа Насосы (ИНН {ИНН_В})", "country": "Нигдения", "role": "дилер",
     "domain": "alpha-pumps.example", "domain_source": "https://alpha-pumps.example/"},
    # 10 — свой домен, но ИНН тот же, что у сведённой 9: не заводится
    {"company": f"Лямбда (ИНН {ИНН_В})", "country": "Нигдения", "role": "дилер",
     "domain": "lambda.example", "domain_source": "https://lambda.example/"},
]}]
ТАЙНЫ = ("beta-seal", "Бета", "Дельта", ИНН_А, ИНН_В, "lambda", "KV-S-", "eps.example", "https://")


def _замер():
    список = dl.дилеры(РАЗВЕДКА)
    р = sl.собрать_реестр(E, I)
    итог = dl.сопоставить(список, р)
    return список, р, итог, ld._сырые(РАЗВЕДКА)


def test_кого_заводить_и_почему_отсеяно():
    список, _, итог, сырые = _замер()
    assert итог.связь == {0: "KV-S-000001-8", 9: "KV-S-000001-8"}
    assert итог.спор_две == {7}
    отбор = ld.отобрать(список, итог, сырые)
    состав = sorted(tuple(x.разведка.id for x in г.дилеры) for г in отбор.группы)
    assert состав == [(1, 8), (3,)]
    assert отбор.отсеяно == collections.Counter({
        "уже в реестре (сведено)": 2, "без сильного ключа": 1, "один ключ — разные имена": 2,
        "домен самого бренда": 1, "спор: ключи ведут в разные сущности": 1,
        "ключ общий со сведённой или спорной записью": 1})
    бета = next(г for г in отбор.группы if len(г.дилеры) == 2)
    assert бета.имя == "Бета Уплотнения" and бета.страна == "Нигдения"
    assert бета.бренды == ["drugaya", "vydumka"]
    assert бета.виды == ["официальный дилер", "дилер"]
    assert бета.note() == "разведка брендов: официальный дилер, дилер (drugaya, vydumka)"
    дельта = next(г for г in отбор.группы if г.инн)
    assert дельта.инн == {ИНН_А} and дельта.формы == {"ооо"} and дельта.виды == ["своя площадка"]
    assert ld.доказательство(сырые[3]) == "https://delta.example/rekvizity"
    assert ld.доказательство(сырые[1]) == "https://beta-seal.example/about"


def test_признаки_как_у_сведения():
    список, _, итог, сырые = _замер()
    бета = next(г for г in ld.отобрать(список, итог, сырые).группы if len(г.дилеры) == 2)
    assert ld.признаки(бета, сырые) == [
        ("domain", "beta-seal.example", "https://beta-seal.example/about", "stated"),
        ("alias", "бетауплотнения", "", "stated")]


def test_в_памяти_после_заведения_сведено_всё_и_споров_не_прибавилось():
    список, р, итог, сырые = _замер()
    отбор = ld.отобрать(список, итог, сырые)
    назначено = {}
    for n, г in enumerate(отбор.группы):
        ld.добавить_в_реестр(р, f"new-{n}", г)
        назначено.update({x.разведка.id: f"new-{n}" for x in г.дилеры})
    после = dl.сопоставить(список, р)
    assert ld.проверить_после(список, итог, после, назначено) == []
    assert set(после.связь) == {0, 1, 3, 8, 9}
    # Мутация: назначение не туда — ловится.
    испорчено = {**назначено, 1: "new-9"}
    assert any("не сведённых" in p for p in ld.проверить_после(список, итог, после, испорчено))
    # Мутация: прежняя связь пропала — ловится.
    пусто = sl.Итог()
    assert any("прежних связей" in p for p in ld.проверить_после(список, итог, пусто, {}))
    # Мутация: спор прибавился — ловится.
    хуже = dl.сопоставить(список, р)
    хуже.спор_две.add(2)
    assert any("споров прибавилось" in p for p in ld.проверить_после(список, итог, хуже, назначено))


def test_гейты_чисто_на_корпусе():
    список, _, итог, сырые = _замер()
    assert ld.гейты(ld.отобрать(список, итог, сырые), сырые) == []


def test_гейт_числа_к_заведению(monkeypatch):
    список, _, итог, сырые = _замер()
    monkeypatch.setitem(ld.ГЕЙТЫ, "макс_к_заведению", 1)
    assert any("к заведению 2" in p for p in ld.гейты(ld.отобрать(список, итог, сырые), сырые))


def test_гейт_спорной_группы_и_площадки_и_доказательства():
    список, _, итог, сырые = _замер()
    отбор = ld.отобрать(список, итог, сырые)
    г = отбор.группы[0]
    # Спор: в группе два имени (как если бы отбор пропустил).
    спорная = г._replace(дилеры=г.дилеры + (список[4],))
    assert any("спорных групп" in p for p in ld.гейты(отбор._replace(группы=[спорная]), сырые))
    # Площадка — по своему закрытому списку, не по общий_домен.
    с_площадкой = г._replace(домены=frozenset({"shop.mtrehber.com"}))
    assert any("площадки" in p for p in ld.гейты(отбор._replace(группы=[с_площадкой]), сырые))
    assert ld.площадка("mtrehber.com") and not ld.площадка("beta-seal.example")
    # Без доказательства.
    без = dict(сырые)
    без[3] = {k: v for k, v in сырые[3].items() if k != "sources"}
    assert any("доказательства" in p for p in ld.гейты(отбор, без))


def test_площадка_и_домен_бренда_отсеиваются_до_гейтов():
    разв = [{"oem_key": "vydumka", "dealers": [
        {"company": "Каппа", "country": "Нигдения", "role": "дилер", "domain": "kappa.tradeatlas.com",
         "domain_source": "https://kappa.tradeatlas.com/"}]}]
    список = dl.дилеры(разв)
    итог = dl.сопоставить(список, sl.собрать_реестр([], []))
    отбор = ld.отобрать(список, итог, ld._сырые(разв))
    assert not отбор.группы and отбор.отсеяно == {"домен почты или площадки": 1}
    assert ld.домен_бренда("vydumka.example", "vydumka")
    assert not ld.домен_бренда("vydumka-dealer.example", "vydumka")


def test_страна_записи():
    assert ld.страна_записи("Россия (по ценам в рублях)") == "Россия"
    assert ld.страна_записи("Турция, склад в Стамбуле") == "Турция"
    assert ld.страна_записи(None) == ""


def test_печать_только_агрегаты(capsys):
    список, _, итог, сырые = _замер()
    ld.печать(список, итог, ld.отобрать(список, итог, сырые))
    out = capsys.readouterr().out
    assert "К ЗАВЕДЕНИЮ: сущностей                  2  (записей разведки 3)" in out
    assert "один ключ — разные имена" in out and "vydumka" in out
    for тайна in ТАЙНЫ:
        assert тайна not in out, тайна


def test_сырые_в_том_же_порядке_что_дилеры():
    список = dl.дилеры(РАЗВЕДКА)
    сырые = ld._сырые(РАЗВЕДКА)
    assert len(сырые) == len(список)
    for x in список:
        assert dl.без_пояснений(сырые[x.разведка.id]["company"]) == \
            dl.без_пояснений(РАЗВЕДКА[0 if x.oem_key == "vydumka" else 1]["dealers"][x.номер]["company"])


# ── проводка: прогон миграций и прогон заведения ────────────────────────────

def _без_комментариев(текст: str) -> str:
    return "\n".join(строка.split("#")[0] for строка in текст.splitlines())


def test_схема_в_прогоне_миграций_после_схемы_поставщиков():
    yml = (ROOT / ".github" / "workflows" / "zip-db.yml").read_text(encoding="utf-8")
    файлы = [s.strip().split()[1] for s in _без_комментариев(yml).splitlines()
             if s.strip().startswith("apply ")]
    где = файлы.index("library/supabase/research_dealers_schema.sql")
    assert файлы.index("library/supabase/suppliers_schema.sql") < где
    assert файлы[-1] == "zip/supabase/migrations_rls_stage1.sql"


def test_прогон_ручной_без_битрикса_проверки_и_схема_перед_записью():
    код = _без_комментариев((ROOT / ".github" / "workflows" / "supplier-dealers.yml").read_text(encoding="utf-8"))
    assert "workflow_dispatch" in код and "schedule" not in код and "cron" not in код
    assert "BITRIX" not in код, "заведение дилеров Битрикс не читает"
    assert "group: supplier-link" in код
    assert "RUN_ID: dealers-${{ github.run_id }}" in код
    assert "ROLLBACK_RUN_ID: ${{ inputs.rollback }}" in код
    схема = код.index('psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f library/supabase/research_dealers_schema.sql')
    запись = код.index("- name: Замер и заведение")
    assert код.index("--locale=C.UTF-8") < схема < запись
    assert "tests/test_load_research_dealers_sql.py" in код
    assert код.index("python library/dealer_link.py") > запись
