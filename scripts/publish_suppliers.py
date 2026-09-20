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
from urllib import error, parse, request

PAGES_PROJECT = "kvant-sourcing-f122"
KEY = "suppliers:v1"
# Тот же предел, что у воркера (SUPPLIERS_MAX_BYTES): снимок, который воркер
# откажется читать, публиковать незачем.
MAX_BYTES = 8 * 1024 * 1024
CF_ID = re.compile(r"[a-fA-F0-9]{32}\Z")
READBACK_DELAYS = (0, 2, 5, 15, 30, 30)

# Признаки, которые попадают в снимок. Остальные виды (alias, trading, legal)
# нужны для сведения, а не для чтения: в снимке они только раздули бы объём.
ПОКАЗЫВАЕМ = ("inn", "vat", "ogrn", "domain", "bitrix")

СУЩНОСТИ_SQL = """
select e.id, e.display_name, e.country, e.note, e.status, e.resolution,
       r.seq is not null as numbered
  from sup_entity e
  left join sup_number_registry r on r.sup_id = e.id
 where e.resolution <> 'merged'
 order by e.id
"""
ПРИЗНАКИ_SQL = """
select sup_id, kind, value, source
  from sup_identifier
 where status <> 'rejected' and kind = any(%s)
 order by sup_id, kind, value
"""
ОЧЕРЕДЬ_SQL = "select count(*) from sup_review where closed_at is null"


class PublishError(Exception):
    """Наружу выходят только постоянные безопасные коды, без данных."""


def require(condition, code):
    if not condition:
        raise PublishError(code)


# ── Сборка снимка ────────────────────────────────────────────────────────────

def собрать(строки, признаки, открытых_в_очереди):
    """Сущности + их признаки → снимок. Чистая функция: тестируется без базы."""
    по_сущности = collections.defaultdict(lambda: collections.defaultdict(list))
    источники = collections.defaultdict(set)
    for sup_id, kind, value, source in признаки:
        по_сущности[sup_id][kind].append(value)
        источники[sup_id].add(source)

    сущности = []
    с_инн = многодоменных = 0
    for sid, имя, страна, причина, статус, _resolution, номер_выдан in строки:
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
        if not номер_выдан:
            запись["number"] = None
        сущности.append(запись)

    снимок = {
        "version": 1,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": {
            "entities": len(сущности),
            "numbered": sum(1 for e in сущности if e["number"]),
            "held": открытых_в_очереди,
            "with_inn": с_инн,
        },
        "entities": сущности,
    }
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
            cur.execute(СУЩНОСТИ_SQL)
            строки = cur.fetchall()
            cur.execute(ПРИЗНАКИ_SQL, (list(ПОКАЗЫВАЕМ),))
            признаки = cur.fetchall()
            cur.execute(ОЧЕРЕДЬ_SQL)
            очередь = cur.fetchone()[0]
    finally:
        conn.close()
    return строки, признаки, очередь


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
            namespace = namespaces["ACL"]["namespace_id"]
        except (KeyError, TypeError):
            raise PublishError("KV_BINDING_MISSING") from None
        require(isinstance(namespace, str) and bool(CF_ID.fullmatch(namespace)), "INVALID_KV_BINDING")
        return namespace

    def value_path(self, namespace, key):
        require(isinstance(namespace, str) and bool(CF_ID.fullmatch(namespace)), "INVALID_KV_BINDING")
        # Ключ закрытым списком: публикатор поставщиков не должен иметь
        # возможности переписать acl:v1 или library:v1 из-за опечатки.
        require(key == KEY, "INVALID_KV_KEY")
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
        строки, признаки, очередь = читать_базу(os.environ.get("SUPABASE_DB_URL"))
        снимок = собрать(строки, признаки, очередь)
        raw = json.dumps(снимок, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        t = снимок["totals"]
        print(f"сущностей: {t['entities']}, с номером: {t['numbered']}, "
              f"с ИНН: {t['with_inn']}, открыто в очереди проверки: {t['held']}")
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
