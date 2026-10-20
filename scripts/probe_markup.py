#!/usr/bin/env python3
"""Зонд: закупка по входящим КП для выигранного объёма ЛУКОЙЛ-Энергосети.

Вопрос владельца 20.10.2026: «Я забрал этот объём. Какой общий коэффициент от
входящих КП тут?» Объём — 59 строк таблицы заказчика (Siemens SGT-400 и Solar
Taurus 60S/70), где цена КВАНТ минимальная. Коэффициент = наша цена / цена
входящего КП поставщика.

ПОЧЕМУ ЗОНД СЧИТАЕТ ТОЛЬКО ЗАКУПКУ. Наши цены — в таблице заказчика у владельца,
цены поставщиков — во вложениях сделки и в закрытой базе. Ни те, ни другие не идут
ни в публичный репозиторий, ни в журнал прогона (CLAUDE.md, правило 5; распоряжение
30.09.2026: наружу — только агрегаты). Поэтому здесь нет ни одной нашей цены, а
закупка печатается суммами по группам строк. Делит нашу сумму на сумму закупки
агент, у которого наша сумма по тем же строкам уже есть.

ДВА СПОСОБА, независимо друг от друга (распоряжение 26.09.2026):
  А. Файлы из Битрикса: сделка 22566 → записи СП-166 → вложения, разбор
     gt/tools/bitrix_tkp.py. Цена принимается двумя ступенями: «арифметика» —
     строка сама подтвердила единица × количество = итог (gt/tools/tkp_tables.py);
     «колонка» — значение из колонки цены по заголовку, без арифметики.
     Наше ТКП, положенное в поле входящих, отделяется по имени файла (НАШЕ_ТКП):
     прогон 39432926797 без этого взял его за предложение поставщика. Оно же
     служит нашей стороной: отношение «наше ТКП / входящее КП» считается по
     строкам внутри прогона, наружу — только статистика отношений.
  Б. Закрытая база: lib_prices_live по ключу номера; входящие КП («разбор КП»,
     «письмо поставщика») отдельно от справочного «каталога ЗИП».
По строке берётся НАИМЕНЬШАЯ цена в рублях: вопрос «сколько мы зарабатываем на
лучшем входящем предложении».

ЗНАК ¥. ¥ — это и юань, и иена (курсы различаются в 22,8 раза). В QT 127 код CNY
назван 7 раз, JPY — ни разу (прогон 39432926797), поэтому ¥ читается как CNY;
вход PROBE_YEN меняет решение, улики печатаются по каждому файлу.

ЧТО В ЖУРНАЛ. По файлу и по группе: сколько строк с ценой и какие номера (номер —
обозначение изготовителя, он уже лежит в gt/data/rfq_demand.json); сумма в рублях
по курсу ЦБ, округлённая до трёх значащих цифр, — только по корзине не меньше
трёх строк (иначе сумма одной строки и есть её цена); отношения наших цен к
закупке — медианой, квартилями и корзинами; валюты, базис и даты — счётом. НЕ
идут: цена строки, итог строки, поставщик поимённо.

НАГРУЗКА НА ПОРТАЛ: поля сделки и СП-166 (2 вызова), записи СП-166 сделки
(1–4 страницы), комментарии таймлайна (1), сделка (1), скачивание вложений
(до PROBE_MAX_FILES, по умолчанию 150) — через общую очередь клиента.

    BITRIX_WEBHOOK_URL=… SUPABASE_DB_URL=… python scripts/probe_markup.py
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gt/tools"))

DEAL = int(os.environ.get("PROBE_DEAL", "22566"))
MAX_FILES = int(os.environ.get("PROBE_MAX_FILES", "150"))
MIN_LINES = 3          # меньше трёх покрытых строк — сумма группы не печатается

GROUPS = {
    "G1": "Siemens SGT-400 · клапаны скида 64/60082…",
    "G2": "Siemens SGT-400 · клапаны 64/60040…, 64/60030…, 64/6009…",
    "G3": "Siemens SGT-400 · модули контроля и КИП",
    "G4": "Siemens SGT-400 · топливные: CT95038/01, 64/04012024/1",
    "G5": "Solar Taurus 60S · клапаны и регуляторы",
    "G6": "Solar Taurus 60S/70 · блок управления и датчики",
}

# Строки объёма: группа, номер, количество. Количество — из заявки
# (gt/data/rfq_demand.json, лист «Энергосети»), с ним сверено построчно.
# Наших цен здесь нет и быть не должно.
LINES = [
    ("G3", "64/51031004/5", 2), ("G3", "64/51031004/6", 2), ("G3", "64/51031004/7", 2),
    ("G3", "64/51031004/8", 2), ("G3", "64/51031004/12", 2), ("G3", "64/51031004/17", 2),
    ("G3", "64/09001062/139", 8), ("G3", "MW05167", 2),
    ("G2", "64/60097007/1", 2),
    ("G4", "CT95038/01", 2),
    ("G3", "CT97200/01", 16),
    ("G2", "64/60040233/32", 2),
    ("G4", "64/04012024/1", 2),
    ("G2", "64/60093008/6", 2), ("G2", "64/60096012/11", 2),
    ("G1", "64/60082037/3", 2), ("G1", "64/60082038/1", 2), ("G1", "64/60082039/1", 2),
    ("G1", "64/60082044/1", 2), ("G1", "64/60082040/1", 2), ("G1", "64/60082041/1", 2),
    ("G2", "64/60030062/39", 2),
    ("G1", "64/60082046/1", 2),
    ("G2", "64/60030062/33", 2),
    ("G6", "1105336-1", 3), ("G6", "120649-18", 1), ("G6", "1071327-1", 1),
    ("G5", "190185-203", 4),
    ("G6", "1046415-79", 4),
    ("G5", "1019431-1600", 4), ("G5", "1066290-2", 4), ("G5", "1045095-201", 4),
    ("G5", "1023518-201", 4), ("G5", "190016-10", 4), ("G5", "190016-9", 4),
    ("G5", "190015-10", 8), ("G5", "186648-700", 4), ("G5", "186648-1", 4),
    ("G5", "1042018-202", 4), ("G5", "186273-1", 4), ("G5", "186273-36", 4),
    ("G4", "CT95038/01", 8),
    ("G1", "64/60082037/1", 4), ("G1", "64/60082041/1", 4), ("G1", "64/60082045/1", 4),
    ("G1", "64/60082039/1", 4), ("G1", "64/60082038/1", 4), ("G1", "64/60082040/1", 4),
    ("G1", "64/60082044/1", 4), ("G1", "64/60082046/1", 4),
    ("G2", "64/60040233/17", 4), ("G2", "64/60040233/16", 4), ("G2", "64/60040228/2", 4),
    ("G1", "64/60082032/1", 4), ("G1", "64/60082033/4", 4),
    ("G2", "64/60040257/1014", 8), ("G2", "64/60040257/1", 8),
    ("G2", "64/60097007/1", 4),
    ("G1", "64/60082033/4", 4),
]

YEN_CLUES = {
    "CNY": re.compile(r"\bRMB\b|\bCNY\b|人民币|元|\bCHINA\b|\bP\.?R\.?C\.?\b|SHANGHAI|BEIJING"
                      r"|SHENZHEN|GUANGZHOU|TIANJIN|HANGZHOU|NINGBO|XIAMEN|WUXI|SUZHOU|\+86",
                      re.I),
    "JPY": re.compile(r"\bJPY\b|円|\bJAPAN\b|TOKYO|OSAKA|YOKOHAMA|NAGOYA|KOBE|\+81", re.I),
}


def ключ(pn: str) -> str:
    from pnkey import key
    return key(pn)


def знач3(x: float) -> str:
    """Три значащие цифры с пробелами разрядов: сумма группы, а не цена строки."""
    if not x:
        return "0"
    p = 3 - int(math.floor(math.log10(abs(x)))) - 1
    v = round(x, p)
    return f"{v:,.0f}".replace(",", " ")


# ---------------------------------------------------------------- курсы ЦБ
def курсы_цб(day: date) -> tuple[dict, str]:
    """Рублей за единицу валюты по ЦБ на дату. Запасной путь — gt/data/fx_rates.json."""
    import requests
    url = f"https://www.cbr.ru/scripts/XML_daily.asp?date_req={day:%d/%m/%Y}"
    try:
        r = requests.get(url, timeout=30)
        r.encoding = "windows-1251"
        out = {"RUB": 1.0}
        for m in re.finditer(r"<CharCode>(\w+)</CharCode>\s*<Nominal>(\d+)</Nominal>"
                             r"\s*<Name>[^<]*</Name>\s*<Value>([\d,]+)</Value>", r.text):
            out[m.group(1)] = float(m.group(3).replace(",", ".")) / int(m.group(2))
        if "USD" in out and "CNY" in out:
            return out, f"ЦБ РФ на {day:%d.%m.%Y}"
    except Exception as e:                                   # noqa: BLE001
        print(f"курс ЦБ не получен ({type(e).__name__}) — беру gt/data/fx_rates.json")
    fx = json.loads((ROOT / "gt/data/fx_rates.json").read_text(encoding="utf-8"))["rates"]
    rub = fx["RUB"]
    return {c: rub / v for c, v in fx.items() if v}, "gt/data/fx_rates.json (справочный)"


def в_рубли(value: float, cur: str, rates: dict, yen: str) -> float | None:
    c = (cur or "").upper().strip()
    if c.startswith("¥") or c == "YEN":
        c = yen
    c = {"RMB": "CNY", "RUR": "RUB", "$": "USD", "€": "EUR", "₽": "RUB"}.get(c, c)
    r = rates.get(c)
    return value * r if r else None


# ---------------------------------------------------------------- вилки (открытые)
def вилки() -> dict:
    d = json.loads((ROOT / "gt/data/rfq_prices.json").read_text(encoding="utf-8"))
    out = {}
    for p in d.get("prices", []):
        lo, hi = p.get("usd_lo"), p.get("usd_hi")
        if lo and hi:
            out[ключ(p["pn"])] = (float(lo) + float(hi)) / 2
    return out


# ---------------------------------------------------------------- печать агрегатов
def разбиение(покрыто: dict[str, set]) -> list[tuple[str, list[str]]]:
    """Группы так, чтобы в каждой печатной было не меньше MIN_LINES покрытых строк.

    Мелкие группы сливаются в «прочие»; если и там меньше порога — «прочие»
    присоединяются к самой крупной группе. Иначе сумма группы из одной строки
    — это цена строки, и её не спасает никакое округление.
    """
    крупные = [g for g in GROUPS if len(покрыто.get(g, ())) >= MIN_LINES]
    мелкие = [g for g in GROUPS if 0 < len(покрыто.get(g, ())) < MIN_LINES]
    out = [(g, [g]) for g in крупные]
    if мелкие:
        n = sum(len(покрыто[g]) for g in мелкие)
        if n >= MIN_LINES:
            out.append(("+".join(мелкие), мелкие))
        elif out:
            big = max(range(len(out)), key=lambda i: sum(len(покрыто[g]) for g in out[i][1]))
            label, gs = out[big]
            out[big] = (label + "+" + "+".join(мелкие), gs + мелкие)
    return out


def сумма_по_группам(отступ: str, цена: dict[int, float], валюта: dict[int, str] | None = None,
                     пометка: str = "закупка") -> float:
    """Суммы по группам строк. цена: индекс строки → рублей за единицу.

    Сумма печатается только по корзине, где не меньше MIN_LINES строк, — иначе
    это цена строки. В валюте источника — только если корзина одновалютная.
    """
    покрыто: dict[str, set] = defaultdict(set)
    for i in цена:
        покрыто[LINES[i][0]].add(i)
    всего = len(цена)
    if всего < MIN_LINES:
        print(f"{отступ}строк {всего} — меньше трёх, суммы не печатаются")
        return 0.0
    итог = 0.0
    for label, gs in разбиение(покрыто):
        idx = sorted(i for g in gs for i in покрыто[g])
        s = sum(цена[i] * LINES[i][2] for i in idx)
        итог += s
        вал = {валюта[i] for i in idx} if валюта else set()
        хвост = f" · валюта источника {next(iter(вал))}" if len(вал) == 1 else (
            f" · валюты {sorted(вал)}" if вал else "")
        print(f"{отступ}[{label}] строк {len(idx)} · {пометка} ≈ {знач3(s)} ₽{хвост}")
        print(f"{отступ}    строки: {', '.join(f'L{i + 1:02d}={LINES[i][1]}' for i in idx)}")
    print(f"{отступ}ИТОГО строк {всего}: {пометка} ≈ {знач3(итог)} ₽")
    return итог


def отношения(отступ: str, наше: dict[int, float], закупка: dict[int, float]) -> None:
    """Наша цена / цена закупки по строкам, где есть обе, — только статистикой."""
    общие = sorted(set(наше) & set(закупка))
    if len(общие) < MIN_LINES:
        print(f"{отступ}строк с обеими ценами {len(общие)} — меньше трёх, не печатается")
        return
    for сег, gs in (("все", set(GROUPS)), ("Siemens", {"G1", "G2", "G3", "G4"}),
                    ("Solar", {"G5", "G6"})):
        idx = [i for i in общие if LINES[i][0] in gs]
        if len(idx) < MIN_LINES:
            if idx:
                print(f"{отступ}{сег}: строк {len(idx)} — меньше трёх")
            continue
        r = sorted(наше[i] / закупка[i] for i in idx)
        k = (sum(наше[i] * LINES[i][2] for i in idx)
             / sum(закупка[i] * LINES[i][2] for i in idx))
        q = statistics.quantiles(r, n=4) if len(r) >= 4 else [r[0], statistics.median(r), r[-1]]
        корз = Counter("<1,0" if x < 1 else "1,0–1,3" if x < 1.3 else "1,3–1,6" if x < 1.6
                       else "1,6–2,0" if x < 2 else "2,0–3,0" if x < 3 else "≥3,0" for x in r)
        print(f"{отступ}{сег}: строк {len(idx)} · К по сумме {k:.2f} · медиана {statistics.median(r):.2f}"
              f" · квартили {q[0]:.2f}…{q[-1]:.2f} · по корзинам {dict(sorted(корз.items()))}")


# Наше ТКП, положенное в поле входящих: первый прогон (39432926797) взял его за
# предложение поставщика. Признак — слово «ТКП» и заказчик в имени файла; список
# классификации печатается целиком, чтобы ошибку было видно.
НАШЕ_ТКП = re.compile(r"(?<![А-Яа-яA-Za-z])(ТКП|TKP)(?![А-Яа-яA-Za-z]).*(ЛУКОЙЛ|LUKOIL|ЭНЕРГОСЕТ)",
                      re.I)


def способ_а(rates: dict, yen: str) -> tuple[dict, dict, dict]:
    """Вложения сделки. Возвращает (входящее, наше ТКП, сведения о файлах)."""
    wh = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not wh:
        print("нет BITRIX_WEBHOOK_URL — способ А пропущен")
        return {}, {}, {}
    import bitrix_tkp as bt
    import tkp_tables as tt
    from bitrix_client import BitrixClient

    bx = BitrixClient(wh)
    bt._ОЧЕРЕДЬ = bx
    ff = bt.file_fields(bx, bt.DEAL_ENTITY)
    try:
        rf = bt.file_fields(bx, bt.SPA_RFQ)
    except Exception as e:                                   # noqa: BLE001
        rf = {}
        print(f"поля СП-166 не прочитались ({type(e).__name__}) — зашитые восемь")
    cands = bt.from_deal(bx, DEAL, ff)
    rq, n_rfq = bt.from_rfq(bx, DEAL, rf)
    cands += rq + bt.from_timeline(bx, DEAL)
    seen, uniq = set(), []
    for c in cands:
        k = c["file_id"] or c["url"][:120]
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    todo = sorted((c for c in uniq if c["direction"] in ("входящее", "неизвестно", "наша цена")),
                  key=bt.rank)
    print(f"сделка {DEAL}: записей СП-166 {n_rfq}, вложений {len(uniq)}, к разбору "
          f"{len(todo)} (предел {MAX_FILES}); по направлению "
          f"{dict(Counter(c['direction'] for c in todo))}")

    files = []
    отказ = Counter()
    for c in todo[:MAX_FILES]:
        body, how, name = bt.take(bx, c)
        if not body:
            отказ[how[:40]] += 1
            continue
        fname = c.get("file_name") or name or ""
        rows, _how = bt.parse(fname, body)
        hdr = bt.header_map(rows) if rows else {}
        fcur = bt.file_currency(rows) if rows else ("", "")
        pr = bt.price_rows(rows, hdr, fcur) if rows else []
        text = " ".join(" ".join(str(x) for x in cells if x not in (None, ""))
                        for _s, _i, cells in rows)
        класс = ("наше ТКП (по имени файла)" if НАШЕ_ТКП.search(fname)
                 else c["direction"])
        files.append({"file_name": fname, "origin": c["origin"], "field": c["field_name"],
                      "direction": c["direction"], "класс": класс, "prices": pr,
                      "fcur": fcur[0] or "не установлена",
                      "улики": ({v: len(rx.findall(text)) for v, rx in YEN_CLUES.items()}
                                | {"знаков ¥": text.count("¥")}) if "¥" in text else None})
    if отказ:
        print(f"не скачано: {dict(отказ)}")
    print(f"разобрано файлов: {len(files)}; классы {dict(Counter(f['класс'] for f in files))}")
    for f in files:
        if f["класс"].startswith("наше ТКП"):
            print(f"  наше ТКП: {f['origin']} · поле «{f['field']}» · {f['file_name']}")

    keys = {ключ(pn): [] for _g, pn, _q in LINES}
    for i, (_g, pn, _q) in enumerate(LINES):
        keys[ключ(pn)].append(i)
    # Предложения по строке: (файл, ступень, валюта, единица, количество в файле).
    предл: dict[int, list] = defaultdict(list)
    for n, f in enumerate(files):
        арифм, _why = tt.extract({"files": [f]}, yen="")
        взято = set()
        for r in арифм:
            for i in keys.get(r["key"], []):
                предл[i].append({"f": n, "ступень": "арифметика", "cur": str(r["currency"]),
                                 "unit": float(r["unit"]), "qty": r.get("qty_in_file") or 0})
                взято.add(i)
        for p in f["prices"]:
            if not p.get("is_price"):
                continue
            pn = p.get("pn") if tt.looks_like_pn(p.get("pn")) else tt.pn_from_row(p.get("raw"))
            for i in keys.get(ключ(pn) if pn else "", []):
                if i in взято:
                    continue
                предл[i].append({"f": n, "ступень": "колонка", "cur": str(p.get("currency") or ""),
                                 "unit": float(p["price"]), "qty": 0})

    band = вилки()

    def руб(o) -> float | None:
        return в_рубли(o["unit"], o["cur"], rates, yen)

    print("\nФАЙЛЫ, ГДЕ НАШЛИСЬ НОМЕРА ОБЪЁМА")
    for n, f in enumerate(files):
        мои = {i: [o for o in os_ if o["f"] == n] for i, os_ in предл.items()}
        мои = {i: os_ for i, os_ in мои.items() if os_}
        if not мои:
            continue
        print(f"\n  {f['file_name']} · {f['origin']} · поле «{f['field']}» · класс «{f['класс']}» · "
              f"валюта файла {f['fcur']}" + (f" · улики ¥ {f['улики']}" if f["улики"] else ""))
        ступ = Counter(o["ступень"] for os_ in мои.values() for o in os_)
        вал = Counter(o["cur"][:14] for os_ in мои.values() for o in os_)
        неод = sum(1 for os_ in мои.values() for o in os_ if len({round(x["unit"], 2) for x in os_}) > 1)
        подозр = Counter()
        цена, валюта = {}, {}
        for i, os_ in мои.items():
            vals = [(руб(o), o) for o in os_]
            vals = [(v, o) for v, o in vals if v]
            if not vals:
                continue
            v, o = min(vals, key=lambda t: t[0])
            цена[i], валюта[i] = v, o["cur"][:14]
            b = band.get(ключ(LINES[i][1]))
            if b:
                rel = v / rates["USD"] / b
                подозр["ниже 5 % вилки" if rel < 0.05 else "выше 20 вилок" if rel > 20
                       else "в разумных пределах"] += 1
            else:
                подозр["вилки нет"] += 1
        print(f"    строк {len(мои)} · ступени {dict(ступ)} · валюты {dict(вал)} · "
              f"строк с разными значениями в файле {неод} · против вилки {dict(подозр)}")
        сумма_по_группам("    ", цена, валюта,
                         "наша цена" if f["класс"].startswith("наше ТКП") else "закупка")

    def лучшее(классы: set, ступени: set) -> tuple[dict, dict]:
        цена, валюта = {}, {}
        for i, os_ in предл.items():
            vals = [(руб(o), o) for o in os_
                    if files[o["f"]]["класс"] in классы and o["ступень"] in ступени]
            vals = [(v, o) for v, o in vals if v]
            if vals:
                v, o = min(vals, key=lambda t: t[0])
                цена[i], валюта[i] = v, o["cur"][:14]
        return цена, валюта

    вход1 = лучшее({"входящее"}, {"арифметика"})
    вход12 = лучшее({"входящее"}, {"арифметика", "колонка"})
    наше1 = лучшее({"наше ТКП (по имени файла)"}, {"арифметика"})
    наше12 = лучшее({"наше ТКП (по имени файла)"}, {"арифметика", "колонка"})
    print("\nСПОСОБ А · ВХОДЯЩИЕ КП (наше ТКП исключено), наименьшая цена по строке")
    print("  ступень «арифметика»:")
    сумма_по_группам("    ", *вход1)
    print("  ступени «арифметика», иначе «колонка»:")
    сумма_по_группам("    ", *вход12)
    print("\nСПОСОБ А · НАШЕ ТКП ИЗ БИТРИКСА (для сверки с таблицей заказчика)")
    print("  ступень «арифметика»:")
    сумма_по_группам("    ", *наше1, пометка="наша цена")
    print("  ступени «арифметика», иначе «колонка»:")
    сумма_по_группам("    ", *наше12, пометка="наша цена")
    print("\nОТНОШЕНИЕ «НАШЕ ТКП / ВХОДЯЩЕЕ КП» ПО СТРОКАМ (обе цены по арифметике строки)")
    отношения("  ", наше1[0], вход1[0])
    print("ТО ЖЕ, ступени «арифметика», иначе «колонка»")
    отношения("  ", наше12[0], вход12[0])
    return вход12[0], наше12[0], {}


def способ_б(rates: dict, yen: str) -> None:
    dsn = (os.getenv("SUPABASE_DB_URL") or "").strip()
    if not dsn:
        print("нет SUPABASE_DB_URL — способ Б пропущен")
        return
    import psycopg2
    keys = sorted({ключ(pn).lower() for _g, pn, _q in LINES})
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000")
    conn.set_session(readonly=True)
    with conn.cursor() as cur:
        cur.execute("select to_regclass('lib_prices_live') is not null")
        rel = "lib_prices_live" if cur.fetchone()[0] else "lib_prices"
        cur.execute("select to_regclass('lib_rfq_cards') is not null")
        карточки = set()
        if cur.fetchone()[0]:
            cur.execute("select card_id from lib_rfq_cards where deal_id = %s", (str(DEAL),))
            карточки = {str(r[0]) for r in cur.fetchall()}
        cur.execute(f"""
            select lib_pn_key(p.part_number), p.price::float, upper(coalesce(p.currency, '')),
                   coalesce(p.basis, ''), p.price_date, coalesce(p.rfq_id, ''),
                   coalesce(p.feed, ''), coalesce(p.rfq_company, '')
              from {rel} p
             where lib_pn_key(p.part_number) = any(%s)
               and coalesce(p.feed, '') not in ('ТКП КВАНТ (отпускная цена)',
                                                'наше КП заказчику', 'прайсы конкурентов')
               and p.price > 0""", (keys,))
        rows = cur.fetchall()
    conn.close()
    print(f"\nСПОСОБ Б — закрытая база ({rel}): строк цены по номерам объёма {len(rows)}; "
          f"карточек СП-166 сделки {DEAL} в базе {len(карточки)}")
    поток_кп = {"разбор КП", "письмо поставщика"}
    for имя, отбор in (("входящие КП (разбор КП, письмо поставщика)", lambda r: r[6] in поток_кп),
                       ("входящие КП по этой сделке", lambda r: r[6] in поток_кп and (
                           r[5] in карточки or r[5] == str(DEAL))),
                       ("справочно: каталог ЗИП", lambda r: r[6] == "каталог ЗИП")):
        sel = [r for r in rows if отбор(r)]
        print(f"\n  {имя}: строк цены {len(sel)} · валюты {dict(Counter(r[2] or '(нет)' for r in sel))}"
              f" · базис {dict(Counter(r[3] or '(нет)' for r in sel))}")
        даты = sorted(r[4] for r in sel if r[4])
        if даты:
            print(f"    даты цен {даты[0]} … {даты[-1]}; старше 180 дней: "
                  f"{sum(1 for d in даты if (date.today() - d).days > 180)} из {len(даты)}")
        цена, валюта = {}, {}
        for i, (_g, pn, _q) in enumerate(LINES):
            vals = [(в_рубли(r[1], r[2], rates, yen), r[2]) for r in sel if r[0] == ключ(pn).lower()]
            vals = [(v, c) for v, c in vals if v]
            if vals:
                цена[i], валюта[i] = min(vals)
        сумма_по_группам("    ", цена, валюта,
                         "каталожная цена" if "каталог" in имя else "закупка")


def main() -> int:
    print(f"ОБЪЁМ: {len(LINES)} строк, {len({ключ(p) for _g, p, _q in LINES})} номеров; "
          f"сделка {DEAL}. Наших цен в зонде нет; суммы — по группам не меньше трёх строк.")
    for g, name in GROUPS.items():
        print(f"  {g}: {name} — строк {sum(1 for x in LINES if x[0] == g)}")
    rates, ист = курсы_цб(date.today())
    # Знак ¥: в QT 127 код CNY назван 7 раз, JPY — ни разу (прогон 39432926797),
    # первый проход тоже установил валюту файла CNY. Вход YEN меняет решение.
    yen = os.environ.get("PROBE_YEN", "CNY").upper()
    print(f"курс: {ист}; USD {rates.get('USD', 0):.4f} · CNY {rates.get('CNY', 0):.4f} · "
          f"JPY {rates.get('JPY', 0):.5f} · EUR {rates.get('EUR', 0):.4f} ₽; знак ¥ читается как {yen}")
    try:
        способ_а(rates, yen)
    except Exception as e:                                   # noqa: BLE001
        print(f"способ А упал: {type(e).__name__}: {str(e)[:200]}")
    try:
        способ_б(rates, yen)
    except Exception as e:                                   # noqa: BLE001
        print(f"способ Б упал: {type(e).__name__}: {str(e)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
