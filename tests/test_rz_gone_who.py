"""Список человека «Кто сколько ведёт» у уволенного открывается (сбой деплоя 30.10.2026).

Боевая сборка 30.10.2026 остановилась на проверке страницы: «число w:who/204/wait не открыло
список заказов». В данных ушедший подписан «Имя (уволен)» (contracts.who), ключ списка — его id,
а обратный разбор id давал имя без подписи — списка с таким именем в drill.who нет. Корпус тот
же, что у сборки, только менеджер ОРК «Олег» уволен: у него «ждём УПД» (1 заказ).

Проверяется двумя способами: разбор ключа списка в странице (_odResolve) и та же проверка
открытых вкладок, что у деплоя (validate_dashboard.check_browser). Корпус придуман
(CLAUDE.md, правило 18).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

WHO = "Олег (ОРК)"
GONE = WHO + " (уволен)"


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    import dashboard
    from tests import fixture
    r = fixture.build_realization(hist=False)
    W = r["contracts"]["ork"]["week"]
    assert (W["drill"]["who"].get(WHO) or {}).get("wait"), "в корпусе у менеджера должен быть «ждём УПД»"
    # уволенный — как его подписывает contracts.who: та же подпись во всех разделах
    r = json.loads(json.dumps(r, ensure_ascii=False).replace(json.dumps(WHO, ensure_ascii=False),
                                                              json.dumps(GONE, ensure_ascii=False)))
    assert GONE in r["contracts"]["ork"]["week"]["drill"]["who"]
    f = tmp_path_factory.mktemp("rz_gone") / "index.html"
    dashboard.write(fixture.build_metrics(), {"source": "rules", "items": []}, str(f),
                    people=fixture.build_people(), **r)
    return f


def test_ключ_уволенного_находит_его_список(page):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:                       # noqa: BLE001 — своя сборка Chromium не скачана
            from scripts.validate_dashboard import find_chrome
            exe, _v = find_chrome()
            if not exe:
                pytest.skip("нет Chromium")
            b = p.chromium.launch(executable_path=exe)
        pg = b.new_page()
        pg.goto(page.resolve().as_uri())
        got = pg.evaluate("""w=>{const C=window.__CONTRACTS__, id=_odWhoKey(w);
            const r=k=>(_odResolve(C,C.ork,'w:who/'+id+'/'+k)||{}).k||null;
            return {id, wait:r('wait'), over:r('over'), want:(C.ork.week.drill.who[w]||{}).wait||[]};}""", GONE)
        b.close()
    assert got["id"].isdigit(), f"ключ уволенного — id, а не имя: {got['id']}"
    assert got["wait"] is not None and sorted(map(str, got["wait"])) == sorted(map(str, got["want"]))
    assert got["over"] is not None


def test_проверка_деплоя_проходит(page):
    from scripts import validate_dashboard as vd
    if not vd.find_chrome()[0]:
        pytest.skip("нет Chromium")
    errors: list[str] = []
    warns: list[str] = []
    vd.check_browser(page, errors, warns)
    assert not [e for e in errors if "w:who/" in e], errors
    assert not errors, errors
