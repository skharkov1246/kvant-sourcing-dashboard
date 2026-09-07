#!/usr/bin/env python3
"""Поиск значения по индексу: где лежит парт-номер, производитель, организация.

    python scripts/lookup.py 4380132                 где встречается номер
    python scripts/lookup.py 4380132 --show          плюс сами записи
    python scripts/lookup.py MOTORTECH --group oem   сузить группу
    python scripts/lookup.py "denso" --near          похожие значения (подстрока)

Группы: pn — парт-номера, oem — производители, model — модели и двигатели,
hs — коды ТН ВЭД, org — организации.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data" / "index.json"
_norm_pn = re.compile(r"[^A-Z0-9]")


def keys_for(value: str) -> list[str]:
    """Одно и то же значение ищем и как парт-номер, и как текст.
    Для цифровых номеров обе нормализации совпадают — дубликат убираем."""
    out, seen = [], set()
    for k in (_norm_pn.sub("", value.upper()), re.sub(r"\s+", " ", value.strip()).casefold()):
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Поиск значения по индексу данных")
    ap.add_argument("value", help="парт-номер, производитель, модель, организация")
    ap.add_argument("--group", choices=["pn", "oem", "model", "hs", "org"], help="сузить группу")
    ap.add_argument("--show", action="store_true", help="показать найденные записи целиком")
    ap.add_argument("--near", action="store_true", help="искать по подстроке, а не точно")
    ap.add_argument("--limit", type=int, default=12, help="сколько вхождений показать")
    a = ap.parse_args()

    if not INDEX.exists():
        print("✗ нет data/index.json — выполните: python scripts/build_index.py", file=sys.stderr)
        return 1
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    files, index = idx["files"], idx["index"]
    groups = [a.group] if a.group else list(index)

    found: list[tuple[str, str, list]] = []
    if a.near:
        needle = a.value.casefold()
        for g in groups:
            for k, post in index.get(g, {}).items():
                if needle in k:
                    found.append((g, k, post))
                    if len(found) >= 40:
                        break
    else:
        for g in groups:
            for k in keys_for(a.value):
                if k in index.get(g, {}):
                    found.append((g, k, index[g][k]))

    if not found:
        print(f"«{a.value}» в индексе нет. Индекс: {idx['summary']['values']} значений "
              f"({', '.join(f'{g} {n}' for g, n in idx['summary']['by_group'].items())}).")
        print("Подсказка: --near ищет по подстроке.")
        return 2

    for g, key, postings in found[:8]:
        print(f"\n=== {g}: «{key}» — {len(postings)} вхождений ===")
        seen: dict[int, int] = {}
        for fi, _ri in postings:
            seen[fi] = seen.get(fi, 0) + 1
        for fi, n in sorted(seen.items(), key=lambda x: -x[1]):
            f = files[fi]
            mark = " ⚠конфиденциально" if f.get("sensitivity") == "конфиденциально" else ""
            print(f"  {f['path']:44s} {n:4d} зап. · {f['subproject']}{mark}")
        if a.show:
            shown = 0
            for fi, ri in postings:
                if shown >= a.limit:
                    break
                f = files[fi]
                try:
                    data = json.loads((ROOT / f["path"]).read_text(encoding="utf-8"))
                    rows = data if isinstance(data, list) else data[f["collection"]]
                    row = rows[ri]
                except Exception as e:
                    print(f"  (не прочитать {f['path']}: {e})")
                    continue
                short = {k: (str(v)[:70] + "…" if len(str(v)) > 70 else v)
                         for k, v in list(row.items())[:8]}
                print(f"\n  → {f['path']} [{ri}]")
                for k, v in short.items():
                    print(f"      {k}: {v}")
                shown += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
