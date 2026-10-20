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
     В закупку идёт только направление «входящее»; «неизвестно» (в том числе
     Result file сделки) считается отдельно и в сумму не входит.
  Б. Закрытая база: lib_prices_live, закупочные потоки, по ключу номера.
По строке берётся НАИМЕНЬШАЯ цена в рублях: вопрос «сколько мы зарабатываем на
лучшем входящем предложении».

ЗНАК ¥. В предложении по SGT-400 цены в ¥, а ¥ — это и юань, и иена (курсы
различаются в 22,8 раза). Молча выбрать нельзя: зонд считает оба варианта и
печатает улики из текста файла счётом (коды валют, страна, телефонный код), плюс
медиану отношения цены к нашей открытой вилке (gt/data/rfq_prices.json) по каждому
варианту.

ЧТО В ЖУРНАЛ. По группе: сколько строк покрыто ценой и какие номера (номер —
обозначение изготовителя, он уже лежит в gt/data/rfq_demand.json); сумма закупки
в рублях по курсу ЦБ, округлённая до трёх значащих цифр, — только если в группе
покрыто не меньше трёх строк (иначе сумма одной строки и есть её цена); валюты,
базис и даты — счётом. НЕ идут: цена строки, итог строки, поставщик поимённо.

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


def печать_способа(имя: str, выбор: dict, rates: dict, ист_курса: str) -> None:
    """выбор: индекс строки → {"rub": {вариант ¥: рубли за единицу}, ...}."""
    print(f"\n=== СПОСОБ {имя} ===")
    for вариант in ("CNY", "JPY"):
        покрыто: dict[str, set] = defaultdict(set)
        сумма: dict[str, float] = defaultdict(float)
        for i, v in выбор.items():
            rub = v["rub"].get(вариант)
            if rub is None:
                continue
            g, pn, qty = LINES[i]
            покрыто[g].add(i)
            сумма[g] += rub * qty
        всего = sum(len(s) for s in покрыто.values())
        print(f"\n  вариант знака ¥ = {вариант} · курс {ист_курса} · строк с ценой: {всего} из {len(LINES)}")
        if всего < MIN_LINES:
            print("  покрытых строк меньше трёх — суммы не печатаются")
            continue
        итог = 0.0
        for label, gs in разбиение(покрыто):
            idx = sorted(i for g in gs for i in покрыто[g])
            s = sum(сумма[g] for g in gs)
            итог += s
            print(f"  [{label}] строк {len(idx)} · закупка ≈ {знач3(s)} ₽ без НДС")
            print(f"      строки: {', '.join(f'L{i + 1:02d}={LINES[i][1]}' for i in idx)}")
        print(f"  ИТОГО по покрытым строкам: закупка ≈ {знач3(итог)} ₽")
        for g in GROUPS:
            if not покрыто.get(g):
                print(f"  {g}: строк с ценой 0 из {sum(1 for x in LINES if x[0] == g)}")


