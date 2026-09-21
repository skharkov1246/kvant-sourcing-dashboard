#!/usr/bin/env python3
"""Положительный и отрицательный контроль продавца: отвечает ли он ЧЕСТНО.

ЗАЧЕМ. «Номер у продавца не найден» и «продавец пишет Out of Stock» — это
сведения, только если по ВЫДУМАННОМУ номеру он отвечает иначе. 18.09.2026
выяснилось, что один из продавцов, на которого ссылаются 27 строк нашей
перепроверки, отдаёт HTTP 200 и полноценную страницу на ЛЮБУЮ строку в адресе:
на выдуманный «zzz-9999-notreal» пришло 47 КБ с заголовком из этого самого
номера и надписью «Out of Stock». Значит его «Out of Stock» — ответ по
умолчанию и не значит ничего, а его «In Stock» — значит, потому что по
умолчанию он так не отвечает.

ЧТО ЭТОТ ЗАМЕР ДАЁТ. По каждому продавцу: что он отвечает на выдуманный номер
(код, размер страницы, есть ли цена, что написано про наличие). Отсюда прямо
читается, какие его утверждения можно принимать.

ЧЕГО НЕ ДАЁТ. Он не говорит, честна ли ЦЕНА: цена на странице выдуманного
номера обычно не появляется, и её отсутствие в контроле не делает найденную
цену подтверждённой. Контроль отвечает ровно на один вопрос — отличает ли
продавец существующий номер от несуществующего.

    python gt/tools/source_control.py [--write]
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "gt/data/ship_source_control.json"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0 Safari/537.36")
# Выдуманный номер: буквы и цифры в формате, похожем на артикул, но заведомо
# несуществующий. Одинаковый для всех продавцов — чтобы ответы сравнивались.
FAKE = "zzz-9999-notreal"
# Для продавцов, опознающих изделие числовым кодом, подставляется этот код.
FAKE_NUM = "99999999"

# Адреса в том виде, в каком их строит сам продавец, и при каждом — НАСТОЯЩИЙ
# номер, который у этого продавца точно есть. Один контроль без второго
# бессмыслен: страница поиска отдаёт 200 и на существующий номер, и на
# выдуманный, и судить по одному коду ответа нельзя — так 18.09.2026 контроль
# едва не обвинил честного продавца. Список закрытый: контроль ОБВИНЯЕТ
# источник, поэтому строится по разобранным случаям.
SELLERS = {
    "santaclarasystems.com": ("https://www.santaclarasystems.com/part/{pn}", "1794-IB16"),
    "classicautomation.com": ("https://www.classicautomation.com/Part/{pn}", "127819"),
    # Контрольный номер обязан быть тем, который у ЭТОГО продавца есть. 18.09.2026
    # здесь стоял 1794-IB16, которого у него нет в каталоге: положительный
    # контроль отдал 404, контроль не измерил ничего — а в замере стояло
    # «прошёл». Шаблон адреса при этом был верен, неверен был номер.
    "us.automationparts.com": ("https://us.automationparts.com/products/{pn}",
                               "3axd50000260324"),
    # Проверяется КАРТОЧКА, а не поиск. Страница поиска отдаёт 200 и на
    # существующий номер, и на выдуманный, потому что список результатов у неё
    # рисуется в браузере, — и контроль по ней обвиняет честного продавца в том,
    # чего он не делал. Наши записи ссылаются именно на карточки, значит и
    # контролировать надо их. Проверено 18.09.2026.
    # У этого продавца изделие опознаётся НОМЕРОМ SKU, а часть адреса с
    # артикулом — украшение: подставь туда выдуманный артикул при настоящем SKU,
    # и придёт та же карточка (проверено 18.09.2026: адрес с «zzz-9999-notreal»
    # и SKU 74093 отдаёт перенаправление на настоящую карточку). Поэтому
    # контроль подставляет выдуманный SKU: на него приходит HTTP 404, то есть
    # существующее от несуществующего продавец отличает. Практический вывод для
    # наших записей: ссылка на этого продавца есть доказательство только вместе
    # с номером SKU.
    "kempstoncontrols.co.uk":
        ("https://www.kempstoncontrols.co.uk/6ES7322-1BL00-0AA0/Siemens/sku/{pn}", "74093"),
    "automation-warehouse.com": ("https://automation-warehouse.com/products/{pn}",
                                 "2cds251001r0427"),
}

MONEY = re.compile(r"(?:US)?\$\s?\d[\d,]*(?:\.\d{2})?|\d[\d ]*(?:,\d{2})?\s?(?:EUR|GBP|USD)")
STOCK = re.compile(r"out of stock|in stock|ready to ship|request a quote|no results|"
                   r"not found|нет в наличии", re.I)


def fetch(url: str) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["curl", "-sS", "-L", "-m", "45", "-A", UA, "-w", "\n__HTTP__%{http_code}", url],
            capture_output=True, text=True, timeout=60)
    except Exception:                                            # noqa: BLE001
        return 0, ""
    body = p.stdout
    code = 0
    if "__HTTP__" in body:
        body, _, tail = body.rpartition("\n__HTTP__")
        code = int(tail.strip() or 0)
    return code, body


def text_of(body: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", t)).split())


def one(tpl: str, pn: str) -> dict:
    code, body = fetch(tpl.format(pn=pn))
    txt = text_of(body)
    return {
        "pn": pn,
        "http": code,
        "bytes": len(body),
        "echoes_number": pn.lower() in txt.lower(),
        "says_about_stock": sorted({m.group(0).lower() for m in STOCK.finditer(txt)}),
        "money_on_page": MONEY.findall(txt)[:3],
    }


def probe(host: str, tpl: str, real_pn: str) -> dict:
    fake = one(tpl, FAKE_NUM if str(real_pn).isdigit() else FAKE)
    real = one(tpl, real_pn)
    # Источник проходит контроль, если на выдуманный номер он отвечает ИНАЧЕ,
    # чем на настоящий: другим кодом, заметно другим размером страницы, либо
    # прямым «ничего не найдено». Одинаковый ответ значит, что различать
    # существующее и несуществующее этот источник не умеет.
    said_none = any(s in ("no results", "not found") for s in fake["says_about_stock"])
    size_differs = (real["bytes"] > 0
                    and abs(fake["bytes"] - real["bytes"]) > 0.2 * real["bytes"])
    # Ответ «доступ закрыт» контролем не является: разница между 429 и 200 — это
    # разница в том, пустил ли нас сайт, а не в том, различает ли он номера.
    blocked = {0, 403, 429, 503}
    # Контроль, у которого НЕ ПРОШЛА положительная сторона, не измерил ничего:
    # если настоящий номер отдаёт «не найдено», значит неверен либо шаблон
    # адреса, либо сам контрольный номер, и о честности продавца это не говорит
    # ни слова. Записывать такому «прошёл» — выдавать отсутствие измерения за
    # измерение; именно это здесь и случилось 18.09.2026 с одним продавцом, на
    # отрицательные ответы которого опираются десятки строк перепроверки.
    # Судим ТОЛЬКО по коду ответа. Первая версия смотрела и на текст страницы —
    # и тут же дала ложное срабатывание: слова «not found» нашлись в навигации
    # честной карточки, вернувшей 200. Текст страницы для этого слишком шумный,
    # а ложное «контроль не состоялся» обесценивает верное измерение.
    real_missing = real["http"] in (404, 410)
    if real_missing and fake["http"] not in blocked:
        return {
            "host": host,
            "control_fake": fake,
            "control_real": real,
            "passes": None,
            "what_it_means": ("Контроль не состоялся: на НАСТОЯЩИЙ номер продавец тоже "
                              "ответил «не найдено». Значит неверен либо шаблон адреса, "
                              "либо сам контрольный номер — этого изделия у продавца "
                              "нет. О том, различает ли он существующее и выдуманное, "
                              "такой прогон не говорит ничего."),
        }
    if fake["http"] in blocked or real["http"] in blocked:
        return {
            "host": host,
            "control_fake": fake,
            "control_real": real,
            "passes": None,
            "what_it_means": ("Контроль не состоялся: сайт закрыл доступ хотя бы на одном "
                              "из двух обращений (403, 429 или обрыв). Разница ответов "
                              "здесь говорит о защите от обращений, а не о том, различает "
                              "ли продавец существующий номер и выдуманный."),
        }
    passes = bool(fake["http"] != real["http"] or said_none or size_differs)
    return {
        "host": host,
        "control_fake": fake,
        "control_real": real,
        "passes": passes,
        "what_it_means": (
            "На выдуманный номер отвечает не так, как на настоящий, — значит его "
            "«не найдено» и «нет в наличии» можно принимать как сведение."
            if passes else
            "На ВЫДУМАННЫЙ номер отвечает так же, как на настоящий. Значит его "
            "отрицательные утверждения («Out of Stock», «карточки нет») — ответ по "
            "умолчанию и сведением НЕ являются. Положительные («In Stock») сведением "
            "являются: по умолчанию он так не отвечает."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    rows = [probe(h, t, pn) for h, (t, pn) in SELLERS.items()]
    for r in rows:
        mark = {True: "прошёл", False: "НЕ ПРОШЁЛ", None: "не состоялся"}[r["passes"]]
        f_, t_ = r["control_fake"], r["control_real"]
        print(f"{mark:10} | выдуманный: HTTP {f_['http']:>3}, {f_['bytes']:>7} б | "
              f"настоящий ({t_['pn']}): HTTP {t_['http']:>3}, {t_['bytes']:>7} б | "
              f"{r['host']}")
        if f_["says_about_stock"]:
            print(f"             на выдуманный пишет: {', '.join(f_['says_about_stock'])}")
    bad = [r["host"] for r in rows if r["passes"] is False]
    skip = [r["host"] for r in rows if r["passes"] is None]
    print(f"\nне прошли контроль: {len(bad)} из {len(rows)}"
          + (f" — {', '.join(bad)}" if bad else ""))
    if skip:
        print("контроль не состоялся у: " + ", ".join(skip)
              + " — либо сайт закрыл доступ, либо контрольный номер неверен; "
                "причина по каждому названа в замере")
    if a.write:
        OUT.write_text(json.dumps({
            "updated": "2026-09-18",
            "source": "Контроль выдуманным номером. Считает gt/tools/source_control.py.",
            "method": (f"По каждому продавцу открывается карточка номера «{FAKE}», которого не "
                       f"существует. Если продавец отдаёт на него полноценную страницу, его "
                       f"отрицательные утверждения сведением не являются."),
            "fake_number": FAKE,
            "fake_number_numeric": FAKE_NUM,
            "sellers": rows,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
