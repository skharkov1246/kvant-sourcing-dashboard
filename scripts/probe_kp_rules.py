#!/usr/bin/env python3
"""Зонд v52 «Правила отметки КП»: стадии СП-166, поле «Offer from supplier»,
поле сделки «Экономика готова» — замер перед переводом дашборда на правила
владельца от 19.10.2026 («Как отмечать полученные КП», 10 пунктов).

Правила (кратко): КП засчитывается только файлом в поле Offer from supplier
запроса; в день получения запрос переводится на Price at Work; без цены —
In Correspondance; отказ — «Отказ в КП»; молчание 10 рабочих дней — «Ответ не
получен (в срок)»; итог каждому запросу на Price at Work — Selected / Not
Selected / «Не прошли по цене» / «Не подошло по технике»; стадию «КП получено»
не использовать; «Экономика готова» = «Да» только когда ни одного запроса на
Price at Work и хотя бы один Selected.

ЧТО МЕРЯЕМ (только счётчики; имена стадий и полей — настройка портала):
  • все стадии воронки 24 СП-166: код, имя, семантика, порядок;
  • карточки за DAYS дней по стадиям: всего, с файлом Offer from supplier, с
    файлом в прочих полях КП (config.RFQ_QUOTE_FIELDS), возраст в стадии;
  • расхождения с правилами: файл есть, а стадия до КП; стадия КП без файла;
    Price at Work без итога дольше 14 дней; «КП получено» после 19.10.2026;
  • поле сделки «Экономика готова»: варианты списка; сделки с «Да» — сколько из
    них с запросом на Price at Work, без Selected, без запросов вовсе.
НАГРУЗКА: ≈ 120 запросов (карточки за DAYS дней по 50, сделки с «Да», справочники).
"""
from __future__ import annotations

import collections
import datetime as dt
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DAYS = int(os.environ.get("KP_DAYS", "60"))
SPA, CAT = 166, 24
OFFER_FIELD = "ufCrm18_1731179998"          # «Offer from supplier» (зонд v49: единственное живое поле КП)
ECON_LABEL = re.compile(r"^\s*экономика\s+готова\s*$", re.I)
ПРАВИЛО_С = dt.date(2026, 10, 19)
КОРЗИНЫ = ((3, "до 3 дн."), (14, "4–14 дн."), (30, "15–30 дн."), (90, "31–90 дн."), (10 ** 6, "больше 90 дн."))

# Роль стадии по ИМЕНИ (правила владельца 19.10.2026). Порядок значим.
РОЛИ = (
    ("price_at_work", re.compile(r"price\s*at\s*work", re.I)),
    ("selected", re.compile(r"^\s*selected\s*$", re.I)),
    ("not_selected", re.compile(r"not\s*selected", re.I)),
    ("lost_price", re.compile(r"не\s*прош\w*\s*по\s*цен", re.I)),
    ("lost_tech", re.compile(r"не\s*подош\w*\s*по\s*техн", re.I)),
    ("kp_legacy", re.compile(r"кп\s*получен", re.I)),
    ("correspondence", re.compile(r"correspond|переписк", re.I)),
    ("refused", re.compile(r"отказ", re.I)),
    ("no_answer", re.compile(r"не\s*получен|нет\s*ответ", re.I)),
    ("unread", re.compile(r"unread", re.I)),
    ("sent", re.compile(r"request\s*sent|отправл", re.I)),
    ("new", re.compile(r"new\s*request|^\s*нов", re.I)),
)
С_КП = {"price_at_work", "selected", "not_selected", "lost_price", "lost_tech", "kp_legacy"}
ДО_КП = {"new", "sent", "unread", "correspondence"}


def роль(имя: str) -> str:
    for r, rx in РОЛИ:
        if rx.search(имя or ""):
            return r
    return "прочее"


def есть_файл(v) -> bool:
    return any(v) if isinstance(v, list) else bool(v)


def корзина(дн):
    for порог, имя in КОРЗИНЫ:
        if дн <= порог:
            return имя
    return КОРЗИНЫ[-1][1]


def разбор_карточек(карточки, роль_стадии, прочие_поля, сегодня):
    """Карточки → {роль: счётчики} и расхождения с правилами. Чистая функция."""
    по = collections.defaultdict(collections.Counter)
    возраст_paw = collections.Counter()
    расх = collections.Counter()
    for к in карточки:
        r = роль_стадии.get(str(к.get("stageId") or ""), "прочее")
        файл = есть_файл(к.get(OFFER_FIELD))
        прочий = any(есть_файл(к.get(f)) for f in прочие_поля)
        по[r]["всего"] += 1
        по[r]["с файлом Offer"] += файл
        по[r]["с файлом в прочих полях"] += прочий
        перешла = str(к.get("movedTime") or "")[:10]
        дн = (сегодня - dt.date.fromisoformat(перешла)).days if re.match(r"\d{4}-\d\d-\d\d", перешла) else None
        if файл and r in ДО_КП:
            расх["файл КП есть, стадия до КП"] += 1
        if r in С_КП and not файл:
            расх["стадия КП, файла нет (не засчитается)"] += 1
        if r == "price_at_work" and дн is not None:
            возраст_paw[корзина(дн)] += 1
            if дн > 14:
                расх["Price at Work без итога дольше 14 дн."] += 1
        if r == "kp_legacy" and перешла and перешла >= ПРАВИЛО_С.isoformat():
            расх["«КП получено» после 19.10.2026"] += 1
        if файл and r in {"refused", "no_answer"}:
            расх["файл КП есть, стадия отказа/молчания"] += 1
    return по, возраст_paw, расх


