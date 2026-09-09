#!/usr/bin/env python3
"""Разбор исходников по щековой дробилке Telsmith 3858 (38x58, рудник Таймырский).

Источники (документы заказчика, в репозиторий не кладутся — только результат разбора):
  1. Дополнение №2 к прейскуранту цен №15-26 к договору поставки ЗФ-76/2026
     (ПАО «ГМК «Норильский никель» — АО «Нордфелт»), .xlsx — потребность и цены дилера;
  2. «Щековая дробилка Telsmith 38x58, модель 3858. Каталог запасных частей», .pdf —
     состав изделия по узлам: позиция на чертеже, Element ID, номер OEM, количество.

Запуск:  python3 zip/tools/telsmith_parse.py <прейскурант.xlsx> <каталог.pdf>
Результат: zip/data/telsmith_3858.json
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "telsmith_3858.json"

# Классы потребности: по ним ведётся сорсинг — у каждого свой передел и свой круг заводов.
CLASSES = [
    ("футеровка", "Футеровки и клинья щёк (литьё, марганцовистая сталь)",
     r"футеровк|клин|щек[аи]|плита дробящ"),
    ("распорная", "Распорная плита, седло, зажимы (литьё + расчёт на срез)",
     r"распорн|седло|зажим седла|проушина"),
    ("вал", "Эксцентриковый вал, подшипники, корпуса, шестерни (поковка, расточка)",
     r"вал|подшипник|корпус подшипника|торцевая крышка|распорная втулка|лабиринт|шестерн|маховик|шкив|кожух"),
    ("гидравлика", "Гидравлика: цилиндры, насосы, клапаны, РВД",
     r"цилиндр|насос|клапан|гидроаккум|распредел|рукав высокого|шланг|фитинг|манометр|гидромотор|делитель потока"),
    ("смазка", "Смазка и фильтрация: станции, баки, фильтры",
     r"смазочн|масл|фильтр|бак|теплообмен|радиатор|сапун|указатель уровня"),
    ("рти", "Уплотнения, кольца, прокладки", r"уплотн|кольц|манжет|прокладк|сальник"),
    ("крепёж", "Крепёж: болты, гайки, шайбы, шпильки",
     r"болт|гайк|шайб|шпильк|штифт|винт|контргайк|шплинт|анкер"),
    ("электрика", "Датчики, реле, панели, трансформаторы",
     r"датчик|реле|панель|трансформатор|переключ|лампа|кабель|термо"),
    ("прочее", "Прочее", r"."),
]

# Номер Telsmith двух видов: конструкционный (узел-деталь) и внутренний код покупного изделия.
RX_STRUCT = re.compile(r"\b([A-Z]{1,3}\d?-\d{2,3}-\d{3,4}[A-Z]?)\b")
RX_INNER = re.compile(r"\b(\d{2}[A-Z]\d{2})\b")


def cls_of(name):
    for key, _, rx in CLASSES:
        if re.search(rx, name, re.I):
            return key
    return "прочее"


def parse_catalog(pdf_path):
    """Каталог запчастей → состав изделия по узлам."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    parts, node = [], None
    for i, page in enumerate(reader.pages, 1):
        for line in (page.extract_text() or "").split("\n"):
            s = line.strip()
            if not s:
                continue
            if "(см. Рисунок" in s:  # заголовок узла: «ВАЛ ЭКСЦЕНТРИКОВЫЙ (см. Рисунок 2)»
                node = re.sub(r"\s*\(см\..*", "", s).strip(" .")
                continue
            # позиция на чертеже трёх- и четырёхзначная (узлы 12xx, 14xx, 17xx)
            m = re.match(r"^(\d{1,4}[A-Za-z]?)\s+(\d{10})\s+(\S+)\s+(.+?)\s+(\d+)$", s)
            if m:
                parts.append({"poz": m.group(1), "eid": m.group(2), "oem": m.group(3),
                              "name": m.group(4).strip(), "qty": int(m.group(5)),
                              "node": node, "page": i})
    return parts


