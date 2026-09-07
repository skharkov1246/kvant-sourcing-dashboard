#!/usr/bin/env python3
"""Разбор Application Guides MOTORTECH → gpu/data/motortech_cross.json.

Зачем. MOTORTECH публикует каталоги применяемости, где ПРЯМО указано, какой их
номер заменяет какой номер OEM: «MOTORTECH 06.50.151 = CATERPILLAR 418-4861 /
232-6346 / 165-1589 / 124-0749». Это кросс-таблицы уровня A (от изготовителя
компонента) — самое надёжное, что вообще бывает по аналогам. Руками их не
перепечатать: сотни пар в трёх PDF.

Как получить исходники. Сайт MOTORTECH закрыт антибот-защитой (Incapsula), curl
получает заглушку. PDF нужно скачать браузером по ссылкам из GUIDES ниже и
положить в каталог, который передаётся первым аргументом (по умолчанию gpu/tools/pdf).

    python3 gpu/tools/parse_motortech.py [каталог_с_pdf]

Извлечение текста своё, без внешних зависимостей: PDF-потоки распаковываются
zlib, а коды глифов переводятся в символы по картам /ToUnicode КАЖДОГО шрифта
отдельно (в этих каталогах подмножества шрифтов, и один и тот же код в разных
шрифтах означает разные цифры — общая карта даёт мусор в номерах).
"""
from __future__ import annotations

import json
import re
import sys
import zlib
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/motortech_cross.json"

BASE = "https://www.motortech.de/fileadmin/user_upload/applicationguides/"
GUIDES = {
    "CAT G3500": ("MOTORTECH-Application-Guide-CATERPILLAR-G3500-Series-01.00.011-EN-2017-08.pdf", "Caterpillar"),
    "CAT G3400": ("MOTORTECH-Application-Guide-CATERPILLAR-G3400-Series-01.00.010-EN-2016-07_01.pdf", "Caterpillar"),
    "Waukesha VHP": ("MOTORTECH-Application-Guide-WAUKESHA-VHP-Series-01.00.014-EN-2018-02.pdf", "Waukesha"),
    "CAT G3600": ("MOTORTECH-Application-Guide-CATERPILLAR-G3600-Series-01.00.012-EN-2017-08.pdf", "Caterpillar"),
}

BRANDS = r"(CATERPILLAR|ALTRONIC|WAUKESHA|CHAMPION|DENSO|IMPCO|MOTORTECH|MOT/CAT|MOT/WAU)"
MT_PN = r"\d{2}\.\d{2}\.\d{3}[A-Za-z]?(?:-[\w.]+)?"
OTHER_PN = r"[A-Z0-9][A-Z0-9./-]{2,17}"
STOP = (r"(?!CATERPILLAR|ALTRONIC|WAUKESHA|CHAMPION|DENSO|IMPCO|MOTORTECH|MOT/CAT|MOT/WAU"
        r"|Optional|Equivalent|Fits|To|Due|For|Incl|Models|CEC|P/N)")
MARKERS = r"(Equivalent to:|Fits Ignition Coil:|Fits Spark Plug:|To replace:)"
PART_RE = (r"(Ignition Coil[s]?|Spark Plug Lead[s]?|Spark Plug Extension[s]?|Spark Plug[s]?|Primary Lead[s]?"
           r"|Pickup[s]?|Trigger Magnet|Throttle Bod\w+|Oxygen Sensor[s]?|Ignition Controller[s]?"
           r"|Overhaul Kit[s]?|Lip seal|Lube Oil Filtration|Camshaft Trigger Magnet|Harness\w*)")


# ── извлечение текста из PDF ────────────────────────────────────────────────
def pdf_text(path: Path) -> str:
    raw = path.read_bytes()
    objs = {int(m.group(1)): m.group(2)
            for m in re.finditer(rb"(\d+)\s+0\s+obj(.*?)endobj", raw, re.S)}

    def stream_of(body):
        if body is None:
            return None
        m = re.search(rb"stream\r?\n", body)
        if not m:
            return None
        end = body.find(b"endstream", m.end())
        try:
            return zlib.decompress(body[m.end():end])
        except Exception:
            return body[m.end():end]

    def parse_cmap(data: bytes) -> dict:
        mp = {}
        for m in re.finditer(rb"beginbfchar(.*?)endbfchar", data, re.S):
            for a, b in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", m.group(1)):
                mp[int(a, 16)] = bytes.fromhex(b.decode()).decode("utf-16-be", "ignore")
        for m in re.finditer(rb"beginbfrange(.*?)endbfrange", data, re.S):
            for a, b, c in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", m.group(1)):
                lo, hi, dst = int(a, 16), int(b, 16), int(c, 16)
                for i in range(lo, hi + 1):
                    mp[i] = chr(dst + i - lo)
        return mp

    font_map = {}
    for num, body in objs.items():
        m = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", body)
        if m:
            font_map[num] = parse_cmap(stream_of(objs.get(int(m.group(1)))) or b"")

    pages = []
    for num, body in sorted(objs.items()):
        if not re.search(rb"/Type\s*/Page\b", body):
            continue
        res = body
        m = re.search(rb"/Resources\s+(\d+)\s+0\s+R", body)
        if m:
            res = objs.get(int(m.group(1)), b"")
        fm = re.search(rb"/Font\s*<<(.*?)>>", res, re.S)
        src = fm.group(1) if fm else b""
        if not src:
            m2 = re.search(rb"/Font\s+(\d+)\s+0\s+R", res)
            src = objs.get(int(m2.group(1)), b"") if m2 else b""
        fonts = {nm.decode(): font_map.get(int(ref), {})
                 for nm, ref in re.findall(rb"/(\w+)\s+(\d+)\s+0\s+R", src)}
        cm = re.search(rb"/Contents\s+(\d+)\s+0\s+R", body)
        if not cm:
            continue
        content = stream_of(objs.get(int(cm.group(1)))) or b""
        cur, buf = {}, []
        for tok in re.finditer(rb"/(\w+)\s+[\d.]+\s+Tf|\((?:\\.|[^\\()])*\)|Td|TD|T\*|ET", content, re.S):
            s = tok.group()
            if s.endswith(b"Tf"):
                cur = fonts.get(tok.group(1).decode(), {})
            elif s.startswith(b"("):
                t = s[1:-1]
                t = re.sub(rb"\\([nrtbf()\\])", lambda m: {b"n": b"\n", b"r": b"\r", b"t": b"\t",
                                                           b"b": b"", b"f": b"", b"(": b"(",
                                                           b")": b")", b"\\": b"\\"}[m.group(1)], t)
                t = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8)]), t)
                buf.append("".join(cur.get(c, chr(c) if 32 <= c < 127 else "") for c in t))
            else:
                buf.append(" ")
        pages.append("".join(buf))
    return " ".join(pages)


