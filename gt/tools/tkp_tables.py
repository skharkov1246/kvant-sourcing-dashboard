#!/usr/bin/env python3
"""Второй проход по вложениям: цена, подтверждённая арифметикой самой строки.

ЗАЧЕМ. Первый проход (gt/tools/bitrix_tkp.py) ищет колонку «цена» по заголовку,
а где заголовка нет — берёт последнее число строки. Замер 18.09.2026 по выгрузке
«Энергосети»: из 6 619 значений по колонке взято 317, остальные 6 302 — догадка,
и она брала номера позиций. Итог: три настоящих предложения поставщиков по этой
самой заявке не дали НИ ОДНОЙ цены в счёт денег.

  QT 127 SGT-400 minor parts (4).pdf   240 номеров заявки, все цены в ¥
  Quotation-CambiaTech Group Limited   48 номеров заявки, цены в $, со сроком
  Quotation p76057.pdf                 371 номер заявки, и 316 значений $0.00

ПРАВИЛО ЗДЕСЬ ДРУГОЕ: не угадывать колонку, а проверять арифметику. Значение
считается ценой за единицу только тогда, когда в той же строке нашлись
количество и итог, и единица × количество = итог. Тогда ошибка «принял итог за
цену» невозможна по построению: у CambiaTech заголовок указывал на колонку
ИТОГА ($79 555 при цене $2 273), и проверка арифметикой это ловит, а проверка
заголовком — нет.

Самопроверка обязательна и потому, что она отделяет предложение от отказа.
Quotation p76057 — ответ поставщика на наш же запрос RFQ 22566-41034, и в нём
316 значений, все до одного $0.00: поставщик вернул перечень без цен. Строка,
чей «адрес цены» указывает на этот файл, ценой не закрыта — и это надо
говорить прямо, а не числить её в деньгах.

ВАЛЮТА. Знак ¥ означает и юань, и иену, а курсы различаются в 22,8 раза
(6,73 против 153,8 за доллар). Молча выбрать нельзя: ошибка в любую сторону
меняет вывод по строке целиком. Поэтому ¥ помечается неоднозначным, и пересчёт
делается ТОЛЬКО по записанному решению с основанием (--yen cny|jpy), а само
основание печатается рядом с числом.

ЦЕН В РЕПОЗИТОРИИ НЕТ. Инструмент читает выгрузку, которая лежит вне дерева git,
и пишет либо счётчики (их можно коммитить: ни цен, ни привязки цены к номеру),
либо документ по пути вне репозитория.

    python gt/tools/tkp_tables.py --tkp out/tkp_full.json
    python gt/tools/tkp_tables.py --tkp out/tkp_full.json --yen cny --json /tmp/rows.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASK = ROOT / "gt/data/ship_lukoil.json"
FX = ROOT / "gt/data/fx_rates.json"

# Направления, которые в цену закупки не идут: наш исходящий запрос и заявка
# заказчика. Там числа есть, но это наши же требования, а не предложение.
SKIP_DIR = {"наш запрос", "заявка"}

# Денежное значение. Разделитель разрядов и разделитель дроби связаны, и
# смешивать их нельзя: PDF теряет пробелы, и «pcs 2 545.20¥» — это количество 2
# и цена 545,20, а не одно число 2 545,20. Замер по ¥-предложению: пробел,
# принятый за разделитель разрядов, ломал самопроверку у 56 строк формы, и
# ровно там, где строка сама называла и количество, и итог.
#   английская запись   1,234.56 · 2,273
#   русская запись      1 234,56 — и только с копейками: «pcs 12 413,700.00»
#                       иначе читается как одно число 12 413, а это количество 12
#                       и цена 413 700,00
#   без разрядов        545.20 · 545,20 · 1234
MONEY = re.compile(r"(?<![\dA-Za-z.,/-])("
                   r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
                   r"|\d{1,3}(?: \d{3})+,\d{1,2}(?!\d)"
                   r"|\d+[.,]\d{2}"
                   r"|\d{2,7}"
                   r")(?![\dA-Za-z/-])")
# Знаки и коды валют. ¥ стоит отдельно: он неоднозначен.
CUR_SIGN = {"$": "USD", "€": "EUR", "£": "GBP", "₽": "RUB", "₹": "INR", "₩": "KRW"}
CUR_CODE = re.compile(r"\b(USD|EUR|GBP|RUB|RUR|CNY|RMB|JPY|INR|AED|CHF|SEK|TRY|KRW|PLN|CZK)\b",
                      re.I)
YEN = "¥"
YEN_MARK = "¥ (юань или иена — не установлено)"
# Количество — целое, и однозначное тоже. Денежный шаблон его не ловит
# намеренно (иначе в кандидаты на цену попадёт каждая цифра строки), поэтому
# количества берутся своим шаблоном. Замер: без этого из 357 строк ¥-предложения
# самопроверку проходили 33 — количества «4» и «2» просто не извлекались.
QTY = re.compile(r"(?<![\dA-Za-z.,/-])(\d{1,6})(?![\dA-Za-z,/-])")
# Наименьшее количество для самопроверки. При количестве 1 единица равна итогу,
# и проверка вырождается: ей удовлетворяет любое повторённое в строке число.
MIN_QTY = 2


def norm_key(pn) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").split("(")[0].upper())


# Слова-единицы и служебные пометки: номером быть не могут.
UNIT_WORD = re.compile(r"^(pcs?|ea|set|kit|шт|компл|ед|nos?|штук)\.?$", re.I)
# Денежная форма токена: с разделителем разрядов или с дробной частью. ЧИСТО
# ЦИФРОВОЙ токен деньгами НЕ считается: номера 3420932, 012633000, 1019431-1600
# — настоящие артикулы, и отказ по ним обрушил разбор трёх файлов (Cummins с 42
# строк до 4, Jenbacher с 20 до 4).
MONEY_SHAPE = re.compile(r"^\d{1,3}(?:[ ,]\d{3})+(?:[.,]\d{1,2})?$|^\d+[.,]\d{1,2}$")
CUR_ANY = re.compile(r"[$€£₽₹₩¥]|\b(USD|EUR|GBP|RUB|RUR|CNY|RMB|JPY|INR|AED|CHF|SEK|TRY|KRW"
                     r"|PLN|CZK)\b", re.I)


def looks_like_pn(s: str) -> bool:
    """Годится ли значение в номер. Отсекает то, чем номер быть не может.

    Замер по Quotation p76057: в поле номера у половины записей стоял «$0.00»
    или дата «08/13/2026». Замер по ¥-предложению: у двух САМЫХ ДОРОГИХ строк
    заявки (MW21215M и MW22316B/01, вместе 1,38 млн USD экспозиции) первый
    проход поставил в номер «¥4,964,400.00» — итог строки. Поэтому знак валюты
    в номере — отказ, и чисто денежная форма — тоже.
    """
    t = str(s or "").strip()
    if not t or CUR_ANY.search(t) or MONEY_SHAPE.fullmatch(t) or UNIT_WORD.match(t):
        return False
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}.*", t):
        return False
    k = norm_key(t)
    if len(k) < 4:
        return False
    return bool(re.search(r"\d", k))


def pn_from_row(text: str) -> str:
    """Номер из самой строки: первый годный токен, не номер позиции.

    Нужен, потому что поле номера первого прохода бывает занято итогом строки.
    Правило независимо от заявки: сверяться с ней тут нельзя, иначе эталон
    выводится из правила (правило 1 правил работы с данными).
    """
    for tok in re.split(r"[\s|]+", str(text or "")):
        tok = tok.strip(" .,;:")
        if looks_like_pn(tok):
            return tok
    return ""


def money_values(text: str) -> list[float]:
    out = []
    for m in MONEY.finditer(text):
        t = m.group(1)
        # Русская запись: пробелы — разряды, запятая — дробь.
        t = t.replace(" ", "").replace(",", ".") if " " in t or (
            "," in t and "." not in t and len(t.split(",")[-1]) <= 2) else t.replace(",", "")
        try:
            out.append(float(t))
        except ValueError:
            continue
    return out


def qty_values(text: str) -> list[int]:
    out = []
    for m in QTY.finditer(text):
        try:
            v = int(m.group(1))
        except ValueError:
            continue
        if MIN_QTY <= v <= 100000:
            out.append(v)
    return out


def currency_of(text: str) -> tuple[str, str]:
    """Валюта строки и то, чем она опознана. Неоднозначность не скрывается."""
    codes = {m.group(1).upper() for m in CUR_CODE.finditer(text)}
    codes = {"CNY" if c == "RMB" else "RUB" if c == "RUR" else c for c in codes}
    signs = {CUR_SIGN[s] for s in CUR_SIGN if s in text}
    seen = codes | signs
    if YEN in text:
        if "CNY" in seen and "JPY" not in seen:
            return "CNY", "знак ¥ и код CNY в той же строке"
        if "JPY" in seen and "CNY" not in seen:
            return "JPY", "знак ¥ и код JPY в той же строке"
        if not seen:
            return YEN_MARK, "только знак ¥, кода валюты в строке нет"
    if len(seen) == 1:
        c = next(iter(seen))
        return c, "код валюты в строке" if c in codes else "знак валюты в строке"
    if len(seen) > 1:
        return "", f"в строке несколько валют: {'/'.join(sorted(seen))}"
    return "", "валюты в строке нет"


def unit_by_arithmetic(text: str) -> tuple[float, float, int] | None:
    """Цена за единицу, подтверждённая строкой: единица × количество = итог.

    Возвращает (единица, итог, количество) либо None. Правила подобраны по
    разобранным ошибкам на живых строках:

    ДОПУСК УЗКИЙ. В предложении арифметика точна, и допуск нужен только на
    округление цены до копеек: 0,005 × количество. С прежним допуском 0,1 %
    строка «49 RW21024/1 pcs 22 768.30¥ | 16 902.60» давала ложную тройку
    22 × 768 = 16 896 (расхождение 6,6 при допуске 16,9) и перебивала верную
    768,30 × 22.

    НЕОДНОЗНАЧНОСТЬ — ОТКАЗ. Если строка даёт две разные цены, обе согласованы
    арифметически, и выбрать между ними нечем: цену не берём вовсе. Пропуск
    здесь дешевле ошибки — ошибка уходит в деньги отчёта.
    """
    vals = money_values(text)
    if len(vals) < 2:
        return None
    found: dict[float, tuple[float, float, int]] = {}
    for q in sorted(set(qty_values(text)), reverse=True):
        for unit in vals:
            if unit <= 0:
                continue
            want = unit * q
            for tot in vals:
                if tot <= unit:
                    continue
                if abs(tot - want) <= 0.02 + 0.005 * q:
                    found.setdefault(round(unit, 4), (unit, tot, q))
    if len(found) != 1:
        return None
    return next(iter(found.values()))


def load_rates() -> dict:
    if not FX.exists():
        return {"USD": 1.0}
    return json.loads(FX.read_text(encoding="utf-8")).get("rates", {"USD": 1.0})


def to_usd(value: float, cur: str, rates: dict, yen: str = "") -> tuple[float | None, str]:
    """Пересчёт в доллары. Неоднозначная валюта не пересчитывается молча."""
    if cur == YEN_MARK:
        if yen not in ("cny", "jpy"):
            return None, ("валюта не установлена: знак ¥ значит и юань, и иену, "
                          "а курсы различаются в 22,8 раза")
        cur = yen.upper()
    if not cur:
        return None, "валюта строки не установлена — пересчёт не делается"
    r = rates.get(cur)
    if not r:
        return None, f"курса {cur} в gt/data/fx_rates.json нет"
    return value / r, f"пересчёт по курсу {cur} {r}"


def extract(doc: dict, yen: str = "") -> tuple[list[dict], Counter]:
    """Строки с ценой, подтверждённой арифметикой. Плюс счётчики отказов."""
    rates = load_rates()
    out: list[dict] = []
    why = Counter()
    for f in doc.get("files", []):
        if (f.get("direction") or "неизвестно").strip() in SKIP_DIR:
            continue
        carry = ""
        for p in f.get("prices", []):
            raw = str(p.get("raw") or "")
            pn = p.get("pn") if looks_like_pn(p.get("pn")) else pn_from_row(raw)
            if pn:
                carry = pn
            trio = unit_by_arithmetic(raw)
            if not trio:
                why["строка не сходится арифметикой" if pn else
                    "строка без номера и без арифметики"] += 1
                continue
            unit, tot, qty = trio
            pn = pn or carry
            if not pn:
                why["арифметика сошлась, номера нет"] += 1
                continue
            cur, cur_why = currency_of(raw)
            usd, conv = to_usd(unit, cur, rates, yen)
            if usd is None:
                why[f"цена есть, в доллары не приведена: {cur_why}"] += 1
            out.append({
                "pn": pn, "key": norm_key(pn),
                "unit": unit, "total": tot, "qty_in_file": qty,
                "currency": cur or "не установлена", "currency_why": cur_why,
                "usd": usd, "usd_why": conv,
                "file": f.get("file_name", ""), "origin": f.get("origin", ""),
                "direction": f.get("direction", ""),
                "sheet": p.get("sheet", ""), "row": p.get("row"),
                "rule": "единица × количество = итог в той же строке",
                "raw": raw[:400],
            })
    return out, why


def counters(rows: list[dict], why: Counter, doc: dict) -> dict:
    """Счётчики для репозитория: ни цен, ни привязки цены к номеру."""
    ask = {k for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]
           for k in (norm_key(r.get("pn")),) if k}
    hit = [r for r in rows if r["key"] in ask]
    by_file = Counter(r["file"] for r in hit)
    return {
        "files_read": len(doc.get("files", [])),
        "rows_confirmed_by_arithmetic": len(rows),
        "rows_matching_request": len(hit),
        "rows_matching_request_in_usd": sum(1 for r in hit if r["usd"] is not None),
        "distinct_request_parts": len({r["key"] for r in hit}),
        "files_with_matches": len(by_file),
        "rejected": dict(why.most_common(8)),
        "what_it_means": ("Цена принята, только если строка подтвердила её сама: единица × "
                          "количество = итог. Так исключён разбор, принимающий итог за цену "
                          "(у CambiaTech заголовок указывал на колонку итога) и принимающий "
                          "номер позиции за цену (у Quotation p76057). Строки, не приведённые "
                          "к долларам, остаются с названной причиной, а не отбрасываются."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tkp", required=True, help="полная выгрузка bitrix_tkp.py (вне репозитория)")
    ap.add_argument("--yen", choices=("cny", "jpy"), default="",
                    help="решение по знаку ¥; без него цены в ¥ не пересчитываются")
    ap.add_argument("--json", help="куда выписать строки с ценами (ВНЕ репозитория)")
    ap.add_argument("--counters", help="куда выписать счётчики (можно в репозиторий)")
    ap.add_argument("--keys-out", help="куда выписать СПИСОК НОМЕРОВ с подтверждённой ценой, "
                                      "без самих цен (можно в репозиторий)")
    a = ap.parse_args()

    tkp = Path(a.tkp)
    if not tkp.exists():
        print(f"нет {tkp}", file=sys.stderr)
        return 1
    doc = json.loads(tkp.read_text(encoding="utf-8"))
    rows, why = extract(doc, a.yen)
    c = counters(rows, why, doc)
    print(f"файлов прочитано {c['files_read']} · строк с ценой, подтверждённой "
          f"арифметикой {c['rows_confirmed_by_arithmetic']} · из них номеров заявки "
          f"{c['rows_matching_request']} ({c['distinct_request_parts']} разных) · "
          f"приведено к долларам {c['rows_matching_request_in_usd']}")
    for k, v in why.most_common(6):
        print(f"    отказ: {k}: {v}")
    ask = {norm_key(r.get("pn")) for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]}
    byf = Counter(r["file"] for r in rows if r["key"] in ask)
    for fn, n in byf.most_common(10):
        print(f"    {n:>4} строк заявки | {fn}")
    if a.json:
        out = Path(a.json).resolve()
        try:
            out.relative_to(ROOT)
        except ValueError:
            pass
        else:
            print(f"ОТКАЗ: {out} внутри репозитория, а строки содержат цены контрагентов.",
                  file=sys.stderr)
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"rows": rows, "counters": c}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"{out} — {len(rows)} строк (вне репозитория)")
    if a.keys_out:
        # Зачем список без цен. Лестница покрытия (gt/tools/ship_coverage.py) читает
        # только репозиторий, а самое сильное доказательство цены — письменное
        # предложение контрагента — лежит вне него. Из-за этого 173 позиции с
        # найденной ценой в лестницу не попадали вовсе, и она показывала знание
        # хуже, чем оно есть. Здесь пишется ровно факт «по этому номеру цена в
        # предложении есть» и её происхождение. Ни числа, ни валюты суммы.
        seen: dict[str, dict] = {}
        for r in rows:
            if r.get("usd") is None:
                continue
            seen.setdefault(r["key"], {
                "pn": r["pn"], "file": r.get("file", ""), "origin": r.get("origin", ""),
                "direction": r.get("direction", ""),
                "currency_known": bool(r.get("currency") and "не установлена" not in
                                       str(r.get("currency"))),
                "rule": r.get("rule", ""),
            })
        Path(a.keys_out).write_text(json.dumps({
            "updated": "2026-09-18",
            "source": ("Номера, по которым цена закупки подтверждена письменным предложением "
                       "контрагента из вложений сделки: единица × количество = итог в той же "
                       "строке. Пишет gt/tools/tkp_tables.py --keys-out; руками не заполнять."),
            "what_it_gives": ("Факт и происхождение, БЕЗ ЧИСЕЛ: по этому номеру предложение "
                              "называет цену, и вот в каком файле какой сделки она стоит. Сами "
                              "цены — коммерческие данные контрагента и остаются в выгрузке вне "
                              "репозитория. Нужен лестнице покрытия: без него 173 позиции с "
                              "найденной ценой в неё не попадали, и она занижала наше знание."),
            "parts": [dict(v, key=k) for k, v in sorted(seen.items())],
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"номеров с подтверждённой ценой записано в {a.keys_out}: {len(seen)}")
    if a.counters:
        Path(a.counters).write_text(json.dumps({
            "updated": "2026-09-18",
            "source": ("Второй проход по вложениям сделок: цена, подтверждённая арифметикой "
                       "строки. Считает gt/tools/tkp_tables.py по выгрузке вне репозитория. "
                       "Ни цен, ни привязки цены к номеру здесь нет."),
            "counters": c,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"счётчики записаны в {a.counters}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
