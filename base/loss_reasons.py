#!/usr/bin/env python3
"""Сюжеты провала по ТЕКСТАМ — сплошной проход по всем сделкам, без выборки.

Стадия в Битриксе говорит только «не прошли по цене» или «поставщик не ответил».
Настоящая причина написана в переписке и во вложениях. Здесь она размечается
правилами по ВСЕМУ корпусу: 100 % покрытие и воспроизводимость. Агенты потом
проверяют разметку и разбирают то, что правила не поняли, — но не наоборот:
выборка в 60 сделок из 2 928 не даёт долей, на которые можно опираться.

Правила намеренно консервативные: признак срабатывает только на явных
формулировках, поэтому доля «не размечено» честно показывает предел метода.

    python base/loss_reasons.py --db base/kvant.db --acts base/kvant_acts.db
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

# Сюжет → выражения. Порядок важен: сделка получает все сработавшие сюжеты,
# но в сводке считается и «главный» — тот, у кого больше всего попаданий.
NARRATIVES: dict[str, tuple[str, list[str]]] = {
    "no_supplier": ("Не нашли изготовителя / нет канала", [
        r"не (?:смогли|удалось) найти (?:поставщик|производител|изготовител)",
        r"нет (?:возможности|канала) поставк", r"снят[оы] с производств",
        r"производство прекращен", r"discontinued", r"no longer (?:available|produced)",
        r"не производит", r"аналог не найден", r"замена не подобран",
    ]),
    "sanctions": ("Санкции и отказ от поставки в РФ", [
        r"санкц", r"экспортн\w+ контрол", r"export control", r"не поставля\w+ в росси",
        r"отказ\w* (?:от )?поставк\w* в (?:рф|росси)", r"embargo", r"эмбарго",
        r"комплаенс", r"compliance (?:check|policy)", r"dual[- ]use", r"двойного назначения",
        r"eccn", r"конечн\w+ пользовател", r"end[- ]user (?:certificate|statement)",
    ]),
    "no_answer": ("Поставщик не ответил", [
        r"не ответил", r"ответа не последовал", r"без ответа", r"нет ответа от",
        r"no (?:response|reply)", r"не выходит на связь", r"игнорир\w+ запрос",
        r"повторн\w+ запрос", r"напомина\w+ о запросе",
    ]),
    "price": ("Проиграли по цене", [
        r"не прошли по цене", r"дорого", r"выше (?:бюджета|рынка|конкурент)",
        r"цена (?:выше|не устраивает|неконкурент)", r"снизить цену", r"переторжк",
        r"предложил\w* дешевле", r"дешевле на \d+", r"price (?:too high|not competitive)",
    ]),
    "lead_time": ("Не прошли по срокам", [
        r"срок(?:и)? (?:поставки )?(?:не устраива|слишком|велик|длинн)",
        r"не укладываемся в срок", r"lead time", r"срок изготовлен\w+ \d+ (?:недел|месяц)",
        r"не успева\w+ (?:к|до)", r"delivery time (?:too long|not acceptable)",
    ]),
    "tech": ("Не прошли по технике", [
        r"не соответству\w+ (?:т[зт]|техническ|требован)", r"техническ\w+ несоответств",
        r"отклонен\w* по техническ", r"не проход\w+ по техническ", r"аналог не допус",
        r"только оригинал", r"оригинальн\w+ запчаст", r"оем только", r"strictly oem",
    ]),
    "docs": ("Документы, сертификация, допуски", [
        r"сертификат", r"деклараци\w+ соответств", r"паспорт качеств", r"ростехнадзор",
        r"разрешение на применение", r"нет документ", r"не предоставил\w* документ",
        r"тр тс", r"еак", r"гост р", r"аттестац",
    ]),
    "customer_cancel": ("Заказчик отменил или перенёс", [
        r"отмен\w+ закупк", r"закупка отменен", r"аннулирован", r"перенес\w+ на",
        r"снят\w* с торгов", r"процедура не состоял", r"cancelled", r"отложен\w* до",
    ]),
    "no_techinfo": ("Заказчик не дал техническую информацию", [
        r"не предоставил\w* (?:чертеж|тех|исходн|опросн)", r"нет чертеж",
        r"недостаточно (?:данных|информации)", r"уточнит\w+ (?:тех|характеристик)",
        r"запрос\w* дополнительн\w+ информаци", r"без специфик",
    ]),
    "competitor": ("Проиграли конкуренту", [
        r"победител\w+ (?:стал|признан)", r"выиграл\w* (?:другой|конкурент|компания)",
        r"заключ\w+ (?:договор|контракт) с друг", r"выбран\w* друг\w+ поставщик",
        r"проиграли (?:конкурс|тендер)", r"protokol|протокол подведения итогов",
    ]),
    "payment_terms": ("Условия оплаты и финансы", [
        r"предоплат\w+ 100", r"аккредитив", r"отсрочк\w+ платеж", r"условия оплаты не",
        r"банковск\w+ гаранти", r"не готовы работать по предоплате", r"payment terms",
    ]),
    "logistics": ("Логистика и таможня", [
        r"логистик\w+ (?:дорог|невозможн)", r"таможн", r"тн вэд", r"параллельн\w+ импорт",
        r"через третьи страны", r"доставка невозможн", r"перевозчик отказ",
    ]),
}

COMPILED = {k: (title, [re.compile(p, re.I) for p in pats]) for k, (title, pats) in NARRATIVES.items()}
MAX_TEXT = 300_000     # больше на одну сделку не разбираем: дальше идут повторы цитат


def texts_for(con: sqlite3.Connection, acon: sqlite3.Connection | None, deal_id: int) -> str:
    parts = []
    for (t,) in con.execute(
            "SELECT t.text FROM file_text t JOIN files f ON f.fid=t.fid WHERE f.deal_id=?", (deal_id,)):
        parts.append(t or "")
        if sum(len(x) for x in parts) > MAX_TEXT:
            break
    src = acon or con
    for subj, body in src.execute("SELECT subject, body FROM activities WHERE deal_id=?", (deal_id,)):
        parts.append(f"{subj or ''}\n{body or ''}")
        if sum(len(x) for x in parts) > MAX_TEXT:
            break
    return "\n".join(parts)[:MAX_TEXT]


def mark(text: str) -> dict[str, int]:
    """Сколько раз сработал каждый сюжет. Пустой словарь — правила ничего не увидели."""
    hits: dict[str, int] = {}
    if not text:
        return hits
    for key, (_title, pats) in COMPILED.items():
        n = sum(len(p.findall(text)) for p in pats)
        if n:
            hits[key] = n
    return hits


def run(db: str, acts_db: str | None, only_lost: bool = False) -> dict:
    con = sqlite3.connect(db, timeout=300)
    con.execute("PRAGMA busy_timeout=300000")
    acon = None
    if acts_db and Path(acts_db).exists():
        acon = sqlite3.connect(acts_db, timeout=300)
        acon.execute("PRAGMA busy_timeout=300000")

    con.execute("""CREATE TABLE IF NOT EXISTS loss_marks (
        deal_id INTEGER, narrative TEXT, hits INTEGER, main INTEGER, chars INTEGER)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_lm_deal ON loss_marks(deal_id)")
    con.execute("DELETE FROM loss_marks")

    where = "WHERE won=0 AND semantic='F'" if only_lost else ""
    deals = con.execute(f"SELECT id, won, semantic, origin_cat, stage, sum_eur FROM deals {where}").fetchall()

    stats = Counter()
    by_narr = Counter()
    by_narr_won = defaultdict(lambda: [0, 0])
    rows = []
    for did, won, sem, _cat, _stage, _sm in deals:
        text = texts_for(con, acon, did)
        stats["всего сделок"] += 1
        if not text:
            stats["без текстов"] += 1
            continue
        stats["с текстами"] += 1
        hits = mark(text)
        if not hits:
            stats["текст есть, сюжет не распознан"] += 1
            continue
        stats["размечено"] += 1
        main = max(hits, key=lambda k: hits[k])
        for k, n in hits.items():
            rows.append((did, k, n, 1 if k == main else 0, len(text)))
            by_narr[k] += 1
            by_narr_won[k][0 if won else 1] += 1
    con.executemany("INSERT INTO loss_marks VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()
    if acon:
        acon.close()
    return {"stats": dict(stats), "by_narrative": dict(by_narr),
            "won_lost": {k: {"won": v[0], "lost": v[1]} for k, v in by_narr_won.items()}}


def main() -> int:
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--db", default=str(here / "kvant.db"))
    ap.add_argument("--acts", default=str(here / "kvant_acts.db"))
    ap.add_argument("--only-lost", action="store_true")
    a = ap.parse_args()
    res = run(a.db, a.acts, a.only_lost)
    print("покрытие:", res["stats"])
    print("\nсюжеты (сколько сделок):")
    for k, n in sorted(res["by_narrative"].items(), key=lambda x: -x[1]):
        wl = res["won_lost"][k]
        print(f"  {NARRATIVES[k][0]:<42} {n:>5}  (выиграно {wl['won']}, проиграно {wl['lost']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
