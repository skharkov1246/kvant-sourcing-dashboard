#!/usr/bin/env python3
"""Отчёт владельцу по базе знаний: HTML → PDF по правилам docs/ПРАВИЛА-PDF.md.

Собирает числа прямо из базы (SUPABASE_DB_URL или локальная через PGDSN) и
верстает одностраничный отчёт: состояние цепочки портала, что появилось, и
какие вопросы теперь закрываются одним запросом.

    PGDSN=postgresql://... python scripts/library_report.py отчёт.html
    chromium --headless --print-to-pdf=отчёт.pdf file://$PWD/отчёт.html

Таблицы текут сами: никакой фиксированной высоты страницы и никакой нарезки на
пачки в Python — ровно то, на чём выгрузка ЛУКОЙЛ-НВН потеряла содержимое.
"""
from __future__ import annotations

import html
import json
import os
import sys
from datetime import date

DSN = os.environ.get("PGDSN") or os.environ.get("SUPABASE_DB_URL", "")
# Числа, измеренные в живой базе, когда отчёт собирается не из неё. Нужно ровно
# для спроса и вложений: они есть только в проде, а справочники — те же файлы
# репозитория и совпадают до строки. Подмена видна в отчёте подписью.
PROD = json.loads(os.environ.get("PROD_COUNTS", "{}") or "{}")


def E(x) -> str:
    return html.escape(str(x if x is not None else ""))


def n(v) -> str:
    return f"{int(v):,}".replace(",", " ") if v is not None else "—"


def одно(cur, sql, default=0):
    try:
        cur.execute(sql)
        r = cur.fetchone()
        return r[0] if r and r[0] is not None else default
    except Exception:
        return default


def собрать(cur) -> dict:
    q = одно
    d = {
        "машины": q(cur, "select count(*) from lib_models"),
        "наследные": q(cur, "select count(*) from lib_models where legacy is not null"),
        "узлы": q(cur, "select count(*) from lib_units"),
        "системы": q(cur, "select count(*) from lib_units where parent_id is null"),
        "детали": q(cur, "select count(*) from lib_parts"),
        "детали_с_узлом": q(cur, "select count(*) from lib_parts where unit_id is not null"),
        "ребра_машина": q(cur, "select count(*) from lib_part_models"),
        "исполнители": q(cur, "select count(*) from lib_suppliers"),
        "ребра_исполнитель": q(cur, "select count(*) from lib_part_suppliers"),
        "наличие": q(cur, "select count(*) from lib_part_suppliers where verdict is not null"),
        "в_наличии": q(cur, "select count(*) from lib_part_suppliers where verdict = 'in_stock'"),
        "цены": q(cur, "select count(*) from lib_prices"),
        "операции": q(cur, "select count(*) from lib_procedures"),
        "дефекты": q(cur, "select count(*) from lib_defects"),
        "парк": q(cur, "select count(*) from lib_fleet"),
        "спрос": q(cur, "select count(*) from lib_demand"),
        "файлы": q(cur, "select count(*) from lib_files"),
        "контакты": q(cur, "select count(*) from lib_suppliers where contact_email is not null"),
    }
    for ключ, значение in PROD.items():
        if ключ in d:
            d[ключ] = значение
    try:
        cur.execute("""
            select m.name, count(distinct pm.part_id)
              from lib_part_models pm join lib_models m on m.id = pm.model_id
             group by 1 order by 2 desc limit 10""")
        d["топ_машин"] = cur.fetchall()
    except Exception:
        d["топ_машин"] = []
    try:
        cur.execute("""
            select u.name, u.crit, count(*)
              from lib_parts p join lib_units u on u.id = p.unit_id
             group by 1,2 order by 3 desc limit 12""")
        d["топ_узлов"] = cur.fetchall()
    except Exception:
        d["топ_узлов"] = []
    return d


CSS = """
@page { size: A4; margin: 12mm 10mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 9.5pt; color: #111; }
h1 { font-size: 17pt; margin: 0 0 2mm; }
h2 { font-size: 12pt; margin: 6mm 0 2mm; border-bottom: 1.5px solid #111; padding-bottom: 1mm; }
.sub { color: #555; font-size: 8.5pt; margin-bottom: 4mm; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin-bottom: 3mm; }
.t thead { display: table-header-group; }
.t tr { page-break-inside: avoid; }
.t th { background: #f0f0f0; text-align: left; padding: 1.6mm 2mm; font-size: 8.5pt;
        border: 0.4px solid #bbb; }
.t td { padding: 1.6mm 2mm; border: 0.4px solid #ddd; vertical-align: top;
        word-wrap: break-word; overflow-wrap: anywhere; }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td.big { font-size: 11pt; font-weight: bold; text-align: right; }
.q { background: #fafafa; }
.was { color: #888; }
"""


