#!/usr/bin/env python3
"""Зонд «2025–2026»: заявка → выданное ТКП → контракт → заказ поставщику → поставка.

Вопросы владельца 09.10.2026: «выданные КП ты считаешь как?», «по ощущениям
конверсия в Норильске сильно ниже», «ты запрыгивал в заказы поставщикам? давай
сконцентрируемся на разборе 25 и 26 годов».

ЧЕМ ОТЛИЧАЕТСЯ ОТ probe_conversion.py (разбор пяти проверяющих 09.10.2026). Модель
сделки та же (воронка заведения, признаки контракта, когорта — год предложения,
иначе создания), функции берутся оттуда. Добавлено:
  • ПРЕДЛОЖЕНИЕ — четыре определения рядом, а не одно:
      O0 — заявка: всякая предпродажная сделка (воронка заведения не 0 и не техническая);
      O1 — датированная стадия «предложение выдано» (как в v4);
      O2 — в сделке есть файл нашего КП (поля «Offer from us», «Result, ТКП»,
           «Образец ТКП»; поле «Result file» — отдельной колонкой, его смысл не установлен);
      O1∪O2.
    Главная ячейка — проигранные сделки с файлом КП, но без стадии «выдано»: это
    часть знаменателя, которую v4 не видел (ТКП ушло, стадию не сдвинули).
  • КОНТРАКТ — два определения: v4 и СТРОГИЙ — сделка не проиграна и (сейчас в кат. 0
    при воронке заведения не 0, или по ней есть живой заказ СП-172 — живой по
    СЕМАНТИКЕ стадии заказа, а не по суффиксу :FAIL).
  • БЮДЖЕТНЫЕ ЗАПРОСЫ — закрытые стадией «Бюджетирование / Мониторинг цен» или с
    «запрос расценки» в названии: это не закупка; конверсия без них — отдельно.
  • НОРНИКЕЛЬ — четыре разметки рядом: по компании (как v4), по воронке клиента
    (воронка заведения, чьё название kam.client_dir относит к холдингу), по номеру
    «НН-» в названии, по КАМ отдела 110; пересечения печатаются.
  • ЗАКАЗЫ СП-172 2025 и 2026 (по дате создания заказа): исход по семантике,
    заказов на сделку, доля сегмента в закупке года, число поставщиков и доля десяти
    крупнейших, тип поставщика, отгрузка против плана, доставка против срока
    заказчику, открытые просроченные, длительности этапов.
  • Нативные «Предложения» Битрикса и документы генератора — только total.

ЧТО В ЖУРНАЛ. Репозиторий публичный (CLAUDE.md, правило 5 и распоряжение 30.09):
счётчики, доли, медианы и перцентили дней, названия холдингов разметки КАМ, ID
воронок и стадий с обезличенными именами (probe_conversion.обезличить). Ни сумм, ни
наценки, ни названий компаний, поставщиков и брендов, ни ID и названий сделок. Доля
по сумме при меньше чем 5 контрактах не печатается.

НАГРУЗКА: те же чтения, что у probe_conversion.py (≈ 950 запросов при 1/с), плюс
родительские сделки заказов вне выборки (по 50), справочники стадий СП-172 и два
запроса total. Поля файлов и денег добавлены в уже идущие select — без новых запросов.
"""
from __future__ import annotations

import collections
import datetime as dt
import importlib.util
import os
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _модуль_v4():
    spec = importlib.util.spec_from_file_location("kvant_probe_conversion", ROOT / "scripts" / "probe_conversion.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pc = _модуль_v4()

YEARS = tuple(y.strip() for y in os.environ.get("CONV_YEARS", "2025,2026").split(",") if y.strip())
HOLDING = pc.HOLDING
ORDER_ENTITY = pc.ORDER_ENTITY
STUCK_DAYS = pc.STUCK_DAYS
OUTLIER_EUR = pc.OUTLIER_EUR
MIN_N = pc.MIN_N
MIN_VALUE_N = 5                 # меньше контрактов — доля по сумме не печатается
TECH_CATS = frozenset({"6", "22", "24", "26", "28"})   # people.TECH_CATS (сверяется тестом)

# Файловые поля нашего КП в сделке (library/doc_folder.py:138-146).
ПОЛЯ_КП = {"UF_CRM_1585568303498": "Offer from us", "UF_CRM_1733957302549": "Result, ТКП",
           "UF_CRM_1783934184627": "Образец ТКП"}
ПОЛЕ_RESULT = "UF_CRM_1780061070"           # «Result file», направление не установлено

# Поля заказа СП-172 (publish_deals.py, зонд полей 02.10.2026).
DL_CUSTOMER = "ufCrm20_1728900218435"       # «Date of deadline to customer»
SHIP_PLAN = "ufCrm20_1723235828"            # «Supplier Shipment Date, planned»
SHIP_FACT = "ufCrm20_1723236324"            # «Supplier Shipment Date, actual»
CUST_PLAN = "ufCrm20_1723236261"            # «Customer delivery date, planned»
CUST_FACT = "ufCrm20_1723236501"            # «Customer delivery date, actual»
SUP_TYPE = "ufCrm20_1755847772314"          # «Тип поставщика», список
ORDER_SELECT = ["id", "stageId", "categoryId", "createdTime", "parentId2", "companyId", "opportunity",
                "currencyId", DL_CUSTOMER, SHIP_PLAN, SHIP_FACT, CUST_PLAN, CUST_FACT, SUP_TYPE]

БЮДЖЕТНЫЙ = re.compile(r"бюджетирован|мониторинг\s+цен", re.I)
РАСЦЕНКА = re.compile(r"запрос\w*\s+расцен", re.I)
НН = re.compile(r"(?<![А-Яа-яЁёA-Za-z])НН-\s?\d")
# Номер заявки «НН-<n>»: кириллица или латиница, любой регистр, дефис или тире или
# пробел, ведущие нули отбрасываются (разбор 09.10.2026: «HH-500», «НН 500» не ловились).
НН_НОМЕР = re.compile(r"(?<![А-Яа-яЁёA-Za-z])[НнHh][НнHh]\s*[-–—]?\s*0*(\d+)")
ТИП_ОБЩИЙ = re.compile(r"(?:производител|трейдер|дилер|дистрибьютор|официальн|посредник|агент|склад|прям)"
                       r"(?:ь|я|и|ей|ы|ов|а|ой|ая|ое|ые|ый|ий|ск(?:ий|ая|ое|ие|ой))?"
                       r"|manufacturers?|traders?|dealers?|distributors?|official|direct|agents?|stock(?:ist)?", re.I)
ТИП_СЛУЖЕБНЫЕ = {"и", "или", "не", "без", "с", "прочее", "другое", "other", "and", "or"}


def тип_общий(v) -> bool:
    """Подпись типа поставщика печатается, только если каждое слово в ней — общее
    слово процесса: «Официальный дистрибьютор» — да, с названием бренда — нет."""
    слова = re.findall(r"[A-Za-zА-Яа-яЁё]+", str(v or ""))
    return bool(слова) and all(ТИП_ОБЩИЙ.fullmatch(w) or w.lower() in ТИП_СЛУЖЕБНЫЕ for w in слова)


def файлы(v) -> set[str]:
    """Значение файлового поля из crm.deal.list → множество ключей файлов.

    Портал отдаёт файл словарём {id, showUrl, downloadUrl}, множественное поле —
    списком таких словарей; пустое — false, "", None или []."""
    if v in (None, "", False, 0, "0", [], {}):
        return set()
    if isinstance(v, list):
        out: set[str] = set()
        for x in v:
            out |= файлы(x)
        return out
    if isinstance(v, dict):
        k = v.get("id") or v.get("ID") or v.get("downloadUrl") or v.get("showUrl")
        return {str(k)} if k else set()
    if isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit()):
        return {str(v).strip()}
    return set()


