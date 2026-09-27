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
Ночной запуск — off, пока владелец не включит запись (library-daily.yml):
холостой замер отметку не пишет, и каждую ночь был бы первым проходом заново.

ПИСЬМА НЕ ДЕРЖАТ СДЕЛКИ И КАРТОЧКИ И В ОБЩИХ ШАГАХ. «начало» и «окно» общие
для всех источников: сбой чтения верха писем, пустая группа или неверный вход
писем здесь — предупреждение, а не отказ. Верх писем (INCREMENT_MAX_MAIL) тогда
не пишется, и шаг писем сам выходит с отказом «нет верха»; входы писем
проверяет он же (indexer.collect_refs_mail_increment).

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
                                                и вернуть прежнюю отметку писем.
                                                Только последний проход; упавший —
                                                по строке шага, а снятый по
                                                таймауту — с ROLLBACK_TO=<конец>
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


def режим_писем_в_общем_шаге() -> str:
    """Режим писем для шагов, общих со сделками и карточками («начало», «окно»).

    Неверный режим здесь — предупреждение и «письма не читаются в этом шаге», а
    не отказ: опечатка во входе писем не должна останавливать разбор сделок и
    карточек. Молча она не проходит — шаг писем с тем же режимом падает сам
    (indexer.collect_refs_mail_increment), и отметка писем не двигается.
    """
    try:
        return режим_писем()
    except ValueError as e:
        print(f"::warning::письма поставщиков: {e} — в этом шаге письма не читаются; "
              "сделки и карточки идут, шаг писем выйдет с отказом", flush=True)
        return "off"


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
# поставщика»), обработанные в окнах этого прохода: в это время писать такие
# файлы больше некому — ручная пачка той же группы стоит в той же очереди
# bitrix-portal, а остальные группы и источники пишут другое происхождение.
# Снимается всё, что разбор писал по этим файлам: цены своего потока, строки
# спроса своей подписи, сами записи файлов, — и строки прогона в
# lib_metric_runs. Всё снятое воспроизводится повтором прохода; поэтому это
# откат своей записи, а не удаление данных.
#
# ОКНА — ИЗ ДВУХ СТРОК, А НЕ ИЗ ОДНОЙ ОТМЕТКИ. Отметку пишет только проход,
# дошедший до конца, а flush() пишет файлы, спрос и цены пакетами по ходу
# разбора: упавший проход (отказ Диска, ошибка записи цен, исключение посреди
# разбора) оставлял записанное без единой строки, по которой его можно найти,
# и откат отвечал «откатывать нечего» (ревизия 27.09.2026). Поэтому разбор
# писем с записью ДО первой записи кладёт строку шага (ШАГ_ПИСЕМ, ключ
# «<прогон>.<попытка>», начало — measured_at по часам базы) и при любом выходе
# — возврате, отказе гейта, исключении — дописывает в неё «конец». Окна отката:
#   • отметка прохода — [nums.начало; measured_at] (как прежде);
#   • строка шага с концом — [measured_at; конец], по попытке, без промежутка
#     между попытками (в нём могла пройти ручная пачка);
#   • строка шага без конца — задание сняли (таймаут, потеря раннера), и
#     finally не успел: конец окна называет человек (ROLLBACK_TO — время конца
#     задания из журнала прогона), не дальше ЗАДАНИЕ_МИН от начала шага.
#
# ТОЛЬКО ПОСЛЕДНИЙ ПРОХОД. Отметка, записанная ПОСЛЕ начала откатываемого
# прохода, значит: следующий проход прочитал его окно, отсеял его файлы по
# lib_files и сдвинул отметку дальше. Сними эти файлы — и письма окна не
# перечитает уже никто (замер ревизии: отметки R1 100, R2 200, R3 300; откат
# R2 оставлял последней R3, и письма 101–200 выпадали). Такой откат — отказ:
# сначала откатываются более поздние проходы, от последнего.
ОТКАТ_ОТМЕТКА = ("select nums, measured_at from lib_metric_runs"
                 " where metric = %s and run_key = %s")
ОТКАТ_ШАГИ = ("select run_key, measured_at, nums from lib_metric_runs"
              " where metric = %s and split_part(run_key, '.', 1) = %s order by measured_at")
