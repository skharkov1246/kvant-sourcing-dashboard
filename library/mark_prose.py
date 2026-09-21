#!/usr/bin/env python3
"""Разметка строк lib_demand, которые на самом деле текст тендерного документа.

ЗАЧЕМ. Разбор текстовых вложений принимал за позицию спецификации любую строку
длиннее восьми знаков (library/indexer.py, ветка разбора текста). На 12.09.2026 в
спросе 1 451 732 строки, из них 369 171 без сегмента — в основном текст извещений
о закупке, проектов договоров и форм КП. Пока он лежит в lib_demand, доли по
сегментам занижены на четверть, а «спрос» как показатель врёт.

ЧТО ДЕЛАЕТ. Один проход по lib_demand, правило из library/docfilter.py, вердикт
по файлу целиком. Помеченное пишется в таблицу-спутник lib_row_junk — исходная
строка не меняется и не удаляется. Откат — delete по run_id, секунды.

ПОРЯДОК РАБОТЫ. Сначала холостой прогон: он ничего не пишет, но печатает шесть
гейтов приёмки. Запись разрешена только после того, как гейты сошлись:

    SUPABASE_DB_URL=... python library/mark_prose.py              # холостой + гейты
    SUPABASE_DB_URL=... APPLY=1 python library/mark_prose.py      # запись
    SUPABASE_DB_URL=... REVERT=<run_id> python library/mark_prose.py   # откат

ГЛАВНЫЙ ГЕЙТ — первый. Он измеряет ложные срабатывания на независимом эталоне:
строках, которые словарь SEGMENTS узнаёт ПО САМОМУ НАИМЕНОВАНИЮ. Эталон честный,
потому что правило в словарь не смотрит, а словарь ничего не знает о признаках
правила. Нельзя брать эталоном segment_id из базы: он наследуется от файла
целиком (indexer.py ставит сегмент по тексту всего файла), поэтому договор со
словом «труба» лежит среди «классифицированных».

Откат наследования сегментов (не этого скрипта, но по тому же ключу прогона):
    update lib_demand set segment_id = null, segment_rule = null, segment_run = null
     where segment_run = '<run_id>';

В журнал идут только агрегаты и наши собственные константы. Ни одного
наименования, ни одного номера сделки: репозиторий публичный.
"""
from __future__ import annotations

import os
import sys
from array import array
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import docfilter as df  # noqa: E402  (после sys.path)
from segments import classify  # noqa: E402

APPLY = os.environ.get("APPLY", "") not in ("", "0", "false")
REVERT = os.environ.get("REVERT", "").strip()
WINDOW = int(os.environ.get("WINDOW", "50000"))
SAMPLE_TO = os.environ.get("SAMPLE_TO", "").strip()

# Пороги можно двигать входами workflow: они не контракт, контракт — гейты.
for _name in ("MIN_ROWS", "MIN_PROSE_ROWS"):
    if os.environ.get(_name):
        setattr(df, _name, int(os.environ[_name]))
for _name in ("MAX_SPEC_SHARE", "MIN_PROSE_SHARE"):
    if os.environ.get(_name):
        setattr(df, _name, float(os.environ[_name]))

# statement_timeout задаётся в строке подключения, а не через SET: SET внутри
# транзакции откатывается вместе с ней, а дефолт пула Supabase — две минуты,
# на которых уже падал прогон миграций №4.
OPTIONS = "-c statement_timeout=900000 -c idle_in_transaction_session_timeout=600000"

READ_SQL = ("select id, item_name, oem, unit, qty, source_file "
            "from lib_demand where id > %s order by id limit %s")
WRITE_SQL = ("insert into lib_row_junk (demand_id, rule, run_id, marks) values %s "
             "on conflict (demand_id) do nothing")
REVERT_SQL = "delete from lib_row_junk where run_id = %s"


def num(v, w=12):
    return f"{v:,}".replace(",", " ").rjust(w)


def pct(a, b):
    return f"{a / b * 100:.2f}%" if b else "—"


