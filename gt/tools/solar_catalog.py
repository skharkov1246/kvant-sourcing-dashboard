#!/usr/bin/env python3
"""Сверка номеров заявки с КАТАЛОГОМ ИЗГОТОВИТЕЛЯ (магазин Solar Turbines).

ЗАЧЕМ. Магазин изготовителя цену отдаёт только после входа, и поэтому его
считали закрытым источником целиком. Это неверно: КАРТА САЙТА магазина открыта
и содержит адрес каждой товарной карточки — 72 491 адрес на 18.09.2026. Адрес
включает сам номер, значит по карте можно, не входя в магазин, ответить на два
вопроса: существует ли номер в номенклатуре изготовителя и какой у него адрес
по самой детали. Цены в карте нет и быть не может — она и не запрашивается.

ПОЧЕМУ ЭТОГО НЕ ВИДЕЛИ РАНЬШЕ. В каталоге номер записан БЕЗ РАЗДЕЛИТЕЛЕЙ:
«1013121-1» лежит по адресу «10131211». Поиск нашим написанием не находит
ничего — и не находил годами, из-за чего у 231 строки заявки из 380 стоял
вердикт «номер не подтверждается открытыми источниками», хотя номер лежит в
каталоге самого изготовителя. Сверка ведётся по ключу без разделителей, тем же
правилом, что и везде в репозитории.

ЧТО ЭТО ДАЁТ И ЧЕГО НЕ ДАЁТ. Даёт: подтверждение существования номера у
изготовителя, его каталожное написание и адрес карточки. НЕ даёт: ни цены, ни
остатка, ни срока — за ними нужен вход, и это решение владельца. Отсутствие
номера в каталоге тоже измерение: у 117 строк с изготовителем Solar номера в
каталоге нет, и это либо внутреннее чертёжное обозначение, либо сборка, которую
изготовитель отдельно не продаёт, либо ошибка переноса — такие строки
закрываются вопросом заказчику, а не поиском.

    # скачать карты сайта (вне репозитория) и свести
    python gt/tools/solar_catalog.py --fetch --dir /tmp/solar --write

    # свести по уже скачанным картам
    python gt/tools/solar_catalog.py --dir /tmp/solar --write
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pnkey import key as _key  # noqa: E402
ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
OUT = ROOT / "gt/data/solar_catalog_match.json"

ROBOTS = "https://shop.solarturbines.com/robots.txt"
# Адрес карточки: .../ShopSolar/product/<номер без разделителей>/<идентификатор>
PRODUCT = re.compile(r"<loc>(https://shop\.solarturbines\.com/ShopSolar/product/"
                     r"([^/<]+)/([^<]+))</loc>")
SITEMAP = re.compile(r"<loc>(https://[^<]*sitemap-product-\d+\.xml)</loc>")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
MAKER = re.compile(r"solar", re.I)


def key(x) -> str:
    """Ключ сведения номера — один на все инструменты, см. gt/tools/pnkey.py."""
    return _key(x)


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def get(url: str, dest: Path) -> bool:
    """Скачивает адрес в файл. Отдельной функцией — чтобы её можно было подменить.

    Переадресацию идём обязательно: `robots.txt` магазина отдаёт 301, и без
    `-L` первая же попытка выглядит как отказ сайта.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl", "-sSL", "-m", "120", "-A", UA, url, "-o", str(dest)],
                       capture_output=True, text=True)
    return r.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def fetch(into: Path) -> list[Path]:
    """Карта сайта берётся из robots.txt, а не угадывается по шаблону."""
    rob = into / "robots.txt"
    if not get(ROBOTS, rob):
        print("robots.txt магазина не скачался", file=sys.stderr)
        return []
    out = []
    for m in re.finditer(r"Sitemap:\s*(\S+)", rob.read_text(encoding="utf-8", errors="replace")):
        idx = into / (m.group(1).rstrip("/").split("/")[-2] + "-index.xml")
        if not get(m.group(1), idx):
            continue
        for sm in SITEMAP.finditer(idx.read_text(encoding="utf-8", errors="replace")):
            dest = into / (sm.group(1).rstrip("/").split("/")[-1])
            if get(sm.group(1), dest):
                out.append(dest)
    return out


def catalog(files: list[Path]) -> dict[str, tuple[str, str]]:
    """Ключ без разделителей → (каталожное написание, адрес карточки)."""
    cat: dict[str, tuple[str, str]] = {}
    for f in files:
        txt = f.read_text(encoding="utf-8", errors="replace")
        for m in PRODUCT.finditer(txt):
            cat.setdefault(key(m.group(2)), (m.group(2), m.group(1)))
    return cat


