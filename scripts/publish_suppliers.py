#!/usr/bin/env python3
"""Снимок реестра поставщиков из Supabase в KV Cloudflare (ключ suppliers:v1).

ЗАЧЕМ ОТДЕЛЬНЫЙ ПУБЛИКАТОР. Воркер портала в Supabase не ходит и ключа базы не
носит: раздел читает готовый снимок из KV. Так же устроена библиотека. Плата за
это — вот этот шаг, зато у страницы нет ни одного пути к базе.

ЧТО НЕ ПОПАДАЕТ В ЖУРНАЛ. Репозиторий публичный, прогон виден всем: печатаются
только агрегаты — счётчики и имена колонок (CLAUDE.md, правило 17). Ни одного
названия компании, ни домена, ни ИНН.

ПО УМОЛЧАНИЮ ВХОЛОСТУЮ: читает базу, собирает снимок, печатает его размер и
счётчики — и ничего не пишет. Запись включается ключом --apply.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import http.client
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib import error, parse, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from library import company_names  # noqa: E402

PAGES_PROJECT = "kvant-sourcing-f122"
KEY = "suppliers:v1"
# ИМЯ ПРИВЯЗКИ KV И ПОРЯДОК ПОИСКА — ровно как у воркера портала:
# aclStore(env) возвращает env.ACL || env.VISITS. Исторически привязана VISITS
# (её завёл счётчик посещений), ACL задумана как правильное имя, но не заведена.
# Публикатор, который ищет только ACL, не находит ничего и падает с
# KV_BINDING_MISSING — так упал прогон 21.09.2026 08:53, собрав снимок целиком
# и не записав его. Писать не туда, откуда читает воркер, ещё хуже: снимок
# молча не доедет до страницы. Поэтому порядок один на обе стороны, и на это
# стоит тест.
ПРИВЯЗКИ = ("ACL", "VISITS")
# Тот же предел, что у воркера (SUPPLIERS_MAX_BYTES): снимок, который воркер
# откажется читать, публиковать незачем.
MAX_BYTES = 8 * 1024 * 1024
CF_ID = re.compile(r"[a-fA-F0-9]{32}\Z")
READBACK_DELAYS = (0, 2, 5, 15, 30, 30)

# Признаки, которые попадают в снимок. Остальные виды (alias, trading, legal)
# нужны для сведения, а не для чтения: в снимке они только раздули бы объём.
ПОКАЗЫВАЕМ = ("inn", "vat", "ogrn", "domain", "bitrix")

# ИМЯ — ИЗ ВИДА sup_name_shown, а не display_name. display_name — ключ сведения
# («supremevalves»), а не вывеска; владелец 24.09.2026: «наименование как
# веб-сайт компании не работает». Выбор имени (карточка Битрикса → реквизиты →
# написание → display_name, если не похож на ключ → домен) сделан ОДИН РАЗ, в
# виде (suppliers_schema.sql, блок 8а), и его же читают /nomenclature и выгрузки
# брендов. Пока схему с видом не применили, вид подменяется пустой выборкой
# (company_names.имена_sql), и страница показывает display_name, как раньше.
СУЩНОСТИ_SQL = """
select e.id, coalesce(nm.name, e.display_name), e.country, e.note, e.status,
       e.resolution, r.seq is not null as numbered, nm.name_source
  from sup_entity e
  left join sup_number_registry r on r.sup_id = e.id
  left join sup_name_shown nm on nm.sup_id = e.id
 where e.resolution <> 'merged'
 order by e.id
"""
# ИНН ИЗ СВЕЖИХ РЕКВИЗИТОВ (library/company_names.py). Сведение пишет ИНН в
# sup_identifier только на своём прогоне; номер, заполненный в Битриксе после
# него, до следующего сведения виден лишь здесь. Идёт признаком inn со своим
# источником — два разных ИНН у записи покажутся оба, как и положено.
ИНН_РЕКВИЗИТОВ_SQL = """
select sup_id, 'inn', inn, 'реквизиты Битрикса'
  from (select distinct on (sup_id) sup_id, inn
          from sup_display_name
         where source = 'bitrix:requisite' and rolled_back_at is null
         order by sup_id, id desc) z
 where inn is not null
