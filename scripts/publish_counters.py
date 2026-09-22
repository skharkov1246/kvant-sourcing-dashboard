#!/usr/bin/env python3
"""Точки числовых замеров в KV (ключ counters:v1) — для страницы счётчиков.

ЗАЧЕМ. Цифры замеров жили только в логах прогонов Actions: девяносто дней, и
сравнивать глазами. Теперь они лежат в lib_metric_runs, но база за Cloudflare
Access не видна, а страница портала читает KV. Этот публикатор — мост: он
переносит историю замеров в тот же namespace, откуда воркер уже читает снимок
номенклатуры и реестр поставщиков.

ЧТО ИМЕННО ПЕРЕНОСИТСЯ. Все замеры, какие есть в таблице, последними N точками
каждого. Не «замер про коды»: страница рисует любой замер, который туда попадёт,
поэтому новый счётчик заводится строкой в таблице, а не правкой публикатора.

ПОЧЕМУ СВОЙ КЛЮЧ, А НЕ ПОЛЕ В crossref:v1. Снимок номенклатуры собирается
ночным разбором и упирается в предел размера (8 МиБ) — история замеров тут
ни при чём и не должна ни делить с ним предел, ни перезаписываться вместе с ним.
Два прогона, пишущие один ключ, рано или поздно затрут работу друг друга.

Клиент Cloudflare берётся у публикатора поставщиков, список ключей — свой и
закрытый: этот публикатор не может переписать ни suppliers:v1, ни acl:v1, ни
crossref:v1.

В журнал — только агрегаты (правило 17). Их и переносим: в таблице по
построению лежат одни числа.

    SUPABASE_DB_URL=… CLOUDFLARE_ACCOUNT_ID=… CLOUDFLARE_API_TOKEN=… \
      python scripts/publish_counters.py --apply
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEY = "counters:v1"
# Сколько точек держать по каждому замеру. Год ежедневных прогонов с запасом:
# история нужна для глаза, а не для регрессии, и снимок должен оставаться
# маленьким — его читает каждый заход на страницу.
ТОЧЕК = 400
# Предел размера — свой и куда более скромный, чем у снимка номенклатуры: это
# сотни точек по десятку чисел, мегабайты здесь означали бы ошибку.
ПРЕДЕЛ = 1 << 20
КОД_ПЕРЕПОЛНЕНИЯ = 2

ИСТОРИЯ = """
select metric,
       run_key,
       to_char(measured_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as at,
       nums,
       note
  from (select m.*,
               row_number() over (partition by metric order by measured_at desc) as n
          from lib_metric_runs m) x
 where n <= %s
 order by metric, at
"""


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


def собрать(строки) -> dict:
    """Точки по замерам. Числа проходят проверку типом, а не доверием.

    Колонка nums объявлена jsonb, и положить в неё строку ничто не мешает —
    таблица публичная по назначению, а снимок уезжает на сайт, поэтому
    нечисловое значение здесь отбрасывается, а не публикуется. Оговорка (note)
    — единственное текстовое поле, и она пишется рукой автора замера.
    """
    замеры: dict[str, list] = {}
    отброшено = 0
    for metric, run_key, at, nums, note in строки:
        числа, плохих = {}, 0
        for имя, значение in (nums or {}).items():
            if isinstance(значение, bool) or not isinstance(значение, (int, float)):
                плохих += 1
                continue
            числа[имя] = значение
        отброшено += плохих
        замеры.setdefault(metric, []).append(
            {"run": run_key, "at": at, "nums": числа,
             **({"note": note} if note else {})})
    return {"version": 1, "metrics": замеры, "dropped": отброшено}


def читать_базу(dsn):
    ps = _публикатор()
    ps.require(isinstance(dsn, str) and dsn.strip(), "SUPABASE_DSN_MISSING")
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=60000")
    try:
        with conn.cursor() as cur:
            cur.execute(ИСТОРИЯ, (ТОЧЕК,))
            return cur.fetchall()
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Точки числовых замеров в KV")
    parser.add_argument("--apply", action="store_true",
                        help="записать в KV (иначе вхолостую)")
    parser.add_argument("--out", help="сохранить снимок в файл (для проверки глазами)")
    args = parser.parse_args(argv)

    ps = _публикатор()

    class CloudflareCounters(ps.Cloudflare):
        КЛЮЧИ = (KEY,)

    try:
        снимок = собрать(читать_базу(os.environ.get("SUPABASE_DB_URL")))
        raw = json.dumps(снимок, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        for имя, точки in sorted(снимок["metrics"].items()):
            последняя = точки[-1]
            print(f"замер «{имя}»: точек {len(точки)}, последняя — прогон "
                  f"{последняя['run']} от {последняя['at']}, чисел "
                  f"{len(последняя['nums'])}")
        if снимок["dropped"]:
            print(f"::warning::нечисловых значений отброшено: {снимок['dropped']}"
                  " — в lib_metric_runs.nums попало что-то кроме чисел")
        print(f"размер снимка: {len(raw)} Б из {ПРЕДЕЛ}")

        ps.require(len(raw) <= ПРЕДЕЛ, "SNAPSHOT_TOO_LARGE")
        # Пустая история — это НЕ повод писать пустой ключ: страница тогда
        # покажет «пока пусто» вместо вчерашних точек, и разобраться, потерялись
        # они или их не было, будет уже нечем.
        ps.require(bool(снимок["metrics"]), "SNAPSHOT_EMPTY")

        if args.out:
            with open(args.out, "wb") as fh:
                fh.write(raw)
            print(f"снимок сохранён в {args.out}")

        if not args.apply:
            print("вхолостую: в KV ничего не записано (--apply включает запись)")
            return 0

        cf = CloudflareCounters(os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""),
                                os.environ.get("CLOUDFLARE_API_TOKEN", ""))
        namespace = cf.namespace()
        прежний = cf.get(namespace, KEY)
        cf.put(namespace, KEY, raw)
        было = f"{len(прежний)} Б" if прежний else "пуст"
        print(f"опубликовано в KV, ключ {KEY}; прежний снимок был {было}")
        return 0
    except ps.PublishError as e:
        print(f"::error::публикация не состоялась: {e}")
        return КОД_ПЕРЕПОЛНЕНИЯ if str(e) == "SNAPSHOT_TOO_LARGE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
