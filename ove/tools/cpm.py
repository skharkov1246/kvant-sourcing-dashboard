#!/usr/bin/env python3
"""CPM-расчёт сетевого графика БИ по ove/data/gantt.json — график считается, а не рисуется.

Сеть задана в самих задачах: id, after (связи finish→start) и dur_wd (рабочие дни).
Календарь — пятидневка (сб/вс нерабочие, праздники не учитываются), отсчёт от T0.
start/end в файле — базовый (нарисованный) план: расчёт их НЕ меняет, а сверяет с ними
ранние даты. Если расчётная ранняя дата позже заложенной — печатается расхождение:
это находка планирования (перекрытие работ в базовой сетке), а не ошибка данных.

Запуск:
  python3 ove/tools/cpm.py                      — ES/EF/LS/LF, полные резервы, крит-путь, сверка
  python3 ove/tools/cpm.py --shift genplan=+2w  — что-если: задержка задачи (+N рд, +Nw недель),
                                                  печать, какие вехи съехали и на сколько
Валидация: уникальные id, все after существуют, нет циклов, dur_wd согласован со start/end.
Только стандартная библиотека — при вызове из сборки внешние зависимости не нужны.
"""
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GANTT = ROOT / "data" / "gantt.json"
DAY = dt.timedelta(days=1)


def die(msg: str) -> None:
    print(f"ОШИБКА: {msg}", file=sys.stderr)
    sys.exit(2)


# ---------- календарь: пятидневка ----------

def is_wd(d: dt.date) -> bool:
    return d.weekday() < 5


def align_wd(d: dt.date) -> dt.date:
    """Ближайший рабочий день, не раньше d."""
    while not is_wd(d):
        d += DAY
    return d


def next_wd(d: dt.date) -> dt.date:
    """Следующий рабочий день после d."""
    return align_wd(d + DAY)


def prev_wd(d: dt.date) -> dt.date:
    """Ближайший рабочий день до d."""
    d -= DAY
    while not is_wd(d):
        d -= DAY
    return d


def add_wd(d: dt.date, n: int) -> dt.date:
    """n-й рабочий день вперёд, считая рабочий день d первым (n >= 1)."""
    d = align_wd(d)
    for _ in range(n - 1):
        d = next_wd(d)
    return d


def sub_wd(d: dt.date, n: int) -> dt.date:
    """n-й рабочий день назад, считая рабочий день d последним (n >= 1)."""
    for _ in range(n - 1):
        d = prev_wd(d)
    return d


def wd_count(a: dt.date, b: dt.date) -> int:
    """Рабочих дней в [a, b] включительно."""
    n, d = 0, a
    while d <= b:
        if is_wd(d):
            n += 1
        d += DAY
    return n


def wd_delta(a: dt.date, b: dt.date) -> int:
    """Сдвиг b относительно a в рабочих днях (b позже a — положительный)."""
    if a > b:
        return -wd_delta(b, a)
    n, d = 0, a
    while d < b:
        d += DAY
        if is_wd(d):
            n += 1
    return n


def f(d: dt.date) -> str:
    return d.strftime("%d.%m.%y")


# ---------- данные и валидация ----------

def load(path: Path):
    """Читает gantt.json, валидирует сеть, возвращает (t0, задачи, топологический порядок)."""
    try:
        g = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — любой сбой чтения фатален
        die(f"не читается {path}: {e}")
    t0 = dt.date.fromisoformat(g["t0"])
    tasks, errs = [], []
    for i, t in enumerate(g.get("tasks", [])):
        tid = t.get("id")
        if not tid:
            errs.append(f"задача №{i + 1} без id ({t.get('name', '?')[:50]})")
            continue
        if "start" not in t:
            errs.append(f"{tid}: нет start")
            continue
        start = dt.date.fromisoformat(t["start"])
        end = dt.date.fromisoformat(t["end"]) if t.get("end") else None
        mile = bool(t.get("milestone"))
        dur = t.get("dur_wd")
        if mile:
            if dur not in (None, 0):
                errs.append(f"{tid}: веха с dur_wd={dur} (должно быть 0)")
            dur = 0
        else:
            if end is None:
                errs.append(f"{tid}: не веха и без end")
                continue
            calc = wd_count(start, end)
            if dur is None:
                print(f"внимание: {tid} без dur_wd — беру {calc} рд из start/end")
                dur = calc
            elif dur != calc:
                errs.append(f"{tid}: dur_wd={dur}, а по start/end выходит {calc} рд — рассинхрон данных")
        tasks.append({"id": tid, "name": t.get("name", tid), "lane": t.get("lane", ""),
                      "start": start, "end": end, "milestone": mile,
                      "crit": bool(t.get("crit")), "dur": dur, "after": list(t.get("after", []))})
    ids = [t["id"] for t in tasks]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        errs.append("дубли id: " + ", ".join(sorted(dup)))
    known = set(ids)
    for t in tasks:
        for p in t["after"]:
            if p not in known:
                errs.append(f"{t['id']}: after ссылается на несуществующий id «{p}»")
            if p == t["id"]:
                errs.append(f"{t['id']}: ссылка сам на себя")
    if errs:
        die("сеть невалидна:\n  " + "\n  ".join(errs))

    # топологическая сортировка (Кан) — заодно ловим циклы
    indeg = {t["id"]: len(t["after"]) for t in tasks}
    succs = {t["id"]: [] for t in tasks}
    for t in tasks:
        for p in t["after"]:
            succs[p].append(t["id"])
    order, queue = [], [t["id"] for t in tasks if indeg[t["id"]] == 0]
    while queue:
        tid = queue.pop(0)
        order.append(tid)
        for s in succs[tid]:
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
    if len(order) < len(tasks):
        die("в сети цикл через: " + ", ".join(tid for tid, d in indeg.items() if d > 0))
    return t0, tasks, order


