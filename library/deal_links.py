"""Связи сквозного свода: карточка запроса СП-166 → сделка → заказчик.

ЗАЧЕМ. Недельный свод (scripts/weekly_offers.py) идёт по цепочке «КП поставщика
→ запрос → сделка → заказчик → наше КП заказчику». До 27.09.2026 в библиотеке
не было двух её звеньев: какой сделке служит карточка запроса (поле parentId2
СП-166 не читалось вовсе — у файлов карточки lib_files.deal_id и
lib_prices.rfq_id хранят номер самой карточки), и кто заказчик сделки (название
и компания сделки нигде не хранились). Свод выводил сделку косвенно — по копии
файла КП в поле сделки.

ГДЕ ХРАНИТСЯ — ОТДЕЛЬНЫЕ ТАБЛИЦЫ, А НЕ КОЛОНКА lib_files
(library/supabase/deal_links_schema.sql):
  · связь — свойство КАРТОЧКИ, а не файла. Один файл висит на нескольких
    карточках (indexer.без_повторов оставляет первую ссылку), и колонка файла
    записала бы сделку одной из них; у карточки без файла КП (запрос ушёл,
    ответа нет) строки lib_files нет вовсе, а в своде она нужна — «спросили, не
    ответили»;
  · разбор и переразбор переписывают строку lib_files целиком (вставка при
    конфликте, UPDATE переразбора), и связь, живущая там, зависела бы от того,
    какой путь записал файл последним;
  · откат по ключу прогона — своими строками своей таблицы, не трогая файлы.

КЛЮЧИ — ТЕКСТОМ ИЗ ЦИФР, как lib_files.deal_id, lib_prices.rfq_id и
lib_prices.rfq_company: соединения идут по индексам без приведения типов.

ОТКУДА. Индексатор читает карточки (collect_refs_rfq) и сделки (collect_refs)
и так — поля parentId2 и title, TITLE, COMPANY_ID, STAGE_ID, DATE_CREATE идут в
тот же select: ни одного лишнего запроса. Историю досчитывает
library/backfill_deal_links.py.

ПОЛЕ, КОТОРОГО ПОРТАЛ НЕ ОТДАЛ, — НЕ «ПУСТО». Строка берётся, только если ключ
поля есть в ответе (parentId2 у карточки, COMPANY_ID у сделки): select, молча
не сработавший, иначе записал бы «сделки нет» у всех карточек разом.
"""
from __future__ import annotations

import re
from datetime import datetime

#: Поля карточки СП-166 для связи (crm.item.list отдаёт их camelCase).
ПОЛЕ_СДЕЛКИ_КАРТОЧКИ = "parentId2"
ПОЛЯ_КАРТОЧКИ = ("id", ПОЛЕ_СДЕЛКИ_КАРТОЧКИ, "title", "createdTime")
#: Поля сделки в crm.deal.list (старый метод — заглавными).
ПОЛЯ_СДЕЛКИ = ("ID", "TITLE", "COMPANY_ID", "STAGE_ID", "DATE_CREATE")
#: Длина названия — как у позиции спроса: хватает, и строка не раздувается.
ДЛИНА = 500

_ЦИФРЫ = re.compile(r"^[0-9]+$")


def номер(v) -> str | None:
    """Номер сущности портала текстом из цифр; «0», пусто и мусор — None.

    Портал отдаёт «нет компании» как «0» (crm.deal.list) или null (crm.item.*)."""
    if v is None or isinstance(v, bool):
        return None
    т = str(v).strip()
    if not _ЦИФРЫ.match(т):
        return None
    т = т.lstrip("0")
    return т or None


def момент(v) -> str | None:
    """Время портала ISO-строкой для timestamptz; неразборчивое — None."""
    т = str(v or "").strip()
    if not т:
        return None
    try:
        datetime.fromisoformat(т.replace("Z", "+00:00"))
    except ValueError:
        return None
    return т


def текст(v) -> str | None:
    т = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v or "")).strip()
    return т[:ДЛИНА] or None


