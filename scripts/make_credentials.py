"""Именные доступы к сайтам: генерация паролей и строк секрета BASIC_AUTH_USERS.

Никаких паролей в репозиторий: скрипт пишет только в указанный каталог (по умолчанию
временный), а в GitHub-секрет попадают ХЭШИ. Пароли раздаёт владелец лично.

Режимы:
  python scripts/make_credentials.py --list users.txt --out /tmp/creds
      users.txt: по строке «email | имя | подразделение» (вывод зонда v25) либо просто email.
      → /tmp/creds/credentials.csv   (email, имя, подразделение, пароль)   — владельцу
      → /tmp/creds/BASIC_AUTH_USERS.txt (email sha256 сайты)               — в секрет GitHub
  python scripts/make_credentials.py --add ivanov@kvant.ru --sites sourcing,zip
      → печатает пароль и готовую строку секрета для ОДНОГО человека (добавить в секрет).
  python scripts/make_credentials.py --hash ivanov@kvant.ru 'пароль'
      → строка секрета для уже известного пароля (например, при смене).

Хэш = sha256("email:пароль"), hex. Логин при входе — почта, регистр не важен.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import secrets
import sys
from pathlib import Path

# без 0/O/1/l/I — пароль диктуют по телефону
ALPHABET = "23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ"


def new_password() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def line(email: str, password: str, sites: str) -> str:
    email = email.strip().lower()
    h = hashlib.sha256(f"{email}:{password}".encode("utf-8")).hexdigest()
    return f"{email} {h} {sites}"


def parse_users(path: Path) -> list[tuple[str, str, str]]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith("==="):
            continue
        parts = [p.strip() for p in s.split("|")]
        email = parts[0].lower()
        if "@" not in email:
            continue
        name = parts[1] if len(parts) > 1 else ""
        dep = parts[2] if len(parts) > 2 else ""
        out.append((email, name, dep))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", type=Path, help="файл со списком сотрудников")
    ap.add_argument("--out", type=Path, default=Path("/tmp/kvant-creds"), help="куда писать результат")
    ap.add_argument("--sites", default="*", help="сайты по умолчанию: * или список через запятую")
    ap.add_argument("--add", help="один email — сгенерировать пароль и строку секрета")
    ap.add_argument("--hash", nargs=2, metavar=("EMAIL", "PASSWORD"), help="строка секрета для известного пароля")
    a = ap.parse_args()

    if a.hash:
        print(line(a.hash[0], a.hash[1], a.sites))
        return 0
    if a.add:
        pw = new_password()
        print(f"почта:   {a.add.strip().lower()}\nпароль:  {pw}\nв секрет BASIC_AUTH_USERS добавить строку:\n{line(a.add, pw, a.sites)}")
        return 0
    if not a.list:
        ap.error("укажите --list, --add или --hash")

    users = parse_users(a.list)
    if not users:
        print("в списке нет ни одной почты", file=sys.stderr)
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    creds = a.out / "credentials.csv"
    secret = a.out / "BASIC_AUTH_USERS.txt"
    seen = set()
    with creds.open("w", newline="", encoding="utf-8") as fc, secret.open("w", encoding="utf-8") as fs:
        w = csv.writer(fc, delimiter=";")
        w.writerow(["email", "имя", "подразделение", "пароль", "сайты"])
        fs.write("# email  sha256(email:пароль)  сайты   — строки людей, которым доступ ВЫДАН\n")
        for email, name, dep in users:
            if email in seen:
                continue
            seen.add(email)
            pw = new_password()
            w.writerow([email, name, dep, pw, a.sites])
            fs.write(line(email, pw, a.sites) + "\n")
    print(f"сотрудников: {len(seen)}\n  пароли (владельцу, не коммитить): {creds}\n  строки секрета (в GitHub → BASIC_AUTH_USERS): {secret}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
