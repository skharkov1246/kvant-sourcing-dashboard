#!/usr/bin/env python3
"""Загрузка базы знаний в PostgreSQL (Supabase) одной командой.

Выгрузку делает export_kb.py: рядом с CSV он кладёт schema.sql. Этот модуль
применяет схему и заливает данные, чтобы не собирать команды \\copy руками.

Строка подключения берётся из SUPABASE_DB_URL — переменной окружения или .env
в корне репозитория. Годится только «Session pooler» (aws-<регион>.pooler
.supabase.com:5432): прямое подключение у Supabase по умолчанию IPv6, а
транзакционный пул не держит подготовленные запросы — так же, как в
.github/workflows/zip-db.yml, где это уже описано для базы ЗИП.

    python base/export_kb.py --db base/kvant.db --out kb
    python base/load_kb.py --dir kb
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"


def db_url() -> str:
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url and ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*SUPABASE_DB_URL\s*=\s*(.+)", line)
            if m:
                url = m.group(1).strip().strip('"\'')
                break
    if not url:
        raise SystemExit("нет SUPABASE_DB_URL: положите строку подключения в .env или окружение")
    return url


def psql(url: str, *args: str) -> str:
    r = subprocess.run(["psql", url, "-v", "ON_ERROR_STOP=1", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"psql: {r.stderr.strip()[:400]}")
    return r.stdout


def run(dir_path: str) -> dict:
    out = Path(dir_path)
    schema = out / "schema.sql"
    if not schema.exists():
        raise SystemExit(f"нет {schema}: сперва запустите export_kb.py")
    url = db_url()
    print("применяю схему…", flush=True)
    psql(url, "-f", str(schema))
    done = {}
    for csv in sorted(out.glob("kb_*.csv.gz")):
        table = csv.stem.replace(".csv", "")
        print(f"  {table}…", end="", flush=True)
        psql(url, "-c", f"\\copy {table} FROM PROGRAM 'zcat {csv.resolve()}' CSV HEADER")
        n = psql(url, "-t", "-c", f"SELECT count(*) FROM {table}").strip()
        done[table] = int(n or 0)
        print(f" {n} строк", flush=True)
    print(f"\nзагружено таблиц: {len(done)}, строк: {sum(done.values())}", flush=True)
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="kb")
    a = ap.parse_args()
    run(a.dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
