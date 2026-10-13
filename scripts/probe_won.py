#!/usr/bin/env python3
"""Зонд v51 «Выиграна, но не переведена в реализацию».

Вопрос владельца 13.10.2026: «есть такое понятие — выигранная сделка, но ещё не
переведённая в реализацию? И сколько таких мы считаем?»

КАК ЭТО СЧИТАЕТ ДАШБОРД (на 13.10.2026). Победа = сделка в воронке 0 «Реализация»,
либо есть заказ поставщику СП-172, либо номер реализации в названии (people.py,
kam.py, reps.py). Стадия с семантикой успеха в предпродажной воронке победой НЕ
считается: такая сделка на вкладке КАМов лежит в «ранних» (воронке продаж), а в
«результате года» — ни в выигранных, ни в проигранных. Отдельного состояния нет.

ЧТО ИЩЕМ — сделки предпродажных воронок (не 0 и не технические), у которых сейчас:
  S  — стадия с семантикой успеха («Сделка выиграна» и т. п.);
  W  — рабочая стадия, имя которой говорит о победе (ПОБЕДА ниже: выиграли,
       победитель, финальная спецификация, контракт или договор подписан, PO);
и раскладываем их:
  A — нет живого заказа и нет номера реализации: выиграна и нигде не исполняется;
  B — номер реализации в названии есть, заказа нет;
  C — живой заказ поставщику есть, а сделка в реализацию не переведена;
  ДВОЙНИК — у компании есть карточка воронки 0, заведённая за 30 дней до победы …
       120 дней после: вероятно, реализацию завели отдельной карточкой.
Рядом — сделки воронки 0 в первой стадии «ТКП согласован (конкурс выигран)»:
выиграли, но договор ещё не подписан; и обратная сторона — сделки с живым заказом
поставщику, которые стоят не в воронке 0 (исполняются, но не переведены).

Дата победы — MOVED_TIME (последний переход стадии): для сделки, стоящей в стадии
победы, это и есть вход в неё. История стадий не читается.

ЧТО В ЖУРНАЛ: счётчики, возраст корзинами, доли; имена стадий — через
probe_conversion.обезличить. Ни сумм, ни названий сделок и компаний, ни имён.
НАГРУЗКА: ≈ 70 запросов (сделки по фильтру стадий, воронка 0, заказы, компании, сделки заказов).
"""
from __future__ import annotations

import collections
import datetime as dt
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _модуль(имя, файл):
    spec = importlib.util.spec_from_file_location(имя, ROOT / "scripts" / файл)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


p26 = _модуль("kvant_probe_2526", "probe_2526.py")
pc = p26.pc

ПОБЕДА = re.compile(r"выигр|победи|финальн\w*\s+специфик|(контракт|договор)\w*\s+подписан|"
                    r"\bPO\b|purchase\s+order|заказ\w*\s+получен", re.I)
НЕ_ПОБЕДА = re.compile(r"не\s*выигр|проигр|победил\w*\s+друг|не\s*прошл", re.I)
ПЕРВАЯ_РЕАЛИЗАЦИИ = re.compile(r"конкурс\s+выигран|ТКП\s+согласован", re.I)
ДВОЙНИК_ДО, ДВОЙНИК_ПОСЛЕ = 30, 120
КОРЗИНЫ = ((30, "до 30 дн."), (90, "31–90 дн."), (180, "91–180 дн."), (365, "181–365 дн."), (10 ** 6, "больше года"))


def стадии_победы(мета, техн):
    """{stage_id: 'S'|'W'} предпродажных воронок. Чистая функция."""
    out = {}
    for sid, m in мета.items():
        cat = str(m.get("cat") or pc.воронка_стадии(sid))
        if cat == "0" or cat in техн:
            continue
        имя, sem = str(m.get("name") or ""), str(m.get("sem") or "").upper()
        if sem == "S":
            out[sid] = "S"
        elif sem != "F" and ПОБЕДА.search(имя) and not НЕ_ПОБЕДА.search(имя):
            out[sid] = "W"
    return out


def корзина(дней):
    for порог, имя in КОРЗИНЫ:
        if дней <= порог:
            return имя
    return КОРЗИНЫ[-1][1]


