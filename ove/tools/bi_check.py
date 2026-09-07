#!/usr/bin/env python3
"""Контроль выпуска БИ Р0: единый чек-лист для каждого PDF реестра.

Проверяет то, что должен видеть Заказчик, и то, чего он видеть не должен:
  титул    — организация, заказчик, обозначение, штамп «не для строительства», ревизия 0;
  ревизии  — у текстовых документов второй лист — «Лист регистрации ревизий»;
  следы    — имена файлов базы, версии v0.x, скобки-заглушки, markdown, TODO,
             упоминания ИИ и инструментов, адреса почты, телефоны;
  метаданные — автор и производитель PDF — организация, в бинарнике нет следов
             конвертеров и браузера;
  шрифты   — все встроены; объём — число страниц в разумных пределах;
  листы    — число страниц = числу исходных SVG, в тексте есть обозначение.

Запуск: python3 ove/tools/bi_check.py [код ...] [--json путь]
Код возврата 1, если есть замечания уровня «брак».
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
ORG = "ООО «КВАНТ»"
CUSTOMER = "Кольская ГМК"

TRACES = [
    (r"\b\w+\.json\b", "имя файла базы"),
    (r"\bv0\.\d", "версия v0.x"),
    (r"\[\s*(?:Ф\.И\.О|заполн|указать|уточнить|TODO|todo)[^\]]*\]", "скобка-заглушка"),
    (r"\*\*|\n#{1,3} |```", "markdown"),
    (r"\bTODO\b|\bFIXME\b|\bXXX\b", "служебная пометка"),
    (r"(?i)\b(claude|anthropic|openai|chatgpt|gpt-?\d|llm|нейросет)", "упоминание ИИ"),
    (r"(?i)черновик\s*v", "«черновик v»"),
    (r"[\w.+-]+@[\w-]+\.[a-z]{2,}", "адрес почты"),
    (r"\+\d[\d\s()-]{8,}\d", "телефон"),
    (r"(?i)\b(генерится|генерируется автоматически|вкладк[аеи] сайта|на сайте)\b", "упоминание сайта/сборки"),
    (r"(?i)сборка\s+20\d\d-\d\d-\d\d", "дата сборки"),
]
BIN_TRACES = [b"Chrom", b"Skia", b"HeadlessChrome", b"LibreOffice", b"pypdf", b"python-docx", b"docx-js"]


def sh(*cmd) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
    except Exception as e:  # noqa: BLE001
        return f"ERR {e}"


def text(pdf: Path, first=None, last=None) -> str:
    cmd = ["pdftotext", "-layout"]
    if first:
        cmd += ["-f", str(first)]
    if last:
        cmd += ["-l", str(last)]
    return sh(*cmd, str(pdf), "-")


def check(row: dict, rel: dict) -> dict:
    code = row["code"]
    out = {"code": code, "kind": row.get("kind", "text"), "errors": [], "warnings": [], "pages": 0}
    if not rel or not rel.get("pdf"):
        out["errors"].append("PDF не собран")
        return out
    pdf = PUBLIC / rel["pdf"]
    if not pdf.exists():
        out["errors"].append(f"нет файла {rel['pdf']}")
        return out
    info = sh("pdfinfo", str(pdf))
    m = re.search(r"Pages:\s+(\d+)", info)
    pages = int(m.group(1)) if m else 0
    out["pages"] = pages
    if pages == 0:
        out["errors"].append("PDF без страниц")
        return out
    # метаданные
    if ORG not in info:
        out["errors"].append("метаданные: автор не организация")
    prod = re.search(r"Producer:\s*(.*)", info)
    if not prod or "ООО" not in prod.group(1):
        out["errors"].append("метаданные: producer не организация")
    raw = pdf.read_bytes()
    for t in BIN_TRACES:
        if t in raw:
            out["errors"].append(f"след инструмента в файле: {t.decode()}")
    # титул (текст PDF переносится по строкам — сверяем по нормализованному)
    norm = lambda s: re.sub(r"\s+", " ", s)
    p1 = norm(text(pdf, 1, 1))
    kind = row.get("kind", "text")
    if kind in ("text", "album"):
        for need, what in ((ORG, "организация"), (CUSTOMER, "заказчик"), (code, "обозначение"),
                           ("НЕ ДЛЯ СТРОИТЕЛЬСТВА", "штамп «не для строительства»"), ("Ревизия 0", "ревизия 0")):
            if need not in p1:
                out["errors"].append(f"титул: нет — {what}")
        p2 = norm(text(pdf, 2, 2))
        if "Лист регистрации ревизий" not in p2:
            out["errors"].append("лист 2: нет листа регистрации ревизий")
        p3 = text(pdf, 3, 3)
        if len(p3.strip()) < 200:
            out["warnings"].append("лист 3 почти пустой — проверить начало содержания")
        if pages < 4:
            out["errors"].append(f"слишком мало страниц: {pages}")
        if pages > 80:
            out["warnings"].append(f"объём {pages} стр. — проверить разумность")
        # колонтитулы: обозначение должно встречаться на большинстве страниц
        mid = norm(text(pdf, min(4, pages), min(6, pages)))
        if code not in mid:
            out["errors"].append("колонтитул: обозначение не найдено на страницах содержания")
        if "Лист " not in mid:
            out["warnings"].append("нижний колонтитул: нумерация «Лист N из M» не найдена")
    else:  # sheet
        srcs = row.get("sources") or []
        if row.get("module"):
            pass
        elif srcs and pages != len(srcs):
            out["errors"].append(f"листов {pages}, исходных SVG {len(srcs)}")
        for need, what in ((code, "обозначение"), (ORG, "организация"), ("НЕ ДЛЯ СТРОИТЕЛЬСТВА", "штамп")):
            if need not in p1:
                out["errors"].append(f"лист 1: нет — {what}")
    # следы по всему тексту
    full = text(pdf)
    for pat, what in TRACES:
        hits = re.findall(pat, full)
        if hits:
            sample = str(hits[0])[:40]
            (out["errors"] if what not in ("адрес почты", "телефон") or kind != "sheet" else out["warnings"]).append(
                f"след: {what} ×{len(hits)} («{sample}»)")
    n_open = len(re.findall(r"(?i)определяется на стадии БИ|уточняется по ТКП", full))
    out["open_items"] = n_open
    # шрифты
    fonts = sh("pdffonts", str(pdf))
    bad = [ln for ln in fonts.splitlines()[2:] if ln.strip() and re.search(r"\s(no)\s", ln)]
    if bad:
        out["errors"].append(f"шрифты не встроены: {len(bad)}")
    return out


def main(argv: list) -> int:
    codes, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--json":
            skip = True
        elif not a.startswith("--"):
            codes.append(a)
    reg = json.loads((DATA / "bi_register.json").read_text(encoding="utf-8"))["rows"]
    rel_p = DATA / "bi_release.json"
    rel = {d["code"]: d for d in json.loads(rel_p.read_text(encoding="utf-8")).get("docs", [])} if rel_p.exists() else {}
    rows = [r for r in reg if not codes or r["code"] in codes]
    results = [check(r, rel.get(r["code"])) for r in rows]
    bad = 0
    for r in results:
        st = "БРАК " if r["errors"] else ("норм " if not r["warnings"] else "замеч")
        bad += bool(r["errors"])
        print(f"{st} {r['code']:26} стр.{r['pages']:>3} откр.{r.get('open_items', 0):>3}  "
              + "; ".join(r["errors"] + r["warnings"]))
    print(f"\nпроверено {len(results)}, брак {bad}, без замечаний {sum(1 for r in results if not r['errors'] and not r['warnings'])}")
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
