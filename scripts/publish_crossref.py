#!/usr/bin/env python3
"""Снимок перекрёстной системы «позиция ↔ поставщик» в KV (ключ crossref:v1).

ЗАЧЕМ ОТДЕЛЬНЫЙ КЛЮЧ, А НЕ ПОЛЕ В suppliers:v1. Реестр поставщиков меняется
сведением (раз в неделю), а котировки — каждым ночным разбором. Один ключ
означал бы перезапись реестра при каждом разборе и наоборот: два прогона,
пишущие один ключ, рано или поздно затрут работу друг друга.

ПОЧЕМУ ОДИН КЛЮЧ НА ОБЕ КАРТОЧКИ. Это один граф, прочитанный с двух сторон.
Разойдись две сборки — и страница позиции покажет поставщика, которого страница
поставщика не знает.

Клиент Cloudflare берётся у публикатора поставщиков: второй стек разбора
ответов, повторов и сверки записи был бы второй же поверхностью для ошибок.
Список ключей при этом СВОЙ и по-прежнему закрытый — этот публикатор не может
переписать ни suppliers:v1, ни acl:v1.

В журнал — только агрегаты (правило 17): счётчики, размер снимка, свои
константы. Ни наименований позиций, ни названий компаний.

    SUPABASE_DB_URL=… CLOUDFLARE_ACCOUNT_ID=… CLOUDFLARE_API_TOKEN=… \
      python scripts/publish_crossref.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from library import crossref  # noqa: E402

KEY = "crossref:v1"
# Код возврата для переполнения предела: прогон обязан покраснеть именно на нём, а
# на сбое сети — нет. Разбор этого кода — в .github/workflows/suppliers-quotes.yml.
КОД_ПЕРЕПОЛНЕНИЯ = 2


def _публикатор():
    """Модуль публикатора поставщиков как библиотека.

    Обычным импортом он не берётся: scripts/ — не пакет, и делать его пакетом
    ради одного класса значит менять пути у всего, что там лежит.
    """
    spec = importlib.util.spec_from_file_location(
        "kvant_publish_suppliers", ROOT / "scripts" / "publish_suppliers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def читать_базу(dsn):
    ps = _публикатор()
    ps.require(isinstance(dsn, str) and dsn.strip(), "SUPABASE_DSN_MISSING")
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=180000")
    try:
        with conn.cursor() as cur:
            наборы = []
            for sql in (crossref.ПРЕДЛОЖЕНИЯ_SQL, crossref.КАТАЛОГ_SQL,
                        crossref.АНАЛОГИ_SQL, crossref.МАШИНЫ_SQL,
                        crossref.ИЗГОТОВИТЕЛИ_SQL, crossref.СПРОС_SQL):
                cur.execute(sql, (crossref.FEED,))
                наборы.append(cur.fetchall())
        return наборы
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Снимок перекрёстной системы в KV")
    parser.add_argument("--apply", action="store_true", help="записать в KV (иначе вхолостую)")
    parser.add_argument("--out", help="сохранить снимок в файл (для проверки глазами)")
    args = parser.parse_args(argv)

    ps = _публикатор()

    class CloudflareCross(ps.Cloudflare):
        КЛЮЧИ = (KEY,)

    try:
        (предложения, каталог, аналоги, машины, изготовители,
         спрос) = читать_базу(os.environ.get("SUPABASE_DB_URL"))
        собран = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        снимок = crossref.собрать(предложения, каталог, аналоги, машины,
                                  изготовители, спрос, собран=собран)
        raw = json.dumps(снимок, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        t = снимок["totals"]
        print(f"позиций: {t['positions']}, из них с выбором из двух компаний: "
              f"{t['with_choice']}, сравнимых по цене: {t['comparable']}")
        print(f"в каталоге: {t['in_catalog']} — только у них будут аналоги, "
              "машина и изготовитель")
        print(f"компаний: {t['companies']}, сведено с реестром: "
              f"{t['companies_resolved']} — у остальных карточка не откроется")
        # Позиции без выбора — это и есть список работы по вторым поставщикам;
        # число выводится вычитанием, а не отдельным счётчиком. Отдельный
        # счётчик «без выбора и со спросом» уже был и выдал ровно ту же цифру:
        # спрос есть у всех позиций, потому что артикулы котировок берутся из
        # спецификаций. Печатается то, что действительно сужает: позиции, которых
        # в спросе нет вовсе.
        print(f"без выбора: {t['positions'] - t['with_choice']}"
              " — по ним и надо запрашивать вторых поставщиков")
        print(f"нет в спросе вовсе: {t.get('no_demand', 0)}"
              " — котировка на артикул из-за пределов наших спецификаций")
        print(f"предложений всего: {t['offers']}; размер снимка: {len(raw)} Б "
              f"({len(raw) / 1024 / 1024:.2f} МиБ из {ps.MAX_BYTES // 1024 // 1024})")

        # РАЗМЕР — ГЕЙТ, А НЕ ПРИМЕЧАНИЕ. Позиции растут с каждым прогоном
        # разбора, и однажды снимок перестанет помещаться. Отказ здесь громче,
        # чем усечённая страница: усечение молча уносит часть номенклатуры.
        ps.require(len(raw) <= ps.MAX_BYTES, "SNAPSHOT_TOO_LARGE")
        ps.require(t["positions"] > 0, "SNAPSHOT_EMPTY")

        if args.out:
            with open(args.out, "wb") as fh:
                fh.write(raw)
            print(f"снимок сохранён в {args.out}")

        if not args.apply:
            print("вхолостую: в KV ничего не записано (--apply включает запись)")
            return 0

        cf = CloudflareCross(os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
                             os.environ.get("CLOUDFLARE_API_TOKEN", ""))
        namespace = cf.namespace()
        прежний = cf.get(namespace, KEY)
        cf.put(namespace, KEY, raw)
        print(f"опубликовано в KV, ключ {KEY}; прежний снимок был "
              f"{'' if прежний else 'пуст'}{len(прежний) if прежний else ''}"
              f"{' Б' if прежний else ''}")
        return 0
    except ps.PublishError as e:
        print(f"::error::публикация не состоялась: {e}")
        # РАЗНЫЕ КОДЫ ВОЗВРАТА — РАЗНЫЕ БЕДЫ, И ГЛУШИТЬ ИХ ОДИНАКОВО НЕЛЬЗЯ.
        # Шаг публикации стоит под continue-on-error, и это верно для
        # недоступного Cloudflare: разбор уже сделан, терять его из-за чужого
        # сбоя незачем. Но тем же глушителем накрывало и переполнение предела, а
        # это беда другой природы: она не пройдёт сама, страница навсегда
        # останется на вчерашнем снимке, и прогон при этом отчитается успехом.
        # Позиции растут с каждым разбором, поэтому случай не гипотетический:
        # запас формы — 1,35 от нынешнего объёма (см. crossref.ПОТОЛОК_ПОЗИЦИЙ).
        return КОД_ПЕРЕПОЛНЕНИЯ if str(e) == "SNAPSHOT_TOO_LARGE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
