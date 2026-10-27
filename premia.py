"""Вкладка «Премии» — прогноз премии по каждой открытой сделке по положению о мотивации
команды КАМа, ред. 2 (решение владельца 12.10.2026: премия привязана к прибыльности продаж).

ВСЕЛЕННАЯ. Открытые сделки всех воронок, кроме технических, — те же, что у вкладки
«Коммерсанты» (people.compute → deals): клиентские воронки и «Пресейл» — состояние
«в проработке», «Реализация» и сделки с заказами поставщикам — «в реализации»;
сделка в стадии «Оплата получена | Закрытие сделки» — «оплачено» (st = "d"): премия по
ней заработана, в пул «в реализации» она не входит (с 25.10.2026).
Своих обращений к Bitrix модуль не делает: считает на том, что уже выгрузили вкладки
«Коммерсанты» и «Реализация».

РЕЗУЛЬТАТ СДЕЛКИ — оценка до калькулятора маржи финдиректора:
  • ПЗ — каскад источников, и источник хранится в строке (правило 16):
      заказы СП-172 × 1,10 (медиана доп. затрат на логистику, таможню и приёмку) →
      закупка сверенного файла экономики × 1,10 →
      прямые затраты книги бюджета → «Cost» экономики проекта × 1,10 →
      оценка по медианному коэффициенту «продажа / ПЗ» сделок с известной закупкой;
  • результат = выручка − ПЗ − финансирование 5 % выручки − платёжный агент 2 % оплаты
    поставщику − поправка на агентское и затраты, которых нет ни в Битриксе, ни в книгах;
  • поправка калибруется на сделках «в реализации» с известной закупкой так, чтобы их
    результат составил 18,7 % выручки — уровень калибровки положения (60 сделок
    квартала по 1С). Замер на книгах бюджетов 2026: операционная прибыль книги — 44 %
    выручки, то есть книги агентского не содержат.

ПУЛ И ДОЛИ — положение ред. 2:
  • пул = 20 % результата × К × Кц; Кц = 0 ниже ×1,17 к ПЗ (согласования в Битриксе
    нет), новизна в Битриксе не отмечается — 1,0. При К ≤ 1,25 предел 25 % результата
    не достигается, поэтому пул линеен по К: строка хранит пул при К = 1, страница
    умножает на К, который задаёт пользователь;
  • исполнитель — «Ответственный» сделки (people.py: это исполнитель, а не КАМ) — 7/20 пула;
  • часть ЛПР — 13/20: КАМ из поля сделки; продукт-оунер в поле при КАМе — эксперт (20 %
    части ЛПР), без КАМа — завёл сделку (80 %). Это допущение до поля «Кто завёл
    сделку» (п. 6.4 положения) — страница его называет;
  • убыточная сделка: часть ЛПР = 13 % результата (отрицательная), исполнителю — 0.
"""
from __future__ import annotations

from collections import Counter

RATE = 0.20            # пул, доля результата сделки при К = 1
EX_RATE = 0.07         # исполнитель
LPR_RATE = 0.13        # часть ЛПР
CAP = 0.25             # пул — не больше этой доли результата
K_DEFAULT = 0.60       # минимум I–II кв. 2027 г.; финдиректор публикует К до 10-го числа
K_MIN, K_MAX = 0.25, 1.25
FIN = 0.05             # финансирование по графику оплат, доля выручки (норматив калькулятора ФД)
PAY_AGENT = 0.02       # платёжный агент, доля оплаты поставщику
PZ_OVER = 0.10         # ПЗ сверх заказов поставщику: логистика, таможня, приёмка (медиана)
RHO_TARGET = 0.187     # результат сделок по калибровке положения: 60 сделок квартала, 1С
PRICE_MIN = 1.17       # минимум финдиректора: коэффициент к ПЗ
COEF_OK = (0.9, 4.0)   # вне этого коридора ПЗ, скорее всего, внесена не полностью
PO_INIT, PO_EXPERT = 0.80, 0.20

SRC_LABEL = {"orders": "заказы поставщикам", "file": "файл экономики", "book": "книга бюджета",
             "econ": "экономика проекта", "est": "оценка"}