# ---------- CPM ----------

def cpm(tasks, order, t0, dur_over=None):
    """Прямой/обратный проход. Возвращает ({id: es/ef/ls/lf/tf}, дата окончания проекта).

    Соглашение по вехам (dur=0): веха встаёт В ДЕНЬ окончания последнего
    предшественника; обычная задача стартует со следующего рабочего дня.
    """
    by = {t["id"]: t for t in tasks}
    dur = {t["id"]: t["dur"] for t in tasks}
    if dur_over:
        dur.update(dur_over)
    es, ef = {}, {}
    for tid in order:
        t = by[tid]
        if t["after"]:
            base = max(ef[p] for p in t["after"])
            es[tid] = base if dur[tid] == 0 else next_wd(base)
        else:
            es[tid] = align_wd(t0)
        ef[tid] = es[tid] if dur[tid] == 0 else add_wd(es[tid], dur[tid])
    end = max(ef.values())
    succs = {t["id"]: [] for t in tasks}
    for t in tasks:
        for p in t["after"]:
            succs[p].append(t["id"])
    lf, ls = {}, {}
    for tid in reversed(order):
        if succs[tid]:
            lf[tid] = min(ls[s] if dur[s] == 0 else prev_wd(ls[s]) for s in succs[tid])
        else:
            lf[tid] = end
        ls[tid] = lf[tid] if dur[tid] == 0 else sub_wd(lf[tid], dur[tid])
    res = {tid: {"es": es[tid], "ef": ef[tid], "ls": ls[tid], "lf": lf[tid],
                 "tf": wd_delta(es[tid], ls[tid])} for tid in order}
    return res, end


def crit_chain(tasks, res):
    """Одна линейная цепочка критического пути (с конца, по ведущим предшественникам)."""
    by = {t["id"]: t for t in tasks}
    end_ef = max(r["ef"] for r in res.values())
    tail = [tid for tid, r in res.items() if r["ef"] == end_ef and r["tf"] == 0][0]
    path = [tail]
    while by[path[0]]["after"]:
        preds = by[path[0]]["after"]
        # ведущий — чьё окончание задало ранний старт; при равенстве берём критичного
        path.insert(0, max(preds, key=lambda p: (res[p]["ef"], res[p]["tf"] == 0)))
    return path


# ---------- отчёт ----------

def cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def report(tasks, order, res, end, t0):
    print(f"CPM-сеть: {len(tasks)} задач, T0 = {f(align_wd(t0))}, календарь — пятидневка без праздников")
    print("ES/EF — ранние старт/финиш, LS/LF — поздние, TF — полный резерв (рд); ● — расчётный крит-путь\n")
    hdr = f"{'id':9}{'рд':>3}  {'ES':8}  {'EF':8}  {'LS':8}  {'LF':8}  {'TF':>3}    задача"
    print(hdr)
    print("-" * len(hdr))
    for t in tasks:  # в порядке файла — по дорожкам, как на сайте
        r = res[t["id"]]
        mark = "●" if r["tf"] == 0 else " "
        print(f"{t['id']:9}{t['dur']:>3}  {f(r['es'])}  {f(r['ef'])}  {f(r['ls'])}  {f(r['lf'])}"
              f"  {r['tf']:>3}  {mark} {cut(t['name'], 56)}")

    path = crit_chain(tasks, res)
    others = [tid for tid in order if res[tid]["tf"] == 0 and tid not in path]
    base_end = max((t["end"] or t["start"]) for t in tasks)
    print(f"\nКритический путь: {' → '.join(path)}")
    if others:
        print(f"Параллельно критичны (TF=0): {', '.join(others)}")
    print(f"Расчётное окончание проекта: {f(end)} ({wd_count(align_wd(t0), end)} рд от T0); "
          f"в базовом плане — {f(base_end)} ({wd_delta(base_end, end):+} рд)")

    print("\nВехи:")
    for t in tasks:
        if not t["milestone"]:
            continue
        r = res[t["id"]]
        d = wd_delta(t["start"], r["ef"])
        note = "в срок" if d == 0 else (f"позже плана на {d} рд" if d > 0 else f"раньше плана на {-d} рд")
        print(f"  {t['id']:4} {cut(t['name'], 44):46} план {f(t['start'])} → расчёт {f(r['ef'])}"
              f" ({note}), TF={r['tf']} рд")

    finds = [(t, res[t["id"]]) for t in tasks if res[t["id"]]["es"] > t["start"]]
    print("\nСверка с базовым планом (расчётный ранний старт позже заложенного — находка планирования):")
    if not finds:
        print("  расхождений нет — базовые даты не раньше расчётных")
    for t, r in finds:
        print(f"  {t['id']:8} план {f(t['start'])} → расчёт {f(r['es'])}  "
              f"+{wd_delta(t['start'], r['es'])} рд  {cut(t['name'], 48)}")
    if finds:
        print("  (перекрытия в нарисованной сетке: связи after дают более поздний ранний старт)")

    drawn = {t["id"] for t in tasks if t["crit"] and not t["milestone"]}
    calc = {t["id"] for t in tasks if res[t["id"]]["tf"] == 0 and not t["milestone"]}
    if drawn != calc:
        print("\nФлаг crit в файле против расчёта:")
        if drawn - calc:
            print("  нарисованы критичными, но имеют резерв: "
                  + ", ".join(f"{x} (TF={res[x]['tf']})" for x in order if x in drawn - calc))
        if calc - drawn:
            print("  расчётно критичны, но не помечены: " + ", ".join(x for x in order if x in calc - drawn))


# ---------- что-если ----------

def parse_shift(s: str):
    m = re.fullmatch(r"\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*([+-]?)(\d+)(w?)\s*", s)
    if not m:
        die(f"не разобрал --shift «{s}» — жду вид id=+N (рабочие дни) или id=+Nw (недели)")
    tid, sign, n, week = m.groups()
    return tid, (-1 if sign == "-" else 1) * int(n) * (5 if week else 1)


def shift_report(tasks, order, res0, end0, t0, tid, delta):
    by = {t["id"]: t for t in tasks}
    if tid not in by:
        die(f"--shift: нет задачи с id «{tid}»")
    dur0 = by[tid]["dur"]
    dur1 = max(0, dur0 + delta)
    res1, end1 = cpm(tasks, order, t0, {tid: dur1})
    print(f"\nЧто-если: {tid} {delta:+} рд (длительность {dur0} → {dur1} рд) — «{cut(by[tid]['name'], 50)}»")
    moved = 0
    for t in tasks:
        if not t["milestone"]:
            continue
        a, b = res0[t["id"]]["ef"], res1[t["id"]]["ef"]
        d = wd_delta(a, b)
        if d:
            moved += 1
            print(f"  веха {t['id']:4} {cut(t['name'], 40):42} {f(a)} → {f(b)}  съехала на {d:+} рд")
        else:
            print(f"  веха {t['id']:4} {cut(t['name'], 40):42} {f(a)} — не съехала")
    if not moved:
        print(f"  вехи не съехали: полный резерв задачи {tid} в базовом расчёте — "
              f"{res0[tid]['tf']} рд, задержка {delta:+} рд им покрывается")
    d_end = wd_delta(end0, end1)
    print(f"  окончание проекта: {f(end0)} → {f(end1)}"
          + (f" ({d_end:+} рд)" if d_end else " (без изменений)"))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="CPM по ove/data/gantt.json: расчёт, сверка, что-если")
    ap.add_argument("--file", default=str(GANTT), help="путь к gantt.json (по умолчанию ove/data)")
    ap.add_argument("--shift", action="append", default=[], metavar="ID=+N[w]",
                    help="задержать задачу на N рабочих дней (Nw — недель); можно несколько раз")
    a = ap.parse_args(argv)
    t0, tasks, order = load(Path(a.file))
    res, end = cpm(tasks, order, t0)
    report(tasks, order, res, end, t0)
    for s in a.shift:
        tid, delta = parse_shift(s)
        shift_report(tasks, order, res, end, t0, tid, delta)


if __name__ == "__main__":
    main()
