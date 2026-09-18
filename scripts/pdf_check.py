#!/usr/bin/env python3
"""Проверка сгенерированного PDF: потери содержимого, выход за границы, кривые разрывы.

Зачем: при ручной вёрстке страниц (фиксированная высота + нарезка строк) браузер молча
обрезает то, что не влезло. На глаз это не видно — нужен машинный контроль.

    python3 scripts/pdf_check.py out.pdf
    python3 scripts/pdf_check.py out.pdf --expect-file ids.txt
    python3 scripts/pdf_check.py out.pdf --expect A1234 --expect B5678

--expect-file: текстовый файл, одна ожидаемая строка на строку файла (каталожные номера,
ID позиций — всё, что обязано присутствовать в готовом документе).

Код возврата 0 — чисто; 1 — найдены проблемы. Подробности см. docs/ПРАВИЛА-PDF.md
"""
from __future__ import annotations

import argparse
import re
import sys

MIN_CHARS_PER_PAGE = 400  # ниже этого страница подозрительна на кривой разрыв


def main() -> int:
    ap = argparse.ArgumentParser(description="Проверка PDF на потери и дефекты вёрстки")
    ap.add_argument("pdf")
    ap.add_argument("--expect", action="append", default=[],
                    help="строка, которая обязана быть в PDF (можно повторять)")
    ap.add_argument("--expect-file", help="файл со списком обязательных строк, по одной на строку")
    ap.add_argument("--min-chars", type=int, default=MIN_CHARS_PER_PAGE,
                    help=f"порог символов на страницу (по умолчанию {MIN_CHARS_PER_PAGE})")
    a = ap.parse_args()

    try:
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer
    except ImportError:
        print("нет pdfminer.six — поставь: pip install pdfminer.six", file=sys.stderr)
        return 1

    expected = list(a.expect)
    if a.expect_file:
        with open(a.expect_file, encoding="utf-8") as fh:
            expected += [ln.strip() for ln in fh if ln.strip()]

    pages = list(extract_pages(a.pdf))
    problems = []
    counts, full = [], []

    for i, pg in enumerate(pages, 1):
        text = "".join(el.get_text() for el in pg if isinstance(el, LTTextContainer))
        counts.append(len(text))
        full.append(text)
        # элементы, вылезшие за MediaBox — прямой признак обрезки
        out = [el for el in pg if isinstance(el, LTTextContainer)
               and (el.y0 < -1 or el.y1 > pg.height + 1 or el.x1 > pg.width + 1 or el.x0 < -1)]
        if out:
            problems.append(f"стр. {i}: {len(out)} текстовых блоков за границами страницы")

    if not pages:
        print("PDF пуст — ни одной страницы", file=sys.stderr)
        return 1

    sparse = [i + 1 for i, c in enumerate(counts) if c < a.min_chars]
    if sparse:
        problems.append(f"полупустые страницы (<{a.min_chars} симв.): {sparse}")

    # сверка обязательных строк: пробелы игнорируем, регистр учитываем
    flat = re.sub(r"\s+", "", "".join(full))
    missing = [s for s in expected if re.sub(r"\s+", "", s) not in flat]
    if missing:
        problems.append(f"не найдено в PDF: {len(missing)} из {len(expected)} обязательных строк")

    print(f"страниц: {len(pages)}")
    print(f"символов на странице: мин {min(counts)}, макс {max(counts)}, "
          f"медиана {sorted(counts)[len(counts) // 2]}")
    if expected:
        print(f"обязательных строк: {len(expected)}, найдено {len(expected) - len(missing)}")

    if problems:
        print("\nПРОБЛЕМЫ:")
        for p in problems:
            print(f"  - {p}")
        for m in missing[:20]:
            print(f"      отсутствует: {m}")
        if len(missing) > 20:
            print(f"      ... и ещё {len(missing) - 20}")
        return 1

    print("\nчисто")
    return 0


if __name__ == "__main__":
    sys.exit(main())
