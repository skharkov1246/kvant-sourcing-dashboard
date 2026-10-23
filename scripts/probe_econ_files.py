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

МАКС = int(os.environ.get("ECON_PROBE_MAX", "40"))
API = "https://cloud-api.yandex.net/v1/disk/public/resources"
ПОДПИСИ = {
    "выручка": re.compile(r"выручк|продаж|revenue|sales", re.I),
    "закупка": re.compile(r"закуп|себестоим|cost|purchase", re.I),
    "маржа": re.compile(r"марж|\bМД\b|margin|прибыл", re.I),
    "итого": re.compile(r"итого|total", re.I),
}
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


def ресурс(public_url: str) -> dict | None:
    """Публичный ресурс Диска: сначала без ключа, не вышло — с ключом прогона (YADISK_TOKEN)."""
    адрес = f"{API}?public_key={urllib.parse.quote(public_url, safe='')}&limit=200"
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


def разобрать_xlsx(b: bytes, сч: collections.Counter) -> None:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(b), read_only=True, data_only=True)
    except Exception:
        сч["xlsx: не открылся"] += 1
        return
    сч["xlsx: открылся"] += 1
    сч[f"листов: {min(len(wb.sheetnames), 10)}{'+' if len(wb.sheetnames) >= 10 else ''}"] += 1
    нашлось = set()
    for ws in wb.worksheets[:6]:
        for row in ws.iter_rows(max_row=200, max_col=12, values_only=True):
            for v in row:
                if isinstance(v, str):
                    for k, rx in ПОДПИСИ.items():
                        if rx.search(v):
                            нашлось.add(k)
    for k in ПОДПИСИ:
        сч[f"подпись «{k}»: {'есть' if k in нашлось else 'нет'}"] += 1


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

    выборка = sorted(со_ссылкой, key=lambda d: str(d.get("DATE_CREATE") or ""), reverse=True)[:МАКС]
    сч: collections.Counter = collections.Counter()
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
        сч[f"форма: частей пути {len([x for x in пу.path.split('/') if x])}"
           f"{' · с запросом' if пу.query else ''}{' · пробел' if ' ' in цель.strip() else ''}"
           f"{' · хвост-пробел' if цель != цель.strip() else ''}"] += 1
        цель = цель.strip()
        вид = ("Диск: файл" if "disk.yandex" in host and "/i/" in цель else
               "Диск: папка или файл (/d/)" if "disk.yandex" in host else
               "не раскрылась" if not цель else f"другое: {host}")
        сч[f"ссылка → {вид}"] += 1
        if "disk.yandex" not in host:
            continue
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
        b = получить(href) if href else None
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