# ── разбор кросс-таблиц ─────────────────────────────────────────────────────
def clean(t: str) -> str:
    t = t.replace("-US", " ").replace("de-DE", " ").replace("en-US", " ")
    t = re.sub(r"\ben\b", " ", t)
    for ch in "®™■▸":
        t = t.replace(ch, " ")
    return re.sub(r"\s+", " ", t)


def parse_guide(name: str, oem: str, url: str, text: str) -> list[dict]:
    t = clean(text)
    out = []
    for m in re.finditer(MARKERS + r"(.{0,420}?)(?=" + MARKERS + r"|Optional:|$)", t, re.S):
        kind, blk = m.group(1), m.group(2)
        left = t[max(0, m.start() - 300):m.start()]

        parts = re.findall(PART_RE, left, re.I)
        engines = re.findall(r"For ((?:G3\d{3}[A-Z/]*|VHP [FL]\d+|L\d{4}|F\d{4})[^■]{0,70}?)(?: e\.g\.|,|$)", left)

        mt = sorted(set(re.findall(MT_PN, blk)))
        if not mt:  # вторая раскладка: «P/N 95.70.002-250  Equivalent to: CATERPILLAR P/N 116-6806»
            tail = re.findall(r"P/N\s*(" + MT_PN + r")\s*$", left.strip()) or re.findall(MT_PN, left[-160:])
            mt = tail[-1:] if tail else []

        cross = []
        for bm in re.finditer(BRANDS + r"\s*P/N\s*((?:" + STOP + OTHER_PN + r"[,\s]*){1,8})", blk):
            pns = [p for p in re.split(r"[,\s]+", bm.group(2))
                   if re.fullmatch(OTHER_PN, p) and p not in {"P/N", "MOTORTECH"}]
            if pns:
                cross.append({"brand": bm.group(1), "pns": pns})
        if not cross:
            continue
        out.append({
            "guide": name, "oem": oem, "url": url, "kind": kind.rstrip(":"),
            "part": parts[-1] if parts else "", "engines": engines[-1].strip() if engines else "",
            "motortech": mt, "cross": cross,
        })
    return out


def main() -> int:
    pdf_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else (ROOT / "tools/pdf")
    records, missing = [], []
    for name, (fn, oem) in GUIDES.items():
        p = pdf_dir / fn
        if not p.exists():
            missing.append(f"{name}: {BASE}{fn}")
            continue
        rec = parse_guide(name, oem, BASE + fn, pdf_text(p))
        print(f"{name}: {len(rec)} записей")
        records += rec

    if missing:
        print("\n⚠ не найдены PDF (скачать браузером — curl режется антибот-защитой):", file=sys.stderr)
        for m in missing:
            print("   " + m, file=sys.stderr)
        if not records:
            print("нечего разбирать — оставляю закоммиченный файл как есть", file=sys.stderr)
            return 0

    pairs = sum(len(c["pns"]) for r in records for c in r["cross"])
    OUT.write_text(json.dumps({
        "updated": date.today().isoformat(),
        "source": "Application Guides MOTORTECH (PDF), разобраны gpu/tools/parse_motortech.py. "
                  "Кросс уровня A: изготовитель компонента сам заявляет эквивалентность номеру OEM.",
        "guides": [{"name": n, "oem": o, "url": BASE + f} for n, (f, o) in GUIDES.items()],
        "stats": {"records": len(records), "oem_pns": pairs,
                  "by_brand": dict(Counter(c["brand"] for r in records for c in r["cross"]))},
        "records": records,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"OK → {OUT}: {len(records)} записей, {pairs} номеров OEM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