def _median(vals: list[float]) -> float | None:
    v = sorted(vals)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def pz_of(buy: float, crow: dict | None, *, econ: bool = True) -> tuple[float, float, str]:
    """(ПЗ, оплата поставщику, источник) без оценки по коэффициенту — каскад положения.
    buy — закупка у поставщиков (заказы СП-172 или финальная экономика файла) в €;
    crow — строка «Реализации» (contracts.rows): книга бюджета и «Cost» экономики в €.
    Один каскад на премии и доход вкладки «Key-аккаунты и продукты» (kaminc.py) —
    CLAUDE.md, правило 14: две вставки одной формулы правятся вместе, поэтому вставка одна.
    econ=False — без «Cost» экономики проекта: ему доход не верит."""
    if buy > 0:
        return buy * (1 + PZ_OVER), buy, "orders"
    if crow:
        # закупка из сверенного файла экономики (contracts: файл первым, решение 25.10.2026) —
        # оффер поставщика, как и заказы: × (1 + PZ_OVER)
        fbuy = float(crow.get("buyEur") or 0)
        if crow.get("buySrc") == "file" and fbuy > 0 and not crow.get("buySusp"):
            return fbuy * (1 + PZ_OVER), fbuy, "file"
        pb = crow.get("planBreak") or {}
        direct = abs(float(pb.get("direct") or 0))
        if direct > 0 and not crow.get("planSusp"):
            purchase = abs(float(pb.get("buy") or 0))
            return direct, (purchase or direct / (1 + PZ_OVER)), "book"
        # «Cost» экономики — только оценка прогноза, когда у заказов нет сумм: в «Реализации»
        # с 25.10.2026 это не закупка (buySrc «none»), а сверка; коридор COEF_OK — ниже
        cost = float(crow.get("buyEconEur") or 0)
        if econ and crow.get("buySrc") == "none" and cost > 0:
            return cost * (1 + PZ_OVER), cost, "econ"
    return 0.0, 0.0, ""


def result_of(rev: float, pz: float, pay: float, plug: float) -> float:
    """Результат сделки по нормативу положения ред. 2 (не «прибыль»): выручка − ПЗ −
    финансирование − платёжный агент − поправка. Валюта — любая, формула линейна:
    премии считают в ₽, доход вкладки — в €."""
    return rev - pz - FIN * rev - PAY_AGENT * pay - plug * rev


def pool_of(result: float, coef: float) -> float:
    """Пул сделки при К = 1: 20 % результата, ниже ×1,17 к ПЗ — 0; убыток — часть ЛПР 13 %."""
    if result <= 0:
        return LPR_RATE * result
    return RATE * result * (1.0 if coef >= PRICE_MIN else 0.0)


def _cost(d: dict, crow: dict | None, eur_rub: float) -> tuple[float, float, str]:
    """(ПЗ ₽, оплата поставщику ₽, источник) без оценки по коэффициенту.

    ПЗ из книги бюджета и из «Cost» экономики «Реализация» перевела в € курсом ЦБ на дату
    книги и на дату КП (решение владельца 25.10.2026): в ₽ они возвращаются ТЕМ ЖЕ курсом
    (planEurRub, kpEurRub строки), иначе ₽ книги сдвинулись бы на разницу курсов ЦБ и
    Битрикса (~12 %, ревью 26.10.2026). Прочие источники — курсом премий eur_rub, как прежде."""
    pz, pay, src = pz_of(float(d.get("buyRaw") or 0), crow)
    k = {"book": (crow or {}).get("planEurRub"), "econ": (crow or {}).get("kpEurRub")}.get(src)
    r = float(k) if k else eur_rub
    return pz * r, pay * r, src


def _parts(d: dict, neg: bool) -> list[list]:
    """Доли пула при К = 1: [uid, роль, доля]. uid "" — роль на сделке не закреплена."""
    kam, prod, own = str(d.get("kam") or ""), str(d.get("prod") or ""), str(d.get("own") or "")
    ex_share = 0.0 if neg else EX_RATE / RATE
    lpr_share = 1.0 if neg else LPR_RATE / RATE
    out: list[list] = []
    if ex_share:
        out.append([own, "ex", round(ex_share, 6)])
    if kam:
        if prod and prod != kam:
            out += [[kam, "kam", round(lpr_share * (1 - PO_EXPERT), 6)],
                    [prod, "po", round(lpr_share * PO_EXPERT, 6)]]
        else:
            out.append([kam, "kam", round(lpr_share, 6)])
    elif prod:
        out += [[prod, "po", round(lpr_share * PO_INIT, 6)],
                ["", "kam", round(lpr_share * (1 - PO_INIT), 6)]]
    else:
        out.append(["", "kam", round(lpr_share, 6)])
    return out


