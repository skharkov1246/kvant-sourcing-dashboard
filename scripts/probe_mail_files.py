"""Зонд: почему вложения писем Битрикса не скачиваются и каким путём их взять.

ЗАМЕР 25.09.2026: холостой разбор писем сделок (прогон 36087141464) и запись
писем лидов (36079859054) — 2 835 и 3 000+ вложений, у ВСЕХ состояние «не
скачался», закачек по ссылке ноль. Путь один — disk.file.get по номеру из FILES
дела, — и он не дал ни одной ссылки. Зонд 18.09.2026 по делам сделок видел то же:
ACCESS_DENIED, то есть у сотрудника вебхука нет прав на эти файлы Диска.
Прежде чем просить владельца о правах, надо знать, какие именно права и нет ли
пути в обход Диска — числом, а не догадкой.

ЧТО МЕРИТ (только агрегаты и коды ответа — правило 17: ни номеров, ни имён,
ни адресов, ни тем писем):
1. Кто вебхук: администратор ли он портала (user.admin) — от этого зависит,
   видит ли он чужие Диски.
2. Какие поля несёт объект FILES у письма: имена ключей и вид ссылки url (путь
   без значений параметров, только имена параметров).
3. По выборке писем каждой группы (лид, сделка, компания и контакт):
   - disk.file.get по id из FILES — код ответа;
   - disk.attachedObject.get по тому же id — код ответа (у привязанного файла
     права проверяет объект-владелец, то есть CRM, а не Диск);
   - совпадают ли id из FILES и из STORAGE_ELEMENT_IDS;
   - доля удачи у писем, где ответственный — сам сотрудник вебхука, против
     остальных (если права решают, разница будет резкой);
   - для удачных — ответил ли DOWNLOAD_URL файлом (код, не содержимое);
   - GET по url из FILES. Первый прогон зонда (36097961413) показал: url — это
     crm_show_file.php с параметром auth, права по нему проверяет дело CRM, а не
     Диск; disk.file.get при этом ACCESS_DENIED на всех 45 файлах, вебхук не
     администратор. Адрес несёт токен и в журнал не пишется.
4. Сколько хранилищ Диска видит вебхук по типу владельца (user, group, common).

НАГРУЗКА НА ПОРТАЛ: около 120 запросов через общий бюджет клиента
(bitrix_client.интервал_портала).
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from urllib.parse import parse_qsl, urlsplit

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitrix_client import BitrixClient, сводка_нагрузки  # noqa: E402

ГРУППЫ = {
    "лид (входящие)": {"TYPE_ID": 4, "OWNER_TYPE_ID": 1, "DIRECTION": 1},
    "сделка": {"TYPE_ID": 4, "OWNER_TYPE_ID": 2},
    "компания и контакт (входящие)": {"TYPE_ID": 4, "OWNER_TYPE_ID": [3, 4], "DIRECTION": 1},
}
ПОРТАЛ = ""
ФАЙЛОВ_НА_ГРУППУ = 3
АДРЕСОВ_НА_ГРУППУ = 10

#: Код отказа портала — заглавные и подчёркивания; описание рядом может нести
#: номер файла, поэтому в журнал идёт только код.
КОД = re.compile(r"[A-Z][A-Z0-9_]{2,59}")


def код(e: Exception) -> str:
    m = КОД.search(str(e))
    return m.group(0) if m else type(e).__name__


def объекты(v) -> list[dict]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        вложенные = [x for x in v.values() if isinstance(x, dict)]
        return вложенные if вложенные and len(вложенные) == len(v) else [v]
    return []


def вид_ссылки(u: str) -> str:
    """Путь ссылки и имена параметров — без хоста и значений."""
    try:
        ч = urlsplit(u)
    except ValueError:
        return "не разбирается"
    имена = sorted({k for k, _ in parse_qsl(ч.query, keep_blank_values=True)})
    return f"{ч.path} ?{','.join(имена)}"


def попытка(bx: BitrixClient, метод: str, fid: str) -> tuple[str, str]:
    """(код ответа, ссылка на скачивание или '')."""
    try:
        r = bx.call(метод, {"id": fid}, retries=1) or {}
    except Exception as e:                                          # noqa: BLE001
        return код(e), ""
    if isinstance(r, dict):
        u = r.get("DOWNLOAD_URL") or ""
        return ("ок" if u else "ответ без ссылки"), u
    return "ответ не словарь", ""


def сигнатура(b: bytes) -> str:
    """Что пришло по первым байтам — без содержимого."""
    for знак, имя in ((b"%PDF", "pdf"), (b"PK\x03\x04", "zip/xlsx/docx"),
                      (b"\xd0\xcf\x11\xe0", "ole2 (xls/doc)"), (b"\x89PNG", "png"),
                      (b"\xff\xd8", "jpeg")):
        if b.startswith(знак):
            return имя
    голова = b[:2000].lower()
    if b"<html" in голова or b"<!doctype" in голова:
        return "страница входа" if (b"auth" in голова or b"login" in голова) else "html"
    return "прочее"


def закачка_ссылки(bx: BitrixClient, u: str) -> str:
    """GET по url из FILES. Адрес несёт токен auth — в журнал не идёт никогда."""
    bx.before_request("файл")
    try:
        r = requests.get(u, timeout=60, allow_redirects=True)
    except Exception as e:                                          # noqa: BLE001
        return f"сеть: {type(e).__name__}"
    тип = (r.headers.get("content-type") or "").split(";")[0]
    return (f"код {r.status_code} · {тип} · {сигнатура(r.content)} · "
            f"{'больше' if len(r.content) > 2000 else 'меньше'} 2 КБ"
            f"{' · имя в заголовке' if 'filename' in (r.headers.get('content-disposition') or '') else ''}")


def закачка(bx: BitrixClient, u: str) -> str:
    bx.before_request("файл")
    try:
        r = requests.get(u, timeout=60)
    except Exception as e:                                          # noqa: BLE001
        return f"сеть: {type(e).__name__}"
    тип = (r.headers.get("content-type") or "").split(";")[0]
    return f"код {r.status_code} · {тип} · {'больше' if len(r.content) > 2000 else 'меньше'} 2 КБ"


def main() -> int:
    global ПОРТАЛ
    bx = BitrixClient(os.environ["BITRIX_WEBHOOK_URL"])
    ч = urlsplit(os.environ["BITRIX_WEBHOOK_URL"])
    ПОРТАЛ = f"{ч.scheme}://{ч.netloc}"

    print("== ВЕБХУК ==")
    try:
        я = bx.call("user.current") or {}
        мой = str(я.get("ID") or "")
    except Exception as e:                                          # noqa: BLE001
        print(f"user.current: {код(e)}")
        мой = ""
    try:
        print(f"администратор портала: {'да' if bx.call('user.admin') else 'нет'}")
    except Exception as e:                                          # noqa: BLE001
        print(f"user.admin: {код(e)}")
    try:
        права = bx.call("scope") or []
        print(f"права вебхука (scope): {', '.join(sorted(права))}")
    except Exception as e:                                          # noqa: BLE001
        print(f"scope: {код(e)}")

    print("\n== ХРАНИЛИЩА ДИСКА, ВИДИМЫЕ ВЕБХУКУ ==")
    try:
        хр = bx.list_paged("disk.storage.getlist", {}, max_items=2000)
        print(f"всего: {len(хр)} · по типу владельца: "
              f"{dict(Counter(str(h.get('ENTITY_TYPE')) for h in хр).most_common())}")
    except Exception as e:                                          # noqa: BLE001
        print(f"disk.storage.getlist: {код(e)}")

    for имя, фильтр in ГРУППЫ.items():
        print(f"\n== ПИСЬМА: {имя} ==")
        try:
            j = bx.call_envelope("crm.activity.list", {
                "filter": фильтр, "order": {"ID": "DESC"},
                "select": ["ID", "FILES", "STORAGE_TYPE_ID", "STORAGE_ELEMENT_IDS",
                           "RESPONSIBLE_ID", "AUTHOR_ID", "PROVIDER_ID"],
                "start": -1})
        except Exception as e:                                      # noqa: BLE001
            print(f"crm.activity.list: {код(e)}")
            continue
        письма = [p for p in ((j or {}).get("result") or []) if p.get("FILES")]
        print(f"писем с вложениями в выборке: {len(письма)}")
        if not письма:
            continue
        ключи, ссылки, хранение, провайдер = Counter(), Counter(), Counter(), Counter()
        совпадают = Counter()
        файлы: list[tuple[str, bool]] = []
        # Номер привязки из ссылки url (attachedId): у привязанного файла права
        # проверяет объект-владелец (дело CRM), а не Диск сотрудника.
        привязки: list[tuple[str, bool]] = []
        адреса: list[str] = []
        for p in письма:
            провайдер[str(p.get("PROVIDER_ID"))] += 1
            хранение[str(p.get("STORAGE_TYPE_ID"))] += 1
            ids_ф = []
            for o in объекты(p.get("FILES")):
                ключи[",".join(sorted(o))] += 1
                if o.get("url"):
                    ссылки[вид_ссылки(str(o["url"]))] += 1
                    адреса.append(str(o["url"]))
                    параметры = dict(parse_qsl(urlsplit(str(o["url"])).query))
                    for имя_п in ("attachedId", "ATTACHED_ID", "attached_id"):
                        if str(параметры.get(имя_п) or "").isdigit():
                            привязки.append((параметры[имя_п],
                                             str(p.get("RESPONSIBLE_ID") or "") == мой))
                            break
                n = str(o.get("id") or "")
                if n.isdigit():
                    ids_ф.append(n)
                    файлы.append((n, str(p.get("RESPONSIBLE_ID") or "") == мой))
            ids_х = [str(x) for x in (p.get("STORAGE_ELEMENT_IDS") or []) if str(x).isdigit()]
            совпадают["совпадают" if sorted(ids_ф) == sorted(ids_х)
                      else "нет STORAGE_ELEMENT_IDS" if not ids_х else "различаются"] += 1
        print(f"провайдер письма: {dict(провайдер.most_common())}")
        print(f"тип хранения (STORAGE_TYPE_ID): {dict(хранение.most_common())}")
        print(f"ключи объекта FILES: {dict(ключи.most_common())}")
        print(f"вид ссылки url в FILES: {dict(ссылки.most_common(3))}")
        print(f"id из FILES и из STORAGE_ELEMENT_IDS: {dict(совпадают.most_common())}")
        итоги = Counter()
        закачки = Counter()
        for fid, свой in файлы[:ФАЙЛОВ_НА_ГРУППУ]:
            чей = "ответственный — вебхук" if свой else "ответственный — другой"
            for метод in ("disk.file.get", "disk.attachedObject.get"):
                к, u = попытка(bx, метод, fid)
                итоги[(метод, чей, к)] += 1
                if u and закачки[метод] < 3:
                    закачки[метод] += 1
                    print(f"  закачка по DOWNLOAD_URL из {метод}: {закачка(bx, u)}")
        for aid, свой in привязки[:ФАЙЛОВ_НА_ГРУППУ]:
            чей = "ответственный — вебхук" if свой else "ответственный — другой"
            к, u = попытка(bx, "disk.attachedObject.get", aid)
            итоги[("attachedObject по attachedId", чей, к)] += 1
            if u and закачки["attachedId"] < 3:
                закачки["attachedId"] += 1
                print(f"  закачка по DOWNLOAD_URL привязки: {закачка(bx, u)}")
        print(f"номеров привязки в url: {len(привязки)}")
        # Ссылка url из FILES: crm_show_file.php с токеном auth — права по ней
        # проверяет CRM (дело), а не Диск сотрудника.
        ответы_url = Counter()
        for u in адреса[:АДРЕСОВ_НА_ГРУППУ]:
            if u.startswith("/"):
                u = ПОРТАЛ + u
            ответы_url[закачка_ссылки(bx, u)] += 1
        print(f"GET по url из FILES ({min(len(адреса), АДРЕСОВ_НА_ГРУППУ)} файлов):"
              f" {dict(ответы_url.most_common())}")
        print(f"ответы по {min(len(файлы), ФАЙЛОВ_НА_ГРУППУ)} файлам:")
        for (метод, чей, к), n in sorted(итоги.items()):
            print(f"  {метод:30s} {чей:26s} {к:28s} {n:>3d}")

    print("\n" + сводка_нагрузки())
    return 0


if __name__ == "__main__":
    sys.exit(main())