def сем_заказа(stage, семантика) -> str:
    """Исход заказа по семантике стадии ('S'/'P'/'F'); без справочника — по суффиксу."""
    s = str(stage or "")
    if s in семантика:
        return семантика[s]
    return "F" if s.endswith(":FAIL") else ("S" if s.endswith(":SUCCESS") else "P")


def разметить(строки, сделки, история, мета, заказы, семантика, клиентские_воронки, кам_сделки,
              бюджет, в_евро, сегодня, техн=TECH_CATS):
    """Строки v4 (pc.классифицировать) → те же строки с определениями O0–O2,
    строгим контрактом, бюджетным признаком, сегментами и стоимостью. Чистая функция."""
    по_id = {str(д["ID"]): д for д in сделки}
    живой = {str(о.get("parentId2")) for о in заказы
             if str(о.get("parentId2") or "0") != "0" and сем_заказа(о.get("stageId"), семантика) != "F"}
    for r in строки:
        д = по_id[r["id"]]
        ряд = sorted(история.get(r["id"], []), key=lambda x: str(x[1]))
        r["tech"] = r["origin"] in техн
        r["o0"] = not r["realization_only"] and not r["tech"]
        r["o1"] = bool(r["offer_dated"])
        ф: set[str] = set()
        for поле in ПОЛЯ_КП:
            ф |= файлы(д.get(поле))
        r["files"], r["o2"] = len(ф), bool(ф)
        r["result_file"] = bool(файлы(д.get(ПОЛЕ_RESULT)))
        # Повторное ТКП — возврат в стадию «выдано» после ухода из неё (переторжка,
        # ревизия), а не проход двух разных стадий «выдано» подряд.
        виды = [pc.вид_стадии(s, мета)[0] == "offer" for s, _ in ряд if pc.воронка_стадии(s) != "0"]
        r["offer_entries"] = sum(1 for i, v in enumerate(виды) if v and (i == 0 or not виды[i - 1]))
        sem = str(д.get("STAGE_SEMANTIC_ID") or "").upper()
        кат = str(д.get("CATEGORY_ID") or "0")
        r["live_order"] = r["id"] in живой
        r["strict"] = sem != "F" and ((кат == "0" and r["origin"] != "0") or r["live_order"])
        имя_стадии = str((мета.get(str(д.get("STAGE_ID") or "")) or {}).get("name") or "")
        r["lost_stage"] = str(д.get("STAGE_ID") or "") if sem == "F" else None
        r["budget"] = (sem == "F" and bool(БЮДЖЕТНЫЙ.search(имя_стадии))) or bool(РАСЦЕНКА.search(str(д.get("TITLE") or "")))
        r["seg_company"] = r["holding"] == HOLDING
        r["seg_funnel"] = r["origin"] in клиентские_воронки
        r["seg_nn"] = bool(НН.search(str(д.get("TITLE") or "")))
        r["nn"] = sorted(set(НН_НОМЕР.findall(str(д.get("TITLE") or ""))))
        r["seg_kam"] = r["id"] in кам_сделки
        опорная = r["offer_date"] or r["created"]
        r["ripe"] = bool(опорная) and (сегодня - dt.date.fromisoformat(опорная)).days > STUCK_DAYS
        v = r.get("amount")
        r["v_offer"] = v if v and 0 < v <= OUTLIER_EUR else None
        # Стоимость контракта: выручка бюджета без НДС, иначе сумма сделки (как v4).
        # Поля «Оплачено» / «Остаток к оплате» сюда не идут: чья это оплата — наша
        # поставщику или заказчика нам — не установлено (DECISIONS.md, Р-2).
        if бюджет.get(r["id"]):
            r["v_contract"], r["v_src"] = бюджет[r["id"]], "бюджет"
        elif r["v_offer"]:
            r["v_contract"], r["v_src"] = r["v_offer"], "сумма сделки"
        else:
            r["v_contract"], r["v_src"] = None, None
    return строки


def _счёт(xs):
    n = len(xs)
    c = sum(1 for r in xs if r["cls"] == "contract")
    cs = sum(1 for r in xs if r["strict"])
    return {"n": n, "c": c, "cs": cs, "lost": sum(1 for r in xs if r["cls"] == "lost"),
            "open": sum(1 for r in xs if r["cls"] == "open"), "conv": pc.доля(c, n), "conv_s": pc.доля(cs, n)}


def свод_определений(rows):
    """Одна выборка → конверсия при каждом определении предложения и контракта."""
    o0 = [r for r in rows if r["o0"]]
    o1 = [r for r in o0 if r["o1"]]
    o2 = [r for r in o0 if r["o2"]]
    lost = [r for r in o0 if r["cls"] == "lost"]
    # «до ТКП» — ни стадии «выдано», ни файла, ни признака v4 «после предложения»
    # (переторжка, «не прошли по цене»): такие — проигрыш после предложения без даты.
    до = [r for r in lost if not (r["o1"] or r["o2"] or r["offer"])]
    к4 = [r for r in o0 if r["cls"] == "contract"]
    return {
        "O0": _счёт(o0), "O1": _счёт(o1), "O2": _счёт(o2),
        "O12": _счёт([r for r in o0 if r["o1"] or r["o2"]]),
        "O2only": _счёт([r for r in o2 if not r["o1"]]), "O1only": _счёт([r for r in o1 if not r["o2"]]),
        "O0nb": _счёт([r for r in o0 if not r["budget"]]),
        "O1nb": _счёт([r for r in o1 if not r["budget"]]), "O1ripe": _счёт([r for r in o1 if r["ripe"]]),
        "O12ripe": _счёт([r for r in o0 if (r["o1"] or r["o2"]) and r["ripe"]]),
        "lost": len(lost), "lost_after": sum(1 for r in lost if r["o1"]),
        "lost_file_only": sum(1 for r in lost if r["o2"] and not r["o1"]), "lost_before": len(до),
        "lost_undated": sum(1 for r in lost if r["offer"] and not (r["o1"] or r["o2"])),
        "lost_before_budget": sum(1 for r in до if r["budget"]),
        "lost_before_reasons": collections.Counter(r["lost_stage"] for r in до).most_common(4),
        "budget": sum(1 for r in o0 if r["budget"]), "budget_o1": sum(1 for r in o1 if r["budget"]),
        "multi": sum(1 for r in o1 if r["offer_entries"] > 1),
        "files_med": pc.медиана([r["files"] for r in o2]), "files_p90": pc.перцентиль([r["files"] for r in o2], 90),
        "result_file": sum(1 for r in o0 if r["result_file"]),
        "result_file_alone": sum(1 for r in o0 if r["result_file"] and not (r["o1"] or r["o2"])),
        "v4_not_strict": sum(1 for r in к4 if not r["strict"]),
        "strict_not_v4": sum(1 for r in o0 if r["strict"] and r["cls"] != "contract"),
        "only_signal": collections.Counter(
            next(k for k, v in r["signals"].items() if v) for r in к4 if sum(r["signals"].values()) == 1),
    }


