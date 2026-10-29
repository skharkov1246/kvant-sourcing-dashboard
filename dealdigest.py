"""Сводка по сделке для модели — закрытый ключ KV dealdigest:v1 (решение владельца 25.10.2026).

«Главное» по сделке модель ищет по всему контексту: сообщения чата и сводка учёта (стадия,
сроки, заказы, договоры, экономика). Сводку пишет сборка дашборда — те же числа, что на
вкладке «Реализация», без повторного чтения портала: main.py собирает её из строк
contracts.compute, очереди (lifecycle и dealctx) и пресейла, кладёт файлом (DEALDIGEST_OUT),
а шаг деплоя (deploy.yml → scripts/dealdigest_kv.py --publish) — в закрытый ключ после
выкладки сайта. Ночная оценка чатов (chat-context.yml → --fetch → DEALDIGEST_PATH,
scripts/chat_context.py загрузить_сводку и строки_сводки) отдаёт её модели строками [d:ключ].

Что в сводке (решение 25.10.2026 — стадия, сроки, заказы, договоры, экономика):
  stage    — стадия сделки (имя стадии портала), дней в стадии, шаг регламента ОРК и его
             имя, сверх нормы ли шаг, «ждём УПД от клиента»;
  dl       — ближайший срок клиента, сорван ли и на сколько дней, заказов без срока;
  deal     — даты выигрыша и входа в реализацию, «замедлено» и «простой» (кроме решённого
             оценкой чата), пункты очереди регламента, договоров и экономики подписями (не
             имена — коды и закрытые подписи); пунктов по чату нет — это вывод модели;
  contract — исход сверки договоров, чьего договора нет, флаги сверки подписями;
  econ     — продажа как на вкладке (€ без НДС, источник), «Сумма» и налог сделки в её
             валюте (dealSum, dealTax), продажа сверенного файла экономики и вердикт сверки;
  presale  — пресейл: чей ход по стадии и пометки (нет сорсера, нет запроса, застой);
  orders   — до ORDERS_MAX живых заказов: шаг, стадия, срок клиента, сорван ли, плановое
             окончание производства, доставка и УПД.

Чего в сводке НЕТ: имён людей, названий заказчика, поставщика и сделки, номеров заказов,
цитат и текста чата (правило 17, SECURITY.md). Ключ закрытый (KV портала), в журнал прогона —
только счётчики. Модели строки уходят ещё и через маску имён (chat_context.строки_сводки).
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

VERSION = 1
KV_KEY = "dealdigest:v1"
ORDERS_MAX = 8               # строк o1…o8 (chat_context.КЛЮЧИ_СВОДКИ)
FLAGS_MAX = 10               # пунктов очереди подписью на сделку (строки_сводки берёт до 10)
# коды очереди dealctx по оценке чата моделью (dealctx.EXC: chatReply, chatRisk, chatDue)
CHAT_CODE_PREFIX = "chat"
STR_MAX = 60                 # знаков в строке значения (строки_сводки режет так же)
# ключи сводки сделки — те, что читает chat_context.строки_сводки (сверяет тест)
DEAL_KEYS = ("stage", "dl", "deal", "contract", "econ", "presale", "orders")
# чей ход в пресейле (presale.BALL_LABEL) и пометки строки пресейла — подписью (правило 4)
PRESALE_FLAG = {"nosrc": "нет сорсера", "gone": "сорсер уволен", "norfq": "нет запроса поставщику",
                "stale": "застой по стадии", "staleUnverified": "застой, чат не проверен",
                "staleChat": "застой по стадии, в чате идёт работа"}


def _s(v) -> str:
    """Строка значения: без переводов строк, не длиннее STR_MAX; длиннее — по слову с «…»
    (иначе строки_сводки отрезала бы посреди слова)."""
    t = " ".join(str(v or "").split())
    if len(t) <= STR_MAX:
        return t
    cut = t[:STR_MAX - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,.;:—-") + "…"


def _n(v):
    """Число или None (NaN и бесконечность — None)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return round(v, 2) if isinstance(v, float) else v


def _put(d: dict, k: str, v) -> None:
    """Пустое не пишется: в строке сводки его и так нет, а ключ легче."""
    if v is None or v == "" or v == [] or v == {}:
        return
    d[k] = v


def _stage(r: dict, step_name) -> dict:
    out: dict = {}
    c = r.get("stepCell") or {}
    step = c.get("step") or r.get("dealStep")
    _put(out, "name", _s(r.get("stageName")))
    _put(out, "days", _n(round(r["stageDays"])) if _n(r.get("stageDays")) is not None else None)
    _put(out, "step", step)
    _put(out, "stepName", _s(step_name(step)) if step else "")
    if c.get("over") is not None:
        out["over"] = bool(c["over"])
    if c.get("waitUtd"):
        out["waitUtd"] = True
    return out


