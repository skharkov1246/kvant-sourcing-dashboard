#!/usr/bin/env python3
"""Зонд v54 «Договоры в портале»: где лежат договоры с заказчиками и поставщиками,
спецификации, акты, счета, рекламации — и насколько они заполнены. Замер перед
конвейером чтения договоров (условия: срок, базис, оплата, пени, гарантия, приёмка).

ЧТО МЕРЯЕМ (только счётчики и доли; подписи полей и названия смарт-процессов —
настройка портала; ни имён, ни компаний, ни сумм, ни текста документов):
  • смарт-процессы с подписью про договор / спецификацию / соглашение / счёт / акт /
    рекламацию / паспорт / сервис / проверку контрагента: записей всего и за год,
    воронки и стадии с числом записей, связь со сделкой, поля-файлы и их
    заполненность, поля-даты и деньги с заполненностью;
  • сделки реализации (кат. 0): «Scan of the signed contract», бюджет, экономика;
  • заказы СП-172: Purchase order, Specifications and invoices, Order confirmation,
    проформа — заполненность и файлов на заказ.
НАГРУЗКА: ≈ 150 запросов (выборки до LIMIT записей на смарт-процесс).
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

LIMIT = int(os.environ.get("CONTRACTS_LIMIT", "2000"))
ТЕМЫ = re.compile(r"договор|контракт|спецификац|соглашен|\bДС\b|сч[её]т|акт|рекламац|претенз|паспорт|сервис|"
                  r"контрагент|гарант|warrant|claim|contract", re.I)
СДЕЛКА_ФАЙЛЫ = {
    "UF_CRM_1727110655024": "Scan of the signed contract",
    "UF_CRM_1577100780": "Transaction budget (ссылка)",
    "UF_CRM_1740133235324": "Экономика проекта (ссылка)",
}
ЗАКАЗ_ФАЙЛЫ = {
    "ufCrm20_1723385385957": "Purchase order",
    "ufCrm20_1723236635": "Specifications and invoices (ссылки)",
    "ufCrm20_1787562161354": "Order confirmation",
    "ufCrm20_1787562201347": "Проформа инвойса / счёт",
    "ufCrm20_1749654314976": "УПД",
    "ufCrm20_1751555578101": "Payment Order (client)",
}


def есть(v) -> bool:
    if isinstance(v, list):
        return any(есть(x) for x in v)
    return v not in (None, "", 0, "0", False, "N", {})


def файлов(v) -> int:
    if isinstance(v, list):
        return sum(файлов(x) for x in v)
    return 1 if isinstance(v, dict) and (v.get("id") or v.get("url")) else 0


def доля(a: int, b: int) -> str:
    return f"{a} из {b} ({100 * a / b:.0f} %)" if b else "0 из 0"


def заполненность(записи: list[dict], поля: dict[str, str]) -> list[tuple[str, str, int, int]]:
    """[(код, подпись, заполнено, файлов)] — чистая функция."""
    out = []
    for код, подпись in поля.items():
        out.append((код, подпись, sum(1 for r in записи if есть(r.get(код))),
                    sum(файлов(r.get(код)) for r in записи)))
    return out


def main() -> int:
    import config
    from bitrix_client import BitrixClient, сводка_нагрузки

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    сегодня = dt.date.today()
    год = (сегодня - dt.timedelta(days=365)).isoformat()
    print(f"зонд v54 · {сегодня} · договоры и документы в портале")

    типы = (client.call("crm.type.list", {}) or {}).get("types") or []
    print(f"\nСМАРТ-ПРОЦЕССОВ В ПОРТАЛЕ: {len(типы)}")
    for t in sorted(типы, key=lambda x: int(x.get("entityTypeId") or 0)):
        print(f"  СП-{t.get('entityTypeId')} «{t.get('title')}»")
    свои = [t for t in типы if ТЕМЫ.search(str(t.get("title") or ""))]
    print(f"\nПО ТЕМЕ (договор, спецификация, счёт, акт, рекламация, сервис…): {len(свои)}")
    for t in свои:
        eid = int(t["entityTypeId"])
        env = client.call_envelope("crm.item.list", {"entityTypeId": eid, "select": ["id"], "start": 0}) or {}
        env_год = client.call_envelope("crm.item.list", {"entityTypeId": eid, "select": ["id"],
                                                         "filter": {">=createdTime": год}, "start": 0}) or {}
        print(f"\n== СП-{eid} «{t.get('title')}»: записей {env.get('total', '?')}, за 365 дн. {env_год.get('total', '?')} ==")
        поля = (client.call("crm.item.fields", {"entityTypeId": eid}) or {}).get("fields") or {}
        файловые = {к: str(m.get("title") or m.get("formLabel") or к) for к, m in поля.items() if m.get("type") == "file"}
        даты = {к: str(m.get("title") or к) for к, m in поля.items()
                if m.get("type") in ("date", "datetime") and к.startswith("ufCrm")}
        деньги = {к: str(m.get("title") or к) for к, m in поля.items()
                  if m.get("type") in ("money", "double", "integer") and к.startswith("ufCrm")}
        связи = {к: str(m.get("title") or к) for к, m in поля.items() if к.startswith("parentId")}
        прочие = {к: str(m.get("title") or к) for к, m in поля.items()
                  if к.startswith("ufCrm") and к not in файловые and к not in даты and к not in деньги}
        sel = ["id", "stageId", "categoryId", "createdTime", *связи, *файловые, *даты, *деньги, *прочие]
        записи = client.list_items(eid, filter={}, select=sel, max_items=LIMIT)
        n = len(записи)
        print(f"  в выборке {n} (до {LIMIT}) · полей: файлов {len(файловые)}, дат {len(даты)}, чисел {len(деньги)}, "
              f"прочих {len(прочие)}, связей {len(связи)}")
        по_ст = collections.Counter(str(r.get("stageId")) for r in записи)
        print("  стадии: " + "; ".join(f"{k} {v}" for k, v in по_ст.most_common(12)))
        for заголовок, набор in (("связи", связи), ("файлы", файловые), ("даты", даты), ("числа", деньги), ("прочие поля", прочие)):
            ряды = [x for x in заполненность(записи, набор) if x[2]]
            if ряды:
                print(f"  {заголовок} (код | подпись | заполнено | файлов):")
                for код, подп, k, f in sorted(ряды, key=lambda x: -x[2])[:25]:
                    print(f"    {код} | {подп[:70]} | {доля(k, n)}" + (f" | {f}" if f else ""))

    print("\nСДЕЛКИ РЕАЛИЗАЦИИ (кат. 0), открытые и созданные за 365 дн.")
    сделки = client.list_deals_fast(filter={"CATEGORY_ID": 0, ">=DATE_CREATE": год}, select=["ID", *СДЕЛКА_ФАЙЛЫ])
    сделки += client.list_deals_fast(filter={"CATEGORY_ID": 0, "STAGE_SEMANTIC_ID": "P", "<DATE_CREATE": год},
                                     select=["ID", *СДЕЛКА_ФАЙЛЫ])
    for код, подп, k, f in заполненность(сделки, СДЕЛКА_ФАЙЛЫ):
        print(f"  {подп}: {доля(k, len(сделки))}" + (f", файлов {f}" if f else ""))

    print("\nЗАКАЗЫ СП-172, созданные за 365 дн.")
    заказы = client.list_items(172, filter={">=createdTime": год}, select=["id", *ЗАКАЗ_ФАЙЛЫ])
    for код, подп, k, f in заполненность(заказы, ЗАКАЗ_ФАЙЛЫ):
        print(f"  {подп}: {доля(k, len(заказы))}" + (f", файлов {f}" if f else ""))
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
