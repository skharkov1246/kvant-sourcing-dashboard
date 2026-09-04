#!/usr/bin/env python3
"""Структурный дифф двух OCR-текстов (см. ocr_pdfs.sh).

    diff_docs.py a.txt b.txt            все различия
    diff_docs.py a.txt b.txt --changes  только вставленные/удалённые требования

Снимает маркеры страниц и колонтитулы вида 113W_000_3494767_27, режет текст
на предложения и сравнивает difflib. Блоки replace — обычно шум OCR и переносы,
блоки insert/delete — реально добавленные и убранные пункты.
"""
import difflib
import re
import sys

FOOTER = re.compile(r"\d{3}\\?[/МWМ!ѴV]*[_ ]?000[_ ]?\d{6,8}[_ ]?\d{2}")
PAGE = re.compile(r"=====\s*PAGE\s*\d+\s*=====")


def load(path):
    text = open(path, encoding="utf-8").read()
    text = PAGE.sub(" ", text)
    text = FOOTER.sub(" ", text)
    for ch in "«»\"„“|":
        text = text.replace(ch, "")
    text = text.replace("—", "-")
    text = re.sub(r"[ \t]+", " ", text)
    parts = re.split(r"(?<=[.;:])\s+|\n", text)
    return [" ".join(p.split()) for p in parts if len(" ".join(p.split())) > 2]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only_changes = "--changes" in sys.argv
    if len(args) != 2:
        sys.exit(__doc__)
    a, b = load(args[0]), load(args[1])
    matcher = difflib.SequenceMatcher(None, [x.lower() for x in a], [x.lower() for x in b], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if only_changes:
            if tag not in ("insert", "delete"):
                continue
            chunk = " ".join(a[i1:i2] + b[j1:j2])
            if not re.search(r"[а-яА-Я]{4}", chunk):
                continue
            print(f"[{tag}] {chunk}")
            continue
        print(f"--- {tag} ---")
        for x in a[i1:i2]:
            print("  A:", x)
        for x in b[j1:j2]:
            print("  B:", x)


if __name__ == "__main__":
    main()
