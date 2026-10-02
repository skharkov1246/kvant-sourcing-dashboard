#!/usr/bin/env python3
"""Снимок сделок, запросов и заказов поставщикам из Bitrix24 в KV (ключ deals:v1).

ЗАЧЕМ. Распоряжение владельца 02.10.2026: в конструкторе отчётов базы
«Управление поставщиками» нужны сделки и запросы поставщикам построчно, а также
заказы поставщикам по сделкам в реализации — там то, что мы уже оплачиваем,
то есть подлинная история закупки. До этого снимка портал знал о запросах
только итоги по поставщику (suppliers:v1 → rfq), а о заказах — ничего.

ЧТО В СНИМКЕ:
  deals   — сделки, к которым есть запрос поставщику или заказ поставщику;
  rfq     — карточки СП-166 «Запросы поставщикам» с даты SINCE;
  orders  — карточки СП-172 «Заказы» (заказ ПОСТАВЩИКУ: companyId — поставщик,
            opportunity — закупка, contracts.py);
  lines   — товарные строки заказов (crm.item.productrow.list);
  money   — ВСЕ суммы одним разделом: суммы сделок, закупки заказов, цены строк,
            курсы. Воркер закрывает его правом suppliers_fin (SUPPLIERS_FIELDS),
            поэтому деньги не размазаны по строкам, а лежат в одном ключе.

НАГРУЗКА НА ПОРТАЛ (CLAUDE.md, «Битрикс не перегружать»; навык bitrix-ingest).
Все вызовы — через BitrixClient (пауза интервал_портала). Списки — по ключу
>id с start=-1, без подсчёта total. Строки заказов — пакетом batch по 50 команд.
Запросов ≈ карточек/50 + заказов/50 × 2 + сделок/50 + компаний/50 + справочники.
Перед чтением печатается ожидаемое число записей, после — сверка с ним: обход
СП-166 уже обрывался без ошибки (навык, п. 3).

ЧТО НЕ ПОПАДАЕТ В ЖУРНАЛ. Репозиторий публичный: в журнал — только счётчики,
коды и названия ПОЛЕЙ (правило 17). Ни названий компаний, ни сумм, ни сделок.

ПО УМОЛЧАНИЮ ВХОЛОСТУЮ: читает портал, собирает снимок, печатает счётчики и
размер — в KV ничего не пишет. Запись — ключом --apply.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEY = "deals:v1"
SINCE = "2025-01-01T00:00:00"
RFQ_ENTITY = 166
ORDER_ENTITY = 172
# Тот же предел, что у воркера (SUPPLIERS_MAX_BYTES).
MAX_BYTES = 8 * 1024 * 1024

# Поля карточки запроса: КП поставщика — файловые поля (config.RFQ_QUOTE_FIELDS);
# маска "*" файловых полей не возвращает, только поимённо (main.py, RFQ_SELECT).
RFQ_SELECT_BASE = ["id", "title", "stageId", "categoryId", "createdTime", "movedTime",
                   "updatedTime", "parentId2", "companyId", "ufCrm18Supplier"]

# Плановые даты заказа СП-172 (contracts.py, зонд v9).
DL_CUSTOMER = "ufCrm20_1728900218435"   # «Date of deadline to customer»
PROD_END = "ufCrm20_1724941935"         # «Production end date, budget»
INBOUND_PLAN = "ufCrm20_1709294315471"  # «Inbound Delivery Date, planned»

# Деньги сделки, продублированные из «экономики проекта» (contracts.py).
ECON_PAID = "UF_CRM_1713874110281"      # «Оплачено»
ECON_REST = "UF_CRM_1713874579940"      # «Остаток к оплате»
DEAL_SELECT = ["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "COMPANY_ID",
               "DATE_CREATE", "CLOSEDATE", "OPPORTUNITY", "CURRENCY_ID", ECON_PAID, ECON_REST]

BATCH = 50


class PublishError(RuntimeError):
    pass


def require(условие, код):
    if not условие:
        raise PublishError(код)


# ── чистые функции: тестируются без портала ─────────────────────────────────

def номер(v) -> str | None:
    """Номер записи портала: только цифры без ведущего нуля; «0» и пусто — нет."""
    s = str(v if v is not None else "").strip()
    return s if s.isdigit() and s != "0" and not s.startswith("0") else None


def компании_ссылки(v) -> list[str]:
    """Значение crm-поля (['CO_372', 'C_45'] или '372') → номера компаний."""
    out = []
    for x in (v if isinstance(v, list) else [v] if v else []):
        s = str(x).strip()
        n = номер(s[3:]) if s.startswith("CO_") else номер(s)
        if n and n not in out:
            out.append(n)
    return out


def поставщик_запроса(карточка) -> str | None:
    """Поставщик карточки СП-166: ссылка «Supplier», затем companyId (main.py)."""
    ссылки = компании_ссылки(карточка.get("ufCrm18Supplier"))
    return ссылки[0] if ссылки else номер(карточка.get("companyId"))


def исход_стадии(stage) -> str:
    """Стадия смарт-процесса → S (успех), F (провал), P (в работе)."""
    s = str(stage or "")
    return "S" if s.endswith(":SUCCESS") else "F" if s.endswith(":FAIL") else "P"


def дата(v) -> str | None:
    s = str(v or "").strip()
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else None


def число(v) -> float | None:
    """Сумма портала: число или money-строка «1000.50|EUR»."""
    if v is None or v == "":
        return None
    s = str(v).split("|", 1)[0].replace(",", ".").strip()
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x == x else None


def валюта_денег(v, умолчание=None):
    s = str(v or "")
    if "|" not in s:
        return умолчание
    return s.split("|", 1)[1].strip() or умолчание


def есть_кп(карточка, поля) -> bool:
    return any(карточка.get(f) not in (None, "", [], {}, False) for f in поля)


def собрать(*, сделки, запросы, заказы, строки, компании, стадии, курсы, база_валюты,
            поля_кп, сейчас=None) -> dict:
    """Записи портала → снимок deals:v1. Чистая функция.

    сделки   — {id: crm.deal}; запросы, заказы — списки crm.item;
    строки   — {номер заказа: [productRow]}; компании — {id: название};
    стадии   — {"deal": {...}, "rfq": {...}, "order": {...}}; курсы — {валюта: к базе}.
    """
    мои_деньги = {"deals": {}, "orders": {}, "lines": [], "base": база_валюты, "rates": курсы}
    нужные_компании = set()

    out_rfq = []
    for к in запросы:
        n = номер(к.get("id"))
        if not n:
            continue
        sup = поставщик_запроса(к)
        if sup:
            нужные_компании.add(sup)
        out_rfq.append({
            "id": n, "deal": номер(к.get("parentId2")), "supplier": sup,
            "stage": к.get("stageId"), "outcome": исход_стадии(к.get("stageId")),
            "created": дата(к.get("createdTime")), "moved": дата(к.get("movedTime")),
            "quote": есть_кп(к, поля_кп),
        })

    out_orders = []
    for з in заказы:
        n = номер(з.get("id"))
        if not n:
            continue
        sup = номер(з.get("companyId"))
        if sup:
            нужные_компании.add(sup)
        out_orders.append({
            "id": n, "title": з.get("title"), "deal": номер(з.get("parentId2")), "supplier": sup,
            "stage": з.get("stageId"), "outcome": исход_стадии(з.get("stageId")),
            "created": дата(з.get("createdTime")), "moved": дата(з.get("movedTime")),
            "deadline": дата(з.get(DL_CUSTOMER)), "prod_end": дата(з.get(PROD_END)),
            "inbound": дата(з.get(INBOUND_PLAN)),
        })
        сумма = число(з.get("opportunity"))
        if сумма is not None:
            мои_деньги["orders"][n] = [сумма, з.get("currencyId") or база_валюты]

    валюта_заказа = {номер(з.get("id")): з.get("currencyId") or база_валюты for з in заказы}
    out_lines = []
    for заказ, ряды in строки.items():
        for р in ряды or []:
            out_lines.append({
                "order": заказ, "name": (р.get("productName") or "").strip() or None,
                "qty": число(р.get("quantity")), "unit": р.get("measureName"),
            })
            # Без НДС — priceExclusive (цена после скидки без налога); сравниваем
            # без НДС (CLAUDE.md, «Цена и предложение»). Индекс — тот же, что у lines.
            мои_деньги["lines"].append([число(р.get("priceExclusive")), число(р.get("price")),
                                        число(р.get("taxRate")), валюта_заказа.get(заказ)])

    нужные_сделки = {r["deal"] for r in out_rfq if r["deal"]} | {o["deal"] for o in out_orders if o["deal"]}
    out_deals = []
    for n in sorted(нужные_сделки, key=int):
        д = сделки.get(n)
        if not д:
            continue
        клиент = номер(д.get("COMPANY_ID"))
        if клиент:
            нужные_компании.add(клиент)
        out_deals.append({
            "id": n, "title": д.get("TITLE"), "customer": клиент,
            "category": str(д.get("CATEGORY_ID") or "0"), "stage": д.get("STAGE_ID"),
            "outcome": {"S": "S", "F": "F"}.get(str(д.get("STAGE_SEMANTIC_ID") or ""), "P"),
            "created": дата(д.get("DATE_CREATE")), "closed": дата(д.get("CLOSEDATE")),
        })
        вал = д.get("CURRENCY_ID") or база_валюты
        мои_деньги["deals"][n] = [число(д.get("OPPORTUNITY")), вал,
                                  число(д.get(ECON_PAID)), число(д.get(ECON_REST)),
                                  валюта_денег(д.get(ECON_PAID), вал)]

    без_сделки = sum(1 for r in out_rfq if r["deal"] and r["deal"] not in сделки) + \
        sum(1 for o in out_orders if o["deal"] and o["deal"] not in сделки)
    снимок = {
        "version": 1,
        "published_at": (сейчас or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": SINCE[:10],
        "totals": {
            "deals": len(out_deals), "rfq": len(out_rfq), "orders": len(out_orders),
            "lines": len(out_lines),
            "rfq_with_quote": sum(1 for r in out_rfq if r["quote"]),
            "orders_live": sum(1 for o in out_orders if o["outcome"] != "F"),
            "orders_with_lines": sum(1 for k, v in строки.items() if v),
            "lost_deal_links": без_сделки,
        },
        "companies": {c: компании[c] for c in sorted(нужные_компании, key=int) if c in компании},
        "stages": стадии,
        "deals": out_deals,
        "rfq": out_rfq,
        "orders": out_orders,
        "lines": out_lines,
        "money": мои_деньги,
    }
    return снимок


# ── портал ──────────────────────────────────────────────────────────────────

def строки_заказов(client, номера) -> dict[str, list]:
    """Товарные строки заказов СП-172 пакетами batch по 50 команд.

    ownerType смарт-процесса — «T» и шестнадцатеричный номер типа (172 → Tac).
    У заказа больше 50 строк — дочитывается отдельно по >id.
    """
    тип = "T" + format(ORDER_ENTITY, "x")
    out: dict[str, list] = {}
    дочитать = []
    for i in range(0, len(номера), BATCH):
        часть = номера[i:i + BATCH]
        # Ключи кодируются: «=» в «filter[=ownerType]» иначе разрежет пару
        # «ключ=значение» на стороне портала не там.
        cmd = {f"o{n}": "crm.item.productrow.list?" + parse.urlencode(
            {"filter[=ownerType]": тип, "filter[=ownerId]": n, "order[id]": "asc"}) for n in часть}
        res = client.call("batch", {"halt": 0, "cmd": cmd}) or {}
        результаты = res.get("result") or {}
        ошибки = res.get("result_error") or {}
        всего = res.get("result_total") or {}
        for n in часть:
            k = f"o{n}"
            if k in ошибки and ошибки[k]:
                out[n] = []            # ACCESS_DENIED: тип без товарных строк
                continue
            ряды = (результаты.get(k) or {}).get("productRows") or [] if isinstance(результаты, dict) else []
            out[n] = list(ряды)
            if isinstance(всего, dict) and int(всего.get(k) or 0) > len(ряды):
                дочитать.append(n)
    for n in дочитать:
        while True:
            последний = out[n][-1]["id"] if out[n] else 0
            res = client.call("crm.item.productrow.list", {
                "filter": {"=ownerType": тип, "=ownerId": int(n), ">id": последний},
                "order": {"id": "asc"}, "start": -1}) or {}
            ряды = res.get("productRows") or [] if isinstance(res, dict) else []
            out[n].extend(ряды)
            if len(ряды) < 50:
                break
    return out


def поля_заказа(client, заказы) -> list[tuple]:
    """Зонд: поля СП-172 и их заполненность — чтобы найти поля оплаты.

    В журнал — код, название и тип ПОЛЯ и число заполненных записей. Значения
    полей не печатаются (правило 17).
    """
    res = client.call("crm.item.fields", {"entityTypeId": ORDER_ENTITY}) or {}
    поля = (res.get("fields") if isinstance(res, dict) else None) or {}
    out = []
    for код, о in поля.items():
        заполнено = sum(1 for з in заказы if з.get(код) not in (None, "", [], {}, False, "0", 0))
        out.append((код, (о or {}).get("title") or "", (о or {}).get("type") or "", заполнено))
    return out


def читать_портал(webhook):
    import config
    from bitrix_client import BitrixClient, сводка_нагрузки

    require(isinstance(webhook, str) and webhook.strip(), "BITRIX_WEBHOOK_MISSING")
    client = BitrixClient(webhook)

    # Ожидаемые числа — до обхода: итог обхода сверяется с ними (навык, п. 3).
    env = client.call_envelope("crm.item.list", {"entityTypeId": RFQ_ENTITY,
                               "filter": {">=createdTime": SINCE}, "select": ["id"], "start": 0})
    ждём_rfq = int((env or {}).get("total") or 0)
    env = client.call_envelope("crm.item.list", {"entityTypeId": ORDER_ENTITY,
                               "filter": {}, "select": ["id"], "start": 0})
    ждём_заказов = int((env or {}).get("total") or 0)
    print(f"ожидается: карточек запросов с {SINCE[:10]} — {ждём_rfq}, заказов поставщикам — {ждём_заказов}")
    print(f"оценка нагрузки: ≈ {ждём_rfq // 50 + 2 * (ждём_заказов // 50) + 60} запросов к порталу")

    поля_кп = list(config.RFQ_QUOTE_FIELDS)
    запросы = client.list_items(RFQ_ENTITY, filter={">=createdTime": SINCE},
                                select=RFQ_SELECT_BASE + поля_кп)
    # Заказы — все, без даты: заказ 2024 года по сделке, которая ещё в
    # реализации, — та же подлинная история закупки. "*" — для зонда полей оплаты.
    заказы = client.list_items(ORDER_ENTITY, filter={}, select=["*"])
    print(f"прочитано: карточек запросов {len(запросы)} из {ждём_rfq}, заказов {len(заказы)} из {ждём_заказов}")
    require(len(запросы) >= ждём_rfq, "RFQ_READ_INCOMPLETE")
    require(len(заказы) >= ждём_заказов, "ORDERS_READ_INCOMPLETE")

    номера_заказов = [n for n in (номер(з.get("id")) for з in заказы) if n]
    строки = строки_заказов(client, номера_заказов)

    нужные_сделки = {номер(к.get("parentId2")) for к in запросы} | {номер(з.get("parentId2")) for з in заказы}
    нужные_сделки.discard(None)
    сделки = client.deals_by_ids(sorted(нужные_сделки, key=int), select=DEAL_SELECT)

    компании_id = set()
    for к in запросы:
        s = поставщик_запроса(к)
        if s:
            компании_id.add(s)
    компании_id |= {номер(з.get("companyId")) for з in заказы}
    компании_id |= {номер(д.get("COMPANY_ID")) for д in сделки.values()}
    компании_id.discard(None)
    компании = client.companies_by_ids(компании_id)

    стадии = {"deal": client.stages(), "deal_category": client.categories(), "rfq": {}, "order": {}}
    for cid in sorted({int(к.get("categoryId") or 0) for к in запросы if к.get("categoryId")}):
        стадии["rfq"].update(client.spa_stages(RFQ_ENTITY, cid))
    for cid in sorted({int(з.get("categoryId") or 0) for з in заказы if з.get("categoryId")}):
        стадии["order"].update(client.spa_stages(ORDER_ENTITY, cid))

    валюты = client.call("crm.currency.list", {}) or []
    база = next((x.get("CURRENCY") for x in валюты if x.get("BASE") == "Y"), "EUR")
    курсы = {}
    for x in валюты:
        try:
            курсы[x.get("CURRENCY")] = float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    зонд = поля_заказа(client, заказы)
    print(сводка_нагрузки())
    return dict(сделки={str(k): v for k, v in сделки.items()}, запросы=запросы, заказы=заказы,
                строки=строки, компании=компании, стадии=стадии, курсы=курсы,
                база_валюты=база, поля_кп=поля_кп), зонд


def _публикатор():
    spec = importlib.util.spec_from_file_location(
        "kvant_publish_suppliers", ROOT / "scripts" / "publish_suppliers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv=None):
    parser = argparse.ArgumentParser(description="Снимок сделок, запросов и заказов поставщикам в KV")
    parser.add_argument("--apply", action="store_true", help="записать в KV (иначе вхолостую)")
    parser.add_argument("--out", help="сохранить снимок в файл (для проверки глазами)")
    args = parser.parse_args(argv)

    try:
        вход, зонд = читать_портал(os.environ.get("BITRIX_WEBHOOK_URL"))
        снимок = собрать(**вход)
        raw = json.dumps(снимок, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        t = снимок["totals"]
        print("снимок: " + ", ".join(f"{k} {v}" for k, v in t.items()))
        исходы = collections.Counter(o["outcome"] for o in снимок["orders"])
        print("заказы по исходу: " + ", ".join(f"{k} {v}" for k, v in sorted(исходы.items())))
        print("зонд полей СП-172 (код · тип · заполнено · название), заполненные:")
        for код, назв, тип, n in sorted(зонд, key=lambda x: -x[3]):
            if n:
                print(f"  {код} · {тип} · {n} · {назв}")
        print(f"размер снимка: {len(raw)} Б ({len(raw) / 1024 / 1024:.2f} МиБ из {MAX_BYTES // 1024 // 1024})")
        require(len(raw) <= MAX_BYTES, "SNAPSHOT_TOO_LARGE")
        require(t["rfq"] > 0 and t["orders"] > 0, "SNAPSHOT_EMPTY")

        if args.out:
            with open(args.out, "wb") as fh:
                fh.write(raw)
            print(f"снимок сохранён в {args.out}")
        if not args.apply:
            print("вхолостую: в KV ничего не записано (--apply включает запись)")
            return 0

        ps = _публикатор()

        class CloudflareDeals(ps.Cloudflare):
            КЛЮЧИ = (KEY,)

        cf = CloudflareDeals(os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
                             os.environ.get("CLOUDFLARE_API_TOKEN", ""))
        namespace = cf.namespace()
        прежний = cf.get(namespace, KEY)
        cf.put(namespace, KEY, raw)
        print(f"опубликовано в KV, ключ {KEY}; прежний снимок был "
              f"{str(len(прежний)) + ' Б' if прежний else 'пуст'}")
        return 0
    except PublishError as e:
        print(f"::error::публикация не состоялась: {e}")
        return 1
    except Exception as e:  # PublishError публикатора поставщиков — свой класс
        if type(e).__name__ == "PublishError":
            print(f"::error::публикация не состоялась: {e}")
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
