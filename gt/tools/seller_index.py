#!/usr/bin/env python3
"""Указатель карточек продавцов: у кого есть страница ПО НАШЕМУ номеру.

ЗАЧЕМ. Веб-поиск — расходуемый и ненадёжный инструмент: лимит запросов на
сессию кончается, а поисковые машины в этом окружении отдают капчу, 403 или
чужую выдачу (замер 18.09.2026: Bing на запрос «"960592C1"» вернул статьи про
тренировки). При этом продавцы сами публикуют карту своего сайта — именно для
того, чтобы их карточки находили. В карте лежит адрес каждой карточки, а в
адресе — номер детали. Значит вопрос «у кого есть страница по этому номеру»
закрывается без поиска вовсе, разом по всей заявке и бесплатно.

ПЕРВЫЙ ОПЫТ — КАТАЛОГ ИЗГОТОВИТЕЛЯ. gt/tools/solar_catalog.py сделал это для
магазина Solar: 72 491 адрес, подтверждено существование 381 номера заявки, и
попутно выяснилось, что в каталоге номер записан без разделителей, из-за чего
поиск нашим написанием не находил ничего. Этот инструмент — то же самое, но для
торговцев, и по списку доменов, а не по одному.

ЧТО ЭТО ДОКАЗЫВАЕТ И ЧЕГО НЕ ДОКАЗЫВАЕТ. Доказывает: у продавца ЕСТЬ карточка
по этому номеру, вот её адрес — то есть адрес по самой детали, а не по классу.
НЕ доказывает ни цены, ни остатка, ни срока: за ними нужно открыть карточку, а
это отдельная работа и отдельный разбор. Правило репозитория «не выдавай родовой
адрес за адрес по детали» здесь выполняется по построению: совпал номер, а не
класс изделия.

ПОЧЕМУ СПИСОК ДОМЕНОВ ЛЕЖИТ В РЕПОЗИТОРИИ. Чтобы его было видно и можно было
оспорить: продавец, попавший в список по ошибке, даёт ложные «адреса по детали».
Список — gt/data/seller_sitemaps.json, и в нём у каждого домена сказано, откуда
он взялся и что о нём известно.

    python gt/tools/seller_index.py --dir /tmp/sm --fetch --write
    python gt/tools/seller_index.py --dir /tmp/sm --write   # по скачанному
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pnkey import key  # noqa: E402

ASK = ROOT / "gt/data/ship_lukoil.json"
RV = ROOT / "gt/data/ship_reverify.json"
SELLERS = ROOT / "gt/data/seller_sitemaps.json"
OUT = ROOT / "gt/data/seller_index.json"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
IS_SITEMAP = re.compile(r"\.xml(\.gz)?$", re.I)
# Из адреса берём последние два отрезка пути: у одних продавцов номер лежит в
# последнем («/product/1042018-3400»), у других перед идентификатором записи
# («/product/1042018-3400/01tUm...»).
SLUG = re.compile(r"/([^/?#]+)/?(?:[?#].*)?$")
# ХВОСТ ОТРЕЗКА, А НЕ ВЕСЬ ОТРЕЗОК. Первая редакция брала отрезок целиком и не
# нашла НИ ОДНОЙ строки заявки при 396 087 проиндексированных адресов: у
# продавца адрес выглядит как «/product/turbine-parts-1008378-2/», то есть перед
# номером стоит имя магазина. Поэтому из отрезка берутся все его хвосты по
# разделителям: «turbine-parts-1008378-2» даёт и «1008378-2», и «parts-1008378-2»,
# и так далее. Совпадением считается хвост — то есть адрес, ЗАКАНЧИВАЮЩИЙСЯ нашим
# номером, а не содержащий его где-то посередине.
SPLIT = re.compile(r"[-_.]+")

# Ключ короче пяти знаков номером не считается: «10», «A1», «SET», «4650» на
# хвосте чужого адреса дают совпадение по совпадению, а не по номеру. Замер по
# заявке: строк с ключом короче пяти знаков — единицы, и все они проза в поле
# номера, которая всё равно закрывается вопросом заказчику.
MIN_KEY = 5


def get(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["curl", "-sSL", "-m", "120", "-A", UA, url, "-o", str(dest)],
                       capture_output=True, text=True)
    return r.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def safe(url: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", url)[-120:]


def fetch(domains: list[dict], into: Path) -> dict[str, list[Path]]:
    """robots.txt → карты сайта → файлы карт. Адреса не угадываются.

    Карта карт (sitemapindex) разворачивается на один уровень: этого хватает
    всем проверенным продавцам, а неограниченная рекурсия по чужому сайту —
    это уже обход, а не чтение опубликованного указателя.
    """
    out: dict[str, list[Path]] = {}
    for d in domains:
        host = str(d.get("host") or "").strip()
        if not host:
            continue
        rob = into / f"robots_{safe(host)}.txt"
        maps: list[str] = []
        if get(f"https://{host}/robots.txt", rob):
            txt = rob.read_text(encoding="utf-8", errors="replace")
            maps = [m.group(1) for m in re.finditer(r"(?im)^\s*sitemap:\s*(\S+)", txt)]
        if not maps:
            print(f"{host}: карт сайта в robots.txt нет", file=sys.stderr)
            continue
        files: list[Path] = []
        for url in maps:
            f = into / f"{safe(host)}__{safe(url)}"
            if not get(url, f):
                continue
            body = f.read_text(encoding="utf-8", errors="replace")
            if "<sitemapindex" in body:
                for m in LOC.finditer(body):
                    u = m.group(1)
                    if not IS_SITEMAP.search(u):
                        continue
                    g = into / f"{safe(host)}__{safe(u)}"
                    if get(u, g):
                        files.append(g)
            else:
                files.append(f)
        out[host] = files
        print(f"{host}: файлов карты {len(files)}", file=sys.stderr)
    return out


def segments(url: str) -> list[str]:
    """Последний отрезок пути и предыдущий: номер лежит то в одном, то в другом."""
    parts = [p for p in url.split("?")[0].split("#")[0].rstrip("/").split("/") if p]
    return parts[-2:] if len(parts) > 1 else parts


def tails(part: str) -> list[str]:
    """Ключи всех хвостов отрезка по разделителям, от самого длинного к короткому."""
    tok = [t for t in SPLIT.split(part) if t]
    out = []
    for i in range(len(tok)):
        k = key("".join(tok[i:]))
        if len(k) >= MIN_KEY:
            out.append(k)
    return out


def index(files_by_host: dict[str, list[Path]]) -> tuple[dict, Counter]:
    """ключ номера → {хост: адрес}. Первый встреченный адрес и остаётся."""
    idx: dict[str, dict[str, str]] = {}
    seen = Counter()
    for host, files in files_by_host.items():
        for f in files:
            try:
                body = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in LOC.finditer(body):
                url = m.group(1)
                if IS_SITEMAP.search(url):
                    continue
                seen[host] += 1
                for part in segments(url):
                    for k in tails(part):
                        idx.setdefault(k, {}).setdefault(host, url)
    return idx, seen


def measure(idx: dict, seen: Counter, ask: list, rv: dict, domains: list[dict],
            verification: dict | None = None) -> dict:
    rows, hosts_hit = [], Counter()
    for r in ask:
        k = key(r.get("pn"))
        if len(k) < MIN_KEY:
            continue
        found = idx.get(k)
        if not found:
            continue
        for h in found:
            hosts_hit[h] += 1
        x = rv.get(k) or {}
        rows.append({
            "pn": r.get("pn"),
            "qty": r.get("qty"),
            "sheet": r.get("sheet"),
            "cards": [{"host": h, "url": u} for h, u in sorted(found.items())],
            "had_contacts": bool(str(x.get("contacts") or "").strip()),
            "had_price": isinstance(x.get("price_low"), (int, float)),
        })
    new_addr = [r for r in rows if not r["had_contacts"]]
    verification = verification or {}
    # ПИСЬМО НА ОПЕРАТОРА, А НЕ НА СТРОКУ. Замер 18.09.2026: все 401 совпадение
    # пришлись на одного оператора с двумя каталогами, и 302 из них — строки без
    # найденной цены. Это один запрос на 302 позиции, а не 302 разбора.
    by_mail: dict[str, list] = {}
    mails = {str(d.get("host")): d for d in domains if d.get("mail")}
    for r in rows:
        if r["had_price"]:
            continue          # цена уже найдена — спрашивать нечего
        for c in r["cards"]:
            d = mails.get(c["host"])
            if d:
                by_mail.setdefault(str(d["mail"]), []).append(r)
                break
    letters = []
    for mail, items in sorted(by_mail.items(), key=lambda kv: -len(kv[1])):
        host = next(d for d in domains if d.get("mail") == mail)
        also = [str(x) for x in (host.get("same_operator_as") or [])
                if any(c["host"] == x for it in items for c in it["cards"])]
        letters.append({
            "to": mail,
            "address_read_on": host.get("mail_read_on"),
            "address_note": host.get("mail_note"),
            "phone": host.get("phone"),
            "rows": len(items),
            "qty_total": sum(float(i.get("qty") or 0) for i in items),
            "pns": [i["pn"] for i in items],
            "subject": f"Запрос цены и наличия по {len(items)} позиц. из вашего каталога",
            "body": letter(host, items, also),
        })
    return {
        "updated": date.today().isoformat(),
        "source": "Карты сайтов продавцов из gt/data/seller_sitemaps.json (robots.txt → "
                  "sitemap). Считает gt/tools/seller_index.py.",
        "what_it_proves": "У продавца ЕСТЬ карточка по этому номеру, и вот её адрес: это "
                          "адрес по самой детали, а не по классу изделия.",
        "what_it_does_not_prove": "Ни цены, ни остатка, ни срока. За ними надо открыть "
                                  "карточку — это отдельная работа и отдельный разбор.",
        "why_not_search": "Веб-поиск расходуем и в этом окружении ненадёжен: лимит запросов "
                          "кончается, машины отдают капчу, 403 или выдачу не по запросу. "
                          "Карту сайта продавец публикует сам, и она отвечает на вопрос "
                          "«есть ли карточка по номеру» разом по всей заявке.",
        "hosts": [{"host": d.get("host"), "why": d.get("why"),
                   "addresses_seen": seen.get(str(d.get("host")), 0),
                   "ask_rows_matched": hosts_hit.get(str(d.get("host")), 0)}
                  for d in domains],
        "addresses_indexed": int(sum(seen.values())),
        "keys_indexed": len(idx),
        "ask_rows_matched": len(rows),
        "of_them_had_no_address_before": len(new_addr),
        "min_key_length": MIN_KEY,
        "why_min_key": "Ключ короче четырёх знаков номером не считается: «10», «A1» и «SET» "
                       "дали бы ложные совпадения с половиной заявки.",
        "letters": letters,
        "verification": verification,
        "why_one_letter": "Все совпадения пришлись на одного оператора с двумя каталогами, "
                          "поэтому это один запрос на все его позиции, а не отдельный разбор "
                          "по каждой строке. Спрашиваются только те строки, по которым цены у "
                          "нас ещё нет. Наших цифр, вилок и имени заказчика в письме нет.",
        "rows": sorted(rows, key=lambda z: str(z["pn"])),
    }


def verify(rows: list[dict], into: Path, take: int = 3) -> dict:
    """Открывает несколько найденных карточек и проверяет, что номер на них ЕСТЬ.

    Совпадение адреса — довод сильный, но не последний: адрес мог бы
    генерироваться под любой запрос. Поэтому вместе с образцом проверяется
    ВЫДУМАННЫЙ адрес того же вида: если витрина отдаёт карточку и на него, её
    адресам верить нельзя и указатель надо выбросить. Образец берётся из начала,
    середины и конца списка, а не первые подряд.
    """
    if not rows:
        return {}
    pick = [rows[0], rows[len(rows) // 2], rows[-1]][:take]
    checked = []
    for r in pick:
        url = r["cards"][0]["url"]
        f = into / ("verify_" + re.sub(r"\W+", "_", url)[-60:] + ".html")
        ok = get(url, f)
        flat = re.sub(r"[^A-Za-z0-9]", "",
                      f.read_text(encoding="utf-8", errors="replace")).upper() if ok else ""
        checked.append({"pn": r["pn"], "url": url,
                        "number_on_page": bool(flat) and key(r["pn"]) in flat})
    # Контроль: адрес того же вида с заведомо несуществующим номером.
    host = pick[0]["cards"][0]["url"].rsplit("/product/", 1)[0]
    fake = f"{host}/product/solar-turbines-9999999-77/"
    f = into / "verify_fake.html"
    ok = get(fake, f)
    flat = re.sub(r"[^A-Za-z0-9]", "",
                  f.read_text(encoding="utf-8", errors="replace")).upper() if ok else ""
    return {
        "checked_cards": checked,
        "invented_address": fake,
        "invented_address_returns_that_number": bool(flat) and "999999977" in flat,
        "what_it_means": "Номер обязан быть НА СТРАНИЦЕ, а выдуманный адрес того же вида не "
                         "должен отдавать карточку с этим номером. Если отдаёт — витрина "
                         "генерирует страницы под любой запрос, и указателю верить нельзя.",
    }


def letter(host: dict, items: list[dict], also: list[str]) -> str:
    """Одно письмо оператору на все его позиции. Наших цифр в нём нет.

    Почему одно, а не по строке: карточки нашлись у ОДНОГО оператора (у него два
    каталога на разных доменах), и 302 позиции закрываются одним запросом. Пять
    вопросов — те же, что в gt/tools/quote_letters.py: письмо с шестым вопросом
    превращается в переписку.
    """
    lines = ["Добрый день!", "",
             "У вас в каталоге есть карточки по позициям ниже — мы их нашли по вашим же "
             "страницам. Просим по каждой позиции дать:", "",
             "1) цену за штуку и цену за всё указанное количество;",
             "2) остаток на складе ЧИСЛОМ на сегодня;",
             "3) срок поставки под указанное количество целиком;",
             "4) срок действия цены;",
             "5) базис поставки (Инкотермс).", ""]
    if also:
        lines += [f"Часть позиций у вас выставлена и на {', '.join(also)} — если это один и "
                  f"тот же склад, достаточно одного ответа.", ""]
    lines += ["Позиции:"]
    for i, it in enumerate(items, 1):
        q = f" — {it['qty']} шт" if it.get("qty") else ""
        lines.append(f"{i}. {it['pn']}{q}")
    lines += ["", "Если позиции нет в наличии, достаточно одного слова «нет» — это тоже "
                  "ответ. Если у позиции есть действующая замена, просим назвать её номер.",
              "", "С уважением,", "КВАНТ"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="папка для карт сайтов (ВНЕ репозитория)")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="открыть образец найденных карточек и проверить, что номер на них "
                         "есть, плюс контроль выдуманным адресом")
    a = ap.parse_args()

    domains = json.loads(SELLERS.read_text(encoding="utf-8"))["hosts"]
    into = Path(a.dir)
    if a.fetch:
        files = fetch(domains, into)
    else:
        files = {}
        for d in domains:
            host = str(d.get("host"))
            files[host] = sorted(into.glob(f"{safe(host)}__*"))
    if not any(files.values()):
        print("карт сайтов нет; запусти с --fetch", file=sys.stderr)
        return 2

    idx, seen = index(files)
    ask = json.loads(ASK.read_text(encoding="utf-8"))["rows"]
    rv = {key(r["pn"]): r for r in json.loads(RV.read_text(encoding="utf-8"))["rows"]}
    # Сначала замер, потом проверка образца по его же строкам: так проверяется
    # ровно то, что попадёт в набор, а не заново собранный список.
    m = measure(idx, seen, ask, rv, domains)
    if a.verify:
        v = verify(m["rows"], into)
        m = measure(idx, seen, ask, rv, domains, v)
        bad = [c for c in v.get("checked_cards", []) if not c["number_on_page"]]
        if bad or v.get("invented_address_returns_that_number"):
            print("ПРОВЕРКА ОБРАЗЦА НЕ ПРОЙДЕНА — набор не записан:", file=sys.stderr)
            for c in bad:
                print(f"  номера нет на странице: {c['pn']} → {c['url']}", file=sys.stderr)
            if v.get("invented_address_returns_that_number"):
                print(f"  выдуманный адрес отдаёт карточку: {v['invented_address']}",
                      file=sys.stderr)
            return 1
        print(f"проверка образца пройдена: {len(v['checked_cards'])} карточки, "
              f"номер на странице у всех; выдуманный адрес карточки не отдаёт")
    print(f"адресов в картах: {m['addresses_indexed']}; ключей: {m['keys_indexed']}")
    print(f"строк заявки с карточкой по номеру: {m['ask_rows_matched']}")
    print(f"  из них адреса продавца у нас до этого НЕ было: "
          f"{m['of_them_had_no_address_before']}")
    for h in m["hosts"]:
        print(f"  {str(h['host']):26} адресов {h['addresses_seen']:>7} | "
              f"совпало со заявкой {h['ask_rows_matched']}")
    if a.write:
        OUT.write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n{OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
