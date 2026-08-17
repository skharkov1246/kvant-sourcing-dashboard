#!/usr/bin/env python3
"""Применение вердиктов скептика по плашкам моделей → gt/data/badge_overrides.json.

Вход: journal.jsonl прогона workflow-проверки (по одному {"type":"result"} на агента).
Каждый check: company, verdict, cause, models_confirmed, models_refuted, fams_verdict,
evidence_url, seen_quote, note.

Что делает:
1) канонизирует названия моделей из свободного текста агента к словарю сайта
   (иначе на карточке появятся плашки-самоделки вида «SGT5-2000E (V94.2)»);
2) сверяет имя компании с ключами живой базы (nrm), несовпадения печатает — их
   надо править руками, молча терять вердикт нельзя;
3) fams_verdict=drop → гасим семейные флаги, которые у компании были;
4) мержит в существующий badge_overrides.json (волны накапливаются).

Запуск:
  python3 gt/tools/apply_badge_checks.py <journal.jsonl> <uni.json> [<sample.json>]
где uni.json — дамп карточек сайта (name+флаги), sample.json — что уходило на проверку.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/badge_overrides.json"

# словарь моделей сайта = MODEL_GROUPS в index.template.html. Порядок важен:
# сначала специфичные (SGT5-2000E), потом общие (SGT-200), иначе «SGT-2000E» съест «SGT-200».
CANON = [
    (r"SGT5[- ]?2000E|V94\.?2|ГТЭ[- ]?160", "SGT5-2000E (V94.2/ГТЭ-160)"),
    (r"SGT5[- ]?4000F|V94\.?3", "SGT5-4000F (V94.3A)"),
    (r"V64\.?3|AE64", "V64.3A (AE64.3A)"),
    (r"SGT[- ]?100\b|Typhoon", "SGT-100"),
    (r"SGT[- ]?200\b|Tornado", "SGT-200"),
    (r"SGT[- ]?300\b|Tempest", "SGT-300"),
    (r"SGT[- ]?400\b|Cyclone", "SGT-400"),
    (r"SGT[- ]?500\b|GT35", "SGT-500"),
    (r"SGT[- ]?600\b|GT10B", "SGT-600"),
    (r"SGT[- ]?700\b|GT10C", "SGT-700"),
    (r"SGT[- ]?750\b", "SGT-750"),
    (r"SGT[- ]?800\b|GTX100", "SGT-800"),
    (r"Saturn", "Saturn 20"),
    (r"Centaur\w*[- ]?40", "Centaur 40"),
    (r"Centaur\w*[- ]?50", "Centaur 50"),
    (r"Taurus\w*[- ]?60|\bT60\b", "Taurus 60"),
    (r"Taurus\w*[- ]?70|\bT70\b", "Taurus 70"),
    (r"Mars[- ]?90", "Mars 90"),
    (r"Mars[- ]?100", "Mars 100"),
    (r"Titan[- ]?130", "Titan 130"),
    (r"Titan[- ]?250", "Titan 250"),
    (r"GT13", "GT13E2"),
    (r"LM[- ]?2500|PGT[- ]?25", "LM2500 (PGT25)"),
    (r"LM[- ]?6000", "LM6000"),
    (r"RB[- ]?211|SGT-A35", "RB211 (SGT-A35)"),
    (r"9F[AB]?\b|MS9001", "GE 9F/9FA"),
    (r"6F\.?03|6FA", "GE 6F.03 (6FA)"),
    (r"MS7001|7EA|Frame\s?7", "GE Frame 7 (MS7001)"),
    (r"MS6001|Frame\s?6\b|\b6B\b", "GE Frame 6B (MS6001)"),
    (r"MS5002|Frame\s?5\b", "GE MS5002"),
    (r"MS5001", "GE MS5001"),
]
CANON = [(re.compile(p, re.I), n) for p, n in CANON]

FAMS = ("sgtAll", "solarAll", "heavyAll")


def nrm(s):
    return re.sub(r"[^a-zа-яё0-9]", "", (s or "").lower())


def canon_models(items):
    """['SGT5-2000E (V94.2)', 'GE CT7'] → ['SGT5-2000E (V94.2/ГТЭ-160)'] (CT7 не наша модель)."""
    out = []
    for raw in items or []:
        # «Solar (семейство — как OEM-заказчик)» и прочие оговорки моделью не считаем
        if re.search(r"семейств|family|линейк", raw, re.I) and not re.search(
            r"saturn|centaur|taurus|mars|titan|sgt|ms\d|frame|lm\d", raw, re.I
        ):
            continue
        for rx, name in CANON:
            if rx.search(raw) and name not in out:
                out.append(name)
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    journal, uni_path = Path(sys.argv[1]), Path(sys.argv[2])
    sample_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    checks = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "result":
            continue
        res = rec.get("result")
        if isinstance(res, dict) and "checks" in res:
            checks.extend(res["checks"])
        elif isinstance(res, dict) and "verdict" in res:
            checks.append(res)
    print(f"вердиктов в журнале: {len(checks)}")

    uni = {nrm(r["name"]): r for r in json.loads(uni_path.read_text(encoding="utf-8"))}
    sample = {}
    if sample_path and sample_path.exists():
        sample = {nrm(r["name"]): r for r in json.loads(sample_path.read_text(encoding="utf-8"))}

    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"checks": {}}
    added = updated = 0
    unmatched = []
    for c in checks:
        name = (c.get("company") or "").replace("&amp;", "&").strip()
        k = nrm(name)
        if not k:
            continue
        src = uni.get(k) or sample.get(k)
        if not src:
            # агент часто дописывает страну: «Doosan Enerbility (Республика Корея)» —
            # отрезаем последнюю скобку, затем пробуем однозначное совпадение по префиксу
            bare = nrm(re.sub(r"\s*\([^()]*\)\s*$", "", name))
            src = uni.get(bare) or sample.get(bare)
            if not src and len(bare) >= 6:
                cand = [v for kk, v in uni.items() if kk.startswith(bare) or bare.startswith(kk)]
                if len({v["name"] for v in cand}) == 1:
                    src = cand[0]
        if not src:
            unmatched.append(name)
            continue
        had = [f for f in FAMS if (src.get(f) if isinstance(src, dict) else False)]
        fams_off = had if (c.get("fams_verdict") == "drop" or c.get("cause") == "not_a_supplier") else []
        models = canon_models(c.get("models_confirmed"))
        if c.get("cause") == "not_a_supplier":
            models = []
        entry = {
            "verdict": c.get("verdict", ""),
            "cause": c.get("cause", ""),
            "models": models,
            "fams_off": fams_off,
            "url": (c.get("evidence_url") or "")[:300],
            "quote": (c.get("seen_quote") or "").replace("&amp;", "&")[:220],
            "note": (c.get("note") or "").replace("&amp;", "&")[:300],
        }
        # имя пишем как в живой базе, чтобы ключ гарантированно сходился на сборке
        key = src["name"] if isinstance(src, dict) and src.get("name") else name
        if key in data["checks"]:
            updated += 1
        else:
            added += 1
        data["checks"][key] = entry

    data["updated"] = date.today().isoformat()
    data["source"] = ("скептик-проверка плашек: по каждой компании открыт сайт и приведена дословная цитата; "
                      f"накоплено вердиктов — {len(data['checks'])}")
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"записано: +{added} новых, {updated} обновлено, всего {len(data['checks'])}")
    if unmatched:
        print(f"НЕ СОПОСТАВЛЕНО с базой ({len(unmatched)}) — проверить руками:")
        for n in unmatched:
            print("  ·", n)
    stat = {}
    for v in data["checks"].values():
        stat[v["verdict"]] = stat.get(v["verdict"], 0) + 1
    print("итог по вердиктам:", stat)


if __name__ == "__main__":
    main()
