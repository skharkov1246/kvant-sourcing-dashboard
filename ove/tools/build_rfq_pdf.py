#!/usr/bin/env python3
"""PDF-версии запросов ТКП (RFQ) проекта ОВЭ-75 — русская и английская.

Русские исходники: ove/public/docs/rfq/ove75-rfq-<NN>-<слаг>.docx (их делает
build_rfq.py). Английские исходники: ove/rfq_en/<слаг>.docx — двуязычные
документы (Part 1 English, Part 2 中文), в репозитории лежат как источник и в
дерево документов сайта не попадают.

Результат в ove/public/docs/rfq/:
  ove75-rfq-<NN>-<слаг>-ru.pdf — техническое задание на русском;
  ove75-rfq-<NN>-<слаг>-en.pdf — то же на английском, с китайской частью.

Конвертация — LibreOffice (soffice), метаданные штампует doc_meta.
Запуск: python3 ove/tools/build_rfq_pdf.py [слаг ...]
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import doc_meta

ROOT = Path(__file__).resolve().parent.parent
RFQ = ROOT / "public" / "docs" / "rfq"
EN_SRC = ROOT / "rfq_en"
SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")


def ru_docs() -> list:
    """[(слаг, русский DOCX)] — слаг из имени ove75-rfq-07-vacfilter.docx.
    Один слаг может встречаться дважды (теплообменники идут двумя позициями),
    английский исходник у таких позиций общий."""
    return [(f.stem.split("-", 3)[3], f)
            for f in sorted(RFQ.glob("ove75-rfq-[0-9][0-9]-*.docx"))]


def convert(files: list, outdir: Path) -> list:
    """DOCX → PDF пачкой; возвращает пути готовых PDF."""
    if not files:
        return []
    if not SOFFICE:
        raise RuntimeError("не найден soffice (LibreOffice) — PDF не собрать")
    subprocess.run([SOFFICE, "--headless", "--norestore", "--convert-to", "pdf",
                    "--outdir", str(outdir), *map(str, files)],
                   check=True, capture_output=True, timeout=900)
    return [outdir / (f.stem + ".pdf") for f in files]


def build(only: list | None = None) -> None:
    docs = [(s, f) for s, f in ru_docs() if not only or s in only]
    made, missing = [], []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # русские
        for (s, f), pdf in zip(docs, convert([f for _, f in docs], tmp)):
            if not pdf.exists():
                missing.append(f"{s}: русский PDF не собрался")
                continue
            dst = RFQ / f"{f.stem}-ru.pdf"
            shutil.move(str(pdf), dst)
            doc_meta.pdf(dst)
            made.append(dst)
        # английские (двуязычные): исходник именуется по позиции, а не по классу —
        # два класса обслуживают по две разные позиции (теплообменники)
        have = [f for _, f in docs if (EN_SRC / f"{f.stem}.docx").exists()]
        for _, f in docs:
            if not (EN_SRC / f"{f.stem}.docx").exists():
                missing.append(f"{f.stem}: нет английского исходника "
                               f"{EN_SRC.name}/{f.stem}.docx")
        for f, pdf in zip(have, convert([EN_SRC / f"{f.stem}.docx" for f in have], tmp)):
            if not pdf.exists():
                missing.append(f"{f.stem}: английский PDF не собрался")
                continue
            dst = RFQ / f"{f.stem}-en.pdf"
            shutil.move(str(pdf), dst)
            doc_meta.pdf(dst)
            made.append(dst)
    # манифест для дерева документов сайта (build.py → __RFQFILES_JSON__)
    manifest = sorted(f"rfq/{p.name}" for p in RFQ.glob("ove75-rfq-*-??.pdf"))
    (ROOT / "data" / "rfq_files.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"PDF запросов ТКП: {len(made)} файлов в {RFQ.relative_to(ROOT.parent)}; "
          f"в манифесте {len(manifest)}")
    for p in sorted(made):
        print(f"  {p.name:44} {p.stat().st_size // 1024:>4} КБ")
    if missing:
        print(f"не собрано: {len(missing)}")
        for m in missing:
            print(f"  {m}")


if __name__ == "__main__":
    build(sys.argv[1:] or None)
