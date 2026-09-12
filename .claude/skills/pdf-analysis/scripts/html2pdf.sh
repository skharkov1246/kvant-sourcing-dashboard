#!/usr/bin/env bash
# HTML -> PDF через headless Chromium. Использование: html2pdf.sh report.html "Отчёт.pdf"
set -eu
SRC="${1:?укажи html}"; DST="${2:-report.pdf}"
CHROME=""
for c in /opt/pw-browsers/chromium-*/chrome-linux/chrome /usr/bin/chromium /usr/bin/google-chrome; do
  [ -x "$c" ] && CHROME="$c" && break
done
[ -n "$CHROME" ] || { echo "Chromium не найден"; exit 1; }
"$CHROME" --headless --no-sandbox --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$DST" "file://$(readlink -f "$SRC")" 2>&1 | grep -i "written to file" || true
command -v pdfinfo >/dev/null && pdfinfo "$DST" | grep -E '^Pages|^Page size'
