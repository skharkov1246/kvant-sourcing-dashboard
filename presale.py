"""Воронка пресейла — сделки воронки «Пресейл», которые ведёт сорсинг.

КАК УСТРОЕНА (зонд v48, 09.10.2026, scripts/probe_presale.py). Воронка сделок №32
«Пресейл»: первая сделка 14.01.2026 (проба), в работе с сентября — 54 сделки в
сентябре, 37 за 9 дней октября. Тринадцать рабочих стадий — «Новый запрос» →
«Назначение сорсера» → «Проработка ТЗ и вопросы заказчику» → «Поиск поставщиков и
сбор КП» → «Анализ КП и фиксация поставщиков» → «Сравнение предложений и расчёт
ЭП» → «Экономика готова | Можно подавать» → тендерные стадии → «Сорсинг завершён /
Ожидаем решение заказчика» → «Согласование финальной спецификации»; успех —
«Сделка выиграна»; двенадцать стадий отказа с причиной («Экономически не
интересно», «Вовремя не подались», «Отмена заказчиком закупки», …).
  • Сделку заводит человек (68 %) или служебная запись (31 %); ведёт — сорсер
    (53 %) или коммерсант (46 %). «Сорсер» заполнен у 66 %, «КАМ» — у 82 %,
    сумма — лишь у 4 % (сумма появляется после сравнения предложений).
  • Под 95 сделками 878 запросов поставщикам (СП-166, воронка «Запросы»): 57 %
    сделок с запросом, медиана 9 запросов на сделку, первый запрос — через 2 дня;
    59 % запросов заводит робот от имени служебной записи.
  • Из 99 сделок, побывавших в воронке, 4 ушли в другие воронки — за полдня:
    это ошибочно заведённые, а не пройденный путь. В реализацию не ушла ни одна.

ЧТО СЧИТАЕТСЯ. Модуль — чистое вычисление из уже выгруженного: сделки воронки,
справочник стадий (client.deal_stage_meta — тот же вызов, что client.stages),
запросы поставщикам отчётного окна (rfqs из main) и справочник людей с ушедшими.
Воронка ищется по имени, а не по номеру: номер в коде устарел бы с первой
перестройкой воронок.

ЧТО ДЕЛАТЬ (то, ради чего блок): сделки без назначенного сорсера, сделки без
единого запроса поставщикам, застрявшие в стадии и те, что ведёт ушедший сорсер.

ЗОЛОТОЕ ПРАВИЛО ЧАТА (решение владельца 19.10.2026, как в реализации): сделка
«стоит» — только если и в её чате тихо. Простой по стадии (> ЗАСТОЙ_ДНЕЙ) — лишь
кандидат; чат сделки читает общий на сборку читатель (chat_activity.Reader, после
реализации — остатком бюджета):
  тихо        → «stale»: стоит, чат подтвердил;
  человек писал за неделю → «staleChat»: в чате идёт работа, действия не требует;
  чат не прочитан или не хватило бюджета → «staleUnverified»: серая корзина,
                сделка видна, но в «стоят» не входит.
Сверка: стоят по стадии = stale + staleChat + staleUnverified (head.staleStage).
Без читателя (chat_reader=None) — прежнее поведение: все простои «stale».
Ещё кандидаты чтения — chat_also (правило вызывающего): очередь КАМа ставит пункт
«мяч у КАМа» уже после 3–7 дней в стадии, раньше ЗАСТОЙ_ДНЕЙ, и золотое правило
действует и там. Такой сделке пишется chat, а флаги не меняются: простоя по стадии
у неё нет. Порядок общий — дольше всех стоящие первыми, так что простой по стадии
(> ЗАСТОЙ_ДНЕЙ) бюджет получает раньше.

КАМ СДЕЛКИ — только из поля «КАМ» (KAM_F). Ответственный сделки — исполнитель
(сорсер или коммерсант, зонд v48: 53 % / 46 %), а не КАМ: подставлять его вместо
пустого поля значило приписать КАМу чужую сделку. Пусто — «КАМ не указан».
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from typing import Callable

import chat_activity
import config
import period

PRESALE_RE = re.compile(r"пре\s*-?\s*сейл|presale|pre\s*-?\s*sale|предпродаж", re.I)
SOURCER_F = config.DEAL_SOURCER_FIELDS[0][0]     # «Сорсер» сделки
HEAD_F = config.DEAL_SOURCER_FIELDS[1][0]        # «Head of sourcing departement» — заполнен у 100 %
KAM_F = "UF_CRM_1740390857"                      # «КАМ»
# Линия продукт-оунера — «Категория оборудования» сделки (решение владельца 19.10.2026);
# зонд v55: в воронке «Пресейл» заполнена у 96 % открытых, в реализации — у 2 %.
CAT_F = "UF_CRM_1781862699536"                   # «Категория оборудования» (список, множественный)
PO_F = "UF_CRM_1779187425"                       # «Product leader»
DEAL_SELECT = ["ID", "TITLE", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE", "MOVED_TIME",
               "ASSIGNED_BY_ID", "CREATED_BY_ID", "COMPANY_ID", "OPPORTUNITY", SOURCER_F, HEAD_F, KAM_F,
               chat_activity.CHAT_FIELD, CAT_F, PO_F]
CAT_NONE = "категория не указана"
KAM_NONE = "КАМ не указан"

# НА КОМ МЯЧ (решение владельца 09.10.2026): сделка пресейла — сделка КАМа, но по
# стадии видно, кто делает следующий шаг: руководитель сорсинга (назначить сорсера),
# сорсер (ТЗ, поиск, КП, экономика), КАМ (подача, тендер, решение заказчика) или
# реализация (выиграна). Таблицей, а не ветвлениями (правило 4): правка разметки —
# одна строка. Ищется по имени стадии, первое совпадение побеждает. Стадия, не
# попавшая ни в одно правило, — «не размечена» и видна счётчиком: молча на КАМа
# она не ложится. Две стадии размечены без подтверждения владельца: «Новый запрос»
# (руководитель сорсинга) и «Техническая часть | Квалификация» (КАМ).
BALL_RULES = (
    (r"новый запрос|назначени\w* сорсер", "head"),
    (r"проработк|поиск поставщ|сбор кп|анализ кп|фиксаци\w* поставщ|сравнени\w* предлож|расч[её]т эп|доработк", "src"),
    (r"экономика готова|можно подавать|техническ\w* част|квалификац|тендерн|переторжк|торги"
     r"|ожидаем решени|сорсинг заверш|согласовани\w* финальн", "kam"),
)
_BALL = [(re.compile(rx, re.I), who) for rx, who in BALL_RULES]
BALL_LABEL = {"head": "руководитель сорсинга", "src": "сорсер", "kam": "КАМ", "real": "реализация",
              "?": "стадия не размечена", "": "закрыта"}
SOURCING_BALL = ("head", "src")


def ball_of(stage_name: str, sem: str = "P") -> str:
    """Чей ход по стадии: head | src | kam | real | ? (не размечена) | '' (закрыта отказом)."""
    sem = (sem or "P").upper()
    if sem == "S":
        return "real"
    if sem == "F":
        return ""
    for rx, who in _BALL:
        if rx.search(stage_name or ""):
            return who
    return "?"
# Пороги — по первому месяцу воронки (медиана «Поиска поставщиков» 11 дней, прочих
# рабочих стадий 3–7): две недели без смены стадии — уже не поиск, а простой.
ЗАСТОЙ_ДНЕЙ = 14

# Регламент воронки «Пресейл» — инструкция сорсера v1.4 (29.10.2026): кто закрывает стадию и норма
# в РАБОЧИХ днях по календарю РФ (period.workdays_ru). Стадия узнаётся по подписи; у стадий без
# нормы — только медиана и P80, вердикта «дольше нормы» нет (решение по умолчанию 30.10.2026).
# «Поиск поставщиков и сбор КП» — две задачи по 1 р.д. (разослать запросы, собрать КП): норма стадии 2.
REG_STAGES = [
    (re.compile(r"(?i)новый\s+запрос"), None, "КАМ"),
    (re.compile(r"(?i)назначение\s+сорсера"), None, "руководитель сорсинга"),
    (re.compile(r"(?i)проработка\s+тз"), 2, "сорсер"),
    (re.compile(r"(?i)поиск\s+поставщиков"), 2, "сорсер"),
    (re.compile(r"(?i)анализ\s+кп"), 3, "сорсер"),
    (re.compile(r"(?i)сравнение\s+предложений"), 2, "сорсер"),
    (re.compile(r"(?i)экономика\s+готова"), None, "КАМ"),
    (re.compile(r"(?i)доработка\s+по\s+обратной"), 1, "сорсер"),
    (re.compile(r"(?i)сорсинг\s+заверш"), None, "КАМ"),
    (re.compile(r"(?i)согласование\s+финальной"), None, "продукт"),
]


# КП → ТКП по ИСТОРИИ стадий (зонд v62, 30.10.2026): ТКП отправлено = вход в «Сорсинг завершен /
# Ожидаем решение заказчика» или дальше («Согласование финальной спецификации», успех); экономика
# готова = вход в «Экономика готова | Можно подавать». Прежний счёт по текущей стадии
# (stages.deal_reached_tkp) не видел 16 из 16 таких сделок и считал 21 лишнюю — он скрыт (TKP_HIDDEN).
ECON_RX = re.compile(r"(?i)экономика\s+готова")
TKP_RX = re.compile(r"(?i)сорсинг\s+заверш|ожида\w*\s+решени|согласование\s+финальной")


def tkp_stage_ids(stage_meta: dict[str, dict], cid: str) -> tuple[set[str], set[str]]:
    """(стадии «экономика готова», стадии «ТКП отправлено» и дальше, включая успех) воронки cid."""
    econ, tkp = set(), set()
    for sid, m in stage_meta.items():
        if str(m.get("cat")) != str(cid):
            continue
        name = str(m.get("name") or "")
        if ECON_RX.search(name):
            econ.add(sid)
        elif TKP_RX.search(name) or m.get("sem") == "S":
            tkp.add(sid)
    return econ, tkp


def reg_of(stage_name: str) -> tuple[int | None, str]:
    """(норма в р.д. или None, кто закрывает) — по подписи стадии; не из регламента — (None, '')."""
    for rx, norm, who in REG_STAGES:
        if rx.search(stage_name or ""):
            return norm, who
    return None, ""
# первый запрос поставщику уходит в среднем через 2 дня после сделки
БЕЗ_ЗАПРОСА_ДНЕЙ = 3
# строк «Требует действия» на странице (вес страницы); полное число — head.actN
ACT_SHOWN = 60


def find_category(category_names: dict[str, str]) -> tuple[str, str] | None:
    """(номер, имя) воронки пресейла по имени; None — воронки нет."""
    for cid, name in sorted(category_names.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        if PRESALE_RE.search(name or ""):
            return str(cid), name
    return None


def _date(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def _uid(v) -> str:
    if isinstance(v, (list, tuple)):
        v = next((x for x in v if str(x or "") not in ("", "0")), "")
    u = str(v or "")
    return "" if u in ("", "0", "None") else u


def _median(vals: list) -> float | None:
    s = sorted(v for v in vals if v is not None)
    if not s:
        return None
    n = len(s)
    return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 1)


def _pct(a: int, b: int) -> int:
    return round(a / b * 100) if b else 0


def _cats(v, labels: dict[str, str]) -> list[str]:
    """Значение поля-списка → подписи категорий; удалённое из справочника значение —
    «значение #id» (видно, что разметить заново), пусто — []."""
    ids = v if isinstance(v, list) else [v]
    out = []
    for x in ids:
        s = str(x or "").strip()
        if s and s != "0":
            lbl = labels.get(s) or f"значение #{s}"
            if lbl not in out:
                out.append(lbl)
    return out


