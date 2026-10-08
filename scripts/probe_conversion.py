#!/usr/bin/env python3
"""Зонд: конверсия выданных ТКП в контракты по клиентским холдингам.

Вопрос владельца 08.10.2026: «какое количество контрактов мы получаем относительно
того количества предложений, которые мы выдаём» — по Норникелю и в сравнении.

ОПРЕДЕЛЕНИЯ — ТЕ ЖЕ, ЧТО НА ДАШБОРДЕ (company.py, «Пульс компании»):
  • КОНТРАКТ — сделка в реализации: есть непроигранный заказ поставщику СП-172
    (parentId2) или номер реализации в начале названия («871. …»), и сделка не
    проиграна (STAGE_SEMANTIC_ID ≠ F). Статус «выиграна» в портале почти не ставят.
  • ПРОИГРАНА — STAGE_SEMANTIC_ID = F.
  • ВЫДАННОЕ ПРЕДЛОЖЕНИЕ (ТКП) — сделка хотя бы раз входила в стадию «ТКП выдано»
    или дальше (stages.deal_reached_tkp по имени и семантике стадии) — по ИСТОРИИ
    стадий, а не по текущей: сделка, проигранная после выдачи ТКП, сейчас стоит в
    стадии проигрыша, и по текущей стадии её в знаменателе не было бы. Контракт без
    такой отметки в истории тоже считается выданным ТКП (договора без предложения не
    бывает) — и печатается отдельно как пробел данных.
  • ХОЛДИНГ — kam.client_dir(название компании сделки): та же разметка, что у
    вкладки КАМ. Вторая, независимая разметка Норникеля — по отделу ответственного
    (отдел 110 «Норникель», kam.CLIENT_GROUPS): расхождение печатается.
  Одна сделка — одно предложение: повторные редакции ТКП в одной сделке не
  размножают знаменатель.

ЧТО В ЖУРНАЛ. Репозиторий публичный (CLAUDE.md, правило 17; распоряжение 30.09):
только счётчики, доли, медианы дней и названия сегментов (холдингов, воронок,
стадий). Ни сумм, ни названий сделок, ни номеров, ни имён людей.

НАГРУЗКА (навык bitrix-ingest): сделки — по ключу >ID; компании — пачками по 50;
история стадий — пачками по 50 сделок (фильтр OWNER_ID массивом), заказы СП-172 —
по ключу >id. Ожидаемое число запросов печатается до обхода, сводка — в конце.
"""
from __future__ import annotations

import collections
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SINCE = os.environ.get("CONV_SINCE", "2024-06-01T00:00:00")   # глубина истории стадий, как HIST_SINCE в contracts.py
HOLDING = "Норникель"
DEPT_HOLDING = "110"           # kam.CLIENT_GROUPS: «Норникель»
ORDER_ENTITY = 172


def номер_реализации(title) -> int:
    """Номер реализации в начале названия («871. …»), как company._regno."""
    m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(title or ""))
    return int(m.group(1)) if m else 0


def ткп_стадия(stage_id, meta) -> bool:
    """Стадия — «ТКП выдано» или дальше (вкл. успех) по справочнику стадий."""
    from stages import deal_reached_tkp
    м = meta.get(stage_id) or {}
    return deal_reached_tkp(stage_id or "", м.get("sem"), м.get("name"))


def медиана(xs):
    return round(statistics.median(xs)) if xs else None


def доля(a, b):
    return round(100 * a / b) if b else None


def классифицировать(сделки, история, мета, заказы, холдинг_сделки):
    """Сделки → строка на сделку: holding, cat, cls (contract/lost/open), tkp (bool),
    tkp_from (history/contract/none), tkp_date, contract_date. Чистая функция.

    история — {deal_id: [(stage_id, iso_time), …]} первых входов по времени;
    заказы — список crm.item СП-172 (уже без проигранных); холдинг_сделки — {deal_id: имя}.
    """
    первый_заказ: dict[str, str] = {}
    for о in заказы:
        d = str(о.get("parentId2") or "")
        t = str(о.get("createdTime") or "")[:10]
        if d and d != "0" and t and (d not in первый_заказ or t < первый_заказ[d]):
            первый_заказ[d] = t
    out = []
    for д in сделки:
        did = str(д["ID"])
        sem = str(д.get("STAGE_SEMANTIC_ID") or "").upper()
        в_реализации = did in первый_заказ or номер_реализации(д.get("TITLE")) > 0
        cls = "lost" if sem == "F" else ("contract" if в_реализации else "open")
        ткп_даты = [t[:10] for s, t in история.get(did, []) if ткп_стадия(s, мета)]
        # текущая стадия тоже свидетель: история могла начаться позже SINCE
        if not ткп_даты and ткп_стадия(str(д.get("STAGE_ID") or ""), мета):
            ткп_даты = [str(д.get("DATE_MODIFY") or д.get("DATE_CREATE") or "")[:10]]
            откуда = "current"
        else:
            откуда = "history" if ткп_даты else "none"
        tkp = bool(ткп_даты)
        if not tkp and cls == "contract":
            tkp, откуда = True, "contract"
        out.append({
            "id": did, "holding": холдинг_сделки.get(did) or "Без клиента",
            "cat": str(д.get("CATEGORY_ID") or "0"), "cls": cls, "tkp": tkp, "tkp_from": откуда,
            "tkp_date": min(ткп_даты) if ткп_даты else None,
            "contract_date": первый_заказ.get(did) if cls == "contract" else None,
            "created": str(д.get("DATE_CREATE") or "")[:10],
        })
    return out


