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

Бюджет — один на сборку (Reader): main.py заводит одного читателя и отдаёт его
сначала реализации (contracts.py), потом пресейлу (presale.py). Пресейл читает
остатком, и стоп по лимиту портала, случившийся у реализации, действует и на него.
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


class Reader:
    """Один бюджет чтения чатов на сборку — общий для всех, кто спрашивает.

    Вкладки читают чаты по очереди: сначала реализация (там деньги), потом пресейл
    остатком бюджета. Бюджет — число ЗАПРОСОВ к порталу (сделка без id чата его не
    тратит), а первый лимит портала останавливает чтение для всех следующих вызовов:
    иначе пресейл после стопа реализации снова упирался бы в тот же лимит.
    Сделка, уже прочитанная в этой сборке, второй раз портал не спрашивает."""

    def __init__(self, client, service_ids: set[str] | None = None, budget: int = MAX_CHATS):
        self.client = client
        self.service_ids = {str(s) for s in (service_ids or set())}
        self.budget = budget
        self.reads = 0                    # запросов к порталу за сборку
        self.stopped = False              # портал упёрся в лимит — дальше не читаем никому
        self._seen: dict[str, dict] = {}

    @property
    def left(self) -> int:
        return max(0, self.budget - self.reads)

    def stats(self) -> dict:
        """Для журнала: только счётчики."""
        return {"reads": self.reads, "budget": self.budget, "left": self.left, "stopped": self.stopped}

    def check(self, candidates: list[tuple[str, str, float]], today: dt.date) -> dict[str, dict]:
        """candidates — [(id сделки, id чата, вес для порядка)] → {id сделки: {status, last, days}}.
        Сначала — самые тяжёлые (по весу); сверх остатка бюджета — skipped."""
        out: dict[str, dict] = {}
        # чтение чатов — необязательная часть сборки: на лимите портала ждём не дольше
        # минуты (у клиента по умолчанию — до 30 мин на вызов), потом останавливаемся
        бюджет_было = getattr(self.client, "limit_wait_budget", None)
        if бюджет_было is not None:
            self.client.limit_wait_budget = min(бюджет_было, LIMIT_WAIT_S)
        try:
            self._check(candidates, today, out)
        finally:
            if бюджет_было is not None:
                self.client.limit_wait_budget = бюджет_было
        return out

    def _check(self, candidates, today, out) -> None:
        for did, cid, _w in sorted(candidates, key=lambda x: -(x[2] or 0)):
            if did in self._seen:
                out[did] = dict(self._seen[did])
                continue
            if not cid:
                out[did] = {"status": "unread", "last": "", "days": None, "why": "nochat"}
                continue
            if self.reads >= self.budget or self.stopped:
                out[did] = {"status": "skipped", "last": "", "days": None}
                continue
            self.reads += 1
            try:
                r = self.client.call("im.dialog.messages.get", {"DIALOG_ID": f"chat{cid}", "LIMIT": MESSAGES},
                                     retries=1)
            except Exception as e:            # нет доступа у вебхука, лимит, сбой
                r = None
                if type(e).__name__ == "BitrixLimitError":
                    # одиночный вызов на лимите ждёт до бюджета ожидания клиента (30 мин):
                    # 400 чатов так пережили бы сборку — первый лимит останавливает чтение
                    self.stopped = True
                elif "ACCESS" in str(e).upper():
                    out[did] = self._seen[did] = {"status": "unread", "last": "", "days": None, "why": "denied"}
                    continue
            # прочитан — только настоящий ответ со списком сообщений: пустой ответ не «тишина»
            readable = isinstance(r, dict) and isinstance(r.get("messages"), list)
            msgs = r["messages"] if readable else []
            last = last_human(msgs, self.service_ids) if readable else None   # максимум даты: порядок не задан
            out[did] = {"status": classify(last, today, readable),
                        "last": last.strftime("%d.%m.%Y") if last else "",
                        "days": (today - last).days if last else None}
            if readable:
                self._seen[did] = dict(out[did])


def check(client, candidates: list[tuple[str, str, float]], today: dt.date,
          service_ids: set[str] | None = None, budget: int = MAX_CHATS) -> dict[str, dict]:
    """Разовая проверка со своим бюджетом — то же, что Reader(...).check(...)."""
    return Reader(client, service_ids, budget).check(candidates, today)


def summary(res: dict[str, dict]) -> dict:
    """Счётчики для журнала и вкладки: только агрегаты."""
    c = {"active": 0, "quiet": 0, "unread": 0, "skipped": 0, "denied": 0}
    for r in res.values():
        c[r["status"]] = c.get(r["status"], 0) + 1
        c["denied"] += r.get("why") == "denied"
    c["checked"] = c["active"] + c["quiet"]
    return c
