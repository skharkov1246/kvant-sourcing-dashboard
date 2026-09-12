#!/usr/bin/env python3
"""Индекс «признак → дефект»: единственное звено цепочки, которого не было нигде.

ЗАЧЕМ. В таблице состояния библиотеки напротив звена «признак» стоит «признаков
нет вовсе». Каталог дефектов из разведки (zip/data/diagnostics_recon.json) несёт
описание симптомов у каждой строки, но текстом — искать по нему нельзя.
Этот скрипт разбирает симптомы на канонические признаки и строит обратный
индекс: по наблюдаемому признаку — перечень дефектов, узлов, методов проверки
и запчастей, которые за ними стоят.

ЧТО МЕРЯЕТ. Долю дефектов, у которых распознан хотя бы один признак, и список
нераспознанных — целиком, не образцом. Без списка следующая ошибка снова будет
неизмеримой (правило 16).

ЧЕГО НЕ ДЕЛАЕТ. Не пишет в источник и не правит каталог дефектов: это
производная проекция, как и dict/node_map.json.

Запуск:  python scripts/build_symptom_index.py
         python scripts/build_symptom_index.py --check
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "zip" / "data" / "diagnostics_recon.json"
OUT = ROOT / "dict" / "symptom.json"

GROUPS = [
    ("vibro", "Вибрация и спектр"),
    ("thermal", "Температура и тепловой контроль"),
    ("oil", "Масло и продукты износа"),
    ("process", "Процессные параметры"),
    ("acoustic", "Шум и ультразвук"),
    ("electric", "Электрические признаки"),
    ("visual", "Осмотр, разборка, НК"),
    ("geometry", "Геометрия, зазоры, положение"),
]

# Канонические признаки. Каждый — то, что диагност наблюдает НА МАШИНЕ,
# а не вывод о причине. Порядок влияет только на вывод.
SYMPTOMS = [
    ("rev1", "vibro", "Оборотная составляющая 1×",
     r"\b1\s*[×xх]|оборотн\w+ (?:составляющ|частот)|синхронн\w+ составляющ"),
    ("rev2", "vibro", "Вторая гармоника 2×", r"\b2\s*[×xх]|втор\w+ гармоник"),
    ("rev3", "vibro", "Третья и высшие гармоники 3×…10×",
     r"\b(?:3|4|5|6|7|8|9|10)\s*[×xх]|высш\w+ гармоник|гармоник\w* 2\s*[×xх]\s*[…\-–]"),
    ("sub", "vibro", "Субсинхронная составляющая 0,4…0,5×",
     r"субсинхрон|0[,.]\s*[3-9]\s*[…\-–—]?\s*0?[,.]?\d*\s*[×xх]|полугармоник|масл\w+ вихр|масл\w+ кнут"),
    ("env", "vibro", "Подшипниковые частоты BPFO / BPFI / BSF / FTF и огибающая",
     r"bpfo|bpfi|\bbsf\b|\bftf\b|огибающ|подшипников\w+ частот"),
    ("blade", "vibro", "Лопастная и пазовая частота",
     r"лопастн|пазов|лопаточн|\bz\s*[×xх]|\bzf\b"),
    ("broad", "vibro", "Широкополосный рост и высокочастотный фон",
     r"широкополос|высокочастотн|октавн|фрикционн\w+ сил|акустическ\w+ эмисси"),
    ("phase", "vibro", "Поведение фазы и вектора",
     r"фаза\b|фазы\b|фазов\w+|вектор\w* |полярн\w+ диаграмм"),
    ("temp", "thermal", "Рост температуры узла",
     r"температур|перегрев|нагрев|тепловизи|термограм|\bК\b(?=\s|$)"),
    ("dtemp", "thermal", "Разница температур между однотипными точками",
     r"разниц\w+ температур|относительно соседн|перепад температур|градиент температур"),
    ("wearmetal", "oil", "Металлы износа в масле",
     r"желез|хром\b|хрома|никел|медь|меди\b|олов|алюмини|кремни|свинц|баббит\w* в масл"),
    ("oilcond", "oil", "Состояние масла: вода, кислотное число, окисление",
     r"вод[аыу]\s|обводн|кислотн\w+ числ|\btan\b|окислен|rpvot|\bmpc\b|лак\w*\b"),
    ("particles", "oil", "Частицы и класс чистоты",
     r"частиц|феррограф|iso\s*4406|nas\s*1638|счёт частиц|счет частиц|стружк"),
    ("head", "process", "Падение напора, подачи, мощности, КПД",
     r"напор|подач\w|расход|производительн|\bкпд\b|мощност|потребля\w+ ток"),
    ("press", "process", "Давление, перепад, пульсации",
     r"давлен|перепад|пульсац|разрежен|вакуум"),
    ("unstable", "process", "Неустойчивость режима и регулирования",
     r"колебани\w+ мощност|неустойчив|автоколеб|рыск|ступенчат\w+ движ|помпаж|срыв\w* поток"),
    ("noise", "acoustic", "Шум, стук, хлопки, скрежет",
     r"шум|стук|хлопк|треск|скрежет|гул\b|свист|удар\w* "),
    ("ultra", "acoustic", "Ультразвуковой уровень над базой, дБ",
     r"ультразв|\+\s*\d+\s*дб|\bдб\b"),
    ("current", "electric", "Ток, спектр тока, боковые полосы",
     r"\bток\b|\bтока\b|спектр\w* ток|\bmcsa\b|боков\w+ полос|скольжен\w+ ротор"),
    ("insul", "electric", "Изоляция, частичные разряды, мегаомметр",
     r"мегаом|изоляц|частичн\w+ разряд|\bпи\b|индекс поляризац|пробо\w"),
    ("corr", "visual", "Коррозия, эрозия, язвы, питтинг",
     r"корроз|эрози|язв\w|питтинг|ржавчин|окалин|наросты|отложен|прикипан"),
    ("crack", "visual", "Трещины, изломы, выкрашивание",
     r"трещин|излом|обрыв\w* вал|выкрашиван|скол\w|beach|усталостн\w+ (?:излом|сфер)"),
    ("fod", "visual", "Забоины, вмятины, посторонний предмет",
     r"\bfod\b|\bdod\b|вмятин|вырыв|загиб|забоин|посторонн\w+ предмет"),
    ("surface", "visual", "Следы на поверхности: борозды, фреттинг, рифление",
     r"борозд|фреттинг|рифлен|стиральн\w+ доск|fluting|frosting|задир|наволакиван|подков"),
    ("leak", "visual", "Течь, утечка, пропуск среды",
     r"течь|течи\b|утечк|пропуск|подтек|выброс\w* |парени"),
    ("clear", "geometry", "Зазор, люфт, просадка",
     r"зазор|люфт|просадк|rod drop|ослаблен\w+ посадк"),
    ("align", "geometry", "Биение, прогиб, расцентровка, осевое положение",
     r"биени|прогиб|расцентров|центровк|осево\w+ (?:сдвиг|положен|смещен)|увод|soft foot|мягк\w+ лап"),
    ("blind", "process", "Отказ самого средства измерения",
     r"замороженн\w+ значен|не меняющ\w+ся значен|нормальн\w+ показани\w+ при|"
     r"оборвавш\w+ся термопар|недостоверн\w+ показани"),
]

# Свободные названия узлов из разведки → канонический узел динамической машины.
NODE_RULES = [
    ("bearing_roll", "Подшипник качения", r"подшипник\w* качени|роликоподш|шарикоподш"),
    ("bearing_slide", "Подшипник скольжения", r"подшипник\w* скольжени|баббит|вкладыш|упорн\w+ подшипник|радиальн\w+ подшипник"),
    ("rotor", "Ротор и вал", r"^ротор|ротор\b|\bвал\b|вал\b|валопровод|лини\w+ валов|колен|крейцкопф|механизм движени"),
    ("blades", "Лопаточный аппарат и проточная часть",
     r"лопат|лопаст|проточн|рабоч\w+ колес|направляющ\w+ аппарат|диффузор|улитк|импеллер|"
     r"ступен\w+ цвд|ступен\w+ цсд|паров\w+ турбин|горловин|throatbush|"
     r"компрессор \(|компрессор в сборе|объёмн\w+ насос|объемн\w+ насос"),
    ("hot", "Горячий тракт и камера сгорания", r"камер\w+ сгорани|горелк|горяч\w+ тракт|жаров|переходн\w+ патруб|зажигани"),
    ("seal", "Уплотнения",
     r"уплотнен|сальник|лабиринт|манжет|прокладк|торцов\w+ уплотн|торцев\w+ уплотн|"
     r"фонар|distance piece"),
    ("cyl", "Цилиндро-поршневая группа", r"цилиндр|поршн|кольц|шток\b|штока|сальников\w+ камер"),
    ("valve", "Клапаны и арматура", r"клапан|арматур|задвижк|обратн\w+ клапан|конденсатоотвод"),
    ("gear", "Передачи: зубчатая, ремённая, муфта", r"зубчат|редуктор|червячн|ремённ|ременн|муфт|мультипликатор"),
    ("oil", "Маслосистема", r"маслосист|масл\w+ систем|маслобак|маслоохлад|систем\w+ смазк|лубрикат|фильтр масл|сапун"),
    ("elec", "Электрическая машина и питание", r"электрическ\w+ машин|электродвиг|двигател|статор|обмотк|изоляц|воздушн\w+ зазор|"
     r"клеммн|кабел|распредел|пускател|генератор|возбужд|контактн\w+ соединен|питающ\w+ сет"),
    ("control", "КИП, САУ, защиты", r"\bкип\b|\bсау\b|защит|датчик|измерен|регулир|сервопривод|управлени|виброконтрол|система контрол"),
    ("hx", "Теплообменные аппараты", r"теплообмен|охладител|конденсатор|трубн\w+ пучок|радиатор|охлаждени"),
    ("pipe", "Обвязка, корпус, фундамент", r"обвязк|корпус|фундамент|опор|рам\b|рама|трубопровод|сварн|литьё|литье|конструкци|крепёж|крепеж|резьбов"),
    ("filter", "Фильтры и воздухозабор", r"фильтр|\bквоу\b|воздухозабор|воздушн\w+ тракт|всасывающ\w+ тракт"),
    ("fuel", "Топливная и газовая система", r"топлив|газ\w+ систем|газоподготовк|сжат\w+ воздух|гидравлическ\w+ систем|гидроагрегат"),
    ("proc", "Процесс и режим", r"процесс|режим|защиты\b|пульсац|помпаж"),
]


def norm(s: str) -> str:
    return str(s or "").lower().replace("ё", "е")


def node_key(name: str):
    t = norm(name)
    for key, title, pat in NODE_RULES:
        if re.search(pat, t):
            return key, title
    return None, None


def build() -> dict:
    src = json.loads(SRC.read_text(encoding="utf-8"))
    rows, unmatched, nodes_unmatched = [], [], []
    by_sym = defaultdict(list)
    node_count = Counter()

    for angle in src["angles"]:
        for d in angle["defects"]:
            text = norm(f"{d['symptoms']} {d['defect']}")
            hits = [k for k, _g, _t, p in SYMPTOMS if re.search(p, text)]
            nk, nt = node_key(d["node"])
            if nk:
                node_count[nt] += 1
            else:
                nodes_unmatched.append(d["node"])
            ref = {
                "angle": angle["key"],
                "scope": angle["scope"],
                "node_raw": d["node"],
                "node": nt or "не разобран",
                "defect": d["defect"],
                "symptoms": d["symptoms"],
                "cause": d["cause"],
                "method": d["method"],
                "consequence": d["consequence"],
                "repairable": d["repairable"],
                "parts": d["parts"],
                "source": d["source"],
                "confidence": d.get("confidence") or "не указана",
                "verdict": d["verdict"]["verdict"],
            }
            rows.append(ref)
            if hits:
                for h in hits:
                    by_sym[h].append(len(rows) - 1)
            else:
                unmatched.append({"node": d["node"], "defect": d["defect"],
                                  "symptoms": d["symptoms"][:200]})

    records = []
    for key, group, title, _pat in SYMPTOMS:
        idx = by_sym.get(key, [])
        if not idx:
            continue
        seg = Counter(rows[i]["scope"] for i in idx)
        nod = Counter(rows[i]["node"] for i in idx)
        records.append({
            "key": key, "group": group, "title": title,
            "n": len(idx),
            "by_scope": dict(sorted(seg.items(), key=lambda kv: -kv[1])),
            "by_node": dict(sorted(nod.items(), key=lambda kv: -kv[1])),
            "defects": idx,
        })
    records.sort(key=lambda r: -r["n"])

    covered = len(rows) - len(unmatched)
    return {
        "generated": date.today().isoformat(),
        "source": "zip/data/diagnostics_recon.json",
        "note": ("Обратный индекс «наблюдаемый признак → дефект». Построен разбором поля "
                 "симптомов каталога дефектов. Проекция: в источник не пишет."),
        "coverage": {
            "defects": len(rows),
            "with_symptom": covered,
            "pct": round(100 * covered / len(rows)) if rows else 0,
            "unmatched_count": len(unmatched),
            "unmatched": unmatched,
            "nodes_matched": sum(node_count.values()),
            "nodes_unmatched": sorted(set(nodes_unmatched)),
        },
        "groups": [{"key": k, "title": t} for k, t in GROUPS],
        "counts": {"symptoms": len(records), "nodes": len(node_count),
                   "defects": len(rows)},
        "by_node": dict(sorted(node_count.items(), key=lambda kv: -kv[1])),
        "records": records,
        "defect_rows": rows,
    }


def main() -> int:
    data = build()
    if "--check" in sys.argv:
        if not OUT.exists():
            print("dict/symptom.json отсутствует — запусти без --check")
            return 1
        old = json.loads(OUT.read_text(encoding="utf-8"))
        a = {k: v for k, v in old.items() if k != "generated"}
        b = {k: v for k, v in data.items() if k != "generated"}
        if a != b:
            print("dict/symptom.json устарел — пересобери")
            return 1
        print("dict/symptom.json актуален")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    c, cov = data["counts"], data["coverage"]
    print(f"признаков {c['symptoms']}, узлов {c['nodes']}, дефектов {c['defects']}")
    print(f"дефектов с распознанным признаком: {cov['with_symptom']}/{cov['defects']} = {cov['pct']} %")
    print(f"узлов не разобрано: {len(cov['nodes_unmatched'])}")
    for r in data["records"][:12]:
        print(f"  {r['n']:3d}  {r['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
