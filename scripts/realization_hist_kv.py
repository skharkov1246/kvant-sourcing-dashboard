#!/usr/bin/env python3
"""Закрытый ключ KV rzhist:v1 — история счётов «Реализации» (решение владельца 30.10.2026, п. 1).

    python scripts/realization_hist_kv.py --fetch ПУТЬ     # KV → файл (RZHIST_PATH, до main.py)
    python scripts/realization_hist_kv.py --publish ПУТЬ   # точка сборки (RZHIST_OUT) → KV

--publish читает ключ заново (а не файл --fetch: тот мог не прочитаться), сливает сегодняшнюю
точку (rzhist.merge: одна точка на день, тот же день — перезапись, не больше 400 дней) и
пишет со сверкой чтением. Ключ не той версии или не JSON — не перезаписывается (история не
теряется из-за сбоя). Нет файла точки (сборка без «Реализации») — ключ не трогается.

В ключ и в журнал — только счёты и даты точек (правило 17). Ключ Cloudflare — только у сборки
(deploy.yml), не у живой сверки. Шаги мягкие («|| echo ::warning::»): деплой не падает.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import rzhist  # noqa: E402
from realization_ctx_kv import CtxError, _kv, kv_store  # noqa: E402


def _история(raw: bytes | None) -> tuple[dict, int]:
    if raw is None:
        return rzhist.empty(), 0
    try:
        val = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CtxError("HIST_BAD_JSON") from None
    h, bad = rzhist.clean(val)
    if h is None:
        raise CtxError("HIST_BAD_VERSION")
    return h, bad


def _счёт(h: dict, bad: int) -> str:
    pts = h["pts"]
    return (f"точек {len(pts)}" + (f" с {pts[0]['d']} по {pts[-1]['d']}" if pts else "")
            + f", не по форме {bad}")


def fetch(path: Path, store, печать=print) -> int:
    """Ключ → файл истории для сборки. Нет ключа — файла нет: страница покажет «история с»."""
    store = store() if callable(store) else store
    raw = _kv(store.get_raw)
    if raw is None:
        печать(f"истории в KV ({rzhist.KV_KEY}) ещё нет — «Реализация» соберётся с одной сегодняшней точкой")
        return 0
    h, bad = _история(raw)
    rzhist.write(path, h)
    печать(f"история счётов «Реализации» прочитана: {_счёт(h, bad)}")
    return 0


def publish(path: Path, store, печать=print) -> int:
    """Точка сборки → ключ (слияние с ключом, прочитанным сейчас)."""
    if not path.exists():
        печать("точки истории нет — сборка без «Реализации»; ключ не тронут")
        return 0
    try:
        pt = rzhist.clean_point(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pt = None
    if pt is None:
        raise CtxError("POINT_BAD_FORM")
    store = store() if callable(store) else store      # клиент KV — только когда есть что писать
    h, bad = _история(_kv(store.get_raw))
    new = rzhist.merge(h, pt)
    _kv(store.put, rzhist.dump(new))
    печать(f"история счётов «Реализации» записана ({rzhist.KV_KEY}): {_счёт(new, bad)}; сегодня — "
           f"{rzhist.log_line(pt, None).rsplit(';', 1)[0]}")
    return 0


def main(argv=None, **kw) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--publish", metavar="ПУТЬ")
    g.add_argument("--fetch", metavar="ПУТЬ")
    a = ap.parse_args(argv)
    try:
        store = kw.get("store") or (lambda: kv_store(rzhist.KV_KEY))
        if a.publish:
            return publish(Path(a.publish), store)
        return fetch(Path(a.fetch), store)
    except CtxError as e:
        print(f"::warning::история счётов «Реализации»: {e}")
        return 1
    except Exception as e:                       # noqa: BLE001 — классом, без трассы и данных
        print(f"::warning::история счётов «Реализации»: сбой {type(e).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
