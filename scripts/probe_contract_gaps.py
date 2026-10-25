#!/usr/bin/env python3
"""Зонд v57 «Где договоры сделок»: почему у последних сделок реализации вкладка пишет
«договоры не найдены в карточках», «пары нет» и «договор не дочитан».

Конвейер чтения договоров (scripts/contract_terms.py) берёт только карточки СП-1174 и
СП-1192 со связью parentId2 = сделка и только два поля файла. Зонд ищет договоры
везде, где они могут быть, по N последним ОТКРЫТЫМ сделкам реализации (тот же отбор,
что у недельного прогона: кат. 0, STAGE_SEMANTIC_ID P, DATE_CREATE по убыванию):
  • СП-1174, СП-1192 и старые СП-132, СП-175 — по связи со сделкой (parentId2),
    с заказом СП-172 этой сделки (parentId172), со сделкой пресейла (тот же «ID чата
    по сделке» в другой воронке), без связи — по компании клиента или поставщика;
  • рамочные договоры, под которыми висят спецификации сделки (parentId175 / 1192);
  • поле сделки «Scan of the signed contract» и файлы заказов (Purchase order,
    Order confirmation, проформа);
  • место файлов 50 сделок в очереди недельного прогона (сделки — новые раньше, файлы —
    по карточкам), чтобы отличить «не дошла очередь» от «файл не читается».

В ЖУРНАЛ — ТОЛЬКО СЧЁТЧИКИ (CLAUDE.md, правило 17): ни номеров сделок и карточек, ни
компаний, ни названий файлов. НАГРУЗКА: ≈ 120–200 запросов (сделки пресейла — по
запросу на сделку с «ID чата»).
"""
from __future__ import annotations

import collections
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

N = int(os.environ.get("PROBE_LIMIT") or os.environ.get("CONTRACT_GAPS_DEALS") or "50")
ОЧЕРЕДЬ = 400                           # недельный прогон: до 400 открытых сделок
ПРОЧИТАНО_25_10 = 215                   # ok 168 + reused 47 в прогоне 25.10.2026
CHAT_FIELD = "UF_CRM_1734380921"        # «ID чата по сделке» (chat_activity.CHAT_FIELD)
SCAN_DEAL = "UF_CRM_1727110655024"      # сделка: «Scan of the signed contract»
ЗАКАЗ_ФАЙЛЫ = {
    "ufCrm20_1723385385957": "Purchase order",
    "ufCrm20_1787562161354": "Order confirmation",
    "ufCrm20_1787562201347": "проформа / счёт",
}
ТИПЫ = {1174: "СП-1174 разовый 2025", 1192: "СП-1192 рамочный 2026",
        132: "СП-132 разовый (старый)", 175: "СП-175 рамочный (старый)"}
РАМОЧНЫЕ = {"parentId175": 175, "parentId1192": 1192}
#: поля файла, которые читает конвейер (contract_terms.ПОЛЯ_ФАЙЛА)
ПОЛЯ_КОНВЕЙЕРА = {1174: ("ufCrm80_1763638685405", "ufCrm80_1774074046"),
                  1192: ("ufCrm88_1771333969727", "ufCrm88_1774084110")}


def _n(v) -> int | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def файлов(v) -> int:
    if isinstance(v, list):
        return sum(файлов(x) for x in v)
    return 1 if isinstance(v, dict) and (v.get("id") or v.get("urlMachine") or v.get("url")) else 0


def доля(a: int, b: int) -> str:
    return f"{a} из {b} ({100 * a / b:.0f} %)" if b else "0 из 0"


def пачки(xs: list, k: int = 50):
    for i in range(0, len(xs), k):
        yield xs[i:i + k]


