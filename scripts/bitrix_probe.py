"""Зонд v30: победа — это переезд в «Реализацию»; номенклатура — из файлов сделок.

Владелец уточнил устройство портала: выигранная сделка не закрывается стадией, а
ПЕРЕЕЗЖАЕТ в направление «Реализация» той же карточкой, с сохранением истории.
Значит признак победы — текущее направление сделки, а не семантика стадии. Прежний
расчёт («выиграно 9 из 2926») был артефактом: переехавшие карточки просто не видны
в исходной воронке, а закрытые в ней — действительно проигранные.

Он же указал, что номенклатура лежит в файлах, привязанных к сделкам, а не в строках
товаров: v28 это подтвердил — штатный метод вернул ноль строк на всех 2926 сделках.
Здесь файлы скачиваются и разбираются, формат определяется по содержимому (сигнатуре),
а не по имени: в v29 имя файла оказалось в другом поле объекта и все 22 168 файлов
определились как «без расширения».

ПЕЧАТАЮТСЯ ТОЛЬКО АГРЕГАТЫ. Имена файлов, названия сделок, содержимое спецификаций
и наименования контрагентов не выводятся: репозиторий публичный.
"""
from __future__ import annotations

import io
import os
import re
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

BASE = os.environ["BITRIX_WEBHOOK_URL"].rstrip("/")
SAMPLE = int(os.environ.get("PROBE_SAMPLE", "400"))     # сколько файлов разбираем

SEGMENTS: dict[str, list[str]] = {
    "ГПУ — газопоршневые": ["газопоршн", "cummins", "камминз", "jenbacher", "waukesha", "mwm",
                            "innio", "qsk", "kta", "g3512", "g3516"],
    "ГТУ — газотурбинные": ["газотурб", "турбин", "sgt", "lm6000", "lm2500", "taurus", "centaur",
                            "solar turbines", "kawasaki", "гпа"],
    "ГШО — горно-шахтное": ["перфоратор", "буров", "epiroc", "atlas copco", "sandvik", "tamrock",
                            "normet", "пдм", "коронк", "штанг", "крепь", "проходческ"],
    "Насосное оборудование": ["насос", "flowserve", "sulzer", "ksb", "grundfos", "цнс", "шламов",
                              "warman", "weir", "рабочее колесо", "торцевое уплотнен"],
    "Компрессоры": ["компрессор", "винтов", "ingersoll", "kaeser", "воздуходув", "ресивер",
                    "осушитель воздух"],
    "Дробление и обогащение": ["дробилк", "мельниц", "грохот", "флотац", "гидроциклон", "metso",
                               "outotec", "футеровк", "сгустител", "классификатор", "конусн"],
    "Трубопроводная арматура": ["задвижк", "затвор", "клапан", "кран шаров", "вентиль", "арматур",
                                "фланец", "фланц"],
    "Электротехника и приводы": ["трансформатор", "кру", "ктп", "частотн", "чрп", "электродвигател",
                                 "schneider", "ячейк", "распредустройств", "кабель"],
    "КИПиА и автоматизация": ["датчик", "расходомер", "манометр", "термопар", "emerson", "endress",
                              "yokogawa", "уровнемер", "контроллер", "преобразователь давлен"],
    "Подъёмно-транспортное": ["конвейер", "транспортёр", "транспортер", "лебёдк", "лебедк",
                              "кран мостов", "редуктор", "тельфер", "роликоопор", "лента конвейер"],
    "Теплообмен и котельное": ["теплообменник", "alfa laval", "котёл", "котел", "градирн",
                               "экономайзер", "калорифер"],
    "Карьерная спецтехника": ["самосвал", "экскаватор", "белаз", "komatsu", "бульдозер",
                              "погрузчик фронтальн", "автогрейдер"],
    "Подшипники и уплотнения": ["подшипник", "skf", "timken", "манжет", "сальник", "john crane"],
    "Водоподготовка и фильтрация": ["мембран", "ультрафильтрац", "осмос", "фильтрующ", "фильтроэлемент",
                                    "водоподготовк", "картридж", "умягчител"],
    "Металлопрокат и трубы": ["швеллер", "двутавр", "металлопрокат", "лист стальн", "отвод",
                              "тройник", "труба"],
    "Сварка и инструмент": ["сварочн", "электрод", "проволок", "абразив", "круг отрезн", "сверло", "фреза"],
}


