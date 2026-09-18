#!/usr/bin/env python3
"""Приёмник адресов служб запчастей изготовителей.

ЗАЧЕМ ПРИВРАТНИК. Адрес попадает в письмо, а письмо уходит наружу: неверный
адрес не просто теряется — строка при этом считается закрытой, и ошибка
прячется. Поэтому запись принимается по тем же правилам, что цена в
gt/tools/rv_merge.py.

ЧТО ОТСЕКАЕТСЯ И ПОЧЕМУ:

  * почта без поля read_on — догадка вида «parts@домен»: не сказано, на какой
    странице она напечатана;
  * страница, не принадлежащая ни изготовителю, ни названному дистрибьютору —
    адрес по классу вместо адреса по детали;
  * имя изготовителя, которого нет в задании, — разведка отвечала не на наш
    вопрос;
  * наш собственный репозиторий и поисковая выдача как источник.

Запись БЕЗ почты не отсекается: форма обращения и телефон — тоже точка входа,
и «почты нет вовсе» — измеренный результат, а не пробел. Она ложится в набор с
пустым email, и письмо по ней не собирается.

    python gt/tools/mc_merge.py scratchpad/mc/out*.json [--dry]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "gt/data/maker_contacts.json"
MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
CIRCULAR = ("github.com/skharkov1246", "claude.ai/code", "kvant-sourcing-dashboard")
SEARCH = ("google.com/search", "yandex.ru/search", "bing.com/search", "duckduckgo.com/?q")
FIELDS = ("maker", "legal_name", "email", "email_kind", "form_url", "phone",
          "via_distributor", "distributor_quote", "read_on", "note", "confidence")


#: Составные зоны: без них «atmus.co.uk» дало бы метку «co», а не «atmus».
PUBLIC_SUFFIX = frozenset({"co.uk", "com.cn", "com.au", "co.jp", "com.br", "co.in",
                           "com.tr", "co.za", "com.mx", "com.sg", "co.kr", "com.hk"})


def norm(t) -> str:
    return re.sub(r"[^a-z0-9а-яё]", "", str(t or "").lower())


def label(dom: str) -> str:
    """Опознаваемая метка домена: то, что слева от зоны.

    ОПЛАЧЕНО ТЕСТОМ 18.09.2026. Первая редакция сверяла домены «по части после
    первой точки», и для любой пары в зоне .com проверка была отключена
    целиком: «atmus.com» и «fleetguard.com» обе кончаются на «com», значит
    «совпадают». Отказ по DENSO прошёл только случайно — там различались зоны.
    """
    parts = [p for p in str(dom or "").lower().split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in PUBLIC_SUFFIX:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else ""


def host(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", str(url or ""))
    return m.group(1).lower() if m else ""


def check(r: dict) -> str:
    """Причина отказа или пустая строка."""
    if not str(r.get("maker") or "").strip():
        return "изготовитель не назван"
    blob = " ".join(str(r.get(f) or "") for f in FIELDS)
    for mark in CIRCULAR:
        if mark in blob:
            return f"круговой источник: {mark}"
    for mark in SEARCH:
        if mark in blob:
            return f"выдача поисковой машины источником не является: {mark}"
    mails = MAIL.findall(str(r.get("email") or ""))
    if mails and not str(r.get("read_on") or "").strip():
        return "почта без названной страницы (read_on): это догадка, а не адрес"
    if mails:
        dom = mails[0].split("@")[-1].lower()
        page = host(r.get("read_on"))
        dl, pl = label(dom), label(page)
        # Почта и страница связаны, если у них одна метка домена. Иначе связь
        # обязана быть НАЗВАНА: либо дистрибьютор, либо юридическое лицо, в
        # имени которого стоит метка почтового домена (случай марки и её
        # владельца: Fleetguard — марка Atmus Filtration Technologies, и почта
        # службы там на atmus.com; такой адрес настоящий, но молча принимать
        # его нельзя — связь должна быть записана).
        if dl and pl and dl != pl:
            # Связь ищется и в legal_name, и в note. Первая редакция смотрела
            # только в legal_name и отбила три настоящих адреса: Det-Tronics
            # (почта перешла на владельца Spectrum Safety), INNIO Jenbacher
            # (Jenbacher — марка INNIO), Oleobi (переименован в Flodraulic). Во
            # всех трёх связь была записана разведкой — в поле note, с цитатой
            # со страницы. Требование остаётся тем же: связь должна быть
            # НАЗВАНА, а не додумана читателем.
            explained = f"{r.get('legal_name') or ''} {r.get('note') or ''}"
            named = bool(norm(r.get("via_distributor"))) or dl in norm(explained)
            if not named:
                return (f"почта в зоне «{dl}» прочитана на странице «{pl}»: связь не названа "
                        f"ни дистрибьютором, ни юридическим лицом, ни разбором")
    if not mails and not str(r.get("form_url") or "").strip() \
            and not str(r.get("phone") or "").strip():
        return "ни почты, ни формы, ни телефона — записывать нечего"
    return ""


def merge(paths: list[Path], dry: bool = False) -> dict:
    took, left, bad_files = [], [], []
    seen: dict[str, dict] = {}
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:                                   # noqa: BLE001
            bad_files.append((p.name, f"{type(e).__name__}: {e}"))
            continue
        for r in d.get("rows") or []:
            why = check(r)
            if why:
                left.append((p.name, str(r.get("maker")), why))
                continue
            row = {f: r.get(f) for f in FIELDS}
            row["found_on"] = date.today().isoformat()
            k = norm(row["maker"])
            # Одно имя дважды: берём запись с почтой, при равенстве — первую.
            prev = seen.get(k)
            if prev and MAIL.findall(str(prev.get("email") or "")):
                left.append((p.name, str(r.get("maker")), "изготовитель уже принят с почтой"))
                continue
            seen[k] = row
    took = list(seen.values())
    took.sort(key=lambda r: norm(r["maker"]))
    doc = {
        "updated": date.today().isoformat(),
        "source": "Адреса служб запчастей изготовителей, прочитанные на их же страницах. "
                  "Собирает разведка, принимает gt/tools/mc_merge.py.",
        "what_it_is": "Точка обращения за ценой и сроком там, где адреса продавца нет.",
        "rule": "Почта принимается только с полем read_on — адресом страницы, где она "
                "напечатана, и только если домен почты связан со страницей либо назван "
                "дистрибьютор. Адрес вида «parts@домен» без прочитанной страницы — догадка: "
                "письмо уходит в никуда, а строка считается закрытой.",
        "rows_with_email": sum(1 for r in took if MAIL.findall(str(r.get("email") or ""))),
        "rows_form_only": sum(1 for r in took if not MAIL.findall(str(r.get("email") or ""))),
        "rows": took,
    }
    if not dry:
        OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"doc": doc, "took": took, "left": left, "bad_files": bad_files}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 1
    r = merge([Path(a) for a in args], dry="--dry" in sys.argv)
    d = r["doc"]
    print(f"принято изготовителей: {len(r['took'])} — с почтой {d['rows_with_email']}, "
          f"только форма или телефон {d['rows_form_only']}")
    for row in r["took"]:
        mail = (MAIL.findall(str(row.get("email") or "")) or ["—"])[0]
        print(f"  + {str(row['maker'])[:28]:30} {mail:38} {str(row.get('confidence') or '')}")
    for name, maker, why in r["left"]:
        print(f"  ОТКАЗ {name} / {maker}: {why}")
    if r["bad_files"]:
        print("\nФАЙЛЫ НЕ ПРОЧИТАНЫ:", file=sys.stderr)
        for name, why in r["bad_files"]:
            print(f"  {name}: {why}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
