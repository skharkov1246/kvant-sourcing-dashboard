#!/usr/bin/env python3
"""Каталог данных репозитория: data/catalog.json.

Зачем: данные разбросаны по 8 подпроектам (data/, ove/data/, gt/data/, zip/data/,
zip/customs/out/, gpu/data/, gidromet/data/, pnw/), нигде не описаны, и понять
«что у нас вообще есть, свежее ли оно и можно ли это публиковать» без чтения
кода невозможно. Каталог собирается сканированием, а не руками, поэтому не врёт.

Для каждого набора данных пишем: путь, размер, число записей, ключи, кто и когда
обновлял (git), какие файлы его читают, и оценку чувствительности.

  python scripts/build_catalog.py            # пересобрать data/catalog.json
  python scripts/build_catalog.py --check    # проверить актуальность (для CI)

Ручные пояснения к наборам живут в data/catalog_notes.json и не затираются.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "catalog.json"
NOTES = ROOT / "data" / "catalog_notes.json"

# где искать данные: каталог → к какому подпроекту относится
DATA_DIRS = {
    "data": "дашборд сорсеров",
    "ove/data": "ОВЭ-75",
    "gt/data": "ГТУ-библиотека",
    "zip/data": "база ЗИП",
    "zip/customs/out": "база ЗИП · таможня",
    "gpu/data": "ГПУ-библиотека",
    "gidromet/data": "гидрометаллургия",
    "pnw/public": "каталог PN (веб)",
}
CODE_EXT = {".py", ".js", ".mjs", ".html", ".yml", ".yaml", ".toml"}
MIN_BYTES = 512                      # мельче — не набор данных, а настройка

# признаки чувствительности: поле в данных → что это значит
SENSITIVE_MARKERS = [
    (re.compile(r'"(e?mail|email)"\s*:\s*"[^"@]+@', re.I), "адреса электронной почты"),
    (re.compile(r'"(phone|contact_phone|tel)"\s*:\s*"[+0-9]', re.I), "телефоны"),
    (re.compile(r'"inn"\s*:\s*"?\d{9,12}', re.I), "ИНН контрагентов"),
    (re.compile(r'"(revenue|margin|margin_pct|deal_profit|op_profit|purchase|custval)"\s*:', re.I),
     "выручка и маржа сделок"),
    (re.compile(r'"(executor|assessment|eng|prof)"\s*:', re.I), "оценки и данные сотрудников"),
    (re.compile(r'"(importer|exporter)"\s*:\s*"', re.I), "участники внешнеэкономических сделок"),
]


def sh(*args: str) -> str:
    try:
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


def git_meta(rel: str) -> dict:
    line = sh("git", "log", "-1", "--format=%ad|%an|%h", "--date=short", "--", rel)
    if not line or "|" not in line:
        return {"last_change": None, "last_author": None, "last_commit": None, "updated_by": "неизвестно"}
    date, author, sha = line.split("|", 2)
    bot = "bot" in author.lower() or "actions" in author.lower()
    return {"last_change": date, "last_author": author, "last_commit": sha,
            "updated_by": "бот (workflow)" if bot else "человек/агент"}


def describe_json(path: Path) -> dict:
    """Форма данных: число записей и ключи — без выгрузки содержимого в каталог."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"format": "json", "parse_error": str(e)[:120]}
    if isinstance(data, list):
        keys = sorted({k for row in data[:50] if isinstance(row, dict) for k in row})
        return {"format": "json", "shape": "список", "records": len(data), "record_keys": keys[:40]}
    if isinstance(data, dict):
        out = {"format": "json", "shape": "объект", "top_keys": sorted(data)[:40]}
        biggest = max(((k, v) for k, v in data.items() if isinstance(v, (list, dict))),
                      key=lambda kv: len(kv[1]), default=None)
        if biggest:
            k, v = biggest
            out["main_collection"] = {"key": k, "records": len(v)}
            if isinstance(v, list) and v and isinstance(v[0], dict):
                out["main_collection"]["record_keys"] = sorted(v[0])[:40]
        return out
    return {"format": "json", "shape": type(data).__name__}


