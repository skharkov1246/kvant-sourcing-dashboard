#!/usr/bin/env python3
"""Разведка по маркам ГПУ → данные сайта (models.json, oem_docs.json, repair.json).

Зачем отдельный скрипт. Разведка по каждой марке приходит одним большим объектом
(модельный ряд, мануалы, регламент, ремонтопригодность, обязательный оригинал,
субпоставщики). Руками её не разложить: марок много, а поля сайта другие — таблица
моделей ждёт `cyl/disp_l/kwe/rpm`, карточка марки — `name/full/status/note/parts_logic`.
Раскладку держим кодом, чтобы следующая волна разведки влилась одной командой, а не
переписыванием трёх файлов вручную.

Что делает дополнительно:
  • считает объём по диаметру и ходу (0,7854·D²·S·цил) и кладёт рядом с паспортным,
    чтобы на сайте было видно, сошлось или нет. Сорсер должен видеть проверку, а не
    верить цифре на слово;
  • считает объём одного цилиндра — именно он определяет комплект гильза-поршень-кольца;
  • марки, уже описанные в models.json (cummins, caterpillar, jenbacher), не затирает:
    у них другой набор полей, заполненный раньше и по другим источникам.

Вход: JSON-массив вида [{"key": ..., "title": ..., "result": {...}}, ...] —
как его отдаёт разведка. Файлов может быть несколько (по волне на файл).

Запуск:
    python gpu/tools/oem_import.py разведка1.json разведка2.json
    python gpu/tools/oem_import.py --check разведка1.json   # сверить без записи
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# Короткое имя марки для заголовка карточки: длинное юридическое название
# («MTU Friedrichshafen GmbH (бренд mtu; юрлицо с 2023 — Rolls-Royce Solutions GmbH)»)
# в шапку не влезает и мешает искать глазами.
SHORT = {
    "waukesha": "Waukesha",
    "guascor": "Guascor",
    "mtu": "MTU",
    "mwm": "MWM",
    "man": "MAN",
    "wartsila": "Wärtsilä",
    "perkins": "Perkins",
    "bergen": "Bergen",
    "liebherr": "Liebherr",
    "yanmar_kawasaki": "Yanmar и Kawasaki",
    "china": "Китайские марки",
    "scania_volvo_doosan": "Scania, Volvo Penta, Doosan",
    "ru_cis": "Российские марки",
    "other_eu_us": "Прочие марки Европы и США",
    "cummins_deep": "Cummins",
    "cat_deep": "Caterpillar",
    "innio_deep": "INNIO Jenbacher",
}

# Марки, карточка которых в models.json заведена раньше и вручную. Глубокие разборы
# этих марок идут в документацию и ремонтопригодность, а таблицу моделей не подменяют:
# там свои поля (КПД, применение), которых в разведке нет.
LEGACY = {"cummins_deep": "cummins", "cat_deep": "caterpillar", "innio_deep": "jenbacher"}


def cyl_count(s):
    """«16, V-образный» → 16. Число цилиндров в разведке приходит строкой с компоновкой."""
    digits = ""
    for ch in str(s):
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else None


def check_disp(m):
    """Дополняет модель расчётным объёмом и объёмом цилиндра. Возвращает отклонение в %."""
    b, s, c, dl = m.get("bore_mm"), m.get("stroke_mm"), cyl_count(m.get("cyl")), m.get("disp_l")
    if not (b and s and c):
        return None
    calc = 0.7854 * b * b * s * c / 1e6
    m["calc_l"] = round(calc, 1)
    m["disp_cyl_l"] = round(calc / c, 2)
    if not dl:
        m["disp_l"] = round(calc, 1)
        return 0.0
    dev = (calc - dl) / dl * 100
    m["dev_pct"] = round(dev, 1)
    return dev


def read_marks(paths):
    """Собирает волны в один список, последняя волна перебивает предыдущую по ключу."""
    by_key = {}
    for p in paths:
        for item in json.loads(Path(p).read_text(encoding="utf-8")):
            res = item.get("research") or item.get("result")
            if res:
                by_key[item["key"]] = (item, res)
    return by_key


def build_model_row(m):
    """Строка таблицы моделей. Порядок полей — порядок столбцов на сайте."""
    return {
        k: v
        for k, v in [
            ("model", m.get("model")),
            ("series", m.get("series")),
            ("cyl", m.get("cyl")),
            ("bore_mm", m.get("bore_mm")),
            ("stroke_mm", m.get("stroke_mm")),
            ("disp_l", m.get("disp_l")),
            ("disp_cyl_l", m.get("disp_cyl_l")),
            ("calc_l", m.get("calc_l")),
            ("dev_pct", m.get("dev_pct")),
            ("kwe", m.get("kwe")),
            ("rpm", m.get("rpm")),
            ("gas", m.get("gas")),
            ("years", m.get("years")),
            ("note", m.get("note")),
        ]
        if v not in (None, "")
    }


# Метки, которые умеет показывать сайт. Разведка иногда возвращает слово мимо
# словаря («warranty» вместо «commercial»), и без проверки такая позиция выводится
# на страницу английским словом вместо русской метки.
VOCAB = {
    "repairable": {"yes", "limited", "no", "unknown"},
    "kind": {"technical", "commercial", "mixed", "warranty"},
}


def build(paths, check=False):
    marks = read_marks(paths)
    if not marks:
        sys.exit("нет данных разведки во входных файлах")

    models = json.loads((DATA / "models.json").read_text(encoding="utf-8"))
    have = {o["key"] for o in models["oems"]}

    docs_oems, repair_oems, warns = [], [], []
    added_models = 0

    for key, (item, res) in marks.items():
        oem = res["oem"]
        short = SHORT.get(key, oem.get("name", key))
        legacy_key = LEGACY.get(key)

        # ── модельный ряд
        rows = []
        for m in res.get("models", []):
            dev = check_disp(m)
            if dev is not None and abs(dev) > 3:
                warns.append(
                    f"{key}/{m.get('model')}: паспорт {m.get('disp_l')} л, "
                    f"расчёт {m.get('calc_l')} л, отклонение {dev:+.1f} %"
                )
            rows.append(build_model_row(m))

        if legacy_key:
            # Глубокий разбор уже описанной марки: доливаем диаметр и ход в готовые
            # строки по совпадению обозначения, новых строк не создаём.
            card = next(o for o in models["oems"] if o["key"] == legacy_key)
            by_model = {r["model"]: r for r in rows}
            for old in card["models"]:
                new = by_model.get(old["model"])
                if not new:
                    continue
                for f in ("series", "bore_mm", "stroke_mm", "disp_cyl_l", "calc_l", "dev_pct"):
                    if f in new and f not in old:
                        old[f] = new[f]
            card["deep"] = key
        elif key not in have:
            models["oems"].append(
                {
                    "key": key,
                    "name": short,
                    "full": oem.get("name", short)
                    + (f" — {oem['country']}" if oem.get("country") else ""),
                    "owner": oem.get("owner", ""),
                    "status": oem.get("status", ""),
                    "note": oem.get("ru_presence", ""),
                    "parts_logic": oem.get("parts_logic", ""),
                    "brand_note": oem.get("brand_note", ""),
                    "models": rows,
                }
            )
            added_models += len(rows)

        # ── метки вне словаря сайта: не правим молча, а показываем
        for r in res.get("repairability", []):
            if r.get("repairable") not in VOCAB["repairable"]:
                warns.append(f"{key}/{r.get('unit')}: решение «{r.get('repairable')}» вне словаря сайта")
        for g in res.get("genuine_required", []):
            if g.get("kind") not in VOCAB["kind"]:
                warns.append(f"{key}/{g.get('unit')}: природа требования «{g.get('kind')}» вне словаря сайта")

        # ── документация и регламент
        docs_oems.append(
            {
                "key": legacy_key or key,
                "name": short,
                "manuals": res.get("manuals", []),
                "overhaul": res.get("overhaul", []),
                "caveats": res.get("caveats", []),
            }
        )

        # ── ремонтопригодность, обязательный оригинал, субпоставщики
        repair_oems.append(
            {
                "key": legacy_key or key,
                "name": short,
                "repairability": res.get("repairability", []),
                "genuine_required": res.get("genuine_required", []),
                "subsuppliers": res.get("subsuppliers", []),
            }
        )

    today = date.today().isoformat()
    docs = {
        "updated": today,
        "source": "Документы изготовителей и их сервисных сетей, найденные в открытом доступе. "
        "Отметка «документ прочитан» ставится только по факту открытия файла; где открыть "
        "не удалось, это сказано в оговорках по марке.",
        "intro": "Реестр документации по маркам газопоршневых установок. Для снабжения важны "
        "не сами двигатели, а четыре вещи из документов: расшифровка обозначения, таблица "
        "диаметров и ходов, каталожные номера расходников и интервалы обслуживания. В графе "
        "«Что внутри» указано именно это.",
        "rule": "Документ изготовителя старше поставки агрегата не заменяет каталог деталей по "
        "серийному номеру. Перед заказом запрашивайте у владельца агрегата серийный номер "
        "двигателя: по нему изготовитель поднимает каталог нужного исполнения.",
        "oems": docs_oems,
    }
    repair = {
        "updated": today,
        "source": "Руководства по капитальному ремонту изготовителей, перечни программ "
        "восстановления (reman / exchange / Reman), каталоги независимых заводов-восстановителей "
        "и сервисных компаний.",
        "intro": "Ремонтопригодность узла решает, что покупать: новую деталь, восстановленную "
        "или ремкомплект. Для каждой марки узлы разобраны по конструкции — сменные ли гильзы и "
        "сёдла клапанов, есть ли ремонтные размеры вала, восстанавливается ли головка.",
        "rule": "Требование «только оригинал» бывает двух разных природ. Техническое — "
        "конструктив, калибровка в блоке управления, допуск изготовителя, парная приработка; "
        "тут неоригинал действительно ломает агрегат. Коммерческое — изготовитель привязал "
        "гарантию или продаёт под своей маркой чужую деталь; тут аналог законен и выгоден. "
        "Графа «Природа требования» проводит эту границу.",
        "kinds": {
            "technical": "Техническая причина: конструктив, прошивка, допуск, приработка",
            "commercial": "Коммерческая причина: привязка гарантии или перемаркировка",
            "mixed": "Смешанная: есть и техническое основание, и коммерческая привязка",
        },
        "oems": repair_oems,
    }

    out = {
        "models.json": models,
        "oem_docs.json": docs,
        "repair.json": repair,
    }

    if check:
        for name, obj in out.items():
            old = (DATA / name).read_text(encoding="utf-8") if (DATA / name).exists() else ""
            if old.strip() != json.dumps(obj, ensure_ascii=False, indent=1).strip():
                sys.exit(f"{name} расходится с разведкой — перезапустите oem_import.py")
        print("данные совпадают с разведкой")
        return

    for name, obj in out.items():
        (DATA / name).write_text(
            json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )

    n_man = sum(len(o["manuals"]) for o in docs_oems)
    n_rep = sum(len(o["repairability"]) for o in repair_oems)
    n_gen = sum(len(o["genuine_required"]) for o in repair_oems)
    n_sub = sum(len(o["subsuppliers"]) for o in repair_oems)
    print(f"марок обработано: {len(marks)}")
    print(f"models.json: марок в файле {len(models['oems'])}, "
          f"моделей всего {sum(len(o['models']) for o in models['oems'])} (+{added_models})")
    print(f"oem_docs.json: мануалов {n_man}, интервалов регламента "
          f"{sum(len(o['overhaul']) for o in docs_oems)}")
    print(f"repair.json: узлов по ремонтопригодности {n_rep}, "
          f"позиций обязательного оригинала {n_gen}, субпоставщиков {n_sub}")
    if warns:
        print(f"\nТРЕБУЕТ ВНИМАНИЯ — {len(warns)}:")
        for w in warns:
            print("  " + w)
    else:
        print("расхождений по объёму и меток вне словаря нет")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--check"]
    if not args:
        sys.exit(__doc__)
    build(args, check="--check" in sys.argv[1:])
