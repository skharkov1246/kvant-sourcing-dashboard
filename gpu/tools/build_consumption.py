#!/usr/bin/env python3
"""Сборка gpu/data/consumption.json — модель годового потребления ЗИП на одну машину.

Зачем. Пока мы считаем по присланной спецификации, мы реактивны: отвечаем на то,
что заказчик уже посчитал сам. Модель разворачивает логику: зная модель двигателя,
наработку и тип газа, мы САМИ считаем, сколько чего он съест за год — и приходим
к владельцу раньше, чем он напишет запрос.

Модель простая и потому проверяемая:
    штук в год = (число цилиндров или фиксированное кол-во на машину)
                 × наработка_ч_в_год / интервал_замены_ч
                 × коэффициент тяжести газа

Все входные допущения лежат в ASSUMPTIONS и печатаются на сайте: любой инженер
может оспорить конкретную цифру, не разбирая всю модель.

Запуск:  python3 gpu/tools/build_consumption.py
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/consumption.json"

# ── допущения модели ────────────────────────────────────────────────────────
ASSUMPTIONS = {
    "hours_per_year": {
        "base": 8000,
        "note": "Базовый режим ГПЭС: круглогодичная работа с плановыми остановами (≈91 % готовности). "
                "Для пиковых и резервных машин ставить 2000–4000 ч, для теплиц с сезонной досветкой — 5000–6000 ч.",
    },
    "gas_severity": {
        "natural": {"k": 1.0, "name": "Природный газ", "note": "Базовый случай, интервалы паспортные."},
        "associated": {"k": 1.5, "name": "Попутный нефтяной газ", "note": "Тяжёлые фракции, нестабильный состав: "
                       "свечи и масло меняются в 1,5 раза чаще. Это самый массовый случай в РФ."},
        "biogas": {"k": 2.0, "name": "Биогаз / свалочный / шахтный", "note": "Сера, силоксаны, хлор — интервалы "
                   "сокращаются вдвое, а иногда сильнее. Масло уходит в отдельный регламент по числу зол."},
    },
    "method": "Интервалы взяты типовые для природного газа (свечи 2000 ч, масло и фильтры 1000–2000 ч, "
              "промежуточное ТО 10 000 ч, верхний ремонт 20–30 тыс. ч, капремонт 60 тыс. ч) и делятся на "
              "коэффициент тяжести газа. Точные интервалы — только из сервисной документации конкретной машины; "
              "модель нужна для планирования закупки и разговора с владельцем, а не как регламент.",
    "prices": "Цены за штуку — середина наших вилок из вкладки «Наш спрос» (открытая розница). Реальная "
              "закупочная будет ниже; модель показывает ПОРЯДОК денег, а не смету.",
}

# позиция: как считается количество (per_cyl — на цилиндр, per_engine — на машину),
# интервал в моточасах, тип износа, ориентировочная цена за штуку в USD
ITEMS = [
    {"key": "plug", "name": "Свеча зажигания", "basis": "per_cyl", "interval": 2000, "usd": 210,
     "sys": "ignition", "note": "Главный расходник. Цена — середина вилки по 4380132 и 5373898."},
    {"key": "plug_seal", "name": "Комплект уплотнений колодца свечи (о-кольца, шайба)", "basis": "per_cyl",
     "interval": 4000, "usd": 12, "sys": "seals", "note": "Меняется обычно через одну замену свечи."},
    {"key": "boot", "name": "Колпачок / удлинитель свечи", "basis": "per_cyl", "interval": 8000, "usd": 50,
     "sys": "ignition", "note": "Ресурс выше свечи, но ниже катушки."},
    {"key": "coil", "name": "Катушка зажигания", "basis": "per_cyl", "interval": 15000, "usd": 260,
     "sys": "ignition", "note": "Дорогая позиция с длинным ресурсом — идеальный кандидат на аналог MOTORTECH."},
    {"key": "oil_filter", "name": "Фильтр масляный", "basis": "per_engine", "qty": 4, "interval": 1500, "usd": 95,
     "sys": "filtration"},
    {"key": "air_filter", "name": "Фильтр воздушный", "basis": "per_engine", "qty": 2, "interval": 4000, "usd": 260,
     "sys": "filtration"},
    {"key": "gas_filter", "name": "Фильтр газовый", "basis": "per_engine", "qty": 2, "interval": 4000, "usd": 700,
     "sys": "gas"},
    {"key": "blowby", "name": "Фильтр вентиляции картера (blow-by)", "basis": "per_engine", "qty": 2,
     "interval": 4000, "usd": 300, "sys": "filtration"},
    {"key": "coolant_filter", "name": "Фильтр охлаждающей жидкости", "basis": "per_engine", "qty": 2,
     "interval": 2000, "usd": 28, "sys": "filtration"},
    {"key": "prechamber_valve", "name": "Клапан газовый форкамеры", "basis": "per_cyl", "interval": 10000, "usd": 375,
     "sys": "gas", "note": "Только на форкамерных двигателях (Jenbacher Type 4/6, часть Cummins и CAT)."},
    {"key": "head", "name": "Головка блока цилиндров (замена или восстановление)", "basis": "per_cyl",
     "interval": 30000, "usd": 3500, "sys": "head", "note": "Верхний ремонт. Самая тяжёлая строка бюджета владельца."},
    {"key": "valve", "name": "Клапаны впуск/выпуск (комплект на цилиндр)", "basis": "per_cyl", "interval": 30000,
     "usd": 320, "sys": "head"},
    {"key": "rings", "name": "Комплект поршневых колец", "basis": "per_cyl", "interval": 30000, "usd": 380,
     "sys": "cpg"},
    {"key": "liner", "name": "Гильза цилиндра", "basis": "per_cyl", "interval": 60000, "usd": 850, "sys": "cpg"},
    {"key": "piston", "name": "Поршень", "basis": "per_cyl", "interval": 60000, "usd": 1200, "sys": "cpg"},
    {"key": "rod_bearing", "name": "Вкладыш шатунный (комплект на цилиндр)", "basis": "per_cyl", "interval": 30000,
     "usd": 150, "sys": "crank"},
    {"key": "main_bearing", "name": "Вкладыш коренной", "basis": "per_engine", "qty": 9, "interval": 60000,
     "usd": 180, "sys": "crank", "note": "Число коренных зависит от компоновки; 9 — как у V16 CAT G3516."},
    {"key": "turbo", "name": "Турбокомпрессор (ремонт картриджа)", "basis": "per_engine", "qty": 1,
     "interval": 30000, "usd": 6000, "sys": "air", "note": "Ремонт, а не новая турбина: новая — от $16 тыс."},
]

# машины: цилиндры, электрическая мощность (кВт), типичный газ в РФ
ENGINES = [
    {"model": "Jenbacher J320 (Type 3)", "oem": "Jenbacher", "cyl": 20, "kwe": 1064, "gas": "associated"},
    {"model": "Jenbacher J420 (Type 4)", "oem": "Jenbacher", "cyl": 20, "kwe": 1413, "gas": "natural"},
    {"model": "Jenbacher J620 (Type 6)", "oem": "Jenbacher", "cyl": 20, "kwe": 3043, "gas": "natural"},
    {"model": "Jenbacher J616 (Type 6)", "oem": "Jenbacher", "cyl": 16, "kwe": 2433, "gas": "natural"},
    {"model": "Caterpillar G3516", "oem": "Caterpillar", "cyl": 16, "kwe": 1030, "gas": "associated"},
    {"model": "Caterpillar G3520", "oem": "Caterpillar", "cyl": 20, "kwe": 2000, "gas": "associated"},
    {"model": "Cummins QSV91G", "oem": "Cummins", "cyl": 18, "kwe": 1540, "gas": "associated"},
    {"model": "Cummins QSK60G", "oem": "Cummins", "cyl": 16, "kwe": 1540, "gas": "associated"},
    {"model": "Waukesha VHP L7042", "oem": "Waukesha", "cyl": 12, "kwe": 1100, "gas": "associated"},
    {"model": "MWM TCG 2020 (CAT CG170)", "oem": "MWM/CAT", "cyl": 16, "kwe": 1560, "gas": "natural"},
]


def per_engine_year(engine, hours, item):
    """Штук в год по одной позиции для одной машины."""
    k = ASSUMPTIONS["gas_severity"][engine["gas"]]["k"]
    # тяжесть газа бьёт по расходникам камеры сгорания и масла, но не по КШМ
    severity = k if item["sys"] in ("ignition", "filtration", "gas", "head", "seals") else 1.0
    base = engine["cyl"] if item["basis"] == "per_cyl" else item.get("qty", 1)
    return base * hours / (item["interval"] / severity)


def main() -> None:
    hours = ASSUMPTIONS["hours_per_year"]["base"]
    rows = []
    for e in ENGINES:
        items, total = [], 0.0
        for it in ITEMS:
            q = per_engine_year(e, hours, it)
            usd = q * it["usd"]
            total += usd
            items.append({"key": it["key"], "name": it["name"], "sys": it["sys"],
                          "qty_year": round(q, 1), "usd_unit": it["usd"], "usd_year": round(usd),
                          "interval": it["interval"], "note": it.get("note", "")})
        items.sort(key=lambda x: -x["usd_year"])
        rows.append({
            "model": e["model"], "oem": e["oem"], "cyl": e["cyl"], "kwe": e["kwe"],
            "gas": e["gas"], "gas_name": ASSUMPTIONS["gas_severity"][e["gas"]]["name"],
            "usd_year": round(total),
            "usd_per_kw_year": round(total / e["kwe"], 1),
            "usd_per_mwh": round(total / (e["kwe"] * hours / 1000), 2),
            "items": items,
        })
    rows.sort(key=lambda r: -r["usd_year"])

    # средний удельный показатель — им считается ёмкость рынка по мегаваттам
    avg_kw = sum(r["usd_per_kw_year"] for r in rows) / len(rows)

    OUT.write_text(json.dumps({
        "updated": date.today().isoformat(),
        "source": "Расчёт gpu/tools/build_consumption.py по типовым интервалам ТО и нашим ценовым вилкам.",
        "assumptions": ASSUMPTIONS,
        "hours_per_year": hours,
        "avg_usd_per_kw_year": round(avg_kw, 1),
        "engines": rows,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"OK → {OUT}")
    print(f"   наработка {hours} ч/год; средний удельный расход ЗИП ≈ ${avg_kw:.0f} на кВт эл. в год")
    for r in rows:
        print(f"   {r['model']:<32} {r['cyl']:>3} цил  {r['kwe']:>5} кВт  "
              f"${r['usd_year']:>8,}/год  ${r['usd_per_kw_year']:>5}/кВт  ${r['usd_per_mwh']:>5}/МВт·ч".replace(",", " "))


if __name__ == "__main__":
    main()
