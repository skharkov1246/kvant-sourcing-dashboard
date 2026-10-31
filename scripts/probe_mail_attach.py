#!/usr/bin/env python3
"""Зонд v64 «Вложения писем запросов»: читаются ли файлы, приложенные к исходящему письму поставщику, и
сколько в них строк — у ручных запросов строк нет ни в карточке, ни в «Request file».

ПОВОД (31.10.2026). Зонд v63: «Request file» — у 24 % карточек (ручные — 6 %); у 41 % ручных карточек
спецификация уходит ВЛОЖЕНИЕМ первого письма. Таблица «КП по когортам недель» считает такую карточку
одной строкой, пока вложение не прочитано. Зонд 25.09.2026 по письмам сделок: url из FILES отдаёт
вебхуку страницу входа, disk.file.get основного вебхука — ACCESS_DENIED; вебхук файлов
(BITRIX_FILES_WEBHOOK_URL, library/indexer.bx_файлов) заведён ровно для этого.

ЧТО МЕРИТ (выборка: первые исходящие письма с вложением у карточек СП-166 без «Request file» за
ОКНО_ДНЕЙ, до PROBE_LIMIT писем, по умолчанию 120):
  1. вебхук файлов задан ли, администратор ли его сотрудник;
  2. по каждому вложению — disk.file.get и disk.attachedObject.get вебхуком файлов (иначе основным):
     код ответа; DOWNLOAD_URL → закачка: вид по сигнатуре;
  3. разбор скачанного (gt/tools/bitrix_tkp.parse) и строки (reqlines.rows_count): распределение;
  4. итог для счёта по строкам: сколько строк дали бы эти карточки против «одной строки».
В журнал — только счётчики и доли (правило 17): ни номеров, ни имён файлов, ни адресов.

НАГРУЗКА: письма за окно ≈ 1 запрос на 50 писем (≈ 60–120) + карточки выборки ≈ 10 + на вложение
2 вызова Диска и закачка (≈ 3 × 150) — всего ≈ 600 при 1/с (≈ 10 мин); предел 900.
"""
from __future__ import annotations

import os
import statistics
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gt" / "tools"))

ОКНО_ДНЕЙ = 60
SPA = 166


def код(e: Exception) -> str:
    s = str(e)
    for k in ("ACCESS_DENIED", "NOT_FOUND", "ERROR_NOT_FOUND", "insufficient_scope", "QUERY_LIMIT_EXCEEDED"):
        if k.lower() in s.lower():
            return k
    return type(e).__name__


def сигнатура(b: bytes) -> str:
    for знак, имя in ((b"%PDF", "pdf"), (b"PK\x03\x04", "zip/xlsx/docx"), (b"\xd0\xcf\x11\xe0", "xls/doc"),
                      (b"\x89PNG", "png"), (b"\xff\xd8", "jpeg")):
        if b.startswith(знак):
            return имя
    г = b[:2000].lower()
    if b"<html" in г or b"<!doctype" in г:
        return "страница входа" if (b"auth" in г or b"login" in г) else "html"
    return "прочее"


def вложения(a: dict) -> list[str]:
    """Номера файлов вложений письма (FILES — список объектов {id, url})."""
    out = []
    for f in a.get("FILES") or []:
        if isinstance(f, dict) and f.get("id"):
            out.append(str(f["id"]))
    return out