def main() -> int:
    import config
    from bitrix_client import BitrixClient, сводка_нагрузки

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    print(f"зонд v57 · {dt.date.today()} · где договоры {N} последних открытых сделок реализации")

    # 1. сделки — как недельный прогон (новые раньше), до ОЧЕРЕДЬ
    сделки: list[dict] = []
    старт = 0
    while len(сделки) < ОЧЕРЕДЬ:
        env = client.call_envelope("crm.deal.list", {
            "filter": {"CATEGORY_ID": 0, "STAGE_SEMANTIC_ID": "P"},
            "order": {"DATE_CREATE": "DESC", "ID": "DESC"},
            "select": ["ID", "DATE_CREATE", "COMPANY_ID", CHAT_FIELD, SCAN_DEAL], "start": старт}) or {}
        часть = env.get("result") or []
        сделки += часть
        if not часть or not env.get("next"):
            break
        старт = env["next"]
    сделки = сделки[:ОЧЕРЕДЬ]
    мои = сделки[:N]
    ids = [int(d["ID"]) for d in мои]
    все_ids = [int(d["ID"]) for d in сделки]
    клиент = {int(d["ID"]): _n(d.get("COMPANY_ID")) for d in сделки}
    print(f"открытых сделок в очереди недельного прогона: {len(сделки)}; разбираются первые {len(мои)}")
    print(f"  у сделки нет компании: {sum(1 for i in ids if not клиент[i])}")

    # 2. заказы СП-172 этих сделок
    заказы: list[dict] = []
    for п in пачки(ids):
        заказы += client.list_items(172, filter={"@parentId2": п},
                                    select=["id", "parentId2", "companyId", "stageId", *ЗАКАЗ_ФАЙЛЫ])
    сем = client.spa_semantics(172, заказы)
    живые = [з for з in заказы if сем.get(str(з.get("stageId")), "F" if str(з.get("stageId")).endswith(":FAIL") else "P") != "F"]
    з_сделки = {int(з["id"]): int(з["parentId2"]) for з in живые}
    поставщики: dict[int, set[int]] = collections.defaultdict(set)
    for з in живые:
        if _n(з.get("companyId")):
            поставщики[int(з["parentId2"])].add(int(з["companyId"]))
    с_заказом = {з_сделки[z] for z in з_сделки}
    print(f"заказов СП-172: {len(заказы)}, живых {len(живые)}; сделок с живым заказом {доля(len(с_заказом), len(ids))}")
    print(f"  живой заказ без компании поставщика: {sum(1 for з in живые if not _n(з.get('companyId')))}")

    # 3. сделки пресейла с тем же «ID чата»
    пресейл: dict[int, int] = {}             # сделка пресейла → сделка реализации
    # по запросу на сделку; 25.10.2026 у 50 новейших не нашлось ни одной — на больших выборках не ищем
    с_чатом = [d for d in мои if str(d.get(CHAT_FIELD) or "").strip()] if len(мои) <= 60 else []
    for d in с_чатом:
        for x in client.call("crm.deal.list", {"filter": {CHAT_FIELD: str(d[CHAT_FIELD]).strip(), "!CATEGORY_ID": 0},
                                                "select": ["ID", "CATEGORY_ID"]}) or []:
            if _n(x.get("ID")):
                пресейл[int(x["ID"])] = int(d["ID"])
    print(f"сделок с «ID чата»: {len(с_чатом)}; найдено сделок другой воронки с тем же чатом: {len(пресейл)} "
          f"(у {len(set(пресейл.values()))} сделок реализации)")

    компании_кл = sorted({c for c in (клиент[i] for i in ids) if c})
    компании_пс = sorted({c for i in ids for c in поставщики.get(i, ())})
    срез = min(str(d.get("DATE_CREATE") or "") for d in мои)[:10]
    срез = (dt.date.fromisoformat(срез) - dt.timedelta(days=365)).isoformat() if срез else "2024-01-01"

    # 4. карточки договоров во всех местах
    #   найдено[сделка][(тип, способ)] = {карточка: (компания, файлов, рамочный)}
    найдено: dict[int, dict[tuple[int, str], dict[int, tuple]]] = collections.defaultdict(lambda: collections.defaultdict(dict))
    рамочные: dict[int, set[int]] = collections.defaultdict(set)     # тип рамочного → номера
    конвейер_файлов: dict[int, int] = collections.Counter()          # все 400 сделок — файлов конвейера
    по_полю: collections.Counter = collections.Counter()
    for тип, имя in ТИПЫ.items():
        поля = (client.call("crm.item.fields", {"entityTypeId": тип}) or {}).get("fields") or {}
        if not поля:
            print(f"{имя}: полей не прочитано — пропуск")
            continue
        ф = [к for к, m in поля.items() if m.get("type") == "file"]
        связи = [к for к in поля if к.startswith("parentId")]
        sel = ["id", "companyId", "createdTime", *связи, *ф]

        def разложить(карточки: list[dict], способ: str, к_сделке) -> None:
            for к in карточки:
                сд = к_сделке(к)
                if not сд:
                    continue
                рам = next((РАМОЧНЫЕ[r] for r in РАМОЧНЫЕ if r in связи and _n(к.get(r))), None)
                найдено[сд][(тип, способ)][int(к["id"])] = (
                    _n(к.get("companyId")), sum(файлов(к.get(x)) for x in ф), рам)
                for r, т in РАМОЧНЫЕ.items():
                    if r in связи and _n(к.get(r)):
                        рамочные[т].add(int(к[r]))
                if способ == "сделка":
                    for x in ф:
                        if файлов(к.get(x)):
                            по_полю[(тип, x, str((поля.get(x) or {}).get("title") or x)[:60])] += 1

        if "parentId2" in связи:
            for п in пачки(все_ids):
                карт = client.list_items(тип, filter={"@parentId2": п}, select=sel)
                if тип in ПОЛЯ_КОНВЕЙЕРА:
                    for к in карт:
                        сд = _n(к.get("parentId2"))
                        поле = next((x for x in ПОЛЯ_КОНВЕЙЕРА[тип] if файлов(к.get(x))), None)
                        if сд and поле:
                            конвейер_файлов[сд] += файлов(к.get(поле))
                разложить([к for к in карт if _n(к.get("parentId2")) in set(ids)], "сделка",
                          lambda к: _n(к.get("parentId2")))
            if пресейл:
                for п in пачки(list(пресейл)):
                    разложить(client.list_items(тип, filter={"@parentId2": п}, select=sel), "пресейл",
                              lambda к: пресейл.get(_n(к.get("parentId2")) or 0))
        if "parentId172" in связи and з_сделки:
            for п in пачки(list(з_сделки)):
                разложить([к for к in client.list_items(тип, filter={"@parentId172": п}, select=sel)
                           if _n(к.get("parentId2")) not in set(ids)], "заказ",
                          lambda к: з_сделки.get(_n(к.get("parentId172")) or 0))
        # без связи со сделкой — по компании клиента или поставщика, заведённые за год до сделок
        for п in пачки(sorted(set(компании_кл) | set(компании_пс))):
            карт = client.list_items(тип, filter={"@companyId": п, ">=createdTime": срез}, select=sel)
            for к in карт:
                if _n(к.get("parentId2")) or ("parentId172" in связи and _n(к.get("parentId172"))):
                    continue
                к_комп = _n(к.get("companyId"))
                for сд in ids:
                    if к_комп == клиент[сд]:
                        разложить([к], "без связи: компания клиента", lambda _к, s=сд: s)
                    elif к_комп in поставщики.get(сд, ()):
                        разложить([к], "без связи: компания поставщика", lambda _к, s=сд: s)

    # 5. рамочные: есть ли файл
    рам_файл: dict[tuple[int, int], int] = {}
    for т, номера in рамочные.items():
        поля = (client.call("crm.item.fields", {"entityTypeId": т}) or {}).get("fields") or {}
        ф = [к for к, m in поля.items() if m.get("type") == "file"]
        for п in пачки(sorted(номера)):
            for к in client.list_items(т, filter={"@id": п}, select=["id", *ф]):
                рам_файл[(т, int(к["id"]))] = sum(файлов(к.get(x)) for x in ф)

    # ---------------------------------------------------------------- итоги
    def сторона(сд: int, комп: int | None) -> str:
        if комп and комп == клиент[сд]:
            return "клиент"
        if комп and комп in поставщики.get(сд, ()):
            return "поставщик"
        return "другая"

    print(f"\nКАРТОЧКИ ДОГОВОРОВ ПО МЕСТАМ (сделок из {len(ids)} · карточек · с файлом · компания клиент/поставщик/другая)")
    места = collections.Counter()
    for сд in ids:
        for (тип, способ), кк in найдено[сд].items():
            места[(тип, способ, "сделок")] += 1
            for комп, ф, _р in кк.values():
                места[(тип, способ, "карточек")] += 1
                места[(тип, способ, "с файлом")] += bool(ф)
                места[(тип, способ, сторона(сд, комп))] += 1
    for тип in ТИПЫ:
        for способ in ("сделка", "заказ", "пресейл", "без связи: компания клиента", "без связи: компания поставщика"):
            if места[(тип, способ, "карточек")]:
                print(f"  {ТИПЫ[тип]} · {способ}: сделок {места[(тип, способ, 'сделок')]} · карточек "
                      f"{места[(тип, способ, 'карточек')]} · с файлом {места[(тип, способ, 'с файлом')]} · "
                      f"{места[(тип, способ, 'клиент')]}/{места[(тип, способ, 'поставщик')]}/{места[(тип, способ, 'другая')]}")

    print("\nПОЛЯ С ФАЙЛОМ у карточек, связанных со сделкой (карточек с файлом в поле):")
    for (тип, код, подп), k in sorted(по_полю.items(), key=lambda x: (x[0][0], -x[1])):
        метка = " ← читает конвейер" if код in ПОЛЯ_КОНВЕЙЕРА.get(тип, ()) else ""
        print(f"  СП-{тип} {код} «{подп}»: {k}{метка}")

    def видит_конвейер(сд: int) -> bool:
        return конвейер_файлов.get(сд, 0) > 0

    def есть(сд: int, способы: set[str], типы=ТИПЫ, кто: str | None = None) -> bool:
        return any(тип in типы and способ in способы and any(ф and (кто is None or сторона(сд, к) == кто)
                                                           for к, ф, _р in кк.values())
                   for (тип, способ), кк in найдено[сд].items())

    скан = {int(d["ID"]) for d in мои if файлов(d.get(SCAN_DEAL))}
    по_файлам_заказа = collections.Counter()
    for з in живые:
        for код, подп in ЗАКАЗ_ФАЙЛЫ.items():
            по_файлам_заказа[подп] += bool(файлов(з.get(код)))
    print(f"\nСДЕЛКА: «Scan of the signed contract» с файлом — {доля(len(скан), len(ids))}")
    print("ЖИВЫЕ ЗАКАЗЫ: " + " · ".join(f"{k} {доля(v, len(живые))}" for k, v in по_файлам_заказа.items()))

    видит = {сд for сд in ids if видит_конвейер(сд)}
    print(f"\nКОНВЕЙЕР ВИДИТ (СП-1174/1192, связь со сделкой, файл в своём поле): {доля(len(видит), len(ids))}")
    нет = [сд for сд in ids if сд not in видит]
    if нет:
        причины = collections.Counter()
        for сд in нет:
            if есть(сд, {"заказ"}, {1174, 1192}):
                причины["в СП-1174/1192, связь только с заказом"] += 1
            elif есть(сд, {"сделка", "заказ"}, {132, 175}):
                причины["в старых СП-132/175"] += 1
            elif есть(сд, {"пресейл"}):
                причины["у сделки пресейла"] += 1
            elif есть(сд, {"без связи: компания клиента", "без связи: компания поставщика"}):
                причины["без связи, по компании"] += 1
            elif сд in скан:
                причины["только скан в поле сделки"] += 1
            elif сд not in с_заказом:
                причины["договора нет нигде, заказа ещё нет"] += 1
            else:
                причины["договора нет нигде, заказ есть"] += 1
        print(f"  не видит {len(нет)} — где договор на самом деле (первое найденное):")
        for k, v in причины.most_common():
            print(f"    {k}: {v}")

    # сторона клиента: где договор с компанией клиента
    кл_видит = {сд for сд in ids if есть(сд, {"сделка"}, {1174, 1192}, "клиент")}
    кл_где = collections.Counter()
    for сд in ids:
        if сд in кл_видит:
            кл_где["конвейер: карточка сделки с компанией клиента"] += 1
        elif есть(сд, {"сделка"}, {1174, 1192}, "другая"):
            кл_где["конвейер: карточка сделки с иной компанией (сторону скажет модель)"] += 1
        elif есть(сд, {"заказ"}, кто="клиент"):
            кл_где["карточка с компанией клиента, связь только с заказом"] += 1
        elif есть(сд, {"сделка"}, {132, 175}, "клиент"):
            кл_где["старые СП-132/175"] += 1
        elif есть(сд, {"пресейл"}, кто="клиент"):
            кл_где["у сделки пресейла"] += 1
        elif есть(сд, {"без связи: компания клиента"}):
            кл_где["без связи со сделкой, компания клиента"] += 1
        elif сд in скан:
            кл_где["только скан в поле сделки"] += 1
        else:
            кл_где["нигде"] += 1
    print(f"\nДОГОВОР С ЗАКАЗЧИКОМ (по компании карточки) — {len(ids)} сделок:")
    for k, v in кл_где.most_common():
        print(f"  {k}: {v}")

    # сторона поставщика: по живым заказам
    пс_где = collections.Counter()
    for з in живые:
        сд, комп = int(з["parentId2"]), _n(з.get("companyId"))

        def карточка_заказа(способы, типы=ТИПЫ) -> bool:
            for (тип, способ), кк in найдено[сд].items():
                if тип in типы and способ in способы:
                    if any(ф and комп and к == комп for к, ф, _р in кк.values()):
                        return True
            return False
        if карточка_заказа({"сделка"}, {1174, 1192}):
            пс_где["конвейер: карточка сделки с компанией поставщика заказа"] += 1
        elif карточка_заказа({"заказ"}, {1174, 1192}):
            пс_где["СП-1174/1192, связь только с заказом"] += 1
        elif карточка_заказа({"сделка", "заказ"}, {132, 175}):
            пс_где["старые СП-132/175"] += 1
        elif карточка_заказа({"без связи: компания поставщика"}):
            пс_где["без связи, компания поставщика"] += 1
        elif any(файлов(з.get(к)) for к in ("ufCrm20_1723385385957", "ufCrm20_1787562161354")):
            пс_где["договора нет, есть Purchase order / Order confirmation в заказе"] += 1
        else:
            пс_где["нигде"] += 1
    print(f"\nДОГОВОР С ПОСТАВЩИКОМ (по компании заказа) — {len(живые)} живых заказов:")
    for k, v in пс_где.most_common():
        print(f"  {k}: {v}")

    # рамочные
    спец_рам = collections.Counter()
    for сд in ids:
        for (тип, способ), кк in найдено[сд].items():
            if способ != "сделка":
                continue
            for _к, ф, рам in кк.values():
                if рам:
                    спец_рам[рам] += 1
    print("\nРАМОЧНЫЕ: карточек сделки под рамочным — " + (" · ".join(f"СП-{т} {v}" for т, v in спец_рам.items()) or "нет")
          + f"; рамочных {len(рам_файл)}, с файлом {sum(1 for v in рам_файл.values() if v)}; конвейер читает рамочный, "
          "только если он сам связан со сделкой")

    # очередь недельного прогона
    поз, конец = 0, 0
    for сд in все_ids:
        поз += конвейер_файлов.get(сд, 0)
        if сд == ids[-1]:
            конец = поз
    print(f"\nОЧЕРЕДЬ НЕДЕЛЬНОГО ПРОГОНА: файлов у {len(сделки)} сделок {поз}; у {len(ids)} новейших — {конец} "
          f"(позиции 1…{конец}); 25.10 прочитано или взято из кэша {ПРОЧИТАНО_25_10} первых")
    за_чертой = sum(1 for i, сд in enumerate(ids) if sum(конвейер_файлов.get(x, 0) for x in ids[:i + 1]) > ПРОЧИТАНО_25_10)
    print(f"  из {len(ids)} новейших сделок с файлами за чертой {ПРОЧИТАНО_25_10}: {за_чертой}")
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
