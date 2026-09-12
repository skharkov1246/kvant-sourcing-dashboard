#!/usr/bin/env python3
"""Взаимозаменяемость и ведомости: чей это номер на самом деле и из чего машина.

ЗАЧЕМ. Каталожный номер сборщика почти никогда не номер изготовителя. Telsmith
14T47 — это серийный сферический подшипник SKF/Timken с посадочным диаметром
140 мм; пока связи нет, сорсер ищет несуществующую деталь у несуществующего
изготовителя и получает отказ. То же с нашими внутренними номерами: KV30 0001 и
Epiroc 7490 0290 74 — одна деталь, и не связать их значит дважды закупать.

ЧТО ЧИТАЕТСЯ. Партномера потребности (поле «номер изготовителя»), сплошные
проверки наличия (настоящий номер и предложенная замена), кросс-таблица Telsmith
и ведомость COP 3060MUX.

ПОЧЕМУ НОМЕР ПРОВЕРЯЕТСЯ, А НЕ БЕРЁТСЯ КАК ЕСТЬ. В поле «номер изготовителя»
руками пишут и описание: «ШАЙБА АЛЮМИНИЕВАЯ - 1/4 BSP». Такое в таблицу
взаимозаменяемости попасть не должно — иначе по ней начнут искать деталь с
номером «шайба алюминиевая».

БЕЗ APPLY=1 идёт вхолостую.

    python library/load_crossrefs.py
    SUPABASE_DB_URL=... APPLY=1 python library/load_crossrefs.py

В журнал идут только агрегаты.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import equipment as eq  # noqa: E402  (после sys.path)
from segments import classify  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
NOT_KEY = re.compile(r"[^0-9a-zа-яё]+")
КИРИЛЛИЦА = re.compile(r"[а-яё]")


def part_key(pn: str) -> str:
    return NOT_KEY.sub("", (pn or "").lower().replace("ё", "е"))[:80]


def похоже_на_номер(s: str) -> bool:
    """Номер это или всё-таки описание.

    Четыре условия, каждое оплачено содержимым поля «номер изготовителя»:
    цифра есть (номера без цифр не бывает), не длиннее сорока знаков, не больше
    трёх слов и не больше двух кириллических букв — «ШАЙБА АЛЮМИНИЕВАЯ - 1/4 BSP»
    отсекается именно последним."""
    t = (s or "").strip()
    if not (2 < len(t) <= 40) or not any(c.isdigit() for c in t):
        return False
    if len(t.split()) > 3:
        return False
    return len(КИРИЛЛИЦА.findall(t.lower())) <= 2


_ТОКЕН = re.compile(r"[^\s,;/()\[\]]+")


def номера_из(текст: str, сколько: int = 3) -> list[str]:
    """Номера, спрятанные в предложении.

    Поле «замена» у продавцов — не номер, а фраза: «Magelis HMISTO501 / HMIS
    серия — официальная замена после EOL». Номер там есть, и выбросить всю
    строку значит потерять 400 подсказок. Токен считается номером, если в нём
    есть И цифра, И латинская буква: так отсекаются и слова, и голые числа
    («1900/1950» — это диапазон моделей, а не партномер)."""
    out = []
    for t in _ТОКЕН.findall(текст or ""):
        t = t.strip(".,:—-")
        if not (4 <= len(t) <= 40):
            continue
        if not (any(c.isdigit() for c in t) and re.search(r"[A-Za-z]", t)):
            continue
        if КИРИЛЛИЦА.search(t.lower()):
            continue
        if t not in out:
            out.append(t)
        if len(out) >= сколько:
            break
    return out


def load(path: str, key: str | None = None):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return []
    d = json.load(open(full, encoding="utf-8"))
    if isinstance(d, list):
        return d
    return d.get(key or "rows") or []


def num(v, w=9):
    return f"{v:,}".replace(",", " ").rjust(w)


def brand_of(запись) -> str:
    return (запись.get("brand") or "") if isinstance(запись, dict) else ""


def build_alts() -> list[dict]:
    alts: dict[tuple, dict] = {}

    def add(pn, alt, kind, maker=None, evidence=None, source="", conf="med"):
        key, alt = part_key(pn), (alt or "").strip()
        if not key or not похоже_на_номер(alt) or part_key(alt) == key:
            return
        alts[(key, alt[:120], kind)] = {
            "part_id": key, "alt_pn": alt[:120], "kind": kind,
            "alt_maker": (maker or None) and str(maker)[:200],
            "evidence": (evidence or None) and str(evidence)[:1000],
            "confidence": conf, "source": source}

    for r in load("gt/data/pn_db.json"):
        add(r.get("pn"), r.get("mpn"), "номер изготовителя", r.get("mk"),
            r.get("ev"), "партномера потребности")
    for путь, откуда in (("gt/data/ship_sweep.json", "проверка наличия (ЛУКОЙЛ)"),
                         ("gt/data/ship_energoseti.json", "проверка наличия (Энергосети)")):
        for r in load(путь):
            add(r.get("pn"), r.get("real_pn"), "номер изготовителя", r.get("real_maker"),
                r.get("note"), откуда)
            # Замена приходит фразой: достаём номера, а фразу оставляем
            # доказательством — по ней потом видно, чья это была рекомендация.
            for кандидат in номера_из(str(r.get("substitute") or "")):
                add(r.get("pn"), кандидат, "замена", r.get("real_maker"),
                    str(r.get("substitute"))[:600], откуда, conf="low")
    # Кросс-каталоги MOTORTECH по газопоршневым: «эта катушка равна вот этой у
    # Caterpillar/Jenbacher». В поле kind написано, равнозначная это замена или
    # только подходит. И номера, и кроссы лежат СПИСКАМИ — их надо разворачивать,
    # иначе в таблицу попадёт строка «['06.50.034']» и пройдёт все проверки:
    # цифры есть, знаков мало, кириллицы нет.
    for r in load("gpu/data/motortech_cross.json", "records"):
        вид = "замена" if "equivalent" in str(r.get("kind") or "").lower() else "аналог"
        свои = [str(x) for x in (r.get("motortech") or []) if x]
        for бренд in (r.get("cross") or []):
            марка = str(brand_of(бренд))
            for чужой in (бренд.get("pns") or []) if isinstance(бренд, dict) else []:
                for свой in свои:
                    add(str(чужой), свой, вид, "MOTORTECH", r.get("url"),
                        "кросс-каталог MOTORTECH")
                    add(свой, str(чужой), вид, марка, r.get("url"),
                        "кросс-каталог MOTORTECH")

    # Ведомость состава: наш внутренний номер и номер изготовителя — одна деталь.
    # KV30 0001 и Epiroc 7490 0290 74 не связать значит закупать дважды.
    for m in load("zip/data/bom.json", "machines"):
        for запись in list(m.get("parts") or []) + list(m.get("kits") or []):
            чужой = str(запись.get("epiroc_pn") or запись.get("oem_pn") or "").strip()
            свой = str(запись.get("kv_pn") or "").strip()
            if not (чужой and свой):
                continue
            add(чужой, свой, "наш номер", "КВАНТ", None, "ведомость состава")
            add(свой, чужой, "номер изготовителя", m.get("oem") or None, None,
                "ведомость состава")

    for r in load("zip/data/telsmith_crossrefs.json", "crossrefs"):
        add(r.get("telsmith_pn"), r.get("real_pn"), "номер изготовителя",
            r.get("real_maker"), r.get("evidence_url"), "кросс-таблица Telsmith",
            r.get("confidence") or "med")
    return list(alts.values())


# Тип позиции в кросс-каталоге записан по-английски («ignition coil», «pickup»).
# Все они из системы зажигания газопоршневой машины — узел известен заранее, и
# гадать по тексту незачем.
ЗАЖИГАНИЕ = ("ignition", "spark", "coil", "pickup", "lead", "harness", "trigger",
             "extension", "boot", "detonation")


def build_motortech_parts() -> dict[str, dict]:
    """Детали из кросс-каталога: номер OEM и номер MOTORTECH — реальные позиции.

    Без них связи взаимозаменяемости повисают: ребро ставится только на деталь,
    которая есть в каталоге, а катушек зажигания Caterpillar у нас не было."""
    детали: dict[str, dict] = {}

    def положи(pn, марка, тип, движки, url):
        key = part_key(pn)
        if not key or not похоже_на_номер(str(pn)):
            return
        тип = (тип or "").strip() or "позиция системы зажигания"
        unit = "gpu.ignition" if any(w in тип.lower() for w in ЗАЖИГАНИЕ) else None
        детали.setdefault(key, {
            "id": key, "catalog_no": str(pn)[:120],
            "name": f"{тип} ({марка})"[:400] if марка else тип[:400],
            "oem": (марка or None) and str(марка)[:200],
            "model": (движки or None) and str(движки)[:600],
            "category": "зажигание" if unit else None,
            "segment_id": "gpu", "unit_id": unit,
            "source": "кросс-каталог MOTORTECH", "url": url})

    for r in load("gpu/data/motortech_cross.json", "records"):
        тип, движки, url = r.get("part"), r.get("engines"), r.get("url")
        for pn in (r.get("motortech") or []):
            положи(pn, "MOTORTECH", тип, движки, url)
        for бренд in (r.get("cross") or []):
            if not isinstance(бренд, dict):
                continue
            for pn in (бренд.get("pns") or []):
                положи(pn, бренд.get("brand"), тип, движки, url)
    return детали


def build_patterns() -> dict[str, dict]:
    """Шифровки номеров: как по номеру понять, чей он и что означает.

    Это вход в цепочку с того, что у сорсера на руках, — со строки из заявки.
    Ловушки хранятся отдельно, потому что стоят денег: «401088700» — это тот же
    4010887 с дописанными нулями, и по первому номеру не найдётся ничего."""
    out: dict[str, dict] = {}

    def add_pat(oem, pattern, meaning, examples, traps, status, source):
        oem = str(oem or "").strip()
        ключ = pattern or meaning or ""
        if not oem or not ключ:
            return
        ид = "шифр." + hashlib.sha1(f"{oem}|{ключ}".encode()).hexdigest()[:12]
        out[ид] = {"id": ид, "oem": oem[:200], "pattern": str(pattern or "")[:200] or None,
                   "meaning": str(meaning or "")[:2000] or None,
                   "examples": str(examples or "")[:1000] or None,
                   "traps": str(traps or "")[:2000] or None,
                   "status": str(status or "")[:60] or None, "source": source}

    for section in load("gt/data/pn_guide.json", "sections"):
        oem = str(section.get("title") or "").strip()
        for r in (section.get("rules") or []):
            add_pat(oem, r.get("pattern"), r.get("meaning"), r.get("examples"),
                    None, r.get("status"), "справочник шифровок ГТУ")
    for b in load("gpu/data/pn_guide.json", "brands"):
        правила = "; ".join(str(x) for x in (b.get("rules") or []))
        ловушки = "; ".join(str(x) for x in (b.get("traps") or []))
        add_pat(b.get("brand"), None, f"{b.get('format') or ''} {правила}".strip(),
                None, ловушки, None, "справочник шифровок ГПУ")
    return out


def имя_машины(m: dict) -> str:
    return str(m.get("name") or "").strip()


def build_bom() -> tuple[list[dict], dict[str, dict], list[dict]]:
    """Ведомость → строки состава, новые детали и машина."""
    строки, детали, машины = [], {}, {}
    for m in load("zip/data/bom.json", "machines"):
        имя = имя_машины(m)
        if not имя:
            continue
        ключ_машины = eq.norm_model(имя) if eq.looks_like_machine(имя) else None
        if ключ_машины:
            машины[ключ_машины] = {"id": ключ_машины, "name": имя[:200],
                                   "source": "ведомость состава"}
        # Сервисные наборы — тоже позиции: у них свой номер изготовителя и свой
        # наш номер, их так же закупают и так же задваивают.
        for j, k in enumerate(m.get("kits") or []):
            pn = str(k.get("oem_pn") or "").strip()
            if not pn:
                continue
            key = part_key(pn)
            имя = str(k.get("name") or pn)
            детали.setdefault(key, {
                "id": key, "catalog_no": pn[:120], "name": имя[:400], "oem": None,
                "model": имя_машины(m)[:600], "category": "сервисный набор",
                "segment_id": classify(f"{имя} {имя_машины(m)}"),
                "unit_id": eq.unit_of(имя), "source": "ведомость состава: комплекты"})
            строки.append({
                "id": f"{ключ_машины or part_key(имя_машины(m))}.комплект.{j}",
                "machine": имя_машины(m)[:200], "model_id": ключ_машины,
                "scheme": str(k.get("scheme") or "")[:40] or None, "level": None,
                "part_id": key, "part_no": pn[:120],
                "own_no": str(k.get("kv_pn") or "")[:120] or None,
                "qty": str(k.get("qty") or "")[:40] or None, "name": имя[:400],
                "source": "ведомость состава: комплекты"})

        for i, p in enumerate(m.get("parts") or []):
            pn = str(p.get("epiroc_pn") or p.get("pn") or "").strip()
            if not pn:
                continue
            key = part_key(pn)
            строки.append({
                "id": f"{ключ_машины or part_key(имя)}.{i}", "machine": имя[:200],
                "model_id": ключ_машины, "scheme": str(p.get("scheme") or "")[:40] or None,
                "level": p.get("level") if isinstance(p.get("level"), int) else None,
                "part_id": key, "part_no": pn[:120],
                "own_no": str(p.get("kv_pn") or "")[:120] or None,
                "qty": str(p.get("qty") or "")[:40] or None,
                "name": str(p.get("desc") or "")[:400] or None,
                "source": "ведомость состава"})
            имя_детали = str(p.get("desc") or pn)
            детали.setdefault(key, {
                "id": key, "catalog_no": pn[:120], "name": имя_детали[:400],
                "oem": None, "model": имя[:600], "category": None,
                "segment_id": classify(f"{имя_детали} {имя}"),
                "unit_id": eq.unit_of(имя_детали), "source": "ведомость состава"})
    return строки, детали, машины


def main() -> int:
    alts = build_alts()
    строки, детали, машины = build_bom()
    зажигание = build_motortech_parts()
    шифровки = build_patterns()
    for k, v in зажигание.items():
        детали.setdefault(k, v)

    print("=== взаимозаменяемость ===")
    print(f"  связей: {num(len(alts))}")
    for k, n in Counter(a["kind"] for a in alts).most_common():
        print(f"    {k:26}{num(n)}")
    print(f"  с названным изготовителем: {sum(1 for a in alts if a['alt_maker'])}")
    print(f"  различных деталей: {len({a['part_id'] for a in alts})}")

    print(f"\n  деталей из кросс-каталога MOTORTECH: {len(зажигание)}")

    print(f"  шифровок номеров: {len(шифровки)} "
          f"(с ловушками: {sum(1 for x in шифровки.values() if x['traps'])})")

    print("\n=== ведомости ===")
    print(f"  машин: {len(машины)} · строк состава: {num(len(строки))} · "
          f"деталей: {len(детали)}")
    сузлом = sum(1 for d in детали.values() if d["unit_id"])
    print(f"  с определённым узлом: {сузлом} · со своим номером: "
          f"{sum(1 for s in строки if s['own_no'])}")

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        return 0

    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras

    import indexer
    conn = psycopg2.connect(url, connect_timeout=20, options="-c statement_timeout=900000")
    conn.autocommit = False
    with conn.cursor() as cur:
        indexer.ensure_segments(cur)
        if машины:
            psycopg2.extras.execute_values(cur, """
                insert into lib_models (id, name, source) values %s
                on conflict (id) do nothing""",
                [(m["id"], m["name"], m["source"]) for m in машины.values()])
        psycopg2.extras.execute_values(cur, """
            insert into lib_parts (id, catalog_no, name, model, segment_id, unit_id, source)
            values %s
            on conflict (id) do update set
              model = coalesce(lib_parts.model, excluded.model),
              unit_id = coalesce(lib_parts.unit_id, excluded.unit_id),
              updated_at = now()""",
            [(d["id"], d["catalog_no"], d["name"], d["model"], d["segment_id"],
              d["unit_id"], d["source"]) for d in детали.values()], page_size=500)

        # Связь ставится только на известную деталь: ключ ведёт в lib_parts, и
        # висячая ссылка здесь означала бы «замена неизвестно чего».
        cur.execute("select id from lib_parts")
        известные = {r[0] for r in cur.fetchall()}
        годные = [a for a in alts if a["part_id"] in известные]
        psycopg2.extras.execute_values(cur, """
            insert into lib_part_alt (part_id, alt_pn, kind, alt_maker, evidence,
                                      confidence, source)
            values %s on conflict (part_id, alt_pn, kind) do nothing""",
            [(a["part_id"], a["alt_pn"], a["kind"], a["alt_maker"], a["evidence"],
              a["confidence"], a["source"]) for a in годные], page_size=500)
        psycopg2.extras.execute_values(cur, """
            insert into lib_pn_patterns (id, oem, pattern, meaning, examples, traps,
                                         status, source)
            values %s
            on conflict (id) do update set
              meaning = excluded.meaning, examples = excluded.examples,
              traps = excluded.traps, status = excluded.status, updated_at = now()""",
            [(x["id"], x["oem"], x["pattern"], x["meaning"], x["examples"], x["traps"],
              x["status"], x["source"]) for x in шифровки.values()], page_size=200)

        psycopg2.extras.execute_values(cur, """
            insert into lib_bom (id, machine, model_id, scheme, level, part_id, part_no,
                                 own_no, qty, name, source)
            values %s
            on conflict (id) do update set
              qty = excluded.qty, name = excluded.name, part_id = excluded.part_id""",
            [(s["id"], s["machine"], s["model_id"], s["scheme"], s["level"],
              s["part_id"] if s["part_id"] in известные else None, s["part_no"],
              s["own_no"], s["qty"], s["name"], s["source"]) for s in строки],
            page_size=500)
        conn.commit()
        print(f"\n  связей записано: {len(годные)} из {len(alts)} "
              f"(остальные — на деталь, которой в каталоге нет)")
        for t in ("lib_part_alt", "lib_bom", "lib_pn_patterns", "lib_parts"):
            cur.execute(f"select count(*) from {t}")
            print(f"  {t:16}{num(cur.fetchone()[0])}")
    conn.close()
    print("\n✓ взаимозаменяемость и ведомости загружены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
