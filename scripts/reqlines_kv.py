#!/usr/bin/env python3
"""Закрытый ключ KV reqlines:v1 — строки файлов «Request file» (reqlines.py, решение владельца 31.10.2026).

    python scripts/reqlines_kv.py --fetch ПУТЬ     # KV → файл (REQLINES_PATH, до main.py)
    python scripts/reqlines_kv.py --publish ПУТЬ   # кэш сборки (REQLINES_OUT) → KV

--publish читает ключ заново и сливает (новые номера файлов дописываются, прежние не теряются) и
пишет. Ключ не той версии или не JSON — не перезаписывается. Нет файла сборки — ключ не трогается.
В ключе и в журнале — только номера файлов, числа строк и хэши (правило 17). Шаги мягкие
(«|| echo ::warning::»): деплой не падает, без ключа строки файлов досчитаются следующими сборками.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import reqlines  # noqa: E402
from realization_ctx_kv import CtxError, _kv, kv_store  # noqa: E402


def _кэш(raw: bytes | None) -> dict:
    if raw is None:
        return {"v": 1, "f": {}}
    try:
        val = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CtxError("REQLINES_BAD_JSON") from None
    if not isinstance(val, dict) or val.get("v") != 1 or not isinstance(val.get("f"), dict):
        raise CtxError("REQLINES_BAD_VERSION")
    return val


def fetch(path: Path, store, печать=print) -> int:
    store = store() if callable(store) else store
    raw = _kv(store.get_raw)
    if raw is None:
        печать(f"кэша строк в KV ({reqlines.KV_KEY}) ещё нет — файлы разберёт сборка (до {reqlines.БЮДЖЕТ} за раз)")
        return 0
    c = _кэш(raw)
    path.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    печать(f"кэш строк запроса прочитан: файлов {len(c['f'])}")
    return 0


def publish(path: Path, store, печать=print) -> int:
    if not path.exists():
        печать("кэша сборки нет — строки запроса не считались; ключ не тронут")
        return 0
    new = reqlines.load_cache(str(path))
    store = store() if callable(store) else store
    old = _кэш(_kv(store.get_raw))
    merged = {"v": 1, "f": {**old["f"], **new["f"]}}
    if len(merged["f"]) == len(old["f"]) and merged["f"] == old["f"]:
        печать(f"кэш строк запроса не изменился ({len(old['f'])} файлов)")
        return 0
    _kv(store.put, json.dumps(merged, ensure_ascii=False).encode("utf-8"))
    печать(f"кэш строк запроса записан ({reqlines.KV_KEY}): файлов {len(merged['f'])}, новых {len(merged['f']) - len(old['f'])}")
    return 0


def main(argv=None, **kw) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--publish", metavar="ПУТЬ")
    g.add_argument("--fetch", metavar="ПУТЬ")
    a = ap.parse_args(argv)
    try:
        store = kw.get("store") or (lambda: kv_store(reqlines.KV_KEY))
        if a.publish:
            return publish(Path(a.publish), store)
        return fetch(Path(a.fetch), store)
    except CtxError as e:
        print(f"::warning::строки запроса: {e}")
        return 1
    except Exception as e:                       # noqa: BLE001 — классом, без трассы и данных
        print(f"::warning::строки запроса: сбой {type(e).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
