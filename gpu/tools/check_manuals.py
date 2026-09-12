#!/usr/bin/env python3
"""Проверка ссылок на мануалы → поле link_status в gpu/data/oem_docs.json.

Зачем. Реестр документации ценен ровно настолько, насколько ссылки открываются.
Часть источников отдаёт документ только через браузер (антибот-защита Imperva на
motortech.de), часть прячет за платным доступом (scribd, pdfcoffee), часть просто
удалила файл. Сорсер не должен выяснять это щелчком по каждой строке — статус
пишется в данные и показывается рядом со ссылкой.

Скрипт СЕТЕВОЙ и в гейт не входит: запускается руками, когда реестр пополнился.
Результат коммитится вместе с данными.

    python gpu/tools/check_manuals.py            # проверить все ссылки
    python gpu/tools/check_manuals.py mtu mwm    # только указанные марки

Как читать статусы:
    открыт        — файл отдан, тип и размер сошлись
    браузер       — сервер отдаёт заглушку защиты от роботов; из браузера файл качается
    онлайн        — читается постранично в браузере, файл за регистрацией
    платный       — страница есть, документ за оплатой
    нет файла     — 404 или 410
    не отвечает   — таймаут или сетевая ошибка
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
FILE = DATA / "oem_docs.json"

# Домены, у которых заведомо иная природа ответа, чем код HTTP: 200 приходит
# на заглушку или на страницу платного доступа, а не на документ.
BOT_GUARD = ("motortech.de",)
PAYWALL = ("scribd.com", "pdfcoffee.com", "emanualonline.com")
# Библиотеки, где документ читается постранично в браузере бесплатно, а файл
# отдают за регистрацию. Для снабженца это рабочий источник: посмотреть таблицу
# моментов затяжки можно, положить PDF на диск — нет.
ONLINE_ONLY = ("manualslib.com", "dokumen.site")


def probe(url: str, tries: int = 3) -> tuple[str, str]:
    """Пробует ссылку до трёх раз и возвращает лучший результат.

    Повтор обязателен: разовый обрыв на большом файле с mtu-solutions.com или
    birkasco.com дал бы в реестре отметку «не отвечает» на живом документе, и
    сорсер вычеркнул бы нужный ему мануал.
    """
    for i in range(tries):
        status, detail = probe_once(url)
        if status != "не отвечает":
            return status, detail
        if i + 1 < tries:
            subprocess.run(["sleep", str(2 * (i + 1))], check=False)
    return status, detail + f" (попыток: {tries})"


def probe_once(url: str) -> tuple[str, str]:
    """Запрашивает первые два килобайта и судит по коду, типу и размеру."""
    try:
        out = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code} %{content_type} %{size_download}",
             "-L", "--max-time", "30", "-r", "0-2000", url],
            capture_output=True, text=True, timeout=60,
        ).stdout.split(None, 2)
    except (subprocess.TimeoutExpired, OSError):
        return "не отвечает", "curl не вернул ответ"
    if len(out) < 3:
        return "не отвечает", "пустой ответ"
    code, ctype, size = out[0], out[1], out[2].strip()
    host = url.split("/")[2] if "://" in url else url
    detail = f"HTTP {code}, {ctype}, {size} Б"

    if any(d in host for d in BOT_GUARD):
        return "браузер", detail + " — сайт закрыт защитой от роботов, файл качается браузером"
    if code in ("404", "410"):
        return "нет файла", detail
    if any(d in host for d in ONLINE_ONLY):
        return "онлайн", detail + " — читается в браузере, файл за регистрацией"
    if code in ("401", "402", "403") or any(d in host for d in PAYWALL):
        return "платный", detail
    if code.startswith("2"):
        if "pdf" in ctype:
            return "открыт", detail
        return "открыт", detail + " — страница, не файл"
    return "не отвечает", detail


def main(keys):
    docs = json.loads(FILE.read_text(encoding="utf-8"))
    seen, tally = {}, {}
    for oem in docs["oems"]:
        if keys and oem["key"] not in keys:
            continue
        for man in oem.get("manuals", []):
            url = man.get("url", "")
            if not url:
                continue
            if url not in seen:
                seen[url] = probe(url)
                print(f"{seen[url][0]:12} {url}")
            man["link_status"], man["link_detail"] = seen[url]
            tally[man["link_status"]] = tally.get(man["link_status"], 0) + 1
            # Отметка «документ прочитан» держится только на слове разведчика.
            # Если файл не отдаётся вовсе — снимаем её: иначе реестр врёт.
            if man["link_status"] in ("нет файла", "не отвечает"):
                man["verified_by_fetch"] = False

    docs["links_checked"] = date.today().isoformat()
    FILE.write_text(json.dumps(docs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\nссылок проверено: {len(seen)}")
    for k, v in sorted(tally.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(set(sys.argv[1:]))
