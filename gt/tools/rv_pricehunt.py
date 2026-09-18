#!/usr/bin/env python3
"""Задание на добор ЦЕНЫ по строкам, где канал уже известен, а цены нет.

ЗАЧЕМ ОТДЕЛЬНЫЙ ИНСТРУМЕНТ. Перепроверка закрыла опознание: по 489 строкам
известно, что это за изделие и кому писать. Но у 137 из них цены нет, хотя
канал НЕ квотируемый — то есть цена в принципе публикуется, её просто не нашли.
Это 1,9 млн USD, и это самая дорогая незакрытая работа: опознавать заново
ничего не надо, надо найти цифру.

ЧЕМ ОТЛИЧАЕТСЯ ОТ rv_brief.py. Тот выдаёт строки, которых перепроверка ещё не
касалась, и просит разобрать их с нуля. Этот выдаёт УЖЕ РАЗОБРАННЫЕ строки и
отдаёт разведчику всё, что про них известно: изделие, изготовителя, канал,
адреса, что именно помешало прошлому проходу. Задача сужена до одной: цена.

ПОЧЕМУ ЗАДАНИЕ ПЕЧАТАЕТСЯ ИЗ ДАННЫХ. 18.09.2026 вилки в задания вписывались
руками, и одиннадцать из двенадцати оказались неверными (40–69 против
настоящих 14–95 и так далее). Разведчик сверяет найденное с вилкой, поэтому
неверная вилка в задании — это прямой путь к неверному вердикту. Здесь ни одно
число не набирается руками.

    python gt/tools/rv_pricehunt.py --top 12
    python gt/tools/rv_pricehunt.py --top 12 --skip 12
    python gt/tools/rv_pricehunt.py --sheet Энергосети --top 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ship_coverage import quote_only  # noqa: E402

ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
QUESTIONS = ROOT / "gt/data/ship_questions.json"
INSIDE = ROOT / "gt/data/ship_inside_quotes.json"
PRICED = ROOT / "gt/data/ship_inside_priced.json"


def key(pn) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(pn or "").split("(")[0].upper())


def expo(r: dict) -> float:
    lo, hi, q = r.get("usd_lo"), r.get("usd_hi"), r.get("qty")
    if lo in (None, "") or hi in (None, "") or not q:
        return 0.0
    return (float(lo) + float(hi)) / 2 * float(q)


def candidates(ask: list, rv: dict) -> list:
    """Строка годится, если канал назван, цены нет и канал не квотируемый.

    Квотируемый канал исключён намеренно: там цену не публикуют в принципе, и
    посылать туда разведку — значит списать её труд в ноль. Такие строки
    закрываются письмом, а не поиском.
    """
    out = []
    for r in ask:
        x = rv.get(key(r.get("pn")))
        if not x or not str(x.get("channel") or "").strip():
            continue
        if isinstance(x.get("price_low"), (int, float)) or quote_only(x):
            continue
        # Строку, по которой добор цены УЖЕ прошёл и цены не нашёл, второй раз
        # в задание не выдаём: отрицательный результат там измерен и записан
        # (какие страницы закрыты, где номера нет в перечне), и повторять его
        # значит тратить разведку на уже отвеченный вопрос. Пометку ставит
        # gt/tools/rv_merge.py в режиме --update.
        if x.get("price_hunt"):
            continue
        out.append((expo(r), r, x))
    out.sort(key=lambda t: -t[0])
    return out


def asked_keys() -> set:
    """Номера, по которым уже стоит вопрос заказчику.

    Такую строку искать бесполезно: пока заказчик не назвал фасовку, исполнение
    или настоящий номер, продавец вернёт вопрос, а не цену. Самый дорогой
    пример — VS-4-57 на 138 000 USD: под одним обозначением в заявке идут два
    разных изделия, и это обозначение МОДЕЛИ котла, а не номер детали.
    """
    if not QUESTIONS.exists():
        return set()
    out = set()
    for q in json.loads(QUESTIONS.read_text(encoding="utf-8")).get("questions", []):
        for part in re.split(r"[,/·]| и ", str(q.get("pn") or "")):
            k = key(part)
            if len(k) >= 4:
                out.add(k)
    return out


def in_house_keys() -> set:
    """Номера, по которым цена поставщика уже ПОДТВЕРЖДЕНА нашим вложением.

    Посылать по ним разведку в открытый доступ — тратить проход на вопрос, на
    который у нас уже есть ответ. Замер 18.09.2026: из двадцати оставшихся к
    добору строк двенадцать были именно такими, то есть три пятых остатка.

    ИСПРАВЛЕНО 18.09.2026. Вычитался ship_inside_quotes — а он даёт АДРЕС цены
    (файл, где есть хоть одна цена и есть наш номер), а не цену по нашей
    строке: 754 номера против 210 с подтверждённой ценой. Из-за этого 48 строк
    на 78 242 USD не попадали ни в добор цены, ни в письма — по ним не искал
    никто, а отчёт считал их отвеченными. Вычитать можно только подтверждённую
    письменную цену: единица × количество = итог в той же строке
    (ship_inside_priced, пишет gt/tools/tkp_tables.py --keys-out). Тот же
    разбор уже был сделан в gt/tools/rv_brief.py, но здесь его не применили.
    """
    if PRICED.exists():
        return {str(p.get("key") or key(p.get("pn")))
                for p in json.loads(PRICED.read_text(encoding="utf-8")).get("parts", [])}
    if not INSIDE.exists():
        return set()
    return {key(r.get("pn"))
            for r in json.loads(INSIDE.read_text(encoding="utf-8")).get("rows", [])}


def select(sel: list) -> tuple[list, list, list]:
    """Делит отобранное на три части: к добору, ждущие заказчика, уже отвеченные.

    Отсев вынесен сюда из печати, чтобы его можно было проверить: пока он жил
    внутри вывода, тест мог подтвердить только то, что отсеивать есть что, а не
    то, что отсев работает.
    """
    asked, house = asked_keys(), in_house_keys()
    blocked = [t for t in sel if key(t[1].get("pn")) in asked]
    rest = [t for t in sel if key(t[1].get("pn")) not in asked]
    inh = [t for t in rest if key(t[1].get("pn")) in house]
    left = [t for t in rest if key(t[1].get("pn")) not in house]
    return left, blocked, inh


def brief(n: int, e: float, r: dict, x: dict) -> str:
    def f(src: dict, name: str) -> str:
        return str(src.get(name) or "").strip()

    lo, hi = r.get("usd_lo"), r.get("usd_hi")
    band = f"{lo}–{hi} USD за штуку" if lo not in (None, "") else "нашей оценки нет"
    lines = [
        f"{n}. НОМЕР (посимвольно как в заявке): {r.get('pn')}",
        f"   наименование в заявке дословно: {f(r, 'name')}",
        f"   количество: {r.get('qty')} {f(r, 'unit') or 'шт'}",
        f"   наша вилка: {band}   экспозиция по середине: {e:,.0f} USD".replace(",", " "),
        f"   лист: {f(r, 'sheet')}   категория: {f(r, 'category')}",
        "",
        "   ЧТО ПО ЭТОЙ СТРОКЕ УЖЕ УСТАНОВЛЕНО ПЕРЕПРОВЕРКОЙ — не переоткрывай:",
        f"   изделие: {f(x, 'what_it_is')}",
        f"   изготовитель: {f(x, 'real_maker')}",
        f"   номер изготовителя: {f(x, 'real_pn')}",
        f"   состояние позиции: {f(x, 'lifecycle')}",
        f"   канал: {f(x, 'channel')}",
        f"   адреса: {f(x, 'contacts')}",
        f"   что помешало найти цену: {f(x, 'blocker')}",
        f"   вердикт по вилке: {f(x, 'band_verdict')}",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--sheet", default="")
    ap.add_argument("--all", action="store_true",
                    help="не отсеивать строки, по которым уже стоит вопрос заказчику")
    ap.add_argument("--rejudge", action="store_true",
                    help="строки с отрицательным вердиктом, вынесенным ДО введения "
                         "контролей источника: их стоит пересудить по нынешнему стандарту")
    a = ap.parse_args()

    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {key(r["pn"]): r for r in json.loads(RV.read_text(encoding="utf-8"))["rows"]}
    if a.rejudge:
        # Стандарт доказательства рос по ходу работы: контроль выдуманным номером,
        # запрет на выдачу поисковой машины и на круговой источник появились уже
        # после того, как часть строк получила отрицательный вердикт. Последняя
        # волна нашла три таких строки, по которым цена на самом деле открыта, —
        # значит остальные надо пересудить, а не считать закрытыми.
        from verdicts import vkey
        neg = {"НЕЧЕМ ПРОВЕРИТЬ", "НЕ ПОДТВЕРЖДЕНА"}
        house = in_house_keys()
        asked = asked_keys()
        out = []
        for r in ask:
            k = key(r.get("pn"))
            x = rv.get(k)
            if not x or x.get("price_hunt") or vkey(x) not in neg:
                continue
            if k in house or k in asked:
                continue
            out.append((expo(r), r, x))
        out.sort(key=lambda t: -t[0])
        print(f"НА ПЕРЕСУД: {len(out)} строк на {sum(t[0] for t in out):,.0f} USD — "
              f"отрицательный вердикт вынесен до введения контролей источника"
              .replace(",", " "), file=sys.stderr)
        take = out[a.skip:a.skip + a.top]
        print(f"СТРОКИ ЗАЯВКИ ({len(take)}). Всё ниже напечатано из данных инструментом "
              f"gt/tools/rv_pricehunt.py --rejudge и НЕ переписано руками.\n")
        for i, (e, r, x) in enumerate(take, start=a.skip + 1):
            print(brief(i, e, r, x))
            print()
        return

    sel = candidates(ask, rv)
    if a.sheet:
        sel = [t for t in sel if str(t[1].get("sheet") or "") == a.sheet]
    print(f"ВСЕГО строк «канал есть, цены нет, канал не квотируемый»: {len(sel)} "
          f"на {sum(t[0] for t in sel):,.0f} USD".replace(",", " "), file=sys.stderr)
    if not a.all:
        sel, blocked, inh = select(sel)
        print(f"  из них отсеяно как ждущие ответа заказчика: {len(blocked)} на "
              f"{sum(t[0] for t in blocked):,.0f} USD — пока заказчик не ответил, "
              f"продавец вернёт вопрос, а не цену".replace(",", " "), file=sys.stderr)
        print(f"  и отсеяно как уже отвеченные: {len(inh)} на "
              f"{sum(t[0] for t in inh):,.0f} USD — предложение поставщика лежит в нашем "
              f"вложении, искать в открытом доступе нечего".replace(",", " "),
              file=sys.stderr)
        print(f"  остаётся к добору цены: {len(sel)} на "
              f"{sum(t[0] for t in sel):,.0f} USD".replace(",", " "), file=sys.stderr)

    take = sel[a.skip:a.skip + a.top]
    print(f"СТРОКИ ЗАЯВКИ ({len(take)}). Всё ниже напечатано из "
          f"gt/data/ship_lukoil.json и gt/data/ship_reverify.json инструментом "
          f"gt/tools/rv_pricehunt.py и НЕ переписано руками. Если найденное "
          f"противоречит написанному здесь — так и напиши в разборе, не подгоняй.\n")
    # Печатается ВСЕГДА, потому что это ошибка, стоившая шести строк заявки:
    # запись «страница снята, 503 трижды» была верна по факту ответа и неверна по
    # выводу. См. gt/docs/ЗАКУПКА-ЛУКОЙЛ-МЕТОД.md, раздел «Отказ страницы бывает
    # нашим, а не её».
    print("ОТКАЗ СТРАНИЦЫ СНАЧАЛА ПРОВЕРЬ СПОСОБОМ ЗАПРОСА. Замер 18.09.2026: "
          "перечень gas-turbine-parts.com три прохода считался снятым по «503 трижды», "
          "а 503 отдаётся только по HTTPS — по чистому http тот же адрес отдаёт 200 и "
          "437 позиций. Прежде чем записать страницу мёртвой, смени схему (http против "
          "https), инструмент (curl против чтения страницы), поддомен (www против без) "
          "и кодировку; в разборе напиши, что именно проверено.\n")
    for i, (e, r, x) in enumerate(take, start=a.skip + 1):
        print(brief(i, e, r, x))
        print()


if __name__ == "__main__":
    main()