def main() -> int:
    import config
    import people
    from bitrix_client import BitrixClient, сводка_нагрузки

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    сегодня = dt.date.today()
    с = (сегодня - dt.timedelta(days=DAYS)).isoformat() + "T00:00:00"
    стадии = client.list_paged("crm.status.list", {"filter": {"ENTITY_ID": f"DYNAMIC_{SPA}_STAGE_{CAT}"},
                                                   "order": {"SORT": "ASC"}})
    роль_стадии = {}
    print(f"зонд v52 · {сегодня} · стадии воронки {CAT} СП-{SPA}: {len(стадии)}")
    for s in стадии:
        sid, имя = str(s.get("STATUS_ID")), str(s.get("NAME") or "")
        роль_стадии[sid] = роль(имя)
        print(f"  {sid} | {имя} | семантика {s.get('SEMANTICS') or 'P'} | порядок {s.get('SORT')} | роль {роль_стадии[sid]}")

    фильтр = {"categoryId": CAT, ">=createdTime": с}
    env = client.call_envelope("crm.item.list", {"entityTypeId": SPA, "filter": фильтр, "select": ["id"], "start": 0}) or {}
    ждём = int(env.get("total") or 0)
    прочие = [f for f in config.RFQ_QUOTE_FIELDS if f != OFFER_FIELD]
    карточки = client.list_items(SPA, filter=фильтр, select=["id", "stageId", "movedTime", "parentId2", OFFER_FIELD, *прочие])
    print(f"карточек за {DAYS} дн.: {len(карточки)} из {ждём}; поле Offer from supplier в списке КП дашборда: "
          f"{'да' if OFFER_FIELD in config.RFQ_QUOTE_FIELDS else 'НЕТ'}")
    if len(карточки) < ждём:
        print("::error::выгрузка карточек оборвалась")
        return 1
    по, возраст_paw, расх = разбор_карточек(карточки, роль_стадии, прочие, сегодня)
    print("\nКАРТОЧКИ ПО РОЛИ СТАДИИ (роль | всего | с файлом Offer | с файлом в прочих полях)")
    for r in [x for x, _ in РОЛИ] + ["прочее"]:
        if по[r]["всего"]:
            print(f"  {r} | {по[r]['всего']} | {по[r]['с файлом Offer']} | {по[r]['с файлом в прочих полях']}")
    print("  Price at Work, возраст в стадии: " + "; ".join(f"{b} {возраст_paw[b]}" for _, b in КОРЗИНЫ if возраст_paw[b]))
    print("\nРАСХОЖДЕНИЯ С ПРАВИЛАМИ")
    for k, v in расх.most_common():
        print(f"  {k}: {v}")

    поля = client.call("crm.deal.fields") or {}
    код = next((c for c, m in поля.items() if ECON_LABEL.search(str(m.get("formLabel") or m.get("listLabel") or m.get("title") or ""))), None)
    if not код:
        print("\n::warning::поле сделки «Экономика готова» не найдено по подписи")
        print(сводка_нагрузки())
        return 0
    варианты = {str(i.get("ID")): str(i.get("VALUE")) for i in (поля[код].get("items") or [])}
    print(f"\nПОЛЕ СДЕЛКИ «Экономика готова»: {код}, тип {поля[код].get('type')}, варианты: "
          + "; ".join(f"{k}={v}" for k, v in варианты.items()))
    да = [k for k, v in варианты.items() if re.match(r"^\s*да\s*$", v, re.I)]
    if not да:
        print("::warning::варианта «Да» нет")
        print(сводка_нагрузки())
        return 0
    сделки = client.list_deals_fast(filter={код: да}, select=["ID", "CATEGORY_ID", "STAGE_SEMANTIC_ID", код])
    сделки = [д for д in сделки if str(д.get("CATEGORY_ID") or "0") not in people.TECH_CATS]
    ids = {str(д["ID"]) for д in сделки}
    запросы = client.list_items(SPA, filter={"categoryId": CAT, "@parentId2": sorted(ids, key=int)},
                                select=["id", "stageId", "parentId2"]) if ids else []
    по_сделке = collections.defaultdict(collections.Counter)
    for к in запросы:
        по_сделке[str(к.get("parentId2"))][роль_стадии.get(str(к.get("stageId") or ""), "прочее")] += 1
    без = sum(1 for d in ids if not по_сделке[d])
    paw = sum(1 for d in ids if по_сделке[d]["price_at_work"])
    без_sel = sum(1 for d in ids if по_сделке[d] and not по_сделке[d]["selected"])
    чисто = sum(1 for d in ids if по_сделке[d] and not по_сделке[d]["price_at_work"] and по_сделке[d]["selected"])
    print(f"сделок с «Экономика готова» = «Да» (без технических воронок): {len(ids)} · по правилу чисто: {чисто} · "
          f"есть запрос на Price at Work: {paw} · нет ни одного Selected: {без_sel} · запросов нет вовсе: {без} · "
          f"в работе / выиграна / проиграна: {sum(1 for д in сделки if str(д.get('STAGE_SEMANTIC_ID') or 'P') == 'P')} / "
          f"{sum(1 for д in сделки if str(д.get('STAGE_SEMANTIC_ID')) == 'S')} / {sum(1 for д in сделки if str(д.get('STAGE_SEMANTIC_ID')) == 'F')}")
    print(сводка_нагрузки())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        места = []
        tb = e.__traceback__
        while tb:
            места.append(f"{Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}")
            tb = tb.tb_next
        print(f"::error::зонд упал: {type(e).__name__} · " + " → ".join(места[-4:]))
        raise SystemExit(1)