def свод_суммы(rows, база="O1"):
    """Конверсия по сумме при определении предложения O1 или O12 — только доли."""
    xs = [r for r in rows if r["o0"] and (r["o1"] if база == "O1" else (r["o1"] or r["o2"]))]
    с = [r for r in xs if r["v_offer"]]
    всего = sum(r["v_offer"] for r in с)
    к = [r for r in с if r["cls"] == "contract"]
    кс = [r for r in с if r["strict"]]
    п = [r for r in с if r["cls"] == "lost"]

    def заполн(cls):
        ys = [r for r in xs if r["cls"] == cls]
        return pc.доля(sum(1 for r in ys if r["v_offer"]), len(ys))

    # Доля по сумме печатается, только если и взятых, и невзятых не меньше
    # MIN_VALUE_N: иначе дополнение к доле — деньги одной сделки.
    def д(a, взятых=len(к)):
        return pc.доля(a, всего) if взятых >= MIN_VALUE_N and len(с) - взятых >= MIN_VALUE_N else None
    return {
        "n": len(xs), "with": len(с), "k": len(к), "ks": len(кс),
        "conv": д(sum(r["v_offer"] for r in к)),
        "conv_s": д(sum(r["v_offer"] for r in кс), len(кс)),
        "conv_casc": д(sum(r["v_contract"] or 0 for r in к)),
        "src": collections.Counter(r["v_src"] for r in к),
        "fill_c": заполн("contract"), "fill_l": заполн("lost"), "fill_o": заполн("open"),
        "won_vs_lost": (round(statistics.median([r["v_offer"] for r in к]) /
                              statistics.median([r["v_offer"] for r in п]), 2)
                        if len(к) >= MIN_VALUE_N and len(п) >= MIN_VALUE_N else None),
    }


def _дн(a, b):
    """Дни от a до b (даты ISO) или None."""
    if not a or not b:
        return None
    try:
        return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    except ValueError:
        return None


def разметить_заказы(заказы, семантика, сделки_все, холдинг_сделки, строки_по_id, клиентские_воронки, в_евро,
                     бюджет=None):
    """Заказы СП-172 → строка на заказ с датами по Москве, исходом и сегментом
    родительской сделки. Чистая функция.

    first_live — дата ПЕРВОГО живого заказа сделки по всем годам: «ТКП → первый
    заказ» считается в году этого заказа, а не по первому заказу внутри года."""
    первый_живой: dict[str, str] = {}
    for о in заказы:
        d, t = str(о.get("parentId2") or "0"), pc.дата_мск(о.get("createdTime"))
        if d != "0" and t and сем_заказа(о.get("stageId"), семантика) != "F" and (d not in первый_живой or t < первый_живой[d]):
            первый_живой[d] = t
    out = []
    бюджет = бюджет or {}
    for о in заказы:
        d = str(о.get("parentId2") or "0")
        r = строки_по_id.get(d)
        дл = сделки_все.get(d) or {}
        if r:
            родитель = "карточка реализации" if r["realization_only"] else f"воронка {r['origin']}"
        elif дл:
            родитель = f"сделка до {pc.SINCE[:7]}, сейчас воронка {str(дл.get('CATEGORY_ID') or '0')}"
        else:
            родитель = "без сделки"
        out.append({
            "deal": d if d != "0" else None,
            "year": (pc.дата_мск(о.get("createdTime")) or "")[:4],
            "created": pc.дата_мск(о.get("createdTime")),
            "sem": сем_заказа(о.get("stageId"), семантика),
            "supplier": str(о.get("companyId") or "0"),
            "eur": в_евро(о.get("opportunity"), о.get("currencyId")),
            "ship_plan": pc.дата_мск(о.get(SHIP_PLAN)) if о.get(SHIP_PLAN) else None,
            "ship_fact": pc.дата_мск(о.get(SHIP_FACT)) if о.get(SHIP_FACT) else None,
            "cust_plan": pc.дата_мск(о.get(CUST_PLAN)) if о.get(CUST_PLAN) else None,
            "cust_fact": pc.дата_мск(о.get(CUST_FACT)) if о.get(CUST_FACT) else None,
            "deadline": pc.дата_мск(о.get(DL_CUSTOMER)) if о.get(DL_CUSTOMER) else None,
            "sup_type": str(о.get(SUP_TYPE) or "") or None,
            "seg_company": холдинг_сделки.get(d) == HOLDING,
            "seg_funnel": bool(r) and r["origin"] in клиентские_воронки,
            "seg_nn": bool(НН.search(str((сделки_все.get(d) or {}).get("TITLE") or ""))),
            "first_live": первый_живой.get(d),
            "outside": d != "0" and d not in строки_по_id and d in сделки_все,
            "orphan": d == "0" or d not in сделки_все,
            "offer_date": r["offer_date"] if r else None,
            "kat0_date": (r["contract_date"] if r and r["signals"].get("кат0") else None),
            "cur": str(о.get("currencyId") or "") or None,
            "deal_cur": str(дл.get("CURRENCY_ID") or "") or None,
            "deal_eur": в_евро(дл.get("OPPORTUNITY"), дл.get("CURRENCY_ID")) if дл else None,
            "budget_eur": бюджет.get(d),
            "parent": родитель,
        })
    return out