def main() -> int:
    import requests

    import config
    import reqlines
    from bitrix_client import BitrixClient, сводка_нагрузки
    import bitrix_tkp

    bx = BitrixClient(config.Settings.load().bitrix_webhook_url)
    вф = os.environ.get("BITRIX_FILES_WEBHOOK_URL", "").strip()
    диск = bx
    if вф:
        диск = BitrixClient(вф)
        диск._throttle = bx._throttle                         # одна очередь частоты на процесс
    limit = int(os.environ.get("PROBE_LIMIT") or 120)
    since = (date.today() - timedelta(days=ОКНО_ДНЕЙ)).isoformat()
    print(f"ожидается: исходящие письма карточек СП-{SPA} с {since}, выборка ≤ {limit} писем с вложением; "
          f"ориентир ≈ 600 запросов, предел 900")
    print(f"1. вебхук файлов: {'задан' if вф else 'не задан — Диск спрашивается основным'}")
    if вф:
        try:
            print(f"   его сотрудник — администратор портала: {'да' if диск.call('user.admin') else 'нет'}")
        except Exception as e:                                      # noqa: BLE001
            print(f"   user.admin: {код(e)}")

    # исходящие письма за окно — первое по карточке
    acts: list[dict] = []
    last = 0
    while True:
        ch = bx.call("crm.activity.list", {
            "filter": {"OWNER_TYPE_ID": SPA, "PROVIDER_ID": "CRM_EMAIL", "DIRECTION": 2,
                       ">=CREATED": since + "T00:00:00", ">ID": last},
            "select": ["ID", "OWNER_ID", "FILES", "CREATED"], "order": {"ID": "ASC"}, "start": -1}) or []
        if not ch:
            break
        acts += ch
        last = int(ch[-1]["ID"])
        if len(ch) < 50:
            break
    первое: dict[str, dict] = {}
    for a in sorted(acts, key=lambda a: str(a.get("CREATED") or "")):
        первое.setdefault(str(a.get("OWNER_ID") or ""), a)
    с_влож = {o: a for o, a in первое.items() if вложения(a)}
    print(f"   исходящих писем {len(acts)}, карточек с письмом {len(первое)}, первое письмо с вложением {len(с_влож)}")

    # карточки без «Request file» — из кандидатов с вложением, равномерно по номеру
    канд = sorted(с_влож, key=lambda x: int(x or 0))
    канд = канд[:: max(1, len(канд) // (limit * 2))][: limit * 2]
    без_файла: list[str] = []
    for i in range(0, len(канд), 50):
        try:
            got = bx.call("crm.item.list", {"entityTypeId": SPA, "filter": {"@id": [int(x) for x in канд[i:i + 50]]},
                                            "select": ["id", reqlines.REQ_FIELD], "start": -1}) or {}
        except Exception as e:                                      # noqa: BLE001
            print(f"   ⚠ карточки не прочитаны: {код(e)}")
            continue
        for c in (got.get("items") or []) if isinstance(got, dict) else []:
            if not reqlines.file_ref(c):
                без_файла.append(str(c["id"]))
    выбор = без_файла[:limit]
    print(f"   из {len(канд)} кандидатов без «Request file» {len(без_файла)}; в выборке {len(выбор)} писем")

    # 2–3. вложения: Диск → DOWNLOAD_URL → закачка → разбор
    коды, виды, способы = Counter(), Counter(), Counter()
    строк: list[int] = []
    карт_строк: dict[str, int] = {}
    файлов_в_письме = Counter()
    for cid in выбор:
        a = с_влож[cid]
        ids = вложения(a)
        файлов_в_письме[min(len(ids), 5)] += 1
        n_card = 0
        for fid in ids[:3]:
            url = ""
            for метод in ("disk.file.get", "disk.attachedObject.get"):
                try:
                    r = диск.call(метод, {"id": fid}, retries=1) or {}
                    u = r.get("DOWNLOAD_URL") if isinstance(r, dict) else ""
                    коды[(метод, "ок" if u else "без ссылки")] += 1
                    if u:
                        url = u
                        break
                except Exception as e:                              # noqa: BLE001
                    коды[(метод, код(e))] += 1
            if not url:
                continue
            bx.before_request("файл")
            try:
                rr = requests.get(url, timeout=60)
            except Exception as e:                                  # noqa: BLE001
                виды[f"сеть: {type(e).__name__}"] += 1
                continue
            вид = сигнатура(rr.content) if rr.status_code == 200 else f"HTTP {rr.status_code}"
            виды[вид] += 1
            if rr.status_code != 200 or вид in ("страница входа", "html") or len(rr.content) < 200:
                continue
            name = bitrix_tkp.name_from(rr.headers.get("Content-Disposition", ""))
            try:
                rows, how = bitrix_tkp.parse(name or "", rr.content)
            except Exception as e:                                  # noqa: BLE001
                rows, how = [], f"ошибка {type(e).__name__}"
            способы[str(how).split(":")[0][:30]] += 1
            n = reqlines.rows_count(rows)
            if n > 0:
                строк.append(n)
                n_card += n
        if n_card:
            карт_строк[cid] = n_card
    print("\n2. ДИСК (вебхук файлов, иначе основной):")
    for (метод, к), n in sorted(коды.items()):
        print(f"   {метод:26s} {к:22s} {n:>4d}")
    print("   закачка: " + " · ".join(f"{k} {v}" for k, v in виды.most_common()))
    print("   вложений в письме: " + " · ".join(f"{k if k < 5 else '5+'} — {v}" for k, v in sorted(файлов_в_письме.items())))
    print("\n3. РАЗБОР ВЛОЖЕНИЙ: " + " · ".join(f"{k} {v}" for k, v in способы.most_common()))
    if строк:
        print(f"   строк в файле: n {len(строк)}, медиана {statistics.median(строк)}, макс {max(строк)}; по корзинам: "
              + " · ".join(f"{lbl} — {sum(1 for x in строк if lo < x <= hi)}"
                           for lo, hi, lbl in ((0, 1, "1"), (1, 5, "2–5"), (5, 20, "6–20"), (20, 100, "21–100"),
                                               (100, 1000, "101–1000"), (1000, 10 ** 9, "> 1000"))))
    print(f"\n4. ДЛЯ СЧЁТА ПО СТРОКАМ: карточек с разобранным вложением {len(карт_строк)} из {len(выбор)} "
          f"({round(len(карт_строк) / max(1, len(выбор)) * 100)} %); строк по вложениям {sum(карт_строк.values())} "
          f"против {len(карт_строк)} при «одна строка»")
    ok = len(карт_строк) / max(1, len(выбор))
    print(f"   вердикт «строки из вложений писем»: {'показать' if len(выбор) >= 30 and ok >= 0.8 else ('предварительно' if len(выбор) >= 10 and ok > 0 else 'скрыть')} · n {len(выбор)}")
    print("\n" + сводка_нагрузки())
    return 0


if __name__ == "__main__":
    sys.exit(main())
