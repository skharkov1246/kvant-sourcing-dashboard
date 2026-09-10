"""CI-зонд: все запросы СП-166 по нашей номенклатуре за период — где реально есть КП.

Вопрос владельца (10.09.2026): сорсинг говорит, что по теме прокотировано всё.
Первый зонд смотрел только сделки со словом «Энергосети» и нашёл 197 запросов,
из них с КП 30. Но запросы по этой же номенклатуре могли идти вне этих сделок —
отдельными сделками, общими RFQ по брендам, без привязки к родителю.

Этот зонд снимает ограничение по сделке: тянет ВСЕ записи СП-166 за период
и размечает их по брендам из двух наших листов (ЛУКОЙЛ-Энергосети 1561 строка
и ЛУКОЙЛ-НВН 528 строк). Отвечает: сколько запросов по теме, в каких стадиях,
по каким сделкам, кому слали и сколько КП реально получено.

Стадии Selected / Not Selected / Price at Work = КП от поставщика получено.
Отказ в КП / Ответ не получен / Не подошло по технике = нет.

Печатает сводку в лог Actions. Секреты не выводит.
Запуск: Actions → Bitrix probe → Run workflow (ветка с этой версией скрипта).
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402
from config import SPA_ENTITY_TYPE_ID, Settings  # noqa: E402

PERIOD_FROM = os.getenv("PROBE_FROM", "2026-01-01")

# бренды из листов Энергосети и НВН — по ним размечаем «наша тема»
TOPICS = {
    "Solar/Taurus": ["solar", "солар", "taurus", "тауру", "centaur", "titan 130", "mars 100"],
    "Siemens SGT": ["siemens", "сименс", "sgt", "simatic", "siprotec", "profibus"],
    "Cummins/ГПЭС": ["cummins", "камминз", "камминс", "qsk", "qsv", "kta", "газопоршн", "гпэс", "гпу"],
    "Jenbacher/INNIO": ["jenbacher", "енбахер", "innio", "мwm", "mwm"],
    "Bently Nevada": ["bently", "бентли", "proximit", "3500"],
    "Fleetguard/фильтры": ["fleetguard", "флитгард", "af25", "lf9", "wf20", "фильтр"],
    "ABB": ["abb", "асс800", "acs800", "acs880"],
    "Буровое НВН": ["drillmec", "дриллмек", "nov ", "national oilwell", "tesco", "shaffer",
                    "bentec", "превентор", "bop", "верхний привод", "top drive", "вибросито"],
    "SLB/Cameron": ["schlumberger", "cameron", "камерон", "swaco", "vetco", "fmc"],
    "КИП/автоматика": ["pepperl", "det-tronics", "det tronics", "allen bradley", "wago",
                       "weidmuller", "harting", "balluff", "баллуф", "scancon", "semikron",
                       "woodward", "comat", "releco", "auma", "asco", "danfoss", "wandfluh"],
    "Насосы": ["bornemann", "marflex", "ingersoll", "pedrollo", "pompetravaini", "grundfos"],
    "ЛУКОЙЛ (прямо)": ["лукойл", "lukoil", "нвн", "нижневолж", "энергосет", "л-эс", "л-нвн"],
}
GOT_QUOTE = {"Selected", "Not Selected", "Price at Work"}
NO_QUOTE = {"Отказ в КП", "Ответ не получен (в срок)", "Не подошло по технике"}


def crm_refs(v) -> list[str]:
    if not v:
        return []
    vals = v if isinstance(v, list) else [v]
    return [(str(x).split("_", 1)[1] if str(x).startswith("CO_") else str(x)) for x in vals]


def topics_of(text: str) -> list[str]:
    t = (text or "").lower()
    return [name for name, kws in TOPICS.items() if any(k in t for k in kws)]


def main() -> int:
    client = BitrixClient(Settings.load().bitrix_webhook_url)
    print(f"период с {PERIOD_FROM}\n")

    select = ["id", "title", "stageId", "createdTime", "parentId2", "companyId",
              "assignedById", "ufCrm18Supplier", "opportunity"]
    items = client.list_items(SPA_ENTITY_TYPE_ID,
                              filter={">=createdTime": PERIOD_FROM}, select=select)
    print(f"=== ВСЕГО ЗАПИСЕЙ СП-166 С {PERIOD_FROM}: {len(items)} ===")

    stages: dict[str, str] = {}
    for cat in (24, 0):
        try:
            stages.update(client.spa_stages(SPA_ENTITY_TYPE_ID, cat))
        except Exception:
            pass

    # разметка по темам
    tagged = []
    for r in items:
        tp = topics_of(r.get("title") or "")
        if tp:
            tagged.append((r, tp))
    print(f"из них по НАШЕЙ номенклатуре: {len(tagged)}\n")

    def stage_of(r):
        return stages.get(str(r.get("stageId")), str(r.get("stageId")))

    # общий разрез по стадиям
    by_stage = Counter(stage_of(r) for r, _ in tagged)
    got = sum(n for s, n in by_stage.items() if s in GOT_QUOTE)
    ref = sum(n for s, n in by_stage.items() if s in NO_QUOTE)
    other = sum(by_stage.values()) - got - ref
    print("--- СТАДИИ (по нашей номенклатуре) ---")
    for s, n in by_stage.most_common():
        mark = "КП ЕСТЬ " if s in GOT_QUOTE else ("нет КП  " if s in NO_QUOTE else "в работе")
        print(f"  {n:>5} | {mark} | {s}")
    tot = sum(by_stage.values()) or 1
    print(f"\n  КП получено      : {got:>5} ({100 * got / tot:.0f}%)")
    print(f"  отказ / молчание : {ref:>5} ({100 * ref / tot:.0f}%)")
    print(f"  в работе, без КП : {other:>5} ({100 * other / tot:.0f}%)")

    # по темам
    print("\n--- ПО ТЕМАМ: запросов / из них с КП ---")
    t_all = Counter()
    t_got = Counter()
    for r, tps in tagged:
        g = stage_of(r) in GOT_QUOTE
        for t in tps:
            t_all[t] += 1
            if g:
                t_got[t] += 1
    for t, n in t_all.most_common():
        print(f"  {n:>5} / {t_got[t]:>4} КП  ({100 * t_got[t] / n:>3.0f}%)  {t}")

    # по месяцам
    print("\n--- ПО МЕСЯЦАМ: запросов / из них с КП ---")
    m_all = Counter()
    m_got = Counter()
    for r, _ in tagged:
        m = (r.get("createdTime") or "")[:7]
        m_all[m] += 1
        if stage_of(r) in GOT_QUOTE:
            m_got[m] += 1
    for m in sorted(m_all):
        print(f"  {m}: {m_all[m]:>5} / {m_got[m]:>4} КП")

    # по сделкам
    parents = Counter(str(r.get("parentId2") or "—") for r, _ in tagged)
    p_got = Counter()
    for r, _ in tagged:
        if stage_of(r) in GOT_QUOTE:
            p_got[str(r.get("parentId2") or "—")] += 1
    top_ids = [d for d, _ in parents.most_common(30) if d not in ("—", "0")]
    dmap = client.deals_by_ids(top_ids, select=["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CURRENCY_ID"]) if top_ids else {}
    print("\n--- ТОП-30 СДЕЛОК ПО ЧИСЛУ ЗАПРОСОВ ПО НАШЕЙ ТЕМЕ ---")
    for did, n in parents.most_common(30):
        d = dmap.get(str(did), {})
        opp = float(d.get("OPPORTUNITY") or 0)
        print(f"  {did:>8}: запросов {n:>4}, с КП {p_got[did]:>4} | {opp:>14,.0f} {d.get('CURRENCY_ID') or ''} | "
              f"{(d.get('TITLE') or ('без привязки' if did == '—' else '?'))[:62]}")

    # поставщики, от кого есть КП
    comp_ids = set()
    for r, _ in tagged:
        if r.get("companyId"):
            comp_ids.add(str(r["companyId"]))
        comp_ids.update(crm_refs(r.get("ufCrm18Supplier")))
    names = client.companies_by_ids(list(comp_ids)) if comp_ids else {}
    sup_got = Counter()
    for r, _ in tagged:
        if stage_of(r) not in GOT_QUOTE:
            continue
        s = ", ".join(dict.fromkeys(filter(None, (
            [names.get(str(r.get("companyId") or ""), "")]
            + [names.get(c, c) for c in crm_refs(r.get("ufCrm18Supplier"))])))) or "—"
        sup_got[s] += 1
    print(f"\n--- ПОСТАВЩИКИ, ОТ КОГО ЕСТЬ КП: {len(sup_got)} компаний ---")
    for s, n in sup_got.most_common(40):
        print(f"  {n:>3}  {s[:92]}")

    print(f"\n--- ВСЕ ЗАПРОСЫ С КП ПО НАШЕЙ ТЕМЕ: {got} ---")
    for r, tps in sorted(((r, t) for r, t in tagged if stage_of(r) in GOT_QUOTE),
                         key=lambda x: x[0].get("createdTime") or ""):
        s = ", ".join(dict.fromkeys(filter(None, (
            [names.get(str(r.get("companyId") or ""), "")]
            + [names.get(c, c) for c in crm_refs(r.get("ufCrm18Supplier"))])))) or "—"
        print(f"  #{r['id']:>7} | {(r.get('createdTime') or '')[:10]} | сделка {str(r.get('parentId2') or '—'):>7} | "
              f"{stage_of(r):<14} | {s[:34]:<34} | {','.join(tps)[:26]:<26} | {(r.get('title') or '')[:52]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