def sensitivity(path: Path, size: int) -> dict:
    head = path.read_bytes()[: 2_000_000].decode("utf-8", "replace")
    found = [label for rx, label in SENSITIVE_MARKERS if rx.search(head)]
    level = "конфиденциально" if found else "внутреннее"
    if not found and size < 20_000:
        level = "публикуемо"
    return {"level": level, "markers": found}


def consumers(rel: str, code_files: list[Path]) -> list[str]:
    """Какие файлы кода упоминают этот набор данных."""
    name = Path(rel).name
    hits = []
    for f in code_files:
        try:
            if name in f.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(f.relative_to(ROOT)))
        except Exception:
            continue
    return sorted(hits)[:10]


def build() -> dict:
    tracked = [ROOT / p for p in sh("git", "ls-files").splitlines() if p]
    code_files = [f for f in tracked if f.suffix in CODE_EXT and f.exists()
                  and "public/index.html" not in str(f) and f.stat().st_size < 3_000_000]
    notes = json.loads(NOTES.read_text(encoding="utf-8")) if NOTES.exists() else {}

    datasets = []
    for dirname, subproject in DATA_DIRS.items():
        d = ROOT / dirname
        if not d.exists():
            continue
        for path in sorted(d.rglob("*")):
            if not path.is_file() or path.suffix not in (".json", ".js", ".csv"):
                continue
            size = path.stat().st_size
            if size < MIN_BYTES:
                continue
            rel = str(path.relative_to(ROOT))
            if rel == str(OUT.relative_to(ROOT)):
                continue
            entry = {
                "path": rel,
                "subproject": subproject,
                "bytes": size,
                "note": notes.get(rel, ""),
            }
            entry.update(describe_json(path) if path.suffix == ".json" else {"format": path.suffix.lstrip(".")})
            entry.update(git_meta(rel))
            entry["sensitivity"] = sensitivity(path, size)
            entry["referenced_by"] = consumers(rel, code_files)
            datasets.append(entry)

    datasets.sort(key=lambda e: (-e["bytes"], e["path"]))
    by_level: dict[str, int] = {}
    for e in datasets:
        lvl = e["sensitivity"]["level"]
        by_level[lvl] = by_level.get(lvl, 0) + 1
    return {
        "schema": "kvant.data-catalog/1",
        "repo": "skharkov1246/kvant-sourcing-dashboard",
        "generated_by": "scripts/build_catalog.py",
        "how_to_refresh": "python scripts/build_catalog.py",
        "summary": {
            "datasets": len(datasets),
            "total_bytes": sum(e["bytes"] for e in datasets),
            "by_sensitivity": by_level,
            "by_subproject": {sp: sum(1 for e in datasets if e["subproject"] == sp)
                              for sp in sorted({e["subproject"] for e in datasets})},
        },
        "datasets": datasets,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Сборка каталога данных репозитория")
    ap.add_argument("--check", action="store_true", help="только проверить, что каталог актуален")
    a = ap.parse_args()

    fresh = build()
    text = json.dumps(fresh, ensure_ascii=False, indent=2) + "\n"
    if a.check:
        if not OUT.exists():
            print(f"✗ нет {OUT.relative_to(ROOT)} — выполните: python scripts/build_catalog.py", file=sys.stderr)
            return 1
        old = json.loads(OUT.read_text(encoding="utf-8"))
        # сравниваем состав и форму, а не метаданные git (они меняются от коммита к коммиту)
        strip = lambda c: [{k: v for k, v in d.items() if k not in ("last_change", "last_author", "last_commit")}
                           for d in c["datasets"]]
        if strip(old) != strip(fresh):
            print("✗ каталог данных устарел — выполните: python scripts/build_catalog.py", file=sys.stderr)
            return 1
        print(f"✓ каталог актуален: {len(fresh['datasets'])} наборов")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    s = fresh["summary"]
    print(f"✓ {OUT.relative_to(ROOT)}: {s['datasets']} наборов, "
          f"{s['total_bytes'] / 1e6:.1f} МБ, по чувствительности: {s['by_sensitivity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
