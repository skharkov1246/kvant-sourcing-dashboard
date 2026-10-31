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

  5. Факты из чата и «главное» (решение владельца 25.10.2026; scripts/chat_context.py chat-ctx-2
     → deal_chat_facts → раздел facts ключа). В карточке сделки — строка «в чате: …» (сумма,
     валюта, НДС, вид) рядом с файлом и «Суммой», ни одну не заменяя; сверка — только строго
     сравнимых (chat_money_cmp); договор и сигналы сорсинга — строками; «главное» — 0–3 пункта
     оценки модели с уверенностью и хозяином по виду (IMP_OWNER → ork.ITEM). Руководителю ОРК —
     закрытый список «Что важно сегодня» (payload["important"]; у направления —
     sepImportant): до IMP_TOP пунктов открытых сделок, чьё доказательство (последнее
     сообщение пункта, без сообщений — дата оценки) не старше IMP_MAX_DAYS, по важности и
     свежести. Это не пункт очереди: в queue, counts, «что делать первым», итоги и плитки
     «главное» не идёт; в шапке — только покрытие (head.facts: есть у N), суммы не
     складываются, числа расхождений и высокой важности — только в закрытой части
     (payload["factsClosed"]).

Пороги — таблицей (CLAUDE.md, правило 4). Текста чатов и договоров здесь нет: в ключе
только номера сделок, даты и значения из закрытых списков — и строка «главного» (оценка
модели до 160 знаков, без сумм, ссылок и имён). Цитат в ключе нет.
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
import lifecycle
import ork

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
# роль из оценки чата («на ком мяч») → роль пункта очереди (ork.ROLES, ork.ROLES_EXTRA)
CHAT_ROLE = {"кам": "kam", "сопровождение": "oss", "логист": "log", "ответственный": "dealResp",
             "сорсер": "src", "руководитель_сорсинга": "src"}
SIGNAL = "сигнал из чата, не эскалация"

# Факты из чата и «главное» (решение владельца 25.10.2026, scripts/chat_context.py chat-ctx-2 →
# deal_chat_facts → раздел facts ключа). Закрытые списки — дословно как в chat_context и
# deal_chat_fact_ok базы (сверяет тест).
MONEY_KINDS = ("наше_кп", "переторжка", "итог", "счёт", "оплата")
MONEY_VAT = ("с_ндс", "без_ндс", "не_указано")
MONEY_CUR = ("RUB", "EUR", "USD", "CNY", "AED", "INR", "GBP", "JPY", "неясно")
CONTRACT_STATUSES = ("подписан", "отправлен", "у_клиента", "на_согласовании", "правки", "неясно")
CONTRACT_SIDES = ("заказчик", "поставщик", "неясно")
CONTRACT_WHERE = ("портал", "эдо", "почта", "бумага", "у_клиента", "у_нас", "у_поставщика", "неясно")
SIGNALS = ("запрос_отправлен", "ответил", "кп_получено", "отказ", "нет_ответа", "запрошено_повторно")
AUTHOR_ROLES = ("кам", "сорсер", "руководитель_сорсинга", "сопровождение", "ответственный", "сотрудник",
                "служебная_запись", "система")
IMP_KINDS = ("срыв_срока_клиента", "деньги_оплата", "расхождение_суммы", "договор_условия", "поставщик",
             "клиент_недоволен", "решение_руководителя", "документы_упд", "иное")
IMP_SEV = ("высокая", "средняя")
IMP_SRC = ("сутки", "история")
DIGEST_KEYS = ("stage", "dl", *(f"o{i}" for i in range(1, 9)), "deal", "contract", "econ", "presale",
               "prev", "facts", "imp")
IMP_TEXT_MAX = 160
IMP_MAX_DAYS = 7        # «главное» старше — не в «Что важно сегодня» (в карточке — с датой)
IMP_TOP = 15            # пунктов в «Что важно сегодня»
IMP_PROPOSED_MAX = 50   # пунктов в ответе модели до проверок (deal_chat_ctx.imp_proposed)
CHAT_MONEY_MAX = 3      # упоминаний сумм в карточке
IMP_LBL = {"срыв_срока_клиента": "срыв срока клиента", "деньги_оплата": "деньги и оплата",
           "расхождение_суммы": "расхождение суммы", "договор_условия": "договор и условия",
           "поставщик": "поставщик", "клиент_недоволен": "клиент недоволен",
           "решение_руководителя": "нужно решение руководителя", "документы_упд": "документы и УПД",
           "иное": "иное"}
# Хозяин пункта «главного» — по виду, по регламенту ОРК (решение владельца 26.10.2026, п. 1;
# правило 4: таблицей): строка ork.ITEM «imp:<вид>» — шаг, роль и копия; «dealResp» —
# ответственный сделки (хозяин «Суммы», как у пункта econGap). ОСС пунктов не получает — со
# сторонами не говорит (Рег. 1.11)
IMP_OWNER = {k: ("dealResp" if k in ("расхождение_суммы", "иное") else f"imp:{k}") for k in IMP_KINDS}
MONEY_LBL = {"наше_кп": "наше КП", "переторжка": "переторжка", "итог": "итог", "счёт": "счёт", "оплата": "оплата"}
VAT_LBL = {"с_ндс": "с НДС", "без_ндс": "без НДС", "не_указано": "НДС не указан"}
CONTRACT_SIDE_LBL = {"заказчик": "с заказчиком", "поставщик": "с поставщиком", "неясно": "сторона неясна"}
CONTRACT_STATUS_LBL = {"подписан": "подписан", "отправлен": "отправлен", "у_клиента": "у клиента",
                       "на_согласовании": "на согласовании", "правки": "правки", "неясно": "статус неясен"}