def проверка_заказов(год_все, сегодня=None):
    """Заказы Норникеля (по компании) одного года ВНЕ клиентской воронки: признаки
    ошибки ввода и откуда они. Только счётчики и доли (журнал публичный).

    Признаки: закупка больше продажи сделки в 3+ раза; валюта заказа не та, что у
    сделки; закупка больше выручки бюджета сделки; сумма больше OUTLIER_EUR; дубль
    (та же сделка, поставщик и сумма); суммы нет."""
    живые = [о for о in год_все if о["sem"] != "F"]
    нн = [о for о in живые if о["seg_company"]]
    вне = [о for о in нн if not о["seg_funnel"]]
    всего = sum(о["eur"] or 0 for о in живые)
    всего_нн = sum(о["eur"] or 0 for о in нн)
    сумма_вне = sum(о["eur"] or 0 for о in вне)

    def корзина(x):
        return ("меньше ×0,1" if x < 0.1 else "×0,1–0,9" if x < 0.9 else "×0,9–1" if x <= 1
                else "×1–3" if x <= 3 else "×3–10" if x <= 10 else "больше ×10")

    def отношение(о):
        if not о["eur"] or not о["deal_eur"]:
            return "сумма сделки пуста" if о["eur"] else "суммы заказа нет"
        return корзина(о["eur"] / о["deal_eur"])
    ключи = collections.Counter((о["deal"], о["supplier"], round(о["eur"] or 0)) for о in вне if о["eur"])
    дубль = [о for о in вне if о["eur"] and ключи[(о["deal"], о["supplier"], round(о["eur"]))] > 1]
    валюта = [о for о in вне if о["cur"] and о["deal_cur"] and о["cur"] != о["deal_cur"]]
    больше3 = [о for о in вне if о["eur"] and о["deal_eur"] and о["eur"] > 3 * о["deal_eur"]]
    меньше01 = [о for о in вне if о["eur"] and о["deal_eur"] and о["eur"] < 0.1 * о["deal_eur"]]
    больше_бюджета = [о for о in вне if о["eur"] and о["budget_eur"] and о["eur"] > о["budget_eur"]]
    выброс = [о for о in вне if о["eur"] and о["eur"] > OUTLIER_EUR]
    # Сделка целиком: сумма ВСЕХ живых заказов сделки за год против её продажи —
    # поштучная проверка не видит, что три заказа вместе больше сделки.
    по_сделке: dict = collections.defaultdict(float)
    for о in живые:
        if о["deal"] and о["eur"]:
            по_сделке[о["deal"]] += о["eur"]
    продажа = {о["deal"]: о["deal_eur"] for о in вне if о["deal"]}
    бюджет_сд = {о["deal"]: о["budget_eur"] for о in вне if о["deal"] and о["budget_eur"]}
    сделки_вне = sorted({о["deal"] for о in вне if о["deal"]})
    сделка_больше = {d for d in сделки_вне if продажа.get(d) and по_сделке[d] > продажа[d]}
    сделка_бюджет_больше = {d for d in сделки_вне if бюджет_сд.get(d) and по_сделке[d] > бюджет_сд[d]}
    помечены = {id(о) for о in больше3 + меньше01 + больше_бюджета + выброс + дубль}
    помечены |= {id(о) for о in вне if о["deal"] in сделка_больше | сделка_бюджет_больше}
    сумма_пом = sum(о["eur"] or 0 for о in вне if id(о) in помечены)
    сумма_вал = sum(о["eur"] or 0 for о in валюта)
    по_сумме = sorted((о["eur"] or 0 for о in вне), reverse=True)
    половина, накоплено = 0, 0.0
    for x in по_сумме:
        if накоплено >= сумма_вне / 2:
            break
        накоплено += x
        половина += 1
    топ_нн = sorted(нн, key=lambda о: -(о["eur"] or 0))[:10]
    return {
        "n": len(вне), "deals": len({о["deal"] for о in вне}), "nn_n": len(нн),
        "share_total": pc.доля(сумма_вне, всего), "share_nn": pc.доля(сумма_вне, всего_нн),
        "parents": collections.Counter(о["parent"] for о in вне).most_common(),
        "ratio": collections.Counter(отношение(о) for о in вне).most_common(),
        "cur_mismatch": len(валюта), "over3": len(больше3), "under01": len(меньше01), "over_budget": len(больше_бюджета),
        "cur_pairs": collections.Counter(f"{о['cur']}→{о['deal_cur']}" for о in валюта).most_common(),
        "cur_pair_ratio": collections.Counter(отношение(о) for о in валюта).most_common(),
        "cur_budget_ok": sum(1 for о in валюта if о["eur"] and о["budget_eur"] and о["eur"] <= о["budget_eur"]),
        "cur_share": pc.доля(сумма_вал, сумма_вне),
        "budget_ratio": collections.Counter(корзина(о["eur"] / о["budget_eur"]) for о in вне
                                            if о["eur"] and о["budget_eur"]).most_common(),
        "deal_ratio": collections.Counter(корзина(по_сделке[d] / продажа[d]) if продажа.get(d) else "сумма сделки пуста"
                                          for d in сделки_вне).most_common(),
        "deal_over": len(сделка_больше), "deal_over_budget": len(сделка_бюджет_больше),
        "budget_known": sum(1 for о in вне if о["budget_eur"]), "outlier": len(выброс), "dupes": len(дубль),
        "flagged": len(помечены),
        "nn_share_wo_flagged": pc.доля(всего_нн - сумма_пом, всего - сумма_пом),
        "outside_share_wo_flagged": pc.доля(сумма_вне - сумма_пом, всего - сумма_пом),
        "half_n": половина,
        "top10_nn_outside": sum(1 for о in топ_нн if not о["seg_funnel"]),
        "top10_nn_share": pc.доля(sum(о["eur"] or 0 for о in топ_нн), всего_нн) if len(нн) >= 10 else None,
        "sem": collections.Counter(о["sem"] for о in вне).most_common(),
    }


def строка_проверки(год, п):
    return (
        f"  {год}: заказов Норникеля {п['nn_n']}, из них вне клиентской воронки {п['n']} по сделкам {п['deals']};"
        f" их доля в закупке года {_п(п['share_total'])}, в закупке Норникеля {_п(п['share_nn'])};"
        f" половину их суммы дают {п['half_n']} заказа(ов)"
        f" | 10 крупнейших заказов Норникеля — {_п(п['top10_nn_share'])} его закупки, из них вне воронки {п['top10_nn_outside']}\n"
        f"    откуда: " + "; ".join(f"{k} {v}" for k, v in п["parents"]) + "\n"
        f"    заказ к продаже своей сделки: " + "; ".join(f"{k} {v}" for k, v in п["ratio"])
        + " | все живые заказы сделки за год к её продаже (по сделкам): " + "; ".join(f"{k} {v}" for k, v in п["deal_ratio"])
        + f"; сделок, где заказы больше продажи, {п['deal_over']}, больше выручки бюджета {п['deal_over_budget']}\n"
        f"    заказ к выручке бюджета сделки (бюджет у {п['budget_known']}): " + "; ".join(f"{k} {v}" for k, v in п["budget_ratio"]) + "\n"
        f"    валюта заказа ≠ валюте сделки {п['cur_mismatch']} ({_п(п['cur_share'])} суммы): "
        + "; ".join(f"{k} {v}" for k, v in п["cur_pairs"]) + " | их заказ к продаже: "
        + "; ".join(f"{k} {v}" for k, v in п["cur_pair_ratio"]) + f" | из них не больше выручки бюджета {п['cur_budget_ok']}\n"
        f"    признаки ошибки: заказ > продажи ×3 {п['over3']}; заказ < продажи ×0,1 {п['under01']}; заказ > выручки бюджета"
        f" {п['over_budget']}; больше {OUTLIER_EUR / 1e6:g} млн € {п['outlier']}; дублей {п['dupes']}; помечено всего"
        f" {п['flagged']} (валюта сама по себе не признак)"
        f" | без помеченных: доля Норникеля в закупке года {_п(п['nn_share_wo_flagged'])},"
        f" вне воронки {_п(п['outside_share_wo_flagged'])} | исход: " + ", ".join(f"{k} {v}" for k, v in п["sem"]))


