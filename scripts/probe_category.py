#!/usr/bin/env python3
"""Зонд v55 «Линии продукт-оунеров»: насколько заполнена «Категория оборудования»
сделки — основа линии продукт-оунера (решение владельца 19.10.2026: линия PO —
по этому полю сделки), и есть ли такая же категория у заказа поставщику и запроса.
Замер перед разделом «Линии PO» на вкладке «КАМ и продукт-оунеры».

ЧТО МЕРЯЕМ (только счётчики и доли; значения списка категорий — настройка портала;
ни имён людей, ни компаний, ни сумм):
  • поле сделки «Категория оборудования» (множественный список): значения списка;
    заполненность у открытых сделок по воронкам, у сделок реализации (кат. 0), у
    созданных за 365 дн. по месяцам; сколько сделок с несколькими значениями;
    сделок на значение (открытый пресейл / открытая реализация / создано за год);
  • «Product leader» рядом: заполненность и у скольких сделок с категорией он есть;
    сколько разных PO на категорию (числом, без имён);
  • поля с подписью про категорию / оборудование / продукт у заказов СП-172 и
    запросов СП-166 — код, подпись, тип, заполненность.
НАГРУЗКА: ≈ 120 запросов (сделки открытые и за год, справочники, две выборки по 2000).
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

КАТЕГОРИЯ = "UF_CRM_1781862699536"     # «Категория оборудования» сделки (enumeration, множественное)
PO = "UF_CRM_1779187425"               # «Product leader»
REAL = "0"                             # воронка «Реализация»
LIMIT = int(os.environ.get("CATEGORY_LIMIT", "2000"))
ТЕМА = re.compile(r"категор|оборудован|product|продукт|equipment|category", re.I)


def значения(v) -> list[str]:
    """Значение поля-списка → список id (пусто, одно, несколько)."""
    if isinstance(v, list):
        return [str(x) for x in v if str(x or "").strip() not in ("", "0")]
    s = str(v or "").strip()
    return [s] if s and s != "0" else []


def доля(a: int, b: int) -> str:
    return f"{a} из {b} ({100 * a / b:.0f} %)" if b else "0 из 0"


def есть(v) -> bool:
    if isinstance(v, list):
        return any(есть(x) for x in v)
    return v not in (None, "", 0, "0", False, {})


def сводка(сделки: list[dict], подписи: dict[str, str]) -> list[str]:
    """Строки отчёта по набору сделок — чистая функция (тест без портала)."""
    n = len(сделки)
    с_кат = [d for d in сделки if значения(d.get(КАТЕГОРИЯ))]
    много = sum(1 for d in с_кат if len(значения(d.get(КАТЕГОРИЯ))) > 1)
    с_po = sum(1 for d in сделки if есть(d.get(PO)))
    оба = sum(1 for d in с_кат if есть(d.get(PO)))
    out = [f"  категория: {доля(len(с_кат), n)}; несколько значений: {много}",
           f"  Product leader: {доля(с_po, n)}; из сделок с категорией — {доля(оба, len(с_кат))}"]
    по_знач = collections.Counter(k for d in с_кат for k in значения(d.get(КАТЕГОРИЯ)))
    po_на = collections.defaultdict(set)
    for d in с_кат:
        for k in значения(d.get(КАТЕГОРИЯ)):
            if есть(d.get(PO)):
                po_на[k].add(str(d.get(PO)))
    for k, c in по_знач.most_common():
        out.append(f"    «{подписи.get(k, k)}»: {c} · разных PO {len(po_на[k])}")
    return out


def main() -> int:
    import config
    from bitrix_client import BitrixClient, сводка_нагрузки

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    сегодня = dt.date.today()
    год = (сегодня - dt.timedelta(days=365)).isoformat()
    print(f"зонд v55 · {сегодня} · линии продукт-оунеров: «Категория оборудования»")

    uf = client.userfields()
    meta = uf.get(КАТЕГОРИЯ) or {}
    подписи = {str(k): str(v) for k, v in (meta.get("enum") or {}).items()}
    print(f"\nПОЛЕ {КАТЕГОРИЯ}: тип {meta.get('type', 'нет поля')}, значений в списке {len(подписи)}")
    for k, v in подписи.items():
        print(f"  {k}: {v}")
    if not meta:
        print("::warning::поля нет в портале — линии PO строить не на чем")

    sel = ["ID", "CATEGORY_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE", КАТЕГОРИЯ, PO]
    открытые = client.list_deals_fast(filter={"STAGE_SEMANTIC_ID": "P"}, select=sel)
    воронки = client.categories()
    print(f"\nОТКРЫТЫЕ СДЕЛКИ: {len(открытые)}")
    for строка in сводка(открытые, подписи):
        print(строка)
    по_вор = collections.defaultdict(list)
    for d in открытые:
        по_вор[str(d.get("CATEGORY_ID") or "0")].append(d)
    print("  по воронкам (заполнено категорией):")
    for cid, ds in sorted(по_вор.items(), key=lambda x: -len(x[1])):
        print(f"    {воронки.get(cid, 'кат.' + cid)}: "
              f"{доля(sum(1 for d in ds if значения(d.get(КАТЕГОРИЯ))), len(ds))}")

    print("\nОТКРЫТАЯ РЕАЛИЗАЦИЯ (кат. 0):")
    for строка in сводка([d for d in открытые if str(d.get("CATEGORY_ID") or "0") == REAL], подписи):
        print(строка)
    print("\nОТКРЫТЫЙ ПРЕСЕЙЛ (прочие воронки):")
    for строка in сводка([d for d in открытые if str(d.get("CATEGORY_ID") or "0") != REAL], подписи):
        print(строка)

    созданные = client.list_deals_fast(filter={">=DATE_CREATE": год}, select=sel)
    print(f"\nСОЗДАНЫ ЗА 365 ДН.: {len(созданные)}")
    for строка in сводка(созданные, подписи):
        print(строка)
    по_мес = collections.defaultdict(lambda: [0, 0])
    for d in созданные:
        м = str(d.get("DATE_CREATE") or "")[:7]
        по_мес[м][0] += 1
        по_мес[м][1] += bool(значения(d.get(КАТЕГОРИЯ)))
    print("  по месяцам создания (заполнено / всего):")
    for м in sorted(по_мес):
        print(f"    {м}: {по_мес[м][1]} / {по_мес[м][0]}")

    for eid, имя in ((172, "ЗАКАЗЫ СП-172"), (config.SPA_ENTITY_TYPE_ID, f"ЗАПРОСЫ СП-{config.SPA_ENTITY_TYPE_ID}")):
        поля = (client.call("crm.item.fields", {"entityTypeId": eid}) or {}).get("fields") or {}
        свои = {к: m for к, m in поля.items() if к.startswith("ufCrm")
                and ТЕМА.search(str(m.get("title") or m.get("formLabel") or ""))}
        print(f"\n{имя}: полей по теме {len(свои)}")
        if not свои:
            continue
        записи = client.list_items(eid, filter={">=createdTime": год}, select=["id", *свои], max_items=LIMIT)
        for к, m in свои.items():
            print(f"  {к} | {str(m.get('title') or '')[:60]} | {m.get('type')} | "
                  f"{доля(sum(1 for r in записи if есть(r.get(к))), len(записи))}")
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