def compute(people: dict | None, contracts: dict | None = None, *,
            eur_rub: float | None = None) -> dict | None:
    """Строки вкладки «Премии». None — если нет сделок или курса (вкладка покажет «Нет данных»)."""
    if not people or not people.get("deals"):
        return None
    if not eur_rub:
        fx = (contracts or {}).get("fx") or {}
        if fx.get("eurUsd") and fx.get("usdRub"):
            eur_rub = float(fx["eurUsd"]) * float(fx["usdRub"])
    if not eur_rub:
        return None
    crows = {str(r.get("deal")): r for r in (contracts or {}).get("rows", [])}

    # проход 1: известная ПЗ, медианный коэффициент и поправка — по сделкам в реализации
    base = []
    for d in people["deals"]:
        rev = float(d.get("raw") or 0) * eur_rub
        pz, pay, src = _cost(d, crows.get(str(d.get("id"))), eur_rub)
        base.append((d, rev, pz, pay, src))
    calib = [(rev, pz, pay) for d, rev, pz, pay, src in base
             if d.get("state") == "real" and src in ("orders", "book") and rev > 0 and pz > 0
             and COEF_OK[0] <= rev / pz <= COEF_OK[1] and not d.get("big")]
    coef_med = _median([rev / pz for rev, pz, _ in calib]) or 0.0
    rev_c = sum(rev for rev, _, _ in calib)
    rho_bitrix = (sum(result_of(rev, pz, pay, 0.0) for rev, pz, pay in calib) / rev_c
                  if rev_c else None)
    plug = max(0.0, rho_bitrix - RHO_TARGET) if rho_bitrix is not None else 0.0

    # проход 2: строки
    rows, srcs = [], Counter()
    no_amount = 0
    for d, rev, pz, pay, src in base:
        if rev <= 0:
            no_amount += 1
            continue
        if not pz and coef_med:
            pz, src = rev / coef_med, "est"
            pay = pz / (1 + PZ_OVER)
        if not pz:
            no_amount += 1
            continue
        result = result_of(rev, pz, pay, plug)
        coef = rev / pz
        neg = result <= 0
        pool1 = pool_of(result, coef)
        flags = []
        if src == "est":
            flags.append("est")
        if coef < PRICE_MIN:
            flags.append("low")
        if not COEF_OK[0] <= coef <= COEF_OK[1]:
            flags.append("anom")
        if neg:
            flags.append("neg")
        if not d.get("own"):
            flags.append("noex")
        if not d.get("kam") and not d.get("prod"):
            flags.append("nokam")
        if d.get("big"):
            flags.append("big")
        srcs[src] += 1
        # «Оплата получена | Закрытие сделки» — успех (решение владельца 19.10.2026): премия
        # по такой сделке заработана, а не ожидается. До 25.10.2026 оплаченные (около 60 %
        # строк) лежали в пуле «в реализации» вместе с ожидаемыми
        crow = crows.get(str(d.get("id"))) or {}
        st = "d" if crow.get("paidStage") else ("r" if d.get("state") == "real" else "p")
        rows.append({
            "id": str(d.get("id")), "t": d.get("t") or "", "cat": d.get("cat") or "",
            "stage": d.get("stage") or "", "st": st,
            "rev": round(rev), "pz": round(pz), "src": src, "coef": round(coef, 3),
            "res": round(result), "p1": round(pool1, 2), "parts": _parts(d, neg),
            "fl": flags, "idle": d.get("idle"),
        })

    def pool_at(st: str, k: float = K_DEFAULT) -> float:
        return sum(r["p1"] * k for r in rows if r["st"] == st and "big" not in r["fl"])

    win = ((people.get("roles") or {}).get("kam") or {}).get("totals", {}).get("winRate")
    return {
        "label": people.get("label", ""),
        "params": {
            "k": K_DEFAULT, "kMin": K_MIN, "kMax": K_MAX, "rate": RATE, "ex": EX_RATE,
            "lpr": LPR_RATE, "cap": CAP, "fin": FIN, "pa": PAY_AGENT, "pzOver": PZ_OVER,
            "rhoTarget": RHO_TARGET, "priceMin": PRICE_MIN,
            "plug": round(plug, 4), "rhoBitrix": round(rho_bitrix, 4) if rho_bitrix is not None else None,
            "coefMed": round(coef_med, 3), "calibN": len(calib), "eurRub": round(eur_rub, 4),
            "winRate": win, "srcLabel": SRC_LABEL,
        },
        "totals": {
            "deals": len(rows), "noAmount": no_amount, "src": dict(srcs),
            "real": sum(1 for r in rows if r["st"] == "r"),
            "paid": sum(1 for r in rows if r["st"] == "d"),
            "presale": sum(1 for r in rows if r["st"] == "p"),
            "poolReal": round(pool_at("r")), "poolPaid": round(pool_at("d")),
            "poolPresale": round(pool_at("p")),
            "low": sum(1 for r in rows if "low" in r["fl"]),
            "neg": sum(1 for r in rows if "neg" in r["fl"]),
            "big": sum(1 for r in rows if "big" in r["fl"]),
        },
        "deals": rows,
        "names": people.get("names") or {},
    }
