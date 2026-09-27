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

ПИСЬМА ПОСТАВЩИКОВ — СВОЯ ОТМЕТКА И СВОЙ ШАГ (группа mail-supplier,
library/mail_source.письма_прохода). Ручная пачка писем (library-mail.yml) идёт
от старых к новым, и входящие этой недели не попадали в свод, пока она до них
не дойдёт. Ежедневный проход берёт письма с номером выше СВОЕЙ отметки
(«инкремент_письма_поставщиков»), не больше MAIL_DAILY_LIMIT за раз; первый
проход, пока отметки нет, — не глубже MAIL_FIRST_DAYS последних дней, чтобы не
перекрыть идущую пачку. С отметкой пачки («почта:mail-supplier») они не
пересекаются: имена замеров разные, а письмо, разобранное одним, другой
отсеивает по lib_files. Отметку писем пишет СВОЙ последний шаг: упавший разбор
писем её не двигает и не держит отметку сделок и карточек — та пишется раньше.

Режим писем — MAIL_DAILY: apply — запись и отметка; dry — холостой замер (в
базу не пишется ничего, отметка не двигается); off — письма не читаются.

Команды (прогон library-daily.yml):
  python library/increment.py начало          — время начала и наибольшие номера
                                                (два-три запроса) → $GITHUB_ENV
  python library/increment.py окно            — какое окно возьмёт проход (база)
  python library/increment.py отметка         — отметка сделок и карточек
  python library/increment.py отметка письма  — отметка писем (граница прохода
                                                INCREMENT_MAIL_TO от разбора)
  ROLLBACK=<прогон> python library/increment.py откат письма
                                              — что записал проход писем прогона
                                                (холостой); с APPLY=1 — снять это
                                                и вернуть прежнюю отметку писем
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

#: Источник → имя замера в lib_metric_runs. У писем источник — группа
#: mail_source.ГРУППЫ; имя не совпадает с отметкой пачки («почта:<группа>»).
ЗАМЕР = {"deals": "инкремент_сделки", "rfq": "инкремент_запросы",
         "mail-supplier": "инкремент_письма_поставщиков"}
#: Источники, чью отметку пишет «отметка» без аргумента.
ИСТОЧНИКИ = ("deals", "rfq")
#: Группа писем ежедневного прохода.
ПИСЬМА = "mail-supplier"
#: Режимы писем (MAIL_DAILY): запись, холостой замер, выключено.
РЕЖИМЫ = ("apply", "dry", "off")
#: Окно первого прохода писем, дней (вход mail_days). Неделя: входящие этой
#: недели — ради них проход и заведён; глубже — дело ручной пачки.
ПЕРВЫЙ_ДНЕЙ_ПИСЕМ = 7
#: Не больше стольких писем за проход (вход mail_limit). Держит портал при
#: наплыве (подключили ящик — пришли тысячи старых писем разом): лишнее уходит
#: на следующий день от границы, ничего не теряя. Нагрузка — library-daily.yml.
ЛИМИТ_ПИСЕМ = 1000
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


def режим_писем() -> str:
    """MAIL_DAILY: apply | dry | off; не задан — off (прежнее поведение). Иное —
    отказ: опечатка не должна молча превращаться ни в запись, ни в «выключено»."""
    р = os.environ.get("MAIL_DAILY", "").strip().lower() or "off"
    if р not in РЕЖИМЫ:
        raise ValueError(f"MAIL_DAILY={р!r}: допустимо " + ", ".join(РЕЖИМЫ))
    return р


def _целое_окружения(имя: str, умолч: int) -> int:
    т = os.environ.get(имя, "").strip()
    if not т:
        return умолч
    if not т.isdigit() or int(т) <= 0:
        raise ValueError(f"{имя}={т!r}: нужно целое больше нуля")
    return int(т)


def первый_дней_писем() -> int:
    return _целое_окружения("MAIL_FIRST_DAYS", ПЕРВЫЙ_ДНЕЙ_ПИСЕМ)


def лимит_писем() -> int:
    return _целое_окружения("MAIL_DAILY_LIMIT", ЛИМИТ_ПИСЕМ)


def первый_дней_для(source: str) -> int:
    return первый_дней_писем() if source == ПИСЬМА else ПЕРВЫЙ_ДНЕЙ


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
              f"окно первого прохода, {первый_дней_для(source)} дн.", flush=True)
        return None
    row = cur.fetchone()
    if not row:
        return None
    nums = row[0]
    return json.loads(nums) if isinstance(nums, str) else nums


ПОЯСНЕНИЕ = {
    "deals": "отметка ежедневного прохода: изменённое после «начало», новое с номером выше «после_id»",
    "rfq": "отметка ежедневного прохода: изменённое после «начало», новое с номером выше «после_id»",
    ПИСЬМА: "отметка ежедневного прохода писем: разобраны письма группы с номером до «после_id» "
            "включительно; «начало» — начало прохода, от него перекрытие следующего",
}