# ---------------------------------------------------------------- способ А: Битрикс
def способ_а(rates: dict) -> dict:
    wh = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not wh:
        print("нет BITRIX_WEBHOOK_URL — способ А пропущен")
        return {}
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

    files, улики = [], {}
    отказ = Counter()
    for c in todo[:MAX_FILES]:
        body, how, name = bt.take(bx, c)
        if not body:
            отказ[how[:40]] += 1
            continue
        fname = c.get("file_name") or name or ""
        rows, how_parsed = bt.parse(fname, body)
        hdr = bt.header_map(rows) if rows else {}
        fcur = bt.file_currency(rows) if rows else ("", "")
        pr = bt.price_rows(rows, hdr, fcur) if rows else []
        text = " ".join(" ".join(str(x) for x in cells if x not in (None, ""))
                        for _s, _i, cells in rows)
        if "¥" in text:
            улики[f'{c["origin"]} · {fname}'] = {
                v: len(rx.findall(text)) for v, rx in YEN_CLUES.items()} | {
                "знаков ¥": text.count("¥"), "валюта файла": fcur[0] or "не установлена"}
        files.append({"file_name": fname, "origin": c["origin"], "direction": c["direction"],
                      "prices": pr})
    if отказ:
        print(f"не скачано: {dict(отказ)}")
    print(f"разобрано файлов: {len(files)}")
    for f, u in улики.items():
        print(f"  улики знака ¥ · {f}: {u}")

    арифм, why = tt.extract({"files": files}, yen="")
    keys = {ключ(pn) for _g, pn, _q in LINES}
    по_ключу_ар: dict[str, list] = defaultdict(list)
    for r in арифм:
        if r["key"] in keys:
            по_ключу_ар[r["key"]].append(r)
    по_ключу_кол: dict[str, list] = defaultdict(list)
    for f in files:
        for p in f["prices"]:
            if not p.get("is_price"):
                continue
            pn = p.get("pn") if tt.looks_like_pn(p.get("pn")) else tt.pn_from_row(p.get("raw"))
            k = ключ(pn) if pn else ""
            if k in keys:
                cur = str(p.get("currency") or "")
                if cur.startswith("¥"):
                    cur = "¥"
                по_ключу_кол[k].append({"unit": float(p["price"]), "currency": cur,
                                        "direction": f["direction"], "file": f["file_name"]})

    def выбрать(источник: dict, ступень: str) -> dict:
        out, напр, вал, файлы, кол_ок = {}, Counter(), Counter(), Counter(), Counter()
        for i, (_g, pn, qty) in enumerate(LINES):
            k = ключ(pn)
            offers = [o for o in источник.get(k, []) if o["direction"] == "входящее"]
            напр.update({o["direction"] for o in источник.get(k, [])})
            if not offers:
                continue
            rub = {}
            for вар in ("CNY", "JPY"):
                vals = [в_рубли(float(o["unit"]), str(o["currency"]), rates, вар) for o in offers]
                vals = [v for v in vals if v]
                if vals:
                    rub[вар] = min(vals)
            if rub:
                out[i] = {"rub": rub}
                вал.update({str(o["currency"])[:12] for o in offers})
                файлы.update({o["file"] for o in offers})
                if ступень == "арифметика":
                    кол_ок["в КП количество ≥ нашего" if max(o.get("qty_in_file") or 0
                           for o in offers) >= qty else "в КП количество меньше нашего"] += 1
        print(f"\n  ступень «{ступень}»: строк с ценой {len(out)} из {len(LINES)}; "
              f"направления у найденных номеров {dict(напр)}; валюты {dict(вал)}; "
              f"файлов-источников {len(файлы)}: {sorted(файлы)}")
        if кол_ок:
            print(f"  количество в КП против нашего: {dict(кол_ок)}")
        return out

    print("\nСПОСОБ А — вложения сделки, разбор строк")
    print(f"  отказы арифметики по всем файлам: {dict(why.most_common(6))}")
    a1 = выбрать(по_ключу_ар, "арифметика")
    a2 = выбрать(по_ключу_кол, "колонка")
    # Объединение ступеней: арифметика сильнее, колонка — только где арифметики нет.
    a12 = dict(a2)
    a12.update(a1)
    # Неизвестное направление — отдельно и только счётом.
    неизв = {ключ(pn) for _g, pn, _q in LINES
             if any(o["direction"] == "неизвестно"
                    for o in по_ключу_ар.get(ключ(pn), []) + по_ключу_кол.get(ключ(pn), []))}
    print(f"  номеров объёма с ценой в файлах направления «неизвестно»: {len(неизв)} "
          f"(в закупку не идут)")
    наша = {ключ(pn) for _g, pn, _q in LINES
            if any(o["direction"] == "наша цена"
                   for o in по_ключу_ар.get(ключ(pn), []) + по_ключу_кол.get(ключ(pn), []))}
    print(f"  номеров объёма с ценой в файлах «наша цена»: {len(наша)}")

    # Правдоподобие варианта ¥: цена против середины открытой вилки, медиана.
    band = вилки()
    for вар in ("CNY", "JPY"):
        rel = []
        for i, v in a12.items():
            b = band.get(ключ(LINES[i][1]))
            if b and вар in v["rub"]:
                rel.append(v["rub"][вар] / rates["USD"] / b)
        if rel:
            print(f"  ¥={вар}: цена / середина нашей вилки — медиана {statistics.median(rel):.2f} "
                  f"по {len(rel)} строкам")
    return {"А1 (арифметика)": a1, "А1+А2 (арифметика, иначе колонка)": a12}


