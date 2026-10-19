#!/usr/bin/env python3
"""Зонд v53 «Реализация как процесс»: от выигрыша до гарантии — что портал знает
о каждой контрольной точке. Замер перед переработкой вкладки «Реализация» в
инструмент руководителя реализации.

ЧТО МЕРЯЕМ (только счётчики, доли и медианы; ни имён, ни компаний, ни сумм):
  • воронка сделок «Реализация» (кат. 0): открытые по стадиям, возраст в стадии,
    заполненность «Ответственный за реализацию» и «Сопровождение сделки», нагрузка
    на сопровождающего (распределение без имён); сделки, у которых все заказы
    закрыты успехом, а сделка открыта; успехов за год;
  • заказы поставщикам СП-172: воронки и стадии, открытые по стадиям и возрасту;
    у открытых — есть ли срок клиента, бюджетные и плановые даты; просрочка срока
    клиента, плановая отгрузка поставщика прошла без факта, плановая доставка
    позже срока клиента; оплата поставщику < 100 % после стадии оплаты;
  • доставленные за 365 дней: в срок клиента или нет, задержка, сквозные сроки
    (заказ → отгрузка поставщика → приход на склад → доставка клиенту), сдвиг
    факта от плана; по схеме поставки; закрывающие: УПД, платёж клиента;
  • гарантия и рекламации: смарт-процессы и поля портала с такими подписями.
НАГРУЗКА: ≈ 250 запросов (сделки, заказы, история стадий заказов за 365 дней,
справочники).
"""
from __future__ import annotations

import collections
import datetime as dt
import os
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DAYS = int(os.environ.get("REAL_DAYS", "365"))
ORDERS = 172
CAT_REAL = "0"

# Поля сделки (зонд v49).
Д_ОТВ_РЕАЛ = "UF_CRM_1781858627"        # Ответственный за реализацию
Д_СОПР = "UF_CRM_1715604558"            # Сопровождение сделки (множественное)
Д_ВЕХИ = {                              # даты вех, ставит робот при входе в стадию
    "выигран": "UF_CRM_1720337258485",
    "договор с заказчиком": "UF_CRM_1720337271345",
    "бюджет": "UF_CRM_1720337279368",
    "договор с поставщиком": "UF_CRM_1720337289483",
    "оплата поставщику": "UF_CRM_1720337304531",
    "подтверждение": "UF_CRM_1720337312884",
    "произведён": "UF_CRM_1720337321894",
    "отгружен": "UF_CRM_1720337330743",
    "доставлен": "UF_CRM_1720337338894",
    "закрыта": "UF_CRM_1720337346586",
}
Д_ОТСРОЧКА = "UF_CRM_1727962588"        # Количество дней отсрочки платежа

# Поля заказа СП-172 (зонд v49).
З = {
    "сопр": "ufCrm20_1723236618",           # Deal support specialist
    "срок": "ufCrm20_1728900218435",        # Date of deadline to customer
    "пр_нач_бюдж": "ufCrm20_1724941908",    # Production start date, budget
    "пр_кон_бюдж": "ufCrm20_1724941935",    # Production end date, budget
    "дост_бюдж": "ufCrm20_1724942005",      # Customer delivery date, budget
    "отгр_план": "ufCrm20_1723235828",      # Supplier Shipment Date, planned
    "отгр_факт": "ufCrm20_1723236324",      # Supplier Shipment Date, actual
    "склад_план": "ufCrm20_1709294315471",  # Inbound Delivery Date, planned
    "склад_факт": "ufCrm20_1723236306",     # Inbound Delivery Date, actual
    "дост_план": "ufCrm20_1723236261",      # Customer delivery date, planned
    "дост_факт": "ufCrm20_1723236501",      # Customer delivery date, actual
    "упд_дата": "ufCrm20_1723236848",       # Date of signing of the UTD
    "упд_файл": "ufCrm20_1749654314976",    # Universal Transfer Document (UTD)
    "оплата_план": "ufCrm20_1723236853",    # Planned payment term
    "пп_клиента": "ufCrm20_1751555578101",  # Payment Order (client)
    "оплачено_пост": "ufCrm20_1755759056275",  # Оплачено поставщику, %
    "подтв_файл": "ufCrm20_1723236426",     # Production confirmation
    "подтв_флаг": "ufCrm20_1723372033545",  # Production confirmation, no file
    "схема": "ufCrm20_1724941500",          # Схема поставки
    "направление": "ufCrm20_1769424743906",  # Направление поставки
}
ПОЛЯ_ГАРАНТИИ = re.compile(r"гарант|warrant|рекламац|претенз|claim|возврат|сервис|service|акт\b|паспорт", re.I)
КОРЗИНЫ = ((7, "до 7 дн."), (30, "8–30 дн."), (90, "31–90 дн."), (180, "91–180 дн."), (10 ** 6, "больше 180 дн."))


