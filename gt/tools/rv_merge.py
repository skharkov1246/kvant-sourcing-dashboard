#!/usr/bin/env python3
"""Приём выдачи разведки в набор перепроверки: один вход, одни проверки.

ЗАЧЕМ. Строки перепроверки приходят от разведки пачками по десять-двадцать, и
каждую пачку до сих пор вносили руками. Цена такой правки известна: набор
дважды принимал строку, у которой в числовом поле цены лежала проза, и один
раз — строку про изделие, которого в заявке нет. Оба случая потом ловил гейт,
но уже после коммита.

ЧТО ПРОВЕРЯЕТСЯ ЗДЕСЬ, ДО ЗАПИСИ:
 * номер строки есть в сводке заявки (иначе разбор не о нашей потребности);
 * номер ещё не разобран (повтор удваивает строку в любом счёте);
 * вердикт опознаётся закрытым списком по НАЧАЛУ текста;
 * цена — положительное число в долларах за штуку либо null, проза живёт
   в price_note;
 * запрещённые поля количества, вилки и экспозиции отсутствуют: они живут
   в сводке заявки, и только там;
 * поле «чем подтверждено» не пустое;
 * ценовой вердикт не выносится без цены и без названного источника;
 * источник не круговой: наш собственный публичный репозиторий и выдачи
   поисковых машин ценой не являются.

КРУГОВОЙ ИСТОЧНИК. Репозиторий публичный, и наши перепроверки проиндексированы:
18.09.2026 разведчик сообщил, что поиск по «15508.2 реле Siemens» первой
строкой возвращает наш собственный PR #305. Следующий проход принял бы
собственный вывод прошлого прохода за независимое подтверждение, и ошибка
стала бы неопровержимой, потому что подтверждалась бы сама собой.

ВЫДАЧА ПОИСКОВОЙ МАШИНЫ — НЕ СТРАНИЦА. В тот же день сводка поиска трижды, на
три разных запроса, выдала по номеру 3420932 цену «7 709 руб., 18 шт в
наличии» у названного магазина, ни разу не дав ссылки на карточку. Открытый
интерфейс самого магазина по этому номеру вернул ноль записей при рабочем
положительном контроле по соседнему номеру: цифра была выдумана сводкой. Взяли
бы её — объявили бы вилку заниженной втрое на основании несуществующей
карточки.

Строка, не прошедшая проверку, НЕ записывается и называется с причиной.
Поле skeptics, если разведка его не заполнила, ставится одной записью с
holds=null и прямым указанием, что независимого опровержения не было: пустое
поле читалось бы как «проверено», а это не так.

ДВА РЕЖИМА. Обычный принимает строки, которых в наборе ещё нет. Режим --update
принимает строки по УЖЕ разобранным номерам: так возвращается добор цены
(gt/tools/rv_pricehunt.py), где опознание уже сделано, а искалась одна цифра.
В этом режиме опознание прежнего разбора НЕ ЗАТИРАЕТСЯ — переписываются только
торговые поля, а прежнее пояснение сохраняется целиком, и к нему добавляется
новое под заголовком с прежним вердиктом. Иначе добор цены стирал бы
доказательства, ради которых первый проход и делался.

    python gt/tools/rv_merge.py <файл выдачи.json> [ещё файлы] [--dry-run]
    python gt/tools/rv_merge.py <файл добора.json> --update
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "gt/data/ship_reverify.json"
ASK = ROOT / "gt/data/ship_lukoil.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ru_words import clean_row                       # noqa: E402
from verdicts import PRICED, UNKNOWN, vkey            # noqa: E402

BANNED = ("qty", "lo", "hi", "expo", "band_lo", "band_hi", "exposure")
# Адреса, которые ценой быть не могут. Список закрытый: он ОТКАЗЫВАЕТ строке,
# а отказ должен опираться на разобранный случай, а не на догадку.
CIRCULAR = (
    ("github.com/skharkov1246", "наш собственный репозиторий: подтверждение самим собой"),
    ("claude.ai/code", "наша собственная сессия работы, а не источник"),
    ("kvant-sourcing-dashboard", "наш собственный репозиторий: подтверждение самим собой"),
)
SEARCH_ENGINES = ("google.com/search", "yandex.ru/search", "bing.com/search",
                  "duckduckgo.com/?q", "search.marcia", "ya.ru/search")
DOMAIN = re.compile(r"[a-z0-9][a-z0-9-]*\.[a-z][a-z.]{1,8}\b", re.I)
NO_SKEPTIC = {
    "lens": "независимого опровержения не было",
    "holds": None,
    "confidence": "не установлена",
    "checked": ("Строка принята от разведки без отдельного скептика: проверку на "
                "опровержение по ней никто не вёл. Это не значит, что вывод неверен, — это "
                "значит, что его никто не пытался сломать."),
    "objection": "",
    "correction": "",
}


def key(x) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(x or "").split("(")[0].upper())


# Поля, которые добор цены ПЕРЕПИСЫВАЕТ: всё торговое. Опознания здесь нет
# намеренно — что это за изделие и кто изготовитель, установил первый проход, и
# второй, искавший одну цифру, права затирать это не имеет.
TRADE = ("price_low", "price_high", "price_note", "price_source", "price_kind",
         "price_authorized", "stock", "lead_time", "volume_note", "recommended",
         "blocker", "band_verdict", "channel", "contacts")
# Поля опознания: заполняются, только если у прежней строки они пусты.
IDENT = ("what_it_is", "real_maker", "real_pn", "lifecycle", "maker_short")


def check(r: dict, ask: dict, have: set[str], update: bool = False) -> str:
    k = key(r.get("pn"))
    if not k:
        return "номер пустой"
    if k not in ask:
        return "номера нет в сводке заявки"
    if update and k not in have:
        return "номер ещё не разобран: доборять цену нечему"
    if not update and k in have:
        return "номер уже разобран"
    if vkey(r) == UNKNOWN:
        return f'вердикт не опознан: «{str(r.get("band_verdict"))[:40]}»'
    if not str(r.get("note") or "").strip():
        return "пустое поле «чем подтверждено»"
    for f in BANNED:
        if f in r:
            return f"поле {f} в строке: количество и вилка живут в сводке заявки"
    for f in ("price_low", "price_high"):
        v = r.get(f)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            return f"{f} не положительное число в долларах за штуку: {v!r}"
    lo, hi = r.get("price_low"), r.get("price_high")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
        return "найденный пол выше найденного потолка"
    if vkey(r) in PRICED:
        if not isinstance(lo, (int, float)):
            return "вердикт по цене без числовой цены"
        src = str(r.get("price_source") or "")
        if not DOMAIN.search(src) and "ССЫЛКА НЕ СОХРАНЕНА" not in src:
            return "вердикт по цене без названной страницы"
        low = src.lower()
        for mark, why in CIRCULAR:
            if mark in low:
                return f"круговой источник ({mark}): {why}"
        if any(e in low for e in SEARCH_ENGINES) and not any(
                DOMAIN.search(part) for part in re.split(
                    r"google\.com/search|yandex\.ru/search|bing\.com/search", low)[1:]):
            return "источник цены — выдача поисковой машины, а не страница продавца"
    return ""


def apply_update(old: dict, new: dict) -> None:
    """Переносит в разобранную строку результат добора цены.

    Прежнее пояснение сохраняется целиком: оно содержит доказательства
    опознания, а второй проход их не перепроверял. Новое пишется следом, под
    заголовком, где назван прежний вердикт по цене, — чтобы по строке было
    видно, что именно изменилось и от чего.
    """
    was = str(old.get("band_verdict") or "").strip() or "вердикта не было"
    add = ("\n\nДОБОР ЦЕНЫ (второй проход, искалась только цифра; опознание выше "
           f"оставлено прежним). Было по цене: «{was[:160]}». Стало: "
           + str(new.get("note") or "").strip())
    for f in TRADE:
        if f in new:
            old[f] = new[f]
    for f in IDENT:
        if not str(old.get(f) or "").strip() and str(new.get(f) or "").strip():
            old[f] = new[f]
    old["note"] = (old.get("note") or "").rstrip() + add
    old["price_hunt"] = True


def merge(paths: list[Path], dry: bool = False, update: bool = False) -> dict:
    out = json.loads(OUT.read_text(encoding="utf-8"))
    ask = {key(r.get("pn")) for r in json.loads(ASK.read_text(encoding="utf-8"))["rows"]}
    have = {key(r.get("pn")) for r in out["rows"]}
    by_key = {key(r.get("pn")): r for r in out["rows"]}
    took, left = [], []
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:                                  # noqa: BLE001
            left.append((p.name, "—", f"файл не читается: {e}"))
            continue
        for r in (d["rows"] if isinstance(d, dict) else d):
            why = check(r, ask, have, update)
            if why:
                left.append((p.name, str(r.get("pn")), why))
                continue
            clean_row(r)
            if update:
                if not dry:
                    apply_update(by_key[key(r.get("pn"))], r)
                took.append(r)
                continue
            if not isinstance(r.get("skeptics"), list) or not r["skeptics"]:
                r["skeptics"] = [dict(NO_SKEPTIC)]
            took.append(r)
            have.add(key(r.get("pn")))
    if took and not dry:
        if not update:
            out["rows"] = out["rows"] + took
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    grown = 0 if update else (len(took) if dry else 0)
    return {"took": took, "left": left, "total": len(out["rows"]) + grown}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 1
    upd = "--update" in sys.argv
    r = merge([Path(a) for a in args], dry="--dry-run" in sys.argv, update=upd)
    what = "обновлено строк" if upd else "принято строк"
    print(f"{what}: {len(r['took'])}  в наборе теперь: {r['total']}")
    for row in r["took"]:
        print(f"  + {str(row['pn']):18} {vkey(row):18} {str(row.get('price_low'))}")
    for name, pn, why in r["left"]:
        print(f"  ОТКАЗ {name} / {pn}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
