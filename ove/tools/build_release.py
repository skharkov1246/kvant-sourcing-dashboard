#!/usr/bin/env python3
"""Реестр выпуска БИ (bi_register.json + bi_release.json) → bi_docs.json для сайта.

Сайт и ZIP-пакеты читают bi_docs.json. После сборки выпуска (docframe.build)
каждая строка реестра получает ровно один файл — PDF Ревизии 0; исходники
(DOCX/SVG/IFC) идут в архив лота отдельной папкой src/. Строки без собранного
PDF остаются в реестре со статусом «в работе» и без кнопки — честно, а не пусто.
Вызывается из ove/build.py после docframe.build().
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOTS = {0: "Общее по комплексу", 1: "Цех обжига", 2: "Участок купороса",
        3: "Отделение электроэкстракции", 4: "Склад готовой продукции"}
NOTE = ("Выпуск Базового инжиниринга, Ревизия 0 — черновики для согласования состава и решений "
        "с Заказчиком. Каждая строка состава ТЗ — отдельный документ с обозначением, титульным "
        "листом и штампом «Эскизная проработка — не для строительства». Архив лота содержит PDF "
        "всех документов лота и папку src с исходниками (Word, SVG, DXF, IFC).")


def build() -> Path:
    reg = json.loads((DATA / "bi_register.json").read_text(encoding="utf-8"))
    rel_path = DATA / "bi_release.json"
    rel = json.loads(rel_path.read_text(encoding="utf-8")) if rel_path.exists() else {"docs": []}
    made = {d["code"]: d for d in rel.get("docs", [])}
    lots = {}
    for r in reg["rows"]:
        lot = r.get("lot") or 0
        L = lots.setdefault(lot, {"lot": lot, "name": LOTS[lot], "docs": []})
        m = made.get(r["code"])
        files = [{"path": m["pdf"], "tag": "PDF"}] if m and m.get("pdf") else []
        src = list(r.get("sources") or [])
        if m and m.get("docx"):
            src.insert(0, m["docx"])
        L["docs"].append({
            "code": r["code"], "name": r["title"], "tz": r.get("tz", ""), "stage": r.get("stage", 1),
            "kind": r.get("kind", "text"), "rev": rel.get("rev", "0"),
            "status": "draft" if files else "task",
            "files": files, "src": src,
            "note": r.get("note", "") if files else "в работе — документ собирается",
        })
    out = {"updated": date.today().isoformat(), "note": NOTE,
           "statuses": {"draft": "Ревизия 0 — для согласования", "task": "в работе"},
           "rev": rel.get("rev", "0"),
           "lots": [lots[k] for k in (1, 2, 3, 4, 0) if k in lots]}
    p = DATA / "bi_docs.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n = sum(len(L["docs"]) for L in out["lots"])
    k = sum(1 for L in out["lots"] for d in L["docs"] if d["files"])
    print(f"bi_docs.json: строк {n}, с PDF Р0 — {k}")
    return p


if __name__ == "__main__":
    build()
