"""Токен приложения Битрикса: продление и хранение.

ЗАЧЕМ. Файлы, привязанные к сделкам, отдаются страницей портала
/bitrix/components/bitrix/crm.deal.show/show_file.php с параметром auth. Проверено
на выборке: код вебхука она не принимает — он действует только на адресах
/rest/<номер>/<код>/. Нужен токен приложения, и получить его может только
приложение портала.

УСТРОЙСТВО. При установке приложение получает пару токенов: рабочий (живёт час) и
токен продления. Рабочий продлевается обращением к oauth.bitrix.info. Токен
продления при этом ЗАМЕНЯЕТСЯ — поэтому держать его в секретах GitHub нельзя,
их не переписать из прогона. Он лежит в базе, в lib_secrets, и обновляется там же.
"""
from __future__ import annotations

import os
import time

import psycopg2
import requests

OAUTH = "https://oauth.bitrix.info/oauth/token/"
REFRESH_KEY = "bitrix_refresh_token"


def _db():
    return psycopg2.connect(os.environ["SUPABASE_DB_URL"], connect_timeout=20)


def read_secret(name: str) -> str | None:
    try:
        with _db() as c, c.cursor() as cur:
            cur.execute("select value from lib_secrets where name = %s", (name,))
            r = cur.fetchone()
            return r[0] if r else None
    except Exception:
        return None


def write_secret(name: str, value: str, note: str = "") -> None:
    with _db() as c, c.cursor() as cur:
        cur.execute("""insert into lib_secrets (name, value, note) values (%s, %s, %s)
                       on conflict (name) do update set value = excluded.value,
                       note = excluded.note, updated_at = now()""", (name, value, note))


class BitrixApp:
    """Рабочий токен с продлением. Продлевает заранее, за пять минут до конца."""

    def __init__(self) -> None:
        self.cid = os.environ.get("BITRIX_CLIENT_ID", "")
        self.secret = os.environ.get("BITRIX_CLIENT_SECRET", "")
        self.token = ""
        self.expires = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.cid and self.secret)

    def access_token(self) -> str:
        if self.token and time.time() < self.expires - 300:
            return self.token
        refresh = read_secret(REFRESH_KEY) or os.environ.get("BITRIX_REFRESH_TOKEN", "")
        if not (self.configured and refresh):
            return ""
        r = requests.get(OAUTH, params={
            "grant_type": "refresh_token",
            "client_id": self.cid,
            "client_secret": self.secret,
            "refresh_token": refresh,
        }, timeout=60)
        if not r.ok:
            raise RuntimeError(f"продление токена не удалось: http {r.status_code}")
        d = r.json()
        self.token = str(d.get("access_token") or "")
        self.expires = time.time() + float(d.get("expires_in") or 3600)
        new_refresh = str(d.get("refresh_token") or "")
        # токен продления заменяется — сохраняем сразу, иначе следующий прогон не войдёт
        if new_refresh and new_refresh != refresh:
            write_secret(REFRESH_KEY, new_refresh, "обновлён при продлении рабочего токена")
        return self.token
