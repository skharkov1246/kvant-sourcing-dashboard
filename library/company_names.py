#!/usr/bin/env python3
"""Имена компаний реестра из карточек Bitrix24. Вхолостую по умолчанию.

ЗАЧЕМ. display_name реестра — это norm_name от названия: «supremevalves»,
«ethosenergybloomfieldwgpwindustrialturbineservices». Для сведения это верный
ключ, для человека — нечитаемая строка. Владелец 24.09.2026: «наименование как
веб-сайт компании не работает». Настоящее название есть в портале: TITLE
карточки компании и наименование в её реквизитах. Реквизиты заодно несут ИНН —
тот самый, которого ждёт папка «Ждут ИНН» на /suppliers.

ЧТО ЧИТАЕТ. Только компании, у которых в реестре есть признак bitrix (номер
карточки портала, sup_identifier.value — цифры, как их пишет сведение из
«bitrix:12345»). Плюс карточки очереди entity_uncertain последнего прогона
сведения — чтобы сосчитать, у скольких ИНН уже заполнен.

    crm.company.list   filter {"@ID": ≤50 номеров}            select ID, TITLE
    crm.requisite.list filter {ENTITY_TYPE_ID: 4, "@ENTITY_ID": ≤50, ">ID": …}

Пачки по ключу, без смещения: смещение считает total на каждой странице и
съедает время метода (CLAUDE.md, «Битрикс не перегружать», пункт 4). Реквизитов
у компании бывает больше одного, поэтому пачка реквизитов дочитывается по
«>ID» с start=-1, пока страница полная.

ОДНА ПЯТАЯ ПОРТАЛА. Распоряжение владельца 24.09.2026: такие фоновые чтения
берут не больше пятой части ёмкости портала. Сам модуль бюджета не держит —
пауза считается в bitrix_client.интервал_портала() из BITRIX_RPS и
BITRIX_PARALLEL; прогон (.github/workflows/suppliers-names.yml) ставит 0,3.
Своих пауз и повторов здесь нет.

ЗАПИСЬ. Вхолостую по умолчанию. APPLY=1 — строки в sup_display_name с ключом
прогона; действующим имя становится только там, где оно изменилось. Гейты
отменяют запись целиком. Откат — ROLLBACK_RUN_ID: строки прогона помечаются
rolled_back_at, и действующим снова становится прежнее имя (правило 5 —
пометка, а не удаление).

В ЖУРНАЛ — ТОЛЬКО АГРЕГАТЫ (правило 17): ни названия, ни ИНН, ни номера карточки.

    python library/company_names.py                       # вхолостую
    APPLY=1 SHARDS=10 SHARD=0 RUN_ID=names-1 python library/company_names.py
    ROLLBACK_RUN_ID=names-1 python library/company_names.py
"""
from __future__ import annotations

import collections
import os
import re
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ПАЧКА = 50
ТИП_КОМПАНИЯ = 4
ПОЛЯ_РЕКВИЗИТОВ = ("ID", "ENTITY_ID", "RQ_INN", "RQ_KPP", "RQ_OGRN",
                   "RQ_COMPANY_NAME", "RQ_COMPANY_FULL_NAME")

# «ПОХОЖЕ НА КЛЮЧ» — зеркало SQL-функции sup_имя_как_ключ в
# library/supabase/suppliers_schema.sql. Совпадение двух сторон сверяется на
# одном корпусе в tests/test_company_names_sql.py, а не глазами.
_КЛЮЧ = re.compile(r"[a-z_]+:\S+|[a-zа-яё0-9]+")


def как_ключ(s: str | None) -> bool:
    """Ключ портала «bitrix:2002», выход norm_name «supremevalves» или пусто."""
    if s is None or not str(s).strip():
        return True
    return bool(_КЛЮЧ.fullmatch(str(s)))


