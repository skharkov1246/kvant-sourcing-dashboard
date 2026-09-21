"""Общий словарь баз данных: свежесть и инварианты проекции.

ЗАЧЕМ. dict/ — проекция семи подпроектов, а не место хранения. Проекция, отставшая
от источников, хуже её отсутствия: по ней принимают решения, а она показывает
позавчерашнюю картину. Гейт запускает pytest на каждый PR, поэтому сверка свежести
живёт здесь, а не отдельным шагом workflow — трогать .github/workflows без владельца
нельзя (CLAUDE.md, раздел «трогать нельзя»).

Отдельно закреплены инварианты, которые ломались бы молча: собственный номер КВАНТ
обязан быть в указателе поиска (до 12.09.2026 его там не было, и поиск по KV давал
ноль при том, что KV приведён примером в подсказке самой страницы), а рёбра цепочки
не должны смешивать изготовителей с указаниями к закупке.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICT = ROOT / "dict"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_dict", ROOT / "scripts" / "build_dict.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dict_is_fresh():
    """Словарь пересобран после последней правки источников."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_dict.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"словарь устарел: {r.stdout}{r.stderr}"


def test_dict_files_exist():
    for name in ("oem.json", "system.json", "chain.json", "summary.json"):
        assert (DICT / name).exists(), f"нет dict/{name}"


def test_oem_keys_are_unique_and_merge_spellings():
    """Ключ производителя один на компанию — ради этого словарь и заводился."""
    recs = json.loads((DICT / "oem.json").read_text(encoding="utf-8"))["records"]
    keys = [r["oem_key"] for r in recs]
    assert len(keys) == len(set(keys)), "ключи производителей повторяются"
    assert all(r["oem_key"] for r in recs), "пустой ключ производителя"
    # Смысл словаря — склейка написаний. Если ни одна компания не склеилась,
    # значит нормализация перестала работать и проекция бесполезна.
    merged = [r for r in recs if r["n_spellings"] > 1]
    assert merged, "ни одно написание не склеилось — проверьте nkey()"


def test_chain_separates_makers_from_routing_notes():
    """Указание «закупка по спецификации» — не изготовитель, и путать их нельзя."""
    ch = json.loads((DICT / "chain.json").read_text(encoding="utf-8"))
    kinds = {e["kind"] for e in ch["records"]}
    assert kinds <= {"maker", "routing_note"}, f"неизвестный вид ребра: {kinds}"
    assert ch["makers"] > 0 and ch["routing_notes"] > 0
    for e in ch["records"]:
        if e["kind"] == "maker":
            assert e["to_key"], f"у изготовителя нет ключа: {e['to']}"
        else:
            assert not e["to_key"], f"указанию к закупке присвоен ключ компании: {e['to']}"


def test_self_edge_is_in_house_not_subsupplier():
    """Ребро компании в саму себя означает собственное изготовление, а не субпоставку."""
    ch = json.loads((DICT / "chain.json").read_text(encoding="utf-8"))
    for e in ch["records"]:
        if e["kind"] == "maker" and e["from_key"] and e["from_key"] == e["to_key"]:
            assert e["relation"] == "in_house", f"самоссылка помечена как {e['relation']}: {e['from']}"


def test_normalizers_are_stable():
    """Нормализация — канон для всего репозитория, её поведение закреплено."""
    m = _load_builder()
    assert m.nkey("AB SKF") == m.nkey("SKF GmbH") == m.nkey('"SKF"') == "skf"
    # Кириллица и латиница не склеиваются: «СКФ» и SKF — разные ключи, и это верно,
    # потому что по-русски так пишут и другие компании.
    assert m.nkey("ООО «СКФ»") != m.nkey("SKF")
    assert m.nkey("Bently Nevada, LLC") == m.nkey("BENTLY NEVADA") == "bentlynevada"
    assert m.norm_pn("1R-1807") == "1R1807"
    assert m.norm_pn("3222 1881 41") == "3222188141"
    assert m.clean_name('«Grundfos Holding A/S»') == "Grundfos Holding A/S"


