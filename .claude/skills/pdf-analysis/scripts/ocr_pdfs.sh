#!/usr/bin/env bash
# OCR всех PDF в папке. Использование: ocr_pdfs.sh <вход> [выход] [dpi] [язык]
# На выходе <выход>/<имя>.txt с маркерами "===== PAGE NN =====". ~1,2 с на страницу.
set -eu
IN="${1:?укажи папку с PDF}"; OUT="${2:-./ocr}"; DPI="${3:-150}"; LANG="${4:-rus}"
PNG="$OUT/.png"; mkdir -p "$OUT" "$PNG"
export OMP_THREAD_LIMIT=1
shopt -s nullglob
for f in "$IN"/*.pdf "$IN"/*.PDF; do
  b=$(basename "$f"); b="${b%.*}"
  mkdir -p "$PNG/$b"
  [ -f "$PNG/$b/p-01.png" ] || pdftoppm -r "$DPI" -png "$f" "$PNG/$b/p"
done
ls -d "$PNG"/*/ 2>/dev/null | xargs -P 4 -I{} bash -c '
  d="{}"; b=$(basename "$d"); out="'"$OUT"'/$b.txt"; lang="'"$LANG"'"
  : > "$out"
  for img in "$d"p-*.png; do
    n=$(basename "$img" .png)
    echo "===== PAGE ${n#p-} =====" >> "$out"
    OMP_THREAD_LIMIT=1 tesseract "$img" - -l "$lang" --psm 6 2>/dev/null >> "$out"
  done'
for t in "$OUT"/*.txt; do echo "$(basename "$t"): $(grep -c '===== PAGE' "$t") стр., $(wc -c < "$t") байт"; done
