#!/usr/bin/env python3
"""Отправка файлов «Премий» владельцу в Telegram ботом оповещений портала.

Ключ бота и адрес переписки — в закрытом хранилище портала (KV, ключ notify:v1; их
задаёт владелец в /admin → «Оповещения»), а не в репозитории и не в сессии агента.
Скрипт работает внутри прогона premia-send.yml и только читает это хранилище.

Получатель проверяется до отправки: переписка личная, и имя пользователя в ней
совпадает с заданным при запуске. Имя передаётся как SHA-256 (строчными, без @),
чтобы его не было ни в репозитории, ни в журнале. Не совпало — ничего не уходит.
В журнал — только состояния: ни ключа, ни адреса, ни содержимого файлов.

    python scripts/premia_telegram.py --to-sha256 HEX ФАЙЛ [ФАЙЛ ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_suppliers import Cloudflare  # noqa: E402

NOTIFY_KEY = "notify:v1"          # тот же ключ, что у воркера портала (NOTIFY_KEY)
API = "https://api.telegram.org/bot{token}/{method}"


class NotifyReader(Cloudflare):
    """Клиент KV, которому открыт ровно один ключ — настройки оповещений, и только на чтение."""
    КЛЮЧИ = (NOTIFY_KEY,)


def name_sha(username: str) -> str:
    return hashlib.sha256(username.strip().lstrip("@").lower().encode("utf-8")).hexdigest()


def recipient_ok(chat: dict | None, to_sha256: str) -> bool:
    """Личная переписка с нужным человеком: группы и каналы не годятся, имя сверяется по хешу."""
    if not chat or chat.get("type") != "private" or not chat.get("username"):
        return False
    return name_sha(chat["username"]) == to_sha256.strip().lower()


def _call(token: str, method: str, **kw) -> dict:
    r = requests.post(API.format(token=token, method=method), timeout=120, **kw)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok or not body.get("ok"):
        raise RuntimeError(f"telegram {method}: {r.status_code}")
    return body.get("result")


def find_chat(token: str, stored: str | None, to_sha256: str) -> str | None:
    """Адрес переписки: сохранённый порталом, если это нужный человек, иначе — из свежих
    сообщений боту (Telegram держит их сутки)."""
    if stored:
        try:
            if recipient_ok(_call(token, "getChat", data={"chat_id": stored}), to_sha256):
                return str(stored)
        except RuntimeError:
            pass
    for up in reversed(_call(token, "getUpdates") or []):
        msg = up.get("message") or up.get("edited_message") or {}
        if recipient_ok(msg.get("chat"), to_sha256):
            return str(msg["chat"]["id"])
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Файлы «Премий» владельцу в Telegram")
    ap.add_argument("--to-sha256", required=True, help="SHA-256 имени получателя (строчными, без @)")
    # Проверка до часовой сборки данных: бот настроен и получатель найден. Иначе
    # прогон 19.10.2026 час собирал данные и только потом узнал, что бота нет.
    ap.add_argument("--check", action="store_true", help="только проверить бота и получателя, без отправки")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args(argv)
    if not a.check and not a.files:
        ap.error("нужны файлы для отправки или --check")
    try:
        cf = NotifyReader(os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_API_TOKEN"))
        raw = cf.get(cf.namespace(), NOTIFY_KEY)
        cfg = json.loads(raw.decode("utf-8")) if raw else {}
        token = str(cfg.get("tgToken") or "").strip()
        if not token:
            print("бот оповещений портала не настроен: /admin → «Оповещения»")
            return 2
        print(f"::add-mask::{token}")          # ключ не попадёт в журнал даже в тексте ошибки
        chat = find_chat(token, cfg.get("tgChat"), a.to_sha256)
        if not chat:
            print("получатель не найден или не совпал: напишите боту /start из нужного аккаунта "
                  "и запустите прогон снова")
            return 3
        if a.check:
            print("бот настроен, получатель найден")
            return 0
        day = dt.date.today().isoformat()
        for f in a.files:
            p = Path(f)
            with p.open("rb") as fh:
                _call(token, "sendDocument", data={"chat_id": chat},
                      files={"document": (f"Премии_{day}{p.suffix}", fh)})
        print(f"отправлено в Telegram: файлов {len(a.files)}")
        return 0
    except Exception as e:                     # в журнал — только класс ошибки
        print(f"отправка не удалась: {type(e).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
