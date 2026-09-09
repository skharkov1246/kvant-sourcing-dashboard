#!/usr/bin/env python3
"""Картина спроса: что у нас запрашивают, как часто и на что мы отвечали.

Считается по строкам спецификаций (таблица positions), а не по названиям
сделок: в карточке написано «Запчасти для ПДМ», а нужен конкретный клапан с
каталожным номером — именно он и есть единица спроса.

ЧТО СЧИТАЕТСЯ ЕДИНИЦЕЙ. Ключ позиции — каталожный номер, если он есть, иначе
приведённое наименование. Приведение убирает регистр, пунктуацию, единицы
измерения и служебные слова, но СОХРАНЯЕТ числа: у запчастей типоразмер несёт
смысл, «манжета 30х42» и «манжета 40х55» — разные позиции.

ОТКУДА СМЕЩЕНИЕ. Один и тот же файл лежит в нескольких сделках (46 % вложений —
копии), поэтому частота считается по числу РАЗНЫХ сделок, а не по числу строк:
иначе одна большая спецификация, продублированная в пяти карточках, выглядит
как пятикратный спрос.

    python base/demand.py --db base/kvant.db --out demand.json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from extract_positions import junk_pn

PUNCT = re.compile(r"[^\w\s./-]+", re.U)
SPACES = re.compile(r"\s+")
STOPW = {"поз", "позиция", "шт", "штук", "компл", "ндс", "итого", "всего", "ед", "изм",
         "наименование", "артикул", "цена", "сумма", "кол", "во", "количество", "для",
         "the", "and", "for", "with", "или", "тип", "type", "pcs", "ea", "set"}
MIN_TOKENS = 2
TOP = 60

# В спецификации соседствуют строки трёх сортов: сама номенклатура, атрибуты
# позиции («Базовая единица измерения: ШТ», «Overall Package Size:») и пункты
# требований («6.1. Расчетное значение веса снегового покрова…», «не менее 2 шт.»).
# Разбор строк берёт все три, и в частотном отчёте наверх лезут именно требования:
# один и тот же пункт типового техзадания повторяется в десятках сделок.
CLAUSE_START = re.compile(r"^\s*\d+(\.\d+)*[.)]\s")                 # «6.1. », «4) »
NORMATIVE = re.compile(
    r"(не\s+менее|не\s+более|не\s+допускается|предусмотреть|должен|должна|должно|должны"
    r"|обеспечить|требуется|при\s+необходимости|в\s+соответствии|согласно"
    r"|рекомендуется|рекомендуемый|следует|осуществля|запрещается|разрешается|необходимо"
    r"|допускается|производится|выполняется|в\s+процессе|при\s+подлет"
    r"|shall\b|must\b|to\s+be\s+provided|as\s+per\b|if\s+required|recommended)", re.I)
# Квалификационные требования закупки: приходят той же таблицей, что и позиции
LEGAL = re.compile(
    r"(исполнительн\w+\s+производств|банкротств|недобросовестн\w+\s+поставщик"
    r"|налогов\w+\s+задолженност|аффилированн|реестр\w*\s+РНП)", re.I)
# Строка-характеристика: свойство и число, без предмета поставки
PARAM_ROW = re.compile(
    r"^\s*(плотность|температура|давление|вязкость|влажность|мощность|напряжение|частота"
    r"|масса|вес|объ[её]м|расход|скорость|длина|ширина|высота|диаметр|degree|pressure"
    r"|temperature|density|viscosity)\b.*\d", re.I)
# Ячейка-ответ вместо позиции
ANSWER = re.compile(r"^\s*(нет|да|yes|no|n\s*/?\s*a|отсутствует|не\s+требуется|наличие|имеется)"
                    r"\s*(/\s*\w+)?\s*$", re.I)
MAX_WORDS = 14          # длиннее — это фраза из техзадания, а не наименование
# Перечень документов, которые заказчик требует приложить к оферте: в тендерных
# пакетах он идёт такой же таблицей с номерами, что и сама номенклатура.
DOCREQ = re.compile(
    r"(vendor\s+master\s+data|material\s+test\s+certificate|inspection\s+and\s+test"
    r"|data\s+book|dossier|packing\s+list|сертификат\s+соответствия|паспорт\s+качества"
    r"|акт\s+приемки|расч[её]тное\s+значение|разрешительн\w+\s+документ)", re.I)
ATTRIBUTE = re.compile(
    r"(единица\s+измерения|package\s+size|phase\s+connection|protection\s+designation"
    r"|declaration\s+of\s+compliance|at\s+the\s+manufacturer|срок\s+поставки|условия\s+оплаты"
    r"|страна\s+происхождения|грузополучатель|дата\s+заполнения|ОКУД|ОКПО"
    r"|оплата\s+по\s+факту|предоплат|отсрочка\s+платежа|альт\.?\s*предложение)", re.I)


def looks_like_item(name: str, has_code: bool) -> bool:
    """Строка номенклатуры, а не требование и не атрибут позиции."""
    s = (name or "").strip()
    if not s:
        return bool(has_code)
    if (CLAUSE_START.search(s) or NORMATIVE.search(s) or ATTRIBUTE.search(s)
            or LEGAL.search(s) or PARAM_ROW.search(s) or ANSWER.match(s)
            or DOCREQ.search(s)):
        return False
    if s.endswith(":"):                      # подпись атрибута, значение в соседней ячейке
        return False
    if len(s.split()) > MAX_WORDS:
        return False
    letters = sum(ch.isalpha() for ch in s)
    return letters >= 3


def norm_name(s: str) -> str:
    s = PUNCT.sub(" ", str(s or "").lower().replace("ё", "е"))
    toks = [t for t in SPACES.sub(" ", s).split() if len(t) > 1 and t not in STOPW]
    return " ".join(toks[:12])


def load(con: sqlite3.Connection) -> list[tuple]:
    return con.execute("""SELECT p.deal_id, p.part_number, p.name, p.manufacturer, p.price,
                                 p.currency, f.field_name, d.company, d.won, d.seg
                          FROM positions p
                          LEFT JOIN files f ON f.fid = p.fid
                          LEFT JOIN deals d ON d.id = p.deal_id""").fetchall()


def run(db_path: str, out_path: str) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=300)
    con.execute("PRAGMA busy_timeout=120000")
    rows = load(con)

    deals_of: dict[str, set[int]] = defaultdict(set)
    lines_of: Counter = Counter()
    label_of: dict[str, str] = {}
    maker_of: dict[str, Counter] = defaultdict(Counter)
    buyers_of: dict[str, set[str]] = defaultdict(set)
    wins_of: dict[str, set[int]] = defaultdict(set)
    priced_of: Counter = Counter()
    kind_of: dict[str, str] = {}
    skipped: Counter = Counter()

    for deal, pn, name, maker, price, _cur, field, company, won, _seg in rows:
        if pn and junk_pn(pn):
            pn = None                        # параметр, а не каталожный номер
        key = ("pn:" + pn) if pn else ("nm:" + norm_name(name))
        if key in ("nm:", "pn:"):
            continue
        if not pn and len(norm_name(name).split()) < MIN_TOKENS:
            continue
        if not looks_like_item(name, bool(pn)):
            skipped["не номенклатура"] += 1
            continue
        deals_of[key].add(int(deal or 0))
        lines_of[key] += 1
        label_of.setdefault(key, (name or pn or "")[:120])
        kind_of.setdefault(key, "код" if pn else "наименование")
        if maker:
            maker_of[key][maker] += 1
        if company:
            buyers_of[key].add(company)
        if won:
            wins_of[key].add(int(deal or 0))
        if price is not None:
            priced_of[key] += 1

    def entry(key: str) -> dict:
        makers = maker_of[key].most_common(1)
        return {"key": key, "kind": kind_of[key], "code": key[3:] if key.startswith("pn:") else None,
                "label": label_of[key], "deals": len(deals_of[key]), "lines": lines_of[key],
                "buyers": len(buyers_of[key]), "wins": len(wins_of[key]),
                "priced": priced_of[key], "maker": makers[0][0] if makers else None}

    ranked = sorted(deals_of, key=lambda k: (-len(deals_of[k]), -lines_of[k]))
    by_freq = Counter(len(v) for v in deals_of.values())

    # спрос по производителям: сколько разных позиций и в скольких сделках
    maker_pos: dict[str, set[str]] = defaultdict(set)
    maker_deals: dict[str, set[int]] = defaultdict(set)
    for key, cnt in maker_of.items():
        for m in cnt:
            maker_pos[m].add(key)
            maker_deals[m] |= deals_of[key]
    makers = sorted(maker_pos, key=lambda m: -len(maker_deals[m]))

    # из каких документов приходит спрос
    field_lines = Counter()
    for _d, _pn, _n, _m, _p, _c, field, *_ in rows:
        field_lines[field or "—"] += 1

    out = {
        "positions_rows": len(rows),
        "skipped_rows": dict(skipped),
        "unique_items": len(deals_of),
        "with_code": sum(1 for k in deals_of if k.startswith("pn:")),
        "one_deal_only": by_freq.get(1, 0),
        "freq_histogram": {str(k): v for k, v in sorted(by_freq.items())[:20]},
        "top_items": [entry(k) for k in ranked[:TOP]],
        "top_codes": [entry(k) for k in ranked if k.startswith("pn:")][:TOP],
        "top_names": [entry(k) for k in ranked if k.startswith("nm:")][:TOP],
        "makers": [{"maker": m, "items": len(maker_pos[m]), "deals": len(maker_deals[m])}
                   for m in makers[:40]],
        "sources": [{"field": f, "lines": n} for f, n in field_lines.most_common(14)],
    }
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent / "kvant.db"))
    ap.add_argument("--out", default="demand.json")
    a = ap.parse_args()
    st = run(a.db, a.out)
    print(f"строк всего {st['positions_rows']}, отсеяно как требования и атрибуты "
          f"{sum(st['skipped_rows'].values())}, разных позиций {st['unique_items']}, "
          f"из них с каталожным номером {st['with_code']}, "
          f"встречались ровно в одной сделке {st['one_deal_only']}")
    print("\nчаще всего запрашивают:")
    for e in st["top_items"][:12]:
        code = f" · {e['code']}" if e["code"] else ""
        maker = f" · {e['maker']}" if e["maker"] else ""
        print(f"  {e['deals']:3} сделок · {e['buyers']:2} заказчиков{code}{maker} · {e['label'][:52]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
