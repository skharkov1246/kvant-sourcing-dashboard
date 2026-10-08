#!/usr/bin/env python3
"""Зонд: конверсия выданных ТКП в контракты по клиентским холдингам.

Вопрос владельца 08.10.2026: «какое количество контрактов мы получаем относительно
того количества предложений, которые мы выдаём» — по Норникелю и в сравнении.

МОДЕЛЬ — ТА ЖЕ, ЧТО У history.py И reps.py (проверено на портале 08.09.2026):
  • ВОРОНКА сделки — та, где она ЗАВЕДЕНА: первая стадия в истории. Победа в этом
    портале — переезд карточки в воронку реализации (кат. 0) с тем же ID, поэтому
    по текущей воронке выигранные тендеры «пропадают» из своей воронки.
  • КОНТРАКТ — сделка не проиграна (STAGE_SEMANTIC_ID ≠ F) и хотя бы одно из:
    переезд в кат. 0 по истории; непроигранный заказ поставщику СП-172; номер
    реализации в названии («871. …»); семантика S — как reps.classify. Дата
    контракта — первый вход в кат. 0, иначе первый заказ поставщику.
  • ВЫДАННОЕ ПРЕДЛОЖЕНИЕ — по ИСТОРИИ стадий (проигранная после ТКП сделка сейчас
    стоит в стадии отказа, и по текущей стадии её в знаменателе не было бы):
      – стадия-предложение вне кат. 0 (reps._QUOTE_RE или stages.deal_reached_tkp:
        «ТКП выдан/отправлен», «Тендерное предложение выдано», «Quotation issued»)
        — даёт факт и дату;
      – стадия «дальше предложения»: кат. 0, успех, отгрузка, проигрыш с именем,
        которое означает поданное предложение («Не прошли по цене», «проиграли»)
        — даёт факт без даты.
  • СДЕЛКИ ТОЛЬКО РЕАЛИЗАЦИИ (заведены сразу в кат. 0, предпродажи в этой карточке
    нет) в конверсию не входят и считаются отдельно.
  • КОНВЕРСИЯ — в трёх видах, потому что одна цифра врёт:
      – по всем: контракты / все выданные предложения;
      – зрелая: без живых сделок моложе STUCK_DAYS (120 дн., history.py) — им
        ещё рано решаться;
      – среди решённых: контракты / (контракты + проиграно + «тихие потери» —
        живые старше 120 дн.: по history.py практически потеряны).
    Контракт без отметки предложения в истории в знаменатель не идёт; счётчик
    таких печатается — это пробел данных, а не отдельный путь.
  • ХОЛДИНГ — kam.client_dir (разметка вкладки КАМ) плюс широкий шаблон юрлиц
    группы, которых разметка КАМ не знает. Вторая, независимая разметка — по КАМ
    сделки (поле «КАМ», иначе ответственный) из отдела 110.
  Одна сделка — одно предложение: редакции ТКП в одной сделке знаменатель не
  размножают.

ЧТО В ЖУРНАЛ. Репозиторий публичный (CLAUDE.md, правило 17; распоряжение 30.09):
счётчики, доли, медианы дней, названия холдингов разметки КАМ и юрлиц группы;
названия воронок и стадий — с маской слов из названий компаний-клиентов. Ни сумм,
ни сделок, ни людей.

НАГРУЗКА (навык bitrix-ingest): сделки — по ключу >ID; компании — пачками по 50;
история стадий — пачками по 50 сделок (OWNER_ID массивом) со сверкой с total и
проверкой, что фильтр сработал; заказы СП-172 — по ключу со сверкой с total.
Оценка запросов и времени — до обхода, сводка нагрузки — в конце.
"""
from __future__ import annotations

