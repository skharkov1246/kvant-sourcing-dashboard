"""Подтверждение готовности заказа к передаче в ОРК — шаг до приёмки, которого нет в регламенте ОРК.

Владелец 01.11.2026: «нашла не учтённый шаг (его нет в регламенте), добавили в конце октября.
Чтобы в реализацию не переводили не готовые по документам заказы (поставщик не прошёл проверку,
оффер старый, поставщик сменился или выдали без оффера), добавили задачу — подтверждение передачи
заказа в работу ОРК (тут заказы могут месяц висеть). Нужно поправить».

ЗОНД v65 (scripts/probe_handover.py, прогон 41256131614, 01.11.2026). Задачу «Подтверждение
готовности передачи поставщика в ОРК по…» ставит робот при создании заказа (медиана 0 ч, с недели
12.10.2026; 55 задач с 01.09, одна на заказ, 53 привязаны к заказу СП-172 через UF_CRM_TASK
«Tac_<id>»). Поля с номером задачи в заказе нет — задачи читаются по названию. Исполнитель — тот,
кто передаёт заказ (22 человека: поиск поставщиков, КАМ, продуктовые направления, ОРК); крайний срок
— 1 р.д. от постановки (медиана и P80). Закрыто 45 (39 — за 1 р.д.), открыто 10, просрочено 8, самая
старая — 21 р.д. Назначение менеджера ОРК — в день закрытия или позже у 24, раньше закрытия — у 3.

ПРАВИЛО (ork.py, contracts.py, lifecycle.py, orkview.py):
  • задача открыта и заказ на шаге 1 «Приёмка сделки» (стадия «Заказ создан», менеджер ОРК не
    назначен) — приёмка ещё не началась: срок руководителя ОРК не идёт, «дольше нормы» нет; заказ
    ждёт подтверждения готовности (ork.judged.waitHo), хозяин — исполнитель задачи; крайний срок
    задачи прошёл — пункт очереди hoOver «подтвердить готовность заказа к передаче в ОРК»,
    руководитель ОРК в копии;
  • задача закрыта позже начала приёмки t0 — приёмка (шаг 1, S1) идёт от закрытия задачи;
  • назначение менеджера ОРК раньше закрытия задачи (ОРК взял заказ до подтверждения) — в счёт, как
    раньше: приёмка кончилась назначением (S1 — 0 р.д.).
Задачи не прочитаны (нет права, сбой) — всё как до 01.11.2026, счётом в журнале сборки.

В журнал — только счётчики (правило 17): ни номеров, ни имён.
"""
from __future__ import annotations

import datetime as dt
import re

TITLE = "Подтверждение готовности передачи поставщика в ОРК"
SINCE = "2026-09-01"                    # первая задача — 28.09.2026 (зонд v65)
SPA_ORDER = 172
BIND = "T" + format(SPA_ORDER, "x") + "_"      # UF_CRM_TASK заказа СП-172: «Tac_<id>»
DONE = {"5"}                            # Битрикс: 5 — завершена (4 — ждёт контроля: не закрыта)
WAIT = "ждём подтверждения готовности к передаче в ОРК"
WHY = "приёмка не началась: " + WAIT
ROLE = "исполнитель задачи «Подтверждение готовности»"
SELECT = ["ID", "STATUS", "CREATED_DATE", "CLOSED_DATE", "DEADLINE", "RESPONSIBLE_ID", "UF_CRM_TASK"]


def _g(t: dict, *keys):
    for k in keys:
        v = t.get(k)
        if v not in (None, ""):
            return v
    return None


def _ts(v) -> str:
    """Время Битрикса → ISO с поясом (без пояса — МСК); пусто или не время — ''."""
    s = str(v or "").strip()
    if not s:
        return ""
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone(dt.timedelta(hours=3)))
    return t.isoformat()


def parse(tasks: list[dict]) -> dict[str, dict]:
    """Задачи tasks.task.list → {id заказа СП-172: {id, open, created, closed, deadline, resp}}.
    На заказ — последняя по постановке задача (повтор после переоткрытия); без привязки к заказу —
    мимо. Ключи задачи — и camelCase (tasks.task.list), и ВЕРХНИЙ регистр."""
    out: dict[str, dict] = {}
    for t in tasks or []:
        binds = _g(t, "ufCrmTask", "UF_CRM_TASK") or []
        binds = binds if isinstance(binds, list) else [binds]
        created = _ts(_g(t, "createdDate", "CREATED_DATE"))
        rec = {"id": str(_g(t, "id", "ID") or ""),
               "open": str(_g(t, "status", "STATUS") or "") not in DONE,
               "created": created, "closed": _ts(_g(t, "closedDate", "CLOSED_DATE")),
               "deadline": _ts(_g(t, "deadline", "DEADLINE")),
               "resp": str(_g(t, "responsibleId", "RESPONSIBLE_ID") or "")}
        if not rec["open"] and not rec["closed"]:
            rec["open"] = True                     # «завершена» без даты закрытия — как открытая
        for b in binds:
            m = re.match(rf"^{BIND}(\d+)$", str(b).strip())
            if not m:
                continue
            oid = m.group(1)
            if oid not in out or created > out[oid]["created"]:
                out[oid] = rec
    return out


def read(client, *, since: str = SINCE) -> tuple[dict[str, dict], str]:
    """Задачи подтверждения готовности с портала → (parse, состояние): «ок» или код отказа.
    Один отбор по названию и дате постановки (≈ 2 страницы на 100 задач); отказ — пустой
    словарь, вкладка считает как до 01.11.2026."""
    tasks: list[dict] = []
    start = 0
    try:
        while True:
            env = client.call_envelope("tasks.task.list", {
                "filter": {"%TITLE": TITLE, ">=CREATED_DATE": since}, "select": SELECT,
                "order": {"ID": "ASC"}, "start": start}) or {}
            res = env.get("result") or {}
            ch = (res.get("tasks") if isinstance(res, dict) else res) or []
            tasks += ch
            nxt = env.get("next")
            if not nxt or not ch or len(tasks) > 5000:
                break
            start = nxt
    except Exception as e:                          # noqa: BLE001 — отказ портала — как без задач
        m = re.search(r"\b([A-Z][A-Z_]{3,40})\b", str(e))
        return {}, m.group(1) if m else type(e).__name__
    return parse(tasks), "ок"
