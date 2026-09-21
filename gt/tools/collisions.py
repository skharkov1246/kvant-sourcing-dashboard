#!/usr/bin/env python3
"""Один номер против разных деталей: замер по самой заявке.

Что нашлось и почему это важно. В заявке «Энергосети» номер `VS-4-57` стоит и у
электрода розжига, и у завихрителя пламени; `VS-6-82` — у шести разных позиций,
от реле давления до сервопривода. Это не номера деталей, а обозначение МОДЕЛИ
котла Victory Energy Voyager (у завода в стоке «VS-4-51 Saturated — Voyager
O-Type Watertube Boiler», у дилера «75000 PPH Victory Energy #Voyager-VS-4-57»).

Дальше ошибка удваивается нашей же сводкой: `ship_lukoil.json` собран ПО
НОМЕРУ, поэтому количества разных деталей складываются в одну строку и к сумме
применяется одна вилка. По `VS-6-82` так сложились 37 + 37 + 18 + 18 + 18 + 18
= 146 штук шести разных изделий, и экспозиция 73 000 USD — арифметика, а не
оценка.

Инструмент делит совпадения на три класса, и только первые два — дефект:
  «разные изделия»           наименования номера попали в разные категории узла
                             (одно изделие не может быть и клапаном, и датчиком)
                             либо почти не пересекаются словами; основание
                             записано у каждой строки полем `reason`;
  «противоположные исполнения» первичный против вторичного, наружный против
                             внутреннего и так далее — либо описка, либо один
                             номер действительно стоит в двух позициях;
  «одна позиция, разные записи» тот же предмет, записанный иначе (усечённое
                             наименование, другая машина в описании). Этот класс
                             автоматически НЕ разбирается и дефектом не
                             объявляется — он выводится числом и с примерами,
                             чтобы его посмотрел человек.

    python gt/tools/collisions.py            # показать замер
    python gt/tools/collisions.py --write    # записать gt/data/ship_collisions.json
    python gt/tools/collisions.py --check    # сверить набор с замером (гейт)
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "gt/data/rfq_demand.json"
MERGED = ROOT / "gt/data/ship_lukoil.json"
OUT = ROOT / "gt/data/ship_collisions.json"
QUEST = ROOT / "gt/data/ship_questions.json"

# Противоположные исполнения: если в наименованиях одного номера встретились оба
# слова пары, это либо описка, либо номер честно стоит в двух позициях. Решает
# заказчик, а не мы.
OPPOSITES = [("первичн", "вторичн"), ("наружн", "внутренн"), ("левы", "правы"),
             ("впускн", "выпускн"), ("верхн", "нижн"), ("передн", "задн"),
             ("подающ", "обратн"), ("основн", "резервн")]
STOP = {"для", "и", "в", "с", "на", "по", "от", "до", "сборе", "шт", "мм"}
SAME = 0.5           # доля общих слов, выше которой считаем записи одним предметом

CLS_DIFF = "разные изделия"
CLS_OPP = "противоположные исполнения"
CLS_WORDING = "одна позиция, разные записи"


def words(name: str) -> set[str]:
    return {w for w in re.findall(r"[а-яёa-z0-9]+", (name or "").lower())
            if len(w) > 2 and w not in STOP}


def exposure(row: dict) -> float:
    lo, hi = row.get("usd_lo"), row.get("usd_hi")
    if lo is None or hi is None:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(row.get("qty") or 0)


def classify(names: list[str], cats: set[str]) -> tuple[str, str]:
    """Класс совпадения и ОСНОВАНИЕ, по которому он вынесен.

    Основание записывается рядом с классом: без него «разные изделия» читается
    как один и тот же вывод и там, где это доказано категорией узла, и там, где
    это всего лишь непохожие наименования.
    """
    if len(cats) > 1:
        return CLS_DIFF, "наименования попали в разные категории узла"
    low = [n.lower() for n in names]
    for a, b in OPPOSITES:
        if any(a in x for x in low) and any(b in x for x in low):
            return CLS_OPP, f"в наименованиях одного номера и «{a}…», и «{b}…»"
    sets = [words(n) for n in names]
    sim = min((len(x & y) / max(1, min(len(x), len(y))))
              for x, y in itertools.combinations(sets, 2)) if len(sets) > 1 else 1.0
    if sim >= SAME:
        return CLS_WORDING, f"наименования пересекаются словами на {sim:.0%}"
    return CLS_DIFF, f"наименования почти не пересекаются: общих слов {sim:.0%}"


def measure() -> dict:
    raw = json.loads(RAW.read_text(encoding="utf-8"))["rows"]
    merged = {r["pn"]: r for r in json.loads(MERGED.read_text(encoding="utf-8"))["rows"]}

    parts: dict[str, dict[str, float]] = collections.defaultdict(
        lambda: collections.defaultdict(float))
    cats: dict[str, set[str]] = collections.defaultdict(set)
    for r in raw:
        pn = (r.get("pn") or "").strip()
        if not pn:
            continue
        parts[pn][(r.get("name") or "").strip()] += float(r.get("qty") or 0)
        cats[pn].add((r.get("cat") or "").strip())

    found: dict[str, list[dict]] = collections.defaultdict(list)
    for pn, names in parts.items():
        if len(names) < 2:
            continue
        row = merged.get(pn)
        if row is None:          # номера нет в сводке — считать по нему нечего
            continue
        cls, why = classify(sorted(names), cats[pn])
        found[cls].append({
            "pn": pn,
            "reason": why,
            "man": (row.get("man") or "").strip(),
            "sheet": row.get("sheet") or "",
            "class": cls,
            "qty_in_summary": float(row.get("qty") or 0),
            "exposure": int(round(exposure(row))),
            "units": sorted(cats[pn]),
            "parts": [{"name": n, "qty": q} for n, q in
                      sorted(names.items(), key=lambda kv: -kv[1])],
        })

    tot = int(round(sum(exposure(r) for r in merged.values())))
    classes = {}
    for cls in (CLS_DIFF, CLS_OPP, CLS_WORDING):
        items = sorted(found.get(cls, []), key=lambda x: -x["exposure"])
        classes[cls] = {
            "articles": len(items),
            "exposure": sum(x["exposure"] for x in items),
            "items": items,
        }
    defect = classes[CLS_DIFF]["exposure"] + classes[CLS_OPP]["exposure"]
    # Ноль в количестве — отдельный дефект самой заявки: позиция названа, а
    # объём не указан, поэтому в сумму по номеру она входит нулём и молча.
    zero = sum(1 for cell in classes.values() for item in cell["items"]
               for p in item["parts"] if not p["qty"])
    return {
        "updated": "2026-09-18",
        "source": "Замер по самой заявке ЛУКОЙЛ: артикулы, под которыми в заявке стоят разные "
                  "детали. Считает gt/tools/collisions.py по gt/data/rfq_demand.json (сырые "
                  "строки заявки) и gt/data/ship_lukoil.json (сводка по номеру).",
        "method": "Класс «разные узлы» — наименования номера попали в разные категории узла, "
                  "это заведомо разные изделия. Класс «противоположные исполнения» — первичный "
                  "против вторичного и подобные пары: либо описка, либо номер честно стоит в двух "
                  "позициях, решает заказчик. Класс «одна позиция, разные записи» автоматически "
                  "не разбирается и дефектом не объявляется: там тот же предмет, записанный "
                  "иначе. Дефект = первые два класса.",
        "why_it_matters": "Сводка собрана ПО НОМЕРУ, поэтому количества разных деталей "
                          "складываются в одну строку и к сумме применяется одна вилка. По "
                          "VS-6-82 сложились 37+37+18+18+18+18 = 146 штук шести разных изделий. "
                          "Экспозиция таких строк — арифметика, а не оценка, и до защиты их надо "
                          "разложить назад по деталям.",
        "totals": {
            "exposure_total": tot,
            "articles_multi_name": sum(c["articles"] for c in classes.values()),
            "defect_articles": classes[CLS_DIFF]["articles"] + classes[CLS_OPP]["articles"],
            "defect_exposure": defect,
            "defect_share_pct": round(defect / tot * 100, 1) if tot else 0.0,
            "zero_qty_parts": zero,
        },
        "classes": classes,
    }


def report(m: dict) -> str:
    t = m["totals"]
    ru = lambda n: f"{int(n):,}".replace(",", " ")  # noqa: E731
    out = [f"артикулов с разными наименованиями: {t['articles_multi_name']}"]
    for cls, cell in m["classes"].items():
        out.append(f"  {cls:30} {cell['articles']:4} артикулов "
                   f"{ru(cell['exposure']):>11} USD")
        for x in cell["items"][:5]:
            names = " // ".join(p["name"][:44] for p in x["parts"][:3])
            out.append(f"      {x['pn']:22} {ru(x['exposure']):>9} USD | {names}")
    out.append(f"дефект (первые два класса): {t['defect_articles']} артикулов на "
               f"{ru(t['defect_exposure'])} USD = {t['defect_share_pct']} % экспозиции")
    return "\n".join(out)


NO_PN = "__БЕЗ_АРТИКУЛА__"


def key(pn: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").upper())


def to_questions(m: dict) -> tuple[list[dict], list[str]]:
    """Вопросы заказчику по находкам и список уже заданных.

    Вопрос механический, поэтому его и генерируем: «под этим номером в заявке
    идут такие-то разные детали, назовите артикул каждой». Руками писать
    тридцать четыре одинаковых вопроса — работа без содержания.
    """
    doc = json.loads(QUEST.read_text(encoding="utf-8"))
    asked = [key(q.get("pn")) for q in doc["questions"]]
    # Метка «без артикула» кириллическая, и key() её обнуляет — поэтому вторым
    # ключом держим сырое имя: иначе повторный прогон добавит вопрос заново.
    raw = {str(q.get("pn") or "") for q in doc["questions"]}
    fresh, skip = [], []
    for cls in (CLS_DIFF, CLS_OPP):
        for item in m["classes"][cls]["items"]:
            k = key(item["pn"])
            # Артикула нет вовсе: в колонке стоит прочерк, и под ним несколько
            # разных уплотнений. Спрашивать «уточните номер такой-то» здесь
            # нечего — нужен один вопрос про всю группу, ниже.
            if not k:
                k = NO_PN
                item = {**item, "pn": NO_PN}
            if item["pn"] in raw or (k and any(k in a for a in asked)):
                skip.append(item["pn"])
                continue
            asked.append(k)
            raw.add(item["pn"])
            parts = "; ".join(f"«{p['name']}» — {int(p['qty'])} шт"
                              for p in item["parts"])
            if item["pn"] == NO_PN:
                ask = (f"В {len(item['parts'])} строках заявки колонка артикула пустая — стоит "
                       f"прочерк: {parts}. По ним нужен номер, чертёж или типоразмер с посадочными "
                       f"размерами: уплотнение подбирается по размеру, а не по наименованию.")
            elif cls == CLS_OPP:
                ask = (f"Под номером {item['pn']} в заявке идут противоположные исполнения: "
                       f"{parts}. Это один и тот же артикул, применённый в двух позициях, или "
                       f"в одной из строк номер указан по ошибке? Если артикул один — подтвердите "
                       f"это письмом, мы посчитаем строку одной позицией.")
            else:
                ask = (f"Под номером {item['pn']} в заявке идут разные изделия: {parts}. "
                       f"Назовите артикул каждого отдельно — либо пришлите шильдик или страницу "
                       f"каталога. Одним номером эти позиции заказать нельзя.")
            fresh.append({
                "pn": item["pn"],
                "qty": item["qty_in_summary"],
                "unit": "шт",
                "kind": ("противоположные исполнения под одним номером" if cls == CLS_OPP
                         else "один номер против разных деталей"),
                "ask": ask,
                "known": (f"Замер по сырым строкам заявки: {parts}. Основание класса — "
                          f"{item.get('reason')}. Узлы: {', '.join(item.get('units') or [])}."),
                "cost": (f"В нашей сводке строка собрана по номеру, поэтому количества сложились: "
                         f"{int(item['qty_in_summary'])} шт и экспозиция "
                         f"{item['exposure']:,} USD".replace(",", " ") +
                         " — это арифметика по разным изделиям, а не оценка одной позиции. "
                         "Ценой такую строку защищать нельзя, пока она не разложена."),
                "source": "gt/tools/collisions.py",
            })
    return fresh, skip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--questions", action="store_true",
                    help="дописать вопросы заказчику по находкам (идемпотентно)")
    a = ap.parse_args()
    m = measure()
    print(report(m))
    if a.questions:
        fresh, skip = to_questions(m)
        doc = json.loads(QUEST.read_text(encoding="utf-8"))
        doc["questions"].extend(fresh)
        doc["updated"] = m["updated"]
        QUEST.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
        print(f"вопросов добавлено {len(fresh)}, уже были заданы {len(skip)}: "
              f"{', '.join(skip) or '—'}")
        return 0
    if a.write:
        OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"записано в {OUT.relative_to(ROOT)}")
        return 0
    if a.check:
        if not OUT.exists():
            print("набора нет — соберите: python gt/tools/collisions.py --write",
                  file=sys.stderr)
            return 1
        old = json.loads(OUT.read_text(encoding="utf-8"))
        if old.get("totals") != m["totals"]:
            print(f"набор устарел: в наборе {old.get('totals')}, замер {m['totals']}",
                  file=sys.stderr)
            return 1
        print("✓ числа набора совпадают с замером")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
