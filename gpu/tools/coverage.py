#!/usr/bin/env python3
"""Заполняемость базы ГПУ → gpu/data/coverage.json.

База росла разведками по одному лоту, и понять, где в ней дыры, можно было
только вручную открывая каждый файл. Этот счётчик проходит по фактическим
данным gpu/data и отвечает на два вопроса: что уже закрыто и что брать
следующим. Ничего не досочиняет — считает только то, что лежит в файлах.

Запуск: python gpu/tools/coverage.py      (--check — сверить без перезаписи)
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data"


def load(name):
    return json.loads((D / f"{name}.json").read_text(encoding="utf-8"))


def pct(done, total):
    return round(100 * done / total) if total else 0


def status(p):
    return "закрыто" if p >= 80 else ("частично" if p >= 40 else "дыра")


def build():
    machines = load("machines")["machines"]
    parts = load("parts")["systems"]
    demand = load("demand")
    analogs = load("analogs")["families"]
    subsup = load("subsuppliers")["systems"]
    sup = load("suppliers")["companies"]
    partlists = load("partlists")["lists"]
    mt = load("motortech_cross")
    fleet = load("fleet")["estimate"]
    aclass = load("analog_classes")["classes"]

    # ── по системам: закрыта ли система спросом, аналогами, субпоставщиками, поставщиками
    # Универсум систем собираем из трёх реестров: разбивка ЗИП, разбивка спроса и
    # разбивка субпоставщиков. Каждый заводился под свою задачу, и ключи у них
    # пересекаются не полностью — «Муфты» и «Масла и химия» есть только в третьем.
    sys_names = {s["key"]: s["name"] for s in parts}
    sys_names.update({k: v for k, v in demand["systems"].items() if k not in sys_names})
    sys_names.update({s["sys"]: s["name"] for s in subsup if s["sys"] not in sys_names})
    rows_by_sys, ana_by_sys, sub_by_sys, sup_by_sys = {}, {}, {}, {}
    for r in demand["rows"]:
        rows_by_sys[r.get("sys")] = rows_by_sys.get(r.get("sys"), 0) + 1
    for f in analogs:
        ana_by_sys[f.get("sys")] = ana_by_sys.get(f.get("sys"), 0) + 1
    for s in subsup:
        sub_by_sys[s["sys"]] = len(s.get("makers", []))
    for c in sup:
        for k in c.get("sys") or []:
            sup_by_sys[k] = sup_by_sys.get(k, 0) + 1

    systems = []
    for key, name in sys_names.items():
        have = sum(1 for x in (rows_by_sys.get(key), ana_by_sys.get(key),
                               sub_by_sys.get(key), sup_by_sys.get(key)) if x)
        systems.append({
            "key": key, "name": name,
            "demand": rows_by_sys.get(key, 0),
            "analogs": ana_by_sys.get(key, 0),
            "subsuppliers": sub_by_sys.get(key, 0),
            "suppliers": sup_by_sys.get(key, 0),
            "layers": have, "pct": pct(have, 4), "status": status(pct(have, 4)),
        })
    systems.sort(key=lambda x: (x["layers"], x["demand"]))

    # ── по машинам: есть ли разобранный список применяемости
    pl_keys = []
    for lst in partlists:
        pl_keys.append((lst.get("engine", "") + " " + lst.get("oem", "")).lower())
    mach = []
    for m in machines:
        model = m["model"].lower().split(" (")[0]
        head = model.split(" / ")[0].strip()
        has = any(head in k for k in pl_keys)
        mach.append({"oem": m["oem"], "model": m["model"],
                     "units": len(m.get("units") or []),
                     "passport": len(m.get("passport") or {}),
                     "partlist": has})
    mach.sort(key=lambda x: (x["partlist"], -x["units"]))

    # ── сводные показатели
    priced = sum(1 for r in demand["rows"] if r.get("usd_lo") or r.get("usd_hi"))
    checked = sum(1 for r in demand["rows"] if r.get("checks"))
    crossed = sum(1 for r in demand["rows"] if r.get("cross"))
    with_mail = sum(1 for c in sup if c.get("email"))
    with_pl = sum(1 for m in mach if m["partlist"])
    closed_sys = sum(1 for s in systems if s["pct"] >= 80)

    metrics = [
        {"key": "partlists", "title": "Машины с разобранным списком применяемости",
         "done": with_pl, "total": len(mach),
         "gap": "Партлист есть только там, где изготовитель aftermarket сам опубликовал каталог "
                "применяемости. По остальным машинам номенклатура собирается по позициям, "
                "а не по составу двигателя.",
         "next": "Искать каталоги применяемости независимых изготовителей по машинам с наибольшим "
                 "парком; в первую очередь — по тем, где парк есть, а списка нет."},
        {"key": "supplier_contacts", "title": "Поставщики с рабочим адресом",
         "done": with_mail, "total": len(sup),
         "gap": "Сайт известен у всех, почта — у трети. Остальным запрос отправить некому: "
                "сорсер упирается в форму обратной связи.",
         "next": "Прогнать реестр сборщиком контактов и добить недостающие адреса вручную "
                 "по страницам контактов и импринтам."},
        {"key": "demand_priced", "title": "Позиции лота с ценовым ориентиром",
         "done": priced, "total": len(demand["rows"]),
         "gap": "Непрощупанные позиции — те, где ни каталог, ни розница цену не отдают.",
         "next": "Закрывать запросом котировок, а не поиском: по этим позициям публичной цены нет."},
        {"key": "demand_checked", "title": "Позиции лота с проверкой",
         "done": checked, "total": len(demand["rows"]),
         "gap": "Проверка — это встречное подтверждение цены или применяемости из второго источника.",
         "next": "Довести проверку по денежным позициям: непроверенная цена в расчёт лота не идёт."},
        {"key": "demand_cross", "title": "Позиции с кроссом на независимого изготовителя",
         "done": crossed, "total": len(demand["rows"]),
         "gap": "Без кросса позиция остаётся в канале OEM — торговаться нечем.",
         "next": "Расширять кросс-каталоги: они существуют не по всем системам."},
        {"key": "systems", "title": "Системы, закрытые всеми четырьмя слоями",
         "done": closed_sys, "total": len(systems),
         "gap": "Слои: спрос в натуре, семейства аналогов, субпоставщики, допущенные поставщики. "
                "Система считается закрытой, когда есть все четыре.",
         "next": "Идти по списку снизу вверх — первыми те, где закрыт один слой из четырёх."},
    ]
    for m in metrics:
        m["pct"] = pct(m["done"], m["total"])
        m["status"] = status(m["pct"])

    out = {
        "updated": date.today().isoformat(),
        "method": "Считается gpu/tools/coverage.py по фактическим файлам gpu/data при каждой сборке "
                  "сайта. Числитель — записи, которые в файлах реально есть; ничего не досчитывается "
                  "и не оценивается экспертно.",
        "intro": "Что в базе ГПУ уже закрыто, а что ещё дыра. Показатель считается по данным, "
                 "а не по ощущению: если цифра не растёт, значит разведка не добавила записей.",
        "metrics": metrics,
        "systems": systems,
        "machines": mach,
        "context": {
            "machines": len(mach),
            "systems": len(systems),
            "suppliers": len(sup),
            "demand_rows": len(demand["rows"]),
            "analog_classes": len(aclass),
            "cross_records": mt["stats"]["records"],
            "cross_oem_pns": mt["stats"]["oem_pns"],
            "fleet_objects": fleet.get("collected_objects"),
            "fleet_mw": fleet.get("collected_mw"),
            "fleet_catalogue_total": fleet.get("catalogue_total"),
        },
    }
    return out


def main():
    out = build()
    p = D / "coverage.json"
    new = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    if "--check" in sys.argv:
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        # дата пересборки меняется каждый день, на сверку она не влияет
        strip = lambda s: "\n".join(x for x in s.splitlines() if '"updated"' not in x)
        if strip(old) != strip(new):
            print("✗ gpu/data/coverage.json устарел: python gpu/tools/coverage.py")
            return 1
        print("✓ заполняемость ГПУ актуальна")
        return 0
    p.write_text(new, encoding="utf-8")
    m = {x["key"]: f'{x["done"]}/{x["total"]}' for x in out["metrics"]}
    print(f"✓ gpu/data/coverage.json: {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