def разложить(сделки, вид_стадии, живые_заказы, карточки0, сегодня, холдинг):
    """Кандидаты → строки {id, kind S|W, group A|B|C, twin, age, year, nn, stage}. Чистая функция.

    карточки0 — [(company, дата создания)] сделок воронки 0."""
    по_компании = collections.defaultdict(list)
    for c, d in карточки0:
        if c and c != "0" and d:
            по_компании[c].append(d)
    out = []
    for д in сделки:
        sid = str(д.get("STAGE_ID") or "")
        if sid not in вид_стадии:
            continue
        did = str(д["ID"])
        if did in живые_заказы:
            группа = "C"
        elif pc.номер_реализации(д.get("TITLE")) > 0:
            группа = "B"
        else:
            группа = "A"
        победа = pc.дата_мск(д.get("MOVED_TIME")) or pc.дата_мск(д.get("DATE_CREATE"))
        дата = dt.date.fromisoformat(победа) if победа else None
        c = str(д.get("COMPANY_ID") or "0")
        двойник = bool(дата) and any(-ДВОЙНИК_ДО <= (dt.date.fromisoformat(x) - дата).days <= ДВОЙНИК_ПОСЛЕ
                                      for x in по_компании.get(c, []))
        out.append({"id": did, "kind": вид_стадии[sid], "group": группа, "twin": двойник,
                    "age": (сегодня - дата).days if дата else None, "year": победа[:4] if победа else "?",
                    "nn": холдинг.get(did) == pc.HOLDING, "stage": sid,
                    "amount": bool(float(д.get("OPPORTUNITY") or 0) > 0)})
    return out


def свод(строки):
    """Строки → счётчики для журнала. Чистая функция."""
    def счёт(xs):
        return {"n": len(xs), "twin": sum(r["twin"] for r in xs), "nn": sum(r["nn"] for r in xs),
                "amount": sum(r["amount"] for r in xs),
                "age": collections.Counter(корзина(r["age"]) for r in xs if r["age"] is not None),
                "year": collections.Counter(r["year"] for r in xs)}
    return {(k, g): счёт([r for r in строки if r["kind"] == k and r["group"] == g])
            for k in ("S", "W") for g in ("A", "B", "C")}