WHERE_LBL = {"портал": "в портале", "эдо": "в ЭДО", "почта": "в почте", "бумага": "на бумаге",
             "у_клиента": "у клиента", "у_нас": "у нас", "у_поставщика": "у поставщика", "неясно": ""}
SIGNAL_LBL = {"запрос_отправлен": "запрос отправлен", "ответил": "поставщик ответил", "кп_получено": "КП получено",
              "отказ": "отказ поставщика", "нет_ответа": "нет ответа", "запрошено_повторно": "запрошено повторно"}
AUTHOR_LBL = {"кам": "КАМ", "сорсер": "сорсер", "руководитель_сорсинга": "руководитель сорсинга",
              "сопровождение": "сопровождение", "ответственный": "ответственный", "сотрудник": "сотрудник",
              "служебная_запись": "служебная запись", "система": "система"}
SPEC_RX = re.compile(r"^[0-9A-Za-zА-ЯЁа-яё№/._-]{1,20}$")

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

# Сделка и заказ поставщика (спецификация «сделка и заказ», 30.10.2026, §1.4, §2.2; решения
# владельца 30.10.2026). Единица пункта (unit) и часть очереди (bucket): exec — «Дела по
# заказам», deal — «Дела по сделкам», hyg — «Порядок в карточках Битрикса» (вне дневного счёта).
# Таблица кодов → часть — одна на «Реализацию»: lifecycle.UNIT / BUCKET / BUCKET_ORDER (коды
# закрытой части в ней же); правило 4 — таблица, а не ветвления
BUCKETS = ("exec", "deal", "hyg")
# шаг пункта сделки — шаг стадии сделки только там, где шаг — сделки (Рег.: 1 — приёмка, 2–3 —
# договор с заказчиком, 13 — закрытие); шаги 4–12 — шаги заказов (§1.6 п. 4): у пункта сделки
# их нет, заказы — своим списком
DEAL_STEPS = (1, 2, 3, 13)
NOT_SET = "не назначен"
ORDER_UNKNOWN = "заказ не определён"


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


def _facts(v) -> dict | None:
    """Факты сделки из чата (раздел facts ключа, scripts/realization_ctx.py): суммы, договор,
    сигналы сорсинга, «главное». Значение не из закрытого списка, сумма не число, текст
    «главного» длиннее предела — запись сделки отбрасывается целиком (её оценка чата — в
    другом разделе и не пропадает). Цитат и номеров сообщений в ключе нет."""
    if not isinstance(v, dict):
        return None
    out: dict = {"money": [], "contract": [], "src": [], "imp": None}
    for m in v.get("money") or []:
        if (not isinstance(m, dict) or m.get("kind") not in MONEY_KINDS or m.get("cur") not in MONEY_CUR
                or m.get("vat") not in MONEY_VAT or _pos(m.get("amount")) is None):
            return None
        day = _day(m.get("date")) if m.get("date") is not None else None
        if m.get("date") is not None and day is None:
            return None
        out["money"].append({"amount": _pos(m["amount"]), "cur": m["cur"], "kind": m["kind"], "vat": m["vat"],
                             "date": day, "at": _ts(m.get("at"))})
    for c in v.get("contract") or []:
        spec = c.get("spec") if isinstance(c, dict) else None
        if (not isinstance(c, dict) or c.get("side") not in CONTRACT_SIDES or c.get("status") not in CONTRACT_STATUSES
                or c.get("where") not in CONTRACT_WHERE
                or (spec is not None and not (isinstance(spec, str) and SPEC_RX.match(spec)))):
            return None
        out["contract"].append({"side": c["side"], "status": c["status"], "where": c["where"], "spec": spec,
                                "at": _ts(c.get("at"))})
    for s in v.get("src") or []:
        if not isinstance(s, dict) or s.get("signal") not in SIGNALS or s.get("role") not in AUTHOR_ROLES:
            return None
        out["src"].append({"signal": s["signal"], "role": s["role"], "at": _ts(s.get("at"))})
    imp = v.get("imp")
    if imp is not None:
        conf = imp.get("conf") if isinstance(imp, dict) else None
        if (not isinstance(imp, dict) or _ts(imp.get("at")) is None or imp.get("src") not in IMP_SRC
                or (conf is not None and not (isinstance(conf, (int, float)) and not isinstance(conf, bool)
                                              and 0 <= conf <= 1))
                or not isinstance(imp.get("items"), list) or len(imp["items"]) > 3):
            return None
        n, dg = imp.get("n"), imp.get("dg")
        if ((n is not None and not (isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= IMP_PROPOSED_MAX))
                or (dg is not None and not isinstance(dg, bool))):
            return None
        items = []
        for x in imp["items"]:
            keys = x.get("keys") if isinstance(x, dict) else None
            ev = x.get("ev") if isinstance(x, dict) else None
            if (not isinstance(x, dict) or x.get("kind") not in IMP_KINDS or x.get("sev") not in IMP_SEV
                    or not isinstance(x.get("text"), str) or not 0 < len(x["text"].strip()) <= IMP_TEXT_MAX
                    or not isinstance(keys, list) or len(keys) > 5 or any(k not in DIGEST_KEYS for k in keys)
                    or (ev is not None and _ts(ev) is None)):
                return None
            # дата доказательства (ключ до 25.10.2026 без неё — дата оценки)
            items.append({"kind": x["kind"], "sev": x["sev"], "text": x["text"].strip(), "keys": list(keys),
                          "ev": _ts(ev) if ev is not None else _ts(imp["at"])})
        out["imp"] = {"at": _ts(imp["at"]), "conf": conf, "src": imp["src"], "items": items, "n": n, "dg": dg}
    return out