import collections
import datetime as dt
import os
import re
import statistics
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SINCE = os.environ.get("CONV_SINCE", "2024-06-01T00:00:00")   # глубина, как HIST_SINCE в contracts.py
HOLDING = "Норникель"
DEPT_HOLDING = "110"           # kam.CLIENT_GROUPS: «Норникель»
ORDER_ENTITY = 172
STUCK_DAYS = 120               # history.STUCK_DAYS: порог «тихой потери»
MSK = ZoneInfo("Europe/Moscow")
# Юрлица группы, которых нет в kam.CLIENT_HOLDINGS (общий модуль здесь не трогаем).
ГРУППА_ШИРЕ = re.compile(r"норникел|nornickel|гипроникел|\bнтэк\b|медвежий\s*ручей|таймырск\w*\s*топлив|"
                         r"норметимпэкс|заполярн\w*\s*(филиал|транспорт)", re.I)
# Проигрыш, который означает: предложение подано и не выиграло.
ПРОИГРЫШ_ПОСЛЕ_ПРЕДЛОЖЕНИЯ = re.compile(r"не\s*прошл\w*\s*по\s*(цен|тех|срок)|проигра|выбрал\w*\s*друг|"
                                        r"цена\s*выше|победил\w*\s*друг", re.I)
ПОСЛЕ_РЕАЛИЗАЦИИ = re.compile(r"ОТГРУ|ДОСТАВ|ПРОИЗВОДСТВО\s*ЗАВЕРШЕНО|ПОДГОТОВКА\s*К\s*ОТГРУЗКЕ", re.I)


def номер_реализации(title) -> int:
    """Номер реализации в начале названия («871. …»), как company._regno."""
    m = re.match(r"\s*(\d{1,4})(?:/\d+)?\.", str(title or ""))
    return int(m.group(1)) if m else 0


def воронка_стадии(stage_id) -> str:
    """'C2:NEW' → '2', 'NEW' → '0' (history._cat_of_stage)."""
    s = str(stage_id or "")
    if s.startswith("C") and ":" in s:
        head = s[1:s.index(":")]
        return head if head.isdigit() else "0"
    return "0"


def дата_мск(s) -> str | None:
    """Метка портала → дата по Москве (история и заказы приходят с разными смещениями)."""
    try:
        d = dt.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return str(s)[:10] if re.match(r"\d{4}-\d\d-\d\d", str(s or "")) else None
    if d.tzinfo is None:
        d = d.replace(tzinfo=MSK)
    return d.astimezone(MSK).date().isoformat()


def вид_стадии(stage_id, meta) -> tuple[str, str]:
    """Стадия → (вид, правило): 'offer' — предложение выдано (даёт дату), 'beyond' —
    дальше предложения (факт без даты), 'none'. Правило — константа кода."""
    from reps import _QUOTE_RE
    from stages import _TKP_NEG, deal_reached_tkp
    м = meta.get(stage_id) or {}
    имя, sem = str(м.get("name") or ""), str(м.get("sem") or "").upper()
    if воронка_стадии(stage_id) == "0":
        return "beyond", "воронка реализации"
    if sem == "S":
        return "beyond", "успех"
    if sem == "F":
        if ПРОИГРЫШ_ПОСЛЕ_ПРЕДЛОЖЕНИЯ.search(имя):
            return "beyond", "проигрыш после предложения"
        return "none", "проигрыш"
    if ПОСЛЕ_РЕАЛИЗАЦИИ.search(имя):
        return "beyond", "отгрузка/производство"
    if any(n in имя.upper() for n in _TKP_NEG):
        return "none", "не выдано"
    if _QUOTE_RE.search(имя) and not str(stage_id).endswith(":EXECUTING"):
        return "offer", "имя: предложение выдано"
    if deal_reached_tkp(str(stage_id), sem, имя):
        return "offer", "имя или суффикс: ТКП выдано"
    return "none", "до предложения"


def медиана(xs):
    return round(statistics.median(xs)) if xs else None


def доля(a, b):
    return round(100 * a / b) if b else None