def test_own_kv_number_is_searchable():
    """Поиск по собственному номеру КВАНТ обязан работать: KV приведён примером
    в подсказке страницы поиска, а до 12.09.2026 возвращал ноль результатов."""
    rows = json.loads((ROOT / "pnw" / "data" / "numbers.json").read_text(encoding="utf-8"))["rows"]
    own = [r for r in rows if r["kind"] == "свой"]
    items = json.loads((ROOT / "pnw" / "data" / "item_master.json").read_text(encoding="utf-8"))["items"]
    assert len(own) == len(items), "свой номер есть не у каждой детали"
    assert all(r["owner"] == "КВАНТ" for r in own)
    assert all(r["number_norm"].startswith("KV") for r in own)


def test_pnw_data_is_catalogued():
    """Каталог данных обязан видеть pnw/data: там 440 адресов и 253 телефона,
    а до 12.09.2026 каталог сканировал только pnw/public и не знал о них (правило 6)."""
    cat = json.loads((ROOT / "data" / "catalog.json").read_text(encoding="utf-8"))
    paths = {d["path"] for d in cat["datasets"]}
    for need in ("pnw/data/supplier_master.json", "pnw/data/item_master.json",
                 "pnw/data/numbers.json"):
        assert need in paths, f"{need} не попал в каталог данных"
    sens = {d["path"]: d["sensitivity"]["level"] for d in cat["datasets"]}
    assert sens["pnw/data/supplier_master.json"] == "конфиденциально", \
        "реестр с контактами поставщиков должен быть помечен как конфиденциальный"


def test_catalog_sees_uncommitted_code_files():
    """Каталог обязан видеть ещё не закоммиченный файл кода.

    Сборщик составлял список файлов по git ls-files, то есть по индексу git.
    Новый сборщик или тест попадал туда только после коммита, поэтому каталог,
    собранный локально ПЕРЕД коммитом, отличался от каталога, который CI считает
    ПОСЛЕ него, и проверка --check краснела на каждом PR с новым файлом кода.
    Так дважды падал гейт. Локальный прогон обязан предсказывать результат CI."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_catalog", ROOT / "scripts" / "build_catalog.py")
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)

    probe = ROOT / "scripts" / "_probe_uncommitted.py"
    probe.write_text('OPEN = "coverage.json"\n', encoding="utf-8")
    try:
        fresh = bc.build()
        cov = [d for d in fresh["datasets"] if d["path"] == "gpu/data/coverage.json"]
        assert cov, "gpu/data/coverage.json пропал из каталога"
        assert "scripts/_probe_uncommitted.py" in cov[0].get("referenced_by", []), \
            "каталог не видит незакоммиченный файл кода — проверка --check снова будет краснеть в CI"
    finally:
        probe.unlink(missing_ok=True)


def test_chain_coverage_is_fresh_and_honest():
    """Счётчик цепочки портала пересобран и не приукрашивает.

    Ноль в клетке обязан означать отсутствие данных, а не «данные где-то есть»:
    по этой карте выбирается следующая работа, и приукрашенный ноль увёл бы
    усилия не туда."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_chain_coverage.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"счётчик цепочки устарел: {r.stdout}{r.stderr}"

    cov = json.loads((ROOT / "data" / "chain_coverage.json").read_text(encoding="utf-8"))
    assert len(cov["links"]) == 8, "цепочка портала — восемь звеньев"
    assert cov["summary"]["cells_total"] == len(cov["segments"]) * 8
    for seg in cov["segments"]:
        for cell in seg["cells"]:
            # Клетка с числом обязана называть файлы, откуда оно взято, — иначе
            # цифру нельзя проверить, и она ничем не лучше выдуманной.
            if cell["n"]:
                assert cell["sources"], f"{seg['segment']}/{cell['link']}: число без источника"
                assert cell["state"] == "есть"
            elif cell.get("draft"):
                # Черновик — собрано, но скептиком не проверено. Клетка не считается
                # заполненной: иначе «собрал» читалось бы как «проверил».
                assert cell["state"] == "черновик"
                assert cell["sources"], f"{seg['segment']}/{cell['link']}: черновик без источника"
            else:
                assert cell["state"] == "пусто"

    # Заполненные клетки считаются по проверенным данным, не по собранным.
    filled = sum(1 for s_ in cov["segments"] for c in s_["cells"] if c["state"] == "есть")
    assert cov["summary"]["cells_filled"] == filled, \
        "в заполненные клетки просочился черновик — тогда карта перестаёт показывать, где пусто"
    draft_only = sum(1 for s_ in cov["segments"] for c in s_["cells"] if c["state"] == "черновик")
    assert cov["summary"]["cells_draft_only"] == draft_only