def свод_закрытого_года(rows, номера_реализации):
    """ТКП года считаются закрытыми: открытые — проигрыш. Рядом — сколько открытых
    и проигранных на деле выиграны отдельной карточкой реализации: по номеру «НН-»
    (тот же номер в названии карточки реализации) и по v4 (та же компания, карточка
    в пределах TWIN_DAYS после ТКП)."""
    o1 = [r for r in rows if r["o0"] and r["o1"]]
    к = [r for r in o1 if r["cls"] == "contract"]
    откр = [r for r in o1 if r["cls"] == "open"]
    проигр = [r for r in o1 if r["cls"] == "lost"]

    def по_номеру(xs):
        return [r for r in xs if set(r.get("nn") or ()) & номера_реализации]
    оно, пно = по_номеру(откр), по_номеру(проигр)
    о4, п4 = [r for r in откр if r.get("twin_of")], [r for r in проигр if r.get("twin_of")]
    оба = {r["id"] for r in оно} | {r["id"] for r in о4}
    return {
        "n": len(o1), "c": len(к), "open": len(откр), "lost": len(проигр),
        "conv_closed": pc.доля(len(к), len(o1)),
        "open_nn": len(оно), "lost_nn": len(пно), "open_v4": len(о4), "lost_v4": len(п4),
        "open_both": len({r["id"] for r in оно} & {r["id"] for r in о4}), "open_any": len(оба),
        "conv_with_open_twins": pc.доля(len(к) + len(оба), len(o1)),
        "conv_with_all_twins": pc.доля(len(к) + len(оба | {r["id"] for r in пно} | {r["id"] for r in п4}), len(o1)),
        "open_with_nn": sum(1 for r in откр if r.get("nn")),
    }


def контроль_номеров(строки, номера_реализации):
    """Положительный контроль сопоставления по номеру «НН-»: находит ли номер карточки
    реализации хоть какую-то предпродажную сделку (любого исхода). Если и здесь ноль —
    в карточке реализации другой номер, и ноль по открытым ТКП ничего не доказывает."""
    пред = [r for r in строки if not r["realization_only"] and r.get("nn")]
    номера_пред = {n for r in пред for n in r["nn"]}
    карточки = [r for r in строки if r["realization_only"] and r.get("nn")]
    return {
        "cards": len(карточки), "card_numbers": len(номера_реализации),
        "cards_matched": sum(1 for r in карточки if set(r["nn"]) & номера_пред),
        "presale_with_nn": len(пред),
        "presale_matched": collections.Counter(r["cls"] for r in пред if set(r["nn"]) & номера_реализации).most_common(),
    }


def строка_закрытого(имя, з):
    return (f"  {имя}: ТКП {з['n']} → контрактов {з['c']}, проиграно {з['lost']}, открыто → проигрыш {з['open']};"
            f" КОНВЕРСИЯ {_п(з['conv_closed'], з['n'])} | открытых с номером НН- {з['open_with_nn']};"
            f" выиграны отдельной карточкой реализации: по номеру НН- {з['open_nn']}, по компании и сроку {з['open_v4']},"
            f" обоими {з['open_both']}, хотя бы одним {з['open_any']} → конверсия {_п(з['conv_with_open_twins'], з['n'])}"
            f" | среди проигранных такая карточка: по номеру {з['lost_nn']}, по компании и сроку {з['lost_v4']}"
            f" → с ними {_п(з['conv_with_all_twins'], з['n'])}")


def свод_заказов(xs, все_года, сегодня, подписи_типов=None):
    """Заказы одного сегмента и года → исполнение и сроки; xs и все_года — строки
    разметить_заказы (все_года — все заказы того же года для доли в закупке)."""
    живые = [о for о in xs if о["sem"] != "F"]
    по_сделке = collections.Counter(о["deal"] for о in xs if о["deal"])
    закупка = sum(о["eur"] or 0 for о in живые)
    закупка_все = sum(о["eur"] or 0 for о in все_года if о["sem"] != "F")
    по_пост = collections.defaultdict(float)
    for о in живые:
        if о["eur"] and о["supplier"] != "0":
            по_пост[о["supplier"]] += о["eur"]
    топ = sum(sorted(по_пост.values(), reverse=True)[:10])
    с_планом = [(о, _дн(о["ship_plan"], о["ship_fact"])) for о in живые if о["ship_plan"] and о["ship_fact"]]
    отгр = [x for _, x in с_планом if x is not None]
    к_сроку = [(о, _дн(о["deadline"], о["cust_fact"])) for о in живые if о["deadline"] and о["cust_fact"]]
    дост = [x for _, x in к_сроку if x is not None]
    просрочены = sum(1 for о in живые if о["sem"] == "P" and not о["cust_fact"] and о["deadline"]
                     and о["deadline"] < сегодня.isoformat())
    типы = collections.Counter(о["sup_type"] for о in живые if о["sup_type"])
    подп = подписи_типов or {}

    def имя_типа(k):
        v = str(подп.get(k) or "")
        return v if тип_общий(v) and len(v) <= 40 else f"тип #{k}"
    первый = {}
    for о in живые:
        if о["deal"] and о["created"] and о["created"] == о.get("first_live") and о["deal"] not in первый:
            первый[о["deal"]] = (о["created"], о["offer_date"], о["kat0_date"])
    return {
        "n": len(xs), "S": sum(1 for о in xs if о["sem"] == "S"), "P": sum(1 for о in xs if о["sem"] == "P"),
        "F": sum(1 for о in xs if о["sem"] == "F"), "deals": len(по_сделке),
        "per_deal_med": pc.медиана(list(по_сделке.values())), "per_deal_p90": pc.перцентиль(list(по_сделке.values()), 90),
        "share_purchase": pc.доля(закупка, закупка_все), "with_eur": sum(1 for о in живые if о["eur"]),
        "suppliers": len(по_пост), "top10": pc.доля(топ, sum(по_пост.values())),
        "types": [(имя_типа(k), v) for k, v in типы.most_common(4)], "types_fill": pc.доля(sum(типы.values()), len(живые)),
        "ship_fact": pc.доля(sum(1 for о in живые if о["ship_fact"]), len(живые)),
        "ship_n": len(отгр), "ship_late": pc.доля(sum(1 for x in отгр if x > 0), len(отгр)),
        "ship_late30": pc.доля(sum(1 for x in отгр if x > 30), len(отгр)),
        "ship_med": pc.медиана(отгр), "ship_p90": pc.перцентиль(отгр, 90),
        "cust_fact": pc.доля(sum(1 for о in живые if о["cust_fact"]), len(живые)),
        "cust_n": len(дост), "cust_late": pc.доля(sum(1 for x in дост if x > 0), len(дост)),
        "cust_med": pc.медиана(дост), "cust_p90": pc.перцентиль(дост, 90), "overdue_open": просрочены,
        "c_to_ship": pc.медиана([x for x in (_дн(о["created"], о["ship_fact"]) for о in живые) if x is not None and x >= 0]),
        "ship_to_cust": pc.медиана([x for x in (_дн(о["ship_fact"], о["cust_fact"]) for о in живые) if x is not None and x >= 0]),
        "offer_to_order": pc.медиана([x for x in (_дн(o, c) for c, o, _ in первый.values()) if x is not None and x >= 0]),
        "kat0_to_order": pc.медиана([x for x in (_дн(k, c) for c, _, k in первый.values()) if x is not None and x >= 0]),
        "outside": sum(1 for о in xs if о["outside"]), "orphan": sum(1 for о in xs if о["orphan"]),
    }


