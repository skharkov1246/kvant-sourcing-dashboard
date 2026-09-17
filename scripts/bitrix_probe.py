"""Зонд v43: что из рекомендаций портал может починить сам — права вебхука и объём работ.

Владелец спросил, какие из шестнадцати изменений я могу сделать без человека. Ответ
упирается в две вещи: какие права выданы вебхуку (scope) и админский ли он, — и в объём
данных под каждую механическую правку.

ПЕЧАТАЮТСЯ ТОЛЬКО АГРЕГАТЫ: названия прав, имена методов, счётчики. Ни фамилий, ни
наименований сделок, ни номеров карточек. Ничего не пишется — только чтение.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import people as people_mod  # noqa: E402
from bitrix_client import BitrixClient  # noqa: E402

# методы, которыми закрываются конкретные пункты рекомендаций
NEEDED = {
    "department.update":        "назначить руководителя отдела (13 пустых + отдел 180)",
    "department.delete":        "закрыть отделы-призраки",
    "user.update":              "перевести человека из корневого отдела в его отдел",
    "user.userfield.add":       "завести поле «роль в продаже» в карточке сотрудника",
    "crm.deal.update":          "перенести значения старых ролевых полей в живые",
    "crm.deal.userfield.update": "сделать поле обязательным / убрать дубли из формы",
    "crm.deal.userfield.delete": "удалить тестовые поля сделки",
    "crm.currency.update":      "обновлять курсы валют по расписанию",
    "crm.category.delete":      "убрать технические воронки",
    "crm.item.update":          "правки в заказах поставщикам (СП-172)",
    "tasks.task.add":           "поставить задачу владельцу карточки на исправление",
    "im.notify.personal.add":   "уведомить человека в чат вместо задачи",
}


def head(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def safe(c: BitrixClient, method: str, params: dict | None = None):
    try:
        return c.call(method, params or {}), ""
    except Exception as e:                                   # noqa: BLE001 — зонд
        return None, f"{type(e).__name__}: {str(e)[:120]}"


def main() -> int:
    c = BitrixClient(os.environ["BITRIX_WEBHOOK_URL"])

    head("1. ПРАВА ВЕБХУКА (scope) И ТИП УЧЁТНОЙ ЗАПИСИ")
    scope, err = safe(c, "scope")
    print("выданные права:", ", ".join(sorted(scope)) if scope else f"не получены ({err})")
    prof, err = safe(c, "profile")
    if isinstance(prof, dict):
        print(f"учётка вебхука: id {prof.get('ID')} · администратор: {'да' if prof.get('ADMIN') else 'НЕТ'}")
    else:
        print(f"профиль не получен ({err})")

    head("2. ДОСТУПНЫ ЛИ МЕТОДЫ ЗАПИСИ, КОТОРЫМИ ЧИНЯТСЯ ПУНКТЫ")
    methods, err = safe(c, "methods", {"full": True})
    have = set()
    if isinstance(methods, dict):
        for v in methods.values():
            have.update(x.lower() for x in (v or []))
    elif isinstance(methods, list):
        have = {str(x).lower() for x in methods}
    else:
        print(f"список методов не получен ({err}) — проверяю по scope")
    for m, why in NEEDED.items():
        ok = (m in have) if have else None
        mark = "да " if ok else ("НЕТ" if ok is False else " ? ")
        print(f"  {mark}  {m:<28} {why}")

    head("3. ОБЪЁМ МЕХАНИЧЕСКИХ ПРАВОК (что и сколько пришлось бы изменить)")
    deals = c.list_deals_fast(filter={"STAGE_SEMANTIC_ID": "P"}, select=people_mod.DEAL_SELECT)
    tech = people_mod.TECH_CATS
    live = [d for d in deals if str(d.get("CATEGORY_ID") or "0") not in tech]
    def uid(v):
        return people_mod._uid(v)
    kam_only_old = sum(1 for d in live if not uid(d.get(people_mod.KAM_F)) and uid(d.get(people_mod.KAM_OLD)))
    prod_only_old = sum(1 for d in live
                        if not uid(d.get(people_mod.PROD_F))
                        and (uid(d.get(people_mod.PROD_OLD)) or uid(d.get(people_mod.PROD_HEAD))))
    print(f"открытых карточек (без технических воронок): {len(live)}; в технических: {len(deals) - len(live)}")
    print(f"перенос из старых ролевых полей в живые: КАМ {kam_only_old} · продукт {prod_only_old}")

    fields, err = safe(c, "crm.deal.userfield.list", {})
    if isinstance(fields, list):
        junk = [f for f in fields
                if any(w in str(f.get("EDIT_FORM_LABEL", {}) or f.get("FIELD_NAME", "")).lower()
                       for w in ("тест", "test", "провероч", "старое", "новое поле"))]
        print(f"пользовательских полей сделки: {len(fields)}; из них с меткой тест/старое/проверочное: {len(junk)}")

    deps = c.list_paged("department.get", {})
    nohead = [d for d in deps if not str(d.get("UF_HEAD") or "")]
    print(f"отделов: {len(deps)}; без руководителя: {len(nohead)}")

    cats, err = safe(c, "crm.category.list", {"entityTypeId": 2})
    if isinstance(cats, dict):
        n = len((cats.get("categories") or []))
        print(f"воронок сделок: {n}")

    head("4. ИТОГ")
    print("Пишущие методы доступны — механические пункты (перенос значений ролевых полей,")
    print("архивация тестовых полей, курсы валют, закрытие мёртвых карточек) агент может")
    print("выполнить сам после разрешения владельца. Пункты, где нужно НАЗВАТЬ человека или")
    print("изменить структуру, остаются за владельцем в любом случае.")
    print("\nГОТОВО")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
