"""История счётов вкладки «Реализация» — «стало лучше или хуже за неделю» (решение владельца
30.10.2026, п. 1: хранить дневные агрегаты в закрытом хранилище из ежедневной сборки).

Поток данных (как у сводки по сделке, dealdigest.py):
  1. deploy.yml ДО main.py: scripts/realization_hist_kv.py --fetch → файл RZHIST_PATH
     (закрытый ключ KV rzhist:v1 → файл на раннере);
  2. main.py: счёты сегодняшней сборки (point) + история из файла → раздел contracts.rzHist
     страницы (page: дельта за неделю и ряд 12 недель), сегодняшняя точка — файлом RZHIST_OUT;
  3. deploy.yml ПОСЛЕ деплоя: realization_hist_kv.py --publish — точка RZHIST_OUT сливается с
     ключом, прочитанным заново (merge: одна точка на день, тот же день перезаписывается, не
     больше CAP_DAYS дней), и пишется со сверкой чтением.

В истории и в журнале — ТОЛЬКО счёты (числа заказов и сделок) и даты точек: ни номеров, ни
имён, ни денег (правило 17). Каждое число называет свою единицу (UNITS): заказ поставщику —
исполнение, сделка — обязательство; заказы и сделки не складываются.

Правило недели (одно — у страницы, тестов и журнала): дельта = сегодня − значение на точке
не позже чем 7 дней назад (ближайшей к этой дате и не старше WINDOW дней: раз в сутки сборка
ходит, дыра в неделю — уже не «за неделю»); такой точки нет — дельты нет. Ряд 12 недель —
то же правило на каждой из 12 недель назад (k = 0 — сегодня). «Срок клиента сорван» (заказы)
до набора истории добирается восстановлением Д-12 (ork.week.tiles.late.hist по пятницам,
«переносы срока не хранятся — по нынешнему сроку»): своя точка истории всегда первее.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

VERSION = 1
KV_KEY = "rzhist:v1"
CAP_DAYS = 400               # история — не длиннее 400 дней (и не больше 400 точек)
WEEK_DAYS = 7
WINDOW = 7                   # точка недели k — в окне [сегодня − 7k − 6, сегодня − 7k]
SPARK_WEEKS = 12             # ряд у числа: 12 недель (последняя — сегодня)
PAGE_DAYS = SPARK_WEEKS * WEEK_DAYS   # в страницу — точки последних 12 недель
DIRS = ("main", "sep")       # основной вид и отдельное направление (contracts.sep)
COUNT_MAX = 10 ** 6          # счёт больше — не по форме

# имя счёта → единица. Части очереди «Что сделать сегодня» — двумя видами: без закрытой части
# (пункты life.queue — их видят все с вкладкой) и *Full — вместе с пунктами DEALCTX (чаты и
# договоры, массив под правом suppliers_fin): страница берёт тот вид, что сама показывает
UNITS = {
    "lateOrders": "order",     # «Срок клиента сорван» — заказы (W3, byOrders.late)
    "lateDeals": "deal",       # ... — сделки этих заказов (W3, byDeals.late)
    "threatOrders": "order",   # «Срок под угрозой» — заказы (risk + forecast)
    "overOrders": "order",     # «Шаги ОРК дольше нормы» — заказы (W2)
    "acceptDeals": "deal",     # «Приёмка дольше нормы» — сделки (W1)
    "noMgrOrders": "order",    # «Заказов без менеджера ОРК» (pulse, kind none)
    "noOwnerDeals": "deal",    # «Сделок без хозяина» (week.noOwner — orkview.no_owner)
    "unpaidDeals": "deal",     # «Клиент не оплатил в срок» — сделки
    "closeDeals": "deal",      # шаг 13 «к закрытию» — сделки (ведомость)
    "execOrders": "order",     # «Дела по заказам» — заказы (часть exec)
    "dealDeals": "deal",       # «Дела по сделкам» — сделки (часть deal)
    "hygOrders": "order",      # «Порядок в карточках» — заказы (часть hyg)
    "hygDeals": "deal",        # ... — сделки
    "execOrdersFull": "order", "dealDealsFull": "deal", "hygOrdersFull": "order", "hygDealsFull": "deal",
}
FIELDS = tuple(UNITS)
# Д-12: восстановление «срок сорван» по пятницам — только у этого счёта
BACKFILL = {"lateOrders": ("tiles", "late")}


def _ok(t) -> bool:
    return isinstance(t, dict) and t.get("state") in ("show", "prelim")


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= COUNT_MAX else None


def _view(contracts: dict | None, d: str) -> dict | None:
    if not isinstance(contracts, dict):
        return None
    v = contracts if d == "main" else contracts.get("sep")
    if not isinstance(v, dict) or not isinstance((v.get("ork") or {}).get("week"), dict):
        return None
    return v


def _queue_counts(queue: list[dict]) -> dict:
    import lifecycle              # одно определение счёта по частям (lifecycle.queue_counts)
    return lifecycle.queue_counts(queue)["bucketCounts"]


def counts(contracts: dict | None, dealctx: dict | None, d: str = "main") -> dict | None:
    """Счёты вида d («main» | «sep») из данных вкладки — те же поля, что рисует «Неделя»
    (_orkWeekHead, _orkWeekToday, _orkWeekLedger). Неизвестное (плитка «Нет данных») — None,
    а не 0. Вида нет — None."""
    v = _view(contracts, d)
    if v is None:
        return None
    W = v["ork"]["week"]
    T = W.get("tiles") or {}
    T1, T2, T3 = T.get("W1") or {}, T.get("W2") or {}, T.get("W3") or {}
    bo = T3.get("byOrders") or T3.get("by") or {}
    bd = T3.get("byDeals") or {}
    out: dict = dict.fromkeys(FIELDS)
    if _ok(T3):
        out["lateOrders"] = _int(bo.get("late") or 0)
        out["lateDeals"] = _int(bd.get("late") or 0)
        out["threatOrders"] = _int((bo.get("risk") or 0) + (bo.get("forecast") or 0))
    if _ok(T2):
        out["overOrders"] = _int(T2.get("value"))
    if _ok(T1) and T1.get("unit") == "deal":
        out["acceptDeals"] = _int(T1.get("value"))
    rows = (W.get("pulse") or {}).get("rows") or []
    none = next((r for r in rows if r.get("kind") == "none"), None)
    out["noMgrOrders"] = _int((none or {}).get("n") or 0)
    own = W.get("noOwner")
    out["noOwnerDeals"] = _int(own.get("n")) if isinstance(own, dict) and own.get("unit") == "deal" else None
    up = T.get("unpaid") or {}
    out["unpaidDeals"] = _int(up.get("deals")) if up.get("deals") is not None else None
    s13 = next((s for s in W.get("ledger") or [] if s.get("no") == 13), None)
    if s13 is not None and s13.get("unit") == "deal":
        out["closeDeals"] = _int(s13.get("now") or 0)
    life = v.get("life") or {}
    lq = list(life.get("queue") or [])
    dq = [q for q in ((dealctx or {}).get("queue") or []) if bool(q.get("seg")) == (d == "sep")]
    for suf, q in (("", lq), ("Full", lq + dq)):
        bc = _queue_counts(q)
        out["execOrders" + suf] = _int(bc["exec"]["orders"])
        out["dealDeals" + suf] = _int(bc["deal"]["deals"])
        out["hygOrders" + suf] = _int(bc["hyg"]["orders"])
        out["hygDeals" + suf] = _int(bc["hyg"]["deals"])
    return out


def point(contracts: dict | None, dealctx: dict | None, day: dt.date) -> dict | None:
    """Точка истории на день day: {d, main, sep?}. Основного вида нет — None (сборка без
    «Реализации»: точка из пустоты затёрла бы день)."""
    m = counts(contracts, dealctx, "main")
    if m is None:
        return None
    p = {"d": day.isoformat(), "main": m}
    s = counts(contracts, dealctx, "sep")
    if s is not None:
        p["sep"] = s
    return p


def _day(v) -> dt.date | None:
    if not isinstance(v, str) or len(v) != 10:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def _clean_counts(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    out = {}
    for k in FIELDS:
        x = v.get(k)
        if x is not None and _int(x) is None:
            return None
        out[k] = x
    return out


def clean_point(p) -> dict | None:
    """Точка по форме: дата ISO, main — счёты (целые 0…10⁶ или None), sep — так же или нет.
    Чужие поля отбрасываются (в историю не попадёт ничего, кроме счётов)."""
    if not isinstance(p, dict) or _day(p.get("d")) is None:
        return None
    m = _clean_counts(p.get("main"))
    if m is None:
        return None
    out = {"d": p["d"], "main": m}
    if p.get("sep") is not None:
        s = _clean_counts(p["sep"])
        if s is None:
            return None
        out["sep"] = s
    return out


def empty() -> dict:
    return {"v": VERSION, "units": dict(UNITS), "pts": []}


def clean(raw) -> tuple[dict | None, int]:
    """История из ключа или файла → (история, отброшено точек). Не та версия или не словарь —
    (None, 0): такую историю публикация не перезаписывает."""
    if not isinstance(raw, dict) or raw.get("v") != VERSION or not isinstance(raw.get("pts"), list):
        return None, 0
    good, bad = [], 0
    for p in raw["pts"]:
        c = clean_point(p)
        if c is None:
            bad += 1
        else:
            good.append(c)
    h = empty()
    by = {p["d"]: p for p in sorted(good, key=lambda p: p["d"])}   # повтор дня — последний
    h["pts"] = [by[k] for k in sorted(by)]
    return h, bad


def merge(hist: dict | None, pt: dict | None, cap: int = CAP_DAYS) -> dict:
    """Точка в историю: одна на день (тот же день — перезапись), по дате; не старше cap дней от
    последней точки и не больше cap точек."""
    h = empty()
    pts = {p["d"]: p for p in ((hist or {}).get("pts") or [])}
    if pt is not None:
        pts[pt["d"]] = pt
    keys = sorted(pts)
    if keys:
        last = dt.date.fromisoformat(keys[-1])
        keys = [k for k in keys if (last - dt.date.fromisoformat(k)).days < cap][-cap:]
    h["pts"] = [pts[k] for k in keys]
    return h


def load(path: str | Path | None) -> dict | None:
    """Файл истории (RZHIST_PATH) → история; нет файла или не по форме — None."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return clean(raw)[0]