def main() -> int:
    import config
    import kam
    import people
    from bitrix_client import BitrixClient, сводка_нагрузки

    техн = frozenset(people.TECH_CATS)
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    сегодня = dt.datetime.now(pc.MSK).date()
    мета = client.deal_stage_meta()
    cats = client.categories()
    лексика = pc.общая_лексика(мета)
    вид = стадии_победы(мета, техн)
    print(f"зонд v51 · {сегодня} · стадий победы в предпродажных воронках: S {sum(1 for v in вид.values() if v == 'S')}, "
          f"W (рабочая стадия с именем победы) {sum(1 for v in вид.values() if v == 'W')}")
    for sid, v in sorted(вид.items(), key=lambda x: (x[1], pc.воронка_стадии(x[0]))):
        print(f"  {v} {sid}: {pc.обезличить((мета.get(sid) or {}).get('name'), лексика)} · "
              f"{pc.имя_воронки(pc.воронка_стадии(sid), cats, лексика)}")

    select = ["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE", "MOVED_TIME",
              "COMPANY_ID", "OPPORTUNITY"]
    сделки = client.list_deals_fast(filter={"STAGE_ID": sorted(вид)}, select=select) if вид else []
    env = client.call_envelope("crm.deal.list", {"filter": {"STAGE_ID": sorted(вид)}, "select": ["ID"], "start": 0}) or {}
    print(f"сделок в стадиях победы: {len(сделки)} из {int(env.get('total') or 0)}")
    if len(сделки) < int(env.get("total") or 0):
        print("::error::выгрузка оборвалась")
        return 1

    сырые = client.list_items(p26.ORDER_ENTITY, filter={}, select=["id", "stageId", "categoryId", "parentId2"])
    семантика, _ = p26.семантика_заказов(client, {str(о.get("categoryId")) for о in сырые if о.get("categoryId")})
    живые = {str(о.get("parentId2")) for о in сырые
             if str(о.get("parentId2") or "0") != "0" and p26.сем_заказа(о.get("stageId"), семантика) != "F"}
    нулевые = client.list_deals_fast(filter={"CATEGORY_ID": 0}, select=["ID", "COMPANY_ID", "DATE_CREATE", "STAGE_ID", "MOVED_TIME",
                                                                     "STAGE_SEMANTIC_ID"])
    карточки0 = [(str(д.get("COMPANY_ID") or "0"), pc.дата_мск(д.get("DATE_CREATE"))) for д in нулевые]
    print(f"заказов поставщикам {len(сырые)}, сделок с живым заказом {len(живые)}; сделок воронки 0 всего {len(нулевые)}")

    нужны = {str(д.get("COMPANY_ID")) for д in сделки if str(д.get("COMPANY_ID") or "0") != "0"}
    компании = client.companies_by_ids(нужны)
    известные = {имя for _, имя in kam.CLIENT_HOLDINGS}

    def холдинг_компании(cid):
        имя = компании.get(cid, "")
        h = kam.client_dir(имя)
        return pc.HOLDING if h not in известные and pc.ГРУППА_ШИРЕ.search(имя) else h
    холдинг = {str(д["ID"]): холдинг_компании(str(д.get("COMPANY_ID") or "0")) for д in сделки}

    строки = разложить(сделки, вид, живые, карточки0, сегодня, холдинг)
    с = свод(строки)
    подпись = {"A": "A — нет заказа и номера реализации", "B": "B — номер реализации есть, заказа нет",
               "C": "C — живой заказ есть, сделка не переведена"}
    for k, имя in (("S", "СТАДИЯ УСПЕХА (семантика S)"), ("W", "РАБОЧАЯ СТАДИЯ С ИМЕНЕМ ПОБЕДЫ")):
        print(f"\n{имя}")
        for g in ("A", "B", "C"):
            x = с[(k, g)]
            print(f"  {подпись[g]}: {x['n']} · Норникель {x['nn']} · сумма заполнена {x['amount']} · "
                  f"вероятный двойник в воронке 0: {x['twin']}")
            if x["n"]:
                print("    возраст с победы: " + "; ".join(f"{b} {x['age'][b]}" for _, b in КОРЗИНЫ if x["age"][b]))
                print("    год победы: " + "; ".join(f"{y} {v}" for y, v in sorted(x["year"].items())))
    по_стадиям = collections.Counter((r["stage"], r["group"]) for r in строки)
    print("\nПО СТАДИЯМ (стадия · группа: сделок)")
    for (sid, g), v in sorted(по_стадиям.items(), key=lambda x: -x[1]):
        print(f"  {sid} · {g}: {v}")

    # Обратная сторона: сделки с живым заказом поставщику, которые не в воронке 0.
    по_заказам = client.deals_by_ids(sorted(живые, key=int), select=["ID", "TITLE", "CATEGORY_ID", "STAGE_ID",
                                                                    "STAGE_SEMANTIC_ID", "MOVED_TIME"])
    вне = [д for д in по_заказам.values() if str(д.get("CATEGORY_ID") or "0") != "0"]
    print(f"\nЖИВОЙ ЗАКАЗ ЕСТЬ, СДЕЛКА НЕ В ВОРОНКЕ 0: {len(вне)} из {len(по_заказам)} сделок с живым заказом")
    for sem, имя in (("P", "в работе (предпродажа)"), ("S", "успех"), ("F", "проиграна")):
        xs = [д for д in вне if str(д.get("STAGE_SEMANTIC_ID") or "").upper() == sem]
        if not xs:
            continue
        возр = collections.Counter(корзина((сегодня - dt.date.fromisoformat(pc.дата_мск(д.get("MOVED_TIME")))).days)
                                   for д in xs if pc.дата_мск(д.get("MOVED_TIME")))
        print(f"  {имя}: {len(xs)} · с номером реализации {sum(1 for д in xs if pc.номер_реализации(д.get('TITLE')) > 0)} · "
              f"технических воронок {sum(1 for д in xs if str(д.get('CATEGORY_ID')) in техн)} · с последнего перехода: "
              + "; ".join(f"{b} {возр[b]}" for _, b in КОРЗИНЫ if возр[b]))
        for (sid,), v in collections.Counter((str(д.get("STAGE_ID")),) for д in xs).most_common(8):
            print(f"    {sid}: {pc.обезличить((мета.get(sid) or {}).get('name'), лексика)} — {v}")

    первые = [д for д in нулевые if ПЕРВАЯ_РЕАЛИЗАЦИИ.search(str((мета.get(str(д.get("STAGE_ID"))) or {}).get("name") or ""))
              and str(д.get("STAGE_SEMANTIC_ID") or "").upper() == "P"]
    возр = collections.Counter(корзина((сегодня - dt.date.fromisoformat(pc.дата_мск(д.get("MOVED_TIME")))).days)
                               for д in первые if pc.дата_мск(д.get("MOVED_TIME")))
    print(f"\nВОРОНКА 0, СТАДИЯ «КОНКУРС ВЫИГРАН / ТКП СОГЛАСОВАН» (договор не подписан): {len(первые)} · "
          f"с живым заказом {sum(1 for д in первые if str(д['ID']) in живые)} · возраст в стадии: "
          + "; ".join(f"{b} {возр[b]}" for _, b in КОРЗИНЫ if возр[b]))
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