def measure(cat: dict, ask: list, rv: dict) -> dict:
    hit, miss_solar = [], []
    spelling = 0
    for r in ask:
        k = key(r.get("pn"))
        if not k:
            continue
        rec = cat.get(k)
        if rec:
            if str(r.get("pn") or "").strip().lower() != rec[0].strip().lower():
                spelling += 1
            hit.append({
                "pn": r.get("pn"),
                "catalog_pn": rec[0],
                "url": rec[1],
                "qty": r.get("qty"),
                "sheet": r.get("sheet"),
                "reverified": k in rv,
                # Адрес по САМОЙ ДЕТАЛИ, а не по классу: карточка открыта по
                # этому номеру. Это единственное, что здесь утверждается.
                "what_it_proves": "номер есть в номенклатуре изготовителя, адрес карточки — "
                                  "по самой детали",
            })
        elif MAKER.search(str(r.get("man") or r.get("maker") or "")):
            miss_solar.append({"pn": r.get("pn"), "qty": r.get("qty"),
                               "name": r.get("name"), "sheet": r.get("sheet")})
    solar = [r for r in ask if MAKER.search(str(r.get("man") or r.get("maker") or ""))]
    not_rev = [h for h in hit if not h["reverified"]]
    return {
        "updated": date.today().isoformat(),
        "source": "Карта сайта магазина изготовителя Solar Turbines (robots.txt → sitemap). "
                  "Считает gt/tools/solar_catalog.py.",
        "what_it_is": "Номера заявки ЛУКОЙЛ, существование которых подтверждено каталогом "
                      "самого изготовителя, с каталожным написанием и адресом карточки.",
        "what_it_is_not": "Ни цены, ни остатка, ни срока: в карте сайта их нет, а карточка "
                          "отдаёт цену только после входа в магазин. Вход — решение владельца.",
        "catalog_addresses": len(cat),
        "ask_rows": len(ask),
        "matched": len(hit),
        "matched_usd": round(sum(expo(r) for r in ask if key(r.get("pn")) in cat), 2),
        "matched_not_reverified": len(not_rev),
        "spelling_differs": spelling,
        "why_spelling_matters": "В каталоге номер записан без разделителей: «1013121-1» лежит "
                                "по адресу «10131211». Поиск нашим написанием не находит "
                                "ничего — поэтому у части строк годами стоял вердикт «номер "
                                "не подтверждается открытыми источниками», хотя номер лежит "
                                "в каталоге изготовителя. Сверка идёт по ключу без "
                                "разделителей.",
        "solar_rows": len(solar),
        "solar_matched": sum(1 for r in solar if key(r.get("pn")) in cat),
        "solar_missing": len(miss_solar),
        "what_missing_means": "Номер с изготовителем Solar, которого в каталоге изготовителя "
                              "НЕТ. Это внутреннее чертёжное обозначение, сборка, которую "
                              "изготовитель отдельно не продаёт, либо ошибка переноса. "
                              "Закрывается вопросом заказчику, а не поиском.",
        "rows": sorted(hit, key=lambda z: str(z["pn"])),
        "solar_missing_rows": sorted(miss_solar, key=lambda z: str(z["pn"])),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="", help="папка с картами сайта (ВНЕ репозитория)")
    ap.add_argument("--fetch", action="store_true", help="скачать карты сайта в --dir")
    ap.add_argument("--write", action="store_true", help="записать набор в репозиторий")
    a = ap.parse_args()

    if not a.dir:
        print("нужна папка с картами сайта: --dir /tmp/solar", file=sys.stderr)
        return 2
    into = Path(a.dir)
    files = sorted(into.glob("sitemap-product-*.xml"))
    if a.fetch:
        files = fetch(into) or files
    if not files:
        print(f"в {into} нет карт сайта; запусти с --fetch", file=sys.stderr)
        return 2

    cat = catalog(files)
    if not cat:
        print("в картах сайта не нашлось ни одного товарного адреса", file=sys.stderr)
        return 1
    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {key(r["pn"]) for r in json.loads(RV.read_text(encoding="utf-8"))["rows"]}
    m = measure(cat, ask, rv)

    print(f"товарных адресов в каталоге изготовителя: {m['catalog_addresses']}")
    print(f"строк заявки: {m['ask_rows']}; номер подтверждён каталогом: {m['matched']}")
    print(f"  из них написание в заявке отличается от каталожного: {m['spelling_differs']}")
    print(f"  из них перепроверка их не касалась: {m['matched_not_reverified']}")
    print(f"строк с изготовителем Solar: {m['solar_rows']}; подтверждено {m['solar_matched']}; "
          f"номера в каталоге нет у {m['solar_missing']}")
    if a.write:
        OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
