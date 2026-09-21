#!/usr/bin/env python3
"""Сборка gt/public/faults.html — признак → дефект → ремонт → номера для ГТУ и ГПУ.

Звено «признак → дефект» цепочки портала: вход в номенклатуру от того, что видит
эксплуатант, а не от каталожного номера. По ГШО оно закрыто досье машины
(zip/data/r1700.json), здесь — двумя срезами направлений: gt/data/faults.json и
gpu/data/faults.json. Срез лежит файлом направления, а не внутри машины: по ГТУ и
ГПУ машин десятки, и строка чаще относится к классу, чем к одной модели.

Вердикт строки разделяет два разных утверждения и не даёт их перепутать:
«подтверждён» — строка дословно есть в скачанном документе, «не проверялся» —
общая инженерная практика по классу машин. На странице это видно у каждой строки,
а в счётчик заполняемости цепочки идут только подтверждённые.

Запуск: python gt/tools/build_faults.py
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent

SRC = {"gtu": REPO / "gt" / "data" / "faults.json",
       "gpu": REPO / "gpu" / "data" / "faults.json"}


def slice_of(path: Path) -> dict:
    """Срез направления. Отсутствующий файл — пустое звено, а не ошибка сборки."""
    if not path.exists():
        return {"rows": [], "gaps": "срез направления ещё не собран"}
    d = json.loads(path.read_text(encoding="utf-8"))
    gaps = d.get("gaps")
    if isinstance(gaps, list):
        gaps = " ".join(str(x) for x in gaps)
    return {"rows": d.get("rows") or [], "gaps": gaps or ""}


def main() -> int:
    data = {k: slice_of(p) for k, p in SRC.items()}
    for k, d in data.items():
        rows = d["rows"]
        ok = sum(1 for x in rows if x.get("verdict") == "подтверждён")
        d["stats"] = {"rows": len(rows), "confirmed": ok,
                      "nodes": len({x.get("node") for x in rows if x.get("node")}),
                      "parts": len({p for x in rows for p in (x.get("parts") or [])})}
        print(f"  · {k}: связок {len(rows)}, подтверждено {ok}, "
              f"узлов {d['stats']['nodes']}, номеров {d['stats']['parts']}")

    tpl = (ROOT / "site" / "faults.template.html").read_text(encoding="utf-8")
    assert "__FAULTS_JSON__" in tpl, "нет плейсхолдера __FAULTS_JSON__"
    out = ROOT / "public" / "faults.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        tpl.replace("__FAULTS_JSON__", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
           .replace("__BUILT__", date.today().isoformat()),
        encoding="utf-8")
    print(f"OK → {out} ({out.stat().st_size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
