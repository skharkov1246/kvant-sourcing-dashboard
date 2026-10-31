#!/usr/bin/env python3
"""Зонд v62 «Не расценено через месяц»: доля запросов сорсинга без КП через 30 дней — когортами.

ВОПРОС ВЛАДЕЛЬЦА (30.10.2026). «Какой процент строк, попадающих в сорсинг, остаётся не
расценён через месяц от начала обработки запроса — когортами».

ОПРЕДЕЛЕНИЯ.
  • СТРОКА — запрос поставщику: карточка СП-166 воронки сорсинга (config.SPA_CATEGORY_ID).
  • НАЧАЛО ОБРАБОТКИ — заведение карточки (createdTime). КОГОРТА — месяц заведения по Москве.
  • РАСЦЕНЕНА — первый вход карточки в стадию КП (stages.KP_STAGES: Price at Work, Selected,
    Not Selected с «не прошли по цене / по технике», прежняя «КП получено») по истории стадий.
    НЕ РАСЦЕНЕНА ЧЕРЕЗ МЕСЯЦ — входа в стадию КП нет за ОКНО_ДНЕЙ дней от заведения.
  • ЗРЕЛОСТЬ — в когорту идут только карточки, заведённые не позже чем ОКНО_ДНЕЙ дней назад:
    у младших месяц ещё не прошёл. Когорта с такими отсечёнными карточками — «неполная».
  • ЗАЯВКА — сделка карточек; её начало — первая карточка сделки в окне зонда; расценена через
    месяц, если хотя бы одна её карточка вошла в стадию КП за ОКНО_ДНЕЙ дней от этого начала.

СДЕЛКИ С ВЫДАННЫМ ТКП (уточнение владельца того же дня: «а строк с выданными ТКП?»). ТКП
выдано — по истории стадий сделки, правилами зонда конверсии (probe_conversion.вид_стадии): дата —
первый вход в стадию «предложение выдано»; нет такой — первый вход в стадию дальше предложения
(торги, ожидание решения, реализация, проигрыш после предложения) — «без даты», его вход и есть
дата-заменитель. Печатается: (1) те же когорты строк, только у сделок с выданным ТКП; (2) заявки с
выданным ТКП — была ли к дате ТКП хотя бы одна цена сорсинга: «цена до ТКП», «запрос до ТКП, цены
нет», «запросы только после ТКП». Чтение — только входы в стадии предложения и дальше (фильтр
полная история стадий сделок, частями по сделкам (≈ 50 сделок на запрос и листание).

ВТОРАЯ СВЕРКА ДРУГИМ ПУТЁМ (правило 26.09.2026). Стадия сейчас против истории: карточки, которые
СЕЙЧАС в стадии КП, а входа в КП в истории нет, — печатаются; и файл «Offer from supplier»
(правило 19.10.2026) — у скольких расценённых и нерасценённых он лежит.

ЧАСТИ. Одна часть — один месяц когорты (вход PROBE_LIMIT — число месяцев, по умолчанию 12; меньше
10 — 10): карточки месяца и входы в КП, сделанные с начала месяца по сегодня. Упавшая часть
считается и выпадает из итога.

ЧТО В ЖУРНАЛ. Только счётчики и доли по когортам и корзинам стадий: ни названий, ни номеров.

НАГРУЗКА: карточки по месяцам (≈ 1 запрос на 50) и входы в стадии КП (≈ 1 на 50) — порядка
300–600 запросов; история стадий сделок с запросами — ещё ≈ 500–900; всего до ≈ 1 500 при 1/с
(≈ 25 мин): ставить вне 01:00–05:30 UTC, не на деплой в :07 и не на скан чатов в 21:00.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MSK = dt.timezone(dt.timedelta(hours=3))
ОКНО_ДНЕЙ = 30
МЕСЯЦЕВ = 12


def ts(v) -> dt.datetime | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=MSK)


def months_back(today: dt.date, n: int) -> list[tuple[dt.date, dt.date]]:
    """n месяцев по [начало, начало следующего), от старого к новому; последний — текущий."""
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        start = dt.date(y, m, 1)
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        out.append((start, nxt))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def kp_stage_ids(category: int, kp_buckets: set[str], classify) -> list[str]:
    """Коды стадий воронки, которые stages.classify_stage относит к КП (таблица stages.py)."""
    known = [f"DT166_{category}:{s}" for s in ("NEW", "PREPARATION", "UC_61BSRU", "UC_GFJ5A8", "UC_H49RUE",
                                                 "SUCCESS", "FAIL", "1", "2", "3", "4", "5")]
    return sorted(s for s in known if classify(s) in kp_buckets)


def first_kp(entries: list[tuple[str, str]]) -> dt.datetime | None:
    ts_ = [t for _, raw in entries if (t := ts(raw))]
    return min(ts_) if ts_ else None


def cohort_stats(cards: list[dict], kp_first: dict[str, dt.datetime], now: dt.datetime, classify,
                 kp_buckets: set[str], offer_field: str) -> dict:
    """Счётчики одной когорты: зрелые карточки, не расценённые через ОКНО_ДНЕЙ, корзины стадий."""
    win = dt.timedelta(days=ОКНО_ДНЕЙ)
    s = {"всего": len(cards), "зрелых": 0, "не_расценено": 0, "расценено": 0, "отсечено_молодых": 0,
         "корзины_нерасц": Counter(), "файл_у_нерасц": 0, "файл_у_расц": 0, "кп_сейчас_без_истории": 0,
         "расценено_позже": 0}
    for c in cards:
        cid = str(c.get("id"))
        created = ts(c.get("createdTime"))
        bucket = classify(str(c.get("stageId") or ""))
        k = kp_first.get(cid)
        if bucket in kp_buckets and not k:
            s["кп_сейчас_без_истории"] += 1
        if not created or created + win > now:
            s["отсечено_молодых"] += 1
            continue
        s["зрелых"] += 1
        has_file = bool(c.get(offer_field))
        if k and k <= created + win:
            s["расценено"] += 1
            s["файл_у_расц"] += has_file
        else:
            s["не_расценено"] += 1
            s["файл_у_нерасц"] += has_file
            s["корзины_нерасц"][bucket] += 1
            if k:
                s["расценено_позже"] += 1
    return s


def deal_stats(cards: list[dict], kp_first: dict[str, dt.datetime], now: dt.datetime) -> dict[str, tuple[str, bool]]:
    """Заявка → (месяц начала ГГГГ-ММ, расценена ли через месяц); незрелые заявки не входят."""
    win = dt.timedelta(days=ОКНО_ДНЕЙ)
    by: dict[str, list[dict]] = defaultdict(list)
    for c in cards:
        d = str(c.get("parentId2") or "")
        if d and d != "0":
            by[d].append(c)
    out = {}
    for d, cs in by.items():
        starts = [t for c in cs if (t := ts(c.get("createdTime")))]
        if not starts:
            continue
        start = min(starts)
        if start + win > now:
            continue
        ok = any((k := kp_first.get(str(c.get("id")))) and k <= start + win for c in cs)
        out[d] = (start.astimezone(MSK).strftime("%Y-%m"), ok)
    return out


def tkp_date(entries: list[tuple[str, str]], kind_of, funnel_of=lambda sid: "") -> tuple[dt.datetime | None, str]:
    """(дата выдачи ТКП, вид): первый вход в «предложение выдано» — «offer»; нет — первый вход в
    стадию дальше предложения — «beyond» (дата-заменитель); нет и его — (None, "").
    Вход в кат. 0 засчитывается, только если раньше сделка была в другой воронке: кат. 0 — и воронка
    по умолчанию, заведение сделки в ней ТКП не означает (probe_conversion)."""
    ev = sorted((t, sid) for sid, raw in entries if (t := ts(raw)))
    first_pre = next((t for t, sid in ev if funnel_of(sid) not in ("", "0")), None)

    def ok(t, sid) -> bool:
        return funnel_of(sid) != "0" or (first_pre is not None and t > first_pre)
    for want in ("offer", "beyond"):
        t = next((t for t, sid in ev if kind_of(sid) == want and ok(t, sid)), None)
        if t:
            return t, want
    return None, ""


def tkp_split(cards: list[dict], kp_first: dict[str, dt.datetime], tkp_at: dt.datetime) -> str:
    """Заявка с выданным ТКП: «price» — цена сорсинга была к дате ТКП; «ask» — запрос до ТКП был,
    цены к ТКП нет; «after» — все запросы заведены после ТКП."""
    pre = [c for c in cards if (t := ts(c.get("createdTime"))) and t <= tkp_at]
    if not pre:
        return "after"
    if any((k := kp_first.get(str(c.get("id")))) and k <= tkp_at for c in cards):
        return "price"
    return "ask"


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


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f} %" if b else "—"


def main() -> int:
    import config
    import stages
    from bitrix_client import BitrixClient

    n = max(10, int(os.environ.get("PROBE_LIMIT") or МЕСЯЦЕВ))
    now = dt.datetime.now(MSK)
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    cat = config.SPA_CATEGORY_ID
    kp_buckets = set(stages.KP_STAGES)
    kp_ids = kp_stage_ids(cat, kp_buckets, stages.classify_stage)
    print(f"стадии КП воронки {cat}: {len(kp_ids)}; окно {ОКНО_ДНЕЙ} дн.; месяцев {n}")
    sel = ["id", "createdTime", "stageId", "parentId2", config.RFQ_OFFER_FIELD]
    cohorts = months_back(now.date(), n)
    cards_by_m: dict[str, list[dict]] = {}
    fails = 0
    for start, nxt in cohorts:
        key = start.strftime("%Y-%m")
        try:
            got = client.list_items(config.SPA_ENTITY_TYPE_ID, select=sel, filter={
                "categoryId": cat, ">=createdTime": f"{start.isoformat()}T00:00:00+03:00",
                "<createdTime": f"{nxt.isoformat()}T00:00:00+03:00"})
        except Exception as e:                  # noqa: BLE001 — часть выпадает, считается
            fails += 1
            print(f"  часть {key}: не прочитана ({type(e).__name__})")
            continue
        cards_by_m[key] = got
    # входы в стадии КП — с начала первой когорты, помесячно (части), со сверкой фильтра стадий
    kp_first: dict[str, dt.datetime] = {}
    for start, nxt in cohorts:
        params = {"entityTypeId": config.SPA_ENTITY_TYPE_ID, "order": {"ID": "ASC"},
                  "select": ["ID", "OWNER_ID", "CREATED_TIME", "STAGE_ID"],
                  "filter": {"STAGE_ID": kp_ids, ">=CREATED_TIME": f"{start.isoformat()}T00:00:00+03:00",
                             "<CREATED_TIME": f"{nxt.isoformat()}T00:00:00+03:00"}}
        pos, chunk_bad = 0, False
        while True:
            params["start"] = pos
            data = client.call_envelope("crm.stagehistory.list", params) or {}
            res = data.get("result") or {}
            items = (res.get("items") if isinstance(res, dict) else res) or []
            if any(str(x.get("STAGE_ID") or "") not in kp_ids for x in items):
                chunk_bad = True
                break
            for x in items:
                t = ts(x.get("CREATED_TIME"))
                o = str(x.get("OWNER_ID") or "")
                if t and o and (o not in kp_first or t < kp_first[o]):
                    kp_first[o] = t
            nxt_pos = data.get("next")
            if not nxt_pos or not items:
                break
            pos = nxt_pos
        if chunk_bad:
            fails += 1
            print(f"  входы в КП {start:%Y-%m}: фильтр STAGE_ID не сработал — часть выпала")
    print(f"частей не прочитано: {fails}; карточек с входом в КП: {len(kp_first)}")
    if fails:
        print("::warning::итог неполный — части выпали")

    print(f"\nКОГОРТЫ ЗАПРОСОВ (строка = карточка СП-166; не расценено = нет входа в стадию КП за {ОКНО_ДНЕЙ} дн.)")
    print("месяц    | карточек | зрелых | не расценено | доля  | из них расценено позже | в работе / отказ / "
          "нет ответа / прочее | файл КП у нерасц. | молодых отсечено")
    tot = Counter()
    all_cards = []
    for start, _ in cohorts:
        key = start.strftime("%Y-%m")
        cs = cards_by_m.get(key)
        if cs is None:
            print(f"{key}  | часть не прочитана")
            continue
        all_cards += cs
        s = cohort_stats(cs, kp_first, now, stages.classify_stage, kp_buckets, config.RFQ_OFFER_FIELD)
        b = s["корзины_нерасц"]
        work = b["new"] + b["sent"] + b["dialog"]
        other = s["не_расценено"] - work - b["refused"] - b["no_answer"]
        mark = " (неполная)" if s["отсечено_молодых"] else ""
        print(f"{key}{mark} | {s['всего']} | {s['зрелых']} | {s['не_расценено']} | {_pct(s['не_расценено'], s['зрелых'])} | "
              f"{s['расценено_позже']} | {work} / {b['refused']} / {b['no_answer']} / {other} | "
              f"{s['файл_у_нерасц']} | {s['отсечено_молодых']}")
        for k in ("зрелых", "не_расценено", "расценено_позже", "файл_у_нерасц", "файл_у_расц", "кп_сейчас_без_истории"):
            tot[k] += s[k]
        tot["new"] += b["new"]
        tot["sent"] += b["sent"]
        tot["dialog"] += b["dialog"]
        tot["refused"] += b["refused"]
        tot["no_answer"] += b["no_answer"]
    print(f"ИТОГО зрелых {tot['зрелых']}: не расценено через {ОКНО_ДНЕЙ} дн. {tot['не_расценено']} "
          f"({_pct(tot['не_расценено'], tot['зрелых'])}); из них расценено позже {tot['расценено_позже']}; "
          f"не отправлены (New) {tot['new']}, отправлены без ответа {tot['sent']}, переписка без цены {tot['dialog']}, "
          f"отказ {tot['refused']}, «нет ответа в срок» {tot['no_answer']}")
    print(f"сверка: файл «Offer from supplier» у расценённых {tot['файл_у_расц']} из "
          f"{tot['зрелых'] - tot['не_расценено']}, у нерасценённых {tot['файл_у_нерасц']} из {tot['не_расценено']}; "
          f"сейчас в стадии КП без входа в КП в истории окна — {tot['кп_сейчас_без_истории']}")

    ds = deal_stats(all_cards, kp_first, now)
    by_m: dict[str, Counter] = defaultdict(Counter)
    for m, ok in ds.values():
        by_m[m]["n"] += 1
        by_m[m]["bad"] += 0 if ok else 1
    print(f"\nКОГОРТЫ ЗАЯВОК (сделка; не расценена = ни одна карточка не вошла в КП за {ОКНО_ДНЕЙ} дн. от первой)")
    print("месяц   | заявок | не расценено | доля")
    tn = tb = 0
    for m in sorted(by_m):
        c = by_m[m]
        tn, tb = tn + c["n"], tb + c["bad"]
        print(f"{m} | {c['n']} | {c['bad']} | {_pct(c['bad'], c['n'])}")
    print(f"ИТОГО заявок {tn}: не расценено через {ОКНО_ДНЕЙ} дн. {tb} ({_pct(tb, tn)}); первая когорта неполная: "
          f"карточки её сделок до окна зонда не читались")

    # ── сделки с выданным ТКП
    from probe_conversion import вид_стадии, воронка_стадии, читать_историю
    мета = client.deal_stage_meta()
    kind_of = lambda sid: вид_стадии(sid, мета)[0]                         # noqa: E731
    tkp_stages = sorted(sid for sid in мета if kind_of(sid) in ("offer", "beyond"))
    # история целиком, без фильтра стадий: правилу кат. 0 нужен вход в предпродажную воронку
    deal_ids = sorted({str(c.get("parentId2")) for c in all_cards if str(c.get("parentId2") or "0") not in ("", "0")},
                      key=int)
    hist: dict[str, list] = {}
    bad = 0
    for part in _parts(deal_ids, max(10, int(os.environ.get("PROBE_PARTS") or 10))):
        h, err = читать_историю(client, part)
        if err:
            bad += 1
            print(f"  часть истории сделок не прочитана: {err}")
            continue
        hist.update(h)
    tkp_at = {}
    kinds = Counter()
    for d in deal_ids:
        t, k = tkp_date(hist.get(d, []), kind_of, воронка_стадии)
        if t:
            tkp_at[d] = t
            kinds[k] += 1
    print(f"\nСДЕЛКИ С ВЫДАННЫМ ТКП: сделок с запросами {len(deal_ids)}, ТКП выдано {len(tkp_at)} (с датой "
          f"{kinds['offer']}, по стадии дальше предложения {kinds['beyond']}); стадий предложения и дальше "
          f"{len(tkp_stages)}; частей истории не прочитано {bad}")
    print(f"КОГОРТЫ СТРОК СДЕЛОК С ВЫДАННЫМ ТКП (не расценено = нет входа в КП за {ОКНО_ДНЕЙ} дн. от запроса)")
    print("месяц   | зрелых | не расценено | доля")
    zt = zb = 0
    for start, _ in cohorts:
        key = start.strftime("%Y-%m")
        cs = [c for c in cards_by_m.get(key) or [] if str(c.get("parentId2") or "") in tkp_at]
        st = cohort_stats(cs, kp_first, now, stages.classify_stage, kp_buckets, config.RFQ_OFFER_FIELD)
        if not st["зрелых"]:
            continue
        zt, zb = zt + st["зрелых"], zb + st["не_расценено"]
        print(f"{key} | {st['зрелых']} | {st['не_расценено']} | {_pct(st['не_расценено'], st['зрелых'])}")
    print(f"ИТОГО строк сделок с ТКП {zt}: не расценено через {ОКНО_ДНЕЙ} дн. {zb} ({_pct(zb, zt)})")
    by_deal: dict[str, list[dict]] = defaultdict(list)
    for c in all_cards:
        by_deal[str(c.get("parentId2") or "")].append(c)
    print("ЗАЯВКИ С ВЫДАННЫМ ТКП ПО МЕСЯЦУ ТКП: была ли к дате ТКП цена сорсинга")
    print("месяц ТКП | заявок | цена до ТКП | запрос до ТКП, цены нет | запросы только после ТКП")
    agg: dict[str, Counter] = defaultdict(Counter)
    first_m = cohorts[0][0]
    for d, t in tkp_at.items():
        if t.astimezone(MSK).date() < first_m:
            continue                           # ТКП раньше окна: запросы до ТКП зонд не читал
        m = t.astimezone(MSK).strftime("%Y-%m")
        agg[m]["n"] += 1
        agg[m][tkp_split(by_deal.get(d, []), kp_first, t)] += 1
    tot2 = Counter()
    for m in sorted(agg):
        a = agg[m]
        tot2.update(a)
        print(f"{m} | {a['n']} | {a['price']} ({_pct(a['price'], a['n'])}) | {a['ask']} ({_pct(a['ask'], a['n'])}) | "
              f"{a['after']} ({_pct(a['after'], a['n'])})")
    print(f"ИТОГО заявок с ТКП в окне {tot2['n']}: цена сорсинга до ТКП {tot2['price']} ({_pct(tot2['price'], tot2['n'])}); "
          f"запрос до ТКП без цены {tot2['ask']} ({_pct(tot2['ask'], tot2['n'])}); запросы только после ТКП "
          f"{tot2['after']} ({_pct(tot2['after'], tot2['n'])}); первый месяц неполный — запросы до окна не читались")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                     # noqa: BLE001 — в публичный журнал только класс ошибки
        print(f"::error::сбой: {type(e).__name__}")
        sys.exit(1)