def bx(method: str, params: dict) -> dict:
    for _ in range(4):
        try:
            r = requests.post(f"{BASE}/{method}.json", json=params, timeout=90)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    return {}


def bx_all(method: str, params: dict, cap: int = 200000) -> list:
    out, start = [], 0
    while True:
        j = bx(method, {**params, "start": start})
        res = j.get("result")
        items = res.get("items") if isinstance(res, dict) and "items" in res else res
        out += items or []
        if "next" not in j or len(out) >= cap:
            return out
        start = j["next"]


def classify(text: str) -> str | None:
    t = text.lower().replace("ё", "е")
    best, score = None, 0
    for name, words in SEGMENTS.items():
        n = sum(t.count(w.replace("ё", "е")) for w in words)
        if n > score:
            best, score = name, n
    return best


def sniff(b: bytes) -> str:
    if b[:2] == b"PK":
        return "zip-документ"          # xlsx/docx/pptx
    if b[:4] == b"%PDF":
        return "pdf"
    if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "старый office (xls/doc)"
    if b[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"):
        return "изображение"
    return "прочее"


def text_from(b: bytes) -> str:
    """Текст из xlsx/docx. Для xlsx хватает общей таблицы строк — она содержит
    все текстовые ячейки книги и читается без разбора самих листов."""
    if b[:2] != b"PK":
        return ""
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        names = set(z.namelist())
        if "xl/sharedStrings.xml" in names:
            raw = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore")
            return " ".join(re.findall(r"<t[^>]*>([^<]{1,300})</t>", raw))
        if "word/document.xml" in names:
            raw = z.read("word/document.xml").decode("utf-8", "ignore")
            return " ".join(re.findall(r"<w:t[^>]*>([^<]{1,300})</w:t>", raw))
    except Exception:
        return ""
    return ""


def download(fo: dict) -> bytes | None:
    """Файлы лежат по-разному: часть в Диске, часть отдаётся прямой ссылкой из
    самого объекта поля. Пробуем оба пути — в v29 один только Диск дал 9 из 40."""
    for key in ("urlMachine", "downloadUrl", "url", "URL_MACHINE", "DOWNLOAD_URL"):
        u = fo.get(key)
        if u:
            try:
                r = requests.get(str(u), timeout=60)
                if r.status_code == 200 and len(r.content) > 200:
                    return r.content
            except Exception:
                pass
    fid = fo.get("id") or fo.get("ID")
    if fid:
        try:
            u = (bx("disk.file.get", {"id": fid}).get("result") or {}).get("DOWNLOAD_URL")
            if u:
                r = requests.get(u, timeout=60)
                if r.status_code == 200 and len(r.content) > 200:
                    return r.content
        except Exception:
            pass
    return None


def main() -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00+03:00")
    print(f"=== Зонд v30: сделки с {since[:10]} ===\n")

    cats = {str(c["ID"]): str(c.get("NAME") or "") for c in
            (bx("crm.dealcategory.list", {"select": ["ID", "NAME"]}).get("result") or [])}
    cats.setdefault("0", "Общее (воронка по умолчанию)")
    print("=== НАПРАВЛЕНИЯ ===")
    for cid, nm in sorted(cats.items(), key=lambda x: int(x[0])):
        print(f"  {cid:>4s}  {nm}")

    real_ids = [cid for cid, nm in cats.items() if "реализац" in nm.lower()]
    print(f"\nнаправление «Реализация»: {real_ids or 'НЕ НАЙДЕНО — победу определить нельзя'}\n")

    uf = bx("crm.deal.userfield.list", {"order": {"FIELD_NAME": "ASC"}}).get("result") or []
    ffields = [str(u["FIELD_NAME"]) for u in uf if u.get("USER_TYPE_ID") == "file"]

    deals = bx_all("crm.deal.list", {
        "filter": {">=DATE_CREATE": since},
        "select": ["ID", "TITLE", "CATEGORY_ID", "STAGE_ID", "OPPORTUNITY"],
        "order": {"ID": "ASC"}})
    print(f"сделок за период: {len(deals)}\n")

    by_cat: Counter = Counter()
    sum_cat: Counter = Counter()
    for d in deals:
        c = str(d.get("CATEGORY_ID") or "0")
        by_cat[c] += 1
        sum_cat[c] += float(d.get("OPPORTUNITY") or 0)
    print("=== СДЕЛКИ ПО НАПРАВЛЕНИЯМ ===")
    print(f"{'напр.':>5s} {'название':40s} {'сделок':>7s} {'сумма, млн':>11s}")
    for c, n in by_cat.most_common():
        print(f"{c:>5s} {cats.get(c, '?')[:40]:40s} {n:>7d} {sum_cat[c] / 1e6:>11.1f}")

    won = sum(by_cat[c] for c in real_ids)
    won_sum = sum(sum_cat[c] for c in real_ids)
    print(f"\nпобеды (сейчас в «Реализации»): {won} сделок на {won_sum / 1e6:.1f} млн")
    print(f"доля побед от всех заведённых за год: {won / max(len(deals), 1) * 100:.1f}%\n")

    # ── номенклатура из файлов ────────────────────────────────────────────────
    print(f"=== ФАЙЛЫ: разбираем выборку из {SAMPLE} ===")
    ids = [str(d["ID"]) for d in deals]
    cat_of = {str(d["ID"]): str(d.get("CATEGORY_ID") or "0") for d in deals}
    amt_of = {str(d["ID"]): float(d.get("OPPORTUNITY") or 0) for d in deals}

    refs: list[tuple[str, dict]] = []
    keyset: Counter = Counter()
    for i in range(0, len(ids), 50):
        j = bx("crm.deal.list", {"filter": {"ID": ids[i:i + 50]}, "select": ["ID"] + ffields})
        for x in j.get("result") or []:
            for f in ffields:
                v = x.get(f)
                if not v:
                    continue
                for fo in (v if isinstance(v, list) else [v]):
                    if isinstance(fo, dict):
                        keyset.update(fo.keys())
                        refs.append((str(x["ID"]), fo))
        if len(refs) > SAMPLE * 8:
            break
    print(f"файловых объектов собрано: {len(refs)}")
    print(f"поля объекта файла: {dict(keyset.most_common())}\n")

    kinds: Counter = Counter()
    seg_files: Counter = Counter()
    seg_deals: dict[str, set] = {}
    ok = fail = 0
    step = max(1, len(refs) // SAMPLE)
    for did, fo in refs[::step][:SAMPLE]:
        b = download(fo)
        if not b:
            fail += 1
            continue
        ok += 1
        k = sniff(b)
        kinds[k] += 1
        t = text_from(b)
        if len(t) < 40:
            continue
        seg = classify(t)
        if seg:
            seg_files[seg] += 1
            seg_deals.setdefault(seg, set()).add(did)

    print(f"скачано: {ok} · не удалось: {fail}")
    print(f"по содержимому: {dict(kinds.most_common())}\n")

    print("=== ЧТО В ФАЙЛАХ: сегменты по разобранным документам ===")
    print(f"{'сегмент':32s} {'файлов':>7s} {'сделок':>7s} {'из них в реализации':>21s} {'сумма, млн':>11s}")
    for seg, n in seg_files.most_common():
        ds = seg_deals.get(seg, set())
        w = sum(1 for d in ds if cat_of.get(d) in real_ids)
        s = sum(amt_of.get(d, 0) for d in ds)
        print(f"{seg:32s} {n:>7d} {len(ds):>7d} {w:>21d} {s / 1e6:>11.1f}")

    print("\n✓ зонд v30 завершён")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
