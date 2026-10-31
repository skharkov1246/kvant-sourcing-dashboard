#!/usr/bin/env python3
"""Зонд v63 «Подтверждение передачи заказа в работу ОРК»: задача Битрикса перед приёмкой.

ВОПРОС ВЛАДЕЛЬЦА (31.10.2026). С конца октября перед передачей заказа в ОРК ставят задачу
«Подтверждение готовности передачи … в работу ОРК» (поставщик прошёл проверку, оффер свежий,
поставщик не сменился). Заказ висит в ней до месяца, а дашборд считает это время приёмкой ОРК
(шаг 1, норма 1 раб. дн.). Решение владельца 31.10.2026: шаг 0 до приёмки, отвечает исполнитель
задачи, норма 5 раб. дн. Зонд меряет, как такие задачи лежат в портале, ДО правки расчёта.

ЧТО МЕРЯЕТСЯ.
  • Задачи tasks.task.list по подстроке названия (ФРАЗЫ) с 01.09.2026: сколько, по месяцам
    заведения, статусы, открытые и закрытые.
  • Привязка к CRM (UF_CRM_TASK): к заказу СП-172 («T<hex 172>_<id>» = «Tac_<id>»), к сделке
    («D_<id>»), к другому, без привязки. Одна задача — сколько заказов.
  • Заказы с задачей: стадия сейчас (код стадии), задача открыта/закрыта; сколько дней от
    заведения заказа до заведения задачи; сколько задача открыта (календарные дни,
    медиана / 75 % / 90 %); у закрытых — сколько дней от закрытия до ухода заказа со стадии.
  • Исполнители: сколько разных, из отдела ОРК или нет (счёт людей, без имён).
  • Названия: сколько задач начинается с каждой ФРАЗЫ; прочие — только числом (в названии
    может стоять заказчик — журнал публичный).

ЧТО В ЖУРНАЛ. Только счётчики и доли: ни названий, ни номеров, ни имён.
НАГРУЗКА: задачи — 1–5 запросов; заказы по id — ≈ 1 на 50; отделы — 2–3. Меньше 30 запросов.
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MSK = dt.timezone(dt.timedelta(hours=3))
С_ДАТЫ = "2026-09-01T00:00:00+03:00"
ФРАЗЫ = ("Подтверждение готовности передачи", "Подтверждение передачи", "готовности передачи")
ЗАКАЗ = 172
ПРЕФИКС_ЗАКАЗА = f"T{ЗАКАЗ:x}_"                    # «Tac_<id>» — привязка задачи к элементу СП-172
ОТДЕЛ_ОРК_RX = re.compile(r"реализаци\w*\s+контракт|\bОРК\b", re.I)


def ts(v) -> dt.datetime | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=MSK)


def дни(a: dt.datetime | None, b: dt.datetime | None) -> float | None:
    return None if not a or not b else round((b - a).total_seconds() / 86400, 1)


def распределение(xs: list[float]) -> str:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return "нет данных"
    q = lambda p: xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]  # noqa: E731
    return f"n {len(xs)} · медиана {statistics.median(xs):.1f} · 75 % {q(.75):.1f} · 90 % {q(.9):.1f} · макс {xs[-1]:.1f}"


def задачи(client) -> list[dict]:
    """Задачи по каждой фразе (подстрока названия), без повторов по id."""
    out: dict[str, dict] = {}
    sel = ["ID", "TITLE", "STATUS", "CREATED_DATE", "CLOSED_DATE", "RESPONSIBLE_ID", "UF_CRM_TASK", "DEADLINE"]
    for фраза in ФРАЗЫ:
        start = 0
        for _ in range(40):
            env = client.call_envelope("tasks.task.list", {
                "filter": {"%TITLE": фраза, ">=CREATED_DATE": С_ДАТЫ}, "select": sel, "start": start}) or {}
            res = env.get("result") or {}
            rows = (res.get("tasks") if isinstance(res, dict) else res) or []
            for t in rows:
                tid = str(t.get("id") or t.get("ID") or "")
                if tid:
                    out[tid] = t
            nxt = env.get("next")
            if not nxt or not rows:
                break
            start = nxt
    return list(out.values())


def поле(t: dict, *keys):
    for k in keys:
        if k in t and t[k] not in (None, ""):
            return t[k]
    return None


def main() -> int:
    import config
    from bitrix_client import BitrixClient
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    print("ЗОНД v63 · подтверждение передачи заказа в работу ОРК")
    try:
        ts_all = задачи(client)
    except Exception as e:  # noqa: BLE001
        print(f"tasks.task.list: отказ портала ({type(e).__name__}) — задачи не прочитаны")
        return 1
    print(f"задач с {С_ДАТЫ[:10]} по фразам {len(ФРАЗЫ)}: {len(ts_all)}")
    if not ts_all:
        return 0
    назв = Counter()
    for t in ts_all:
        title = str(поле(t, "title", "TITLE") or "")
        k = next((f for f in ФРАЗЫ if title.casefold().startswith(f.casefold())), None)
        назв[k or ("содержит фразу, начало другое" if any(f.casefold() in title.casefold() for f in ФРАЗЫ) else "прочее")] += 1
    print("названия:", "; ".join(f"«{k}» {v}" for k, v in назв.most_common()))
    мес = Counter((ts(поле(t, "createdDate", "CREATED_DATE")) or dt.datetime.min.replace(tzinfo=MSK)).astimezone(MSK).strftime("%m.%Y") for t in ts_all)
    print("по месяцам заведения:", ", ".join(f"{k} {v}" for k, v in sorted(мес.items())))
    стат = Counter(str(поле(t, "status", "STATUS") or "?") for t in ts_all)
    print("статусы (5 — закрыта):", ", ".join(f"{k} {v}" for k, v in sorted(стат.items())))
    привязки = Counter()
    к_заказам: dict[str, list[dict]] = {}
    на_задачу = Counter()
    for t in ts_all:
        crm = поле(t, "ufCrmTask", "UF_CRM_TASK") or []
        crm = crm if isinstance(crm, list) else [crm]
        if not crm:
            привязки["без привязки"] += 1
        зак = [str(x)[len(ПРЕФИКС_ЗАКАЗА):] for x in crm if str(x).startswith(ПРЕФИКС_ЗАКАЗА)]
        for x in crm:
            s = str(x)
            привязки["к заказу СП-172" if s.startswith(ПРЕФИКС_ЗАКАЗА) else "к сделке" if s.startswith("D_")
                     else f"другое ({re.sub(r'_.*', '', s)[:6]})"] += 1
        на_задачу[len(зак)] += 1
        for oid in зак:
            к_заказам.setdefault(oid, []).append(t)
    print("привязки UF_CRM_TASK:", ", ".join(f"{k} {v}" for k, v in привязки.most_common()))
    print("заказов на задачу:", ", ".join(f"{k} — {v}" for k, v in sorted(на_задачу.items())))
    print(f"заказов с задачей: {len(к_заказам)}; с двумя и больше задачами: {sum(1 for v in к_заказам.values() if len(v) > 1)}")
    закрыта = lambda t: str(поле(t, "status", "STATUS") or "") == "5" or bool(поле(t, "closedDate", "CLOSED_DATE"))  # noqa: E731
    сейчас = dt.datetime.now(MSK)
    откр = [дни(ts(поле(t, "createdDate", "CREATED_DATE")), сейчас) for t in ts_all if not закрыта(t)]
    закр = [дни(ts(поле(t, "createdDate", "CREATED_DATE")), ts(поле(t, "closedDate", "CLOSED_DATE"))) for t in ts_all if закрыта(t)]
    print(f"открыты сейчас: {len(откр)} — возраст, кал. дн.: {распределение(откр)}")
    print(f"закрыты: {len(закр)} — от заведения до закрытия, кал. дн.: {распределение(закр)}")
    # исполнители: ОРК или нет (счёт людей)
    try:
        орк: set[str] = set()
        for d in client.departments():
            if ОТДЕЛ_ОРК_RX.search(str(d.get("NAME") or "")):
                орк |= {str(x) for x in client.dept_member_ids(d["ID"])}
        исп = {str(поле(t, "responsibleId", "RESPONSIBLE_ID") or "") for t in ts_all} - {""}
        print(f"исполнителей: {len(исп)}; из отдела ОРК {len(исп & орк)}, вне ОРК {len(исп - орк)}")
    except Exception as e:  # noqa: BLE001
        print(f"отделы: не прочитаны ({type(e).__name__})")
    # заказы с задачей: стадия, заведение заказа → задача, закрытие задачи → уход со стадии
    ids = sorted(к_заказам, key=lambda x: int(x) if x.isdigit() else 0)
    заказы: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        пачка = [int(x) for x in ids[i:i + 50] if x.isdigit()]
        for it in client.list_items(ЗАКАЗ, filter={"@id": пачка}, select=["id", "stageId", "createdTime", "movedTime", "categoryId"]):
            заказы[str(it["id"])] = it
    print(f"заказов прочитано: {len(заказы)} из {len(ids)}")
    стадии = Counter(str(o.get("stageId") or "?").split(":")[-1] for o in заказы.values())
    print("стадия заказов с задачей (код):", ", ".join(f"{k} {v}" for k, v in стадии.most_common()))
    до_задачи, после = [], []
    откр_по_стадии = Counter()
    for oid, tt in к_заказам.items():
        o = заказы.get(oid)
        if not o:
            continue
        t = min(tt, key=lambda x: str(поле(x, "createdDate", "CREATED_DATE") or ""))
        до_задачи.append(дни(ts(o.get("createdTime")), ts(поле(t, "createdDate", "CREATED_DATE"))))
        st = str(o.get("stageId") or "?").split(":")[-1]
        if not закрыта(t):
            откр_по_стадии[st] += 1
        else:
            cl, mv = ts(поле(t, "closedDate", "CLOSED_DATE")), ts(o.get("movedTime"))
            if cl and mv and mv >= cl:
                после.append(дни(cl, mv))
    print(f"от заведения заказа до заведения задачи, кал. дн.: {распределение(до_задачи)}")
    print("открытые задачи — стадия заказа (код):", ", ".join(f"{k} {v}" for k, v in откр_по_стадии.most_common()) or "нет")
    print(f"закрыта задача → заказ ушёл со стадии (последний переход после закрытия), кал. дн.: {распределение(после)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
