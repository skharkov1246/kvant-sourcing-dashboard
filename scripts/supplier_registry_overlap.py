#!/usr/bin/env python3
"""Пересечение файловых реестров поставщиков: чем их сводить и что при этом склеится.

ЗАЧЕМ. Поставщики живут в шести реестрах. Один — Bitrix (kb_rfq) — лежит в базе, и
его меряет scripts/supplier_merge_stats.py. Остальные пять лежат файлами прямо в
репозитории, поэтому их пересечение считается здесь и сейчас, без прогонов и
секретов. Правило сведения обязано опираться на замер, а не на догадку
(CLAUDE.md, правило 3).

ЧТО ВЫЯСНИЛ ЗАМЕР 20.09.2026 — и почему правило именно такое:

  • ДОМЕН СИЛЬНЕЕ ИМЕНИ. 62 домена несут по два и больше РАЗНЫХ написания имени,
    146 строк за ними. Слияние по имени их не найдёт: «parkerhannifincorporation»
    и «parkerhannifingasturbine» — одна компания, один сайт, разные строки.
    По ship_sellers против pnw домен дал 41 совпадение против 17 по имени.

  • ИМЯ ПОЧТИ БЕЗОПАСНО, НО НЕ СОВСЕМ. Всего 4 нормализованных имени из 2395 с
    доменом указывают на разные домены — 0,17 %. Три из четырёх при этом
    выглядят как один поставщик в разных странах (домены .ae и .com, .de и .uk),
    то есть скорее группа, чем чужая компания. Но проверять обязано правило, а
    не глаз: такие имена идут в очередь как кандидаты, а не сливаются.

  • ДОСЬЕ ГТУ СОВПАДАЮТ С pnw НА ДВЕ ТРЕТИ: 877 имён из 1349. При этом у досье
    НЕТ ни одного сайта, поэтому проверить их доменом нечем — только именем.
    Это самый крупный и самый слабо обоснованный кусок сведения.

ОТСЮДА ПРАВИЛО, которое закладывается в sup_identifier:
  1. совпал домен → слияние, evidence = домен, status = verified;
  2. совпало имя И домены не противоречат → слияние, status = inferred;
  3. совпало имя, а домены разные → НЕ слияние: строка в sup_review,
     kind = ambiguous_match;
  4. совпало только имя, домена нет ни у одной стороны (случай досье ГТУ) →
    status = candidate, слияние ждёт подтверждения человеком.

ЧТО ПЕЧАТАЕТ. По умолчанию только числа. Названия компаний лежат в этих же
файлах публичного репозитория, то есть новой утечки не создают, — но в журнал
прогона они не идут (правило 17). Примеры показывает --examples, и домены в них
маскируются до двух букв.

    python scripts/supplier_registry_overlap.py
    python scripts/supplier_registry_overlap.py --examples
"""
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Пять файловых реестров. Шестой — Bitrix — в базе, его меряет supplier_merge_stats.py.
РЕЕСТРЫ = [
    ("pnw/supplier_master", "pnw/data/supplier_master.json", "suppliers", "name", "site"),
    ("gt/dossiers",         "gt/data/dossiers.json",         "dossiers",  None,   "site"),
    ("gt/ship_sellers",     "gt/data/ship_sellers.json",     "rows",      "seller", "site"),
    ("zip/supplier_crm",    "zip/data/supplier_crm.json",    "suppliers", "name", "site"),
]

# Правовые формы: у одной компании они пишутся по-разному и различать по ним нельзя.
ФОРМЫ = re.compile(
    r"\b(gmbh|ltd|limited|llc|inc|co|corp|corporation|company|s\.?a\.?|b\.?v\.?|"
    r"pte|plc|ооо|зао|оао|ао|пао|тоо)\b\.?", re.I)
# Почтовые хостинги: адрес на них не опознаёт компанию.
ХОСТИНГИ = {"mail", "gmail", "yandex", "qq", "163", "126", "outlook", "hotmail",
            "yahoo", "inbox", "list", "bk", "rambler"}


def norm_name(s: str | None) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"[«»\"'`]", "", s)
    s = ФОРМЫ.sub("", s)
    return re.sub(r"[^a-zа-я0-9]+", "", s)


# Сокращения ОДНОГО И ТОГО ЖЕ слова. Ltd — это и есть Limited, Corp — Corporation:
# блокировать по ним слияние значит расщеплять компанию из-за того, что в одном
# реестре вывеску написали полностью, а в другом сократили. Прогон 20.09.2026 на
# пяти файловых реестрах поймал ровно такие две пары из четырёх сработок.
#
# А вот ооо ↔ llc сюда НЕ ВХОДИТ, и это не забывчивость: это перевод вывески, а не
# сокращение. Совпадение перевода ничего не доказывает про юрлицо — нужен домен
# или ИНН. То же с ао ↔ jsc, оао ↔ pjsc.
СИНОНИМЫ_ФОРМ = {
    "limited": "ltd",
    "corporation": "corp",
    "incorporated": "inc",
    "company": "co",
}