def po_lines(rows: list[dict]) -> dict:
    """Линии продукт-оунеров по «Категории оборудования» сделок пресейла.
    Сделка с несколькими категориями входит в каждую (итог по линиям больше числа
    сделок — так и подписано). «Стоят» — простой, подтверждённый тишиной в чате."""
    lines: dict[str, dict] = {}
    for r in rows:
        for c in (r.get("cats") or [CAT_NONE]):
            # «выиграно» не считается: выигранная сделка пресейла уходит в сделку
            # реализации, стадии успеха в пресейле не ставят (живая сверка 19.10:
            # 0 из 20 линий) — колонка из одних нулей только путала бы
            a = lines.setdefault(c, {"cat": c, "n": 0, "open": 0, "lost": 0, "stale": 0,
                                     "ballKam": 0, "ballSrc": 0, "noPo": 0, "_po": defaultdict(int)})
            a["n"] += 1
            if r["open"]:
                a["open"] += 1
                a["stale"] += "stale" in r["flags"]
                a["ballKam"] += r["ball"] == "kam"
                a["ballSrc"] += r["ball"] in SOURCING_BALL
                if r.get("poId"):
                    a["_po"][r["po"] + (" (уволен)" if r.get("poGone") else "")] += 1
                else:
                    a["noPo"] += 1
            else:
                a["lost"] += r["sem"] == "F"
    out = []
    for a in lines.values():
        po = a.pop("_po")
        a["pos"] = [{"name": k, "n": v} for k, v in sorted(po.items(), key=lambda kv: -kv[1])]
        out.append(a)
    out.sort(key=lambda a: (a["cat"] == CAT_NONE, -a["open"], -a["n"]))
    with_cat = sum(1 for r in rows if r.get("cats"))
    return {"lines": out, "deals": len(rows), "withCat": with_cat, "withCatPct": _pct(with_cat, len(rows)),
            "multi": sum(1 for r in rows if len(r.get("cats") or []) > 1)}


