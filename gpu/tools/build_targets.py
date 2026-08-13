#!/usr/bin/env python3
"""Сборка gpu/data/targets.json — адресный расчёт годовой потребности по владельцам парка.

Это то место, где стратегия превращается в письмо, которое можно отправить завтра.
Логика: берём реестр парка, схлопываем объекты в группы владельцев, прикладываем
модель потребления и получаем по каждому — сколько ЗИП он съедает за год в деньгах
и в штуках по ключевым позициям. Отдельно помечаем тех, кто УЖЕ наш заказчик:
там вход бесплатный.

Разговор с владельцем меняется принципиально. Было: «пришлите спецификацию, посчитаем».
Стало: «у вас шесть станций на 70 МВт, при 8000 моточасов это примерно 4300 свечей
и 1300 фильтров в год на 3,4 млн долларов; вот как мы это закрываем».

Запуск:  python3 gpu/tools/build_targets.py
Зависит от: data/fleet.json, data/consumption.json, data/budget-совпадения (зашиты ниже).
"""
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
OUT = ROOT / "data/targets.json"

# группировка юрлиц в холдинги: (регэксп по владельцу, имя группы)
GROUPS = [
    (r"ЛУКОЙЛ|РИТЭК", "Группа ЛУКОЙЛ"),
    (r"РН-|Роснефть|Башнефть|ОНГГ", "Группа Роснефть (вкл. Башнефть)"),
    (r"Газпромнефть|СибурТюменьГаз", "Газпром нефть / СИБУР"),
    (r"Михеевский|Карабашмедь|Башкирская медь|ЮГК", "ГОК и цветная металлургия"),
    (r"ЯТЭК", "ЯТЭК"),
    (r"БГК|БашРТС", "Башкирская генерирующая (БГК/БашРТС)"),
    (r"НАТЭК", "НАТЭК Инвест-Энерго"),
    (r"Перспектива", "Перспектива"),
    (r"Ямалкоммунэнерго", "Ямалкоммунэнерго"),
    (r"ИНК|Иркутская нефтяная", "Иркутская нефтяная компания"),
    (r"РуссНефть", "РуссНефть"),
]

# компании, с которыми мы уже работаем (из data/budget_snapshot.json, 127 заказчиков)
OUR_CUSTOMERS = {
    "Группа ЛУКОЙЛ": "★ действующий заказчик: LUKOIL Mid East, ЛУКОЙЛ-Пермнефтеоргсинтез, "
                     "ЛУКОЙЛ-Нижегороднефтеоргсинтез, ЛУКОЙЛ-МЦПБ, Ставролен — 4+ юрлица в бюджетах",
    "ГОК и цветная металлургия": "△ профиль совпадает с действующими: Норникель, Кольская ГМК, "
                                 "ГРК «Быстринское» — заходить через ту же логику отношений",
    "Газпром нефть / СИБУР": "△ СИБУР в базе есть: ЗапСибНефтехим, Амурский ГХК",
}

# тип газа по сегменту — бьёт по расходу через модель потребления
GAS_BY_SEG = {
    "нефть и газ": "associated",
    "ГОК и металлы": "natural",
    "нефтехимия": "associated",
    "энергетика": "natural",
    "ЖКХ": "natural",
    "промышленность": "natural",
    "ЦОД": "natural",
}


def group_of(owner: str) -> str:
    for rx, name in GROUPS:
        if re.search(rx, owner, re.I):
            return name
    return owner


