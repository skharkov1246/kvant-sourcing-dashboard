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
    "__MARKET_JSON__": "market.json",
    "__ALT_JSON__": "alternative.json",
    "__QUESTIONS_JSON__": "questions.json",
    "__TIANXIN_JSON__": "tianxin.json",
    "__SOFTWARE_JSON__": "software.json",
    "__ANSWERS_JSON__": "questions_answers.json",
    "__REFRAME_JSON__": "reframe.json",
    "__DISC_JSON__": "discrepancies.json",
    "__DELIV_JSON__": "deliverables.json",
    "__CALC_JSON__": "calc.json",
    "__HEDGE_JSON__": "hedge.json",
    "__BIDOCS_JSON__": "bi_docs.json",
    "__APPLY_JSON__": "podacha.json",
    "__GANTT_JSON__": "gantt.json",
    "__READY_JSON__": "readiness.json",
    "__REVS_JSON__": "revisions.json",
    "__ECO_JSON__": "eco_prelim.json",
    "__AUDIT_JSON__": "audit_log.json",
    "__RFQFILES_JSON__": "rfq_files.json",
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
    pending, held = [], []
    for key, name in OPTIONAL.items():
        assert key in tpl, f"нет плейсхолдера {key} в шаблоне"
        path = DATA / name
        if not path.exists():
            tpl = tpl.replace(key, "null")
            pending.append(name)
            continue
        obj = json.loads(path.read_text(encoding="utf-8"))
        # publish:false — файл есть, но на открытый сайт не идёт (персональные
        # и финансовые данные). Снимается переключением одного значения.
        if isinstance(obj, dict) and obj.get("publish") is False:
            tpl = tpl.replace(key, "null")
            held.append(name)
            continue
        tpl = tpl.replace(key, compact(path))
    if pending:
        print("в работе (пока null):", ", ".join(pending))
    if held:
        print("НЕ публикуется (publish=false):", ", ".join(held))
    tpl = tpl.replace("__BUILT_AT__", date.today().isoformat())
    assert "__" + "JSON__" not in tpl, "остались незаменённые плейсхолдеры"

    OUT.mkdir(exist_ok=True)
    out = OUT / "index.html"
    out.write_text(tpl, encoding="utf-8")

    # сводный Word-документ для выгрузки командой (реестр расхождений + вопросы);
    # решения из общей базы вшиваются автоматически при каждой пересборке
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "tools"))
    import build_docx
    build_docx.build()
    # расчётная записка (независимый пересчёт КВАНТ) — из calc.json
    import build_calcnote
    build_calcnote.build()
    # черновики ПЗ по лотам (из bi_lot*.json) и пакет подачи на конкурс
    import build_bi_docs
    build_bi_docs.build()
    import build_apply_docs
    build_apply_docs.build()
    # спецификации и опросные листы, пакет запросов ТКП, план реализации ЦИМ
    import build_specs
    build_specs.build()
    import build_rfq
    build_rfq.build()
    import build_bim_plan
    build_bim_plan.build()
    # сводная ведомость закладных решений для цифровизации (из zakladki.json)
    import build_zakladki
    build_zakladki.build()
    # единые метаданные всех выпускаемых DOCX/PDF (автор — ООО «КВАНТ»)
    import doc_meta
    doc_meta.build()
    # ZIP-пакеты документов по лотам и пакет подачи (после метаданных)
    import build_paket
    build_paket.build()

    bx = json.loads((DATA / "bitrix_ove.json").read_text(encoding="utf-8"))
    eq = json.loads((DATA / "equipment.json").read_text(encoding="utf-8"))
    sup = json.loads((DATA / "suppliers.json").read_text(encoding="utf-8"))
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ) | "
          f"позиций оборудования {len(eq['items'])}, закупочных позиций {len(sup['positions'])}, "
          f"Bitrix {'живой' if bx.get('live') else 'сид'}")


if __name__ == "__main__":
    build()
