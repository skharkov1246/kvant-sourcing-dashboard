"""Сверка договоров «один к одному» (back-to-back): обязательства перед заказчиком против
условий поставщика по той же сделке. Навык `contract-review`.

Чистые функции. На вход — условия, уже извлечённые из договоров (конвейер чтения —
прогон Actions, строки в закрытой базе; здесь только правила), и сроки звеньев с
пульта реализации (lifecycle). На выход — флаги риска с объяснением. Ни текста
договоров, ни сумм в журнал: флаги — коды и дни.

Где лежат договоры (зонд v54, 19.10.2026): СП-1174 «2025 — разовый договор /
спецификация / ДС» (986 за год; финальная версия файла у 93 %, связь с заказом
поставщику у 58 %, со сделкой у 76 %), рамочные — СП-175 и СП-1192, прежние
спецификации — СП-132. Экспертиза юриста и перечень рисков уже есть в карточке.

Схема условий одной стороны (TERMS_SCHEMA) — то, что модель извлекает из договора;
каждое значение — с цитатой и страницей (правило 16: храни, почему получилось).
"""
from __future__ import annotations

TERMS_SCHEMA = {
    "type": "object",
    "properties": {
        "side": {"type": "string", "enum": ["customer", "supplier"]},
        "deliveryDays": {"type": ["integer", "null"], "description": "срок поставки, дней"},
        "deliveryFrom": {"type": ["string", "null"], "description": "от чего считается срок: подписание, аванс, согласование РКД, дата"},
        "deliveryDate": {"type": ["string", "null"], "description": "срок поставки датой, YYYY-MM-DD"},
        "incoterms": {"type": ["string", "null"], "description": "базис Incoterms 2020: EXW, FCA, DAP, DDP…"},
        "place": {"type": ["string", "null"], "description": "пункт базиса"},
        "prepayPct": {"type": ["number", "null"], "description": "аванс, % от суммы"},
        "payDaysAfter": {"type": ["integer", "null"], "description": "оплата через N дней после события"},
        "payAfter": {"type": ["string", "null"], "description": "событие: поставка, УПД, ввод, приёмка"},
        "penaltyPctDay": {"type": ["number", "null"], "description": "пени за просрочку поставки, % в день"},
        "penaltyCapPct": {"type": ["number", "null"], "description": "потолок пеней, % суммы; null — без потолка"},
        "warrantyMonths": {"type": ["integer", "null"]},
        "warrantyFrom": {"type": ["string", "null"], "description": "поставка, ввод в эксплуатацию, отгрузка"},
        "warrantyMaxMonths": {"type": ["integer", "null"], "description": "но не больше N мес. от поставки"},
        "claimDays": {"type": ["integer", "null"], "description": "срок предъявления претензий по качеству, дней"},
        "acceptance": {"type": ["string", "null"], "description": "порядок приёмки: по местам, по качеству при шефмонтаже, П-6/П-7"},
        "documents": {"type": "array", "items": {"type": "string"}, "description": "ТР ТС/ЕАЭС, паспорт, перевод, сертификат происхождения…"},
        "currency": {"type": ["string", "null"]},
        "fxFixed": {"type": ["boolean", "null"], "description": "цена в рублях по фиксированному курсу"},
        "law": {"type": ["string", "null"], "description": "применимое право; CISG исключена?"},
        "cisgExcluded": {"type": ["boolean", "null"]},
        "evidence": {"type": "object", "description": "поле → {quote, page}"},
    },
    "required": ["side"],
}

ЛОГИСТИКА_ДНИ = 19          # медиана «отгрузка → склад → клиент» (зонд v53: 7 + 12); пульт даёт точнее
ЗАПАС_ДНИ = 7              # меньше — срок заказчика держится на удаче
РИСК_БАЗИСА = {"EXW": 0, "FCA": 1, "FAS": 1, "FOB": 1, "CFR": 2, "CIF": 2, "CPT": 2, "CIP": 2,
               "DAP": 3, "DPU": 3, "DDP": 4}

