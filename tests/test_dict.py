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
            else:
                assert cell["state"] == "пусто"