"""
ПРИЗНАКИ_SQL = """
select sup_id, kind, value, source
  from sup_identifier
 where status <> 'rejected' and kind = any(%s)
 order by sup_id, kind, value
"""
ОЧЕРЕДЬ_SQL = "select count(*) from sup_review where closed_at is null"
# ОТЛОЖЕННЫЕ ДО ИНН — ЭТО СПИСОК РАБОТЫ, А НЕ ЦИФРА. Страница показывала только
# счётчик очереди, и раздать работу по нему было нельзя: чтобы снять спор, надо
# открыть конкретные карточки Bitrix, а какие именно — знал только журнал
# прогона, где печаталась одна длина списка.
#
# Сами карточки в базе уже лежат: сведение кладёт их в payload строки
# entity_uncertain (ключи, имена, причина). Достаём их оттуда, ничего не
# пересчитывая, — второй расчёт разошёлся бы с первым.
#
# Замер 21.09.2026: отложено 394 сущности, карточек к заполнению 980 — против
# 8 266 карточек без ИНН вообще. Разница в 8,4 раза, и она решает, месяц это
# работы или день.
# ТОЛЬКО ПОСЛЕДНИЙ ПРОГОН СВЕДЕНИЯ. sup_review копит строки: сущность, отложенную
# и в прошлый раз, и в этот, «closed_at is null» вернёт дважды. Холостой прогон
# 21.09.2026 показал 767 сущностей там, где сведение отложило 394 — ровно вдвое,
# и это ушло бы на страницу владельцу числом компаний. Карточек при этом было
# 980, то есть верно: дубли ссылались на те же карточки, и по ним ошибка не
# видна. Считать надо по прогону, а не по всей таблице.
#
# ЧЕГО ЭТОТ ОТБОР НЕ ЗАКРЫВАЕТ, и это надо знать, а не обнаружить. Берётся прогон
# последней ОТКРЫТОЙ строки, а не последний прогон сведения вообще. Пока спорные
# сущности есть, разницы нет: сведение откладывает их заново каждый раз, и его
# прогон и есть последний. Но если очередь разойдётся — сведение не отложит
# ничего, а прежние строки останутся незакрытыми, — сюда попадёт ПРОШЛЫЙ список,
# и страница покажет работу, которой уже нет.
#
# Чинится это закрытием строк, а не запросом: закрывать отработанное — дело
# сведения и человека. Запрос отличить «спор снят» от «строку забыли закрыть»
# не может, и притворяться, что может, хуже, чем сказать об этом здесь.
# Признак, по которому это будет видно: карточек в очереди больше нуля, а прогон
# сведения в тот же день отложил ноль.
ОЧЕРЕДЬ_ИНН_SQL = """
select payload
  from sup_review
 where kind = 'entity_uncertain' and closed_at is null
   and run_id = (select run_id from sup_review
                  where kind = 'entity_uncertain' and closed_at is null
                  order by id desc limit 1)
 order by id
"""
# Отзывчивость: только действующий факт. Прежние прогоны лежат рядом со статусом
# superseded — история метрики и есть то, ради чего её заводят, но в снимок идёт
# один, текущий.
ОТЗЫВЧИВОСТЬ_SQL = """
select subject_id, value
  from sup_fact
 where subject_kind = 'entity' and field = 'rfq_stats' and status <> 'superseded'
