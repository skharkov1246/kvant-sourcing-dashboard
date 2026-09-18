#!/usr/bin/env python3
"""Слияние счётчиков «КП поставщиков против наших вилок» по охватам.

Тот же случай, что у описи входящих КП (scripts/merge_tkp_index.py), и та же
цена ошибки: прогон длится десятки минут, за это время в ветку ложатся чужие
коммиты в ТОТ ЖЕ файл, а буквальная запись своего файла поверх уносит чужой
охват целиком. У счётчиков это опаснее, чем кажется: цифры по «Энергосети»
стоят в листе решений владельцу, и прогон по «НВН» стирал бы их молча.

Охваты — разные заявки. Складывать их нельзя, поэтому слияние идёт по ключу
охвата: свой охват заменяется целиком, чужие не трогаются.

    python scripts/merge_offer_stats.py --mine <мой файл> --into <файл в репозитории>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KEEP = ("updated", "source", "method")


def scopes(doc: dict) -> dict:
    """Охваты документа, с поддержкой старой одноохватной формы."""
    if isinstance(doc.get("scopes"), dict):
        return doc["scopes"]
    if doc.get("rows_with_offer") is not None:          # форма до разделения
        name = doc.get("scope") or "без охвата"
        body = {k: v for k, v in doc.items() if k not in ("scope", *KEEP)}
        return {name: body}
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mine", required=True)
    ap.add_argument("--into", required=True)
    a = ap.parse_args()

    mine = json.loads(Path(a.mine).read_text(encoding="utf-8"))
    add = scopes(mine)
    if not add:
        print("в моём файле нет ни одного охвата — сливать нечего", file=sys.stderr)
        return 1

    dst = Path(a.into)
    base = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    out = {k: mine.get(k) or base.get(k) for k in KEEP}
    out["scopes"] = dict(scopes(base))
    for name, body in add.items():
        out["scopes"][name] = body
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("охваты в файле: " + ", ".join(
        f"{k} ({(v or {}).get('rows_with_offer', 0)} строк с КП)"
        for k, v in sorted(out["scopes"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
