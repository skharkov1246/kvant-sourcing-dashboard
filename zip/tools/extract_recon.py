#!/usr/bin/env python3
"""Разбор трёх разведок 12.09.2026 в наборы данных портала.

  насосы, КИПиА, электротехника   → zip/data/dirs_recon.json
  ремонтные решения и исполнители → zip/data/repair_recon.json
  субпоставщики и шифровки номеров→ zip/data/subsupplier_recon.json

ОДНО ПРАВИЛО НА ВСЕ ТРИ. Вердикт скептика проставляется КАЖДОЙ записи явно.
«Скептик не сослался» и «угол без скептика» — разные значения и оба не значат
«проверено»: пустое поле читалось бы как «всё хорошо», и это была бы ложь.
Вердикты, не легшие ни на одну запись, не выбрасываются, а лежат списком
orphan_verdicts: потерянная проверка хуже отсутствующей, потому что о ней
никто не узнает.

Запуск:  python zip/tools/extract_recon.py [--check]
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data"
B = Path("/root/.claude/projects/-home-user-kvant-sourcing-dashboard/"
         "22942900-899d-580c-84cd-eb230f46e70b/subagents/workflows")

WF = {"dirs": "wf_ac548360-4ac", "repair": "wf_8d397769-8ec", "subs": "wf_059cf484-ec3"}

SEG = {"pumps": "Насосы", "instrum": "КИПиА", "electro": "Электротехника и приводы",
       "gtu": "ГТУ — газотурбинные", "gpu": "ГПУ — газопоршневые",
       "gsho": "ГШО — горно-шахтное", "recip": "Поршневые компрессоры"}

ANGLE = {
    "types": "Типы, стандарты и применение",
    "wear": "Расходная номенклатура и ресурс",
    "seals": "Торцевые уплотнения — денежная группа",
    "hydraulics": "Гидравлическая часть и обратное проектирование",
    "market": "Аутмаркет, российский рынок и правила запроса",
    "classes": "Классы приборов и обязательные стандарты",
    "tier1": "Кто делает начинку для брендов",
    "valves": "Регулирующие клапаны, приводы и позиционеры",
    "ru": "Российский рынок, поверка и замещение",
    "motors": "Электродвигатели: типоряды и стандарты",
    "drives": "Частотные преобразователи и силовая электроника",
    "switchgear": "Трансформаторы и распределительные устройства",
    "surfacing": "Наплавка, напыление и нанесение покрытий",
    "machining": "Механическая обработка, балансировка, сборка",
    "reverse": "Обратное проектирование и изготовление заново",
    "ru_pumps": "Исполнители: насосы",
    "ru_compressors": "Исполнители: компрессоры и турбомашины",
    "ru_electro": "Исполнители: электрические машины",
}

NOT_CHECKED = "скептик не сослался"
NO_SKEPTIC = "угол без скептика"


def load(wf):
    p = B / wf / "journal.jsonl"
    if not p.exists():
        return None
    return [json.loads(l)["result"] for l in p.read_text().splitlines()
            if l.strip() and json.loads(l).get("type") == "result"]


def words(s):
    s = re.sub(r"[^а-яa-z0-9 ]+", " ", str(s or "").lower().replace("ё", "е"))
    return {w for w in s.split() if len(w) > 3}


def attach(items, verdicts, name_fields):
    """Разложить свободные формулировки скептика по записям — по доле общих слов.

    Порог 0,35 подобран так, чтобы вердикт не сел на чужую запись: ошибочная
    привязка выдаёт непроверенное за проверенное, а это дороже пропуска."""
    for it in items:
        it["verdict"] = {"verdict": NOT_CHECKED, "why": "", "correction": "", "claim": ""}
    pool = [(i, set().union(*[words(it.get(f)) for f in name_fields]) or set())
            for i, it in enumerate(items)]
    orphans = []
    for v in verdicts:
        claim = (v.get("claim") or "").strip()
        tw = words(claim)
        best, score = None, 0.0
        if tw:
            for i, iw in pool:
                if not iw:
                    continue
                # Доля слов ПРЕТЕНЗИИ, найденных в записи. Делить на длину записи
                # нельзя: карточка технологии длиннее претензии в разы, и любая
                # доля выходит ничтожной — так первая версия не привязала ни
                # одного вердикта из 124.
                ratio = len(tw & iw) / len(tw)
                if ratio > score:
                    best, score = i, ratio
        body = {"verdict": v.get("verdict") or "не указан", "why": v.get("why") or "",
                "correction": v.get("correction") or "", "claim": claim}
        # Опровержение имеет приоритет: если на запись метит и подтверждение,
        # и опровержение, показать надо худшее — иначе ошибка скроется.
        if best is not None and score >= 0.35:
            cur = items[best]["verdict"]["verdict"]
            if cur == NOT_CHECKED or (body["verdict"] == "опровергнуто" and cur != "опровергнуто"):
                items[best]["verdict"] = body
                continue
        orphans.append(body)
    return orphans


# Поле региона — свободный текст с адресом: «Москва (ул. Николоямская, 15);
# сервисный центр — Екатеринбург». Ключом такое значение быть не может:
# получится полсотни «регионов» по числу записей. Берём головной город.
CITY = re.compile(r"^[\s,;]*(?:г\.?\s*|пос[её]лок\s+|п\.\s*)?"
                  r"([А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*(?:\s+[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*)?)")


def city_of(raw):
    m = CITY.match(str(raw or ""))
    return m.group(1) if m else "не указан"


def region_tally(items):
    c = {}
    for it in items:
        k = city_of(it.get("region"))
        c[k] = c.get(k, 0) + 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def claim_tally(angles):
    """Вердикты по утверждениям угла — тем, что скептик проверял сам, а не
    привязываясь к карточке. Их нельзя терять: это и есть проверка."""
    c = {}
    for a in angles:
        for v in (a.get("skeptic") or {}).get("checked_claims", []):
            k = v["verdict"]
            c[k] = c.get(k, 0) + 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def tally(items):
    c = {}
    for it in items:
        v = it["verdict"]["verdict"]
        c[v] = c.get(v, 0) + 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


# ────────────────────────────────────────── насосы, КИПиА, электротехника
def build_dirs(res):
    angles = []
    for r in sorted(res, key=lambda x: (x["segment"], x["angle"])):
        f = [dict(x) for x in r["findings"]]
        c = [dict(x) for x in r["companies"]]
        for it in f + c:
            it["verdict"] = {"verdict": NO_SKEPTIC, "why": "", "correction": "", "claim": ""}
        angles.append({
            "key": f'{r["segment"]}/{r["angle"]}', "segment": r["segment"],
            "segment_title": SEG.get(r["segment"], r["segment"]),
            "angle": r["angle"], "title": ANGLE.get(r["angle"], r["angle"]),
            "summary": r.get("summary") or "",
            "findings": f, "companies": c,
            "gaps": list(r.get("gaps") or []), "dead_ends": list(r.get("dead_ends") or []),
            "skeptic": None,
        })
    by_seg, by_kind = {}, {}
    for a in angles:
        by_seg[a["segment_title"]] = by_seg.get(a["segment_title"], 0) + len(a["findings"])
        for c in a["companies"]:
            k = c.get("kind") or "не указан"
            by_kind[k] = by_kind.get(k, 0) + 1
    return {
        "generated": date.today().isoformat(),
        "subject": "Насосы, КИПиА, электротехника: три направления, которых в портале не было",
        "method": ("Пятнадцать углов разведки по трём направлениям. Скептик не отработал "
                   "ни по одному: недельный лимит сессии исчерпан 12.09.2026. Поэтому у "
                   "КАЖДОЙ записи стоит вердикт «угол без скептика» — это не оговорка "
                   "в примечании, а состояние данных: проверку предстоит провести."),
        "stats": {
            "angles": len(angles), "with_skeptic": 0,
            "findings": sum(len(a["findings"]) for a in angles),
            "companies": sum(len(a["companies"]) for a in angles),
            "gaps": sum(len(a["gaps"]) for a in angles),
            "dead_ends": sum(len(a["dead_ends"]) for a in angles),
            "by_segment": dict(sorted(by_seg.items(), key=lambda kv: -kv[1])),
            "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        },
        "angles": angles,
    }


# ────────────────────────────────────────── ремонтные решения и исполнители
def build_repair(res):
    tech_src = [r for r in res if r.get("technologies")]
    cont_src = [r for r in res if r.get("contractors")]
    skept = [r for r in res if r.get("verdicts")]

    tech_angles = []
    for r in sorted(tech_src, key=lambda x: x["angle"]):
        items = [dict(x) for x in r["technologies"]]
        vs = [v for s in skept if s.get("angle") == r["angle"] for v in s["verdicts"]]
        orph = attach(items, vs, ("name", "how", "standard"))
        tech_angles.append({
            "key": r["angle"], "title": ANGLE.get(r["angle"], r["angle"]),
            "summary": r.get("summary") or "", "technologies": items,
            "dead_ends": list(r.get("dead_ends") or []),
            "skeptic": {
                "overall": " ".join(s.get("overall") or "" for s in skept
                                    if s.get("angle") == r["angle"]).strip(),
                "missing": [m for s in skept if s.get("angle") == r["angle"]
                            for m in (s.get("missing") or [])],
                "checked_claims": orph,
            } if vs else None,
        })

    cont_angles = []
    for r in sorted(cont_src, key=lambda x: x["angle"]):
        items = [dict(x) for x in r["contractors"]]
        for it in items:
            it["city"] = city_of(it.get("region"))
            it["verdict"] = {"verdict": NO_SKEPTIC, "why": "", "correction": "", "claim": ""}
        cont_angles.append({
            "key": r["angle"], "title": ANGLE.get(r["angle"], r["angle"]),
            "summary": r.get("summary") or "", "contractors": items,
            "dead_ends": list(r.get("dead_ends") or []), "skeptic": None,
        })

    alltech = [t for a in tech_angles for t in a["technologies"]]
    allcont = [c for a in cont_angles for c in a["contractors"]]
    own = [c for c in allcont if str(c.get("own_production") or "").strip()
           and not re.match(r"^\s*(нет|не\b|не подтвержд)", str(c["own_production"]), re.I)]
    return {
        "generated": date.today().isoformat(),
        "subject": "Ремонтные решения и исполнители: чем восстанавливают и кто это делает",
        "method": ("Шесть углов: три по технологиям восстановления со скептиком у каждого "
                   "и три по исполнителям — эти скептика не получили, недельный лимит "
                   "сессии кончился. У технологий вердикт адресный, у исполнителей — "
                   "«угол без скептика» у всех записей без исключения."),
        "stats": {
            "technologies": len(alltech), "contractors": len(allcont),
            "contractors_own_production": len(own),
            "tech_angles": len(tech_angles), "contractor_angles": len(cont_angles),
            "with_skeptic": sum(1 for a in tech_angles if a["skeptic"]),
            "missing": sum(len(a["skeptic"]["missing"]) for a in tech_angles if a["skeptic"]),
            "dead_ends": sum(len(a["dead_ends"]) for a in tech_angles + cont_angles),
            "checked_claims": sum(len(a["skeptic"]["checked_claims"])
                                  for a in tech_angles if a["skeptic"]),
            "by_verdict": tally(alltech + allcont),
            "by_claim_verdict": claim_tally(tech_angles),
            "by_region": region_tally(allcont),
        },
        "tech_angles": tech_angles,
        "contractor_angles": cont_angles,
    }


# ────────────────────────────────────────── субпоставщики и шифровки
def build_subs(res):
    chain_src = [r for r in res if r.get("chains")]
    rule_src = [r for r in res if r.get("rules")]
    skept = [r for r in res if r.get("verdicts")]

    segs = []
    for seg in sorted({r["segment"] for r in chain_src} | {r["segment"] for r in rule_src}):
        chains = [dict(x) for c in chain_src if c["segment"] == seg for x in c["chains"]]
        rules = [dict(x) for c in rule_src if c["segment"] == seg for x in c["rules"]]
        vs = [v for s in skept if s.get("segment") == seg for v in s["verdicts"]]
        orph = attach(rules, vs, ("oem", "format", "examples", "maker_hint")) if vs else None
        if not vs:
            for it in rules:
                it["verdict"] = {"verdict": NO_SKEPTIC, "why": "", "correction": "", "claim": ""}
        for it in chains:
            it["verdict"] = {"verdict": NO_SKEPTIC if not vs else NOT_CHECKED,
                             "why": "", "correction": "", "claim": ""}
        segs.append({
            "segment": seg, "title": SEG.get(seg, seg),
            "summary": " ".join(c.get("summary") or "" for c in chain_src
                                if c["segment"] == seg).strip(),
            "chains": chains, "rules": rules,
            "ours": sorted({x for c in chain_src if c["segment"] == seg
                            for x in (c.get("ours") or [])}),
            "dead_ends": sorted({x for c in chain_src + rule_src if c["segment"] == seg
                                 for x in (c.get("dead_ends") or [])}),
            "skeptic": {
                "overall": " ".join(s.get("overall") or "" for s in skept
                                    if s.get("segment") == seg).strip(),
                "missing": [m for s in skept if s.get("segment") == seg
                            for m in (s.get("missing") or [])],
                "checked_claims": orph or [],
            } if vs else None,
        })

    allchains = [c for s in segs for c in s["chains"]]
    allrules = [r for s in segs for r in s["rules"]]
    direct = [c for c in allchains if re.match(r"^\s*да", str(c.get("buyable_direct") or ""), re.I)]
    return {
        "generated": date.today().isoformat(),
        "subject": "Субпоставщики двух уровней и правила чтения номеров изготовителей",
        "method": ("Семь направлений по двум углам: цепочка «OEM → узел → кто делает → кто "
                   "делает ему» и правило чтения каталожного номера. Скептик отработал по "
                   "двум направлениям из семи — остальные упёрлись в недельный лимит. "
                   "Записи без проверки помечены явно."),
        "stats": {
            "segments": len(segs), "chains": len(allchains), "rules": len(allrules),
            "buyable_direct": len(direct),
            "ours": sum(len(s["ours"]) for s in segs),
            "with_skeptic": sum(1 for s in segs if s["skeptic"]),
            "dead_ends": sum(len(s["dead_ends"]) for s in segs),
            "checked_claims": sum(len(s["skeptic"]["checked_claims"])
                                  for s in segs if s["skeptic"]),
            "by_verdict": tally(allchains + allrules),
            "by_claim_verdict": claim_tally(segs),
            "by_segment": {s["title"]: len(s["chains"]) for s in segs},
        },
        "segments": segs,
    }


TARGETS = [("dirs", "dirs_recon.json", build_dirs),
           ("repair", "repair_recon.json", build_repair),
           ("subs", "subsupplier_recon.json", build_subs)]


def main() -> int:
    check = "--check" in sys.argv
    rc = 0
    for key, fname, fn in TARGETS:
        res = load(WF[key])
        out = D / fname
        if res is None:
            if check:
                print(f"{fname}: журнал недоступен — проверка пропущена")
                continue
            print(f"{fname}: журнал разведки недоступен, файл не трогаем")
            continue
        data = fn(res)
        if check:
            if not out.exists():
                print(f"{fname} отсутствует"); rc = 1; continue
            strip = lambda o: {k: v for k, v in o.items() if k != "generated"}
            if strip(json.loads(out.read_text(encoding="utf-8"))) != strip(data):
                print(f"{fname} устарел — пересобери"); rc = 1
            else:
                print(f"{fname} актуален")
            continue
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        st = data["stats"]
        print(f"{fname}: " + ", ".join(f"{k} {v}" for k, v in st.items()
                                       if isinstance(v, int)))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
