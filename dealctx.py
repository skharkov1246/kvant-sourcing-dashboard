"""Чат и договор сделки во вкладке «Реализация» — решение владельца 23.10.2026
(макет «Реализация: чаты и договоры» одобрен как есть).

ОТКУДА. Оценки хода сделки по чату (scripts/chat_context.py → deal_chat_ctx) и разбор
договоров (scripts/contract_terms.py → contract_terms, contract_b2b_flags) живут в
закрытой базе. Прогоны, которые их пишут, кладут свод по сделкам в закрытый ключ KV
realctx:v1 (scripts/realization_ctx.py --publish); сборка дашборда читает его в файл
на раннере (deploy.yml → --fetch), путь — REALCTX_PATH. Нет файла — вкладка такая же,
как была: ни плиток, ни колонок, «подвисла» — по тишине в чате.

ЧТО ДЕЛАЕТ.
  1. «Подвисла» (decide). Кандидат — простой по стадии, как раньше; решает чат:
       тихо QUIET_DAYS+ дней — подвисла (chat_activity, как было);
       пишут, но по текущей оценке ход за нами («ждёт_нас»), никто не двигает
       («стоит») или откат («назад») — подвисла;
       сборка чат не прочла, а прогон оценки читал его не раньше FRESH_DAYS назад —
       решает он: тишина — по последнему сообщению человека, иначе — по оценке;
       иначе — «чат не проверен», как было.
     Оценка ТЕКУЩАЯ, если прогон оценки читал чат не раньше FRESH_DAYS назад и после
     оценки в чате не писал человек — ни по данным прогона, ни по чтению этой сборки:
     старая оценка свежую переписку не судит.
  2. Числа плиток (build → head) — видят все, у кого есть вкладка.
  3. Закрытая часть (build → payload): по сделке — оценка чата, свод договоров, что
     делать первым; пункты очереди. Уходит отдельным массивом страницы DEALCTX: воркер
     оставляет его только праву «Поставщики: договоры и деньги» и админу
     (access/acl.js, RIGHT_PAYLOADS; решение владельца 19.10.2026).
  4. Финальная экономика из файла «Экономика проекта» (решение владельца 23.10.2026;
     scripts/econ_files.py → econ_file_reads → свод econ): только сверенные файлы. В
     карточке сделки — продажа в валюте подачи, закупка в €, «наценка над поставщиком»
     (не маржа: логистика и таможня внутри коэффициента), дата чтения, переторжка. «Сумма»
     сделки без НДС (OPPORTUNITY − TAX_VALUE) сверяется с продажей файла без НДС в валюте
     подачи (econ_ratio) на день сборки: больше ECON_GAP — пункт очереди ответственному
     сделки (econ_verdict). Пункт при любом расхождении: у файла с переторжкой «Сумма»
     равна продаже «Нашего КП» до неё («до переторжки»). Не пункт: суммы в сделке нет
     («суммы нет» — внести её решит владелец), отношение около 1,20 или 1,22 при пустом
     налоге сделки («≈ с НДС»: НДС не вычтен — до решения владельца; налог указан — он
     вычтен, и 1,20 — настоящее расхождение), «Сумма» менялась после чтения файла
     (отношение сборки не то, что записал прогон чтения: сверка — после перечтения, а
     прогон чтения такие сделки перечитывает первыми). На день чтения суммы не было, а
     теперь есть — не «менялась»: сверяется прямо. Числа файла — только в DEALCTX.

Пороги — таблицей (CLAUDE.md, правило 4). Текста чатов и договоров здесь нет: в ключе
только номера сделок, даты и значения из закрытых списков.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
from collections import Counter

import chat_activity
import contract_b2b

VERSION = 1
FRESH_DAYS = 7          # прогон оценки читал чат не раньше — иначе его данные не основание
REPLY_HOURS = 24        # «ждут ответа дольше рабочего дня»: часы будних дней с сообщения
DUE_MAX_DAYS = 60       # срок из чата, прошедший давнее, — история, а не пункт очереди
ECON_GAP = 0.10         # «Сумма» сделки и продажа файла расходятся больше — пункт очереди
ECON_OTHER_CUR = "валюта сделки другая"   # в файле нет курса валюты сделки — не сверить
ECON_NO_SUM = "суммы нет"                 # «Суммы» в сделке нет — не сверить, а не «расходится на 100 %»
ECON_CHANGED = "сумма менялась после чтения файла"   # сверка — после перечтения файла
# «Сумма» с НДС при нетто-продаже файла: ставки 20 % и 22 % (с 2026 г.), ±1 п.п. — «≈ с
# НДС», не расхождение до решения владельца (прогон чтения считает такие отдельно)
ECON_VAT = (1.20, 1.22)
ECON_VAT_TOL = 0.01
ECON_KP_TOL = 0.005     # «Сумма» = продажа «Нашего КП» до переторжки — в пределах 0,5 %
# вердикт сверки «Суммы» с файлом (econ_verdict)
ECON_OK = "сходится"
ECON_VAT_LIKE = "≈ с НДС"
ECON_PRE_REBID = "до переторжки"
ECON_OVER = "расходится"
MSK = dt.timezone(dt.timedelta(hours=3))

# Закрытые списки оценки — дословно как в scripts/chat_context.py (сверяет тест).
STATES = ("идёт", "ждёт_клиента", "ждёт_поставщика", "ждёт_нас", "стоит", "неясно")
DIRECTIONS = ("вперёд", "стоит", "назад", "неясно")
RISKS = ("низкий", "средний", "высокий")
RISK_KINDS = ("срок", "оплата", "техника", "документы", "цена", "отказ_клиента", "поставщик", "претензия")
BLOCKERS = ("ответ", "оплата", "документы", "техника", "цена", "поставка", "согласование", "другое")
ROLES = ("кам", "сорсер", "руководитель_сорсинга", "сопровождение", "логист", "ответственный",
         "клиент", "поставщик", "перевозчик", "неясно")
DUE_KINDS = ("отгрузка", "доставка", "оплата", "документы", "ответ", "подача", "встреча", "другое")
# Исход разбора договоров сделки — scripts/contract_terms.ИСХОДЫ (сверяет тест); «разошлось» —
# исход или флаги прогона не сошлись с пересчётом (scripts/realization_ctx.py)
OUTCOMES = ("пара", "недочитана", "не читается", "без стороны", "спорная", "поставщик без заказа", "нет пары",
            "нет договоров", "разошлось")
UNREADABLE = "не читается"                  # файла нет у портала, не прочесть, длиннее предела — нужен человек
NO_PAIR = ("нет пары", "нет договоров")    # исходы, где у сделки нет договора одной из сторон

OUR_ROLES = ("кам", "сорсер", "руководитель_сорсинга", "сопровождение", "логист", "ответственный")
STALL = {"ждёт_нас": "ход за нами", "стоит": "никто не двигает"}   # состояние → почему подвисла
BACK = "назад"
WAIT = {"ждёт_клиента": "ждём клиента", "ждёт_поставщика": "ждём поставщика"}
STATE_LBL = {"идёт": "идёт", "ждёт_клиента": "ждёт клиента", "ждёт_поставщика": "ждёт поставщика",
             "ждёт_нас": "ждёт нас", "стоит": "стоит", "неясно": "неясно"}
ROLE_LBL = {"кам": "КАМ", "сорсер": "сорсер", "руководитель_сорсинга": "руководитель сорсинга",
            "сопровождение": "сопровождение", "логист": "логист", "ответственный": "ответственный",
            "клиент": "клиент", "поставщик": "поставщик", "перевозчик": "перевозчик", "неясно": "неясно"}
DUE_ACC = {"отгрузка": "отгрузку", "доставка": "доставку", "оплата": "оплату", "документы": "документы",
           "ответ": "ответ", "подача": "подачу", "встреча": "встречу", "другое": "срок"}
# «Не найден», а не «нет»: договор ищется в карточках договоров (СП-1174/1192 и прежних
# СП-132/175), связанных со сделкой или с её заказом, в рамочных над её спецификациями и в
# Purchase order заказов (scripts/contract_terms.py, зонд v57) — его может не быть в
# портале, а не у сделки
SIDE_MISSING = "договор {whose} не найден: ни в карточках договоров сделки и её заказов, ни в Purchase order"
# короткие подписи флагов сверки для «что сделать»; полный текст — contract_b2b.FLAG_TEXT
FLAG_SHORT = {"lateByDesign": "срок", "thinBuffer": "запас по сроку", "basisGap": "базис",
              "cashGap": "оплата", "penaltyUncovered": "пени", "penaltyNoCap": "потолок пеней",
              "warrantyGap": "гарантия", "warrantyStart": "начало гарантии", "claimGap": "приёмка",
              "docsGap": "документы", "fxGap": "валюта", "cisg": "Венская конвенция"}
SIDE_LBL = {"customer": "заказчика", "supplier": "поставщика"}
CONTRACT_HEAD = "руководитель реализации"

# Пункты очереди — в форме lifecycle.EXC: код → (важность, что случилось, что сделать).
# Важность дробная: пункт встаёт между пунктами lifecycle по смыслу, порядок общий.
EXC = {
    "chatReply": (1.5, f"в чате ждут нашего ответа дольше {REPLY_HOURS} ч", "ответить в чате сделки"),
    "chatRisk": (2.5, "высокий риск по чату", "разобрать риск"),
    "chatDue": (3.5, "срок из чата прошёл", "подтвердить у второй стороны"),
    "contractRisk": (5.5, "договоры: риск сверки заказчик ↔ поставщик", "допсоглашение с поставщиком"),
    "contractMissing": (12.5, "договор стороны не найден", "найти договор и загрузить файл в карточку"),
    # сверка данных, а не срыв срока: рядом с «внести срок оплаты» (lifecycle noPayPlan)
    # подпись чипа — со строчной, как у соседних; текст пункта — словами решения владельца
    "econGap": (8.5, "сумма сделки расходится с финальной экономикой", "обновить «Сумму» сделки по финальному расчёту"),
    "contractUnreadable": (12.6, "файл договора не читается", "перезалить файл договора в карточку: целиком, "
                           "без пароля; длинный — частями"),
}


# ── чтение ключа ────────────────────────────────────────────────────────────────

def load(path: str | None = None) -> dict | None:
    """Свод из файла, который сборка кладёт из KV (deploy.yml → realization_ctx.py --fetch).
    Нет пути, файла или формы — None: вкладка собирается без чатов и договоров."""
    path = path if path is not None else os.environ.get("REALCTX_PATH")
    if not path:
        return None
    try:
        raw = json.loads(open(path, encoding="utf-8").read())
    except Exception:
        return None
    return clean(raw)


def _ts(v) -> dt.datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = dt.datetime.fromisoformat(v)
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).astimezone(MSK)


def _day(v) -> dt.date | None:
    if not isinstance(v, str) or len(v) != 10:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _chat(v) -> dict | None:
    """Чат сделки из ключа: когда читан и когда писал человек; оценка — если была.
    Значение не из закрытого списка — запись целиком отбрасывается."""
    if not isinstance(v, dict):
        return None
    checked = _ts(v.get("checked"))
    if checked is None:
        return None
    out = {"checked": checked, "human": _ts(v.get("human")), "at": _ts(v.get("at"))}
    if out["at"] is None:
        return out
    kinds = v.get("kinds")
    blocker = v.get("blocker")
    if (v.get("state") not in STATES or v.get("direction") not in DIRECTIONS or v.get("risk") not in RISKS
            or v.get("ball") not in ROLES or not isinstance(v.get("reply"), bool)
            or not isinstance(kinds, list) or any(k not in RISK_KINDS for k in kinds)):
        return None
    if blocker is not None and not (isinstance(blocker, dict) and blocker.get("kind") in BLOCKERS
                                    and blocker.get("on") in ROLES):
        return None
    due = _day(v.get("due")) if v.get("due") is not None else None
    if v.get("due") is not None and (due is None or v.get("dueKind") not in DUE_KINDS):
        return None
    conf = v.get("conf")
    if conf is not None and not (isinstance(conf, (int, float)) and not isinstance(conf, bool) and 0 <= conf <= 1):
        return None
    out.update(state=v["state"], direction=v["direction"], risk=v["risk"], kinds=list(kinds)[:3],
               ball=v["ball"], reply=v["reply"], due=due, dueKind=v.get("dueKind") if due else None,
               blocker=blocker, conf=conf)
    return out


def _contract(v) -> dict | None:
    if not isinstance(v, dict) or v.get("outcome") not in OUTCOMES or _ts(v.get("at")) is None:
        return None
    read = v.get("read") or {}
    flags = []
    for f in v.get("flags") or []:
        if not isinstance(f, dict) or f.get("code") not in contract_b2b.FLAG_TEXT:
            return None
        flags.append({"code": f["code"], "order": _int(f.get("order")), "days": _int(f.get("days"))})
    return {"at": _ts(v["at"]), "outcome": v["outcome"],
            "read": {s: max(0, _int(read.get(s)) or 0) for s in SIDE_LBL},
            "flags": flags if v["outcome"] == "пара" else []}


_CUR = re.compile(r"^[A-Z]{3}$")


def _pos(v) -> float | None:
    """Конечное положительное число или None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        return None
    return float(v)