def записать(cur, source: str, начало: float, после_id: int, run_key: str) -> None:
    cur.execute(
        "insert into lib_metric_runs (metric, run_key, nums, note) values (%s, %s, %s::jsonb, %s) "
        "on conflict (metric, run_key) do update set nums = excluded.nums, "
        "measured_at = now(), note = excluded.note",
        (ЗАМЕР[source], run_key,
         json.dumps({"начало": int(начало), "после_id": int(после_id)}),
         ПОЯСНЕНИЕ[source]))


def окно_из_базы(source: str) -> tuple[datetime, int]:
    import indexer
    conn = indexer.connect()
    with conn.cursor() as cur:
        отметка = прочитать(cur, source)
    conn.close()
    дней = первый_дней_для(source)
    с, после_id = окно(отметка, datetime.now(timezone.utc), первый_дней=дней)
    if source == ПИСЬМА:
        print(f"ежедневный проход ({source}): "
              + (f"письма с номером выше {после_id}, перекрытие — созданные с {для_фильтра(с)}"
                 if после_id else
                 f"отметки нет — первый проход: письма, созданные с {для_фильтра(с)}"
                 f" ({дней} дн.)"), flush=True)
        return с, после_id
    print(f"ежедневный проход ({source}): изменённое с {для_фильтра(с)}"
          + (f", новое с номером выше {после_id}" if после_id else
             " — отметки нет, окно первого прохода") , flush=True)
    return с, после_id