def parse_pricelist(xlsx_path):
    """Прейскурант дилера → потребность с ценами."""
    import openpyxl

    ws = openpyxl.load_workbook(str(xlsx_path), data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = next(i for i, r in enumerate(rows)
                if r and any(isinstance(c, str) and "Каталожный" in c for c in r))
    need = []
    for r in rows[head + 2:]:
        if not r or not isinstance(r[0], (int, float)):
            continue
        need.append({"no": int(r[0]), "eid": str(r[1] or "").strip(), "ens": str(r[2] or "").strip(),
                     "name": str(r[3] or "").strip(), "qty": r[4],
                     "price_rub": r[5], "sum_rub": r[6], "lead_days": r[7]})
    return need


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    parts = parse_catalog(sys.argv[2])
    need = parse_pricelist(sys.argv[1])
    by_eid = {p["eid"]: p for p in parts}

    for n in need:
        p = by_eid.get(n["eid"])
        struct = RX_STRUCT.search(n["name"])
        inner = RX_INNER.search(n["name"])
        n["oem"] = p["oem"] if p else (struct.group(1) if struct else (inner.group(1) if inner else ""))
        # покупное изделие под внутренним кодом Telsmith — кандидат на прямую закупку у изготовителя
        n["pn_kind"] = "конструкционный" if RX_STRUCT.search(n["oem"] or "") else (
            "внутренний код покупного" if RX_INNER.search(n["oem"] or "") else "не определён")
        n["node"] = p["node"] if p else ""
        n["in_catalog"] = bool(p)
        n["class"] = cls_of(n["name"])

    groups = defaultdict(lambda: {"positions": 0, "sum_rub": 0})
    for n in need:
        g = groups[n["class"]]
        g["positions"] += 1
        if isinstance(n["sum_rub"], (int, float)):
            g["sum_rub"] += n["sum_rub"]

    doc = {
        "machine": {
            "name": "Telsmith 3858", "type": "Щековая дробилка, зев 38x58 дюймов (965 x 1473 мм)",
            "oem": "Telsmith; в составе Astec Industries с 1986-87 гг. через Barber-Greene, с 2021 единый бренд ASTEC",
            "status": "Снята с производства: площадка в Мекуоне остановлена 14.08.2020 и закрыта 31.03.2021; "
                      "модель поддерживается только запчастями, в действующем каталоге Astec отсутствует",
            "series_note": "НЕ относится к серии Iron Giant — по фирменной литературе это 4448 и 5060. "
                           "Модель 3858 идёт отдельно, рядом с 3258. Поиск по ключу «Iron Giant 3858» "
                           "уводит на другую машину и другие номера деталей (273-918 вместо 273-19xx)",
            "search_key": "Корень номера без префикса исполнения: 273-1917, 273-1922, 273-1723, 277-619, 277-601; "
                          "вторым ключом — типоразмер «Telsmith 38x58». Префикс задаёт исполнение, корень — узел",
            "site": "Рудник Таймырский, ПАО «ГМК «Норильский никель»",
            "channel": "АО «Нордфелт», прейскурант 15-26, доп. №2, срок поставки 120 дней"},
        "classes": [{"key": k, "title": t, **groups[k]} for k, t, _ in CLASSES if k in groups],
        "need": need,
        "catalog": parts,
        "stats": {"need_positions": len(need),
                  "need_sum_rub": round(sum(n["sum_rub"] for n in need if isinstance(n["sum_rub"], (int, float))), 2),
                  "catalog_rows": len(parts), "catalog_nodes": len({p["node"] for p in parts if p["node"]}),
                  "matched": sum(1 for n in need if n["in_catalog"])},
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    s = doc["stats"]
    print(f"zip/data/telsmith_3858.json: потребность {s['need_positions']} поз. на "
          f"{s['need_sum_rub']:,.0f} ₽".replace(",", " ") +
          f" | каталог {s['catalog_rows']} строк в {s['catalog_nodes']} узлах | сопоставлено {s['matched']}")
    for c in sorted(doc["classes"], key=lambda x: -x["sum_rub"]):
        print(f"  {c['positions']:3} поз. | {c['sum_rub']/1e6:7.1f} млн ₽ | {c['title']}")


if __name__ == "__main__":
    main()