def классифицировать(сделки, история, мета, заказы, холдинг_сделки, сегодня):
    """Сделки → строка на сделку. Чистая функция.

    история — {deal_id: [(stage_id, iso_time), …]} первых входов в стадию;
    заказы — crm.item СП-172 без проигранных; холдинг_сделки — {deal_id: имя}.
    """
    первый_заказ: dict[str, str] = {}
    for о in заказы:
        d = str(о.get("parentId2") or "")
        t = дата_мск(о.get("createdTime"))
        if d and d != "0" and t and (d not in первый_заказ or t < первый_заказ[d]):
            первый_заказ[d] = t
    out = []
    for д in сделки:
        did = str(д["ID"])
        ряд = sorted(история.get(did, []), key=lambda r: str(r[1]))
        origin = воронка_стадии(ряд[0][0]) if ряд else str(д.get("CATEGORY_ID") or "0")
        в_кат0 = next((дата_мск(t) for s, t in ряд if воронка_стадии(s) == "0"), None) if origin != "0" else None
        sem = str(д.get("STAGE_SEMANTIC_ID") or "").upper()
        сигналы = {"кат0": bool(в_кат0), "заказ": did in первый_заказ,
                   "номер": номер_реализации(д.get("TITLE")) > 0, "успех": sem == "S"}
        cls = "lost" if sem == "F" else ("contract" if any(сигналы.values()) else "open")
        виды = [(вид_стадии(s, мета)[0], дата_мск(t)) for s, t in ряд]
        дата_предложения = min((t for v, t in виды if v == "offer" and t), default=None)
        if дата_предложения:
            откуда = "history"
        elif any(v == "beyond" for v, _ in виды):
            откуда = "beyond"
        elif cls == "contract":
            откуда = "contract_only"
        else:
            откуда = "none"
        дата_контракта = (в_кат0 or первый_заказ.get(did)) if cls == "contract" else None
        создана = дата_мск(д.get("DATE_CREATE"))
        опорная = дата_предложения or создана
        возраст = (сегодня - dt.date.fromisoformat(опорная)).days if опорная else None
        out.append({
            "id": did, "holding": холдинг_сделки.get(did) or "Без клиента", "origin": origin,
            "realization_only": origin == "0", "cls": cls, "signals": сигналы,
            "offer": откуда in ("history", "beyond"), "offer_from": откуда,
            "offer_date": дата_предложения, "contract_date": дата_контракта, "created": создана,
            "cohort": (дата_предложения or дата_контракта or создана or "")[:4] or "?",
            "stuck": cls == "open" and возраст is not None and возраст > STUCK_DAYS,
            "young": cls == "open" and (возраст is None or возраст <= STUCK_DAYS),
        })
    return out


def свод(строки):
    """Строки одной выборки → счётчики конверсии предложение → контракт."""
    база = [r for r in строки if not r["realization_only"]]
    п = [r for r in база if r["offer"]]
    к = [r for r in п if r["cls"] == "contract"]
    л = [r for r in п if r["cls"] == "lost"]
    тихие = [r for r in п if r["stuck"]]
    молодые = [r for r in п if r["young"]]
    дни = []
    for r in к:
        if r["offer_date"] and r["contract_date"]:
            a, b = dt.date.fromisoformat(r["offer_date"]), dt.date.fromisoformat(r["contract_date"])
            if b >= a:
                дни.append((b - a).days)
    return {
        "deals": len(база), "offers": len(п), "contracts": len(к), "lost": len(л),
        "stuck": len(тихие), "young": len(молодые),
        "conv": доля(len(к), len(п)),
        "conv_mature": доля(len(к), len(п) - len(молодые)),
        "conv_decided": доля(len(к), len(к) + len(л) + len(тихие)),
        "contracts_only": sum(1 for r in база if r["cls"] == "contract" and r["offer_from"] == "contract_only"),
        "offers_undated": sum(1 for r in п if r["offer_from"] == "beyond"),
        "realization_only": sum(1 for r in строки if r["realization_only"]),
        "days_median": медиана(дни), "days_n": len(дни),
    }