def _dl(r: dict) -> dict:
    out: dict = {}
    _put(out, "next", _s(r.get("dlNext")))
    if r.get("late"):
        out["late"] = True
        _put(out, "lateDays", _n(r.get("lateDays")))
        _put(out, "lateOrders", _n(r.get("lateN")))
    _put(out, "noDl", _n(r.get("noDl")) or None)
    return out


def by_model(r: dict) -> bool:
    """Простой сделки подтвердила оценка переписки моделью (dealctx.decide → src «model»):
    пояснение строки — ровно dealctx.PUBLIC_STUCK_MODEL."""
    from dealctx import PUBLIC_STUCK_MODEL
    return PUBLIC_STUCK_MODEL in str(r.get("why") or "")


def _deal(r: dict, flags: list[str]) -> dict:
    out: dict = {}
    _put(out, "won", _s(r.get("wonAt")))
    _put(out, "inReal", _s(r.get("entered")))
    _put(out, "kp", _s(r.get("kpDate")))
    # «подвисла», решённая оценкой чата, — вывод самой модели, а не учёт: вернуть его модели
    # как сводку значит дать ей опереться на свой вчерашний вердикт (ревью 28.10.2026).
    # Тогда — только то, что сказал учёт без модели: «замедлено» по сорванному сроку
    if by_model(r):
        if r.get("late"):
            out["slow"] = True
    else:
        if r.get("slow"):
            out["slow"] = True
        if r.get("stuck"):
            out["stuck"] = True
    _put(out, "orders", _n(r.get("norders")))
    _put(out, "flags", flags[:FLAGS_MAX])
    return out


def _contract(cell: dict | None) -> dict:
    """Ячейка договоров карточки (dealctx._contract_cell) → исход, чьего нет, флаги подписью."""
    if not cell:
        return {}
    from dealctx import SIDE_LBL
    out: dict = {"outcome": _s(cell.get("outcome")), "read": _s(cell.get("at"))}
    _put(out, "missing", [SIDE_LBL[s] for s in cell.get("missing") or [] if s in SIDE_LBL])
    _put(out, "flags", [_s(f.get("short")) for f in cell.get("flags") or []][:FLAGS_MAX])
    return out


def _econ(r: dict, cell: dict | None, e: dict | None) -> dict:
    """Продажа как на вкладке и сверка с файлом. Деньги — по решению 25.10.2026 («экономика»
    в сводке): только в закрытом ключе и в запросе модели, не в журнале."""
    out: dict = {}
    _put(out, "saleEur", _n(r.get("saleEur")))
    _put(out, "src", {"file": "файл экономики", "bitrix": "Сумма сделки"}.get(r.get("saleSrc") or "", ""))
    if r.get("vatK") and r.get("vatK") != 1:
        out["vat"] = "без НДС по правилу"
    _put(out, "dealSum", _n(r.get("saleAmt")))       # «Сумма» сделки в её валюте, как в портале
    _put(out, "dealTax", _n(r.get("saleTax")))
    _put(out, "cur", _s(r.get("saleCur")))
    if e:
        _put(out, "fileSale", _n(e.get("sale")))
        _put(out, "fileCur", _s(e.get("cur")))
        if e.get("rebid"):
            out["rebid"] = True
            _put(out, "kpSale", _n(e.get("kpSale")))
    if cell:
        if cell.get("gap") is not None:
            out["gapPct"] = _n(cell["gap"])
            out["gap"] = ("до переторжки" if cell.get("preRebid") else "расходится" if cell.get("over")
                          else "похоже на сумму с НДС" if cell.get("vat") else "сходится")
        elif cell.get("gapNote"):
            out["gap"] = _s(cell["gapNote"])
    return out


def _order(o: dict) -> dict:
    out: dict = {}
    _put(out, "step", o.get("step"))
    _put(out, "stage", _s(o.get("stageName")))
    _put(out, "dl", _s(o.get("dl")))
    if o.get("late"):
        out["late"] = True
    _put(out, "prodEnd", _s(o.get("prodEnd")))
    _put(out, "delivered", _s(o.get("delivered")))
    if o.get("delUtd"):
        out["utd"] = True
    if o.get("stepOver") is not None:
        out["over"] = bool(o["stepOver"])
    if o.get("stepWaitUtd"):
        out["waitUtd"] = True
    return out


