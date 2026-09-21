#!/usr/bin/env python3
"""Досье сделки: всё, что о ней известно, одним текстом.

Собирает в одном месте карточку, историю стадий, запросы поставщикам, чат,
переписку и тексты вложений. Нужно, чтобы разбор сделки не начинался с
десятка запросов к четырём базам — агенту достаточно одной команды.

    python base/dossier.py 19034 19036 ...
    python base/dossier.py --list won      # идентификаторы по группе
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILE_BUDGET = 24_000      # знаков текста вложений на сделку
CHAT_BUDGET = 24_000
ACT_BUDGET = 12_000


def _con(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    c = sqlite3.connect(str(path), timeout=300)
    c.execute("PRAGMA busy_timeout=300000")
    return c


def dossier(db: Path, chats: Path, acts: Path, rfq: dict, deal_id: int) -> str:
    con = _con(db)
    if con is None:
        return f"# сделка {deal_id}: база не найдена"
    row = con.execute("""SELECT d.id, d.title, d.origin_cat, d.category, d.stage, d.semantic, d.won,
                                d.date_create, d.won_date, d.sum_eur, d.currency, d.sum_orig,
                                d.company, d.assigned, coalesce(s.name, d.seg), d.item, d.brand
                         FROM deals d LEFT JOIN segments s ON s.code = d.seg WHERE d.id=?""",
                      (deal_id,)).fetchone()
    if not row:
        return f"# сделка {deal_id}: не найдена"
    out = [f"# СДЕЛКА {row[0]} · {row[1]}",
           f"воронка заведения: {row[2]} · сейчас: {row[3]} · стадия: {row[4]} ({row[5]})",
           f"исход: {'ДОШЛА ДО РЕАЛИЗАЦИИ ' + str(row[8] or '') if row[6] else 'НЕ ДОШЛА'}",
           f"создана: {row[7]} · сумма: {row[11]} {row[10]} (={row[9]:.0f} EUR)" if row[9] is not None else "",
           f"клиент: {row[12]} · ответственный: {row[13]} · сегмент: {row[14]}"]

    hist = con.execute("SELECT stage, at FROM stage_events WHERE deal_id=? ORDER BY at", (deal_id,)).fetchall()
    if hist:
        out.append("\n## ПУТЬ ПО СТАДИЯМ")
        out += [f"  {a[:10]} → {s}" for s, a in hist]

    rows = rfq.get(str(deal_id)) or []
    if rows:
        out.append(f"\n## ЗАПРОСЫ ПОСТАВЩИКАМ ({len(rows)})")
        for r in rows[:40]:
            out.append(f"  [{r.get('stage')}] {r.get('supplier') or '—'} · {str(r.get('title'))[:70]} · {str(r.get('created'))[:10]}")
    else:
        out.append("\n## ЗАПРОСЫ ПОСТАВЩИКАМ: НИ ОДНОГО")

    ccon = _con(chats)
    if ccon:
        msgs = ccon.execute("SELECT at, author, text FROM messages WHERE deal_id=? ORDER BY at", (deal_id,)).fetchall()
        if msgs:
            out.append(f"\n## ЧАТ СДЕЛКИ ({len(msgs)} сообщений)")
            used = 0
            for at, au, t in msgs:
                line = f"  [{str(at)[:16]}] #{au}: {t}"
                out.append(line[:1500])
                used += len(line)
                if used > CHAT_BUDGET:
                    out.append(f"  … ещё {len(msgs)} сообщений обрезано")
                    break
        ccon.close()

    acon = _con(acts)
    if acon:
        rows2 = acon.execute("SELECT created, type, direction, subject, body FROM activities WHERE deal_id=? ORDER BY created",
                             (deal_id,)).fetchall()
        mail = [r for r in rows2 if r[1] == "письмо"]
        if mail:
            out.append(f"\n## ПЕРЕПИСКА ({len(mail)} писем)")
            used = 0
            for cr, _t, d, subj, body in mail:
                out.append(f"  [{str(cr)[:16]} {'исх' if str(d) == '2' else 'вх'}] {subj}\n    {str(body)[:1200]}")
                used += len(str(body)[:1200])
                if used > ACT_BUDGET:
                    break
        acon.close()

    files = con.execute("""SELECT f.filename, f.ext, f.chars, group_concat(t.text,'')
                           FROM files f LEFT JOIN file_text t ON t.fid=f.fid
                           WHERE f.deal_id=? GROUP BY f.fid ORDER BY f.chars DESC""", (deal_id,)).fetchall()
    if files:
        out.append(f"\n## ВЛОЖЕНИЯ ({len(files)})")
        used = 0
        for nm, ext, ch, txt in files:
            out.append(f"  --- {nm} ({ext}, {ch or 0} знаков)")
            if txt:
                take = min(6000, max(0, FILE_BUDGET - used))
                if take <= 0:
                    out.append("  … остальные вложения обрезаны")
                    break
                out.append(txt[:take])
                used += take
    con.close()
    return "\n".join(x for x in out if x)


def load_rfq(path: Path) -> dict:
    """Запросы поставщикам, разложенные по сделкам."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    stages = raw.get("stages") or {}
    by: dict[str, list] = {}
    for it in raw.get("items") or []:
        p = it.get("parentId2")
        if not p:
            continue
        by.setdefault(str(int(p)), []).append({
            "stage": (stages.get(str(it.get("stageId"))) or {}).get("name", it.get("stageId")),
            "title": it.get("title"), "created": it.get("createdTime"),
            "supplier": it.get("ufCrm18Supplier") or it.get("companyId"),
        })
    return by


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--db", default=str(HERE / "kvant.db"))
    ap.add_argument("--chats", default=str(HERE / "kvant_chats.db"))
    ap.add_argument("--acts", default=str(HERE / "kvant_acts.db"))
    ap.add_argument("--rfq", default=str(HERE / "rfq_export.json"))
    ap.add_argument("--list", default=None, choices=["all", "won", "lost", "norfq"])
    a = ap.parse_args()
    if a.list:
        con = _con(Path(a.db))
        where = {"all": "", "won": "WHERE won=1", "lost": "WHERE won=0",
                 "norfq": ""}[a.list]
        print(" ".join(str(r[0]) for r in con.execute(f"SELECT id FROM deals {where} ORDER BY id")))
        return 0
    rfq = load_rfq(Path(a.rfq))
    for i in a.ids:
        print(dossier(Path(a.db), Path(a.chats), Path(a.acts), rfq, int(i)))
        print("\n" + "=" * 100 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