def строка_свода(имя, с):
    def f(v):
        return "—" if v is None else f"{v}%"
    dm = "—" if с["days_median"] is None else f"{с['days_median']} дн. (по {с['days_n']})"
    return (f"  {str(имя)[:30]:30} предложений {с['offers']:5} · контрактов {с['contracts']:4}"
            f" · проиграно {с['lost']:4} · тихих потерь {с['stuck']:4} · молодых {с['young']:4}"
            f" | конверсия {f(с['conv']):>4} · зрелая {f(с['conv_mature']):>4}"
            f" · среди решённых {f(с['conv_decided']):>4} | предложение→контракт {dm}"
            f" | контрактов без отметки предложения {с['contracts_only']},"
            f" предложений без даты {с['offers_undated']}, сделок только реализации {с['realization_only']}")


def слова_клиентов(компании):
    """Основы слов из названий компаний-клиентов — для маски в названиях воронок и
    стадий. Основа — первые 5 букв: ловит склонения («Полюс» → «Полюсу»)."""
    стоп = {"ооо", "оао", "зао", "пао", "llc", "ltd", "филиал", "компания", "завод", "group", "общество",
            "клиент", "сервис", "групп", "холдинг", "торгов", "промышл"}
    основы = set()
    for n in компании.values():
        for w in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", str(n)):
            w = w.lower()
            if any(w.startswith(x) for x in стоп):
                continue
            основы.add(w[:5])
    return sorted(основы, key=len, reverse=True)


def маска(текст, основы):
    out = str(текст or "")
    for w in основы:
        out = re.sub(rf"(?<![A-Za-zА-Яа-яЁё]){re.escape(w)}[A-Za-zА-Яа-яЁё]*", "‹клиент›", out, flags=re.I)
    return out


def отчёт(строки, история, кам_сделки, мета, cats, слова):
    import kam
    print(f"\nВЫБОРКА: сделки, созданные с {SINCE[:10]}; одна сделка — одно предложение; год — по дате "
          f"предложения (иначе контракта, иначе создания); {SINCE[:4]} — неполный год")

    прошли = collections.Counter()
    for ряд in история.values():
        for s in {s for s, _ in ряд}:
            прошли[s] += 1
    по_воронке = collections.defaultdict(list)
    for sid, m in мета.items():
        if прошли.get(sid):
            v, why = вид_стадии(sid, мета)
            по_воронке[воронка_стадии(sid)].append(
                (int(m.get("sort") or 0), f"{маска(m.get('name') or sid, слова)} [{v} · {why} · {прошли[sid]}]"))
    print("\nКлассификация стадий (стадия [вид · правило · сделок прошло]) — проверить глазами:")
    for cat in sorted(по_воронке, key=int):
        print(f"  {маска(cats.get(cat, 'воронка ' + cat), слова)}: "
              + "; ".join(t for _, t in sorted(по_воронке[cat])))

    print("\nКОНВЕРСИЯ ПРЕДЛОЖЕНИЕ → КОНТРАКТ")
    print(строка_свода("Все клиенты", свод(строки)))
    hn = [r for r in строки if r["holding"] == HOLDING]
    print(строка_свода(HOLDING, свод(hn)))

    # Имена в журнал — только холдингов разметки КАМ: для прочих client_dir отдаёт
    # сырое название компании-клиента, а это клиентские данные в публичном журнале.
    известные = {имя for _, имя in kam.CLIENT_HOLDINGS} | {"Без клиента"}
    по_холдингу = collections.defaultdict(list)
    for r in строки:
        по_холдингу[r["holding"] if r["holding"] in известные else "Прочие клиенты"].append(r)
    print("\nДля сравнения — холдинги разметки КАМ, прочие клиенты одной строкой:")
    for h in sorted((h for h in по_холдингу if h != HOLDING), key=lambda h: -свод(по_холдингу[h])["offers"]):
        print(строка_свода(h, свод(по_холдингу[h])))

    for заголовок, выборка in ((HOLDING, hn), ("Все клиенты", строки)):
        print(f"\n{заголовок} — по году:")
        годы = sorted({r["cohort"] for r in выборка})
        for год in годы:
            print(строка_свода(год, свод([r for r in выборка if r["cohort"] == год])))
        if sum(свод([r for r in выборка if r["cohort"] == г])["offers"] for г in годы) != свод(выборка)["offers"]:
            raise RuntimeError("годы не сходятся с итогом")

    print(f"\n{HOLDING} — по воронке, где сделка заведена:")
    for cat in sorted({r["origin"] for r in hn}, key=lambda x: -свод([r for r in hn if r["origin"] == x])["offers"]):
        с = свод([r for r in hn if r["origin"] == cat])
        if с["offers"]:
            print(строка_свода(маска(cats.get(cat, "воронка " + cat), слова), с))

    к = [r for r in hn if r["cls"] == "contract" and not r["realization_only"]]
    print(f"\n{HOLDING} — чем подтверждён контракт (у одного контракта может быть несколько признаков): "
          + ", ".join(f"{s} {sum(1 for r in к if r['signals'][s])}" for s in ("кат0", "заказ", "номер", "успех"))
          + f"; без переезда в кат. 0: {sum(1 for r in к if not r['signals']['кат0'])}")

    по_каму = [r for r in строки if r["id"] in кам_сделки]
    общие = {r["id"] for r in hn} & кам_сделки
    print(f"\nПроверка разметки {HOLDING}: по компании {len(hn)} сделок, по КАМ из отдела {DEPT_HOLDING} "
          f"{len(по_каму)}, в обеих {len(общие)}; у КАМ-сделок без компании "
          f"{sum(1 for r in по_каму if r['holding'] == 'Без клиента')}, другой компании "
          f"{sum(1 for r in по_каму if r['holding'] not in (HOLDING, 'Без клиента'))}")
    print(строка_свода(f"{HOLDING} (по КАМ)", свод(по_каму)))
    print(строка_свода(f"{HOLDING} (обе разметки)", свод([r for r in hn if r["id"] in общие])))