def build(rows: list[dict] | None, life: dict | None, payload: dict | None, presale: dict | None,
          now: dt.datetime, *, ctx: dict | None = None) -> dict:
    """→ {"version", "at", "deals": {сделка: сводка}}.

    rows — строки contracts.compute (открытые сделки реализации), life — contracts.life_all
    (очередь регламента обоих видов), payload — закрытая часть dealctx.build (очередь чатов и
    договоров, ячейки договоров и экономики), ctx — свод dealctx.clean (файл экономики),
    presale — presale.compute (openDeals). Ни одного имени: только стадии, шаги, даты, числа
    и подписи из закрытых таблиц."""
    import ork
    import presale as presale_mod

    def step_name(st) -> str:
        return (ork.STEP.get(st) or {}).get("name", "")
    labels = {**((life or {}).get("labels") or {}), **((payload or {}).get("labels") or {})}
    flags: dict[str, list[str]] = {}
    for q in list((life or {}).get("queue") or []) + list((payload or {}).get("queue") or []):
        # пункты по чату (chatReply, chatRisk, chatDue) — вчерашняя оценка модели, а не учёт:
        # в сводке их нет, иначе «главное» оперлось бы на [d:deal] как на факт (ревью 28.10.2026)
        if str(q.get("code") or "").startswith(CHAT_CODE_PREFIX):
            continue
        lbl = _s(labels.get(q.get("code")) or "")
        did = str(q.get("deal") or "")
        if lbl and did and lbl not in flags.setdefault(did, []):
            flags[did].append(lbl)
    cells = (payload or {}).get("deals") or {}
    econ = (ctx or {}).get("econ") or {}
    deals: dict[str, dict] = {}
    for r in rows or []:
        if r.get("closed"):
            continue
        did = str(r.get("deal") or "")
        if not did.isdigit():
            continue
        c = cells.get(did) or {}
        d: dict = {}
        _put(d, "stage", _stage(r, step_name))
        _put(d, "dl", _dl(r))
        _put(d, "deal", _deal(r, flags.get(did) or []))
        _put(d, "contract", _contract(c.get("contract")))
        _put(d, "econ", _econ(r, c.get("econ"), econ.get(did)))
        orders = [_order(o) for o in r.get("orders") or [] if o.get("live")][:ORDERS_MAX]
        _put(d, "orders", [o for o in orders if o])
        deals[did] = d
    for p in (presale or {}).get("openDeals") or []:
        did = str(p.get("id") or "")
        if not did.isdigit() or did in deals:
            continue
        d = {"stage": {}, "presale": {}}
        _put(d["stage"], "name", _s(p.get("stageName")))
        _put(d["stage"], "days", _n(p.get("days")))
        _put(d["presale"], "ball", _s(presale_mod.BALL_LABEL.get(p.get("ball") or "", "")))
        _put(d["presale"], "flags", [PRESALE_FLAG[f] for f in p.get("flags") or [] if f in PRESALE_FLAG])
        _put(d["presale"], "created", _s((p.get("created") or "")[:10]))
        deals[did] = {k: v for k, v in d.items() if v}
    return {"version": VERSION, "at": now.isoformat(timespec="seconds"), "deals": deals}


def check(raw) -> str:
    """Форма сводки перед публикацией: "" — годна, иначе постоянный код без данных.
    Та же, что принимает chat_context.загрузить_сводку, плюс закрытый список ключей сделки."""
    if not isinstance(raw, dict) or raw.get("version") != VERSION or not isinstance(raw.get("deals"), dict):
        return "DIGEST_BAD_FORM"
    try:
        at = dt.datetime.fromisoformat(str(raw.get("at") or ""))
    except ValueError:
        return "DIGEST_BAD_AT"
    if at.tzinfo is None:
        return "DIGEST_BAD_AT"
    for k, v in raw["deals"].items():
        if not str(k).isdigit() or not isinstance(v, dict) or not set(v) <= set(DEAL_KEYS):
            return "DIGEST_BAD_DEAL"
        if len(v.get("orders") or []) > ORDERS_MAX:
            return "DIGEST_BAD_DEAL"
    return ""


def write(path: str | Path, digest: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(digest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def counts(digest: dict) -> dict:
    """Счётчики для журнала (правило 17): сделок, с заказами, с договором, с экономикой, пресейла."""
    D = (digest or {}).get("deals") or {}
    return {"deals": len(D), "orders": sum(1 for x in D.values() if x.get("orders")),
            "contract": sum(1 for x in D.values() if x.get("contract")),
            "econ": sum(1 for x in D.values() if x.get("econ")),
            "presale": sum(1 for x in D.values() if x.get("presale"))}
