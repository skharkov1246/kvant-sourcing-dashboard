"""Зонд v37: прогон people.compute на живых данных — до мержа, а не после деплоя.

Гейт считает вкладки на придуманном корпусе: секретов Bitrix в нём нет. Поэтому
ошибки, которые видны только на живых данных (роль не распозналась по реальной
должности, поле пришло списком, стадия без справочника), доезжали бы до прода.
Здесь модуль запускается по-настоящему и печатает агрегаты результата.

ПЕЧАТАЮТСЯ ТОЛЬКО АГРЕГАТЫ: счётчики, суммы, доли, должности и названия отделов.
Ни фамилий, ни названий сделок, ни клиентов. Самое важное — в конце.
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import people as people_mod  # noqa: E402
from bitrix_client import BitrixClient  # noqa: E402


def head(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main() -> int:
    c = BitrixClient(os.environ["BITRIX_WEBHOOK_URL"])
    t0 = time.time()
    people = people_mod.roster(c)
    deps = {str(d["ID"]): d.get("NAME", "") for d in c.list_paged("department.get", {})}

    head("1. РАСПОЗНАВАНИЕ РОЛЕЙ (каскад должность → отдел → не коммерсант)")
    roles = Counter(); why = Counter(); pos_of_role = {"kam": Counter(), "prod": Counter()}
    for uid, p in people.items():
        if not p["active"]:
            continue
        r, w = people_mod.resolve_role(p, deps)
        roles[r] += 1; why[(r, w)] += 1
        if r in pos_of_role:
            pos_of_role[r][p["pos"] or "(пусто)"] += 1
    print("действующих по ролям: " + " · ".join(f"{k}={v}" for k, v in roles.most_common()))
    print("как определено: " + " · ".join(f"{r}/{w}={n}" for (r, w), n in why.most_common()))
    for r in ("kam", "prod"):
        print(f"\nдолжности роли «{r}»:")
        for p, n in pos_of_role[r].most_common(20):
            print(f"  {n:>3}  {p}")

    head("2. ПРОГОН people.compute НА ЖИВЫХ ДАННЫХ")
    t1 = time.time()
    data = people_mod.compute(c)
    print(f"посчитано за {time.time()-t1:.1f} с (всего с состава {time.time()-t0:.1f} с)")
    st, rc = data["staff"], data["recon"]
    print(f"состав: всего {st['total']} · действующих {st['active']} · отключённых {st['fired']} "
          f"· в роли КАМ {st['kam']} · в роли продукт-оунер {st['prod']}")
    print(f"портфель: открытых (без технических воронок) {rc['openTotal']} · технических отброшено {rc['tech']}")
    print(f"покрытие: за КАМами {rc['kam']} · за продукт-оунерами {rc['prod']} · и там, и там {rc['both']} "
          f"· ни за кем {rc['none']} ({rc['noneSum']}) · на уволенных {rc['orphan']} ({rc['orphanSum']}), "
          f"из них с живой ролью {rc['orphanCovered']}")

    for key in ("kam", "prod"):
        b = data["roles"][key]; t = b["totals"]
        head(f"3. РОЛЬ «{key}» — итоги")
        print(f"людей с сделками {t['peopleAll']} (действующих {t['people']}) · без единой сделки {len(b['idlePeople'])}")
        print(f"открытых {t['open']} · из них закреплено полем карточки {t['byField']} "
              f"({t['byField']*100//max(1,t['open'])}%) · роль не закреплена у {b['uncovered']['n']} ({b['uncovered']['sum']})")
        print(f"проработка {t['presale']} шт / {t['presaleSum']} · реализация {t['real']} шт / {t['realSum']} "
              f"· закупка {t['buy']} · маржа {t['margin']} ({t['marginPct']}%)")
        print(f"год: создано {t['created']} · выиграно {t['won']} ({t['wonSum']}) · проиграно {t['lost']} "
              f"· win-rate {t['winRate']}% · взвешенный пайплайн {t['weighted']}")
        print(f"риски: просрочено {t['late']} ({t['lateSum']}) · застой {t['stale']} · брошено {t['dead']} "
              f"· без суммы {t['noAmt']} · без клиента {t['noComp']} · маржа в минус {t['neg']} "
              f"· чистых карточек {t['cleanPct']}%")
        print(f"нагрузка: на человека {t['perPersonDeals']} · медиана {t['medianDeals']} · максимум {t['maxDeals']} "
              f"· денег на человека {t['perPersonSum']}")
        print("воронки: " + " · ".join(f"{f['cat']}={f['n']}" for f in b["funnels"][:8]))
        print("распределение нагрузки по людям (сделок, без имён): "
              + ", ".join(str(p["open"]) for p in sorted(b["people"], key=lambda x: -x["open"])[:15]))

    head("4. ЧТО ДОЛЖЕН УВИДЕТЬ ВЛАДЕЛЕЦ — проверка на пустоту")
    bad = []
    if not data["roles"]["kam"]["people"]:
        bad.append("во вкладке КАМов нет ни одного человека")
    if not data["roles"]["prod"]["people"]:
        bad.append("во вкладке продукт-оунеров нет ни одного человека")
    if rc["openTotal"] < 100:
        bad.append(f"открытых сделок подозрительно мало: {rc['openTotal']}")
    if data["roles"]["kam"]["totals"]["byField"] == 0:
        bad.append("поле «КАМ» нигде не прочиталось — атрибуция свалилась на владельца")
    print("ПРОБЛЕМЫ: " + ("; ".join(bad) if bad else "нет, данные для вкладок полные"))
    print("\nГОТОВО")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
