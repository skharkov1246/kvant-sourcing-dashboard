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
  5. доля КП по карточкам против доли по строкам (у зрелых, старше 30 дней, карточек выборки):
     насколько взвешивание меняет картину;
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

    sel = ["id", "createdTime", "stageId", "parentId2", REQ_FIELD]
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
    for d in deals[::шаг]:
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

    # 5. доля КП: по карточкам и по строкам (зрелые карточки выборки)
    зрелые = [c for c in cards if str(c["id"]) in по_карте and по_карте[str(c["id"])][0]
              and str(c.get("createdTime") or "")[:10] <= (today - dt.timedelta(days=ЗРЕЛОСТЬ_ДНЕЙ)).isoformat()]
    kp = [c for c in зрелые if stages.classify_stage(str(c.get("stageId") or "")) in stages.KP_STAGES]
    s_all = sum(по_карте[str(c["id"])][0] for c in зрелые)
    s_kp = sum(по_карте[str(c["id"])][0] for c in kp)
    if зрелые:
        print(f"\n5. ДОЛЯ КП (стадия КП сейчас; зрелые, старше {ЗРЕЛОСТЬ_ДНЕЙ} дн.): по карточкам "
              f"{round(len(kp) / len(зрелые) * 100)} % ({len(kp)} из {len(зрелые)}), по строкам "
              f"{round(s_kp / max(1, s_all) * 100)} % ({s_kp} из {s_all})")
        print(f"    вердикт «КП по строкам против карточек»: {вердикт(len(зрелые), покр)} · n {len(зрелые)}")

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
    print("\n" + сводка_нагрузки())
    return 0


if __name__ == "__main__":
    sys.exit(main())
