"""Зонд v29: где лежит номенклатура — разведка вложений сделок.

Владелец: номенклатура не в строках Битрикса, а в файлах, привязанных к сделкам.
Прежде чем разбирать их все, нужно понять устройство: какие файловые поля есть,
сколько сделок с файлами, какие форматы, и — главное — хватает ли вебхуку прав,
чтобы файлы скачать (скоуп «Диск»). Без этого дальнейшая работа бессмысленна.

ПЕЧАТАЮТСЯ ТОЛЬКО АГРЕГАТЫ. Имена файлов, названия сделок и содержимое не выводятся:
репозиторий публичный. Из разобранных файлов показывается лишь распределение по
сегментам оборудования и число распознанных строк.
"""
from __future__ import annotations

import io
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

BASE = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
SAMPLE_FILES = 40          # сколько файлов пробуем скачать в разведке


def bx(method: str, params: dict) -> dict:
    for _ in range(4):
        try:
            r = requests.post(f"{BASE}/{method}.json", json=params, timeout=90)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return {}


def bx_all(method: str, params: dict, cap: int = 100000) -> list:
    out, start = [], 0
    while True:
        j = bx(method, {**params, "start": start})
        res = j.get("result")
        items = res.get("items") if isinstance(res, dict) and "items" in res else res
        out += items or []
        if "next" not in j or len(out) >= cap:
            return out
        start = j["next"]


def bx_batch(cmds: dict) -> dict:
    for _ in range(4):
        try:
            r = requests.post(f"{BASE}/batch.json", json={"halt": 0, "cmd": cmds}, timeout=120)
            r.raise_for_status()
            j = r.json()
            return {"res": (j.get("result") or {}).get("result") or {},
                    "err": (j.get("result") or {}).get("result_error") or {}}
        except Exception:
            continue
    return {"res": {}, "err": {}}