def дата(v) -> dt.date | None:
    s = str(v or "")[:10]
    try:
        return dt.date.fromisoformat(s) if re.match(r"\d{4}-\d\d-\d\d$", s) else None
    except ValueError:
        return None


def есть(v) -> bool:
    if isinstance(v, list):
        return any(v)
    return v not in (None, "", 0, "0", False, "N")


def корзина(дн: int) -> str:
    for порог, имя in КОРЗИНЫ:
        if дн <= порог:
            return имя
    return КОРЗИНЫ[-1][1]


def медиана(xs) -> str:
    xs = [x for x in xs if x is not None]
    if not xs:
        return "—"
    q = statistics.quantiles(xs, n=4) if len(xs) > 1 else [xs[0]] * 3
    return f"медиана {statistics.median(xs):.0f}, 75 % {q[2]:.0f}, n={len(xs)}"


def доля(a: int, b: int) -> str:
    return f"{a} из {b} ({100 * a / b:.0f} %)" if b else f"{a} из 0"


def разбор_открытых(заказы: list[dict], закрытые: set, сегодня: dt.date, после_оплаты: set) -> collections.Counter:
    """Открытые заказы → счётчики контрольных точек. Чистая функция."""
    c: collections.Counter = collections.Counter()
    for z in заказы:
        if str(z.get("stageId")) in закрытые:
            continue
        c["открыто"] += 1
        срок = дата(z.get(З["срок"]))
        факт = дата(z.get(З["дост_факт"]))
        отгр_план, отгр_факт = дата(z.get(З["отгр_план"])), дата(z.get(З["отгр_факт"]))
        дост_план, дост_бюдж = дата(z.get(З["дост_план"])), дата(z.get(З["дост_бюдж"]))
        for k in ("срок", "дост_бюдж", "отгр_план", "дост_план"):
            c[f"есть {k}"] += bool(дата(z.get(З[k])))
        if срок and not факт:
            if срок < сегодня:
                c["срок клиента прошёл, не доставлен"] += 1
                c[f"просрочка {корзина((сегодня - срок).days)}"] += 1
            elif (срок - сегодня).days <= 14 and not отгр_факт:
                c["до срока ≤ 14 дн., поставщик не отгрузил"] += 1
        if отгр_план and not отгр_факт and отгр_план < сегодня:
            c["плановая отгрузка поставщика прошла без факта"] += 1
        прогноз = дост_план or дост_бюдж
        if срок and прогноз and прогноз > срок and not факт:
            c["план доставки позже срока клиента"] += 1
        if str(z.get("stageId")) in после_оплаты:
            try:
                опл = float(z.get(З["оплачено_пост"]) or 0)
            except (TypeError, ValueError):
                опл = 0.0
            if опл < 100:
                c["после стадии оплаты, оплачено поставщику < 100 %"] += 1
        if not срок:
            c["нет срока клиента"] += 1
    return c


def разбор_доставленных(заказы: list[dict], сегодня: dt.date, с: dt.date) -> dict:
    """Заказы с фактом доставки клиенту в окне → сроки, срыв, закрывающие. Чистая функция."""
    out = {"n": 0, "в срок": 0, "со сроком": 0, "задержка": [], "сквозной": [], "заказ→отгрузка": [],
           "отгрузка→склад": [], "склад→клиент": [], "сдвиг отгрузки": [], "сдвиг доставки к бюджету": [],
           "без УПД": 0, "без УПД > 14 дн.": 0, "без п/п клиента > 30 дн.": 0, "срок оплаты прошёл, п/п нет": 0,
           "по схеме": collections.defaultdict(lambda: [0, 0])}
    for z in заказы:
        факт = дата(z.get(З["дост_факт"]))
        if not факт or факт < с or факт > сегодня:
            continue
        out["n"] += 1
        создан = дата(z.get("createdTime"))
        срок = дата(z.get(З["срок"]))
        отгр_ф, склад_ф = дата(z.get(З["отгр_факт"])), дата(z.get(З["склад_факт"]))
        if срок:
            out["со сроком"] += 1
            out["в срок"] += факт <= срок
            if факт > срок:
                out["задержка"].append((факт - срок).days)
            сх = out["по схеме"][str(z.get(З["схема"]) or "—")]
            сх[0] += 1
            сх[1] += факт <= срок
        if создан:
            out["сквозной"].append((факт - создан).days)
            if отгр_ф and отгр_ф >= создан:
                out["заказ→отгрузка"].append((отгр_ф - создан).days)
        if отгр_ф and склад_ф and склад_ф >= отгр_ф:
            out["отгрузка→склад"].append((склад_ф - отгр_ф).days)
        if склад_ф and факт >= склад_ф:
            out["склад→клиент"].append((факт - склад_ф).days)
        отгр_п = дата(z.get(З["отгр_план"]))
        if отгр_п and отгр_ф:
            out["сдвиг отгрузки"].append((отгр_ф - отгр_п).days)
        бюдж = дата(z.get(З["дост_бюдж"]))
        if бюдж:
            out["сдвиг доставки к бюджету"].append((факт - бюдж).days)
        упд = дата(z.get(З["упд_дата"])) or есть(z.get(З["упд_файл"]))
        if not упд:
            out["без УПД"] += 1
            out["без УПД > 14 дн."] += (сегодня - факт).days > 14
        if not есть(z.get(З["пп_клиента"])):
            out["без п/п клиента > 30 дн."] += (сегодня - факт).days > 30
            оп = дата(z.get(З["оплата_план"]))
            out["срок оплаты прошёл, п/п нет"] += bool(оп and оп < сегодня)
    return out