def clean(raw) -> dict | None:
    """Ключ — данные, а не код: запись не по форме отбрасывается и считается (bad).
    Раздела econ в ключе до 23.10.2026 нет — пустой словарь; раздела facts до 25.10.2026 —
    тоже."""
    if not isinstance(raw, dict) or raw.get("version") != VERSION:
        return None
    chats, contracts, econ, facts, bad = {}, {}, {}, {}, 0
    for src, dst, fn in ((raw.get("chats"), chats, _chat), (raw.get("contracts"), contracts, _contract),
                         (raw.get("econ"), econ, _econ), (raw.get("facts"), facts, _facts)):
        for k, v in (src or {}).items() if isinstance(src, dict) else ():
            x = fn(v)
            if x is None or not str(k).isdigit():
                bad += 1
                continue
            dst[str(k)] = x
    return {"at": _ts(raw.get("at")), "chats": chats, "contracts": contracts, "econ": econ, "facts": facts,
            "bad": bad}


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


def customer_contract_deals(contracts: dict | None) -> set[str]:
    """Сделки, у которых договор заказчика найден и прочитан (свод contracts, read.customer > 0):
    оплата поставщику у них — не «без договора с заказчиком», даже если стадия отстала."""
    return {str(did) for did, c in (contracts or {}).items()
            if isinstance(c, dict) and ((c.get("read") or {}).get("customer") or 0) > 0}


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


def _people(r: dict, ork_head: str) -> dict:
    """Люди СДЕЛКИ для ork.owner (спецификация «сделка и заказ», §1.3, E9): руководитель ОРК —
    по справочнику отделов, КАМ и ОСС сделки — поля сделки. Менеджер ОРК — роль ЗАКАЗА: у
    пункта сделки его нет (до 30.10.2026 брался исполнитель шага первого попавшегося живого
    заказа). Пункт заказа берёт менеджера своего заказа (_reg с order)."""
    return {"orkMgr": "", "dealOrk": "", "orkHead": _nm(ork_head),
            "kam": _nm(r.get("kam")), "ossOrder": "", "ossDeal": _nm(r.get("ossDeal"))}


def ord_mgrs(r: dict) -> list[dict]:
    """Менеджеры ОРК живых заказов сделки: [{who, n}] — n заказов у каждого, «не назначен»
    отдельной строкой; больше заказов — выше. Менеджер заказа — orders[].orkMgr строки
    (contracts.compute: ответственный заказа из ОРК, иначе «Ответственный за реализацию»)."""
    c = Counter(_nm(o.get("orkMgr")) or NOT_SET for o in r.get("orders") or [] if o.get("live"))
    return [{"who": w, "n": n} for w, n in sorted(c.items(), key=lambda x: (-x[1], x[0] == NOT_SET, x[0]))]


def ord_mgrs_text(mg: list[dict]) -> str:
    """«менеджеры заказов: Пётр (2), не назначен (1)» (спецификация §1.3); заказов нет — ''."""
    return ("менеджеры заказов: " + ", ".join(f"{m['who']} ({m['n']})" for m in mg)) if mg else ""


def _step_now(r: dict) -> int | None:
    """Шаг СДЕЛКИ (спецификация §1.6 п. 4, §4.3): приёмка (1) — сделка в реализации без
    заказа; иначе шаг стадии сделки, если он — шаг сделки (DEAL_STEPS: 1–3, 13); шаги 4–12 —
    шаги заказов, у пункта сделки шага нет (None). Шаг одного заказа без номера пункту сделки
    не ставится (до 30.10.2026 — ячейка шага строки, самый ранний шаг живых заказов)."""
    if not r.get("closed") and r.get("inCat0", True) and not r.get("norders") and not r.get("orders"):
        return 1
    st = r.get("dealStep")
    return st if st in DEAL_STEPS else None


def _reg(code: str, r: dict, ppl: dict, key: str | None = None, order: dict | None = None) -> dict:
    """Хозяин пункта по регламенту (решение владельца 26.10.2026, п. 1; ork.ITEM): шаг, роль,
    исполнитель, копия.

    order — пункт ЗАКАЗА (строка orders[] сделки): шаг — шаг этого заказа сейчас, менеджер ОРК
    и ОСС — этого заказа. Без order — пункт СДЕЛКИ (§1.6 п. 5): хозяин — роль сделки; где
    регламент даёт пункт менеджеру ОРК (чаты, договор поставщика), хозяин — руководитель ОРК, а
    менеджеры заказов сделки — в копии списком («менеджеры заказов: …»); шаг — шаг сделки."""
    if order is not None:
        # исполнитель задачи подтверждения передачи — хозяин шага 0 (решение владельца 31.10.2026)
        pp = {**ppl, "orkMgr": _nm(order.get("orkMgr")), "ossOrder": _nm(order.get("oss")),
              "confirmResp": _nm(order.get("confirmWho"))}
        ow = ork.item_owner(code, order.get("step"), pp, key=key)
        return {"step": order.get("step"), "role": ow["role"], "roleName": ork.ROLES.get(ow["role"], ""),
                "who": ow["who"], "escalateTo": ow["copy"], "copyRole": ow["copyRole"]}
    st = _step_now(r)
    ow = ork.item_owner(code, st, ppl, key=key)
    role, who, copy, copy_role = ow["role"], ow["who"], ow["copy"], ow["copyRole"]
    mg = ord_mgrs(r)
    also: dict = {}
    if role == "orkMgr":
        # менеджер ОРК — роль заказа: пункт сделки ведёт руководитель ОРК, менеджеры — в копии;
        # заказов нет — копия по таблице ITEM. Прежняя копия (КАМ) не теряется: alsoCopy
        head = ork.owner(None, ppl, role="orkHead", esc=None)
        role, who = "orkHead", head["who"]
        if mg:
            if copy_role:
                also = {"alsoCopy": copy, "alsoCopyRole": copy_role}
            copy, copy_role = ord_mgrs_text(mg), "orkMgr"
    elif copy_role == "orkMgr":
        copy = ord_mgrs_text(mg) or NOT_SET
    return {"step": st, "role": role, "roleName": ork.ROLES.get(role, ""),
            "who": who, "escalateTo": copy, "copyRole": copy_role,
            **({"ordMgrs": mg} if copy_role == "orkMgr" else {}), **also}