"""


class PublishError(Exception):
    """Наружу выходят только постоянные безопасные коды, без данных."""


def require(condition, code):
    if not condition:
        raise PublishError(code)


# ── Сборка снимка ────────────────────────────────────────────────────────────

def номер_карточки(ключ) -> str:
    """Номер карточки компании портала из ключа очереди: «bitrix:123» → «123».

    Сведение кладёт в payload ключи вида «bitrix:123» (load_supplier_master,
    Сущность.ключи), а страница строит из них ссылку на карточку. С префиксом
    ссылка вела в никуда: …/company/details/bitrix%3A123/. Всё, что после
    префикса не число, — не карточка; ноль — тоже (crm_id() в base/fetch_rfq.py
    так обозначает её отсутствие)."""
    if ключ is None:
        return ""
    s = str(ключ).strip()
    if s.startswith("bitrix:"):
        s = s[len("bitrix:"):]
    return s if s.isdigit() and int(s) > 0 else ""


# КТО «ЖДЁТ ИНН» — критерий папки на странице. Владелец 24.09.2026: «убери всех
# тех, кто нераспределённо дожидается ИНН, в отдельную папку».
#
# В папку идут:
#   • отложенные сведением (inn_queue): их нет в sup_entity вовсе, номер не
#     выдан, спор снимет только ИНН (load_supplier_master.спорная);
#   • записи реестра без вечного номера (numbered = false): не распределены.
#
# В папку НЕ идут кандидаты сведения с номером — одиночки («один источник») и
# «проверить нечем». Номер им выдан навсегда, ИНН для сведения им не нужен
# (спорная() их не держит), и это большая часть реестра: убрать их значило бы
# убрать со страницы почти всех поставщиков. Их неуверенность видна в колонке
# «Чем слито», а не прячется.
def ждёт_инн(номер_выдан) -> bool:
    return not номер_выдан


def чистые(значения, пусто=("",)):
    """Множество непустых строк из сырого списка payload.

    None отсеивается ДО str(), а не после: str(None) — это «None», строка
    непустая, и она проходит любую проверку на истинность. Так в список имён
    попала бы запись «None», а в список карточек — ссылка в никуда. Поймано
    собственным тестом на мусорных значениях.

    Ноль для карточки — тоже отсутствие, а не карточка: именно его возвращает
    crm_id() в base/fetch_rfq.py для None, "" и "0".
    """
    out = set()
    for v in (значения or []):
        if v is None:
            continue
        s = str(v).strip()
        if s not in пусто:
            out.add(s)
    return out


def собрать(строки, признаки, открытых_в_очереди, отзывчивость=(), ждут_инн=()):
    """Сущности + их признаки → снимок. Чистая функция: тестируется без базы."""
    rfq = {sid: v for sid, v in отзывчивость}
    по_сущности = collections.defaultdict(lambda: collections.defaultdict(list))
    источники = collections.defaultdict(set)
    for sup_id, kind, value, source in признаки:
        по_сущности[sup_id][kind].append(value)
        источники[sup_id].add(source)

    сущности = []
    с_инн = многодоменных = с_историей = измеримых = 0
    # Хвост строки — источник имени (с 24.09.2026); прежняя форма строки без
    # него тоже читается.
    for sid, имя, страна, причина, статус, _resolution, номер_выдан, *хвост in строки:
        мои = по_сущности.get(sid, {})
        домены = sorted(set(мои.get("domain", [])))
        инн = sorted(set(мои.get("inn", [])))
        if инн:
            с_инн += 1
        if len(домены) > 1:
            многодоменных += 1
        запись = {
            "number": sid,
            "name": имя,
            # Несколько ИНН у одной сущности — это противоречие, а не деталь:
            # показываем оба, а не первый попавшийся, иначе ошибка невидима.
            "inn": ", ".join(инн) if инн else None,
            "domain": ", ".join(домены) if домены else None,
            "country": страна,
            "sources": sorted(источники.get(sid, ())),
            "merged_by": причина,
            "status": статус,
        }
        if хвост and хвост[0]:
            запись["name_from"] = хвост[0]
        if ждёт_инн(номер_выдан):
            запись["wait_inn"] = True
        # Отзывчивость отдаётся числителем и знаменателем, а не долей: «50 %» из
        # двух запросов и из сорока — разные утверждения, и страница обязана
        # уметь их различить. Доля считается там, где показывается.
        стат = rfq.get(sid)
        if стат and стат.get("sent"):
            запись["rfq"] = стат
            с_историей += 1
            if стат["sent"] >= 3:
                измеримых += 1
        if not номер_выдан:
            запись["number"] = None
        сущности.append(запись)

    снимок = {
        "version": 1,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": {
            "entities": len(сущности),
            "numbered": sum(1 for e in сущности if e["number"]),
            "wait_inn": sum(1 for e in сущности if e.get("wait_inn")),
            # Имя счётчика — по тому, что он считает. В очереди проверки лежат не
            # только строки, ждущие ИНН: там же спорные слияния и всё прочее.
            # Подпись «ждут ИНН» на этой цифре была бы враньём.
            "review_open": открытых_в_очереди,
            "with_inn": с_инн,
            "with_rfq": с_историей,
            "rfq_measurable": измеримых,
        },
        "entities": сущности,
    }
    # ОЧЕРЕДЬ НА ИНН — СПИСКОМ, А НЕ ЧИСЛОМ. Карточки берутся из payload как
    # есть: пересчитывать их здесь значило бы завести второй источник правды,
    # который разойдётся с первым. Счётчик карточек — distinct: одна карточка,
    # попавшая к двум сущностям, это одна единица ручной работы, а не две.
    очередь = []
    карточки = set()
    for строка in ждут_инн:
        # Ноль отбрасывается наравне с пустым: это не карточка, а её
        # отсутствие — так его и возвращает crm_id() в base/fetch_rfq.py
        # для None, "" и "0". Отправить человека открывать карточку 0
        # значит послать его в никуда.
        ключи = sorted({номер_карточки(k) for k in (строка.get("keys") or [])} - {""})
        if not ключи:
            continue                      # без карточки заполнять нечего
        карточки.update(ключи)
        очередь.append({
            "reason": строка.get("reason"),
            "names": sorted(чистые(строка.get("names"))),
            "cards": ключи,
        })
    if очередь:
        снимок["inn_queue"] = очередь
        снимок["totals"]["inn_entities"] = len(очередь)
        снимок["totals"]["inn_cards"] = len(карточки)
    # Оговорка считается, а не пишется руками: сущность с двумя разными доменами
    # — это склеенные компании (дефект опознания, разобранный 20.09.2026). Пока
    # такие есть, страница обязана об этом говорить, а не молчать.
    if многодоменных:
        снимок["caveat"] = (
            f"{многодоменных} записей имеют более одного домена — это признак склеенных "
            "компаний, а не одной с несколькими сайтами. Такие строки проверяются вручную; "
            "правило опознания уже исправлено, новые прогоны их не создают."
        )
    return снимок


def читать_базу(dsn):
    require(isinstance(dsn, str) and bool(dsn), "SUPABASE_DB_URL_MISSING")
    import psycopg2

    # statement_timeout — в параметрах подключения, а не через SET: SET внутри
    # транзакции откатывается вместе с ней (CLAUDE.md, правило 9).
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=15000")
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            есть = company_names.вид_имён_есть(cur)
            print(f"имена для показа: {'вид ' + company_names.ВИД_ИМЁН if есть else 'вида нет — display_name'}")
            cur.execute(company_names.имена_sql(СУЩНОСТИ_SQL, есть))
            строки = cur.fetchall()
            cur.execute(ПРИЗНАКИ_SQL, (list(ПОКАЗЫВАЕМ),))
            признаки = cur.fetchall()
            cur.execute("select to_regclass('sup_display_name') is not null")
            if cur.fetchone()[0]:
                cur.execute(ИНН_РЕКВИЗИТОВ_SQL)
                признаки = признаки + cur.fetchall()
            cur.execute(ОЧЕРЕДЬ_SQL)
            очередь = cur.fetchone()[0]
            cur.execute(ОТЗЫВЧИВОСТЬ_SQL)
            отзывчивость = cur.fetchall()
            cur.execute(ОЧЕРЕДЬ_ИНН_SQL)
            ждут_инн = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    return строки, признаки, очередь, отзывчивость, ждут_инн


# ── Cloudflare ───────────────────────────────────────────────────────────────

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PublishError("CLOUDFLARE_REDIRECT_REJECTED")


class Cloudflare:
    """Клиент KV. Повтор — только для чтения: повторённый PUT затрёт чужую запись."""

    def __init__(self, account, token, opener=None, sleep=time.sleep):
        require(isinstance(account, str) and bool(CF_ID.fullmatch(account)), "INVALID_CLOUDFLARE_ACCOUNT")
        require(isinstance(token, str) and bool(token) and "\r" not in token and "\n" not in token,
                "CLOUDFLARE_TOKEN_MISSING")
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{account}"
        self.token = token
        self.opener = opener or request.build_opener(request.ProxyHandler({}), NoRedirect())
        self.sleep = sleep

    def call(self, method, path, body=None, missing=False):
        require(method in ("GET", "PUT") and path.startswith("/") and not path.startswith("//"),
                "INVALID_API_REQUEST")
        if body is not None:
            require(isinstance(body, bytes) and len(body) <= MAX_BYTES, "SNAPSHOT_TOO_LARGE")
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            req = request.Request(self.base + path, data=body, method=method, headers={
                "Authorization": "Bearer " + self.token, "Accept": "application/json",
                "Content-Type": "application/json"})
            try:
                with self.opener.open(req, timeout=60) as response:
                    require(response.status == 200, "CLOUDFLARE_UNEXPECTED_STATUS")
                    length = response.headers.get("Content-Length")
                    if length is not None:
                        require(length.isdigit() and int(length) <= MAX_BYTES, "RESPONSE_TOO_LARGE")
                    raw = response.read(MAX_BYTES + 1)
                    require(len(raw) <= MAX_BYTES, "RESPONSE_TOO_LARGE")
                    require(length is None or len(raw) == int(length), "INCOMPLETE_HTTP_RESPONSE")
                    return raw
            except error.HTTPError as failure:
                status = failure.code
                failure.close()
                if status == 404 and missing:
                    return None
                if status in (401, 403):
                    raise PublishError("CLOUDFLARE_KV_ACCESS_DENIED" if "/storage/kv/" in path
                                       else "CLOUDFLARE_PROJECT_ACCESS_DENIED") from None
                if 300 <= status < 400:
                    raise PublishError("CLOUDFLARE_REDIRECT_REJECTED") from None
                if status != 429 and status not in (500, 502, 503, 504):
                    raise PublishError("CLOUDFLARE_HTTP_FAILED") from None
            except (error.URLError, TimeoutError, OSError, http.client.HTTPException):
                pass
            # Таймаут может прийти ПОСЛЕ удачной записи. Повторять PUT вслепую
            # нельзя: перечитываем значение и сверяем (как у библиотеки).
            if method == "PUT":
                raise PublishError("CLOUDFLARE_WRITE_OUTCOME_UNCONFIRMED") from None
            if attempt < attempts - 1:
                self.sleep((2, 5)[attempt])
        raise PublishError("CLOUDFLARE_RETRIES_EXHAUSTED")

    def envelope(self, method, path, body=None):
        raw = self.call(method, path, body)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PublishError("CLOUDFLARE_BAD_JSON") from None
        require(isinstance(value, dict) and value.get("success") is True, "CLOUDFLARE_API_FAILED")
        return value

    def namespace(self):
        result = self.envelope("GET", f"/pages/projects/{PAGES_PROJECT}").get("result")
        try:
            namespaces = result["deployment_configs"]["production"]["kv_namespaces"]
            require(isinstance(namespaces, dict), "INVALID_KV_BINDING")
            имя = next((n for n in ПРИВЯЗКИ if n in namespaces), None)
            if имя is None:
                raise PublishError("KV_BINDING_MISSING")
            namespace = namespaces[имя]["namespace_id"]
            print(f"привязка KV: {имя}")
        except (KeyError, TypeError):
            raise PublishError("KV_BINDING_MISSING") from None
        require(isinstance(namespace, str) and bool(CF_ID.fullmatch(namespace)), "INVALID_KV_BINDING")
        return namespace

    # ЗАКРЫТЫЙ СПИСОК КЛЮЧЕЙ — СВОЙСТВО КЛАССА, А НЕ МОДУЛЯ. Публикатор не должен
    # иметь возможности переписать acl:v1 или library:v1 из-за опечатки. Список
    # объявлен здесь, чтобы наследник (публикатор перекрёстной системы) задал
    # СВОЙ единственный ключ, а не расширил этот: расширенный общий список
    # означал бы, что каждый публикатор может писать чужой снимок.
    КЛЮЧИ = (KEY,)

    def value_path(self, namespace, key):
        require(isinstance(namespace, str) and bool(CF_ID.fullmatch(namespace)), "INVALID_KV_BINDING")
        require(key in self.КЛЮЧИ, "INVALID_KV_KEY")
        return f"/storage/kv/namespaces/{namespace}/values/{parse.quote(key, safe='')}"

    def get(self, namespace, key):
        return self.call("GET", self.value_path(namespace, key), missing=True)

    def put(self, namespace, key, raw):
        try:
            self.envelope("PUT", self.value_path(namespace, key), raw)
        except PublishError as failure:
            if str(failure) != "CLOUDFLARE_WRITE_OUTCOME_UNCONFIRMED":
                raise
            for delay in READBACK_DELAYS:
                if delay:
                    self.sleep(delay)
                if self.get(namespace, key) == raw:
                    return
            raise PublishError("KV_READBACK_NOT_CONFIRMED") from None


# ── Прогон ───────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description="Снимок реестра поставщиков в KV")
    parser.add_argument("--apply", action="store_true", help="записать в KV (иначе вхолостую)")
    parser.add_argument("--out", help="сохранить снимок в файл (для проверки глазами)")
    args = parser.parse_args(argv)

    try:
        (строки, признаки, очередь, отзывчивость,
         ждут_инн) = читать_базу(os.environ.get("SUPABASE_DB_URL"))
        снимок = собрать(строки, признаки, очередь, отзывчивость, ждут_инн)
        raw = json.dumps(снимок, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        t = снимок["totals"]
        print(f"сущностей: {t['entities']}, с номером: {t['numbered']}, "
              f"с ИНН: {t['with_inn']}, открыто в очереди проверки: {t['review_open']}")
        print(f"с историей запросов: {t['with_rfq']}, из них измеримых (3+): "
              f"{t['rfq_measurable']}")
        # Только счётчики: ни имени компании, ни номера карточки в журнал
        # публичного репозитория не уходит (правило 17). Сам список едет в KV,
        # который читается страницей за Cloudflare Access.
        print(f"ждут ИНН: сущностей {t.get('inn_entities', 0)}, "
              f"карточек к заполнению {t.get('inn_cards', 0)}, "
              f"записей реестра без номера {t['wait_inn']}")
        по_источнику = collections.Counter(e.get("name_from") or "display_name"
                                           for e in снимок["entities"])
        print("имя взято: " + ", ".join(f"{k} {v}" for k, v in sorted(по_источнику.items())))
        print(f"признаков прочитано: {len(признаки)}, размер снимка: {len(raw)} Б "
              f"({len(raw) / 1024 / 1024:.2f} МиБ из {MAX_BYTES // 1024 // 1024})")
        if "caveat" in снимок:
            print("оговорка на странице: " + снимок["caveat"])
        require(len(raw) <= MAX_BYTES, "SNAPSHOT_TOO_LARGE")
        require(t["entities"] > 0, "SNAPSHOT_EMPTY")

        if args.out:
            with open(args.out, "wb") as fh:
                fh.write(raw)
            print(f"снимок сохранён в {args.out}")

        if not args.apply:
            print("вхолостую: в KV ничего не записано (--apply включает запись)")
            return 0

        cf = Cloudflare(os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN"))
        namespace = cf.namespace()
        прежний = cf.get(namespace, KEY)
        cf.put(namespace, KEY, raw)
        print(f"опубликовано в KV, ключ {KEY}; прежний снимок был "
              f"{'размером ' + str(len(прежний)) + ' Б' if прежний else 'пуст'}")
        return 0
    except PublishError as failure:
        print(f"ОТКАЗ: {failure}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
