#!/usr/bin/env python3
"""Зонд v63 «Строки запроса»: сколько строк в запросе поставщику и откуда их взять — до того, как
доля КП на дашборде станет считаться по строкам, а не по карточкам.

ВОПРОС ВЛАДЕЛЬЦА (31.10.2026). «Нужен срез по строкам запроса. Нельзя оценить 5 запросов одинаково.
Есть запросы по 1000 строк, а есть по одной». Таблица «КП по когортам недель» (PR #554) считает
карточки СП-166 поровну; нужна доля СТРОК.

ГДЕ СТРОКИ. Товарных строк нет ни у сделок пресейла (зонд v49: 0 из 95), ни у заказов; числового поля
«позиций» у карточки нет. Позиции лежат в файле «Request file» карточки (ufCrm18_1727423346 — наш
исходящий запрос, gt/tools/bitrix_tkp.RFQ_FILE_FIELDS). Зонд меряет, годится ли он опорой:

  1. заполненность «Request file» у карточек окна, файлов на карточку, вид файла (по сигнатуре);
  2. товарные строки самой карточки (crm.item.productrow.list, ownerType «Ta6») — на выборке;
  3. разбор файлов выборки сделок (все карточки сделки — чтобы видеть, один ли файл ушёл нескольким
     поставщикам): скачано, разобрано, строк в файле (непустых и «с артикулом»), распределение;
  4. один файл на несколько карточек сделки (по хэшу содержимого) — сколько поставщиков на строку;
  5. сквозной счёт по строкам (решение владельца 31.10.2026): строка запроса — единица; файл, ушедший
     нескольким поставщикам сделки, — его строки один раз; строка расценена, если КП дал хотя бы один
     поставщик (карточка с этим файлом на стадии КП); рядом — доля по карточкам тех же файлов;
  6. строк на заявку (сделку): сумма строк разных файлов сделки — распределение.
Каждый раздел — «вердикт: показать / предварительно / скрыть» (n ≥ 30 и покрытие ≥ 80 % — показать).

ВЫБОРКА. Карточки воронки 24, заведённые за ОКНО_ДНЕЙ; сделки — каждая k-я по номеру, пока не набрано
PROBE_LIMIT файлов (по умолчанию 250). ЧАСТИ — вход PROBE_PARTS (меньше 10 — 10), по номеру карточки
смежными срезами; упавшая часть выпадает из выводов вслух.

ЖУРНАЛ ПУБЛИЧНЫЙ (правило 17): только счётчики, доли, медианы, коды полей. Ни номеров, ни названий
файлов, ни содержимого строк. Файлы не сохраняются.

НАГРУЗКА: crm.item.fields 1 + crm.item.list ≈ карточки/50 (≈ 60–80) + товарные строки — пакет по 50
(≈ 2–4) + закачки файлов ≤ PROBE_LIMIT через очередь клиента (≈ 250); всего ≈ 350 при 1/с (≈ 6–8 мин).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib import parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "gt" / "tools"))

SPA = 166
REQ_FIELD = "ufCrm18_1727423346"          # «Request file» — наш исходящий запрос
ОКНО_ДНЕЙ = 120
ЗРЕЛОСТЬ_ДНЕЙ = 30
КОРЗИНЫ = [(1, "1"), (5, "2–5"), (20, "6–20"), (100, "21–100"), (1000, "101–1000"), (10 ** 9, "> 1000")]


def корзина(n: int) -> str:
    for hi, lbl in КОРЗИНЫ:
        if n <= hi:
            return lbl
    return КОРЗИНЫ[-1][1]


def вердикт(n: int, покрытие: float) -> str:
    return "показать" if n >= 30 and покрытие >= 0.8 else ("предварительно" if n >= 10 else "скрыть")


def файлы_поля(v) -> list[dict]:
    """Значение файлового поля crm.item.list: объект или список объектов с urlMachine."""
    if not v:
        return []
    items = v if isinstance(v, list) else [v]
    return [x for x in items if isinstance(x, dict) and (x.get("urlMachine") or x.get("url"))]


def вид(content: bytes) -> str:
    if content[:4] == b"%PDF":
        return "pdf"
    if content[:2] == b"PK":
        return "xlsx/zip"
    if content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xls/doc"
    return "прочее"


def строки(rows: list) -> tuple[int, int]:
    """(непустых строк, строк с артикулом) из разбора bitrix_tkp.parse: [(лист, №, ячейки)]."""
    import bitrix_tkp
    непуст, с_арт = 0, 0
    for _sheet, _n, cells in rows:
        vals = [str(c).strip() for c in (cells or []) if c is not None and str(c).strip()]
        if not vals:
            continue
        непуст += 1
        if bitrix_tkp.find_pns(" ".join(vals)):
            с_арт += 1
    return непуст, с_арт


def части(ids: list[int], n: int) -> list[list[int]]:
    ids = sorted(ids)
    k = max(1, -(-len(ids) // n))
    return [ids[i:i + k] for i in range(0, len(ids), k)]


def текст_строк(html: str) -> tuple[int, int, int]:
    """(непустых строк, строк таблицы <tr>, строк с артикулом) текста письма или поля: HTML → строки."""
    import re
    import bitrix_tkp
    s = str(html or "")
    tr = len(re.findall(r"(?i)<tr\b", s))
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    lines = [x.strip() for x in s.splitlines() if x.strip()]
    return len(lines), tr, sum(1 for x in lines if bitrix_tkp.find_pns(x))


def без_файла(client, cards: list[dict], поля: dict, bitrix_tkp) -> None:
    """7–10. Почему у карточек нет «Request file» и где у них позиции: доля с файлом по месяцам, по
    автору (робот / человек) и по исполнителю (без имён); другие файловые поля; текст названия и
    текстовых полей; исходящее письмо — вложение и строки в тексте. Только счётчики."""
    import config
    служ = set(config.SERVICE_ACCOUNT_IDS)
    есть = lambda c: bool(файлы_поля(c.get(REQ_FIELD)))           # noqa: E731
    # 7. доля с файлом: по месяцам, по автору, по исполнителю
    по_мес, по_автору, по_исп = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for c in cards:
        f = есть(c)
        m = str(c.get("createdTime") or "")[:7]
        a = "робот (служебная запись)" if str(c.get("createdBy") or "") in служ else "человек"
        for d, k in ((по_мес, m), (по_автору, a), (по_исп, str(c.get("assignedById") or ""))):
            d[k][0] += 1
            d[k][1] += f
    print("\n7. ДОЛЯ КАРТОЧЕК С «REQUEST FILE»:")
    print("    по месяцу заведения: " + " · ".join(f"{k} {v[1]}/{v[0]} ({round(v[1] / v[0] * 100)} %)"
                                                   for k, v in sorted(по_мес.items()) if v[0]))
    print("    по автору карточки: " + " · ".join(f"{k} {v[1]}/{v[0]} ({round(v[1] / v[0] * 100)} %)"
                                                  for k, v in по_автору.items() if v[0]))
    исп = sorted(по_исп.values(), key=lambda v: -v[0])[:12]
    print("    по исполнителю (без имён, по числу карточек): " + " · ".join(
        f"№{i + 1} {v[1]}/{v[0]} ({round(v[1] / v[0] * 100)} %)" for i, v in enumerate(исп)))

    # выборка карточек без файла — до 400, равномерно по номеру
    нет = [c for c in cards if not есть(c)]
    проба = нет[:: max(1, len(нет) // 400)][:400]
    ids = [str(c["id"]) for c in проба]
    file_f = [k for k, f in поля.items() if (f or {}).get("type") == "file" and k != REQ_FIELD]
    text_f = [k for k, f in поля.items() if (f or {}).get("type") in ("string", "text") and not (f or {}).get("isReadOnly")]
    подпись = lambda k: (поля.get(k) or {}).get("title") or (поля.get(k) or {}).get("listLabel") or k   # noqa: E731
    full: list[dict] = []
    for i in range(0, len(ids), 50):
        try:
            full += client.list_items(SPA, filter={"@id": [int(x) for x in ids[i:i + 50]]},
                                      select=["id", "title"] + file_f + text_f)
        except Exception as e:                                      # noqa: BLE001
            print(f"  ⚠ карточки выборки не прочитаны: {type(e).__name__}")
    print(f"\n8. ДРУГИЕ ФАЙЛОВЫЕ ПОЛЯ у карточек без «Request file» (выборка {len(full)} из {len(нет)}):")
    for k in file_f:
        n = sum(1 for c in full if файлы_поля(c.get(k)))
        if n:
            print(f"    «{подпись(k)}» {k}: {n} ({round(n / max(1, len(full)) * 100)} %)")
    print(f"    ни одного файла ни в каком поле: {sum(1 for c in full if not any(файлы_поля(c.get(k)) for k in file_f))}")

    # 9. название и текстовые поля: заполненность, строк, строк с артикулом
    print("\n9. ТЕКСТ КАРТОЧЕК БЕЗ ФАЙЛА:")
    tl = [текст_строк(c.get("title") or "") for c in full]
    print(f"    название: с артикулом {sum(1 for x in tl if x[2])} из {len(tl)}; длина медиана "
          f"{statistics.median([len(str(c.get('title') or '')) for c in full]) if full else '—'} знаков")
    for k in text_f:
        vals = [str(c.get(k) or "") for c in full if str(c.get(k) or "").strip()]
        if len(vals) < max(3, len(full) // 50):
            continue
        st = [текст_строк(v) for v in vals]
        lines = [x[0] for x in st]
        print(f"    «{подпись(k)}» {k}: заполнено {len(vals)} ({round(len(vals) / max(1, len(full)) * 100)} %), "
              f"строк медиана {statistics.median(lines)}, макс {max(lines)}, с артикулом {sum(1 for x in st if x[2])}, "
              f"многострочных (≥ 3) {sum(1 for x in lines if x >= 3)}")

    # 10. исходящие письма карточек без файла: есть ли, вложения, строки в тексте
    acts: list[dict] = []
    for i in range(0, len(ids), 50):
        try:
            acts += client.list_paged("crm.activity.list", {
                "filter": {"OWNER_TYPE_ID": SPA, "OWNER_ID": ids[i:i + 50], "PROVIDER_ID": "CRM_EMAIL", "DIRECTION": 2},
                "select": ["ID", "OWNER_ID", "DESCRIPTION", "FILES", "CREATED"], "order": {"ID": "ASC"}})
        except Exception as e:                                      # noqa: BLE001
            print(f"  ⚠ письма не прочитаны: {type(e).__name__}")
    own = set(ids)
    первое: dict[str, dict] = {}
    for a in sorted(acts, key=lambda a: str(a.get("CREATED") or "")):
        o = str(a.get("OWNER_ID") or "")
        if o in own and o not in первое:
            первое[o] = a
    вл = [a for a in первое.values() if a.get("FILES")]
    тексты = [текст_строк(a.get("DESCRIPTION") or "") for a in первое.values()]
    print(f"\n10. ПЕРВОЕ ИСХОДЯЩЕЕ ПИСЬМО карточек без файла (выборка {len(ids)}): письмо есть у {len(первое)} "
          f"({round(len(первое) / max(1, len(ids)) * 100)} %); с вложением {len(вл)}"
          + (f", вложений медиана {statistics.median([len(a['FILES']) for a in вл])}" if вл else ""))
    if тексты:
        print(f"    текст письма: строк медиана {statistics.median([x[0] for x in тексты])}; с таблицей (<tr>) "
              f"{sum(1 for x in тексты if x[1])}, строк таблицы медиана "
              f"{statistics.median([x[1] for x in тексты if x[1]]) if any(x[1] for x in тексты) else '—'}; "
              f"с артикулом {sum(1 for x in тексты if x[2])}, строк с артикулом медиана "
              f"{statistics.median([x[2] for x in тексты if x[2]]) if any(x[2] for x in тексты) else '—'}")
    без_всего = sum(1 for c in full if not any(файлы_поля(c.get(k)) for k in file_f) and str(c["id"]) not in первое)
    print(f"    ни файла, ни исходящего письма: {без_всего} из {len(full)}")


def main() -> int:
    import config
    import stages
    from bitrix_client import BitrixClient, сводка_нагрузки
    import bitrix_tkp

    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    bitrix_tkp._ОЧЕРЕДЬ = client                       # закачки — через общую очередь портала
    limit = int(os.environ.get("PROBE_LIMIT") or 250)
    n_parts = max(10, int(os.environ.get("PROBE_PARTS") or 10))
    today = dt.date.today()
    since = (today - dt.timedelta(days=ОКНО_ДНЕЙ)).isoformat()
    print(f"ожидается: карточки воронки {config.SPA_CATEGORY_ID} за {ОКНО_ДНЕЙ} дн. (с {since}), "
          f"частей {n_parts}, закачек ≤ {limit}; ориентир ≈ 350 запросов, предел 700")

    поля = (client.call("crm.item.fields", {"entityTypeId": SPA}) or {}).get("fields") or {}
    fobj = поля.get(REQ_FIELD) or {}
    print(f"основа: поле «Request file» {REQ_FIELD}: тип {fobj.get('type', '—')}, "
          f"множественное {'да' if fobj.get('isMultiple') else 'нет'}")

    sel = ["id", "createdTime", "stageId", "parentId2", "createdBy", "assignedById", REQ_FIELD]
    flt = {"categoryId": config.SPA_CATEGORY_ID, ">=createdTime": since + "T00:00:00"}
    # оценка и части по номеру карточки: первый и последний номер окна
    cards: list[dict] = []
    упало = 0
    try:
        idx = client.list_items(SPA, filter=flt, select=["id"])
    except Exception as e:                                          # noqa: BLE001
        print(f"⚠ список карточек не прочитан: {type(e).__name__}")
        print(сводка_нагрузки())
        return 1
    ids = [int(x["id"]) for x in idx]
    for part in части(ids, n_parts):
        try:
            got = client.list_items(SPA, filter={**flt, ">=id": part[0], "<=id": part[-1]}, select=sel)
            cards += got
        except Exception as e:                                      # noqa: BLE001
            упало += 1
            print(f"  ⚠ часть упала: {type(e).__name__}")
    print(f"частей прочитано {n_parts - упало} из {n_parts}; карточек {len(cards)} (по номерам {len(ids)})")
    покр_частей = (n_parts - упало) / n_parts

    # 1. заполненность «Request file»
    с_файлом = [c for c in cards if файлы_поля(c.get(REQ_FIELD))]
    nf = Counter(len(файлы_поля(c.get(REQ_FIELD))) for c in с_файлом)
    print(f"\n1. «REQUEST FILE» У КАРТОЧЕК ОКНА: с файлом {len(с_файлом)} из {len(cards)} "
          f"({round(len(с_файлом) / max(1, len(cards)) * 100)} %); файлов на карточку: "
          + " · ".join(f"{k} — {v}" for k, v in sorted(nf.items())))
    print(f"    вердикт «заполненность Request file»: {вердикт(len(cards), покр_частей)} · n {len(cards)}")

    # 2. товарные строки карточек — выборка 100
    тип = "T" + format(SPA, "x")
    проба = [str(c["id"]) for c in cards[:: max(1, len(cards) // 100)]][:100]
    с_рядами, рядов, отказ = 0, [], Counter()
    for i in range(0, len(проба), 50):
        cmd = {f"c{n}": "crm.item.productrow.list?" + parse.urlencode(
            {"filter[=ownerType]": тип, "filter[=ownerId]": n}) for n in проба[i:i + 50]}
        try:
            res = client.call("batch", {"halt": 0, "cmd": cmd}) or {}
        except Exception as e:                                      # noqa: BLE001
            отказ[type(e).__name__] += len(cmd)
            continue
        rr, err = res.get("result") or {}, res.get("result_error") or {}
        for k in cmd:
            if isinstance(err, dict) and err.get(k):
                e = err[k]
                отказ[str(e.get("error") if isinstance(e, dict) else e)[:30]] += 1
                continue
            v = ((rr.get(k) or {}).get("productRows") or []) if isinstance(rr, dict) else []
            if v:
                с_рядами += 1
                рядов.append(len(v))
    print(f"\n2. ТОВАРНЫЕ СТРОКИ КАРТОЧЕК (выборка {len(проба)}): с рядами {с_рядами}"
          + (f", рядов медиана {statistics.median(рядов)}, макс {max(рядов)}" if рядов else "")
          + (f"; отказы: " + " · ".join(f"{k} {v}" for k, v in отказ.most_common()) if отказ else ""))

    # 3–6. разбор файлов выборки сделок
    by_deal: dict[str, list[dict]] = defaultdict(list)
    for c in с_файлом:
        d = str(c.get("parentId2") or "")
        if d and d != "0":
            by_deal[d].append(c)
    deals = sorted(by_deal, key=int)
    шаг = max(1, len(deals) // max(1, limit // 4))
    выбор, файлов = [], 0
    for d in (deals[::шаг] if limit > 0 else []):
        k = sum(len(файлы_поля(c.get(REQ_FIELD))) for c in by_deal[d])
        if файлов + k > limit and выбор:
            break
        выбор.append(d)
        файлов += k
    print(f"\n3. РАЗБОР ФАЙЛОВ: сделок с файлами {len(deals)}, в выборке {len(выбор)} (каждая {шаг}-я), "
          f"файлов {файлов}")
    итог = Counter()
    виды, способы = Counter(), Counter()
    по_карте: dict[str, tuple[int, int]] = {}
    хэш_карт: dict[str, list[str]] = defaultdict(list)
    строк_файла: dict[str, int] = {}
    арт_файла: dict[str, int] = {}
    for d in выбор:
        for c in by_deal[d]:
            for f in файлы_поля(c.get(REQ_FIELD)):
                url = f.get("urlMachine") or f.get("url")
                body, why, name = bitrix_tkp.fetch(url)
                if body is None:
                    итог["не скачан: " + why[:40]] += 1
                    continue
                итог["скачан"] += 1
                виды[вид(body)] += 1
                h = hashlib.sha256(body).hexdigest()
                хэш_карт[d + ":" + h].append(str(c["id"]))
                if h in строк_файла:
                    n1, na = строк_файла[h], арт_файла.get(h, 0)
                else:
                    try:
                        rows, how = bitrix_tkp.parse(name or "", body)
                    except Exception as e:                          # noqa: BLE001
                        rows, how = [], f"ошибка {type(e).__name__}"
                    способы[str(how).split(":")[0][:30]] += 1
                    n1, na = строки(rows)
                    строк_файла[h], арт_файла[h] = n1, na
                    if n1:
                        итог["разобран"] += 1
                prev = по_карте.get(str(c["id"]), (0, 0))
                по_карте[str(c["id"])] = (prev[0] + n1, prev[1] + na)
    print("    закачка: " + " · ".join(f"{k} {v}" for k, v in итог.most_common()))
    print("    вид по сигнатуре: " + " · ".join(f"{k} {v}" for k, v in виды.most_common()))
    print("    разбор: " + " · ".join(f"{k} {v}" for k, v in способы.most_common()))
    n_rows = [v[0] for v in по_карте.values() if v[0]]
    n_art = [v[1] for v in по_карте.values() if v[1]]
    if n_rows:
        print(f"    строк на карточку (непустых): n {len(n_rows)}, медиана {statistics.median(n_rows)}, "
              f"P80 {sorted(n_rows)[int(len(n_rows) * 0.8) - 1] if len(n_rows) > 1 else n_rows[0]}, макс {max(n_rows)}")
        print("    по корзинам: " + " · ".join(f"{lbl} — {Counter(корзина(x) for x in n_rows).get(lbl, 0)}"
                                             for _hi, lbl in КОРЗИНЫ))
    if n_art:
        print(f"    строк с артикулом на карточку: n {len(n_art)}, медиана {statistics.median(n_art)}, макс {max(n_art)}")
    покр = (итог.get("разобран", 0) / max(1, len(строк_файла))) if строк_файла else 0
    print(f"    вердикт «строки из Request file»: {вердикт(len(n_rows), min(покр, покр_частей))} · "
          f"n {len(n_rows)} · разобрано файлов {итог.get('разобран', 0)} из {len(строк_файла)} разных")

    # 4. один файл — несколько карточек сделки
    кратн = Counter(len(v) for v in хэш_карт.values())
    print("\n4. ОДИН ФАЙЛ НА НЕСКОЛЬКО КАРТОЧЕК СДЕЛКИ (разных поставщиков): "
          + " · ".join(f"{k} карт. — {v} файлов" for k, v in sorted(кратн.items())))

    # 5. СКВОЗНОЙ СЧЁТ ПО СТРОКАМ (владелец 31.10.2026: «не надо взвешивать, просто считай сквозно по
    # строкам»): единица — строка запроса; один файл, ушедший нескольким поставщикам сделки, — его строки
    # один раз; строка расценена, если хотя бы одна карточка с этим файлом сейчас на стадии КП. Зрелые —
    # файл ушёл первой карточкой не позже ЗРЕЛОСТЬ_ДНЕЙ назад. Для сравнения — доля карточек тех же сделок
    by_id = {str(c["id"]): c for c in cards}
    порог = (today - dt.timedelta(days=ЗРЕЛОСТЬ_ДНЕЙ)).isoformat()
    стр_все = стр_кп = групп = групп_кп = без_строк = 0
    карт_все, карт_кп = set(), set()
    for k, cids in хэш_карт.items():
        h = k.split(":", 1)[1]
        cs = [by_id[c] for c in set(cids) if c in by_id]
        if not cs or min(str(c.get("createdTime") or "")[:10] for c in cs) > порог:
            continue
        n = строк_файла.get(h, 0)
        if not n:
            без_строк += 1
            continue
        priced = any(stages.classify_stage(str(c.get("stageId") or "")) in stages.KP_STAGES for c in cs)
        групп += 1
        групп_кп += priced
        стр_все += n
        стр_кп += n if priced else 0
        for c in cs:
            карт_все.add(str(c["id"]))
            if stages.classify_stage(str(c.get("stageId") or "")) in stages.KP_STAGES:
                карт_кп.add(str(c["id"]))
    if стр_все:
        print(f"\n5. СКВОЗНОЙ СЧЁТ ПО СТРОКАМ (зрелые, файл ушёл раньше {ЗРЕЛОСТЬ_ДНЕЙ} дн.; КП — стадия сейчас): "
              f"строк {стр_все}, расценено {стр_кп} — {round(стр_кп / стр_все * 100)} %; разных файлов {групп}, "
              f"с КП хоть от одного поставщика {групп_кп}; файлов без разобранных строк {без_строк}")
        print(f"    для сравнения — по карточкам тех же файлов: {round(len(карт_кп) / max(1, len(карт_все)) * 100)} % "
              f"({len(карт_кп)} из {len(карт_все)})")
        print(f"    вердикт «сквозной счёт по строкам»: {вердикт(групп, min(покр, покр_частей))} · n {групп} файлов")

    # 6. строк на заявку: сумма строк разных файлов сделки
    на_сделку = []
    for d in выбор:
        hs = {k.split(":", 1)[1] for k in хэш_карт if k.startswith(d + ":")}
        s = sum(строк_файла.get(h, 0) for h in hs)
        if s:
            на_сделку.append(s)
    if на_сделку:
        print(f"\n6. СТРОК НА ЗАЯВКУ (сделку, разные файлы): n {len(на_сделку)}, медиана {statistics.median(на_сделку)}, "
              f"макс {max(на_сделку)}; по корзинам: " + " · ".join(
                  f"{lbl} — {Counter(корзина(x) for x in на_сделку).get(lbl, 0)}" for _hi, lbl in КОРЗИНЫ))
    без_файла(client, cards, поля, bitrix_tkp)
    print("\n" + сводка_нагрузки())
    return 0


if __name__ == "__main__":
    sys.exit(main())