# ----------------------------------------------------------------------------- печать

def _п(v, n=None, мин=MIN_N):
    if v is None:
        return "—"
    return f"{v}%" + (" (мало данных)" if n is not None and n < мин else "")


def строка_определений(имя, с):
    o0, o1, o2, o12, o2o = с["O0"], с["O1"], с["O2"], с["O12"], с["O2only"]
    return (
        f"  {имя}\n"
        f"    заявки O0 {o0['n']} → контрактов {o0['c']} ({_п(o0['conv'], o0['n'])}), строгих {o0['cs']} ({_п(o0['conv_s'], o0['n'])})"
        f" | проиграно {o0['lost']}, открыто {o0['open']} | без бюджетных запросов {с['O0nb']['n']} → {_п(с['O0nb']['conv'], с['O0nb']['n'])}\n"
        f"    ТКП по стадии O1 {o1['n']} → {o1['c']} ({_п(o1['conv'], o1['n'])}), строгих {_п(o1['conv_s'], o1['n'])}"
        f" | зрелые {с['O1ripe']['n']} → {_п(с['O1ripe']['conv'], с['O1ripe']['n'])}"
        f" | без бюджетных запросов {с['O1nb']['n']} → {_п(с['O1nb']['conv'], с['O1nb']['n'])}\n"
        f"    ТКП по файлу O2 {o2['n']} → {_п(o2['conv'], o2['n'])}; файл без стадии {o2o['n']}: выигр. {o2o['c']},"
        f" проигр. {o2o['lost']}, откр. {o2o['open']} | O1∪O2 {o12['n']} → {o12['c']} ({_п(o12['conv'], o12['n'])}),"
        f" строгих {_п(o12['conv_s'], o12['n'])}, зрелые {_п(с['O12ripe']['conv'], с['O12ripe']['n'])}"
        f" | стадия без файла {с['O1only']['n']}\n"
        f"    проиграно {с['lost']}: после стадии ТКП {с['lost_after']}, с файлом без стадии {с['lost_file_only']},"
        f" после предложения без даты (переторжка, «не прошли по цене») {с['lost_undated']},"
        f" до ТКП {с['lost_before']} (из них бюджетных {с['lost_before_budget']})"
        f" | бюджетных запросов {с['budget']} (среди O1 {с['budget_o1']})"
        f" | повторных ТКП (≥2 входа в стадию) {с['multi']} | файлов КП на сделку медиана {с['files_med']} / 90% {с['files_p90']}"
        f" | «Result file» {с['result_file']} (без прочих признаков {с['result_file_alone']})\n"
        f"    контракты v4 не строгие {с['v4_not_strict']}, строгие не v4 {с['strict_not_v4']};"
        f" держатся на одном признаке: " + (", ".join(f"{k} {v}" for k, v in sorted(с["only_signal"].items())) or "—"))


def строка_суммы(имя, д4, д12):
    def часть(д):
        src = ", ".join(f"{k} {v}" for k, v in sorted(д["src"].items(), key=lambda kv: str(kv[0])))
        return (f"по сумме {_п(д['conv'])}, строгих {_п(д['conv_s'])}, со стоимостью контракта по каскаду {_п(д['conv_casc'])}"
                f" (источник: {src or '—'}) | сумма есть у {д['with']} из {д['n']}: у контрактов {_п(д['fill_c'])},"
                f" проигранных {_п(д['fill_l'])}, открытых {_п(д['fill_o'])} | выигранная/проигранная по медиане ×{д['won_vs_lost']}")
    return f"  {имя}\n    O1: {часть(д4)}\n    O1∪O2: {часть(д12)}"


def строка_заказов(имя, з):
    типы = ", ".join(f"{k} {v}" for k, v in з["types"]) or "—"
    return (
        f"  {имя}: заказов {з['n']} (успех {з['S']} / в работе {з['P']} / провал {з['F']}) по сделкам {з['deals']};"
        f" на сделку медиана {з['per_deal_med']} / 90% {з['per_deal_p90']} | доля в закупке года {_п(з['share_purchase'])}"
        f" | поставщиков {з['suppliers']}, 10 крупнейших — {_п(з['top10'])} закупки сегмента | тип: {типы}"
        f" (заполнен у {_п(з['types_fill'])})\n"
        f"    отгрузка поставщиком: факт у {_п(з['ship_fact'])}; позже плана {_п(з['ship_late'], з['ship_n'])},"
        f" позже на 30+ дн. {_п(з['ship_late30'], з['ship_n'])}, сдвиг медиана {з['ship_med']} / 90% {з['ship_p90']} дн. (по {з['ship_n']})"
        f" | заказчику: факт у {_п(з['cust_fact'])}; позже срока {_п(з['cust_late'], з['cust_n'])},"
        f" сдвиг медиана {з['cust_med']} / 90% {з['cust_p90']} дн. (по {з['cust_n']}); открытых просроченных {з['overdue_open']}\n"
        f"    медианы дней: заказ → отгрузка {з['c_to_ship']}, отгрузка → заказчик {з['ship_to_cust']},"
        f" ТКП → первый заказ {з['offer_to_order']}, кат. 0 → первый заказ {з['kat0_to_order']}"
        f" | сделка вне выборки {з['outside']}, без сделки {з['orphan']}")


def сегменты(rows):
    return [
        (f"{HOLDING} (компания)", [r for r in rows if r["seg_company"]]),
        (f"{HOLDING} (воронка клиента)", [r for r in rows if r["seg_funnel"]]),
        (f"{HOLDING} (номер НН-)", [r for r in rows if r["seg_nn"]]),
        (f"{HOLDING} (КАМ отдела {pc.DEPT_HOLDING})", [r for r in rows if r["seg_kam"]]),
        ("Прочие клиенты (без Норникеля)", [r for r in rows if not (r["seg_company"] or r["seg_funnel"] or r["seg_nn"])]),
        ("Все клиенты", rows),
    ]


def когорты(rows, сегодня):
    зрелые_до = (сегодня - dt.timedelta(days=STUCK_DAYS)).isoformat()
    out = [(год, [r for r in rows if r["cohort"] == год]) for год in YEARS]
    if YEARS:
        последний = YEARS[-1]
        out.append((f"{последний} зрелые (предложение или заявка до {зрелые_до})",
                    [r for r in rows if r["cohort"] == последний and r["ripe"]]))
        out.append(("–".join((YEARS[0], последний)) if len(YEARS) > 1 else последний,
                    [r for r in rows if r["cohort"] in YEARS]))
    return out