ОТКАТ_ПОЗЖЕ = ("select run_key from lib_metric_runs where metric = %s and run_key <> %s"
               " and measured_at > %s order by measured_at")
ОТКАТ_СЧЁТ = {
    "цен": "select count(*) from lib_prices where feed = %s and source_url = any(%s)",
    "строк спроса": "select count(*) from lib_demand where source = %s and source_file = any(%s)",
}
ОТКАТ_СНЯТЬ = {
    "цен": "delete from lib_prices where feed = %s and source_url = any(%s)",
    "строк спроса": "delete from lib_demand where source = %s and source_file = any(%s)",
    "файлов": "delete from lib_files where origin = %s and file_id = any(%s)",
}
#: Строка шага писем прогона: metric; run_key — «<прогон>.<попытка>».
ШАГ_ПИСЕМ = ЗАМЕР[ПИСЬМА] + ":шаг"
ПОЯСНЕНИЕ_ШАГА = ("шаг писем ежедневного прохода: measured_at — начало записи, «конец» — "
                  "выход разбора (секунды эпохи); окно отката прохода")
#: Задание library-daily.yml длится не дольше timeout-minutes (сверяет тест).
#: Окно прохода длиннее — не окно одного задания: в него попала бы запись
#: ручной пачки, шедшей после, и откат снял бы чужое.
ЗАДАНИЕ_МИН = 180


class ОтказОтката(ValueError):
    """Откат прохода писем снял бы чужое или потерял бы окно — не делается."""


def ключ_шага() -> str:
    """run_key строки шага: «<прогон>.<попытка>». Прогон — тот же, что у отметки
    (GITHUB_RUN_ID, без него — начало прохода); перезапуск задания — новая
    попытка и своя строка, чтобы окно одной не расползалось на промежуток."""
    прогон = os.getenv("GITHUB_RUN_ID") or str(int(float(os.environ["INCREMENT_START"])))
    попытка = (os.getenv("GITHUB_RUN_ATTEMPT") or "1").strip()
    if "." in прогон or not попытка.isdigit():
        raise ValueError(f"ключ шага: прогон {прогон!r}, попытка {попытка!r}")
    return f"{прогон}.{попытка}"


def начать_шаг(cur, run_key: str, граница: int) -> None:
    """Строка шага ДО первой записи: measured_at — начало окна отката по часам базы."""
    cur.execute(
        "insert into lib_metric_runs (metric, run_key, nums, note) values (%s, %s, %s::jsonb, %s) "
        "on conflict (metric, run_key) do update set nums = excluded.nums, "
        "measured_at = now(), note = excluded.note",
        (ШАГ_ПИСЕМ, run_key, json.dumps({"граница": int(граница)}), ПОЯСНЕНИЕ_ШАГА))


ШАГ_КОНЕЦ = ("update lib_metric_runs set nums = nums || jsonb_build_object("
             "'конец', extract(epoch from clock_timestamp())) where metric = %s and run_key = %s")


def закончить_шаг(cur, run_key: str) -> None:
    """«Конец» строки шага — по часам базы, с долями секунды: последняя запись
    разбора легла раньше, и округление вниз не должно выбросить её из окна."""
    cur.execute(ШАГ_КОНЕЦ, (ШАГ_ПИСЕМ, run_key))


def момент(т: str) -> datetime:
    """ROLLBACK_TO: секунды эпохи или ISO 8601 с поясом (…Z, …+03:00)."""
    т = т.strip()
    try:
        return datetime.fromtimestamp(float(т), tz=timezone.utc)
    except ValueError:
        pass
    м = datetime.fromisoformat(т.replace("Z", "+00:00"))
    if м.tzinfo is None:
        raise ValueError(f"ROLLBACK_TO={т!r}: нужен часовой пояс (Z или +03:00)")
    return м


def _nums(значение) -> dict:
    return (json.loads(значение) if isinstance(значение, str) else значение) or {}


def _utc(с: float) -> datetime:
    return datetime.fromtimestamp(float(с), tz=timezone.utc)