def _by_person(role: str, who: str, r: dict) -> dict:
    """Пункт, которого регламент не описывает: хозяин — человек сделки по роли."""
    return {"step": _step_now(r), "role": role,
            "roleName": ork.ROLES.get(role) or ork.ROLES_EXTRA.get(role, ""),
            "who": who or "не назначен", "escalateTo": "", "copyRole": ""}


def _resp(r: dict) -> dict:
    """Ответственный сделки — хозяин «Суммы» и файла договора в карточке сделки."""
    return _by_person("dealResp", _nm(r.get("manager")), r)


def _bucket(code: str, key: str | None, unit: str) -> str:
    """Часть очереди пункта по таблице lifecycle (unit_bucket): пункт заказа — с заказом."""
    return lifecycle.unit_bucket(code, key, "1" if unit == "order" else None)[1]


def _item(code: str, r: dict, own: dict, act: str = "", days: int | None = None, what: str = "",
          conf: float | None = None, *, key: str | None = None, order: dict | None = None) -> dict:
    """Пункт очереди; own — хозяин (_reg, _by_person): шаг, роль, исполнитель и копия.

    order — строка orders[] сделки: пункт ЗАКАЗА (unit = order) с номером, поставщиком и сроком
    этого заказа; без него — пункт СДЕЛКИ (unit = deal). bucket — часть очереди по таблице
    lifecycle.BUCKET (ключ — key, иначе код). Поля — по договору полей спецификации, §4.3."""
    sev, what0, act0 = EXC[code]
    what = what or what0
    if code.startswith("chat"):
        # пункт по чату — оценка модели, а не факт: уверенность — в самом пункте
        what += " — оценка модели" + (f", уверенность {round(conf * 100)} %" if conf is not None else "")
    who = own["who"]
    unit = "order" if order is not None else "deal"
    o = order or {}
    return {"code": code, "sev": sev, "what": what, "act": act or act0, "days": days,
            **own, "whoGone": _gone(who), "inWd": False,
            **({"signal": SIGNAL} if code in ("chatDue", "chatRisk") else {}),
            **({"itemKey": key} if key else {}),
            "unit": unit, "bucket": _bucket(code, key, unit),
            "kam": _nm(r.get("kam")), "kamId": _nm(r.get("kamId")),
            "deal": r["deal"], "seq": r.get("seq") or 0, "customer": r.get("customer") or "—",
            "order": str(o.get("id") or ""), "ordLbl": o.get("lbl") or "", "ordNo": o.get("ordNo"),
            "title": r.get("title") or "", "sup": (o.get("sup") or "—") if order is not None else "",
            "dl": o.get("dl") or "", "phase": "",
            # отдельное направление (contracts.ОТДЕЛЬНЫЕ_ВОРОНКИ): пункт — в его очереди
            "seg": r.get("seg")}


def person_ids(queue: list[dict], life_queue: list[dict], rows: list[dict]) -> None:
    """id Битрикса хозяина и копии у пунктов закрытой части (whoId, escalateToId), как у пунктов
    life.queue (R4, Д-11а): по ним «Смотреть как» отбирает дела человека. Справочник «имя → id» —
    из тех же имён очереди «Реализации» (who/whoId, escalateTo/escalateToId, kam/kamId) и КАМов
    строк сделок; имени нет в справочнике — id не ставим (подпись роли, «не назначен», сводка
    «менеджеры заказов: …»)."""
    ids: dict[str, str] = {}

    def put(name, pid) -> None:
        name, pid = _nm(name), _nm(pid)
        if name and pid and name != NOT_SET:
            ids.setdefault(name, str(pid))
    for q in life_queue:
        put(q.get("who"), q.get("whoId"))
        put(q.get("escalateTo"), q.get("escalateToId"))
        put(q.get("kam"), q.get("kamId"))
    for r in rows:
        put(r.get("kam"), r.get("kamId"))
    for q in queue:
        if not _nm(q.get("whoId")) and ids.get(_nm(q.get("who"))):
            q["whoId"] = ids[_nm(q.get("who"))]
        if q.get("escalateTo") and not _nm(q.get("escalateToId")) and ids.get(_nm(q.get("escalateTo"))):
            q["escalateToId"] = ids[_nm(q.get("escalateTo"))]


def _unit_payload(queue: list[dict], prefix: str) -> dict:
    """{ordCounts, dealCounts, bucketCounts} очереди; prefix «sep» — sepOrdCounts и т. д."""
    oc, dc = unit_counts(queue)
    name = (lambda k: prefix + k[0].upper() + k[1:]) if prefix else (lambda k: k)
    return {name("ordCounts"): oc, name("dealCounts"): dc, name("bucketCounts"): bucket_counts(queue)}


def unit_counts(queue: list[dict]) -> tuple[dict, dict]:
    """(ordCounts, dealCounts): по коду — разных заказов у пунктов заказа и разных сделок у
    пунктов сделки (спецификация §1.8 п. 4: счёт — число разных объектов своей единицы, а не
    длина списка). Одно определение с очередью «Реализации» — lifecycle.queue_counts."""
    c = lifecycle.queue_counts(queue)
    return c["ordCounts"], c["dealCounts"]


