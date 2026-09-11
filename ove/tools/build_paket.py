#!/usr/bin/env python3
"""Сборка ZIP-пакетов документов ОВЭ-75 из реестров ove/data/bi_docs.json и podacha.json.

Архивы кладутся в ove/public/docs/paket/:
  - ove75-lot{N}-paket.zip (N=1..4) — файлы документов лота N из bi_docs.json;
  - ove75-obshchee-paket.zip        — файлы лота 0 («Общее по комплексу»);
  - ove75-podacha-paket.zip         — файлы позиций podacha.json со статусами ready/form.

Пути в реестрах — относительно ove/public/ (docs/...). Внутри архива префикс docs/
снимается: файлы из корня docs/ лежат плоско, подпапки по типу (bi/, sketches/,
specs/, apply/, rfq/, partner/) сохраняются как относительные пути.
У документа берётся files[] (элементы — строки или объекты {path, tag}), при
отсутствии поля — одиночный file. Отсутствующие файлы пропускаются
с предупреждением, сборка не падает. Только stdlib.
Запуск: python3 ove/tools/build_paket.py
"""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
OUTDIR = PUBLIC / "docs" / "paket"


def doc_files(doc: dict) -> list[str]:
    """Пути файлов документа: files[] (строки или объекты {path,...}), иначе file."""
    files = doc.get("files")
    if isinstance(files, list):
        out = []
        for f in files:
            if isinstance(f, dict):
                f = f.get("path") or f.get("file")
            if f:
                out.append(str(f).strip())
        return out
    f = doc.get("file")
    return [str(f).strip()] if f else []


def arcname(rel: str) -> str:
    """Имя внутри архива: docs/bi/x.docx → bi/x.docx; docs/x.docx → x.docx (плоско)."""
    rel = rel.lstrip("/")
    return rel[len("docs/"):] if rel.startswith("docs/") else rel


def make_zip(name: str, rel_paths: list[str]) -> tuple[Path, int, int] | None:
    """Собирает OUTDIR/name из уникальных существующих файлов (пути — от ove/public/).

    Возвращает (путь, файлов, байт) или None, если класть нечего.
    """
    seen: set[str] = set()
    items: list[tuple[Path, str]] = []
    for rel in rel_paths:
        # элемент — путь либо пара (путь, имя внутри архива)
        arc = None
        if isinstance(rel, (tuple, list)):
            rel, arc = rel
        if not rel or rel in seen:
            continue
        seen.add(rel)
        src = PUBLIC / rel
        if not src.is_file():
            print(f"  ! {name}: пропуск, нет файла {rel}")
            continue
        items.append((src, arc or arcname(rel)))
    if not items:
        print(f"  ! {name}: ни одного существующего файла — архив не создан")
        return None
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in items:
            z.write(src, arc)
    with zipfile.ZipFile(out) as z:  # самопроверка целостности
        bad = z.testzip()
        if bad:
            print(f"  ! {name}: testzip нашёл повреждённый элемент {bad}")
    return out, len(items), out.stat().st_size


def build() -> None:
    bi = json.loads((DATA / "bi_docs.json").read_text(encoding="utf-8"))
    podacha = json.loads((DATA / "podacha.json").read_text(encoding="utf-8"))
    made: list[tuple[Path, int, int]] = []

    lots = {lot.get("lot"): lot for lot in bi.get("lots", [])}

    def lot_items(lot: dict) -> list:
        """Выпуск лота: PDF под читаемыми именами «NN обозначение — название.pdf»
        в корне архива, исходники (Word, SVG, DXF, IFC) — в папке src/."""
        out = []
        for i, doc in enumerate(lot.get("docs", []), 1):
            code = doc.get("code") or ""
            for f in doc_files(doc):
                if code and f.lower().endswith(".pdf"):
                    title = str(doc.get("name", "")).replace("/", "-").replace(":", " —")[:70]
                    out.append((f, f"{i:02d} {code} — {title}.pdf"))
                else:
                    out.append(f)
            for s in doc.get("src", []) or []:
                out.append((s, "src/" + arcname(s)))
        return out

    for n in (1, 2, 3, 4):
        lot = lots.get(n)
        if not lot:
            print(f"  ! лот {n}: не найден в bi_docs.json")
            continue
        res = make_zip(f"ove75-lot{n}-paket.zip", lot_items(lot))
        if res:
            made.append(res)

    lot0 = lots.get(0)
    if lot0:
        res = make_zip("ove75-obshchee-paket.zip", lot_items(lot0))
        if res:
            made.append(res)
    else:
        print("  ! лот 0 (общее): не найден в bi_docs.json")

    rels = [f for d in podacha.get("docs", [])
            if d.get("status") in ("ready", "form") for f in doc_files(d)]
    res = make_zip("ove75-podacha-paket.zip", rels)
    if res:
        made.append(res)

    print("ZIP-пакеты →", "; ".join(
        f"{out.name}: {cnt} файлов, {size // 1024} КБ" for out, cnt, size in made))


if __name__ == "__main__":
    build()