def block(head):
    print(f"\n=== {head} ===", flush=True)


class Files:
    """Счётчики по каждому файлу. Файлов около 25 тысяч — помещается в память."""

    def __init__(self):
        self.idx: dict[str, int] = {}
        self.rows = array("i")
        self.spec = array("i")       # строк с защитным признаком (с учётом словаря)
        self.prose = array("i")      # строк-кандидатов в прозу
        self.spec_sh = array("i")    # то же без защиты словарём — для теневого решения
        self.prose_sh = array("i")
        self.dict_hit = array("i")   # строк, которые словарь узнаёт по самой строке

    def index(self, fid: str) -> int:
        i = self.idx.get(fid)
        if i is None:
            i = len(self.rows)
            self.idx[fid] = i
            for a in (self.rows, self.spec, self.prose, self.spec_sh, self.prose_sh, self.dict_hit):
                a.append(0)
        return i

    def verdicts(self):
        """Вердикт по каждому файлу — настоящий и теневой."""
        real, shadow = [], []
        for i in range(len(self.rows)):
            real.append(df.file_verdict(self.rows[i], self.spec[i], self.prose[i]))
            shadow.append(df.file_verdict(self.rows[i], self.spec_sh[i], self.prose_sh[i]))
        return real, shadow


def read_all(cur, files: Files):
    """Один проход по спросу. Возвращает кандидатов и счётчики для гейтов."""
    cand_id, cand_file, cand_mark = array("q"), array("i"), []
    sh_id, sh_file = array("q"), array("i")
    dict_ids: list[tuple[int, int]] = []      # (id строки, индекс файла) — эталон гейта 1
    words: Counter = Counter()
    struct_rows = 0
    struct_cand = 0
    last, seen = 0, 0
    while True:
        cur.execute(READ_SQL, (last, WINDOW))
        chunk = cur.fetchall()
        if not chunk:
            break
        for rid, name, oem, unit, qty, src in chunk:
            last = rid
            seen += 1
            fi = files.index(src or "")
            files.rows[fi] += 1
            structured = bool((oem or "").strip() or (unit or "").strip() or qty is not None)
            if structured:
                struct_rows += 1
            hit = classify(name or "") is not None
            if hit:
                files.dict_hit[fi] += 1
                dict_ids.append((rid, fi))

            sp, pr = df.row_marks(name, oem, unit, qty, dict_hit=hit)
            if sp:
                files.spec[fi] += 1
            if df.row_is_prose(sp, pr):
                files.prose[fi] += 1
                cand_id.append(rid)
                cand_file.append(fi)
                cand_mark.append(",".join(sorted(pr & df.CLOSED)))
                words.update(pr & df.CLOSED)
                if structured:
                    struct_cand += 1

            # Теневое решение: та же логика без защиты словарём. Нужно, чтобы
            # измерить пересечение правила с эталоном, а не тавтологию.
            sp_s, pr_s = df.row_marks(name, oem, unit, qty, dict_hit=False)
            if sp_s:
                files.spec_sh[fi] += 1
            if df.row_is_prose(sp_s, pr_s):
                files.prose_sh[fi] += 1
                sh_id.append(rid)
                sh_file.append(fi)
        if seen % 200000 < WINDOW:
            print(f"  прочитано {num(seen, 0)}", flush=True)
    return dict(seen=seen, cand_id=cand_id, cand_file=cand_file, cand_mark=cand_mark,
                sh_id=sh_id, sh_file=sh_file, dict_ids=dict_ids, words=words,
                struct_rows=struct_rows, struct_cand=struct_cand)


def hist(values, title):
    print(f"  {title}")
    bins = [0] * 21
    for v in values:
        bins[min(20, int(v * 20))] += 1
    for b, n in enumerate(bins):
        if n:
            print(f"    {b * 5:>3}–{(b + 1) * 5:>3}%{num(n, 10)}")


