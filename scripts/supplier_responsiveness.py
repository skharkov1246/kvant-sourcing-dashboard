#!/usr/bin/env python3
"""Отзывчивость поставщика: сколько писали, сколько ответили, сколько дали КП.

ЗАЧЕМ. Схема потока сделки упирается в шаг 5: отклик на запрос 20 % (176 КП из
862 запросов), и по этой цифре ничего нельзя сделать, пока она общая. Нужна
отзывчивость КАЖДОГО поставщика — тогда подбор адресатов перестаёт быть памятью
сорсера и становится списком, отсортированным по тому, кто отвечает.

ЧТО ЭТО ЗА ПРОГОН. Замер, а не запись: читает СП-166, раскладывает карточки по
поставщикам и стадиям и печатает РАСПРЕДЕЛЕНИЕ. Он отвечает на вопрос, годится
ли такая метрика вообще, прежде чем строить под неё хранение (CLAUDE.md,
правило 3: правило сначала меряют, потом применяют).

РАЗМЕР ВЫБОРКИ РЯДОМ ВСЕГДА. Медиана запросов на компанию — один (замер
20.09.2026). Доля «0 % ответов» по одному неотвеченному запросу и по сорока —
разные утверждения, и без знаменателя интерфейс покажет их одинаково
(docs/suppliers/METRIC_DICTIONARY.md).

В журнал идут ТОЛЬКО агрегаты: ни одного названия компании, ни одного номера
карточки (правило 17, репозиторий публичный).

    BITRIX_WEBHOOK_URL=… python scripts/supplier_responsiveness.py
    BITRIX_WEBHOOK_URL=… SUPABASE_DB_URL=… python scripts/supplier_responsiveness.py --apply
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stages import classify_stage  # noqa: E402

SPA_RFQ = 166
ПОЛЕ_ПОСТАВЩИКА = "ufCrm18Supplier"

# Что считать ответом. «Ответ» и «КП» — разные события, и складывать их нельзя:
# переписка без цены отклик показывает, а закупку не двигает.
ОТВЕТИЛ = {"dialog", "selected", "refused"}   # хоть как-то среагировал
ДАЛ_КП = {"selected"}                          # цена получена
МОЛЧАЛ = {"no_answer"}
НЕ_ОТПРАВЛЕН = {"new"}


def crm_id(v) -> int:
    """ID компании из поля карточки: приходит и числом, и строкой «CO_123»."""
    сырой = str(v or "")
    цифры = сырой.rsplit("_", 1)[-1] if "_" in сырой else сырой
    return int(цифры) if цифры.isdigit() else 0


def доля(часть: int, целое: int) -> str:
    """Доля со знаменателем рядом — иначе цифра врёт о своей надёжности."""
    return f"{часть}/{целое}" + (f" ({100 * часть / целое:.0f} %)" if целое else "")


# СОРОК ПРОЦЕНТОВ ЗАПРОСОВ БЕЗ ИСХОДА — замер 20.09.2026. Карточка стоит в стадии
# «Отправлен»: ни ответа, ни отказа, ни отметки «не ответил в срок». Это состояние
# ведения, и пока оно такое, любая доля ответа занижена на неизвестную величину.
# Разбор по возрасту отвечает, что это: свежие запросы, по которым ответа ещё ждут,
# или брошенные карточки. Разница определяет, кому это чинить.
БЕЗ_ИСХОДА = {"sent", "other"}
ВОЗРАСТ = ((7, "меньше недели"), (30, "1–4 недели"), (90, "1–3 месяца"),
           (365, "3–12 месяцев"), (10 ** 6, "больше года"))


def возраст_дней(создана: str, сейчас: float) -> int | None:
    """Дней с создания карточки. Дата приходит ISO с часовым поясом или пустой."""
    if not создана:
        return None
    try:
        t = datetime.fromisoformat(str(создана).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(int((сейчас - t.timestamp()) / 86400), 0)


def без_исхода_по_возрасту(карточки: list[dict]) -> collections.Counter:
    сейчас = time.time()
    out: collections.Counter[str] = collections.Counter()
    for k in карточки:
        if classify_stage(str(k.get("stageId") or "")) not in БЕЗ_ИСХОДА:
            continue
        д = возраст_дней(k.get("createdTime"), сейчас)
        if д is None:
            out["дата создания пуста"] += 1
            continue
        for предел, имя in ВОЗРАСТ:
            if д < предел:
                out[имя] += 1
                break
    return out


def разложить(карточки: list[dict]) -> dict[int, collections.Counter]:
    по_компании: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
    for k in карточки:
        cid = crm_id(k.get(ПОЛЕ_ПОСТАВЩИКА))
        bucket = classify_stage(str(k.get("stageId") or ""))
        по_компании[cid][bucket] += 1
    return по_компании


def сводка(по_компании: dict[int, collections.Counter], *, будет_запись: bool = False,
           возрасты: collections.Counter | None = None) -> None:
    возрасты = возрасты or collections.Counter()
    без_компании = по_компании.get(0, collections.Counter())
    компании = {cid: c for cid, c in по_компании.items() if cid}

    всего = sum(sum(c.values()) for c in по_компании.values())
    print(f"карточек СП-166 прочитано: {всего}")
    print(f"  из них без ссылки на компанию: {доля(sum(без_компании.values()), всего)}")
    # «Названы в карточках» и «писали» — разные вещи: карточка в стадии «Новый»
    # ещё не отправлена, и считать её запросом значит завысить знаменатель.
    писали = sum(1 for c in компании.values()
                 if sum(v for b, v in c.items() if b not in НЕ_ОТПРАВЛЕН))
    print(f"  компаний названо в карточках: {len(компании)}")
    print(f"  из них кому реально отправили: {доля(писали, len(компании))}")
    print()

    отправлено = ответили = кп = молчали = 0
    по_объёму: collections.Counter[str] = collections.Counter()
    отклики: list[tuple[int, int]] = []          # (ответов, отправлено)
    for c in компании.values():
        n = sum(v for b, v in c.items() if b not in НЕ_ОТПРАВЛЕН)
        if not n:
            continue
        a = sum(v for b, v in c.items() if b in ОТВЕТИЛ)
        q = sum(v for b, v in c.items() if b in ДАЛ_КП)
        отправлено += n
        ответили += a
        кп += q
        молчали += sum(v for b, v in c.items() if b in МОЛЧАЛ)
        отклики.append((a, n))
        по_объёму["1 запрос" if n == 1 else "2–4" if n < 5 else
                   "5–9" if n < 10 else "10–29" if n < 30 else "30 и больше"] += 1

    print("ПО ВСЕМ ОТПРАВЛЕННЫМ ЗАПРОСАМ")
    print(f"  отправлено:       {отправлено}")
    print(f"  хоть как ответил: {доля(ответили, отправлено)}")
    print(f"  дал КП:           {доля(кп, отправлено)}")
    print(f"  промолчал в срок: {доля(молчали, отправлено)}")
    print()

    print("СКОЛЬКО ЗАПРОСОВ ПРИХОДИТСЯ НА КОМПАНИЮ")
    for разряд in ("1 запрос", "2–4", "5–9", "10–29", "30 и больше"):
        n = по_объёму.get(разряд, 0)
        print(f"  {разряд:14} {n:6}  ({100 * n / max(len(отклики), 1):.1f} % компаний)")
    print()

    # ГЛАВНЫЙ ВОПРОС ЗАМЕРА: на скольких компаниях отзывчивость вообще
    # осмысленна. При одном запросе она принимает два значения, 0 или 100, и
    # сортировать по ней список адресатов бессмысленно.
    осмысленно = [(a, n) for a, n in отклики if n >= 3]
    print("НА СКОЛЬКИХ КОМПАНИЯХ ОТЗЫВЧИВОСТЬ ОСМЫСЛЕННА")
    print(f"  компаний с 3+ отправленными: {доля(len(осмысленно), len(отклики))}")
    if осмысленно:
        доли = sorted(a / n for a, n in осмысленно)
        сер = доли[len(доли) // 2]
        print(f"  медианная доля ответов у них: {100 * сер:.0f} %")
        print(f"  не ответили ни разу:          {доля(sum(1 for d in доли if d == 0), len(доли))}")
        print(f"  ответили всегда:              {доля(sum(1 for d in доли if d == 1), len(доли))}")
    print()
    if возрасты:
        всего_без = sum(возрасты.values())
        print("ЗАПРОСЫ БЕЗ ЗАФИКСИРОВАННОГО ИСХОДА — ПО ВОЗРАСТУ")
        print(f"  всего таких карточек: {всего_без}")
        for _, имя in ВОЗРАСТ:
            if возрасты.get(имя):
                print(f"    {имя:18} {доля(возрасты[имя], всего_без)}")
        if возрасты.get("дата создания пуста"):
            print(f"    {'дата пуста':18} {возрасты['дата создания пуста']}")
        print("    Свежие — это ожидание; старые — брошенные карточки, и это")
        print("    чинится ведением, а не кодом. Разница в том, кому чинить.")
        print()
    if not будет_запись:
        print("Замер, записи не было. Хранение метрики строится после него, а не до.")


# ── ЗАПИСЬ В sup_fact ────────────────────────────────────────────────────────
# ДОЛЯ НЕ ПИШЕТСЯ ЧИСЛОМ. Хранятся числитель и знаменатель; долю считает тот, кто
# показывает. Иначе «50 %» из двух запросов и из сорока лягут в базу одинаково, и
# восстановить разницу будет неоткуда.
МЕТОД = "rfq_stats_by_stage"
МЕТОД_ВЕРСИЯ = "1"
ПОЛЕ = "rfq_stats"

# Гейт приёмки: если по твёрдому ключу портала не нашлось почти ничего, значит
# сведение и замер разошлись, и писать такое в базу нельзя.
МИН_СОПОСТАВЛЕНО = 0.5


def статистика(c: collections.Counter) -> dict:
    """Счётчики одной компании в том виде, в каком они лягут в sup_fact."""
    отправлено = sum(v for b, v in c.items() if b not in НЕ_ОТПРАВЛЕН)
    return {
        "sent": отправлено,
        "answered": sum(v for b, v in c.items() if b in ОТВЕТИЛ),
        "quoted": sum(v for b, v in c.items() if b in ДАЛ_КП),
        "silent": sum(v for b, v in c.items() if b in МОЛЧАЛ),
        # Ни ответа, ни отказа, ни отметки «не ответил в срок»: карточка стоит.
        # Это состояние ведения, и прятать его в знаменателе нельзя.
        "no_outcome": отправлено - sum(v for b, v in c.items()
                                       if b in ОТВЕТИЛ or b in МОЛЧАЛ),
        "cards": sum(c.values()),
    }


def записать(dsn: str, по_компании: dict[int, collections.Counter], run_id: str) -> int:
    """Одной транзакцией: новые факты, прежние — в superseded."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=15000")
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("select value, sup_id from sup_identifier "
                        "where kind = 'bitrix' and status <> 'rejected'")
            по_ключу = {}
            for значение, sup_id in cur.fetchall():
                if str(значение).isdigit():
                    по_ключу[int(значение)] = sup_id

            строки = []
            сопоставлено = 0
            for cid, c in по_компании.items():
                if not cid:
                    continue
                sup_id = по_ключу.get(cid)
                if not sup_id:
                    continue
                st = статистика(c)
                if not st["sent"]:
                    continue
                сопоставлено += 1
                строки.append((
                    "entity", sup_id, ПОЛЕ, json.dumps(st, ensure_ascii=False),
                    "stated", "bitrix", МЕТОД, МЕТОД_ВЕРСИЯ, run_id))

            компаний = sum(1 for cid, c in по_компании.items()
                           if cid and sum(v for b, v in c.items() if b not in НЕ_ОТПРАВЛЕН))
            доля_сопоставленных = сопоставлено / max(компаний, 1)
            print(f"сопоставлено с реестром по ключу портала: "
                  f"{доля(сопоставлено, компаний)}")
            if доля_сопоставленных < МИН_СОПОСТАВЛЕНО:
                raise RuntimeError(
                    f"гейт не сошёлся: сопоставлено {100 * доля_сопоставленных:.0f} %, "
                    f"нужно от {100 * МИН_СОПОСТАВЛЕНО:.0f} % — замер и реестр разошлись")

            psycopg2.extras.execute_values(cur, """
                insert into sup_fact
                  (subject_kind, subject_id, field, value, status, source_type,
                   method, method_ver, run_id)
                values %s""",
                строки,
                template="(%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)", page_size=500)

            # Прежние факты того же поля — в superseded, а не удалять: история
            # отзывчивости и есть то, ради чего метрику заводят.
            cur.execute("""update sup_fact set status = 'superseded'
                            where field = %s and run_id <> %s and status <> 'superseded'""",
                        (ПОЛЕ, run_id))
            устарело = cur.rowcount
        conn.commit()
    except Exception as e:                      # noqa: BLE001
        conn.rollback()
        print(f"ЗАПИСЬ ОТМЕНЕНА, откат выполнен: {e}", file=sys.stderr)
        return 4
    finally:
        conn.close()
    print(f"\nЗАПИСАНО, ключ прогона {run_id}: фактов {len(строки)}, "
          f"прежних помечено superseded {устарело}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Отзывчивость поставщиков")
    parser.add_argument("--apply", action="store_true",
                        help="записать факты в sup_fact (иначе только замер)")
    args = parser.parse_args()

    url = os.environ.get("BITRIX_WEBHOOK_URL", "")
    if not url:
        print("нет переменной BITRIX_WEBHOOK_URL", file=sys.stderr)
        return 2
    from bitrix_client import BitrixClient

    print("выгрузка карточек запросов (СП-166)…", flush=True)
    карточки = BitrixClient(url).list_items(
        SPA_RFQ, select=["id", "stageId", "createdTime", ПОЛЕ_ПОСТАВЩИКА])
    по_компании = разложить(карточки)
    сводка(по_компании, будет_запись=args.apply,
           возрасты=без_исхода_по_возрасту(карточки))
    if not args.apply:
        return 0
    dsn = os.environ.get("SUPABASE_DB_URL", "")
    if not dsn:
        print("запись запрошена, но нет SUPABASE_DB_URL", file=sys.stderr)
        return 2
    return записать(dsn, по_компании, f"rfq-{int(time.time())}")


if __name__ == "__main__":
    raise SystemExit(main())
