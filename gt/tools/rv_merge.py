#!/usr/bin/env python3
"""Приём выдачи разведки в набор перепроверки: один вход, одни проверки.

ЗАЧЕМ. Строки перепроверки приходят от разведки пачками по десять-двадцать, и
каждую пачку до сих пор вносили руками. Цена такой правки известна: набор
дважды принимал строку, у которой в числовом поле цены лежала проза, и один
раз — строку про изделие, которого в заявке нет. Оба случая потом ловил гейт,
но уже после коммита.

ЧТО ПРОВЕРЯЕТСЯ ЗДЕСЬ, ДО ЗАПИСИ:
 * номер строки есть в сводке заявки (иначе разбор не о нашей потребности);
 * номер ещё не разобран (повтор удваивает строку в любом счёте);
 * вердикт опознаётся закрытым списком по НАЧАЛУ текста;
 * цена — положительное число в долларах за штуку либо null, проза живёт
   в price_note;
 * запрещённые поля количества, вилки и экспозиции отсутствуют: они живут
   в сводке заявки, и только там;
 * поле «чем подтверждено» не пустое;
 * ценовой вердикт не выносится без цены и без названного источника.

Строка, не прошедшая проверку, НЕ записывается и называется с причиной.
Поле skeptics, если разведка его не заполнила, ставится одной записью с
holds=null и прямым указанием, что независимого опровержения не было: пустое
поле читалось бы как «проверено», а это не так.

    python gt/tools/rv_merge.py <файл выдачи.json> [ещё файлы] [--dry-run]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "gt/data/ship_reverify.json"
ASK = ROOT / "gt/data/ship_lukoil.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verdicts import PRICED, UNKNOWN, vkey            # noqa: E402

BANNED = ("qty", "lo", "hi", "expo", "band_lo", "band_hi", "exposure")
DOMAIN = re.compile(r"[a-z0-9][a-z0-9-]*\.[a-z][a-z.]{1,8}\b", re.I)
NO_SKEPTIC = {
    "lens": "независимого опровержения не было",
    "holds": None,
    "confidence": "не установлена",
    "checked": ("Строка принята от разведки без отдельного скептика: проверку на "
                "опровержение по ней никто не вёл. Это не значит, что вывод неверен, — это "
                "значит, что его никто не пытался сломать."),
    "objection": "",
    "correction": "",
}


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


def check(r: dict, ask: dict, have: set[str]) -> str:
    k = key(r.get("pn"))
    if not k:
        return "номер пустой"
    if k not in ask:
        return "номера нет в сводке заявки"
    if k in have:
        return "номер уже разобран"
    if vkey(r) == UNKNOWN:
        return f'вердикт не опознан: «{str(r.get("band_verdict"))[:40]}»'
    if not str(r.get("note") or "").strip():
        return "пустое поле «чем подтверждено»"
    for f in BANNED:
        if f in r:
            return f"поле {f} в строке: количество и вилка живут в сводке заявки"
    for f in ("price_low", "price_high"):
        v = r.get(f)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            return f"{f} не положительное число в долларах за штуку: {v!r}"
    lo, hi = r.get("price_low"), r.get("price_high")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
        return "найденный пол выше найденного потолка"
    if vkey(r) in PRICED:
        if not isinstance(lo, (int, float)):
            return "вердикт по цене без числовой цены"
        src = str(r.get("price_source") or "")
        if not DOMAIN.search(src) and "ССЫЛКА НЕ СОХРАНЕНА" not in src:
            return "вердикт по цене без названной страницы"
    return ""


def merge(paths: list[Path], dry: bool = False) -> dict:
    out = json.loads(OUT.read_text(encoding="utf-8"))
    ask = {key(r.get("pn")) for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]}
    have = {key(r.get("pn")) for r in out["rows"]}
    took, left = [], []
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:                                  # noqa: BLE001
            left.append((p.name, "—", f"файл не читается: {e}"))
            continue
        for r in (d["rows"] if isinstance(d, dict) else d):
            why = check(r, ask, have)
            if why:
                left.append((p.name, str(r.get("pn")), why))
                continue
            if not isinstance(r.get("skeptics"), list) or not r["skeptics"]:
                r["skeptics"] = [dict(NO_SKEPTIC)]
            took.append(r)
            have.add(key(r.get("pn")))
    if took and not dry:
        out["rows"] = out["rows"] + took
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"took": took, "left": left, "total": len(out["rows"]) + (0 if not dry else len(took))}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 1
    r = merge([Path(a) for a in args], dry="--dry-run" in sys.argv)
    print(f"принято строк: {len(r['took'])}  в наборе теперь: {r['total']}")
    for row in r["took"]:
        print(f"  + {str(row['pn']):18} {vkey(row):18} {str(row.get('price_low'))}")
    for name, pn, why in r["left"]:
        print(f"  ОТКАЗ {name} / {pn}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
