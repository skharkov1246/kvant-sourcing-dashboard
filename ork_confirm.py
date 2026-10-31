"""Шаг 0 «Подтверждение передачи в ОРК»: задачи Битрикса «Подтверждение готовности передачи…» → заказ.

Решение владельца 31.10.2026 (docs/agents/DECISIONS.md): перед передачей заказа в ОРК ставят задачу
«Подтверждение готовности передачи … в работу ОРК» (поставщик прошёл проверку, оффер свежий,
поставщик не сменился). Пока задача открыта, заказ стоит на шаге 0 (ork.PRE_STEP), приёмка ОРК
(шаг 1) начинается с её закрытия. Зонд v63 (scripts/probe_ork_confirm.py, 31.10.2026): 54 задачи с
октября, все названия начинаются с «Подтверждение готовности передачи», 53 привязаны к заказу
СП-172 (UF_CRM_TASK = «Tac_<id>»), одна задача на заказ, статусы 2 (открыта) и 5 (закрыта).

load(client) → (by_order | None, stats):
  by_order[id заказа] = {open, from, at, spans, n, resp, status}: open — открыта хоть одна задача
  заказа; from — создание текущей открытой задачи (их несколько — самой ранней), иначе последней
  закрытой; at — закрытие последней закрытой, если открытых нет, иначе ""; spans — [[создание,
  закрытие | ""], …] по времени (дни на шаге 0 для пути заказа); n — задач у заказа; resp —
  исполнитель (RESPONSIBLE_ID) текущей или последней задачи; status — её статус.
  None — задачи не прочитаны (нет права task, отказ портала, лимит, разбор): шага 0 нет ни у
  одного заказа, все числа прежние (contracts, правило 15 — «Нет данных», а не ноль).

Привязка — только к заказу (ORDER_PREFIX): задачи к сделке («D_») и без привязки идут в счёт, но
шага 0 не дают. Закрыта — только STATUS из CONFIRM_CLOSED (правило 4; решение 31.10.2026, б:
«Ждёт контроля» и прочие — открыта, пока автор не принял).

В журнал — только счёт (правило 17): ни названий, ни id, ни имён.
"""
from __future__ import annotations

import datetime as dt
import re

# --- таблицы (правило 4) --------------------------------------------------------------------
# подстрока фильтра tasks.task.list (%TITLE) — по итогу зонда v63 названия начинаются ровно с неё
PHRASES = ("Подтверждение готовности передачи",)
# задача в счёте, только если название НАЧИНАЕТСЯ с фразы: подстрока в середине («Re: …», чужая
# задача с цитатой) — не задача подтверждения
TITLE_RX = re.compile(r"^\s*подтверждение\s+готовности\s+передачи", re.I)
# задачи заведены с октября 2026 (зонд v63); окно — с 01.09, не позже 01.10 (фикстура живёт 19.10)
SINCE = "2026-09-01T00:00:00+03:00"
ORDER_ENTITY = 172
ORDER_PREFIX = f"T{ORDER_ENTITY:x}_"         # «Tac_<id>» — привязка задачи к элементу СП-172
DEAL_PREFIX = "D_"
CONFIRM_CLOSED = frozenset({"5"})          # «Завершена»; 2, 3, 4 («Ждёт контроля»), 6, 7 — открыта
MAX_PAGES = 10                             # по 50 задач: нагрузка — 1–5 запросов
SELECT = ("ID", "TITLE", "STATUS", "CREATED_DATE", "CLOSED_DATE", "RESPONSIBLE_ID", "UF_CRM_TASK")
# прочитано 0 задач при стольких живых заказах, заведённых с SINCE, — «проверить видимость задач
# вебхуку» (read = "empty"), а не «задач нет»
EMPTY_MIN = 10

MSK = dt.timezone(dt.timedelta(hours=3))


def _field(t: dict, *keys):
    """Поле задачи: tasks.task.list отдаёт camelCase (id, createdDate), снимок и старые ответы —
    UPPER (ID, CREATED_DATE); пустое — None."""
    for k in keys:
        if k in t and t[k] not in (None, ""):
            return t[k]
    return None


def _ts(v) -> dt.datetime | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=MSK)


def _iso(v) -> str:
    t = _ts(v)
    return t.isoformat() if t else ""


def _links(t: dict) -> list[str]:
    crm = _field(t, "ufCrmTask", "UF_CRM_TASK") or []
    return [str(x) for x in (crm if isinstance(crm, (list, tuple)) else [crm]) if str(x or "")]


