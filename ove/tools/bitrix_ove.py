#!/usr/bin/env python3
"""Выгрузка поставщиков ОВЭ-75 из Bitrix24 → ove/data/bitrix_ove.json.

Отвечает на вопрос сорсинга перед заходом в конкурс за Базовый инжиниринг:
кого из нужного нам по ОВЭ-75 пула мы уже знаем, кому уже слали запросы
и чем это кончилось.

Ищет по двум осям:
  1. Сделки и запросы СП-166 (смарт-процесс 166 «Запросы поставщикам»),
     чьи названия совпадают с классами оборудования комплекса ОВЭ-75.
  2. Компании в CRM, чьи названия совпадают с именами кандидатов из
     мирового/китайского пула (ove/data/suppliers.json, поле world/cn).

Запуск: нужен BITRIX_WEBHOOK_URL (в CI — секрет). Без него скрипт
завершается штатно и НЕ трогает существующий JSON — на сайте останется сид.

    python3 ove/tools/bitrix_ove.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "ove" / "data" / "bitrix_ove.json"
SUPPLIERS = ROOT / "ove" / "data" / "suppliers.json"

# Ключевые слова по классам оборудования ОВЭ-75 — как их пишут в названиях
# сделок и запросов СП-166. Ключ — код класса из suppliers.json.
KEYWORDS: dict[str, list[str]] = {
    "roaster":    ["кипящего слоя", "печь КС", "обжиг", "roaster"],
    "whb":        ["котёл-утилизатор", "котел-утилизатор", "котел утилизатор", "КУ пара"],
    "esp":        ["электрофильтр", "электрофильтры", "ЭГА", "УГТ1"],
    "cyclone":    ["циклон", "СЦН-"],
    "fan":        ["дымосос", "ДН-15", "вентилятор дутьевой", "воздуходувка"],
    "baghouse":   ["рукавный фильтр", "рукавные фильтры", "аспирация"],
    "vacfilter":  ["вакуум-фильтр", "вакуумный фильтр", "барабанный фильтр"],
    "filterpress": ["фильтр-пресс", "фильтр пресс", "фильтрпресс"],
    "thickener":  ["сгуститель", "сгустители"],
    "centrifuge": ["центрифуга", "центрифуги"],
    "evaporator": ["выпарной", "выпарная", "кристаллизатор", "испаритель"],
    "hx":         ["теплообменник", "теплообменники", "пластинчатый теплообменник"],
    "cell":       ["электролизная ванна", "электролизные ванны", "ванна электролиз", "полимербетон"],
    "anode":      ["анод свинц", "аноды свинц", "свинцовый анод", "аноды из свинц"],
    "cathode":    ["катодная матрица", "катодные матрицы", "матрица катод"],
    "busbar":     ["ошиновка", "шинопровод", "магистральные шины"],
    "stripper":   ["катодосдирочн", "стрип-машина", "КСМ"],
    # «Основы» в медном электролизе — катодные основы (стартовые листы). Владелец
    # называет партнёра «поставщик по линии подготовки основ» — ищем его в CRM.
    "starter":    ["катодные основы", "катодных основ", "подготовки основ",
                   "стартовый лист", "стартовые листы", "основодерочн", "матричный лист"],
    "crane":      ["мостовой кран", "кран-балка", "спецкран", "кран электролиз"],
    "rectifier":  ["выпрямитель", "выпрямительный агрегат", "преобразовательный агрегат"],
    "conveyor":   ["конвейер", "транспортёр", "транспортер", "шнек", "питатель дисковый", "дозатор"],
    "pump":       ["насос погружной", "насос химический", "циркуляционный насос"],
    "agitator":   ["мешалка", "перемешивающее устройство", "реактор с мешалкой", "репульпатор"],
    "plc":        ["контроллер", "ПЛК", "Regul", "АБАК", "SCADA"],
    "instr":      ["расходомер", "уровнемер", "датчик давления", "термопреобразователь", "плотномер", "КИПиА"],
    "scale":      ["весы", "весовое оборудование", "весоизмерительн"],
    "burner":     ["горелка", "мазутная горелка", "ГМ-4,5", "МГМГ"],
}

SELECT = ["id", "title", "stageId", "createdTime", "parentId2", "companyId",
          "assignedById", "ufCrm18Supplier", "opportunity"]


def load_candidates() -> list[str]:
    """Имена компаний мирового/китайского пула из suppliers.json."""
    if not SUPPLIERS.exists():
        return []
    data = json.loads(SUPPLIERS.read_text(encoding="utf-8"))
    names: set[str] = set()
    for pos in data.get("positions", []):
        for key in ("world", "cn", "ru"):
            for name in pos.get(key) or []:
                # первое слово-бренд — по нему и ищем, Bitrix матчит подстрокой
                token = name.split("(")[0].strip()
                if len(token) >= 4:
                    names.add(token)
    return sorted(names)


def main() -> int:
    try:
        from bitrix_client import BitrixClient
        from config import SPA_ENTITY_TYPE_ID, Settings
        client = BitrixClient(Settings.load().bitrix_webhook_url)
    except Exception as exc:  # нет секрета / нет сети — оставляем сид как есть
        print(f"Bitrix недоступен ({exc}) — сид {OUT.name} не тронут")
        return 0

    users = client.users()
    stages = client.spa_stages(SPA_ENTITY_TYPE_ID, 24)

    def who(uid):
        return users.get(str(uid), f"#{uid}")

    def stage(sid):
        return stages.get(str(sid), str(sid))

    # 1. Запросы СП-166 по классам оборудования ОВЭ-75
    by_class: dict[str, dict[str, dict]] = defaultdict(dict)
    all_rfqs: dict[str, dict] = {}
    for cls, words in KEYWORDS.items():
        for kw in words:
            for r in client.list_items(SPA_ENTITY_TYPE_ID, filter={"%title": kw}, select=SELECT):
                by_class[cls][str(r["id"])] = r
                all_rfqs[str(r["id"])] = r
    print(f"запросов СП-166 по классам оборудования ОВЭ-75: {len(all_rfqs)}")

    comp_ids = {str(r.get("companyId")) for r in all_rfqs.values() if r.get("companyId")}
    names = client.companies_by_ids(comp_ids) if comp_ids else {}

    def pack(r):
        # Сайт временно открыт без пароля (распоряжение владельца 13.08.2026,
        # пароль ставится утром 14.08). На открытом ресурсе не публикуем имена
        # сорсеров, названия и номера запросов — это персональные данные людей,
        # которые в решении об открытом доступе не участвовали. Наружу идёт то,
        # что нужно сорсингу: компания, стадия, дата.
        #
        # Когда сайт закроют паролем — вернуть сюда id, title и sourcer:
        # по номеру запрос открывается в Bitrix одним кликом, а имя сорсера
        # отвечает на вопрос «к кому идти за историей переписки».
        return {
            "company": names.get(str(r.get("companyId"))) or r.get("ufCrm18Supplier") or "—",
            "stage": stage(r.get("stageId")),
            "created": str(r.get("createdTime"))[:10],
        }

    classes = {
        cls: sorted((pack(r) for r in items.values()), key=lambda x: x["created"], reverse=True)
        for cls, items in by_class.items() if items
    }

    # свод по компаниям: кому и сколько раз слали
    per_company: dict[str, dict] = {}
    for cls, rows in classes.items():
        for row in rows:
            rec = per_company.setdefault(row["company"], {"name": row["company"], "n": 0,
                                                          "last": "", "classes": set()})
            rec["n"] += 1
            rec["classes"].add(cls)
            if row["created"] > rec["last"]:
                rec["last"] = row["created"]
    companies = sorted(
        ({"name": v["name"], "n": v["n"], "last": v["last"], "classes": sorted(v["classes"])}
         for v in per_company.values()),
        key=lambda x: -x["n"])

    # 2. Компании мирового/китайского пула: заведены ли и слали ли запросы
    pool = []
    for cand in load_candidates():
        comps = client.list_paged("crm.company.list", {
            "filter": {"%TITLE": cand}, "select": ["ID", "TITLE", "WEB", "EMAIL"]})
        for c in comps[:3]:
            items = client.list_items(SPA_ENTITY_TYPE_ID,
                                      filter={"companyId": str(c["ID"])}, select=SELECT)
            items.sort(key=lambda x: str(x.get("createdTime")), reverse=True)
            pool.append({
                "query": cand,
                "name": c.get("TITLE"),
                "n": len(items),
                "last": str(items[0].get("createdTime"))[:10] if items else "",
            })
    print(f"компаний мирового пула найдено в CRM: {len(pool)}")

    OUT.write_text(json.dumps({
        "live": True,
        "updated": date.today().isoformat(),
        "source": "Bitrix24, смарт-процесс 166 «Запросы поставщикам» + crm.company.list",
        "n_rfq": len(all_rfqs),
        "classes": classes,
        "companies": companies,
        "pool": pool,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
