#!/usr/bin/env python3
"""Разбор разведки по диагностике и дефектам в zip/data/diagnostics_recon.json.

Пять углов разведки, у каждого — собственный скептик с заданием опровергать
по умолчанию. Каждый факт и каждый дефект получает ЯВНЫЙ вердикт: пустое поле
читалось бы как «проверено, всё хорошо», а это было бы ложью. Поэтому
«скептик не сослался» и «угол без скептика» — разные значения.

Запуск:  python zip/tools/extract_diagnostics.py <каталог-журнала>
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "zip" / "data" / "diagnostics_recon.json"

TITLES = {
    "common/vibro": "Вибродиагностика: нормы, спектральные признаки, уставки",
    "common/other_methods": "Невибрационные методы: масло, тепловизор, ультразвук, МССА, НК",
    "turbo/defects": "Турбомашины: дефекты ГТУ, ПТУ, турбокомпрессоров, электрических машин",
    "pumps/defects": "Насосы: дефекты, зазоры, кавитация, торцевые уплотнения",
    "recip/defects": "Поршневые компрессоры: дефекты клапанов, колец, штока, PV-диаграмма",
}

# «Утв. 12», «12.», «Утверждение 12»
NUM = re.compile(r"^(?:утв\w*\.?\s*)?(\d{1,3})\s*[.):–-]", re.I)
# «КАТАЛОГ, «Масляный вихрь»», «Каталог дефектов: «Обрыв стержней»»
CAT = re.compile(r"^катал\w+[^«»]*[«\"]([^«»\"]+)[»\"]", re.I)


def norm(s):
    return re.sub(r"[^а-яёa-z0-9]+", " ", str(s or "").lower().replace("ё", "е")).strip()


def words(s):
    return {w for w in norm(s).split() if len(w) > 3}


def best_defect(name, defects):
    """Ближайший дефект по названию: доля общих слов, порог 0,4."""
    target = words(name)
    if not target:
        return None
    best, score = None, 0.0
    for i, d in enumerate(defects):
        cur = words(d["defect"]) | words(d["node"])
        if not cur:
            continue
        ratio = len(target & cur) / len(target)
        if ratio > score:
            best, score = i, ratio
    return best if score >= 0.4 else None


def attach(angle, verdicts):
    """Разложить вердикты скептика по фактам и дефектам угла."""
    findings = angle["findings"]
    defects = angle["defects"]
    for f in findings:
        f["verdict"] = {"verdict": "скептик не сослался", "why": "", "correction": ""}
    for d in defects:
        d["verdict"] = {"verdict": "скептик не сослался", "why": "", "correction": ""}
    orphans = []
    for v in verdicts:
        claim = (v.get("claim") or "").strip()
        body = {"verdict": v.get("verdict") or "не указан",
                "why": v.get("why") or "",
                "correction": v.get("correction") or "",
                "claim": claim}
        m = NUM.match(claim)
        if m:
            i = int(m.group(1)) - 1
            if 0 <= i < len(findings):
                findings[i]["verdict"] = body
                continue
        m = CAT.match(claim)
        if m:
            j = best_defect(m.group(1), defects)
            if j is not None:
                defects[j]["verdict"] = body
                continue
        j = best_defect(claim, defects)
        if j is not None and defects[j]["verdict"]["verdict"] == "скептик не сослался":
            defects[j]["verdict"] = body
            continue
        orphans.append(body)
    return orphans


def main(src):
    src = Path(src)
    rows = [json.loads(l) for l in (src / "journal.jsonl").read_text().splitlines() if l.strip()]
    results = [r["result"] for r in rows if r.get("type") == "result"]
    recon = {f"{r['scope']}/{r['angle']}": r for r in results if "findings" in r}
    skept = {f"{r['scope']}/{r['angle']}": r for r in results if "verdicts" in r}

    angles = []
    for key in sorted(recon, key=lambda k: (k.split("/")[0], k)):
        r = recon[key]
        s = skept.get(key)
        a = {
            "key": key,
            "title": TITLES.get(key, key),
            "scope": r["scope"],
            "summary": r.get("summary") or "",
            "findings": [dict(f) for f in r["findings"]],
            "defects": [dict(d) for d in r["defects"]],
            "dead_ends": list(r.get("dead_ends") or []),
        }
        if s:
            a["skeptic"] = {
                "overall": s.get("overall") or "",
                "missing": list(s.get("missing") or []),
                "orphan_verdicts": attach(a, s["verdicts"]),
            }
        else:
            for f in a["findings"]:
                f["verdict"] = {"verdict": "угол без скептика", "why": "", "correction": ""}
            for d in a["defects"]:
                d["verdict"] = {"verdict": "угол без скептика", "why": "", "correction": ""}
            a["skeptic"] = None
        angles.append(a)

    by_verdict, by_node, by_conf = {}, {}, {}
    for a in angles:
        for item in a["findings"] + a["defects"]:
            v = item["verdict"]["verdict"]
            by_verdict[v] = by_verdict.get(v, 0) + 1
            by_conf[item.get("confidence") or "не указана"] = \
                by_conf.get(item.get("confidence") or "не указана", 0) + 1
        for d in a["defects"]:
            by_node[d["node"]] = by_node.get(d["node"], 0) + 1

    data = {
        "generated": date.today().isoformat(),
        "subject": "Диагностика динамического оборудования: признак → дефект → метод → ремонт → запчасть",
        "method": (
            "Пять параллельных углов разведки, у каждого — собственный скептик с заданием "
            "опровергать по умолчанию. Вердикт проставлен каждому факту и каждому дефекту: "
            "«скептик не сослался» означает, что проверка этого пункта не проводилась, "
            "а не что он верен."
        ),
        "caveat": (
            "Ограничение, заявленное самими скептиками и сохранённое дословно: бюджет веб-поиска "
            "был исчерпан, iso.org и astm.org отдавали 403. Полные тексты ISO 20816-1, ISO 20816-4, "
            "ISO 10816-7, ISO 13373-1/2/3 и ASTM D4378 удалось выгрузить и сверить, а ISO 10816-3, "
            "ISO 13373-9, ISO 13379-1 и ISO 17359 — нет. Численные значения из несверенных "
            "стандартов перед рабочим применением обязаны быть подтверждены по официальному тексту."
        ),
        "stats": {
            "angles": len(angles),
            "with_skeptic": sum(1 for a in angles if a["skeptic"]),
            "findings": sum(len(a["findings"]) for a in angles),
            "defects": sum(len(a["defects"]) for a in angles),
            "nodes": len(by_node),
            "dead_ends": sum(len(a["dead_ends"]) for a in angles),
            "missing": sum(len(a["skeptic"]["missing"]) for a in angles if a["skeptic"]),
            "by_verdict": dict(sorted(by_verdict.items(), key=lambda kv: -kv[1])),
            "by_node": dict(sorted(by_node.items(), key=lambda kv: -kv[1])),
            "by_confidence": dict(sorted(by_conf.items(), key=lambda kv: -kv[1])),
        },
        "angles": angles,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    st = data["stats"]
    print(f"углов {st['angles']}, фактов {st['findings']}, дефектов {st['defects']}, "
          f"узлов {st['nodes']}, тупиков {st['dead_ends']}, пропусков {st['missing']}")
    print("вердикты:", st["by_verdict"])
    for a in angles:
        orph = len(a["skeptic"]["orphan_verdicts"]) if a["skeptic"] else 0
        print(f"  {a['key']:24s} фактов {len(a['findings']):3d} дефектов {len(a['defects']):3d} "
              f"без адреса {orph}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
