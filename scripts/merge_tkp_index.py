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

ОДИН ОХВАТ ТОЖЕ НЕЛЬЗЯ ЗАМЕНЯТЬ ЦЕЛИКОМ. Замерено 18.09.2026: прогон по «НВН»
записал 361 файл там, где прежний прогон того же охвата записал 389, и 28
записей исчезли молча — прогон дошёл не до всех файлов (таймаут, отказ
скачивания, другое состояние Bitrix). Поэтому опись внутри охвата сливается
ПО ФАЙЛУ: запись свежего прогона побеждает, запись прежнего, которой в свежем
нет, остаётся. Сколько записей пришло от прежнего прогона, видно числом в поле
`inventory_kept` — иначе усадка снова будет незаметной.
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


def key_of(item: dict) -> str:
    """Чем один файл описи отличается от другого."""
    fid = str(item.get("file_id") or "").strip()
    if fid:
        return f"id:{fid}"
    return "nm:{}|{}".format(item.get("origin") or "", item.get("file_name") or "")


def merge_scope(old: dict, new: dict) -> dict:
    """Свежий прогон поверх прежнего, но опись файлов — объединением.

    Счётчики прогона (deals, files, downloaded, state) описывают ИМЕННО этот
    прогон, поэтому берутся свежие: подменять их суммой нельзя, это было бы
    выдуманное число. А опись файлов — знание о том, что в сделках лежит, и
    оно не должно уменьшаться от того, что прогон не дошёл до части файлов.
    """
    out = dict(old or {})
    out.update({k: v for k, v in (new or {}).items() if k != "inventory"})
    by = {key_of(x): x for x in ((old or {}).get("inventory") or []) if isinstance(x, dict)}
    was = set(by)
    for x in ((new or {}).get("inventory") or []):
        if isinstance(x, dict):
            by[key_of(x)] = x
    fresh_keys = {key_of(x) for x in ((new or {}).get("inventory") or []) if isinstance(x, dict)}
    kept = len(was - fresh_keys)
    if "inventory" in (new or {}) or by:
        out["inventory"] = list(by.values())
    if kept:
        out["inventory_kept"] = kept
        out["inventory_note"] = (
            f"{kept} записей перенесены от прежнего прогона этого же охвата: свежий прогон до "
            f"них не дошёл. Счётчики выше — про свежий прогон, опись — про всё, что мы знаем.")
    else:
        out.pop("inventory_kept", None)
        out.pop("inventory_note", None)
    return out


def merge(mine: dict, into: dict) -> tuple[dict, list[str], list[str]]:
    """Итог, список обновлённых охватов и список сохранённых чужих."""
    base = scopes_of(into)
    fresh = scopes_of(mine)
    kept = [k for k in base if k not in fresh]
    for name, body in fresh.items():
        base[name] = merge_scope(base.get(name) or {}, body)
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
    carried = sum(int(v.get("inventory_kept") or 0) for v in out["scopes"].values())
    print(f"обновлено охватов: {', '.join(updated) or '—'}")
    print(f"сохранено чужих охватов: {', '.join(kept) or '—'}")
    print(f"в описи стало охватов {len(out['scopes'])}, файлов {files}")
    if carried:
        print(f"перенесено записей от прежних прогонов тех же охватов: {carried} "
              f"(свежий прогон до них не дошёл)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
