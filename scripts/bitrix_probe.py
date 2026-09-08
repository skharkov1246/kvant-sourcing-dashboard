"""Зонд v27: сегментация сделок за календарный год по типам оборудования.

Задача владельца: выделить десять направлений, по которым имеет смысл глубокое
исследование (как сделано по ГПУ, ГТУ и ГШО), и понять, какие из них изучены хуже
всего при сопоставимом объёме спроса.

ЧТО ПЕЧАТАЕТСЯ: только агрегаты по сегментам — число сделок, сумма, доля выигранных
и проигранных, средний чек, число запросов поставщикам. Названия сделок, клиентов,
контрагентов и позиций НЕ выводятся: репозиторий публичный, журналы сборок открыты.
По нераспознанному остатку печатаются только строчные русские слова длиной от пяти
букв с частотой от пяти — названия компаний и брендов почти всегда пишутся с большой
буквы или латиницей и такой фильтр не проходят.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests

# ── сегменты. Ключ — короткое имя, значение — слова-приметы (в нижнем регистре).
# Порядок важен: сделка относится к сегменту с наибольшим числом совпадений,
# при равенстве — к тому, что выше.
SEGMENTS: dict[str, list[str]] = {
    "ГПУ — газопоршневые": [
        "газопоршн", "гпу", "cummins", "камминз", "jenbacher", "енбахер", "waukesha",
        "mwm", "innio", "g3512", "gta", "qsk", "kta", "поршневая электростанция", "пгу",
    ],
    "ГТУ — газотурбинные": [
        "газотурб", "гту", "турбина", "sgt", "lm6000", "lm2500", "frame", "solar turbines",
        "taurus", "centaur", "mars 100", "siemens sgt", "kawasaki", "ггпа", "гпа",
    ],
    "ГШО — горно-шахтное": [
        "перфоратор", "буров", "гшо", "epiroc", "эпирок", "atlas copco", "sandvik", "сандвик",
        "tamrock", "normet", "нормет", "пдм", "погрузочно-доставочн", "коронка", "штанга буров",
        "самоходн вагон", "крепь", "комбайн проход",
    ],
    "Насосное оборудование": [
        "насос", "flowserve", "флоусерв", "sulzer", "зульцер", "ksb", "grundfos", "грундфос",
        "цнс", "шламов", "warman", "уорман", "weir", "гнб", "консольн", "секционн", "пескового",
    ],
    "Компрессоры": [
        "компрессор", "винтов", "ingersoll", "kaeser", "кэзер", "atlas copco ga", "воздуходув",
        "осушитель воздуха", "ресивер",
    ],
    "Дробление и обогащение": [
        "дробилк", "мельниц", "грохот", "флотац", "гидроциклон", "metso", "метсо", "outotec",
        "оутотек", "сгущ", "конусн", "щеков", "футеровк", "сепаратор магн", "классификатор",
        "обогатит",
    ],
    "Трубопроводная арматура": [
        "задвижк", "затвор дисков", "клапан", "кран шаров", "вентиль", "арматур",
        "регулирующ клапан", "обратн клапан", "предохранит клапан", "фланц",
    ],
    "Электротехника и приводы": [
        "трансформатор", "кру", "ктп", "частотн", "чрп", "преобразователь частот",
        "электродвигател", "abb", "schneider", "шнейдер", "ячейк", "щит", "распредустройств",
        "генератор синхрон", "кабель",
    ],
    "КИПиА и автоматизация": [
        "датчик", "расходомер", "манометр", "термопар", "emerson", "endress", "yokogawa",
        "асу тп", "контроллер", "уровнемер", "кипиа", "кип и а", "преобразователь давлен",
    ],
    "Подъёмно-транспортное": [
        "конвейер", "транспортёр", "транспортер", "лебёдк", "лебедк", "кран мостов",
        "редуктор", "тельфер", "лента конвейерн", "барабан привод", "роликоопор",
    ],
    "Теплообмен и котельное": [
        "теплообменник", "alfa laval", "альфа лаваль", "котёл", "котел ", "градирн",
        "экономайзер", "калорифер", "теплообменн",
    ],
    "Карьерная спецтехника": [
        "самосвал", "экскаватор", "белаз", "komatsu", "коматсу", "погрузчик фронтальн",
        "бульдозер", "автогрейдер", "caterpillar 7", "буровой станок",
    ],
    "Подшипники и уплотнения": [
        "подшипник", "skf", "timken", "тимкен", "уплотнени", "john crane", "манжет",
        "сальник", "торцевое уплотнен", "ртэ", "рти",
    ],
    "Металлопрокат и трубы": [
        "труба", "трубы", "лист стальн", "швеллер", "двутавр", "металлопрокат", "отвод",
        "тройник", "металлоконструкц", "арматура а500",
    ],
    "Сварка и инструмент": [
        "сварочн", "электрод", "проволок сварочн", "резак", "инструмент", "абразив",
        "круг отрезн", "сверло", "фреза",
    ],
}

STOP = re.compile(r"[^а-яё]")
NOISE = {
    "поставка", "поставки", "запрос", "запросы", "коммерческое", "предложение", "оборудование",
    "оборудования", "заказчик", "заказчика", "договор", "спецификация", "тендер", "закупка",
    "закупки", "материалы", "запчасти", "запасные", "части", "комплект", "комплекта", "новая",
    "новый", "проект", "работы", "услуги", "прочее", "разное", "позиция", "позиции",
}


def bx_all(method: str, params: dict, cap: int = 100000) -> list:
    base = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
    out, start = [], 0
    while True:
        j = {}
        for _ in range(4):
            try:
                r = requests.post(f"{base}/{method}.json", json={**params, "start": start}, timeout=90)
                r.raise_for_status()
                j = r.json()
                break
            except Exception:
                continue
        res = j.get("result")
        items = res.get("items") if isinstance(res, dict) and "items" in res else res
        out += items or []
        if "next" not in j or len(out) >= cap:
            return out
        start = j["next"]


def classify(text: str) -> str | None:
    t = text.lower().replace("ё", "е")
    best, score = None, 0
    for name, words in SEGMENTS.items():
        n = sum(1 for w in words if w.replace("ё", "е") in t)
        if n > score:
            best, score = name, n
    return best


def main() -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00+03:00")
    print(f"=== Зонд v27: сделки, созданные с {since[:10]} ===\n")

    deals = bx_all("crm.deal.list", {
        "filter": {">=DATE_CREATE": since},
        "select": ["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "OPPORTUNITY", "CURRENCY_ID",
                   "DATE_CREATE", "CLOSED", "ASSIGNED_BY_ID"],
        "order": {"ID": "ASC"},
    })
    print(f"сделок за период: {len(deals)}\n")
    if not deals:
        print("данных нет — проверьте права вебхука")
        return 0

    # позиции сделок: название номенклатуры сильно точнее заголовка сделки
    rows = bx_all("crm.item.productrow.list", {"filter": {"=ownerType": "D"}}, cap=200000)
    by_deal: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_deal[str(r.get("ownerId"))].append(str(r.get("productName") or ""))
    print(f"строк номенклатуры получено: {len(rows)} · сделок с позициями: {len(by_deal)}\n")

    seg_deals: Counter = Counter()
    seg_sum: Counter = Counter()
    seg_won: Counter = Counter()
    seg_lost: Counter = Counter()
    seg_won_sum: Counter = Counter()
    seg_rows: Counter = Counter()
    unknown_titles: list[str] = []
    total_sum = 0.0

    for d in deals:
        did = str(d.get("ID"))
        text = " ".join([str(d.get("TITLE") or "")] + by_deal.get(did, []))
        seg = classify(text) or "— не распознано"
        stage = str(d.get("STAGE_ID") or "")
        amount = float(d.get("OPPORTUNITY") or 0)
        total_sum += amount
        seg_deals[seg] += 1
        seg_sum[seg] += amount
        seg_rows[seg] += len(by_deal.get(did, []))
        if "WON" in stage:
            seg_won[seg] += 1
            seg_won_sum[seg] += amount
        elif "LOSE" in stage or "APOLOGY" in stage:
            seg_lost[seg] += 1
        if seg == "— не распознано":
            unknown_titles.append(text)

    print("=== СЕГМЕНТЫ (по сумме сделок) ===")
    print(f"{'сегмент':32s} {'сделок':>7s} {'сумма, млн':>11s} {'выигр':>6s} {'проигр':>7s} "
          f"{'winrate':>8s} {'ср.чек, тыс':>12s} {'позиций':>8s}")
    for seg, _ in seg_sum.most_common():
        n = seg_deals[seg]
        w, l = seg_won[seg], seg_lost[seg]
        closed = w + l
        wr = f"{w / closed * 100:5.1f}%" if closed else "     —"
        avg = seg_sum[seg] / n / 1000 if n else 0
        print(f"{seg:32s} {n:>7d} {seg_sum[seg] / 1e6:>11.1f} {w:>6d} {l:>7d} "
              f"{wr:>8s} {avg:>12.0f} {seg_rows[seg]:>8d}")
    print(f"{'ИТОГО':32s} {len(deals):>7d} {total_sum / 1e6:>11.1f}")

    print("\n=== ПРОИГРАННЫЕ ДЕНЬГИ (сумма закрытых минус выигранных) ===")
    for seg, _ in seg_sum.most_common():
        miss = seg_sum[seg] - seg_won_sum[seg]
        if seg_deals[seg] >= 3:
            print(f"{seg:32s} упущено {miss / 1e6:>9.1f} млн · выиграно {seg_won_sum[seg] / 1e6:>8.1f} млн")

    # запросы поставщикам (СП-166) — объём проработки по тем же сегментам
    try:
        rfq = bx_all("crm.item.list", {"entityTypeId": 166, "filter": {">=createdTime": since[:10]},
                                       "select": ["id", "title", "createdTime"]}, cap=60000)
        rseg: Counter = Counter()
        for it in rfq:
            rseg[classify(str(it.get("title") or "")) or "— не распознано"] += 1
        print(f"\n=== ЗАПРОСЫ ПОСТАВЩИКАМ (СП-166), карточек за период: {len(rfq)} ===")
        for seg, n in rseg.most_common():
            d = seg_deals.get(seg, 0)
            ratio = f"{n / d:5.1f}" if d else "    —"
            print(f"{seg:32s} запросов {n:>6d} · сделок {d:>5d} · запросов на сделку {ratio}")
    except Exception as e:
        print(f"\nСП-166 недоступен: {e}")

    print(f"\n=== НЕРАСПОЗНАННЫЙ ОСТАТОК: {len(unknown_titles)} сделок ===")
    print("частые строчные русские слова (>=5 букв, >=5 вхождений) — подсказка для новых сегментов:")
    tok: Counter = Counter()
    for t in unknown_titles:
        for w in t.split():
            w2 = STOP.sub("", w.lower())
            if len(w2) >= 5 and w2 not in NOISE and w == w.lower():
                tok[w2] += 1
    shown = [(w, n) for w, n in tok.most_common(200) if n >= 5][:60]
    for i in range(0, len(shown), 3):
        print("   " + "".join(f"{w:24s}{n:>4d}   " for w, n in shown[i:i + 3]))

    print("\n✓ зонд v27 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
