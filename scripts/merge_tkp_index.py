#!/usr/bin/env python3
"""Сливает опись входящих КП по охватам: своё добавить, чужое не потерять.

Зачем. Опись `gt/data/bitrix_tkp_index.json` пишут и прогон в Actions, и я в
сессии, и прогоны длятся минуты — коммиты сталкиваются. Перебазирование тут не
работает: конфликт в одном и том же файле, и `git pull --rebase` падает, а
прогон 17.09.2026 из-за этого дважды потерял готовую опись (commit прошёл,
push отбился «fetch first»).

Правильное слияние для этого файла — не текстовое, а по смыслу: у описи ключ
верхнего уровня `scopes`, и каждый охват (ключевое слово или id сделки) свой.
Прогон обновляет ТОЛЬКО свой охват, чужие переносит как есть.

    python scripts/merge_tkp_index.py --mine /tmp/mine.json --into gt/data/bitrix_tkp_index.json

Старый однопрогонный формат (с верхним `inventory`) переносится в охват
«(прежний прогон)», чтобы прежние цифры не исчезли молча.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LEGACY = "(прежний прогон)"


def scopes_of(doc: dict) -> dict:
    """Охваты документа, с переносом старого формата."""
    if not isinstance(doc, dict):
        return {}
    sc = doc.get("scopes")
    if isinstance(sc, dict):
        return dict(sc)
    if doc.get("inventory"):
        return {LEGACY: {k: doc[k] for k in
                         ("updated", "state", "deals", "rfq_items", "files",
                          "downloaded", "inventory") if k in doc}}
    return {}


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def merge(mine: dict, into: dict) -> tuple[dict, list[str], list[str]]:
    """Итог, список обновлённых охватов и список сохранённых чужих."""
    base = scopes_of(into)
    fresh = scopes_of(mine)
    kept = [k for k in base if k not in fresh]
    base.update(fresh)
    out = {
        "updated": mine.get("updated") or into.get("updated") or "",
        "source": mine.get("source") or into.get("source")
        or "Bitrix24: опись входящих КП, адресный обход по сделкам, БЕЗ цен",
        "method": mine.get("method") or into.get("method") or "",
        "scopes": base,
    }
    return out, sorted(fresh), sorted(kept)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mine", required=True, help="опись этого прогона")
    ap.add_argument("--into", required=True, help="опись из репозитория, куда сливать")
    a = ap.parse_args()
    mine, into = load(Path(a.mine)), load(Path(a.into))
    if not scopes_of(mine):
        print("в моей описи нет ни одного охвата — сливать нечего", file=sys.stderr)
        return 1
    out, updated, kept = merge(mine, into)
    Path(a.into).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    files = sum(len(v.get("inventory") or []) for v in out["scopes"].values())
    print(f"обновлено охватов: {', '.join(updated) or '—'}")
    print(f"сохранено чужих охватов: {', '.join(kept) or '—'}")
    print(f"в описи стало охватов {len(out['scopes'])}, файлов {files}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
