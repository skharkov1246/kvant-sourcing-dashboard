#!/usr/bin/env python3
"""Карта узлов ГТУ: сведение меток и классификатор с измеренной точностью.

ЗАЧЕМ. Счётчик цепочки видит по ГТУ восемь узлов на 25 041 позицию. При этом
разметка в базе PN есть, но: заполнена у 2 642 строк из 12 442 (21 %), а метки
разошлись на 54 варианта, где «Горячий тракт: камеры, горелки, зажигание» и
«горячий тракт» — одно и то же, а 545 строк честно помечены «требует разметки».

ЧТО ДЕЛАЕТ. Первое: сводит 54 метки к каноническим узлам таблицей соответствия —
это детерминированно и спору не подлежит. Второе: предлагает разметку для строк
без метки по тексту наименования — и ТУТ ЖЕ МЕРЯЕТ СВОЮ ТОЧНОСТЬ на строках,
размеченных человеком. Число точности печатается и хранится в файле.

ПОЧЕМУ НЕ ПИШЕТ В ИСТОЧНИК. Предложение классификатора — гипотеза, а gt/data/pn_db.json
читают сборка сайта и база PN. Гипотеза лежит отдельно, в dict/node_map.json,
и попадёт в источник только решением владельца, когда точность его устроит.

Запуск:  python scripts/build_node_map.py
         python scripts/build_node_map.py --check
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dict" / "node_map.json"

# Канонические узлы. Ключи согласованы с gt/data/parts.json, где это возможно.
NODES = {
    "hot": "Горячий тракт: камера сгорания, горелки, зажигание, лопатки",
    "compressor": "Компрессор, ВНА, воздушный тракт",
    "rotor": "Ротор, подшипники, силовая турбина",
    "fuel": "Топливная система и клапаны",
    "controls": "САУ, КИП, электрика, защиты",
    "seals": "Уплотнения и прокладки",
    "fasteners": "Крепёж",
    "filters": "Фильтры и расходники",
    "oil": "Маслосистема",
    "exhaust": "Выхлоп и диффузор",
    "package": "Пакет и навесное оборудование",
    "tooling": "Оснастка и инструмент",
    "chemicals": "Химия и расходка",
    "start": "Пусковая система",
    "other": "Не определено",
}

# 54 метки источника → канонический узел. Соответствие детерминированное:
# спор возможен только о том, куда отнести метку, а не о том, как её прочитать.
LABEL_MAP = {
    "крепёж": "fasteners", "ротор / крепёж": "fasteners",
    "прочее / требует разметки": "other",
    "уплотнения и прокладки": "seals", "уплотнения": "seals",
    "компрессор / уплотнения": "seals",
    "сау, кип, электрика": "controls", "кип": "controls", "сау": "controls",
    "кип/вибрация": "controls", "кип/управление": "controls", "кип / защиты": "controls",
    "защиты": "controls", "электрика пакета": "controls", "управление/топливо": "controls",
    "топливная система, клапаны": "fuel", "топливо": "fuel", "топливо / горелки": "fuel",
    "топливо / горелка": "fuel",
    "фильтры и расходники": "filters", "маслосистема/фильтрация": "filters",
    "оснастка и инструмент": "tooling",
    "пакет / навесное (bop)": "package", "пожарная система": "package",
    "трубопроводы": "package", "корпус/инспекция": "package", "горячий тракт / корпус": "package",
    "горячий тракт: камеры, горелки, зажигание": "hot", "горячий тракт": "hot",
    "зажигание": "hot", "камера сгорания": "hot", "лопатки": "hot", "лопатки / са": "hot",
    "горячий тракт / са": "hot", "горячий тракт / камера сгорания": "hot",
    "лопатки и сопловые": "hot", "лопатки / ротор": "hot",
    "ротор, подшипники, уплотнения": "rotor", "подшипники": "rotor", "ротор": "rotor",
    "силовая турбина": "rotor", "привод агрегатов": "rotor",
    "компрессор": "compressor", "воздушная система": "compressor",
    "воздушный тракт": "compressor", "компрессор / механизация вна": "compressor",
    "компрессор / вна": "compressor", "компрессор / механизация": "compressor",
    "маслосистема": "oil",
    "выхлоп / диффузор": "exhaust", "выхлоп": "exhaust",
    "химия и расходка": "chemicals",
    "пусковая система": "start",
}

# Правила по тексту наименования. Порядок значим: первое сработавшее побеждает,
# поэтому узкие правила стоят выше широких.
RULES = [
    ("fasteners", r"\b(винт|болт|гайк|шайб|шпильк|заклёпк|заклепк|штифт|шплинт|стопорн\w* кольц|"
                  r"screw|bolt|nut|washer|stud|rivet|pin\b)"),
    ("seals", r"(уплотн|прокладк|манжет|сальник|кольц\w* резин|о-кольц|o-ring|gasket|seal\b|"
              r"набивк|торцев\w* уплотн)"),
    ("filters", r"(фильтр|элемент фильтр|картридж|filter|сепаратор|осушител)"),
    ("hot", r"(камер\w* сгорани|горелк|жаров|лопатк|сопл|форсунк|свеч\w* зажиган|запальн|"
            r"термопар\w* горяч|переходник\w* горяч|combustor|blade|vane|nozzle|igniter|liner)"),
    ("compressor", r"(компрессор|вна\b|направляющ\w* аппарат|воздухозаборн|квоу|компрессорн|"
                   r"compressor|igv\b|inlet guide)"),
    ("rotor", r"(ротор|подшипник|вкладыш|вал\b|шестерн|редуктор|муфт|bearing|rotor|shaft|coupling|"
              r"балансир)"),
    ("fuel", r"(топливн|клапан|вентил|регулятор давлен|дозатор|газов\w* арматур|fuel|valve)"),
    ("oil", r"(масл|смазк|маслян|lube|oil\b)"),
    ("tooling", r"(оснастк|приспособлен|съёмник|съемник|калибр|шаблон|притир|"
                r"монтажн\w* (ключ|планк)|tool\b|fixture|gauge)"),
    ("controls", r"(датчик|реле|контроллер|плат\w*|модуль|кабел|провод|разъём|разъем|шкаф|"
                 r"преобразовател|термопар|манометр|вибро|sensor|transmitter|relay|switch|"
                 r"выключател|автомат|блок питан)"),
    ("exhaust", r"(выхлоп|диффузор|глушител|дымов|exhaust|silencer)"),
    ("start", r"(пусков|стартер|starter|турбодетандер)"),
    ("chemicals", r"(герметик|смаз\w* паст|клей|краск|растворител|очистител|химич)"),
    ("tooling_wide", r"(инструмент|ключ\b)"),
    ("package", r"(трубопровод|рукав|шланг|опор\w* рам|рам\w* пакет|дверь|панел|лестниц|"
                r"огнетушител|пожарн|вентилятор пакет)"),
]
COMPILED = [(k.replace("_wide", ""), re.compile(p, re.I)) for k, p in RULES]

# Ключи таблицы приводятся тем же правилом, каким ищут по ней. Без этого «Крепёж»
# в таблице и «крепеж» в запросе — разные строки, и 828 размеченных строк молча
# уходили в «не определено». Поймано списком unmapped в самом файле карты.
LABEL_MAP = {k.lower().replace("ё", "е"): v for k, v in LABEL_MAP.items()}


def classify(text: str) -> str | None:
    t = str(text or "").lower().replace("ё", "е")
    if not t.strip():
        return None
    for key, rx in COMPILED:
        if rx.search(t):
            return key
    return None


def build() -> dict:
    rows = json.loads((ROOT / "gt" / "data" / "pn_db.json").read_text(encoding="utf-8"))["rows"]

    # ── 1. Сведение меток
    labels = Counter(r["seg"].strip() for r in rows if r.get("seg"))
    unmapped = sorted(k for k in labels if k.lower().replace("ё", "е") not in LABEL_MAP)
    mapping = [{"label": k, "count": v,
                "node": LABEL_MAP.get(k.lower().replace("ё", "е"), "other"),
                "mapped": k.lower().replace("ё", "е") in LABEL_MAP}
               for k, v in labels.most_common()]

    # ── 2. Точность классификатора на строках, размеченных человеком.
    # Строки «требует разметки» из проверки исключаются: у них нет истины.
    hit = miss = skip = 0
    confusion: Counter = Counter()
    for r in rows:
        lab = (r.get("seg") or "").strip().lower().replace("ё", "е")
        truth = LABEL_MAP.get(lab)
        if not truth or truth == "other":
            continue
        got = classify(r.get("desc"))
        if got is None:
            skip += 1
        elif got == truth:
            hit += 1
        else:
            miss += 1
            confusion[f"{truth} → {got}"] += 1
    judged = hit + miss
    precision = round(100 * hit / judged) if judged else 0
    coverage = round(100 * judged / (judged + skip)) if (judged + skip) else 0

    # ── 3. Предложение разметки для строк без метки
    proposed: Counter = Counter()
    unresolved = 0
    for r in rows:
        if (r.get("seg") or "").strip():
            continue
        got = classify(r.get("desc"))
        if got:
            proposed[got] += 1
        else:
            unresolved += 1

    return {
        "note": "Карта узлов ГТУ: сведение 54 меток к каноническим узлам плюс классификатор по "
                "тексту наименования с ИЗМЕРЕННОЙ точностью. В источник ничего не пишется: "
                "предложение классификатора — гипотеза, она попадёт в gt/data/pn_db.json только "
                "решением владельца.",
        "nodes": [{"node": k, "title": v} for k, v in NODES.items()],
        "labels": {"total": sum(labels.values()), "distinct": len(labels),
                   "unmapped": unmapped, "map": mapping},
        "classifier": {
            "measured_on": "строки, размеченные человеком, кроме «требует разметки»",
            "judged": judged, "correct": hit, "wrong": miss,
            "no_rule_matched": skip,
            "precision_pct": precision,
            "coverage_pct": coverage,
            "top_confusions": [{"pair": k, "n": v} for k, v in confusion.most_common(10)],
            "honest_note": "Точность измерена, а не заявлена. Правила, дающие путаницу из "
                           "top_confusions, уточняются в первую очередь.",
        },
        "proposal": {
            "rows_without_label": sum(1 for r in rows if not (r.get("seg") or "").strip()),
            "would_classify": sum(proposed.values()),
            "would_leave_unresolved": unresolved,
            "by_node": [{"node": k, "title": NODES[k], "n": v} for k, v in proposed.most_common()],
        },
    }


def main() -> int:
    fresh = build()
    if "--check" in sys.argv:
        if not OUT.exists() or json.loads(OUT.read_text(encoding="utf-8")) != fresh:
            print("✗ dict/node_map.json устарел — выполните: python scripts/build_node_map.py",
                  file=sys.stderr)
            return 1
        print(f"✓ карта узлов актуальна: точность {fresh['classifier']['precision_pct']} %")
        return 0
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    c, p = fresh["classifier"], fresh["proposal"]
    print(f"✓ dict/node_map.json: меток {fresh['labels']['distinct']} сведено к "
          f"{len(NODES)} узлам, несведённых {len(fresh['labels']['unmapped'])}")
    print(f"  точность классификатора: {c['precision_pct']} % "
          f"({c['correct']} из {c['judged']}), покрытие правилами {c['coverage_pct']} %")
    print(f"  предложил бы разметку для {p['would_classify']} строк из {p['rows_without_label']} "
          f"без метки, {p['would_leave_unresolved']} остались бы неразобранными")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
