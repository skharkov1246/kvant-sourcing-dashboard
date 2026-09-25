"""Проверщик разведки брендов (scripts/brand_research_check.py).

Корпус придуман (CLAUDE.md, правило 18): бренд «vydumka», коды «ВЫДУМ-…»,
адреса example.test. Последний тест гоняет проверщик по настоящему набору
data/brand_research/ — так гейт не пропустит файл бренда, собранный в обход
правил навыка .claude/skills/brand-research/SKILL.md.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location("brand_research_check",
                                                  ROOT / "scripts" / "brand_research_check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["brand_research_check"] = mod
    spec.loader.exec_module(mod)
    return mod


M = _mod()
КЛЮЧИ = {"vydumka", "drugaya", "vydumkaпопрофилю", "заказпоспецификации"}
МИНУС, NBSP = chr(0x2212), chr(0xA0)


def _бренд(**правки) -> dict:
    d = {
        "oem_key": "vydumka", "name": "Выдумка (тестовый бренд)", "segment_focus": "насосы",
        "researched_at": "2026-01-01", "method": "тестовый метод", "summary": "тестовая сводка",
        "machines": [{"model": "ВМ-1", "kind": "насос", "segment": "насосы", "rating": "1 кВт",
                      "status": "выпускается", "sources": ["https://example.test/vm1"], "verified": True}],
        "service_docs": [{"title": "Руководство ВМ-1", "machine": "ВМ-1", "kind": "O&M",
                          "sources": ["https://example.test/doc"]}],
        "parts": [{"code": "ВЫДУМ-101", "issuer": "Выдумка", "description": "фильтр", "unit": "смазка",
                   "source_url": "https://example.test/p", "quote": "Фильтр ВЫДУМ 101 для ВМ-1"}],
        "parts_rejected": [{"code": "ВЫДУМ-999", "verdict": "источник недоступен", "reason": "страница молчит"}],
        "sub_suppliers": [{"company": "Придуманный завод", "component": "подшипник", "evidence": "каталог",
                           "sources": ["https://example.test/s"]}],
        "dealers": [{"company": "Придуманный дилер", "country": "Нигдения", "role": "продажи",
                     "sources": ["https://example.test/d"]}],
        "gaps": ["каталог закрыт"], "verifier_notes": "всё сверено", "corrections": [],
    }
    d.update(правки)
    return d


def _нарушения(d, имя="vydumka.json"):
    return M.проверить_бренд(d, имя, КЛЮЧИ)


def test_чистая_запись_без_нарушений():
    assert _нарушения(_бренд()) == []


def test_код_в_цитате_с_точностью_до_пробелов_и_дефисов():
    assert M.код_в_цитате("3115 9170 91", "Seal kit 3115-9170-91")
    assert M.код_в_цитате("140-3813", f"140{МИНУС}3813 4 Element, Primary")
    assert M.код_в_цитате("MW21215M", f"Siemens{NBSP}MW21215M")
    assert M.код_в_цитате("ВЫДУМ-101", "выдум 101")
    assert not M.код_в_цитате("ВЫДУМ-102", "ВЫДУМ-101")
    assert not M.код_в_цитате("", "что угодно")


def test_код_не_из_цитаты_и_короткий_код_ловятся():
    d = _бренд()
    d["parts"][0]["quote"] = "Фильтр ВЫДУМ-100"
    assert any("код не стоит в цитате" in n for n in _нарушения(d))
    d = _бренд()
    d["parts"][0]["code"] = "1-2"
    assert any("короче трёх" in n for n in _нарушения(d))


def test_ссылка_обязательна_у_машины_кода_субпоставщика_и_дилера():
    d = _бренд()
    del d["machines"][0]["sources"]
    d["parts"][0]["source_url"] = "ftp://example.test/p"
    d["sub_suppliers"][0]["sources"] = []
    d["dealers"][0]["sources"] = ["example.test/d"]
    n = _нарушения(d)
    assert any(x.startswith("vydumka.json machines[0]") and "источник" in x for x in n)
    assert any(x.startswith("vydumka.json parts[0]") and "http" in x for x in n)
    assert any(x.startswith("vydumka.json sub_suppliers[0]") and "источник" in x for x in n)
    assert any(x.startswith("vydumka.json dealers[0]") and "http" in x for x in n)


def test_контакты_ловятся():
    for текст, вид in (("пишите ivan.petrov@primer.test", "e-mail"),
                       ("звонить +7 (000) 000-00-01", "телефон"),
                       ("тел. 000 000 00 01", "телефон"),
                       ("горячая линия 8 (800) 000-00-01", "телефон"),
                       ("лежат в /tmp/scratch/brand/", "локальный путь")):
        d = _бренд()
        d["dealers"][0]["role"] = текст
        assert any(x.endswith(f"dealers[0].role: {вид}") for x in _нарушения(d)), текст


def test_номера_деталей_и_ссылки_контактами_не_считаются():
    for текст in ("504-0262 1 Valve", "9869 0090 01b, 2022-02", "U+2212 вместо дефиса",
                  "момент 55±5 Н·м", "0242356503 Bosch", "17.03.2022 приостановил",
                  "https://example.test/9869+0107+01d+ST7+000000000", "https://example.test/home/x",
                  # без выемки ссылок эти две сошли бы за телефон и e-mail
                  "см. https://example.test/cat?+70000000001 и далее",
                  "см. https://example.test/wanted-parts-@primer.test/x"):
        assert M.найти_контакты(текст) == [], текст


def test_ключ_бренда_из_словаря_и_без_описания():
    assert any("нет в dict/oem.json" in x for x in _нарушения(_бренд(oem_key="nevedomo"), "nevedomo.json"))
    n = _нарушения(_бренд(oem_key="vydumkaпопрофилю"), "vydumkaпопрофилю.json")
    assert any("вставленным описанием" in x for x in n)
    assert any("имя файла" in x for x in _нарушения(_бренд(), "drugaya.json"))
    assert M.ключ_с_описанием("caterpillarпотипу")
    assert not M.ключ_с_описанием("мирколец")
    assert not M.ключ_с_описанием("vydumka")
    assert M.ключ_указание("заказпоспецификацииразмеры")


def test_ключ_записи_не_бренда_не_годится():
    """Запись словаря вида «несколько» или «указание» — не бренд для разведки."""
    n = M.проверить_бренд(_бренд(oem_key="drugaya"), "drugaya.json", КЛЮЧИ, {"drugaya"})
    assert any(M.НЕ_БРЕНД in x for x in n)
    assert M.проверить_бренд(_бренд(), "vydumka.json", КЛЮЧИ, {"drugaya"}) == []


def test_повтор_кода_и_принят_он_же_отклонён():
    d = _бренд()
    d["parts"].append(dict(d["parts"][0], code="выдум 101"))
    assert any("код повторяется" in x for x in _нарушения(d))
    d = _бренд()
    d["parts_rejected"][0]["code"] = "ВЫДУМ 101"
    assert any("принят и отклонён" in x for x in _нарушения(d))


def test_схема_файла():
    d = _бренд(machinez=[])
    assert any("неизвестное поле" in x for x in _нарушения(d))
    d = _бренд()
    del d["gaps"]
    d["researched_at"] = "24.09.2026"
    n = _нарушения(d)
    assert any(x.startswith("vydumka.json gaps") for x in n)
    assert any("ГГГГ-ММ-ДД" in x for x in n)
    d = _бренд()
    d["parts"][0]["quote"] = ""
    assert any("нет поля quote" in x for x in _нарушения(d))


def _очередь(*записи):
    return {"records": [dict(r) for r in записи]}


def test_очередь():
    бренды = {"vydumka": _бренд()}
    сделано = {"priority": 1, "oem_key": "vydumka", "name": "Выдумка", "focus": "насосы",
               "status": "сделано", "researched_at": "2026-01-01"}
    ждёт = {"priority": 2, "oem_key": "drugaya", "name": "Другая", "focus": "фильтры", "status": "в очереди"}
    assert M.проверить_очередь(_очередь(сделано, ждёт), КЛЮЧИ, бренды) == []

    def есть(правило, *записи, б=бренды):
        return any(правило in x for x in M.проверить_очередь(_очередь(*записи), КЛЮЧИ, б))

    assert есть("статус не из списка", сделано, dict(ждёт, status="почти"))
    assert есть("файла бренда нет", сделано, dict(ждёт, status="сделано"))
    assert есть("статус не «сделано»", dict(сделано, status="в очереди"), ждёт)
    assert есть("нет записи в очереди", ждёт)
    assert есть("повторяется", сделано, dict(ждёт, oem_key="vydumka"))
    assert есть("priority повторяется", сделано, dict(ждёт, priority=1))
    assert есть("дата разведки", dict(сделано, researched_at="2026-01-02"), ждёт)
    assert есть("нет в dict/oem.json", сделано, dict(ждёт, oem_key="nevedomo"))
    assert есть("указание к закупке", сделано, dict(ждёт, oem_key="заказпоспецификации"))


def _набор(tmp_path: Path, бренд: dict, очередь: dict) -> tuple[Path, Path]:
    папка = tmp_path / "br"
    папка.mkdir()
    (папка / f"{бренд['oem_key']}.json").write_text(json.dumps(бренд, ensure_ascii=False), encoding="utf-8")
    (папка / "queue.json").write_text(json.dumps(очередь, ensure_ascii=False), encoding="utf-8")
    словарь = tmp_path / "oem.json"
    словарь.write_text(json.dumps({"records": [{"oem_key": k} for k in sorted(КЛЮЧИ)]}, ensure_ascii=False),
                       encoding="utf-8")
    return папка, словарь


def test_main_код_возврата_и_печать_только_агрегатов(tmp_path, capsys):
    очередь = _очередь({"priority": 1, "oem_key": "vydumka", "name": "Выдумка", "focus": "насосы",
                        "status": "сделано", "researched_at": "2026-01-01"})
    папка, словарь = _набор(tmp_path, _бренд(), очередь)
    assert M.main(["--dir", str(папка), "--dict", str(словарь)]) == 0
    assert "нарушений нет" in capsys.readouterr().out

    плохой = copy.deepcopy(_бренд())
    плохой["dealers"][0]["role"] = "пишите ivan.petrov@primer.test"
    плохой["parts"][0]["quote"] = "Фильтр ВЫДУМ-100"
    (tmp_path / "2").mkdir()
    папка, словарь = _набор(tmp_path / "2", плохой, очередь)
    assert M.main(["--dir", str(папка), "--dict", str(словарь)]) == 1
    out = capsys.readouterr().out
    assert "нарушений: 2" in out
    for значение in ("ivan.petrov", "primer.test", "ВЫДУМ", "Придуманный", "Фильтр"):
        assert значение not in out, значение


def test_набор_в_репозитории_проходит_проверку(capsys):
    """Настоящие файлы data/brand_research/ — по тем же правилам."""
    assert M.main([]) == 0, capsys.readouterr().out