def окна_прохода(отметка: tuple | None, шаги: list[tuple],
                 до: datetime | None = None) -> list[tuple[datetime, datetime, str]]:
    """Окна записи прохода: [(с, по, откуда)]. Без базы — чтобы проверять логику.

    отметка — (nums, measured_at) отметки прогона или None; шаги — строки шага
    (run_key, measured_at, nums). До — ROLLBACK_TO для шага без конца.
    """
    окна: list[tuple[datetime, datetime, str]] = []
    if отметка:
        начало = float(_nums(отметка[0]).get("начало") or 0)
        if начало <= 0:
            raise ОтказОтката("у отметки прохода нет начала — окно было бы «с 1970 года»")
        окна.append((_utc(начало), отметка[1], "отметка"))
    незакрытые = []
    for ключ, начало, nums in шаги:
        конец = _nums(nums).get("конец")
        if конец is not None:
            окна.append((начало, _utc(конец), f"шаг {ключ}"))
        elif отметка and окна[0][0] <= начало <= окна[0][1]:
            continue            # конец не записан, но шаг лежит в окне отметки
        else:
            незакрытые.append((ключ, начало))
    if len(незакрытые) > 1:
        raise ОтказОтката(f"у прогона {len(незакрытые)} попытки без конца — одним ROLLBACK_TO "
                          "их окна не разделить, а угадывать их откат не станет: такой случай "
                          "разбирается руками по журналам попыток")
    if незакрытые:
        ключ, начало = незакрытые[0]
        if до is None:
            raise ОтказОтката(f"шаг {ключ} начат {начало.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ} "
                              "и не записал конец (задание сняли) — укажи ROLLBACK_TO: время конца "
                              "задания из журнала прогона")
        # Раньше начала шага или дальше длины задания — проверка ниже, общая.
        окна.append((начало, до, f"шаг {ключ} до ROLLBACK_TO"))
    elif до is not None:
        raise ОтказОтката("ROLLBACK_TO задан, а шага без конца нет — окно известно из базы; "
                          "сними ROLLBACK_TO")
    for с, по, откуда in окна:
        if по < с or по - с > timedelta(minutes=ЗАДАНИЕ_МИН):
            raise ОтказОтката(f"окно «{откуда}» длиннее задания ({ЗАДАНИЕ_МИН} мин) или кончается "
                              "раньше начала — это не окно одного прохода")
    return окна


def откатить_письма(cur, run_key: str, применить: bool,
                    до: datetime | None = None) -> dict | None:
    """Откат прохода писем прогона run_key. Счёт — только числа. None — у прогона
    нет ни отметки, ни строки шага. ОтказОтката — откат потерял бы окно или
    снял бы чужое (см. выше); в базе при этом не тронуто ничего."""
    import mail_source
    import price_store
    группа = mail_source.группа_или_отказ(ПИСЬМА)
    cur.execute(ОТКАТ_ОТМЕТКА, (ЗАМЕР[ПИСЬМА], run_key))
    отметка = cur.fetchone()
    cur.execute(ОТКАТ_ШАГИ, (ШАГ_ПИСЕМ, run_key))
    шаги = cur.fetchall()
    if not отметка and not шаги:
        return None
    окна = окна_прохода(отметка, шаги, до)
    cur.execute(ОТКАТ_ПОЗЖЕ, (ЗАМЕР[ПИСЬМА], run_key, min(с for с, _, _ in окна)))
    позже = [r[0] for r in cur.fetchall()]
    if позже:
        raise ОтказОтката(f"после этого прохода отметку писем записали прогоны {', '.join(позже)}: "
                          "их проход прочитал окно этого и отсеял его файлы — сними их, и письма "
                          "окна не перечитает никто. Откатывай от последнего")
    for с, по, откуда in окна:
        print(f"окно прохода ({откуда}): {с.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%S}Z … "
              f"{по.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%S}Z", flush=True)
    условие = " or ".join(["processed_at between %s and %s"] * len(окна))
    cur.execute(f"select file_id from lib_files where origin = %s and ({условие})",
                (группа["origin"], *[м for с, по, _ in окна for м in (с, по)]))
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
    cur.execute("delete from lib_metric_runs where metric = %s and split_part(run_key, '.', 1) = %s",
                (ШАГ_ПИСЕМ, run_key))
    счёт["строк шага"] = cur.rowcount
    return счёт