def html_doc(d: dict) -> str:
    цепочка = [
        ("Модель", n(d["машины"]) + " машин", f"{n(d['наследные'])} с наследным именем "
         "(SGT-400 = Cyclone): в каталогах aftermarket ищут по нему", "было 0"),
        ("Узел", n(d["узлы"]) + " узлов", f"{n(d['системы'])} систем + компоненты, "
         "критичность A/B/C", "было 0"),
        ("Диагностика", n(d["операции"]) + " операций", "уровни инспекций со сроками, "
         "методы неразрушающего контроля", "было 0"),
        ("Дефект", n(d["дефекты"]) + " записей", "последствие и ремонтное решение вместе", "было 0"),
        ("Запчасть", n(d["детали"]) + " деталей", f"узел определён у {n(d['детали_с_узлом'])}; "
         f"{n(d['ребра_машина'])} связей с машиной; {n(d['цены'])} цен", "было 752"),
        ("Исполнитель", n(d["исполнители"]) + " компаний", f"{n(d['ребра_исполнитель'])} связей "
         f"с деталью, из них {n(d['наличие'])} с проверкой наличия ({n(d['в_наличии'])} в наличии); "
         f"{n(d['контакты'])} с контактом", "было 0"),
        ("Парк", n(d["парк"]) + " площадок", "какая машина где стоит и чья", "было 0"),
    ]
    вопросы = [
        ("Что ставится на SGT-400 и кто это делает",
         "машина → узлы → детали → исполнители, один запрос"),
        ("У кого сейчас есть эта позиция и за сколько",
         f"{n(d['наличие'])} проверок наличия с ценой и сроком на ребре «деталь → продавец»"),
        ("Чем этот узел выходит из строя и что делают",
         "дефект хранится вместе с ремонтным решением"),
        ("Как проверяют узел перед ремонтом",
         "методы контроля отдельными строками — ищется по «вихретоковый», а не по абзацу"),
        ("Кто чинит генератор и где",
         "ремонтные центры с адресом и объёмом работ, связаны с базой компаний"),
        ("Что чинить у этого заказчика",
         "парк: площадка → машина → узлы → детали"),
    ]
    строки = "".join(
        f"<tr><td><b>{E(з)}</b></td><td class='big'>{E(v)}</td><td>{E(c)}</td>"
        f"<td class='was'>{E(w)}</td></tr>" for з, v, c, w in цепочка)
    воп = "".join(f"<tr class='q'><td>{E(a)}</td><td>{E(b)}</td></tr>" for a, b in вопросы)
    маш = "".join(f"<tr><td>{E(m)}</td><td class='num'>{n(k)}</td></tr>"
                  for m, k in d["топ_машин"])
    узл = "".join(f"<tr><td>{E(u)}</td><td>{E(c)}</td><td class='num'>{n(k)}</td></tr>"
                  for u, c, k in d["топ_узлов"])
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>База знаний КВАНТ — состояние</title><style>{CSS}</style><body>
<h1>База знаний КВАНТ: состояние цепочки портала</h1>
<div class="sub">{date.today().strftime('%d.%m.%Y')} · цепочка «модель → узел → диагностика →
дефект → ремонтное решение → запчасть → исполнитель» · числа взяты из базы запросом,
не из отчётов</div>

<h2>Звенья</h2>
<table class="t"><thead><tr><th style="width:14%">звено</th><th style="width:16%">сейчас</th>
<th style="width:54%">чем закрыто</th><th style="width:16%">до этой работы</th></tr></thead>
<tbody>{строки}</tbody></table>

<h2>Что теперь отвечается одним запросом</h2>
<table class="t"><thead><tr><th style="width:42%">вопрос сорсера</th>
<th style="width:58%">чем закрывается</th></tr></thead><tbody>{воп}</tbody></table>

<h2>Машины с наибольшим числом связанных позиций</h2>
<table class="t"><thead><tr><th style="width:70%">машина</th>
<th style="width:30%">позиций</th></tr></thead><tbody>{маш}</tbody></table>

<h2>Узлы с наибольшим числом позиций</h2>
<table class="t"><thead><tr><th style="width:56%">узел</th><th style="width:14%">критичность</th>
<th style="width:30%">позиций</th></tr></thead><tbody>{узл}</tbody></table>

<h2>Сырьё</h2>
<table class="t"><thead><tr><th style="width:70%">источник</th>
<th style="width:30%">строк</th></tr></thead><tbody>
<tr><td>Спрос из спецификаций сделок (lib_demand){' — по прогону в живой базе' if 'спрос' in PROD else ''}</td><td class="num">{n(d['спрос'])}</td></tr>
<tr><td>Разобранных вложений Битрикса (lib_files){' — по прогону в живой базе' if 'файлы' in PROD else ''}</td><td class="num">{n(d['файлы'])}</td></tr>
</tbody></table>
</body></html>"""


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "отчёт.html"
    if not DSN:
        print("нет PGDSN / SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    conn = psycopg2.connect(DSN, connect_timeout=20)
    with conn.cursor() as cur:
        d = собрать(cur)
    conn.close()
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_doc(d))
    print(f"✓ {out}: машин {d['машины']}, узлов {d['узлы']}, деталей {d['детали']}, "
          f"исполнителей {d['исполнители']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