def форма(s: str | None) -> frozenset[str]:
    """Правовые формы, найденные в сыром названии: {'ооо'}, {'ао'}, пустое.

    ЗАЧЕМ ОТДЕЛЬНО ОТ norm_name. Та формы ВЫРЕЗАЕТ — и правильно делает: одна
    компания пишется «ООО Ромашка», «Ромашка, ООО» и «Romashka LLC», по форме их
    не свести. Но у вырезания есть обратная цена: «ООО Ромашка» и «АО Ромашка»
    после нормализации неотличимы, а это РАЗНЫЕ ЮРИДИЧЕСКИЕ ЛИЦА. Слить их —
    нарушить запрет смешивать юрлица (ТЗ §1.12) и приписать одному чужие сделки.

    Поэтому форма не участвует в поиске совпадения, а только запрещает его:
    имена совпали и формы РАЗНЫЕ — не сливаем, строка уходит человеку. Формы нет
    ни у одной стороны или она одна и та же — сливаем как прежде.

    Пары вроде ооо/llc намеренно НЕ считаются равными: это перевод вывески, а не
    доказательство, что за ними одно лицо. Нужен домен или ИНН.
    """
    сырые = (m.group(1).lower().replace(".", "")
             for m in ФОРМЫ.finditer(s or "") if m.group(1))
    return frozenset(СИНОНИМЫ_ФОРМ.get(f, f) for f in сырые if f)


def norm_domain(s: str | None) -> str:
    m = re.search(r"(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)",
                  (s or "").lower().strip())
    if not m:
        return ""
    d = m.group(1)
    return "" if d.split(".")[0] in ХОСТИНГИ else d


def маска(d: str) -> str:
    """Домен в примерах — до двух букв: пример нужен для формы, не для адреса."""
    точка = d.rfind(".")
    return f"{d[:2]}…{d[точка:]}" if точка > 0 else d[:2] + "…"


def читать() -> dict[str, list[tuple[str, str]]]:
    """Реестр → список (нормализованное имя, нормализованный домен)."""
    out: dict[str, list[tuple[str, str]]] = {}
    for имя, путь, ключ, поле_имени, поле_сайта in РЕЕСТРЫ:
        файл = ROOT / путь
        if not файл.exists():
            continue
        узел = json.loads(файл.read_text(encoding="utf-8"))[ключ]
        строки = []
        items = узел.items() if isinstance(узел, dict) else enumerate(узел)
        for ключ_записи, запись in items:
            if not isinstance(запись, dict):
                continue
            # у досье имя записи и есть ключ словаря
            сырое = запись.get(поле_имени) if поле_имени else ключ_записи
            строки.append((norm_name(сырое), norm_domain(запись.get(поле_сайта))))
        out[имя] = строки
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--examples", action="store_true",
                    help="показать примеры расхождений (домены маскируются)")
    args = ap.parse_args()

    наборы = читать()
    if not наборы:
        print("ни один реестр не найден")
        return 1

    print("РЕЕСТР                    строк  с именем  с доменом  разных имён  разных доменов")
    по_имени: dict[str, set[str]] = collections.defaultdict(set)
    по_домену: dict[str, set[str]] = collections.defaultdict(set)
    for реестр, строки in наборы.items():
        имена = {n for n, _ in строки if n}
        домены = {d for _, d in строки if d}
        for n in имена:
            по_имени[n].add(реестр)
        for d in домены:
            по_домену[d].add(реестр)
        print(f"{реестр:24} {len(строки):6}  {sum(1 for n, _ in строки if n):8}  "
              f"{sum(1 for _, d in строки if d):9}  {len(имена):11}  {len(домены)}")

    def пары(индекс):
        c = collections.Counter()
        for где in индекс.values():
            сорт = sorted(где)
            for i, a in enumerate(сорт):
                for b in сорт[i + 1:]:
                    c[(a, b)] += 1
        return c

    for заголовок, индекс in (("ПЕРЕСЕЧЕНИЕ ПО ИМЕНИ", по_имени),
                              ("ПЕРЕСЕЧЕНИЕ ПО ДОМЕНУ", по_домену)):
        print(f"\n{заголовок}")
        c = пары(индекс)
        for (a, b), n in c.most_common():
            print(f"  {a:24} ∩ {b:24} {n}")
        if not c:
            print("  ни одного совпадения")

    # Опасное направление: одно имя указывает на разные домены.
    все = [(n, d) for строки in наборы.values() for n, d in строки if n and d]
    домены_имени: dict[str, set[str]] = collections.defaultdict(set)
    имена_домена: dict[str, set[str]] = collections.defaultdict(set)
    for n, d in все:
        домены_имени[n].add(d)
        имена_домена[d].add(n)
    спорных = {n: v for n, v in домены_имени.items() if len(v) > 1}
    многоимённых = {d: v for d, v in имена_домена.items() if len(v) > 1}

    print(f"\nОДНО ИМЯ — РАЗНЫЕ ДОМЕНЫ: {len(спорных)} из {len(домены_имени)} "
          f"({100 * len(спорных) / max(len(домены_имени), 1):.2f} %)")
    print("  слияние ПО ИМЕНИ склеило бы разные компании — эти идут в очередь проверки")
    if args.examples:
        for n, v in list(спорных.items())[:8]:
            print(f"    {len(v)} домена: {', '.join(маска(x) for x in sorted(v))}")

    print(f"\nОДИН ДОМЕН — РАЗНЫЕ ИМЕНА: {len(многоимённых)} доменов, "
          f"{sum(len(v) for v in многоимённых.values())} написаний")
    print("  это одна компания под разными написаниями — слияние ПО ИМЕНИ их пропустит")
    if args.examples:
        for d, v in list(многоимённых.items())[:8]:
            print(f"    {маска(d):14} → {len(v)} написания")

    строк = sum(len(v) for v in наборы.values())
    print(f"\nИТОГО строк в файловых реестрах: {строк}")
    print(f"Разных нормализованных имён:     {len(по_имени)}")
    print(f"Схлопнулось бы по имени:         {строк - len(по_имени)}")
    print("\nШестой реестр — Bitrix (kb_rfq) — здесь не считается: он в базе.")
    print("Его меряет scripts/supplier_merge_stats.py, Actions → «Поставщики — замер сведения».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