def write(path: str | Path, val: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(val, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def dump(val: dict) -> bytes:
    return json.dumps(val, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sample(pts: list[dict], d: str, f: str, today: dt.date, k: int):
    """Значение недели k: последняя точка в окне [today − 7k − (WINDOW−1), today − 7k] → (дата, v)."""
    hi = today - dt.timedelta(days=WEEK_DAYS * k)
    lo = hi - dt.timedelta(days=WINDOW - 1)
    for p in reversed(pts):
        t = dt.date.fromisoformat(p["d"])
        if lo <= t <= hi:
            v = (p.get(d) or {}).get(f)
            if v is not None:
                return t, v, "kv"
        if t < lo:
            break
    return None


def _backfill(contracts: dict | None, d: str, f: str, today: dt.date) -> dict:
    """Д-12 для f (только lateOrders): {k: (пятница, значение)} по восстановленному ряду."""
    if f not in BACKFILL:
        return {}
    v = _view(contracts, d)
    lt = ((((v or {}).get("ork") or {}).get("week") or {}).get("tiles") or {}).get("late") or {}
    hs, fr = lt.get("hist") or [], lt.get("fridays") or []
    out = {}
    for x, s in zip(hs, fr):
        t = _day(s)
        if t is None or _int(x) is None or t > today:
            continue
        k = (today - t).days // WEEK_DAYS
        if 1 <= k < SPARK_WEEKS:
            out[k] = (t, x)
    return out


def series(hist: dict, contracts: dict | None, d: str, f: str, today: dt.date) -> list:
    """Ряд 12 недель (старая → сегодня): [{at, v, src}] или None, где точки нет."""
    pts = (hist or {}).get("pts") or []
    bf = _backfill(contracts, d, f, today)
    out = []
    for k in range(SPARK_WEEKS - 1, -1, -1):
        s = _sample(pts, d, f, today, k)
        if s is None and k in bf:
            s = (*bf[k], "d12")
        out.append(None if s is None else {"at": s[0].isoformat(), "v": s[1], "src": s[2]})
    return out


def delta(hist: dict, contracts: dict | None, d: str, f: str, today: dt.date) -> dict | None:
    """Дельта за неделю: сегодня − неделя назад (правило в шапке модуля) → {d, now, was, at,
    src} или None (нет сегодняшней точки, нет точки неделю назад, плитка «Нет данных»)."""
    s = series(hist, contracts, d, f, today)
    now, was = s[-1], s[-2]
    if now is None or was is None or now["at"] != today.isoformat():
        return None
    return {"d": now["v"] - was["v"], "now": now["v"], "was": was["v"], "at": was["at"], "src": was["src"]}


def page(hist: dict, contracts: dict | None, today: dt.date) -> dict:
    """Раздел contracts.rzHist страницы: дельты и ряды по видам, даты истории и точки последних
    12 недель (только счёты). Страница правила не считает — берёт готовое."""
    pts = (hist or {}).get("pts") or []
    lo = today - dt.timedelta(days=PAGE_DAYS)
    out = {"v": VERSION, "today": today.isoformat(), "since": pts[0]["d"] if pts else None,
           "n": len(pts), "units": dict(UNITS), "wk": {}, "spark": {},
           "pts": [p for p in pts if dt.date.fromisoformat(p["d"]) > lo]}
    for d in DIRS:
        if not any(p.get(d) for p in pts):
            continue
        wk, sp = {}, {}
        for f in FIELDS:
            w = delta(hist, contracts, d, f, today)
            if w is not None:
                wk[f] = w
            s = series(hist, contracts, d, f, today)
            if sum(1 for x in s if x is not None) >= 2:
                sp[f] = [None if x is None else x["v"] for x in s]
                if any(x and x["src"] == "d12" for x in s):
                    sp[f + "Src"] = [None if x is None else x["src"] for x in s]
        out["wk"][d], out["spark"][d] = wk, sp
    return out


def log_line(pt: dict | None, hist: dict | None) -> str:
    """Строка журнала: только счёты основного вида и длина истории (правило 17)."""
    pts = (hist or {}).get("pts") or []
    m = (pt or {}).get("main") or {}
    c = lambda k: "—" if m.get(k) is None else str(m[k])      # noqa: E731
    return (f"заказов: сорван {c('lateOrders')}, под угрозой {c('threatOrders')}, дольше нормы "
            f"{c('overOrders')}, без менеджера ОРК {c('noMgrOrders')}, дела по заказам {c('execOrders')}, "
            f"порядок в карточках {c('hygOrders')}; сделок: сорван {c('lateDeals')}, приёмка дольше "
            f"нормы {c('acceptDeals')}, не оплатил {c('unpaidDeals')}, дела по сделкам {c('dealDeals')}, "
            f"порядок в карточках {c('hygDeals')}, к закрытию {c('closeDeals')}, без хозяина "
            f"{c('noOwnerDeals')}; точек истории {len(pts)}"
            + (f" с {pts[0]['d']}" if pts else ""))