def инн_верен(s) -> bool:
    """Российский ИНН с верной контрольной суммой: 10 цифр юрлица, 12 — ИП.

    Живёт здесь, рядом с реквизитами, а не в ревизии портала, где появился:
    её же зовёт связь реестров (library/supplier_link.py), и второе написание
    контрольной суммы разошлось бы с первым.
    """
    s = re.sub(r"\s", "", str(s or ""))      # «77 0000 0000» — тот же ИНН, что и без пробелов
    if not re.fullmatch(r"\d{10}|\d{12}", s):
        return False
    d = [int(c) for c in s]

    def контроль(веса):
        return sum(w * x for w, x in zip(веса, d)) % 11 % 10

    if len(d) == 10:
        return контроль((2, 4, 10, 3, 5, 9, 4, 6, 8)) == d[9]
    return (контроль((7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == d[10]
            and контроль((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == d[11])


# ── Выбор имени на чтении: один на все страницы ─────────────────────────────
# Сам выбор живёт в виде sup_name_shown (suppliers_schema.sql, блок 8а). Здесь —
# только то, что нужно читателям, чтобы не упасть, пока схему с видом не
# применили к рабочей базе: вместо вида подставляется пустая выборка той же
# формы, и запрос работает по-старому (display_name). Правка, меняющая основание,
# не должна ронять то, что на нём стоит (CLAUDE.md, стиль работы).
ВИД_ИМЁН = "sup_name_shown"
ЗАГЛУШКА_ИМЁН = ("(select null::text as sup_id, null::text as name, "
                 "null::text as name_source where false)")


def вид_имён_есть(cur) -> bool:
    cur.execute("select to_regclass(%s) is not null", (ВИД_ИМЁН,))
    return bool(cur.fetchone()[0])


def имена_sql(sql: str, есть: bool) -> str:
    """Запрос с видом имён, а без вида — с пустой заглушкой на его месте."""
    if есть:
        return sql
    return re.sub(rf"\bjoin\s+{ВИД_ИМЁН}\b", "join " + ЗАГЛУШКА_ИМЁН, sql)


# ── Портал ───────────────────────────────────────────────────────────────────

def пачки(ids, n: int = ПАЧКА) -> list[list[int]]:
    """Номера карточек → пачки не больше n, по возрастанию, без повторов."""
    чистые = sorted({int(i) for i in ids if str(i).strip().isdigit() and int(i) > 0})
    return [чистые[i:i + n] for i in range(0, len(чистые), n)]


def названия(client, ids) -> dict[int, str]:
    """TITLE карточек компаний. Одна пачка — один запрос: по @ID больше 50 не вернётся."""
    out: dict[int, str] = {}
    for пачка in пачки(ids):
        res = client.call("crm.company.list", {
            "filter": {"@ID": пачка}, "select": ["ID", "TITLE"], "start": -1}) or []
        for c in res:
            сырой = str(c.get("ID") or "")
            if сырой.isdigit():
                out[int(сырой)] = str(c.get("TITLE") or "").strip()
    return out


def реквизиты(client, ids) -> dict[int, list[dict]]:
    """Реквизиты компаний пачками по @ENTITY_ID, дочитка по «>ID» без смещения."""
    out: dict[int, list[dict]] = collections.defaultdict(list)
    for пачка in пачки(ids):
        после = 0
        while True:
            res = client.call("crm.requisite.list", {
                "filter": {"ENTITY_TYPE_ID": ТИП_КОМПАНИЯ, "@ENTITY_ID": пачка, ">ID": после},
                "select": list(ПОЛЯ_РЕКВИЗИТОВ), "order": {"ID": "ASC"}, "start": -1}) or []
            for r in res:
                сырой = str(r.get("ENTITY_ID") or "")
                if сырой.isdigit():
                    out[int(сырой)].append(r)
            номера = [int(r["ID"]) for r in res if str(r.get("ID") or "").isdigit()]
            if len(res) < ПАЧКА or not номера or max(номера) <= после:
                break
            после = max(номера)
    return dict(out)


# ── Разбор ───────────────────────────────────────────────────────────────────

def часть(ключ: str, частей: int, номер: int) -> bool:
    """Одно разбиение на весь прогон: CRC32 ключа по модулю числа частей.

    Хеш стабилен между частями и прогонами, поэтому сущность, появившаяся между
    частью 3 и частью 7, не выпадет из обеих (правило дробления, «разбиение ровно
    одно»)."""
    return частей <= 1 or zlib.crc32(str(ключ).encode("utf-8")) % частей == номер


def _текст(v) -> str:
    return str(v or "").strip()


def имя_из_названий(titles: list[str]) -> str:
    """Лучший TITLE сущности: самый длинный из непохожих на ключ.

    У сущности бывает несколько карточек портала (сведение схлопнуло задвоения).
    Самый длинный — по той же причине, что в load_supplier_master.показать():
    короткое написание обычно обрезок длинного."""
    годные = [t for t in (_текст(x) for x in titles) if not как_ключ(t)]
    return max(sorted(годные), key=len) if годные else ""


def из_реквизитов(строки: list[dict]) -> dict:
    """Краткое наименование (иначе полное) и налоговые номера из реквизитов.

    Разные ИНН у одной сущности — противоречие, а не выбор: номер тогда не
    берётся вовсе, и это считается отдельным счётчиком."""
    имена = [(_текст(r.get("RQ_COMPANY_NAME")), _текст(r.get("RQ_COMPANY_FULL_NAME")))
             for r in строки]
    кратк = [к for к, _ in имена if к and not как_ключ(к)]
    полн = [п for _, п in имена if п and not как_ключ(п)]
    инн = sorted({_текст(r.get("RQ_INN")) for r in строки} - {""})
    first = (lambda xs: sorted(xs)[0] if xs else "")
    return {
        "name": (max(sorted(кратк), key=len) if кратк
                 else (max(sorted(полн), key=len) if полн else "")),
        "full_name": max(sorted(полн), key=len) if полн else "",
        "inn": инн[0] if len(инн) == 1 else "",
        "inn_conflict": len(инн) > 1,
        "kpp": first({_текст(r.get("RQ_KPP")) for r in строки} - {""}) if len(инн) == 1 else "",
        "ogrn": first({_текст(r.get("RQ_OGRN")) for r in строки} - {""}) if len(инн) == 1 else "",
    }


ПОЛЯ_СТРОКИ = ("name", "card_id", "full_name", "inn", "kpp", "ogrn")


def собрать(сущности: dict[str, list[int]], титулы: dict[int, str],
            реквиз: dict[int, list[dict]], текущие: dict[tuple[str, str], dict]):
    """Сущности → строки к записи и счётчики. Чистая функция, без базы и портала.

    сущности — {sup_id: [номера карточек портала]};
    текущие  — {(sup_id, источник): действующая строка sup_display_name}.
    Строка пишется только там, где что-то изменилось: повтор прогона без
    изменений в портале не пишет ничего."""
    строки: list[dict] = []
    сч = collections.Counter()
    for sup_id in sorted(сущности):
        карточки = sorted(сущности[sup_id])
        сч["сущностей"] += 1
        titles = [титулы[c] for c in карточки if c in титулы]
        if titles:
            сч["карточек найдено у сущностей"] += 1
        сырые_титулы = [t for t in titles if _текст(t)]
        имя = имя_из_названий(titles)
        if сырые_титулы and not имя:
            сч["название похоже на ключ — не взято"] += 1
        карта_титула = next((c for c in карточки if _текст(титулы.get(c)) == имя), None)
        rq_строки = [r for c in карточки for r in реквиз.get(c, [])]
        rq = из_реквизитов(rq_строки) if rq_строки else None
        if rq and rq["inn_conflict"]:
            сч["ИНН в реквизитах расходятся"] += 1
        кандидаты = []
        if имя:
            сч["с названием из карточки"] += 1
            кандидаты.append(("bitrix:title", {"name": имя, "card_id": str(карта_титула or ""),
                                               "full_name": "", "inn": "", "kpp": "", "ogrn": ""}))
        if rq and (rq["name"] or rq["inn"]):
            if rq["name"]:
                сч["с наименованием из реквизитов"] += 1
            if rq["inn"]:
                сч["с ИНН из реквизитов"] += 1
            карта_рекв = next((c for c in карточки if реквиз.get(c)), None)
            кандидаты.append(("bitrix:requisite", {
                "name": rq["name"], "card_id": str(карта_рекв or ""),
                "full_name": rq["full_name"], "inn": rq["inn"], "kpp": rq["kpp"],
                "ogrn": rq["ogrn"]}))
        for источник, новое in кандидаты:
            было = текущие.get((sup_id, источник))
            if было and all(_текст(было.get(k)) == _текст(новое.get(k)) for k in ПОЛЯ_СТРОКИ):
                сч[f"{источник}: без изменений"] += 1
                continue
            сч[f"{источник}: {'изменено' if было else 'новое'}"] += 1
            строки.append({"sup_id": sup_id, "source": источник,
                           "previous_name": (было or {}).get("name"),
                           **{k: (_текст(новое.get(k)) or None) for k in ПОЛЯ_СТРОКИ}})
    return строки, сч


ГЕЙТЫ = {
    # Портал должен ответить хотя бы по половине спрошенных карточек. Меньше —
    # значит, чтение сломано (вебхук, права), а не компании пропали, и писать
    # по такому ответу нельзя.
    "мин_доля_найденных": 0.5,
}


def гейты(сч: collections.Counter, строки: list[dict]) -> list[str]:
    """Непройденный гейт отменяет запись целиком. Возвращает список провалов."""
    провалы = []
    if сч["сущностей"]:
        доля = сч["карточек найдено у сущностей"] / сч["сущностей"]
        if доля < ГЕЙТЫ["мин_доля_найденных"]:
            провалы.append(f"портал ответил по {100 * доля:.1f} % сущностей, "
                           f"порог {100 * ГЕЙТЫ['мин_доля_найденных']:.0f} %")
    # Страховка на запись: пустое имя и имя-ключ сюда не должны были дойти.
    плохих = sum(1 for s in строки if s["name"] is not None and как_ключ(s["name"]))
    плохих += sum(1 for s in строки if s["name"] is None and not s["inn"])
    if плохих:
        провалы.append(f"строк с пустым или похожим на ключ именем: {плохих}")
    return провалы


# ── База ─────────────────────────────────────────────────────────────────────

СУЩНОСТИ_SQL = """
select i.sup_id, i.value
  from sup_identifier i
  join sup_entity e on e.id = i.sup_id
 where i.kind = 'bitrix' and i.status <> 'rejected' and e.resolution <> 'merged'
 order by i.sup_id, i.value
"""
ТЕКУЩИЕ_SQL = """
select distinct on (sup_id, source) sup_id, source, name, card_id, full_name, inn, kpp, ogrn
  from sup_display_name
 where rolled_back_at is null
 order by sup_id, source, id desc
"""


def читать_базу(cur):
    cur.execute(СУЩНОСТИ_SQL)
    сущности: dict[str, list[int]] = collections.defaultdict(list)
    for sup_id, value in cur.fetchall():
        v = str(value or "").strip()
        if v.isdigit():
            сущности[sup_id].append(int(v))
    # Холостой прогон идёт и по базе, где схему с таблицей ещё не применяли:
    # инструмент чтения спрашивает, что есть, прежде чем читать (CLAUDE.md).
    cur.execute("select to_regclass('sup_display_name') is not null")
    текущие: dict[tuple[str, str], dict] = {}
    if cur.fetchone()[0]:
        cur.execute(ТЕКУЩИЕ_SQL)
        текущие = {(r[0], r[1]): dict(zip(ПОЛЯ_СТРОКИ, r[2:])) for r in cur.fetchall()}
    else:
        print("таблицы sup_display_name нет — все имена считаются новыми")
    # Очередь «ждут ИНН» — тем же запросом, что у страницы: второй отбор
    # разошёлся бы с первым.
    sys.path.insert(0, str(ROOT / "scripts"))
    from publish_suppliers import ОЧЕРЕДЬ_ИНН_SQL, номер_карточки  # noqa: PLC0415
    cur.execute(ОЧЕРЕДЬ_ИНН_SQL)
    очередь: set[int] = set()
    for (payload,) in cur.fetchall():
        for k in (payload or {}).get("keys") or []:
            n = номер_карточки(k)
            if n:
                очередь.add(int(n))
    return dict(сущности), текущие, очередь


def записать(cur, строки: list[dict], run_id: str) -> int:
    import psycopg2.extras

    if not строки:
        return 0
    psycopg2.extras.execute_values(cur, """
        insert into sup_display_name
          (sup_id, source, name, previous_name, card_id, full_name, inn, kpp, ogrn, run_id)
        values %s""",
        [(s["sup_id"], s["source"], s["name"], s["previous_name"], s["card_id"],
          s["full_name"], s["inn"], s["kpp"], s["ogrn"], run_id) for s in строки],
        page_size=500)
    return len(строки)


def откатить(cur, run_id: str) -> int:
    """Пометить строки прогона откаченными. Прежнее имя снова действует само."""
    cur.execute("update sup_display_name set rolled_back_at = now() "
                "where run_id = %s and rolled_back_at is null", (run_id,))
    return cur.rowcount


# ── Прогон ───────────────────────────────────────────────────────────────────

def _целое(имя: str, умолчание: int) -> int:
    try:
        return int(os.environ.get(имя) or умолчание)
    except ValueError:
        return умолчание


def main() -> int:
    import psycopg2

    dsn = os.environ.get("SUPABASE_DB_URL", "")
    if not dsn:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    откат = os.environ.get("ROLLBACK_RUN_ID", "").strip()
    apply = os.environ.get("APPLY", "") in ("1", "true")
    частей = max(1, _целое("SHARDS", 1))
    номер = _целое("SHARD", 0)
    run_id = os.environ.get("RUN_ID", "").strip() or f"names-{int(time.time())}"

    # statement_timeout и lock_timeout — в строке подключения (правило 9).
    conn = psycopg2.connect(dsn, connect_timeout=20,
                            options="-c statement_timeout=300000 -c lock_timeout=15000")
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            if откат:
                n = откатить(cur, откат)
                conn.commit()
                print(f"откат прогона: строк помечено откаченными {n}; "
                      "действующими снова стали прежние имена")
                return 0
            сущности, текущие, очередь = читать_базу(cur)
        conn.rollback()

        мои = {k: v for k, v in сущности.items() if часть(k, частей, номер)}
        моя_очередь = sorted(c for c in очередь if часть(str(c), частей, номер))
        карточки = sorted({c for v in мои.values() for c in v})
        print(f"часть {номер + 1} из {частей}: сущностей с карточкой портала {len(мои)} "
              f"(всего {len(сущности)}), карточек {len(карточки)}, "
              f"карточек очереди «ждут ИНН» {len(моя_очередь)}", flush=True)

        url = os.environ.get("BITRIX_WEBHOOK_URL", "")
        if not url:
            print("нет BITRIX_WEBHOOK_URL — портал не прочитан", file=sys.stderr)
            return 2
        sys.path.insert(0, str(ROOT))
        from bitrix_client import BitrixClient, сводка_нагрузки  # noqa: PLC0415
        client = BitrixClient(url)
        титулы = названия(client, карточки)
        реквиз = реквизиты(client, sorted(set(карточки) | set(моя_очередь)))

        строки, сч = собрать(мои, титулы, реквиз, текущие)
        очередь_с_инн = sum(1 for c in моя_очередь
                            if any(_текст(r.get("RQ_INN")) for r in реквиз.get(c, [])))
        print("ИТОГ ЧАСТИ (только счётчики)")
        for k in sorted(сч):
            print(f"  {k:44} {сч[k]}")
        print(f"  {'строк к записи':44} {len(строки)}")
        print(f"  {'карточек очереди с ИНН в реквизитах':44} {очередь_с_инн} из {len(моя_очередь)}")
        if очередь_с_инн:
            print("    их снимет следующий прогон сведения: он читает реквизиты сам")

        провалы = гейты(сч, строки)
        if провалы:
            print("ГЕЙТЫ НЕ СОШЛИСЬ — запись отменена:")
            for p in провалы:
                print(f"  ✗ {p}")
            print(сводка_нагрузки())
            return 1
        if not apply:
            print("вхолостую: в базу ничего не записано (APPLY=1 включает запись)")
            print(сводка_нагрузки())
            return 0
        with conn.cursor() as cur:
            n = записать(cur, строки, run_id)
        conn.commit()
        print(f"записано строк: {n}, ключ прогона {run_id} (откат — ROLLBACK_RUN_ID)")
        print(сводка_нагрузки())
        return 0
    except Exception as e:                       # noqa: BLE001
        conn.rollback()
        # Только вид ошибки: текст исключения базы может нести значения строк.
        print(f"ПРОГОН ОТМЕНЁН, откат транзакции: {type(e).__name__}", file=sys.stderr)
        return 4
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