def свод(строки):
    """Строки одной выборки → счётчики конверсии ТКП → контракт."""
    ткп = [r for r in строки if r["tkp"]]
    к = [r for r in ткп if r["cls"] == "contract"]
    п = [r for r in ткп if r["cls"] == "lost"]
    дни = []
    for r in к:
        # Срок — только по истории: дата «текущей стадии» — дата правки, а не выдачи ТКП.
        if r["tkp_date"] and r["contract_date"] and r["tkp_from"] == "history":
            from datetime import date
            a, b = date.fromisoformat(r["tkp_date"]), date.fromisoformat(r["contract_date"])
            if b >= a:
                дни.append((b - a).days)
    return {
        "deals": len(строки), "tkp": len(ткп), "contracts": len(к), "lost": len(п),
        "open": len(ткп) - len(к) - len(п),
        "conv": доля(len(к), len(ткп)), "conv_decided": доля(len(к), len(к) + len(п)),
        "contracts_without_tkp_mark": sum(1 for r in к if r["tkp_from"] == "contract"),
        "days_median": медиана(дни), "days_n": len(дни),
    }


def строка_свода(имя, с):
    c = "—" if с["conv"] is None else f"{с['conv']}%"
    cd = "—" if с["conv_decided"] is None else f"{с['conv_decided']}%"
    dm = "—" if с["days_median"] is None else f"{с['days_median']} дн. (по {с['days_n']})"
    return (f"  {имя:28} ТКП {с['tkp']:5} · контрактов {с['contracts']:4} · проиграно {с['lost']:4} · "
            f"в работе {с['open']:4} · конверсия {c:>4} · среди решённых {cd:>4} · ТКП→контракт {dm}")


def отчёт(строки, отдел_сделки, мета, cats):
    print(f"\nВЫБОРКА: сделки, созданные с {SINCE[:10]}; одна сделка — одно предложение")
    print("Стадии, засчитанные как «ТКП выдано и дальше» (по воронкам):")
    по_воронке = collections.defaultdict(list)
    for sid, m in мета.items():
        if ткп_стадия(sid, мета):
            по_воронке[m.get("cat", "0")].append(m.get("name") or sid)
    for cat in sorted(по_воронке, key=lambda x: int(x) if x.isdigit() else 0):
        print(f"  {cats.get(cat, 'воронка ' + cat)}: " + "; ".join(по_воронке[cat]))

    print("\nКОНВЕРСИЯ ТКП → КОНТРАКТ")
    print(строка_свода("Все клиенты", свод(строки)))
    hn = [r for r in строки if r["holding"] == HOLDING]
    print(строка_свода(HOLDING, свод(hn)))
    # Имена в журнал — только холдингов из разметки КАМ (kam.CLIENT_HOLDINGS):
    # для прочих client_dir возвращает сырое название компании-клиента, а это
    # клиентские данные в публичном журнале. Прочие сводятся в одну строку.
    import kam
    известные = {имя for _, имя in kam.CLIENT_HOLDINGS}
    по_холдингу = collections.defaultdict(list)
    for r in строки:
        по_холдингу[r["holding"] if r["holding"] in известные else "Прочие клиенты"].append(r)
    print("\nДля сравнения — холдинги из разметки КАМ и все прочие клиенты одной строкой:")
    for h in sorted((h for h in по_холдингу if h != HOLDING),
                    key=lambda h: -sum(1 for r in по_холдингу[h] if r["tkp"])):
        print(строка_свода(h, свод(по_холдингу[h])))

    print(f"\n{HOLDING} — по году выдачи ТКП:")
    for год in sorted({(r["tkp_date"] or "")[:4] for r in hn if r["tkp"]} - {""}):
        print(строка_свода(год, свод([r for r in hn if (r["tkp_date"] or "").startswith(год)])))
    print("Все клиенты — по году выдачи ТКП:")
    for год in sorted({(r["tkp_date"] or "")[:4] for r in строки if r["tkp"]} - {""}):
        print(строка_свода(год, свод([r for r in строки if (r["tkp_date"] or "").startswith(год)])))

    print(f"\n{HOLDING} — по воронкам:")
    for cat in sorted({r["cat"] for r in hn}, key=lambda x: -sum(1 for r in hn if r["cat"] == x and r["tkp"])):
        с = свод([r for r in hn if r["cat"] == cat])
        if с["tkp"]:
            print(строка_свода(cats.get(cat, "воронка " + cat)[:28], с))

    # Вторая, независимая разметка Норникеля — по отделу ответственного (КАМ-группа 110).
    по_отделу = [r for r in строки if r["id"] in отдел_сделки]
    общие = {r["id"] for r in hn} & отдел_сделки
    print(f"\nПроверка разметки {HOLDING}: по названию компании {len(hn)} сделок, по отделу "
          f"ответственного {len(по_отделу)}, в обеих {len(общие)}")
    print(строка_свода(f"{HOLDING} (по отделу)", свод(по_отделу)))
    print(строка_свода(f"{HOLDING} (обе разметки)", свод([r for r in hn if r["id"] in общие])))

    откуда = collections.Counter(r["tkp_from"] for r in строки if r["tkp"])
    print("\nОткуда отметка ТКП: " + ", ".join(f"{k} {v}" for k, v in откуда.most_common())
          + " (contract — контракт без ТКП-стадии в истории: пробел данных, а не отдельный путь)")