FLAG_TEXT = {
    "lateByDesign": "срок поставщика + путь позже срока заказчика: срыв заложен в договор",
    "thinBuffer": "запас между сроком поставщика с путём и сроком заказчика меньше недели",
    "basisGap": "заказчику базис дальше, чем у поставщика: путь, страховка и риск утраты — на нас",
    "cashGap": "аванс поставщику при оплате заказчика после поставки: кассовый разрыв",
    "penaltyUncovered": "пени заказчику не покрыты пенями поставщика",
    "penaltyNoCap": "пени заказчику без потолка",
    "warrantyGap": "гарантия заказчику длиннее гарантии поставщика",
    "warrantyStart": "гарантия заказчику от ввода, у поставщика — от отгрузки или поставки",
    "claimGap": "приёмка у заказчика позже срока претензий к поставщику",
    "docsGap": "заказчик требует документы, которых поставщик не обязан дать",
    "fxGap": "заказчику цена в рублях по фиксированному курсу, поставщику — в валюте",
    "cisg": "с иностранным поставщиком Венская конвенция не исключена",
}


def _n(v):
    return v if isinstance(v, (int, float)) else None


def flags(customer: dict, supplier: dict, *, logistics_days: float | None = None) -> list[dict]:
    """[{code, text, days?}] — риски, которые сделка несёт из-за разницы условий."""
    out: list[dict] = []

    def add(code: str, **kw):
        out.append({"code": code, "text": FLAG_TEXT[code], **kw})

    путь = ЛОГИСТИКА_ДНИ if logistics_days is None else logistics_days
    c_days, s_days = _n(customer.get("deliveryDays")), _n(supplier.get("deliveryDays"))
    if c_days is not None and s_days is not None:
        запас = c_days - (s_days + путь)
        if запас < 0:
            add("lateByDesign", days=round(-запас))
        elif запас < ЗАПАС_ДНИ:
            add("thinBuffer", days=round(запас))
    cb, sb = str(customer.get("incoterms") or "").upper(), str(supplier.get("incoterms") or "").upper()
    if cb in РИСК_БАЗИСА and sb in РИСК_БАЗИСА and РИСК_БАЗИСА[cb] > РИСК_БАЗИСА[sb]:
        add("basisGap")
    if (_n(supplier.get("prepayPct")) or 0) > 0 and (_n(customer.get("prepayPct")) or 0) == 0 \
            and _n(customer.get("payDaysAfter")) is not None:
        add("cashGap", days=customer["payDaysAfter"])
    cp, sp = _n(customer.get("penaltyPctDay")), _n(supplier.get("penaltyPctDay"))
    if cp and (sp is None or sp < cp):
        add("penaltyUncovered")
    if cp and customer.get("penaltyCapPct") is None:
        add("penaltyNoCap")
    cw, sw = _n(customer.get("warrantyMonths")), _n(supplier.get("warrantyMonths"))
    if cw is not None and sw is not None and cw > sw:
        add("warrantyGap", days=round((cw - sw) * 30.4))
    cwf, swf = str(customer.get("warrantyFrom") or ""), str(supplier.get("warrantyFrom") or "")
    if "ввод" in cwf.lower() and swf and "ввод" not in swf.lower():
        add("warrantyStart")
    if "шефмонтаж" in str(customer.get("acceptance") or "").lower() and _n(supplier.get("claimDays")) is not None:
        add("claimGap", days=supplier["claimDays"])
    нужны = {d.strip().lower() for d in customer.get("documents") or [] if d}
    дадут = {d.strip().lower() for d in supplier.get("documents") or [] if d}
    if нужны - дадут:
        add("docsGap")
    if customer.get("fxFixed") and str(supplier.get("currency") or "").upper() not in ("", "RUB"):
        add("fxGap")
    if supplier.get("cisgExcluded") is False:
        add("cisg")
    return out
