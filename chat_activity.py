"""Активность в чате сделки (решение владельца 19.10.2026): сделка «подвисла» —
только если и в её чате тихо. Стадия может стоять неделями, пока в чате идёт работа
с поставщиком, логистом и заказчиком; без чата такая сделка ложно числилась
замедленной.

Читается только то, что уже подозревается в простое (кандидаты), — не все сделки:
один запрос на сделку (im.dialog.messages.get по «ID чата по сделке»), бюджет
MAX_CHATS на сборку, сначала самые денежные. Человек — автор с id > 0 не из
служебных записей. Итог по каждой сделке:
  active  — человек писал в чате не позже QUIET_DAYS назад: не подвисла;
  quiet   — чат прочитан, людей дольше QUIET_DAYS нет (или не было вовсе): подвисла;
  unread  — чат не прочитан (нет доступа у вебхука, ошибка): подвисшей не считаем,
            помечаем «чат не проверен»;
  skipped — не хватило бюджета чтения: так же, как unread.
"""
from __future__ import annotations

import datetime as dt

CHAT_FIELD = "UF_CRM_1734380921"   # «ID чата по сделке» (зонд v49: заполнено у 100 % сделок)
QUIET_DAYS = 7                     # человек писал за неделю — сделка в работе
MAX_CHATS = 400                    # чатов на одну сборку: ≤ 400 запросов при ≤ 2 запросах/с
LIMIT_WAIT_S = 60.0                # ожидание на лимите портала при чтении чатов, с
MESSAGES = 50                      # последних сообщений: найти живого автора и среди переписки роботов


def chat_id(deal: dict) -> str:
    v = deal.get(CHAT_FIELD)
    v = v[0] if isinstance(v, list) and v else v
    s = str(v or "").strip()
    return s if s.isdigit() and s != "0" else ""


def last_human(messages: list[dict], service_ids: set[str]) -> dt.date | None:
    """Дата последнего сообщения человека: автор > 0 и не служебная запись."""
    best = None
    for m in messages or []:
        a = str(m.get("author_id") or m.get("AUTHOR_ID") or "0")
        if not a.isdigit() or a == "0" or a in service_ids:
            continue
        s = str(m.get("date") or m.get("DATE") or "")[:10]
        try:
            d = dt.date.fromisoformat(s)
        except ValueError:
            continue
        best = d if best is None or d > best else best
    return best


def classify(last: dt.date | None, today: dt.date, readable: bool) -> str:
    if not readable:
        return "unread"
    if last is not None and (today - last).days <= QUIET_DAYS:
        return "active"
    return "quiet"


def check(client, candidates: list[tuple[str, str, float]], today: dt.date,
          service_ids: set[str] | None = None, budget: int = MAX_CHATS) -> dict[str, dict]:
    """candidates — [(id сделки, id чата, вес для порядка)] → {id сделки: {status, last, days}}.
    Сначала — самые тяжёлые (по весу); сверх бюджета — skipped."""
    service_ids = {str(s) for s in (service_ids or set())}
    out: dict[str, dict] = {}
    # чтение чатов — необязательная часть сборки: на лимите портала ждём не дольше
    # минуты (у клиента по умолчанию — до 30 мин на вызов), потом останавливаемся
    бюджет_было = getattr(client, "limit_wait_budget", None)
    if бюджет_было is not None:
        client.limit_wait_budget = min(бюджет_было, LIMIT_WAIT_S)
    try:
        return _check(client, candidates, today, service_ids, budget, out)
    finally:
        if бюджет_было is not None:
            client.limit_wait_budget = бюджет_было


def _check(client, candidates, today, service_ids, budget, out) -> dict[str, dict]:
    стоп = False                          # портал упёрся в лимит — дальше не читаем
    for i, (did, cid, _w) in enumerate(sorted(candidates, key=lambda x: -(x[2] or 0))):
        if not cid:
            out[did] = {"status": "unread", "last": "", "days": None, "why": "nochat"}
            continue
        if i >= budget or стоп:
            out[did] = {"status": "skipped", "last": "", "days": None}
            continue
        try:
            r = client.call("im.dialog.messages.get", {"DIALOG_ID": f"chat{cid}", "LIMIT": MESSAGES},
                            retries=1)
        except Exception as e:            # нет доступа у вебхука, лимит, сбой
            r = None
            if type(e).__name__ == "BitrixLimitError":
                # одиночный вызов на лимите ждёт до бюджета ожидания клиента (30 мин):
                # 400 чатов так пережили бы сборку — первый лимит останавливает чтение
                стоп = True
            elif "ACCESS" in str(e).upper():
                out[did] = {"status": "unread", "last": "", "days": None, "why": "denied"}
                continue
        # прочитан — только настоящий ответ со списком сообщений: пустой ответ не «тишина»
        readable = isinstance(r, dict) and isinstance(r.get("messages"), list)
        msgs = r["messages"] if readable else []
        last = last_human(msgs, service_ids) if readable else None   # максимум даты: порядок ответа не задан
        out[did] = {"status": classify(last, today, readable),
                    "last": last.strftime("%d.%m.%Y") if last else "",
                    "days": (today - last).days if last else None}
    return out


def summary(res: dict[str, dict]) -> dict:
    """Счётчики для журнала и вкладки: только агрегаты."""
    c = {"active": 0, "quiet": 0, "unread": 0, "skipped": 0, "denied": 0}
    for r in res.values():
        c[r["status"]] = c.get(r["status"], 0) + 1
        c["denied"] += r.get("why") == "denied"
    c["checked"] = c["active"] + c["quiet"]
    return c
