"""Суточный скан чатов сделок → снимок реакции коммерсантов (data/chat_snapshot.json).

Запускается РАЗ В СУТКИ (полночь МСК) отдельным джобом — НЕ при каждой пересборке дашборда.
Копит дневные снимки (последние 14 дн); дашборд читает их и показывает среднее за неделю.
AI-оценку (вовлечённость/профессионализм) НЕ трогает — она обновляется отдельно (Opus по тексту чатов);
здесь только дозаписываем выборку текста сообщений в /tmp/rep_samples.json для ручной/Opus-оценки.

Использование:  .venv/bin/python chat_scan.py
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import config
import reps as reps_mod
from bitrix_client import BitrixClient

SNAP = Path(__file__).parent / "data" / "chat_snapshot.json"
SAMPLES = Path("/tmp/rep_samples.json")


def main() -> int:
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    print("• Скан чатов сделок (реакция + выборка текста)…")
    react, rep_ids, unames = reps_mod.scan_chat_reaction(client, collect_samples=True)
    uid2label = {u: l for l, u in rep_ids.items()}

    # снимок: отделяем тяжёлые samples от метрик
    day = (dt.datetime.utcnow() + dt.timedelta(hours=3)).date().isoformat()  # МСК-дата
    day_rec = {}
    samples_out = {}
    for uid, r in react.items():
        s = r.pop("samples", [])
        day_rec[uid] = r
        samples_out[uid] = {"label": uid2label.get(uid, uid), "name": unames.get(uid, uid), "samples": s}

    try:
        snap = json.loads(SNAP.read_text(encoding="utf-8"))
    except Exception:
        snap = {"days": {}, "assessment": {}}
    snap.setdefault("days", {})[day] = day_rec
    # держим последние 14 дней
    for d in sorted(snap["days"])[:-14]:
        del snap["days"][d]
    snap["updated"] = (dt.datetime.utcnow() + dt.timedelta(hours=3)).isoformat(timespec="seconds")
    snap.setdefault("assessment", {})

    SNAP.parent.mkdir(parents=True, exist_ok=True)
    SNAP.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    SAMPLES.write_text(json.dumps(samples_out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  ✓ снимок за {day}: {len(day_rec)} сотрудников, дней в истории {len(snap['days'])}")
    for uid, r in day_rec.items():
        print(f"    {uid2label.get(uid, uid):10}: упом {r.get('mentions',0)}, медиана {r.get('medMin')} мин, "
              f"висит {r.get('waitN',0)} (>сут {r.get('waitGt1d',0)}), выборка {len(samples_out[uid]['samples'])} реплик")
    print(f"  ✓ выборка текста для оценки: {SAMPLES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