def _econ(v) -> dict | None:
    """Финальная экономика сделки из ключа — только сверенный файл (scripts/realization_ctx.py
    берёт лишь статус «сверено»): числа положительны, продажа не ниже закупки, наценка
    согласована с ними, курсы — «код валюты → число». ratio и note — сверка с «Суммой» на
    день чтения (econ_file_reads.bitrix_ratio, bitrix_note); в ключе до 25.10.2026 их нет.
    kpSale — у переторжки продажа «Нашего КП» в валюте подачи (kp_sale_sub); без переторжки
    её нет."""
    if not isinstance(v, dict):
        return None
    ratio, note = v.get("ratio"), v.get("note")
    if (ratio is not None and (isinstance(ratio, bool) or not isinstance(ratio, (int, float))
                               or not math.isfinite(ratio) or ratio < 0)) \
            or note not in (None, ECON_OTHER_CUR, ECON_NO_SUM):
        return None
    at, sale, se, buy = _ts(v.get("at")), _pos(v.get("sale")), _pos(v.get("saleEur")), _pos(v.get("buyEur"))
    cur, mk, items, fx = v.get("cur"), v.get("markup"), _int(v.get("items")), v.get("fx") or {}
    kp = v.get("kpSale")
    if kp is not None and (_pos(kp) is None or v.get("rebid") is not True):
        return None
    if (at is None or sale is None or se is None or buy is None or se < buy
            or not (isinstance(cur, str) and _CUR.match(cur)) or not isinstance(v.get("rebid"), bool)
            or not items or items <= 0 or isinstance(mk, bool) or not isinstance(mk, (int, float))
            or not math.isfinite(mk) or abs(mk - (se / buy - 1)) > 1e-4 or not isinstance(fx, dict)
            or any(not (isinstance(k, str) and _CUR.match(k)) or _pos(x) is None for k, x in fx.items())):
        return None
    out = {"at": at, "sale": sale, "cur": cur, "saleEur": se, "buyEur": buy, "markup": float(mk),
           "rebid": v["rebid"], "items": items, "fx": {k: float(x) for k, x in fx.items()}}
    if kp is not None:
        out["kpSale"] = float(kp)
    if "ratio" in v:
        out["ratioAtRead"] = float(ratio) if ratio is not None else None
        out["noteAtRead"] = note
    return out


