"""Ежедневное пополнение библиотеки: только то, что появилось или изменилось.

Распоряжение владельца 24.09.2026: «каждый новый запрос, новый спрос и
предложение — ежедневно. База будет пополняться ежедневно». Сплошной обход
портала для этого не годится: он стоит тысячи запросов и именно на нём портал
отказывал (CLAUDE.md, «Битрикс не перегружать»). Ежедневный проход читает
только сделки и карточки запросов, созданные или изменённые после прошлого
УСПЕШНОГО прохода, — десятки запросов, а не тысячи.

ОТМЕТКА ПРОХОДА живёт в базе, в общем журнале замеров lib_metric_runs (схема
уже есть, миграция не нужна): замер «инкремент_сделки» / «инкремент_запросы»,
в nums — только числа (правило 17): начало прохода (секунды эпохи) и наибольший
номер сущности на момент начала. Отметку пишет ПОСЛЕДНИЙ шаг прогона, когда
разбор и распознавание обоих источников прошли: упавший прогон отметку не
двигает, и следующий день перечитает то же окно.

ДВА ПРИЗНАКА НОВИЗНЫ, А НЕ ОДИН. Изменённое — фильтром по дате изменения на
портале. Новое — ещё и по номеру выше прошлого наибольшего: фильтр по дате у
карточек запросов уже однажды молча съедал две трети записей (замер 21.09.2026,
createdTime), и страховка номером стоит одну-две страницы.

Команды (прогон library-daily.yml):
  python library/increment.py начало    — время начала и наибольшие номера
                                          (два запроса к порталу) → $GITHUB_ENV
  python library/increment.py окно      — какое окно возьмёт проход (только база)
  python library/increment.py отметка   — записать отметку успешного прохода
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

#: Источник → имя замера в lib_metric_runs.
ЗАМЕР = {"deals": "инкремент_сделки", "rfq": "инкремент_запросы"}
#: Перекрытие окна, часов: часы портала и раннера расходятся, а карточку,
#: изменённую в секунду начала прошлого прохода, терять нельзя. Повторно
#: прочитанный файл отсеется по lib_files, потерянный не вернётся.
ЗАПАС_Ч = 2
#: Окно первого прохода, пока отметки нет, дней.
ПЕРВЫЙ_ДНЕЙ = 3
#: Часовой пояс фильтров портала — как у сплошного обхода (indexer.collect_refs).
МСК = timezone(timedelta(hours=3))


def окно(отметка: dict | None, сейчас: datetime, *, запас_ч: float = ЗАПАС_Ч,
         первый_дней: int = ПЕРВЫЙ_ДНЕЙ) -> tuple[datetime, int]:
    """(изменённые начиная с, новые с номером больше) по прошлой отметке.

    Без отметки — последние `первый_дней` дней и без страховки номером: номер 0
    означал бы «все записи», то есть сплошной обход, от которого и уходим.
    """
    if отметка:
        try:
            начало = datetime.fromtimestamp(float(отметка["начало"]), tz=timezone.utc)
            после_id = int(отметка.get("после_id") or 0)
            return начало - timedelta(hours=запас_ч), max(0, после_id)
        except (KeyError, TypeError, ValueError):
            pass
    return сейчас - timedelta(days=первый_дней), 0


def для_фильтра(момент: datetime) -> str:
    """Дата для фильтра портала: ISO с часовым поясом, как у сплошного обхода."""
    return момент.astimezone(МСК).strftime("%Y-%m-%dT%H:%M:%S+03:00")


def объединить(*списки: list[dict], ключ: str = "id") -> list[dict]:
    """Записи нескольких выборок без повторов по номеру, по возрастанию номера."""
    по_номеру: dict[int, dict] = {}
    for список in списки:
        for x in список or []:
            try:
                по_номеру.setdefault(int(x[ключ]), x)
            except (KeyError, TypeError, ValueError):
                continue
    return [по_номеру[k] for k in sorted(по_номеру)]


def прочитать(cur, source: str) -> dict | None:
    """Последняя отметка источника или None. Нет таблицы — тоже None, вслух."""
    import psycopg2
    try:
        cur.execute("select nums from lib_metric_runs where metric = %s "
                    "order by measured_at desc limit 1", (ЗАМЕР[source],))
    except psycopg2.Error as e:
        cur.connection.rollback()
        print(f"::warning::отметка прохода не прочитана ({type(e).__name__}) — "
              f"окно первого прохода, {ПЕРВЫЙ_ДНЕЙ} дн.", flush=True)
        return None
    row = cur.fetchone()
    if not row:
        return None
    nums = row[0]
    return json.loads(nums) if isinstance(nums, str) else nums


def записать(cur, source: str, начало: float, после_id: int, run_key: str) -> None:
    cur.execute(
        "insert into lib_metric_runs (metric, run_key, nums, note) values (%s, %s, %s::jsonb, %s) "
        "on conflict (metric, run_key) do update set nums = excluded.nums, "
        "measured_at = now(), note = excluded.note",
        (ЗАМЕР[source], run_key,
         json.dumps({"начало": int(начало), "после_id": int(после_id)}),
         "отметка ежедневного прохода: изменённое после «начало», новое с номером выше «после_id»"))


def окно_из_базы(source: str) -> tuple[datetime, int]:
    import indexer
    conn = indexer.connect()
    with conn.cursor() as cur:
        отметка = прочитать(cur, source)
    conn.close()
    с, после_id = окно(отметка, datetime.now(timezone.utc))
    print(f"ежедневный проход ({source}): изменённое с {для_фильтра(с)}"
          + (f", новое с номером выше {после_id}" if после_id else
             " — отметки нет, окно первого прохода") , flush=True)
    return с, после_id


def _наибольшие_номера() -> dict[str, int]:
    import indexer
    j = indexer.bx("crm.deal.list", {"select": ["ID"], "order": {"ID": "DESC"}, "start": -1})
    сделки = int(((j.get("result") or [{}])[0] or {}).get("ID") or 0)
    карточки = indexer.bx_max_id("crm.item.list", {"entityTypeId": indexer.SPA_RFQ})
    return {"deals": сделки, "rfq": карточки}


def main(argv: list[str]) -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    команда = argv[1] if len(argv) > 1 else ""
    if команда == "начало":
        начало = int(datetime.now(timezone.utc).timestamp())
        номера = _наибольшие_номера()
        if not номера["deals"] or not номера["rfq"]:
            print("::error::наибольший номер не прочитан — отметка вышла бы нулевой, "
                  "а ноль значит «все записи»", flush=True)
            return 2
        строки = [f"INCREMENT_START={начало}",
                  f"INCREMENT_MAX_DEALS={номера['deals']}",
                  f"INCREMENT_MAX_RFQ={номера['rfq']}"]
        print("\n".join(строки), flush=True)
        if os.getenv("GITHUB_ENV"):
            with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as fh:
                fh.write("\n".join(строки) + "\n")
        return 0
    if команда == "окно":
        for source in ЗАМЕР:
            окно_из_базы(source)
        return 0
    if команда == "отметка":
        try:
            начало = float(os.environ["INCREMENT_START"])
            макс = {"deals": int(os.environ["INCREMENT_MAX_DEALS"]),
                    "rfq": int(os.environ["INCREMENT_MAX_RFQ"])}
        except (KeyError, ValueError):
            print("::error::нет INCREMENT_START / INCREMENT_MAX_* — шаг «начало» не прошёл",
                  flush=True)
            return 2
        run_key = os.getenv("GITHUB_RUN_ID") or str(int(начало))
        import indexer
        conn = indexer.connect()
        with conn.cursor() as cur:
            for source in ЗАМЕР:
                записать(cur, source, начало, макс[source], run_key)
        conn.commit()
        conn.close()
        print(f"отметка записана: начало {int(начало)}, номера {макс}", flush=True)
        return 0
    print("команда: начало | окно | отметка", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