def _наибольшие_номера() -> dict[str, int]:
    import indexer
    j = indexer.bx("crm.deal.list", {"select": ["ID"], "order": {"ID": "DESC"}, "start": -1})
    сделки = int(((j.get("result") or [{}])[0] or {}).get("ID") or 0)
    карточки = indexer.bx_max_id("crm.item.list", {"entityTypeId": indexer.SPA_RFQ})
    return {"deals": сделки, "rfq": карточки}


def верх_писем() -> int | None:
    """Наибольший номер письма группы на начало прохода или None — вслух.

    Сбой здесь — предупреждение, а не отказ шага «начало»: шаг общий, и письма
    не вправе остановить разбор сделок и карточек. Ноль (в группе нет писем)
    тоже не пишется — ноль значит «все письма». Без INCREMENT_MAX_MAIL шаг писем
    сам выходит с отказом «нет верха», и отметка писем не двигается.
    """
    import indexer
    import mail_source
    try:
        верх = mail_source.наибольший_номер(ПИСЬМА, indexer.bx)
    except Exception as e:  # сеть, отказ портала после бюджета ожидания, ответ не тот
        print(f"::warning::письма поставщиков: верх прохода не прочитан ({type(e).__name__}) — "
              "шаг писем выйдет с отказом, сделки и карточки идут", flush=True)
        return None
    if not верх:
        print("::warning::письма поставщиков: верх прохода — ноль (в группе нет писем или "
              "портал не ответил) — шаг писем выйдет с отказом, сделки и карточки идут",
              flush=True)
        return None
    return верх


def main(argv: list[str]) -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    команда = argv[1] if len(argv) > 1 else ""
    что = argv[2] if len(argv) > 2 else ""
    # Режим и входы писем проверяются строго только там, где работают письма
    # («отметка письма» и сам шаг писем): в общих шагах их ошибка — предупреждение.
    if команда == "начало":
        режим = режим_писем_в_общем_шаге()
        начало = int(datetime.now(timezone.utc).timestamp())
        номера = _наибольшие_номера()
        if not номера["deals"] or not номера["rfq"]:
            print("::error::наибольший номер не прочитан — отметка вышла бы нулевой, "
                  "а ноль значит «все записи»", flush=True)
            return 2
        строки = [f"INCREMENT_START={начало}",
                  f"INCREMENT_MAX_DEALS={номера['deals']}",
                  f"INCREMENT_MAX_RFQ={номера['rfq']}"]
        if режим != "off":
            верх = верх_писем()
            if верх:
                строки.append(f"INCREMENT_MAX_MAIL={верх}")
        print(f"письма поставщиков: режим {режим}", flush=True)
        в_окружение(строки)
        return 0
    if команда == "окно":
        режим = режим_писем_в_общем_шаге()
        for source in ИСТОЧНИКИ:
            окно_из_базы(source)
        if режим != "off":
            try:
                окно_из_базы(ПИСЬМА)
            except ValueError as e:     # вход mail_days: его проверит шаг писем
                print(f"::warning::окно писем не показано: {e}", flush=True)
        return 0
    if команда == "отметка" and что == "письма":
        try:
            режим = режим_писем()
        except ValueError as e:
            print(f"::error::{e}", flush=True)
            return 2
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
    if not run_key or "." in run_key:
        print("::error::ROLLBACK=<номер прогона> (без попытки) — чей проход писем откатить",
              flush=True)
        return 2
    применить = os.environ.get("APPLY", "").strip().lower() in ("1", "true", "yes")
    до_т = os.environ.get("ROLLBACK_TO", "").strip()
    try:
        до = момент(до_т) if до_т else None
    except ValueError as e:
        print(f"::error::{e}", flush=True)
        return 2
    import indexer
    conn = indexer.connect()
    try:
        with conn.cursor() as cur:
            счёт = откатить_письма(cur, run_key, применить, до)
    except ОтказОтката as e:
        conn.rollback()
        conn.close()
        print(f"::error::откат прохода писем прогона {run_key} не сделан: {e}", flush=True)
        return 2
    if счёт is None:
        conn.rollback()
        conn.close()
        print(f"::error::у прогона {run_key} нет ни отметки писем, ни строки шага — "
              "откатывать нечего", flush=True)
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