def compute(*, cid: str, cat_name: str, deals: list[dict], stage_meta: dict[str, dict],
            rfqs: list[dict], people: dict[str, dict] | None, names: dict[str, str],
            today: dt.date, service_ids: set[str] | None = None,
            chat_reader: chat_activity.Reader | None = None,
            cat_labels: dict[str, str] | None = None,
            chat_also: Callable[[dict], bool] | None = None,
            history: dict[str, list[tuple[str, str]]] | None = None) -> dict:
    """history — первые входы сделок в стадии {ID: [(STAGE_ID, время ISO), …]} (client.stage_history
    по стадиям «экономика готова» и «ТКП отправлено»); None — история не прочитана: КП → ТКП не
    считается (полей нет), а не «ноль».
    chat_reader — общий на сборку читатель чатов: простой по стадии проверяется
    чатом сделки (золотое правило, см. шапку). None — без чата, как до 19.10.2026.
    chat_also(строка) — открытые сделки, чей чат прочитать сверх простоя по стадии
    (очередь КАМа: kamqueue.chat_wanted); флагов им не меняет, только пишет chat."""
    people = people or {}
    service = set(service_ids or ())
    stages = sorted(((sid, m) for sid, m in stage_meta.items() if str(m.get("cat")) == str(cid)),
                    key=lambda kv: kv[1].get("sort", 0))
    sem_of = {sid: m.get("sem", "P") for sid, m in stages}

    by_deal: dict[str, list[dict]] = defaultdict(list)
    for r in rfqs:
        p = str(r.get("parentId2") or "")
        if p:
            by_deal[p].append(r)

    def person(uid: str) -> tuple[str, bool]:
        if not uid:
            return "", False
        p = people.get(uid) or {}
        gone = p.get("active") is False
        return (p.get("name") or names.get(uid) or f"user#{uid}"), gone

    econ_ids, tkp_ids = tkp_stage_ids(stage_meta, cid)

    def entered(did: str, sid: str, ids: set[str]) -> str:
        """Дата первого входа сделки в любую из стадий ids по истории; сейчас в такой стадии, а
        входа в выдаче нет (история урезана) — «сейчас»; не входила — ''."""
        ts = [t for s, t in (history or {}).get(did, []) if s in ids and t]
        if ts:
            return min(ts)[:10]
        return today.isoformat() if sid in ids else ""

    rows = []
    for d in deals:
        did = str(d.get("ID"))
        sid = str(d.get("STAGE_ID") or "")
        sem = sem_of.get(sid) or (str(d.get("STAGE_SEMANTIC_ID") or "P").upper())
        created = _date(d.get("DATE_CREATE"))
        moved = _date(d.get("MOVED_TIME")) or created
        mine = by_deal.get(did, [])
        firsts = [t for r in mine if (t := _date(r.get("createdTime")))]
        src_uid = _uid(d.get(SOURCER_F))
        src, src_gone = person(src_uid)
        # КАМ — только поле «КАМ»: ответственный сделки — исполнитель, а не КАМ
        kam_uid = _uid(d.get(KAM_F))
        kam, kam_gone = person(kam_uid)
        head, head_gone = person(_uid(d.get(HEAD_F)))
        po_uid = _uid(d.get(PO_F))
        po, po_gone = person(po_uid)
        cats = _cats(d.get(CAT_F), cat_labels or {})
        open_ = sem == "P"
        stage_name = (stage_meta.get(sid) or {}).get("name") or sid
        ball = ball_of(stage_name, sem)
        # кто держит мяч поимённо: сорсер без назначенного сорсера — руководитель
        who, who_gone = {"head": (head, head_gone), "kam": (kam, kam_gone) if kam_uid else (KAM_NONE, False),
                         "src": (src, src_gone) if src_uid else (head, head_gone)}.get(ball, ("", False))
        age = (today - created).days if created else None
        flags = []
        on_src = ball in SOURCING_BALL
        # действия сорсинга — только пока мяч у сорсинга; у КАМа — только застой
        if open_ and on_src and not src_uid:
            flags.append("nosrc")
        if open_ and on_src and src_gone:
            flags.append("gone")
        if open_ and on_src and not mine and age is not None and age >= БЕЗ_ЗАПРОСА_ДНЕЙ:
            flags.append("norfq")
        days = (today - moved).days if moved else None
        # регламент: рабочих дней на стадии (после дня входа по MOVED_TIME, по сегодня — как дожим
        # поставщика wdf) против нормы: вошла в пн с нормой 2 — срок ср, в чт уже «дольше нормы»
        norm, closer = reg_of(stage_name)
        wd = period.workdays_ru(moved, today) if moved and moved < today else (0 if moved else None)
        if open_ and days is not None and days > ЗАСТОЙ_ДНЕЙ:
            flags.append("stale")
        rows.append({
            "id": did, "t": str(d.get("TITLE") or f"Сделка #{did}")[:90],
            "stage": sid, "stageName": stage_name,
            "sem": sem, "open": open_, "days": days, "age": age,
            "ball": ball, "who": who, "whoGone": who_gone,
            "created": created.isoformat() if created else "",
            "src": src, "srcId": src_uid, "srcGone": src_gone,
            "kam": kam or KAM_NONE, "kamId": kam_uid, "kamGone": kam_gone,
            "rfq": len(mine), "quotes": sum(1 for r in mine if r.get("_hasQuote")),
            "lag": (min(firsts) - created).days if firsts and created else None,
            "sum": float(d.get("OPPORTUNITY") or 0) > 0,
            "company": str(d.get("COMPANY_ID") or "0") not in ("", "0"),
            "flags": flags, "chat": None,
            "wd": wd, "norm": norm, "closer": closer, "over": bool(open_ and norm is not None and wd is not None and wd > norm),
            "cats": cats, "po": po, "poId": po_uid, "poGone": po_gone,
            "_chat": chat_activity.chat_id(d),
            **({"econAt": entered(did, sid, econ_ids), "tkpAt": entered(did, sid, tkp_ids)}
               if history is not None else {}),
        })

    # ЗОЛОТОЕ ПРАВИЛО ЧАТА: простой по стадии подтверждает только тишина в чате.
    # Порядок чтения — дольше всех стоящие первыми: сумма у пресейла заполнена у 4 %
    # сделок, весом по деньгам здесь не упорядочить.
    stale_stage = sum(1 for r in rows if "stale" in r["flags"])
    if chat_reader is not None:
        cands = [(r["id"], r["_chat"], float(r["days"] or 0)) for r in rows
                 if "stale" in r["flags"] or (chat_also is not None and r["open"] and chat_also(r))]
        res = chat_reader.check(cands, today) if cands else {}
        for r in rows:
            ch = res.get(r["id"])
            if not ch:
                continue
            r["chat"] = {k: ch.get(k) for k in ("status", "days", "last")}
            if "stale" not in r["flags"] or ch["status"] == "quiet":
                continue
            нов = "staleChat" if ch["status"] == "active" else "staleUnverified"
            r["flags"] = [нов if f == "stale" else f for f in r["flags"]]
    for r in rows:
        r.pop("_chat", None)

    open_rows = [r for r in rows if r["open"]]
    # стадии: рабочие по порядку воронки, затем исходы
    st_rows = []
    for sid, m in stages:
        here = [r for r in rows if r["stage"] == sid]
        st_rows.append({"id": sid, "name": m.get("name") or sid, "sem": m.get("sem", "P"), "n": len(here),
                        "ball": ball_of(m.get("name") or sid, m.get("sem", "P")),
                        "med": _median([r["days"] for r in here]),
                        "max": max((r["days"] for r in here if r["days"] is not None), default=None),
                        "stale": sum(1 for r in here if "stale" in r["flags"]),
                        # регламент: кто закрывает, норма, рабочие дни на стадии и сколько дольше нормы
                        "norm": reg_of(m.get("name") or sid)[0], "closer": reg_of(m.get("name") or sid)[1],
                        "wdMed": _median([r["wd"] for r in here if r["wd"] is not None]),
                        "wdMax": max((r["wd"] for r in here if r["wd"] is not None), default=None),
                        "over": sum(1 for r in here if r["over"])})
    known = {sid for sid, _ in stages}
    чужие = sum(1 for r in rows if r["stage"] not in known)

    # заведено по неделям (Пн–Вс), последние 8 недель, с исходом
    monday = today - dt.timedelta(days=today.weekday())
    weeks = []
    for i in range(7, -1, -1):
        a = monday - dt.timedelta(days=7 * i)
        b = a + dt.timedelta(days=6)
        here = [r for r in rows if r["created"] and a.isoformat() <= r["created"] <= b.isoformat()]
        weeks.append({"from": a.isoformat(), "n": len(here),
                      "lost": sum(1 for r in here if r["sem"] == "F"),
                      "won": sum(1 for r in here if r["sem"] == "S")})

    # по сорсеру сделки
    agg: dict[str, dict] = {}
    for r in rows:
        k = r["srcId"] or ""
        a = agg.setdefault(k, {"id": k, "n": r["src"] or "сорсер не назначен", "gone": r["srcGone"],
                               "open": 0, "won": 0, "lost": 0, "rfq": 0, "quotes": 0,
                               "norfq": 0, "stale": 0})
        a["open" if r["open"] else ("won" if r["sem"] == "S" else "lost")] += 1
        a["rfq"] += r["rfq"]
        a["quotes"] += r["quotes"]
        a["norfq"] += "norfq" in r["flags"]
        a["stale"] += "stale" in r["flags"]
        if history is not None and r["quotes"]:
            # КП → ТКП: сделки сорсера с КП файлом — дошли до «Экономика готова» и до «ТКП отправлено»
            a["kp"] = a.get("kp", 0) + 1
            a["kpEcon"] = a.get("kpEcon", 0) + bool(r["econAt"] or r["tkpAt"])
            a["kpTkp"] = a.get("kpTkp", 0) + bool(r["tkpAt"])
    by_src = sorted(agg.values(), key=lambda a: (a["id"] == "", -a["open"], -a["rfq"]))

    with_rfq = [r for r in rows if r["rfq"]]
    # «в чате идёт работа» — не действие: такая сделка в списке, только если с ней
    # есть что делать ещё (нет сорсера, нет запросов)
    act = [r for r in open_rows if any(f != "staleChat" for f in r["flags"])]
    order = {"gone": 0, "nosrc": 1, "norfq": 2, "stale": 3, "staleUnverified": 4, "staleChat": 5}
    act.sort(key=lambda r: (min(order[f] for f in r["flags"]), -(r["days"] or 0)))
    lost = [{"name": s["name"], "n": s["n"]} for s in st_rows if s["sem"] == "F" and s["n"]]
    lost.sort(key=lambda x: -x["n"])
    since28 = (today - dt.timedelta(days=27)).isoformat()
    return {
        "cid": str(cid), "name": cat_name,
        "head": {
            "total": len(rows), "open": len(open_rows),
            "won": sum(1 for r in rows if r["sem"] == "S"),
            "lost": sum(1 for r in rows if r["sem"] == "F"),
            "new28": sum(1 for r in rows if r["created"] >= since28),
            "withRfq": len(with_rfq), "withRfqPct": _pct(len(with_rfq), len(rows)),
            "rfq": sum(r["rfq"] for r in rows),
            "rfqMed": _median([r["rfq"] for r in with_rfq]),
            # запросы, заведённые служебной записью (роботом пресейла)
            "robotRfqPct": _pct(sum(1 for r in rows for x in by_deal.get(r["id"], [])
                                    if str(x.get("createdBy") or "") in service),
                                sum(r["rfq"] for r in rows)),
            "lagMed": _median([r["lag"] for r in rows if r["lag"] is not None]),
            "withQuotes": sum(1 for r in rows if r["quotes"]),
            "noSrc": sum(1 for r in open_rows if "nosrc" in r["flags"]),
            "gone": sum(1 for r in open_rows if "gone" in r["flags"]),
            "noRfq": sum(1 for r in open_rows if "norfq" in r["flags"]),
            "stale": sum(1 for r in open_rows if "stale" in r["flags"]),
            # золотое правило чата: стоят по стадии = тихо + работа в чате + не проверен
            "staleChat": sum(1 for r in open_rows if "staleChat" in r["flags"]),
            "staleUnverified": sum(1 for r in open_rows if "staleUnverified" in r["flags"]),
            "staleStage": stale_stage,
            "chatGate": chat_reader is not None,
            "sumPct": _pct(sum(1 for r in rows if r["sum"]), len(rows)),
            "srcPct": _pct(sum(1 for r in rows if r["srcId"]), len(rows)),
            "otherStage": чужие,
            # на ком мяч по открытым сделкам
            "ballHead": sum(1 for r in open_rows if r["ball"] == "head"),
            "ballSrc": sum(1 for r in open_rows if r["ball"] == "src"),
            "ballKam": sum(1 for r in open_rows if r["ball"] == "kam"),
            "ballUnknown": sum(1 for r in open_rows if r["ball"] == "?"),
            "staleDays": ЗАСТОЙ_ДНЕЙ, "noRfqDays": БЕЗ_ЗАПРОСА_ДНЕЙ,
            # полная длина списка «Требует действия» — до обрезки «act» до 60:
            # заголовок по обрезанному списку показывал 60 при любом числе сверх
            "actN": len(act),
            # КП → ТКП по истории стадий (нет истории — полей нет): сделки с КП файлом, из них дошли до
            # «Экономика готова» и до «ТКП отправлено»; сейчас на «Экономика готова» — ждут ТКП от КАМа
            **({"kp": sum(1 for r in rows if r["quotes"]),
                "kpEcon": sum(1 for r in rows if r["quotes"] and (r["econAt"] or r["tkpAt"])),
                "kpTkp": sum(1 for r in rows if r["quotes"] and r["tkpAt"]),
                "wait7": sum(1 for r in open_rows if r["stage"] in econ_ids)} if history is not None else {}),
        },
        "stages": st_rows,
        "poLines": po_lines(rows),
        "lost": lost,
        "weeks": weeks,
        "bySourcer": by_src,
        # таблица «Требует действия» на вкладке «Сорсинг» — первые ACT_SHOWN (вес
        # страницы); сколько всего — head.actN
        "act": act[:ACT_SHOWN],
        # все открытые сделки, без обрезки: очередь КАМа (kamqueue.py) берёт отсюда
        # мяч у КАМа и сделки без КАМа — по обрезанному «act» часть их терялась
        "openDeals": [{k: r[k] for k in ("id", "t", "stage", "stageName", "days", "ball", "who",
                                         "kam", "kamId", "kamGone", "flags", "chat", "created",
                                         "wd", "norm", "closer", "over", "src", "srcId", "srcGone")}
                      for r in sorted(open_rows, key=lambda r: -(r["days"] or 0))],
        # сделки с КП файлом (и открытые, и закрытые) — списки за числами «КП → ТКП»
        **({"kpDeals": [{"id": r["id"], "t": r["t"], "stageName": r["stageName"], "sem": r["sem"],
                         "srcId": r["srcId"], "src": r["src"], "srcGone": r["srcGone"], "quotes": r["quotes"],
                         "econ": r["econAt"], "tkp": r["tkpAt"]}
                        for r in sorted(rows, key=lambda r: (r["tkpAt"] or "9", r["id"])) if r["quotes"]]}
           if history is not None else {}),
    }
