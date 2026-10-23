#!/usr/bin/env python3
"""Закрытый ключ KV realctx:v1 — клиент и чтение для сборки дашборда.

Свод оценок чатов и разбора договоров по сделкам пишет scripts/realization_ctx.py
(--publish, из закрытой базы). Сборке (deploy.yml) нужно только прочитать ключ в файл —
без базы и без разбора договоров, поэтому чтение живёт здесь отдельно.

    python scripts/realization_ctx.py --fetch ПУТЬ   # то же, через общий вход
    python scripts/realization_ctx_kv.py ПУТЬ        # KV → файл (dealctx.load, REALCTX_PATH)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dealctx  # noqa: E402  (форма свода — общая со сборкой)

KV_KEY = "realctx:v1"


class CtxError(Exception):
    """Наружу выходит только постоянный код, без данных."""


class KVStore:
    """Один ключ KV (realctx:v1). Клиент — общий с публикаторами (publish_suppliers.Cloudflare):
    та же привязка, что у портала, закрытый список ключей, повтор только для чтения."""

    def __init__(self, cf, namespace, delays):
        self.cf, self.ns, self.delays = cf, namespace, delays

    def get_raw(self) -> bytes | None:
        return self.cf.get(self.ns, KV_KEY)

    def put(self, raw: bytes) -> None:
        self.cf.put(self.ns, KV_KEY, raw)
        for delay in self.delays:
            if delay:
                self.cf.sleep(delay)
            if self.cf.get(self.ns, KV_KEY) == raw:
                return
        raise CtxError("KV_READBACK_MISMATCH")


def kv_store() -> KVStore:
    spec = importlib.util.spec_from_file_location("kvant_publish_suppliers", ROOT / "scripts" / "publish_suppliers.py")
    ps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ps)

    class CloudflareCtx(ps.Cloudflare):
        КЛЮЧИ = (KV_KEY,)

    try:
        cf = CloudflareCtx(os.environ.get("CLOUDFLARE_ACCOUNT_ID", ""), os.environ.get("CLOUDFLARE_API_TOKEN", ""))
        return KVStore(cf, cf.namespace(), ps.READBACK_DELAYS)
    except ps.PublishError as e:
        raise CtxError(str(e)) from None


def _kv(fn, *args):
    """Ошибки клиента KV (его PublishError) → свой код, без данных."""
    try:
        return fn(*args)
    except CtxError:
        raise
    except Exception as e:
        if type(e).__name__ == "PublishError":
            raise CtxError(str(e)) from None
        raise


def fetch(path: Path, store, печать=print) -> int:
    """Ключ → файл для сборки дашборда (dealctx.load, путь REALCTX_PATH)."""
    raw = _kv(store.get_raw)
    if raw is None:
        печать(f"свода в KV ({KV_KEY}) ещё нет — «Реализация» соберётся без чатов и договоров")
        return 0
    try:
        val = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CtxError("KV_BAD_JSON") from None
    разбор = dealctx.clean(val)
    if разбор is None:
        raise CtxError("KV_BAD_VERSION")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    печать(f"свод чатов и договоров прочитан: чатов {len(разбор['chats'])}, сделок с договорами "
           f"{len(разбор['contracts'])}, записей не по форме {разбор['bad']}")
    return 0




def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("::error::нужен один аргумент — путь файла")
        return 2
    try:
        return fetch(Path(argv[0]), kv_store())
    except CtxError as e:
        print(f"::error::свод чатов и договоров: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