def в_окружение(строки: list[str]) -> None:
    """Строки «ИМЯ=значение» — в журнал и в $GITHUB_ENV следующих шагов задания."""
    print("\n".join(строки), flush=True)
    if os.getenv("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as fh:
            fh.write("\n".join(строки) + "\n")


def граница_писем_в_окружение(граница: int) -> None:
    """Разбор писем прошёл с записью — граница прохода для «отметка письма»."""
    в_окружение([f"INCREMENT_MAIL_TO={int(граница)}"])


# ─────────────────────────────────────── откат прохода писем
# ЧТО ЗАПИСАЛ ПРОХОД ПИСЕМ ПРОГОНА. Файлы писем группы (origin — «письмо
# поставщика»), обработанные между началом прохода (nums.начало отметки) и
# записью его отметки (measured_at): в это время писать такие файлы больше
# некому — ручная пачка той же группы стоит в той же очереди bitrix-portal, а
# остальные группы и источники пишут другое происхождение. Снимается всё, что
# разбор писал по этим файлам: цены своего потока, строки спроса своей подписи,
# сами записи файлов, — и отметка прогона: прежняя отметка снова последняя, и
# следующий проход перечитает то же окно. Всё снятое воспроизводится повтором
# прохода; поэтому это откат своей записи, а не удаление данных.
ОТКАТ_ОТМЕТКА = ("select nums, measured_at from lib_metric_runs"
                 " where metric = %s and run_key = %s")
ОТКАТ_ФАЙЛЫ = ("select file_id from lib_files where origin = %s"
               " and processed_at >= to_timestamp(%s) and processed_at <= %s")
ОТКАТ_СЧЁТ = {
    "цен": "select count(*) from lib_prices where feed = %s and source_url = any(%s)",
    "строк спроса": "select count(*) from lib_demand where source = %s and source_file = any(%s)",
}
ОТКАТ_СНЯТЬ = {
    "цен": "delete from lib_prices where feed = %s and source_url = any(%s)",
    "строк спроса": "delete from lib_demand where source = %s and source_file = any(%s)",
    "файлов": "delete from lib_files where origin = %s and file_id = any(%s)",
}


def откатить_письма(cur, run_key: str, применить: bool) -> dict | None:
    """Откат прохода писем прогона run_key. Счёт — только числа. None — отметки нет."""
    import mail_source
    import price_store
    группа = mail_source.группа_или_отказ(ПИСЬМА)
    cur.execute(ОТКАТ_ОТМЕТКА, (ЗАМЕР[ПИСЬМА], run_key))
    строка = cur.fetchone()
    if not строка:
        return None
    nums = строка[0] if not isinstance(строка[0], str) else json.loads(строка[0])
    начало = float((nums or {}).get("начало") or 0)
    cur.execute(ОТКАТ_ФАЙЛЫ, (группа["origin"], начало, строка[1]))
    файлы = sorted({r[0] for r in cur.fetchall()})
    параметры = {"цен": (price_store.FEED_ПИСЬМА, файлы),
                 "строк спроса": (группа["источник_строки"], файлы),
                 "файлов": (группа["origin"], файлы)}
    счёт: dict[str, int] = {}
    if not применить:
        for что, запрос in ОТКАТ_СЧЁТ.items():
            cur.execute(запрос, параметры[что])
            счёт[что] = int(cur.fetchone()[0])
        счёт["файлов"] = len(файлы)
        return счёт
    for что, запрос in ОТКАТ_СНЯТЬ.items():
        cur.execute(запрос, параметры[что])
        счёт[что] = cur.rowcount
    cur.execute("delete from lib_metric_runs where metric = %s and run_key = %s",
                (ЗАМЕР[ПИСЬМА], run_key))
    счёт["отметок"] = cur.rowcount
    return счёт


def _наибольшие_номера(письма: bool = False) -> dict[str, int]:
    import indexer
    j = indexer.bx("crm.deal.list", {"select": ["ID"], "order": {"ID": "DESC"}, "start": -1})
    сделки = int(((j.get("result") or [{}])[0] or {}).get("ID") or 0)
    карточки = indexer.bx_max_id("crm.item.list", {"entityTypeId": indexer.SPA_RFQ})
    номера = {"deals": сделки, "rfq": карточки}
    if письма:
        import mail_source
        номера[ПИСЬМА] = mail_source.наибольший_номер(ПИСЬМА, indexer.bx)
    return номера


def main(argv: list[str]) -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    команда = argv[1] if len(argv) > 1 else ""
    что = argv[2] if len(argv) > 2 else ""
    try:
        режим = режим_писем()
        if режим != "off" and команда in ("начало", "окно"):
            первый_дней_писем(), лимит_писем()
    except ValueError as e:
        print(f"::error::{e}", flush=True)
        return 2
    if команда == "начало":
        начало = int(datetime.now(timezone.utc).timestamp())
        номера = _наибольшие_номера(письма=режим != "off")
        if not номера["deals"] or not номера["rfq"] or номера.get(ПИСЬМА) == 0:
            print("::error::наибольший номер не прочитан — отметка вышла бы нулевой, "
                  "а ноль значит «все записи»", flush=True)
            return 2
        строки = [f"INCREMENT_START={начало}",
                  f"INCREMENT_MAX_DEALS={номера['deals']}",
                  f"INCREMENT_MAX_RFQ={номера['rfq']}"]
        if ПИСЬМА in номера:
            строки.append(f"INCREMENT_MAX_MAIL={номера[ПИСЬМА]}")
        print(f"письма поставщиков: режим {режим}", flush=True)
        в_окружение(строки)
        return 0
    if команда == "окно":
        for source in ИСТОЧНИКИ + ((ПИСЬМА,) if режим != "off" else ()):
            окно_из_базы(source)
        return 0
    if команда == "отметка" and что == "письма":
        return _отметка_писем(режим)
    if команда == "отметка" and not что:
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
            for source in ИСТОЧНИКИ:
                записать(cur, source, начало, макс[source], run_key)
        conn.commit()
        conn.close()
        print(f"отметка записана: начало {int(начало)}, номера {макс}", flush=True)
        return 0
    if команда == "откат" and что == "письма":
        return _откат_писем()
    print("команда: начало | окно | отметка | отметка письма | откат письма", file=sys.stderr)
    return 2


def _отметка_писем(режим: str) -> int:
    """Отметка писем — только после разбора с записью, отдавшего границу.

    Границу (INCREMENT_MAIL_TO) разбор отдаёт ЛИШЬ дойдя до конца с записью:
    упал — её нет, и отметка остаётся прежней; холостой замер её не отдаёт
    никогда. Без границы шаг не ошибка: письма выключены или шёл замер.
    """
    граница_т = os.environ.get("INCREMENT_MAIL_TO", "").strip()
    if режим != "apply" or not граница_т:
        print(f"отметка писем не двигается: режим {режим}"
              + ("" if граница_т else ", границы прохода нет (разбор с записью не прошёл)"),
              flush=True)
        return 0
    try:
        начало = float(os.environ["INCREMENT_START"])
        граница = int(граница_т)
    except (KeyError, ValueError):
        print("::error::нет INCREMENT_START или граница писем не число", flush=True)
        return 2
    if граница <= 0:
        print("::error::граница писем нулевая — ноль значит «все письма»", flush=True)
        return 2
    run_key = os.getenv("GITHUB_RUN_ID") or str(int(начало))
    import indexer
    conn = indexer.connect()
    with conn.cursor() as cur:
        записать(cur, ПИСЬМА, начало, граница, run_key)
    conn.commit()
    conn.close()
    print(f"отметка писем {ПИСЬМА} записана: начало {int(начало)}, граница {граница}",
          flush=True)
    return 0


def _откат_писем() -> int:
    run_key = os.environ.get("ROLLBACK", "").strip()
    if not run_key:
        print("::error::ROLLBACK=<номер прогона> — чей проход писем откатить", flush=True)
        return 2
    применить = os.environ.get("APPLY", "").strip().lower() in ("1", "true", "yes")
    import indexer
    conn = indexer.connect()
    with conn.cursor() as cur:
        счёт = откатить_письма(cur, run_key, применить)
    if счёт is None:
        conn.rollback()
        conn.close()
        print(f"::error::отметки писем прогона {run_key} нет — откатывать нечего", flush=True)
        return 2
    if применить:
        conn.commit()
    else:
        conn.rollback()
    conn.close()
    print(f"откат прохода писем прогона {run_key}: "
          + " · ".join(f"{к} {v}" for к, v in счёт.items())
          + ("" if применить else " (холостой: ничего не снято; APPLY=1 — снять)"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
