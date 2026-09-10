#!/usr/bin/env python3
"""Зонд v35: где лежат файлы КП по запросам поставщикам (СП-166) и как их достать.

Владелец: «выгружай все файлы в рамках запросов поставщиков — проверь всё, что мы
получали». Прежде чем качать тысячи вложений, нужно точно знать ДВА факта:

  1) где у запроса СП-166 живут файлы — в полях записи, в комментариях таймлайна
     или во вложениях писем (activity);
  2) какой способ скачивания реально отдаёт файл, а не страницу входа.

Зонд v34 показал, что ссылки show_file.php отдают страницу входа при любой подстановке
ключа, а «рабочий» способ uf.php вернул 83 байта на всех двадцати файлах — то есть
заглушку, а не файл. Поэтому здесь проверяются все пути разом на небольшой выборке,
и по каждому печатается, что именно пришло: размер и сигнатура содержимого.

Ничего не скачивается массово и ничего коммерческого в лог не идёт: только имена полей,
типы файлов, размеры и признак «файл/не файл». Репозиторий публичный.
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bitrix_client import BitrixClient  # noqa: E402

SPA = 166
GOT_QUOTE = {"Selected", "Not Selected", "Price at Work"}
# сделки двух наших RFQ — Энергосети и НВН
OUR_DEALS = {"22566", "22564", "22568", "22016", "21926", "22292", "22282", "18016"}


def sniff(b: bytes) -> str:
    if not b:
        return "пусто"
    if b[:2] == b"PK":
        return "zip/xlsx/docx"
    if b[:4] == b"%PDF":
        return "pdf"
    if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "старый office"
    if b[:3] == b"\xff\xd8\xff" or b[:4] == b"\x89PNG":
        return "изображение"
    if b[:4] == b"Rar!" or b[:2] == b"\x1f\x8b" or b[:2] == b"7z":
        return "архив"
    head = b[:400].lower()
    if b"<html" in head or b"<!doctype" in head:
        return "HTML (скорее всего страница входа)"
    return "прочее"


def try_get(url: str, sess: requests.Session) -> tuple[str, int]:
    try:
        r = sess.get(url, timeout=60, allow_redirects=True)
    except Exception as e:
        return (f"ошибка сети: {type(e).__name__}", 0)
    if r.status_code != 200:
        return (f"http {r.status_code}", 0)
    return (sniff(r.content), len(r.content))


def main() -> None:
    wh = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not wh:
        print("нет BITRIX_WEBHOOK_URL", file=sys.stderr)
        sys.exit(1)
    bx = BitrixClient(wh)
    sess = requests.Session()
    base = wh.rstrip("/")

    print("=== Зонд v35: где файлы запросов поставщикам и чем их взять ===\n")

    # ---------------------------------------------------------------- права вебхука
    try:
        sc = bx.call("scope", {})
        print(f"скоупы вебхука ({len(sc)}): {', '.join(sorted(sc))}\n")
    except Exception as e:
        print(f"scope недоступен: {e}\n")

    # ---------------------------------------------------------------- поля СП-166
    file_fields: list[str] = []
    try:
        fl = bx.call("crm.item.fields", {"entityTypeId": SPA}) or {}
        fields = fl.get("fields") or {}
        for name, meta in fields.items():
            if str(meta.get("type")) == "file":
                file_fields.append(name)
        print(f"полей у СП-166: {len(fields)} · из них файловых: {len(file_fields)}")
        print(f"  файловые поля: {file_fields}\n")
    except Exception as e:
        print(f"crm.item.fields не отдал поля: {e}\n")

    # ---------------------------------------------------------------- выборка запросов с КП
    sel = ["id", "title", "stageId", "parentId2", "createdTime"] + file_fields
    items = bx.call("crm.item.list", {
        "entityTypeId": SPA, "order": {"id": "DESC"},
        "filter": {">=createdTime": "2026-07-01"},
        "select": sel, "start": -1}) or {}
    items = items.get("items", []) if isinstance(items, dict) else []
    stages = bx.spa_stages(SPA, 24)
    quoted = [i for i in items if stages.get(str(i.get("stageId")), str(i.get("stageId"))) in GOT_QUOTE]
    ours = [i for i in quoted if str(i.get("parentId2") or "") in OUR_DEALS]
    print(f"записей СП-166 с 01.07 в первой странице выборки: {len(items)} · с КП: {len(quoted)} · по нашим сделкам: {len(ours)}")

    sample = (ours + [i for i in quoted if i not in ours])[:12]
    print(f"в выборку зонда взято: {len(sample)} запросов\n")

    # ---------------------------------------------------------------- где лежат файлы
    found: list[dict] = []          # найденные файловые объекты
    src_count: Counter = Counter()

    for it in sample:
        iid = it["id"]
        # 1) файловые поля самой записи
        full = bx.call("crm.item.get", {"entityTypeId": SPA, "id": iid}) or {}
        item = (full.get("item") or {}) if isinstance(full, dict) else {}
        for fname, val in item.items():
            if not val or fname in ("id", "title"):
                continue
            vals = val if isinstance(val, list) else [val]
            for fo in vals:
                if isinstance(fo, dict) and (fo.get("id") or fo.get("ID")):
                    src_count["поле записи"] += 1
                    found.append({"src": f"поле {fname}", "item": iid, "fo": fo})

        # 2) комментарии таймлайна — пробуем разные написания типа сущности
        for ent in (SPA, f"DYNAMIC_{SPA}", "dynamic_166"):
            try:
                cs = bx.call("crm.timeline.comment.list", {
                    "filter": {"ENTITY_ID": iid, "ENTITY_TYPE": ent}, "select": ["ID", "COMMENT", "FILES"]}) or []
                for c in (cs if isinstance(cs, list) else []):
                    for fo in (c.get("FILES") or []):
                        src_count[f"комментарий ({ent})"] += 1
                        found.append({"src": f"комментарий {ent}", "item": iid, "fo": fo})
                if cs:
                    break
            except Exception:
                continue

        # 3) дела/письма таймлайна
        for otid in (SPA, 2):
            owner = iid if otid == SPA else it.get("parentId2")
            if not owner:
                continue
            try:
                acts = bx.call("crm.activity.list", {
                    "filter": {"OWNER_TYPE_ID": otid, "OWNER_ID": owner},
                    "select": ["ID", "PROVIDER_ID", "TYPE_ID", "SUBJECT", "FILES"],
                    "start": -1}) or []
                for a in (acts if isinstance(acts, list) else [])[:40]:
                    fs = a.get("FILES") or []
                    if isinstance(fs, dict):
                        fs = list(fs.values())
                    for fo in fs:
                        if isinstance(fo, dict):
                            src_count[f"activity owner={otid} ({a.get('PROVIDER_ID')})"] += 1
                            found.append({"src": f"activity{otid}", "item": iid, "fo": fo})
            except Exception as e:
                src_count[f"activity owner={otid}: ОШИБКА {type(e).__name__}"] += 1

    print("--- ГДЕ НАШЛИСЬ ФАЙЛОВЫЕ ОБЪЕКТЫ ---")
    if not src_count:
        print("  ни одного файлового объекта не найдено ни в полях, ни в таймлайне")
    for k, v in src_count.most_common():
        print(f"  {v:>4}  {k}")
    print(f"\nвсего объектов для проверки скачивания: {len(found)}\n")

    if found:
        keys = Counter()
        for f in found[:5]:
            keys.update(f["fo"].keys())
        print(f"ключи файлового объекта (по первым 5): {sorted(keys)}\n")

    # ---------------------------------------------------------------- чем скачать
    strategies: dict[str, Counter] = {}
    sizes: dict[str, list[int]] = {}

    def note(name: str, res: tuple[str, int]) -> None:
        strategies.setdefault(name, Counter())[res[0]] += 1
        sizes.setdefault(name, []).append(res[1])

    for f in found[:25]:
        fo = f["fo"]
        fid = fo.get("id") or fo.get("ID") or fo.get("fileId")
        for key in ("urlMachine", "downloadUrl", "url", "URL_MACHINE", "DOWNLOAD_URL", "viewUrl"):
            u = fo.get(key)
            if u:
                u = str(u)
                if u.startswith("/"):
                    host = base.split("/rest/")[0]
                    u = host + u
                note(f"объект.{key}", try_get(u, sess))
        if fid:
            try:
                df = bx.call("disk.file.get", {"id": fid}) or {}
                dl = df.get("DOWNLOAD_URL") if isinstance(df, dict) else None
                note("disk.file.get → DOWNLOAD_URL", try_get(str(dl), sess) if dl else ("метод не дал ссылки", 0))
            except Exception as e:
                note("disk.file.get → DOWNLOAD_URL", (f"ошибка REST: {type(e).__name__}", 0))
            try:
                ext = bx.call("disk.file.getExternalLink", {"id": fid})
                note("disk.file.getExternalLink", try_get(str(ext), sess) if ext else ("метод не дал ссылки", 0))
            except Exception as e:
                note("disk.file.getExternalLink", (f"ошибка REST: {type(e).__name__}", 0))
            note("rest/download?token", try_get(f"{base}/download.json?id={fid}", sess))

    print("--- ЧТО ОТВЕТИЛ КАЖДЫЙ СПОСОБ СКАЧИВАНИЯ ---")
    if not strategies:
        print("  нечего было качать")
    for name, c in strategies.items():
        good = sum(n for k, n in c.items() if k in ("zip/xlsx/docx", "pdf", "старый office", "изображение", "архив"))
        sz = [s for s in sizes[name] if s]
        med = sorted(sz)[len(sz) // 2] if sz else 0
        mark = "  ✔ ГОДИТСЯ" if good else ""
        print(f"  {name:34} файлов {good:>3} из {sum(c.values()):>3} · медиана {med:>8} б · {dict(c)}{mark}")

    print("\n✓ зонд v35 завершён")


if __name__ == "__main__":
    main()