def bucket_counts(queue: list[dict]) -> dict | None:
    """Счёт очереди по частям (§2.1, §4.3) в своих единицах — lifecycle.queue_counts (одно
    определение у life, life_all, head.sep и закрытой части): exec — разных заказов, сделок этих
    заказов и людей-хозяев; deal — разных сделок и людей; hyg — разных заказов (у пунктов заказа)
    и разных сделок (у пунктов сделки). Ни у одного пункта нет bucket — None (очередь пуста или
    ещё без частей). «не назначен» человеком не считается."""
    if not any(q.get("bucket") for q in queue):
        return None
    return lifecycle.queue_counts(queue)["bucketCounts"]


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


def _flag_order(f: dict, r: dict | None) -> dict | None:
    """Заказ сделки, к которому разбор договоров отнёс флаг (f.order — id СП-172); id нет или
    заказа с таким id у сделки нет — None («заказ не определён»)."""
    if f.get("order") is None or not r:
        return None
    return next((o for o in r.get("orders") or [] if str(o.get("id")) == str(f["order"])), None)


def _contract_cell(c: dict, r: dict | None = None) -> dict:
    """Ячейка договоров карточки сделки. r — строка сделки: у флага с заказом — номер заказа
    («950/1») и поставщик (E38: «у флагов договора — f.order»)."""
    missing = [s for s in SIDE_LBL if not c["read"][s]] if c["outcome"] in NO_PAIR else []
    flags = []
    for f in c["flags"]:
        o = _flag_order(f, r)
        flags.append({"code": f["code"], "text": contract_b2b.FLAG_TEXT[f["code"]],
                      "short": FLAG_SHORT.get(f["code"], f["code"]), "order": f["order"], "days": f["days"],
                      "ordLbl": (o.get("lbl") or "") if o else "", "sup": (o.get("sup") or "") if o else ""})
    return {"at": c["at"].strftime("%d.%m.%Y"), "outcome": c["outcome"], "missing": missing,
            "read": c["read"], "flags": flags}


def _to_sub(cur: str, e: dict) -> float | None:
    """Единиц валюты подачи файла за 1 единицу валюты суммы из чата — или None."""
    if cur == e.get("cur"):
        return 1.0
    if cur == "EUR" and _pos(e.get("saleEur")):
        return e["sale"] / e["saleEur"]
    return _pos((e.get("fx") or {}).get(cur))


def _cmp(x: float, base: float) -> dict:
    d = round(abs(x / base - 1), 6)
    return {"pct": _gap_pct(d), "over": d > ECON_GAP}


def chat_money_cmp(m: dict, r: dict, e: dict | None) -> dict:
    """Сумма из чата против продажи файла и «Суммы» сделки — только строго сравнимые
    (правило «сравниваем без НДС»): сумма без НДС и в валюте файла или по курсам файла.
    «Наше КП» — с продажей «Нашего КП» файла (у переторжки — kpSale, без неё продажа файла
    и есть КП); итог и переторжка — с продажей файла. С «Суммой» без НДС (OPPORTUNITY −
    TAX_VALUE) — в валюте сделки; налога нет, а «Сумма» больше в 1,20 или 1,22 раза — «похоже
    на «Сумму» с НДС», не пункт, как у сверки с файлом (econ_verdict). Счёт и оплата бывают частью суммы — не сравниваются.
    → {"file": {pct, over} | {why} | None, "sum": {pct, over} | {why}}. Ни одна цифра не
    заменяет другую: это третий источник рядом с файлом и «Суммой»."""
    if m["kind"] in ("счёт", "оплата"):
        why = {"why": "не сравнить: счёт и оплата бывают частью суммы"}
        return {"file": why if e else None, "sum": why}
    if m["vat"] != "без_ндс":
        why = {"why": "не сравнить: НДС не указан" if m["vat"] == "не_указано" else "не сравнить: сумма с НДС"}
        return {"file": why if e else None, "sum": why}
    out: dict = {"file": None}
    if e:
        base = (e.get("kpSale") if e.get("rebid") else e["sale"]) if m["kind"] == "наше_кп" else e["sale"]
        k = _to_sub(m["cur"], e) if m["cur"] != "неясно" else None
        if base is None:
            out["file"] = {"why": "не сравнить: в файле нет продажи «Нашего КП»"}
        elif k is None:
            out["file"] = {"why": "не сравнить: валюта"}
        else:
            out["file"] = _cmp(m["amount"] * k, base)
    amt, tax = _num(r.get("saleAmt")), _num(r.get("saleTax"))
    cur = str(r.get("saleCur") or "").upper()
    if amt is None or tax is None or amt <= 0 or tax < 0 or tax >= amt:
        out["sum"] = {"why": "не сравнить: суммы сделки нет"}
    elif m["cur"] != cur:
        out["sum"] = {"why": "не сравнить: валюта"}
    else:
        out["sum"] = _cmp(m["amount"], amt - tax)
        # налога в сделке нет, а «Сумма» больше чистой суммы чата в 1,20 или 1,22 раза —
        # «Сумма», похоже, с НДС (правило 26.10.2026 вкладки): тот же вердикт, что у сверки
        # с файлом в той же карточке (econ_verdict → ECON_VAT_LIKE), — не пункт (ревью 28.10.2026)
        if out["sum"]["over"] and econ_verdict(amt / m["amount"], tax_empty=not tax) == ECON_VAT_LIKE:
            out["sum"] = {**out["sum"], "over": False, "vat": True}
    return out


def _cmp_text(c: dict | None, what: str) -> str:
    if not c:
        return ""
    if "why" in c:
        return f"{what}: {c['why']}"
    if c.get("vat"):
        return f"{what}: похоже на «Сумму» с НДС, {gap_text(c['pct'])} % — не пункт"
    return f"{what}: {'расходится' if c['over'] else 'сходится'}, {gap_text(c['pct'])} %"


