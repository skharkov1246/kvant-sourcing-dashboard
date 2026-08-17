#!/usr/bin/env python3
"""Сборка ove/data/questions.json — единый реестр вопросов Заказчику.

Три источника, сводятся в один список:
  1. Разночтения — противоречия, найденные прямо в документах Заказчика.
     Ведутся вручную в этом файле: каждое проверено по исходнику и снабжено
     ссылкой на конкретное место.
  2. Вопросы к Заказчику из ove/data/tender.json (questions_to_customer).
  3. Открытые вопросы из черновика БИ — поля open[] в bi_lot1..4.json,
     собираются автоматически с привязкой к лоту и разделу.

Ответы Заказчика и комментарии инженеров на сайте не хранятся: они живут в
localStorage браузера и выгружаются файлом. Собранные ответы возвращаются
в этот файл руками — тогда они попадают в общую версию.

Запуск: python3 ove/tools/build_questions.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "ove" / "data"

# ── 1. Разночтения — из единого реестра ove/data/discrepancies.json ──────────
# Ведутся там (ручные + слияние прочёсов через build_disc.py); id стабильные,
# на них завязаны ответы Заказчика. Здесь только читаем.

SEV_ORDER = {"blocker": 0, "major": 1, "minor": 2, "open": 3}


def main():
    tender = json.loads((DATA / "tender.json").read_text(encoding="utf-8"))
    disc = json.loads((DATA / "discrepancies.json").read_text(encoding="utf-8"))
    items = []

    def flat(side):
        if isinstance(side, dict):
            q, w = side.get("q", ""), side.get("where", "")
            return f"{q} — {w}" if w else q
        return str(side or "")

    for c in disc["items"]:
        items.append({"id": c["id"], "kind": "Разночтение", "lot": c.get("lot", 0),
                      "sev": c["sev"], "title": c["title"],
                      "a": flat(c.get("a")), "b": flat(c.get("b")),
                      "why": c.get("essence", ""), "ask": c.get("ask", "")})

    for i, q in enumerate(tender.get("questions_to_customer", []), 1):
        items.append({"id": f"Q-{i:02d}", "kind": "Вопрос", "lot": 0, "sev": "major",
                      "title": q, "a": "", "b": "", "why": "", "ask": q})

    n = 0
    for lot in (1, 2, 3, 4):
        path = DATA / f"bi_lot{lot}.json"
        if not path.exists():
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        for s in d.get("sections", []):
            for o in s.get("open", []):
                n += 1
                items.append({"id": f"O-{lot}-{n:03d}", "kind": "Открытый вопрос БИ", "lot": lot,
                              "sev": "open", "title": o, "a": "", "b": "",
                              "why": f"Раздел БИ: {s.get('title','')}", "ask": o})

    items.sort(key=lambda x: (SEV_ORDER.get(x["sev"], 9), x["id"]))
    out = {
        "updated": "",
        "note": ("Реестр ведётся на сайте. Ответы Заказчика и комментарии инженеров вносятся в поля "
                 "и отправляются кнопкой «⚡ в общую базу»: воркер коммитит их в "
                 "ove/data/questions_answers.json с именем инженера в авторе коммита, автосборка "
                 "запекает в страницу для всей команды. Резерв: «⬇ ответы» файлом — и вернуть владельцу."),
        "counts": {k: sum(1 for x in items if x["sev"] == k) for k in ("blocker", "major", "minor", "open")},
        "items": items,
    }
    (DATA / "questions.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"questions.json: всего {len(items)} — " +
          ", ".join(f"{k} {v}" for k, v in out["counts"].items()))


if __name__ == "__main__":
    main()
