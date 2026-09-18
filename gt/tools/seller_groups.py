#!/usr/bin/env python3
"""«Два независимых продавца» — признак, который надо считать, а не видеть на глаз.

Этим доводом закрывается строка: одна цена может быть наценкой перепродавца,
две совпадающие цены РАЗНЫХ компаний — уже рынок. За ночь 17–18.09.2026 довод
развалился шесть раз: витрины с разными доменами оказывались одним оператором.
Улики каждый раз были прямые — общий объект в коде страницы, общий складской
номер, общая почта, посимвольно совпадающее описание.

Инструмент делает две вещи.

1. СЧИТАЕТ, где наша же перепроверка ссылается на две витрины ОДНОЙ группы.
   Такая строка выглядит подтверждённой двумя источниками, а подтверждена
   одним, и это надо видеть числом, а не вспоминать.
2. СЧИТАЕТ деньги: сколько экспозиции стоит на строках, где все названные
   продавцы принадлежат одной группе. Это худший класс: цена есть, а рынка нет.

    python gt/tools/seller_groups.py            # замер
    python gt/tools/seller_groups.py --check    # для гейта
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GROUPS = ROOT / "gt/data/ship_seller_groups.json"
REVERIFY = ROOT / "gt/data/ship_reverify.json"
OUT = ROOT / "gt/data/ship_seller_independence.json"
SUMMARY = ROOT / "gt/data/ship_lukoil.json"
# Домен в прозе: пишем ссылки без схемы, поэтому ищем именно «имя.зона».
DOMAIN = re.compile(r"\b((?:[a-z0-9][a-z0-9-]*\.)+(?:com|net|org|ru|de|cz|pl|in|cn|eu|at|io|uk|store|parts))\b",
                    re.I)
FIELDS = ("price_source", "channel", "contacts", "stock", "note")


def key(pn) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").split("(")[0].upper())


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def group_of(groups: list[dict]) -> dict[str, str]:
    """Домен → имя группы. Поддомены сводятся к своему домену списка."""
    out: dict[str, str] = {}
    for g in groups:
        for d in g.get("domains") or []:
            out[d.lower()] = g["group"]
    return out


def domains_in(row: dict) -> set[str]:
    """Домены, названные в строке. `www.` снимается: иначе www.x.com и x.com
    считались двумя витринами одной группы — ложная тревога на ровном месте."""
    text = " ".join(str(row.get(f) or "") for f in FIELDS)
    out = set()
    for m in DOMAIN.finditer(text):
        d = m.group(1).lower()
        out.add(d[4:] if d.startswith("www.") else d)
    return out


def resolve(dom: str, by_domain: dict[str, str]) -> str | None:
    """Имя группы для домена или его родителя (www., sa. и прочие поддомены)."""
    parts = dom.split(".")
    for i in range(len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in by_domain:
            return by_domain[cand]
    return None


# Наши собственные формулировки, которыми в строке ПРЯМО сказано, что витрины
# одного оператора. Без этого раздела замер был бы тавтологией: строка, где я
# сам написал «это одна компания», попадала в дефект наравне со строкой, где
# две витрины выданы за рынок. Правило проекта: эталон не может быть
# производным от правила.
MARKED = re.compile(
    r"одна\s+компания|одн(?:ой|ого)\s+(?:компани|оператор|групп)|не\s+два\s+независимых"
    r"|не\s+явля(?:ется|ются)\s+независим|того\s+же\s+(?:владельца|оператора)"
    r"|обе\s+компании\s+группы|той\s+же\s+групп|одной\s+платформе|под\s+пятью\s+вывесками"
    r"|под\s+четырьмя\s+вывесками|независимым\s+источником\s+не|нельзя\s+складывать"
    r"|одно\s+лицо|один\s+оператор|одного\s+владельца|тот\s+же\s+владелец"
    r"|она\s+же\s+одно\s+лицо|вывесками",
    re.I)


def marked(row: dict) -> bool:
    """Сказано ли в самой строке, что это один оператор."""
    return bool(MARKED.search(" ".join(str(row.get(f) or "") for f in FIELDS)))


def expo(r: dict) -> float:
    lo, hi = r.get("usd_lo"), r.get("usd_hi")
    if lo in (None, "") or hi in (None, ""):
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(r.get("qty") or 0)


def measure() -> dict:
    gdoc = load(GROUPS) or {}
    by_domain = group_of(gdoc.get("groups") or [])
    rv = (load(REVERIFY) or {}).get("rows") or []
    lk = {key(r.get("pn")): r for r in ((load(SUMMARY) or {}).get("rows") or [])}

    hits, usd = [], 0.0
    unmarked, unmarked_usd = [], 0.0
    for r in rv:
        doms = domains_in(r)
        seen: dict[str, set[str]] = {}
        for d in doms:
            g = resolve(d, by_domain)
            if g:
                seen.setdefault(g, set()).add(d)
        many = {g: sorted(v) for g, v in seen.items() if len(v) > 1}
        if not many:
            continue
        row = lk.get(key(r.get("pn")))
        e = expo(row) if row else 0.0
        usd += e
        item = {"pn": r.get("pn"), "usd": round(e, 2),
                "verdict": (r.get("band_verdict") or "").split("(")[0].strip(),
                "groups": many, "marked": marked(r)}
        hits.append(item)
        if not item["marked"]:
            unmarked.append(item)
            unmarked_usd += e
    hits.sort(key=lambda x: -x["usd"])
    unmarked.sort(key=lambda x: -x["usd"])
    return {
        "groups_known": len(gdoc.get("groups") or []),
        "domains_known": len(by_domain),
        "rows_checked": len(rv),
        "rows_citing_one_group_twice": len(hits),
        "usd_on_those_rows": round(usd, 2),
        "rows_marked_as_one_operator": len(hits) - len(unmarked),
        "rows_not_marked": len(unmarked),
        "usd_not_marked": round(unmarked_usd, 2),
        "rows": hits,
        "not_marked": unmarked,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="только код возврата и одна строка")
    ap.add_argument("--write", action="store_true", help="записать замер в набор")
    a = ap.parse_args()
    if not GROUPS.exists():
        print("нет списка групп продавцов", file=sys.stderr)
        return 1
    m = measure()
    print(f"групп известно {m['groups_known']}, доменов {m['domains_known']}; "
          f"строк перепроверки {m['rows_checked']}, из них ссылаются на две витрины одной "
          f"группы {m['rows_citing_one_group_twice']} "
          f"на {m['usd_on_those_rows']:,.0f} USD".replace(",", " "))
    print(f"  из них строка сама говорит, что это один оператор: "
          f"{m['rows_marked_as_one_operator']}; НЕ говорит: {m['rows_not_marked']} "
          f"на {m['usd_not_marked']:,.0f} USD — это и есть работа".replace(",", " "))
    if a.write:
        gdoc = load(GROUPS) or {}
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": "Замер признака «два независимых продавца» по строкам перепроверки "
                      "заявки ЛУКОЙЛ. Группы витрин — gt/data/ship_seller_groups.json.",
            "method": "Из текста строки вынимаются домены, каждый сводится к своей группе. "
                      "Строка попадает в замер, если названы ДВЕ и более витрины одной группы. "
                      "Отдельно считается, сказано ли в самой строке, что это один оператор: "
                      "без этого деления замер был бы тавтологией — строка, где мы сами "
                      "написали «это одна компания», попадала бы в дефект наравне со строкой, "
                      "где две витрины выданы за рынок.",
            "why": gdoc.get("why", ""),
            "totals": {k: v for k, v in m.items() if k not in ("rows", "not_marked")},
            "rows": m["rows"],
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")
    if not a.check:
        for h in m["not_marked"][:20] or m["rows"][:20]:
            gs = "; ".join(f"{g}: {', '.join(d)}" for g, d in h["groups"].items())
            flag = "оговорено" if h["marked"] else "НЕ ОГОВОРЕНО"
            print(f"  {h['usd']:>10,.0f} | {h['pn']:26} | {flag:<12} | {gs}".replace(",", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
