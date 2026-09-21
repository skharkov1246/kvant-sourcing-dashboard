#!/usr/bin/env python3
"""Перенос вердиктов проверки в наборы разведки — по ТОЧНОЙ ссылке, не по похожести.

ЗАЧЕМ. 12.09.2026 каталог дефектов и карточки исполнителей легли на портал
черновиком: скептиков на них не хватило. Проверка проведена отдельным прогоном,
и её результат надо положить в те же записи, а не рядом.

ПОЧЕМУ ТОЧНАЯ ССЫЛКА. Привязка «по похожести слов» уже подводила: она либо не
находила ничего, либо сажала вердикт на соседнюю карточку. Вердикт на чужой
записи хуже отсутствующего — он выдаёт непроверенное за проверенное. Поэтому
скептик возвращает поля node и defect ДОСЛОВНО, а здесь они сверяются после
одной нормализации: регистр, ё/е, пробелы. Всё, что не легло точно, остаётся
в файле списком unbound, а не исчезает.

Запуск:  python zip/tools/apply_verdicts.py <каталог-журнала> [<ещё-каталог> …]
         python zip/tools/apply_verdicts.py --check
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data"
DIAG = D / "diagnostics_recon.json"
REPAIR = D / "repair_recon.json"
REPORT = D / "verdicts_applied.json"

ALLOWED = {"подтверждено", "частично", "опровергнуто", "непроверяемо"}
REJECTED_KEEP = {"опровергнуто"}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower().replace("ё", "е")).strip()


def read_journal(*srcs):
    """Журналов может быть НЕСКОЛЬКО: проверка идёт по направлениям, каждое
    своим прогоном. Отчёт обязан собирать их все — иначе следующий прогон
    затрёт заявления скептиков предыдущего, а именно в них лежит, чего
    проверка НЕ покрыла."""
    out, any_found = [], False
    for src in srcs:
        p = Path(src) / "journal.jsonl"
        if not p.exists():
            print(f"журнал недоступен, пропущен: {src}")
            continue
        any_found = True
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("type") == "result" and isinstance(rec.get("result"), dict):
                out.append(rec["result"])
    return out if any_found else None


def apply(results):
    diag = json.loads(DIAG.read_text(encoding="utf-8"))
    rep = json.loads(REPAIR.read_text(encoding="utf-8"))

    # Индекс дефектов строится по ДВУМ написаниям узла, и это не перестраховка.
    # Скептик читает dict/symptom.json, где узел уже сведён к каноническому
    # («Подшипник качения»), а в наборе разведки лежит исходное свободное
    # название («Подшипник качения электродвигателя»). Первый прогон привязал
    # 43 вердикта из 232 ровно поэтому. Ключом служит и канонический узел, и
    # исходный. Привязки по одному названию дефекта нет: см. комментарий ниже.
    sym = json.loads((ROOT.parent / "dict" / "symptom.json").read_text(encoding="utf-8"))
    canon = {}
    for row in sym.get("defect_rows", []):
        canon.setdefault((norm(row.get("node")), norm(row.get("defect"))),
                         norm(row.get("node_raw")))
    dindex = {}
    for a in diag["angles"]:
        for d in a["defects"]:
            dindex.setdefault((norm(d["node"]), norm(d["defect"])), []).append(d)
    # индекс исполнителей: имя → запись
    cindex = {}
    for a in rep["contractor_angles"]:
        for c in a["contractors"]:
            cindex.setdefault(norm(c["name"]), []).append(c)

    # Считаем ЗАПИСИ, а не события привязки: два прогона проверки могут
    # перекрыться по узлу (так «КИП, САУ, защиты» попал и в общий прогон, и в
    # прогон КИПиА), и тогда карточка получает вердикт дважды. Число событий
    # выходило больше числа карточек — 342 против 340, и тест это поймал.
    # Перекрытие само по себе не ошибка, но знать о нём надо: побеждает
    # последний вердикт, и это должно быть видно, а не подразумеваться.
    bound = {"defects": 0, "contractors": 0}
    touched = {"defects": set(), "contractors": set()}
    rebound = []
    unbound, overall, missing = [], [], []
    for r in results:
        scope, key = r.get("scope"), r.get("key")
        if r.get("overall"):
            overall.append({"scope": scope, "key": key, "overall": r["overall"],
                            "checked": r.get("checked")})
        for m in (r.get("missing") or []):
            missing.append({"scope": scope, "key": key, "text": m})
        for v in (r.get("verdicts") or []):
            verdict = v.get("verdict")
            if verdict not in ALLOWED:
                unbound.append({"scope": scope, "key": key, "why": "вердикт вне словаря",
                                "verdict": verdict, "ref": v})
                continue
            body = {"verdict": verdict, "why": v.get("why") or "",
                    "correction": v.get("correction") or "",
                    "claim": v.get("source") or ""}
            if v.get("ref_name"):
                hit = cindex.get(norm(v["ref_name"]))
                field = "contractors"
            else:
                nd, df = norm(v.get("ref_node")), norm(v.get("ref_defect"))
                hit = dindex.get((nd, df))
                if not hit:
                    raw = canon.get((nd, df))
                    if raw:
                        hit = dindex.get((raw, df))
                # Привязки ТОЛЬКО по названию дефекта здесь НЕТ, и это
                # выяснилось дорого. Уникальности названия мало: скептик,
                # получивший в группу канонический узел «КИП, САУ, защиты»,
                # вернул по нему ссылки на карточки поршневой машины, и
                # запасной вариант послушно посадил «Крутильно-усталостный
                # излом вала» на функцию безопасности, а следом перезаписал
                # вердикт «опровергнуто» у карточки про предел температуры
                # нагнетания по API 618 — то есть стёр найденную ошибку.
                # Узел обязан совпасть: по каноническому написанию либо по
                # исходному. Не совпал — вердикт идёт в unbound, где его видно.
                field = "defects"
            if not hit:
                unbound.append({"scope": scope, "key": key, "why": "ссылка не нашлась",
                                "ref": {k: v.get(k) for k in ("ref_node", "ref_defect", "ref_name")},
                                "verdict": verdict})
                continue
            for rec in hit:
                was = rec["verdict"]["verdict"]
                if id(rec) in touched[field]:
                    rebound.append({"scope": scope, "key": key,
                                    "ref": v.get("ref_name") or
                                           f'{v.get("ref_node")} | {v.get("ref_defect")}',
                                    "was": was, "now": verdict,
                                    "kept": was if was in REJECTED_KEEP
                                            and verdict not in REJECTED_KEEP else verdict})
                    # Найденная ошибка не отменяется вердиктом, который её просто
                    # не нашёл. Прогоны перекрываются по узлу, и второй скептик
                    # видит карточку в чужом для себя контексте: так «опровергнуто»
                    # по пределу температуры нагнетания API 618 чуть не сменилось
                    # на «частично». Понизить «опровергнуто» может только другое
                    # «опровергнуто» — то есть новый разбор той же ошибки.
                    if was in REJECTED_KEEP and verdict not in REJECTED_KEEP:
                        continue
                touched[field].add(id(rec))
                rec["verdict"] = dict(body)
            bound[field] += 1

    def tally(items):
        c = {}
        for it in items:
            v = it["verdict"]["verdict"]
            c[v] = c.get(v, 0) + 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))

    # Сводка в наборе обязана пересчитываться вместе с записями. Иначе страница
    # берёт stats.by_verdict и показывает состояние ДО проверки: так на портале
    # висело «скептик не сослался: 224», когда непроверенной оставалась одна
    # карточка. Запись поправлена, а витрина продолжала врать.
    diag["stats"]["by_verdict"] = tally(
        [x for a in diag["angles"] for x in a["findings"] + a["defects"]])
    rep["stats"]["by_verdict"] = tally(
        [x for a in rep["tech_angles"] for x in a["technologies"]]
        + [x for a in rep["contractor_angles"] for x in a["contractors"]])

    DIAG.write_text(json.dumps(diag, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    REPAIR.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    alld = [d for a in diag["angles"] for d in a["defects"]]
    allc = [c for a in rep["contractor_angles"] for c in a["contractors"]]
    report = {
        "source": "прогоны проверки черновика",
        "bound": {k: len(v) for k, v in touched.items()},
        "verdicts_applied": bound,
        "rebound_count": len(rebound),
        "rebound": rebound,
        "unbound_count": len(unbound),
        "unbound": unbound,
        "by_verdict_defects": tally(alld),
        "by_verdict_contractors": tally(allc),
        "overall": overall,
        "missing": missing,
        "note": ("Вердикты привязаны по точной ссылке на узел и название дефекта "
                 "(для исполнителей — на название компании). Непривязанные лежат "
                 "списком unbound: потерянная проверка хуже отсутствующей."),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def main() -> int:
    if "--check" in sys.argv:
        if not REPORT.exists():
            print("verdicts_applied.json отсутствует — прогон проверки ещё не переносился")
            return 0
        r = json.loads(REPORT.read_text(encoding="utf-8"))
        print(f"карточек с вердиктом: дефектов {r['bound']['defects']}, исполнителей "
              f"{r['bound']['contractors']}; без адреса {r['unbound_count']}; "
              f"перекрытий {r.get('rebound_count', 0)}")
        return 0
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("укажи каталог журнала проверки (можно несколько)")
        return 1
    results = read_journal(*args)
    if results is None:
        print("журнал недоступен — файлы не трогаем")
        return 1
    r = apply(results)
    print(f"карточек с вердиктом: дефектов {r['bound']['defects']}, "
          f"исполнителей {r['bound']['contractors']}")
    print(f"вердиктов применено: {r['verdicts_applied']['defects']} + "
          f"{r['verdicts_applied']['contractors']}; перекрытий прогонов: {r['rebound_count']}")
    print(f"без адреса: {r['unbound_count']}")
    print("дефекты:", r["by_verdict_defects"])
    print("исполнители:", r["by_verdict_contractors"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