def report(files: Files, scan, real, shadow):
    marked = [i for i, fi in enumerate(scan["cand_file"]) if real[fi] == "документация"]
    sh_marked = {scan["sh_id"][i] for i, fi in enumerate(scan["sh_file"]) if shadow[fi] == "документация"}
    doc_files = sum(1 for v in real if v == "документация")
    doc_rows = sum(files.rows[i] for i, v in enumerate(real) if v == "документация")

    block("объём")
    print(f"  строк всего{num(scan['seen'])}")
    print(f"  файлов{num(len(files.rows), 17)}")

    block("вердикты файлов")
    per = Counter(real)
    print(f"  {'вердикт':32}{'файлов':>10}{'строк в них':>14}")
    for v, n in per.most_common():
        rows = sum(files.rows[i] for i, x in enumerate(real) if x == v)
        print(f"  {v:32}{num(n, 10)}{num(rows, 14)}")

    block("что помечено")
    print(f"  строк помечено{num(len(marked))}   ({pct(len(marked), scan['seen'])} всей базы)")
    by_mark = Counter(scan["cand_mark"][i] for i in marked)
    for m, n in by_mark.most_common():
        print(f"    по признаку {m or '—':22}{num(n, 10)}")
    print("  срабатывания закрытого списка (это наши константы, а не данные):")
    for w, n in scan["words"].most_common():
        print(f"    {w:24}{num(n, 10)}")
    grey = doc_rows - len(marked)
    print(f"  строк в осуждённых файлах БЕЗ признаков — не помечено:{num(grey, 8)}")

    # ── ГЕЙТ 1 ─────────────────────────────────────────────────────────────────
    block("ГЕЙТ 1. ложные на независимом эталоне — главная цифра")
    etalon = scan["dict_ids"]
    bad = [rid for rid, _fi in etalon if rid in sh_marked]
    print(f"  строк, которые словарь узнаёт по самой строке:{num(len(etalon), 12)}")
    print(f"  из них теневое правило пометило бы:{num(len(bad), 22)}   {pct(len(bad), len(etalon))}")
    print("  ПРИЁМКА: 0; стоп при доле выше 0,05%")
    gate1 = len(bad) / len(etalon) * 100 if etalon else 0.0

    # ── ГЕЙТ 2 ─────────────────────────────────────────────────────────────────
    block("ГЕЙТ 2. доказанные спецификации")
    proven = [i for i in range(len(files.rows))
              if files.rows[i] >= 5 and files.dict_hit[i] / files.rows[i] >= 0.30]
    condemned = [i for i in proven if real[i] == "документация"]
    proven_rows = sum(files.rows[i] for i in proven)
    proven_nodict = proven_rows - sum(files.dict_hit[i] for i in proven)
    proven_set = set(proven)
    nodict_marked = sum(1 for i, fi in enumerate(scan["sh_file"])
                        if fi in proven_set and scan["sh_id"][i] in sh_marked)
    print(f"  файлов, где ≥5 строк и ≥30% узнаёт словарь:{num(len(proven), 14)}")
    print(f"  из них осуждено правилом:{num(len(condemned), 32)}   ПРИЁМКА: 0")
    print(f"  их строк, которые словарь НЕ узнаёт:{num(proven_nodict, 21)}")
    print(f"  из них пометило бы теневое правило:{num(nodict_marked, 22)}   "
          f"{pct(nodict_marked, proven_nodict)}   ПРИЁМКА: не выше 0,5%")
    gate2 = len(condemned)

    # ── ГЕЙТ 3 ─────────────────────────────────────────────────────────────────
    block("ГЕЙТ 3. структурный контроль — инвариант кода")
    print(f"  строк с непустыми oem/unit/qty:{num(scan['struct_rows'], 26)}")
    print(f"  из них помечено:{num(scan['struct_cand'], 41)}   ПРИЁМКА: строго 0")

    # ── ГЕЙТ 4 ─────────────────────────────────────────────────────────────────
    block("ГЕЙТ 4. устойчивость порога")
    big = [i for i in range(len(files.rows)) if files.rows[i] >= df.MIN_ROWS]
    ss = [files.spec[i] / files.rows[i] for i in big]
    ps = [files.prose[i] / files.rows[i] for i in big]
    hist(ss, "доля строк с защитным признаком (порог " + str(df.MAX_SPEC_SHARE) + ")")
    hist(ps, "доля строк-кандидатов в прозу (порог " + str(df.MIN_PROSE_SHARE) + ")")
    # Мерим не расстояние до порога, а переворачивается ли вердикт при его сдвиге.
    # Расстояние обманывает: файл с нулевой долей защиты отстоит от порога 0,05
    # ровно на 0,05, хотя он самый надёжный кандидат из возможных.
    base_spec, base_prose = df.MAX_SPEC_SHARE, df.MIN_PROSE_SHARE
    shaky = 0
    for i in big:
        base = real[i]
        for ds, dp in ((0.05, 0), (-0.05, 0), (0, 0.05), (0, -0.05)):
            df.MAX_SPEC_SHARE = max(0.0, base_spec + ds)
            df.MIN_PROSE_SHARE = max(0.0, base_prose + dp)
            if df.file_verdict(files.rows[i], files.spec[i], files.prose[i]) != base:
                shaky += 1
                break
    df.MAX_SPEC_SHARE, df.MIN_PROSE_SHARE = base_spec, base_prose
    print(f"  файлов, чей вердикт переворачивается при сдвиге порога на 0,05:"
          f"{num(shaky, 8)}   {pct(shaky, len(big))}   ПРИЁМКА: ниже 5%")
    gate4 = shaky / len(big) * 100 if big else 0.0

    # ── ГЕЙТ 5 ─────────────────────────────────────────────────────────────────
    block("СЕТКА ПОРОГОВ. цена ослабления, посчитана за тот же проход")
    print("  Пороги не контракт — контракт гейты. Таблица показывает, чем оплачен")
    print("  каждый шаг ослабления: сколько прибавится помеченного и сколько")
    print("  настоящей номенклатуры при этом попадёт под нож.")
    etalon_ids = {rid for rid, _fi in etalon}
    print(f"\n  {'защита≤':>8}{'проза≥':>8}{'файлов':>9}{'строк':>10}{'ложных':>8}{'доля ложных':>13}")
    base_spec, base_prose = df.MAX_SPEC_SHARE, df.MIN_PROSE_SHARE
    for ms in (0.05, 0.10, 0.15, 0.20, 0.30, 0.50):
        for mp in (0.12, 0.20):
            df.MAX_SPEC_SHARE, df.MIN_PROSE_SHARE = ms, mp
            v_real, v_sh = files.verdicts()
            f_n = sum(1 for v in v_real if v == "документация")
            r_n = sum(1 for fi in scan["cand_file"] if v_real[fi] == "документация")
            sh = {scan["sh_id"][i] for i, fi in enumerate(scan["sh_file"])
                  if v_sh[fi] == "документация"}
            fp = len(sh & etalon_ids)
            print(f"  {ms:>8.2f}{mp:>8.2f}{num(f_n, 9)}{num(r_n, 10)}{num(fp, 8)}"
                  f"{pct(fp, len(etalon_ids)):>13}")
    df.MAX_SPEC_SHARE, df.MIN_PROSE_SHARE = base_spec, base_prose

    block("ГЕЙТ 5. польза — не блокирующий")
    print(f"  помечено строк:{num(len(marked), 27)}")
    print(f"  осуждено файлов:{num(doc_files, 26)}")
    print("  ожидание 30–50% от строк без сегмента; ниже 15% — правило не окупается,")
    print("  чинить надо разборщик, а не размечать последствия")

    block("СВОДКА ГЕЙТОВ")
    ok1 = gate1 <= 0.05
    ok2 = gate2 == 0
    ok3 = scan["struct_cand"] == 0
    ok4 = gate4 < 5.0
    for name, ok, val in (("1 · ложные на эталоне", ok1, f"{gate1:.3f}%"),
                          ("2 · доказанные спецификации", ok2, f"{gate2} осуждено"),
                          ("3 · структурный инвариант", ok3, f"{scan['struct_cand']} помечено"),
                          ("4 · устойчивость порога", ok4, f"{gate4:.1f}%")):
        print(f"  {'ПРОЙДЕН ' if ok else 'НЕ ПРОЙДЕН'}  {name:32}{val}")
    return marked, all((ok1, ok2, ok3, ok4))


