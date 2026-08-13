#!/usr/bin/env python3
"""Слияние находок прочёсов в единый реестр расхождений ОВЭ-75.

Источник истины — ove/data/discrepancies.json (ручные + уже принятые позиции,
у каждой стабильный id C-NN; на id завязаны ответы Заказчика в
questions_answers.json, поэтому id никогда не переиспользуются и не меняются).

Прочёсы пишут кандидатов в disc_a.json / disc_b.json / disc_c.json. Этот скрипт
добавляет НОВЫЕ позиции (дедупликация по нормализованному заголовку и по паре
цитат), присваивает следующие свободные C-id и переписывает реестр. Файлы
disc_* остаются как есть — журнал происхождения.

Запуск: python3 ove/tools/build_disc.py
После него: python3 ove/tools/build_questions.py && python3 ove/build.py
"""
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
REG = DATA / "discrepancies.json"
SOURCES = ["disc_a.json", "disc_b.json", "disc_c.json"]
VALID_CAT = {"numbers", "steel", "struct", "refs", "terms", "units", "other"}
VALID_SEV = {"blocker", "major", "minor"}


def norm(s: str) -> str:
    return re.sub(r"[^а-яёa-z0-9]+", " ", str(s or "").lower()).strip()


def fp(item: dict) -> tuple:
    """Отпечаток позиции: заголовок + начала обеих цитат."""
    a = item.get("a") or {}
    b = item.get("b") or {}
    qa = a.get("q") if isinstance(a, dict) else str(a)
    qb = b.get("q") if isinstance(b, dict) else str(b)
    return (norm(item.get("title"))[:80], norm(qa)[:60], norm(qb)[:60])


def main() -> None:
    reg = json.loads(REG.read_text(encoding="utf-8"))
    seen = {fp(i) for i in reg["items"]}
    seen_titles = {norm(i["title"])[:80] for i in reg["items"]}
    next_n = 1 + max(int(m.group(1)) for i in reg["items"]
                     if (m := re.match(r"C-(\d+)$", i["id"])))

    added, skipped = [], 0
    for name in SOURCES:
        path = DATA / name
        if not path.exists():
            continue
        src = json.loads(path.read_text(encoding="utf-8"))
        for it in src.get("items", []):
            # валидация формы: без двух цитат позицию не берём
            a, b = it.get("a") or {}, it.get("b") or {}
            if not (isinstance(a, dict) and isinstance(b, dict)
                    and str(a.get("q", "")).strip() and str(b.get("q", "")).strip()):
                skipped += 1
                continue
            f = fp(it)
            if f in seen or norm(it.get("title"))[:80] in seen_titles:
                skipped += 1
                continue
            seen.add(f)
            seen_titles.add(norm(it.get("title"))[:80])
            added.append({
                "id": f"C-{next_n:02d}",
                "cat": it.get("cat") if it.get("cat") in VALID_CAT else "other",
                "sev": it.get("sev") if it.get("sev") in VALID_SEV else "minor",
                "lot": it.get("lot", 0),
                "title": str(it.get("title", "")).strip()[:200],
                "a": {"q": str(a.get("q", "")).strip(), "where": str(a.get("where", "")).strip()},
                "b": {"q": str(b.get("q", "")).strip(), "where": str(b.get("where", "")).strip()},
                "essence": str(it.get("essence", "")).strip(),
                "ask": str(it.get("ask", "")).strip(),
                "src": f"прочёс {name} · {src.get('scope', '')}".strip(),
            })
            next_n += 1

    reg["items"].extend(added)
    sev_order = {"blocker": 0, "major": 1, "minor": 2}
    reg["items"].sort(key=lambda x: (sev_order.get(x["sev"], 9), x["id"]))
    REG.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"добавлено {len(added)}, пропущено (дубли/брак) {skipped}, всего {len(reg['items'])}")


if __name__ == "__main__":
    main()
