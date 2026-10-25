#!/usr/bin/env python3
"""Зонд v56 «Финальная экономика сделки»: можно ли читать файл экономики по ссылке.

ПОВОД (23.10.2026): в сделке реализации поля Битрикса («Сумма», «Cost (rub)»,
«Offer sum of selected supplier») хранили числа ранней итерации расчёта, а ссылка
«Экономика проекта» (UF_CRM_1740133235324, clck.ru) ведёт на финальную итерацию в
Яндекс Диске. Прежде чем строить чтение файла — замер: куда ведут ссылки, что за
файлы, можно ли их скачать без ключа (публичный API Диска) и есть ли в них узнаваемые
строки выручки, закупки и маржи.

ЧТО МЕРЯЕМ (только счётчики; ни ссылок, ни имён файлов, ни сумм, ни номеров сделок):
  • открытые сделки реализации: со ссылкой и без; домен ссылки;
  • короткая ссылка clck.ru → куда раскрылась (Яндекс Диск файл / папка / другое);
  • публичный ресурс Диска: файл или папка, тип файла, в папке — сколько файлов,
    есть ли признак итерации в имени (v1, итер., финал — только счёт);
  • xlsx: удалось ли скачать и открыть, сколько листов, нашлись ли подписи
    «выручка/продажа», «закуп/себестоимость», «маржа/МД», «итого».
НАГРУЗКА: Битрикс ≈ 15 запросов; внешние ссылки — не больше ECON_PROBE_MAX (40).
"""
from __future__ import annotations

import collections
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import contracts as ct  # noqa: E402
from bitrix_client import BitrixClient  # noqa: E402

МАКС = int(os.environ.get("ECON_PROBE_MAX", "10"))   # владелец 23.10: не больше 30 файлов в день
API = "https://cloud-api.yandex.net/v1/disk/public/resources"
ПОДПИСИ = {
    "выручка": re.compile(r"выручк|продаж|revenue|sales", re.I),
    "закупка": re.compile(r"закуп|себестоим|cost|purchase", re.I),
    "маржа": re.compile(r"марж|\bМД\b|margin|прибыл", re.I),
    "итого": re.compile(r"итого|total", re.I),
}
СЛОВАРЬ = {k: re.compile(v, re.I) for k, v in {
    # продажа
    "стоимость для заказчика": r"стоимост\w* для заказчик|цена для заказчик|для клиента",
    "цена продажи": r"цен\w* продаж|сумм\w* продаж|selling|sale price|sales price",
    "контракт/договор": r"сумм\w* (контракт|договор)|стоимост\w* (контракт|договор)|цен\w* (контракт|договор)|contract",
    "ТКП/КП": r"\bТКП\b|\bКП\b|коммерческ\w* предлож",
    "с НДС / без НДС": r"с ндс|без ндс|ндс",
    "выручка/доход": r"выручк|доход|revenue",
    # закупка
    "закупка/себестоимость": r"закуп|себестоим",
    "поставщик": r"поставщик|supplier|vendor",
    "cost/purchase": r"\bcost\b|purchase",
    "EXW/FCA/DAP": r"\b(EXW|FCA|DAP|DDP|CIF|CPT)\b",
    # маржа
    "маржа/МД": r"марж|\bМД\b|margin",
    "прибыль": r"прибыл|profit",
    "наценка": r"наценк|markup|mark-up",
    "рентабельность": r"рентаб",
    # прочее
    "логистика": r"логист|доставк|freight|транспорт",
    "таможня/пошлина": r"тамож|пошлин|customs|duty",
    "курс валюты": r"курс|exchange rate",
    "итого": r"итого|total",
    "итерация/версия": r"итерац|верси|финал|final",
}.items()}
ИТЕРАЦИЯ = re.compile(r"\bv\d+\b|итерац|финал|final|верс", re.I)


class НетРедиректа(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def раскрыть(url: str) -> str:
    """Короткую ссылку — в адрес, куда она ведёт (один шаг, без перехода)."""
    op = urllib.request.build_opener(НетРедиректа())
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "kvant-probe"})
    try:
        with op.open(req, timeout=20) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:
        return e.headers.get("Location") or ""
    except Exception:
        return ""


КОДЫ: collections.Counter = collections.Counter()