def sample(scan, marked, path):
    """Гейт 6 — глазами, только на машине владельца."""
    if os.environ.get("GITHUB_ACTIONS"):
        print("SAMPLE_TO в Actions запрещён: выборка содержит наименования позиций",
              file=sys.stderr)
        return
    ids = [scan["cand_id"][i] for i in marked[:200]]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(str(i) for i in ids))
    print(f"  идентификаторы 200 помеченных строк записаны в {path}")


def main() -> int:
    url = os.environ.get("SUPABASE_DB_URL", "")
    if not url:
        print("нет переменной SUPABASE_DB_URL", file=sys.stderr)
        return 2
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url, connect_timeout=20, options=OPTIONS)
    conn.autocommit = True

    if REVERT:
        with conn.cursor() as cur:
            cur.execute(REVERT_SQL, (REVERT,))
            print(f"снято пометок: {cur.rowcount} (прогон {REVERT})")
            cur.execute("update lib_mark_runs set reverted_at = now() where run_id = %s", (REVERT,))
        conn.close()
        return 0

    run_id = df.RULE_VERSION + "-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    print(f"правило: {df.RULE_VERSION} · прогон: {run_id}")
    print(f"режим: {'ЗАПИСЬ В БАЗУ' if APPLY else 'холостой, без записи'}")
    print(f"пороги: строк≥{df.MIN_ROWS} · защита≤{df.MAX_SPEC_SHARE} · "
          f"проза≥{df.MIN_PROSE_ROWS} и ≥{df.MIN_PROSE_SHARE}", flush=True)

    files = Files()
    with conn.cursor() as cur:
        scan = read_all(cur, files)
    real, shadow = files.verdicts()
    marked, gates_ok = report(files, scan, real, shadow)

    if SAMPLE_TO:
        sample(scan, marked, SAMPLE_TO)

    if not APPLY:
        print("\nхолостой прогон — в базе ничего не изменилось. Для записи: APPLY=1")
        conn.close()
        return 0

    if not gates_ok:
        print("\nЗАПИСЬ ОТМЕНЕНА: не пройдены гейты приёмки. Сначала пороги, потом запись.",
              file=sys.stderr)
        conn.close()
        return 1

    rows = [(scan["cand_id"][i], df.RULE_VERSION, run_id, scan["cand_mark"][i]) for i in marked]
    with conn.cursor() as cur:
        cur.execute("insert into lib_mark_runs (run_id, rule, mode, params, rows_total, rows_marked, "
                    "files_total, files_marked, rows_dict_hit) "
                    "values (%s,%s,'разметка',%s::jsonb,%s,%s,%s,%s,%s)",
                    (run_id, df.RULE_VERSION,
                     '{"min_rows": %d, "max_spec_share": %s, "min_prose_rows": %d, "min_prose_share": %s}'
                     % (df.MIN_ROWS, df.MAX_SPEC_SHARE, df.MIN_PROSE_ROWS, df.MIN_PROSE_SHARE),
                     scan["seen"], len(rows), len(files.rows),
                     sum(1 for v in real if v == "документация"), len(scan["dict_ids"])))
        for i in range(0, len(rows), 5000):
            psycopg2.extras.execute_values(cur, WRITE_SQL, rows[i:i + 5000], page_size=1000)
            print(f"  записано {num(min(i + 5000, len(rows)), 0)} из {num(len(rows), 0)}", flush=True)
        cur.execute("update lib_mark_runs set finished_at = now() where run_id = %s", (run_id,))
    conn.close()
    print(f"\n✓ помечено {len(rows)} строк, прогон {run_id}")
    print(f"  откат: REVERT={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