def main() -> int:
    import config
    import kam
    from bitrix_client import BitrixClient, сводка_нагрузки

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    всего = client.count("crm.deal.list", {">=DATE_CREATE": SINCE})
    print(f"ожидается сделок с {SINCE[:10]}: {всего}; оценка нагрузки ≈ "
          f"{всего // 50 * 2 + всего // 50 * 3 + 80} запросов к порталу")
    сделки = client.list_deals_fast(filter={">=DATE_CREATE": SINCE}, select=[
        "ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE", "DATE_MODIFY",
        "COMPANY_ID", "ASSIGNED_BY_ID"])
    print(f"прочитано сделок: {len(сделки)} из {всего}")
    if len(сделки) < всего:
        print("::error::обход сделок оборвался — итог был бы неполным")
        return 1

    компании = client.companies_by_ids({str(д.get("COMPANY_ID")) for д in сделки if str(д.get("COMPANY_ID") or "0") != "0"})
    холдинг = {str(д["ID"]): kam.client_dir(компании.get(str(д.get("COMPANY_ID")), "")) if str(д.get("COMPANY_ID") or "0") != "0" else "Без клиента"
               for д in сделки}
    юрлиц = sum(1 for n in компании.values() if kam.client_dir(n) == HOLDING)
    print(f"компаний у сделок: {len(компании)}, из них в холдинге {HOLDING}: {юрлиц}")

    отдел = client.dept_member_ids(DEPT_HOLDING)
    отдел_сделки = {str(д["ID"]) for д in сделки if str(д.get("ASSIGNED_BY_ID") or "") in отдел}

    мета = client.deal_stage_meta()
    cats = client.categories()

    история: dict[str, list] = {}
    ids = [str(д["ID"]) for д in сделки]
    for i in range(0, len(ids), 50):
        часть = [int(x) for x in ids[i:i + 50]]
        start = 0
        while True:
            data = client.call_envelope("crm.stagehistory.list", {
                "entityTypeId": 2, "filter": {"OWNER_ID": часть},
                "select": ["OWNER_ID", "CREATED_TIME", "STAGE_ID"],
                "order": {"CREATED_TIME": "ASC"}, "start": start})
            res = (data or {}).get("result") or {}
            items = (res.get("items") if isinstance(res, dict) else res) or []
            for x in items:
                o, s = str(x.get("OWNER_ID")), str(x.get("STAGE_ID") or "")
                if o and s and all(s != st for st, _ in история.get(o, [])):
                    история.setdefault(o, []).append((s, str(x.get("CREATED_TIME") or "")))
            nxt = (data or {}).get("next")
            if not nxt or not items:
                break
            start = nxt
    print(f"история стадий: сделок с записями {len(история)} из {len(сделки)}")

    заказы = client.list_items(ORDER_ENTITY, filter={}, select=["id", "stageId", "createdTime", "parentId2"])
    заказы = [о for о in заказы if not str(о.get("stageId", "")).endswith(":FAIL")]
    print(f"непроигранных заказов поставщикам: {len(заказы)}")

    строки = классифицировать(сделки, история, мета, заказы, холдинг)
    отчёт(строки, отдел_сделки, мета, cats)
    print(сводка_нагрузки())
    return 0


if __name__ == "__main__":
    # Трассировка с содержимым сделки в публичный журнал не уходит: печатается
    # только тип ошибки и строка кода.
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        tb = e.__traceback__
        while tb and tb.tb_next:
            tb = tb.tb_next
        print(f"::error::зонд упал: {type(e).__name__} в строке {tb.tb_lineno if tb else '?'}")
        raise SystemExit(1)