def получить(url: str, timeout: int = 30, ключ: str = "") -> bytes | None:
    """Тело ответа или None; код ответа — в КОДЫ (только счёт, без адресов)."""
    h = {"User-Agent": "kvant-probe"}
    if ключ:
        h["Authorization"] = f"OAuth {ключ}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            КОДЫ[f"{'с ключом' if ключ else 'без ключа'}: {r.status}"] += 1
            return r.read(30 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        # имя ошибки API (FieldValidationError и т. п.) — без описания: в нём бывает сам ключ
        try:
            имя = str(json.loads(e.read(4096) or b"{}").get("error") or "")[:40]
        except Exception:
            имя = ""
        КОДЫ[f"{'с ключом' if ключ else 'без ключа'}: {e.code}{' ' + имя if имя else ''}"] += 1
        return None
    except Exception as e:
        КОДЫ[f"{'с ключом' if ключ else 'без ключа'}: {type(e).__name__}"] += 1
        return None


def ключ_и_путь(public_url: str) -> tuple[str, str]:
    """https://disk.yandex.ru/d/<id>/<файл> → («…/d/<id>», «/<файл>»): API ждёт ссылку на
    общую папку и путь внутри неё отдельно (иначе 400 DiskPublicKeyFormatError)."""
    u = urllib.parse.urlparse(public_url)
    части = [x for x in u.path.split("/") if x]
    if len(части) > 2 and части[0] in ("d", "i"):
        return (f"{u.scheme}://{u.netloc}/{части[0]}/{части[1]}",
                "/" + "/".join(urllib.parse.unquote(x) for x in части[2:]))
    return public_url, ""


def ресурс(public_url: str) -> dict | None:
    """Публичный ресурс Диска: сначала без ключа, не вышло — с ключом прогона (YADISK_TOKEN)."""
    pk, путь = ключ_и_путь(public_url)
    адрес = (f"{API}?public_key={urllib.parse.quote(pk, safe='')}&limit=200"
             + (f"&path={urllib.parse.quote(путь, safe='')}" if путь else ""))
    for ключ in ("", os.environ.get("YADISK_TOKEN", "").strip()):
        if ключ is None:
            continue
        raw = получить(адрес, ключ=ключ)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        if not os.environ.get("YADISK_TOKEN", "").strip():
            break
    return None


def общий_диск(путь: str) -> tuple[str, str] | None:
    """Путь файла на общем диске Яндекс 360 → (метка, путь внутри диска) или None.
    Принимает «vd/<метка>/disk/<путь>», «vd/<метка>/<путь>» и «vd:<метка>:disk:/<путь>»."""
    м = re.match(r"^vd[:/]([A-Za-z0-9_-]+)[:/](?:disk:?/)?(.*)$", путь.strip("/"))
    return (м.group(1), м.group(2)) if м else None


def таблицы_диска(ключ: str, макс: int = 20000) -> dict[str, list[dict]]:
    """Все таблицы на Диске ключа (включая принятые общие папки): имя файла → ресурсы.
    Файл из ссылки сотрудника лежит у получателя по другому пути — ищем по имени."""
    по_имени: dict[str, list[dict]] = collections.defaultdict(list)
    смещение = 0
    while смещение < макс:
        raw = получить("https://cloud-api.yandex.net/v1/disk/resources/files?media_type=spreadsheet"
                       f"&limit=1000&offset={смещение}&fields=items.name,items.path,items.file,items.modified",
                       ключ=ключ)
        if not raw:
            break
        items = json.loads(raw).get("items") or []
        for x in items:
            по_имени[str(x.get("name", "")).lower()].append(x)
        if len(items) < 1000:
            break
        смещение += 1000
        time.sleep(0.5)
    return по_имени


ОБЩЕЕ = re.compile(
    r"цен|стоим|итог|сумм|курс|ндс|кол-?в|\bед\.?|наимен|поставщ|логист|достав|тамож|пошлин|марж|наценк|прибыл|"
    r"коэф|%|валют|евро|\bруб(\b|л)|\b(usd|eur|cny|rub|aed|exw|fca|dap|ddp|cip|cpt)\b|ткп|\bкп\b|срок|\bвес|объ[её]м|расход|"
    r"комисс|банк|страх|итерац|верс|дата|№|номер|артикул|\bкод|позиц|бренд|производ|склад|упаков|сертиф|себест|"
    r"закуп|продаж|оплат|аванс|отсроч|ставк|клиент|заказчик|total|price|cost|qty|amount|margin|rate|incoterms|базис",
    re.I)
# личное в подписях шаблона (блок подписи, реквизиты): почта, телефон, ссылка, ФИО — не печатать
ЛИЧНОЕ = re.compile(r"@|https?://|www\.|\+?\d[\d\s()-]{6,}|[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(вич|вна|ич|на)\b")
СКЕЛЕТ = {"файлов": 0, "листов": collections.Counter(), "активный": collections.Counter(), "скрытых": collections.Counter(),
          "имена": collections.Counter(), "подписи": collections.Counter(), "формулы": collections.Counter()}


def _норм(t: str) -> str:
    t = re.sub(r"\s+", " ", str(t)).strip()
    return re.sub(r"\d+([.,]\d+)?", "N", t)[:70]


def скелет(b: bytes) -> None:
    """Подписи и формулы шаблона по листам — для понимания логики файла. Каждый файл даёт
    множество (лист, ячейка, текст); печатается только то, что повторяется в 5+ файлах и
    похоже на заголовок шаблона (ОБЩЕЕ). Числа в формулах — «N», имена листов — «Лk»."""
    import openpyxl
    try:
        wf = openpyxl.load_workbook(io.BytesIO(b), data_only=False)
    except Exception:
        return
    СКЕЛЕТ["файлов"] += 1
    n = len(wf.sheetnames)
    СКЕЛЕТ["листов"][n] += 1
    акт = wf.sheetnames.index(wf.active.title) + 1 if wf.active is not None else 0
    СКЕЛЕТ["активный"]["последний" if акт == n else f"{акт} из {n}"] += 1
    СКЕЛЕТ["скрытых"][sum(1 for ws in wf.worksheets if ws.sheet_state != "visible")] += 1
    имя_в_номер = {nm: f"Л{i}" for i, nm in enumerate(wf.sheetnames, 1)}
    подписи, формулы, имена = set(), set(), set()
    for i, ws in enumerate(wf.worksheets, 1):
        имена.add((i, _норм(ws.title) if ОБЩЕЕ.search(ws.title) else "…"))
        for row in ws.iter_rows(max_row=300, max_col=40):
            for c in row:
                v = c.value
                if isinstance(v, str) and v.startswith("="):
                    f = v
                    for nm, k in sorted(имя_в_номер.items(), key=lambda x: -len(x[0])):
                        f = f.replace(f"'{nm}'!", f"{k}!").replace(f"{nm}!", f"{k}!")
                    f = re.sub(r"'[^']*'!", "Л?!", f)
                    # числа-константы (наценка, ставки) — «N»; номера строк в ссылках (D25, $D$25) не трогаем
                    f = re.sub(r"(?<![A-Za-zЛ$\d.])\d+(?:\.\d+)?", "N", f)
                    формулы.add((i, c.coordinate, f[:90]))
                elif isinstance(v, str) and ОБЩЕЕ.search(v) and len(v.strip()) <= 70 and not ЛИЧНОЕ.search(v):
                    подписи.add((i, c.coordinate, _норм(v)))
    for x in подписи:
        СКЕЛЕТ["подписи"][x] += 1
    for x in формулы:
        СКЕЛЕТ["формулы"][x] += 1
    for x in имена:
        СКЕЛЕТ["имена"][x] += 1


def печать_скелета(порог: int = 5) -> None:
    S = СКЕЛЕТ
    if not S["файлов"]:
        return
    print(f"\nСКЕЛЕТ ШАБЛОНА по {S['файлов']} файлам (печатается то, что есть в {порог}+ файлах)")
    print("  листов: " + " · ".join(f"{k}: {v}" for k, v in sorted(S["листов"].items())))
    print("  активный лист при открытии: " + " · ".join(f"{k}: {v}" for k, v in S["активный"].most_common()))
    print("  скрытых листов: " + " · ".join(f"{k}: {v}" for k, v in sorted(S["скрытых"].items())))
    for (i, nm), v in sorted(S["имена"].items()):
        if v >= порог:
            print(f"  лист {i}: имя «{nm}» в {v} файлах")
    def ключ(x):
        (i, ref, _t), _v = x
        m = re.match(r"([A-Z]+)(\d+)", ref)
        return (i, int(m.group(2)) if m else 0, m.group(1) if m else ref)
    for что in ("подписи", "формулы"):
        rows = sorted(((k, v) for k, v in S[что].items() if v >= порог), key=ключ)
        print(f"  {что}: {len(rows)}")
        по_листу = collections.Counter()
        for (i, ref, t), v in rows:
            по_листу[i] += 1
            if по_листу[i] <= 90:
                print(f"    Л{i} {ref}: {t}  [{v}]")


def разобрать_xlsx(b: bytes, сч: collections.Counter) -> None:
    """Шаблон расчёта: какие слова словаря есть в файле и стоит ли рядом число.
    В журнал — только слово словаря, номер листа и счёт; ни значений, ни имён листов."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(b), read_only=True, data_only=True)
    except Exception:
        сч["xlsx: не открылся"] += 1
        return
    сч["xlsx: открылся"] += 1
    сч[f"листов: {min(len(wb.sheetnames), 10)}{'+' if len(wb.sheetnames) >= 10 else ''}"] += 1
    нашлось: dict[str, set] = collections.defaultdict(set)
    с_числом: set[str] = set()
    for лист, ws in enumerate(wb.worksheets[:10], 1):
        for row in ws.iter_rows(max_row=400, max_col=30, values_only=True):
            row = list(row)
            for i, v in enumerate(row):
                if not isinstance(v, str):
                    continue
                for слово, rx in СЛОВАРЬ.items():
                    if rx.search(v):
                        нашлось[слово].add(лист)
                        if any(isinstance(x, (int, float)) and not isinstance(x, bool) and abs(x) > 0
                               for x in row[i + 1:i + 8]):
                            с_числом.add(слово)
    for слово in СЛОВАРЬ:
        if слово in нашлось:
            сч[f"слово «{слово}»: есть (листы {','.join(map(str, sorted(нашлось[слово])))})"
               f"{' · число справа' if слово in с_числом else ''}"] += 1

def main() -> int:
    client = BitrixClient(config.Settings.load().bitrix_webhook_url)
    сделки = client.list_deals_fast(filter={"CATEGORY_ID": 0, "STAGE_SEMANTIC_ID": "P"},
                                    select=["ID", "DATE_CREATE", ct.ECON_LINK])
    сделки = [d for d in сделки if str(d.get("CATEGORY_ID", "0") or "0") == "0"]
    со_ссылкой = [d for d in сделки if str(d.get(ct.ECON_LINK) or "").strip().startswith("http")]
    print(f"открытые сделки реализации: {len(сделки)}; со ссылкой «Экономика проекта» "
          f"{len(со_ссылкой)} ({100 * len(со_ссылкой) / max(1, len(сделки)):.0f} %)")
    домены = collections.Counter(urllib.parse.urlparse(str(d[ct.ECON_LINK]).strip()).netloc for d in со_ссылкой)
    print("домен ссылки: " + " · ".join(f"{k} {v}" for k, v in домены.most_common(8)))

    # где лежат файлы экономики: верхние папки путей по всем ссылкам — только счёт, без имён
    верх: collections.Counter = collections.Counter()
    for d in (со_ссылкой if os.environ.get("ECON_PROBE_ALL") else []):
        u = str(d[ct.ECON_LINK]).strip()
        ц = раскрыть(u) if "clck.ru" in u else u
        if "sba.yandex" in urllib.parse.urlparse(ц).netloc:
            ц = (urllib.parse.parse_qs(urllib.parse.urlparse(ц).query).get("url") or [""])[0]
        ч = [x for x in urllib.parse.urlparse(ц).path.split("/") if x]
        if ч[:2] == ["edit", "disk"] and len(ч) > 2:
            пп = urllib.parse.unquote(ч[2]).split("/")
            верх["/".join(пп[1:3] if пп[:1] == ["disk"] else пп[:2])] += 1
        else:
            верх["(не /edit/disk)"] += 1
        time.sleep(0.3)
    всего = sum(верх.values())
    доли = [n for _k, n in верх.most_common()]
    if всего:
        print(f"файлы экономики: ссылок {всего}; разных папок (2 уровня от корня) {len(верх)}; "
              f"в самой частой {доли[0] if доли else 0}, в 3 частых {sum(доли[:3])}, в 10 частых {sum(доли[:10])}")
    выборка = sorted(со_ссылкой, key=lambda d: str(d.get("DATE_CREATE") or ""), reverse=True)[:МАКС]
    сч: collections.Counter = collections.Counter()
    тк_диска = os.environ.get("YADISK_TOKEN", "").strip()
    по_имени = таблицы_диска(тк_диска) if тк_диска else {}
    print(f"таблиц на Диске ключа: {sum(len(v) for v in по_имени.values())}")
    for d in выборка:
        url = str(d[ct.ECON_LINK]).strip()
        цель = раскрыть(url) if "clck.ru" in url else url
        time.sleep(0.5)
        # clck.ru ведёт через страницу проверки ссылок Яндекса: адрес — в параметре url
        if "sba.yandex" in urllib.parse.urlparse(цель).netloc:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(цель).query)
            цель = (q.get("url") or [""])[0]
            сч["через страницу проверки ссылок Яндекса"] += 1
        host = urllib.parse.urlparse(цель).netloc
        # форма адреса — без самого адреса: сколько частей пути, есть ли запрос и пробелы
        пу = urllib.parse.urlparse(цель)
        # первые части пути — только служебные слова Диска, остальное «…»
        СЛОВА = {"d", "i", "client", "disk", "edit", "public", "docs", "mail", "recent", "shared"}
        части = [x for x in пу.path.split("/") if x]
        сч["вид пути: /" + "/".join(x if x in СЛОВА else "…" for x in части[:2])] += 1
        сч[f"форма: частей пути {len(части)}"
           f"{' · с запросом' if пу.query else ''}{' · пробел' if ' ' in цель.strip() else ''}"
           f"{' · хвост-пробел' if цель != цель.strip() else ''}"] += 1
        цель = цель.strip()
        вид = ("Диск: файл" if "disk.yandex" in host and "/i/" in цель else
               "Диск: папка или файл (/d/)" if "disk.yandex" in host else
               "не раскрылась" if not цель else f"другое: {host}")
        сч[f"ссылка → {вид}"] += 1
        if "disk.yandex" not in host:
            continue
        личный = части[:2] == ["client", "disk"] or части[:1] == ["edit"]
        if личный:
            # ссылка на личный Диск (открывается только вошедшему в аккаунт):
            # читается ключом прогона по пути disk:/…, если ключ от того же аккаунта
            # /client/disk/<папка>/<файл> и /edit/disk/disk%2F<папка>%2F<файл>: путь от корня Диска
            путь = "/".join(urllib.parse.unquote(x) for x in части[2:])
            # первые слова пути — только служебные (disk, vd), прочее «…»
            сч["начало пути: " + "/".join(x if x in ("disk", "vd", "client", "edit") else "…"
                                          for x in путь.split("/")[:2])] += 1
            об = общий_диск(путь)
            if об:
                # общий диск организации: свой метод API (virtual-disks), путь vd:<метка>:disk:/…
                if not тк_диска:
                    сч["общий диск: ключа нет"] += 1
                    continue
                vd = f"vd:{об[0]}:disk:/{об[1]}"
                raw = получить("https://cloud-api.yandex.net/v1/disk/virtual-disks/resources/download?path="
                               + urllib.parse.quote(vd, safe=""), ключ=тк_диска)
                time.sleep(1.0)
                href = json.loads(raw).get("href") if raw else None
                if not href:
                    сч["общий диск: ссылка на скачивание не получена"] += 1
                    continue
                сч["общий диск: ссылка на скачивание получена"] += 1
                if not об[1].lower().endswith((".xlsx", ".xlsm")):
                    сч[f"общий диск: файл не xlsx ({об[1].rsplit('.', 1)[-1].lower()[:5] if '.' in об[1] else 'без расширения'})"] += 1
                    continue
                b = получить(href, ключ=тк_диска)
                time.sleep(1.0)
                if not b:
                    сч["xlsx: не скачался"] += 1
                    continue
                разобрать_xlsx(b, сч)
                скелет(b)
                continue
            if части[0] == "edit" and путь.startswith("disk/"):
                путь = путь[len("disk/"):]
            тк = os.environ.get("YADISK_TOKEN", "").strip()
            if not тк:
                сч["личный Диск: ключа нет"] += 1
                continue
            raw = получить(f"https://cloud-api.yandex.net/v1/disk/resources?path={urllib.parse.quote('disk:/' + путь.lstrip('/'), safe='')}&limit=200", ключ=тк)
            time.sleep(0.5)
            как = "полный путь" if raw else ""
            куски = путь.strip("/").split("/")
            for i in range(1, len(куски) - 1):
                if raw:
                    break
                # общая папка у получателя лежит в корне под своим именем: хвост пути
                raw = получить("https://cloud-api.yandex.net/v1/disk/resources?path="
                               + urllib.parse.quote("disk:/" + "/".join(куски[i:]), safe="") + "&limit=200", ключ=тк)
                как = "хвост пути" if raw else ""
                time.sleep(0.3)
            if not raw:
                кандидаты = по_имени.get(куски[-1].lower(), [])
                if len(кандидаты) == 1:
                    raw, как = json.dumps(dict(кандидаты[0], type="file")).encode(), "имя файла"
                elif кандидаты:
                    # одноимённых несколько — берём свежий, считаем отдельно
                    свежий = max(кандидаты, key=lambda x: x.get("modified", ""))
                    raw, как = json.dumps(dict(свежий, type="file")).encode(), "имя файла (из нескольких)"
            if not raw:
                сч["личный Диск: не найден ни по пути, ни по имени"] += 1
                continue
            сч[f"личный Диск: найден — {как}"] += 1
            try:
                р = json.loads(raw)
            except json.JSONDecodeError:
                continue
        else:
            р = ресурс(цель)
        time.sleep(0.5)
        if not р or "type" not in р:
            сч["Диск: публичный ресурс недоступен без ключа"] += 1
            continue
        if р["type"] == "dir":
            files = [x for x in (р.get("_embedded") or {}).get("items") or [] if x.get("type") == "file"]
            сч[f"папка: файлов {min(len(files), 10)}{'+' if len(files) >= 10 else ''}"] += 1
            сч[f"папка: с признаком итерации в имени {'есть' if any(ИТЕРАЦИЯ.search(x.get('name', '')) for x in files) else 'нет'}"] += 1
            xlsx = sorted((x for x in files if str(x.get("name", "")).lower().endswith((".xlsx", ".xlsm"))),
                          key=lambda x: x.get("modified", ""), reverse=True)
            сч[f"папка: xlsx {min(len(xlsx), 5)}{'+' if len(xlsx) >= 5 else ''}"] += 1
            файл = xlsx[0] if xlsx else None
        else:
            файл = р
            сч[f"файл: {str(р.get('name', '')).rsplit('.', 1)[-1].lower() if '.' in str(р.get('name', '')) else 'без расширения'}"] += 1
        if not файл or not str(файл.get("name", "")).lower().endswith((".xlsx", ".xlsm")):
            continue
        href = файл.get("file")
        if not href and not личный:
            pk, путь = ключ_и_путь(цель)
            raw = получить(f"{API}/download?public_key={urllib.parse.quote(pk, safe='')}"
                           + (f"&path={urllib.parse.quote(путь, safe='')}" if путь else ""))
            try:
                href = json.loads(raw).get("href") if raw else None
            except json.JSONDecodeError:
                href = None
        b = (получить(href, ключ=os.environ.get("YADISK_TOKEN", "").strip() if личный else "")
             if href else None)
        time.sleep(0.5)
        if not b:
            сч["xlsx: не скачался"] += 1
            continue
        разобрать_xlsx(b, сч)
    print(f"выборка: {len(выборка)} последних сделок со ссылкой; ключ Диска в прогоне: "
          f"{'есть' if os.environ.get('YADISK_TOKEN', '').strip() else 'нет'}")
    print("коды ответов Диска: " + " · ".join(f"{k} {v}" for k, v in sorted(КОДЫ.items())))
    for k, v in sorted(сч.items()):
        print(f"  {k}: {v}")
    печать_скелета()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
