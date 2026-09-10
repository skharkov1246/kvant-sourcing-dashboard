#!/usr/bin/env python3
"""Перенос переписки в чатах в общий полнотекстовый индекс.

Зачем. Чаты сделок собираются в отдельную базу kvant_chats.db, и в общий поиск
они не попадали вовсе: в индексе лежали вложения, дела и названия сделок, а
164 тысячи сообщений — 13,8 млн знаков живой переписки с заказчиками и
поставщиками — искать было нечем. На вопрос «что нам ответил поставщик по этой
позиции» система отвечать не умела.

Сообщения складываются в индекс не поштучно, а по сделке: чат читают целиком,
и отдельная реплика «ок, принято» без соседних бесполезна. Это заодно бережёт
индекс — одна запись вместо двухсот на сделку.

    python base/index_chats.py --db base/kvant.db --chats base/kvant_chats.db
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

CHUNK = 40_000          # столько же, сколько у вложений: длинные чаты режем на части


def run(db_path: str, chats_db: str) -> dict:
    main = sqlite3.connect(db_path, timeout=300)
    main.execute("PRAGMA busy_timeout=300000")
    chats = sqlite3.connect(f"file:{chats_db}?mode=ro", uri=True, timeout=300)

    by_deal: dict[int, list[str]] = defaultdict(list)
    # сообщение уже несёт свою сделку, join не нужен; порядок по времени важен —
    # переписку читают подряд, а не вразбивку
    sql = "SELECT deal_id, author, text FROM messages ORDER BY deal_id, at"
    for deal, who, text in chats.execute(sql):
        if not text or not str(deal).isdigit():
            continue
        by_deal[int(deal)].append(f"{who}: {text}" if who else str(text))

    main.execute("DELETE FROM search WHERE kind='chat'")
    n = chars = 0
    for deal, lines in by_deal.items():
        body = "\n".join(lines)
        chars += len(body)
        for i in range(0, len(body), CHUNK):
            main.execute("INSERT INTO search (body, kind, ref, deal_id) VALUES (?,'chat',?,?)",
                         (body[i:i + CHUNK], f"chat:{deal}", deal))
        n += 1
    main.commit()
    main.close()
    print(f"в индекс добавлено чатов по {n} сделкам, {chars/1e6:.1f} млн знаков", flush=True)
    return {"deals": n, "chars": chars}


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(here / "kvant.db"))
    ap.add_argument("--chats", default=str(here / "kvant_chats.db"))
    a = ap.parse_args()
    run(a.db, a.chats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