def строка_карточки(x: dict) -> tuple | None:
    """(card_id, deal_id, title, created_at) — или None, если ответ не годен:
    нет номера карточки или портал не отдал поле parentId2 вовсе."""
    if not isinstance(x, dict) or ПОЛЕ_СДЕЛКИ_КАРТОЧКИ not in x:
        return None
    к = номер(x.get("id"))
    if not к:
        return None
    return (к, номер(x.get(ПОЛЕ_СДЕЛКИ_КАРТОЧКИ)), текст(x.get("title")),
            момент(x.get("createdTime")))


def строка_сделки(x: dict) -> tuple | None:
    """(deal_id, title, company_id, stage, created_at) — или None: нет номера
    сделки или портал не отдал COMPANY_ID вовсе."""
    if not isinstance(x, dict) or "COMPANY_ID" not in x:
        return None
    д = номер(x.get("ID"))
    if not д:
        return None
    return (д, текст(x.get("TITLE")), номер(x.get("COMPANY_ID")),
            текст(x.get("STAGE_ID")), момент(x.get("DATE_CREATE")))


def без_повторов(строки: list[tuple]) -> list[tuple]:
    """Одна строка на ключ (последняя): два одинаковых ключа в одном операторе
    с «on conflict» PostgreSQL не пропускает («cannot affect row a second time»)."""
    return list({r[0]: r for r in строки if r}.values())


# ── SQL ──────────────────────────────────────────────────────────────────────
# Досчёт истории пишет ТОЛЬКО НОВЫЕ строки (on conflict do nothing): записанное
# разбором свежее, а откат снимает ровно вставленное этим прогоном.
НОВЫЕ_КАРТОЧКИ = """
insert into lib_rfq_cards (card_id, deal_id, title, created_at, run_id)
select v.c, v.d, v.t, v.m::timestamptz, %s
  from (values %s) as v(c, d, t, m)
on conflict (card_id) do nothing
returning card_id, deal_id"""

НОВЫЕ_СДЕЛКИ = """
insert into lib_deals (deal_id, title, company_id, stage, created_at, run_id)
select v.d, v.t, v.k, v.s, v.m::timestamptz, %s
  from (values %s) as v(d, t, k, s, m)
on conflict (deal_id) do nothing
returning deal_id, company_id"""

# Разбор пишет СВЕЖЕЕ: портал только что отдал карточку, и это правда на сейчас.
# Строка меняется, только если что-то в ней изменилось, — тогда и ключ прогона
# её; неизменная строка остаётся за тем, кто её записал.
СВЕЖИЕ_КАРТОЧКИ = """
insert into lib_rfq_cards as t (card_id, deal_id, title, created_at, run_id)
select v.c, v.d, v.t, v.m::timestamptz, %s
  from (values %s) as v(c, d, t, m)
on conflict (card_id) do update
   set deal_id = excluded.deal_id, title = excluded.title,
       created_at = coalesce(excluded.created_at, t.created_at),
       run_id = excluded.run_id, seen_at = now()
 where (t.deal_id, t.title, t.created_at)
       is distinct from (excluded.deal_id, excluded.title,
                         coalesce(excluded.created_at, t.created_at))"""

СВЕЖИЕ_СДЕЛКИ = """
insert into lib_deals as t (deal_id, title, company_id, stage, created_at, run_id)
select v.d, v.t, v.k, v.s, v.m::timestamptz, %s
  from (values %s) as v(d, t, k, s, m)
on conflict (deal_id) do update
   set title = excluded.title, company_id = excluded.company_id, stage = excluded.stage,
       created_at = coalesce(excluded.created_at, t.created_at),
       run_id = excluded.run_id, seen_at = now()
 where (t.title, t.company_id, t.stage, t.created_at)
       is distinct from (excluded.title, excluded.company_id, excluded.stage,
                         coalesce(excluded.created_at, t.created_at))"""

