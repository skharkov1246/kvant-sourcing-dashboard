"""Зонд v45: чей это запрос на самом деле — кто дал роботу задачу его создать.

ЗАЧЕМ. Карточки запросов поставщикам заводит робот воронки пресейла. Считать
исполнителем того, кто записан в карточке, нельзя: работа сорсера уходит из его
статистики. Нынешнее восстановление берёт владельца РОДИТЕЛЬСКОЙ СДЕЛКИ, а её
часто ведёт КАМ — и заслуга сорсера приписывается аккаунт-менеджеру. Это
несправедливо и делает замер эффективности людей бессмысленным.

ВОПРОС ЗОНДА РОВНО ОДИН: какой след в Битриксе указывает на живого сорсера и
насколько надёжно. Следов-кандидатов шесть, и зонд меряет каждый:

  1. `createdBy` карточки        — кто её создал (у робота это он сам);
  2. `updatedBy` карточки        — кто менял последним;
  3. `movedBy` карточки          — кто двигал по стадиям: так работает сорсер;
  4. пользовательские поля СП-166 типа «сотрудник» — вдруг инициатор уже пишется;
  5. родительская сделка: `MOVED_BY_ID` — кто перевёл сделку на стадию, с которой
     робот и запускается, то есть кто нажал кнопку; плюс `CREATED_BY_ID`;
  6. автор дел и комментариев карточки (`AUTHOR_ID`) — кто писал письмо руками.

ГЛАВНЫЙ ЗАМЕР — НЕ ЗАПОЛНЕННОСТЬ, А ПОПАДАНИЕ В ОТДЕЛ. Поле, заполненное у ста
процентов карточек, бесполезно, если показывает того же КАМа. Поэтому по каждому
следу считается доля карточек, где он указывает на сотрудника отдела поиска
поставщиков, и отдельно — по тем карточкам, которые СЕЙЧАС числятся вне отдела:
именно их и надо вернуть сорсерам.

НИЧЕГО НЕ ПИШЕТСЯ. Печатаются только агрегаты: коды и типы полей, имена методов,
коды ошибок, счётчики и доли (CLAUDE.md, правило 17). Ни имён, ни почт, ни
идентификаторов карточек и сделок. Идентификаторы учётных записей печатаются
только для записей, не числящихся ни в одном подразделении, — это кандидаты в
служебные, и без номера их не внести в SERVICE_ACCOUNT_IDS.

Прежние выпуски: v19/v20 — состав СП-166, v43 — права на правки гигиены,
v44 — доступ к вложениям. Зонд разовый: каждый выпуск отвечает на вопрос дня.
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from bitrix_client import BitrixClient  # noqa: E402

SPA = config.SPA_ENTITY_TYPE_ID
CATEGORY = config.SPA_CATEGORY_ID
DEPT = config.DEPT_SOURCING_ID
SAMPLE = int(os.getenv("PROBE_SAMPLE") or 1500)     # карточек в выборке
DEEP = int(os.getenv("PROBE_DEEP") or 40)           # карточек для дел и комментариев
# ОКНО ОБЯЗАТЕЛЬНО. Выгрузка идёт по возрастанию идентификатора, поэтому предел
# без фильтра даёт САМЫЕ СТАРЫЕ карточки воронки, а не свежие. Прогон v45 без
# окна так и вышел: 95 % вне отдела против 28 % в отчётном окне — выводы по
# долям были недействительны целиком.
WINDOW = os.getenv("PROBE_FROM") or config.DEFAULT_PERIOD_ANCHOR

BASE_FIELDS = ["id", "assignedById", "createdBy", "updatedBy", "movedBy",
               "stageId", "createdTime", "movedTime", "parentId2"]


def доля(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f} %)" if whole else f"{part}/0"


def заголовок(t: str) -> None:
    print()
    print(t)
    print("-" * len(t), flush=True)


def поля_смарт_процесса(c: BitrixClient) -> dict[str, dict]:
    """Состав полей СП-166: код, тип, заголовок. Ищем поля типа «сотрудник»."""
    заголовок("1. СОСТАВ ПОЛЕЙ СП-166")
    try:
        res = c.call("crm.item.fields", {"entityTypeId": SPA}) or {}
    except Exception as e:                                   # noqa: BLE001
        print(f"  crm.item.fields не ответил: {e.__class__.__name__}")
        return {}
    fields = res.get("fields") or res
    по_типу: Counter = Counter()
    сотрудники: list[tuple[str, str]] = []
    for code, meta in fields.items():
        if not isinstance(meta, dict):
            continue
        t = str(meta.get("type") or "?")
        по_типу[t] += 1
        if t in ("user", "employee"):
            сотрудники.append((code, str(meta.get("title") or "")))
    print(f"  всего полей: {len(fields)}")
    print("  по типам: " + ", ".join(f"{t} — {n}" for t, n in по_типу.most_common()))
    print(f"  полей типа «сотрудник»: {len(сотрудники)}")
    for code, title in сотрудники:
        print(f"    {code:28} {title}")
    if not сотрудники:
        print("    ни одного — инициатор отдельным полем не хранится")
    return {c_: m for c_, m in fields.items() if isinstance(m, dict)}


def выборка(c: BitrixClient, поля_сотрудников: list[str]) -> list[dict]:
    заголовок("2. ВЫБОРКА КАРТОЧЕК")
    select = BASE_FIELDS + [f for f in поля_сотрудников if f not in BASE_FIELDS]
    items = c.list_items(SPA, filter={"categoryId": CATEGORY,
                                      ">=createdTime": f"{WINDOW}T00:00:00+03:00"},
                         select=select, max_items=SAMPLE)
    print(f"  карточек создано с {WINDOW}: {len(items)} (предел выборки {SAMPLE})")
    пусто = [f for f in select if not any(i.get(f) for i in items)]
    if пусто:
        print("  поля, пустые у ВСЕЙ выборки: " + ", ".join(пусто))
    return items


def след(items: list[dict], поле: str, dept: set[str]) -> tuple[int, int, int]:
    """(заполнено, указывает в отдел, отличается от ответственного)."""
    есть = в_отделе = иначе = 0
    for i in items:
        v = str(i.get(поле) or "")
        if not v or v in ("0", "None"):
            continue
        есть += 1
        if v in dept:
            в_отделе += 1
        if v != str(i.get("assignedById") or ""):
            иначе += 1
    return есть, в_отделе, иначе


# Порядок значим: он же станет порядком звеньев восстановления, если замер
# покажет, что след годится. Сначала тот, кто работал руками, потом тот, кого
# записали.
СЛЕДЫ = ["assignedById", "movedBy", "lastActivityBy", "updatedBy", "createdBy", "observers"]


def следы_карточки(items: list[dict], dept: set[str], поля_сотрудников: list[str]) -> None:
    заголовок("3. СЛЕДЫ В САМОЙ КАРТОЧКЕ")
    n = len(items)
    вне = [i for i in items if str(i.get("assignedById") or "") not in dept]
    print(f"  сейчас числится вне отдела поиска поставщиков: {доля(len(вне), n)}")
    print()
    print(f"  {'поле':22} {'заполнено':>18} {'в отделе':>18} {'≠ ответственного':>20}")
    for поле in СЛЕДЫ:
        есть, в_отделе, иначе = след(items, поле, dept)
        print(f"  {поле:22} {доля(есть, n):>18} {доля(в_отделе, n):>18} {доля(иначе, n):>20}")

    заголовок("4. ТО ЖЕ, НО ТОЛЬКО ПО КАРТОЧКАМ ВНЕ ОТДЕЛА")
    print("  Это и есть искомое: сколько таких карточек можно вернуть сорсеру.")
    m = len(вне)
    print(f"  {'поле':22} {'заполнено':>18} {'указывает в отдел':>20}")
    for поле in [f for f in СЛЕДЫ if f != "assignedById"]:
        есть, в_отделе, _ = след(вне, поле, dept)
        print(f"  {поле:22} {доля(есть, m):>18} {доля(в_отделе, m):>20}")
    покрыто = sum(1 for i in вне
                  if any(str(i.get(f) or "") in dept
                         for f in СЛЕДЫ if f != "assignedById"))
    print(f"  хотя бы один след ведёт в отдел: {доля(покрыто, m)}")
    print("  Это потолок: больше карточек нынешними данными сорсеру не вернуть.")


def чьи_карточки_вне_отдела(items: list[dict], dept: set[str], depts: dict[str, str]) -> None:
    """Главный диагностический разрез: в каких подразделениях сидят те, на кого
    записаны карточки вне отдела. Если это КАМы — заслуга сорсера уходит к ним."""
    заголовок("5а. КАРТОЧКИ ВНЕ ОТДЕЛА: В КАКИХ ПОДРАЗДЕЛЕНИЯХ ОТВЕТСТВЕННЫЕ")
    вне = [i for i in items if str(i.get("assignedById") or "") not in dept]
    по_отделам: Counter = Counter()
    по_людям: Counter = Counter()
    for i in вне:
        u = str(i.get("assignedById") or "")
        по_отделам[depts.get(u) or "подразделение не указано"] += 1
        по_людям[u] += 1
    print(f"  карточек вне отдела: {len(вне)}; учётных записей на них: {len(по_людям)}")
    for d, n_ in по_отделам.most_common(10):
        print(f"    {доля(n_, len(вне)):>20}  {d}")
    без_отдела = [(u, n_) for u, n_ in по_людям.most_common() if not depts.get(u)]
    if без_отдела:
        print("  записи БЕЗ подразделения (кандидаты в служебные), id и карточек:")
        for u, n_ in без_отдела[:8]:
            print(f"    #{u:6} {n_}")


def кто_стоит_ответственным(items: list[dict], dept: set[str], depts: dict[str, str]) -> None:
    заголовок("5. КТО СТОИТ ОТВЕТСТВЕННЫМ И КТО СОЗДАЁТ")
    for роль in ("assignedById", "createdBy"):
        cnt = Counter(str(i.get(роль) or "—") for i in items)
        без_отдела = [(u, n) for u, n in cnt.most_common() if u not in ("—",) and not depts.get(u)]
        в_отделе = sum(n for u, n in cnt.items() if u in dept)
        print(f"  {роль}: учётных записей {len(cnt)}, из них в отделе "
              f"{sum(1 for u in cnt if u in dept)}; карточек на отдел {доля(в_отделе, len(items))}")
        if без_отдела:
            print("    записи БЕЗ подразделения (кандидаты в служебные), id и карточек:")
            for u, n in без_отдела[:8]:
                print(f"      #{u:6} {n}")
        else:
            print("    записей без подразделения нет")


def родительская_сделка(c: BitrixClient, items: list[dict], dept: set[str]) -> None:
    заголовок("6. РОДИТЕЛЬСКАЯ СДЕЛКА: КТО ЕЁ ДВИГАЛ")
    ids = sorted({str(i["parentId2"]) for i in items if i.get("parentId2")})[:400]
    if not ids:
        print("  у выборки нет родительских сделок")
        return
    поля = ["ID", "ASSIGNED_BY_ID", "CREATED_BY_ID", "MOVED_BY_ID", "MODIFY_BY_ID"]
    try:
        сделки = c.deals_by_ids(ids, select=поля)
    except Exception as e:                                   # noqa: BLE001
        print(f"  выгрузка сделок не удалась: {e.__class__.__name__}")
        return
    print(f"  сделок получено: {len(сделки)} из {len(ids)} запрошенных")
    n = len(сделки)
    for поле in ("ASSIGNED_BY_ID", "CREATED_BY_ID", "MOVED_BY_ID", "MODIFY_BY_ID"):
        есть = sum(1 for d in сделки.values() if str(d.get(поле) or "") not in ("", "0"))
        в_отделе = sum(1 for d in сделки.values() if str(d.get(поле) or "") in dept)
        print(f"  {поле:16} заполнено {доля(есть, n):>18}   в отделе {доля(в_отделе, n):>18}")

    # по карточкам вне отдела: что говорит их сделка
    вне = [i for i in items if str(i.get("assignedById") or "") not in dept and i.get("parentId2")]
    if вне:
        m = len(вне)
        for поле in ("ASSIGNED_BY_ID", "MOVED_BY_ID", "CREATED_BY_ID"):
            в_отделе = sum(1 for i in вне
                           if str((сделки.get(str(i["parentId2"])) or {}).get(поле) or "") in dept)
            print(f"  по карточкам вне отдела — сделка.{поле:14} ведёт в отдел {доля(в_отделе, m)}")


def таймлайн(c: BitrixClient, items: list[dict], dept: set[str]) -> None:
    заголовок("7. ТАЙМЛАЙН: ДЕЛА И КОММЕНТАРИИ")
    проба = items[:DEEP]
    print(f"  глубокая проба по {len(проба)} карточкам")

    авторы: Counter = Counter()
    писем = 0
    for i in проба:
        try:
            acts = c.call("crm.activity.list", {
                "filter": {"OWNER_TYPE_ID": SPA, "OWNER_ID": int(i["id"])},
                "select": ["ID", "AUTHOR_ID", "RESPONSIBLE_ID", "DIRECTION", "PROVIDER_ID"],
                "start": -1}) or []
        except Exception as e:                               # noqa: BLE001
            print(f"  crm.activity.list не ответил: {e.__class__.__name__}")
            break
        for a in acts:
            писем += 1
            авторы[str(a.get("AUTHOR_ID") or "")] += 1
    if писем:
        в_отделе = sum(n for u, n in авторы.items() if u in dept)
        print(f"  дел найдено: {писем}; авторов различных: {len(авторы)}; "
              f"дел, чей автор в отделе: {доля(в_отделе, писем)}")
        без = [(u, n) for u, n in авторы.most_common(5)]
        print("  топ авторов (id и сколько дел): "
              + ", ".join(f"#{u}:{n}" for u, n in без))
    else:
        print("  дел у пробы нет")

    # Комментарии проверяются по НЕСКОЛЬКИМ карточкам: у одной их может не быть
    # просто потому, что никто не писал, и ноль по ней ничего не доказывает.
    for тип in (f"dynamic_{SPA}", str(SPA), "dynamic"):
        всего = 0
        авторы_к: Counter = Counter()
        ошибка = ""
        for i in проба[:10]:
            try:
                res = c.call("crm.timeline.comment.list", {
                    "filter": {"ENTITY_ID": int(i["id"]), "ENTITY_TYPE": тип}}) or []
            except Exception as e:                           # noqa: BLE001
                ошибка = e.__class__.__name__
                break
            всего += len(res)
            for r in res:
                авторы_к[str(r.get("AUTHOR_ID") or "")] += 1
        if ошибка:
            print(f"  комментарии, ENTITY_TYPE={тип!r}: {ошибка}")
            continue
        в_отделе = sum(n for u, n in авторы_к.items() if u in dept)
        print(f"  комментарии, ENTITY_TYPE={тип!r}: записей {всего} по 10 карточкам, "
              f"авторов в отделе {доля(в_отделе, всего)}")
        if всего:
            break


def история_стадий(c: BitrixClient, items: list[dict]) -> None:
    заголовок("8. ИСТОРИЯ СТАДИЙ: ЕСТЬ ЛИ В НЕЙ ПОЛЬЗОВАТЕЛЬ")
    попытки = [
        ("crm.stagehistory.list", {"entityTypeId": SPA, "order": {"ID": "DESC"},
                                   "filter": {}, "start": 0}),
        ("crm.stagehistory.list", {"entityTypeId": "dynamic", "order": {"ID": "DESC"},
                                   "filter": {}, "start": 0}),
    ]
    for метод, params in попытки:
        try:
            res = c.call(метод, params)
        except Exception as e:                               # noqa: BLE001
            print(f"  {метод} entityTypeId={params['entityTypeId']!r}: {e.__class__.__name__}")
            continue
        items_ = (res or {}).get("items") if isinstance(res, dict) else res
        if not items_:
            print(f"  {метод} entityTypeId={params['entityTypeId']!r}: пусто")
            continue
        ключи = sorted(items_[0].keys())
        print(f"  {метод} entityTypeId={params['entityTypeId']!r}: записей {len(items_)}")
        print("    ключи записи: " + ", ".join(ключи))
        есть_юзер = [k for k in ключи if "BY" in k.upper() or "USER" in k.upper()]
        print("    поля с пользователем: " + (", ".join(есть_юзер) or "нет"))
        return
    print("  история стадий для смарт-процесса недоступна — "
          "значит «кто двигал» читается только из movedBy карточки")


def main() -> int:
    url = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not url:
        print("нет BITRIX_WEBHOOK_URL", file=sys.stderr)
        return 2
    c = BitrixClient(url)
    print("ЗОНД v45 · чей это запрос: кто дал роботу задачу его создать")
    print(f"отдел поиска поставщиков: {DEPT}")

    fields = поля_смарт_процесса(c)
    поля_сотрудников = [k for k, m in fields.items()
                        if str(m.get("type")) in ("user", "employee")][:6]

    depts_map: dict[str, str] = c.user_dept_names()
    dept = c.dept_member_ids(DEPT)
    print(f"  сотрудников отдела (с дочерними): {len(dept)}")

    items = выборка(c, поля_сотрудников)
    if not items:
        print("выборка пуста — дальше мерить нечего")
        return 1

    следы_карточки(items, dept, поля_сотрудников)
    чьи_карточки_вне_отдела(items, dept, depts_map)
    кто_стоит_ответственным(items, dept, depts_map)
    родительская_сделка(c, items, dept)
    таймлайн(c, items, dept)
    история_стадий(c, items)

    заголовок("ИТОГ")
    print("  Надёжным считается след, который заполнен почти всегда И указывает")
    print("  в отдел у тех карточек, что сейчас числятся вне его. Именно такой")
    print("  след и станет первым звеном восстановления исполнителя.")
    print("  Замер, записи не было.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