def parse(tasks: list[dict]) -> tuple[dict[str, dict], dict]:
    """Строки tasks.task.list → (by_order, stats). Повторы по id убираются; задача без названия
    по фразе (TITLE_RX) — не в счёте задач подтверждения (skipped)."""
    seen: dict[str, dict] = {}
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        tid = str(_field(t, "id", "ID") or "")
        if tid:
            seen[tid] = t
    st = {"read": True, "raw": len(seen), "n": 0, "skipped": 0, "order": 0, "deal": 0, "none": 0,
          "other": 0, "orders": 0, "open": 0, "closed": 0, "multi": 0}
    per: dict[str, list[dict]] = {}
    for t in seen.values():
        if not TITLE_RX.match(str(_field(t, "title", "TITLE") or "")):
            st["skipped"] += 1
            continue
        st["n"] += 1
        links = _links(t)
        oids = [x[len(ORDER_PREFIX):] for x in links if x.startswith(ORDER_PREFIX)]
        if oids:
            st["order"] += 1
        elif any(x.startswith(DEAL_PREFIX) for x in links):
            st["deal"] += 1
        elif links:
            st["other"] += 1
        else:
            st["none"] += 1
        rec = {"open": str(_field(t, "status", "STATUS") or "") not in CONFIRM_CLOSED,
               "from": _iso(_field(t, "createdDate", "CREATED_DATE")),
               "at": _iso(_field(t, "closedDate", "CLOSED_DATE")),
               "resp": str(_field(t, "responsibleId", "RESPONSIBLE_ID") or ""),
               "status": str(_field(t, "status", "STATUS") or "")}
        for oid in dict.fromkeys(x for x in oids if x):
            per.setdefault(oid, []).append(rec)
    by_order: dict[str, dict] = {}
    for oid, recs in per.items():
        recs.sort(key=lambda r: (r["from"], r["at"]))
        opened = [r for r in recs if r["open"]]
        closed = sorted((r for r in recs if not r["open"]), key=lambda r: (r["at"], r["from"]))
        cur = opened[0] if opened else closed[-1]
        # дни на шаге 0: открытая — до сих пор (""), закрытая — до закрытия; закрытая без даты
        # закрытия дней не даёт (конец неизвестен — не «открыта до сих пор»)
        spans = [[r["from"], "" if r["open"] else r["at"]] for r in recs
                 if r["from"] and (r["open"] or r["at"])]
        by_order[oid] = {"open": bool(opened), "from": cur["from"],
                         "at": "" if opened else cur["at"], "spans": spans, "n": len(recs),
                         "resp": cur["resp"], "status": cur["status"]}
    st["orders"] = len(by_order)
    st["open"] = sum(1 for x in by_order.values() if x["open"])
    st["closed"] = st["orders"] - st["open"]
    st["multi"] = sum(1 for x in by_order.values() if x["n"] > 1)
    return by_order, st


def load(client) -> tuple[dict[str, dict] | None, dict]:
    """Задачи подтверждения передачи с SINCE → (by_order, stats). Любой отказ (нет права task,
    ACCESS_DENIED, лимит портала, разбор, заглушка без метода) — (None, {read: False, why: класс
    ошибки}): шага 0 нет, числа прежние."""
    try:
        rows: list[dict] = []
        for phrase in PHRASES:
            rows += client.tasks_list({"%TITLE": phrase, ">=CREATED_DATE": SINCE}, list(SELECT),
                                      max_pages=MAX_PAGES)
        return parse(rows)
    except Exception as e:  # noqa: BLE001 — отказ без последствий: шага 0 нет, числа прежние
        return None, {"read": False, "why": type(e).__name__}


def head(stats: dict | None, live_since: int = 0) -> dict:
    """contracts.head.confirm: {read: True | False | "empty", n, orders, open, why}. live_since —
    живых заказов, заведённых с SINCE: при EMPTY_MIN и больше ноль задач — «empty» (видимость
    задач вебхуку не проверена), а не «задач нет»."""
    s = stats or {"read": False, "why": "не читались"}
    if not s.get("read"):
        return {"read": False, "n": None, "orders": None, "open": None,
                "why": f"задачи подтверждения передачи не прочитаны: {s.get('why') or 'отказ портала'}"}
    if not s.get("n") and live_since >= EMPTY_MIN:
        return {"read": "empty", "n": 0, "orders": 0, "open": 0,
                "why": f"задач подтверждения передачи 0 при {live_since} живых заказах с {SINCE[:10]} — "
                       "проверить видимость задач вебхуку"}
    return {"read": True, "n": s.get("n", 0), "orders": s.get("orders", 0), "open": s.get("open", 0), "why": ""}


def summary(stats: dict | None) -> str:
    """Одна строка в журнал — только счёт (правило 17)."""
    s = stats or {}
    if not s.get("read"):
        return f"задачи подтверждения передачи в ОРК: не прочитаны ({s.get('why') or '—'}) — шага 0 нет, числа прежние"
    return (f"задачи подтверждения передачи в ОРК с {SINCE[:10]}: прочитано {s.get('raw', 0)}, по названию "
            f"{s.get('n', 0)} (не по названию {s.get('skipped', 0)}); к заказу {s.get('order', 0)}, к сделке "
            f"{s.get('deal', 0)}, к другому {s.get('other', 0)}, без привязки {s.get('none', 0)}; заказов с задачей "
            f"{s.get('orders', 0)} (открыта {s.get('open', 0)}, закрыта {s.get('closed', 0)}, с двумя и больше "
            f"задачами {s.get('multi', 0)})")
