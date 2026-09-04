#!/usr/bin/env bash
# Ставит всё, что нужно для чтения, OCR и печати PDF. Идемпотентен.
set -u
need_apt=0
for b in pdftotext pdftoppm pdfinfo tesseract; do command -v "$b" >/dev/null || need_apt=1; done
if [ "$need_apt" = "1" ]; then
  apt-get update -qq || true                      # без update install падает с 404
  apt-get install -y -qq poppler-utils tesseract-ocr tesseract-ocr-rus
fi
python3 -c "import pypdf" 2>/dev/null || pip install --user --quiet cffi pypdf
for b in pdftotext pdftoppm pdfinfo tesseract; do printf '%-10s %s\n' "$b" "$(command -v $b || echo НЕТ)"; done
python3 -c "import pypdf; print('pypdf     ', pypdf.__version__)" 2>/dev/null || echo "pypdf      НЕТ"
tesseract --list-langs 2>/dev/null | tail -n +2 | tr '\n' ' '; echo