def main() -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00+03:00")
    print(f"=== Зонд v29: вложения сделок с {since[:10]} ===\n")

    # 1. Какие вообще права у вебхука
    sc = bx("scope", {})
    scopes = sorted(sc.get("result") or [])
    print(f"права вебхука ({len(scopes)}): {', '.join(scopes)}")
    print(f"  скоуп «disk»: {'ЕСТЬ' if 'disk' in scopes else 'НЕТ — файлы скачать не выйдет'}")
    print(f"  скоуп «crm» : {'есть' if 'crm' in scopes else 'НЕТ'}\n")

    # 2. Файловые пользовательские поля сделок
    uf = bx("crm.deal.userfield.list", {"order": {"FIELD_NAME": "ASC"}}).get("result") or []
    types = Counter(str(u.get("USER_TYPE_ID")) for u in uf)
    file_fields = [str(u["FIELD_NAME"]) for u in uf if u.get("USER_TYPE_ID") == "file"]
    print(f"пользовательских полей сделки: {len(uf)} · из них файловых: {len(file_fields)}")
    print(f"  типы полей: {dict(types.most_common(12))}")
    print(f"  файловые поля: {file_fields}\n")

    # 3. Справочник стадий: без него выигрыш не определить — стадии в портале свои
    # (C2, C4, C8, C10, C30 с кодами UC_*), а SEMANTICS отвечает, чем стадия является:
    # S — успех, F — провал, P — в работе.
    sem: dict[str, str] = {}
    ent = bx("crm.status.entity.items", {"entityId": "DEAL_STAGE"}).get("result") or []
    cats = bx("crm.dealcategory.list", {"select": ["ID", "NAME"]}).get("result") or []
    entities = ["DEAL_STAGE"] + [f"DEAL_STAGE_{c['ID']}" for c in cats]
    for e in entities:
        for it in bx("crm.status.list", {"filter": {"ENTITY_ID": e}}).get("result") or []:
            sem[str(it.get("STATUS_ID"))] = str(it.get("SEMANTICS") or "P")
    print(f"направлений сделок: {len(cats)} · стадий в справочнике: {len(sem)}")
    print(f"  из них успешных: {sum(1 for v in sem.values() if v == 'S')} · "
          f"провальных: {sum(1 for v in sem.values() if v == 'F')} · "
          f"в работе: {sum(1 for v in sem.values() if v not in ('S', 'F'))}")
    print(f"  (пример разбора первых элементов справочника получен: {len(ent)} записей)\n")

    # 4. Сделки за период
    deals = bx_all("crm.deal.list", {"filter": {">=DATE_CREATE": since},
                                     "select": ["ID", "STAGE_ID", "OPPORTUNITY", "CATEGORY_ID"],
                                     "order": {"ID": "ASC"}})
    ids = [str(d["ID"]) for d in deals]
    print(f"сделок за период: {len(ids)}\n")

    won = lost = work = unknown = 0
    won_sum = lost_sum = 0.0
    for d in deals:
        st = str(d.get("STAGE_ID") or "")
        amount = float(d.get("OPPORTUNITY") or 0)
        s_ = sem.get(st)
        if s_ == "S":
            won += 1
            won_sum += amount
        elif s_ == "F":
            lost += 1
            lost_sum += amount
        elif s_ is None:
            unknown += 1
        else:
            work += 1
    closed = won + lost
    print("=== ИСХОДЫ ПО СПРАВОЧНИКУ СТАДИЙ ===")
    print(f"  выиграно: {won:>5d} на {won_sum / 1e6:>10.1f} млн")
    print(f"  проиграно:{lost:>5d} на {lost_sum / 1e6:>10.1f} млн")
    print(f"  в работе: {work:>5d} · стадия не найдена в справочнике: {unknown}")
    if closed:
        print(f"  доля выигранных среди закрытых: {won / closed * 100:.1f}% "
              f"· по деньгам: {won_sum / max(won_sum + lost_sum, 1) * 100:.1f}%\n")

    # 5. Сколько сделок с файлами в UF-полях и какие форматы
    per_field = Counter()
    fmt = Counter()
    with_files = 0
    file_refs: list[dict] = []
    if file_fields:
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            j = bx("crm.deal.list", {"filter": {"ID": chunk},
                                     "select": ["ID"] + file_fields})
            for x in j.get("result") or []:
                got = False
                for f in file_fields:
                    v = x.get(f)
                    if not v:
                        continue
                    files = v if isinstance(v, list) else [v]
                    for fo in files:
                        if not isinstance(fo, dict) or "id" not in fo:
                            continue
                        got = True
                        per_field[f] += 1
                        name = str(fo.get("fileName") or "")
                        ext = name.rsplit(".", 1)[-1].lower() if "." in name else "без расширения"
                        fmt[ext] += 1
                        file_refs.append({"deal": str(x["ID"]), "id": fo["id"], "ext": ext})
                if got:
                    with_files += 1
    print(f"сделок с файлами в UF-полях: {with_files} из {len(ids)} "
          f"({with_files / max(len(ids), 1) * 100:.1f}%) · файлов всего: {len(file_refs)}")
    if per_field:
        print("  по полям:", dict(per_field.most_common()))
    if fmt:
        print("  форматы:", dict(fmt.most_common(15)))
    print()

    # 6. Вложения в делах/письмах таймлайна — сюда обычно попадают спецификации из писем
    act_files = Counter()
    act_total = 0
    probe_ids = ids[-300:]
    for i in range(0, len(probe_ids), 50):
        chunk = probe_ids[i:i + 50]
        cmds = {d: (f"crm.activity.list?filter[OWNER_TYPE_ID]=2&filter[OWNER_ID]={d}"
                    f"&select[]=ID&select[]=TYPE_ID&select[]=PROVIDER_ID&select[]=FILES") for d in chunk}
        out = bx_batch(cmds)
        for _did, acts in (out["res"] or {}).items():
            for a in acts or []:
                act_total += 1
                fl = a.get("FILES")
                if fl:
                    n = len(fl) if isinstance(fl, (list, dict)) else 1
                    act_files[str(a.get("PROVIDER_ID") or a.get("TYPE_ID"))] += n
    print(f"дел и писем в таймлайне (выборка {len(probe_ids)} свежих сделок): {act_total}")
    print(f"  из них с вложениями, по источнику: {dict(act_files.most_common(10))}")
    print(f"  вложений всего в выборке: {sum(act_files.values())}\n")

    # 7. Пробуем скачать и разобрать выборку файлов
    print(f"=== пробное скачивание {min(SAMPLE_FILES, len(file_refs))} файлов ===")
    ok = fail = parsed = 0
    rows_total = 0
    why: Counter = Counter()
    for ref in file_refs[:SAMPLE_FILES]:
        content = None
        try:
            df = bx("disk.file.get", {"id": ref["id"]})
            url = (df.get("result") or {}).get("DOWNLOAD_URL")
            if not url:
                why[str(df.get("error") or "нет DOWNLOAD_URL")] += 1
            else:
                rr = requests.get(url, timeout=60)
                if rr.status_code == 200 and len(rr.content) > 100:
                    content = rr.content
                else:
                    why[f"http {rr.status_code}"] += 1
        except Exception as e:
            why[type(e).__name__] += 1
        if content is None:
            fail += 1
            continue
        ok += 1
        if ref["ext"] in ("xlsx", "xlsm"):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
                n = 0
                for ws in wb.worksheets:
                    for _ in ws.iter_rows(values_only=True):
                        n += 1
                rows_total += n
                parsed += 1
            except Exception as e:
                why[f"xlsx: {type(e).__name__}"] += 1
    print(f"скачано: {ok} · не удалось: {fail} · разобрано таблиц: {parsed} · строк в них: {rows_total}")
    if why:
        print("  причины отказов:", dict(why.most_common(8)))

    print("\n✓ зонд v29 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