def отчёт(строки, заказы_разм, мета, сегодня, подписи_типов=None, клиентские_воронки=()):
    лексика = pc.общая_лексика(мета)
    print(f"\nОПРЕДЕЛЕНИЯ: O0 — заявка (предпродажная сделка, без технических воронок); O1 — стадия «выдано»;"
          f" O2 — файл нашего КП в сделке; контракт v4 — кат. 0 / заказ / номер / успех; строгий — сейчас в кат. 0"
          f" или живой заказ. Когорта — год предложения, иначе заявки. Зрелые — старше {STUCK_DAYS} дн.")
    print(f"воронки клиента {HOLDING}: {', '.join(sorted(клиентские_воронки, key=int)) or 'не найдены'}")

    hn = [r for r in строки if r["o0"]]
    пары = (("компания", "seg_company"), ("воронка", "seg_funnel"), ("НН-", "seg_nn"), ("КАМ", "seg_kam"))
    print(f"\nРАЗМЕТКИ {HOLDING} (заявки O0): " + "; ".join(f"{a} {sum(1 for r in hn if r[k])}" for a, k in пары)
          + " | пересечения: " + "; ".join(
              f"{a}∩{b} {sum(1 for r in hn if r[ka] and r[kb])}"
              for i, (a, ka) in enumerate(пары) for b, kb in пары[i + 1:]))

    print("\nКОНВЕРСИЯ ПРИ РАЗНЫХ ОПРЕДЕЛЕНИЯХ")
    for заголовок, выборка in когорты(строки, сегодня):
        print(f"\n— {заголовок}")
        for имя, rows in сегменты(выборка):
            print(строка_определений(имя, свод_определений(rows)))

    print("\nПРОИГРАНО ДО ТКП — стадии закрытия (ID имя: сделок), 2025–2026")
    for имя, rows in сегменты([r for r in строки if r["cohort"] in YEARS])[:2] + сегменты([r for r in строки if r["cohort"] in YEARS])[-1:]:
        с = свод_определений(rows)
        print(f"  {имя}: " + "; ".join(
            f"{sid} {pc.обезличить((мета.get(sid) or {}).get('name'), лексика)}: {n}" for sid, n in с["lost_before_reasons"]))

    print("\nКОНВЕРСИЯ ПО СУММЕ (доли; сумм в журнале нет)")
    for заголовок, выборка in когорты(строки, сегодня):
        print(f"\n— {заголовок}")
        for имя, rows in сегменты(выборка):
            print(строка_суммы(имя, свод_суммы(rows, "O1"), свод_суммы(rows, "O12")))

    print("\nЗАКАЗЫ ПОСТАВЩИКАМ СП-172 (год создания заказа; сегмент — по родительской сделке)")
    for год in YEARS:
        все = [о for о in заказы_разм if о["year"] == год]
        print(f"\n— {год}")
        for имя, xs in ((f"{HOLDING} (компания)", [о for о in все if о["seg_company"]]),
                        (f"{HOLDING} (воронка клиента)", [о for о in все if о["seg_funnel"]]),
                        ("Прочие клиенты (без Норникеля)",
                         [о for о in все if not (о["seg_company"] or о["seg_funnel"] or о["seg_nn"]) and not о["orphan"]]),
                        ("Все заказы", все)):
            print(строка_заказов(имя, свод_заказов(xs, все, сегодня, подписи_типов)))

    if YEARS:
        год0 = YEARS[0]
        номера = {n for r in строки if r["realization_only"] for n in (r.get("nn") or ())}
        к = контроль_номеров(строки, номера)
        print(f"\nТКП {год0} СЧИТАЮТСЯ ЗАКРЫТЫМИ (открытые — проигрыш)")
        print(f"  контроль по номеру НН-: карточек реализации с номером {к['cards']} (разных номеров {к['card_numbers']}),"
              f" из них номер есть у предпродажной сделки выборки {к['cards_matched']}; предпродажных сделок с номером"
              f" {к['presale_with_nn']}, совпали с карточкой: " + (", ".join(f"{a} {b}" for a, b in к["presale_matched"]) or "0"))
        for имя, rows in сегменты([r for r in строки if r["cohort"] == год0]):
            print(строка_закрытого(имя, свод_закрытого_года(rows, номера)))

    print("\nЗАКАЗЫ НОРНИКЕЛЯ ВНЕ КЛИЕНТСКОЙ ВОРОНКИ — ПРОВЕРКА")
    for год in YEARS:
        print(строка_проверки(год, проверка_заказов([о for о in заказы_разм if о["year"] == год])))

    print("\nКОНТРАКТЫ БЕЗ ЖИВОГО ЗАКАЗА (строгие; когорта)")
    for год in YEARS:
        for имя, rows in сегменты([r for r in строки if r["cohort"] == год and r["o0"]])[:1] + \
                сегменты([r for r in строки if r["cohort"] == год and r["o0"]])[-1:]:
            к = [r for r in rows if r["strict"]]
            print(f"  {год} {имя}: строгих контрактов {len(к)}, без живого заказа {sum(1 for r in к if not r['live_order'])}")


# ----------------------------------------------------------------------------- чтение

def семантика_заказов(client, категории) -> tuple[dict, int]:
    """{stageId: 'S'|'P'|'F'} по crm.status.list воронок СП-172 → (карта, воронок)."""
    out: dict[str, str] = {}
    for cid in sorted(категории):
        ent = f"DYNAMIC_{ORDER_ENTITY}_STAGE_{cid}"
        for s in client.list_paged("crm.status.list", {"filter": {"ENTITY_ID": ent}, "order": {"SORT": "ASC"}}):
            out[str(s.get("STATUS_ID"))] = str(s.get("SEMANTICS") or "P").upper()[:1] or "P"
    return out, len(категории)


def нативные_документы(client):
    """total «Предложений» Битрикса (entityTypeId 7) с начала первого года и документов
    генератора у сделок — по одному запросу; ошибка → тип ошибки."""
    out = []
    for имя, метод, params in (
            (f"предложений Битрикса (entityTypeId 7) с {YEARS[0]}-01-01", "crm.item.list",
             {"entityTypeId": 7, "filter": {">=createdTime": f"{YEARS[0]}-01-01T00:00:00"}, "select": ["id"], "start": 0}),
            ("документов генератора у сделок за всё время", "crm.documentgenerator.document.list",
             {"filter": {"entityTypeId": 2}, "select": ["id"], "start": 0})):
        try:
            env = client.call_envelope(метод, params) or {}
            out.append(f"{имя}: {int(env.get('total') or 0)}")
        except Exception as e:  # noqa: BLE001 — нет метода или прав: печатаем тип
            out.append(f"{имя}: недоступно ({type(e).__name__})")
    return out


