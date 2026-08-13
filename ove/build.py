#!/usr/bin/env python3
"""Сборка ove/public/index.html — сайт проекта ОВЭ-75 (КГМК, Мончегорск).

Данные: ove/data/*.json. Плейсхолдеры в ove/site/index.template.html —
__PROJECT_JSON__, __LOTS_JSON__, __EQUIP_JSON__, __FLOW_JSON__, __STAGES_JSON__,
__RES_JSON__, __AUTO_JSON__, __WORLD_JSON__, __CHINA_JSON__, __TENDER_JSON__,
__CAPEX_JSON__, __SUP_JSON__, __BITRIX_JSON__, __BUILT_AT__.

Запуск:  python3 ove/build.py
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SITE = ROOT / "site"
OUT = ROOT / "public"

FILES = {
    "__PROJECT_JSON__": "project.json",
    "__LOTS_JSON__": "lots.json",
    "__EQUIP_JSON__": "equipment.json",
    "__FLOW_JSON__": "flowsheet.json",
    "__STAGES_JSON__": "stages.json",
    "__RES_JSON__": "resources.json",
    "__AUTO_JSON__": "automation.json",
    "__WORLD_JSON__": "world.json",
    "__CHINA_JSON__": "china.json",
    "__TENDER_JSON__": "tender.json",
    "__CAPEX_JSON__": "capex.json",
    "__SUP_JSON__": "suppliers.json",
    "__BITRIX_JSON__": "bitrix_ove.json",
}

# Файлы, которых может ещё не быть: собираются агентами. Пока файла нет —
# на его место идёт null, и вкладка честно показывает «раздел в работе».
OPTIONAL = {
    "__BI1_JSON__": "bi_lot1.json",
    "__BI2_JSON__": "bi_lot2.json",
    "__BI3_JSON__": "bi_lot3.json",
    "__BI4_JSON__": "bi_lot4.json",
    "__COMPETITOR_JSON__": "competitor.json",
    "__PARTNER_JSON__": "partner.json",
}


def compact(path: Path) -> str:
    """Валидируем JSON и вставляем компактно; </ нейтрализуем — данные едут в <script>."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build() -> None:
    tpl = (SITE / "index.template.html").read_text(encoding="utf-8")
    for key, name in FILES.items():
        assert key in tpl, f"нет плейсхолдера {key} в шаблоне"
        tpl = tpl.replace(key, compact(DATA / name))
    pending = []
    for key, name in OPTIONAL.items():
        assert key in tpl, f"нет плейсхолдера {key} в шаблоне"
        path = DATA / name
        if path.exists():
            tpl = tpl.replace(key, compact(path))
        else:
            tpl = tpl.replace(key, "null")
            pending.append(name)
    if pending:
        print("в работе (пока null):", ", ".join(pending))
    tpl = tpl.replace("__BUILT_AT__", date.today().isoformat())
    assert "__" + "JSON__" not in tpl, "остались незаменённые плейсхолдеры"

    OUT.mkdir(exist_ok=True)
    out = OUT / "index.html"
    out.write_text(tpl, encoding="utf-8")

    bx = json.loads((DATA / "bitrix_ove.json").read_text(encoding="utf-8"))
    eq = json.loads((DATA / "equipment.json").read_text(encoding="utf-8"))
    sup = json.loads((DATA / "suppliers.json").read_text(encoding="utf-8"))
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ) | "
          f"позиций оборудования {len(eq['items'])}, закупочных позиций {len(sup['positions'])}, "
          f"Bitrix {'живой' if bx.get('live') else 'сид'}")


if __name__ == "__main__":
    build()