def test_gsho_machine_registry():
    """Реестр машин ГШО собран и склеивает написания одной машины.

    Счётчик цепочки показывал по ГШО одну машину при 1918 позициях номенклатуры:
    обозначения лежали внутри строкового поля machine через запятую и отдельной
    сущностью не существовали. Реестр закрывает первое звено цепочки."""
    r = subprocess.run([sys.executable, str(ROOT / "zip" / "tools" / "build_machines.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"реестр машин устарел: {r.stdout}{r.stderr}"

    d = json.loads((ROOT / "zip" / "data" / "machines.json").read_text(encoding="utf-8"))
    assert d["stats"]["machines"] > 50, "машин подозрительно мало — проверьте разбор поля machine"
    keys = [m["machine_key"] for m in d["machines"]]
    assert len(keys) == len(set(keys)), "ключи машин повторяются"

    # Смысл реестра — склейка написаний. ST14 и ST-14 обязаны быть одной машиной.
    merged = [m for m in d["machines"] if len(m["spellings"]) > 1]
    assert merged, "ни одно написание не склеилось — проверьте mkey()"

    # Бренд — один изготовитель, а не перечень: поле brand в источнике бывает
    # списком «Epiroc, Normet, Paus», и такой список не должен попасть в реестр.
    for b in d["brands"]:
        assert "," not in b["brand"], f"в бренд попал перечень: {b['brand']}"

    # Число позиций у машины обязано быть положительным: машина без единой детали
    # означает, что она попала в реестр из мусорного значения поля.
    assert all(m["parts"] > 0 for m in d["machines"])


def test_machine_registry_does_not_inflate():
    """Реестр машин не подмешивает к машинам неразобранное.

    Поле mach базы PN содержит не только машины: туда попали детали
    («Уплотнение кольцевое»), корзины бренда («Solar (сток)») и машины совсем
    других сегментов. Подмешать их к турбинам значит завысить заполняемость
    цепочки — а по ней выбирается следующая работа."""
    m = json.loads((DICT / "machine.json").read_text(encoding="utf-8"))
    kinds = {r["kind"] for r in m["records"]}
    assert kinds <= {"turbine", "other_machine", "mining_machine"}, \
        f"в реестр попал неразобранный вид: {kinds}"
    # Виды part, bucket и unknown обязаны быть посчитаны, но НЕ попасть в записи.
    for bad in ("part", "bucket", "unknown"):
        assert m["pn_db_kinds"].get(bad, 0) > 0, f"вид {bad} перестал считаться — проверьте классификатор"
    keys = [r["machine_key"] for r in m["records"]]
    assert len(keys) == len(set(keys))


def test_machine_classifier_catches_flagship_models():
    """Классификатор обязан ловить самые массовые машины базы.

    Замыкающий \\b в семействах ломал правило молча: в LM2500 граница слова
    после «LM2» не наступает, и самая массовая машина базы — 3664 позиции —
    проваливалась в «не определено»."""
    m = _load_builder()
    for name in ("LM2500", "LM6000", "GE Frame 6B", "RB211-535", "SGT-400",
                 "Taurus 60S", "Centaur 50 (по документу)", "GE LMS100"):
        kind, _stem = m.mach_kind(name)
        assert kind == "turbine", f"{name} не опознана как турбина, а как {kind}"
    for name in ("Уплотнение кольцевое", "Шкаф управления PMS МЛСК Ф-1"):
        assert m.mach_kind(name)[0] == "part", f"{name} принята за машину"
    assert m.mach_kind("Solar (сток)")[0] == "bucket"
    assert m.mach_kind("Буровой насос 12T1600")[0] == "other_machine"


def test_node_map_is_fresh_and_complete():
    """Карта узлов пересобрана и не теряет метки молча.

    Ключи таблицы соответствия приводятся тем же правилом, каким по ней ищут.
    Без этого «Крепёж» в таблице и «крепеж» в запросе — разные строки, и 828
    размеченных человеком строк уходили в «не определено», не подав признака."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_node_map.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"карта узлов устарела: {r.stdout}{r.stderr}"

    d = json.loads((DICT / "node_map.json").read_text(encoding="utf-8"))
    assert d["labels"]["unmapped"] == [], \
        f"метки не сведены к узлам: {d['labels']['unmapped']}"
    assert d["labels"]["distinct"] > 40, "меток стало подозрительно мало"


def test_node_classifier_precision_floor():
    """Точность классификатора измеряется на ручной разметке и не должна падать.

    Классификатор предлагает разметку для строк без метки — по этому предложению
    потом принимают решение. Заявленная, но не измеренная точность бесполезна,
    поэтому порог закреплён здесь и проверяется на каждом PR."""
    d = json.loads((DICT / "node_map.json").read_text(encoding="utf-8"))
    c = d["classifier"]
    assert c["judged"] >= 1000, "выборка для измерения точности слишком мала"
    assert c["precision_pct"] >= 80, (
        f"точность упала до {c['precision_pct']} %: проверьте правила, "
        f"путаница — {c['top_confusions'][:3]}")
    # Предложение не должно молча объявлять разобранным то, что не разобрано.
    p = d["proposal"]
    assert p["would_classify"] + p["would_leave_unresolved"] == p["rows_without_label"]


def test_makers_counted_as_companies_not_rows():
    """Изготовители считаются уникальными компаниями, а не строками.

    Счётчик суммировал строки разных файлов и врал в обе стороны: по ГТУ он видел
    72 компании, не подключив пять реестров из восьми, а по ГШО — 4368, потому что
    4309 строк odm_suppliers это связи «позиция × кандидат» на 1289 компаний,
    а не изготовители. Сумма строк несравнима между направлениями."""
    cov = json.loads((ROOT / "data" / "chain_coverage.json").read_text(encoding="utf-8"))
    by_seg = {s["segment"]: s for s in cov["segments"]}

    gsho = next(c for c in by_seg["gsho"]["cells"] if c["link"] == "maker")
    odm = json.loads((ROOT / "zip" / "data" / "odm_suppliers.json").read_text(encoding="utf-8"))
    assert gsho["n"] < len(odm), (
        "изготовителей ГШО не может быть больше, чем строк связей: "
        f"{gsho['n']} против {len(odm)} — считаются строки, а не компании")

    gtu = next(c for c in by_seg["gtu"]["cells"] if c["link"] == "maker")
    assert len(gtu["sources"]) >= 6, (
        f"по ГТУ подключено лишь {len(gtu['sources'])} реестров — "
        "остальные компании в счёт не попадут")
    assert gtu["n"] > 1000, "по ГТУ реестров восемь, компаний должно быть заметно больше сотни"


def test_recip_recon_marks_unverified_explicitly():
    """У каждого факта разведки есть вердикт, и он не пустой.

    Пустое поле вердикта читается как «проверено, всё хорошо». Состояния
    «скептик не сослался» и «не проверялся» — разные вещи, и обе означают,
    что факт подтверждённым считать нельзя."""
    d = json.loads((ROOT / "zip" / "data" / "recip_recon.json").read_text(encoding="utf-8"))
    allowed = {"подтверждено", "частично", "опровергнуто", "непроверяемо",
               "скептик не сослался", "не проверялся"}
    for a in d["angles"]:
        for f in a["findings"]:
            assert f.get("verdict") in allowed, f"{a['key']}: вердикт «{f.get('verdict')}»"
            assert f.get("source"), f"{a['key']}/{f['topic']}: факт без источника"
        # Угол без скептика обязан честно об этом сообщать.
        if not a["skeptic"]:
            assert all(f["verdict"] == "не проверялся" for f in a["findings"])

    # Счётчик цепочки обязан брать только проверенное: неподтверждённое
    # не заполняет звено.
    cov = json.loads((ROOT / "data" / "chain_coverage.json").read_text(encoding="utf-8"))
    recip = next(s for s in cov["segments"] if s["segment"] == "recip")
    ok = sum(1 for a in d["angles"] for f in a["findings"]
             if f["verdict"] in ("подтверждено", "частично"))
    total = sum(len(a["findings"]) for a in d["angles"])
    assert ok < total, "все факты помечены проверенными — проверьте разбор вердиктов"
    assert recip["filled"] >= 4, "разведка не дошла до счётчика цепочки"


def test_parts_counted_as_unique_numbers_not_rows():
    """Запчасти считаются уникальными партномерами, а не строками файлов.

    Третий случай одной и той же болезни счётчика: суммы строк из файлов, которые
    пересекаются. По ГТУ складывались 12 442 строки базы PN и 10 986 строк сквозного
    справочника, который ИЗ НЕЁ ЖЕ И СОБРАН, — получалось 25 041 вместо 12 934.
    Номера берутся из явных полей, а не угадываются по тексту: иначе в номера
    попадают обозначения машин вроде QSV91G."""
    cov = json.loads((ROOT / "data" / "chain_coverage.json").read_text(encoding="utf-8"))
    by_seg = {s["segment"]: s for s in cov["segments"]}

    gtu = next(c for c in by_seg["gtu"]["cells"] if c["link"] == "part")
    pn_db = json.loads((ROOT / "gt" / "data" / "pn_db.json").read_text(encoding="utf-8"))["rows"]
    im = json.loads((ROOT / "pnw" / "data" / "item_master.json").read_text(encoding="utf-8"))["items"]
    gtu_items = sum(1 for x in im if x.get("section") == "ГТУ")
    assert gtu["n"] < len(pn_db) + gtu_items, (
        f"по ГТУ {gtu['n']} номеров при {len(pn_db)} строках базы PN и {gtu_items} строках "
        "справочника — это сумма пересекающихся файлов, а не уникальные номера")

    # Номер короче четырёх знаков — это индекс строки, а не партномер.
    for seg in by_seg.values():
        cell = next(c for c in seg["cells"] if c["link"] == "part")
        if cell["n"]:
            assert cell["sources"], f"{seg['segment']}: номера без источника"


def test_chain_counter_declares_its_scope():
    """Счётчик обязан говорить, чего он не видит.

    Он меряет только файлы репозитория. Инженерная библиотека живёт в закрытой
    Supabase, и часть звеньев закрыта именно там: на 12.09.2026 в lib_defects
    16 записей, в lib_procedures 34 ремонтные операции, в lib_suppliers 4 480
    компаний. Без оговорки страница читается как «этих звеньев нет нигде» —
    и увела бы работу не туда."""
    cov = json.loads((ROOT / "data" / "chain_coverage.json").read_text(encoding="utf-8"))
    scope = cov.get("scope", "")
    assert scope, "счётчик не объявляет свой охват"
    assert "lib_" in scope and "Supabase" in scope, \
        "в оговорке не назван второй источник знаний — библиотека"

    page = ROOT / "zip" / "public" / "chain.html"
    if page.exists():
        html = page.read_text(encoding="utf-8")
        assert "Что этот счётчик не видит" in html, "оговорка не попала на страницу"


def test_diagnostics_verdicts_are_explicit():
    """У каждого факта и каждого дефекта разведки по диагностике стоит вердикт.

    Пустое поле вердикта читается как «проверено, всё хорошо», и это была бы
    ложь: скептик адресно проверял численные нормы, а каталог дефектов — точечно.
    Поэтому «скептик не сослался» существует отдельным значением и обязано
    стоять там, где проверки не было."""
    d = json.loads((ROOT / "zip" / "data" / "diagnostics_recon.json").read_text(encoding="utf-8"))
    assert d["stats"]["angles"] >= 5
    ALLOWED = {"подтверждено", "частично", "опровергнуто", "непроверяемо",
               "скептик не сослался", "угол без скептика"}
    for a in d["angles"]:
        for item in a["findings"] + a["defects"]:
            v = item.get("verdict")
            assert isinstance(v, dict) and v.get("verdict") in ALLOWED, \
                f"{a['key']}: вердикт отсутствует или не из словаря — {v}"
        # Вердикт без адреса означает, что проверка скептика потерялась по дороге.
        if a["skeptic"]:
            assert not a["skeptic"]["orphan_verdicts"], \
                f"{a['key']}: вердикты скептика не легли ни на один факт"

    # Оговорка о непроверенных стандартах обязана остаться в файле дословно:
    # сгладить её — значит выдать несверенные цифры за сверенные.
    assert "403" in d["caveat"] and "ISO 10816-3" in d["caveat"], \
        "оговорка скептика о недоступных стандартах пропала из набора"


def test_symptom_index_is_fresh_and_measured():
    """Индекс «признак → дефект» пересобран, и его охват измерен, а не заявлен.

    Звено «признак» было пустым во всех направлениях. Индекс закрывает его
    разбором поля симптомов, и главное здесь — не число признаков, а доля
    дефектов, до которых индекс дотянулся, и полный список тех, до кого нет."""
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_symptom_index.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"индекс признаков устарел: {r.stdout}{r.stderr}"

    d = json.loads((ROOT / "dict" / "symptom.json").read_text(encoding="utf-8"))
    cov = d["coverage"]
    assert cov["pct"] >= 90, f"охват признаками просел до {cov['pct']} % — правило ослабло"
    # Список непокрытых обязан лежать целиком, а не числом: без него следующая
    # ошибка снова будет неизмеримой.
    assert len(cov["unmatched"]) == cov["unmatched_count"]
    assert cov["with_symptom"] + cov["unmatched_count"] == cov["defects"]

    # Узлы сведены полностью: «не разобран» в готовом индексе означает, что
    # правило не дописано, а такой узел не найдётся поиском.
    assert not cov["nodes_unmatched"], f"узлы без правила: {cov['nodes_unmatched']}"

    # Признак обязан вести к дефектам, а дефект — нести запчасти: иначе цепочка
    # обрывается там же, где начиналась.
    for rec in d["records"]:
        assert rec["defects"], f"признак {rec['key']} ни на что не указывает"
    assert all(r["parts"] for r in d["defect_rows"]), \
        "у дефекта пустое поле запчастей — звено «дефект → запчасть» разорвано"


def test_oem_atlas_declares_its_own_incompleteness():
    """Атлас производителей заявляет неполноту числом, а не умалчивает о ней.

    Неполный справочник, выдающий себя за полный, хуже отсутствующего: по нему
    делают вывод «такого изготовителя нет», хотя разведка до его направления
    просто не дошла. Поэтому число сделанных пар обязано стоять рядом с числом
    запланированных, а недостающие — лежать списком."""
    d = json.loads((ROOT / "zip" / "data" / "oem_atlas.json").read_text(encoding="utf-8"))
    cm = d["completeness"]
    assert cm["pairs_done"] == len(d["pairs"])
    assert cm["pairs_done"] < cm["pairs_planned"], \
        "если пары сделаны все, уберите оговорку о неполноте — иначе она врёт в другую сторону"
    assert cm["pairs_missing"], "недостающие пары не перечислены"
    assert len(cm["pairs_missing"]) == cm["pairs_planned"] - cm["pairs_done"] - 4, \
        "число недостающих пар не сходится с планом (по трём направлениям регион «Китай» не планировался)"

    # Страна обязана быть ключом, а не свободным текстом: «Германия» и
    # «Германия, Фридрихсхафен» — одна страна, иначе счёт по странам бессмыслен.
    assert d["stats"]["countries"] <= 25, \
        f"стран {d['stats']['countries']} — нормализация страны развалилась"
    for m in d["makers"]:
        assert "," not in m["country"] and "/" not in m["country"], \
            f"в ключ страны попал свободный текст: {m['country']}"
        # Исходное значение сохраняется целиком: в нём адрес завода, он нужнее ключа.
        assert m["country_raw"], f"{m['name']}: потеряно исходное значение страны"


def test_recon_batch_verdicts_are_explicit():
    """Три разведки 12.09.2026: у каждой записи вердикт, и он не приукрашивает.

    По этим наборам будут заказывать детали и отдавать работы подрядчикам.
    Запись без вердикта читалась бы как проверенная, поэтому «угол без скептика»
    существует отдельным значением и обязано стоять там, где скептика не было:
    из тридцати шести скептиков этих трёх разведок отработали пять."""
    ALLOWED = {"подтверждено", "частично", "опровергнуто", "непроверяемо",
               "скептик не сослался", "угол без скептика"}

    dirs = json.loads((ROOT / "zip" / "data" / "dirs_recon.json").read_text(encoding="utf-8"))
    items = [x for a in dirs["angles"] for x in a["findings"] + a["companies"]]
    assert items, "набор пуст"
    assert all(x["verdict"]["verdict"] == "угол без скептика" for x in items), \
        "по насосам, КИПиА и электротехнике скептик не отработал ни по одному углу — " \
        "любой другой вердикт означает, что проверка приписана задним числом"
    assert dirs["stats"]["with_skeptic"] == 0

    rep = json.loads((ROOT / "zip" / "data" / "repair_recon.json").read_text(encoding="utf-8"))
    tech = [t for a in rep["tech_angles"] for t in a["technologies"]]
    cont = [c for a in rep["contractor_angles"] for c in a["contractors"]]
    assert len(tech) >= 40 and len(cont) >= 50
    for x in tech + cont:
        assert x["verdict"]["verdict"] in ALLOWED
    # Исполнители скептика не получили — все до одного.
    assert all(c["verdict"]["verdict"] == "угол без скептика" for c in cont)
    # Проверенные утверждения угла не выбрасываются: потерянная проверка хуже
    # отсутствующей, потому что о ней никто не узнает.
    claims = [v for a in rep["tech_angles"] if a["skeptic"]
              for v in a["skeptic"]["checked_claims"]]
    assert len(claims) == rep["stats"]["checked_claims"] > 50
    assert all(v["verdict"] in ALLOWED and v["claim"] for v in claims)

    subs = json.loads((ROOT / "zip" / "data" / "subsupplier_recon.json").read_text(encoding="utf-8"))
    assert subs["stats"]["chains"] >= 100 and subs["stats"]["rules"] >= 70
    for sg in subs["segments"]:
        for x in sg["chains"] + sg["rules"]:
            assert x["verdict"]["verdict"] in ALLOWED
        # Направление со скептиком не может целиком остаться «углом без скептика»
        # и наоборот: это означало бы, что привязка вердиктов развалилась.
        if sg["skeptic"]:
            assert any(x["verdict"]["verdict"] != "угол без скептика" for x in sg["rules"])
        else:
            assert all(x["verdict"]["verdict"] == "угол без скептика"
                       for x in sg["chains"] + sg["rules"])
    # Правило чтения номера без указания, где оно ломается, — приглашение
    # ошибиться в закупке: именно на границе правила и рождается не та деталь.
    rules = [r for sg in subs["segments"] for r in sg["rules"]]
    assert sum(1 for r in rules if (r.get("breaks") or "").strip()) >= len(rules) * 0.9


def test_every_chain_link_started_somewhere():
    """Ни одно звено цепочки не пусто во всех направлениях сразу.

    12.09.2026 таких звеньев было три: признак, дефект, исполнитель. Тест
    удерживает достигнутое: если звено снова опустеет везде, это регрессия
    данных, и увидеть её надо на гейте, а не через месяц на портале."""
    cov = json.loads((ROOT / "data" / "chain_coverage.json").read_text(encoding="utf-8"))
    assert cov["empty_everywhere"] == [], \
        "звено пусто во всех направлениях: " + \
        ", ".join(x["title"] for x in cov["empty_everywhere"])
    assert cov["summary"]["links_not_started"] == 0