def читать_историю(client, ids):
    """История стадий пачками по 50 сделок, со сверкой с total. → (история, ошибка)."""
    история: dict[str, list] = {}
    for i in range(0, len(ids), 50):
        часть = [int(x) for x in ids[i:i + 50]]
        свои = {str(x) for x in часть}
        start, total, got = 0, None, 0
        while True:
            data = client.call_envelope("crm.stagehistory.list", {
                "entityTypeId": 2, "filter": {"OWNER_ID": часть},
                "select": ["ID", "OWNER_ID", "CREATED_TIME", "STAGE_ID"],
                "order": {"ID": "ASC"}, "start": start})
            res = (data or {}).get("result") or {}
            items = (res.get("items") if isinstance(res, dict) else res) or []
            if total is None:
                total = int((data or {}).get("total") or 0)
                if ({str(x.get("OWNER_ID")) for x in items} - свои) or total > 50 * 80:
                    return история, "фильтр OWNER_ID массивом не сработал — обход остановлен"
            got += len(items)
            for x in items:
                o, s = str(x.get("OWNER_ID")), str(x.get("STAGE_ID") or "")
                if o in свои and s and all(s != st for st, _ in история.get(o, [])):
                    история.setdefault(o, []).append((s, str(x.get("CREATED_TIME") or "")))
            nxt = (data or {}).get("next")
            if not nxt or not items:
                break
            start = nxt
        if got < (total or 0):
            return история, f"история стадий неполна в пачке {i // 50 + 1}: {got} из {total}"
    return история, None