# СВЕРКА PYTHON ↔ SQL. Те же строки, что уйдут в запись, считает база: всего,
# со связью, уже записано и расходится с записанным. Python считает своё —
# расхождение значит, что строки портятся по дороге (тип, пустота, порядок).
СВЕРКА_КАРТОЧЕК = """
select count(*)::bigint, count(v.d)::bigint,
       count(t.card_id)::bigint,
       count(*) filter (where t.card_id is not null and t.deal_id is distinct from v.d)::bigint
  from (values %s) as v(c, d, t, m)
  left join lib_rfq_cards t on t.card_id = v.c"""

СВЕРКА_СДЕЛОК = """
select count(*)::bigint, count(v.k)::bigint,
       count(t.deal_id)::bigint,
       count(*) filter (where t.deal_id is not null and t.company_id is distinct from v.k)::bigint
  from (values %s) as v(d, t, k, s, m)
  left join lib_deals t on t.deal_id = v.d"""

ЗАПИСАНО_КАРТОЧЕК = ("select count(*)::bigint, count(deal_id)::bigint from lib_rfq_cards"
                     " where run_id = %s and card_id = any(%s)")
ЗАПИСАНО_СДЕЛОК = ("select count(*)::bigint, count(company_id)::bigint from lib_deals"
                   " where run_id = %s and deal_id = any(%s)")

ОТКАТ = ("delete from lib_rfq_cards where run_id = %s",
         "delete from lib_deals where run_id = %s")

ЕСТЬ_ТАБЛИЦЫ = ("select to_regclass(current_schema() || '.lib_rfq_cards') is not null"
                " and to_regclass(current_schema() || '.lib_deals') is not null")


def счёт_python(строки: list[tuple], связь: int) -> tuple[int, int]:
    """(строк, со связью) — связь: индекс колонки сделки или компании."""
    return len(строки), sum(1 for r in строки if r[связь] is not None)


def _с_ключом(cur, запрос: str, run_id: str) -> str:
    """Первое %s запроса — ключ прогона, второе — VALUES для execute_values."""
    к = запрос.split("%s")
    assert len(к) == 3, "в запросе ровно два места %s"
    return cur.mogrify(к[0] + "%s", (run_id,)).decode() + к[1] + "%s" + к[2]


def записать_свежие(cur, карточки: list[tuple], сделки: list[tuple], run_id: str,
                    execute_values) -> tuple[int, int] | None:
    """Свежие связи из разбора: (карточек, сделок) вставлено или изменено.

    None — таблиц нет (схему ещё не применили): разбор идёт дальше без связей,
    вызывающий печатает предупреждение. Ночной разбор из-за вспомогательной
    записи не падает. Пакет — одним оператором (page_size = длине): rowcount
    execute_values иначе говорит только о последней странице."""
    cur.execute(ЕСТЬ_ТАБЛИЦЫ)
    if not cur.fetchone()[0]:
        return None
    n_к = n_с = 0
    карточки, сделки = без_повторов(карточки), без_повторов(сделки)
    if карточки:
        execute_values(cur, _с_ключом(cur, СВЕЖИЕ_КАРТОЧКИ, run_id), карточки,
                       page_size=len(карточки))
        n_к = cur.rowcount
    if сделки:
        execute_values(cur, _с_ключом(cur, СВЕЖИЕ_СДЕЛКИ, run_id), сделки,
                       page_size=len(сделки))
        n_с = cur.rowcount
    return n_к, n_с


def сверить(cur, запрос: str, строки: list[tuple], execute_values) -> tuple[int, int, int, int]:
    """(строк, со связью, уже записано, расходится с записанным) — счёт базы."""
    if not строки:
        return 0, 0, 0, 0
    r = execute_values(cur, запрос, строки, page_size=max(1, len(строки)), fetch=True)
    return tuple(int(x) for x in r[0])


def вставить_новые(cur, запрос: str, строки: list[tuple], run_id: str,
                   execute_values) -> list[tuple]:
    """Вставить только отсутствующие; вернуть (ключ, связь) вставленных."""
    if not строки:
        return []
    return list(execute_values(cur, _с_ключом(cur, запрос, run_id), строки,
                               page_size=max(1, len(строки)), fetch=True))