def main() -> int:
    import config
    import kam
    import people
    from bitrix_client import BitrixClient, бюджет_портала, сводка_нагрузки

    if set(people.TECH_CATS) != set(TECH_CATS):
        print("::warning::people.TECH_CATS изменился — технические воронки взяты оттуда")
    техн = frozenset(people.TECH_CATS)
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    SINCE = pc.SINCE
    env = client.call_envelope("crm.deal.list", {"filter": {">=DATE_CREATE": SINCE}, "select": ["ID"], "start": 0}) or {}
    всего = int(env.get("total") or 0)
    env = client.call_envelope("crm.item.list", {"entityTypeId": ORDER_ENTITY, "filter": {},
                                                 "select": ["id"], "start": 0}) or {}
    ждём_заказов = int(env.get("total") or 0)
    if всего <= 0 or ждём_заказов <= 0:
        print(f"::error::не получено число сделок ({всего}) или заказов ({ждём_заказов}) — полноту не проверить")
        return 1
    rps, par = бюджет_портала()
    n = 2 + всего // 50 + 1 + всего // 150 + (всего // 50 + 1) * 6 + ждём_заказов // 50 + 1 + 80
    print(f"ожидается: сделок с {SINCE[:10]} — {всего}, заказов поставщикам — {ждём_заказов}; "
          f"оценка ≈ {n} запросов, ≈ {n * par / rps / 60:.0f} мин при {rps:g}/с без учёта задержек сети")
    for строка in нативные_документы(client):
        print(строка)

    сделки = client.list_deals_fast(filter={">=DATE_CREATE": SINCE}, select=[
        "ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE",
        "COMPANY_ID", "ASSIGNED_BY_ID", people.KAM_F, people.KAM_OLD, "OPPORTUNITY", "CURRENCY_ID",
        ПОЛЕ_RESULT, *ПОЛЯ_КП])
    print(f"прочитано сделок: {len(сделки)} из {всего}")
    if len(сделки) < всего:
        print("::error::обход сделок оборвался — итог был бы неполным")
        return 1
    print("файлы в сделках: " + "; ".join(
        f"{подпись} у {sum(1 for д in сделки if файлы(д.get(поле)))}" for поле, подпись in
        [*ПОЛЯ_КП.items(), (ПОЛЕ_RESULT, "Result file")]))

    сырые = client.list_items(ORDER_ENTITY, filter={}, select=ORDER_SELECT)
    if len(сырые) < ждём_заказов:
        print(f"::error::заказы СП-172 прочитаны не все: {len(сырые)} из {ждём_заказов}")
        return 1
    семантика, воронок = семантика_заказов(client, {str(о.get("categoryId")) for о in сырые if о.get("categoryId")})
    без_сем = sum(1 for о in сырые if str(о.get("stageId") or "") not in семантика)
    print(f"заказов поставщикам: {len(сырые)}; воронок заказов {воронок}, стадий со справочником {len(семантика)},"
          f" заказов со стадией вне справочника {без_сем}; по семантике: "
          + ", ".join(f"{k} {v}" for k, v in sorted(collections.Counter(сем_заказа(о.get('stageId'), семантика) for о in сырые).items()))
          + f"; по суффиксу :FAIL проиграно {sum(1 for о in сырые if str(о.get('stageId', '')).endswith(':FAIL'))}")

    по_выборке = {str(д["ID"]) for д in сделки}
    вне = sorted({str(о.get("parentId2")) for о in сырые
                  if str(о.get("parentId2") or "0") != "0" and str(о.get("parentId2")) not in по_выборке}, key=int)
    старые = client.deals_by_ids(вне, select=["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID",
                                              "COMPANY_ID", "DATE_CREATE", "OPPORTUNITY", "CURRENCY_ID"])
    print(f"родительских сделок заказов вне выборки: {len(вне)}, получено {len(старые)}")

    все_сделки = {str(д["ID"]): д for д in сделки} | старые
    нужны = {str(д.get("COMPANY_ID")) for д in все_сделки.values() if str(д.get("COMPANY_ID") or "0") != "0"}
    компании = client.companies_by_ids(нужны)
    print(f"компаний у сделок: запрошено {len(нужны)}, получено {len(компании)}")
    известные = {имя for _, имя in kam.CLIENT_HOLDINGS}

    def холдинг_компании(cid):
        if not cid or cid == "0":
            return "Без клиента"
        имя = компании.get(cid, "")
        h = kam.client_dir(имя)
        if h not in известные and pc.ГРУППА_ШИРЕ.search(имя):
            return HOLDING
        return h
    холдинг = {k: холдинг_компании(str(д.get("COMPANY_ID") or "0")) for k, д in все_сделки.items()}

    состав = people.roster(client)
    if not состав:
        print("::error::состав портала (user.get) не получен — разметку по КАМ не сделать")
        return 1
    отдел = {uid for uid, p in состав.items() if pc.DEPT_HOLDING in (p.get("depts") or [])}

    def кам(д):
        for f in (people.KAM_F, people.KAM_OLD, "ASSIGNED_BY_ID"):
            u = people._uid(д.get(f))
            if u:
                return u
        return ""
    кам_сделки = {str(д["ID"]) for д in сделки if кам(д) in отдел}

    мета = client.deal_stage_meta()
    cats = client.categories()
    клиентские = {c for c, имя in cats.items()
                  if kam.client_dir(str(имя)) == HOLDING or pc.ГРУППА_ШИРЕ.search(str(имя))}

    история, ошибка = pc.читать_историю(client, [str(д["ID"]) for д in сделки])
    if ошибка:
        print(f"::error::{ошибка}")
        return 1
    без = len(сделки) - len(история)
    print(f"история стадий: сделок с записями {len(история)} из {len(сделки)}, без записей {без};"
          f" записей {sum(len(v) for v in история.values())}")
    if без > len(сделки) // 100:
        print("::error::без истории стадий больше 1 % сделок — итог был бы неполным")
        return 1

    валюты = client.call("crm.currency.list", {}) or []
    курс = {}
    for x in валюты:
        try:
            курс[x.get("CURRENCY")] = float(x.get("AMOUNT") or 1) / float(x.get("AMOUNT_CNT") or 1)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    база_валюта = next((x.get("CURRENCY") for x in валюты if x.get("BASE") == "Y"), "EUR")

    def в_евро(сумма, валюта):
        try:
            v = float(сумма or 0)
        except (TypeError, ValueError):
            return None
        k = 1.0 if (валюта or база_валюта) == база_валюта else курс.get(валюта)
        return v * k if (k and v > 0) else None
    деньги_сделок = {str(д["ID"]): в_евро(д.get("OPPORTUNITY"), д.get("CURRENCY_ID")) for д in сделки}
    бюджет = pc.читать_бюджеты(в_евро)

    сегодня = dt.datetime.now(pc.MSK).date()
    заказы_v4 = [о for о in сырые if not str(о.get("stageId", "")).endswith(":FAIL")]
    строки = pc.классифицировать(сделки, история, мета, заказы_v4, холдинг, сегодня, деньги_сделок)
    разметить(строки, сделки, история, мета, сырые, семантика, клиентские, кам_сделки, бюджет, в_евро, сегодня, техн)
    пар = pc.найти_выигрыши_новой_карточкой(строки)
    print(f"карточек только реализации с найденной предпродажной парой (компания и срок): {пар}")
    по_id = {r["id"]: r for r in строки}
    заказы_разм = разметить_заказы(сырые, семантика, все_сделки, холдинг, по_id, клиентские, в_евро, бюджет)

    try:
        поля = (client.call("crm.item.fields", {"entityTypeId": ORDER_ENTITY}) or {}).get("fields") or {}
    except Exception as e:  # noqa: BLE001 — подписи необязательны, отчёт важнее
        print(f"подписи типов поставщика недоступны ({type(e).__name__})")
        поля = {}
    подписи = {str(i.get("ID")): i.get("VALUE") for i in ((поля.get(SUP_TYPE) or {}).get("items") or [])
               if i.get("ID") is not None}
    отчёт(строки, заказы_разм, мета, сегодня, подписи, клиентские)
    print(сводка_нагрузки())
    return 0


if __name__ == "__main__":
    # Трассировка с содержимым сделки в публичный журнал не уходит (как в v4).
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
