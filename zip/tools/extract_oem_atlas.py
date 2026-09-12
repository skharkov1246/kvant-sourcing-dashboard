#!/usr/bin/env python3
"""Атлас производителей и моделей → zip/data/oem_atlas.json.

ЗАЧЕМ. Владелец просил: топовые западные и китайские производители, их
стандартные линейки, что снято с производства, как читается их номер детали.
Разведка шла по семи парам «направление × регион»; вернулись семь из
восемнадцати запланированных, остальные упёрлись в лимит сессии.

ЧТО ВАЖНО В РАЗБОРЕ. Поле country в разведке — свободный текст: «Германия»,
«Германия, Фридрихсхафен», «США / Италия (инженерия — Флоренция)». Считать
такие значения ключом нельзя: получится 70 стран вместо 15. Страна выделяется
отдельно, а исходное значение сохраняется целиком — в нём адрес завода, и он
нужнее ключа.

ПОЛНОТА ЗАЯВЛЕНА ЧИСЛОМ. В файле лежит, сколько пар из скольких вернулось:
атлас неполон, и это должно быть видно, а не обнаружиться при пользовании.

Запуск:  python zip/tools/extract_oem_atlas.py [<каталог-журнала>]
         python zip/tools/extract_oem_atlas.py --check
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "oem_atlas.json"
JOURNAL = ("/root/.claude/projects/-home-user-kvant-sourcing-dashboard/"
           "22942900-899d-580c-84cd-eb230f46e70b/subagents/workflows/wf_12ce6f58-6a8")

SEG = {"gtu": "ГТУ — газотурбинные", "gpu": "ГПУ — газопоршневые",
       "gsho": "ГШО — горно-шахтное", "recip": "Поршневые компрессоры",
       "pumps": "Насосы", "instrum": "КИПиА", "electro": "Электротехника и приводы"}
REG = {"west": "Запад", "china": "Китай", "ru": "Россия"}
# Восемнадцать пар было запланировано: семь направлений × два-три региона.
PLANNED = 18

COUNTRY = [
    ("Китай", r"кита|кнр|china"), ("США", r"\bсша\b|usa|united states"),
    ("Германия", r"герман|germany"), ("Япония", r"япон|japan"),
    ("Италия", r"итали|italy"), ("Швейцария", r"швейцар|switzerland"),
    ("Швеция", r"швеци|sweden"), ("Финляндия", r"финлянд|finland"),
    ("Нидерланды", r"нидерланд|голланд|netherlands"), ("Австрия", r"австри(?!йск\w+ вен)|austria"),
    ("Великобритания", r"великобритан|британ|англи|\buk\b|united kingdom"),
    ("Дания", r"дани[яи]|denmark"), ("Норвегия", r"норвег|norway"),
    ("Франция", r"франци|france"), ("Чехия", r"чехи|czech"),
    ("Канада", r"канад|canada"), ("Республика Корея", r"коре|korea"),
    ("Бельгия", r"бельги|belgium"), ("Индия", r"инди[яи]|india"),
    ("Польша", r"польш|poland"), ("Бразилия", r"бразил|brazil"),
]


def country_of(raw: str) -> str:
    """Первая страна, названная в свободном тексте. Порядок слева направо:
    «США / Италия» — американская компания с итальянским производством."""
    t = str(raw or "").lower().replace("ё", "е")
    hits = [(m.start(), name) for name, pat in COUNTRY
            for m in [re.search(pat, t)] if m]
    if not hits:
        return "не определена"
    return min(hits)[1]


def read(src: Path):
    rows = [json.loads(l) for l in (src / "journal.jsonl").read_text().splitlines() if l.strip()]
    return [r["result"] for r in rows if r.get("type") == "result" and "makers" in (r.get("result") or {})]


def build(src: Path) -> dict:
    res = read(src)
    pairs, makers, by_country, by_seg = [], [], {}, {}
    for r in res:
        seg, reg = r["segment"], r["region"]
        pairs.append({"segment": seg, "segment_title": SEG.get(seg, seg),
                      "region": reg, "region_title": REG.get(reg, reg),
                      "summary": r.get("summary") or "",
                      "makers": len(r["makers"]),
                      "licensed": list(r.get("licensed") or []),
                      "dead_ends": list(r.get("dead_ends") or [])})
        for m in r["makers"]:
            c = country_of(m.get("country"))
            rec = {"name": m["name"], "country": c, "country_raw": m.get("country") or "",
                   "country_note": m.get("country_note") or "",
                   "owner": m.get("owner") or "", "former_names": m.get("former_names") or "",
                   "lines": m.get("lines") or "", "specs": m.get("specs") or "",
                   "active_lines": m.get("active_lines") or "",
                   "discontinued": m.get("discontinued") or "",
                   "rank": m.get("rank") or "", "pn_system": m.get("pn_system") or "",
                   "site": m.get("site") or "", "source": m.get("source") or "",
                   "confidence": m.get("confidence") or "не указана",
                   "segment": seg, "segment_title": SEG.get(seg, seg),
                   "region": reg, "region_title": REG.get(reg, reg)}
            makers.append(rec)
            by_country[c] = by_country.get(c, 0) + 1
            by_seg[SEG.get(seg, seg)] = by_seg.get(SEG.get(seg, seg), 0) + 1

    # Расшифровка номера — то, ради чего атлас в основном и собирался.
    with_pn = [m for m in makers if len(m["pn_system"]) > 40]
    return {
        "generated": date.today().isoformat(),
        "subject": "Атлас производителей динамического оборудования: линейки, снятое с производства, расшифровка номера",
        "completeness": {
            "pairs_done": len(pairs), "pairs_planned": PLANNED,
            "pairs_missing": [f"{SEG.get(s, s)} × {REG.get(g, g)}"
                              for s in SEG for g in ("west", "china")
                              if not any(p["segment"] == s and p["region"] == g for p in pairs)],
            "note": "Атлас неполон: разведка по недостающим парам упёрлась в лимит сессии "
                    "и будет дозапущена. Число сделанных пар стоит рядом с числом "
                    "запланированных, чтобы неполнота не обнаружилась при пользовании.",
        },
        "stats": {
            "makers": len(makers),
            "countries": len(by_country),
            "with_pn_rule": len(with_pn),
            "licensed": sum(len(p["licensed"]) for p in pairs),
            "dead_ends": sum(len(p["dead_ends"]) for p in pairs),
            "by_country": dict(sorted(by_country.items(), key=lambda kv: -kv[1])),
            "by_segment": dict(sorted(by_seg.items(), key=lambda kv: -kv[1])),
        },
        "pairs": pairs,
        "makers": sorted(makers, key=lambda m: (m["segment"], m["country"], m["name"])),
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = Path(args[0]) if args else Path(JOURNAL)
    if "--check" in sys.argv:
        if not OUT.exists():
            print("zip/data/oem_atlas.json отсутствует")
            return 1
        if not (src / "journal.jsonl").exists():
            print("журнал разведки недоступен — проверка пропущена (файл не трогаем)")
            return 0
        old = json.loads(OUT.read_text(encoding="utf-8"))
        fresh = build(src)
        strip = lambda o: {k: v for k, v in o.items() if k != "generated"}
        if strip(old) != strip(fresh):
            print("zip/data/oem_atlas.json устарел — пересобери")
            return 1
        print("zip/data/oem_atlas.json актуален")
        return 0
    data = build(src)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    st, cm = data["stats"], data["completeness"]
    print(f"производителей {st['makers']}, стран {st['countries']}, "
          f"с правилом номера {st['with_pn_rule']}, лицензий {st['licensed']}, "
          f"тупиков {st['dead_ends']}")
    print(f"пар разведки: {cm['pairs_done']} из {cm['pairs_planned']}; "
          f"не сделано: {', '.join(cm['pairs_missing'])}")
    for k, v in st["by_country"].items():
        print(f"  {v:3d}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
