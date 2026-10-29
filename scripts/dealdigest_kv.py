#!/usr/bin/env python3
"""Закрытый ключ KV dealdigest:v1 — сводка по сделке для модели (решение владельца 25.10.2026).

Сводку собирает сборка дашборда (main.py → dealdigest.build → файл DEALDIGEST_OUT); шаг
деплоя публикует её в ключ ПОСЛЕ выкладки сайта — в ключе те же числа, что на вкладке.
Ночная оценка чатов читает ключ в файл (DEALDIGEST_PATH, scripts/chat_context.py).

    python scripts/dealdigest_kv.py --publish ПУТЬ   # файл сборки → KV (deploy.yml)
    python scripts/dealdigest_kv.py --fetch ПУТЬ     # KV → файл (chat-context.yml)

В журнал — только счётчики и постоянные коды ошибок: сделок в сводке, с заказами, с
договором, с экономикой, пресейла (правило 17). Ключ Cloudflare — только у сборки и оценки
чатов, не у живой сверки (live-check.yml гоняет код правки).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import dealdigest  # noqa: E402
from realization_ctx_kv import CtxError, _kv, kv_store  # noqa: E402


def _разобрать(raw: bytes) -> dict:
    try:
        val = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CtxError("DIGEST_BAD_JSON") from None
    код = dealdigest.check(val)
    if код:
        raise CtxError(код)
    return val


def _счёт(val: dict) -> str:
    n = dealdigest.counts(val)
    return (f"сделок {n['deals']}: с заказами {n['orders']}, с договором {n['contract']}, "
            f"с экономикой {n['econ']}, пресейла {n['presale']}")


def publish(path: Path, store, печать=print) -> int:
    """Файл сборки → ключ. Нет файла (сборка без «Реализации») — ключ не трогается: прежняя
    сводка устареет сама (chat_context.СВОДКА_ЧАСОВ), а не заменится пустой."""
    if not path.exists():
        печать("сводки по сделкам нет — сборка без «Реализации»; ключ не тронут")
        return 0
    raw = path.read_bytes()
    val = _разобрать(raw)
    if not val["deals"]:
        печать("сводка по сделкам пуста — ключ не тронут")
        return 0
    store = store() if callable(store) else store       # клиент KV — только когда есть что писать
    _kv(store.put, raw)
    печать(f"сводка по сделкам опубликована ({dealdigest.KV_KEY}): {_счёт(val)}")
    return 0


def fetch(path: Path, store, печать=print) -> int:
    """Ключ → файл для оценки чатов. Нет ключа — файла нет: модель видит стадию из портала."""
    store = store() if callable(store) else store
    raw = _kv(store.get_raw)
    if raw is None:
        печать(f"сводки в KV ({dealdigest.KV_KEY}) ещё нет — модель увидит стадию из портала")
        return 0
    val = _разобрать(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    печать(f"сводка по сделкам прочитана: {_счёт(val)}, собрана {val['at'][:16]}")
    return 0


def main(argv=None, **kw) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--publish", metavar="ПУТЬ")
    g.add_argument("--fetch", metavar="ПУТЬ")
    a = ap.parse_args(argv)
    try:
        store = kw.get("store") or (lambda: kv_store(dealdigest.KV_KEY))
        if a.publish:
            return publish(Path(a.publish), store)
        return fetch(Path(a.fetch), store)
    # шаг мягкий (deploy.yml и chat-context.yml: «|| echo ::warning::») — и пометка жёлтая, а
    # не красная; сбой клиента KV не по его коду (requests и т. п.) — классом, без трассы и
    # без данных (ревью 28.10.2026)
    except CtxError as e:
        print(f"::warning::сводка по сделкам: {e}")
        return 1
    except Exception as e:                       # noqa: BLE001
        print(f"::warning::сводка по сделкам: сбой {type(e).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
