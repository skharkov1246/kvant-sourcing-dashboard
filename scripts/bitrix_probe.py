"""Зонд v47: откуда у руководителя отдела поиска поставщиков сотни запросов.

ВОПРОС. После PR #401 владелец видит на дашборде у руководителя отдела
несколько сотен запросов. Запасное звено «руководитель сорсинга» даёт лишь 24,
значит остальное пришло другим путём — и какой это путь, надо знать, а не
предполагать.

ЗОНД ПРОГОНЯЕТ ТОТ ЖЕ САМЫЙ РАСЧЁТ, ЧТО ДАШБОРД. Карточки отчётного окна,
родительские сделки с теми же полями, те же служебные записи — и настоящий
metrics.build. После него у каждой карточки стоит `_ownerBy` — чем определён
исполнитель. По нему и раскладываются запросы руководителя: записан ли он
ответственным в самой карточке Битрикса, пришёл ли через поле сделки, через
след на карточке или через владельца сделки.

Для карточек, где руководитель записан ответственным в самом Битриксе, зонд
проверяет второе: кто их создал и кто указан «Сорсером» в сделке. Если сорсер в
сделке — другой человек, руководитель выступает раздатчиком, а не
исполнителем, и это видно числом.

Печатаются только агрегаты: номер руководителя, счётчики и доли. Имён нет.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import metrics as metrics_mod  # noqa: E402
import period as period_mod  # noqa: E402
from bitrix_client import BitrixClient  # noqa: E402

SELECT = ["id", "assignedById", "createdBy", "stageId", "createdTime", "movedTime",
          "parentId2", "categoryId", "title", "movedBy", "updatedBy", "lastActivityBy"]


def доля(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f} %)" if whole else f"{part}/0"


def роль(uid, head: str, dept: set[str], service: set[str]) -> str:
    """Кем приходится запись: руководитель, робот, сотрудник отдела или нет.
    Порядок значим: руководитель числится в отделе, но считается отдельно."""
    u = str(uid or "")
    if u in ("", "0", "None"):
        return "без автора"
    if u == head:
        return "сам руководитель"
    if u in service:
        return "служебная запись"
    if u in dept:
        return "сотрудник отдела"
    return "вне отдела"


def сорсер_сделки(deal: dict | None, code: str, head: str, dept: set[str]) -> str:
    """Кто указан «Сорсером» в сделке. Множественное поле — первый непустой."""
    if not deal:
        return "сделки нет"
    v = deal.get(code)
    if isinstance(v, list):
        v = next((x for x in v if str(x or "") not in ("", "0", "None")), "")
    v = str(v or "")
    if v in ("", "0", "None"):
        return "«Сорсер» не заполнен"
    if v == head:
        return "«Сорсер» — сам руководитель"
    if v in dept:
        return "«Сорсер» — другой сотрудник отдела"
    return "«Сорсер» — вне отдела"


def заголовок(t: str) -> None:
    print()
    print(t)
    print("-" * len(t), flush=True)


def main() -> int:
    url = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not url:
        print("нет BITRIX_WEBHOOK_URL", file=sys.stderr)
        return 2
    c = BitrixClient(url)
    print("ЗОНД v47 · откуда у руководителя отдела сотни запросов")

    # руководитель отдела — из справочника, а не из кода
    deps = c.departments()
    head = next((str(d.get("UF_HEAD") or "") for d in deps
                 if str(d.get("ID")) == str(config.DEPT_SOURCING_ID)), "")
    dept = c.dept_member_ids(config.DEPT_SOURCING_ID)
    names = c.users()
    depts = c.user_dept_names()
    service = config.service_accounts(names)
    print(f"  руководитель отдела {config.DEPT_SOURCING_ID}: #{head or '—'}; "
          f"сам в составе отдела: {'да' if head in dept else 'нет'}; сотрудников {len(dept)}")
    print(f"  служебных записей: {len(service)}")

    p = period_mod.parse_period(f"{config.DEFAULT_PERIOD_ANCHOR}:{dt.date.today().isoformat()}")
    rfqs = c.list_items(config.SPA_ENTITY_TYPE_ID,
                        filter={"categoryId": config.SPA_CATEGORY_ID,
                                ">=createdTime": p.start_iso, "<=createdTime": p.end_iso},
                        select=SELECT)
    print(f"  карточек окна {p.label}: {len(rfqs)}")
    raw_head = sum(1 for r in rfqs if str(r.get("assignedById")) == head)
    print(f"  из них в Битриксе ответственным записан руководитель: {доля(raw_head, len(rfqs))}")

    sourcer_codes = [f for f, *_ in config.DEAL_SOURCER_FIELDS]
    parent_ids = {str(r.get("parentId2")) for r in rfqs if r.get("parentId2")}
    deal_index = c.deals_by_ids(parent_ids, select=[
        "ID", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "ASSIGNED_BY_ID", *sourcer_codes])

    # тот же расчёт, что у дашборда
    m = metrics_mod.build(p, rfqs, deal_index, [], dept, names, {}, {}, {},
                          depts, service, None, config.DEAL_SOURCER_FIELDS)

    заголовок("1. ЗАПРОСЫ, ЗАСЧИТАННЫЕ РУКОВОДИТЕЛЮ, — ЧЕМ ОПРЕДЕЛЁН ИСПОЛНИТЕЛЬ")
    свои = [r for r in rfqs if r.get("_owner") == head]
    print(f"  всего засчитано руководителю: {доля(len(свои), len(rfqs))}")
    for how, n in Counter(r.get("_ownerBy") for r in свои).most_common():
        print(f"    {n:6}  {how}")
    строка = next((s for s in m.get("sourcersA") or [] if s["id"] == head), None)
    print(f"  в таблице сорсеров дашборда у руководителя: {строка['c'] if строка else 'строки нет'}")

    заголовок("2. ГДЕ РУКОВОДИТЕЛЬ ЗАПИСАН ОТВЕТСТВЕННЫМ В САМОМ БИТРИКСЕ")
    прямые = [r for r in rfqs if str(r.get("assignedById")) == head]
    n = len(прямые)
    if not n:
        print("  таких карточек нет")
        return 0
    авторы = Counter(роль(r.get("createdBy"), head, dept, service) for r in прямые)
    print("  кто создал эти карточки:")
    for k, v in авторы.most_common():
        print(f"    {доля(v, n):>18}  {k}")

    двигал = Counter(роль(r.get("movedBy"), head, dept, service) for r in прямые)
    print("  кто последним двигал их стадию:")
    for k, v in двигал.most_common():
        print(f"    {доля(v, n):>18}  {k}")

    сорсер = Counter(сорсер_сделки(deal_index.get(str(r.get("parentId2"))), sourcer_codes[0],
                                   head, dept) for r in прямые)
    print("  кто указан «Сорсером» в их сделке:")
    for k, v in сорсер.most_common():
        print(f"    {доля(v, n):>18}  {k}")

    недели = Counter((r.get("createdTime") or "")[:7] for r in прямые)
    print("  по месяцам создания: " + ", ".join(f"{k} — {v}" for k, v in sorted(недели.items())))
    print()
    print("Замер, записи не было.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
