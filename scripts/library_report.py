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
    # СПРОС — ЖИВЫЕ СТРОКИ. В lib_demand лежат и строки с действующей пометкой:
    # текст документа, принятый за позицию, и прежняя редакция файла, заменённая
    # переразбором, — их счёт завышал спрос, а переразобранный файл давал его
    # дважды. До миграции разметки вида ещё нет — тогда таблица, и подпись это
    # говорит. Наличие спрашивается у базы: «одно» глотает ошибку и отдало бы
    # ноль вместо числа.
    живые = bool(q(cur, "select to_regclass('lib_demand_live') is not null", False))
    d = {
        "спрос_живой": живые,
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
        "спрос": q(cur, "select count(*) from "
                   + ("lib_demand_live" if живые else "lib_demand")),
        "файлы": q(cur, "select count(*) from lib_files"),
        "контакты": q(cur, "select count(*) from lib_suppliers where contact_email is not null"),
        "признаки": q(cur, "select count(*) from lib_symptoms"),
        "признак_дефект": q(cur, "select count(*) from lib_symptom_defects"),
        "наш_номер": q(cur, "select count(*) from lib_parts where kv_no is not null"),
        "декларации": q(cur, "select count(*) from lib_customs"),
        "экспортёры": q(cur, "select count(distinct exporter) from lib_customs where exporter is not null"),
        "декл_узел": q(cur, "select count(*) from lib_customs where unit_id is not null"),
        "расход_строк": q(cur, "select count(*) from lib_consumption"),
        "расход_машин": q(cur, "select count(distinct coalesce(model_id, model_raw)) from lib_consumption"),
        "расход_год": q(cur, "select round(sum(usd_year)) from lib_consumption"),
        "кон_позиций": q(cur, "select count(*) from lib_exposure where not have_price"),
        "кон_сумма": q(cur, "select round(sum(usd_exposure)) from lib_exposure where not have_price"),
        "кон_адрес": q(cur, "select count(*) from lib_exposure where not have_price and deal is not null"),
        # Сколько денег стоит за позициями, по которым ВООБЩЕ не известно, кому
        # писать. Это другая работа, чем «достать цену из файла», и смешивать их
        # в одну очередь нельзя.
        "кон_без_исп": q(cur, """select count(*) from lib_exposure e
             where not e.have_price and not exists (select 1 from lib_part_suppliers s
                                                     where s.part_id = e.part_id)"""),
        "кон_без_исп_usd": q(cur, """select round(sum(e.usd_exposure)) from lib_exposure e
             where not e.have_price and not exists (select 1 from lib_part_suppliers s
                                                     where s.part_id = e.part_id)"""),
        "кон_топ10": q(cur, """select round(sum(usd_exposure)) from (
             select usd_exposure from lib_exposure where not have_price
              order by usd_exposure desc nulls last limit 10) t"""),
        "признак_метод": q(cur, "select count(*) from lib_symptom_ops"),
        "дефект_ремонт": q(cur, "select count(*) from lib_defect_ops"),
        "дефект_решение": q(cur, """select count(*) from lib_defects d
             where d.fix is not null or exists
               (select 1 from lib_defect_ops o where o.defect_id = d.id)"""),
        "замены": q(cur, "select count(*) from lib_part_alt"),
        "замены_детали": q(cur, "select count(distinct part_id) from lib_part_alt"),
        "ведомость": q(cur, "select count(*) from lib_bom"),
        "цены_детали": q(cur, "select count(distinct part_id) from lib_prices"),
        "цены_ссылки": q(cur, "select count(*) from lib_prices where source_url is not null"),
        "статьи": q(cur, "select count(*) from lib_knowledge"),
        "статьи_узел": q(cur, "select count(*) from lib_knowledge where unit_id is not null"),
        "без_узла": q(cur, "select count(*) from lib_parts where unit_id is null"),
        "без_машины": q(cur, """select count(*) from lib_parts p
             where not exists (select 1 from lib_part_models m where m.part_id = p.id)"""),
        "без_исполнителя": q(cur, """select count(*) from lib_parts p
             where not exists (select 1 from lib_part_suppliers s where s.part_id = p.id)"""),
        "без_цены": q(cur, """select count(*) from lib_parts p
             where not exists (select 1 from lib_prices pr where pr.part_id = p.id)"""),
        "спрос_опознан": q(cur, "select count(*) from lib_demand_catalog"),
        "сделок_опознано": q(cur, "select count(distinct deal_id) from lib_demand_catalog"),
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
/* Стиль «ведомость на белой бумаге» (.claude/skills/tkp-vedomost): Times New Roman,
   чёрный текст, тонкие чёрные линии, обычное начертание. Прежняя вёрстка отчёта шла
   рубленым шрифтом с серыми плашками и жирными числами — это признаки документа,
   собранного машиной, а владельцу уходит документ, а не выгрузка. */
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body { font-family: "Times New Roman", "Liberation Serif", "FreeSerif", serif;
       font-size: 10.5pt; line-height: 1.25; color: #000; background: #fff; margin: 0; }
p { margin: 0 0 2.2mm; text-align: justify; }
h1 { font-size: 13pt; font-weight: normal; text-align: center; text-transform: uppercase;
     letter-spacing: 0.6px; margin: 0 0 1.5mm; }
/* Заголовок не остаётся один в конце страницы, а короткий раздел не рвётся: иначе на
   последнюю страницу уезжают две строки, и проверка PDF справедливо считает её пустой. */
h2 { font-size: 11pt; font-weight: normal; margin: 4.5mm 0 1.5mm; page-break-after: avoid; }
section { page-break-inside: avoid; }
.sub { text-align: center; margin: 0 0 4mm; font-size: 9.5pt; }
table.t { width: 100%; border-collapse: collapse; table-layout: fixed; margin: 1.5mm 0 3mm;
          font-size: 9.5pt; line-height: 1.22; }
.t thead { display: table-header-group; }
.t tr { page-break-inside: avoid; }
.t th, .t td { border: 0.5pt solid #000; padding: 1.2mm 1.6mm; vertical-align: top;
               text-align: left; font-weight: normal; overflow-wrap: anywhere; }
.t th { text-align: center; }
td.num, td.big { text-align: right; white-space: nowrap;
                 font-variant-numeric: tabular-nums; }
small { font-size: 9pt; }
"""


def html_doc(d: dict) -> str:
    цепочка = [
        ("Модель", n(d["машины"]) + " машин", f"{n(d['наследные'])} с наследным именем "
         "(SGT-400 = Cyclone): в каталогах aftermarket ищут по нему", "было 0"),
        ("Узел", n(d["узлы"]) + " узлов", f"{n(d['системы'])} систем + компоненты, "
         "критичность A/B/C", "было 0"),
        ("Признак", n(d["признаки"]) + " признаков", f"у каждого назван узел, что меряют и "
         f"чем подтвердить: {n(d['признак_метод'])} связей с методом и "
         f"{n(d['признак_дефект'])} с дефектом — заготовка по общей практике, "
         "нужна проверка инженером", "было 0"),
        ("Диагностика", n(d["операции"]) + " операций", "уровни инспекций со сроками, "
         "методы неразрушающего контроля", "было 0"),
        ("Дефект", n(d["дефекты"]) + " записей", f"причина и последствие раздельно; "
         f"у {n(d['дефект_решение'])} есть ремонтное решение, из них {n(d['дефект_ремонт'])} "
         f"ссылкой на операцию, а не текстом", "было 0"),
        ("Запчасть", n(d["детали"]) + " деталей", f"узел определён у {n(d['детали_с_узлом'])}; "
         f"{n(d['ребра_машина'])} связей с машиной; {n(d['замены'])} связей "
         f"взаимозаменяемости на {n(d['замены_детали'])} деталей; {n(d['ведомость'])} строк "
         f"ведомости состава", "было 752"),
        ("Наш номер", n(d["наш_номер"]) + " позиций", "внутренний номер KV проставлен: "
         "сорсер и склад говорят номерами KV, и теперь по ним находится деталь, "
         "машина и поставщик", "было 0"),
        ("Цена", n(d["цены"]) + " цен", f"на {n(d['цены_детали'])} деталей, "
         f"{n(d['цены_ссылки'])} со ссылкой на источник; поток происхождения у каждой",
         "было 0"),
        ("Исполнитель", n(d["исполнители"]) + " компаний", f"{n(d['ребра_исполнитель'])} связей "
         f"с деталью, из них {n(d['наличие'])} с проверкой наличия ({n(d['в_наличии'])} в наличии); "
         f"{n(d['контакты'])} с контактом", "было 0"),
        ("Парк", n(d["парк"]) + " площадок", "какая машина где стоит и чья", "было 0"),
        ("Поставки", n(d["декларации"]) + " деклараций", f"кто фактически вёз такое "
         f"оборудование: {n(d['экспортёры'])} экспортёров, у {n(d['декл_узел'])} поставок "
         f"выведен узел; ценовой ориентир по группе и по узлу — доллар за килограмм",
         "было 0"),
        ("Деньги на кону", n(d["кон_сумма"]) + " $", f"{n(d['кон_позиций'])} позиций "
         f"заявки, по которым у нас НЕТ цены; у {n(d['кон_адрес'])} известен адрес, где "
         f"цена уже лежит — сделка и файл. На десять крупнейших приходится "
         f"{n(d['кон_топ10'])} $. Позиций, по которым не известно и кому писать: "
         f"{n(d['кон_без_исп'])} на {n(d['кон_без_исп_usd'])} $ — это другая работа, "
         f"поиск исполнителя, и она отделена от «достать цену из файла»", "было 0"),
        ("Содержание", n(d["расход_машин"]) + " машин", f"{n(d['расход_строк'])} строк "
         f"расхода с интервалом замены в моточасах и ценой; оценка годового содержания "
         f"{n(d['расход_год'])} $ на эти машины — РАСЧЁТ по типовым интервалам, не наши счета",
         "было 0"),
    ]
    вопросы = [
        ("Что ставится на SGT-400 и кто это делает",
         "машина → узлы → детали → исполнители, один запрос"),
        ("У кого сейчас есть эта позиция и за сколько",
         f"{n(d['наличие'])} проверок наличия с ценой и сроком на ребре «деталь → продавец»"),
        ("Чем этот узел выходит из строя и что делают",
         "признак → дефект → ремонтное решение одним запросом: «металл в масле» → "
         "«износ и проворот вкладыша» → «замена вкладышей подшипников скольжения»"),
        ("Как проверяют узел перед ремонтом",
         "методы контроля отдельными строками — ищется по «вихретоковый», а не по абзацу"),
        ("Кто чинит генератор и где",
         "ремонтные центры с адресом и объёмом работ, связаны с базой компаний"),
        ("Что чинить у этого заказчика",
         "парк: площадка → машина → узлы → детали"),
        ("Кто уже возит такую деталь и почём",
         "таможенные декларации: экспортёр, страна, условия поставки и цена за "
         "килограмм; ориентир по товарной группе и по узлу"),
        ("С чего начинать работу сегодня",
         "очередь по деньгам: позиции без цены по убыванию суммы, с адресом файла, "
         "где цена уже лежит, и с тем, что о детали уже известно"),
        ("Сколько стоит содержать эту машину в год",
         "расход по узлам с интервалом замены — оценка по типовым интервалам ТО, "
         "помеченная расчётом"),
    ]
    строки = "".join(
        f"<tr><td>{E(з)}</td><td class='big'>{E(v)}</td><td>{E(c)}</td>"
        f"<td>{E(w)}</td></tr>" for з, v, c, w in цепочка)
    воп = "".join(f"<tr><td>{E(a)}</td><td>{E(b)}</td></tr>" for a, b in вопросы)
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

<section><h2>Что теперь отвечается одним запросом</h2>
<table class="t"><thead><tr><th style="width:42%">вопрос сорсера</th>
<th style="width:58%">чем закрывается</th></tr></thead><tbody>{воп}</tbody></table>

</section>

<section><h2>Машины с наибольшим числом связанных позиций</h2>
<table class="t"><thead><tr><th style="width:70%">машина</th>
<th style="width:30%">позиций</th></tr></thead><tbody>{маш}</tbody></table></section>

<section><h2>Узлы с наибольшим числом позиций</h2>
<table class="t"><thead><tr><th style="width:56%">узел</th><th style="width:14%">критичность</th>
<th style="width:30%">позиций</th></tr></thead><tbody>{узл}</tbody></table></section>

<section><h2>Где узкие места — это и есть следующая работа</h2>
<table class="t"><thead><tr><th style="width:62%">чего не хватает</th>
<th style="width:38%">позиций</th></tr></thead><tbody>
<tr><td>Деталей без узла</td><td class="num">{n(d['без_узла'])}</td></tr>
<tr><td>Деталей без связи с машиной</td><td class="num">{n(d['без_машины'])}</td></tr>
<tr><td>Деталей без исполнителя</td><td class="num">{n(d['без_исполнителя'])}</td></tr>
<tr><td>Деталей без цены</td><td class="num">{n(d['без_цены'])}</td></tr>
<tr><td>Статей разведки без узла (всего статей {n(d['статьи'])})</td>
    <td class="num">{n(d['статьи'] - d['статьи_узел'])}</td></tr>
</tbody></table></section>

<section><h2>Сырьё</h2>
<table class="t"><thead><tr><th style="width:70%">источник</th>
<th style="width:30%">строк</th></tr></thead><tbody>
<tr><td>Спрос из спецификаций сделок ({'lib_demand_live — без помеченного текста документов и заменённых редакций' if d.get('спрос_живой') else 'lib_demand — все строки, разметка ещё не применена'}){' — по прогону в живой базе' if 'спрос' in PROD else ''}</td><td class="num">{n(d['спрос'])}</td></tr>
<tr><td>Разобранных вложений Битрикса (lib_files){' — по прогону в живой базе' if 'файлы' in PROD else ''}</td><td class="num">{n(d['файлы'])}</td></tr>
<tr><td>Строк спроса, опознанных по каталогу — сведены по артикулу с известной
    деталью, на {n(d['сделок_опознано'])} сделках</td>
    <td class="num">{n(d['спрос_опознан'])}</td></tr>
</tbody></table></section>
</body></html>"""


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "отчёт.html"
    if not DSN:
        print("нет PGDSN / SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    # autocommit обязателен, хотя отчёт только читает: «одно» глотает ошибку
    # запроса и отдаёт ноль, а без autocommit первая же ошибка (например, ещё не
    # применённая миграция со связями) обрывает транзакцию, и ВСЕ следующие
    # запросы тоже отдают ноль. Отчёт показал бы пустую базу вместо своих чисел.
    conn = psycopg2.connect(DSN, connect_timeout=20)
    conn.autocommit = True
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