def main() -> int:
    import config
    import kam
    import people
    from bitrix_client import BitrixClient, бюджет_портала, сводка_нагрузки

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    env = client.call_envelope("crm.deal.list", {"filter": {">=DATE_CREATE": SINCE}, "select": ["ID"], "start": 0})
    всего = int((env or {}).get("total") or 0)
    env = client.call_envelope("crm.item.list", {"entityTypeId": ORDER_ENTITY, "filter": {},
                                                 "select": ["id"], "start": 0})
    ждём_заказов = int((env or {}).get("total") or 0)
    if всего <= 0 or ждём_заказов <= 0:
        print(f"::error::не получено число сделок ({всего}) или заказов ({ждём_заказов}) — полноту не проверить")
        return 1
    rps, par = бюджет_портала()
    n = 2 + всего // 50 + 1 + всего // 150 + (всего // 50 + 1) * 6 + ждём_заказов // 50 + 1 + 60
    print(f"ожидается: сделок с {SINCE[:10]} — {всего}, заказов поставщикам — {ждём_заказов}; "
          f"оценка ≈ {n} запросов, ≈ {n * par / rps / 60:.0f} мин при {rps:g}/с")

    сделки = client.list_deals_fast(filter={">=DATE_CREATE": SINCE}, select=[
        "ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE",
        "COMPANY_ID", "ASSIGNED_BY_ID", people.KAM_F, people.KAM_OLD])
    print(f"прочитано сделок: {len(сделки)} из {всего}")
    if len(сделки) < всего:
        print("::error::обход сделок оборвался — итог был бы неполным")
        return 1

    компании = client.companies_by_ids({str(д.get("COMPANY_ID")) for д in сделки
                                        if str(д.get("COMPANY_ID") or "0") != "0"})

    def холдинг_компании(cid):
        if not cid or cid == "0":
            return "Без клиента"
        имя = компании.get(cid, "")
        h = kam.client_dir(имя)
        return HOLDING if (h == HOLDING or ГРУППА_ШИРЕ.search(имя)) else h
    холдинг = {str(д["ID"]): холдинг_компании(str(д.get("COMPANY_ID") or "0")) for д in сделки}
    по_разметке = sum(1 for n in компании.values() if kam.client_dir(n) == HOLDING)
    шире = sorted(n for n in компании.values() if kam.client_dir(n) != HOLDING and ГРУППА_ШИРЕ.search(n))
    print(f"компаний у сделок: {len(компании)}; юрлиц {HOLDING}: по разметке КАМ {по_разметке}, "
          f"добавлено шаблоном группы {len(шире)}" + (": " + "; ".join(шире) if шире else ""))

    состав = people.roster(client)
    отдел = {uid for uid, p in состав.items() if DEPT_HOLDING in (p.get("depts") or [])}
    кам_сделки = {str(д["ID"]) for д in сделки
                  if str(д.get(people.KAM_F) or д.get(people.KAM_OLD) or д.get("ASSIGNED_BY_ID") or "") in отдел}
    print(f"отдел {DEPT_HOLDING}: сотрудников (вкл. уволенных) {len(отдел)}, сделок по КАМ {len(кам_сделки)}")

    мета = client.deal_stage_meta()
    cats = client.categories()

    история, ошибка = читать_историю(client, [str(д["ID"]) for д in сделки])
    if ошибка:
        print(f"::error::{ошибка}")
        return 1
    print(f"история стадий: сделок с записями {len(история)} из {len(сделки)}")

    сырые = client.list_items(ORDER_ENTITY, filter={}, select=["id", "stageId", "createdTime", "parentId2"])
    if len(сырые) < ждём_заказов:
        print(f"::error::заказы СП-172 прочитаны не все: {len(сырые)} из {ждём_заказов}")
        return 1
    заказы = [о for о in сырые if not str(о.get("stageId", "")).endswith(":FAIL")]
    print(f"заказов поставщикам: {len(сырые)}, непроигранных {len(заказы)}")

    строки = классифицировать(сделки, история, мета, заказы, холдинг, dt.datetime.now(MSK).date())
    отчёт(строки, история, кам_сделки, мета, cats, слова_клиентов(компании))
    print(сводка_нагрузки())
    return 0


if __name__ == "__main__":
    # Трассировка с содержимым сделки в публичный журнал не уходит: печатаются
    # только тип ошибки и места в коде (файл:строка), без сообщения.
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
