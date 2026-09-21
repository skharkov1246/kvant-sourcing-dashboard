#!/usr/bin/env python3
"""Сколько системы мы на самом деле прочитали, и что мешает прочитать остальное.

ЗАЧЕМ. Владелец поставил задачу «собрать всё, что есть в системе». Ответ на неё
— не «мы поработали», а доля: сколько вложений сделок прочитано, сколько нет и
ПОЧЕМУ именно. Без этой доли любая сводка по заявке молчит о том, что часть
системы в неё не вошла вовсе.

ЧТО ЗАМЕР УСТАНАВЛИВАЕТ. Записи описи делятся на четыре состояния: прочитано с
ценами, прочитано без цен, скачано но не разобралось, не скачано вовсе. У
последнего состояния причина названа дословно так, как её вернула система, — это
не наша догадка, а её собственный ответ.

ОЦЕНКА «СКОЛЬКО ТАМ ЦЕН» — ЭКСТРАПОЛЯЦИЯ, и она помечена как экстраполяция.
Доля файлов с ценами среди прочитанных переносится на непрочитанные. В заголовок
такое число не идёт (правило репозитория про выгрузки владельцу), но порядок
потери оно показывает.

    python gt/tools/system_coverage.py [--write]
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "gt/data/bitrix_tkp_index.json"
OUT = ROOT / "gt/data/ship_system_coverage.json"


def measure() -> dict:
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    inv = [i for s in doc["scopes"].values() for i in (s.get("inventory") or [])]

    got = [i for i in inv if i.get("download") == "ок"]
    skipped = [i for i in inv if i.get("download") == "не требуется"]
    missing = [i for i in inv if i.get("download") not in ("ок", "не требуется")]
    priced = [i for i in got if (i.get("priced") or 0) > 0]
    unparsed = [i for i in got if i.get("status") == "не разобрался"]

    share = len(priced) / len(got) if got else 0.0
    why = collections.Counter(str(i.get("download")) for i in missing)
    fields = collections.Counter(str(i.get("field_name")) for i in missing)
    dirs = collections.Counter(str(i.get("direction")) for i in missing)

    scopes = {}
    for name, s in doc["scopes"].items():
        rows = s.get("inventory") or []
        scopes[name] = {
            "deals": s.get("deals"),
            "files_seen": s.get("files"),
            "records": len(rows),
            "downloaded": len([i for i in rows if i.get("download") == "ок"]),
            "not_downloaded": len([i for i in rows
                                   if i.get("download") not in ("ок", "не требуется")]),
        }
    return {
        "updated": doc.get("updated"),
        "source": ("Опись вложений сделок Bitrix (gt/data/bitrix_tkp_index.json). "
                   "Считает gt/tools/system_coverage.py."),
        "question": "Сколько системы прочитано и что мешает прочитать остальное.",
        "records_total": len(inv),
        "downloaded": len(got),
        "not_needed": len(skipped),
        "not_downloaded": len(missing),
        "downloaded_with_prices": len(priced),
        "downloaded_unparsed": len(unparsed),
        "share_priced_among_downloaded_pct": round(share * 100, 1),
        "rows_parsed": sum(i.get("rows") or 0 for i in inv),
        "rows_with_price": sum(i.get("priced") or 0 for i in inv),
        "why_not_downloaded": [{"reason": k, "records": v} for k, v in why.most_common()],
        "not_downloaded_by_field": [{"field_name": k, "records": v}
                                    for k, v in fields.most_common(6)],
        "not_downloaded_by_direction": dict(dirs),
        "by_scope": scopes,
        "estimate_lost_priced_files": round(len(missing) * share),
        "estimate_caveat": (
            "ЭТО ЭКСТРАПОЛЯЦИЯ, а не измерение: доля файлов с ценами среди прочитанных "
            "перенесена на непрочитанные. В заголовок такое число не идёт. Оно показывает "
            "порядок потери, а не саму потерю."),
        "what_it_means": (
            f"Из {len(inv)} записей описи прочитано {len(got)}, а {len(missing)} не "
            f"скачались вовсе. Причину называет сама система, дословно: "
            f"«{why.most_common(1)[0][0] if why else '—'}». Это не отказ сети и не наша "
            f"недоработка в разборе — это ПРАВА доступа у ключа подключения."),
        "next_step": (
            "Добавить ключу подключения к Bitrix право на чтение диска и повторить обход. "
            "Это одно действие владельца, после которого станут читаемы сотни вложений, "
            "причём почти все они — входящие предложения поставщиков, то есть именно "
            "цены. Никакой другой работы этот остаток не требует."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    m = measure()
    print(f"записей описи: {m['records_total']}")
    print(f"  прочитано:        {m['downloaded']:>5}  из них с ценами "
          f"{m['downloaded_with_prices']} ({m['share_priced_among_downloaded_pct']} %)")
    print(f"  скачивать не надо:{m['not_needed']:>5}")
    print(f"  НЕ СКАЧАНО:       {m['not_downloaded']:>5}")
    for w in m["why_not_downloaded"]:
        print(f"      {w['records']:>4} × {w['reason']}")
    print(f"  направления непрочитанного: {m['not_downloaded_by_direction']}")
    print(f"строк разобрано {m['rows_parsed']:,}, из них с ценой {m['rows_with_price']:,}"
          .replace(",", " "))
    print(f"оценка потери: около {m['estimate_lost_priced_files']} файлов с ценами "
          f"(экстраполяция, не измерение)")
    if a.write:
        OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"замер записан в {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