def _msk_day(t: dt.datetime) -> dt.date:
    """День метки по Москве: «сегодня» вкладки — московское, а метка оценки бывает в UTC, и у
    полуночи срок свежести иначе сдвигается на день (ревью 28.10.2026)."""
    return t.astimezone(MSK).date() if t.tzinfo else t.date()


def _dmy(t) -> str:
    return t.strftime("%d.%m.%Y") if t else ""


def _facts_cell(f: dict, r: dict, e: dict | None, today: dt.date, ppl: dict | None = None) -> dict:
    """Факты из чата в карточке сделки (закрытая часть): суммы — строкой «в чате: …» рядом с
    файлом и «Суммой», ни одну не заменяя; договор и сигналы сорсинга — строками;
    «главное» — пункты с хозяином по виду (IMP_OWNER). Свежесть «главного» — IMP_MAX_DAYS."""
    ppl = ppl if ppl is not None else _people(r, "")
    money = []
    for m in f["money"][:CHAT_MONEY_MAX]:
        cmp = chat_money_cmp(m, r, e)
        money.append({
            "line": (f"в чате: {_amt(m['amount'])} {m['cur'] if m['cur'] != 'неясно' else '(валюта не указана)'} · "
                     f"{VAT_LBL[m['vat']]} · {MONEY_LBL[m['kind']]}"
                     + (f", {m['date'].strftime('%d.%m')}" if m["date"] else "")
                     + (f" (сообщение {m['at'].strftime('%d.%m')})" if m["at"] else "")),
            "file": _cmp_text(cmp["file"], "с файлом"), "sum": _cmp_text(cmp["sum"], "с «Суммой» без НДС"),
            "gap": any(isinstance(c, dict) and c.get("over") for c in cmp.values())})
    contract = [(f"договор {CONTRACT_SIDE_LBL[c['side']]}: {CONTRACT_STATUS_LBL[c['status']]}"
                 + (f", {WHERE_LBL[c['where']]}" if WHERE_LBL[c["where"]] else "")
                 + (f", спецификация {c['spec']}" if c["spec"] else "")
                 + (f" ({c['at'].strftime('%d.%m')})" if c["at"] else "")) for c in f["contract"]]
    src = [f"{SIGNAL_LBL[s['signal']]} — {AUTHOR_LBL[s['role']]}" + (f", {s['at'].strftime('%d.%m')}" if s["at"] else "")
           for s in f["src"]]
    out: dict = {"money": money, "contract": contract, "src": src, "imp": None}
    imp = f.get("imp")
    if imp:
        items = [_imp_item(x, r, today, ppl) for x in imp["items"]]
        n = imp.get("n")
        out["imp"] = {
            "at": _dmy(imp["at"]), "src": imp["src"], "conf": round(imp["conf"] * 100) if imp["conf"] is not None else None,
            # свежесть — по дате доказательства пункта, а не по дате вызова (ревью 25.10.2026): пункт
            # истории по сообщениям трёхмесячной давности — не «сегодня»
            "fresh": any(x["fresh"] for x in items) if items else (today - _msk_day(imp["at"])).days <= IMP_MAX_DAYS,
            "items": items,
            # модель вернула больше пунктов, чем прошло проверки: «сняты проверкой», а не «важного нет»
            "dropped": max(0, n - len(items)) if n is not None else 0,
            # была ли у модели сводка сборки (сроки, заказы, договоры, экономика) — или только стадия
            "dg": imp.get("dg")}
    return out


def imp_orders(keys: list[str], r: dict) -> list[dict]:
    """Заказы строки сделки по ключам сводки o1…o8 «главного»: oN — N-я строка заказов сводки
    сделки (dealdigest.build: живые заказы в порядке строки, первые ORDERS_MAX, пустые строки
    выпадают). Сводка пишется каждой сборкой, оценка читает последнюю: порядок живых заказов
    тот же, пока заказ не закрыт и не заведён новый между сборкой и оценкой. Ключа нет или он
    дальше заказов строки — заказа нет."""
    import dealdigest
    live = [o for o in r.get("orders") or [] if o.get("live")][:dealdigest.ORDERS_MAX]
    shown = [o for o in live if dealdigest._order(o)]
    out = []
    for k in keys:
        m = re.fullmatch(r"o([1-9])", k)
        if m and int(m.group(1)) <= len(shown):
            o = shown[int(m.group(1)) - 1]
            if o not in out:
                out.append(o)
    return out


def _imp_item(x: dict, r: dict, today: dt.date, ppl: dict) -> dict:
    """Пункт «главного» с хозяином по регламенту (IMP_OWNER → ork.ITEM): шаг, роль, копия."""
    key = IMP_OWNER[x["kind"]]
    # заказы, на строки сводки которых сослалась модель (o1…o8): один заказ — пункт этого заказа
    # (его менеджер ОРК и шаг), иначе — сделки (E9, E38)
    ords = imp_orders(x.get("keys") or [], r)
    one = ords[0] if len(ords) == 1 else None
    own = _resp(r) if key == "dealResp" else _reg("imp", r, ppl, key=key, order=one)
    who = own["who"]
    ev = x.get("ev")
    return {"kind": x["kind"], "lbl": IMP_LBL[x["kind"]], "sev": x["sev"], "text": x["text"],
            "unit": "order" if one is not None else "deal",
            "order": str(one.get("id")) if one is not None else "", "ordLbl": (one.get("lbl") or "") if one else "",
            "orders": [{"order": str(o.get("id")), "lbl": o.get("lbl") or ""} for o in ords],
            "role": own["roleName"], "roleKey": own["role"], "step": own["step"], "who": who,
            "whoGone": _gone(who), "copy": own["escalateTo"], "copyRole": own["copyRole"],
            **({"alsoCopy": own["alsoCopy"], "alsoCopyRole": own["alsoCopyRole"]} if own.get("alsoCopy") else {}),
            "basis": list(x["keys"]),
            "ev": _msk_day(ev).strftime("%d.%m.%Y") if ev else "",
            "fresh": bool(ev) and (today - _msk_day(ev)).days <= IMP_MAX_DAYS}


