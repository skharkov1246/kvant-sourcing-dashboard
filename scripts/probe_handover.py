#!/usr/bin/env python3
"""Зонд v65 «Подтверждение готовности к передаче в ОРК»: задача, которой нет в регламенте ОРК.

ПОВОД (01.11.2026, владелец). В конце октября добавили задачу «Подтверждение готовности передачи
поставщику…»: пока она открыта, заказ не готов по документам (поставщик не прошёл проверку, оффер
старый, поставщик сменился, выдали без оффера), и в работу ОРК его не переводят — «тут заказы могут
месяц висеть». Вкладка «Реализация» считает такой заказ шагом 1 «Приёмка сделки» руководителя ОРК
с начала приёмки t0 и ставит «дольше нормы» (пример владельца: 5 р.д. при норме 1).

ЧТО МЕРИТ (только счётчики, доли и распределения — правило 17; ни названий, ни имён, ни номеров):
  1. поля СП-172, заведённые с 01.10.2026 (по метке времени в коде поля), и поля со словами
     «задач / подтвержд / готовн / передач» — код, тип, подпись (подписи — настройка портала);
  2. задачи с «Подтверждение готовности» в названии с 01.09.2026: число, неизменная часть названия
     (только встретившаяся ≥ 3 раз — шаблон, а не текст с именем), статусы, кто ставит;
  3. к чему привязаны (UF_CRM_TASK): заказ СП-172, сделка, иное; одна ли задача на заказ;
  4. исполнитель — по отделу (ОРК, поиск поставщиков, КАМ, иное), сколько разных людей;
  5. крайний срок от постановки (р.д.); открытые — возраст (р.д.) и просроченные; закрытые — срок
     закрытия (р.д.), медиана, P80, максимум;
  6. заказы с задачей: от создания заказа до задачи (ч), стадия сейчас у открытых, назначение
     менеджера ОРК («Дата записи нового ответственного») раньше или позже закрытия задачи;
  7. охват: заказы, созданные с первой задачи, — у скольких есть задача; поле заказа с номером
     задачи (если нашлось в п. 1) — заполнено ли и совпадает ли с привязкой.

НАГРУЗКА: поля 1 + отделы и люди ≈ 15 + задачи ≈ 5–10 страниц + заказы ≈ 10 — всего ≈ 50 запросов
при 1/с; предел 150.
"""
from __future__ import annotations

import collections
import datetime as dt
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPA = 172
С_ПОЛЕЙ = dt.date(2026, 10, 1)
С_ЗАДАЧ = dt.date(2026, 9, 1)
НАЗВАНИЕ = "Подтверждение готовности"
СЛОВА_ПОЛЯ = re.compile(r"задач|task|подтвержд|готовн|передач|оффер|offer|провер", re.I)
ASSIGN_AT = "ufCrm20_1787924508120"          # «Дата записи нового ответственного» (contracts.ASSIGN_AT)
ПРЕДЕЛ = 150
ОТДЕЛЫ = (("ОРК", re.compile(r"реализаци\w* контракт|\bОРК\b", re.I)),
          ("поиск поставщиков", re.compile(r"поиска поставщик|сорс|закуп|снабж", re.I)),
          ("КАМ", re.compile(r"группа по работе с|ключев|работе с заказчик|спецпроект|шельф|продаж", re.I)),
          ("сопровождение", re.compile(r"сопровожден", re.I)))
ЗАКРЫТА = {"5"}                               # Битрикс: 5 — завершена; 4 — ждёт контроля
СТАТУС = {"1": "новая", "2": "ждёт выполнения", "3": "выполняется", "4": "ждёт контроля",
          "5": "завершена", "6": "отложена", "7": "отклонена"}


def код(e: Exception) -> str:
    m = re.search(r"\b([A-Z][A-Z_]{3,40})\b", str(e))
    return m.group(1) if m else type(e).__name__


def метка_поля(code: str) -> dt.date | None:
    """Дата заведения поля по коду ufCrm20_<секунды или миллисекунды> (UTC)."""
    m = re.match(r"ufCrm\d+_(\d{10,13})$", code or "")
    if not m:
        return None
    n = int(m.group(1))
    n = n // 1000 if n > 10 ** 11 else n
    try:
        return dt.datetime.fromtimestamp(n, dt.timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


def вр(v) -> dt.datetime | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone(dt.timedelta(hours=3)))


def шаблон(title: str) -> str:
    """Неизменная часть названия: до первой цифры, кавычки, «№» или двоеточия, не больше 7 слов."""
    s = re.split(r"[\d\"«»№:(]", str(title or ""), maxsplit=1)[0]
    return " ".join(s.split()[:7]).strip(" -–—.,")


