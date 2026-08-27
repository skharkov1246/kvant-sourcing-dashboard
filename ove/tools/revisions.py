#!/usr/bin/env python3
"""Ревизии документов ОВЭ-75 — срезы для согласования.

Каждая ревизия — полная копия всех сгенерированных документов (docx/pdf)
в ove/public/docs/rev/r{N}/ + запись в ove/data/revisions.json с динамикой
по файлам (новый/изменён/без изменений/удалён — по sha256 против прошлой
ревизии). Git хранит каждый коммит, ревизии — крупные согласуемые срезы.

Запуск:  python3 ove/tools/revisions.py "Метка ревизии" ["заметка"]
Ревизию режем руками на вехах, не в сборке — чтобы архив не зашумлялся.
"""
import hashlib
import json
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "public" / "docs"
REV = DOCS / "rev"
MANIFEST = ROOT / "data" / "revisions.json"
EXT = {".docx", ".pdf", ".xlsx"}


def _files():
    for p in sorted(DOCS.rglob("*")):
        if p.is_file() and p.suffix in EXT and REV not in p.parents:
            yield p


def cut(label: str, note: str = "") -> None:
    man = {"revisions": []}
    if MANIFEST.exists():
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    prev = man["revisions"][-1] if man["revisions"] else None
    prev_sha = {f["path"]: f["sha"] for f in prev["files"]} if prev else {}

    n = len(man["revisions"])
    dst = REV / f"r{n}"
    if dst.exists():
        raise SystemExit(f"r{n} уже существует — ревизии не перезаписываются")

    files, counts = [], {"new": 0, "changed": 0, "same": 0}
    for p in _files():
        rel = p.relative_to(DOCS).as_posix()
        sha = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        st = "same" if prev_sha.get(rel) == sha else ("changed" if rel in prev_sha else "new")
        counts[st] += 1
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, out)
        files.append({"path": rel, "size": p.stat().st_size, "sha": sha, "status": st})
    removed = sorted(set(prev_sha) - {f["path"] for f in files})

    man["revisions"].append({
        "id": n, "date": date.today().isoformat(), "label": label, "note": note,
        "files": files, "removed": removed,
        "counts": {**counts, "removed": len(removed)},
    })
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Р{n} «{label}»: файлов {len(files)} "
          f"(новых {counts['new']}, изменённых {counts['changed']}, без изменений {counts['same']}"
          f"{', удалённых ' + str(len(removed)) if removed else ''}) → {dst}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("нужна метка: python3 ove/tools/revisions.py \"Метка\" [\"заметка\"]")
    cut(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