def build(rows: list[dict], life: dict | None, ctx: dict | None,
          now: dt.datetime, today: dt.date | None = None, *, ork_head: str = "") -> tuple[dict | None, dict | None]:
    """→ (числа плиток для head, закрытая часть DEALCTX). Без свода — (None, None).

    rows — строки сделок contracts.compute (после decide), life — его раздел «life» вместе
    с очередью отдельных направлений (contracts.life_all): «что делать первым» по сделке;
    today — тот же день, что у contracts.compute (as_of): иначе строка и ячейка одной
    сделки судили бы свежесть по разным дням.

    Отдельное направление (строка с seg, решение владельца 25.10.2026) считается в свои
    плитки — head["sep"] — и свои счётчики очереди (sepCounts): в плитки и счётчики
    основного вида его сделки не входят. Пункты очереди помечены seg.

    Хозяин пункта — по регламенту ОРК (решение владельца 26.10.2026, п. 1; ork.ITEM): срок из
    чата — по виду (отгрузка и документы — менеджер ОРК, доставка и оплата — КАМ), риск по
    чату — менеджер ОРК, КАМ в копии; ОСС их не получает (Рег. 1.11). Договор заказчика не
    найден — КАМ, поставщика — менеджер ОРК; риск сверки договоров — руководитель ОРК
    (ork_head — по справочнику отделов, иначе подпись роли). Ответ в чате — тот, на ком мяч по
    оценке; «Сумма» и файл договора — ответственный сделки.

    Сделка и заказ (спецификация 30.10.2026, §1.6, E9, E10): пункт сделки (unit = deal) — у роли
    сделки; где таблица даёт менеджера ОРК, хозяин — руководитель ОРК, менеджеры заказов — в
    копии списком; шаг — шаг сделки (_step_now). Флаг сверки договоров с заказом — пункт этого
    заказа (unit = order: номер, поставщик, шаг и менеджер ОРК заказа)."""
    if ctx is None:
        return None, None
    now = now.astimezone(MSK)
    today = today or now.date()
    live = [r for r in rows if not r.get("closed")]
    chats, cons, econ, facts = ctx["chats"], ctx["contracts"], ctx.get("econ") or {}, ctx.get("facts") or {}
    deals: dict[str, dict] = {}
    queue: list[dict] = []
    # «главное» — не пункт очереди: в queue, counts, «что делать первым» и итоги не идёт
    important: dict[bool, list[dict]] = {False: [], True: []}
    by_seg: dict[bool, Counter] = {False: Counter(), True: Counter()}     # основной вид / направление
    for r in live:
        did = str(r["deal"])
        rec: dict = {}
        ppl = _people(r, ork_head)
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
                    ball = cur["ball"]
                    own = (_by_person(CHAT_ROLE[ball], _person(r, ball), r)
                           if ball in OUR_ROLES and ball in CHAT_ROLE
                           else _reg("chatRisk", r, ppl))      # мяч не определён — менеджер ОРК, КАМ в копии
                    h = work_hours(cur["human"], now) if cur["human"] else None
                    if h is not None and h > REPLY_HOURS:
                        n["replyLate"] += 1
                        queue.append(_item("chatReply", r, own, days=(today - cur["human"].date()).days,
                                           conf=cur.get("conf")))
                if cur["risk"] == "высокий":
                    n["high"] += 1
                    kinds = ", ".join(k.replace("_", " ") for k in cur["kinds"])
                    queue.append(_item("chatRisk", r, _reg("chatRisk", r, ppl),
                                       act="разобрать риск" + (f": {kinds}" if kinds else ""),
                                       conf=cur.get("conf")))
                due = cur.get("due")
                if due and due < today and (today - due).days <= DUE_MAX_DAYS:
                    n["dueLate"] += 1
                    queue.append(_item("chatDue", r, _reg("chatDue", r, ppl, key=f"chatDue:{cur['dueKind']}"),
                                       act=f"подтвердить {DUE_ACC[cur['dueKind']]} у второй стороны "
                                           f"(срок был {due.strftime('%d.%m')})",
                                       days=(today - due).days, conf=cur.get("conf")))
        c = cons.get(did)
        if c:
            n["contractSeen"] += 1
            cell = _contract_cell(c, r)
            rec["contract"] = cell
            n["outcome:" + c["outcome"]] += 1
            if cell["flags"]:
                n["risk"] += 1
                n["flags"] += len(cell["flags"])
                # флаг с заказом — пункт ЭТОГО заказа (номер, поставщик, шаг заказа; E10): один
                # пункт на заказ; флаги без заказа (или с заказом не этой сделки) — один пункт сделки
                by_ord: dict[str, list] = {}
                for f in c["flags"]:
                    o = _flag_order(f, r)
                    by_ord.setdefault(str(o["id"]) if o else "", []).append(f)
                o_by_id = {str(o.get("id")): o for o in r.get("orders") or []}
                for oid, fl in by_ord.items():
                    shorts = ", ".join(dict.fromkeys(FLAG_SHORT.get(f["code"], f["code"]) for f in fl))
                    if oid:
                        o = o_by_id[oid]
                        queue.append(_item("contractRisk", r, _reg("contractRisk", r, ppl, order=o),
                                           act=f"допсоглашение с поставщиком по заказу {o.get('lbl') or oid}: {shorts}",
                                           order=o))
                    else:
                        queue.append(_item("contractRisk", r, _reg("contractRisk", r, ppl),
                                           what=f"{EXC['contractRisk'][1]} — {ORDER_UNKNOWN}",
                                           act="допсоглашение с поставщиком: " + shorts))
            if c["outcome"] == UNREADABLE:
                # файла нет у портала (404, пуст), формат не читается или длиннее предела чтения —
                # следующий прогон не вылечит: перезалить — тому же, кто ищет договор
                queue.append(_item("contractUnreadable", r, _resp(r)))
            if cell["missing"]:
                n["nopair"] += 1
                # договор заказчика — с выигрыша сделки; поставщика — когда заказ размещён
                need = [s for s in cell["missing"] if s == "customer" or (r.get("norders") or 0) > 0]
                if need:
                    whose = " и ".join(SIDE_LBL[s] for s in need)
                    side = "both" if len(need) > 1 else need[0]
                    # разбор договоров не говорит, у какого заказа нет договора поставщика: пункт
                    # сделки, заказ не определён (E10)
                    queue.append(_item("contractMissing", r, _reg("contractMissing", r, ppl,
                                                                  key=f"contractMissing:{side}"),
                                       what=SIDE_MISSING.format(whose=whose)
                                       + (f" — {ORDER_UNKNOWN}" if "supplier" in need else ""),
                                       act=f"найти договор {whose} и загрузить файл в карточку",
                                       key=f"contractMissing:{side}"))
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
                queue.append(_item("econGap", r, _resp(r),
                                   what=("Сумма сделки — продажа «Нашего КП» до переторжки: расходится с "
                                         if cell["preRebid"] else "Сумма сделки расходится с ")
                                   + f"финальной экономикой на {gap_text(cell['gap'])} %"))
        fx = facts.get(did)
        if fx:
            cell = _facts_cell(fx, r, e, today, ppl)
            rec["facts"] = cell
            n["factsMoney"] += bool(cell["money"])
            n["factsGap"] += any(m["gap"] for m in cell["money"])
            imp = cell["imp"]
            свежие = [(x, raw) for x, raw in zip(imp["items"], fx["imp"]["items"]) if x["fresh"]] if imp else []
            if свежие:
                n["imp"] += 1
                n["impHigh"] += any(x["sev"] == "высокая" for x, _raw in свежие)
                for x, raw in свежие:
                    important[bool(r.get("seg"))].append({
                        **x, "deal": r["deal"], "seq": r.get("seq") or 0, "title": r.get("title") or "",
                        "customer": r.get("customer") or "—", "at": imp["at"], "conf": imp["conf"],
                        "src": imp["src"], "dg": imp["dg"], "_t": raw["ev"]})
        if rec:
            deals[did] = rec
    queue.sort(key=lambda q: (q["sev"], -(q["days"] or 0), -q["seq"]))
    person_ids(queue, (life or {}).get("queue") or [], rows)
    # что делать первым по сделке — общий порядок пунктов lifecycle и этих
    first: dict[str, dict] = {}
    for q in sorted(list((life or {}).get("queue") or []) + queue,
                    key=lambda q: (q["sev"], -(q["days"] or 0))):
        did = str(q["deal"])
        if did not in first:
            # пункт заказа — с номером заказа (E38: «у „что делать первым“ — order»)
            first[did] = {"who": q["who"], "act": q["act"], "what": q["what"], "code": q["code"],
                          "unit": q.get("unit") or ("order" if q.get("order") else "deal"),
                          "order": str(q.get("order") or ""), "ordLbl": q.get("ordLbl") or ""}
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
            # факты из чата — только покрытие (есть у N): ни сумм, ни чисел оценок модели — расхождения и
            # высокая важность — в закрытой части (payload["factsClosed"]; ревью 25.10.2026)
            "facts": {"money": n["factsMoney"], "imp": n["imp"]},
            "rules": {"freshDays": FRESH_DAYS, "replyHours": REPLY_HOURS, "dueMaxDays": DUE_MAX_DAYS,
                      "econGapPct": round(ECON_GAP * 100), "impMaxDays": IMP_MAX_DAYS},
        }
    n_sep = sum(1 for r in live if r.get("seg"))
    head = _head(by_seg[False], len(live) - n_sep)
    if n_sep:
        head["sep"] = _head(by_seg[True], n_sep)

    def _top(items: list[dict]) -> list[dict]:
        # важность, свежесть оценки, номер сделки (новые выше)
        items = sorted(items, key=lambda x: (IMP_SEV.index(x["sev"]), -x["_t"].timestamp(), -x["seq"]))
        return [{k: v for k, v in x.items() if k != "_t"} for x in items[:IMP_TOP]]
    payload = {"at": head["at"], "deals": deals, "queue": queue,
               "labels": {k: v[1] for k, v in EXC.items()},
               # counts — пункты по коду (прежнее, на один выпуск: тесты и KV); в своих единицах —
               # ordCounts (разных заказов), dealCounts (разных сделок), bucketCounts — по частям
               # очереди (спецификация §4.3). Шаблон читает их, а не counts
               "counts": dict(Counter(q["code"] for q in queue if not q.get("seg"))),
               "sepCounts": dict(Counter(q["code"] for q in queue if q.get("seg"))),
               **_unit_payload([q for q in queue if not q.get("seg")], ""),
               **_unit_payload([q for q in queue if q.get("seg")], "sep"),
               # «Что важно сегодня» руководителю реализации — основной вид и вкладка направления
               "important": _top(important[False]), "sepImportant": _top(important[True]),
               "impLabels": dict(IMP_LBL),
               # числа оценок модели — только закрытой части: сумма в чате расходится с файлом или
               # «Суммой», «главное» высокой важности (у скольких сделок)
               "factsClosed": {"main": {"gap": by_seg[False]["factsGap"], "impHigh": by_seg[False]["impHigh"]},
                               "sep": {"gap": by_seg[True]["factsGap"], "impHigh": by_seg[True]["impHigh"]}}}
    return head, payload
