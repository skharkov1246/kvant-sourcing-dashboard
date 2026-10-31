#!/usr/bin/env python3
"""Очередь звонилки поставщикам: кому звонить и что сказать — закрытый ключ KV callq:v1.

ЗАЧЕМ (владелец, 31.10.2026). Через месяц без цены остаются 75 % запросов СП-166, из них треть
— «отправлено, ответа нет» (зонд v62). Регламент велит звонить после 2 рабочих дней молчания,
но очереди звонков нет. Звонит голосовой агент на английском, со ссылкой на наше письмо
(callbot/agent). Этот скрипт строит очередь; звонки запускает обработчик портала (следующий шаг).

КОМУ ЗВОНИТЬ (правила — таблицей ПРАВИЛА, правило 4).
  • Карточка СП-166 воронки сорсинга, заведена не раньше ПРАВИЛА["возраст_дней"] назад, файла КП
    в «Offer from supplier» нет (правило 19.10.2026), и одна из причин:
      sent    — стадия «Отправлено» (PREPARATION), наше последнее письмо по карточке
                ПРАВИЛА["молчание_рд"] рабочих дней назад и раньше (календарь РФ — норма регламента);
      dialog  — переписка без цены (UC_61BSRU, UC_GFJ5A8): последнее письмо в любую сторону
                ПРАВИЛА["переписка_рд"] рабочих дней назад и раньше;
      promise — наступил день после даты «КП обещано на» (поле карточки — переменная окружения
                CALL_PROMISE_FIELD; пока поля нет — причина не работает).
  • Письмо — дело CRM_EMAIL на карточке. Нет нашего письма в CRM — звонить не с чем (ссылаться не
    на что): строка считается, в очередь не идёт.
  • Поставщик — поле «Supplier» (ufCrm18Supplier), затем «Supplier contact» и компании их контактов;
    companyId карточки — последним и только если это не компания сделки: он бывает унаследован от
    сделки, то есть это заказчик (зонд v61). Заказчику звонилка не звонит никогда.
  • Телефон — у контакта поставщика, иначе у компании поставщика; приводится к виду
    +<код страны> библиотекой phonenumbers; невалидный — считается и не идёт. Страна и часовой
    пояс — по номеру; номер-отправитель — по стране (ПУЛ_НОМЕРОВ).
  • Не звонить: поле компании CALL_DNC_FIELD = «Да»; попыток (поле карточки CALL_ATTEMPTS_FIELD)
    не меньше ПРАВИЛА["попыток_макс"]. Пока полей нет — проверки нет (счётчик печатается).
  • Слепое сравнение моделей: компания навсегда попадает в группу gpt, eleven или control (arm_of);
    по control не звоним — она мерит, сколько КП пришло бы без звонков.
  • Один звонок в день на компанию: все её молчащие запросы — в одном звонке, не больше
    ПРАВИЛА["запросов_в_звонке"], старейшие первыми; остальные — в следующий день.

ЧТО ЛЕЖИТ В КЛЮЧЕ (закрытый, KV портала): по звонку — компания, контакт, телефон, страна, пояс,
пул номера-отправителя и запросы (номер RFQ, тема, дата письма, адрес отправителя и получателя,
причина, сорсер), плюс готовые переменные для агента (agent_vars). В журнал — только счётчики,
коды стран и пулов: ни названий, ни телефонов, ни адресов (правило 17).

ЧАСТИ. Чтение писем — частями по номерам карточек (вход PARTS, ≥ 10, распоряжение 22.09.2026).

    python scripts/call_queue.py            # холостой: только счётчики
    python scripts/call_queue.py --write    # то же и запись в KV callq:v1
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

MSK = dt.timezone(dt.timedelta(hours=3))
KV_KEY = "callq:v1"
VERSION = 1
# Правила — таблицей (правило 4): изменение — правка одной строки с причиной.
ПРАВИЛА = {
    "возраст_дней": 90,        # карточки старше — запрос умер, звонить поздно
    "молчание_рд": 2,          # регламент сорсинга: молчание 2 р.д. — дальше телефон
    "переписка_рд": 5,         # переписка без цены дольше — дожим
    "попыток_макс": 3,         # звонков на запрос, дальше «Ответ не получен (в срок)»
    "запросов_в_звонке": 5,    # больше агент не удержит в одном разговоре
}
SENT = {"PREPARATION"}                     # stages.py: sent
DIALOG = {"UC_61BSRU", "UC_GFJ5A8"}        # stages.py: dialog
OUT, IN = "2", "1"                         # DIRECTION дела: исходящее, входящее
# Номер-отправитель по стране поставщика: код пула → номер берёт обработчик из настроек
# (сами номера в репозиторий не попадают). Нет страны в таблице — пул по умолчанию.
ПУЛ_НОМЕРОВ = {
    **{c: "HK" for c in ("CN", "HK", "MO", "TW")},
    **{c: "SG" for c in ("SG", "MY", "TH", "VN", "ID", "PH", "KH", "MM", "LA", "BN")},
    **{c: "GB" for c in ("AE", "SA", "QA", "OM", "KW", "BH", "IN", "TR")},
}
ПУЛ_ПО_УМОЛЧАНИЮ = "GB"
# Кому звоним (владелец, 31.10.2026: «Китай / Дубай / Юго-Восточная Азия»): страна — по номеру
# телефона. Холостой прогон 31.10 показал, что без фильтра в очередь идут Россия (205 звонков),
# Индия (196), ЕС и США — вне задачи пилота. Переменная CALL_COUNTRIES: список кодов через запятую
# («CN,HK,AE») или «*» — все страны.
СТРАНЫ_ПИЛОТА = frozenset(("CN", "HK", "MO", "TW", "AE", "SG", "MY", "TH", "VN", "ID", "PH", "KH", "MM", "LA", "BN"))


def parse_countries(spec: str) -> frozenset | None:
    """«CN,HK,AE» → набор; «*» → None (все страны); пусто или мусор → СТРАНЫ_ПИЛОТА."""
    spec = str(spec or "").strip()
    if spec == "*":
        return None
    got = frozenset(x.strip().upper() for x in spec.split(",") if re.fullmatch(r"[A-Za-z]{2}", x.strip()))
    return got or СТРАНЫ_ПИЛОТА
# Объявление об ИИ — по стране номера (решение владельца 31.10.2026: в начале не объявлять):
# «start» — фраза в начале (ЕС: AI Act ст. 50 с 02.08.2026; США), «end» — короткая фраза в
# конце (Китай: правила маркировки ИИ-контента с 01.09.2025 — до заключения юриста), «off».
# Слепое сравнение моделей (решение владельца 31.10.2026: GPT-Realtime-2 против ElevenLabs, по сотне
# разговоров каждой) и контроль без звонков. Группа — по компании, навсегда (хеш номера компании с
# солью пилота): один поставщик не слышит разные голоса, попытки одного запроса идут одной моделью.
# Доли — переменная CALL_ARMS «gpt:1,eleven:1,control:1»; новая соль — новый пилот.
ГРУППЫ_ПО_УМОЛЧАНИЮ = "gpt:1,eleven:1,control:1"
СОЛЬ_ПИЛОТА = "pilot-2026-11"


def parse_arms(spec: str) -> list[tuple[str, int]]:
    """«gpt:1,eleven:1,control:1» → [(группа, вес)]; мусор и нулевые веса отбрасываются."""
    out = []
    for part in str(spec or "").split(","):
        name, _, w = part.strip().partition(":")
        if re.fullmatch(r"[a-z]+", name or "") and w.strip().isdigit() and int(w) > 0:
            out.append((name, int(w)))
    return out or parse_arms(ГРУППЫ_ПО_УМОЛЧАНИЮ)


def arm_of(company_id: str, arms: list[tuple[str, int]], salt: str = СОЛЬ_ПИЛОТА) -> str:
    """Группа компании: детерминированно по хешу, пропорционально весам."""
    h = int(hashlib.sha256(f"{salt}:{company_id}".encode()).hexdigest()[:12], 16)
    x = h % sum(w for _, w in arms)
    for name, w in arms:
        if x < w:
            return name
        x -= w
    return arms[-1][0]


ЕС = ("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE "
      "IS LI NO").split()
ОБЪЯВЛЕНИЕ = {**{c: "start" for c in ЕС + ["US", "CA", "GB"]}, **{c: "end" for c in ("CN", "HK", "MO")}}


# ───────────────────────────── разбор ─────────────────────────────

def ts(v) -> dt.datetime | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=MSK)


def phone_e164(raw) -> tuple[str, str, str] | None:
    """Телефон из Битрикса → (+E164, страна ISO, часовой пояс IANA) или None. Номер без кода
    страны не угадывается: «8 495…» и «0…» без «+» и «00» — невалидны."""
    import phonenumbers
    from phonenumbers import timezone as pn_tz
    s = re.sub(r"[^\d+]", "", str(raw or ""))
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        # без «+» и «00» код страны берётся только из самих цифр; ведущие 8 и 0 — внутренний формат
        # (российское «8 495…» иначе читалось бы как Вьетнам +84): такой номер не угадываем
        s = "+" + s if (len(s) >= 11 and s[0] not in "08") else ""
    if not s:
        return None
    try:
        n = phonenumbers.parse(s, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(n):
        return None
    region = phonenumbers.region_code_for_number(n) or ""
    zones = [z for z in pn_tz.time_zones_for_number(n) if z and z != "Etc/Unknown"]
    return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164), region, (zones[0] if zones else "")


def multifield(values) -> list[str]:
    """PHONE/EMAIL Битрикса: [{"VALUE": …, "VALUE_TYPE": …}] → значения (мобильный первым)."""
    out = [v for v in (values or []) if isinstance(v, dict) and v.get("VALUE")]
    out.sort(key=lambda v: 0 if str(v.get("VALUE_TYPE") or "").upper() == "MOBILE" else 1)
    return [str(v["VALUE"]) for v in out]


def email_meta(a: dict) -> dict:
    """Дело-письмо → {dir, t, subject, from, to}. Адреса — как в письме (только в закрытый ключ)."""
    s = a.get("SETTINGS") if isinstance(a.get("SETTINGS"), dict) else {}
    meta = s.get("EMAIL_META") if isinstance(s.get("EMAIL_META"), dict) else {}
    comm = [c.get("VALUE") for c in (a.get("COMMUNICATIONS") or []) if isinstance(c, dict) and c.get("VALUE")]
    to = str(meta.get("to") or meta.get("__to") or (comm[0] if comm else "") or "")
    return {"dir": str(a.get("DIRECTION") or ""), "t": ts(a.get("CREATED")), "subject": str(a.get("SUBJECT") or "").strip(),
            "from": str(meta.get("from") or "").strip(), "to": to.strip()}


def last_emails(acts: list[dict]) -> dict[str, dict]:
    """Карточка → {"out": последнее исходящее, "any": время последнего письма в любую сторону}."""
    out: dict[str, dict] = {}
    for a in acts:
        cid = str(a.get("OWNER_ID") or "")
        m = email_meta(a)
        if not cid or not m["t"]:
            continue
        x = out.setdefault(cid, {"out": None, "any": None})
        if x["any"] is None or m["t"] > x["any"]:
            x["any"] = m["t"]
        if m["dir"] == OUT and (x["out"] is None or m["t"] > x["out"]["t"]):
            x["out"] = m
    return out


def reason(card: dict, mail: dict | None, today: dt.date, workdays, promise_field: str = "") -> str:
    """Причина звонка (sent / dialog / promise) или пусто. workdays(a, b) — рабочих дней между датами."""
    stage = str(card.get("stageId") or "").split(":")[-1]
    if promise_field:
        p = ts(card.get(promise_field))
        if p and p.astimezone(MSK).date() < today:
            return "promise"
    if not mail or not mail.get("out"):
        return ""
    if stage in SENT:
        n = workdays(mail["out"]["t"].astimezone(MSK).date(), today)
        return "sent" if n is not None and n >= ПРАВИЛА["молчание_рд"] else ""
    if stage in DIALOG and mail.get("any"):
        n = workdays(mail["any"].astimezone(MSK).date(), today)
        return "dialog" if n is not None and n >= ПРАВИЛА["переписка_рд"] else ""
    return ""


def num_id(v) -> str:
    """Номер записи CRM → строка ASCII-цифр без ведущих нулей или «». Пустое поле, None, 0, «None»,
    дробь, надстрочные цифры — «»: str(None) == «None» уже валил прогон (карточка без сделки)."""
    if isinstance(v, bool):
        return ""
    if isinstance(v, int):
        return str(v) if v > 0 else ""
    s = str(v if v is not None else "").strip()
    if not re.fullmatch(r"[0-9]{1,18}", s):
        return ""
    return str(int(s)) if int(s) > 0 else ""


def crm_refs(val) -> tuple[list[str], list[str]]:
    """Значение crm-поля (['CO_5', 'C_7', 'CT_8', '9']) → (компании, контакты) в порядке поля;
    то же правило, что probe_deal_rfq.ссылки_crm и main._crm_ref_ids."""
    comps: list[str] = []
    conts: list[str] = []
    for x in (val if isinstance(val, list) else [val] if val else []):
        s = str(x).strip()
        if s.startswith("CO_"):
            comps.append(s[3:])
        elif s.startswith("CT_"):
            conts.append(s[3:])
        elif s.startswith("C_"):
            conts.append(s[2:])
        else:
            comps.append(s)
    comps = [num_id(c) for c in comps]
    conts = [num_id(c) for c in conts]
    return [c for c in dict.fromkeys(comps) if c], [c for c in dict.fromkeys(conts) if c]


def supplier_of(card: dict, deal_company: str, contact_company: dict[str, str]) -> tuple[str, list[str]]:
    """Поставщик карточки → (компания, контакты). companyId карточки бывает унаследован от сделки —
    это заказчик (зонд v61), поэтому порядок: поле «Supplier», «Supplier contact», компании их
    контактов и только потом companyId; компания сделки поставщиком не бывает никогда."""
    c1, t1 = crm_refs(card.get("ufCrm18Supplier"))
    c2, t2 = crm_refs(card.get("ufCrm18SupplContact"))
    conts = list(dict.fromkeys(t1 + t2))
    comps = c1 + c2 + [num_id(contact_company.get(t)) for t in conts] + [num_id(card.get("companyId"))]
    comps = [c for c in dict.fromkeys(comps) if c and c != num_id(deal_company)]
    return (comps[0] if comps else ""), conts


def rfq_no(card: dict) -> str:
    """Номер запроса, как в теме письма: <сделка>-<карточка> (навык supplier-rfq)."""
    d = num_id(card.get("parentId2"))
    return f"{d}-{card.get('id')}" if d else str(card.get("id"))


МЕСЯЦЫ = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def human_date(t: dt.datetime | None) -> str:
    """Дата письма для агента по-английски, без зависимости от локали: «21 October 2026»."""
    if not t:
        return ""
    d = t.astimezone(MSK)
    return f"{d.day} {МЕСЯЦЫ[d.month - 1]} {d.year}"


def agent_vars(call: dict) -> dict:
    """Переменные для агента (callbot/agent/system_prompt.md). Наша компания, имя агента и номер
    подставляет обработчик из настроек — в очереди их нет."""
    lines = []
    for i, r in enumerate(call["requests"], 1):
        lines.append(f'{i}. RFQ {r["rfq_no"]}, subject "{r["subject"]}", emailed on {r["email_date"]} '
                     f'from {r["email_from"] or "our purchasing mailbox"} to {r["email_to"] or "your company email"}')
    return {"supplier_company": call["company"], "contact_name": call["contact_name"] or "the person responsible for quotations",
            "requests_count": str(len(call["requests"])), "requests_block": "\n".join(lines),
            "first_rfq_no": call["requests"][0]["rfq_no"], "first_email_date": call["requests"][0]["email_date"],
            "first_subject": call["requests"][0]["subject"], "first_email_from": call["requests"][0]["email_from"],
            "disclosure": ОБЪЯВЛЕНИЕ.get(call["country"], "off")}


AGENT_DIR = ROOT / "callbot" / "agent"


def template(name: str) -> str:
    """Текст агента из callbot/agent без служебных комментариев и заголовка файла."""
    t = (AGENT_DIR / name).read_text(encoding="utf-8")
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"\A\s*#[^\n]*\n", "", t)
    return t.strip() + "\n"


def render(tpl: str, values: dict) -> tuple[str, list[str]]:
    """{{ключ}} → значение; → (текст, неподставленные ключи)."""
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), tpl)
    return out, sorted(set(re.findall(r"\{\{(\w+)\}\}", out)))


def call_texts(call: dict, settings: dict) -> tuple[str, str, list[str]]:
    """Готовые промпт и текст автоответчика звонка: переменные очереди + настройки (наша компания,
    имя агента) + фраза об ИИ по стране. → (промпт, автоответчик, неподставленные ключи)."""
    disc = json.loads((AGENT_DIR / "disclosure.json").read_text(encoding="utf-8"))[call["agent_vars"]["disclosure"]]
    values = {**call["agent_vars"], **settings, "opening_disclosure": disc["opening"],
              "closing_disclosure": disc["closing"], "closing_spoken": disc["closing_spoken"]}
    prompt, miss1 = render(template("system_prompt.md"), values)
    vm, miss2 = render(template("voicemail.md"), values)
    return prompt, vm, sorted(set(miss1) | set(miss2))


def build(cards: list[dict], mails: dict[str, dict], contacts: dict[str, dict], companies: dict[str, dict],
          today: dt.date, workdays, fields: dict | None = None, settings: dict | None = None,
          arms: list[tuple[str, int]] | None = None,
          deal_company: dict[str, str] | None = None,
          countries: frozenset | None = СТРАНЫ_ПИЛОТА) -> tuple[list[dict], Counter]:
    """Звонки на сегодня и счётчики. contacts — {id: {name, phones:[…], company}};
    companies — {id: {name, phones:[…], dnc}}; deal_company — сделка → её компания (заказчик:
    поставщиком не бывает);
    settings — {our_company, agent_name}: есть — к звонку прикладываются готовые тексты агента;
    arms — группы слепого сравнения (parse_arms): у звонка поле arm; «control» обработчик не набирает;
    countries — страны пилота по номеру телефона (None — все)."""
    f = fields or {}
    arms = arms or parse_arms(ГРУППЫ_ПО_УМОЛЧАНИЮ)
    deal_company = deal_company or {}
    contact_company = {k: str(v.get("company") or "") for k, v in contacts.items()}
    st: Counter = Counter()
    by_co: dict[str, list[tuple[dict, dict, str, list[str]]]] = defaultdict(list)
    for c in cards:
        st["карточек"] += 1
        if c.get("_offer"):
            st["есть КП — не звонить"] += 1
            continue
        m = mails.get(str(c.get("id")))
        why = reason(c, m, today, workdays, f.get("promise", ""))
        if not why:
            st["нет нашего письма в CRM" if not (m and m.get("out")) else "срок не наступил"] += 1
            continue
        if f.get("attempts") and int(num_id(c.get(f["attempts"])) or 0) >= ПРАВИЛА["попыток_макс"]:
            st["попыток исчерпано"] += 1
            continue
        co, conts = supplier_of(c, deal_company.get(num_id(c.get("parentId2")), ""), contact_company)
        if not co and not conts:
            st["поставщик не указан"] += 1
            continue
        if num_id(c.get("companyId")) not in ("", co):
            st["компания карточки — не поставщик (взят из поля Supplier)"] += 1
        co = co or f"ct:{conts[0]}"
        if (companies.get(co) or {}).get("dnc"):
            st["компания: не звонить"] += 1
            continue
        st[f"причина {why}"] += 1
        by_co[co].append((c, m or {}, why, conts))
    calls: list[dict] = []
    for co, items in by_co.items():
        comp = companies.get(co) or {}
        phone = None
        contact_name = ""
        for _, _, _, conts in items:           # телефон контакта поставщика — первым
            for t in conts:
                ct = contacts.get(t) or {}
                for p in ct.get("phones") or []:
                    phone = phone_e164(p)
                    if phone:
                        contact_name = ct.get("name") or ""
                        break
                if phone:
                    break
            if phone:
                break
        if not phone:
            for p in comp.get("phones") or []:
                phone = phone_e164(p)
                if phone:
                    break
        if not phone:
            st["нет годного телефона (компаний)"] += 1
            continue
        e164, country, tz = phone
        if countries is not None and country not in countries:
            st[f"вне пилота: страна {country or '??'}"] += 1
            st["вне пилота (компаний)"] += 1
            continue
        items.sort(key=lambda x: (x[1].get("out") or {}).get("t") or dt.datetime.max.replace(tzinfo=MSK))
        reqs = []
        for c, m, why, _ in items[:ПРАВИЛА["запросов_в_звонке"]]:
            o = m.get("out") or {}
            reqs.append({"card": str(c.get("id")), "rfq_no": rfq_no(c), "subject": o.get("subject") or str(c.get("title") or ""),
                         "email_date": human_date(o.get("t")), "email_from": o.get("from", ""), "email_to": o.get("to", ""),
                         "reason": why, "sourcer": str(c.get("assignedById") or "")})
        st["отложено на завтра (больше 5 в звонке)"] += max(0, len(items) - ПРАВИЛА["запросов_в_звонке"])
        call = {"company_id": co, "company": comp.get("name") or f"company #{co}", "contact_name": contact_name,
                "phone": e164, "country": country, "tz": tz, "from_pool": ПУЛ_НОМЕРОВ.get(country, ПУЛ_ПО_УМОЛЧАНИЮ),
                "arm": arm_of(co, arms), "requests": reqs}
        call["agent_vars"] = agent_vars(call)
        if settings and settings.get("our_company") and settings.get("agent_name"):
            call["prompt"], call["voicemail"], miss = call_texts(call, settings)
            if miss:
                st["тексты: неподставленные переменные"] += 1
        calls.append(call)
        st[f"страна {country or '??'}"] += 1
        st[f"пул {call['from_pool']}"] += 1
        st[f"группа {call['arm']}"] += 1
    st["звонков"] = len(calls)
    st["запросов в звонках"] = sum(len(c["requests"]) for c in calls)
    return calls, st


# ───────────────────────────── ввод-вывод ─────────────────────────────

def _parts(seq: list, parts: int) -> list[list]:
    """Ровно одно разбиение на parts почти равных смежных частей (распоряжение 22.09.2026)."""
    parts = max(1, min(parts, len(seq))) if seq else 1
    k, m = divmod(len(seq), parts)
    out, i = [], 0
    for j in range(parts):
        n = k + (1 if j < m else 0)
        out.append(seq[i:i + n])
        i += n
    return [x for x in out if x]


def read_by_ids(client, method: str, flt_key: str, ids: list[str], select: list[str], extra: dict | None = None) -> list[dict]:
    """crm.*.list по фильтру номеров пачками по 50, по ключу >ID; фильтр сверяется на каждой
    странице. У crm.activity.list номера — массивом в OWNER_ID (так probe_deal_rfq), у компаний и
    контактов — @ID (так bitrix_client.companies_by_ids)."""
    out = []
    for i in range(0, len(ids), 50):
        part = [int(x) for x in ids[i:i + 50]]
        own = {str(x) for x in part}
        last = 0
        while True:
            flt = {flt_key: part, ">ID": last, **(extra or {})}
            ch = client.call(method, {"filter": flt, "select": select, "order": {"ID": "ASC"}, "start": -1}) or []
            if isinstance(ch, dict):
                ch = list(ch.values())
            if not ch:
                break
            col = flt_key.lstrip("@")
            if any(str(x.get(col) or "") not in own for x in ch):
                raise RuntimeError(f"фильтр {flt_key} {method} не сработал")
            out += ch
            last = int(ch[-1]["ID"])
            if len(ch) < 50:
                break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Очередь звонилки поставщикам → KV callq:v1")
    ap.add_argument("--write", action="store_true", help="записать очередь в KV (без флага — холостой)")
    ap.add_argument("--parts", type=int, default=int(os.environ.get("PARTS") or 10))
    a = ap.parse_args(argv)

    import config
    import period
    from bitrix_client import BitrixClient

    today = dt.datetime.now(MSK).date()
    since = (dt.datetime.now(MSK) - dt.timedelta(days=ПРАВИЛА["возраст_дней"])).isoformat()
    fields = {k: os.environ.get(v, "").strip() for k, v in (("promise", "CALL_PROMISE_FIELD"),
                                                              ("attempts", "CALL_ATTEMPTS_FIELD"),
                                                              ("dnc", "CALL_DNC_FIELD"))}
    print("поля звонилки: " + ", ".join(f"{k} {'есть' if v else 'нет'}" for k, v in fields.items()))
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    cat = config.SPA_CATEGORY_ID
    stages = [f"DT{config.SPA_ENTITY_TYPE_ID}_{cat}:{s}" for s in sorted(SENT | DIALOG)]
    sel = ["id", "title", "stageId", "companyId", "parentId2", "ufCrm18Supplier", "ufCrm18SupplContact", "createdTime",
           "assignedById",
           config.RFQ_OFFER_FIELD] + [v for k, v in fields.items() if v and k != "dnc"]
    cards = client.list_items(config.SPA_ENTITY_TYPE_ID, select=sel,
                              filter={"categoryId": cat, "@stageId": stages, ">=createdTime": since})
    if fields["promise"]:
        seen = {str(c["id"]) for c in cards}
        cards += [c for c in client.list_items(config.SPA_ENTITY_TYPE_ID, select=sel, filter={
            "categoryId": cat, f"<{fields['promise']}": today.isoformat(), ">=createdTime": since})
            if str(c["id"]) not in seen]
    for c in cards:
        c["_offer"] = bool(c.get(config.RFQ_OFFER_FIELD))
    print(f"карточек в работе за {ПРАВИЛА['возраст_дней']} дн.: {len(cards)}")

    ids = sorted({num_id(c.get("id")) for c in cards if not c["_offer"]} - {""}, key=int)
    acts: list[dict] = []
    for part in _parts(ids, max(10, a.parts)):
        acts += read_by_ids(client, "crm.activity.list", "OWNER_ID", part,
                            ["ID", "OWNER_ID", "DIRECTION", "CREATED", "SUBJECT", "SETTINGS", "COMMUNICATIONS"],
                            {"OWNER_TYPE_ID": config.SPA_ENTITY_TYPE_ID, "PROVIDER_ID": "CRM_EMAIL"})
    mails = last_emails(acts)
    print(f"писем на карточках: {len(acts)}; карточек с нашим письмом: {sum(1 for m in mails.values() if m['out'])}")

    live = [c for c in cards if not c["_offer"]]
    ct_ids, co_ids = set(), set()
    for c in live:
        for fld in ("ufCrm18Supplier", "ufCrm18SupplContact"):
            cs, ts_ = crm_refs(c.get(fld))
            co_ids |= set(cs)
            ct_ids |= set(ts_)
        if num_id(c.get("companyId")):
            co_ids.add(num_id(c.get("companyId")))
    contacts = {str(x["ID"]): {"name": " ".join(p for p in (x.get("NAME"), x.get("LAST_NAME")) if p).strip(),
                               "phones": multifield(x.get("PHONE")), "company": num_id(x.get("COMPANY_ID"))}
                for x in read_by_ids(client, "crm.contact.list", "@ID", sorted(ct_ids, key=int),
                                     ["ID", "NAME", "LAST_NAME", "PHONE", "COMPANY_ID"])}
    co_ids |= {v["company"] for v in contacts.values() if v["company"]}
    co_ids = sorted(co_ids, key=int)
    deal_ids = sorted({num_id(c.get("parentId2")) for c in live} - {""}, key=int)
    print(f"карточек без сделки: {sum(1 for c in live if not num_id(c.get('parentId2')))}")
    deal_company = {d: num_id(x.get("COMPANY_ID")) for d, x in
                    client.deals_by_ids(deal_ids, select=["ID", "COMPANY_ID"]).items()}
    co_sel = ["ID", "TITLE", "PHONE"] + ([fields["dnc"]] if fields["dnc"] else [])
    companies = {str(x["ID"]): {"name": x.get("TITLE") or "", "phones": multifield(x.get("PHONE")),
                                "dnc": bool(fields["dnc"]) and str(x.get(fields["dnc"]) or "").upper() in ("Y", "1", "TRUE", "ДА")}
                 for x in read_by_ids(client, "crm.company.list", "@ID", co_ids, co_sel)}

    settings = {"our_company": os.environ.get("CALL_OUR_COMPANY", "").strip(),
                "agent_name": os.environ.get("CALL_AGENT_NAME", "").strip()}
    print("тексты агента: " + ("собираются" if all(settings.values()) else
                               "не собираются — нет CALL_OUR_COMPANY или CALL_AGENT_NAME"))
    arms = parse_arms(os.environ.get("CALL_ARMS", ""))
    print("группы сравнения: " + ", ".join(f"{n} {w}" for n, w in arms))
    countries = parse_countries(os.environ.get("CALL_COUNTRIES", ""))
    print("страны пилота: " + ("все" if countries is None else ", ".join(sorted(countries))))
    calls, st = build(cards, mails, contacts, companies, today, period.workdays_ru, fields, settings, arms, deal_company,
                      countries)
    # журнал — только счётчики, коды стран и пулов (правило 17)
    for k in sorted(st):
        print(f"  {k}: {st[k]}")
    if not a.write:
        print("холостой режим: очередь не записана")
        return 0
    from realization_ctx_kv import CtxError, _kv, kv_store
    raw = json.dumps({"v": VERSION, "built": dt.datetime.now(MSK).isoformat(timespec="seconds"),
                      "rules": ПРАВИЛА, "calls": calls}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        store = kv_store(KV_KEY)
        _kv(store.put, raw)
    except CtxError as e:
        print(f"::error::очередь не записана: {e}")
        return 1
    print(f"очередь записана в KV {KV_KEY}: звонков {len(calls)}, байт {len(raw)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                     # noqa: BLE001 — в публичный журнал только класс ошибки
        print(f"::error::сбой: {type(e).__name__}" + (f": {e}" if type(e) is RuntimeError else ""))
        # где упало — только файл, строка и функция, без значений и текста ошибки (правило 17)
        import traceback
        for fr in traceback.extract_tb(e.__traceback__)[-6:]:
            print(f"  в {Path(fr.filename).name}:{fr.lineno} {fr.name}")
        sys.exit(1)