# ---------------------------------------------------------------- способ Б: база
def способ_б(rates: dict) -> dict:
    dsn = (os.getenv("SUPABASE_DB_URL") or "").strip()
    if not dsn:
        print("нет SUPABASE_DB_URL — способ Б пропущен")
        return {}
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
    print(f"  потоки {dict(Counter(r[6] for r in rows))}")
    print(f"  валюты {dict(Counter(r[2] or '(нет)' for r in rows))}")
    print(f"  базис {dict(Counter(r[3] or '(нет)' for r in rows))}")
    даты = sorted(r[4] for r in rows if r[4])
    if даты:
        print(f"  даты цен: {даты[0]} … {даты[-1]}; старше 180 дней на сегодня: "
              f"{sum(1 for d in даты if (date.today() - d).days > 180)} из {len(даты)}")
    print(f"  поставщиков (счётом): {len({r[7] for r in rows if r[7]})}")

    def выбрать(only_deal: bool) -> dict:
        out = {}
        for i, (_g, pn, _q) in enumerate(LINES):
            k = ключ(pn).lower()
            offers = [r for r in rows if r[0] == k
                      and (not only_deal or r[5] in карточки or r[5] == str(DEAL))]
            rub = {}
            for вар in ("CNY", "JPY"):
                vals = [в_рубли(r[1], r[2], rates, вар) for r in offers]
                vals = [v for v in vals if v]
                if vals:
                    rub[вар] = min(vals)
            if rub:
                out[i] = {"rub": rub}
        return out
    return {"Б1 (предложения по этой сделке)": выбрать(True),
            "Б2 (все предложения в базе)": выбрать(False)}


def main() -> int:
    print(f"ОБЪЁМ: {len(LINES)} строк, {len({ключ(p) for _g, p, _q in LINES})} номеров; "
          f"сделка {DEAL}. Наших цен в зонде нет; закупка — суммами по группам.")
    for g, name in GROUPS.items():
        print(f"  {g}: {name} — строк {sum(1 for x in LINES if x[0] == g)}")
    rates, ист = курсы_цб(date.today())
    print(f"курс: {ист}; USD {rates.get('USD', 0):.4f} · CNY {rates.get('CNY', 0):.4f} · "
          f"JPY {rates.get('JPY', 0):.5f} · EUR {rates.get('EUR', 0):.4f} ₽")
    итоги = {}
    try:
        итоги.update(способ_а(rates))
    except Exception as e:                                   # noqa: BLE001
        print(f"способ А упал: {type(e).__name__}: {str(e)[:200]}")
    try:
        итоги.update(способ_б(rates))
    except Exception as e:                                   # noqa: BLE001
        print(f"способ Б упал: {type(e).__name__}: {str(e)[:200]}")
    for имя, выбор in итоги.items():
        печать_способа(имя, выбор, rates, ист)
    # Сверка способов: на скольких строках оба нашли цену и совпали ли они.
    names = list(итоги)
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            x, y = итоги[names[a]], итоги[names[b]]
            общие = set(x) & set(y)
            for вар in ("CNY", "JPY"):
                пары = [(x[i]["rub"].get(вар), y[i]["rub"].get(вар)) for i in общие]
                пары = [(p, q) for p, q in пары if p and q]
                if пары:
                    равны = sum(1 for p, q in пары if abs(p - q) <= 0.01 * max(p, q))
                    print(f"сверка {names[a]} ↔ {names[b]} (¥={вар}): общих строк {len(пары)}, "
                          f"совпали в пределах 1 % — {равны}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
