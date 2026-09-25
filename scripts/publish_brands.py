#!/usr/bin/env python3
"""Карточки брендов и сводка «код · цена · бренд · поставщик» в KV.

ЗАЧЕМ. Этап 8.1 (docs/suppliers/IMPLEMENTATION_PLAN.md) и распоряжение владельца
24.09.2026: страница на портале за Cloudflare Access вместо выгрузок из Supabase
руками. Сборка — library/brands.py, запросы — library/codes_sql.py.

КЛЮЧИ СВОИ И ЗАКРЫТЫМ СПИСКОМ: brands:v1, brands:links:v1, brands:pairs:v1,
brands:codes:00…15
(library/brands.ВСЕ_КЛЮЧИ). Клиент Cloudflare — публикатора поставщиков, как у
publish_crossref.py: этот публикатор не может переписать ни suppliers:v1, ни
crossref:v1, ни acl:v1.

ПОЧЕМУ В ТОМ ЖЕ ПРОГОНЕ, ЧТО И crossref:v1. Шаг стоит в
.github/workflows/suppliers-quotes.yml сразу за публикацией номенклатуры: оба
снимка собираются по базе после одного и того же ночного разбора, иначе
/brands и /nomenclature показали бы разные котировки.

ИМЕНА КОМПАНИЙ — ИЗ БИТРИКСА. display_name реестра — сжатый ключ, а у части
карточек в базе только номер компании портала. Сборщик берёт названия по
номерам из Битрикса (crm.company.list) и названия брендов карточки запроса из
смарт-процесса 176. Нет вебхука или портал не ответил — снимок собирается с
номерами и пометкой, публикация не срывается.

В ЖУРНАЛ — ТОЛЬКО АГРЕГАТЫ (правило 17): числа брендов, поставщиков, кодов,
размеры ключей и заполненность по полям. Ни имени бренда с числами, ни имени
компании, ни кода.

    SUPABASE_DB_URL=… BITRIX_WEBHOOK_URL=… CLOUDFLARE_ACCOUNT_ID=… \\
      CLOUDFLARE_API_TOKEN=… python scripts/publish_brands.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import brands, codes_sql, company_names  # noqa: E402

КОД_ПЕРЕПОЛНЕНИЯ = 2
# Запас до предела: больше этой доли — предупреждение в журнал. Не отказ: снимок
# ещё влезает, но следующий рост его уронит, и узнать об этом надо заранее.
ЗАПАС = 0.80
# Предел одного запроса. Задаётся в строке подключения, а не через SET
# (CLAUDE.md, правило 9): SET внутри транзакции откатывается вместе с ней, а
# дефолт пула Supabase — две минуты. На синтетике объёма живой базы самый
# тяжёлый запрос (бренды) шёл 27 с; пятнадцать минут — запас на медленный диск.
ПРЕДЕЛ_ЗАПРОСА_МС = 15 * 60 * 1000


def _публикатор():
    """Модуль публикатора поставщиков как библиотека (scripts/ — не пакет)."""
    spec = importlib.util.spec_from_file_location(
        "kvant_publish_suppliers", ROOT / "scripts" / "publish_suppliers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def читать_реестр(cur):
    """Реестр брендов из базы либо None — тогда работаем по файлу.

    Читатель один на публикаторы /brands и /nomenclature: brands.читать_реестр."""
    return brands.читать_реестр(cur)


def читать_базу(dsn, карта):
    """Все запросы — в одной транзакции только для чтения: один снимок базы.

    Разбор, идущий параллельно, снимает и вставляет строки цен по файлам; в
    разных транзакциях бренды и коды посчитались бы по разным состояниям.

    Возвращает (коды, каталог, время, реестр). Реестр брендов есть — запросы
    берут карту ключей из него (вид lib_brand_map), а не из переданной карты
    словаря-файла."""
    ps = _публикатор()
    ps.require(isinstance(dsn, str) and dsn.strip(), "SUPABASE_DSN_MISSING")
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options=f"-c statement_timeout={ПРЕДЕЛ_ЗАПРОСА_МС}")
    conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
    коды, каталог, время = {}, {}, {}
    try:
        with conn.cursor() as cur:
            cur.execute(codes_sql.SETTINGS)
            реестр = читать_реестр(cur)
            имена = company_names.вид_имён_есть(cur)
            for имя, sql in codes_sql.запросы(карта, из_реестра=реестр is not None,
                                              имена=имена).items():
                t = time.monotonic()
                cur.execute(sql)
                колонки = [d[0] for d in cur.description]
                коды[имя] = [dict(zip(колонки, r)) for r in cur.fetchall()]
                время[имя] = time.monotonic() - t
            for имя, sql in brands.КАТАЛОГ_ЗАПРОСЫ.items():
                t = time.monotonic()
                cur.execute(sql)
                каталог[имя] = cur.fetchall()
                время["каталог." + имя] = time.monotonic() - t
        conn.rollback()
        return коды, каталог, время, реестр
    finally:
        conn.close()


def имена_из_битрикса(коды, url):
    """Названия компаний по номерам портала и брендов карточки по номерам СП-176.

    Сбой портала — не сбой публикации: снимок собирается с номерами. В журнал —
    только класс ошибки: текст ответа портала мог бы нести адрес вебхука."""
    if not url:
        print("::warning::нет BITRIX_WEBHOOK_URL — имена компаний и брендов карточки "
              "останутся номерами")
        return {}, {}
    from bitrix_client import BitrixClient

    ключи = set()
    for r in коды.get("suppliers", []):
        ключи.update(str(r.get("portal_keys") or "").split())
    for r in коды.get("match", []):
        ключи.update(str(r.get("ключи_портала") or "").split())
    for r in коды.get("pairs", []):
        ключи.update(str(r.get("portal_keys") or "").split())
    компании, бренды_карточки = {}, {}
    try:
        bx = BitrixClient(url)
        for k, имя in bx.companies_by_ids(ключи).items():
            # Клиент подставляет «company#N», когда названия нет: это не имя.
            if имя and not str(имя).startswith("company#"):
                компании[str(k)] = str(имя).strip()
        нужны = {str(r.get("brand_id")) for r in коды.get("card_brands", [])}
        if нужны:
            for it in bx.list_items(176, select=["id", "title"]):
                ид = str(it.get("id"))
                if ид in нужны and (it.get("title") or "").strip():
                    бренды_карточки[ид] = it["title"].strip()
    except Exception as failure:  # noqa: BLE001 — портал не должен ронять публикацию
        print(f"::warning::Битрикс не ответил ({type(failure).__name__}) — часть имён "
              "останется номерами")
    print(f"имён компаний из Битрикса: {len(компании)} из {len(ключи)} номеров; "
          f"имён брендов карточки: {len(бренды_карточки)}")
    return компании, бренды_карточки


def печать_итогов(снимки, размеры, предел):
    с = снимки[brands.КЛЮЧ]
    print(f"брендов: {len(с['brands'])}, поставщиков: {len(с['suppliers'])}, "
          f"пар «бренд × поставщик»: {len(снимки[brands.КЛЮЧ_ПАР]['pairs'])}, "
          f"кодов с ценой КП: {len(снимки[brands.КЛЮЧ_СВЯЗЕЙ]['codes'])}")
    print(f"словарь брендов: записей {с['dict']['records']}, написаний сведено к ключу "
          f"{с['dict']['spellings_mapped']}, спорных написаний {с['dict']['ambiguous']}")
    for п in с["totals"]["tiles"]:
        print(f"  {п['label']}: {п['value'] if п['value'] is not None else 'нет в итогах'}")
    if not с["totals"]["locale_ok"]:
        print("::warning::проверка локали базы не пройдена: ключи кодов считаются не так, "
              "как на живой базе (CLAUDE.md, правило 21а)")
    о = brands.итоги_облака(с)
    # Облако: только числа — ни имени бренда, ни ключа (правило 17).
    print(f"облако: ключей {о['keys']}, слов {о['words']}; не марка {о['not_brand']} ("
          + ", ".join(f"{п} {n}" for п, n in о["reasons"].items())
          + f"); сведено ключей-дублей {о['merged_keys']} в {о['merged_words']} слов")
    print(f"облако, первые {brands.ОБЛАКО_СЛОВ}: до правки не марок {о['top_not_brand_before']}, "
          f"дублей {о['top_dup_before']}; после — словарных слов {о['top_dict_after']} из {о['top']}")
    print(f"облако, у скольких хуже: марок словаря без места в облаке {о['dict_outside']}; "
          f"записей словаря с узкой пометкой {о['dict_not_brand']}; ключей в списке поиска "
          f"{о['keys']} (ни один не убран)")
    if о["dict_outside"]:
        print("::warning::марка словаря выпала из облака — сведение потеряло ключ")
    н = с["coverage"]["undefined"]
    print(f"не определено: бренд без ключа словаря {н['brands_without_dict_key']} из "
          f"{н['brands']}; деталь без узла {н['parts_without_unit']} из {н['parts']}; "
          f"поставщик без имени {н['suppliers_without_name']} из {н['suppliers']}; "
          f"бренд карточки без имени {н['card_brands_without_name']} из {н['card_brands']}")
    for имя, вселенная in с["coverage"]["universes"].items():
        print(f"заполненность карточки, {'бренды с деталями в каталоге' if имя == 'catalog' else 'все бренды'}"
              f" ({вселенная['total']}):")
        for п in вселенная["fields"]:
            print(f"  {п['label']:<42} {п['filled']:>6} из {п['total']:<6} {п['pct']:>5.1f} %  {п['status']}")
    for ключ, n in размеры.items():
        доля = n / предел
        print(f"размер {ключ}: {n} Б ({n / 1024 / 1024:.2f} МиБ, {100 * доля:.1f} % предела)")
        if доля > ЗАПАС:
            print(f"::warning::{ключ} занимает {100 * доля:.0f} % предела: запас меньше "
                  f"{100 * (1 - ЗАПАС):.0f} %, при росте снимок перестанет помещаться")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Карточки брендов и сводка кодов в KV")
    parser.add_argument("--apply", action="store_true", help="записать в KV (иначе вхолостую)")
    parser.add_argument("--out", help="сохранить ключи файлами в эту папку (проверка глазами)")
    args = parser.parse_args(argv)

    ps = _публикатор()

    class CloudflareBrands(ps.Cloudflare):
        КЛЮЧИ = brands.ВСЕ_КЛЮЧИ

    try:
        словарь = brands.читать_файл(brands.ФАЙЛ_СЛОВАРЯ)
        карта, _, _ = brands.карта_словаря(словарь)
        # Записи словаря, которые не бренд (указание, несколько, описание, номер —
        # library/oem_kind.py): их части ячейки бренда не дают, а реестр базы,
        # засеянный до пометок, брендами их не показывает.
        разложение = brands.разложение_словаря(словарь)
        не_бренды = brands.ключи_не_брендов(словарь)
        коды, каталог, время, реестр = читать_базу(os.environ.get("SUPABASE_DB_URL"),
                                                   sorted(карта.items()))
        print("время запросов: " + ", ".join(f"{k} {v:.1f} с" for k, v in время.items()))
        if реестр:
            реестр = brands.реестр_без_не_брендов(реестр, не_бренды)
            словарь = реестр["словарь"]
            print(f"ключ бренда: реестр базы (брендов {реестр['брендов']}, написаний "
                  f"{реестр['написаний']}, элементов СП-176 с ключом {len(реестр['карточка'])})")
        else:
            print("ключ бренда: словарь-файл " + brands.ФАЙЛ_СЛОВАРЯ
                  + " (реестра брендов в базе нет или он пуст)")
        имена_портала, имена_брендов = имена_из_битрикса(коды, os.environ.get("BITRIX_WEBHOOK_URL"))
        if реестр:
            # Имя из Битрикса — свежее; из реестра — то, что было при засеве.
            имена_брендов = {**реестр["имена"], **имена_брендов}
        собран = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        снимки = brands.собрать(
            коды, каталог,
            словарь=словарь,
            ключи_карточки=(реестр or {}).get("карточка"),
            разложение=разложение,
            цепочка=brands.читать_файл(brands.ФАЙЛ_ЦЕПОЧКИ),
            атлас=brands.читать_файл(brands.ФАЙЛ_АТЛАСА),
            каналы=brands.читать_файл(brands.ФАЙЛ_КАНАЛОВ),
            ряды=brands.читать_файл(brands.ФАЙЛ_РЯДОВ),
            имена_портала=имена_портала, имена_брендов=имена_брендов, собран=собран)
        сырые = {k: json.dumps(v, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                 for k, v in снимки.items()}
        печать_итогов(снимки, {k: len(v) for k, v in сырые.items()}, ps.MAX_BYTES)

        # РАЗМЕР — ГЕЙТ, А НЕ ПРИМЕЧАНИЕ: усечённая страница молча уносит часть
        # данных, отказ громче.
        ps.require(all(len(v) <= ps.MAX_BYTES for v in сырые.values()), "SNAPSHOT_TOO_LARGE")
        ps.require(bool(снимки[brands.КЛЮЧ]["brands"]), "SNAPSHOT_EMPTY")

        if args.out:
            папка = Path(args.out)
            папка.mkdir(parents=True, exist_ok=True)
            for k, raw in сырые.items():
                (папка / (k.replace(":", "_") + ".json")).write_bytes(raw)
            print(f"ключи сохранены в {папка}")

        if not args.apply:
            print("вхолостую: в KV ничего не записано (--apply включает запись)")
            return 0

        cf = CloudflareBrands(os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
                              os.environ.get("CLOUDFLARE_API_TOKEN", ""))
        namespace = cf.namespace()
        # ПОРЯДОК ЗАПИСИ: корзины кодов, потом связи, сводка последней. Сводку
        # читают первой; пока она старая, страница не ведёт в корзины, которых
        # ещё нет, — а новые корзины со старыми ссылками совместимы.
        for k in brands.КЛЮЧИ_КОРЗИН + (brands.КЛЮЧ_ПАР, brands.КЛЮЧ_СВЯЗЕЙ, brands.КЛЮЧ):
            cf.put(namespace, k, сырые[k])
        print(f"опубликовано в KV: {len(сырые)} ключей ({brands.КЛЮЧ}, {brands.КЛЮЧ_СВЯЗЕЙ}, "
              f"{brands.КЛЮЧ_ПАР}, корзин кодов {brands.КОРЗИН})")
        return 0
    except ps.PublishError as e:
        print(f"::error::публикация не состоялась: {e}")
        return КОД_ПЕРЕПОЛНЕНИЯ if str(e) == "SNAPSHOT_TOO_LARGE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
