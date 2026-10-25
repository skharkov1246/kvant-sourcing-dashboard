#!/usr/bin/env python3
"""Зонд v58 «Сегменты реализации»: сколько открытых сделок и живых заказов вкладки
«Реализация» приходится на клиентские воронки — чтобы вынести сделки отдельного
клиентского направления в свою вкладку (решение владельца 25.10.2026: их сложности
известны, ими занимаются, ежедневного внимания руководителя реализации они не требуют).

Вселенная — как у contracts.compute: открытые сделки воронки реализации (кат. 0) и
сделки других воронок, у которых есть живой заказ СП-172. Сегмент сделки:
  • «своя воронка N» — сама сделка в клиентской воронке N;
  • «кат. 0 · клиент воронки N» — сделка реализации, компания которой есть у сделок
    клиентской воронки N (воронка пресейла — не клиентская, её компании не берутся);
  • «кат. 0 · прочие».
По сегменту: открытых сделок, живых заказов, заказов с сорванным сроком клиента
(«Date of deadline to customer» прошёл, «Customer delivery date, actual» пусто) и под
риском (≤ 14 дн., поставщик не отгрузил).

В ЖУРНАЛ — ТОЛЬКО СЧЁТЧИКИ и названия воронок (настройка портала, CLAUDE.md: «наружу —
только агрегаты … названия сегментов»): ни сделок, ни компаний. НАГРУЗКА: ≈ 60–150
запросов (воронки, заказы, сделки клиентских воронок по ключу).
"""
from __future__ import annotations

import collections
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DL_CUSTOMER = "ufCrm20_1728900218435"   # contracts.DL_CUSTOMER
DEL_FACT = "ufCrm20_1723236501"         # contracts.DEL_FACT
SHIP_FACT = "ufCrm20_1723236324"        # contracts.SHIP_FACT
BOT_FLAG = "UF_CRM_1719589079635"       # сделка: флажок клиентского бота (зонд v49)
RISK_DAYS = 14                          # lifecycle.RISK_DAYS


def _n(v) -> int | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _d(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


def main() -> int:
    import config
    import presale
    from bitrix_client import BitrixClient, сводка_нагрузки

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    сегодня = dt.date.today()
    print(f"зонд v58 · {сегодня} · сегменты вкладки «Реализация» по клиентским воронкам")

    имена = client.categories()
    пс = presale.find_category(имена)
    print(f"воронок сделок: {len(имена)}; пресейл — {('кат. ' + пс[0]) if пс else 'не найден'}")

    заказы = client.list_items(172, filter={}, select=["id", "parentId2", "stageId", DL_CUSTOMER, DEL_FACT, SHIP_FACT])
    сем = client.spa_semantics(172, заказы)
    живые = [з for з in заказы if _n(з.get("parentId2")) and
             сем.get(str(з.get("stageId")), "F" if str(з.get("stageId")).endswith(":FAIL") else "P") not in ("F", "S")]
    по_сделке: dict[int, list[dict]] = collections.defaultdict(list)
    for з in живые:
        по_сделке[int(з["parentId2"])].append(з)

    sel = ["ID", "CATEGORY_ID", "COMPANY_ID", "STAGE_SEMANTIC_ID", BOT_FLAG]
    кат0 = {int(d["ID"]): d for d in client.list_deals_fast(filter={"CATEGORY_ID": 0, "STAGE_SEMANTIC_ID": "P"}, select=sel)}
    чужие = [str(i) for i in по_сделке if i not in кат0]
    вне = {int(k): v for k, v in client.deals_by_ids(чужие, select=sel).items()} if чужие else {}
    вне = {i: d for i, d in вне.items() if str(d.get("STAGE_SEMANTIC_ID") or "P").upper() != "F"}
    print(f"открытых сделок кат. 0: {len(кат0)}; сделок других воронок с живым заказом: {len(вне)}")

    # компании клиентских воронок (все, кроме реализации и пресейла)
    клиентские = [c for c in имена if c != "0" and (not пс or c != пс[0])]
    компании: dict[str, set[int]] = {}
    for c in клиентские:
        дд = client.list_deals_fast(filter={"CATEGORY_ID": int(c)}, select=["ID", "COMPANY_ID"])
        компании[c] = {x for x in (_n(d.get("COMPANY_ID")) for d in дд) if x}

    def сегмент(d: dict) -> str:
        c = str(d.get("CATEGORY_ID") or "0")
        if c != "0":
            return f"своя воронка {c} «{имена.get(c, '?')}»"
        к = _n(d.get("COMPANY_ID"))
        за = [x for x in клиентские if к and к in компании.get(x, ())]
        if len(за) == 1:
            return f"кат. 0 · клиент воронки {за[0]} «{имена.get(за[0], '?')}»"
        if за:
            return "кат. 0 · клиент нескольких воронок"
        return "кат. 0 · прочие"

    счёт: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for i, d in {**кат0, **вне}.items():
        s = сегмент(d)
        счёт[s]["сделок"] += 1
        счёт[s]["флажок бота"] += str(d.get(BOT_FLAG) or "") in ("1", "Y", "true", "True")
        for з in по_сделке.get(i, []):
            счёт[s]["живых заказов"] += 1
            срок, факт, отгр = _d(з.get(DL_CUSTOMER)), _d(з.get(DEL_FACT)), _d(з.get(SHIP_FACT))
            if срок and not факт and срок < сегодня:
                счёт[s]["срок клиента сорван"] += 1
            elif срок and not факт and not отгр and (срок - сегодня).days <= RISK_DAYS:
                счёт[s]["под риском"] += 1
    print("\nСЕГМЕНТ: сделок · живых заказов · срок сорван · под риском · флажок бота")
    for s, k in sorted(счёт.items(), key=lambda x: -x[1]["сделок"]):
        print(f"  {s}: {k['сделок']} · {k['живых заказов']} · {k['срок клиента сорван']} · {k['под риском']} · {k['флажок бота']}")
    # пересекающийся счёт: сделка кат. 0 считается у каждой воронки, где есть её компания
    print("\nПО ВОРОНКАМ (с пересечением): компаний · сделок кат. 0 с этими компаниями · живых заказов · "
          "срок сорван · под риском")
    for c in клиентские:
        k = collections.Counter()
        for i, d in кат0.items():
            if _n(d.get("COMPANY_ID")) not in компании.get(c, ()):
                continue
            k["сделок"] += 1
            for з in по_сделке.get(i, []):
                k["заказов"] += 1
                срок, факт, отгр = _d(з.get(DL_CUSTOMER)), _d(з.get(DEL_FACT)), _d(з.get(SHIP_FACT))
                if срок and not факт and срок < сегодня:
                    k["сорван"] += 1
                elif срок and not факт and not отгр and (срок - сегодня).days <= RISK_DAYS:
                    k["риск"] += 1
        print(f"  воронка {c} «{имена.get(c, '?')}»: {len(компании.get(c, ()))} · {k['сделок']} · {k['заказов']} · "
              f"{k['сорван']} · {k['риск']}")
    всего = collections.Counter()
    for i in кат0:
        for з in по_сделке.get(i, []):
            всего["заказов"] += 1
            срок, факт = _d(з.get(DL_CUSTOMER)), _d(з.get(DEL_FACT))
            всего["сорван"] += bool(срок and not факт and срок < сегодня)
    print(f"  всего кат. 0: сделок {len(кат0)} · живых заказов {всего['заказов']} · срок сорван {всего['сорван']}")
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