def clean(raw) -> dict | None:
    """Ключ — данные, а не код: запись не по форме отбрасывается и считается (bad).
    Раздела econ в ключе до 23.10.2026 нет — пустой словарь."""
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        return None
    chats, contracts, econ, bad = {}, {}, {}, 0
    for src, dst, fn in ((raw.get("chats"), chats, _chat), (raw.get("contracts"), contracts, _contract),
                         (raw.get("econ"), econ, _econ)):
        for k, v in (src or {}).items() if isinstance(src, dict) else ():
            x = fn(v)
            if x is None or not str(k).isdigit():
                bad += 1
                continue
            dst[str(k)] = x
    return {"at": _ts(raw.get("at")), "chats": chats, "contracts": contracts, "econ": econ, "bad": bad}


def _num(v) -> float | None:
    """Поле суммы Битрикса (строка или число) → число; пусто — 0; не число — None."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return 0.0
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def econ_vat(ratio) -> bool:
    """Отношение «Сумма» / продажа файла — около 1,20 или 1,22: похоже на «Сумму» с НДС. Само
    по себе не довод: при указанном налоге НДС уже вычтен (econ_verdict)."""
    return (isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and math.isfinite(ratio)
            and any(abs(round(ratio, 6) - v) <= ECON_VAT_TOL + 1e-9 for v in ECON_VAT))


def econ_verdict(ratio: float, *, tax_empty: bool, sale=None, kp_sale=None) -> str:
    """Отношение «Сумма» без НДС / продажа файла → вердикт сверки (ECON_OK и т. д.).

      • у файла с переторжкой «Сумма» равна продаже «Нашего КП» (kp_sale, в пределах
        ECON_KP_TOL), а продажа переторжки от неё отличается — ECON_PRE_REBID: «Сумму» не
        обновили после переторжки; пункт при любом расхождении;
      • расхождение не больше ECON_GAP — ECON_OK;
      • налога в сделке нет (tax_empty), а отношение около 1,20 или 1,22 — ECON_VAT_LIKE:
        НДС, похоже, не вычтен; не пункт до решения владельца. Налог указан — он уже вычтен,
        и такое отношение — настоящее расхождение;
      • иначе — ECON_OVER.
    Один вердикт у сборки (_econ_cell) и у журнала прогона чтения (scripts/econ_files.py)."""
    d = round(abs(ratio - 1), 6)
    s, kp = _pos(sale), _pos(kp_sale)
    if s and kp and d > ECON_KP_TOL and abs(ratio * s / kp - 1) <= ECON_KP_TOL:
        return ECON_PRE_REBID
    if d <= ECON_GAP:
        return ECON_OK
    if tax_empty and econ_vat(ratio):
        return ECON_VAT_LIKE
    return ECON_OVER


def econ_changed(ratio, note, before, before_note) -> bool:
    """Сверка «Суммы» с файлом сегодня не та, что записал прогон чтения: отношение другое (с
    точностью 1e-4) или «не сверить» стало другим. По нему прогон чтения ставит сделку в
    очередь первой (scripts/econ_files.очередь)."""
    if ratio is None or before is None:
        return (ratio is None) != (before is None) or (note or None) != (before_note or None)
    return abs(ratio - before) > 1e-4 * max(1.0, before)


def econ_stale(e: dict, ratio, note) -> bool:
    """Сверка «Суммы» с файлом сегодня (econ_ratio) не та, что на день чтения (econ_changed):
    «Сумму» меняли после чтения, файл могли обновить вместе с ней — до перечтения файлу не
    верим. Сверки сегодня нет (ratio None), на день чтения суммы не было («суммы нет») или
    ключ до 25.10.2026 без сверки на день чтения — не устаревание. Одно правило у карточки
    (_econ_cell) и у продажи «Реализации» (contracts.compute: файл первым)."""
    return (ratio is not None and "ratioAtRead" in e
            and not (e["ratioAtRead"] is None and e.get("noteAtRead") == ECON_NO_SUM)
            and econ_changed(ratio, note, e["ratioAtRead"], e.get("noteAtRead")))


def econ_ratio(amount, deal_cur: str, e: dict, tax=None) -> tuple[float | None, str]:
    """«Сумма» сделки без НДС в валюте подачи / продажа файла без НДС — или (None, почему не
    сверить).

    Сравнение — в валюте подачи (решение владельца 23.10.2026) и без НДС (правило «сравниваем
    без НДС»): «Сумма» — OPPORTUNITY − TAX_VALUE (tax; пусто — 0). Валюта сделки — валюта
    подачи: как есть; евро — по курсу самого файла (продажа в валюте подачи / продажа в €);
    другая — по курсам файла (e["fx"]: единиц валюты подачи за 1 единицу); курса нет —
    «валюта сделки другая». Суммы в сделке нет (пусто или 0) — «суммы нет»: сверять не с
    чем, это не «расходится на 100 %». Сумма или налог не число, налог отрицателен или не
    меньше суммы — сверки нет. Тот же расчёт делает прогон чтения (scripts/econ_files.py)
    на день чтения."""
    sale = _pos(e.get("sale"))
    if sale is None:
        return None, ""
    a = _num(amount)
    if a is None:
        return None, ""
    if a <= 0:
        return None, ECON_NO_SUM
    t = _num(tax)
    if t is None or t < 0 or t >= a:
        return None, ""
    amount = a - t
    cur = str(deal_cur or "").upper()
    fx = e.get("fx") or {}
    if cur and cur == e.get("cur"):
        k = 1.0
    elif cur == "EUR" and _pos(e.get("saleEur")):
        k = sale / e["saleEur"]
    elif cur and _pos(fx.get(cur)):
        k = fx[cur]
    else:
        return None, ECON_OTHER_CUR
    return amount * k / sale, ""


# ── правила ───────────────────────────────────────────────────────────────────

def _dm(t) -> str:
    return t.strftime("%d.%m") if t else ""


def current(a: dict | None, today: dt.date, build_last: str = "") -> dict | None:
    """Оценка, которой можно верить сегодня, — или None.

    build_last — дата последнего сообщения человека по чтению этой сборки
    (chat_activity, «ДД.ММ.ГГГГ»): человек писал после оценки — оценка устарела."""
    if not a or a.get("at") is None or a.get("state") is None:
        return None
    if (today - a["checked"].date()).days > FRESH_DAYS:
        return None
    if a["human"] is not None and a["human"] > a["at"]:
        return None
    if build_last:
        # сборка знает только день сообщения: в день оценки (и позже) судить нельзя —
        # человек мог написать после неё; тогда итог как без оценки
        try:
            if dt.datetime.strptime(build_last, "%d.%m.%Y").date() >= a["at"].date():
                return None
        except ValueError:
            pass
    return a


def stall_reason(a: dict | None) -> str:
    if not a:
        return ""
    if a["state"] in STALL:
        return STALL[a["state"]]
    if a["direction"] == BACK:
        return "откат: отказ, перенос, претензия или переделка"
    return ""


# Для всех — откуда решение, без самой оценки: модель — это оценка, а не факт
PUBLIC_STUCK = "простой подтверждён чтением чата"
PUBLIC_STUCK_MODEL = "простой — по оценке переписки моделью"


def decide(ch: dict, a: dict | None, today: dt.date) -> tuple[bool | None, str, str, str]:
    """Простой по стадии + чат → (подвисла, пояснение для всех, пояснение для права, откуда).

    подвисла: True — да; False — нет; None — чат не проверен (серая корзина).
    Пояснение для всех (строка сделки в CONTRACTS) не несёт оценки модели и данных
    прогона оценки — только то, что сборка прочла сама; подробное — в DEALCTX.
    Без оценки (a is None) итог и пояснения те же, что до 23.10.2026."""
    st = ch.get("status")
    if st == "quiet":
        note = f"в чате тихо {ch['days']} дн." if ch.get("days") is not None else "в чате людей не было"
        return True, note, note, "quiet"
    cur = current(a, today, ch.get("last") or "")
    why = stall_reason(cur)
    if st == "active":
        if why:
            return True, PUBLIC_STUCK_MODEL, f"в чате пишут, но по оценке чата {_dm(cur['at'])}: {why}", "model"
        note = f"в чате работа: последнее сообщение {ch['days']} дн. назад — не подвисла"
        priv = note
        if cur and cur["state"] in WAIT and cur.get("due") and cur["due"] >= today:
            priv += f"; {WAIT[cur['state']]} до {cur['due'].strftime('%d.%m')}"
        return False, note, priv, "active"
    # сборка чат не прочла (нет доступа, бюджет): решает прогон оценки, если УДАЧНО читал
    # его недавно (checked — последнее удачное чтение, scripts/realization_ctx.py)
    if a and (today - a["checked"].date()).days <= FRESH_DAYS:
        hd = a["human"].date() if a["human"] else None
        if hd is None or (today - hd).days > chat_activity.QUIET_DAYS:
            return True, PUBLIC_STUCK, (f"по чтению {_dm(a['checked'])}: в чате тихо {(today - hd).days} дн." if hd
                                        else f"по чтению {_dm(a['checked'])}: в чате людей не было"), "ctxQuiet"
        if why:
            return True, PUBLIC_STUCK_MODEL, f"по оценке чата {_dm(cur['at'])}: {why}", "model"
        return False, "в чате работа — не подвисла", f"по чтению {_dm(a['checked'])}: в чате работа — не подвисла", \
            "ctxActive"
    note = "чат не проверен — простой не подтверждён"
    return None, note, note, "unverified"


def work_hours(since: dt.datetime, now: dt.datetime) -> float:
    """Часы будних дней между двумя моментами по Москве; праздники не учитываются."""
    since, now = since.astimezone(MSK), now.astimezone(MSK)
    since = max(since, now - dt.timedelta(days=DUE_MAX_DAYS))   # дальше счёт не нужен
    total, t = 0.0, since
    while t < now:
        nxt = min(now, (t + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
        if t.weekday() < 5:
            total += (nxt - t).total_seconds() / 3600
        t = nxt
    return total


# ── сборка ─────────────────────────────────────────────────────────────────────

def _nm(v) -> str:
    return v if v not in (None, "", "—") else ""


def _gone(name: str) -> bool:
    return name.endswith("(уволен)")      # contracts.who подписывает ушедших так


def _person(r: dict, role: str) -> str:
    """Человек сделки по роли из оценки; роли без поля в сделке — подписью роли."""
    if role == "кам":
        return _nm(r.get("kam")) or ROLE_LBL[role]
    if role == "сопровождение":
        return _nm(r.get("ossDeal")) or _nm(r.get("oss")) or ROLE_LBL[role]
    if role == "ответственный":
        return _nm(r.get("manager")) or ROLE_LBL[role]
    return ROLE_LBL.get(role, role)


def _owner(r: dict) -> str:
    """Хозяин уровня сделки — как в lifecycle: ОСС сделки, иначе ответственный."""
    return _nm(r.get("ossDeal")) or _nm(r.get("manager")) or "—"


def _item(code: str, r: dict, who: str, act: str = "", days: int | None = None, what: str = "",
          conf: float | None = None) -> dict:
    sev, what0, act0 = EXC[code]
    what = what or what0
    if code.startswith("chat"):
        # пункт по чату — оценка модели, а не факт: уверенность — в самом пункте
        what += " — оценка модели" + (f", уверенность {round(conf * 100)} %" if conf is not None else "")
    return {"code": code, "sev": sev, "what": what, "act": act or act0, "days": days,
            "who": who, "whoGone": _gone(who), "kam": _nm(r.get("kam")), "kamId": _nm(r.get("kamId")),
            "deal": r["deal"], "seq": r.get("seq") or 0, "customer": r.get("customer") or "—",
            "order": "", "title": r.get("title") or "", "sup": "", "dl": "", "phase": "",
            # отдельное направление (contracts.ОТДЕЛЬНЫЕ_ВОРОНКИ): пункт — в его очереди
            "seg": r.get("seg")}


def _chat_cell(a: dict, cur: dict | None, r: dict, now: dt.datetime) -> dict:
    today = now.date()
    due = a.get("due")
    b = a.get("blocker")
    return {
        "at": a["at"].strftime("%d.%m.%Y"), "cur": cur is not None,
        "state": STATE_LBL[a["state"]], "dir": a["direction"], "risk": a["risk"],
        "kinds": [k.replace("_", " ") for k in a["kinds"]],
        "ball": ROLE_LBL[a["ball"]], "ballWho": _person(r, a["ball"]) if a["ball"] in OUR_ROLES else "",
        "reply": a["reply"],
        "replyH": round(work_hours(a["human"], now)) if (a["reply"] and a["human"]) else None,
        "due": due.strftime("%d.%m.%Y") if due else "", "dueKind": a.get("dueKind") or "",
        "duePassed": bool(due and due < today),
        "blocker": f"{b['kind']} · на стороне: {ROLE_LBL[b['on']]}" if b else "",
        "human": a["human"].strftime("%d.%m.%Y") if a["human"] else "",
        # уверенность модели, % — оценка переписки, а не факт; нет — не показывается
        "conf": round(a["conf"] * 100) if a.get("conf") is not None else None,
    }


def _amt(v: float) -> str:
    """Сумма с неразрывными пробелами между разрядами: в узкой карточке число не рвётся."""
    return f"{round(v):,}".replace(",", "\u00a0")


def _gap_pct(d: float) -> float:
    """Расхождение в процентах с одним знаком — в сторону от порога: 10,04 % (пункт: «больше
    10 %») не печатается «10 %» — это «10,1 %», а 9,96 % (не пункт) — «9,9 %»."""
    x = round(d * 1000, 6)
    return (math.ceil(x) if d > ECON_GAP else math.floor(x)) / 10


def gap_text(pct: float) -> str:
    return f"{pct:.1f}".replace(".", ",")


def _econ_cell(e: dict, r: dict) -> dict:
    """Финальная экономика в карточке сделки. Сумма или налог сделки неизвестны сборке
    (строка без saleAmt, она или saleTax не число) — сверки нет, а не «расходится на 100 %».
    «Сумма» менялась после чтения файла (econ_changed) — тоже: файл могли обновить вместе с
    ней, сверка — после перечтения. На день чтения суммы не было («суммы нет») — сверять
    было не с чем, и сегодняшняя «Сумма» сверяется с файлом прямо."""
    amt, tax = r.get("saleAmt"), r.get("saleTax")
    if amt is None or ("saleTax" in r and tax is None):
        ratio, note = None, ""
    else:
        ratio, note = econ_ratio(amt, r.get("saleCur") or "", e, tax=tax)
    if econ_stale(e, ratio, note):
        ratio, note = None, ECON_CHANGED
    verdict = (econ_verdict(ratio, tax_empty=not _num(tax), sale=e["sale"], kp_sale=e.get("kpSale"))
               if ratio is not None else "")
    # порог — по округлённой доле: 10 % ровно не пункт и при шуме плавающей точки
    d = round(abs(ratio - 1), 6) if ratio is not None else None
    return {"at": e["at"].strftime("%d.%m.%Y"), "sale": f"{_amt(e['sale'])}\u00a0{e['cur']}", "cur": e["cur"],
            "buy": f"€{_amt(e['buyEur'])}", "markup": round(e["markup"] * 100, 1), "rebid": e["rebid"],
            "items": e["items"], "gap": _gap_pct(d) if d is not None else None,
            "gapNote": note, "vat": verdict == ECON_VAT_LIKE, "preRebid": verdict == ECON_PRE_REBID,
            "over": verdict in (ECON_OVER, ECON_PRE_REBID)}


def _contract_cell(c: dict) -> dict:
    missing = [s for s in SIDE_LBL if not c["read"][s]] if c["outcome"] in NO_PAIR else []
    return {"at": c["at"].strftime("%d.%m.%Y"), "outcome": c["outcome"], "missing": missing,
            "read": c["read"],
            "flags": [{"code": f["code"], "text": contract_b2b.FLAG_TEXT[f["code"]],
                       "short": FLAG_SHORT.get(f["code"], f["code"]), "order": f["order"], "days": f["days"]}
                      for f in c["flags"]]}


def build(rows: list[dict], life: dict | None, ctx: dict | None,
          now: dt.datetime, today: dt.date | None = None) -> tuple[dict | None, dict | None]:
    """→ (числа плиток для head, закрытая часть DEALCTX). Без свода — (None, None).

    rows — строки сделок contracts.compute (после decide), life — его раздел «life» вместе
    с очередью отдельных направлений (contracts.life_all): «что делать первым» по сделке;
    today — тот же день, что у contracts.compute (as_of): иначе строка и ячейка одной
    сделки судили бы свежесть по разным дням.

    Отдельное направление (строка с seg, решение владельца 25.10.2026) считается в свои
    плитки — head["sep"] — и свои счётчики очереди (sepCounts): в плитки и счётчики
    основного вида его сделки не входят. Пункты очереди помечены seg."""
    if ctx is None:
        return None, None
    now = now.astimezone(MSK)
    today = today or now.date()
    live = [r for r in rows if not r.get("closed")]
    chats, cons, econ = ctx["chats"], ctx["contracts"], ctx.get("econ") or {}
    deals: dict[str, dict] = {}
    queue: list[dict] = []
    by_seg: dict[bool, Counter] = {False: Counter(), True: Counter()}     # основной вид / направление
    for r in live:
        did = str(r["deal"])
        rec: dict = {}
        n = by_seg[bool(r.get("seg"))]
        a = chats.get(did)
        if r.get("chat"):
            # подробное «почему подвисла / не подвисла» — только здесь, под правом
            rec["why"] = decide(r["chat"], a, today)[2]
        if a and a.get("at") is not None:
            build_last = (r.get("chat") or {}).get("last") or ""
            cur = current(a, today, build_last)
            n["chatSeen"] += 1
            rec["chat"] = _chat_cell(a, cur, r, now)
            if cur:
                n["chatCur"] += 1
                if cur["reply"]:
                    n["reply"] += 1
                    who = _person(r, cur["ball"]) if cur["ball"] in OUR_ROLES else _owner(r)
                    h = work_hours(cur["human"], now) if cur["human"] else None
                    if h is not None and h > REPLY_HOURS:
                        n["replyLate"] += 1
                        queue.append(_item("chatReply", r, who, days=(today - cur["human"].date()).days,
                                           conf=cur.get("conf")))
                if cur["risk"] == "высокий":
                    n["high"] += 1
                    kinds = ", ".join(k.replace("_", " ") for k in cur["kinds"])
                    queue.append(_item("chatRisk", r, _owner(r), act="разобрать риск" + (f": {kinds}" if kinds else ""),
                                       conf=cur.get("conf")))
                due = cur.get("due")
                if due and due < today and (today - due).days <= DUE_MAX_DAYS:
                    n["dueLate"] += 1
                    queue.append(_item("chatDue", r, _nm(r.get("ossDeal")) or _nm(r.get("oss")) or _owner(r),
                                       act=f"подтвердить {DUE_ACC[cur['dueKind']]} у второй стороны "
                                           f"(срок был {due.strftime('%d.%m')})",
                                       days=(today - due).days, conf=cur.get("conf")))
        c = cons.get(did)
        if c:
            n["contractSeen"] += 1
            cell = _contract_cell(c)
            rec["contract"] = cell
            n["outcome:" + c["outcome"]] += 1
            if cell["flags"]:
                n["risk"] += 1
                n["flags"] += len(cell["flags"])
                shorts = list(dict.fromkeys(f["short"] for f in cell["flags"]))
                queue.append(_item("contractRisk", r, CONTRACT_HEAD,
                                   act="допсоглашение с поставщиком: " + ", ".join(shorts)))
            if c["outcome"] == UNREADABLE:
                # файла нет у портала (404, пуст), формат не читается или длиннее предела чтения —
                # следующий прогон не вылечит: перезалить — тому же, кто ищет договор
                queue.append(_item("contractUnreadable", r, _nm(r.get("manager")) or _owner(r)))
            if cell["missing"]:
                n["nopair"] += 1
                # договор заказчика — с выигрыша сделки; поставщика — когда заказ размещён
                need = [s for s in cell["missing"] if s == "customer" or (r.get("norders") or 0) > 0]
                if need:
                    whose = " и ".join(SIDE_LBL[s] for s in need)
                    queue.append(_item("contractMissing", r, _nm(r.get("manager")) or _owner(r),
                                       what=SIDE_MISSING.format(whose=whose),
                                       act=f"найти договор {whose} и загрузить файл в карточку"))
        e = econ.get(did)
        if e:
            n["econSeen"] += 1
            cell = _econ_cell(e, r)
            rec["econ"] = cell
            n["econOther"] += cell["gapNote"] == ECON_OTHER_CUR
            n["econNoSum"] += cell["gapNote"] == ECON_NO_SUM
            n["econChanged"] += cell["gapNote"] == ECON_CHANGED
            n["econVat"] += cell["vat"]
            n["econPreRebid"] += cell["preRebid"]
            if cell["over"]:
                n["econGap"] += 1
                # хозяин «Суммы» — ответственный сделки
                queue.append(_item("econGap", r, _nm(r.get("manager")) or _owner(r),
                                   what=("Сумма сделки — продажа «Нашего КП» до переторжки: расходится с "
                                         if cell["preRebid"] else "Сумма сделки расходится с ")
                                   + f"финальной экономикой на {gap_text(cell['gap'])} %"))
        if rec:
            deals[did] = rec
    queue.sort(key=lambda q: (q["sev"], -(q["days"] or 0), -q["seq"]))
    # что делать первым по сделке — общий порядок пунктов lifecycle и этих
    first: dict[str, dict] = {}
    for q in sorted(list((life or {}).get("queue") or []) + queue,
                    key=lambda q: (q["sev"], -(q["days"] or 0))):
        did = str(q["deal"])
        if did not in first:
            first[did] = {"who": q["who"], "act": q["act"], "what": q["what"], "code": q["code"]}
    for did, f in first.items():
        deals.setdefault(did, {})["todo"] = f
    at = ctx.get("at")

    def _head(n: Counter, open_n: int) -> dict:
        return {
            "open": open_n, "at": at.strftime("%d.%m.%Y %H:%M") if at else "",
            "chat": {"seen": n["chatSeen"], "cur": n["chatCur"], "reply": n["reply"], "replyLate": n["replyLate"],
                     "high": n["high"], "dueLate": n["dueLate"]},
            "contract": {"seen": n["contractSeen"], "risk": n["risk"], "flags": n["flags"], "nopair": n["nopair"],
                         "pair": n["outcome:пара"], "unread": n["outcome:недочитана"],
                         "unreadable": n["outcome:" + UNREADABLE]},
            # финальная экономика из файла — только счёт сделок: числа файла — в DEALCTX
            "econ": {"seen": n["econSeen"], "gap": n["econGap"], "otherCur": n["econOther"],
                     "noSum": n["econNoSum"], "vat": n["econVat"], "changed": n["econChanged"],
                     "preRebid": n["econPreRebid"]},
            "rules": {"freshDays": FRESH_DAYS, "replyHours": REPLY_HOURS, "dueMaxDays": DUE_MAX_DAYS,
                      "econGapPct": round(ECON_GAP * 100)},
        }
    n_sep = sum(1 for r in live if r.get("seg"))
    head = _head(by_seg[False], len(live) - n_sep)
    if n_sep:
        head["sep"] = _head(by_seg[True], n_sep)
    payload = {"at": head["at"], "deals": deals, "queue": queue,
               "labels": {k: v[1] for k, v in EXC.items()},
               "counts": dict(Counter(q["code"] for q in queue if not q.get("seg"))),
               "sepCounts": dict(Counter(q["code"] for q in queue if q.get("seg")))}
    return head, payload