def привязка(v) -> list[tuple[str, str]]:
    """UF_CRM_TASK → [(вид, номер)]: «T<hex>_<id>» — смарт-процесс, «D_<id>» — сделка."""
    out = []
    for x in v if isinstance(v, list) else ([v] if v else []):
        m = re.match(r"^(T[0-9a-f]+|[A-Z]{1,3})_(\d+)$", str(x).strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def распр(xs: list[float], ед: str) -> str:
    if not xs:
        return "нет данных"
    s = sorted(xs)
    p80 = s[min(len(s) - 1, int(round(0.8 * (len(s) - 1))))]
    return f"n {len(s)} · медиана {statistics.median(s):g} {ед} · P80 {p80:g} · макс {s[-1]:g}"


def корзины(xs: list[int], edges=((None, 1, "≤ 1"), (1, 5, "2–5"), (5, 10, "6–10"), (10, 20, "11–20"),
                                  (20, None, "> 20"))) -> str:
    return " · ".join(f"{lbl} — {sum(1 for x in xs if (lo is None or x > lo) and (hi is None or x <= hi))}"
                      for lo, hi, lbl in edges)


def отдел_по(uid: str, users: dict, deps: dict) -> str:
    """Отдел исполнителя крупно (ОРК, поиск поставщиков, КАМ, сопровождение, иное) по справочнику
    people.roster ({uid: {depts}}) и именам отделов; ушедший — тем же отделом."""
    if uid not in users:
        return "не найден"
    names = [deps.get(str(x), "") for x in users[uid].get("depts") or []]
    for lbl, rx in ОТДЕЛЫ:
        if any(rx.search(n) for n in names):
            return lbl
    return "иное"


def main() -> int:
    import config
    import period as period_mod
    from bitrix_client import BitrixClient, сводка_нагрузки

    bx = BitrixClient(config.Settings.load().bitrix_webhook_url)
    msk = dt.timezone(dt.timedelta(hours=3))
    сегодня = dt.datetime.now(msk).date()
    hexed = "T" + format(SPA, "x")
    print(f"ожидается: поля СП-{SPA}, задачи «{НАЗВАНИЕ}…» с {С_ЗАДАЧ:%d.%m.%Y}, их заказы; ориентир ≈ 50 запросов, "
          f"предел {ПРЕДЕЛ}")

    # 1. поля
    print(f"\n1. ПОЛЯ СП-{SPA} (заведены с {С_ПОЛЕЙ:%d.%m.%Y} или со словами задача/подтверждение/готовность/передача)")
    поле_задачи = ""
    try:
        f = bx.call("crm.item.fields", {"entityTypeId": SPA}) or {}
        f = f.get("fields", f) if isinstance(f, dict) else {}
        for code, meta in sorted(f.items()):
            if not str(code).startswith("ufCrm"):
                continue
            title = str(meta.get("title") or meta.get("listLabel") or meta.get("formLabel") or "")
            d = метка_поля(code)
            if (d and d >= С_ПОЛЕЙ) or СЛОВА_ПОЛЯ.search(title):
                print(f"   {code:28s} {str(meta.get('type') or ''):14s} {d or '—'}  «{title}»")
                if re.search(r"подтвержд|готовн|передач", title, re.I) and re.search(r"задач|task|id", title, re.I):
                    поле_задачи = code
    except Exception as e:                                          # noqa: BLE001
        print(f"   поля не прочитаны: {код(e)}")
    print(f"   поле с номером задачи подтверждения: {поле_задачи or 'не найдено по подписи'}")

    # 2. задачи
    задачи: list[dict] = []
    start = 0
    while True:
        try:
            env = bx.call_envelope("tasks.task.list", {
                "filter": {"%TITLE": НАЗВАНИЕ, ">=CREATED_DATE": С_ЗАДАЧ.isoformat()},
                "select": ["ID", "TITLE", "STATUS", "CREATED_DATE", "CLOSED_DATE", "DEADLINE", "RESPONSIBLE_ID",
                           "CREATED_BY", "UF_CRM_TASK", "CHANGED_DATE"],
                "order": {"ID": "ASC"}, "start": start}) or {}
        except Exception as e:                                      # noqa: BLE001
            print(f"\n2. ЗАДАЧИ: не прочитаны ({код(e)})")
            break
        res = env.get("result") or {}
        ch = (res.get("tasks") if isinstance(res, dict) else res) or []
        задачи += ch
        nxt = env.get("next")
        if not nxt or not ch or len(задачи) > 2000:
            break
        start = nxt
    g = lambda t, *ks: next((t.get(k) for k in ks if t.get(k) not in (None, "")), None)   # noqa: E731
    print(f"\n2. ЗАДАЧИ «{НАЗВАНИЕ}…» с {С_ЗАДАЧ:%d.%m.%Y}: {len(задачи)}")
    if not задачи:
        print("\n" + сводка_нагрузки())
        return 0
    шаблоны = collections.Counter(шаблон(g(t, "title", "TITLE")) for t in задачи)
    for s, n in шаблоны.most_common(6):
        print(f"   название «{s}…» — {n}" if n >= 3 else f"   прочие названия (встречены < 3 раз) — {n}")
    по_неделям = collections.Counter()
    for t in задачи:
        c = вр(g(t, "createdDate", "CREATED_DATE"))
        if c:
            по_неделям[(c.astimezone(msk).date() - dt.timedelta(days=c.astimezone(msk).weekday())).isoformat()] += 1
    print("   по неделям постановки: " + " · ".join(f"{k[8:10]}.{k[5:7]} — {v}" for k, v in sorted(по_неделям.items())))
    st = collections.Counter(СТАТУС.get(str(g(t, "status", "STATUS")), str(g(t, "status", "STATUS"))) for t in задачи)
    print("   статусы: " + " · ".join(f"{k} {v}" for k, v in st.most_common()))
    авторы = collections.Counter(str(g(t, "createdBy", "CREATED_BY")) for t in задачи)
    исп = collections.Counter(str(g(t, "responsibleId", "RESPONSIBLE_ID")) for t in задачи)
    print(f"   ставят: разных авторов {len(авторы)}, у самого частого {авторы.most_common(1)[0][1]} задач; "
          f"исполнителей разных {len(исп)}, у самого частого {исп.most_common(1)[0][1]}")

    # 3. привязка
    вид = collections.Counter()
    заказ_задачи: dict[str, list[dict]] = collections.defaultdict(list)
    for t in задачи:
        b = привязка(g(t, "ufCrmTask", "UF_CRM_TASK"))
        вид[", ".join(sorted({("заказ СП-172" if k == hexed else ("сделка" if k == "D" else k)) for k, _ in b}))
            or "без привязки"] += 1
        for k, oid in b:
            if k == hexed:
                заказ_задачи[oid].append(t)
    print("\n3. ПРИВЯЗКА (UF_CRM_TASK): " + " · ".join(f"{k} — {v}" for k, v in вид.most_common()))
    print(f"   заказов с задачей {len(заказ_задачи)}; задач на заказ: "
          + " · ".join(f"{k} — {v}" for k, v in sorted(collections.Counter(min(len(v), 3) for v in заказ_задачи.values()).items())))

    # 4. исполнитель по отделу
    try:
        import people as people_mod
        deps = {str(d["ID"]): str(d.get("NAME") or "") for d in bx.departments()}
        users = people_mod.roster(bx)

        def отдел(uid: str) -> str:
            return отдел_по(uid, users, deps)
        по_отделу = collections.Counter()
        for uid, n in исп.items():
            по_отделу[отдел(uid)] += n
        print("\n4. ИСПОЛНИТЕЛЬ ПО ОТДЕЛУ (задач): " + " · ".join(f"{k} — {v}" for k, v in по_отделу.most_common())
              + f"; людей по отделам: " + " · ".join(
                  f"{k} {v}" for k, v in collections.Counter(отдел(u) for u in исп).most_common()))
        print(f"   автор = исполнитель у {sum(1 for t in задачи if str(g(t, 'createdBy', 'CREATED_BY')) == str(g(t, 'responsibleId', 'RESPONSIBLE_ID')))} задач")
    except Exception as e:                                          # noqa: BLE001
        print(f"\n4. исполнитель по отделу: не прочитан ({код(e)})")

    # 5. сроки
    wd = period_mod.workdays_ru
    срок_пост, возраст, закрыто_за, просрочено = [], [], [], 0
    for t in задачи:
        c, dl = вр(g(t, "createdDate", "CREATED_DATE")), вр(g(t, "deadline", "DEADLINE"))
        cl = вр(g(t, "closedDate", "CLOSED_DATE"))
        закрыта = str(g(t, "status", "STATUS")) in ЗАКРЫТА
        if c and dl:
            x = wd(c, dl)
            if x is not None:
                срок_пост.append(x)
        if c and закрыта and cl:
            x = wd(c, cl)
            if x is not None:
                закрыто_за.append(x)
        elif c and not закрыта:
            x = wd(c, сегодня)
            if x is not None:
                возраст.append(x)
            if dl and dl.astimezone(msk).date() < сегодня:
                просрочено += 1
    print(f"\n5. СРОКИ (р.д. по календарю РФ)")
    print(f"   крайний срок от постановки: {распр(срок_пост, 'р.д.')}; задач без крайнего срока "
          f"{len(задачи) - len(срок_пост)}")
    print(f"   открытые: {распр(возраст, 'р.д.')}; по возрасту: {корзины(возраст)}; просрочено {просрочено}")
    print(f"   закрытые: от постановки до закрытия {распр(закрыто_за, 'р.д.')}; по сроку: {корзины(закрыто_за)}")

    # 6. заказы с задачей
    заказы: dict[str, dict] = {}
    ids = sorted(заказ_задачи, key=int)
    sel = ["id", "stageId", "createdTime", "assignedById", "parentId2", ASSIGN_AT] + ([поле_задачи] if поле_задачи else [])
    for i in range(0, len(ids), 50):
        try:
            r = bx.call("crm.item.list", {"entityTypeId": SPA, "filter": {"@id": [int(x) for x in ids[i:i + 50]]},
                                          "select": sel, "start": -1}) or {}
            for o in (r.get("items") or []) if isinstance(r, dict) else []:
                заказы[str(o["id"])] = o
        except Exception as e:                                      # noqa: BLE001
            print(f"   ⚠ заказы не прочитаны: {код(e)}")
    имена: dict[str, str] = {}
    for cid in sorted({m.group(1) for o in заказы.values() if (m := re.match(rf"DT{SPA}_(\d+):", str(o.get("stageId") or "")))}):
        try:
            имена.update(bx.spa_stages(SPA, int(cid)))
        except Exception:                                           # noqa: BLE001
            pass
    лаг, стадии_откр, назн = [], collections.Counter(), collections.Counter()
    for oid, ts in заказ_задачи.items():
        o = заказы.get(oid)
        if not o:
            назн["заказ не прочитан"] += 1
            continue
        t = min(ts, key=lambda t: str(g(t, "createdDate", "CREATED_DATE") or ""))
        oc, tc = вр(o.get("createdTime")), вр(g(t, "createdDate", "CREATED_DATE"))
        if oc and tc:
            лаг.append(round((tc - oc).total_seconds() / 3600, 1))
        закрыта = str(g(t, "status", "STATUS")) in ЗАКРЫТА
        if not закрыта:
            стадии_откр[имена.get(str(o.get("stageId")), "стадия не в справочнике")] += 1
        a = вр(o.get(ASSIGN_AT))
        cl = вр(g(t, "closedDate", "CLOSED_DATE"))
        if not a:
            назн["менеджер ОРК не назначен" + (" · задача открыта" if not закрыта else " · задача закрыта")] += 1
        elif not закрыта:
            назн["назначен, задача ещё открыта"] += 1
        elif cl and a.astimezone(msk).date() < cl.astimezone(msk).date():
            назн["назначен раньше закрытия задачи"] += 1
        else:
            назн["назначен в день закрытия или позже"] += 1
    print(f"\n6. ЗАКАЗЫ С ЗАДАЧЕЙ: прочитано {len(заказы)} из {len(заказ_задачи)}")
    print(f"   от создания заказа до задачи: {распр(лаг, 'ч')}")
    print("   стадия сейчас у заказов с открытой задачей: " + (" · ".join(f"«{k}» — {v}" for k, v in стадии_откр.most_common(8)) or "—"))
    print("   назначение менеджера ОРК против задачи: " + " · ".join(f"{k} — {v}" for k, v in назн.most_common()))

    # 7. охват: заказы с первой задачи
    первая = min((вр(g(t, "createdDate", "CREATED_DATE")) for t in задачи if вр(g(t, "createdDate", "CREATED_DATE"))), default=None)
    if первая:
        try:
            новые = bx.list_items(SPA, filter={">=createdTime": первая.astimezone(msk).date().isoformat() + "T00:00:00"},
                                  select=["id", "createdTime"] + ([поле_задачи] if поле_задачи else []))
            с_задачей = [o for o in новые if str(o["id"]) in заказ_задачи]
            print(f"\n7. ОХВАТ: заказов с {первая.astimezone(msk).date():%d.%m.%Y} — {len(новые)}, с задачей {len(с_задачей)} "
                  f"({round(len(с_задачей) / max(1, len(новые)) * 100)} %)")
            if поле_задачи:
                есть = [o for o in новые if str(o.get(поле_задачи) or "").strip() not in ("", "0")]
                совп = sum(1 for o in есть if any(str(g(t, "id", "ID")) == str(o.get(поле_задачи)).strip()
                                                  for t in заказ_задачи.get(str(o["id"]), [])))
                print(f"   поле {поле_задачи}: заполнено у {len(есть)}, совпадает с привязкой задачи у {совп}")
        except Exception as e:                                      # noqa: BLE001
            print(f"\n7. охват не прочитан: {код(e)}")
    print("\n" + сводка_нагрузки())
    return 0


if __name__ == "__main__":
    sys.exit(main())