def main() -> int:
    import config
    from bitrix_client import BitrixClient, сводка_нагрузки

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    сегодня = dt.date.today()
    с = сегодня - dt.timedelta(days=DAYS)
    print(f"зонд v53 · {сегодня} · реализация: окно {DAYS} дн.")

    # ── Сделки «Реализация»
    мета = client.deal_stage_meta()
    стадии0 = [(k, m) for k, m in мета.items() if m["cat"] == CAT_REAL]
    print(f"\nВОРОНКА СДЕЛОК «Реализация» (кат. {CAT_REAL}): стадий {len(стадии0)}")
    sel = ["ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "MOVED_TIME", "DATE_CREATE", "CLOSEDATE", "ASSIGNED_BY_ID",
           Д_ОТВ_РЕАЛ, Д_СОПР, Д_ОТСРОЧКА, *Д_ВЕХИ.values()]
    открытые = client.list_deals_fast(filter={"CATEGORY_ID": 0, "STAGE_SEMANTIC_ID": "P"}, select=sel)
    закрытые = [d for d in client.list_deals_fast(filter={"CATEGORY_ID": 0, "!STAGE_SEMANTIC_ID": "P",
                                                          ">=DATE_MODIFY": с.isoformat()}, select=sel)
                if (дата(d.get("MOVED_TIME")) or с) >= с]
    по_ст = collections.Counter(str(d.get("STAGE_ID")) for d in открытые)
    возраст = collections.defaultdict(list)
    for d in открытые:
        m = дата(d.get("MOVED_TIME"))
        if m:
            возраст[str(d.get("STAGE_ID"))].append((сегодня - m).days)
    print("  стадия | семантика | открыто | возраст в стадии, дн.")
    for k, m in стадии0:
        print(f"  {m['name']} | {m['sem']} | {по_ст.get(k, 0)} | {медиана(возраст.get(k, []))}")
    сем = collections.Counter(str(d.get("STAGE_SEMANTIC_ID")) for d in закрытые)
    print(f"  открыто всего: {len(открытые)} · закрыто за {DAYS} дн.: успехом {сем.get('S', 0)}, отказом {сем.get('F', 0)}")
    отв = sum(1 for d in открытые if есть(d.get(Д_ОТВ_РЕАЛ)))
    сопр = collections.Counter()
    for d in открытые:
        for u in (d.get(Д_СОПР) or []) if isinstance(d.get(Д_СОПР), list) else [d.get(Д_СОПР)]:
            if u:
                сопр[str(u)] += 1
    нагр = sorted(сопр.values(), reverse=True)
    print(f"  «Ответственный за реализацию» заполнен: {доля(отв, len(открытые))} · «Сопровождение сделки»: "
          f"{доля(sum(1 for d in открытые if есть(d.get(Д_СОПР))), len(открытые))}")
    print(f"  сопровождающих у открытых сделок: {len(нагр)} · сделок на человека: "
          + (", ".join(str(x) for x in нагр[:12]) if нагр else "—"))
    print(f"  отсрочка платежа заказчика заполнена: {доля(sum(1 for d in открытые if есть(d.get(Д_ОТСРОЧКА))), len(открытые))}")
    for имя, поле in Д_ВЕХИ.items():
        n = sum(1 for d in открытые if дата(d.get(поле)))
        print(f"  веха «{имя}» с датой у открытых: {доля(n, len(открытые))}")

    # ── Заказы СП-172
    кат = (client.call("crm.category.list", {"entityTypeId": ORDERS}) or {}).get("categories") or []
    print(f"\nЗАКАЗЫ ПОСТАВЩИКАМ СП-{ORDERS}: воронок {len(кат)}")
    стадии_з: dict[str, tuple[str, str, int, str]] = {}
    for k in кат:
        for s in client.list_paged("crm.status.list", {"filter": {"ENTITY_ID": f"DYNAMIC_{ORDERS}_STAGE_{k['id']}"},
                                                       "order": {"SORT": "ASC"}}):
            стадии_з[str(s["STATUS_ID"])] = (str(s.get("NAME") or ""), str(s.get("SEMANTICS") or "P"),
                                            int(s.get("SORT") or 0), str(k["id"]))
    поля_з = (client.call("crm.item.fields", {"entityTypeId": ORDERS}) or {}).get("fields") or {}
    sel_z = ["id", "categoryId", "stageId", "movedTime", "createdTime", "parentId2", "assignedById", *З.values()]
    заказы = client.list_items(ORDERS, filter={">=createdTime": (сегодня - dt.timedelta(days=DAYS + 365)).isoformat()},
                               select=sel_z)
    старые_откр = client.list_items(ORDERS, filter={"<createdTime": (сегодня - dt.timedelta(days=DAYS + 365)).isoformat(),
                                                    "@stageId": [k for k, v in стадии_з.items() if v[1] == "P"]},
                                    select=sel_z) if стадии_з else []
    заказы += старые_откр
    закр = {k for k, v in стадии_з.items() if v[1] in ("S", "F")}
    откр = [z for z in заказы if str(z.get("stageId")) not in закр]
    print(f"  заказов в выгрузке: {len(заказы)} (созданы за {DAYS + 365} дн. + старые открытые {len(старые_откр)}) · открыто: {len(откр)}")
    по_ст_з = collections.Counter(str(z.get("stageId")) for z in откр)
    возр_з = collections.defaultdict(list)
    for z in откр:
        m = дата(z.get("movedTime"))
        if m:
            возр_з[str(z.get("stageId"))].append((сегодня - m).days)
    for kid in sorted({v[3] for v in стадии_з.values()}, key=int):
        имя_к = next((k["name"] for k in кат if str(k["id"]) == kid), kid)
        print(f"  воронка «{имя_к}»: стадия | семантика | открыто | возраст в стадии, дн.")
        for sid, (имя, sm, _, k) in sorted(стадии_з.items(), key=lambda x: x[1][2]):
            if k == kid:
                print(f"    {имя} | {sm} | {по_ст_з.get(sid, 0)} | {медиана(возр_з.get(sid, []))}")
    # стадии «после оплаты поставщику» — по имени: всё, что в порядке воронки после стадии с «оплат»
    после_оплаты: set = set()
    for kid in {v[3] for v in стадии_з.values()}:
        ряд = sorted([(v[2], sid, v[0]) for sid, v in стадии_з.items() if v[3] == kid and v[1] == "P"])
        idx = next((i for i, (_, _, имя) in enumerate(ряд) if re.search(r"оплат", имя, re.I)), None)
        if idx is not None:
            после_оплаты |= {sid for _, sid, _ in ряд[idx + 1:]}
    c = разбор_открытых(заказы, закр, сегодня, после_оплаты)
    n = c["открыто"]
    print("\nОТКРЫТЫЕ ЗАКАЗЫ — КОНТРОЛЬНЫЕ ТОЧКИ")
    for k in ("есть срок", "есть дост_бюдж", "есть отгр_план", "есть дост_план", "нет срока клиента",
              "срок клиента прошёл, не доставлен", "до срока ≤ 14 дн., поставщик не отгрузил",
              "плановая отгрузка поставщика прошла без факта", "план доставки позже срока клиента",
              "после стадии оплаты, оплачено поставщику < 100 %"):
        print(f"  {k}: {доля(c[k], n)}")
    print("  просрочка срока клиента по давности: " + "; ".join(f"{b} {c['просрочка ' + b]}" for _, b in КОРЗИНЫ if c["просрочка " + b]))

    д = разбор_доставленных(заказы, сегодня, с)
    схемы = {str(i.get("ID")): str(i.get("VALUE")) for i in (поля_з.get(З["схема"]) or {}).get("items") or []}
    print(f"\nДОСТАВЛЕНО КЛИЕНТУ ЗА {DAYS} ДН. (факт доставки): {д['n']}")
    print(f"  в срок клиента: {доля(д['в срок'], д['со сроком'])} · без срока клиента: {д['n'] - д['со сроком']}")
    print(f"  задержка у опоздавших, дн.: {медиана(д['задержка'])}")
    for k in ("сквозной", "заказ→отгрузка", "отгрузка→склад", "склад→клиент", "сдвиг отгрузки", "сдвиг доставки к бюджету"):
        print(f"  {k}, дн.: {медиана(д[k])}")
    print(f"  без УПД: {доля(д['без УПД'], д['n'])}, из них доставлено больше 14 дн. назад: {д['без УПД > 14 дн.']}")
    print(f"  нет п/п клиента через 30+ дн. после доставки: {д['без п/п клиента > 30 дн.']} · плановый срок оплаты прошёл, п/п нет: "
          f"{д['срок оплаты прошёл, п/п нет']}")
    print("  в срок по схеме поставки (схема | доставлено со сроком | в срок):")
    for k, (всего, вср) in sorted(д["по схеме"].items(), key=lambda x: -x[1][0]):
        print(f"    {схемы.get(k, k)} | {всего} | {вср}")
    успех_без_факта = sum(1 for z in заказы if стадии_з.get(str(z.get("stageId")), ("", ""))[1] == "S"
                          and not дата(z.get(З["дост_факт"])) and (дата(z.get("movedTime")) or с) >= с)
    print(f"  заказ закрыт успехом за {DAYS} дн. без даты доставки клиенту: {успех_без_факта}")

    # ── Сделки, у которых все заказы закрыты, а сделка открыта
    по_сделке = collections.defaultdict(list)
    for z in заказы:
        if z.get("parentId2"):
            по_сделке[str(z["parentId2"])].append(стадии_з.get(str(z.get("stageId")), ("", "P"))[1])
    ids_откр = {str(d["ID"]) for d in открытые}
    все_закр = sum(1 for i in ids_откр if по_сделке.get(i) and all(s == "S" for s in по_сделке[i]))
    без_заказов = collections.Counter(мета.get(str(d.get("STAGE_ID")), {}).get("name", "?")
                                      for d in открытые if not по_сделке.get(str(d["ID"])))
    print(f"\nСДЕЛКИ ↔ ЗАКАЗЫ: открытых сделок со всеми заказами в успехе: {все_закр} · открытых без заказов: "
          f"{sum(без_заказов.values())} (" + "; ".join(f"{k} {v}" for k, v in без_заказов.most_common(8)) + ")")

    # ── История стадий заказов: сколько живёт заказ в каждой стадии
    hist = client.stage_history(ORDERS, since=с.isoformat() + "T00:00:00")
    дл = collections.defaultdict(list)
    for oid, ряд in hist.items():
        for (s0, t0), (_, t1) in zip(ряд, ряд[1:]):
            a, b = дата(t0), дата(t1)
            if a and b:
                дл[s0].append((b - a).days)
    print(f"\nИСТОРИЯ СТАДИЙ ЗАКАЗОВ за {DAYS} дн.: заказов {len(hist)} · дней в стадии до следующей")
    for sid, (имя, sm, _, k) in sorted(стадии_з.items(), key=lambda x: (int(x[1][3]), x[1][2])):
        if дл.get(sid):
            print(f"  [{k}] {имя}: {медиана(дл[sid])}")

    # ── Гарантия, рекламации, сервис
    типы = (client.call("crm.type.list", {}) or {}).get("types") or []
    print(f"\nГАРАНТИЯ И РЕКЛАМАЦИИ: смарт-процессов в портале {len(типы)}")
    for t in типы:
        if ПОЛЯ_ГАРАНТИИ.search(str(t.get("title") or "")):
            env = client.call_envelope("crm.item.list", {"entityTypeId": t["entityTypeId"], "select": ["id"], "start": 0}) or {}
            print(f"  СП-{t['entityTypeId']} «{t.get('title')}»: записей {env.get('total', '?')}")
    поля_с = client.call("crm.deal.fields") or {}
    for где, поля in (("сделка", поля_с), (f"заказ СП-{ORDERS}", поля_з)):
        найдено = [(код, str(m.get("formLabel") or m.get("listLabel") or m.get("title") or ""))
                   for код, m in поля.items()
                   if ПОЛЯ_ГАРАНТИИ.search(str(m.get("formLabel") or m.get("listLabel") or m.get("title") or ""))]
        print(f"  поля «{где}» с подписью про гарантию / рекламацию / сервис / акт: {len(найдено)}"
              + ("" if not найдено else " — " + "; ".join(f"{к} «{п[:60]}»" for к, п in найдено[:15])))
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
