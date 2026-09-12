"""Досье машины R1700G: сведение разведки, своих позиций и генерация SQL.

Корпус придуман (правило 18 CLAUDE.md): номера вида 999-0001 в каталоге Caterpillar
не существуют, и это нарочно — тест проверяет логику сведения, а не данные.

Что закреплено тестами (каждое — цена прошлой ошибки в подобных сборщиках):
  • один и тот же номер из двух направлений даёт одну строку, а не две;
  • аналоги из разных направлений копятся, а не затирают друг друга;
  • вердикт «снят» не затирает «подтверждён» из второго прохода;
  • своя позиция, которой нет в разведке, попадает в перечень, а не теряется;
  • SQL идемпотентен по форме (on conflict do update) и экранирует кавычки.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    """Модули zip/tools не пакет — грузим по пути, как это делает сборка сайта."""
    path = ROOT / "zip" / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_r1700_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


dossier = _load("r1700_dossier")


def test_norm_pn_сводит_написания():
    assert dossier.norm_pn("1R-1808") == dossier.norm_pn("1r1808") == "1R1808"
    assert dossier.norm_pn("423-8524") == "4238524"
    assert dossier.norm_pn(None) == ""


def test_merge_parts_сводит_дубли_и_копит_аналоги():
    slices = {
        "filters": {"rows": [{
            "pn": "999-0001", "name_ru": "Фильтр придуманный", "node": "06 Фильтры и жидкости",
            "qty": "1", "interval": "500 ч", "source": "https://example.test/a", "confidence": "med",
            "verdict": "снят",
            "alts": [{"brand": "ТестФильтр", "pn": "TF-1", "kind": "аналог"}],
        }]},
        "hyd": {"rows": [{
            "pn": "999-0001", "name_ru": "", "name_en": "Imaginary Filter", "node": "",
            "applic": "R1700G", "source": "https://example.test/b", "confidence": "high",
            "verdict": "подтверждён",
            "alts": [{"brand": "ДругойБренд", "pn": "DB-2", "kind": "аналог"}],
        }]},
    }
    parts, alts = dossier.merge_parts(slices)
    assert len(parts) == 1, "один номер — одна строка"
    p = parts[0]
    assert p["name_en"] == "Imaginary Filter", "пустое поле добирается из второго направления"
    assert p["node"] == "06 Фильтры и жидкости", "непустой узел не затирается пустым"
    assert p["confidence"] == "high", "доверие берётся лучшее"
    assert p["verdict"] == "подтверждён", "«снят» не должен перебивать «подтверждён»"
    assert len(p["alts"]) == 2 and len(alts) == 2, "аналоги копятся из обоих направлений"
    assert len(p["sources"]) == 2, "источники копятся"
    assert set(p["slices"]) == {"filters", "hyd"}


def test_своя_позиция_без_разведки_попадает_в_перечень():
    parts, alts = [], []
    own = [{
        "position_id": 1, "pp": 7, "kv": "KV-000999-9", "pn": "999-0002", "pns": ["999-0002"],
        "name": "Насос придуманный", "node": "05 Гидравлика", "node_dossier": "05 Гидравлика",
        "machine": "R1700G", "applications": "R1700G (тест)", "material": None, "hs": None,
        "note": "тестовая строка", "qty_quarter": 2, "price_min": 10, "price_max": 20,
        "price_cur": "EUR", "price_src": "тест", "bitrix_status": "продавали", "opendb_signal": None,
        "crossrefs": [{"number": "X-1", "brand": "ТестБренд", "kind": "analog"},
                      {"number": "X-2", "brand": "", "kind": "unknown"}],
    }]
    linked, added = dossier.link_own_to_parts(parts, alts, own)
    assert added == 1 and linked == 1
    p = parts[0]
    assert p["verdict"] == "наша база" and p["confidence"] == "high"
    assert p["kv"] == ["KV-000999-9"] and p["position_id"] == 1
    assert p["price_eur_min"] == 10 and p["bitrix_status"] == "продавали"
    assert len(p["alts"]) == 2, "кроссы из нашего разбора aliases становятся аналогами"
    assert any(a["brand"] == "не определён" for a in p["alts"]), "номер без бренда не выбрасывается"
    assert len(alts) == 2


def test_своя_позиция_дополняет_строку_разведки():
    parts, alts = dossier.merge_parts({"filters": {"rows": [{
        "pn": "999-0003", "name_ru": "Элемент", "node": "06 Фильтры и жидкости",
        "source": "https://example.test/c", "confidence": "low", "verdict": "подтверждён", "alts": [],
    }]}})
    own = [{
        "position_id": 42, "pp": 3, "kv": "KV-000998-1", "pn": "9990003", "pns": ["9990003"],
        "name": "Элемент наш", "node": "06 Фильтры", "node_dossier": "06 Фильтры и жидкости",
        "machine": "R1700G", "applications": "R1700G", "material": None, "hs": None, "note": "",
        "qty_quarter": 1, "price_min": 5, "price_max": 9, "price_cur": "EUR", "price_src": "тест",
        "bitrix_status": None, "opendb_signal": "кросс не подтверждён", "crossrefs": [],
    }]
    linked, added = dossier.link_own_to_parts(parts, alts, own)
    assert (linked, added) == (1, 0), "написание номера без дефиса — тот же номер"
    assert parts[0]["position_id"] == 42 and parts[0]["kv"] == ["KV-000998-1"]
    assert parts[0]["verdict"] == "подтверждён", "вердикт разведки сохраняется"


def test_playbook_считается_от_цифр():
    parts = [
        {"pn": "999-0004", "alts": [], "verdict": "подтверждён", "price_usd": "", "confidence": "med",
         "bitrix_status": "продавали"},
        {"pn": "999-0005", "alts": [{"brand": "b", "pn": "1"}], "verdict": "снят", "price_usd": "10",
         "confidence": "low"},
    ]
    steps = dossier.playbook(parts, [], [], {"importers": [], "rows": []}, {}, [])
    text = json.dumps(steps, ensure_ascii=False)
    assert steps, "шаги должны считаться даже на пустых каналах"
    assert "999-0004" in text, "позиция с историей сделки попадает в первый шаг"
    assert "снят" in text, "о снятых номерах предупреждаем отдельно"


def test_sql_идемпотентен_и_экранирует():
    sql = _load("r1700_sql")
    assert sql.q("ООО \"Тест'ов\"") == "'ООО \"Тест''ов\"'"
    assert sql.q("") == "null" and sql.q(None) == "null"
    stmt = sql.ins("mach_parts", ["machine_key", "pn", "note"],
                   [["R1700G", "999-0006", "строка с 'кавычкой'"]], conflict="machine_key, pn")
    assert "on conflict (machine_key, pn) do update set note = excluded.note" in stmt
    assert "''кавычкой''" in stmt
    assert sql.ins("mach_parts", ["a"], []).startswith("-- mach_parts: нет строк")


def test_досье_и_страница_собираются_на_живых_данных():
    """Файл досье в репозитории должен быть целым и внутренне согласованным."""
    p = ROOT / "zip" / "data" / "r1700.json"
    if not p.exists():
        return
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["schema"] == "kvant.r1700/1"
    assert d["machine"]["key"] == "R1700G"
    assert d["stats"]["parts"] == len(d["parts"])
    assert d["stats"]["alts"] == len(d["alts"])
    norms = [x["pn_norm"] for x in d["parts"]]
    assert len(norms) == len(set(norms)), "в перечне не должно быть дублей по номеру"
    for x in d["parts"]:
        assert x["sources"], f"деталь {x['pn']} без источника"