def main() -> None:
    fleet = json.loads((ROOT / "data/fleet.json").read_text(encoding="utf-8"))
    consum = json.loads((ROOT / "data/consumption.json").read_text(encoding="utf-8"))

    # удельные показатели из модели: средний $/кВт·год и разбивка по позициям на кВт
    engines = consum["engines"]
    per_kw = {"natural": 0.0, "associated": 0.0}
    for gas in per_kw:
        sel = [e for e in engines if e["gas"] == gas] or engines
        per_kw[gas] = sum(e["usd_per_kw_year"] for e in sel) / len(sel)

    # штук на МВт в год по ключевым позициям — усредняем по машинам
    key_items = ["plug", "oil_filter", "air_filter", "prechamber_valve", "coil", "head"]
    per_mw_qty = {}
    for k in key_items:
        vals = []
        for e in engines:
            it = next((i for i in e["items"] if i["key"] == k), None)
            if it:
                vals.append(it["qty_year"] / (e["kwe"] / 1000))
        per_mw_qty[k] = sum(vals) / len(vals) if vals else 0
    names = {i["key"]: i["name"] for e in engines for i in e["items"]}

    # схлопываем объекты в группы
    agg = {}
    for o in fleet["objects"]:
        if "Узбекистан" in o["owner"]:
            continue  # не РФ
        if o["status"] == "проектируется":
            continue  # ещё нет парка
        g = group_of(o["owner"])
        a = agg.setdefault(g, {"group": g, "mw": 0.0, "objects": [], "segs": set(), "building": 0.0})
        if o["status"] == "строится":
            a["building"] += o["mw"]
        else:
            a["mw"] += o["mw"]
        a["objects"].append({"name": o["name"], "owner": o["owner"], "mw": o["mw"], "status": o["status"]})
        a["segs"].add(o["seg"])

    rows = []
    for g, a in agg.items():
        if a["mw"] <= 0:
            continue
        seg = sorted(a["segs"])[0]
        gas = GAS_BY_SEG.get(seg, "natural")
        rate = per_kw[gas]
        usd = a["mw"] * 1000 * rate
        rows.append({
            "group": g,
            "mw": round(a["mw"], 2),
            "mw_building": round(a["building"], 2),
            "objects_n": len(a["objects"]),
            "segments": sorted(a["segs"]),
            "gas": gas,
            "usd_per_kw": round(rate, 1),
            "usd_year": round(usd),
            "qty_year": {k: round(a["mw"] * v) for k, v in per_mw_qty.items()},
            "our_customer": OUR_CUSTOMERS.get(g, ""),
            "objects": sorted(a["objects"], key=lambda x: -x["mw"]),
        })
    # приоритет: деньги × наличие отношений
    for r in rows:
        boost = 2.0 if r["our_customer"].startswith("★") else 1.4 if r["our_customer"] else 1.0
        r["priority"] = round(r["usd_year"] * boost)
    rows.sort(key=lambda r: -r["priority"])

    total_mw = sum(r["mw"] for r in rows)
    total_usd = sum(r["usd_year"] for r in rows)
    warm_usd = sum(r["usd_year"] for r in rows if r["our_customer"])

    OUT.write_text(json.dumps({
        "updated": date.today().isoformat(),
        "source": "Расчёт gpu/tools/build_targets.py: реестр парка (data/fleet.json) × модель "
                  "потребления (data/consumption.json). Юрлица схлопнуты в холдинги, объекты в "
                  "статусе «проектируется» исключены, «строится» показано отдельно.",
        "caveat": "Оценка по мощности, а не по спецификации: реальная потребность зависит от "
                  "наработки, типа газа, возраста машин и того, что уже закуплено. Цифра нужна, "
                  "чтобы начать разговор с владельцем предметно, а не чтобы выставить счёт.",
        "item_names": names,
        "per_kw": {k: round(v, 1) for k, v in per_kw.items()},
        "totals": {"groups": len(rows), "mw": round(total_mw, 1), "usd_year": round(total_usd),
                   "warm_usd_year": round(warm_usd),
                   "warm_share": round(warm_usd / total_usd * 100) if total_usd else 0},
        "targets": rows,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"OK → {OUT}")
    print(f"   групп владельцев: {len(rows)}; парк {total_mw:.0f} МВт; "
          f"годовой ЗИП ≈ ${total_usd:,.0f}".replace(",", " "))
    print(f"   из них там, где отношения уже есть: ${warm_usd:,.0f} "
          f"({warm_usd/total_usd*100:.0f} %)".replace(",", " "))
    for r in rows[:8]:
        mark = "★" if r["our_customer"].startswith("★") else ("△" if r["our_customer"] else " ")
        print(f"   {mark} {r['group'][:44]:<44} {r['mw']:>7.1f} МВт  "
              f"${r['usd_year']:>9,}/год  свечей {r['qty_year']['plug']:>5}/год".replace(",", " "))


if __name__ == "__main__":
    main()
