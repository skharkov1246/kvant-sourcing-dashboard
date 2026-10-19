#!/usr/bin/env python3
"""Файлы «Премий» для пересылки: PDF для чтения и HTML с изменяемым К.

Решение владельца 12–13.10.2026: «Премии» сняты с портала, а файл для пересылки
нужен. Данные — раздел premia из `main.py --data-json` (расчёт premia.py, тот же, что
шёл во вкладку). Скрипт работает внутри прогона premia-send.yml; файлы остаются на
раннере и уходят владельцу в Telegram (scripts/premia_telegram.py). В журнал — только
счётчики: ни имён, ни сумм.

    python scripts/premia_files.py --data modules.json --out DIR [--k 0.60]

В DIR: premia.html (вкладка целиком, открывается без сети), doc.html (исходник PDF:
ведомость, Times New Roman, A4 альбомная — печатается headless Chromium) и ids.txt
(номера сделок для scripts/pdf_check.py: ни одна строка не должна потеряться).
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "templates" / "dashboard_core.html"
FILE_TEMPLATE = ROOT / "templates" / "premia_file.html"
ROLE = {"ex": "исполнитель", "kam": "КАМ", "po": "продукт-оунер"}
STATE = {"r": "в реализации", "p": "в проработке"}


def _json_for_script(obj) -> str:
    # как dashboard._json_for_script: закрывающий тег внутри данных не закроет <script>
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def html_file(premia: dict) -> str:
    """Вкладка «Премии» одним файлом: стили дашборда и код вкладки (templates/premia_file.html)."""
    core = DASHBOARD.read_text(encoding="utf-8")
    style = core[core.index("<style>"):core.index("</style>") + len("</style>")]
    page = FILE_TEMPLATE.read_text(encoding="utf-8")
    for key, val in (("__STYLE__", style), ("__PREMIA_JSON__", _json_for_script(premia))):
        if page.count(key) != 1:
            raise ValueError(f"шаблон файла премий: нет или не одна вставка {key}")
        page = page.replace(key, val)
    return page


def _rub(v: float) -> str:
    """12 345 678 — целые рубли, разряды неразрывным пробелом, минус типографский."""
    s = f"{abs(round(v)):,}".replace(",", " ")
    return ("−" if round(v) < 0 else "") + s


def _num(v: float, digits: int = 2) -> str:
    return f"{v:.{digits}f}".replace(".", ",")


def _pct(v: float) -> str:
    return _num(v * 100, 1) + " %"


def _name(names: dict, uid: str) -> str:
    if not uid:
        return "роль не закреплена"
    n = names.get(uid)
    return (n[0] if n else f"сотрудник #{uid}") + ("" if not n or n[1] else " (уволен)")


def people(premia: dict, k: float) -> list[dict]:
    """Кто сколько получит при К — так же, как таблица вкладки: выбросы в итоги не входят."""
    by: dict[str, dict] = {}
    for d in premia["deals"]:
        if "big" in d["fl"]:
            continue
        for uid, role, share in d["parts"]:
            b = by.setdefault(uid, {"uid": uid, "roles": set(), "n": 0, "real": 0.0, "pre": 0.0})
            b["roles"].add(role)
            b["n"] += 1
            b["real" if d["st"] == "r" else "pre"] += d["p1"] * k * share
    return sorted(by.values(), key=lambda b: -(b["real"] + b["pre"]))


def doc_html(premia: dict, k: float) -> str:
    """Исходник PDF: ведомость на белой бумаге (навык tkp-vedomost, docs/ПРАВИЛА-PDF.md)."""
    P, N, T = premia["params"], premia.get("names") or {}, premia["totals"]
    E = html.escape
    win = (P.get("winRate") or 0) / 100 if P.get("winRate") is not None else None
    rows_people, sum_real, sum_pre = [], 0.0, 0.0
    for i, b in enumerate(people(premia, k), 1):
        sum_real += b["real"]
        sum_pre += b["pre"]
        roles = ", ".join(ROLE[r] for r in ("kam", "po", "ex") if r in b["roles"])
        rows_people.append(
            f"<tr><td class='c'>{i}</td><td>{E(_name(N, b['uid']))}</td><td>{roles}</td>"
            f"<td class='r'>{b['n']}</td><td class='r'>{_rub(b['real'])}</td>"
            f"<td class='r'>{_rub(b['pre'])}</td>"
            f"<td class='r'>{_rub(b['pre'] * win) if win is not None else '—'}</td></tr>")
    rows_people.append(
        f"<tr class='tot'><td></td><td>Итого</td><td></td><td></td><td class='r'>{_rub(sum_real)}</td>"
        f"<td class='r'>{_rub(sum_pre)}</td><td class='r'>{_rub(sum_pre * win) if win is not None else '—'}</td></tr>")

    src = P.get("srcLabel") or {}
    rows_deals = []
    for d in sorted(premia["deals"], key=lambda d: -d["p1"]):
        notes = [src.get(d["src"], d["src"])]
        if "big" in d["fl"]:
            notes.append("сумму проверить, в итоги не входит")
        if "low" in d["fl"]:
            notes.append(f"ниже ×{_num(P['priceMin'])}")
        parts = "; ".join(f"{ROLE[role]}: {E(_name(N, uid))} — {_rub(d['p1'] * k * share)}"
                          for uid, role, share in d["parts"])
        rows_deals.append(
            f"<tr><td class='c'>{E(d['id'])}</td><td>{E(d['t'])}<div class='m'>{E(d['cat'])} · "
            f"{E(d['stage'])} · {STATE.get(d['st'], '')}</div></td>"
            f"<td class='r'>{_rub(d['rev'])}</td><td class='r'>{_rub(d['pz'])}<div class='m'>"
            f"{E('; '.join(notes))}</div></td><td class='r'>{_num(d['coef'])}</td>"
            f"<td class='r'>{_rub(d['res'])}</td><td class='r'>{_rub(d['p1'] * k)}</td><td>{parts}</td></tr>")

    method = (
        f"Результат сделки равен выручке за вычетом ПЗ, финансирования {_pct(P['fin'])} выручки, "
        f"платёжного агента {_pct(P['pa'])} оплаты поставщику и поправки {_pct(P['plug'])} выручки "
        f"на агентское и затраты, которых нет ни в Битриксе, ни в книгах бюджетов; поправка "
        f"калибруется по {P['calibN']} сделкам в реализации с известной закупкой до уровня "
        f"{_pct(P['rhoTarget'])} выручки. ПЗ берётся каскадом: заказы поставщикам × "
        f"{_num(1 + P['pzOver'])}, прямые затраты книги бюджета, экономика проекта, оценка по "
        f"медианному коэффициенту ×{_num(P['coefMed'])}. Пул равен {round(P['rate'] * 100)} % "
        f"результата × К: исполнителю {round(P['ex'] * 100)} %, ЛПР {round(P['lpr'] * 100)} %; "
        f"ниже ×{_num(P['priceMin'])} к ПЗ пул равен нулю; по убыточной сделке часть ЛПР "
        f"отрицательная, исполнителю ноль. Исполнитель — «Ответственный» сделки, КАМ — поле «КАМ», "
        f"продукт-оунер при КАМе — эксперт (20 % части ЛПР), без КАМа — завёл сделку (80 %); "
        f"новизна и распределение решений между ЗКД, КД и старшим КАМом не учитываются. "
        f"«В реализации» начисляется пропорционально оплате заказчика, «в проработке» — если сделка "
        f"будет выиграна по нынешней сумме"
        + (f", «ожидаемо» — с учётом доли выигрыша {round(win * 100)} %" if win is not None else "")
        + ". Сделки-выбросы (сумму надо проверить) в итоги не входят. Это прогноз до калькулятора "
          "маржи финансового директора, а не начисление.")

    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><title>Прогноз премий по сделкам</title>
<style>
@page {{ size: A4 landscape; margin: 12mm 12mm 12mm 14mm; }}
body {{ font-family: "Times New Roman", "Liberation Serif", "FreeSerif", serif; font-size: 10.5pt;
       line-height: 1.28; color: #000; background: #fff; margin: 0; }}
h1 {{ font-size: 13pt; font-weight: normal; text-align: center; text-transform: uppercase; margin: 0 0 6pt; }}
h2 {{ font-size: 11pt; font-weight: normal; margin: 12pt 0 5pt; }}
p {{ text-align: justify; margin: 0 0 6pt; }}
.sub {{ text-align: center; margin-bottom: 10pt; }}
table.t {{ width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 9pt; }}
.t thead {{ display: table-header-group; }}
.t tr {{ page-break-inside: avoid; }}
.t th, .t td {{ border: 0.5pt solid #000; padding: 2pt 3pt; vertical-align: top;
               word-wrap: break-word; overflow-wrap: anywhere; }}
.t th {{ font-weight: normal; text-align: center; }}
.r {{ text-align: right; white-space: nowrap; }}
.c {{ text-align: center; }}
.m {{ font-size: 8pt; white-space: normal; }}   /* пометка под числом переносится, а не лезет в соседнюю ячейку */
</style></head><body>
<h1>Прогноз премий по открытым сделкам</h1>
<div class="sub">Положение о мотивации команды КАМа, ред. 2 · состояние {E(premia.get('label', ''))} ·
коэффициент компании К = {_num(k)} · курс 1 € = {_num(P['eurRub'])} руб.</div>
<h2>1. Как считается</h2>
<p>{method}</p>
<h2>2. Кто сколько получит при К = {_num(k)}, руб. до НДФЛ</h2>
<table class="t"><thead><tr>
<th style="width:5%">№</th><th style="width:27%">Сотрудник</th><th style="width:20%">Роли</th>
<th style="width:8%">Сделок</th><th style="width:13%">В реализации</th><th style="width:14%">В проработке</th>
<th style="width:13%">Ожидаемо</th></tr></thead><tbody>
{''.join(rows_people)}
</tbody></table>
<h2>3. Сделки, {T['deals']} (в реализации {T['real']}, в проработке {T['presale']}), суммы в руб.</h2>
<table class="t"><thead><tr>
<th style="width:7%">№ сделки</th><th style="width:24%">Сделка</th><th style="width:9%">Выручка</th>
<th style="width:13%">ПЗ, источник</th><th style="width:5%">×ПЗ</th><th style="width:9%">Результат</th>
<th style="width:8%">Пул при К</th><th style="width:25%">Кому</th></tr></thead><tbody>
{''.join(rows_deals)}
</tbody></table>
</body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Файлы «Премий» для пересылки")
    ap.add_argument("--data", required=True, help="JSON из main.py --data-json")
    ap.add_argument("--out", required=True, help="каталог для файлов")
    ap.add_argument("--k", default="0.60", help="К компании для PDF")
    a = ap.parse_args(argv)
    premia = (json.loads(Path(a.data).read_text(encoding="utf-8")) or {}).get("premia")
    if not premia or not premia.get("deals"):
        print("премий в данных нет — файлы не собраны")
        return 1
    k = float(str(a.k).replace(",", "."))
    P = premia["params"]
    if not P["kMin"] <= k <= P["kMax"]:
        print(f"К вне границ положения {P['kMin']}–{P['kMax']}")
        return 1
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "premia.html").write_text(html_file(premia), encoding="utf-8")
    (out / "doc.html").write_text(doc_html(premia, k), encoding="utf-8")
    (out / "ids.txt").write_text("\n".join(d["id"] for d in premia["deals"]) + "\n", encoding="utf-8")
    print(f"файлы собраны: сделок {len(premia['deals'])}, сотрудников {len(people(premia, k))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
